"""Upstream queries, in memory and in Postgres, and loading the campus into the database."""

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from agent.campus.graph import DOMAIN_EDGES, served_rooms, upstream
from agent.campus.loader import Campus, load_campus
from agent.campus.store import load_into_db, upstream_sql


@pytest.fixture(scope="module")
def campus(repo: Path) -> Campus:
    return load_campus(repo / "campus_data")


def ids(hits) -> list[str]:  # type: ignore[no-untyped-def]
    return [h.asset_id for h in hits]


def test_hvac_upstream_of_demo_room(campus: Campus) -> None:
    hits = upstream(campus.graph, "CSE-3204", "hvac")
    assert [(h.depth, h.asset_id) for h in hits] == [
        (1, "CSE-Z3S1"),
        (2, "CSE-AHU-3"),
        (3, "SSB-BLR-1"),
        (3, "SSB-CH-1"),
    ]


def test_electrical_upstream_reaches_main_switchboard(campus: Campus) -> None:
    hits = upstream(campus.graph, "CSE-3204", "electrical")
    types = [h.type for h in hits]
    assert types[0] == "circuit"
    assert ids(hits)[-1] == "CSE-MSB"
    assert "hvac_zone" not in types


def test_domains_do_not_leak(campus: Campus) -> None:
    for room in campus.rooms():
        for domain, allowed in DOMAIN_EDGES.items():
            reachable = {campus.graph.nodes[room]["type"]} | {t for pair in allowed for t in pair}
            for h in upstream(campus.graph, room, domain):
                assert h.type in reachable, (room, domain, h)


def test_elevator_walk_starts_at_the_floor(campus: Campus) -> None:
    hits = upstream(campus.graph, "CSE-3204", "elevator")
    assert ids(hits)[0] == "CSE-ELB-1"


def test_plumbing_only_for_plumbed_rooms(campus: Campus) -> None:
    assert ids(upstream(campus.graph, "CHEM-2108", "plumbing"))[1].startswith("CHEM-WH")
    assert upstream(campus.graph, "CSE-3204", "plumbing") == []


def test_served_rooms_inverts_upstream(campus: Campus) -> None:
    g = campus.graph
    for asset in ("CSE-AHU-3", "CSE-Z3S1", "SSB-CH-1", "LIB-PNL-2"):
        domain = "electrical" if g.nodes[asset]["type"] == "panel" else "hvac"
        rooms = served_rooms(g, asset, domain)
        expected = {r for r in campus.rooms() if asset in ids(upstream(g, r, domain))}
        assert rooms == expected and rooms
    assert {"CSE-3204", "CSE-3210", "CSE-3100H"} <= served_rooms(g, "CSE-AHU-3", "hvac")
    assert len(served_rooms(g, "CSE-ELB-1", "elevator")) == 81


# ---- Postgres --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def loaded(migrated_engine: Engine, campus: Campus) -> Engine:
    with migrated_engine.begin() as conn:
        load_into_db(conn, campus)
    return migrated_engine


@pytest.mark.db
def test_load_counts_and_idempotence(loaded: Engine, campus: Campus) -> None:
    with loaded.begin() as conn:
        counts = load_into_db(conn, campus)
        assert counts["assets"] == campus.graph.number_of_nodes()
        assert counts["edges"] == campus.graph.number_of_edges()
        assert counts["rooms"] == 303
        assert counts["crews"] == 8
        assert counts["stale_assets"] == 0
        n_assets = conn.execute(text("SELECT count(*) FROM assets")).scalar_one()
        n_edges = conn.execute(text("SELECT count(*) FROM asset_edges")).scalar_one()
    assert n_assets == campus.graph.number_of_nodes()
    assert n_edges == campus.graph.number_of_edges()


@pytest.mark.db
def test_attributes_survive_the_load(loaded: Engine) -> None:
    with loaded.connect() as conn:
        attrs = conn.execute(
            text("SELECT attributes FROM assets WHERE id = 'SSB-BLR-1'")
        ).scalar_one()
        room = conn.execute(
            text("SELECT room_type, capacity, aliases FROM rooms WHERE asset_id = 'CSE-B140'")
        ).one()
    assert attrs["kind"] == "boiler"
    assert "last_service_date" in attrs
    assert room.room_type == "computer_lab"
    assert "the CSE basement lab" in room.aliases


@pytest.mark.db
@pytest.mark.parametrize("domain", ["hvac", "electrical", "plumbing", "elevator"])
def test_sql_upstream_matches_networkx(loaded: Engine, campus: Campus, domain: str) -> None:
    with loaded.connect() as conn:
        for room in campus.rooms():
            assert upstream_sql(conn, room, domain) == upstream(campus.graph, room, domain), room  # type: ignore[arg-type]
