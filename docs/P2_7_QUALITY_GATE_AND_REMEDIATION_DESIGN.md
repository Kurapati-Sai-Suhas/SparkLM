# P2.7 — Quality-gate semantics fix + remediation pipeline design

**Status:** gate fixed and tested. Pipeline and pilot **designed, not executed**.
Zero production grading-data changes. RESEED = NO.

---

## A. How the gate bypassed canonical execution

`hidden_test_quality._run_case`, one line:

```python
verdict = runner(mutant.source, mutant.language, case.get("stdin", ""))
```

That bypassed **both halves** of the shared seam, not one:

| half | what the grader does | what the gate did |
|---|---|---|
| what runs | `GradingService._build_executable` wraps the solution in the harness | passed `mutant.source` **unwrapped** |
| what it is fed | `GradingService.prepare_stdin` expands `\n` and, under v3, builds the canonical envelope | passed the **raw stored stdin** |

Consequence: a mutant was executed as a bare `Solution` class with no `__main__`
driver, against untransformed input. Tier-1 and Tier-2 kill rates were computed
against semantics no learner ever experiences, so a suite could pass the gate
while the grader fed the same question different arguments — the exact
grader/oracle divergence eliminated everywhere else in this milestone.

The reference solution and both mutant tiers all flowed through this one
function, so all three were affected identically.

## B. The fix

**`ExecutionPlan`** — a frozen dataclass in `hidden_test_quality`, holding two
callables and nothing else, so the module stays pure (no ORM, no Django, no
services import):

```python
build_executable: Callable[[str, str], str]   # (source, language) -> source
prepare_stdin:    Callable[[str, str], str]   # (stored stdin, language) -> stdin
```

`_run_case` now runs both halves in the grader's own order, then calls the
runner with the results. A failure in either is an `EXECUTION_ERROR` — an
unmeasured mutant, which is already a blocker — never a silent fallback to raw
execution.

**`GradingService.quality_execution_plan(question)`** is the single production
factory, living on the class that owns the seam and wired to the same two
functions the grader and the oracle use.

**`plan` is keyword-only with no default.** A default is precisely how the
bypass would come back, so calling the gate without one is a `TypeError`. All
44 existing gate tests failed the moment this landed, which is the correct
signal; they now pass an explicitly named pass-through double that lives in the
test file, never in production code.

Nothing else changed. No `Question`, `hidden_test_cases`, `expected_output`,
`trust_state` or `adaptive_eligible` write. No template touched.

## C. Tests added

`groups/test_quality_gate_seam.py` — 23 tests, `SimpleTestCase`, executing real
generated source in a local CPython subprocess. No Judge0, no database.

**The decisive one** pins an input where the two paths genuinely disagree, so
the bypass cannot return unnoticed:

```
question declares s: str, stored stdin "110"
  canonical -> plan sends ["110"] -> solve("110") -> len == 3
  raw       -> unwrapped source, raw stdin        -> no output at all
```

**Input matrix**, each running a correct solution end to end: string-numeric
(`110`), leading zeros (`007`), quoted string (`"0"`), integer, float, bool,
null, list (one argument, not splatted), object, multiple arguments,
zero arguments, multiline string, space-separated tokens.

**Contract failures**: a case that cannot map to the declared signature yields
`EXECUTION_ERROR` and fails the whole gate.

**Structural guards** (AST, not text search — the module's docstring names the
bypass it removed, so a substring check would pass for the wrong reason):

- the gate calls no `json.loads`/`dumps` anywhere;
- it imports neither `json` nor Django nor services;
- it constructs no wrapper (`{user_code}`, `sys.stdin` absent);
- `_run_case` reaches the runner **only** via plan-produced locals — the source
  and stdin arguments must be `ast.Name` nodes named `source` and `stdin`, so an
  expression reading straight from the mutant or case fails the test;
- `plan` is keyword-only with no default;
- exactly one production file constructs an `ExecutionPlan` (`services.py`).

## D. Mutation results

**10 killed / 10. Zero unexplained survivors.**

