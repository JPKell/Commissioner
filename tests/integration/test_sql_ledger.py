"""`SqlEgressLedger` is `InMemoryEgressLedger` with a different store — and the ways that is proven.

Every test here runs on both dialects through the ``engine`` fixture. The centrepiece is the
parity test: the same script of requests, evaluated and recorded against both implementations,
must produce byte-identical decisions once each stream's own ids are stripped. Everything after it
is a property that parity alone would not pin down — durability across a restart, the absence of
side effects on the read path, a duplicate id refused rather than silently swallowed, an enum
stored the same way on both dialects.
"""

from __future__ import annotations

import json
from datetime import UTC, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest
import sqlalchemy as sa
from baseaicore import DataClassification, from_rfc3339

from commissioner import EgressRequest, EgressTarget, StoreFailure, UnsupportedDialect, Verdict
from commissioner.ledger import InMemoryEgressLedger
from commissioner.policy import OrderedClassificationPolicy
from commissioner.sql import SqlEgressLedger
from conftest import MIDDAY, ManualClock, mounted, session_factory_for

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session

    from commissioner import EgressDecision


def sql_ledger(engine: Engine, *, prefix: str = "egress_") -> SqlEgressLedger:
    """Mount, create and return a ledger over ``engine``."""
    mounted(engine, prefix=prefix)
    return SqlEgressLedger(session_factory_for(engine), table_prefix=prefix)


def without_ids(decisions: object) -> list[dict[str, Any]]:
    """Canonical payloads with the decision id removed, so two ledgers' streams are comparable.

    Two policy instances draw ids from two generators, so the ids differ by construction and
    prove nothing; everything else in the record is the thing under test.
    """
    documents = [
        dict(decision.to_payload().model_dump(mode="json"))
        for decision in cast("list[EgressDecision]", decisions)
    ]
    for document in documents:
        document.pop("decision_id")
    return documents


def script(clock: ManualClock) -> list[EgressRequest]:
    """A run that exercises every branch: local, fail-closed, within ceiling, over ceiling."""
    requests = [
        EgressRequest(
            run_id="traj-1",
            source_ref="turn-1",
            data_classification=DataClassification.PUBLIC,
            target=EgressTarget(name="local", remote=False),
        ),
        EgressRequest(
            run_id="traj-1",
            source_ref="turn-2",
            data_classification=DataClassification.CONFIDENTIAL,
            target=EgressTarget(name="remote-a", remote=True, max_data_classification=None),
        ),
        EgressRequest(
            run_id="traj-1",
            source_ref="turn-3",
            data_classification=DataClassification.INTERNAL,
            target=EgressTarget(
                name="remote-a", remote=True, max_data_classification=DataClassification.INTERNAL
            ),
        ),
        EgressRequest(
            run_id="traj-2",
            source_ref="turn-1",
            data_classification=DataClassification.CONFIDENTIAL,
            target=EgressTarget(
                name="remote-b", remote=True, max_data_classification=DataClassification.INTERNAL
            ),
        ),
    ]
    clock.advance(timedelta(minutes=len(requests)))
    return requests


def evaluate_and_record(
    ledger: object, requests: list[EgressRequest], clock: ManualClock
) -> list[EgressDecision]:
    """Evaluate each request against its own policy tick and record it."""
    policy = OrderedClassificationPolicy(clock=clock)
    decisions = []
    for index, request in enumerate(requests):
        clock.set(MIDDAY + timedelta(minutes=index))
        decision = policy.evaluate(request)
        ledger.record(decision)  # type: ignore[attr-defined] # EgressLedger, either implementation
        decisions.append(decision)
    return decisions


def test_it_records_exactly_what_the_in_memory_ledger_records(engine: Engine) -> None:
    memory_clock, sql_clock = ManualClock(), ManualClock()
    memory = InMemoryEgressLedger()
    durable = sql_ledger(engine)

    from_memory = evaluate_and_record(memory, script(memory_clock), memory_clock)
    from_sql = evaluate_and_record(durable, script(sql_clock), sql_clock)

    assert without_ids(from_memory) == without_ids(from_sql)
    assert without_ids(memory.decisions()) == without_ids(durable.decisions())
    for run_id in ("traj-1", "traj-2"):
        assert without_ids(memory.decisions(run_id=run_id)) == without_ids(
            durable.decisions(run_id=run_id)
        )


@pytest.mark.contract
def test_a_decision_survives_a_restart_and_reads_back_byte_identically(engine: Engine) -> None:
    clock = ManualClock()
    durable = sql_ledger(engine)
    written = evaluate_and_record(durable, script(clock), clock)

    # A second ledger over the same table: a new process, as far as the rows are concerned.
    reopened = SqlEgressLedger(session_factory_for(engine))
    read_back = list(reopened.decisions())

    assert [d.to_payload().model_dump(mode="json") for d in read_back] == [
        d.to_payload().model_dump(mode="json") for d in written
    ]
    assert read_back == written


