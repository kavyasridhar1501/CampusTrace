"""Check campus_data/ end to end and print a summary.

    uv run python -m agent.campus.validate                 # validate campus_data/
    uv run python -m agent.campus.validate --write-schemas # regenerate JSON Schemas

Exit code 0 means every check passed. The checks that matter most for root cause:
every room has an HVAC zone directly upstream that leads to an air handler and a plant,
and every room reaches an electrical panel through a circuit.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import networkx as nx

from agent.campus.loader import Campus, Issue, build_graph, parse_files
from agent.campus.schema import SCHEMA_MODELS

# Which node types may be joined by each edge type, as (from_type, to_type).
ALLOWED_EDGES: dict[str, set[tuple[str, str]]] = {
    "contains": {("building", "floor"), ("floor", "room")},
    "served_by": {
        ("room", "hvac_zone"),
        ("room", "circuit"),
        ("room", "plumbing_riser"),
        ("floor", "elevator_bank"),
        ("hvac_zone", "air_handler"),
        ("air_handler", "plant"),
        ("air_handler", "panel"),
        ("plant", "panel"),
        ("circuit", "panel"),
        ("panel", "panel"),
        ("plumbing_riser", "water_heater"),
        ("water_heater", "panel"),
        ("water_heater", "plant"),
        ("elevator_bank", "panel"),
    },
    "part_of": {("elevator", "elevator_bank")},
}


def _served_by(g: nx.DiGraph) -> nx.DiGraph:
    return g.edge_subgraph(
        (u, v) for u, v, d in g.edges(data=True) if d["edge_type"] == "served_by"
    )


def _up(g: nx.DiGraph, node: str, node_type: str) -> list[str]:
    """Direct served_by providers of node with the given type."""
    return [
        v
        for v in g.successors(node)
        if g.edges[node, v]["edge_type"] == "served_by" and g.nodes[v]["type"] == node_type
    ]


def _down(g: nx.DiGraph, node: str, edge_type: str = "served_by") -> list[str]:
    return [u for u in g.predecessors(node) if g.edges[u, node]["edge_type"] == edge_type]


def _of_type(g: nx.DiGraph, node_type: str) -> Iterable[str]:
    return (n for n, d in g.nodes(data=True) if d["type"] == node_type)


def check_graph(campus: Campus) -> list[Issue]:
    g = campus.graph
    issues: list[Issue] = []

    for u, v, d in g.edges(data=True):
        pair = (g.nodes[u]["type"], g.nodes[v]["type"])
        if pair not in ALLOWED_EDGES[d["edge_type"]]:
            issues.append(Issue(u, f"{d['edge_type']} edge to {v} not allowed for {pair}"))

    sb = _served_by(g)
    if not nx.is_directed_acyclic_graph(sb):
        cycle = nx.find_cycle(sb)
        issues.append(Issue("served_by", f"cycle: {cycle}"))

    for node, data in g.nodes(data=True):
        loc = data.get("located_in")
        if loc is not None and loc in g and g.nodes[loc]["type"] != "room":
            issues.append(Issue(node, f"located_in {loc} is not a room"))

    # Every room: HVAC zone -> air handler -> plant, and circuit -> panel.
    for room in _of_type(g, "room"):
        zones = _up(g, room, "hvac_zone")
        if not zones:
            issues.append(Issue(room, "no upstream HVAC zone"))
        elif not any(_up(g, z, "air_handler") for z in zones):
            issues.append(Issue(room, "HVAC zone has no air handler"))
        circuits = _up(g, room, "circuit")
        if not circuits:
            issues.append(Issue(room, "no upstream circuit"))
        elif not any(_up(g, c, "panel") for c in circuits):
            issues.append(Issue(room, "circuit has no electrical panel"))

    for zone in _of_type(g, "hvac_zone"):
        if not _up(g, zone, "air_handler"):
            issues.append(Issue(zone, "HVAC zone has no air handler"))
        if not _down(g, zone):
            issues.append(Issue(zone, "HVAC zone serves no rooms"))
    for ahu in _of_type(g, "air_handler"):
        if not _up(g, ahu, "plant"):
            issues.append(Issue(ahu, "air handler has no plant (boiler or chiller)"))
        if not _up(g, ahu, "panel"):
            issues.append(Issue(ahu, "air handler has no electrical panel"))
        if not _down(g, ahu):
            issues.append(Issue(ahu, "air handler serves nothing"))
    for plant in _of_type(g, "plant"):
        if g.nodes[plant]["attributes"].get("kind") not in {"boiler", "chiller"}:
            issues.append(Issue(plant, "plant needs attributes.kind of boiler or chiller"))
    for circuit in _of_type(g, "circuit"):
        if not _up(g, circuit, "panel"):
            issues.append(Issue(circuit, "circuit has no panel"))
        if not _down(g, circuit):
            issues.append(Issue(circuit, "circuit serves no rooms"))
    for panel in _of_type(g, "panel"):
        is_main = g.nodes[panel]["attributes"].get("role") == "main"
        if not is_main and not _up(g, panel, "panel"):
            issues.append(
                Issue(panel, "panel has no upstream panel (mark the main with role: main)")
            )
    for elevator in _of_type(g, "elevator"):
        if not any(g.edges[elevator, v]["edge_type"] == "part_of" for v in g.successors(elevator)):
            issues.append(Issue(elevator, "elevator is not part_of a bank"))
    for bank in _of_type(g, "elevator_bank"):
        if not _down(g, bank):
            issues.append(Issue(bank, "elevator bank serves no floors"))
        if not _down(g, bank, "part_of"):
            issues.append(Issue(bank, "elevator bank has no elevators"))
        if not _up(g, bank, "panel"):
            issues.append(Issue(bank, "elevator bank has no panel"))
    for riser in _of_type(g, "plumbing_riser"):
        if not _up(g, riser, "water_heater"):
            issues.append(Issue(riser, "plumbing riser has no water heater"))
        if not _down(g, riser):
            issues.append(Issue(riser, "plumbing riser serves no rooms"))

    return issues


def check_campus_file(campus: Campus) -> list[Issue]:
    issues: list[Issue] = []
    listed = set(campus.campus.buildings)
    if listed != set(campus.buildings):
        issues.append(
            Issue("campus.yaml", f"buildings {sorted(listed)} != files {sorted(campus.buildings)}")
        )
    if listed != set(campus.assets):
        issues.append(
            Issue("campus.yaml", f"buildings {sorted(listed)} != assets {sorted(campus.assets)}")
        )
    walk = campus.campus.walking_minutes
    for a in listed:
        for b in listed:
            minutes = walk.get(a, {}).get(b)
            if minutes is None:
                issues.append(Issue("campus.yaml", f"walking_minutes missing {a} -> {b}"))
            elif a == b and minutes != 0:
                issues.append(Issue("campus.yaml", f"walking_minutes {a} -> {a} must be 0"))
            elif a != b and minutes <= 0:
                issues.append(Issue("campus.yaml", f"walking_minutes {a} -> {b} must be > 0"))
            elif walk.get(b, {}).get(a) != minutes:
                issues.append(Issue("campus.yaml", f"walking_minutes {a} <-> {b} not symmetric"))
    return issues


def check_buildings(campus: Campus) -> list[Issue]:
    issues: list[Issue] = []
    for code, b in campus.buildings.items():
        labels = [f.label for f in b.floors]
        levels = [f.level for f in b.floors]
        if len(set(labels)) != len(labels) or len(set(levels)) != len(levels):
            issues.append(Issue(f"buildings/{code}", "floor labels and levels must be unique"))
        for fl in b.floors:
            for r in fl.rooms:
                expected = "B" if fl.label == "B" else fl.label
                if not r.number.startswith(expected):
                    issues.append(
                        Issue(
                            f"buildings/{code}",
                            f"room {r.number} is not numbered for floor {fl.label}",
                        )
                    )
    for code, af in campus.assets.items():
        for a in af.assets:
            if not a.id.startswith(f"{code}-"):
                issues.append(Issue(f"assets/{code}", f"{a.id} must start with {code}-"))
    return issues


def check_crews(campus: Campus) -> list[Issue]:
    issues: list[Issue] = []
    ids = [c.id for c in campus.crews.crews]
    for dup in [i for i, n in Counter(ids).items() if n > 1]:
        issues.append(Issue("crews.yaml", f"duplicate crew ID {dup}"))
    for c in campus.crews.crews:
        if c.home_building not in campus.buildings:
            issues.append(Issue("crews.yaml", f"{c.id} home_building {c.home_building} unknown"))
    covered = {t for c in campus.crews.crews for t in c.trades}
    for trade in ("plumbing", "electrical", "hvac", "structural", "custodial", "grounds"):
        if trade not in covered:
            issues.append(Issue("crews.yaml", f"no technician covers {trade}"))
    return issues


def validate(data_dir: Path) -> tuple[Campus | None, list[Issue]]:
    campus, issues = parse_files(data_dir)
    if campus is None:
        return None, issues
    issues = build_graph(campus)
    issues += check_campus_file(campus)
    issues += check_buildings(campus)
    issues += check_graph(campus)
    issues += check_crews(campus)
    return campus, issues


def summary(campus: Campus) -> str:
    g = campus.graph
    types = Counter(d["type"] for _, d in g.nodes(data=True))
    edges = Counter(d["edge_type"] for _, _, d in g.edges(data=True))
    lines = [f"{campus.campus.name}: {len(campus.buildings)} buildings, {types['room']} rooms"]
    for code in campus.campus.buildings:
        n = sum(1 for _, d in g.nodes(data=True) if d["type"] == "room" and d["building"] == code)
        lines.append(f"  {code:5} {n:4} rooms  {campus.buildings[code].name}")
    lines.append("nodes: " + ", ".join(f"{t} {n}" for t, n in sorted(types.items())))
    lines.append("edges: " + ", ".join(f"{t} {n}" for t, n in sorted(edges.items())))
    lines.append(f"crews: {len(campus.crews.crews)} technicians")
    return "\n".join(lines)


def write_schemas(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema()
        text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        (out_dir / f"{name}.schema.json").write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", nargs="?", default="campus_data", type=Path)
    parser.add_argument("--write-schemas", action="store_true")
    args = parser.parse_args(argv)

    if args.write_schemas:
        write_schemas(args.data_dir / "schemas")
        print(f"wrote JSON Schemas to {args.data_dir / 'schemas'}")
        return 0

    campus, issues = validate(args.data_dir)
    for issue in issues:
        print(f"ERROR {issue}", file=sys.stderr)
    if campus is not None:
        print(summary(campus))
    if issues:
        print(f"{len(issues)} problem(s) found", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