| id | attack | killed by |
|---|---|---|
| M1 | the original bypass restored exactly | canonical-result test |
| M2 | `prepare_stdin` bypassed | canonical-result test |
| M3 | `build_executable` bypassed | canonical-result test |
| M4 | a second parser (`json.loads` in the gate) | canonical-result test |
| M5 | gate reimplements the newline rule | canonical-result test |
| M6 | `plan` made optional | `test_the_plan_is_required` |
| M7 | runner handed the unwrapped mutant | canonical-result test |
| M8 | contract failure falls back to raw execution | contract-mismatch test |
| M9 | production plan stops preparing stdin | raw-vs-canonical test |
| M10 | production plan stops wrapping | raw-vs-canonical test |

One planted equivalent mutant survived as designed (sort-key reordering;
identifiers are unique and every consumer groups by tier explicitly).

**Full regression: 1,989 passed, 0 failed, 0 errors.**

An earlier run reported 1,342 errors. That was **infrastructure, not code** —
the Docker daemon stopped mid-run and the test database vanished. Restarted and
re-ran clean on a fresh test DB. Reporting it because a 1,342-error run that
goes unexplained is exactly the kind of thing that gets rationalised away.

## E. Final P2.7h-1 policy

The floor is a **coverage** requirement. You cannot require more coverage than
the input domain contains — but a question must not be able to *claim* a small
domain to escape the bar. The policy resolves both without lowering
`MIN_HIDDEN_TESTS`, padding, or weakening Tier-1.

### E1. Effective floor

```
effective_floor = min(MIN_HIDDEN_TESTS, declared_domain_cardinality)
```

`domain_cardinality` is a new **explicit, reviewable** field on
`InputContract`, defaulting to `None` (unbounded → floor stays 12). Declaring a
finite domain is not a discount — it triggers a **strictly stronger**
requirement:

> When `domain_cardinality < MIN_HIDDEN_TESTS`, the suite must be
> **EXHAUSTIVE**: every input in the domain present, none missing.

Twelve arbitrary cases is a weaker guarantee than complete enumeration of a
4-input domain. This closes the escape hatch by making the small-domain path
harder to satisfy, not easier.

### E2. Per category

| case | policy |
|---|---|
| **A. normal deterministic** | unchanged: floor 12, Tier-1 100%, Tier-2 ≥80% |
| **B. zero-argument** | `domain_cardinality = 1` → floor 1, exhaustive. The duplicate-stdin rule is **suspended** at cardinality 1 (only one input exists), and `validate_case`'s "stdin is empty" rejection must not fire when declared arity is 0 |
| **C. finite/small domain** | E1 exhaustive rule; cardinality must be *derivable from the stated constraints* and is reviewed with the question |
| **D. empty stdin** | valid only when declared arity is 0, or the single parameter declares text. Otherwise `CONTRACT_MISMATCH` — matching what the adapter already does |
| **E. structurally invalid cases** | unchanged blocker via `validate_suite`. The gate reports and fails; it never skips |
| **F. CONTRACT_MISMATCH** | the gate cannot execute → `EXECUTION_ERROR` → blocker. Now carries a distinct `execution contract:` prefix so triage can route it separately from a runner outage |
| **G. NEEDS_MANUAL_REVIEW** | **must not pass.** A question with undeclared parameter types is executed by legacy guessing, so its kill rates would be measured under a guess. Recommended: `prepare_stdin` surfaces `invocation.warnings` and any `undeclared_parameter_type` becomes a blocker |

### E3. NOT_APPLICABLE stays closed

Already enforced structurally (`Mutant.__post_init__` requires a reason;
`EQUIVALENT` requires a written argument). Two additions:

- a `NOT_APPLICABLE` reason must reference the **declared** `InputContract`,
  not the mutant's convenience;
- a **Tier-1** mutant may never be `NOT_APPLICABLE` for a reason that would
  also apply to a real learner mistake — Tier-1 exists to model those.

Tier-1 remains **100%, all-or-nothing**. Unchanged.

### E4. Not yet wired

Per the brief, none of E1–E3 is implemented. Integration points:
`hidden_tests.MIN_HIDDEN_TESTS` / `validate_case` (E1, B, D),
`hidden_test_quality.InputContract` (the new field), and
`GradingService.prepare_stdin` (must return warnings for G — it currently
discards them).

## F. Why the policy fits the adapter

The adapter already computes everything the policy needs, so the policy adds no
second source of truth:

- **arity** comes from `declared_signature`, so "zero-argument" is derived, not
  declared twice;
- **empty-stdin legality** is already the adapter's rule — arity 0 → `[]`,
  declared text → `""`, anything else → `CONTRACT_MISMATCH`;
