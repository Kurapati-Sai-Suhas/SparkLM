# P2.7h-25 — Early example check: design and offline proof

Design and offline proof only. Nothing written to production; no
`OracleExecution`, no reference, no batch, no ledger row.

**The check works: it caught q2027's known-bad example. It also found a
blocking pre-reseed defect nobody was looking for.**

---

## 10A — the boundary

### What already exists and is reused

Inspecting the oracle infrastructure first paid off — most of what an early
check needs is already built, and the two halves must be separated carefully:

| layer | reused? | why |
|---|---|---|
| `GradingService._build_executable` | **yes** | described in-code as "the shared execution seam"; decides what code runs. Needs no reference row. |
| `GradingService.prepare_stdin` | **yes** | the other half of that seam; decides what it is fed. One parser, so the check cannot drift from grading. |
| the Judge0 runner (`coding_views._run_on_judge0`) | **yes** | the same callable the grader and oracle take |
| `normalize_output` | **yes** | the same comparator, deliberately not loosened |
| `oracle_pipeline.run_question(record=False)` | **no** | iterates *stored hidden cases*; the pilot has none |
| `OracleService._execute` | **no** | requires `reference.is_canonical` — an APPROVED, ACTIVE `ReferenceSolution` with intact provenance. Creating one is a production write. |
| `provenance.record_execution` | **no, by construction** | never imported |

The reuse line is exact: **the execution layer, not the lifecycle layer.** The
early check gets grading-identical semantics and none of the trust
guarantees — which is precisely why it must not be confused with oracle
evidence.

### The six questions

1. **Can a reference be validated before hidden-test expansion?** Partially. It
   can be *executed* and sanity-checked. It cannot be *approved* — approval
   runs through `ReferenceSolution.submit_for_review/approve/activate`, which
   is a production write.
2. **Can one generated example be executed safely?** Yes — Judge0 sandbox, one
   call, no database access on the path.
3. **Can the example be checked against the verified specification?** Indirectly
   and honestly: against a reference *written from* that specification. The
   specification is prose; only code computes.
4. **Without recording provenance?** Yes, and better than expected —
   `execute_case`/`run_question` already default to `record=False`. The new
   module goes further and imports no provenance writer at all.
5. **Can an example be rejected offline before reseed?** Yes. Proven on q2027.
6. **What prevents confusion with full oracle verification?** Four independent
   things: verdict strings that collide with no lifecycle value (tested against
   `STATUS_CHOICES`, `TRUST_CHOICES`, `OracleExecution.STATUS_CHOICES`); every
   record stamped `evidence_class=EARLY_EXAMPLE_CHECK`,
   `is_oracle_evidence=False`, `supports_trust_transition=False`; an AST-level
   test that the module names no writer; and the docstring table below.

```
full oracle                          early example check
─────────────────────────────────    ──────────────────────────────────
APPROVED, ACTIVE ReferenceSolution   a REFERENCE_CANDIDATE, unreviewed
every hidden case                    one example
REQUIRED_RUNS identical runs         one run; determinism NOT established
OracleExecution provenance written   nothing written, anywhere
can support ORACLE_VERIFIED          can support NOTHING in the lifecycle
```

---

## 10B — reference implementation options

| | A. human-written | B. LLM-written + review | C. trusted source | D. derived from hidden tests |
|---|---|---|---|---|
| authority | highest | **none until reviewed** | depends on the source | circular |
| risk | author error | plausible-but-wrong code | licensing, availability | **cannot be used** — it would define the answers from the answers |
| reproducibility | high | low (sampling) | high | n/a |
| cost | human minutes/question | seconds + review | acquisition | n/a |
| provenance | author recorded | provider + prompt version + digest | source + licence | n/a |
| independent of the artifact? | yes | yes, if prompted from the specification | yes | no |
| usable before hidden tests? | yes | yes | yes | **no** |

**D is disqualified on principle** and is listed to be ruled out: deriving a
reference from hidden tests makes the answer key its own evidence.

**B is what this proof used**, and the module refuses to let that be forgotten:
`ReferenceCandidate(origin="llm")` raises unless a provider *and* prompt
version are supplied, and `status` stays `REFERENCE_CANDIDATE` until a
`reviewed_by` is recorded.

**Why an LLM-written reference is not oracle evidence:** it is the same class
of artifact as the statement it is checking, produced by the same kind of
process, with no independent authority. It can agree with a wrong example
because both were written by a model with the same misconception. Its only
claim is *"an implementation someone wrote produced this number"*.

