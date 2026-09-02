# Commissioner — Specification

**Type:** Python package · **Import/distribution name:** `commissioner` · **Layer:** 3 (capability package)
**Status:** Specified, not implemented. Part of the PromptCadence arc
([roadmap](../../roadmap/promptcadence-roadmap.md)); decision record D-10 resolves the skeleton's open
question — a package **and** a SetSpec payload: the decision *shape* is the cross-application
contract (`governance.egress_decision` v1), the evaluation and the ledger are the small shared
implementation, and anything application-specific stays out.

---

## 1. Purpose

Turn the suite's egress *behaviour* into an egress *record*. The behaviour exists — LoadCoach's
`allow_remote` opt-in, IdeaPress's per-stage egress badge (risk S4:
"private content leaving the machine via a remote backend",
[Risks](../../apps/ideapress/risks.md)) — but no durable, queryable record of each decision does.
Commissioner evaluates "may data of this classification go to this target?" against an ordered
lattice, and persists every verdict — approved or denied — as a versioned payload, so "what left
this machine, when, and under whose policy?" has an answer that outlives the process.

## 2. Scope

* `EgressRequest` / `EgressDecision` value objects, serializable as SetSpec
  `governance.egress_decision` 1.0.
* The `EgressPolicy` protocol and the shipped `OrderedClassificationPolicy` (classification ≤
  target ceiling, with explicit deny reasons).
* The `EgressLedger` protocol, `InMemoryEgressLedger`, and `SqlEgressLedger` over mountable models
  (`commissioner.sql`, same pattern as LoadLedger — roadmap §2, D-6).
* Query surface: by run, by verdict, by target, by time window.

## 3. Explicit non-goals

* **No enforcement.** Commissioner renders and records verdicts; *acting* on a deny — refusing the
  call, halting the trajectory, painting the badge — is the application's job. A package cannot
  intercept an HTTP call it does not make.
* **No network inspection.** The evaluated facts are what the caller declares (classification,
  target); Commissioner is a policy-and-ledger, not a proxy or a firewall.
* No application-specific policy: what counts as `internal` in a given deployment, which tiers
  exist, when a human must confirm — all caller-side. The shipped policy is exactly the ordered
  comparison and nothing more.
* No persistence ownership: mountable models, application-owned tables, as with LoadLedger.

## 4. Responsibilities

| Responsibility | Detail |
|---|---|
| Vocabulary | Use `baseaicore.DataClassification` (ordered: `PUBLIC < INTERNAL < CONFIDENTIAL`); never define a parallel taxonomy |
| Evaluation | `evaluate(request) -> EgressDecision`, deterministic, with a machine-readable reason on every deny |
| Record | Persist every decision — approved and denied alike — with policy name/version and timestamps |
| Contract | Serialize decisions as SetSpec `governance.egress_decision` 1.0 so IdeaPress's badge (or any tool) reads them without Commissioner installed |
| Query | Filterable history for UIs, explanations and audits |

## 5. Dependencies

`baseaicore`, `setspec>=0.5,<0.6` (the payload — a cross-application shape, which is the one
justified reason a capability package imports SetSpec). `commissioner.sql` additionally imports
`sqlalchemy>=2,<3` (extra: `commissioner[sql]`).

## 6. Consumers

PromptCadence (every turn's egress verdict), IdeaPress (the S4 egress badge backed by real records —
adoption phase, [roadmap §6](../../roadmap/promptcadence-roadmap.md)).

## 7. Public API

```python
@dataclass(frozen=True, slots=True)
class EgressTarget:
    name: str                          # PromptCadence: the tier name; IdeaPress: the backend name
    remote: bool
    max_data_classification: DataClassification | None   # required when remote
    provider_kind: str | None = None

@dataclass(frozen=True, slots=True)
class EgressRequest:
    run_id: str                        # trajectory / stage-attempt identity
    source_ref: str                    # turn id, step id, stage id
    data_classification: DataClassification
    target: EgressTarget
    requested_at: datetime | None = None    # default: injected clock; travels on the payload

class Verdict(Enum): APPROVED = "approved"; DENIED = "denied"; VIOLATION = "violation"
# VIOLATION: recorded after the fact when verification finds egress that policy never approved
# (PromptCadence contract 4 — a local-tier turn answered by a remote provider)

@dataclass(frozen=True, slots=True)
class EgressDecision:
    decision_id: str
    request: EgressRequest
    verdict: Verdict
    reason: str                        # "within_ceiling" | "classification_exceeds_ceiling" |
                                       # "target_not_remote" | "no_ceiling_declared" | caller-supplied
    policy_name: str
    policy_version: str
    decided_at: datetime
    def to_payload(self) -> GovernanceEgressDecision: ...    # setspec.governance.v1
    @classmethod
    def from_payload(cls, payload: GovernanceEgressDecision) -> "EgressDecision": ...

class EgressPolicy(Protocol):
    name: str
    version: str
    def evaluate(self, request: EgressRequest) -> EgressDecision: ...

OrderedClassificationPolicy()
# local target                       → APPROVED / "target_not_remote"
# remote, no declared ceiling        → DENIED   / "no_ceiling_declared"   (fail closed)
# classification ≤ ceiling           → APPROVED / "within_ceiling"
# classification > ceiling           → DENIED   / "classification_exceeds_ceiling"

class EgressLedger(Protocol):
    def record(self, decision: EgressDecision) -> None: ...
    def decisions(self, *, run_id: str | None = None, verdict: Verdict | None = None,
                  target: str | None = None, since: datetime | None = None
                  ) -> Sequence[EgressDecision]: ...

InMemoryEgressLedger()
SqlEgressLedger(session_factory, *, table_prefix: str = "egress_")
def mount_egress_tables(metadata: MetaData, *, prefix: str = "egress_") -> EgressTables: ...

# Errors (subclass baseaicore.SuiteError)
CommissionerError            COMMISSIONER_ERROR
└── StoreFailure          EGRESS_STORE_FAILURE
```

