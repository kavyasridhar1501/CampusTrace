"""Generate one simulated quarter of classes, exams and events from schedule_rules.yaml.

    uv run python -m agent.campus.schedules            # summary only
    uv run python -m agent.campus.schedules --load     # write to schedule_blocks

Deterministic: the same rules, campus and seed give the same blocks. A room never has two
blocks at once. Classes meet only in instruction weeks and not on holidays; finals week
holds only exams and events.
"""

import argparse
import hashlib
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, create_engine, func, insert, select

from agent.campus.loader import Campus, load_campus
from agent.config import get_settings
from agent.db import ScheduleBlock

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Quarter(Strict):
    name: str
    instruction_start: date
    instruction_end: date
    finals_start: date
    finals_end: date


class Pattern(Strict):
    days: list[str]
    minutes: int
    starts: list[str]
    days_per_course: int | None = None


class LabRoomRule(Strict):
    type: str
    min_capacity: int = 0
    name_prefix: str | None = None


class Finals(Strict):
    minutes: int
    starts: list[str]


class Events(Strict):
    per_week: dict[str, float]
    weekday_starts: list[str]
    weekend_starts: list[str]
    minutes: list[int]


class ScheduleRules(Strict):
    seed: int
    timezone: str
    quarter: Quarter
    holidays: list[date] = Field(default_factory=list)
    patterns: dict[str, Pattern]
    occupancy: dict[str, dict[str, float]]
    lab_rooms: list[LabRoomRule]
    lab_occupancy: float
    departments: dict[str, list[str]]
    finals: Finals
    events: Events


@dataclass(frozen=True, order=True)
class Block:
    room_id: str
    starts_at: datetime
    ends_at: datetime
    kind: str
    title: str


def load_rules(path: str | Path) -> ScheduleRules:
    return ScheduleRules.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


class RoomCalendar:
    def __init__(self) -> None:
        self._by_room: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)

    def free(self, room: str, start: datetime, end: datetime) -> bool:
        return all(end <= s or start >= e for s, e in self._by_room[room])

    def add(self, room: str, start: datetime, end: datetime) -> None:
        self._by_room[room].append((start, end))


@dataclass(frozen=True)
class Course:
    room_id: str
    title: str
    pattern: str
    start: str
    days: tuple[str, ...]


def is_lab_room(rules: ScheduleRules, room_type: str, capacity: int, name: str) -> bool:
    return any(
        r.type == room_type
        and capacity >= r.min_capacity
        and (r.name_prefix is None or name.startswith(r.name_prefix))
        for r in rules.lab_rooms
    )


def plan_courses(rules: ScheduleRules, campus: Campus, rng: random.Random) -> list[Course]:
    g = campus.graph
    courses: list[Course] = []
    used: set[str] = set()

    def new_title(depts: list[str]) -> str:
        while True:
            title = f"{rng.choice(depts)} {rng.randint(1, 199)}"
            if title not in used:
                used.add(title)
                return title

    for room in sorted(campus.rooms()):
        d = g.nodes[room]
        depts = rules.departments.get(d["building"], ["GEN"])
        if is_lab_room(rules, d["room_type"], d["capacity"], d["name"]):
            plan = {"lab": rules.lab_occupancy}
        else:
            plan = rules.occupancy.get(d["room_type"], {})
        for pname, share in plan.items():
            p = rules.patterns[pname]
            slots: list[tuple[tuple[str, ...], str]]
            if p.days_per_course:
                # One meeting a week: each (day, start) slot is a separate section.
                slots = [((day,), s) for day in p.days for s in p.starts]
            else:
                slots = [(tuple(p.days), s) for s in p.starts]
            for days, start in slots:
                if rng.random() < share:
                    title = new_title(depts) + (" lab" if pname == "lab" else "")
                    courses.append(Course(room, title, pname, start, days))
    return courses


