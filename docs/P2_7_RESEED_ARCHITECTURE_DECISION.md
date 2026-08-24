# P2.7 — What bulk reseed may and may not do

Analysis only. No production write, no role, no migration, no reseed.

The decision rests on four facts established by reading the code, not by
preference:

1. **`_servable_questions()` excludes exactly two things** — placeholder content,
   and an empty `hidden_test_cases`. It does **not** filter `status` or
   `trust_state`. Reseed lifts *both* exclusions in a single transaction.
2. **The `source: llm_unverified` tag is read by nothing.** `hidden_tests.py`
   permits `source` as an optional string; no grading, serving, approval or
   trust path consults it. It is decorative.
3. **`EXPECTED_OUTPUT_REPAIR` is an action class with no command.** The
   architecture has deliberately never built a writer for answer keys —
   `oracle_execute` says so explicitly. Reseed writing `expected_output` is a
   back door around the one write this milestone refuses to build.
4. **All 1,141 candidates are stubs, not damaged questions.** Every one has a
   `*args, **kwargs` starter with **zero declared parameters**, contract **v1**,
   an empty suite, and a templated statement. `build_invocation` refuses them:
   `NEEDS_MANUAL_REVIEW — starter takes *args/**kwargs, so no arity is declared`.

Consequence: reseeding a question as the command is written today takes it from
*invisible* to *served to learners, graded against LLM-invented answer keys*.
The trust boundary stops those verdicts teaching the adaptive model; it does
**not** stop them being shown to a learner as Wrong Answer.

---

## STEP 1 — Per-artifact analysis

| # | artifact | reseed writes it? | authority / role today | in question digest | in artifact digest | needs pre-image | safe before oracle |
|---|---|---|---|---|---|---|---|
| 1 | `content` | **yes** | statement / `learnlm_remediate_rw` | yes | yes | yes | **yes** — a wrong statement is a pedagogy defect, not a wrong key |
| 2 | `boilerplate_code` | **yes** | boilerplate / `learnlm_boilerplate_rw` | yes | yes | yes | **yes**, but it decides binding |
| 3 | `hidden_test_cases` | **no — see Step 2** | `learnlm_hidden_test_rw` via `expand_hidden_tests` | yes | yes (per-case digests) | yes | **no** |
| 4 | reference solution | **no** | `learnlm_oracle_rw`, `reference_create/review` | no | yes (id + source hash) | n/a (separate table) | n/a — it *is* the oracle |
| 5 | oracle executions | **no** | `learnlm_oracle_rw`, `oracle_execute` | no | yes (per-case evidence) | n/a | n/a |
| 6 | quality report | **no** | not a DB object — a file artifact | no | yes (rates, blockers, mutant digest) | no | no |
| 7 | approvals | **no** | `learnlm_approve_rw` | no | — | no | no |
| 8 | promotion / `trust_state` | **no** | `learnlm_promote_rw` | yes | — | no | no |
| 9 | publication / `status` | **no** | `learnlm_status_rw` | yes | — | no | no |
| 10 | learner submissions | **never** | no role holds it; nothing should | no | no | no | never |
| 11 | adaptive eligibility | **never** | frozen at submission; no writer exists | no | no | no | never |
| + | `execution_contract_version` | **no** | `learnlm_contract_rw`, `remediate_contract` | yes | yes | yes | yes, but requires binding cases first |

Everything in rows 3–9 requires the full chain — quality gate, oracle, approval,
promotion, publication — and every one of those is a per-question act with a
human at three points. None of it is bulk-able.

**A blocker in row 2:** `remediate_boilerplate` is *annotation-only* by design —
"no renamed class, method or parameter, no reordered parameters, no altered
body". Replacing `*args, **kwargs` with a real signature is none of those
things. **The existing boilerplate authority cannot write these starters.**
Generating a signature is a different authority from annotating one, and it does
not exist yet.

## STEP 2 — Hidden test policy

> *Can a question receive LLM-generated hidden tests before a verified oracle
> exists, without allowing those tests to become grading truth?*

**As the system stands: no.** Not because of the trust model, which is sound,
but because of the serving predicate, which is orthogonal to it.

- **Option A — write them tagged `LLM_UNVERIFIED`** is already implemented and
  provides **zero** protection. Nothing reads the tag. Worse, writing them
  simultaneously satisfies both serving exclusions, so the question goes live
  the same instant.
- **Option B — a staging/draft state** implies new storage and a migration for
  something the repo already has (below).
- **Option C — cases may not enter production until they pass structural
  validation + reference agreement + quality gate + oracle** is circular as
  stated: the oracle executes *against* stored cases, so cases must exist
  somewhere before it can run.
