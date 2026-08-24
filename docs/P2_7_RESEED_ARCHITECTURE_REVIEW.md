# P2.7 — Bulk reseed architecture review

Analysis and design only. No write, no role, no migration, no reseed.

---

## PHASE A — Candidate census

```
CANDIDATES 1141          status DRAFT 1141 · trust UNVERIFIED 1141 · contract v1 1141

EVIDENCE ATTACHED
  references 0 · oracle executions 0 · approvals 0 · pre-images 0
  submissions 0 · adaptive-eligible 0

HIDDEN TESTS          0 cases  1140     4 cases  1   ← q2201, anomaly
STARTERS              variadic (*args/**kwargs)  1141      public methods per starter  {1: 1141}
BINDABILITY           NEEDS_MANUAL_REVIEW        1141      (every single one)
LANGUAGES             {cpp, java, python} 1124  ·  {java, python} 17
TOPICS                15 distinct — Array 776 (68%), String 100, Math 96, Hash Table 74, …
DIFFICULTY            1000.0 → 263 · 1300.0 → 582 · 1600.0 → 296   (three values, whole set)
TITLES                1141 distinct · 0 duplicates · 0 blank · 1124 with stray whitespace
CONTENT               230–455 chars, templated · hidden_wrapper_code 0
```

**These are stubs, uniformly.** Every one has a single public method, a variadic
starter declaring no arity, contract v1, and no evidence of any kind. Not one of
the 1,141 can bind a test case today.

### Anomalies

| anomaly | count | consequence |
|---|---|---|
| **q2201 has 4 hidden tests** with a placeholder statement | 1 | Answer keys of unknown provenance already exist. It is invisible **only** because of the placeholder exclusion — replacing its content alone would make it servable and gradable against unverified keys. Exclude from any pilot; treat as its own case. |
| 1,124 titles carry leading/trailing whitespace | 1124 | Cosmetic, but it reaches the LLM prompt and the learner. Strip at generation. |
| 17 questions have no `cpp` starter | 17 | Not unsafe; the generator must not assume a fixed language set. |
| `base_difficulty` takes 3 values across the whole set | 1141 | Not a reseed problem — it is the static label Glicko-2 replaced. Reseed must **not** write it. |

Everything else is uniform. **Safe-to-consider population: 1,140** (all except q2201).

## PHASE B — What "reseed" means

| interpretation | verdict |
|---|---|
| A · fully trusted questions | **Rejected.** Trust requires an oracle, a human approval and a promotion. No generator can produce it. |
| B · learner-visible questions | **Rejected.** Visibility is the *consequence* to avoid; see Phase G. |
| C · bindable DRAFT questions | Close, but "bindable" implies the contract migration, which requires cases — outside a content generator's authority. |
| D · content + starter only | Correct scope, but silent about what happens next. |
| E · content + starter + hidden tests | **Rejected.** Writes answer keys, creates a back-door `EXPECTED_OUTPUT_REPAIR`, and flips questions to servable. |
| **F · content + starter, then hand off to the existing lifecycle** | **RECOMMENDED.** |

**Reseed generates a statement and a signature. Nothing else.** It ends with a
question that is legible to a human and *ready to be authored against* — still
DRAFT, still UNVERIFIED, still contract v1, still with an empty suite, and
therefore still invisible to learners.

This preserves every property: least privilege (two column owners, no union),
provenance (one action per column), reversibility (pre-image per question),
auditability (`RemediationAction` per write), and the oracle/approval/publication
separations, which reseed never touches.

## PHASE C — Single-question lifecycle, derived from the guards

