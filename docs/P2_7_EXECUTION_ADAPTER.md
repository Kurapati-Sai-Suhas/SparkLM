# P2.7 — Execution-Contract Follow-Up: the shared adapter

**Status:** design + pure implementation complete. No grading-data changes. No
production writes. RESEED = NO.

Everything below was measured or executed. Where something is an observation
rather than a population figure, it says so.

---

## A. Root cause of the 835 arity mismatches

Two independent mechanisms in `GENERIC_PYTHON_WRAPPER`, both in the invocation
step rather than the parser:

**1. A JSON list is splatted positionally.**

```python
elif isinstance(parsed_input, list):
    res = target_method(*parsed_input)
```

`[1,8,6,2,5,4,8,3,7]` calls `maxArea(1, 8, 6, 2, 5, 4, 8, 3, 7)` — nine
arguments to a one-parameter function.

**2. The per-line parse is all-or-nothing.** One unparseable line abandons the
split entirely and passes the whole blob as a single string, so `abc\ndef`
reaches a two-parameter function as one argument. Under v1 there is **no way to
pass two unquoted strings**.

Both raise `TypeError` before the learner's code runs, so the question fails
every submission including correct ones. LeetCode #1 (`twoSum`, stdin
`2 7 11 15\n9`) is in this class.

A third mechanism compounds it: the entry point is `dir(sol)[0]`, which is
**alphabetical**, so a class-design question with `getRandom`, `insert` and
`remove` is always called as `getRandom`.

## B. Root cause of the 48 grader crashes

`GradingService.grade` assumed both stored fields were text:

```python
verdict = self._runner(..., tc.get('stdin', '').replace('\\n', '\n'))
expected = tc.get('expected_output', '').strip()
```

48 production questions store a **list** in one of them. `.strip()` raises
`AttributeError`, unhandled, so the request 500s and the learner receives no
verdict at all — not a wrong answer, an error page.

## C. Root cause of the 20 `text_retyped` findings

`json.loads` on the whole stdin, with the declared type never consulted. A
question whose signature says `s: str` is handed `int 110`. Its stored expected
outputs were produced by code asked a different question.

---

## D. Canonical shared execution semantics

One transformation, in `groups/execution_adapter.py`:

```
stored stdin → declared signature → typed arguments → JSON-array envelope
```

The envelope is the mechanism. **The wrapper already contained a complete
calling convention that nothing was targeting deliberately** — that same
`target_method(*parsed_input)` branch. Every argument list can be written as a
JSON array:

| class | signature | envelope | binds as |
|---|---|---|---|
| A | `nums: list` | `[[1,2,3]]` | one list argument |
| B | `a: int, b: int` | `[6,7]` | two scalars |
| C | `n: int` | `[42]` | one scalar |
| D | `s: str` | `["110"]` | the **string** `110` |
| E | `*args` | — | refused |
| F | `s: str, nums: list` | `["x",[1,2]]` | mixed |
| G | keyword-only | — | refused |
| H | `()` | `[]` | zero arguments |

Verified by executing the real template against all sixteen cases, not by
reading it.

The adapter **refuses rather than guesses**: `CONTRACT_MISMATCH` when stored
stdin contradicts the declared signature, `INVALID_INPUT` when the case is not
structurally usable, `NEEDS_MANUAL_REVIEW` when there is no declared contract
to check against.

## E. V1 / V2 architectural decision

**V1 can be corrected with zero template changes.** The envelope drives the
unchanged harness. This is implemented as contract **v3** — v1's template
verbatim, fed canonical stdin. No new template exists.

**V2 cannot be corrected from the server, and that is measured.** Its wrapper
tokenises each line with `_sparklm_token`, which coerces by shape before the
annotation is consulted. Every server-side encoding of the string `110` was
executed against the real v2 template:

```
bare 110              -> int:110
["110"]               -> str:'["110"]'
"110"                 -> str:'"110"'
'110'                 -> str:"'110'"
110 / 110\n /  110    -> int:110
```

