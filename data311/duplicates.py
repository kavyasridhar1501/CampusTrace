"""San Francisco duplicate labels, from the city's own status_notes.

Labels: a case is a duplicate when its status_notes carries one of the city's DUPLICATE
markers. When the note names the earlier case ("Duplicate of 1001244235", "previously
reported as 1001234474", "SR #101001244332"), the pair (duplicate, original) is resolved
against the pulled cases.

The test built from these is structure only: it sees category, subtype, location and
time, never text. build_candidates() lists, for each case, the earlier open-window cases
in the same category within a radius and a time window. A case-level label (is this a
duplicate?) covers every case; a pair-level label (is this earlier case its original?)
covers the duplicates whose original could be resolved.
"""

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

DUPLICATE_MARKER = re.compile(r"duplicate", re.IGNORECASE)
ORIGINAL_REF = re.compile(
    r"(?:duplicate\s+of|previously\s+reported\s+as|same\s+as\s+reported\s+in\s+sr|sr\s*#)"
    r"\s*#?\s*(\d{6,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Case:
    id: str
    opened: datetime
    category: str
    subtype: str
    lat: float | None
    lon: float | None
    notes: str

    @property
    def is_duplicate(self) -> bool:
        return bool(DUPLICATE_MARKER.search(self.notes))

    def referenced_ids(self) -> list[str]:
        return ORIGINAL_REF.findall(self.notes)


def parse_case(row: dict[str, str]) -> Case:
    def num(v: str | None) -> float | None:
        if not v:
            return None
        x = float(v)
        return None if x == 0 else x

    return Case(
        id=row["service_request_id"],
        opened=datetime.fromisoformat(row["requested_datetime"]),
        category=row.get("service_name") or "",
        subtype=row.get("service_subtype") or "",
        lat=num(row.get("lat")),
        lon=num(row.get("long")),
        notes=row.get("status_notes") or "",
    )


def resolve_original(case: Case, by_id: dict[str, Case]) -> str | None:
    """The earlier case this duplicate points to, if the note names one we have.
    Some notes carry a two-digit prefix on the ID ("SR #10" + ID); try both forms."""
    for ref in case.referenced_ids():
        for candidate in (ref, ref[2:] if ref.startswith("10") else None):
            if (
                candidate
                and candidate in by_id
                and candidate != case.id
                and by_id[candidate].opened <= case.opened
            ):
                return candidate
    return None


def haversine_m(a: Case, b: Case) -> float:
    assert a.lat is not None and a.lon is not None and b.lat is not None and b.lon is not None
    r = 6_371_000.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Candidate:
    later: str
    earlier: str
    same_subtype: bool
    distance_m: float
    hours_apart: float


def build_candidates(
    cases: Sequence[Case], radius_m: float = 150.0, window: timedelta = timedelta(hours=72)
) -> list[Candidate]:
    """Earlier cases in the same category within radius and window, for every case with a
    location. Cases are bucketed by category and scanned in time order."""
    out: list[Candidate] = []
    by_cat: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        if c.lat is not None and c.lon is not None:
            by_cat[c.category].append(c)
    for group in by_cat.values():
        group.sort(key=lambda c: c.opened)
        start = 0
        for i, later in enumerate(group):
            while group[start].opened < later.opened - window:
                start += 1
            for earlier in group[start:i]:
                d = haversine_m(later, earlier)
                if d <= radius_m:
                    out.append(
                        Candidate(
                            later=later.id,
                            earlier=earlier.id,
                            same_subtype=later.subtype == earlier.subtype,
                            distance_m=round(d, 1),
                            hours_apart=round(
                                (later.opened - earlier.opened).total_seconds() / 3600, 3
                            ),
                        )
                    )
    return out


@dataclass(frozen=True)
class LabelSummary:
    cases: int
    duplicates: int
    with_reference: int
    resolved: int
    resolved_in_candidates: int


def summarise(cases: Iterable[Case], candidates: Iterable[Candidate]) -> LabelSummary:
    cases = list(cases)
    by_id = {c.id: c for c in cases}
    dups = [c for c in cases if c.is_duplicate]
    resolved = {c.id: o for c in dups if (o := resolve_original(c, by_id))}
    cand_pairs = {(x.later, x.earlier) for x in candidates}
    return LabelSummary(
        cases=len(cases),
        duplicates=len(dups),
        with_reference=sum(1 for c in dups if c.referenced_ids()),
        resolved=len(resolved),
        resolved_in_candidates=sum(1 for k, v in resolved.items() if (k, v) in cand_pairs),
    )
