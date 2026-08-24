# P2.7 — Contract census for the reseed candidate population

**Phase 11 · M2 P2.7h-26 · read-only analysis and local tests**

Status: complete. No production write, no Judge0 call, no contract migration,
no reseed. The pilot has not started.

---

## 1. The question, and the answer

> Of the reseed candidates, how many can be executed under the current
> contract, how many will require v3 after signature declaration, and how many
> cannot yet be determined because their future signature is unknown?

```
executable under the contract they carry today            0
will require v3 after signature declaration        UNKNOWN
cannot be determined yet                              1,140   (all of them)
```

All three numbers fall out of one fact: **every reseed candidate stores a
variadic placeholder.** `def solve(self, *args, **kwargs)` declares no arity
and no types. There is no signature to evaluate, so there is no contract
verdict to give — and inventing one would be a guess recorded as a fact.

This is not a gap in the census. It is the census's finding. The contract
decision is not knowable before `declare_signature` runs, and it becomes
knowable, per question and at zero cost, the moment it does.

---

## 2. The candidate population — and why it is 1,140, not 1,141

Phase 6 reported 1,141. The operative predicate reports **1,140**. Nothing
moved; the two predicates differ, and the difference is worth keeping.

A candidate is what the *authoring path* will accept — `statement_blockers()`
returning empty:

| clause | requirement |
|---|---|
| `draft` | `status == DRAFT` |
| `unverified` | `trust_state == UNVERIFIED` |
| `no_cases` | no stored hidden test cases |
| `no_approval` | no `QuestionApproval` |
| `no_execution` | no `OracleExecution` |
| `placeholder_marker` | `content` still carries the placeholder marker |

Exactly two questions fail exactly one clause each, and either relaxation
yields 1,141:

| question | title | fails only |
|---|---|---|
| q2201 | Largest Number After Digit Swaps by Parity | `no_cases` — 4 stored tests |
| q92 | Reverse Linked List II | `placeholder_marker` — has a real statement |

Both exclusions are correct. Authoring over q2201 would silently change what
its four existing tests are testing; authoring over q92 would overwrite a
statement someone may have written. `--near-miss` prints this list, so the
number can never quietly change meaning again.

---

## 3. The binding matrix

What actually decides how a question is invoked, in the order the code decides
it (`GradingService._build_executable`, then `prepare_stdin`):

| | harness | stdin | single-container parameter |
|---|---|---|---|
| **custom wrapper** | the question's own | raw | its own contract; version never consulted |
| **v1** | `GENERIC_PYTHON_WRAPPER` | raw, literal-`\n` expanded | **mis-splat** |
| **v2** | `V2_PYTHON_WRAPPER` | raw; one line per parameter | correct |
| **v3** | `GENERIC_PYTHON_WRAPPER` — *the same one* | canonical envelope built server-side | correct by construction |

Three points that matter more than the table:

1. **A custom wrapper is checked first** and defeats the contract version
   entirely. Such a question can never exhibit the splat defect and must never
   be scheduled for a v3 migration. Zero candidates carry one.
2. **v3 introduces no new harness.** It reuses v1's template verbatim and
   differs only in the bytes fed to it. That is why migrating a question
   changes what its stored expected outputs *mean* — and why migration can
   never be a bulk operation.
3. **v2 and v3 read the signature from different places.** v2 calls
   `inspect.signature` on the *learner's submitted code* at run time; v3 reads
   the *stored starter* at request time. For reseed, only v3's source is under
   our control, which is why v3 — not v2 — is the relevant contract.

### The v3 requirement rule

v1 JSON-parses the whole stdin blob and, if the result is a list, splats it
positionally. That is correct whenever the parameter count matches the element
count. It is wrong in exactly one shape:

```
(nums: list[int])   stdin "[3, 6, 4]"  ->  f(3, 6, 4)    WRONG
(s: str, k: int)    stdin "abc\n2"     ->  f("abc", 2)   right
(colors: str)       stdin "aabbcc"     ->  f("aabbcc")   right
```

