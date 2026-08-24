# P2.7 — V1 Execution-Contract Remediation

**Status:** design + safe implementation complete. No reseed. No production writes.

The brief for this phase was to make execution semantics correct *before* any
bulk reseeding, on the standing principle that the dependency chain runs
`EXECUTION CORRECTNESS → GRADING CORRECTNESS → LEARNER SIGNAL → MASTERY →
ROUTING` and must never be reversed.

Everything below was measured. Where a number is an observation from a sample
rather than a population figure, it says so.

---

## A. What the defect actually is

`GENERIC_PYTHON_WRAPPER` (`groups/services.py:56`) decides what to pass to the
learner's function by inspecting the *shape* of stdin, never the *type the
question declares*:

```python
stdin_str = sys.stdin.read().strip()
try:
    parsed_input = json.loads(stdin_str)
except Exception:
    lines = [ln for ln in stdin_str.split(chr(10)) if ln.strip() != '']
    try:
        args = [json.loads(ln) for ln in lines]
    except Exception:
        parsed_input = stdin_str
```

For a question whose signature says `s: str`, this is what the two shipped
contracts deliver:

| stdin   | v1 delivers | v2 delivers | declared `s: str` |
|---------|-------------|-------------|-------------------|
| `110`   | `int`       | `int`       | `"110"`           |
| `000`   | `"000"`     | `int 0`     | `"000"`           |
| `0`     | `int`       | `int`       | `"0"`             |
| `true`  | `True`      | `True`      | `"true"`          |
| `null`  | `None`      | `"null"`    | `"null"`          |
| `1.0`   | `float`     | `float`     | `"1.0"`           |
| `007`   | `"007"`     | `int 7`     | `"007"`           |
| `hello` | `str`       | `str`       | `"hello"`         |

## B. The finding that overturned the original plan

**v2 shares the defect and is worse for some inputs.** `int()` accepts leading
zeros where JSON rejects them, so `000` → `0` and `007` → `7` under v2 while v1
preserved both. "Migrate everything to v2" is not a remediation; for
leading-zero questions it is a regression.

Two further defects were found while modelling the wrapper faithfully, neither
of which was in the original brief:

**A JSON list is splatted into separate arguments.** `target_method(*parsed_input)`
means stdin `[1,8,6,2,5,4,8,3,7]` calls `maxArea(1, 8, 6, 2, 5, 4, 8, 3, 7)` —
nine arguments to a one-parameter function. It raises `TypeError` before the
learner's code runs.

**The per-line parse is all-or-nothing.** One unparseable line sends the whole
input through as a single string, so under v1 there is **no way to pass two
unquoted strings** to a two-parameter function.

**The entry point is alphabetical.** `dir(sol)[0]` — a helper named `check`
silently outranks `solve`, and a class-design question with `getRandom`,
`insert` and `remove` is always called as `getRandom`.

---

## C. Production impact (read-only, `learnlm_census_ro`)

Gates: role `learnlm_census_ro`, `read_only=True`, host class `REMOTE_HOSTNAME`,
`is_production=True`. Nothing was executed, nothing was written.

```
questions        2926   (all status=DRAFT, all contract v1)
analysable       2818
WITH test cases  1677   <- the denominator
without tests    1249
```

Against the 1,677 questions that have hidden test cases *and* a parseable
Python starter:

| finding | count | share |
|---|---:|---:|
| `arity_mismatch` — wrong NUMBER of arguments; every submission fails | **835** | 49.8% |
| `type_unstable` — argument types differ between cases of one question | 449 | 26.8% |
| `unannotated` — no declared type to check against | 341 | 20.3% |
| `boolean_casing` — expected output stored as `True`/`False` | 64 | 3.8% |
| `ambiguous_entry_point` — more than one public method | 49 | 2.9% |
| `non_string_test_field` — grader raises `AttributeError` | 48 | 2.9% |
| `blank_stdin` | 24 | 1.4% |
| `zero_parameters` | 21 | 1.3% |
| `text_retyped` — declared text, handed a number/bool/None | **20** | 1.2% |
| `variadic_starter` | 2 | 0.1% |

