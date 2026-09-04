# Changelog

All notable changes to `commissioner` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/), pre-1.0 per
packaging and release standards §3.

## [Unreleased]

### Added
- Repository scaffold: toolchain copied from `py/LoadLedger` (hatchling, ruff, mypy strict,
  import-linter, pytest with `pytest-randomly`, hash-pinned `requirements/` locks, CI and release
  workflows), with the `setspec` dependency and its import-linter allowance added (this package,
  like MirrorWall, is permitted to import SetSpec — master architecture §2).
- Phase 1, the pure core: `EgressTarget`, `EgressRequest`, `Verdict`, `EgressDecision` with
  `to_payload`/`from_payload` against `setspec.governance.v1`'s `governance.egress_decision` 1.0;
  the `EgressPolicy` protocol and `OrderedClassificationPolicy`, the whole shipped policy in four
  rows (ADR-0054 rule 2); `CommissionerError`/`StoreFailure`. No I/O, no SQL, no logging, no
  environment reads.
- Phase 2, the durable half: `commissioner.ledger` and `commissioner.sql` under the new
  `commissioner[sql]` extra (ADR-0050, LoadLedger's mounting pattern copied by hand — the roadmap's
  revisit trigger is a third mountable package).
  - `EgressLedger` protocol and `InMemoryEgressLedger` — a process-local, thread-safe ledger with
    no store of its own.
  - `mount_egress_tables(metadata, *, prefix="egress_") -> EgressTables` adds one table,
    `{prefix}decisions`, to a host's own `MetaData`: `decision_id` (primary key), `run_id`,
    `verdict`, `target_name` and `decided_at` as indexed, filterable columns, and
    `decision_json` — the decision's own `governance.egress_decision` payload, in canonical form —
    as the byte-stable record. `verdict` is stored as plain text, never `sa.Enum`, so the same
    column type renders on both dialects.
  - `SqlEgressLedger(session_factory, *, table_prefix="egress_")` — `record`/`decisions` over a
    host-owned session factory; no engine, no URL, no migration. Refuses a dialect other than
    SQLite or PostgreSQL with `UnsupportedDialect`, and a `decision_id` this ledger has already
    recorded with `StoreFailure`, rather than silently absorbing either.
  - `UnsupportedDialect` (`EGRESS_UNSUPPORTED_DIALECT`), added to the error hierarchy alongside
    `StoreFailure`.
  - The append-only surface is asserted structurally, not merely documented: neither
    `commissioner.ledger` nor `commissioner.sql` exposes an update or delete path, over both the
    protocol and both implementations.
- `.importlinter`'s `no-sql-in-phase-1` contract replaced by
  `only-the-sql-module-imports-sqlalchemy`: `sqlalchemy` is now permitted in `commissioner.sql`
  alone, and nothing in the package imports `alembic` at runtime (the host owns every migration).
- The `[sql]` extra: `sqlalchemy>=2,<3`. The pure core still resolves to `baseaicore` and `setspec`
  alone.
