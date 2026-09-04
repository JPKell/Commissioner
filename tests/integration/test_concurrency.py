"""Many processes, one run: real subprocess contention, and why there is nothing to lose.

Unlike a budget ledger, there is no shared aggregate here for two writers to race over — no
balance, no running total. Every decision is an independent row keyed by its own ``decision_id``,
so the property worth proving with real processes is narrower than LoadLedger's: that many
concurrent inserts under real file-level contention (SQLite's ``busy_timeout``, WAL) land every
row, with none lost and none duplicated. See ``egress_subprocess.py``'s module docstring for why
this module has no fault-injection counterpart to LoadLedger's ``test_atomicity.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

from conftest import mounted

SCRIPT = Path(__file__).parent / "egress_subprocess.py"

DECISIONS_PER_WRITER = 40
WRITERS = 3


def test_several_processes_recording_one_run_at_once_lose_nothing(tmp_path: Path) -> None:
    """The claim as a caller would test it: several processes, one run, one exact total.

    Every child is started before any is waited on, so their write transactions genuinely overlap.
    On SQLite the losing writer waits on the winner's write lock (pysqlite's five-second default
    ``timeout``) rather than racing it.
    """
    path = tmp_path / "egress.sqlite3"
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=wal")
    _, tables = mounted(engine)

    children = [
        subprocess.Popen(  # noqa: S603 — a fixed argv, no shell, no user input
            [
                sys.executable,
                str(SCRIPT),
                "hammer",
                str(path),
                "wal",
                str(DECISIONS_PER_WRITER),
                "traj-1",
                f"writer-{writer}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for writer in range(WRITERS)
    ]
    for child in children:
        _, errors = child.communicate(timeout=120)
        assert child.returncode == 0, errors.decode()

    with engine.connect() as connection:
        recorded = connection.execute(
            sa.select(sa.func.count()).select_from(tables.decisions)
        ).scalar_one()
        distinct_ids = connection.execute(
            sa.select(sa.func.count(sa.distinct(tables.decisions.c.decision_id)))
        ).scalar_one()
        run_rows = connection.execute(
            sa.select(sa.func.count())
            .select_from(tables.decisions)
            .where(tables.decisions.c.run_id == "traj-1")
        ).scalar_one()
    expected = WRITERS * DECISIONS_PER_WRITER
    assert recorded == expected
    assert distinct_ids == expected  # no two writers' decision ids collided
    assert run_rows == expected
    engine.dispose()
