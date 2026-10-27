"""Simulated repair history: about 600 past jobs, two years before the Fall 2026 quarter.

Two steps, kept apart on purpose:

1. skeleton(): deterministic code samples which asset failed, when, the pattern (true
   cause, action, parts, minutes) and the technician, from failure_patterns.yaml. Chronic
   assets fail more often. About 8% of first visits address the wrong cause and get a
   second job on the same asset a few days later, which is how wrong diagnoses show up.
2. write_notes(): Claude writes the technician's short free-text note for each record,
   in batches, given only that record's fields. Responses are cached on disk by request
   hash, so rerunning costs nothing.

    uv run python -m agent.repairs.history skeleton     # writes the skeleton, no API calls
    uv run python -m agent.repairs.history notes        # adds notes (needs Anthropic credentials)
    uv run python -m agent.repairs.history sample       # writes a review sample
    uv run python -m agent.repairs.history load         # inserts into the repairs table

Output: campus_data/repair_history.jsonl. pattern_id is the ground truth for the learning
loop; it is kept in the file and never shown to the agent.
"""

import argparse
import hashlib
import json
import math
import os
import random
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, create_engine, func, insert, select

from agent.campus.loader import Campus, load_campus
from agent.config import get_settings
from agent.db import Repair
from agent.intake.symptoms import SymptomType

Trade = Literal["plumbing", "electrical", "hvac", "structural", "custodial", "grounds"]

HISTORY_START = date(2024, 9, 23)
HISTORY_END = date(2026, 9, 22)
TARGET_JOBS = 600
MISDIAGNOSIS_RATE = 0.08
SEED = 20260927
NAMESPACE = uuid.UUID("6f1c0b52-3c1e-4c55-9a8e-6a2f1d1a0001")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pattern(Strict):
    id: str
    weight: float
    symptoms: list[SymptomType]
    spread: float = Field(ge=0, le=1)
    cause: str
    action: str
    parts: list[str]
    minutes: tuple[int, int]
    season: dict[Literal["winter", "summer"], float] = Field(default_factory=dict)
    kinds: list[str] | None = None
    room_types: list[str] | None = None
    trade: Trade | None = None


class AssetType(Strict):
    trade: Trade | None
    rate_per_year: float
    age_factor_per_decade: float
    patterns: list[Pattern]


class FailurePatterns(Strict):
    age_ref: int
    chronic: dict[str, float]
    asset_types: dict[str, AssetType]


class RepairRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    asset_id: str
    asset_type: str
    building: str
    pattern_id: str
    symptom: SymptomType
    confirmed_cause: str
    action: str
    parts: list[str]
    minutes: int
    completed_at: datetime
    technician: str
    trade: Trade
    outcome: Literal["fixed", "symptom_returned"]
    note_raw: str | None = None


def load_patterns(path: str | Path) -> FailurePatterns:
    return FailurePatterns.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def _season(day: date) -> str | None:
    if day.month in (12, 1, 2):
        return "winter"
    if day.month in (6, 7, 8, 9):
        return "summer"
    return None


def asset_rate(fp: FailurePatterns, campus: Campus, asset: str) -> float:
    d = campus.graph.nodes[asset]
    at = fp.asset_types[d["type"]]
    year = d.get("install_year") or campus.buildings[d["building"]].year_built
    age = max(0, HISTORY_END.year - year)
    factor = float(at.age_factor_per_decade ** ((age - fp.age_ref) / 10))
    return at.rate_per_year * factor * fp.chronic.get(asset, 1.0)


def eligible_patterns(fp: FailurePatterns, campus: Campus, asset: str) -> list[Pattern]:
    d = campus.graph.nodes[asset]
    out = []
    for p in fp.asset_types[d["type"]].patterns:
        if p.kinds and d.get("attributes", {}).get("kind") not in p.kinds:
            continue
        if p.room_types and d.get("room_type") not in p.room_types:
            continue
        out.append(p)
    return out


