"""Shared fixtures: a hand-driven clock, and two real databases.

Nothing here reads the system clock. Every instant in this package's tests comes from
:class:`ManualClock`, because determinism (spec §11 contract 3) is invisible against a clock that
moves on its own.

The second half is the database harness the integration tests share. It lives in this file rather
than in ``tests/integration/conftest.py`` deliberately: with no ``__init__.py`` under ``tests/``,
two files named ``conftest`` put two different modules on ``sys.path`` under one name, and
``from conftest import MIDDAY`` then resolves to whichever pytest inserted last. One conftest, one
name (C3_HANDOFF.md §11).

WeightsDB ships exactly these helpers (``weightsdb.testing.temporary_postgres``,
``MigrationHarness``) and this package may not import them: ADR-0050 decision 4 forbids the sibling
import and ``.importlinter`` asserts it, for the substantive reason that an egress ledger must not
drag an engine, a migration runner and a backup implementation into its dependency footprint. The
pattern is copied by hand instead, which is what the ADR intends.

Every integration test runs on **both** dialects (ADR-0006: two, both first-class). PostgreSQL
needs a reachable server: locally there is none unless the operator started one, so those legs skip
with a reason that names the URL, and pytest's ``-ra`` summary — on by default in this repository's
``addopts``— prints every one of them. Setting ``COMMISSIONER_REQUIRE_POSTGRES=1`` turns the
skip into a failure, which is what CI's ``db-matrix`` job does: a silently skipped dialect is an
untested dialect, and the both-dialects promise is only as good as its enforcement.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from commissioner.sql import mount_egress_tables

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine, MetaData
    from sqlalchemy.orm import Session

    from commissioner.sql import EgressTables

MIDDAY = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
"""A millisecond-aligned instant, so it survives a `governance.egress_decision` round trip exactly
(``TimestampField`` truncates to millisecond precision)."""

DAY = timedelta(days=1)


class ManualClock:
    """A clock the test moves by hand, satisfying :data:`baseaicore.Clock`."""

    def __init__(self, start: datetime = MIDDAY) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, when: datetime) -> None:
        self._now = when


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://commissioner:commissioner@localhost:5432/commissioner_test"
)
"""Where the PostgreSQL legs look for a server, unless ``COMMISSIONER_POSTGRES_URL`` says otherwise.

CI's ``db-matrix`` job starts a service with exactly these credentials and sets the variable
anyway, so the job states which server it is testing rather than inheriting a default.
"""


def postgres_url() -> str:
    """Return a reset, empty PostgreSQL database's URL, or skip the test that asked for one.

    Resets by dropping and recreating the ``public`` schema rather than assuming a pristine
    database: the server is reused across tests, and a previous test's ``alembic_version`` table
    would otherwise make the next autogenerate a no-op.

    Raises:
        Failed: If ``COMMISSIONER_REQUIRE_POSTGRES=1`` and no server is reachable.
    """
    url = os.environ.get("COMMISSIONER_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    required = os.environ.get("COMMISSIONER_REQUIRE_POSTGRES") == "1"
    try:
        probe = sa.create_engine(url)
        try:
            with probe.connect() as connection:
                connection.execute(sa.text("SELECT 1"))
        finally:
            probe.dispose()
    except Exception as exc:  # noqa: BLE001 — any failure means "no usable server", by design
        if required:
            pytest.fail(f"COMMISSIONER_REQUIRE_POSTGRES=1 but {url} is unreachable: {exc}")
        pytest.skip(f"POSTGRESQL LEG SKIPPED — no server at {url}: {exc}")
    reset = sa.create_engine(url)
    try:
        with reset.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        reset.dispose()
    return url


@pytest.fixture(params=["sqlite", "postgresql"])
def database_url(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Yield one URL per supported dialect, so every test using it runs on both.

    SQLite is a real **file**, never ``:memory:``: an in-memory database has no journal and
    survives no process boundary, which would silently exempt the concurrency test from the thing
    it claims to prove.
    """
    if request.param == "sqlite":
        directory = tmp_path_factory.mktemp("commissioner-sqlite")
        return f"sqlite:///{directory / 'egress.sqlite3'}"
    return postgres_url()


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    """An engine on a fresh, empty database of one dialect, disposed on exit."""
    made = sa.create_engine(database_url)
    try:
        yield made
    finally:
        made.dispose()


def mounted(engine: Engine, *, prefix: str = "egress_") -> tuple[MetaData, EgressTables]:
    """Mount the egress table into a throwaway host metadata and create it.

    ``create_all`` lives here, in a test, and never in ``src/`` — the package ships a shape, not a
    database (ADR-0050 decision 5). The host in ``tests/integration/hostapp`` does it properly,
    through Alembic; this is the shortcut for tests about the ledger rather than about mounting.
    """
    metadata = sa.MetaData()
    tables = mount_egress_tables(metadata, prefix=prefix)
    metadata.create_all(engine, tables=list(tables.all_tables))
    return metadata, tables


def session_factory_for(engine: Engine) -> sessionmaker[Session]:
    """Return the callable ``SqlEgressLedger`` takes: a factory of sessions it may own."""
    return sessionmaker(bind=engine)
