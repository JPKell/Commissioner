"""A child process that records decisions — for the one property that needs two real processes.

Run as a script, never imported by a test module (the name has no ``test_`` prefix, so pytest does
not collect it).

**Deliberately smaller than LoadLedger's ``ledger_subprocess.py``.** That module also injects a
``SIGKILL`` fault mid-transaction, because a budget debit writes a run row, several balance rows
and an entry row in one transaction, and the property under test is that a crash between them
leaves nothing partial. `SqlEgressLedger.record` issues exactly one ``INSERT`` into one table —
there is no second statement for a crash to land *between*, so there is no atomicity boundary for
a fault-injection harness to prove. What is still worth proving with real processes is that many
independent inserts under real file-level contention (SQLite's ``busy_timeout``, WAL) land every
row with none lost and none corrupted — the ``hammer`` mode below.

Usage::

    python egress_subprocess.py hammer <db-path> <journal-mode> <count> <run-id> <writer-id>
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import sqlalchemy as sa
from baseaicore import DataClassification
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from commissioner import EgressRequest, EgressTarget
from commissioner.policy import OrderedClassificationPolicy
from commissioner.sql import SqlEgressLedger

WHEN = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
"""The one instant every decision here is decided at, so ordering is by decision id alone."""


def engine_for(path: str, journal_mode: str) -> sa.Engine:
    """An engine on a real SQLite file, with the journal mode the test asked for."""
    made = sa.create_engine(f"sqlite:///{path}")

    @event.listens_for(made, "connect")
    def _apply_journal_mode(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined] # a DBAPI connection
        cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        cursor.close()

    return made


def run_hammer(path: str, journal_mode: str, count: int, run_id: str, writer_id: str) -> None:
    """Record ``count`` decisions against a run another process is also writing to at once."""
    engine = engine_for(path, journal_mode)
    ledger = SqlEgressLedger(sessionmaker(bind=engine))
    policy = OrderedClassificationPolicy(clock=lambda: WHEN)
    for index in range(count):
        request = EgressRequest(
            run_id=run_id,
            source_ref=f"{writer_id}-turn-{index}",
            data_classification=DataClassification.PUBLIC,
            target=EgressTarget(name="local", remote=False),
        )
        ledger.record(policy.evaluate(request))
    engine.dispose()


def main(argv: list[str]) -> None:
    """Dispatch on the first argument. See the module docstring for the call shape."""
    if argv[1] == "hammer":
        run_hammer(argv[2], argv[3], int(argv[4]), argv[5], argv[6])
    else:  # pragma: no cover — the caller is this file's own test module
        raise SystemExit(f"unknown mode {argv[1]!r}")


if __name__ == "__main__":
    main(sys.argv)
