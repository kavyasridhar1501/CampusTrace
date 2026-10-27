"""Upstream and downstream queries over the asset graph.

A symptom domain says which served_by edges a walk may follow. Temperature and air
symptoms walk the HVAC edges, power symptoms the electrical edges (as the plan says);
plumbing and elevator symptoms get their own domains.

Two implementations with the same answers: NetworkX over the in-memory graph
(agent.campus.loader), and recursive SQL over asset_edges (agent.campus.store).
tests/test_campus_store.py checks they agree for every room.
"""

from collections import deque
from dataclasses import dataclass
from typing import Literal

import networkx as nx

Domain = Literal["hvac", "electrical", "plumbing", "elevator"]

# served_by (from_type, to_type) pairs each domain may follow.
DOMAIN_EDGES: dict[Domain, frozenset[tuple[str, str]]] = {
    "hvac": frozenset(
        {("room", "hvac_zone"), ("hvac_zone", "air_handler"), ("air_handler", "plant")}
    ),
    "electrical": frozenset({("room", "circuit"), ("circuit", "panel"), ("panel", "panel")}),
    "plumbing": frozenset({("room", "plumbing_riser"), ("plumbing_riser", "water_heater")}),
    "elevator": frozenset(
        {("floor", "elevator_bank"), ("elevator_bank", "panel"), ("panel", "panel")}
    ),
}


@dataclass(frozen=True, order=True)
class Hit:
    depth: int
    asset_id: str
    type: str


def start_nodes(g: nx.DiGraph, node: str, domain: Domain) -> list[str]:
    """Elevator symptoms are reported from a room but the elevator bank serves floors,
    so the walk starts at the room's floor."""
    if domain == "elevator" and g.nodes[node]["type"] == "room":
        return [g.nodes[node]["floor"]]
    return [node]


def upstream(g: nx.DiGraph, node: str, domain: Domain) -> list[Hit]:
    """Every asset reachable from node along the domain's served_by edges, nearest first.
    Depth counts edges from node (from the floor, for elevator walks from a room)."""
    allowed = DOMAIN_EDGES[domain]
    seen: dict[str, int] = {}
    queue = deque((s, 0) for s in start_nodes(g, node, domain))
    starts = {s for s, _ in queue}
    while queue:
        current, depth = queue.popleft()
        ctype = g.nodes[current]["type"]
        for nxt in g.successors(current):
            if g.edges[current, nxt]["edge_type"] != "served_by":
                continue
            if (ctype, g.nodes[nxt]["type"]) not in allowed:
                continue
            if nxt in seen or nxt in starts:
                continue
            seen[nxt] = depth + 1
            queue.append((nxt, depth + 1))
    return sorted(Hit(d, a, g.nodes[a]["type"]) for a, d in seen.items())


def served_rooms(g: nx.DiGraph, asset: str, domain: Domain) -> set[str]:
    """Rooms whose upstream walk in this domain reaches asset."""
    allowed = DOMAIN_EDGES[domain]
    found: set[str] = set()
    stack = [asset]
    seen = {asset}
    while stack:
        current = stack.pop()
        ctype = g.nodes[current]["type"]
        for prev in g.predecessors(current):
            ptype = g.nodes[prev]["type"]
            if g.edges[prev, current]["edge_type"] != "served_by" or (ptype, ctype) not in allowed:
                continue
            if prev in seen:
                continue
            seen.add(prev)
            if ptype == "room":
                found.add(prev)
            elif ptype == "floor" and domain == "elevator":
                found.update(
                    r for r in g.successors(prev) if g.edges[prev, r]["edge_type"] == "contains"
                )
            stack.append(prev)
    return found
