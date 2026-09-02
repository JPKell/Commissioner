# Commissioner — Development Plan

**Sequence position:** PromptCadence arc, stream P ([roadmap §4](../../roadmap/promptcadence-roadmap.md)).
Depends on `baseaicore>=0.4.1` (`DataClassification`) and `setspec>=0.5`
(`governance.egress_decision` — SetSpec Phase 6 in the roadmap must land first).
**Target:** `commissioner 0.1.0` at the end of Phase 2.

The smallest package in the arc, on purpose: the skeleton's open question was whether Commissioner
deserves to exist as a package at all, and the answer (D-10) holds only while it stays exactly
this small — the shared shape, the shared comparison, the shared ledger, nothing else.

---

## Phase 1 — Vocabulary binding, policy, decision, payload round-trip

**Goal:** the evaluation and the contract; deterministic and fail-closed.

**Prerequisites:** `setspec 0.5.0` published with `governance.egress_decision` 1.0 and its
goldens; the D-2 (`DataClassification`) and D-10 ADRs accepted.

**Work**
* Repository skeleton (standard toolchain).
* `types.py`: `EgressTarget`, `EgressRequest`, `Verdict`, `EgressDecision` with
  `to_payload`/`from_payload`.
* `policy.py`: `EgressPolicy` protocol, `OrderedClassificationPolicy` with the four documented
  outcomes, fail-closed on a missing ceiling.
* `errors.py`.

**Files/subsystems**
```text
src/commissioner/{__init__,__about__,types,policy,errors}.py
tests/unit/{test_policy_matrix,test_decision,test_payload_roundtrip}.py
```

**Tests**
* The full policy matrix: every classification × {local, remote-with-ceiling(each level),
  remote-without-ceiling}; reasons exact.
* Round-trip against SetSpec goldens; an unknown-minor payload read without loss.
* Determinism goldens (injected ids and clock).
* `VIOLATION` is constructible and serializable but never produced by the shipped policy
  (asserted over the matrix).

**Acceptance criteria**
1. `mypy --strict` clean; coverage ≥ 95 %; the matrix is exhaustive by construction
   (parametrized over the enums, not hand-listed).

**Known risks:** scope creep toward enforcement or application policy. Mitigated by the non-goals
list and D-10's explicit bar.
**Likely failure modes:** a reason string drifting from the documented set; payload extras lost on
round-trip.
**Gold standards:** fail closed; denial as durable as approval; no parallel vocabulary.
**Deferred:** persistence.

---

## Phase 2 — Ledgers and mounting — publish 0.1.0

**Goal:** the durable, append-only record, mounted the LoadLedger way.

**Prerequisites:** Phase 1; LoadLedger Phase 2 (the mounting pattern's tests exist to copy).

**Work**
* `ledger.py`: `EgressLedger` protocol, `InMemoryEgressLedger`, `SqlEgressLedger`; filtered
  queries.
* `sql.py`: `mount_egress_tables(metadata, prefix)` — same rules as `loadledger.sql` (plain
  types, no engine, host-owned migrations); append-only surface (no update/delete API).
* The `[sql]` extra; README; publish `commissioner 0.1.0`.

**Files/subsystems**
```text
src/commissioner/{ledger,sql}.py
tests/integration/{test_sql_ledger,test_mounting}.py     # both dialects; miniature host reused
```

**Tests**
* Approvals and denials persisted symmetrically; `VIOLATION` rows accepted.
* Filters (run, verdict, target, since) on 100 000 rows within budget.
* Mounting suite mirrored from LoadLedger (two hosts, autogenerate, prefix).
* API-surface test: no mutation path exists.

**Acceptance criteria**
1. Spec §20 criteria met, including the `setspec`-only reader script (criterion 2).
2. `commissioner 0.1.0` published; `pip install commissioner[sql]` resolves standalone.

**Known risks:** duplicated mounting machinery drifting from LoadLedger's. Accepted for v1 (two
small copies beat a premature shared base); the roadmap's revisit trigger names a third mountable
package as the extraction point.
**Likely failure modes:** a query path added for a UI that quietly becomes an update path;
dialect-specific enum storage.
**Gold standards:** append-only audit surface; both dialects green; ≥ 95 % coverage.
**Deferred:** IdeaPress badge adoption (roadmap §6); aggregation helpers; richer policies.
