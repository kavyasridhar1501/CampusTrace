"""The first migration: schema matches the models, and the receipts ledger is append-only.

These run against the separate test database (TEST_DATABASE_URL, a *_test database).
Receipts written here persist for the test session, because nothing can delete them, so
each test reads the current chain tip instead of assuming an empty table.
"""

import hashlib
import json
import uuid
from itertools import count, pairwise

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from agent.db import Base
from tests.conftest import REPO

pytestmark = pytest.mark.db

_counter = count()


def _new_hash() -> str:
    return hashlib.sha256(f"receipt-{next(_counter)}-{uuid.uuid4()}".encode()).hexdigest()


def _tip(conn: Connection) -> str:
    tip = conn.execute(text("SELECT content_hash FROM receipts ORDER BY seq DESC LIMIT 1"))
    return tip.scalar_one_or_none() or "genesis"


def _insert(conn: Connection, previous_hash: str, content_hash: str) -> int:
    body = json.dumps({"previous_hash": previous_hash, "content_hash": content_hash})
    return conn.execute(
        text(
            "INSERT INTO receipts (receipt_id, previous_hash, content_hash, signature,"
            " public_key_id, body) VALUES (:rid, :prev, :hash, 'sig', 'test-key', :body)"
            " RETURNING seq"
        ),
        {"rid": uuid.uuid4(), "prev": previous_hash, "hash": content_hash, "body": body},
    ).scalar_one()


def _append(engine: Engine) -> tuple[int, str]:
    with engine.begin() as conn:
        content_hash = _new_hash()
        return _insert(conn, _tip(conn), content_hash), content_hash


def test_extensions_installed(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        exts = dict(conn.execute(text("SELECT extname, extversion FROM pg_extension")).all())
    assert "postgis" in exts
    assert "vector" in exts


def test_models_match_migration(migrated_engine: Engine) -> None:
    def include_name(name: str | None, type_: str, parent_names: object) -> bool:
        return type_ != "table" or name in Base.metadata.tables

    with migrated_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_name": include_name})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_receipts_chain_in_seq_order(migrated_engine: Engine) -> None:
    seqs = [_append(migrated_engine)[0] for _ in range(3)]
    assert seqs == sorted(seqs)
    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT previous_hash, content_hash FROM receipts ORDER BY seq")
        ).all()
    assert rows[0][0] == "genesis"
    for (_, prev_content), (next_prev, _) in pairwise(rows):
        assert next_prev == prev_content


def test_wrong_previous_hash_is_rejected(migrated_engine: Engine) -> None:
    _append(migrated_engine)
    with (
        pytest.raises(IntegrityError, match="does not match chain tip"),
        migrated_engine.begin() as conn,
    ):
        _insert(conn, "0" * 64, _new_hash())


def test_second_genesis_is_rejected(migrated_engine: Engine) -> None:
    _append(migrated_engine)
    with (
        pytest.raises(IntegrityError, match="does not match chain tip"),
        migrated_engine.begin() as conn,
    ):
        _insert(conn, "genesis", _new_hash())


def test_explicit_seq_is_overridden(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        seq = conn.execute(
            text(
                "INSERT INTO receipts (seq, receipt_id, previous_hash, content_hash, signature,"
                " public_key_id, body) VALUES (999999, :rid, :prev, :hash, 's', 'k', '{}')"
                " RETURNING seq"
            ),
            {"rid": uuid.uuid4(), "prev": _tip(conn), "hash": _new_hash()},
        ).scalar_one()
    assert seq != 999999


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE receipts SET signature = 'forged'",
        "UPDATE receipts SET body = '{}'::jsonb WHERE seq = (SELECT min(seq) FROM receipts)",
        "DELETE FROM receipts",
        "DELETE FROM receipts WHERE seq = (SELECT max(seq) FROM receipts)",
        "TRUNCATE receipts CASCADE",
    ],
)
def test_receipts_cannot_be_changed(migrated_engine: Engine, statement: str) -> None:
    _append(migrated_engine)
    with pytest.raises(DBAPIError, match="append-only"), migrated_engine.begin() as conn:
        conn.execute(text(statement))


def test_anchor_must_match_its_receipt(migrated_engine: Engine) -> None:
    seq, tip = _append(migrated_engine)
    with pytest.raises(IntegrityError, match="does not match"), migrated_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO anchors (seq, tip_hash) VALUES (:s, :h)"), {"s": seq, "h": "f" * 64}
        )
    with migrated_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO anchors (seq, tip_hash) VALUES (:s, :h)"), {"s": seq, "h": tip}
        )


@pytest.mark.parametrize(
    "statement",
    ["UPDATE anchors SET github_commit_sha = 'x'", "DELETE FROM anchors", "TRUNCATE anchors"],
)
def test_anchors_cannot_be_changed(migrated_engine: Engine, statement: str) -> None:
    seq, tip = _append(migrated_engine)
    with migrated_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO anchors (seq, tip_hash) VALUES (:s, :h)"), {"s": seq, "h": tip}
        )
    with pytest.raises(DBAPIError, match="append-only"), migrated_engine.begin() as conn:
        conn.execute(text(statement))


def test_downgrade_is_refused(test_db_url: str, migrated_engine: Engine) -> None:
    _append(migrated_engine)
    cfg = Config(str(REPO / "infra" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    with pytest.raises(NotImplementedError, match="append-only"):
        command.downgrade(cfg, "base")
    with migrated_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM receipts")).scalar_one() > 0
