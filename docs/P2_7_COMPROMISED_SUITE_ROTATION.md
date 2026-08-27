# P2.7 — Compromised answer keys: blast radius, rotation design, history plan

**Phase 20.6 · M2 P2.7h-34 · design only, nothing rotated**

No suite was rotated. No oracle ran. No approval, promotion or publication
changed. Migration `0050` is generated and **not applied**.

---

## 1. What is compromised, and how badly

Hidden-test inputs, expected outputs and two complete reference solutions were
committed to a **public** repository. Phase 20.5 untracked twelve files;
this phase found two more that the first scan missed because they are not
JSON, and redacted them.

| Question | State | Exposure | Severity |
|---|---|---|---|
| **q3309** | PUBLISHED · ORACLE_VERIFIED · adaptive-eligible | 12-case suite, plus cases quoted in a doc | **live** |
| **q1436** | PUBLISHED · ORACLE_VERIFIED · adaptive-eligible | 13-case suite, **complete reference solution**, plus cases quoted in a doc | **live** |
| q17, q266, q963, q1779, q2 | DRAFT | cases and one reference solution | not served |
| q1940, q1974, q2057, q2290 | DRAFT | 59-case pilot suites | not served |

### The one fact that lowers the temperature

**Both live questions have zero learner submissions.** No score, Elo update or
mastery estimate anywhere in the system rests on a run against an exposed
suite. The exposure is real but, so far, **unrealised** — and both questions
remain adaptive-eligible, so they can be served at any moment.

---

## 2. Blast radius of rotating a suite (20.6A / 20.6C)

Measured from production, read-only:

```
q3309   12 cases   24 oracle executions   approval #1 (2026-08-19)
        suite digest      a6d97e733f521ae1…
        artifact digest   b1df39f5d0aa7308…
        reference #2 APPROVED active canonical  hash 18ad8f390642c315
        submissions 0

q1436   13 cases   26 oracle executions   approval #2 (2026-08-23)
        suite digest      28d35453ba7fca94…
        artifact digest   e4d4362e95c88359…
        reference #3 APPROVED active canonical  hash 2e0551949f7ca6ba
        submissions 0
```

Twenty-four and twenty-six executions is two per case — `REQUIRED_RUNS = 2`,
the agreeing-runs rule. Every case currently has evidence; none is orphaned.

**What goes stale the moment the suite changes:**

| Artifact | Effect | Why |
|---|---|---|
| suite digest | changes | that is the *point* — it invalidates the published key |
| every `case_digest` | changes | identity is derived from the input |
| all 50 oracle executions | **cover nothing** | executions are scoped by `case_digest` |
| artifact digest | changes | it frames `case_count` and every per-case digest |
| `QuestionApproval` | **stale** | it binds `artifact_digest` |
| `trust_state` | **unsupported** | ORACLE_VERIFIED would rest on evidence for a suite no longer served |
| `is_adaptive_eligible` | still True | PUBLISHED ∧ ORACLE_VERIFIED — it would not notice |
| learner submissions | untouched | they record what a learner did against the old suite and **must not be altered** |

**The system fails closed, which is the good news.** `question_promote`
compares the freshly recomputed artifact digest against the approval's and
refuses on mismatch, and `question_approve` recomputes rather than trusting a
supplied digest. A stale approval therefore cannot be *used* — it can only
leave the question stuck.

---

## 3. What the lifecycle already supports, and what is missing (20.6B)

| Capability | Exists? | Command |
|---|---|---|
| suite expansion (append) | **yes** | `expand_hidden_tests` — appends only; never removes, reorders or edits a case |
| expected-output repair | **yes** | `remediate_hidden_tests` — rewrites answers, holds every `stdin` fixed |
| input repair | **yes** | `remediate_inputs` — rewrites named `stdin`s, holds every answer fixed |
| withdraw from service | **yes** | `question_status` — `PUBLISHED → PENDING_REVIEW` is a legal transition |
| re-run the oracle | **yes** | `oracle_execute` |
| quality gate | **yes** | `quality_gate` |
| re-approve | **yes** | `question_approve` (recomputes the digest) |
| **suite replacement / case removal** | **NO** | nothing can remove a case |
| **trust demotion** | **NO** | see below |
| **approval supersession** | **NO** | a stale approval is unusable but never revoked |

### The blocking gap: `ORACLE_VERIFIED` is one-way

`question_promote` requires `trust_state == UNVERIFIED` and refuses otherwise —
it promotes or it declines. `question_status` states plainly that it "cannot
write `trust_state`". **Nothing demotes.**

This blocks rotation twice over. After replacing a suite the question would
still claim ORACLE_VERIFIED on evidence that no longer covers it, *and*
`question_promote` would refuse to re-establish trust because the question is
not UNVERIFIED. The question would be stuck: withdrawn from publication but
permanently claiming verified trust.

### Minimum missing machinery

1. **`question_demote`** — `ORACLE_VERIFIED → UNVERIFIED`, under
   `learnlm_promote_rw` (the role that already owns `trust_state`), recording
   an append-only action. This is the smallest and most important piece: it
   makes trust *revocable*, which rotation requires and which the model
   currently treats as a one-way door.
