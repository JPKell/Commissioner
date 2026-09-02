"""The comparison, and nothing more (ADR-0054 rule 2).

:class:`EgressPolicy` is the protocol every policy implements; :class:`OrderedClassificationPolicy`
is the whole shipped policy, and it is exactly four rows:

.. code-block:: text

    local target                  -> APPROVED / "target_not_remote"
    remote, no declared ceiling   -> DENIED   / "no_ceiling_declared"     (fail closed)
    classification <= ceiling     -> APPROVED / "within_ceiling"
    classification >  ceiling     -> DENIED   / "classification_exceeds_ceiling"

It uses :class:`baseaicore.DataClassification`'s own ordering (``<=``) and defines no ranking of
its own. A remote target with no declared ceiling is denied, never assumed public — the absent
value is the reason to refuse, not a reason to guess (ADR-0054 rule 3).

Nothing here enforces anything: ``evaluate`` never raises for a deny, makes no HTTP request, and
halts no caller. Acting on a verdict is the caller's job (ADR-0054 rule 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from baseaicore import UlidGenerator

from commissioner.types import EgressDecision, EgressRequest, Verdict

if TYPE_CHECKING:
    from baseaicore import Clock, RandomnessSource

__all__ = ["EgressPolicy", "OrderedClassificationPolicy"]


@runtime_checkable
class EgressPolicy(Protocol):
    """What every Commissioner policy offers (spec §7).

    :class:`OrderedClassificationPolicy` implements it now. A caller may supply a different,
    legitimate implementation — per-provider ceilings, time-boxed approvals — and this package
    carries no opinion about which one is correct (ADR-0054 rule 6): the record shape already
    carries ``policy_name``/``policy_version``, so a second implementation arrives without a
    schema change.
    """

    name: str
    version: str

    def evaluate(self, request: EgressRequest) -> EgressDecision:
        """Decide one request and return the recorded decision.

        Deterministic given the same request and the same policy state (spec §11 contract 3).
        Never raises for a deny — a deny is data (ADR-0054 rule 4).
        """
        ...


class OrderedClassificationPolicy:
    """The shipped policy: classification compared against a target's declared ceiling.

    Thread-safe: id generation is the only shared state, and it is already synchronized by
    :class:`~baseaicore.UlidGenerator`.
    """

    __slots__ = ("_clock", "_ids", "name", "version")

    name: str
    version: str

    def __init__(self, *, clock: Clock, randomness_source: RandomnessSource | None = None) -> None:
        """Build the policy over an injected clock and id generator.

        Args:
            clock: Returns the current timezone-aware instant. Injected and required — a policy
                that read the system clock directly could not produce a reproducible golden
                decision, which spec §11 contract 3 requires.
            randomness_source: Where ``decision_id``'s random bits come from, forwarded to the
                internal :class:`~baseaicore.UlidGenerator`. Defaults to
                :class:`random.SystemRandom`; a test wanting byte-identical ``decision_id`` values
                across two policy instances passes a seeded :class:`random.Random` (spec §11
                contract 3 — "ids ... injected").
        """
        self.name = "OrderedClassificationPolicy"
        self.version = "1.0"
        self._clock: Clock = clock
        self._ids = UlidGenerator(clock=clock, randomness_source=randomness_source)

    def evaluate(self, request: EgressRequest) -> EgressDecision:
        """Decide one request against its target's declared ceiling.

        Args:
            request: What to evaluate — already a valid :class:`~commissioner.types.EgressRequest`.

        Returns:
            The decision, with a freshly drawn id and the current instant from the injected
            clock. Exactly one of the four documented ``(verdict, reason)`` pairs; asserted
            exhaustively, over the full classification × target matrix, by this package's own
            test suite.
        """
        target = request.target
        if not target.remote:
            verdict, reason = Verdict.APPROVED, "target_not_remote"
        elif target.max_data_classification is None:
            # Fail closed: absence is the reason to refuse, not a reason to guess (ADR-0054 §3).
            verdict, reason = Verdict.DENIED, "no_ceiling_declared"
        elif request.data_classification <= target.max_data_classification:
            verdict, reason = Verdict.APPROVED, "within_ceiling"
        else:
            verdict, reason = Verdict.DENIED, "classification_exceeds_ceiling"

        return EgressDecision(
            decision_id=self._ids.new_id(),
            request=request,
            verdict=verdict,
            reason=reason,
            policy_name=self.name,
            policy_version=self.version,
            decided_at=self._clock(),
        )