def generate(rules: ScheduleRules, campus: Campus) -> list[Block]:
    tz = ZoneInfo(rules.timezone)
    rng = random.Random(rules.seed)
    q = rules.quarter
    holidays = set(rules.holidays)
    cal = RoomCalendar()
    blocks: list[Block] = []

    def at(day: date, hhmm: str) -> datetime:
        return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz)

    def place(room: str, start: datetime, minutes: int, kind: str, title: str) -> bool:
        end = start + timedelta(minutes=minutes)
        if not cal.free(room, start, end):
            return False
        cal.add(room, start, end)
        blocks.append(Block(room, start, end, kind, title))
        return True

    courses = plan_courses(rules, campus, rng)
    instruction_days = [
        d for d in _days(q.instruction_start, q.instruction_end) if d not in holidays
    ]
    for c in courses:
        minutes = rules.patterns[c.pattern].minutes
        for day in instruction_days:
            if WEEKDAYS[day.weekday()] in c.days:
                placed = place(c.room_id, at(day, c.start), minutes, "class", c.title)
                assert placed, f"pattern overlap in {c.room_id} at {day} {c.start}"

    # Finals: each MWF/TuTh course gets one exam in its room. Slot order is shuffled per
    # course and the first free slot wins, so rooms never double-book.
    finals_days = _days(q.finals_start, q.finals_end)
    for c in courses:
        if c.pattern not in ("mwf", "tuth"):
            continue
        slots = [(d, s) for d in finals_days for s in rules.finals.starts]
        rng.shuffle(slots)
        for day, s in slots:
            if place(c.room_id, at(day, s), rules.finals.minutes, "exam", f"{c.title} final"):
                break
        else:
            raise RuntimeError(f"no free finals slot for {c.title} in {c.room_id}")

    # Events: Poisson-ish arrivals per eligible room, evenings and weekends, all quarter.
    g = campus.graph
    all_days = _days(q.instruction_start, q.finals_end)
    weeks = len(all_days) / 7
    for room in sorted(campus.rooms()):
        rate = rules.events.per_week.get(g.nodes[room]["room_type"])
        if not rate:
            continue
        n = sum(1 for _ in range(int(rate * weeks * 2)) if rng.random() < 0.5)
        for _ in range(n):
            for _attempt in range(10):
                day = rng.choice(all_days)
                weekend = day.weekday() >= 5
                starts = rules.events.weekend_starts if weekend else rules.events.weekday_starts
                minutes = rng.choice(rules.events.minutes)
                if place(room, at(day, rng.choice(starts)), minutes, "event", "Event"):
                    break

    return sorted(blocks)


def fingerprint(blocks: list[Block]) -> str:
    h = hashlib.sha256()
    for b in blocks:
        h.update(
            f"{b.room_id}|{b.starts_at.isoformat()}|{b.ends_at.isoformat()}|{b.kind}|{b.title}\n".encode()
        )
    return h.hexdigest()


def load_into_db(conn: Connection, rules: ScheduleRules, blocks: list[Block]) -> int:
    """Insert the quarter's blocks. Refuses if the quarter already has blocks, so a
    second run never duplicates or silently replaces a schedule."""
    tz = ZoneInfo(rules.timezone)
    q_start = datetime.combine(rules.quarter.instruction_start, time.min, tzinfo=tz)
    q_end = datetime.combine(rules.quarter.finals_end + timedelta(days=1), time.min, tzinfo=tz)
    existing = conn.execute(
        select(func.count())
        .select_from(ScheduleBlock)
        .where(ScheduleBlock.starts_at >= q_start, ScheduleBlock.starts_at < q_end)
    ).scalar_one()
    if existing:
        raise RuntimeError(
            f"{existing} blocks already exist for {rules.quarter.name}; load into a fresh database"
        )
    rows = [
        {
            "room_id": b.room_id,
            "starts_at": b.starts_at,
            "ends_at": b.ends_at,
            "kind": b.kind,
            "title": b.title,
        }
        for b in blocks
    ]
    for i in range(0, len(rows), 5000):
        conn.execute(insert(ScheduleBlock), rows[i : i + 5000])
    return len(rows)


def summary(blocks: list[Block]) -> str:
    kinds = Counter(b.kind for b in blocks)
    rooms = len({b.room_id for b in blocks})
    return f"{len(blocks)} blocks in {rooms} rooms: " + ", ".join(
        f"{k} {n}" for k, n in sorted(kinds.items())
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the simulated quarter schedule")
    parser.add_argument("data_dir", nargs="?", default="campus_data")
    parser.add_argument("--load", action="store_true", help="insert into schedule_blocks")
    args = parser.parse_args(argv)
    campus = load_campus(args.data_dir)
    rules = load_rules(Path(args.data_dir) / "schedule_rules.yaml")
    blocks = generate(rules, campus)
    print(summary(blocks))
    print(f"fingerprint {fingerprint(blocks)}")
    if args.load:
        with create_engine(get_settings().database_url).begin() as conn:
            print(f"inserted {load_into_db(conn, rules, blocks)} blocks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
