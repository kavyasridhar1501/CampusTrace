"""campus_data/ loads into the asset graph and passes every structural check, and the
validator catches the mistakes it is meant to catch."""

import json
import shutil
from pathlib import Path

import networkx as nx
import pytest
import yaml

from agent.campus.loader import CampusDataError, load_campus
from agent.campus.schema import SCHEMA_MODELS
from agent.campus.validate import main, validate


@pytest.fixture(scope="module")
def campus(repo: Path):  # type: ignore[no-untyped-def]
    return load_campus(repo / "campus_data")


def test_campus_data_is_valid(repo: Path) -> None:
    campus, issues = validate(repo / "campus_data")
    assert campus is not None
    assert [str(i) for i in issues] == []


def test_six_buildings_about_300_rooms(campus) -> None:  # type: ignore[no-untyped-def]
    assert len(campus.buildings) == 6
    assert 280 <= len(campus.rooms()) <= 320


def test_every_room_has_hvac_zone_and_panel(campus) -> None:  # type: ignore[no-untyped-def]
    g = campus.graph
    for room in campus.rooms():
        upstream = nx.descendants(g, room)
        types = {g.nodes[n]["type"] for n in upstream}
        assert {"hvac_zone", "air_handler", "plant", "circuit", "panel"} <= types, room


def test_demo_rooms_share_only_the_air_handler(campus) -> None:  # type: ignore[no-untyped-def]
    """The plan's opening demo: 3204 freezing, 3210 too hot, third-floor hallway musty.
    The three rooms are in different zones and meet at one air handler."""
    g = campus.graph

    def hvac_up(room: str) -> set[str]:
        sb = g.edge_subgraph(
            (u, v) for u, v, d in g.edges(data=True) if d["edge_type"] == "served_by"
        )
        return {
            n
            for n in nx.descendants(sb, room)
            if g.nodes[n]["type"] in {"hvac_zone", "air_handler"}
        }

    shared = hvac_up("CSE-3204") & hvac_up("CSE-3210") & hvac_up("CSE-3100H")
    assert shared == {"CSE-AHU-3"}


def test_json_schemas_match_models(repo: Path) -> None:
    for name, model in SCHEMA_MODELS.items():
        path = repo / "campus_data" / "schemas" / f"{name}.schema.json"
        expected = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        assert path.read_text(encoding="utf-8") == expected, f"regenerate {path.name}"


def test_crews_cover_every_trade(campus) -> None:  # type: ignore[no-untyped-def]
    assert len(campus.crews.crews) == 8
    trades = {t for c in campus.crews.crews for t in c.trades}
    assert trades == {"plumbing", "electrical", "hvac", "structural", "custodial", "grounds"}


# ---- the validator catches broken data -------------------------------------------------


@pytest.fixture
def data(tmp_path: Path, repo: Path) -> Path:
    dst = tmp_path / "campus_data"
    shutil.copytree(repo / "campus_data", dst)
    return dst


def _edit_assets(data: Path, code: str, fn) -> None:  # type: ignore[no-untyped-def]
    path = data / "assets" / f"{code}.yaml"
    doc = yaml.safe_load(path.read_text())
    fn({a["id"]: a for a in doc["assets"]}, doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def _messages(data: Path) -> list[str]:
    _, issues = validate(data)
    return [str(i) for i in issues]


def test_room_without_zone_is_caught(data: Path) -> None:
    def drop(assets, doc):  # type: ignore[no-untyped-def]
        assets["CSE-Z3S1"]["serves"].remove("CSE-3204")

    _edit_assets(data, "CSE", drop)
    assert "CSE-3204: no upstream HVAC zone" in _messages(data)


def test_room_without_circuit_is_caught(data: Path) -> None:
    def drop(assets, doc):  # type: ignore[no-untyped-def]
        for a in assets.values():
            if a["type"] == "circuit" and "LIB-1010" in a.get("serves", []):
                a["serves"].remove("LIB-1010")

    _edit_assets(data, "LIB", drop)
    assert "LIB-1010: no upstream circuit" in _messages(data)


def test_zone_without_air_handler_is_caught(data: Path) -> None:
    def drop(assets, doc):  # type: ignore[no-untyped-def]
        assets["SSB-Z2N1"]["upstream"] = []

    _edit_assets(data, "SSB", drop)
    msgs = _messages(data)
    assert "SSB-Z2N1: HVAC zone has no air handler" in msgs
    assert any(m.startswith("SSB-2001: HVAC zone has no air handler") for m in msgs)


def test_dangling_reference_is_caught(data: Path) -> None:
    def bad(assets, doc):  # type: ignore[no-untyped-def]
        assets["CSE-AHU-3"]["upstream"].append("CSE-NOPE-1")

    _edit_assets(data, "CSE", bad)
    assert any("unknown ID CSE-NOPE-1" in m for m in _messages(data))


def test_disallowed_edge_type_is_caught(data: Path) -> None:
    def bad(assets, doc):  # type: ignore[no-untyped-def]
        assets["CSE-Z3S1"]["upstream"].append("SSB-CH-1")

    _edit_assets(data, "CSE", bad)
    assert any("not allowed for ('hvac_zone', 'plant')" in m for m in _messages(data))


def test_cycle_is_caught(data: Path) -> None:
    def bad(assets, doc):  # type: ignore[no-untyped-def]
        assets["CSE-MSB"]["upstream"] = ["CSE-PNL-M"]

    _edit_assets(data, "CSE", bad)
    assert any(m.startswith("served_by: cycle") for m in _messages(data))


def test_duplicate_id_is_caught(data: Path) -> None:
    def dup(assets, doc):  # type: ignore[no-untyped-def]
        doc["assets"].append(dict(assets["CSE-WH-1"]))

    _edit_assets(data, "CSE", dup)
    assert any("duplicate ID CSE-WH-1" in m for m in _messages(data))


def test_unknown_field_is_a_schema_error(data: Path) -> None:
    path = data / "buildings" / "LEC.yaml"
    path.write_text(path.read_text().replace("year_built:", "yeer_built: 2005\nyear_built:", 1))
    msgs = _messages(data)
    assert any("yeer_built" in m and "Extra inputs are not permitted" in m for m in msgs)
    with pytest.raises(CampusDataError):
        load_campus(data)


def test_asymmetric_walking_time_is_caught(data: Path) -> None:
    path = data / "campus.yaml"
    path.write_text(path.read_text().replace("CSE:  {CSE: 0, CHEM: 4", "CSE:  {CSE: 0, CHEM: 9"))
    assert "campus.yaml: walking_minutes CSE <-> CHEM not symmetric" in _messages(data)


def test_cli_exit_codes(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(data)]) == 0
    assert "OK" in capsys.readouterr().out

    def drop(assets, doc):  # type: ignore[no-untyped-def]
        assets["CSE-Z3S1"]["serves"].remove("CSE-3204")

    _edit_assets(data, "CSE", drop)
    assert main([str(data)]) == 1