- **CONTRACT_MISMATCH / INVALID_INPUT / NEEDS_MANUAL_REVIEW** are the adapter's
  own verdicts, already computed per question by `contract_impact`;
- **the gate now executes through the seam**, so a suite that passes is a suite
  that passes *under the semantics learners get* — which is what makes the kill
  rates mean anything at all.

## G. Hybrid remediation classes

Ordered by dependency. **Nothing below is implemented.**

| # | class | automated? | human approval | new reference | oracle | QuestionApproval | provenance | frozen artifacts | rollback |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **CONTRACT_REPAIR** — malformed stored `stdin`/`expected_output` types (48 questions) | yes, deterministic type coercion only where the intent is unambiguous | per batch | no | no | no | records the transformation + pre-image digest | pre-image of every changed row | per-batch revert from pre-image |
| 2 | **BOILERPLATE_REPAIR** — add missing parameter annotations (457) | proposal automated, never applied unattended | **per question** | no | no | no | records who annotated | starter source pre-image | per-question revert |
| 3 | **STATEMENT_REPAIR** — contradictory statement/title/example (9 of 20 sampled) | **no** | **per question** | no | no | no | statement version + author | statement pre-image | per-question revert |
| 4 | **HIDDEN_TEST_REPAIR** — regenerate/extend suites to meet the policy | generation automated | per question | **yes** | **yes** | yes | oracle run ids + reference hash | prior suite in full | per-question revert to prior suite |
| 5 | **EXPECTED_OUTPUT_REPAIR** — re-derive keys after contract migration (615) | derivation automated **by oracle only** | per question | **yes** | **yes** | **yes** | oracle execution ids, determinism proof, reference `source_hash` | prior keys in full | per-question revert |
| 6 | **MANUAL_REVIEW** — semantic conflicts, ambiguous specs, class-design (12 conflicts + 49 ambiguous entry points) | no | **yes, adjudicated** | case by case | case by case | yes | adjudicator + rationale | everything | n/a — nothing changes without a decision |
| 7 | **COMPLETE_REBUILD / RESEED** — only where statement, tests and keys are all unsalvageable | no | **yes** | yes | yes | yes | full new provenance chain | old question archived, not deleted | restore archived question |

**Two ordering rules that are not negotiable:**

1. **Statement repair precedes key repair.** 9 of 20 sampled statements are
   defective; deriving keys from them — by oracle or by LLM — produces
   confidently wrong answers with fresh provenance.
2. **Contract repair precedes oracle execution.** Running the oracle against a
   question whose arguments are about to change mints keys that the migration
   then invalidates.

**Legacy grading truth is preserved** by capturing the pre-image of every
changed row plus a batch digest before any write, and by archiving rather than
deleting in class 7. No submission currently depends on any key: 0 questions are
`PUBLISHED`, 0 are `ORACLE_VERIFIED`, and 0 submissions are `adaptive_eligible`
(44 submissions, all ineligible). **Re-verify at remediation time rather than
relying on this.**

## H. Pilot batch design — 7 questions, NOT executed

Every failure mode represented, plus a control. IDs are from the audited sample
except the control, which was drawn from the 500 SAFE questions with the same
seed (`20250815`) because the sample contained none.

