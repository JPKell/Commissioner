"""``to_payload``/``from_payload`` against the *published* SetSpec goldens (spec §11 contract 4).

Every golden is read from the installed ``setspec 0.5.0`` wheel via
:mod:`setspec.artifacts` — never from a local copy — so a drift between what this package targets
and what SetSpec actually publishes fails here rather than downstream.
"""

from __future__ import annotations

from typing import Any

import pytest
from setspec import SchemaVersion, canonical_dumps
from setspec.artifacts import golden_names, golden_payloads
from setspec.governance.v1 import GovernanceEgressDecisionIn, GovernanceEgressDecisionOut

from commissioner import EgressDecision

_SCHEMA = "governance.egress_decision"
_VERSION = SchemaVersion(1, 0)

_GOLDEN_NAMES = golden_names(_SCHEMA, _VERSION)
_GOLDENS: dict[str, dict[str, Any]] = dict(
    zip(_GOLDEN_NAMES, golden_payloads(_SCHEMA, _VERSION), strict=True)
)


class TestGoldenRoundTrip:
    """Every published golden reads into an `EgressDecision` and back out unchanged."""

    @pytest.mark.parametrize("name", _GOLDEN_NAMES)
    def test_a_golden_reads_into_a_decision_and_back_out_byte_identical(self, name: str) -> None:
        raw = _GOLDENS[name]
        payload = GovernanceEgressDecisionIn.model_validate(raw)
        decision = EgressDecision.from_payload(payload)
        rebuilt = decision.to_payload()
        assert canonical_dumps(rebuilt) == canonical_dumps(raw)

    @pytest.mark.parametrize("name", _GOLDEN_NAMES)
    def test_from_payload_of_to_payload_is_the_identity(self, name: str) -> None:
        # spec §11 contract 4, stated the other direction: from_payload(to_payload(d)) == d.
        raw = _GOLDENS[name]
        decision = EgressDecision.from_payload(GovernanceEgressDecisionIn.model_validate(raw))
        again = EgressDecision.from_payload(decision.to_payload())
        assert again == decision

    @pytest.mark.parametrize("name", _GOLDEN_NAMES)
    def test_to_payload_writes_through_the_strict_writer_model_too(self, name: str) -> None:
        # to_payload() already returns a GovernanceEgressDecisionOut; this asserts the golden
        # itself validates against the strict (extra="forbid") half, not only the preserving one.
        raw = _GOLDENS[name]
        GovernanceEgressDecisionOut.model_validate(raw)

    def test_at_least_four_goldens_are_exercised(self) -> None:
        # A regression guard on this test module itself: if setspec ever published fewer goldens,
        # the parametrized tests above would silently run over a shorter list.
        assert set(_GOLDEN_NAMES) >= {"minimal", "full", "denied_no_ceiling", "violation"}


class TestUnknownMinorForwardCompatibility:
    """spec §11 contract 4 / gold standards §2: an unknown-minor field is read without loss."""

    def test_an_unknown_top_level_field_survives_validation_and_redump(self) -> None:
        augmented = dict(_GOLDENS["minimal"])
        augmented["future_field"] = "added-by-a-later-minor"
        payload = GovernanceEgressDecisionIn.model_validate(augmented)
        assert payload.extras == {"future_field": "added-by-a-later-minor"}
        redumped = payload.model_dump(mode="json", by_alias=True)
        assert redumped["future_field"] == "added-by-a-later-minor"

    def test_the_unknown_field_has_nowhere_to_land_on_the_value_object(self) -> None:
        # EgressDecision carries no `extras` bucket of its own — an unknown field is preserved by
        # the payload model a caller reads, not by this package's value objects, exactly as an
        # unknown field has nowhere to land on any value object a payload is read into.
        augmented = dict(_GOLDENS["minimal"])
        augmented["future_field"] = "added-by-a-later-minor"
        payload = GovernanceEgressDecisionIn.model_validate(augmented)
        decision = EgressDecision.from_payload(payload)
        assert not hasattr(decision, "future_field")
        assert not hasattr(decision, "extras")
