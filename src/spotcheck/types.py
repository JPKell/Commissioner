"""The value objects an egress decision is made of (spec §7).

Pure data. Nothing here performs I/O, reads a clock, or decides anything — a target, a request,
and the decision one policy made about it, plus the payload conversion that lets a decision leave
the process as SetSpec's ``governance.egress_decision`` (ADR-0051 §4).

Two rules run through this module:

* **No parallel vocabulary.** Sensitivity is :class:`baseaicore.DataClassification`, ordered and
  caller-declared; this module adds no levels and no aliases of its own
  (ADR-0046, ADR-0054 rule 5).
* **No policy opinion in the shape.** A remote target with no declared ceiling is a legitimate,
  constructible :class:`EgressTarget` — it is what a policy *evaluates*, not something the type
  refuses to hold. Baking "approved implies a ceiling" into these types would reject a decision
  from any policy other than the shipped one (mirrors the same choice SetSpec's payload makes,
  B1 handoff §5c).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from baseaicore import DataClassification
from setspec.governance.v1 import (
    EgressRequestFields,
    EgressTargetFields,
    EgressVerdict,
    GovernanceEgressDecisionOut,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["EgressDecision", "EgressRequest", "EgressTarget", "Verdict"]


def _require_non_blank(value: object, *, field: str, owner: str) -> str:
    """Return ``value`` unchanged if it is a non-blank string; raise otherwise."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner}.{field} must be a non-blank string; got {value!r}.")
    return value


def _require_aware(value: datetime | None, *, field: str, owner: str) -> None:
    """Raise unless ``value`` is ``None`` or a timezone-aware instant."""
    if value is None:
        return
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{owner}.{field} must be timezone-aware or None; got a naive datetime. A naive "
            "instant has no defensible UTC reading (ADR-0016 sibling rule)."
        )


@dataclass(frozen=True, slots=True)
class EgressTarget:
    """Where a request's data would go.

    Attributes:
        name: The target's own name — a tier name for PromptCadence, a backend name for
            IdeaPress. Caller-defined; this package imposes no vocabulary on it.
        remote: Whether this target leaves the local machine.
        max_data_classification: The declared ceiling, or ``None`` when the target declares none.
            **Legitimately absent on a remote target**: that is the fail-closed case
            :class:`~spotcheck.policy.OrderedClassificationPolicy` must be able to deny
            (ADR-0054 rule 3), not a value this type refuses to hold.
        provider_kind: The provider kind serving this target, when known.

    Raises:
        ValueError: If ``name`` is blank, if ``remote`` is not a ``bool``, or if
            ``max_data_classification`` is neither ``None`` nor a
            :class:`~baseaicore.DataClassification`.
    """

    name: str
    remote: bool
    max_data_classification: DataClassification | None = None
    provider_kind: str | None = None

    def __post_init__(self) -> None:
        """Validate the target's shape.

        Raises:
            ValueError: As documented on the class.
        """
        _require_non_blank(self.name, field="name", owner="EgressTarget")
        if isinstance(self.remote, bool) is False:
            raise ValueError(f"EgressTarget.remote must be a bool; got {self.remote!r}.")
        if self.max_data_classification is not None and not isinstance(
            self.max_data_classification, DataClassification
        ):
            raise ValueError(
                "EgressTarget.max_data_classification must be a DataClassification or None; got "
                f"{type(self.max_data_classification).__name__!r}."
            )