def skeleton(
    fp: FailurePatterns, campus: Campus, seed: int = SEED, target: int = TARGET_JOBS
) -> list[RepairRecord]:
    rng = random.Random(seed)
    g = campus.graph
    tz = ZoneInfo(campus.campus.timezone)
    assets = sorted(n for n, d in g.nodes(data=True) if d["type"] in fp.asset_types)
    assets = [a for a in assets if eligible_patterns(fp, campus, a)]
    rates = [asset_rate(fp, campus, a) for a in assets]
    days = [
        HISTORY_START + timedelta(days=i) for i in range((HISTORY_END - HISTORY_START).days + 1)
    ]
    crews = campus.crews.crews

    def technician(trade: Trade, asset_type: str, day: date) -> str:
        if asset_type == "elevator_bank":
            return "elevator vendor"
        wd = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[day.weekday()]
        on_shift = [c for c in crews if trade in c.trades and wd in c.shift.days]
        pool = on_shift or [c for c in crews if trade in c.trades]
        return rng.choice(pool).id

    def make(
        asset: str, p: Pattern, day: date, outcome: Literal["fixed", "symptom_returned"], n: int
    ) -> RepairRecord:
        d = g.nodes[asset]
        at = fp.asset_types[d["type"]]
        trade = p.trade or at.trade
        assert trade is not None, p.id
        tech = technician(trade, d["type"], day)
        start = 7 if day.weekday() < 5 else 10
        when = datetime.combine(day, time(start), tzinfo=tz) + timedelta(
            minutes=rng.randint(30, 7 * 60)
        )
        return RepairRecord(
            id=str(uuid.uuid5(NAMESPACE, f"{seed}-{n}")),
            asset_id=asset,
            asset_type=d["type"],
            building=d["building"],
            pattern_id=p.id,
            symptom=rng.choice(p.symptoms),
            confirmed_cause=p.cause,
            action=p.action,
            parts=list(p.parts),
            minutes=rng.randint(*p.minutes),
            completed_at=when.astimezone(UTC),
            technician=tech,
            trade=trade,
            outcome=outcome,
        )

    # Scale rates so the expected total is the target, then draw each asset's count.
    scale = target / (sum(rates) * len(days) / 365)
    records: list[RepairRecord] = []
    n = 0
    for asset, rate in zip(assets, rates, strict=True):
        lam = rate * scale * len(days) / 365
        k = _poisson(rng, lam)
        pats = eligible_patterns(fp, campus, asset)
        for _ in range(k):
            day = rng.choice(days)
            season = _season(day)
            weights = [p.weight * (p.season.get(season, 1.0) if season else 1.0) for p in pats]  # type: ignore[call-overload]
            p = rng.choices(pats, weights)[0]
            if len(pats) > 1 and rng.random() < MISDIAGNOSIS_RATE:
                wrong = rng.choice([q for q in pats if q.id != p.id])
                records.append(make(asset, wrong, day, "symptom_returned", n))
                n += 1
                day = min(HISTORY_END, day + timedelta(days=rng.randint(1, 10)))
            records.append(make(asset, p, day, "fixed", n))
            n += 1
    records.sort(key=lambda r: (r.completed_at, r.id))
    return records


def _poisson(rng: random.Random, lam: float) -> int:
    # Knuth's method; lam stays small here (a few per asset).
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


# ---- notes -----------------------------------------------------------------------------

NOTE_SYSTEM = """You write the short repair notes facilities technicians type into a work-order \
system when they close a job. For each record you get the asset, the symptom, the confirmed \
cause, the action taken, the parts used and the minutes spent.

Write one note per record, 1 to 3 sentences, in a technician's voice: terse, practical, \
sometimes abbreviated (AHU, VAV, DAT, GFCI, PRV), no greeting, no sign-off. State the cause \
and fix as given. Do not add causes, readings, parts or follow-up work that are not in the \
record. When outcome is symptom_returned the technician did not know that yet; write it as a \
normal closed job. Vary sentence shape across records. Return every record's id unchanged."""


class Note(BaseModel):
    id: str
    note: str


class NoteBatch(BaseModel):
    notes: list[Note]


NoteWriter = Callable[[list[dict[str, Any]]], dict[str, str]]


def note_input(r: RepairRecord, campus: Campus) -> dict[str, Any]:
    d = campus.graph.nodes[r.asset_id]
    return {
        "id": r.id,
        "asset": f"{r.asset_id} ({d['name']})",
        "symptom": r.symptom,
        "confirmed_cause": r.confirmed_cause,
        "action": r.action,
        "parts": r.parts,
        "minutes": r.minutes,
        "outcome": r.outcome,
    }


