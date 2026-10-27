"""San Jose 311 Service Request Data (data.sanjoseca.gov, CC0).

One CSV per year from the dataset page's download links. robots.txt disallows /api/ and
/datastore/, so the CKAN API is not used. The files have no free text, no close date and
no duplicate field; they are used for category and timing structure only.

    uv run python -m data311.sj 2024 2025
"""

import argparse
import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from data311.http import PoliteClient

HOST = "data.sanjoseca.gov"
DATASET_ID = "5bd6605f-43fc-4ccc-b80b-3c15b5a02319"
CACHE = Path("data/raw/sj311")

# Resource IDs from https://data.sanjoseca.gov/dataset/311-service-request-data (2026-10-27).
RESOURCES = {
    2017: "6399d341-880c-49eb-8cb6-5fb294bf48dc",
    2018: "d4746f5f-fe13-440b-8488-0d72ca8662c7",
    2019: "c5c5034a-b238-440c-84b5-5654799ca159",
    2020: "850c5060-ccf6-4515-82d6-c582dcd8e546",
    2021: "4b0563f3-ed85-4889-8c39-05f160dbe0eb",
    2022: "0f26e09e-9e1f-4305-9d2d-fb5d8292ae8a",
    2023: "00b1c1ba-b83e-40a0-8260-e634c57e4fcc",
    2024: "ac55d3cd-b4fe-4f96-bf12-4ce3191c8bd4",
    2025: "761d15cc-0e89-46f4-a35b-0e586d746edf",
    2026: "d886727c-60f1-4be7-9a30-f6806375b1a3",
}

COLUMNS = (
    "Incident_ID",
    "Status",
    "Source",
    "Category",
    "Service Type",
    "Latitude",
    "Longitude",
    "Date Created",
    "Date Last Updated",
    "Department",
)


def download_url(year: int) -> str:
    return (
        f"https://{HOST}/dataset/{DATASET_ID}/resource/{RESOURCES[year]}"
        f"/download/311-service-requests-{year}.csv"
    )


def download_year(client: PoliteClient, year: int) -> Path:
    return client.fetch(download_url(year), CACHE / f"311-service-requests-{year}.csv")


def parse_time(value: str) -> datetime | None:
    """'1/1/2025 12:04:07 AM', local time, naive."""
    return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p") if value else None


def read_year(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: unexpected header {reader.fieldnames}")
        yield from reader


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="San Jose 311 downloader")
    parser.add_argument("years", nargs="+", type=int, choices=sorted(RESOURCES))
    args = parser.parse_args(argv)
    with PoliteClient(cache_dir=Path("data/raw"), min_delay={HOST: 10.0}) as client:
        for year in args.years:
            print(download_year(client, year))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