2. **Atomic suite rotation.** Today a rotation needs `remediate_inputs` then
   `remediate_hidden_tests` — two commands, two roles, and a window in between
   where inputs and answers disagree. **Withdrawing the question first closes
   that window**, so the existing pair is sufficient *if* step 1 of the
   sequence below is mandatory. A dedicated `rotate_suite` would be cleaner
   but is not required.
3. **Approval supersession** — optional. The digest check already fails
   closed; a `superseded_at` field would only make the intent visible.

---

## 4. Proposed rotation sequence — **not executed**

Order matters, and the first step is the one that makes the rest safe.

1. **`question_status` q → `PENDING_REVIEW`.** `is_adaptive_eligible` becomes
   False immediately and the question stops being served. Every later step is
   then offline, and the inconsistent window in step 3 cannot reach a learner.
2. **`preimage_capture`** a fresh batch over q3309 and q1436, then freeze —
   the rollback anchor.
3. **Rotate the cases.** `remediate_inputs` for the new inputs, then
   `remediate_hidden_tests` for the matching answers. Every input must change,
   or the exposed key still works. Preserve the *behaviour* being tested:
   category coverage and mutant kills must survive, so design the replacement
   from the same input contract rather than by perturbing values.
4. **`quality_gate`** — Tier-1 100%, Tier-2 ≥ 80%, no duplicates, no missing
   categories. A rotated suite that no longer kills the mutants is a weaker
   question, not a safer one.
5. **`question_demote`** *(does not exist yet — step 1 of the missing
   machinery)* → `UNVERIFIED`.
6. **`oracle_execute`** — fresh evidence, two agreeing runs per new case.
7. **`question_approve`** — recomputes the artifact digest over the new suite.
8. **`question_promote`** → ORACLE_VERIFIED.
9. **`question_status`** → `PUBLISHED`.

**Rollback path:** steps 2–4 are reversible from the pre-image captured in
step 2. Step 6 is the point of no return — an oracle execution is append-only
and is superseded, never deleted. If a rotation must be abandoned after step
6, the question stays at PENDING_REVIEW/UNVERIFIED, which is safe: withdrawn
rather than wrongly trusted.

**Do not simply delete the exposed cases.** A 12-case suite cut to 6 falls
below `MIN_HIDDEN_TESTS = 12` and loses category coverage; the question would
be weaker *and* still fail the gate. Replacement, not deletion.

---

## 5. Reference provenance (20.6D) — designed, migration generated

Phase 20.5 could not persist four LLM-written reference candidates honestly:
`ReferenceSolution` had nowhere to record who wrote them. `ReferenceCandidate`
refuses an unattributed LLM reference in memory, but that refusal could not
survive being saved.

Added to `ReferenceSolution`:

```
origin                human | llm | trusted_source | unrecorded
provider              e.g. "gemini-2.5-flash"
model_name            the exact model identifier
prompt_version        the prompt template version
specification_digest  the operator-verified spec it was written from
```

Two decisions worth stating:

- **`origin` defaults to `unrecorded`, never `human`.** Back-filling three
  legacy rows as human-authored would manufacture provenance — precisely the
  failure these fields exist to prevent.
- **A CheckConstraint enforces that `origin = llm` requires both `provider`
  and `prompt_version`.** In the database, not in `save()`, because
  `QuerySet.update()` and `bulk_create` bypass `save()` — the same reasoning
  that put the approval-provenance constraint there.

`0050_reference_provenance` is **generated and not applied**. Unlike `0049`
this is **real DDL**: five `ADD COLUMN`s and one constraint. It needs the
owner credential and your approval. Eight tests cover it; the suite was run
against a rebuilt database, so the migration is known to apply cleanly.

---

## 6. History remediation — **plan only, not executed**

`GRADING TRUTH IN HISTORY = YES`. Every removed and redacted file remains in
already-pushed commits (`6ddf898`, `3ff6830` and others) and is reachable by
`git show <commit>:<path>`, by anyone who cloned, and possibly through
GitHub's cached views. **Removing files from HEAD does not remove them from
the repository.**

Recommended order, for your approval:

1. **Rotate q3309 and q1436 first (§4).** This is the only step that works
   even if a copy already escaped. History rewriting cannot un-publish
   something already read; rotation makes what was read worthless. **Do this
   even if you never do steps 3–5.**
2. Back up: confirm `backup/pre-push-p2.7-2026-08-24` and take a fresh clone.
3. `git filter-repo --invert-paths` over the affected paths, across all refs.
4. Force-push every branch and tag — **needs your explicit authorisation**;
   it rewrites shared history and every clone must be re-cloned.
5. Ask GitHub Support to purge cached views; delete forks if any exist.
6. Verify: `git log --all -- <path>` returns empty for each path.

Consider also whether the repository needs to be public at all. Private would
close this class of exposure permanently and costs nothing but visibility.

---

## 7. Production integrity

```
questions 2926 · references 3 · executions 70 · approvals 2 · submissions 44
ledger 0 · pre-images 11 · batches 2 · candidates 1136
q3309 PUBLISHED/ORACLE_VERIFIED   q1436 PUBLISHED/ORACLE_VERIFIED
pilot four DRAFT/UNVERIFIED
bank fingerprint 43059aaa…  UNCHANGED
PRODUCTION WRITES = 0
```

Regression: **2,480 passed** against a rebuilt database.