- **Option D — the design the repo already implements.** Proposals live in a
  **reviewed plan file** (`remediation/*.json`); they reach `hidden_test_cases`
  only through `expand_hidden_tests`, which binds every case under the declared
  contract, rejects duplicates, requires a digest handshake, records a
  `SUITE_EXPANSION` action and is scoped to `learnlm_hidden_test_rw`. That is
  exactly the path q1436 took.

**Adopt D**, with one gap that must be closed or consciously accepted:

> Under D, cases are written before the oracle runs — which today makes the
> question **servable**. Either `_servable_questions()` must additionally require
> `status = PUBLISHED`, or you accept that written-but-unverified suites grade
> learners.

That is a product decision, not an engineering one, and it has a large blast
radius: requiring PUBLISHED today would reduce the served bank from ~1,785
questions to **2** (q3309, q1436). I am not making that call.

**What the trust model already guarantees, and what it does not.**
`CaseEvidence.is_oracle_backed` keeps a stored `expected_output` *legacy* until
an execution's output digest matches it, and `build_artifact` blocks approval on
legacy cases. So an LLM's answer can never become ORACLE_VERIFIED without a
human-approved reference independently producing the same output. That is real
protection — for the adaptive model. It is not protection for the learner.

**A second, operational consequence.** If the oracle *disagrees* with an
LLM-written `expected_output`, the case is CONFLICT, approval is blocked, and
there is **no writer able to fix it** — `EXPECTED_OUTPUT_REPAIR` has no command.
Across 1,140 questions, disagreements are certain. Writing LLM answer keys in
bulk therefore manufactures a backlog the system cannot clear.

## STEP 3 — The single-question lifecycle

Derived from the guards, which impose a non-obvious order:

```
PLACEHOLDER STUB
 1. STATEMENT      generate content, human-reviewed        content
 2. SIGNATURE      replace *args/**kwargs with a declared  boilerplate_code
                   signature — NEW AUTHORITY REQUIRED
 3. HIDDEN TESTS   proposals in a reviewed plan file, each hidden_test_cases
                   binding under the declared contract     (SUITE_EXPANSION)
 4. CONTRACT       v1 → v3                                 contract version
 5. REFERENCE      create → review → approve → activate    ReferenceSolution
 6. QUALITY GATE   per-question mutant spec, then run      file artifact
 7. ORACLE         execute; every case ORACLE_BACKED       OracleExecution
 8. REVIEW         read-only, produces the digest          —
 9. APPROVAL       human, digest handshake                 QuestionApproval
10. STATUS         DRAFT → PENDING_REVIEW                  status
11. PROMOTION      → ORACLE_VERIFIED                       trust_state
12. STATUS         PENDING_REVIEW → PUBLISHED              status
```

**Why 3 precedes 4** (the example ordering in the brief has this backwards):
`remediate_contract` refuses a question with no cases — *"the question stores no
test cases, so nothing demonstrates that the contract executes"* — and it binds
every stored case under the target contract before allowing the migration. So
cases must exist first. Equally, cases cannot be authored before step 2, because
a `*args/**kwargs` starter declares no arity to bind against. The dependency is
**signature → cases → contract**, and it is enforced, not advisory.

**Steps 5–12 are exactly the q1436 pipeline** and are unchanged. Steps 1–4 are
what reseed is actually about.

## STEP 4 — Bulk strategy

Only steps 1–2 are bulk-able. Everything from 3 onward carries a human decision.

- **Isolation.** Per-question transaction: one bad question aborts only itself.
  `run_question`-style iteration already does this elsewhere in the repo.
- **Independent commit.** Yes — each question is one `transaction.atomic(using=alias)`
  around its own write plus its own `RemediationAction`.
- **Resumability.** The placeholder marker is a natural idempotency key: once
  `content` is replaced the question leaves the candidate set. That is elegant
  but insufficient — a run that writes content and then fails before the
  signature leaves a half-done question that no longer matches the selector. So
  an explicit **ledger** is required: a per-question row recording which stage
  it reached, keyed by batch.
- **Batch IDs.** One `RemediationBatch` per slice (`reseed-2026-08-A`), frozen
  before any write, exactly as `p27-pilot-1` was. Slices of ~50, never 1,141 in
  one batch — a frozen batch's membership is what rollback restores, and a
  1,141-member batch is an all-or-nothing safety net.
- **Pre-images.** Mandatory, captured by `learnlm_preimage_rw` before the batch
  is frozen. Currently **0 of 1,141** have one.
- **Rollback.** Existing `preimage_rollback` restores only differing fields and
  works per question; it needs the writing role's privileges, so a reseed
  rollback needs whichever role wrote.
