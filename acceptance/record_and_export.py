#!/usr/bin/env python3
"""Spec §20 criterion 1: a confidential trajectory can never reach a remote tier, end to end.

This is PromptCadence's own acceptance criterion 4 (roadmap), run through Commissioner alone,
since PromptCadence has no code yet — it is specified, not implemented (workspace `CLAUDE.md`).
The claim this script proves: a `CONFIDENTIAL` request aimed at a remote tier whose declared
ceiling cannot admit it is refused, the refusal is recorded exactly as durably as an approval, and
the refusal is queryable afterward by verdict alone — a UI or an auditor asking "what did this
policy ever deny?" gets a real, persisted answer.

It also produces the input to `read_verdict.py`, which is spec §20 criterion 2's other half: this
script uses `commissioner[sql]` to evaluate, record and export a decision; `read_verdict.py` reads
that export back using `setspec` alone, no Commissioner installed.

Run it in a throwaway virtualenv holding `commissioner[sql]`::

    python -m venv /tmp/commissioner-acceptance
    /tmp/commissioner-acceptance/bin/pip install ".[sql]"
    /tmp/commissioner-acceptance/bin/python acceptance/record_and_export.py <output.json>

It exits ``0`` when every claim below holds and non-zero with a message when one does not, so it is
a check rather than a demonstration — M10's exit condition is *"clean-venv acceptance scripts
pass"*, and a script that only printed things would pass while being wrong. Nothing here imports
pytest or this repository's test helpers: the point is that an application with `commissioner[sql]`
installed and nothing else can do this.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from baseaicore import DataClassification
from setspec import canonical_dumps
from sqlalchemy.orm import sessionmaker

from commissioner import EgressRequest, EgressTarget, Verdict
from commissioner.policy import OrderedClassificationPolicy
from commissioner.sql import SqlEgressLedger, mount_egress_tables

WHEN = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _check(claim: str, condition: bool) -> None:  # noqa: FBT001 — a check takes the answer
    """Print the claim and stop the script if it does not hold."""
    print(f"{'ok  ' if condition else 'FAIL'}  {claim}")
    if not condition:
        sys.exit(f"acceptance failed: {claim}")


def main(argv: list[str]) -> None:
    """Evaluate, record, query and export — the shape spec §20 criteria 1 and 2 ask for."""
    output = Path(argv[1]) if len(argv) > 1 else Path("exported_decision.json")

    with tempfile.TemporaryDirectory(prefix="commissioner-acceptance-") as temporary:
        engine = sa.create_engine(f"sqlite:///{Path(temporary) / 'egress.sqlite3'}")
        metadata = sa.MetaData()
        tables = mount_egress_tables(metadata)
        metadata.create_all(engine, tables=list(tables.all_tables))
        ledger = SqlEgressLedger(sessionmaker(bind=engine))
        policy = OrderedClassificationPolicy(clock=lambda: WHEN)

        # The refused case: confidential data, a remote tier whose declared ceiling is INTERNAL.
        remote_tier = EgressTarget(
            name="tier.remote_cheap",
            remote=True,
            max_data_classification=DataClassification.INTERNAL,
        )
        confidential_remote = policy.evaluate(
            EgressRequest(
                run_id="traj-acceptance-1",
                source_ref="turn-1",
                data_classification=DataClassification.CONFIDENTIAL,
                target=remote_tier,
            )
        )
        _check(
            "a confidential request to a remote tier is denied",
            confidential_remote.verdict is Verdict.DENIED,
        )
        _check(
            "and it names the ceiling as the reason",
            confidential_remote.reason == "classification_exceeds_ceiling",
        )

        # The contrast case, so the test is not merely "everything is denied": the same data,
        # evaluated against the local tier, is approved — confidentiality does not block local work.
        local_tier = EgressTarget(name="tier.local", remote=False)
        confidential_local = policy.evaluate(
            EgressRequest(
                run_id="traj-acceptance-1",
                source_ref="turn-2",
                data_classification=DataClassification.CONFIDENTIAL,
                target=local_tier,
            )
        )
        _check(
            "the same confidential data reaches the local tier",
            confidential_local.verdict is Verdict.APPROVED,
        )

        ledger.record(confidential_remote)
        ledger.record(confidential_local)

        # The claim that matters: the refusal is recorded exactly as durably as the approval, and
        # it is queryable by verdict alone — not merely present in a full dump of everything.
        denied = ledger.decisions(run_id="traj-acceptance-1", verdict=Verdict.DENIED)
        _check("the denial is queryable by verdict alone", len(denied) == 1)
        _check(
            "and it is the confidential-to-remote decision, not some other row",
            denied[0].decision_id == confidential_remote.decision_id,
        )
        approved = ledger.decisions(run_id="traj-acceptance-1", verdict=Verdict.APPROVED)
        _check("the approval is recorded beside it, symmetrically", len(approved) == 1)

        # Never approved for the remote tier, across the whole recorded history for this run —
        # the structural half of "can never reach a remote tier", not just this one decision.
        confidential_ever_approved_remote = any(
            decision.verdict is Verdict.APPROVED and decision.request.target.remote
            for decision in ledger.decisions(run_id="traj-acceptance-1")
            if decision.request.data_classification is DataClassification.CONFIDENTIAL
        )
        _check(
            "no confidential decision in this run was ever approved for a remote target",
            not confidential_ever_approved_remote,
        )

        output.write_text(canonical_dumps(confidential_remote.to_payload()), encoding="utf-8")
        _check(f"the denial was exported to {output}", output.exists())

    print("\nAll acceptance checks passed.")


if __name__ == "__main__":
    main(sys.argv)
