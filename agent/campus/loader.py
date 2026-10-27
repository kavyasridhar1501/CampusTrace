"""Load campus_data/ YAML into typed models and an in-memory asset graph.

The graph is a NetworkX DiGraph. Every building, floor, room and piece of equipment is a
node with attributes type, building, name, and (for rooms) room_type and capacity. Edges
carry edge_type, one of contains, served_by, part_of, and always point the way the plan
writes them: building -> floor -> room for contains, consumer -> provider for served_by
(room -> hvac_zone -> air_handler -> plant), member -> group for part_of.

Loading the same graph into Postgres (assets, asset_edges, rooms) is a separate step.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from pydantic import BaseModel, ValidationError

from agent.campus.schema import (
    AssetsFile,
    BuildingFile,
    CampusFile,
    CrewsFile,
)


@dataclass(frozen=True)
class Issue:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class Campus:
    campus: CampusFile
    buildings: dict[str, BuildingFile]
    assets: dict[str, AssetsFile]
    crews: CrewsFile
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)

    def rooms(self) -> list[str]:
        return [n for n, d in self.graph.nodes(data=True) if d["type"] == "room"]


class CampusDataError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


def floor_id(code: str, label: str) -> str:
    return f"{code}-F{label}"


def room_id(code: str, number: str) -> str:
    return f"{code}-{number}"


def _read(path: Path, model: type[BaseModel], issues: list[Issue]) -> Any:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        issues.append(Issue(str(path), f"cannot read YAML: {exc}"))
        return None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            issues.append(Issue(f"{path}:{loc}", err["msg"]))
        return None


def parse_files(data_dir: Path) -> tuple[Campus | None, list[Issue]]:
    """Parse and schema-check every file. Returns (campus, issues); campus is None if any
    file failed to parse, because the graph checks need all of them."""
    issues: list[Issue] = []
    campus_file = _read(data_dir / "campus.yaml", CampusFile, issues)
    crews = _read(data_dir / "crews.yaml", CrewsFile, issues)

    buildings: dict[str, BuildingFile] = {}
    for path in sorted((data_dir / "buildings").glob("*.yaml")):
        b = _read(path, BuildingFile, issues)
        if b is None:
            continue
        if b.code != path.stem:
            issues.append(Issue(str(path), f"code {b.code} does not match file name"))
        buildings[b.code] = b

    assets: dict[str, AssetsFile] = {}
    for path in sorted((data_dir / "assets").glob("*.yaml")):
        a = _read(path, AssetsFile, issues)
        if a is None:
            continue
        if a.building != path.stem:
            issues.append(Issue(str(path), f"building {a.building} does not match file name"))
        assets[a.building] = a

    if issues or campus_file is None or crews is None:
        return None, issues
    return Campus(campus=campus_file, buildings=buildings, assets=assets, crews=crews), issues


def build_graph(campus: Campus) -> list[Issue]:
    """Add nodes and edges to campus.graph. Returns problems that stop an edge or node
    from being added (duplicate IDs, dangling references). Type rules and coverage
    checks live in agent.campus.validate."""
    g = campus.graph
    issues: list[Issue] = []

    def add_node(node_id: str, where: str, **attrs: Any) -> None:
        if node_id in g:
            issues.append(Issue(where, f"duplicate ID {node_id}"))
            return
        g.add_node(node_id, **attrs)

    for code, b in campus.buildings.items():
        add_node(code, f"buildings/{code}", type="building", building=code, name=b.name)
        for fl in b.floors:
            fid = floor_id(code, fl.label)
            add_node(
                fid,
                f"buildings/{code}",
                type="floor",
                building=code,
                level=fl.level,
                name=f"{b.name}, floor {fl.label}",
            )
            g.add_edge(code, fid, edge_type="contains")
            for r in fl.rooms:
                rid = room_id(code, r.number)
                add_node(
                    rid,
                    f"buildings/{code}",
                    type="room",
                    building=code,
                    name=r.name or rid,
                    room_type=r.type,
                    capacity=r.capacity,
                    aliases=list(r.aliases),
                    floor=fid,
                )
                if rid in g and g.nodes[rid].get("floor") == fid:
                    g.add_edge(fid, rid, edge_type="contains")

    for code, af in campus.assets.items():
        for a in af.assets:
            add_node(
                a.id,
                f"assets/{code}",
                type=a.type,
                building=code,
                name=a.name,
                install_year=a.install_year,
                criticality=a.criticality,
                last_service_date=a.last_service_date,
                located_in=a.located_in,
                attributes=dict(a.attributes),
            )

    def add_edge(src: str, dst: str, edge_type: str, where: str) -> None:
        for end in (src, dst):
            if end not in g:
                issues.append(Issue(where, f"unknown ID {end}"))
                return
        if g.has_edge(src, dst):
            issues.append(Issue(where, f"duplicate edge {src} -> {dst}"))
            return
        g.add_edge(src, dst, edge_type=edge_type)

    for code, af in campus.assets.items():
        for a in af.assets:
            where = f"assets/{code}:{a.id}"
            for up in a.upstream:
                add_edge(a.id, up, "served_by", where)
            for consumer in a.serves:
                add_edge(consumer, a.id, "served_by", where)
            if a.part_of is not None:
                add_edge(a.id, a.part_of, "part_of", where)
            if a.located_in is not None and a.located_in not in g:
                issues.append(Issue(where, f"located_in {a.located_in} does not exist"))

    return issues


def load_campus(data_dir: str | Path) -> Campus:
    """Parse, build the graph, and raise CampusDataError on any structural problem.
    Use agent.campus.validate for the full set of checks."""
    campus, issues = parse_files(Path(data_dir))
    if campus is None:
        raise CampusDataError(issues)
    issues = build_graph(campus)
    if issues:
        raise CampusDataError(issues)
    return campus
