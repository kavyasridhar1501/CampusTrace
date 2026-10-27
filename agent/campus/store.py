"""Load the campus into Postgres (assets, asset_edges, rooms, crews) and query it.

    uv run python -m agent.campus.store            # load campus_data/ into DATABASE_URL

Loading is an upsert: rows are inserted or updated, never deleted. Rows in the database
that are no longer in the YAML are reported, not removed.
"""

import argparse
from collections.abc import Sequence
from datetime import date, time
from typing import Any

from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert

from agent.campus.graph import DOMAIN_EDGES, Domain, Hit
from agent.campus.loader import Campus, load_campus
from agent.config import get_settings
from agent.db import Asset, AssetEdge, Crew, Room


def _jsonable(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, date) else v for k, v in attrs.items()}


def asset_rows(campus: Campus) -> list[dict[str, Any]]:
    rows = []
    for node, d in campus.graph.nodes(data=True):
        attrs: dict[str, Any] = dict(d.get("attributes") or {})
        if d.get("last_service_date") is not None:
            attrs["last_service_date"] = d["last_service_date"]
        if d.get("located_in") is not None:
            attrs["located_in"] = d["located_in"]
        if d["type"] == "floor":
            attrs["level"] = d["level"]
        rows.append(
            {
                "id": node,
                "type": d["type"],
                "building": d["building"],
                "name": d["name"],
                "install_year": d.get("install_year"),
                "criticality": d.get("criticality", 3),
                "attributes": _jsonable(attrs),
            }
        )
    return rows


def load_into_db(conn: Connection, campus: Campus) -> dict[str, int]:
    g = campus.graph
    assets = asset_rows(campus)
    stmt = insert(Asset).values(assets)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[Asset.id],
            set_={
                c: stmt.excluded[c]
                for c in ("type", "building", "name", "install_year", "criticality", "attributes")
            },
        )
    )

    rooms = [
        {
            "asset_id": n,
            "aliases": d["aliases"],
            "capacity": d["capacity"],
            "room_type": d["room_type"],
        }
        for n, d in g.nodes(data=True)
        if d["type"] == "room"
    ]
    stmt = insert(Room).values(rooms)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[Room.asset_id],
            set_={c: stmt.excluded[c] for c in ("aliases", "capacity", "room_type")},
        )
    )

    edges = [
        {"from_id": u, "to_id": v, "edge_type": d["edge_type"]} for u, v, d in g.edges(data=True)
    ]
    conn.execute(insert(AssetEdge).values(edges).on_conflict_do_nothing())

    crews = [
        {
            "id": c.id,
            "name": c.name,
            "trades": list(c.trades),
            "shift_days": list(c.shift.days),
            "shift_start": time.fromisoformat(c.shift.start),
            "shift_end": time.fromisoformat(c.shift.end),
            "home_building": c.home_building,
        }
        for c in campus.crews.crews
    ]
    stmt = insert(Crew).values(crews)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[Crew.id],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "name",
                    "trades",
                    "shift_days",
                    "shift_start",
                    "shift_end",
                    "home_building",
                )
            },
        )
    )

    in_yaml = {a["id"] for a in assets}
    in_db = set(conn.execute(select(Asset.id)).scalars())
    return {
        "assets": len(assets),
        "rooms": len(rooms),
        "edges": len(edges),
        "crews": len(crews),
        "stale_assets": len(in_db - in_yaml),
    }


_UPSTREAM_SQL = text(
    """
    WITH RECURSIVE up(id, depth) AS (
        SELECT start_id, 0 FROM unnest(CAST(:starts AS text[])) AS start_id
        UNION
        SELECT e.to_id, up.depth + 1
        FROM up
        JOIN asset_edges e ON e.from_id = up.id AND e.edge_type = 'served_by'
        JOIN assets f ON f.id = e.from_id
        JOIN assets t ON t.id = e.to_id
        WHERE f.type || '>' || t.type = ANY(CAST(:pairs AS text[]))
    )
    SELECT up.id, min(up.depth) AS depth, a.type
    FROM up JOIN assets a ON a.id = up.id
    WHERE up.id <> ALL(CAST(:starts AS text[]))
    GROUP BY up.id, a.type
    ORDER BY depth, up.id
    """
)


def upstream_sql(conn: Connection, node: str, domain: Domain) -> list[Hit]:
    starts: Sequence[str] = [node]
    if domain == "elevator":
        floor = conn.execute(
            text(
                "SELECT e.from_id FROM asset_edges e JOIN assets a ON a.id = :n"
                " WHERE e.to_id = :n AND e.edge_type = 'contains' AND a.type = 'room'"
            ),
            {"n": node},
        ).scalar_one_or_none()
        if floor is not None:
            starts = [floor]
    pairs = [f"{a}>{b}" for a, b in DOMAIN_EDGES[domain]]
    rows = conn.execute(_UPSTREAM_SQL, {"starts": list(starts), "pairs": pairs}).all()
    return [Hit(int(depth), asset_id, type_) for asset_id, depth, type_ in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load campus_data/ into Postgres")
    parser.add_argument("data_dir", nargs="?", default="campus_data")
    args = parser.parse_args(argv)
    campus = load_campus(args.data_dir)
    engine = create_engine(get_settings().database_url)
    with engine.begin() as conn:
        counts = load_into_db(conn, campus)
    print(", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