Reproduce with:

```bash
python manage.py contract_impact --alias default --stratified 20 --seed 20250815 --exclude 1779
```

### The severe one is not the one the brief expected

`text_retyped` is only 20 questions. `arity_mismatch` is **835** — roughly half
the graded bank is called with the wrong number of arguments, which raises
`TypeError` before the learner's code executes. Those questions fail every
submission, including correct ones.

`non_string_test_field` (48) is a distinct failure: `GradingService.grade`
calls `.strip()` on `stdin` and `expected_output` without checking, so a
list-valued field raises inside the grader and the learner receives no verdict
at all. This was found by the analysis crashing on real data.

### Two corrections made to my own numbers before publishing them

An earlier run of this report said 41 `text_retyped` and 837 `arity_mismatch`.
Both were wrong:

- `text_retyped` compared *any* declared-text parameter against *any* non-string
  argument. `wordBreak(s: str, wordDict: list[str])` handed `"cat"` and `["c"]`
  is called exactly as declared; the coarse check accused it. Positional
  matching gives **20**, not 41.
- `*args`/`**kwargs` starters were counted as fixed-arity. They accept any
  number of arguments, so no arity claim can be made. 1,142 such starters exist
  — but 1,140 have no test cases at all, so the corrected count is **835**.

The percentages were also initially computed over a population that included
1,249 questions with no test cases. They now use 1,677.

---

## D. What was implemented

`groups/input_contract.py` — pure, annotation-aware coercion. No imports at
all: no ORM, no settings, no clock, no environment.

```python
def coerce_argument(text, annotation):
    if declares_text(annotation):
        return text          # UNTOUCHED — no strip, no unquote, no case change
    if declares_sequence(annotation):
        return [coerce_token(part) for part in text.split()]
    ...
```

**It is deliberately not wired into v1 or v2.** Both templates remain frozen.
v1 is the contract every one of these questions was graded under, and editing
it would silently re-grade all of them at once. Correct semantics are now
*available*; nothing adopts them until a question is migrated deliberately,
with oracle verification. A test asserts that neither shipped template mentions
`input_contract`.

`groups/contract_impact.py` — the read-only blast-radius analysis above,
plus the deterministic stratified sampler.

## E. Contract identity: grader and oracle

Proven behaviourally, not by inspection. A capturing stub runner drives both
services and the executed source is compared byte-for-byte, under v1, under v2,
and under a per-question wrapper — plus the literal-`\n` expansion both apply.
An AST guard (real `Call` nodes only, so a docstring cannot satisfy it) asserts
`OracleService._execute` calls `GradingService._build_executable` rather than
building its own.

## F. §6 — Empty input: recorded, not resolved

For a declared text parameter a blank line is unambiguous: it is `""`. That is
pinned by test.

For an **unannotated** parameter it is genuinely undecided, and the analysis
does not pretend otherwise. What v1 does today is pinned instead: blank stdin
yields `args = []`, so the function is called with **zero** arguments — which
is why a one-parameter question on blank stdin is an `arity_mismatch`, not a
wrong answer. 24 questions are affected. **The decision is a human's.**

## G. §7 — Boolean output: the renderer stays, the data is what needs repair

`render_output` emits lowercase, matching `str(res).lower()` in v1.
`normalize_output` does not fold case, so the 64 questions storing
`True`/`False` cannot pass.

Changing the renderer to match those 64 would break every question that
correctly stores `true`/`false`. So the renderer is correct and the stored data
is wrong. **The 64 questions were not rewritten** — that is a repair decision,
out of scope here.

## H. §9 — Mutation testing

