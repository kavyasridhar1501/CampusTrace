"""The portal client's rules, and the 311 parsing helpers. No network: httpx.MockTransport."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from data311 import sf, sj
from data311.duplicates import Case, build_candidates, resolve_original, summarise
from data311.http import Blocked, ContactEmailMissing, Disallowed, PoliteClient, user_agent
from data311.trades import TradeMap, approved_sf_categories, load_trade_map, sf_trade

ROBOTS = "User-agent: *\nCrawl-delay: 2\nDisallow: /api/\n"


class Recorder:
    def __init__(self, routes: dict[str, httpx.Response]):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes.get(request.url.path, httpx.Response(404))


def make_client(
    tmp_path: Path, routes: dict[str, httpx.Response], **kw: object
) -> tuple[PoliteClient, Recorder, list[float]]:
    rec = Recorder({"/robots.txt": httpx.Response(200, text=ROBOTS), **routes})
    sleeps: list[float] = []
    now = [0.0]
    client = PoliteClient(
        cache_dir=tmp_path,
        contact_email=kw.pop("contact_email", "someone@example.org"),  # type: ignore[arg-type]
        transport=httpx.MockTransport(rec),
        sleep=sleeps.append,
        clock=lambda: now[0],
        **kw,  # type: ignore[arg-type]
    )
    return client, rec, sleeps


@pytest.mark.parametrize("email", [None, "", "not-an-address"])
def test_refuses_without_contact_email(
    tmp_path: Path, email: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    from agent.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ContactEmailMissing):
        PoliteClient(
            cache_dir=tmp_path,
            contact_email=email,
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        )
    get_settings.cache_clear()


def test_user_agent_names_project_and_contact() -> None:
    assert user_agent("a@b.org") == "CampusTrace/0.1 (portfolio research project; contact: a@b.org)"


def test_request_carries_user_agent_and_is_cached(tmp_path: Path) -> None:
    client, rec, _ = make_client(tmp_path, {"/data.csv": httpx.Response(200, text="a,b\n1,2\n")})
    dest = tmp_path / "out" / "data.csv"
    client.fetch("https://example.org/data.csv", dest)
    client.fetch("https://example.org/data.csv", dest)
    paths = [r.url.path for r in rec.requests]
    assert paths == ["/robots.txt", "/data.csv"]
    assert all("someone@example.org" in r.headers["user-agent"] for r in rec.requests)
    meta = json.loads((tmp_path / "out" / "data.csv.meta.json").read_text())
    assert meta["bytes"] == len("a,b\n1,2\n")
    assert meta["user_agent"].startswith("CampusTrace/0.1")


def test_robots_disallow_stops_the_request(tmp_path: Path) -> None:
    client, rec, _ = make_client(tmp_path, {"/api/3/action/x": httpx.Response(200)})
    with pytest.raises(Disallowed):
        client.fetch("https://example.org/api/3/action/x", tmp_path / "x.json")
    assert [r.url.path for r in rec.requests] == ["/robots.txt"]


def test_crawl_delay_is_respected(tmp_path: Path) -> None:
    client, _, sleeps = make_client(
        tmp_path, {"/a": httpx.Response(200, text="a"), "/b": httpx.Response(200, text="b")}
    )
    client.fetch("https://example.org/a", tmp_path / "a")
    client.fetch("https://example.org/b", tmp_path / "b")
    # robots.txt is fetched first; /a and /b each then wait its Crawl-delay of 2 s,
    # which is longer than the 1 s default minimum.
    assert sleeps == [2.0, 2.0]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_refusal_raises_and_writes_nothing(tmp_path: Path, status: int) -> None:
    client, _, _ = make_client(tmp_path, {"/data.csv": httpx.Response(status)})
    dest = tmp_path / "data.csv"
    with pytest.raises(Blocked):
        client.fetch("https://example.org/data.csv", dest)
    assert not dest.exists()


def test_real_portal_rules_allow_our_paths_only(tmp_path: Path) -> None:
    """The robots.txt texts read on 2026-10-27, trimmed to the rules that matter."""
    sj_robots = "User-agent: *\nDisallow: /api/\nDisallow: /datastore/*\nCrawl-delay: 10\n"
    rec = Recorder({"/robots.txt": httpx.Response(200, text=sj_robots)})
    client = PoliteClient(
        cache_dir=tmp_path, contact_email="a@b.org", transport=httpx.MockTransport(rec)
    )
    client.check_allowed(sj.download_url(2025))
    with pytest.raises(Disallowed):
        client.check_allowed(f"https://{sj.HOST}/api/3/action/package_show?id=x")


def test_sj_urls_and_time_format() -> None:
    assert sj.download_url(2025).endswith(
        "/resource/761d15cc-0e89-46f4-a35b-0e586d746edf/download/311-service-requests-2025.csv"
    )
    assert sj.parse_time("1/1/2025 12:04:07 AM") == datetime(2025, 1, 1, 0, 4, 7)


def test_sj_header_check(tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    good.write_text(",".join(f'"{c}"' for c in sj.COLUMNS) + "\n" + ",".join(['"1"'] * 10) + "\n")
    assert len(list(sj.read_year(good))) == 1
    bad = tmp_path / "bad.csv"
    bad.write_text('"Incident_ID","Description"\n"1","x"\n')
    with pytest.raises(ValueError, match="unexpected header"):
        list(sj.read_year(bad))


def test_sf_year_filter() -> None:
    assert sf.year_where(2024) == (
        "requested_datetime >= '2024-01-01T00:00:00' AND requested_datetime < '2025-01-01T00:00:00'"
    )


# ---- duplicate labels -----------------------------------------------------------------


def case(
    id_: str,
    hour: float,
    notes: str = "",
    lat: float = 37.77,
    lon: float = -122.42,
    cat: str = "Sewer",
) -> Case:
    return Case(id_, datetime(2025, 3, 1) + timedelta(hours=hour), cat, "", lat, lon, notes)


# Note forms copied from the 2025 sample of status_notes.
@pytest.mark.parametrize(
    ("notes", "is_dup", "refs"),
    [
        (
            "Case is a Duplicate - This issue has already been reported through an earlier"
            " request.",
            True,
            [],
        ),
        (
            "case_is_a_duplicate - Request is a duplicate and has been previously reported"
            " as 1001234474",
            True,
            ["1001234474"],
        ),
        (
            "Cancelled - Maximo Service Request has been cancelled. Memo:Duplicate of 1001244235",
            True,
            ["1001244235"],
        ),
        ("Case is a Duplicate - Same as reported in SR #101001244332", True, ["101001244332"]),
        ("not_accepted - *DUPLICATE COMPLAINT*", True, []),
        ("case_resolved - EAM Case 1011099 update", False, []),
    ],
)
def test_duplicate_markers(notes: str, is_dup: bool, refs: list[str]) -> None:
    c = case("1", 0, notes)
    assert c.is_duplicate is is_dup
    assert c.referenced_ids() == refs


def test_resolve_original_handles_prefixed_ids() -> None:
    orig = case("1001244332", 0)
    dup = case("1001244400", 5, "Case is a Duplicate - Same as reported in SR #101001244332")
    assert resolve_original(dup, {c.id: c for c in (orig, dup)}) == "1001244332"


def test_resolve_original_rejects_later_case() -> None:
    later = case("1001244500", 10)
    dup = case("1001244400", 5, "Duplicate of 1001244500")
    assert resolve_original(dup, {c.id: c for c in (later, dup)}) is None


def test_candidates_respect_category_radius_and_window() -> None:
    a = case("A", 0)
    b = case("B", 10, "Duplicate of A")  # same place, 10 h later
    far = case("C", 11, lat=37.80)  # ~3 km away
    late = case("D", 75)  # outside 72 h of A, within 72 h of B
    other = case("E", 12, cat="Streetlights")
    cands = build_candidates([a, b, far, late, other])
    pairs = {(c.later, c.earlier) for c in cands}
    assert ("B", "A") in pairs
    assert ("C", "A") not in pairs and ("C", "B") not in pairs
    assert ("D", "A") not in pairs and ("D", "B") in pairs
    assert not any("E" in p for p in pairs)


def test_summary_counts() -> None:
    a = case("1000001", 0)
    b = case("1000002", 1, "Duplicate of 1000001")
    c = case("1000003", 2, "Case is a Duplicate")
    s = summarise([a, b, c], build_candidates([a, b, c]))
    assert (s.cases, s.duplicates, s.with_reference, s.resolved, s.resolved_in_candidates) == (
        3,
        2,
        1,
        1,
        1,
    )


# ---- trade map -----------------------------------------------------------------------


def test_trade_map_starts_unapproved() -> None:
    assert load_trade_map().approved is False
    assert approved_sf_categories() == set()


def test_sf_trade_subtype_overrides() -> None:
    m = TradeMap.model_validate(
        {
            "approved": True,
            "sf": [
                {"service_name": "Sewer", "trade": "plumbing"},
                {"service_name": "Residential Building", "subtypes": {"no_heat": "hvac"}},
            ],
        }
    )
    assert sf_trade("Sewer", "anything", m) == "plumbing"
    assert sf_trade("Residential Building", "no_heat", m) == "hvac"
    assert sf_trade("Residential Building", "noise", m) is None
    assert sf_trade("Noise", "x", m) is None