> **v3 is required ⟺ exactly one parameter, and its value is a container.**

The rule is three-valued, not boolean. `UNKNOWN` is returned for a variadic
stub, a keyword-only parameter, no callable — and for **a lone unannotated
parameter**, whose arity is known but whose kind is not. That last case is the
subtle one, and reading it as "does not need v3" is a real defect: it moved 208
questions into the wrong column of the reference class and shifted the
projected v3 population by four points before the tests caught it.

---

## 4. Census results

```
Questions in bank        2926
Reseed candidates        1140
  declaring a signature     0

Stored contract      v1        1140
Harness              generic   1140

Signature shape
  E  VARIADIC        1140    *args/**kwargs — no arity is declared

v3 requirement
  V3_REQUIRED           0
  V1_SUFFICIENT         0
  UNKNOWN            1140
```

The population is perfectly uniform: one contract, one harness, one shape, one
verdict. 1,124 distinct placeholder texts across 1,140 questions (17 share the
generic `solve` stub), but every one of them is variadic.

### Shape classes

Mutually exclusive, first match wins, ordered so a question is described by the
earliest thing that stops the harness binding it. Only class E is populated
among candidates; the rest are pinned by tests so the census stays honest when
signatures start arriving.

| | class | meaning |
|---|---|---|
| A | `NO_PYTHON_STARTER` | no python entry; nothing to bind |
| B | `UNPARSEABLE` | not valid Python |
| C | `NO_CALLABLE` | parses, declares nothing callable |
| D | `AMBIGUOUS_ENTRY_POINT` | >1 public method; v1 picks alphabetically, v2 refuses |
| E | `VARIADIC` | **the reseed placeholder — all 1,140** |
| F | `KEYWORD_ONLY` | stdin cannot supply argument names |
| G | `ZERO_PARAMETERS` | stdin is not delivered anywhere |
| H | `UNANNOTATED` | parameters declared, kinds unknown |
| I | `SINGLE_CONTAINER` | **the shape v1 mis-splats** |
| J | `SINGLE_SCALAR` | one parameter, scalar kind |
| K | `MULTI_PARAMETER` | splat arity is correct |

---

## 5. The q1974 regression, reproduced offline

`findGreatestCommonDivisorOfArray(self, nums: list[int]) -> int`, in memory, no
Judge0:

```
v1   stdin "[3, 6, 4]"  ->  json.loads -> [3, 6, 4] -> method(*[3,6,4])
                        ->  TypeError: takes 2 positional arguments but 4 were given

v3   stdin "[3, 6, 4]"  ->  build_invocation -> arguments [[3, 6, 4]]
                        ->  envelope "[[3,6,4]]" -> method([3, 6, 4]) -> 3
```

The variadic stub that q1974 **currently stores** refuses v3 binding outright
(`NEEDS_MANUAL_REVIEW`, detail names `*args`), and `prepare_stdin` raises
`no arity is declared`. That refusal is the guard that stops a premature bulk
migration, and it is asserted, not described.

q1974 is also the empirical anchor for the whole phase: of the five pilot
signatures actually declared so far, it is the one — and the only one — that
needs v3.

| pilot | declared signature | verdict |
|---|---|---|
| q1940 | `(s: str, k: int)` | V1_SUFFICIENT |
| **q1974** | `(nums: list[int])` | **V3_REQUIRED** |
| q2027 | `(colors: str)` | V1_SUFFICIENT |
| q2057 | `(word1: str, word2: str)` | V1_SUFFICIENT |
| q2290 | `(n: int)` | V1_SUFFICIENT |

---

## 6. Projecting the v3 population