---

## 10C — the prototype

`groups/reseed_example_check.py`. Takes the verified specification's digest,
the artifact, a candidate example and a `ReferenceCandidate`; returns a record
classifying the example and carrying everything needed to reproduce it:

```
evidence_class, question_id, specification_digest, artifact_digest,
arguments, claimed_output, reference{digest, origin, provider,
prompt_version, reviewed_by, status}, actual_output, status_id, stderr,
verdict, detail, is_oracle_evidence=False, supports_trust_transition=False
```

Verdicts: `EXAMPLE_PASS`, `EXAMPLE_WRONG_OUTPUT`, `EXAMPLE_RUNTIME_ERROR`,
`EXAMPLE_INVALID_INPUT`, `EXAMPLE_UNRESOLVED`.

Extraction returns `None` rather than guessing, and an unreadable example is
`EXAMPLE_UNRESOLVED` — never a pass. *A parser that shrugs and approves is
worse than no parser.*

---

## 10D — q2027 regression: **PASS**

```
input            ['AABAA']
artifact claims  True
reference output 'false'        status_id=3 (Accepted)
VERDICT          EXAMPLE_WRONG_OUTPUT
reference        REFERENCE_CANDIDATE  841b804d…  origin=llm
oracle evidence? False   supports trust? False
```

The artifact that passed structural, conformance and presentation validation is
caught here in one execution.

### Verdict and explanation are separate claims

The regenerated q2027 artifact (`colors="AABAAAB"` → `true`) returns
`EXAMPLE_PASS`, and its explanation is still wrong: it says "Alice can remove
the middle A", but for `AABAAAB` the middle is index 3, an `A` with a `B`
neighbour; the removable `A` is at index 4.

So `explanation_status()` returns `EXPLANATION_UNVERIFIED` and only a recorded
human reviewer changes that. A mutation making it return `EXPLANATION_VERIFIED`
unconditionally is killed.

---

## 10E — all five artifacts

| Question | Artifact | Example verdict | Explanation | Result |
|---|---|---|---|---|
| q1940 | artifacts-v2 | `EXAMPLE_PASS` — `("abc", 2)` → `6` | `EXPLANATION_UNVERIFIED` | example consistent |
| q1974 | artifacts-v2 | **`EXAMPLE_UNRESOLVED`** — contract cannot bind | `EXPLANATION_UNVERIFIED` | **blocked, see below** |
| q2027 | artifacts-v3 | `EXAMPLE_PASS` — `("AABAAAB")` → `true` | `EXPLANATION_UNVERIFIED` — **known wrong** | verdict consistent, prose defective |
| q2057 | artifacts-v2 | `EXAMPLE_PASS` — `("abc","bcd")` → `true` | `EXPLANATION_UNVERIFIED` | example consistent |
| q2290 | artifacts-v2 | `EXAMPLE_PASS` — `(1)` → `4` | `EXPLANATION_UNVERIFIED` | example consistent |

A reference agreeing with an example proves neither that the specification is
right (the operator established that separately) nor that the reference is.

---

## The finding nobody was looking for

**q1974 cannot be executed at all under its declared contract, and neither can
any reseeded question whose method takes a single list.**

The first run reported:

```
Runtime Error: Solution.findGreatestCommonDivisorOfArray() takes 2 positional
arguments but 4 were given
```

The v1 generic harness parses stdin, sees a JSON list, and splats it:
`target_method(*parsed_input)`. So `[3, 6, 4]` becomes three arguments to a
one-parameter method. The contract cannot distinguish "one list argument" from
"three scalar arguments".

Verified end-to-end that v3 fixes it, entirely in memory:

```
v3 + the DECLARED starter → stdin '[[3,6,4]]' → status Accepted → stdout '3'
production Question rows still 2926 — the probe was never saved
```

And v3 with the question's **stored** starter correctly refuses:
`NEEDS_MANUAL_REVIEW — starter takes *args/**kwargs, so no arity is declared`.

Three consequences for the reseed pipeline:

1. **Contract migration to v3 is mandatory**, not optional, for any candidate
   whose declared signature takes a single container. `remediate_contract` and
   `learnlm_contract_rw` already exist for exactly this.
2. **Ordering is forced**: statement → signature declaration → **contract
   migration** → any example or oracle execution. v3 binding needs the declared
   starter, which only `declare_signature` writes.
