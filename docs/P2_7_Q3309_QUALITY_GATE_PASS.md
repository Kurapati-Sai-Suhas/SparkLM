# P2.7 — q3309 quality gate: PASS

Ten mutants, four of them curated misconceptions, executed on Judge0 against the
final twelve-case suite. **QUALITY_GATE = PASS.** Nothing was written to the
database and the oracle has still not run.

---

## The gate's contract, as it actually is

Read before authoring anything, so the mutants were written to the existing
schema rather than a new one:

```
Mutant           identifier · tier (1|2) · description · source · language
                 applicable / not_applicable_reason · equivalence_argument
source           a COMPLETE wrong solution, not a patch — executed through the
                 same wrapper and the same stdin seam a learner submission uses
killed           any case whose normalised output differs, or a non-accepted
                 Judge0 status (a crash or timeout is a kill: the learner sees
                 a failure)
survived         passed all 12 with NO equivalence argument — recorded as a
                 suite GAP, never assumed harmless
equivalent       survived WITH a written structural argument
Tier-1 threshold 100%   — all-or-nothing; equivalence is not a defence
Tier-2 threshold 80%    effective (equivalents excluded from the denominator)
runner           injected by `quality_gate`; here, Judge0
```

## A. Tier-1 — four curated misconceptions, all killed

| id | misconception | why a learner would make it | killed by |
|---|---|---|---|
| `t1-last-occurrence` | `rfind` instead of `find` | one letter apart, and it passes every single-occurrence test | **case 9** |
| `t1-count-not-index` | returns how many times, not where | misreading "first occurrence" as "occurrences"; they agree whenever the answer is 0 or 1 | **case 1** |
| `t1-off-by-one-window` | `range(len(h) - len(n))`, missing the `+ 1` | the classic off-by-one; invisible unless a match sits at the very end | **case 7** |
| `t1-empty-needle-not-found` | returns −1 for an empty needle | the ORIGINAL statement said `1 ≤ |needle|` — this is the exact misconception the statement repair addressed | **case 4** |

Each is a wrong algorithm a person could plausibly write. None was added to
raise a number.

## B. Tier-2 — six mechanical mutants, five killed, one equivalent

| id | mutation | outcome |
|---|---|---|
| `t2-comparison-inverted` | `==` → `!=` on the window | killed, case 1 |
| `t2-returns-start-plus-one` | returns `start + 1` | killed, case 1 |
| `t2-window-one-short` | window `len(needle) - 1` | killed, case 1 |
| `t2-scan-starts-at-one` | loop starts at index 1 | killed, case 3 |
| `t2-missing-returns-length` | returns `len(haystack)` instead of −1 | killed, case 2 |
| `t2-empty-branch-removed` | the explicit empty-needle branch deleted | **EQUIVALENT** |

The equivalence argument, recorded in the spec and in the report:

> With an empty needle the loop runs over `range(len(haystack) + 1)` and its
> first comparison is `haystack[0:0] == ''`, which is True, so it returns 0 —
> exactly what the deleted branch returned. The branch is documentation of the
> statement's rule, not behaviour, and no input can distinguish the two
> versions. Structural, not "it passed the tests".

That is the only route to EQUIVALENT the framework allows, and it is why the
Tier-2 denominator is 5 rather than 6.

## C. Independent analysis, before Judge0

Every mutant was executed **in-process** against the live 12-case suite first,
so each prediction was checked before the gate was asked. The gate's verdict
then had to agree — and did, mutant for mutant and case for case.

The reference itself was run against all twelve first: 12/12 agree. Without
that, mutant analysis measures nothing.

**One prediction was wrong and is corrected in the spec:**
`t1-last-occurrence` was designed to be exposed by case 10 (`abcabc/abc`), and
it is — but case 9 (`aaaa/aa`) reaches it first, because `rfind` gives 2 where
the answer is 0. Same misconception, caught one case earlier. The rationale
file now says so rather than repeating the original guess.

Six distinct cases do the killing — 1, 2, 3, 4, 7, 9 — so the result is not one
lucky test carrying the suite.

## D. Results

```
hidden tests        12         malformed 0    duplicates 0
missing categories  none       canonical reference #2
tier 1 kill rate    1.0        (required 100%)
tier 2 kill rate    1.0        effective, equivalents excluded (required 80%)

[1] t1-count-not-index         KILLED  case 1
[1] t1-empty-needle-not-found  KILLED  case 4
[1] t1-last-occurrence         KILLED  case 9
[1] t1-off-by-one-window       KILLED  case 7
[2] t2-comparison-inverted     KILLED  case 1
[2] t2-missing-returns-length  KILLED  case 2
[2] t2-returns-start-plus-one  KILLED  case 1
[2] t2-scan-starts-at-one      KILLED  case 3
[2] t2-window-one-short        KILLED  case 1
[2] t2-empty-branch-removed    EQUIVALENT

blockers: none
```