No encoding delivers the bare string. **Correcting v2 requires changing
V2_PYTHON_WRAPPER**, which §6 forbids without reporting first — so it was not
changed. Reported here as a decision for you.

**Migration to v2 is not necessary and is not recommended.** It shares the
coercion defect, is worse on leading zeros, and cannot be fixed without a
template change that v1 does not need.

**Recommendation:** one shared semantic layer (`execution_adapter`) beneath v1
via v3. Leave v2 as-is; it has no production questions.

## F. Pure adapter implementation

`groups/execution_adapter.py` — imports `ast` and `json` and nothing else. No
ORM, no Django, no settings, no clock, no I/O. Asserted by test.

`contract_impact` now **imports** its signature readers from the adapter rather
than keeping a second copy. Two parsers that agree today disagree after the
next edit.

Changed outside the adapter, all server-side Python, **no templates**:

- `services.py` — new `GradingService.prepare_stdin`; `grade()` reads test
  cases through `execution_adapter.read_test_case`; new
  `ExecutionContractError`.
- `oracle.py` — `_execute` now calls `GradingService.prepare_stdin` instead of
  its own copy of the newline rule.
- `execution_contract.py` — `CONTRACT_V3` added to `KNOWN_CONTRACTS`. No
  migration: `execution_contract_version` is a plain `CharField` with no
  `choices` and no constraint.

### Templates verified unchanged by digest

All six wrapper templates were hashed against `git HEAD` and are byte-for-byte
identical. The digests are now pinned in the test suite, so a future edit fails
loudly rather than being caught by diff review:

```
GENERIC_PYTHON_WRAPPER  85766c03fbbd4d00  (2164 bytes)
GENERIC_JAVA_WRAPPER    ede876af69ecbc58  (4010 bytes)
GENERIC_JS_WRAPPER      18f29dad1e7afef8  (1689 bytes)
V2_PYTHON_WRAPPER       efc0cac8e35e23ff  (2092 bytes)
V2_JAVA_WRAPPER         9b0f0e0c50fc577d  (4129 bytes)
V2_JS_WRAPPER           7d4a1acaf126ca42  (2573 bytes)
```

The first attempt at this check reported four untouched templates as changed —
`git show` with `text=True` decodes as the system codepage, turning every em
dash in a template comment into mojibake. The finding was in the checker, not
the code; recorded because the same trap would mislead the next person.

## G. GradingService / OracleService identity

Both reach execution through the same two functions — `_build_executable` for
*what runs* and `prepare_stdin` for *what it is fed*. Proven three ways:

1. behaviourally, driving both real services through a capturing runner and
   comparing source, language and stdin byte-for-byte, under v1 and v3;
2. structurally, by AST — real `Call` nodes only, so the comment explaining the
   change cannot satisfy the guard (it quotes the old code, and a text search
   did pass for the wrong reason before this was fixed);
3. by mutation — reverting the oracle to its own copy of the rule is killed.

**v1 and v2 stdin is byte-for-byte unchanged.** Every production question is
v1, so this phase changes no existing grading. Two tests pin exactly that, and
mutating either is killed.

## H. Boolean output rule

Canonical is **lowercase** `true`/`false`, matching `str(res).lower()` in the
shipped wrapper. Stored `True`/`False` is non-canonical and can never be
produced — those 67 questions fail every submission.

The renderer was **not** loosened. Making the comparator case-insensitive would
also make a genuinely wrong `true` match an expected `True` from an unrelated
code path. Tests assert the comparator does not fold case, does not equate
`1` with `1.0`, `1 2` with `12`, or `[1,2]` with `[2,1]`.

A separate defect found while specifying this: the wrapper prints
`json.dumps(res).replace(" ", "")` for containers, which strips spaces **inside
strings** — `["a b"]` becomes `["ab"]`. `canonical_output` uses `separators`
instead, removing padding without corrupting content.