| q | failure mode | what it exercises |
|---|---|---|
| **1689** | semantic answer-key conflict (all 4 keys wrong) | class 5 + 6: keys wrong under *both* readings, and the statement is self-contradictory — the hardest path |
| **963** | statement/example defect + INVALID_INPUT | class 3 then 1: statement says axis-parallel, title and key say any orientation, worked example cites absent points |
| **3309** | CONTRACT_MISMATCH (case 4, `'\n\n'`) | class 1: key is semantically right, only the stored form is unusable |
| **17** | INVALID_INPUT (list-valued `expected_output`) | class 1: pure mechanical repair, content already correct |
| **266** | NEEDS_MIGRATION (boolean casing) | class 1: `True`/`False` → the wrapper's `true`/`false`; unpassable today, trivially repairable |
| **1436** | semantic conflict + unannotated parameter | classes 2 + 6: needs annotation *and* adjudication (case 2's input violates the stated constraint) |
| **264** | **SAFE control** — `nthUglyNumber(n: int)`, 4 cases | proves the workflow leaves a healthy question unchanged |

Seven questions, six failure modes, one control. Small enough to adjudicate by
hand; broad enough that a workflow which survives it has met every class except
7 (complete rebuild), which should not be piloted until the others are proven.

**Success criterion:** the control must emerge byte-identical, and every other
question must reach a *decision* — repaired, quarantined, or explicitly
deferred — with a complete provenance chain. A pilot that repairs six questions
but cannot say why the seventh was left alone has not proven the workflow.

## I. Exact human approval points

Trust is conferred at exactly one place: **promotion**, by a human, after
everything else has already succeeded.

```
reference authored        -> DRAFT, no trust
reference approved        -> human #1 (reviewer)      | approval provenance stamped
reference activated       -> human #2 (operator)      | canonical oracle chosen
oracle executed           -> determinism verified, 2 runs, no trust conferred
keys re-derived           -> stored, still UNPROVENANCED
quality gate              -> PASS required, confers no trust
human adjudication        -> human #3                 | REQUIRED wherever the
                                                      | statement/key relation
                                                      | is disputed
QuestionApproval          -> human #4 (approver)      | artifact digest frozen
promotion                 -> trust_state flips        | the ONLY trust event
```

**A repaired artifact does NOT become trusted because a reference exists, an
oracle agrees, or mutation testing passes.** Those are necessary and jointly
insufficient. The oracle can only confirm that a reference behaves
deterministically — it cannot know whether the reference answers the question
the statement asks, which is precisely where 12 of the sampled conflicts live.

**Any conflict blocks trust.** Specifically: oracle non-determinism, a
gate `FAIL`, an unresolved statement/key disagreement, or an adjudicator
declining. None may be overridden by another passing signal.

## J. Remaining blockers before remediation

1. **Statement remediation** for classes D/E — gates everything downstream.
2. 162 `CONTRACT_MISMATCH` + 43 `INVALID_INPUT` need human data repair.
3. 457 `NEEDS_MANUAL_REVIEW` need starter annotations.
4. 49 ambiguous entry points need a decision (quarantine, rewrite, or a
   class-design execution model).
5. **P2.7h-1 policy E1–E3 is designed, not implemented**, and `prepare_stdin`
   must surface adapter warnings before category G can be enforced.
6. v2's coercion defect — unfixable server-side; decision needed on changing
   `V2_PYTHON_WRAPPER`.
7. Reference pipeline at scale: **1** reference exists.
8. **Pre-image capture and batch rollback tooling does not exist.** No
   remediation should touch a row until it does.
9. Re-verify that no learner evidence depends on current keys (expected true:
   0 published, 0 verified, 0 eligible).

## K. Remaining blockers before reseed

**RESEED = NO.**

1. execution semantics — done for v1 via v3; **not** for v2;
2. semantic key error rate — measured on the sample (22.6% of assessable
   cases); **not** a bank-wide rate and the design does not support one;
3. CONTRACT_MISMATCH repair — not started;
4. INVALID_INPUT repair — not started;
5. NEEDS_MANUAL_REVIEW classification — not started;
6. answer-key remediation strategy — **designed (hybrid), not approved**;
7. reference/oracle/adjudication workflow — pilot 1779 only; the 7-question
   pilot is designed and **not executed**;
8. hidden-test strategy — policy designed, not implemented; no sampled question
   meets the floor;
9. P2.7h-1 wiring — execution seam **fixed**; policy not wired;
10. small-batch remediation — not started;
11. post-batch validation — not started.

## L. Production grading truth unchanged

Read-only confirmation via `learnlm_census_ro` after this phase:

```
questions                    2926
reference solutions          1        (pilot 1779 only)
oracle executions            20       (pilot: 10 cases x 2 runs)
question approvals           0
questions PUBLISHED          0
questions ORACLE_VERIFIED    0
questions declaring v3       0
write privileges on groups_question: NONE (read-only role)
```

Identical to the state recorded at the end of every prior phase.

**Code changed this phase** — no data, no templates, no migrations:

```
M groups/hidden_test_quality.py    ExecutionPlan; _run_case runs through it
M groups/services.py               GradingService.quality_execution_plan
M groups/test_hidden_test_quality.py   explicit pass-through double
? groups/test_quality_gate_seam.py     new, 23 tests
```

(`coding_views.py` and `test_coding_views.py` also show as modified — those are
the 409 mapping from the previous phase, already reported.)