## E. Verdict

```
QUALITY_GATE = PASS
```

Which means exactly one thing: **the suite catches the wrong answers that were
put in front of it.** It is not oracle evidence, not approval, and not
`ORACLE_VERIFIED` — the command prints that disclaimer with every verdict.

## F. The artifact

`reports/q3309-quality.json`, and `quality/q3309_quality_spec.json` alongside it.

The report carries the four keys `QualityOutcome` consumes **plus a provenance
block** saying what the verdict was measured against — `from_mapping` reads the
four and ignores the rest, so the evidence travels without changing what the
approval path parses:

```
verdict                     PASS
question_id                 3309
question_state_digest       ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e
execution_contract_version  v3
reference_id / hash         #2 · python · 18ad8f390642c315…
case_count                  12      case_identities: all twelve
categories                  12
results                     per mutant: tier, outcome, killing case
```

Four numbers with no statement of which suite, which reference and which
revision produced them are not evidence. This is why the block exists, and it
was added in this phase after the sweep showed the report could be silently
detached from its subject.

The report is reproducible: re-running the command against the current
production state reproduces it, and any change to the suite, the reference or
the contract moves `question_state_digest` and `reference_source_hash` with it.

## G. Tests — 23 on the runner

New this phase: a spec with no Tier-1 mutant blocks; a surviving Tier-1 mutant
blocks even at 100% Tier-2; EQUIVALENT requires a written argument (a survivor
without one is `SURVIVED`); the report binds the question digest, reference id,
source hash, contract and case identities; the provenance block does not
disturb `load_quality_outcome`. The structural-only test was **strengthened**:
asserting Judge0 is untouched was too weak — a mutation that passed the mutants
through anyway still failed them all as `EXECUTION_ERROR` and reported FAIL, so
nothing noticed. It now asserts no mutant result exists at all.

## H. Mutation — 19 killed / 19, **0 survivors**

Attacked: both tier thresholds set to zero; the "no Tier-1 supplied" and "no
Tier-2 supplied" blockers removed; every survivor counted as equivalent; the
12-case floor disabled; missing categories dropped before they can block; the
verdict ignoring all blockers; a wrong answer not counted as a kill; duplicates
no longer blocking; a structural-only run executing mutants; a structural-only
run writing a report; the gate no longer measuring through the grader's seam;
one question's suite measured against another's mutants; the provenance block
removed, given a wrong digest, stripped of the reference hash, or always
claiming PASS.

The planted "equivalent" — a space in the printed verdict label — was **killed**,
because a test asserts the exact label. It was not equivalent after all, which
is the right outcome to report rather than to relabel.

## I. Regression

```
2408 passed, 0 failed, 0 errors   (groups, common, learning — 3m41s)
```

## J. Production safety

```
q3309   ebb26e7f…3a61e   12 cases   DRAFT / UNVERIFIED   adaptive False
OracleExecution q3309 = 0      QuestionApproval = 0
q1436, q963, q17 unchanged · q264, q266, q1689 at their pre-images
remediation actions 12
fingerprint  9cc3c8d8b0d050d0cebb6a43a44adbb68612d2e5077129c915481c1b66715acf   unchanged
```

The gate executed ten wrong solutions on Judge0 and wrote one local file. No
production row was touched — which is the property the structural test enforces,
not merely a claim.

## K. The next command — the first real OracleExecution

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py oracle_execute --question 3309 --alias oracle --execute --operator Suhas
```

It runs reference #2 against all twelve cases, `REQUIRED_RUNS` times each, and
records `OracleExecution` provenance scoped to the reference's current source
hash. It writes nothing to the question and cannot set a trust state.

Two things to expect: the 10⁴-character case will be the slowest, and the run
will produce roughly `12 × REQUIRED_RUNS` rows. Afterwards
`question_review --quality-report reports/q3309-quality.json` becomes possible
for the first time in this milestone.

---

```
q3309 SUITE        = FINAL + VERIFIED (12 cases)
QUALITY_GATE       = PASS
q3309 ORACLE       = NOT STARTED
QUESTION_APPROVAL  = NOT STARTED
PROMOTION          = NOT STARTED
RESEED             = NO
```