def claude_note_writer(cache_dir: Path, model: str) -> NoteWriter:
    """Notes from Claude, cached by request hash. Needs Anthropic credentials."""
    import anthropic

    client = anthropic.Anthropic()

    def write(batch: list[dict[str, Any]]) -> dict[str, str]:
        payload = json.dumps(
            {"model": model, "system": NOTE_SYSTEM, "records": batch}, sort_keys=True
        )
        key = hashlib.sha256(payload.encode()).hexdigest()
        path = cache_dir / f"{key}.json"
        if path.exists():
            return dict(json.loads(path.read_text(encoding="utf-8")))
        response = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=NOTE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(batch, indent=1)}],
            output_format=NoteBatch,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"note batch refused: {response.stop_details}")
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"note batch did not parse (stop_reason={response.stop_reason})")
        notes = {n.id: n.note.strip() for n in parsed.notes}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(notes, indent=1, sort_keys=True), encoding="utf-8")
        return notes

    return write


def write_notes(
    records: Sequence[RepairRecord], campus: Campus, writer: NoteWriter, batch_size: int = 25
) -> list[RepairRecord]:
    out: list[RepairRecord] = []
    for i in range(0, len(records), batch_size):
        chunk = records[i : i + batch_size]
        notes = writer([note_input(r, campus) for r in chunk])
        missing = [r.id for r in chunk if not notes.get(r.id)]
        if missing:
            raise RuntimeError(f"no note returned for {missing}")
        out.extend(r.model_copy(update={"note_raw": notes[r.id]}) for r in chunk)
    return out


# ---- files and database ----------------------------------------------------------------


def write_jsonl(records: Sequence[RepairRecord], path: Path) -> None:
    path.write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")


def read_jsonl(path: Path) -> list[RepairRecord]:
    return [
        RepairRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def review_sample(records: Sequence[RepairRecord], n: int = 30, seed: int = 7) -> str:
    rows = random.Random(seed).sample(list(records), min(n, len(records)))
    lines = [
        "# Repair history review sample (simulation)",
        "",
        f"{len(rows)} of {len(records)} records, chosen at random. Check that each note matches",
        "its cause and action and adds nothing. Mark problems in the last column.",
        "",
        "| asset | symptom | cause | action | note | ok? |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: r.completed_at):
        cells = [
            r.asset_id,
            r.symptom,
            r.confirmed_cause,
            r.action,
            r.note_raw or "(no note yet)",
            "",
        ]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def load_into_db(conn: Connection, records: Sequence[RepairRecord]) -> int:
    """Insert history rows. Refuses if any are already present."""
    ids = [uuid.UUID(r.id) for r in records]
    existing = conn.execute(
        select(func.count()).select_from(Repair).where(Repair.id.in_(ids))
    ).scalar_one()
    if existing:
        raise RuntimeError(
            f"{existing} history rows already in repairs; load into a fresh database"
        )
    if any(r.note_raw is None for r in records):
        raise RuntimeError("notes missing; run the notes step first")
    conn.execute(
        insert(Repair),
        [
            {
                "id": uuid.UUID(r.id),
                "job_id": None,
                "asset_id": r.asset_id,
                "symptom": r.symptom,
                "confirmed_cause": r.confirmed_cause,
                "action": r.action,
                "parts": r.parts,
                "minutes": r.minutes,
                "note_raw": r.note_raw,
                "completed_at": r.completed_at,
            }
            for r in records
        ],
    )
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulated repair history")
    parser.add_argument("step", choices=["skeleton", "notes", "sample", "load"])
    parser.add_argument("--data-dir", default="campus_data", type=Path)
    args = parser.parse_args(argv)
    out = args.data_dir / "repair_history.jsonl"
    campus = load_campus(args.data_dir)

    if args.step == "skeleton":
        records = skeleton(load_patterns(args.data_dir / "failure_patterns.yaml"), campus)
        write_jsonl(records, out)
        print(f"wrote {len(records)} records to {out}")
    elif args.step == "notes":
        model = os.environ.get("REPAIR_HISTORY_MODEL", "claude-opus-5-5")
        writer = claude_note_writer(Path("data/cache/llm/repair-notes"), model)
        records = write_notes(read_jsonl(out), campus, writer)
        write_jsonl(records, out)
        print(f"wrote notes for {len(records)} records")
    elif args.step == "sample":
        path = Path("docs/review/repair-history-sample.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(review_sample(read_jsonl(out)), encoding="utf-8")
        print(f"wrote {path}")
    else:
        with create_engine(get_settings().database_url).begin() as conn:
            print(f"inserted {load_into_db(conn, read_jsonl(out))} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
