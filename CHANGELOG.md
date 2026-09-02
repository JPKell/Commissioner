# Changelog

All notable changes to `spotcheck` are documented here.
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
  rows (ADR-0054 rule 2); `SpotCheckError`/`StoreFailure`. No I/O, no SQL, no logging, no
  environment reads.

### Deferred
- `spotcheck.ledger`, `spotcheck.sql`, `mount_egress_tables`, `SqlEgressLedger` and the `[sql]`
  extra — Phase 2 (ADR-0054, mirroring LoadLedger's mounting pattern).
