# P2.7 — The reseed contract-setting step

**Phase 12 · M2 P2.7h-27 · implementation and local verification**

Status: **READY**, pending one approval — migration `0049` is generated but not
applied. No production write, no contract migration, no batch, no pilot.

---

## 1. What was built

`python manage.py reseed_contract` — a dedicated command that chooses
`execution_contract_version` for a question whose signature has just been
declared and whose suite does not exist yet.

```
reseed_statement  →  declare_signature  →  reseed_contract  →  hidden tests  →  oracle
                                          ▲               ▲
                                          │               └── suite authored AGAINST the contract
                                          └── the only window where the contract can be chosen
```

That window is the entire safety argument. v3 changes what a stored expected
output *means*, so the contract must be fixed before any case is authored and
can never be moved after. `stub_blockers` already requires
`hidden_test_cases == []`, which pins the decision to exactly this gap.

---

## 2. Why not `remediate_contract` (12A)

It refuses a question with no stored test cases:

> the question stores no test cases, so nothing demonstrates that the contract
> executes

That refusal is **correct for a live question** and was left completely
untouched. But a reseed candidate has no cases *by definition* at this point,
so waiting for execution evidence would invert the lifecycle: cases first,
contract second, every stored answer silently re-read.

The two commands are separately gated and cover disjoint populations. A test
asserts the refusal text still exists in `remediate_contract`, so weakening it
later fails here.

---

## 3. Preconditions and refusals (12B)

`reseed_authoring.contract_blockers()` — the exact inverse of
`signature_blockers()` on one clause, which is what makes the order
unskippable:

| | `signature_blockers` | `contract_blockers` |
|---|---|---|
| starter declares parameters | **refuses** | requires |
| starter is variadic | requires | **refuses** |

A question can never be eligible for both at the same moment. That is asserted,
not asserted-about.

Everything required, and all of it enforced:

- frozen batch · pre-image exists (`require_pre_image`, write-ahead)
- `DRAFT` · `UNVERIFIED` · `hidden_test_cases == []`
- zero `QuestionApproval` · zero `OracleExecution`
- a declared signature — no `*args`, no `**kwargs`, no keyword-only, at least
  one parameter
- `--expect-digest` matches
- the row is re-read under `SELECT FOR UPDATE`
- **digest, eligibility and the decision itself are all re-checked inside the
  lock**

Refused: published, oracle-verified, has hidden tests, has approval, has oracle
evidence, variadic, keyword-only, zero-parameter, unclassifiable, missing
pre-image, stale digest, wrong batch, and already-decided.

Idempotency is an **explicit refusal**, not a silent no-op: one
`CONTRACT_DECLARATION` per question per batch. An append-only trail that
accumulates duplicates cannot answer "when was this decided".

---

## 4. The decision (12C)

`reseed_authoring.contract_target()` **imports** the Phase 11 rule and restates
nothing:

```python
verdict = census.v3_requirement(source)     # V3_REQUIRED / V1_SUFFICIENT / UNKNOWN
```

- exactly one parameter, container kind → **v3**
- anything else classifiable → **v1**
- `UNKNOWN` → refuse as `NEEDS_MANUAL_REVIEW`; no contract is better than a
  wrong one
- **custom wrapper → v1, never migrated.** `_build_executable` consults the
  per-question wrapper *before* the version, so the generic harness never runs
  and the splat cannot occur. Migrating such a question would change a field
  nothing reads.

A test asserts `contract_target` contains no container/kind reasoning of its
own, by AST — a second copy of the rule would be a second thing to keep true,
and Phase 11 is where it was mutation-tested.

---

## 5. Write scope (12D)

```
CONTRACT_FIELD = "execution_contract_version"
```

The **only** field. Proven three ways rather than promised:

1. `update_fields=[CONTRACT_FIELD]` — asserted by AST that exactly one `save()`
   exists and names exactly this one literal.
2. Before/after substitution across every captured field, inside the
   transaction; any other movement raises and the write is reverted.
3. The post-write value is verified to be the chosen target.

Untouched and unreachable: content, boilerplate, hidden tests, expected output,
status, trust state, wrapper, reference, approval, oracle execution,
submission, adaptive eligibility.

### The decision that writes nothing

A `V1_SUFFICIENT` question already declares v1, so the field does not change.
The command still records the audit row, and this is load-bearing: without it,
*"we chose v1"* and *"we never looked"* are the same row, and suite authoring
cannot tell whether it may start. **The decision is the artifact, not the
diff.**

---

## 6. Audit (12E)

`CONTRACT_REPAIR` was assessed and is **not appropriate**. A new class was
added:

```
RemediationAction.CLASS_CONTRACT_DECLARATION = "CONTRACT_DECLARATION"
```

