import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from agent.config import get_settings

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = get_settings().test_database_url
    # Tests drop and recreate the schema. Never point them at anything but a test DB.
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"TEST_DATABASE_URL must name a database ending in _test, got {database!r}")
    return url


@pytest.fixture(scope="session")
def migrated_engine(test_db_url: str) -> Iterator[Engine]:
    """A freshly migrated test database. Skips the test when Postgres is unreachable."""
    from alembic import command
    from alembic.config import Config

    engine = create_engine(test_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as exc:
        engine.dispose()
        message = f"Postgres not reachable at TEST_DATABASE_URL: {exc.orig}"
        # CI sets REQUIRE_DB=1 so a missing database fails the build instead of skipping.
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(message)
        pytest.skip(message)

    cfg = Config(str(REPO / "infra" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()
