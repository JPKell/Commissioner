"""Typed refusals — the error hierarchy Commissioner raises, per spec §7 and §13.

Every error subclasses :class:`baseaicore.SuiteError`, so a caller that already handles suite
errors handles these, and every ``code`` is part of the public contract.

One thing is deliberately absent: nothing here is raised for a denied egress request.
:func:`~commissioner.policy.OrderedClassificationPolicy.evaluate` never raises for a deny — a deny
is data, recorded through the same path as an approval
(:doc:`ADR-0054 <adr>` rule 4). A policy that raised on refusal would be the defect
:doc:`ADR-0053 <adr>` rejects for tool calls, arriving here instead.

:class:`StoreFailure` names the code Phase 2's ledger will raise on a write failure (spec §13); it
is declared now because it is part of the public API this phase publishes, even though nothing in
Phase 1 constructs one — Phase 1 has no store.
"""

from __future__ import annotations

from typing import ClassVar

from baseaicore import SuiteError

__all__ = ["CommissionerError", "StoreFailure"]


class CommissionerError(SuiteError):
    """Base for every error this package raises.

    Nothing raises it directly; it exists so a caller can catch every Commissioner refusal with one
    ``except`` without also catching unrelated suite errors.
    """

    code: ClassVar[str] = "COMMISSIONER_ERROR"


class StoreFailure(CommissionerError):
    """A ledger could not record a decision (spec §13).

    Raised by ``EgressLedger.record`` implementations in Phase 2, never by
    :mod:`commissioner.policy`. An unrecordable governance decision is not a decision that may
    proceed — PromptCadence's error table halts the turn rather than continuing unrecorded — and
    that policy is the caller's, stated here only as the typed signal it acts on.
    """

    code: ClassVar[str] = "EGRESS_STORE_FAILURE"
