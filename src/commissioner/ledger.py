"""``EgressLedger`` — the protocol, and the process-local double (spec §7, §10, §14).

``InMemoryEgressLedger`` is first-class, not a stub: it implements the whole protocol
:class:`~commissioner.sql.SqlEgressLedger` does, over the same filtering rules, so a consumer that
tests against this one is testing the ordering and the filters it will run in production. What it
does not do is survive the process — it owns no storage (spec §10: Commissioner owns no data). For
a ledger that does survive, mount the tables into an application's own database and use
:class:`~commissioner.sql.SqlEgressLedger`; it is observably this ledger with a different store.

**Append-only, and that is a surface property.** Neither this module nor :mod:`commissioner.sql`
exposes an update or delete path (spec §14) — the audit surface a caller relies on is a value only
if nothing here can rewrite history. ``tests/unit/test_ledger_surface.py`` asserts this
structurally, over both implementations, rather than leaving it to code review.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol, runtime_checkable

__all__ = ["EgressLedger", "InMemoryEgressLedger"]

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from commissioner.types import EgressDecision, Verdict


@runtime_checkable
class EgressLedger(Protocol):
    """What every Commissioner ledger offers (spec §7).

    :class:`InMemoryEgressLedger` and :class:`~commissioner.sql.SqlEgressLedger` both implement
    it. A caller written against this protocol never learns which one it holds, which is the
    point: the in-memory ledger is the deterministic double later phases and other applications
    test against, not a stub with a reduced surface.
    """

    def record(self, decision: EgressDecision) -> None:
        """Persist one decision — approved, denied or a written-after-the-fact violation alike.

        A denial is as durable as an approval (spec §11 contract 1): nothing about this method
        distinguishes how a decision was produced, and it never raises for a deny — a deny is
        already data by the time it reaches here (ADR-0054 rule 4).

        Raises:
            commissioner.errors.StoreFailure: If the decision could not be written. The caller
                decides whether to proceed unrecorded (spec §13); Commissioner does not.
        """
        ...

    def decisions(
        self,
        *,
        run_id: str | None = None,
        verdict: Verdict | None = None,
        target: str | None = None,
        since: datetime | None = None,
    ) -> Sequence[EgressDecision]:
        """Return recorded decisions, oldest-decided first, narrowed by whichever filters are given.

        Side-effect-free: no implementation of this protocol may write through this method.

        Raises:
            ValueError: If ``since`` is naive.
        """
        ...


class InMemoryEgressLedger:
    """A ledger held in this process's memory, for the life of this process.

    Thread-safe: every public method takes one lock, so a record and the list a concurrent
    ``decisions()`` call sees are never torn.
    """

    __slots__ = ("_decisions", "_lock")

    def __init__(self) -> None:
        """Build an empty ledger.

        Nothing is configured: unlike a budget ledger, an egress ledger evaluates nothing of its
        own — it only persists decisions an :class:`~commissioner.policy.EgressPolicy` already
        made.
        """
        self._decisions: list[EgressDecision] = []
        self._lock = threading.Lock()

    def record(self, decision: EgressDecision) -> None:
        """Append one decision.

        Args:
            decision: The decision to persist, already fully formed — this ledger draws no id and
                reads no clock; every field on the record is the caller's.
        """
        with self._lock:
            self._decisions.append(decision)

    def decisions(
        self,
        *,
        run_id: str | None = None,
        verdict: Verdict | None = None,
        target: str | None = None,
        since: datetime | None = None,
    ) -> Sequence[EgressDecision]:
        """Return recorded decisions, oldest-decided first, narrowed by whichever filters are given.

        Ordering is by ``(decided_at, decision_id)`` rather than insertion order: unlike a budget
        ledger's entry id, ``decision_id`` is minted by the policy before this ledger ever sees the
        decision (spec §7), so nothing here can assume it reflects recording order.
        :class:`~commissioner.sql.SqlEgressLedger` orders its query the same way, so the two
        implementations agree independent of the order ``record`` was called in.

        Args:
            run_id: Keep only decisions for this run.
            verdict: Keep only decisions with this verdict.
            target: Keep only decisions whose request targeted this target name.
            since: Keep only decisions decided at or after this instant. The window is half-open —
                ``decided_at >= since`` — so two consecutive queries with touching bounds return
                each decision exactly once.

        Returns:
            A tuple, oldest-decided first. Filters combine with AND.

        Raises:
            ValueError: If ``since`` is naive. Comparing a naive bound against stored aware
                instants would silently shift the window by the reader's local offset.
        """
        if since is not None and (since.tzinfo is None or since.tzinfo.utcoffset(since) is None):
            raise ValueError(
                "decisions(since=...) requires a timezone-aware instant; got a naive one."
            )
        with self._lock:
            snapshot = tuple(self._decisions)
        narrowed = (
            decision
            for decision in snapshot
            if (run_id is None or decision.request.run_id == run_id)
            and (verdict is None or decision.verdict == verdict)
            and (target is None or decision.request.target.name == target)
            and (since is None or decision.decided_at >= since)
        )
        return tuple(sorted(narrowed, key=_ordering_key))


def _ordering_key(decision: EgressDecision) -> tuple[datetime, str]:
    """Return the ``(decided_at, decision_id)`` pair both ledger implementations sort history by.

    ``decided_at`` first, since that is when this record was actually made; ``decision_id`` breaks
    a tie between two decisions made at the same instant, which a coarse clock in a test can
    produce even though a real one rarely will.
    """
    return (decision.decided_at, decision.decision_id)