**The 67 questions were not rewritten.**

## I. Empty-input rule

- **Declared text parameter** — unambiguous, it is `""`.
- **Zero-parameter function** — blank stdin is correct and yields `[]`.
- **Zero-parameter function with non-blank stdin** — `CONTRACT_MISMATCH`.
- **Declared `int`/`bool`/etc. with blank stdin** — `CONTRACT_MISMATCH`, not a
  silent zero.
- **Undeclared parameter** — executes with legacy guessing but is reported
  `NEEDS_MANUAL_REVIEW`. Still a human's decision; the adapter does not pretend
  to have resolved it.

## J. Mutation results

| target | mutants | killed | survivors |
|---|---:|---:|---:|
| `execution_adapter` + both seams | 28 | 27 | 1 (planted, equivalent) |
| `contract_impact` (prior sweep) | 22 | 21 | 1 (planted, equivalent) |
| `input_contract` (prior sweep) | 19 | 18 | 1 (planted, equivalent) |

**Zero unexplained survivors.**

The one survivor here is `return "null"` → `return json.dumps(None)`, which is
the identical string by definition of `json.dumps`.

Four survivors were found and **closed**, not explained away: permissive
boolean spellings (`yes`/`1` accepted), `int(float("1.5"))` silently
truncating, `null` coercion widening to `None`/`nil`, and `list[str]` elements
being retyped. One mutant had a bad anchor and was re-run rather than dropped.

## K. New production impact (read-only, `learnlm_census_ro`)

2,926 questions, all `status=DRAFT`, all contract v1. 1,785 have at least one
hidden test case; 1,141 have none.

Verdicts under canonical execution — **exclusive**, one per question:

| segment | n | INVALID_INPUT | CONTRACT_MISMATCH | NEEDS_MANUAL_REVIEW | NEEDS_MIGRATION | SAFE |
|---|---:|---:|---:|---:|---:|---:|
| graded (≥1 case) | 1785 | 43 | 170 | 457 | 615 | 500 |
| no test cases | 1141 | 0 | 0 | 1141 | 0 | 0 |
| variadic starters | 1142 | 0 | 0 | 1142 | 0 | 0 |
| fixed-arity starters | 1784 | 43 | 170 | 456 | 615 | 500 |
| declares text input | 349 | 19 | 27 | 11 | 238 | 54 |
| Python-cased boolean output | 67 | 0 | 6 | 9 | 52 | 0 |

**The adapter resolves most of the 835.** Cross-tabulated rather than inferred
from two totals:

| the 835 arity mismatches, under canonical execution | count |
|---|---:|
| NEEDS_MIGRATION (invocable; arguments change, so answers must be re-derived) | 425 |
| NEEDS_MANUAL_REVIEW (invocable; no declared types) | 231 |
| CONTRACT_MISMATCH (stdin genuinely cannot map to the signature) | 162 |
| INVALID_INPUT (structurally broken test data) | 17 |

**656 of 835 become invocable**, including q1 `twoSum`. Of the 20
`text_retyped`, 16 become `NEEDS_MIGRATION`, 3 `NEEDS_MANUAL_REVIEW`, 1
`INVALID_INPUT`.

Reproduce:

```bash
python manage.py contract_impact --alias default --stratified 20 --seed 20250815 --exclude 1779
```

**SAFE means the adapter can invoke the question as its signature declares. It
does not mean the stored answers are correct.**

## L. Deterministic sample readiness

Unchanged: seed `20250815`, 1779 excluded, same stratified round-robin design,
reproduces identically across runs. **Not oracle-executed.**

```
q963  q1664 q17   q1716 q716  q266  q1265 q3318 q2201 q1896
q1100 q782  q3309 q1689 q1436 q622  q3339 q687  q381  q118
```

## M. Historical `expected_output`