Both write the same column under the same role, so the class is the *only*
thing separating them in the trail. `remediate_contract` justifies its write by
**execution**; this one has none by construction. Filed under one label they
would be indistinguishable — and the first question a reviewer asks of that
table is precisely *"which contract changes were made without execution
evidence?"*

Every successful run produces exactly one append-only action carrying operator,
question, pre-image (pre-state), post-digest, reason, and the contract
selected:

```
detail = "<reason> | v1 -> v3 (V3_REQUIRED)"
```

Rows go through `pre_image.record_action`, which re-reads the question and
computes the post-digest itself. A test asserts by AST that the command never
constructs a `RemediationAction` directly — otherwise it could record a digest
of its own choosing.

---

## 7. Ledger (12F)

```
STATEMENT_WRITTEN → SIGNATURE_WRITTEN → CONTRACT_SET
```

`ReseedLedger.STAGE_CONTRACT = "CONTRACT_SET"`, and deliberately **not**
COMPLETE: the question still has no cases, no oracle, no approval, and
DRAFT/UNVERIFIED trust. Naming it COMPLETE would make the ledger assert a
readiness no other table agrees with.

**`CONTRACT_SET` is terminal in `ADVANCES`** — a deliberate omission. What
follows is suite authoring, which is not a reseed write, does not run under a
reseed role, and has not been built. Wiring `CONTRACT_SET → COMPLETE` now would
let the orchestrator mark questions finished on a transition nothing has
verified.

### The stage has no live signal

A `V1_SUFFICIENT` question's contract write is invisible in the row, so
`derive_stage` reads the contract stage from the **append-only trail**, and
flags a discrepancy when the trail says v3 was chosen but the row says
otherwise — a reverted or overwritten write.

### Ripple: the orchestrator's terminal changed

`reseed_orchestrate` runs stages 1–2 and previously expected `COMPLETE`. Since
statement + signature now derives to `SIGNATURE_WRITTEN`, its terminal is named
explicitly:

```python
ORCHESTRATED_TERMINAL = ReseedLedger.STAGE_SIGNATURE
```

It reports what it achieved, not what remains. Five existing tests encoded the
old terminal and were updated to the new lifecycle.

---

## 8. Migration 0049 — generated, **not applied** (needs approval)

```
groups/migrations/0049_reseed_contract_stage.py
  ~ Alter field action_class on remediationaction
  ~ Alter field stage on reseedledger
```

`manage.py sqlmigrate groups 0049` against the production schema emits:

```
-- (no-op)
-- (no-op)
COMMIT;
```

Both operations are **choices-only**. Django models choices at the application
level; a Postgres `CharField` carries no `CHECK` for them, so this migration
changes zero schema and zero data. `showmigrations` confirms it is `[ ]`
unapplied on production.

It is still not applied, because this phase does not change production schema
without explicit approval. Applying it is a prerequisite for running
`reseed_contract` against production, since Django validates choices on
`full_clean` and the ledger stage would otherwise be unknown to the deployed
code.

---

## 9. q1974 regression (12G)

```
declared:  findGreatestCommonDivisorOfArray(self, nums: list[int]) -> int
before:    v1
verdict:   V3_REQUIRED
after:     v3
```

Proven offline, no production execution, no Judge0:

```
v1   stdin "[3, 6, 4]"  ->  json.loads -> [3, 6, 4] -> method(*[3,6,4])
                        ->  TypeError: takes 2 positional arguments but 4 were given

v3   stdin "[3, 6, 4]"  ->  prepare_stdin -> "[[3,6,4]]"
                        ->  method([3, 6, 4]) -> gcd(min, max) -> 3
```

The v1 splat failure is raised for real in the test, not described. The v3 path
runs the actual `GradingService.prepare_stdin` on the actual migrated row and
feeds the envelope to real GCD logic.

---

## 10. Pilot fixtures (12H)

Offline only. Nothing written to production.

| | signature | contract |
|---|---|---|
| q1940 | `(s: str, k: int)` | v1 |
| **q1974** | `(nums: list[int])` | **v3** |
| q2027 | `(colors: str)` | v1 |
| q2057 | `(word1: str, word2: str)` | v1 |
| q2290 | `(n: int)` | v1 |

A test asserts exactly one of the five needs v3, and that it is q1974. Another
walks the ordering invariant as a sequence: refused before the signature
exists → acts once it does → refuses again once a suite is authored.

---

## 11. Privilege boundary (12I)

**`learnlm_contract_rw` is sufficient. No privilege change is needed.**

```
CONTRACT_REPAIR_PROBE      groups_question.execution_contract_version  UPDATE
CONTRACT_REPAIR_FORBIDDEN  content, hidden_test_cases, status, trust_state,
                           boilerplate_code, hidden_wrapper_code   (UPDATE)
                           INSERT, DELETE, TRUNCATE on groups_question
```

