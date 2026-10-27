"""The simulated quarter schedule follows its rules."""

from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from agent.campus.loader import Campus, load_campus
from agent.campus.schedules import Block, fingerprint, generate, load_into_db, load_rules
from agent.campus.store import load_into_db as load_campus_into_db

NO_CLASS_TYPES = {
    "office",
    "restroom",
    "hallway",
    "mechanical",
    "electrical",
    "storage",
    "custodial",
    "server_room",
    "kitchen",
    "locker_room",
    "stacks",
}


@pytest.fixture(scope="module")
def campus(repo: Path) -> Campus:
    return load_campus(repo / "campus_data")


@pytest.fixture(scope="module")
def rules(repo: Path):  # type: ignore[no-untyped-def]
    return load_rules(repo / "campus_data" / "schedule_rules.yaml")


@pytest.fixture(scope="module")
def blocks(rules, campus: Campus) -> list[Block]:  # type: ignore[no-untyped-def]
    return generate(rules, campus)


def test_deterministic(rules, campus: Campus, blocks: list[Block]) -> None:  # type: ignore[no-untyped-def]
    assert fingerprint(generate(rules, campus)) == fingerprint(blocks)


def test_no_room_is_double_booked(blocks: list[Block]) -> None:
    by_room: dict[str, list[Block]] = defaultdict(list)
    for b in blocks:
        by_room[b.room_id].append(b)
    for room, bs in by_room.items():
        bs.sort(key=lambda b: b.starts_at)
        for a, b in pairwise(bs):
            assert a.ends_at <= b.starts_at, (room, a, b)


def test_classes_only_in_instruction_weeks_and_not_on_holidays(rules, blocks: list[Block]) -> None:  # type: ignore[no-untyped-def]
    q = rules.quarter
    for b in blocks:
        day = b.starts_at.date()
        if b.kind == "class":
            assert q.instruction_start <= day <= q.instruction_end
            assert day not in rules.holidays
            assert day.weekday() < 5
        if b.kind == "exam":
            assert q.finals_start <= day <= q.finals_end


def test_finals_week_has_no_classes(rules, blocks: list[Block]) -> None:  # type: ignore[no-untyped-def]
    kinds = {b.kind for b in blocks if b.starts_at.date() >= rules.quarter.finals_start}
    assert kinds <= {"exam", "event"}


def test_one_final_per_lecture_course(blocks: list[Block]) -> None:
    finals = Counter(
        (b.room_id, b.title.removesuffix(" final")) for b in blocks if b.kind == "exam"
    )
    assert finals and max(finals.values()) == 1
    lecture_courses = {
        (b.room_id, b.title)
        for b in blocks
        if b.kind == "class"
        and not b.title.endswith(" lab")
        and b.ends_at - b.starts_at < timedelta(minutes=100)
    }
    assert set(finals) == lecture_courses


def test_room_types_without_classes(campus: Campus, blocks: list[Block]) -> None:
    g = campus.graph
    for b in blocks:
        if b.kind in ("class", "exam"):
            assert g.nodes[b.room_id]["room_type"] not in NO_CLASS_TYPES, b


def test_timezone_and_demo_wing(rules, blocks: list[Block]) -> None:  # type: ignore[no-untyped-def]
    assert all(str(b.starts_at.tzinfo) == rules.timezone for b in blocks)
    # The demo needs exams in CSE rooms during finals week.
    assert any(b.kind == "exam" and b.room_id.startswith("CSE-") for b in blocks)


def test_lecture_halls_are_busy(rules, campus: Campus, blocks: list[Block]) -> None:  # type: ignore[no-untyped-def]
    start = rules.quarter.instruction_start + timedelta(days=7)
    monday = start + timedelta(days=(7 - start.weekday()) % 7)
    week = [monday + timedelta(days=i) for i in range(5)]
    halls = [r for r in campus.rooms() if campus.graph.nodes[r]["room_type"] == "lecture_hall"]
    hours = sum(
        (b.ends_at - b.starts_at).total_seconds() / 3600
        for b in blocks
        if b.room_id in halls and b.kind == "class" and b.starts_at.date() in week
    )
    # 8:00-18:20 weekdays is about 51 hours; occupancy 0.8 means roughly 30-40 hours per hall.
    assert 25 * len(halls) <= hours <= 45 * len(halls)


def test_no_class_on_thanksgiving(blocks: list[Block]) -> None:
    assert not any(b.kind == "class" and b.starts_at.date() == date(2026, 11, 26) for b in blocks)


@pytest.mark.db
def test_load_into_db_once(
    migrated_engine: Engine, campus: Campus, rules, blocks: list[Block]
) -> None:  # type: ignore[no-untyped-def]
    with migrated_engine.begin() as conn:
        load_campus_into_db(conn, campus)
        assert load_into_db(conn, rules, blocks) == len(blocks)
    with pytest.raises(RuntimeError, match="already exist"), migrated_engine.begin() as conn:
        load_into_db(conn, rules, blocks)
    with migrated_engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM schedule_blocks")).scalar_one()
    assert n == len(blocks)