## 8. Inputs

Egress requests built by the caller from its own facts; policy configuration; an injected clock.

## 9. Outputs

`EgressDecision`s (in-process and as SetSpec payloads), decision history, typed errors.

## 10. Data ownership

None of its own; mountable models, application-owned tables, exactly as
[LoadLedger §10](../loadledger/spec.md).

## 11. Public contracts

1. **A denial is as durable as an approval.** `evaluate` never raises for a deny; the caller
   records both through the same `record()` and a test asserts the ledger holds them
   symmetrically.
2. **Fail closed**: a remote target with no declared ceiling is denied, never assumed `public`.
3. **Determinism**: same request + same policy version ⇒ identical decision (ids and timestamps
   injected), golden-tested.
4. **Round-trip**: `from_payload(to_payload(d))` preserves every field; the payload validates
   against SetSpec's committed schema and goldens. *Every* field is literal: `requested_at` is on
   `governance.egress_decision` `1.0` for no other reason than this contract, since a value-object
   field with nowhere to land makes the round trip silently lossy. It is nullable there exactly as
   it is here, and it is never confused with `decided_at`, which is the record's own timestamp and
   is required.
5. **No parallel vocabulary**: the classification type is `baseaicore.DataClassification`;
   Commissioner adds no levels and no aliases.
6. The `VIOLATION` verdict is only ever written by a caller's verification step — the shipped
   policy never produces it, and the ledger accepts it, because an after-the-fact violation is a
   governance fact the record must be able to hold.

## 12. Configuration

Constructor arguments only.

## 13. Error behaviour

| Condition | Behaviour |
|---|---|
| Remote target without a ceiling | A `DENIED` decision — data, not an exception |
| Store write failure | `StoreFailure`; the caller decides whether to proceed unrecorded (PromptCadence does not — an unrecordable decision halts the turn) |
| Malformed payload on `from_payload` | SetSpec's `ValidationError`, propagated |

## 14. Security considerations

Decisions carry classifications, target names and references — never content. The ledger is the
audit surface for the suite's most sensitive question, so `commissioner.sql` rows are append-only by
convention and the package exposes no update or delete.

## 15. Performance

| Measure | Target |
|---|---|
| `record` (`SqlEgressLedger`, SQLite) | ≤ 5 ms |
| `decisions` over 100 000 rows, filtered | ≤ 200 ms |

`evaluate` carries no budget: it is a pure in-memory comparison sitting beside model calls
measured in seconds, and a microsecond-scale target would be measurement fuss, not protection —
the consumer's per-turn overhead budget already contains it.

## 16. Cross-platform

Pure Python; fully portable.

## 17. Observability

No logging. A decision is exactly the body of the suite's `egress.evaluated` event.

## 18. Test strategy

| Area | Tests |
|---|---|
| Policy matrix | Every classification × every target shape, including the fail-closed row |
| Symmetry | Denials persisted and queryable identically to approvals |
| Round-trip | Payload round-trip against SetSpec goldens; unknown-minor payload read without loss |
| Determinism | Golden decisions across the CI matrix |
| Mounting | Same suite as LoadLedger's (two databases, autogenerate, prefix) |
| Append-only | No public mutation path (API surface test) |

Coverage floor: **95 %**.

## 19. Compatibility and versioning

Semver, pre-1.0 `0.x`. The payload's compatibility follows SetSpec's reader policy (unknown minor
accepted, unknown major rejected); policy `version` strings are part of every record.

## 20. Acceptance criteria

1. PromptCadence's acceptance criterion 4 (a confidential trajectory can never reach a remote tier, and
   the refusal is queryable) runs through this package end to end.
2. A `setspec`-only script reads a decision exported by PromptCadence and prints its verdict — no
   Commissioner installed (the payload is the contract).
3. `mypy --strict`, `ruff`, `lint-imports` clean; coverage ≥ 95 %.

## 21. Future extensions

* IdeaPress adoption: the S4 badge reads recorded decisions instead of ad-hoc backend flags.
* Aggregation helpers ("what classifications left this machine this week, to where?") when a UI
  consumer needs them.
* Additional policy dimensions (per-provider ceilings, time-boxed approvals) as new `EgressPolicy`
  implementations — the record shape already carries policy name/version, so they arrive without
  a schema change.