- **Partial completion.** Represented by the ledger + the action trail: a
  question with a `STATEMENT` action but no `SIGNATURE` action is mid-flight.
- **Audit identity.** One `RemediationAction` per question per stage, carrying
  `post_digest` — already the established shape.
- **Double-reseed prevention.** Selector + ledger + digest handshake: refuse if
  the question already has an action of that class in any batch.

## STEP 5 — Role architecture

**A — one `learnlm_reseed_rw` holding `UPDATE (content, boilerplate_code, hidden_test_cases)`**

- Tables: `groups_question`; SELECT on batch/pre-image; SELECT+INSERT on action.
- Security: **unions three deliberately separated authorities.** The separation
  exists because statement, starter and keys are different kinds of truth; a
  single role that can rewrite a statement *and* its answer key can make any
  question say anything and mark any answer correct.
- Transactions: one per question, simple.
- Rollback: one role restores everything.
- Auditability: one action class, coarser.

**B — separate writes through the existing owners**

- `learnlm_remediate_rw` writes `content`; `learnlm_boilerplate_rw` writes the
  signature; `learnlm_hidden_test_rw` writes cases via `expand_hidden_tests`.
- Security: preserves the separation exactly. No new authority is created.
- Transactions: **three connections cannot share one transaction.** A question
  can end up with a new statement and an old signature. Mitigated by the ledger
  and by ordering (statement first is harmless on its own).
- Rollback: per column, per role — more steps, same guarantees.
- Complexity: higher operationally; three commands per question.

**Recommendation: B, with one addition.** The separation is the single most
valuable property this milestone bought, and reseed is not a good enough reason
to spend it. The addition is that step 2 (signature generation) needs a new
command under `learnlm_boilerplate_rw` — the *role* is right, the existing
*command*'s annotation-only guard is not. Extending that command with an
explicitly-reviewed "signature declaration" mode is smaller and safer than
creating a union role.

## STEP 6 — Decision

1. **Reseed modifies exactly two columns:** `content` and `boilerplate_code`.
2. **Reseed must never modify:** `hidden_test_cases`, `expected_output`,
   `execution_contract_version`, `trust_state`, `status`, references, oracle
   executions, approvals, submissions, or adaptive eligibility.
3. **Hidden tests before oracle verification:** permitted *into the database*
   only through the reviewed-plan → `expand_hidden_tests` path (option D), and
   never by reseed. Whether such a question may be *served* before publication
   is an open product decision (see Step 2).
4. **Single-question lifecycle:** the 12 steps above, with the enforced
   dependency signature → cases → contract.
5. **Bulk lifecycle:** steps 1–2 only, in frozen batches of ~50, per-question
   transactions, pre-images first, ledger-tracked, resumable.
6. **Role architecture:** B — existing owners, no union role; extend the
   boilerplate command rather than widening any grant.
7. **Production writes eventually required:** per question, one `UPDATE
   (content)` + one `RemediationAction`; one `UPDATE (boilerplate_code)` + one
   `RemediationAction`; preceded by one `QuestionPreImage` INSERT and one
   `RemediationBatch` INSERT per slice.
8. **Dry-run requirements:** projected before/after digest per question; the
   proposed statement and signature in full; AST proof that the new signature
   declares ≥1 parameter and binds; confirmation the question is in the frozen
   batch with a verified pre-image; refusal if any action of that class already
   exists; and a per-slice summary that writes nothing.
9. **Tests required:** per-question isolation (one failure aborts only itself);
   resumability after mid-batch failure; double-reseed refusal; digest handshake;
   role privilege tests over a real connection (sufficiency + minimality);
   AST guard that reseed cannot touch `hidden_test_cases`; proof the artifact
   digest is unaffected for already-approved questions. Mutation targets: drop
   the handshake, drop the ledger check, widen `update_fields`, route through
   `default`, skip the pre-image, let a failure abort the batch, allow a
   signature with no parameters.
10. **Phases remaining before a 5-question production slice: six.**
    1. Decide the serving-gate question (Step 2) — yours, blocking.
    2. Design + review the signature-generation mode for the boilerplate command.
    3. Migration for the new action class(es) + ledger model.
    4. Build reseed as content-only and signature-only commands with `--alias`,
       handshake, dry-run.
    5. Tests + mutation sweep to 0 real survivors.
    6. Capture pre-images for the slice, freeze a batch, dry-run, then apply 5.

**The honest scale note.** q1436 consumed roughly ten phases and several human
adjudications to reach PUBLISHED. Steps 3–12 are not compressible by tooling;
they are compressible only by deciding that most of the 1,141 do not need to
become trusted questions. Bulk reseed should be understood as *making 1,141
stubs into legible, bindable draft questions* — not as manufacturing 1,141
verified ones.