Per-question, every candidate is `UNKNOWN` and stays that way. But the size of
the eventual migration can be estimated from a **measured reference class**:
the 1,786 non-candidate questions, 1,505 of which already declare a signature.
This is not inference from titles, topics or difficulty — none of those enter
the calculation. It is the shape distribution of the same bank, authored by the
same pipeline.

```
reference class (1,786 non-candidates)
  V3_REQUIRED       364
  V1_SUFFICIENT   1,141
  UNKNOWN           281      (excluded from the denominator)

base rate  364 / 1,505  =  24.2%

projected v3 need among the 1,140 candidates:
  276   (251–301 at 95%)
```

**This is a population estimate and nothing else.** It is not written to any
question, it does not set any contract, and it does not license a bulk
migration. It exists to size the work: expect roughly a quarter of the reseed
population to need v3, not all of it and not none of it. The pilot's 1-in-5 is
consistent with 24.2% at this sample size.

### A side finding, out of scope but worth recording

Of the 364 reference-class questions whose signature reads `V3_REQUIRED`,
**363 are stored as v1**. These are live questions with single-container
signatures on the contract that mis-splats them. This census does not act on
that — `contract_impact` already covers the served population and factors in
stored cases and custom wrappers — but the number belongs in the record.

Two questions in the bank already declare v3: **q3309** and **q1436**, both
`PUBLISHED` / `ORACLE_VERIFIED`, both migrated in earlier phases of this
roadmap. The comments in `execution_contract.py` and
`GradingService.prepare_stdin` still claim "zero questions declare v3" and are
now stale. Flagged, not edited — this phase does not touch production code
paths.

---

## 7. Migration lifecycle: when the contract can be set

The ordering constraint the census exposed:

```
reseed_statement    ─┐
                     ├─  question still has NO signature and NO cases
declare_signature   ─┘   ← the contract becomes decidable exactly here
                          |
        v3_requirement(starter) -> V3_REQUIRED | V1_SUFFICIENT
                          |
generate hidden tests    ─┐
                          ├─ cases must be authored under the chosen contract
oracle verification      ─┘
```

The verdict must be computed **immediately after `declare_signature` and
before any test case is authored**, because the contract determines what a
stored expected output means. Authoring cases first and migrating after would
silently reinterpret every one of them.

### `remediate_contract` cannot do this job

The existing migration command refuses a question with no stored test cases:

> `the question stores no test cases, so nothing demonstrates that the contract executes`

That refusal is correct for its own purpose — migrating a *live* question
without evidence would be reckless. But it makes the command unusable at the
point in the reseed pipeline where the contract must be set, because a
candidate has no cases *by definition* at that moment.

The reseed path therefore needs its own contract-setting step, subject to the
same write-authority discipline as `reseed_statement` and `declare_signature`:
a single dedicated column-scoped role, a pre-image record, an expected-digest
precondition re-checked inside `select_for_update()`, and a ledger stage. That
step is **designed here and not built** — it is Phase 12 work, and it writes.

---

## 8. Tests and mutation

`groups/test_contract_census.py` — **72 tests, all passing.** Local and
synthetic: no Judge0 call, no production read, no write. The rules the census
depends on are asserted against the real `execution_adapter` and the real
`GradingService` seam rather than described in prose, because the census counts
questions and a count resting on a wrong rule is worthless.