| module | mutants | killed | survivors |
|---|---:|---:|---:|
| `input_contract.py` | 19 | 18 | 1 (planted, equivalent) |
| `contract_impact.py` | 22 | 21 | 1 (planted, equivalent) |

**Zero unexplained survivors.**

Both survivors are equivalence sanity mutants, proven equivalent rather than
asserted:

- `input_contract` S1: `"true" if value else "false"` → `str(value).lower()`.
  Equivalent by exhaustion — `bool` has two values and both agree.
- `contract_impact` S1: reordering two entries in `REASON_ORDER`, which sets
  display order only and is asserted by no test.

Three survivors were found and **closed** rather than explained away: stdin not
being stripped the way the wrapper strips it (a non-breaking space distinguishes
them), non-string `expected_output` crashing instead of counting, and the same
question being sampled twice when it matches several strata. The last one also
required fixing the test fixture, which had no multi-stratum questions and so
could not have failed.

The drift guard that keeps the v2 replica honest was itself mutation-tested:
three independent replica changes were all caught.

## I. §11 — Stratified sample for manual review

Seed `20250815`, question 1779 excluded (already audited in the pilot).
Round-robin across strata, not proportional — proportional allocation would
spend every slot on the two largest classes and show none of the rare ones. A
`clean` stratum is included, because a sample of only-broken questions cannot
show whether the rest are fine.

Re-running with the same seed selects the same questions.

```
q963  non_string_test_field    q782   non_string_test_field
q1664 arity_mismatch           q3309  arity_mismatch
q17   text_retyped             q1689  text_retyped
q1716 type_unstable            q1436  type_unstable
q716  ambiguous_entry_point    q622   ambiguous_entry_point
q266  boolean_casing           q3339  boolean_casing
q1265 blank_stdin              q687   blank_stdin
q3318 zero_parameters          q381   zero_parameters
q2201 variadic_starter         q118   variadic_starter
q1896 unannotated              q1100  clean
```

Ids only. Nothing was executed and no question content was read into this
report.

## J. Test coverage added

101 tests across `test_input_contract.py` and `test_contract_impact.py`, all
`SimpleTestCase`. No database, no Judge0, no network — which is an assertion,
not a convenience: coercion is a pure function of stdin and the declared
signature, so any query would mean grading depends on something else, and
`SimpleTestCase` fails on one.

## K. Limitation — the full suite was not run

No local PostgreSQL is running in this environment (port 5432 refuses), and the
test-isolation plugin correctly pins tests to localhost, so every DB-backed
suite errors on connection setup. **The two suites added here pass; the rest of
the suite was not executed and I am not claiming it is green.** They need to be
run wherever a local Postgres is available before this is merged.

---

## L. Was anything reseeded? **NO.**

`reseed_questions` was not run. No `expected_output`, `hidden_test_cases`,
`Question` content, `trust_state` or `adaptive_eligible` value was modified.
No reference was created or approved. No question was approved or promoted.
Judge0 was not called. The oracle was not executed. Existing grading truth is
byte-for-byte unchanged.

## M. Is the question bank now trustworthy? **NO.**

Nothing in this phase repaired a single question. It established, with
measurements rather than estimates, that roughly **half the graded bank cannot
be called at all** under the contract it is graded by — and it made correct
semantics available without changing how any existing question behaves.

The 35.9% conflict rate observed in the 39-case pilot audit remains an
observation from that sample. It is **not** a population estimate and is not
extrapolated here.

---

## Next decisions (for a human, not for this phase)

1. What blank stdin means for an unannotated parameter (§F).
2. Whether the 64 `True`/`False` expected outputs are repaired, and by whom.
3. Whether `arity_mismatch` (835) is fixed in the wrapper or per question —
   this is the largest class by a wide margin and was not anticipated by the
   brief.
4. Whether `GradingService.grade` should defend against non-string test fields
   (48) rather than raising inside the grader.

Reseeding remains **blocked** until 1–4 are answered.