```
STUB (placeholder content · *args starter · v1 · no cases)

 1 CONTENT      generate statement                  AUTOMATED + HUMAN GATE
 2 SIGNATURE    replace *args/**kwargs              AUTOMATED + HUMAN GATE   ← new capability
 3 CASES        author suite ≥12, categorised       HUMAN AUTHORED
 4 CONTRACT     v1 → v3                             AUTOMATED (fully gated)
 5 REFERENCE    create → review → approve → activate HUMAN GATE (approve)
 6 QUALITY GATE mutant spec + run                   HUMAN AUTHORED spec
 7 ORACLE       execute, ≥2 agreeing runs/case      AUTOMATED
 8 REVIEW       read-only, emits the digest         AUTOMATED
 9 APPROVAL     digest handshake                    HUMAN
10 PROMOTION    → ORACLE_VERIFIED                   HUMAN (--confirm)
11 STATUS       DRAFT → PENDING_REVIEW → PUBLISHED  HUMAN (--confirm)
12 SERVING / ADAPTIVE ELIGIBILITY                   consequence, never written
```

### The `signature → cases → contract` dependency, precisely

The earlier statement needs refining, because the code enforces less than
assumed:

- **`expand_hidden_tests._check_executable` returns early unless the contract is
  v3.** Under v1 it performs **no binding check at all** — cases can be written
  to a stub and nothing will notice they can never execute.
