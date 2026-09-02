"""Value-object construction: what `EgressTarget`, `EgressRequest` and `EgressDecision` refuse.

Also covers spec §11 contract 1 (a denial is as durable as an approval — `evaluate` never raises
for one) and contract 6 (`VIOLATION` is constructible and serializable, though never produced by
the shipped policy — that half of the claim lives in `test_policy_matrix.py`).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from baseaicore import DataClassification

from commissioner import (
    EgressDecision,
    EgressRequest,
    EgressTarget,
    OrderedClassificationPolicy,
    Verdict,
)
from conftest import MIDDAY, ManualClock

_NAIVE = datetime(2026, 9, 2, 12, 0, 0)  # noqa: DTZ001 — deliberately naive, for a refusal test


class TestEgressTarget:
    def test_a_blank_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="name"):
            EgressTarget(name="  ", remote=False)

    def test_a_non_bool_remote_is_refused(self) -> None:
        with pytest.raises(ValueError, match="remote"):
            EgressTarget(name="t", remote="yes")  # type: ignore[arg-type]

    def test_a_non_classification_ceiling_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_data_classification"):
            EgressTarget(name="t", remote=True, max_data_classification="public")  # type: ignore[arg-type]

    def test_a_remote_target_with_no_ceiling_is_legitimately_constructible(self) -> None:
        # ADR-0054 rule 3: the fail-closed case is a value the type holds, not one it refuses.
        target = EgressTarget(name="t", remote=True)
        assert target.max_data_classification is None

    def test_provider_kind_defaults_to_none(self) -> None:
        assert EgressTarget(name="t", remote=False).provider_kind is None


class TestEgressRequest:
    def _target(self) -> EgressTarget:
        return EgressTarget(name="t", remote=False)

    def test_a_blank_run_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            EgressRequest(
                run_id="",
                source_ref="turn-1",
                data_classification=DataClassification.PUBLIC,
                target=self._target(),
            )

    def test_a_blank_source_ref_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source_ref"):
            EgressRequest(
                run_id="run-1",
                source_ref="   ",
                data_classification=DataClassification.PUBLIC,
                target=self._target(),
            )

    def test_a_non_classification_is_refused(self) -> None:
        with pytest.raises(ValueError, match="data_classification"):
            EgressRequest(
                run_id="run-1",
                source_ref="turn-1",
                data_classification="public",  # type: ignore[arg-type]
                target=self._target(),
            )

    def test_a_non_target_is_refused(self) -> None:
        with pytest.raises(ValueError, match="target"):
            EgressRequest(
                run_id="run-1",
                source_ref="turn-1",
                data_classification=DataClassification.PUBLIC,
                target="somewhere",  # type: ignore[arg-type]
            )

    def test_a_naive_requested_at_is_refused(self) -> None:
        with pytest.raises(ValueError, match="requested_at"):
            EgressRequest(
                run_id="run-1",
                source_ref="turn-1",
                data_classification=DataClassification.PUBLIC,
                target=self._target(),
                requested_at=_NAIVE,
            )

    def test_requested_at_defaults_to_none(self) -> None:
        request = EgressRequest(
            run_id="run-1",
            source_ref="turn-1",
            data_classification=DataClassification.PUBLIC,
            target=self._target(),
        )
        assert request.requested_at is None

    def test_an_aware_requested_at_is_accepted(self) -> None:
        request = EgressRequest(
            run_id="run-1",
            source_ref="turn-1",
            data_classification=DataClassification.PUBLIC,
            target=self._target(),
            requested_at=MIDDAY,
        )
        assert request.requested_at == MIDDAY


class TestEgressDecision:
    def _request(self) -> EgressRequest:
        return EgressRequest(
            run_id="run-1",
            source_ref="turn-1",
            data_classification=DataClassification.CONFIDENTIAL,
            target=EgressTarget(
                name="tools.agent.local_fast",
                remote=True,
                max_data_classification=DataClassification.PUBLIC,
            ),
        )

    def test_a_blank_decision_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="decision_id"):
            EgressDecision(
                decision_id="",
                request=self._request(),
                verdict=Verdict.VIOLATION,
                reason="local_tier_turn_answered_by_remote_provider",
                policy_name="PostHocVerification",
                policy_version="1.0",
                decided_at=MIDDAY,
            )

    def test_a_non_request_is_refused(self) -> None:
        with pytest.raises(ValueError, match="request"):
            EgressDecision(
                decision_id="dec-1",
                request="run-1",  # type: ignore[arg-type]
                verdict=Verdict.VIOLATION,
                reason="r",
                policy_name="p",
                policy_version="1.0",
                decided_at=MIDDAY,
            )

    def test_a_naive_decided_at_is_refused(self) -> None:
        with pytest.raises(ValueError, match="decided_at"):
            EgressDecision(
                decision_id="dec-1",
                request=self._request(),
                verdict=Verdict.VIOLATION,
                reason="local_tier_turn_answered_by_remote_provider",
                policy_name="PostHocVerification",
                policy_version="1.0",
                decided_at=_NAIVE,
            )

    def test_a_non_verdict_is_refused(self) -> None:
        with pytest.raises(ValueError, match="verdict"):
            EgressDecision(
                decision_id="dec-1",
                request=self._request(),
                verdict="violation",  # type: ignore[arg-type]
                reason="r",
                policy_name="p",
                policy_version="1.0",
                decided_at=MIDDAY,
            )

    def test_violation_is_constructible_and_carries_a_caller_supplied_reason(self) -> None:
        # ADR-0054 rule 7: VIOLATION is writable but never produced by the shipped policy — the
        # "never produced" half is asserted, over the whole matrix, in test_policy_matrix.py.
        decision = EgressDecision(
            decision_id="dec-1",
            request=self._request(),
            verdict=Verdict.VIOLATION,
            reason="local_tier_turn_answered_by_remote_provider",
            policy_name="PostHocVerification",
            policy_version="1.0",
            decided_at=MIDDAY,
        )
        assert decision.verdict is Verdict.VIOLATION
        payload = decision.to_payload()
        assert payload.verdict.value == "violation"
        assert EgressDecision.from_payload(payload) == decision

    def test_evaluate_never_raises_for_a_denial(self, clock: ManualClock) -> None:
        # Spec §11 contract 1, the construction-time half: a DENIED decision is an ordinary
        # return value, never an exception.
        policy = OrderedClassificationPolicy(clock=clock)
        request = EgressRequest(
            run_id="run-1",
            source_ref="turn-1",
            data_classification=DataClassification.CONFIDENTIAL,
            target=EgressTarget(name="frontier", remote=True),
        )
        decision = policy.evaluate(request)
        assert decision.verdict is Verdict.DENIED