@dataclass(frozen=True, slots=True)
class EgressRequest:
    """What is being evaluated: which run, which data, headed where.

    Attributes:
        run_id: The trajectory or stage-attempt identity this request is evaluated for. Opaque to
            this package.
        source_ref: A finer locator within the run — a turn id, step id or stage id.
        data_classification: How sensitive the data under evaluation is. Required and
            non-nullable, unlike :attr:`EgressTarget.max_data_classification`, which may
            legitimately be absent.
        target: Where the data would go.
        requested_at: When the caller built this request. ``None`` when not stated — the default
            an injected clock fills in downstream; it travels on the payload
            (governance.egress_decision §11 contract 4).

    Raises:
        ValueError: If ``run_id`` or ``source_ref`` is blank, if ``data_classification`` is not a
            :class:`~baseaicore.DataClassification`, if ``target`` is not an
            :class:`EgressTarget`, or if ``requested_at`` is naive.
    """

    run_id: str
    source_ref: str
    data_classification: DataClassification
    target: EgressTarget
    requested_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the request's shape.

        Raises:
            ValueError: As documented on the class.
        """
        _require_non_blank(self.run_id, field="run_id", owner="EgressRequest")
        _require_non_blank(self.source_ref, field="source_ref", owner="EgressRequest")
        if not isinstance(self.data_classification, DataClassification):
            raise ValueError(
                "EgressRequest.data_classification must be a DataClassification; got "
                f"{type(self.data_classification).__name__!r}."
            )
        if not isinstance(self.target, EgressTarget):
            raise ValueError(
                f"EgressRequest.target must be an EgressTarget; got {type(self.target).__name__!r}."
            )
        _require_aware(self.requested_at, field="requested_at", owner="EgressRequest")


class Verdict(StrEnum):
    """The three outcomes a recorded egress decision may hold (spec §7).

    ``VIOLATION`` is writable but never produced by the shipped policy — it is written by a
    caller's own verification step after the fact, when it finds egress that policy never
    approved (ADR-0054 rule 7).
    """

    APPROVED = "approved"
    DENIED = "denied"
    VIOLATION = "violation"


@dataclass(frozen=True, slots=True)
class EgressDecision:
    """One recorded verdict on "may this classification go to this target" (ADR-0054).

    A denial is as durable as an approval — nothing about this shape distinguishes how a decision
    was produced, and a consumer reading one never needs to.

    Attributes:
        decision_id: This decision's own identity.
        request: What was evaluated.
        verdict: What was decided.
        reason: A machine-readable reason — one of ``"within_ceiling"``,
            ``"classification_exceeds_ceiling"``, ``"target_not_remote"``,
            ``"no_ceiling_declared"`` for the shipped policy, or a caller-supplied string for a
            ``VIOLATION`` record written by a verification step.
        policy_name: Which policy produced this decision.
        policy_version: That policy's version — part of what makes the decision reproducible
            (ADR-0054 rule 3: same request and same policy version, same decision).
        decided_at: When this decision was made. Required, and never confused with
            :attr:`EgressRequest.requested_at` — this is the record's own timestamp.

    Raises:
        ValueError: If ``decision_id``, ``reason``, ``policy_name`` or ``policy_version`` is
            blank, if ``request`` is not an :class:`EgressRequest`, if ``verdict`` is not a
            :class:`Verdict`, or if ``decided_at`` is naive.
    """

    decision_id: str
    request: EgressRequest
    verdict: Verdict
    reason: str
    policy_name: str
    policy_version: str
    decided_at: datetime

    def __post_init__(self) -> None:
        """Validate the decision's shape.

        Raises:
            ValueError: As documented on the class.
        """
        _require_non_blank(self.decision_id, field="decision_id", owner="EgressDecision")
        if not isinstance(self.request, EgressRequest):
            raise ValueError(
                f"EgressDecision.request must be an EgressRequest; got "
                f"{type(self.request).__name__!r}."
            )
        if not isinstance(self.verdict, Verdict):
            raise ValueError(
                f"EgressDecision.verdict must be a Verdict; got {type(self.verdict).__name__!r}."
            )
        _require_non_blank(self.reason, field="reason", owner="EgressDecision")
        _require_non_blank(self.policy_name, field="policy_name", owner="EgressDecision")
        _require_non_blank(self.policy_version, field="policy_version", owner="EgressDecision")
        if (
            self.decided_at.tzinfo is None
            or self.decided_at.tzinfo.utcoffset(self.decided_at) is None
        ):
            raise ValueError(
                "EgressDecision.decided_at must be timezone-aware; got a naive datetime."
            )

    def to_payload(self) -> Any:  # noqa: ANN401 — a GovernanceEgressDecisionOut instance
        """Render this decision as SetSpec's ``governance.egress_decision`` 1.0 payload.

        The return type is ``Any``: ``GovernanceEgressDecisionOut`` is generated at import time by
        :func:`setspec.base.payload_models`, which mypy cannot resolve to a usable annotation
        (the same limitation FreeWeight's own ``wire_payload`` documents for
        ``CapabilityEvidenceOut``). The runtime value is exactly that class.

        Returns:
            A :class:`~setspec.governance.v1.GovernanceEgressDecisionOut`, field for field — the
            writer half, so a document this method produces can carry no field this build does
            not know about (ADR-0009 rule 5).
        """
        return GovernanceEgressDecisionOut(
            decision_id=self.decision_id,
            request=EgressRequestFields(
                run_id=self.request.run_id,
                source_ref=self.request.source_ref,
                data_classification=self.request.data_classification,
                target=EgressTargetFields(
                    name=self.request.target.name,
                    remote=self.request.target.remote,
                    max_data_classification=self.request.target.max_data_classification,
                    provider_kind=self.request.target.provider_kind,
                ),
                requested_at=self.request.requested_at,
            ),
            verdict=EgressVerdict(self.verdict.value),
            reason=self.reason,
            policy_name=self.policy_name,
            policy_version=self.policy_version,
            decided_at=self.decided_at,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> EgressDecision:  # noqa: ANN401 — see to_payload
        """Rebuild a decision from a validated ``governance.egress_decision`` payload.

        Args:
            payload: A :class:`~setspec.governance.v1.GovernanceEgressDecisionIn` (or ``Out``)
                this build's own SetSpec install already validated — this method performs no
                further validation of its own beyond what :class:`EgressDecision`'s constructor
                requires, and lets any error SetSpec raised during ``payload``'s own construction
                have already happened (spec §13: propagated, never wrapped).

        Returns:
            The equivalent :class:`EgressDecision`. ``from_payload(to_payload(d)) == d`` for every
            field (spec §11 contract 4); an unknown-minor field the payload carried in
            :attr:`~setspec.base.PayloadDefinition.extras` has nowhere to land on this value
            object, exactly as an unknown field has nowhere to land on any value object a payload
            is read into.
        """
        target = payload.request.target
        return cls(
            decision_id=payload.decision_id,
            request=EgressRequest(
                run_id=payload.request.run_id,
                source_ref=payload.request.source_ref,
                data_classification=payload.request.data_classification,
                target=EgressTarget(
                    name=target.name,
                    remote=target.remote,
                    max_data_classification=target.max_data_classification,
                    provider_kind=target.provider_kind,
                ),
                requested_at=payload.request.requested_at,
            ),
            verdict=Verdict(payload.verdict.value),
            reason=payload.reason,
            policy_name=payload.policy_name,
            policy_version=payload.policy_version,
            decided_at=payload.decided_at,
        )
