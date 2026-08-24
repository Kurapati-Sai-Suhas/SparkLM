# P2.7 — The quality gate and approval path

**The lifecycle already existed.** Almost all of it was built in P2.7g-3 and
P2.7h-1; what was missing was one runner and the alias plumbing. This phase
adds the runner, maps the chain, and reports two blockers that stop q3309 today
— neither of which is the oracle.

Nothing was approved, promoted, executed or written.

---

## A. The existing trust lifecycle

Mapped from the code, not invented:

```
DRAFT / UNVERIFIED
   │
   │  reference_create → reference_review submit/approve/activate
   │  (ReferenceSolution: review_state × is_active, provenance frozen)
   ▼
canonical reference                                        ← q3309 IS HERE
   │
   │  oracle_execute --execute        writes OracleExecution provenance
   ▼
oracle evidence on record
   │
   │  quality_gate  (P2.7h-1 evaluate_suite)  → QualityOutcome JSON
   ▼
QUALITY_GATE_PASS
   │
   │  question_review   read-only; prints the artifact digest
   │  question_approve --digest --confirm   writes QuestionApproval only
   ▼
QUESTION_APPROVED
   │
   │  question_promote --confirm   re-derives the digest from live state
   ▼
ORACLE_VERIFIED  (the ONLY writer of trust_state)
   │
   │  Question.status × trust_state ⇒ is_adaptive_eligible, frozen onto
   │  CodeSubmission at submit time
   ▼
learner evidence counts toward mastery
```

Five distinct states, and the repository already keeps them apart:

| state | written by | means |
|---|---|---|
| `ORACLE_EVIDENCE_READY` | `oracle_execute --execute` | executions are on record for the current reference revision |
| `QUALITY_GATE_PASS` | `quality_gate` (this phase) | the suite catches wrong answers, by measurement |
| `QUESTION_APPROVED` | `question_approve` | a human read the artifact and vouched for it |
| `ORACLE_VERIFIED` | `question_promote` | the artifact is still exactly what was approved |
| `PROMOTABLE` | derived | status × trust_state make submissions count |

`question_promote` re-proves seven things independently of the approval —
approval exists, schema matches, **recomputed digest == approved digest**,
reference canonical *now*, oracle evidence still resolves, quality verdict
passed, status not DRAFT. The approval freezes the quality verdict so promotion
cannot have fresh unreviewed numbers substituted, while suite drift is still
caught because every case digest is inside the artifact digest.

## B. The gap this phase closed

`hidden_test_quality.evaluate_suite` has existed since P2.7h-1, and both
`question_review` and `question_approve` **require** a `--quality-report` in
`QualityOutcome` shape. Nothing in the repository produced one. The gate was a
library with no runner, so the approval path could not be walked even with
perfect oracle evidence.

`groups/management/commands/quality_gate.py` is that runner:

```bash
python manage.py quality_gate --question 3309 --alias oracle \
    --spec quality/q3309.json --report-out reports/q3309-quality.json
```

**Read-only.** It executes mutants on Judge0 and writes a JSON file; it touches
no row, enforced by a structural test that no `save`/`create`/`update`/`delete`
call exists anywhere in the module.

Two of the gate's inputs are judgements and therefore arrive in an operator
spec file, never derived:

- the **input contract** — "negative values" is a real coverage gap for a
  problem over integers and nonsense for one over string lengths;
- the **Tier-1 mutants** — realistic misconceptions written by a human for this
  problem. Generating them automatically would measure whether the suite
  catches the mistakes a machine thought of, which is not the question being
  asked.

`--structural-only` runs the checks that need no runner and **can never
produce a PASS** — it is for reading a suite before investing in mutants.

One mapping worth recording: the gate reports `tier2_effective_kill_rate`
(equivalents excluded from the denominator) while `QualityOutcome` calls the
field `tier2_kill_rate`. Same number; mapped once in the writer rather than
renamed in either module.

## C. The approval path — exists, and is blocked differently

`question_review`, `question_approve` and `question_promote` are all built and
tested (P2.7g-3). **Nothing needed inventing.** What they lack is the alias and
role plumbing every operator command gained later: none of the three has
`--alias`, so on production they would write through `learnlm_census_ro` and
fail.

The gate constants are now defined for when they are threaded:

```
ALLOWED_APPROVAL_ROLES    {learnlm_approve_rw}    APPROVAL_PROBE   INSERT groups_questionapproval
ALLOWED_PROMOTION_ROLES   {learnlm_promote_rw}    PROMOTION_PROBE  UPDATE (status), UPDATE (trust_state)
```

with forbidden lists that keep the two apart: an approver records a judgement
and must not be able to enact it (no `groups_question` UPDATE at all); a
promoter enacts one and must not be able to author it (no approval INSERT, no
reference or execution writes, and no access to `content`,
`hidden_test_cases`, `boilerplate_code`, `hidden_wrapper_code` or
`execution_contract_version`).

**Neither role exists yet, and neither command is threaded.** I did not thread
them in this phase: promotion is the single most consequential write in the
system, and wiring it deserves its own brief rather than arriving at the end of
a gate-building phase.

## D. Provenance requirements for real evidence

Every field the artifact digest binds already exists on `OracleExecution` —
nothing needed inventing here either:

```
question_id · case_digest · input_digest · output_digest · produced_output
reference_id · reference_source_hash · language · execution_contract_version
status · executed_at · executor (operator + limits) · provenance_schema_version
is_authoritative
```