**Untouched.** No `Question`, `hidden_test_cases`, `expected_output`,
`trust_state` or `adaptive_eligible` value was read-modify-written. No
reference created, no oracle run, no Judge0 call, no reseed.

The adapter corrects **future** execution semantics only. Every existing answer
key remains **UNPROVENANCED** until oracle verification and human adjudication.
A `NEEDS_MIGRATION` verdict explicitly means the stored outputs must be
re-derived, because canonical execution passes different arguments than the
ones that produced them.

## N. Blockers before semantic answer-key remediation

1. **162 `CONTRACT_MISMATCH` + 43 `INVALID_INPUT`** need human repair of the
   stored test data; no adapter can infer their intent.
2. **457 `NEEDS_MANUAL_REVIEW`** have no declared parameter types. Annotating
   the starters is a content task, not an execution one.
3. **49 ambiguous entry points** — `dir(sol)[0]` is alphabetical. Not fixable
   server-side; needs either starter changes or a v2-style "exactly one public
   method" rule, which is a template decision.
4. **v2's coercion defect** is unfixed and unfixable from the server (§E).
   Decision needed on whether to change `V2_PYTHON_WRAPPER`.
5. **Adoption mechanism.** v3 exists and is wired but zero questions declare
   it. Flipping a question's contract changes what its stored outputs mean, so
   it must follow reference → oracle → adjudication, per question.

## O. Blockers before reseed

**RESEED = NO.** Unchanged and not close.

1. execution semantics corrected — **done for v1 via v3**, not for v2;
2. semantic answer-key error rate measured on the randomised sample — **not
   started**; the sample is prepared and deliberately unexecuted;
3. remediation strategy approved — **not started**;
4. oracle/reference/adjudication workflow proven — pilot 1779 only;
5. a small batch through the complete pipeline — **not started**.

---

## §12 — P2.7h-1: the 12-case floor is NOT compatible as written

Determined, not wired. Two independent incompatibilities, both in
`groups/hidden_tests.py`:

**1. `validate_case` rejects blank stdin** (`"'stdin' is empty"`, line ~94).
Under canonical execution a zero-argument question has blank stdin *by
definition* — that is class H, and `[]` is the correct invocation. The gate
would reject every case of every such question. 21 graded questions declare
zero parameters.

**2. The duplicate-stdin rule makes the floor unreachable for them.**
`validate_suite` flags any repeated `stdin`. A zero-argument question has
exactly one possible stdin, so it can never have 12 non-duplicate cases. The
floor is structurally unsatisfiable, not merely unmet.

`validate_case` already type-checks the string fields, so it would correctly
catch the 48 `INVALID_INPUT` questions — that part is compatible.

**Exact integration point:** `groups/oracle_pipeline.py:189–194`, where the
floor breach is currently appended as an advisory blocker. Wiring it as
enforcing requires first deciding whether zero-argument questions are exempt
from both rules, or whether the canonical model should forbid zero-argument
questions outright. **Not integrated in this phase.**

---

## Limitations

**The full suite was not run.** No local PostgreSQL (port 5432 refuses), so
every DB-backed suite errors on connection setup. What was verified here:

- 193 tests in the three adapter/impact suites pass — all `SimpleTestCase`, no
  database, plus real subprocess execution of the generated wrappers;
- `manage.py check` clean;
- 1,950 tests collect cleanly under `pytest groups common`.

`groups/test_coding_views.py` exercises `GradingService.grade` through the view
and is DB-backed. **It has not been run against these changes.** The riskiest
change it covers is `grade()`'s test-case reading, which has direct
`SimpleTestCase` coverage and byte-for-byte v1 pinning — but that is an
argument for confidence, not a substitute for running it.

**`groups/input_contract.py` is now superseded** by the adapter and is wired to
nothing. A test asserts no production module imports it. It should be deleted
in a follow-up; two coercion models in one codebase is the drift hazard this
phase set out to remove.