def test_decisions_filters_narrow_the_same_way_the_in_memory_ledger_narrows(engine: Engine) -> None:
    clock = ManualClock()
    memory = InMemoryEgressLedger()
    durable = sql_ledger(engine)
    for request in script(clock):
        policy = OrderedClassificationPolicy(clock=clock)
        decision = policy.evaluate(request)
        memory.record(decision)
        durable.record(decision)

    for kwargs in (
        {"run_id": "traj-1"},
        {"run_id": "traj-2"},
        {"verdict": Verdict.APPROVED},
        {"verdict": Verdict.DENIED},
        {"target": "remote-a"},
        {"target": "no-such-target"},
        {"since": MIDDAY},
        {"since": MIDDAY + timedelta(minutes=2)},
        {"run_id": "traj-1", "verdict": Verdict.DENIED},
    ):
        assert without_ids(memory.decisions(**kwargs)) == without_ids(
            durable.decisions(**kwargs)
        ), kwargs


def test_decisions_since_is_inclusive_and_refuses_a_naive_bound(engine: Engine) -> None:
    clock = ManualClock()
    durable = sql_ledger(engine)
    evaluate_and_record(durable, script(clock), clock)

    at_second_minute = durable.decisions(since=MIDDAY + timedelta(minutes=2))
    assert [d.request.source_ref for d in at_second_minute] == ["turn-3", "turn-1"]

    with pytest.raises(ValueError, match="timezone-aware"):
        durable.decisions(since=MIDDAY.replace(tzinfo=None))


def snapshot(engine: Engine, tables: Any) -> list[list[tuple[Any, ...]]]:
    """Every row in the mounted set, for a before/after comparison."""
    with engine.connect() as connection:
        return [
            [tuple(row) for row in connection.execute(sa.select(table).order_by(*table.c))]
            for table in tables.all_tables
        ]


def test_reading_writes_nothing_at_any_frequency(engine: Engine) -> None:
    clock = ManualClock()
    _, tables = mounted(engine)
    durable = SqlEgressLedger(session_factory_for(engine))
    evaluate_and_record(durable, script(clock), clock)

    before = snapshot(engine, tables)
    for _ in range(100):
        durable.decisions()
        durable.decisions(run_id="traj-1")
        durable.decisions(verdict=Verdict.DENIED)
        durable.decisions(target="no-such-target")
    assert snapshot(engine, tables) == before


def test_a_duplicate_decision_id_raises_store_failure_and_writes_nothing(engine: Engine) -> None:
    clock = ManualClock()
    _, tables = mounted(engine)
    durable = SqlEgressLedger(session_factory_for(engine))
    policy = OrderedClassificationPolicy(clock=clock)
    decision = policy.evaluate(script(clock)[0])
    durable.record(decision)
    before = snapshot(engine, tables)

    with pytest.raises(StoreFailure) as raised:
        durable.record(decision)
    assert raised.value.details["decision_id"] == decision.decision_id
    assert raised.value.code == "EGRESS_STORE_FAILURE"
    assert snapshot(engine, tables) == before


def test_the_indexed_columns_agree_with_the_canonical_record(engine: Engine) -> None:
    from commissioner.sql import _from_utc  # noqa: PLC0415 — private; reached for in this test only

    clock = ManualClock()
    _, tables = mounted(engine)
    durable = SqlEgressLedger(session_factory_for(engine))
    evaluate_and_record(durable, script(clock), clock)

    with engine.connect() as connection:
        rows = connection.execute(sa.select(tables.decisions)).mappings().all()
    assert rows
    for row in rows:
        document = json.loads(row["decision_json"])
        assert document["decision_id"] == row["decision_id"]
        assert document["request"]["run_id"] == row["run_id"]
        assert document["verdict"] == row["verdict"]
        assert document["request"]["target"]["name"] == row["target_name"]
        assert from_rfc3339(document["decided_at"]) == _from_utc(row["decided_at"])


def test_a_third_dialect_is_refused_rather_than_attempted() -> None:
    durable = SqlEgressLedger(lambda: cast("Session", _StubSession("mysql")))
    clock = ManualClock()
    policy = OrderedClassificationPolicy(clock=clock)
    with pytest.raises(UnsupportedDialect) as raised:
        durable.record(policy.evaluate(script(clock)[0]))
    assert raised.value.details["dialect"] == "mysql"
    assert raised.value.code == "EGRESS_UNSUPPORTED_DIALECT"


class _StubSession:
    """The smallest thing ``_require_supported_dialect`` reads: a session whose bind names a
    dialect."""

    def __init__(self, dialect_name: str) -> None:
        self._dialect_name = dialect_name

    def get_bind(self) -> object:
        return type(
            "_Bind", (), {"dialect": type("_Dialect", (), {"name": self._dialect_name})()}
        )()

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def test_a_stored_instant_comes_back_utc_from_either_driver() -> None:
    """SQLite hands back a naive value, PostgreSQL an aware one; both must read as the same UTC.

    The PostgreSQL arm is unreachable from a SQLite leg and this machine has no server, so the two
    driver behaviours are exercised directly against the helper that reconciles them.
    """
    from commissioner.sql import _from_utc  # noqa: PLC0415 — see the module docstring above

    naive_from_sqlite = MIDDAY.replace(tzinfo=None)
    aware_from_postgresql = MIDDAY.astimezone(timezone(timedelta(hours=-5)))
    assert _from_utc(naive_from_sqlite) == MIDDAY
    assert _from_utc(aware_from_postgresql) == MIDDAY
    assert _from_utc(aware_from_postgresql).tzinfo is UTC
