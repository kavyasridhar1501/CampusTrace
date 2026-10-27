"""311 category to facility trade mapping.

data311/trade_map.yaml holds the mapping. It starts as a proposal; the SF full pull only
runs for categories listed there once Kavya sets `approved: true`.

The draft below (DRAFT_SF) is the starting point for the proposal table: categories
whose names point to a facilities trade. Categories mapped to None are mixed and get
split by subtype after the aggregate query. Nothing in the city data maps cleanly to
HVAC; the proposal says so rather than forcing a category into it.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Trade = Literal["plumbing", "electrical", "hvac", "structural", "custodial", "grounds"]
TRADE_MAP = Path(__file__).with_name("trade_map.yaml")

DRAFT_SF: dict[str, Trade | None] = {
    "Streetlights": "electrical",
    "Sewer": "plumbing",
    "Water Quality": "plumbing",
    "Waste of Water": "plumbing",
    "Residential Building": None,
    "Damage Property": "structural",
    "Sidewalk and Curb": "structural",
    "Street Defect": "structural",
    "Tree Maintenance": "grounds",
    "Litter Receptacle Maintenance": "custodial",
    "Street and Sidewalk Cleaning": "custodial",
    "Graffiti Public": "custodial",
    "Graffiti Private": "custodial",
}


class SfEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str
    trade: Trade | None = None
    # Subtype-level overrides for mixed categories; subtypes not listed are excluded.
    subtypes: dict[str, Trade] = Field(default_factory=dict)


class SjEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_type: str
    trade: Trade


class TradeMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = False
    approved_on: str | None = None
    sf: list[SfEntry] = Field(default_factory=list)
    sj: list[SjEntry] = Field(default_factory=list)


def load_trade_map(path: Path = TRADE_MAP) -> TradeMap:
    return TradeMap.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def approved_sf_categories(path: Path = TRADE_MAP) -> set[str]:
    m = load_trade_map(path)
    return {e.service_name for e in m.sf} if m.approved else set()


def sf_trade(service_name: str, subtype: str, m: TradeMap) -> Trade | None:
    for e in m.sf:
        if e.service_name == service_name:
            return e.subtypes.get(subtype, e.trade if not e.subtypes else None)
    return None
