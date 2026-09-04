#!/usr/bin/env python3
"""Spec §20 criterion 2: a `setspec`-only script reads an exported decision and prints its verdict.

"The payload is the contract" — a decision `record_and_export.py` produced with `commissioner[sql]`
installed is read here by a virtualenv holding `setspec` alone. If this script imported
`commissioner`, it would prove nothing this criterion asks for.

Run it in a throwaway virtualenv holding `setspec` and nothing this package ships::

    python -m venv /tmp/commissioner-reader
    /tmp/commissioner-reader/bin/pip install setspec
    /tmp/commissioner-reader/bin/python acceptance/read_verdict.py <exported.json>

It exits ``0`` and prints the verdict when the file validates as a genuine
``governance.egress_decision`` payload denying egress, and non-zero with a message otherwise — so a
file that merely parses as JSON without being a real denial still fails this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError
from setspec.governance.v1 import EgressVerdict, GovernanceEgressDecisionIn


def _check(claim: str, condition: bool) -> None:  # noqa: FBT001 — a check takes the answer
    print(f"{'ok  ' if condition else 'FAIL'}  {claim}")
    if not condition:
        sys.exit(f"acceptance failed: {claim}")


def main(argv: list[str]) -> None:
    """Read the exported payload, validate it, and print what it decided."""
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <exported.json>")
    raw = Path(argv[1]).read_text(encoding="utf-8")

    try:
        payload = GovernanceEgressDecisionIn.model_validate_json(raw)
    except ValidationError as exc:
        sys.exit(f"acceptance failed: not a valid governance.egress_decision payload: {exc}")
    _check("the file validates as a governance.egress_decision payload", True)
    _check(
        "it describes a denial — the refusal record.py exported",
        payload.verdict is EgressVerdict.DENIED,
    )
    _check(
        "and it names the ceiling as the reason",
        payload.reason == "classification_exceeds_ceiling",
    )

    print(f"\nverdict:     {payload.verdict.value}")
    print(f"reason:      {payload.reason}")
    print(f"run_id:      {payload.request.run_id}")
    print(f"target:      {payload.request.target.name}")
    print(f"decided_at:  {payload.decided_at.isoformat()}")
    print("\nAll acceptance checks passed — read with setspec alone, Commissioner not installed.")


if __name__ == "__main__":
    main(sys.argv)
