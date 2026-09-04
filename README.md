# Commissioner

Turn the suite's egress *behaviour* into an egress *record*. Given a request — "may data of this
classification go to this target?" — Commissioner answers deterministically and fail-closed, and
every verdict, approved or denied alike, is representable as SetSpec's
`governance.egress_decision` 1.0 so a reader can validate one with Commissioner not installed.

**Status:** **0.1.0.** The value objects, the shipped policy, the payload round trip,
`InMemoryEgressLedger`, the mountable table and `SqlEgressLedger` on SQLite and PostgreSQL; see
[docs/packages/commissioner/development-plan.md](docs/packages/commissioner/development-plan.md).

Part of the **Local AI Suite**.

## This package ships a table, not a database

The thing a new caller gets wrong, so it is above the fold. `commissioner.sql` does **not** own a
database. It adds one table to a `MetaData` **you** own, which then appears in **your**
`alembic revision --autogenerate` beside the tables you wrote yourself, upgrades with **your**
history, and is backed up, restored and pruned by whoever owns that database:

```python
# your_app/models.py — at module import, beside your own tables
import sqlalchemy as sa
from commissioner.sql import mount_egress_tables

metadata = sa.MetaData()
# ... your own tables ...
egress_tables = mount_egress_tables(metadata, prefix="egress_")
```

Commissioner holds no engine, opens no connection, reads no URL or environment variable, and runs
no migration — `create_all` appears nowhere in `src/`. Sessions arrive by injection. Two
applications mounting this table have **two tables in two databases — never one** (ADR-0050).

**Mount eagerly, at import.** Autogenerate only sees what was mounted before the metadata was
inspected, so a host that mounts lazily gets a migration that silently *drops* the table. There is
a test in this repository that does exactly that and watches it happen.

The table is append-only by design: `commissioner.sql` and `commissioner.ledger` expose exactly two
operations, `record` and `decisions` — no update, no delete, ever.

## Install

```bash
pip install commissioner          # the pure core: the payload, the policy comparison
pip install "commissioner[sql]"   # adds SQLAlchemy, mount_egress_tables and SqlEgressLedger
```

The core resolves to `baseaicore` and `setspec` and nothing else — a consumer that only wants the
policy comparison does not acquire an ORM to get it.

## What it does, and what it refuses

* **It evaluates the ordered comparison, and nothing more.** `OrderedClassificationPolicy` is a
  local target (approved), a remote target with no declared ceiling (denied, fail closed), a
  classification at or under the ceiling (approved), or a classification over it (denied). It
  uses `baseaicore.DataClassification`'s own ordering and defines no ranking of its own.
* **It never enforces.** `evaluate` never raises for a deny — a deny is data, recorded exactly the
  way an approval is. Commissioner makes no HTTP request and halts no caller; acting on a verdict is
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
from commissioner import EgressRequest, EgressTarget, OrderedClassificationPolicy

# A real caller injects its own clock; a fixed instant keeps this example's round trip exact —
# `to_payload()` truncates to millisecond precision, and `datetime.now()` rarely lands on one.
policy = OrderedClassificationPolicy(clock=lambda: datetime(2026, 9, 2, 12, 0, tzinfo=UTC))

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

Recording it — with `InMemoryEgressLedger` for a process-local double, or `SqlEgressLedger` for a
ledger that survives a restart:

```python
from commissioner import InMemoryEgressLedger

ledger = InMemoryEgressLedger()
ledger.record(decision)
ledger.decisions(verdict=decision.verdict)  # a denial is as queryable as an approval
```

```python
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from commissioner.sql import SqlEgressLedger, mount_egress_tables

metadata = sa.MetaData()
egress_tables = mount_egress_tables(metadata)
engine = sa.create_engine("sqlite:///egress.sqlite3")
metadata.create_all(engine, tables=list(egress_tables.all_tables))  # a real app migrates instead

ledger = SqlEgressLedger(sessionmaker(bind=engine))
ledger.record(decision)
ledger.decisions(run_id="traj-1")
```

## Durability, and what is promised about concurrent writers

`SqlEgressLedger` is `InMemoryEgressLedger` with a different store: both order history the same
way and reconstruct the same decision from the same fields, so a consumer that tests against the
in-memory ledger tests the same behaviour it will run in production.

Unlike a budget ledger, there is no shared aggregate — no balance, no running total — for two
writers to race over. Every decision is an independent row keyed by its own `decision_id`, recorded
with a single `INSERT`, so two processes recording two different decisions never contend for the
same row. A duplicate `decision_id` is a caller bug, not a retry to absorb: it raises
`StoreFailure` rather than being silently swallowed, because an "insert or ignore" path on an
append-only ledger is indistinguishable from a quiet update.

## Documentation

Project documentation lives under [`docs/`](docs/README.md).

| Read this | For |
|---|---|
| [docs/packages/commissioner/spec.md](docs/packages/commissioner/spec.md) | Purpose, scope, non-goals, public contracts, acceptance criteria |
| [docs/packages/commissioner/development-plan.md](docs/packages/commissioner/development-plan.md) | The phased build plan: goals, work, tests, acceptance criteria per phase |

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