3. Classifying this as `EXAMPLE_RUNTIME_ERROR` would have sent someone hunting
   a bug in the reference. The pre-flight now reports it as
   `EXAMPLE_UNRESOLVED` naming the contract, and it reads the **artifact's**
   starter rather than the question's stored placeholder — checking the stored
   one asks whether the contract can bind a signature that does not exist yet,
   and answers yes, wrongly. Both behaviours are tested; removing the pre-flight
   is mutation O12 and is killed.

---

## 10F — q1974 with either specification

The proposed style revision (`eb8458fd…`) is **not applied**; the verified text
(`ad2a9eb0…`) remains on disk.

The early check is unaffected by the choice. It consumes the specification only
as a **digest recorded in the record** — the executable behaviour comes from
the reference, and the reference is written from the requirement, which both
wordings state identically (`SEMANTIC DIFFERENCE = NONE`, established in Phase
9.6). Under either version q1974 returns `EXAMPLE_UNRESOLVED` for the contract
reason, which is a property of the question's contract, not its prose.

---

## 10G — mutation: 13 killed / 14, 0 real survivors

```
O1  expected-output comparison removed           killed
O2  reference execution skipped entirely         killed
O3  runtime error treated as a pass              killed
O4  wrong output treated as a pass               killed
O5  unreadable example treated as a pass         killed
O6  unavailable runner treated as a pass         killed
O7  LLM reference no longer needs provenance     killed
O8  unreviewed reference reported as reviewed    killed
O9  explanation automatically verified           killed
O10 result claims to be oracle evidence          killed
O11 a verdict collides with a lifecycle value    killed
O12 contract pre-flight removed                  killed
O13 unencodable input treated as a pass          killed
E1  EQUIVALENT: comment wording                  survived
```

One test defect was found and fixed before the sweep: the "writes nothing"
guard did a raw substring search and flagged the module's own docstring, which
explains at length how it differs from `OracleExecution`. It now walks the AST
and checks identifiers — the same mistake as an earlier check that matched
`.update(` inside `digest.update(`.

---

## 10H — provenance: **no migration needed**

A durable file artifact is sufficient, and safer than a table.

Every record binds four digests — specification, artifact, reference, plus the
question id — so it cannot be silently reassociated. Written as
`<id>.example-check.json` beside the artifact, it is exactly as durable as the
artifacts themselves and lives in the same reviewed directory.

**A new table would be actively worse.** The one hazard worth engineering
against is a developer feeding an early result into the approval lifecycle as
if it were oracle evidence. A model living beside `OracleExecution` invites
exactly that. A JSON file cannot be passed to `question_promote`, cannot be
joined against `QuestionApproval`, and cannot satisfy `reference.is_canonical`.

Enforcement, in four independent layers:

1. verdict strings collide with **no** lifecycle value — tested against all
   three choice lists
2. every record carries `is_oracle_evidence=False` and
   `supports_trust_transition=False`
3. the module names no writer — AST-verified
4. `ORACLE_VERIFIED` still requires `OracleExecution` rows, which nothing here
   can produce

---

## 10I — reference safety

`ReferenceCandidate` makes provenance structural: `origin` must be one of
`human`/`llm`/`trusted_source`, and an `llm` origin **raises** without a
provider and prompt version. Status is `REFERENCE_CANDIDATE` until a
`reviewed_by` is recorded; nothing promotes it automatically. The source digest
binds the exact code executed.

All five references used here are `origin="llm"`, provider
*"assistant (written from the operator-verified specification)"*, prompt
`reference-from-verified-specification/v1`, **unreviewed**. They are adequate
to *reject* an example and inadequate to *bless* one.

---

## Regression

```
3002 passed, 2 warnings in 297s      (pytest --ignore=scripts)
example-check suite: 29 passed
```

## 10J — production

```
Questions 2926 · hidden tests unchanged · status/trust unchanged
References 3 · OracleExecutions 70 · Approvals 2 · Submissions 44
RemediationActions 17 · ReseedLedger 0 · Batches 1
bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  unchanged
```

The v3 probe was an unsaved in-memory `Question`; row count confirmed 2926
after it.

---

## What this changes for the next phase

Two things must now happen before any production reseed, and neither was on the
roadmap this morning:

1. **A contract decision for the candidate population.** How many of the 1,141
   declare a single-container parameter? Every one of them needs v3 before an
   example — or a hidden test — can execute. That is a census, and it is
   cheap.
2. **Reference review is a real cost.** Five unreviewed LLM references sit
   behind these results. They can reject; they cannot approve.