- **`remediate_contract` is the enforcing gate.** It refuses a question with no
  cases (*"the question stores no test cases, so nothing demonstrates that the
  contract executes"*), refuses a starter with no declared signature, refuses
  >1 public method, refuses a wrapper, and binds **every** stored case under v3,
  refusing on failure *or on a warning*.

So the order is required for **correctness** but only enforced at step 4. The
design must therefore not rely on step 3 to catch anything: the case-authoring
plan must run its own binding check, exactly as the q1436 proof script did before
that write. Reversing the order — cases before signature — produces a suite that
looks written and fails at the contract gate, after the work is done.

**Automatable:** 1, 2 (generation), 4, 7, 8.
**Human-gated, not automatable:** 3 (a case is a claim about the answer), 5
(approval), 6 (mutants are claims about misconceptions), 9, 10, 11.

Steps 5–11 are unchanged from q1436 and are **not** part of reseed.

## PHASE D — Bulk architecture

Slices of ~50, never one transaction.

| concern | design |
|---|---|
| batch identity | one `RemediationBatch` per slice, `reseed-YYYY-MM-<letter>`, `purpose` naming the selector and the slice bounds |
| candidate snapshot | membership frozen at capture; a question that leaves the selector afterwards is still a member, which is what makes rollback well-defined |
| pre-image | `preimage_capture` for all 50 **before** freeze, by `learnlm_preimage_rw` |
| projected digest | computed per question in the dry-run and re-checked at write time |
| transaction boundary | **one question, one transaction**, containing its column write + its `RemediationAction` |
| audit action | one action per question per column — a `STATEMENT_*` and a `SIGNATURE_*` class |
| failure handling | a failed question rolls back only itself; the slice continues |
| retry / resume | driven by a **ledger**, not by the selector — a question that got content but not a signature no longer matches `PLACEHOLDER_MARKER` and would be invisible to a re-run |
| rollback | existing `preimage_rollback`, per question, restoring only differing fields |
| concurrency | `select_for_update()` on the question inside its transaction, as every existing remediation command does |
| stale digest | `--expect-digest` per question, re-checked under the lock; refuse rather than overwrite |

The ledger is the one genuinely new piece of state and exists because the
selector is self-erasing: reseed's own success removes a question from the
candidate set, so progress cannot be derived from the selector.

## PHASE E — Role architecture

| option | assessment |
|---|---|
| A · one union `learnlm_reseed_rw` | Unions statement + starter authority. The separation exists because a role that can rewrite a statement *and* its starter can change what is asked and how it is bound in one act. **No overwhelming justification exists** — the two writes are days apart in review terms and need not share a transaction. **Rejected.** |
| **B · existing owner roles** | `learnlm_remediate_rw` writes `content`; `learnlm_boilerplate_rw` writes `boilerplate_code`. Zero new grants. Cost: two connections, so the two writes cannot share one transaction — mitigated by the ledger and by ordering (a new statement on a stub is harmless alone). **RECOMMENDED.** |
| C · separate statement/starter/key reseed commands | Same privilege shape as B; the difference is command surface, not authority. Adopt as the *shape* of B: two commands, two roles, one column each — matching every existing remediation command. |

**B, expressed as C.** No role is created in this phase.

One gap to close inside B: `remediate_boilerplate` is **annotation-only** — *"no
renamed class, method or parameter, no reordered parameters, no altered body"*.
Replacing `*args, **kwargs` with a declared signature is none of those. The
*role* is right; the *command* needs an explicitly-reviewed signature-declaration
mode, guarded to: same class, same method name, body unchanged, parameters only
ever going from variadic to declared — never renamed once declared.

## PHASE F — EXPECTED_OUTPUT_REPAIR: how the back door stays shut

`CLASS_EXPECTED_OUTPUT_REPAIR` exists with **no command**, deliberately —
`oracle_execute` states it cannot write `expected_output` "not behind a flag, not
with `--force`: the writer does not exist anywhere in this phase".

Reseed avoids becoming that writer by never touching `hidden_test_cases` **at
all**. Expected outputs enter through exactly one path, unchanged from q1436:

```
human-authored plan file (remediation/q<id>_suite_expansion.json)
   └─ reviewed: each case's input, answer, category and rationale
   └─ proved BEFORE the write: binds under the contract, no duplicate identity,
      reference agreement on every case
        ↓  expand_hidden_tests --alias hiddentest   (learnlm_hidden_test_rw)
   └─ digest handshake · frozen batch · verified pre-image
   └─ SUITE_EXPANSION action recorded
        ↓
   oracle_execute  →  each stored answer is LEGACY until an execution's output
                      digest matches it (`CaseEvidence.is_oracle_backed`)
        ↓
   build_artifact blocks approval on any case that is not oracle-backed
```

The answer key is therefore always *proposed* by a human and *confirmed* by
executing an approved reference. An LLM may draft a case in the plan file — that
is a document, not a database row — but it cannot reach `hidden_test_cases`
without a human review and cannot reach ORACLE_VERIFIED without independent
execution agreement.

**The residual hazard is a CONFLICT with no repair path.** If the oracle
disagrees with a stored answer, the case is blocked and `EXPECTED_OUTPUT_REPAIR`
has no writer, so the question is stuck. Mitigation, in order of preference:
(1) prove reference agreement *before* writing the suite — the q1436 procedure,
which makes conflict nearly impossible; (2) if a conflict still occurs, roll the
suite back via the pre-image and re-author. **Do not build an expected-output
writer to resolve it.**

## PHASE G — Serving safety

Serving policy is unchanged by this design.

`_servable_questions()` excludes placeholder content **and** empty
`hidden_test_cases`. Reseed writes content — lifting the first exclusion — and
does **not** write cases, so the second exclusion still holds:

```
before reseed   placeholder ✔ excluded   empty suite ✔ excluded   → invisible
after  reseed   real content ✗           empty suite ✔ excluded   → STILL INVISIBLE
after  suite expansion (reviewed)                                 → becomes servable
```

A reseeded question becomes learner-visible **only** when a human-reviewed suite
is written — which is also the moment reference agreement has been proved. That
is the safety property that makes option F safe without any serving-policy
change.

**Exception: q2201.** It already has 4 cases, so generating its content would
lift the only exclusion holding it back and make it immediately gradable against
answer keys nobody has verified. **Exclude it from bulk reseed** and route it
through the full q1436 lifecycle individually.

Gating the bank on `PUBLISHED` remains a **future product decision** (P2.7h-12)
and is not proposed here.

## PHASE H — Trust lifecycle

```
DRAFT ──────────── reseed leaves it here
  │  question_status (learnlm_status_rw), after a suite exists
PENDING_REVIEW ─── required before promotion (DB CHECK forbids DRAFT+ORACLE_VERIFIED)
  │  question_promote (learnlm_promote_rw), needs a promoted-eligible approval
ORACLE_VERIFIED
  │  question_status → PUBLISHED, gated on the full re-derived evidence chain
PUBLISHED
  │  is_adaptive_eligible = PUBLISHED and ORACLE_VERIFIED, read at submission time
ADAPTIVE ELIGIBLE
```

**Confirmed: reseed does none of it.** It cannot set `trust_state` (no grant, no
code path — `question_promote` is the only writer in the codebase), cannot set
`status` (`question_status` is the only writer), creates no oracle evidence, no
approval, and no submissions. Its two columns are `content` and
`boilerplate_code`, and neither participates in any trust decision.

## PHASE I — Failure analysis

| failure | detection point | auto-retry safe? | human review? | rollback |
|---|---|---|---|---|
| invalid starter (unparseable) | `ast.parse` in the dry-run | **yes** — regenerate | no | nothing written |
| starter is variadic again | `declared_signature()` returns no parameters | **yes** — regenerate with a stricter prompt | no | nothing written |
| signature doesn't match cases | contract gate (step 4) binds every case | no | **yes** — either is wrong | pre-image restores the suite |
| cases don't match statement | reference disagreement, or human review of the plan | no | **yes** | plan file, nothing written |
| duplicate cases | `expand_hidden_tests` normalized-identity check | no | **yes** | refused before write |
| weak hidden tests | quality gate Tier-1 < 1.0 | no | **yes** — author more cases | nothing written |
| wrong expected outputs | reference disagreement pre-write; oracle CONFLICT post-write | no | **yes** | pre-image rollback of the suite |
| reference disagreement | `oracle_execute` reconciliation | no | **yes** | no write; artifact blocked |
| Judge0 failure | non-3 status / `GradingUnavailable` | **yes** — transient | no | no evidence recorded |
| quality mutant survives | gate blockers | no | **yes** | nothing written |
| stale candidate | `--expect-digest` under the lock | **yes** — recompute and retry | no | refused |
| concurrent modification | `select_for_update` + digest re-check | **yes** | no | transaction aborts |
| partial batch failure | per-question transaction; ledger | **yes** — resume from ledger | no | only the failed question |
| LLM timeout | generator | **yes** | no | nothing written |
| malformed JSON | schema validation before any write | **yes** | no | nothing written |
| **placeholder removed but content is nonsense** | **NOT automatically detectable** | **no** | **YES — mandatory** | pre-image rollback |

The last row is the important one. Once the marker is gone the safety net is
gone, and no mechanical check distinguishes a good statement from a fluent
irrelevant one. **This is why step 1 must be human-gated**, and why generated
content should be reviewed *before* it is written, not after.

## PHASE J — Five-question pilot (design only; not run)

**Selection.** From the 1,140 safe candidates: exclude q2201; take the topic with
the largest population (Array, 776) for representativeness; require a parseable
starter, exactly one public method, a non-blank title, and no existing evidence
— all 1,140 satisfy this. Pick 5 spanning all three difficulty values
(1000/1300/1600) so the generator is exercised across the range. Record the five
ids in the pilot document *before* generating anything.

**Generated:** a statement and a Python signature for each — as **files**,
reviewed by a human, before any database contact.

**Written:** per question, one `UPDATE (content)` + `RemediationAction`, then one
`UPDATE (boilerplate_code)` + `RemediationAction`. Preceded by 5 pre-images and
one frozen batch.

**Untouched:** hidden tests, contract, trust_state, status, references, oracle,
approvals, submissions, and all 1,136 other candidates.

**Verification per question:** live digest equals the predicted digest; rewinding
the written column reproduces the previous digest exactly; the starter parses,
declares ≥1 parameter, is non-variadic, keeps the original method name and class,
and has exactly one public method; the statement no longer contains
`PLACEHOLDER_MARKER`; the question is **still not servable** (empty suite);
status/trust unchanged; exactly one action per column; pre-image verifies.

**Rollback criteria:** any digest mismatch, any starter that fails to declare a
signature, any statement a reviewer rejects, or any question becoming servable.
Rollback is per question via `preimage_rollback`.

**Success criteria:** 5/5 written, 5/5 verified, 5/5 still invisible to learners,
bank fingerprint changed in exactly the 5 expected rows, regression green.

## PHASE K — Future command interface (no production writes)

```bash
# 1. capture — existing command, existing role
python manage.py preimage_capture --alias preimage --batch reseed-2026-08-A \
    --question <id> [--question <id> …] --operator <you> --apply --confirm

# 2. statement — NEW command, existing role/column
python manage.py reseed_statement --alias remediate --batch reseed-2026-08-A \
    --question <id> --source-file generated/q<id>_statement.html \
    --expect-digest <sha256> --reason "<why>" --operator <you> [--apply --confirm]

# 3. signature — NEW MODE on the existing boilerplate command/role
python manage.py remediate_boilerplate --alias boilerplate --batch reseed-2026-08-A \
    --question <id> --source-file generated/q<id>_starter.py --declare-signature \
    --expect-digest <sha256> --reason "<why>" --operator <you> [--apply --confirm]

# 4. orchestration — dry-run by default, resumable from the ledger
python manage.py reseed_slice --batch reseed-2026-08-A --limit 50 \
    --operator <you> [--apply --confirm]
```

Every one dry-runs by default, takes `--alias`, requires `--expect-digest`, and
records one action per write — the established shape of every P2.7 command.

## PHASE L — Final decision

1. **Architecture:** option **F** — reseed generates a statement and a signature,
   then hands off. It never writes grading truth.
2. **Trust boundary:** reseed's authority ends at `content` and
   `boilerplate_code`. `hidden_test_cases`, `execution_contract_version`,
   `trust_state`, `status`, references, executions, approvals and submissions are
   all outside it. Expected outputs enter only via a reviewed plan through
   `expand_hidden_tests`, and become trusted only through oracle agreement.
3. **Role model:** B-as-C — `learnlm_remediate_rw` for content,
   `learnlm_boilerplate_rw` for the signature, one column each, **no union
   role**. `remediate_boilerplate` gains a guarded signature-declaration mode.
4. **Batch model:** ~50 per frozen `RemediationBatch`, pre-images first,
   one transaction per question, `--expect-digest` re-checked under
   `select_for_update`.
5. **Audit model:** one `RemediationAction` per question per column, carrying
   `post_digest`; plus a ledger recording per-question stage, because the
   selector erases itself on success.
6. **Rollback model:** existing `preimage_rollback`, per question, differing
   fields only, through whichever role wrote.
7. **Pilot:** 5 Array questions spanning all three difficulties, q2201 excluded,
   generated to files and human-reviewed before any write, verified by digest
   rewind, and required to remain **not servable**.
8. **Phases before the first production pilot: five.**
   1. Migration for the new action class(es) + ledger model.
   2. Guarded signature-declaration mode on `remediate_boilerplate`.
   3. `reseed_statement` command (dry-run, handshake, alias, action).
   4. Tests + mutation sweep to 0 real survivors on both writers.
   5. Capture pre-images for 5, freeze the batch, dry-run, review, apply.
9. **Phases before bulk reseed: nine** — the five above, then:
   6. `reseed_slice` orchestration + ledger + resume semantics.
   7. Its own tests + mutation sweep.
   8. A 50-question slice, verified end to end.
   9. Review of generation quality across that slice before scaling to 1,140.

**Scope note.** This gets 1,140 stubs to *legible, bindable drafts*. It does not
make them trusted: steps 3 and 5–11 remain per-question with a human at four
gates, and q1436 took roughly ten phases to traverse them. Deciding how many of
the 1,140 genuinely need to become trusted questions is a separate product
question, and it should be answered before step 6 rather than after.