`collect_case_evidence` scopes evidence to the reference's **current** source
hash, so executions from a superseded revision are invisible — an approval
cannot rest on an implementation nobody approved. It requires, per case: at
least one SUCCESS, no NONDETERMINISTIC row, a single agreed output digest, and
`REQUIRED_AGREEING_RUNS` agreeing runs.

**A dry run leaves none of this.** q3309's clean 5/5 dry-run wrote nothing and
is not evidence; `oracle_execute --execute` is what turns it into evidence.

## E. Tests

18 new, all passing:

```
writes nothing to the database (behavioural)     no write call exists (AST)
report round-trips into load_quality_outcome     killed mutant → kill rate 1.0
surviving tier-1 mutant blocks                   spec for another question refused
spec with no mutants refused                     malformed JSON refused
missing spec refused                             unknown question refused
bad mutant tier refused                          structural-only executes nothing
structural-only writes no report                 structural-only cannot pass
never claims a trust state (behavioural)         cannot set one (AST)
question read routed through the alias (AST)     uses the shared execution plan
```

The last one matters: an earlier phase found the quality gate bypassing both
halves of the execution seam, so the runner is required to build its
`ExecutionPlan` from `GradingService.quality_execution_plan`.

## F. Mutation

Not run this phase. The command is read-only and its 18 tests include the
structural guards that a mutation sweep would target (no write call, no trust
state, alias routing, shared execution plan). **A sweep is owed when the
approval and promotion commands are threaded** — that is where a mutant can
cause a wrong write rather than a wrong report, and I would rather run one
sweep against the write path than a weak one against a reporter.

## G. Regression

```
2365 passed, 0 failed, 0 errors   (groups, common, learning — 3m54s)
```

## H. q3309 read-only quality gate — FAIL, twice over

```
QUALITY GATE  (STRUCTURAL ONLY)     role learnlm_oracle_rw     production True

  question            3309 — Find the Index of the First Occurrence in a
  hidden tests        5
  malformed           0
  duplicates          0
  missing categories  empty_input, singleton, minimum_boundary,
                      maximum_boundary, duplicate_values
  canonical reference #2

    contract: 5 hidden test(s), coverage floor is 12
    BLOCKER: missing required categories: (the five above)
    BLOCKER: only 5 hidden tests; the floor is 12

  QUALITY_GATE = FAIL
```

(Two further blockers in that output — "no Tier-1 mutants", "no Tier-2
mutants" — are artifacts of `--structural-only`, which supplies none. They are
not findings about q3309.)

**Two real blockers, neither of them the oracle:**

1. **q3309 has 5 hidden tests; the floor is 12.** Reaching it means authoring
   seven more cases — new inputs and new answers, which is a grading-truth
   write of a class this pilot has not performed.
2. **No case carries a `category` label.** The gate reads categories from an
   explicit `case["category"]` key; q3309's cases have `stdin`,
   `expected_output` and `explanation` only, so coverage is *unmeasurable*
   rather than absent — case 4 genuinely is the empty-input case, and the gate
   cannot see that. Labelling them changes `hidden_test_cases`, which changes
   every case digest and therefore the artifact digest: another grading-truth
   write with its own action class.

So the honest position: **q3309 cannot be promoted by adding oracle evidence
alone.** Its suite is too small and unlabelled for the gate this repository
already defines, and that was true before any of this milestone's repairs.

## I. Production safety

```
q3309  8a342568…  DRAFT/UNVERIFIED  v3  adaptive False
q1436  0b2a79f2…  DRAFT/UNVERIFIED  v3  adaptive False
q264, q266, q963, q1689   unchanged
references   q1779 APPROVED/active · q3309 APPROVED/active
OracleExecution   q3309: 0   q1436: 0   total 20
QuestionApproval  0        remediation actions 11
fingerprint  e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8   unchanged
```

## J. Exact next step for the FIRST real OracleExecution

The oracle run is still the right next action — it is the only evidence that
does not require writing grading truth, and it is prerequisite to everything
else:

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py oracle_execute --question 3309 --alias oracle --execute --operator Suhas
```

It records `REQUIRED_RUNS` executions per case against reference #2's current
source hash, writes nothing to the question, and cannot set a trust state.

But it is worth deciding **before** running it what it is for, given §H: with a
5-case unlabelled suite, q3309 will have complete oracle evidence and still not
pass the quality gate. Three honest options:

- **record the evidence anyway** — it is real, it is cheap, and it makes the
  artifact digest computable so `question_review` can be exercised end to end;
- **expand and label the suite first**, as a new remediation action class
  (`hidden_test_cases` writes: seven new cases plus category labels), then run
  the oracle once against the final suite — avoids recording evidence that a
  later suite change invalidates, since new cases change the case digests;
- **lower the floor for this pilot** — I would not: the floor is the gate's
  main defence against a suite that agrees with a wrong reference.

I recommend the second: oracle evidence is scoped to case digests, so evidence
recorded now for five cases becomes partially stale the moment the suite grows.

---

```
QUALITY GATE        = BUILT + TESTED (runner; the library predates this phase)
QUESTION APPROVAL   = EXISTS, BLOCKED — no --alias, no approval/promotion roles
q3309 ORACLE EXECUTION = NOT STARTED
q3309 APPROVAL      = NOT STARTED (and gate-blocked: 5 cases < floor 12)
PROMOTION           = NOT STARTED
RESEED              = NO
```
