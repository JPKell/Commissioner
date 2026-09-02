# SpotCheck

Turn the suite's egress *behaviour* into an egress *record*. Given a request — "may data of this
classification go to this target?" — SpotCheck answers deterministically and fail-closed, and
every verdict, approved or denied alike, is representable as SetSpec's
`governance.egress_decision` 1.0 so a reader can validate one with SpotCheck not installed.

**Status:** **Phase 1 complete, unreleased.** The pure core is implemented and tested — the value
objects, the shipped policy, and the payload round trip. `spotcheck.ledger`, `spotcheck.sql` and
the `spotcheck[sql]` extra arrive in Phase 2, which is also when `0.1.0` publishes; see
[docs/packages/spotcheck/development-plan.md](docs/packages/spotcheck/development-plan.md).

Part of the **Local AI Suite**.

## Install

Not on PyPI yet. Until `0.1.0` publishes at the end of Phase 2:

```bash
pip install -e .
```

## What it does, and what it refuses

* **It evaluates the ordered comparison, and nothing more.** `OrderedClassificationPolicy` is a
  local target (approved), a remote target with no declared ceiling (denied, fail closed), a
  classification at or under the ceiling (approved), or a classification over it (denied). It
  uses `baseaicore.DataClassification`'s own ordering and defines no ranking of its own.
* **It never enforces.** `evaluate` never raises for a deny — a deny is data, recorded exactly the
  way an approval is. SpotCheck makes no HTTP request and halts no caller; acting on a verdict is
  the caller's job.
* **It never guesses.** A remote target with no declared ceiling is denied, never assumed public.
  The absent value is the reason to refuse.
* **It carries no application policy.** What counts as `internal` in a deployment, which tiers
  exist, when a human must confirm: all caller-side. The package's only opinion is the ordering.
* **`VIOLATION` is writable but never produced here.** The shipped policy only ever answers
  `APPROVED` or `DENIED`; `VIOLATION` is a caller's own after-the-fact verification, and the type
  can hold and serialize one.

## Quickstart

```python
from datetime import UTC, datetime

from baseaicore import DataClassification
from spotcheck import EgressRequest, EgressTarget, OrderedClassificationPolicy

policy = OrderedClassificationPolicy(clock=lambda: datetime.now(UTC))

request = EgressRequest(
    run_id="traj-1",
    source_ref="turn-1",
    data_classification=DataClassification.INTERNAL,
    target=EgressTarget(
        name="tools.plan.remote_cheap",
        remote=True,
        max_data_classification=DataClassification.INTERNAL,
        provider_kind="openai_compatible",
    ),
)
decision = policy.evaluate(request)
print(decision.verdict.value, decision.reason)  # approved within_ceiling

payload = decision.to_payload()  # setspec.governance.v1.GovernanceEgressDecisionOut
assert type(decision).from_payload(payload) == decision
```

## Documentation

Project documentation lives under [`docs/`](docs/README.md).

| Read this | For |
|---|---|
| [docs/packages/spotcheck/spec.md](docs/packages/spotcheck/spec.md) | Purpose, scope, non-goals, public contracts, acceptance criteria |
| [docs/packages/spotcheck/development-plan.md](docs/packages/spotcheck/development-plan.md) | The phased build plan: goals, work, tests, acceptance criteria per phase |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pytest -m "not live and not performance"
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow and [`SECURITY.md`](SECURITY.md) for
how to report a vulnerability.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
