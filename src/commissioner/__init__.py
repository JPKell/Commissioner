"""Commissioner — records an egress verdict; enforcing it is the caller's (ADR-0054).

Layer 3 capability package. Turns the suite's egress *behaviour* into an egress *record*: given a
request ("may data of this classification go to this target?"), :class:`OrderedClassificationPolicy`
answers deterministically and fail-closed, and every verdict — approved or denied alike — is
representable as SetSpec's ``governance.egress_decision`` 1.0 so a reader can validate one with
Commissioner not installed. :class:`InMemoryEgressLedger` persists decisions for the life of this
process; mount :func:`commissioner.sql.mount_egress_tables` into an application's own database and
use :class:`commissioner.sql.SqlEgressLedger` for a ledger that survives one.

This module is pure: no I/O, no SQL, no logging, no environment.
``commissioner.sql`` and the ``commissioner[sql]`` extra are deliberately **not** imported here, so
installing this package never drags in an ORM (ADR-0050 decision 4). Import it explicitly:
``from commissioner.sql import SqlEgressLedger``.

    >>> from datetime import UTC, datetime
    >>> from baseaicore import DataClassification
    >>> from commissioner import (
    ...     EgressRequest, EgressTarget, InMemoryEgressLedger, OrderedClassificationPolicy
    ... )
    >>> clock = lambda: datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    >>> policy = OrderedClassificationPolicy(clock=clock)
    >>> ledger = InMemoryEgressLedger()
    >>> request = EgressRequest(
    ...     run_id="traj-1",
    ...     source_ref="turn-1",
    ...     data_classification=DataClassification.INTERNAL,
    ...     target=EgressTarget(name="remote-cheap", remote=True, max_data_classification=None),
    ... )
    >>> decision = policy.evaluate(request)
    >>> ledger.record(decision)
    >>> decision.verdict.value, decision.reason
    ('denied', 'no_ceiling_declared')
    >>> len(ledger.decisions(verdict=decision.verdict))
    1

Anything not listed in ``__all__`` is private and may change without a version bump, whatever its
module happens to be named. ``DataClassification`` is not re-exported: it is BaseAiCore's, and a
second name for one type is how two components stop agreeing about it.
"""

from __future__ import annotations

from commissioner.__about__ import __version__
from commissioner.errors import CommissionerError, StoreFailure, UnsupportedDialect
from commissioner.ledger import EgressLedger, InMemoryEgressLedger
from commissioner.policy import EgressPolicy, OrderedClassificationPolicy
from commissioner.types import EgressDecision, EgressRequest, EgressTarget, Verdict

__all__ = [
    "CommissionerError",
    "EgressDecision",
    "EgressLedger",
    "EgressPolicy",
    "EgressRequest",
    "EgressTarget",
    "InMemoryEgressLedger",
    "OrderedClassificationPolicy",
    "StoreFailure",
    "UnsupportedDialect",
    "Verdict",
    "__version__",
]
