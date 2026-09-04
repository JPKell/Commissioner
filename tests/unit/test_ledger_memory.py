"""`InMemoryEgressLedger`: symmetric recording, filters, and the ordering both ledgers share.

The SQL-backed ledger's own parity test (`tests/integration/test_sql_ledger.py`) runs the same
scripts against both implementations; this module is the in-memory ledger's tests in isolation, so
its own behaviour is pinned down without a database in the loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from baseaicore import DataClassification

from commissioner import EgressDecision, EgressRequest, EgressTarget, Verdict
from commissioner.ledger import InMemoryEgressLedger
from conftest import MIDDAY


def _target(name: str = "remote-a", *, remote: bool = True) -> EgressTarget:
    return EgressTarget(
        name=name, remote=remote, max_data_classification=DataClassification.INTERNAL
    )


def _decision(
    decision_id: str,
    *,
    run_id: str = "traj-1",
    verdict: Verdict = Verdict.APPROVED,
    target: EgressTarget | None = None,
    decided_at: datetime = MIDDAY,
) -> EgressDecision:
    return EgressDecision(
        decision_id=decision_id,
        request=EgressRequest(
            run_id=run_id,
            source_ref="turn-1",
            data_classification=DataClassification.INTERNAL,
            target=target if target is not None else _target(),
        ),
        verdict=verdict,
        reason="within_ceiling"
        if verdict is Verdict.APPROVED
        else "classification_exceeds_ceiling",
        policy_name="OrderedClassificationPolicy",
        policy_version="1.0",
        decided_at=decided_at,
    )


class TestRecordAndDecisions:
    def test_an_empty_ledger_has_no_decisions(self) -> None:
        assert InMemoryEgressLedger().decisions() == ()

    def test_approvals_and_denials_are_recorded_symmetrically(self) -> None:
        ledger = InMemoryEgressLedger()
        approved = _decision("d1", verdict=Verdict.APPROVED)
        denied = _decision("d2", verdict=Verdict.DENIED)
        ledger.record(approved)
        ledger.record(denied)
        assert set(ledger.decisions()) == {approved, denied}

    def test_a_violation_row_is_accepted(self) -> None:
        # spec §11 contract 6: the ledger accepts VIOLATION though no shipped policy produces one.
        ledger = InMemoryEgressLedger()
        violation = _decision("d1", verdict=Verdict.VIOLATION)
        ledger.record(violation)
        assert ledger.decisions()[0].verdict is Verdict.VIOLATION


class TestFilters:
    def _populated(self) -> InMemoryEgressLedger:
        ledger = InMemoryEgressLedger()
        ledger.record(
            _decision("d1", run_id="traj-1", verdict=Verdict.APPROVED, target=_target("remote-a"))
        )
        ledger.record(
            _decision(
                "d2",
                run_id="traj-1",
                verdict=Verdict.DENIED,
                target=_target("remote-b"),
                decided_at=MIDDAY + timedelta(minutes=1),
            )
        )
        ledger.record(
            _decision(
                "d3",
                run_id="traj-2",
                verdict=Verdict.APPROVED,
                target=_target("remote-a"),
                decided_at=MIDDAY + timedelta(minutes=2),
            )
        )
        return ledger

    def test_run_id_narrows(self) -> None:
        ids = [d.decision_id for d in self._populated().decisions(run_id="traj-1")]
        assert ids == ["d1", "d2"]

    def test_verdict_narrows(self) -> None:
        ids = [d.decision_id for d in self._populated().decisions(verdict=Verdict.DENIED)]
        assert ids == ["d2"]

    def test_target_narrows(self) -> None:
        ids = [d.decision_id for d in self._populated().decisions(target="remote-a")]
        assert ids == ["d1", "d3"]

    def test_since_is_inclusive(self) -> None:
        ids = [
            d.decision_id for d in self._populated().decisions(since=MIDDAY + timedelta(minutes=1))
        ]
        assert ids == ["d2", "d3"]

    def test_filters_combine_with_and(self) -> None:
        ids = [
            d.decision_id for d in self._populated().decisions(run_id="traj-1", target="remote-a")
        ]
        assert ids == ["d1"]

    def test_since_refuses_a_naive_bound(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            self._populated().decisions(since=MIDDAY.replace(tzinfo=None))


class TestOrdering:
    def test_decisions_come_back_ordered_by_decided_at_regardless_of_record_order(self) -> None:
        # decision_id is minted by the policy, not by this ledger (spec §7) — nothing here may
        # assume it reflects recording order, so a decision recorded out of chronological order
        # must still come back in decided_at order.
        ledger = InMemoryEgressLedger()
        later = _decision("z-later", decided_at=MIDDAY + timedelta(minutes=5))
        earlier = _decision("a-earlier", decided_at=MIDDAY)
        ledger.record(later)
        ledger.record(earlier)
        assert [d.decision_id for d in ledger.decisions()] == ["a-earlier", "z-later"]

    def test_a_tie_in_decided_at_breaks_on_decision_id(self) -> None:
        ledger = InMemoryEgressLedger()
        ledger.record(_decision("z"))
        ledger.record(_decision("a"))
        assert [d.decision_id for d in ledger.decisions()] == ["a", "z"]
