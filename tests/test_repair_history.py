"""The simulated repair history follows the failure patterns. No API calls."""

from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from agent.campus.loader import Campus, load_campus
from agent.campus.store import load_into_db as load_campus_into_db
from agent.intake.symptoms import SYMPTOM_DOMAINS, SYMPTOM_TYPES
from agent.repairs.history import (
    RepairRecord,
    eligible_patterns,
    load_into_db,
    load_patterns,
    read_jsonl,
    review_sample,
    skeleton,
    write_notes,
)


@pytest.fixture(scope="module")
def campus(repo: Path) -> Campus:
    return load_campus(repo / "campus_data")


@pytest.fixture(scope="module")
def fp(repo: Path):  # type: ignore[no-untyped-def]
    return load_patterns(repo / "campus_data" / "failure_patterns.yaml")


@pytest.fixture(scope="module")
def records(fp, campus: Campus) -> list[RepairRecord]:  # type: ignore[no-untyped-def]
    return skeleton(fp, campus)


def fake_writer(batch):  # type: ignore[no-untyped-def]
    return {r["id"]: f"{r['confirmed_cause']}. {r['action']}." for r in batch}


def test_symptom_vocabulary_is_consistent(fp) -> None:  # type: ignore[no-untyped-def]
    assert set(SYMPTOM_DOMAINS) == set(SYMPTOM_TYPES)
    used = {s for at in fp.asset_types.values() for p in at.patterns for s in p.symptoms}
    assert used <= set(SYMPTOM_TYPES)


def test_pattern_ids_unique_and_chronic_assets_exist(fp, campus: Campus) -> None:  # type: ignore[no-untyped-def]
    ids = [p.id for at in fp.asset_types.values() for p in at.patterns]
    assert len(ids) == len(set(ids))
    for asset in fp.chronic:
        assert asset in campus.graph
        assert eligible_patterns(fp, campus, asset)


def test_deterministic_and_about_600(fp, campus: Campus, records: list[RepairRecord]) -> None:  # type: ignore[no-untyped-def]
    again = skeleton(fp, campus)
    assert [r.model_dump() for r in again] == [r.model_dump() for r in records]
    assert 520 <= len(records) <= 680
    assert len({r.id for r in records}) == len(records)


def test_records_match_their_patterns(fp, campus: Campus, records: list[RepairRecord]) -> None:  # type: ignore[no-untyped-def]
    by_id = {p.id: p for at in fp.asset_types.values() for p in at.patterns}
    for r in records:
        p = by_id[r.pattern_id]
        assert p in eligible_patterns(fp, campus, r.asset_id)
        assert r.symptom in p.symptoms
        assert r.confirmed_cause == p.cause and r.action == p.action
        assert p.minutes[0] <= r.minutes <= p.minutes[1]


def test_chronic_assets_fail_most(fp, records: list[RepairRecord]) -> None:  # type: ignore[no-untyped-def]
    top = {a for a, _ in Counter(r.asset_id for r in records).most_common(len(fp.chronic) + 2)}
    assert len(set(fp.chronic) & top) >= len(fp.chronic) - 1


def test_misdiagnoses_are_followed_by_the_real_fix(records: list[RepairRecord]) -> None:
    wrong = [r for r in records if r.outcome == "symptom_returned"]
    assert 0.03 <= len(wrong) / len(records) <= 0.15
    for w in wrong:
        later = [
            r
            for r in records
            if r.asset_id == w.asset_id
            and r.outcome == "fixed"
            and timedelta(0) <= r.completed_at - w.completed_at <= timedelta(days=11)
        ]
        assert later, w


def test_technicians_have_the_trade(campus: Campus, records: list[RepairRecord]) -> None:
    trades = {c.id: set(c.trades) for c in campus.crews.crews}
    for r in records:
        if r.technician == "elevator vendor":
            assert r.asset_type == "elevator_bank"
        else:
            assert r.trade in trades[r.technician], r


def test_write_notes_and_review_sample(campus: Campus, records: list[RepairRecord]) -> None:
    noted = write_notes(records[:60], campus, fake_writer, batch_size=25)
    assert all(r.note_raw for r in noted)
    assert "| asset | symptom |" in review_sample(noted, n=10)


def test_missing_note_is_an_error(campus: Campus, records: list[RepairRecord]) -> None:
    def drops_one(batch):  # type: ignore[no-untyped-def]
        notes = fake_writer(batch)
        notes.pop(batch[0]["id"])
        return notes

    with pytest.raises(RuntimeError, match="no note returned"):
        write_notes(records[:5], campus, drops_one)


def test_committed_file_matches_generator(repo: Path, records: list[RepairRecord]) -> None:
    on_disk = read_jsonl(repo / "campus_data" / "repair_history.jsonl")
    strip = lambda rs: [r.model_dump(exclude={"note_raw"}) for r in rs]  # noqa: E731
    assert strip(on_disk) == strip(records)


@pytest.mark.db
def test_load_into_db(migrated_engine: Engine, campus: Campus, records: list[RepairRecord]) -> None:
    noted = write_notes(records, campus, fake_writer)
    with migrated_engine.begin() as conn:
        load_campus_into_db(conn, campus)
        with pytest.raises(RuntimeError, match="notes missing"):
            load_into_db(conn, records)
        assert load_into_db(conn, noted) == len(noted)
    with pytest.raises(RuntimeError, match="already in repairs"), migrated_engine.begin() as conn:
        load_into_db(conn, noted)
    with migrated_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM repairs")).scalar_one() == len(noted)
