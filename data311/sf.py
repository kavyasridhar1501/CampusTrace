"""San Francisco 311 Cases (dataset vw6y-z8j6 on data.sf.gov, PDDL 1.0).

Columns and their contents are documented in docs/data-sources.md. The public dataset
has no reporter free text; status_notes is written by city staff.

    uv run python -m data311.sf summary          # aggregate by category, 2024-2025
    uv run python -m data311.sf pull "Sewer"     # one approved category, 2024-2025

The full pull is limited to categories Kavya has approved in data311/trade_map.yaml.
"""

import argparse
import csv
import io
import re
from collections.abc import Iterator
from pathlib import Path

from agent.config import get_settings
from data311.http import PoliteClient

HOST = "data.sf.gov"
DATASET = "vw6y-z8j6"
RESOURCE = f"https://{HOST}/resource/{DATASET}"
YEARS = (2024, 2025)
PAGE = 50_000
CACHE = Path("data/raw/sf311")

FIELDS = (
    "service_request_id",
    "requested_datetime",
    "closed_date",
    "status_description",
    "status_notes",
    "agency_responsible",
    "service_name",
    "service_subtype",
    "service_details",
    "address",
    "lat",
    "long",
    "source",
)

# status_notes forms seen in the 2025 sample: "Case is a Duplicate", "case_is_a_duplicate",
# "Memo:Duplicate of 1001244235", "*DUPLICATE COMPLAINT*".
DUPLICATE_SQL = "upper(status_notes) like '%DUPLICATE%'"


def _headers() -> dict[str, str]:
    token = get_settings().socrata_app_token
    return {"X-App-Token": token.get_secret_value()} if token else {}


def year_where(year: int) -> str:
    return (
        f"requested_datetime >= '{year}-01-01T00:00:00' "
        f"AND requested_datetime < '{year + 1}-01-01T00:00:00'"
    )


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def category_summary(
    client: PoliteClient, years: tuple[int, ...] = YEARS
) -> list[dict[str, object]]:
    """Per year, category and subtype: case count, DUPLICATE-noted count, closed count.
    Three small aggregate queries per year. Time to close comes from the full pull."""
    out: dict[tuple[int, str, str], dict[str, object]] = {}
    for year in years:
        where = year_where(year)
        queries = {
            "n": ("count(*) as n", where),
            "dup": ("count(*) as n", f"{where} AND {DUPLICATE_SQL}"),
            "closed": ("count(*) as n", f"{where} AND closed_date IS NOT NULL"),
        }
        for key, (select, w) in queries.items():
            rows = client.fetch_json(
                f"{RESOURCE}.json",
                CACHE / "summary" / f"{year}-subtype-{key}.json",
                params={
                    "$select": f"service_name, service_subtype, {select}",
                    "$where": w,
                    "$group": "service_name, service_subtype",
                    "$limit": "5000",
                },
                headers=_headers(),
            )
            for r in rows:
                k = (year, r.get("service_name") or "", r.get("service_subtype") or "")
                out.setdefault(k, {"year": year, "service_name": k[1], "service_subtype": k[2]})
                out[k][key] = int(r["n"])
    return sorted(out.values(), key=lambda r: (r["year"], r["service_name"], r["service_subtype"]))  # type: ignore[return-value]


def pull_category(client: PoliteClient, category: str, year: int) -> Path:
    """Page one category's cases for one year into data/raw/sf311/<year>-<slug>/."""
    out_dir = CACHE / f"{year}-{slug(category)}"
    quoted = category.replace("'", "''")
    offset = 0
    while True:
        page = client.fetch(
            f"{RESOURCE}.csv",
            out_dir / f"page-{offset // PAGE:04d}.csv",
            params={
                "$select": ", ".join(FIELDS),
                "$where": f"{year_where(year)} AND service_name = '{quoted}'",
                "$order": "service_request_id",
                "$limit": str(PAGE),
                "$offset": str(offset),
            },
            headers=_headers(),
        )
        rows = sum(1 for _ in csv.reader(io.StringIO(page.read_text(encoding="utf-8")))) - 1
        if rows < PAGE:
            return out_dir
        offset += PAGE


def read_category(out_dir: Path) -> Iterator[dict[str, str]]:
    for page in sorted(out_dir.glob("page-*.csv")):
        with page.open(encoding="utf-8", newline="") as f:
            yield from csv.DictReader(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="San Francisco 311 loader")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    p = sub.add_parser("pull")
    p.add_argument("category")
    args = parser.parse_args(argv)

    with PoliteClient(cache_dir=Path("data/raw"), min_delay={HOST: 1.0}) as client:
        if args.cmd == "summary":
            rows = category_summary(client)
            print(f"{len(rows)} (year, category, subtype) rows")
        else:
            from data311.trades import approved_sf_categories

            if args.category not in approved_sf_categories():
                print(f"{args.category!r} is not approved in data311/trade_map.yaml")
                return 1
            for year in YEARS:
                print(pull_category(client, args.category, year))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
