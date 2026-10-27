"""Typed schemas for the campus YAML files in campus_data/.

The Pydantic models here are the source of truth. campus_data/schemas/*.schema.json is
generated from them (python -m agent.campus.validate --write-schemas) so editors can
check the YAML, and a test keeps the two in sync.

Files:
  campus.yaml             campus name, timezone, location, building list, walking times
  buildings/<CODE>.yaml   floors and rooms of one building
  assets/<CODE>.yaml      equipment located in one building and what it serves
  crews.yaml              technicians, trades, shifts, home buildings

IDs: a building's ID is its code (CSE). Floors are <CODE>-F<label> (CSE-F3, CSE-FB).
Rooms are <CODE>-<number> (CSE-3204). Equipment IDs start with <CODE>- and are written
out in full in assets files.
"""

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

BuildingCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}$")]
AssetId = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}-[A-Z0-9][A-Z0-9-]*$")]
RoomNumber = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{2,6}$")]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

RoomType = Literal[
    "classroom",
    "lecture_hall",
    "seminar",
    "computer_lab",
    "dry_lab",
    "wet_lab",
    "office",
    "conference",
    "study",
    "stacks",
    "lounge",
    "dining",
    "kitchen",
    "gym",
    "locker_room",
    "restroom",
    "hallway",
    "lobby",
    "storage",
    "custodial",
    "mechanical",
    "electrical",
    "server_room",
]

EquipmentType = Literal[
    "hvac_zone",
    "air_handler",
    "plant",
    "panel",
    "circuit",
    "elevator_bank",
    "elevator",
    "water_heater",
    "plumbing_riser",
]

Trade = Literal["plumbing", "electrical", "hvac", "structural", "custodial", "grounds"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoomSpec(Strict):
    number: RoomNumber
    type: RoomType
    capacity: int = Field(ge=0)
    name: str | None = None
    aliases: list[str] = Field(default_factory=list)


class FloorSpec(Strict):
    label: Annotated[str, StringConstraints(pattern=r"^(B|[1-9])$")]
    level: int = Field(ge=-2, le=20, description="-1 for the basement, 1 for the ground floor")
    aliases: list[str] = Field(default_factory=list)
    rooms: list[RoomSpec] = Field(min_length=1)


class BuildingFile(Strict):
    code: BuildingCode
    name: str
    aliases: list[str] = Field(default_factory=list)
    year_built: int = Field(ge=1850, le=2030)
    floors: list[FloorSpec] = Field(min_length=1)


class AssetSpec(Strict):
    """One piece of equipment. Edges come from three fields:

    upstream  served_by edges from this asset to each listed asset (zone -> air handler)
    serves    served_by edges from each listed room or floor to this asset (room -> zone)
    part_of   part_of edge from this asset to its group (elevator -> elevator bank)
    """

    id: AssetId
    type: EquipmentType
    name: str
    install_year: int = Field(ge=1900, le=2030)
    last_service_date: date | None = None
    criticality: int = Field(ge=1, le=5)
    located_in: AssetId | None = None
    upstream: list[AssetId] = Field(default_factory=list)
    serves: list[AssetId] = Field(default_factory=list)
    part_of: AssetId | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AssetsFile(Strict):
    building: BuildingCode
    assets: list[AssetSpec] = Field(min_length=1)


class Location(Strict):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class CampusFile(Strict):
    name: str
    timezone: str
    location: Location
    buildings: list[BuildingCode] = Field(min_length=1)
    walking_minutes: dict[BuildingCode, dict[BuildingCode, int]]


class Shift(Strict):
    days: list[Weekday] = Field(min_length=1)
    start: ClockTime
    end: ClockTime

    @field_validator("end")
    @classmethod
    def end_after_start(cls, end: str, info: Any) -> str:
        start = info.data.get("start")
        if start is not None and end <= start:
            raise ValueError("shifts that cross midnight are not supported")
        return end


class CrewMember(Strict):
    id: Annotated[str, StringConstraints(pattern=r"^tech-\d{2}$")]
    name: str
    trades: list[Trade] = Field(min_length=1)
    shift: Shift
    home_building: BuildingCode


class CrewsFile(Strict):
    crews: list[CrewMember] = Field(min_length=1)


SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "campus": CampusFile,
    "building": BuildingFile,
    "assets": AssetsFile,
    "crews": CrewsFile,
}