```
27 killed / 28   ·   real survivors: 0

V1  a lone unannotated parameter is called sufficient (the bug)   killed
V2  multi-parameter signatures report UNKNOWN                     killed
V3  variadic starters are given a verdict                         killed
V4  keyword-only parameters are given a verdict                   killed
V5  the container test is inverted                                killed
V6  mappings are no longer containers                             killed
V7  an empty signature is given a verdict                         killed
V8  a blank starter is given a verdict                            killed
W1  a custom wrapper is scheduled for a v3 migration              killed
W2  a blank wrapper string counts as a custom harness             killed
W3  a non-dict wrapper field is treated as custom                 killed
S1  variadic is checked before ambiguous entry point              killed
S2  unparseable and no-callable are merged                        killed
S3  the unannotated class is never reported                       killed
S4  single container and single scalar are swapped                killed
S5  zero-parameter methods fall through to a shape verdict        killed
S6  a missing starter is not distinguished                        killed
S7  keyword-only starters are classified by arity instead         killed
S8  a variadic placeholder is reported as declaring a signature   killed
P1  UNKNOWNs are counted in the reference denominator             killed
P2  an empty reference class still produces a number              killed
P3  the interval collapses to the point estimate                  killed
P4  the projection claims to be a per-question verdict            killed
G1  the summary double-counts a question's shape                  killed
C1  the stored-cases clause is dropped from the predicate         killed
C2  the placeholder marker clause is dropped                      killed
C3  the census writes                                             killed
E1  EQUIVALENT: wording of a comment                              survived
```

Four survivors on the first sweep — V3, V4, V8 and S8 — and all four were real
gaps, not equivalent mutants:

- **V3 / V4.** For a *pure* `*args` stub `declared_signature` returns no
  parameters, so the empty-signature check already answers `UNKNOWN` and
  removing the variadic and keyword-only guards changed nothing observable.
  Add one real parameter alongside — `f(self, a: list[int], *args)` — and the
  two diverge: the signature now reports exactly one container parameter and
  reads as a confident `V3_REQUIRED` for a function that may take four
  arguments. The guards are not redundant; the tests were.
- **V8.** `boilerplate_code` is a JSON field, so its `python` entry is whatever
  was stored. `ast.parse` raises `TypeError`, not `SyntaxError`, on a
  non-string, and a census that dies on one bad row reports nothing about the
  other 1,139.
- **S8.** Nothing asserted that a variadic placeholder reports
  `declares_signature = False` — the exact field carrying the headline claim
  that **zero** candidates declare a signature.

Sixteen tests were added to close them. The equivalent-mutant canary survived,
as it must.

Full regression: **2,396 passed.**

---

## 9. Production integrity

```
Question           2926 = 2926   OK      RemediationAction    17 =  17   OK
ReseedLedger          0 =    0   OK      RemediationBatch      1 =   1   OK
ReferenceSolution     3 =    3   OK      QuestionApproval      2 =   2   OK
OracleExecution      70 =   70   OK      CodeSubmission       44 =  44   OK
QuestionPreImage      7 =    7   OK

bank fingerprint  3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6
                  MATCH
```

A note on that fingerprint, because it briefly appeared not to match. The
canonical formula is the raw-SQL one recorded in Phase 2 — `md5` of
`hidden_test_cases` and `content`, joined with `"|"`, and it does **not**
include `boilerplate_code`. A first pass here used `repr()` over a wider field
tuple and produced a different digest. Two formulas, one bank; recomputed under
the canonical recipe it matches exactly. Phase 2 hit the same ambiguity and
documented it, which is the only reason it took minutes rather than an
afternoon to rule out.

**No question row, hidden test, status, trust state, reference, execution,
approval, submission or ledger row was created, modified or deleted.** No
production batch was created. `reseed_statement` and `declare_signature` were
not run. No contract was migrated.

---

## 10. What this phase concludes

1. The contract question **cannot be answered for any candidate today**, and
   the honest count is 1,140 UNKNOWN rather than a number that looks decisive.
2. It becomes answerable **per question, deterministically, at zero cost**, the
   moment `declare_signature` writes a real starter.
3. The eventual v3 population is **roughly 276 of 1,140 (251–301)** — a quarter
   of the work, not all of it — from a measured reference class, as a sizing
   estimate only.
4. `remediate_contract` **cannot perform the migration** where the pipeline
   needs it, and the reseed path needs its own write-authorised contract step.
5. The correct order is **declare signature → decide contract → author cases**,
   never author-then-migrate.

Next phase, when authorised: build the contract-setting step (it writes), or
begin the five-question pilot. Neither has started.