That is exactly the required grant and exactly the required denials, checked at
runtime rather than documented — an over-granted role is refused, not trusted.

The role is **shared with `remediate_contract` deliberately**. Postgres grants
are per column and both commands write the same one, so a dedicated role would
need identical grants and would separate credentials, not capabilities. The
separation that matters is the precondition, and that is where it lives.

---

## 12. Mutation (12J)

```
29 killed / 31   ·   real survivors: 0
```

Every mutant the brief named, and more:

```
D1  the contract decision is omitted (always v1)                    killed
D2  the v3/single-container rule is inverted                        killed
D3  UNKNOWN is accepted instead of refused                          killed
D4  UNKNOWN silently becomes v1                                     killed
D5  q1974's container signature reads as V1_SUFFICIENT              killed
O1  a variadic placeholder is accepted                              killed
O2  keyword-only parameters are accepted                            killed
O3  a starter declaring no parameters is accepted                   killed
O4  hidden cases no longer block the contract stage                 killed
W1  a custom-wrapper question is migrated to v3                     killed
H1  a stale expect-digest is accepted                               killed
H2  the digest is not re-checked inside the lock                    killed
H3  eligibility is not re-checked inside the lock                   killed
H4  the missing pre-image write-ahead is skipped                    killed
H5  a wrong batch is accepted                                       killed
H6  the decision is not re-taken under the lock                     killed
S1  status becomes writable alongside the contract                  killed
S2  trust_state is actually persisted alongside the contract        killed
S3  the before/after substitution proof is removed                  killed
S4  the contract is not verified after the write                    killed
A1  the audit row is not written                                    killed
A2  the audit is filed as CONTRACT_REPAIR                           killed
A3  a second decision may be recorded for the same question         killed
A4  the v1 decision is not audited                                  killed
L1  the contract stage advances straight to COMPLETE                killed
L2  the signature stage still advances straight to COMPLETE         killed
L3  CONTRACT_SET is just an alias for COMPLETE                      killed
L4  the ledger reports CONTRACT_SET before the audit exists         killed
L5  a reverted contract write is not flagged                        killed
E1  EQUIVALENT: wording of a comment                                survived
E2  EQUIVALENT: trust_state assigned but never in update_fields     survived
```

Seven survivors on the first sweep. **Six were real gaps; one was genuinely
equivalent and was reclassified rather than papered over.**

- **H2, H3, H6, S3, S4** — the checks *inside the lock*. Every one of those
  conditions is already checked while planning, so in a single-threaded test
  the second check never fires and could be deleted unnoticed. That is exactly
  the bug class the lock exists for: the plan is read outside the transaction
  and another writer may act in between. Five tests now make the collaborator
  disagree with itself between the two calls — the only way to prove the
  second check is load-bearing.
- **O3** — a zero-parameter starter was accepted. No stdin is delivered
  anywhere, so the decision would mean nothing, and the ledger would read
  CONTRACT_SET for a question nobody decided anything about.
- **S2** — assigning `trust_state` without naming it in `update_fields`
  persists nothing, and the following `refresh_from_db` discards it.
  Functionally equivalent. Reclassified to `E2` and **kept**, because it
  demonstrates that `update_fields` is the protection; a non-equivalent
  variant that really persists the column was added in its place and dies.

Eleven tests were added to close the six real gaps.

---

## 13. Production integrity (12K)

```
Question           2926 = 2926   OK      RemediationAction    17 =  17   OK
ReseedLedger          0 =    0   OK      RemediationBatch      1 =   1   OK
ReferenceSolution     3 =    3   OK      QuestionApproval      2 =   2   OK
OracleExecution      70 =   70   OK      CodeSubmission       44 =  44   OK
QuestionPreImage      7 =    7   OK

reseed candidates  1140 = 1140   OK
migration 0049     [ ] unapplied

bank fingerprint  3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6
                  MATCH

PRODUCTION WRITES = 0
```

No production batch created. `reseed_statement` and `declare_signature` were
not run. No hidden test created. No oracle run. Nothing approved, promoted or
published. The pilot has not begun.

Tests: **60** in `test_reseed_contract.py`. Full regression: **2,456 passed.**

> One note on that regression number. An intermediate run reported `1 failed, 5
> errors` — caused by two of my own pytest sessions sharing `--reuse-db`
> concurrently, the same self-inflicted interference seen in an earlier phase.
> Re-run serially it is clean. Recorded because a green number that once was
> red should say why.

---

## 14. What remains before the pilot can run

1. **Approve migration 0049** (provably a no-op; apply then verify).
2. Decide whether `CONTRACT_SET → COMPLETE` should ever be wired, and by which
   authority. Left open deliberately.
3. Extend `reseed_orchestrate` to drive stage 3, or keep contract selection a
   separate deliberate invocation. Not decided here.

None of these has been started.
