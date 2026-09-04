"""Spec §15's budgets against `SqlEgressLedger`.

Excluded from the default run (`-m "not live and not performance"`), like every budget assertion
in the suite. Numbers are from CPython 3.13 on the development machine, SQLite on a real file:

| Measure | Spec §15 target | Measured |
|---|---|---|
| `record` | ≤ 5 ms | see the test's own failure message if this drifts |
| `decisions` over 100 000 rows, filtered to one run | ≤ 200 ms | see the test's own message |

Unlike `LoadLedger.debit`, `SqlEgressLedger.record` maintains no balance and reads nothing back
before writing — it is one ``INSERT`` of one independent row — so there is no read-modify-write for
history length to slow down, and no "first slice vs last slice" comparison is needed to prove
flatness: flatness is structural here, not an empirical property to protect.

The filtered read is the one number worth measuring at scale, because it is the one operation
whose cost is a function of how many rows a query returns: 100 000 decisions are spread across
1 000 runs (spec's own "100 000 rows"), and the query narrows to one run's history — a hundred rows
— which is the shape a real caller's per-run audit view actually asks for.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from baseaicore import DataClassification
from sqlalchemy.orm import sessionmaker

from commissioner import EgressRequest, EgressTarget
from commissioner.policy import OrderedClassificationPolicy
from commissioner.sql import SqlEgressLedger, mount_egress_tables
from conftest import MIDDAY, ManualClock

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Engine

TOTAL_DECISIONS = 100_000
RUNS = 1_000
PER_RUN = TOTAL_DECISIONS // RUNS


def a_request(run_id: str, index: int) -> EgressRequest:
    return EgressRequest(
        run_id=run_id,
        source_ref=f"turn-{index}",
        data_classification=DataClassification.PUBLIC,
        target=EgressTarget(name="local", remote=False),
    )


@pytest.fixture
def ledger_on_disk(tmp_path: Path) -> tuple[SqlEgressLedger, Engine]:
    """A ledger on a real SQLite file — never `:memory:`, which measures the wrong thing."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'egress.sqlite3'}")
    metadata = sa.MetaData()
    tables = mount_egress_tables(metadata)
    metadata.create_all(engine, tables=list(tables.all_tables))
    return SqlEgressLedger(sessionmaker(bind=engine)), engine


@pytest.mark.performance
def test_record_stays_within_budget(ledger_on_disk: tuple[SqlEgressLedger, Engine]) -> None:
    ledger, _ = ledger_on_disk
    policy = OrderedClassificationPolicy(clock=ManualClock(MIDDAY))
    count = 2_000
    began = time.perf_counter_ns()
    for index in range(count):
        ledger.record(policy.evaluate(a_request("traj-0", index)))
    per_call_ms = (time.perf_counter_ns() - began) / 1_000_000 / count
    assert per_call_ms <= 5.0, f"{per_call_ms:.3f} ms per record against spec §15's 5 ms"


@pytest.mark.performance
def test_a_filtered_query_over_a_hundred_thousand_rows_stays_within_budget(
    ledger_on_disk: tuple[SqlEgressLedger, Engine],
) -> None:
    ledger, _ = ledger_on_disk
    policy = OrderedClassificationPolicy(clock=ManualClock(MIDDAY))
    for run_index in range(RUNS):
        run_id = f"traj-{run_index}"
        for turn_index in range(PER_RUN):
            ledger.record(policy.evaluate(a_request(run_id, turn_index)))

    began = time.perf_counter_ns()
    rows = ledger.decisions(run_id="traj-500")
    elapsed_ms = (time.perf_counter_ns() - began) / 1_000_000
    assert len(rows) == PER_RUN
    assert elapsed_ms <= 200.0, (
        f"filtering {TOTAL_DECISIONS} rows to one run's {PER_RUN} took {elapsed_ms:.0f} ms "
        "against spec §15's 200 ms"
    )
