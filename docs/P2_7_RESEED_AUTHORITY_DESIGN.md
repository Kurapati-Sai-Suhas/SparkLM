# P2.7h-17 — Reseed write authority: DESIGN ONLY

Nothing in this document has been applied. No role was created, no privilege
granted or revoked, no schema altered, no batch or ledger row created, no
question modified. The SQL in §4 is a proposal awaiting your decision.

The headline is that **the reseed needs one new role, not three.** Two of the
three authorities a reseed requires already exist and are proposed unchanged.

---

## 1. Authority matrix

| stage | role | may write | forbidden | audit class |
|---|---|---|---|---|
| statement generation | `learnlm_remediate_rw` *(exists, unchanged)* | `question.content` | boilerplate, hidden tests, wrapper, contract, status, trust; INSERT/DELETE on question | `STATEMENT_GENERATION` |
| signature declaration | `learnlm_boilerplate_rw` *(exists, unchanged)* | `question.boilerplate_code` | content, hidden tests, wrapper, contract, status, trust; INSERT/DELETE on question | `SIGNATURE_DECLARATION` |
| hidden-test plan | `learnlm_hidden_test_rw` *(exists)* | `question.hidden_test_cases` | content, boilerplate, status, trust | `HIDDEN_TEST_REPAIR` / `SUITE_EXPANSION` |
| contract | `learnlm_contract_rw` *(exists)* | `question.execution_contract_version` | everything else | `CONTRACT_REPAIR` |
| oracle | `learnlm_oracle_rw` *(exists)* | `oracleexecution` INSERT | every question column | — |
| approval | `learnlm_approve_rw` *(exists)* | `questionapproval` INSERT | every question column | — |
| promotion | `learnlm_promote_rw` *(exists)* | `question.trust_state`, approval promotion columns | `status`, content, answers | — |
| publication | `learnlm_status_rw` *(exists)* | `question.status` | `trust_state`, content, answers | `STATUS_TRANSITION` |
| **ledger / coordination** | **`learnlm_reseed_rw` — NEW** | `reseedledger` INSERT, and UPDATE of `stage, last_error, attempts, updated_at` only | **every column of every other table**, incl. all of `groups_question`, the entire trust chain, the audit trail, the pre-image, and learner submissions | **none — it records no action** |

The shape to notice: the coordinator is the *least* privileged participant, not
the most. It can change nothing a learner or the trust chain will ever see.

---

## 2. Statement generation needs no new authority

`learnlm_remediate_rw` already holds exactly `UPDATE (content)` and is already
forbidden boilerplate, hidden tests, wrapper, contract, status and trust. That
is precisely what statement generation requires, so
`STATEMENT_GENERATION_PROBE` and `STATEMENT_GENERATION_FORBIDDEN` are defined
as *identical* to the repair pair, and a test asserts the identity rather than
letting the two drift.

**Answer to the question asked: yes, `learnlm_remediate_rw` can safely perform
content-only statement generation, and no grant needs to change.**

### Distinguishing GENERATION from REPAIR

Same role, same column — so the distinction has to live in the command
precondition, and it is made mutually exclusive so that no question can be
eligible for both:

| | `remediate_statement` (exists) | `reseed_statement` (proposed) |
|---|---|---|
| precondition | `PLACEHOLDER_MARKER` **absent** — a real statement exists and is being corrected | `PLACEHOLDER_MARKER` **present** — no statement exists and one is being authored |
| additionally requires | frozen batch + pre-image | frozen batch + pre-image, **and** the question is a virgin stub: `DRAFT` / `UNVERIFIED`, `hidden_test_cases == []`, no approval, no oracle execution |
| audit class | `STATEMENT_REPAIR` | `STATEMENT_GENERATION` |
| refuses | a question still carrying the marker | a question whose marker is gone |

The two preconditions are complementary predicates on the same column, so
every question is eligible for exactly one of them. That is what makes the
audit trail honest: `STATEMENT_REPAIR` means a human adjudicated a defective
statement, `STATEMENT_GENERATION` means text was authored where none existed,
and a reader disputing what a question asked can tell which happened.

> **Note:** `remediate_statement` does not currently refuse marker-bearing
> content. Adding that refusal is a one-line gate change and is **not made in
> this phase** — it is implementation, and this phase is design. It is listed
> in §10 as part of the next phase's work.

---

## 3. Signature declaration — the unresolved authority

### The fact that decides it

**PostgreSQL grants are per column, not per edit shape.** An annotation repair
and a signature declaration both write `groups_question.boilerplate_code`.
Any role able to do one is, at the database layer, able to do the other. No
arrangement of roles can change that.

So the question is not "which role", it is "where does the safety come from",
and the honest answer is: **from the command's precondition, not from the
grant.** A dedicated role would separate credentials, not capabilities.

### Why annotation-only exists, and why it does not apply here

`remediate_boilerplate` refuses added, removed, renamed or reordered
parameters because a starter's signature is **load-bearing for hidden tests
that already exist** — `execution_adapter` binds every stored case against the
declared signature, so moving the signature under a live suite silently
changes what the answers mean. Its own refusal message already anticipates
this milestone: *a renamed method "is a different action class and needs its
own review."*

For a reseed stub, none of that is true. A candidate has **zero hidden test
cases, no oracle execution, no approval, `DRAFT`/`UNVERIFIED` trust, and a
placeholder statement.** Nothing is bound to `*args, **kwargs`. The blast
radius of replacing it is not "small", it is *empty* — there is no grading
truth to corrupt yet.

That is the crux: the safe distinction is **the question's state, not the
diff's shape.**

### The three options

| | **A. Extend `learnlm_boilerplate_rw`, separately gated** | **B. New `learnlm_signature_rw`** | **C. Recommended — A, gated on stub state and bounded by batch** |
|---|---|---|---|
| **writable columns** | `boilerplate_code` | `boilerplate_code` | `boilerplate_code` |
| **forbidden** | content, hidden tests, wrapper, contract, status, trust; INSERT/DELETE | identical | identical |
| **blast radius** | one column, all questions | one column, all questions — **identical to A** | one column, restricted to questions in a frozen batch whose pre-image shows a variadic starter and an empty suite |
| **audit** | `SIGNATURE_DECLARATION` | `SIGNATURE_DECLARATION` | `SIGNATURE_DECLARATION` |
| **production safety** | rests entirely on the command gate | rests entirely on the command gate | rests on the gate **and** on a precondition that is false for every published question |
| **rollback** | pre-image already captures `boilerplate_code`; existing rollback works unchanged | same | same |
| **separation preserved** | statement author still cannot touch the starter; signature author still cannot touch the statement | same | same, plus the window is bounded in time by batch freeze |
| **cost** | none | an 11th role, an 11th alias, five more `.env` keys | none |

**B buys nothing at the database layer** — the grants are byte-identical to A,
so the blast radius is byte-identical. What it does buy is *credential
separation and revocation granularity*: you could withdraw signature authority
after the reseed without disturbing annotation repair. That is a real
operational property, but it is bought with an extra role in a `.env` that
already carries a duplicate-key defect and one dormant role.

**Recommendation: C.** It is A's privilege model with the safety argument
moved to where it actually holds. The `declare_signature` command refuses
unless *all* of the following are true, checked under a row lock at write
time:

```
content contains PLACEHOLDER_MARKER      hidden_test_cases == []
status == DRAFT                          no QuestionApproval exists
trust_state == UNVERIFIED                no OracleExecution exists
the stored starter's signature is variadic (*args/**kwargs) or absent
the question belongs to a FROZEN batch with a captured pre-image
```

### The precondition, measured against real production rows

Not asserted — evaluated read-only against the live bank:

```
q3309   REFUSED on 7 independent grounds
        no placeholder marker · status=PUBLISHED · trust=ORACLE_VERIFIED
        12 hidden cases · has approval · has oracle execution
        declared params [('haystack','str'), ('needle','str')]

q1436   REFUSED on 7 independent grounds
        no placeholder marker · status=PUBLISHED · trust=ORACLE_VERIFIED
        13 hidden cases · has approval · has oracle execution
        declared params [('paths','list[list[str]]')]

q2201   REFUSED — 4 hidden cases

across the 1141 candidates      eligible 1140 · refused 1
across all PUBLISHED questions  none eligible
```

Two things this settles. Every published question is refused on **seven**
independent grounds, not one — the gate is defence in depth, so a bug in any
single check does not open the door. And q2201, the one candidate that
carries a hidden suite, is excluded **by the precondition itself** rather than
by a hand-maintained exclusion list; the rule that protects published
questions also protects the one anomalous stub, with no special case.

---

## 4. Proposed role and exact SQL — NOT APPLIED

Issued by `neondb_owner`, which owns the tables:

```sql
-- The reseed coordinator. It writes progress and nothing else.
-- NOINHERIT is deliberate: see §9.
CREATE ROLE learnlm_reseed_rw LOGIN PASSWORD '<choose a strong password>'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

GRANT CONNECT ON DATABASE neondb TO learnlm_reseed_rw;
GRANT USAGE  ON SCHEMA public    TO learnlm_reseed_rw;

-- Read-only on everything it coordinates: it must SEE whether a stage is
-- already done, and never DO it.
GRANT SELECT ON groups_question         TO learnlm_reseed_rw;
GRANT SELECT ON groups_remediationbatch TO learnlm_reseed_rw;

-- The one table it writes.
GRANT SELECT, INSERT ON groups_reseedledger TO learnlm_reseed_rw;
GRANT UPDATE (stage, last_error, attempts, updated_at)
    ON groups_reseedledger TO learnlm_reseed_rw;
```

**No sequence grant.** `groups_reseedledger.id` is `GENERATED BY DEFAULT AS
IDENTITY`, so `INSERT` alone supplies it — the same finding that removed a
sequence grant from the approval role. A minimality test proves every line
above is load-bearing: drop any one and the coordinator's work fails.

**No grant on any other table.** Not the audit trail, not the pre-image, not
approvals, references, executions or submissions.

Two column-level details carry more weight than they look:

- `UPDATE` is granted on four columns, **not on `question_id` or `batch_id`**.
  A ledger row therefore cannot be retargeted after creation: the coordinator
  may advance a row's stage forever and can never point a finished record at a
  different question.
- `DELETE` is not granted. Advancing is allowed; *forgetting* is not. A row
  that could be deleted is a record that a question was touched and then
  un-recorded.

Settings would gain a `reseed` alias gated on `RESEED_USER`, exactly like the
other nine — absent env var, absent alias, no fallback.

---

## 5. Ledger ownership — dedicated role, and why

The alternative was to let each stage's role write its own ledger row —
`remediate_rw` marks `STATEMENT_WRITTEN`, `boilerplate_rw` marks
`SIGNATURE_WRITTEN`. That would widen **two** content authorities instead of
adding one powerless authority, and it would put the record of the work under
the same credential as the work. A role that can both change a question and
write the record of having changed it is not audited by that record.

A dedicated `learnlm_reseed_rw` inverts this: the role that writes the ledger
can write *nothing else*, so the ledger provably cannot become an authority —
not by policy, but because its only writer has no power to act on anything it
records.

The Phase 1 invariants are unchanged and re-asserted here: the ledger stores
**no digest, no status, no trust_state, no hidden tests, no expected outputs**,
and no trust or serving module imports it. Every write must still re-read the
question and compare against the operator's `--expect-digest`. The ledger says
which stage to attempt; never what the row contained.

---

## 6. Audit model

| event | recorded as | written by |
|---|---|---|
| statement authored | `RemediationAction(STATEMENT_GENERATION)` with post-image digest | `learnlm_remediate_rw` |
| signature declared | `RemediationAction(SIGNATURE_DECLARATION)` with post-image digest | `learnlm_boilerplate_rw` |
| progress advanced | `ReseedLedger.stage` | `learnlm_reseed_rw` |

The audit trail and the progress record are deliberately different artifacts
with different writers. `RemediationAction` is append-only in the model *and*
in the database (UPDATE/DELETE are forbidden to every role that appends to
it), and carries the digest that makes rollback safe. The ledger carries no
digest at all and is authoritative for nothing.

**The coordinator has no audit authority.** A role able to append actions could
record work it never performed — which is worse than no record, because it
reads as evidence. This is asserted by test and killed as a mutant.

---

## 7. Orchestrator design — not implemented

```
   ┌─ ledger: PENDING
   │
   ▼
  statement    reseed_statement    --alias remediate     STATEMENT_GENERATION
   │                                                     ledger → STATEMENT_WRITTEN
   ▼
  signature    declare_signature   --alias boilerplate   SIGNATURE_DECLARATION
   │                                                     ledger → SIGNATURE_WRITTEN
   ▼                                                     ledger → COMPLETE
  ══════════ end of the reseed's authority ══════════
   │
   ▼
  hidden-test plan   expand_hidden_tests  --alias hiddentest
  contract           remediate_contract   --alias contract
  oracle             oracle_execute       --alias oracle
  approval           question_approve     --alias approve      (human)
  promotion          question_promote     --alias promote
  publication        question_status      --alias status
```

**The ledger covers stages 1–2 only, and that is the whole reason it exists.**
Those two writes erase the placeholder marker, which is the candidate
selector — so after a partial failure the selector can no longer tell a
finished question from a half-done one. Stages 3–8 are already discoverable
from live state and the evidence tables (does a suite exist? an execution? an
approval?), so recording them would duplicate a truth that already has an
authoritative home.

**Resume** re-derives the stage from live state and the action trail, never
from the ledger row. `FAILED` does not auto-advance; a failed question is
retried at whatever stage it actually reached.

### The process-level caveat you should weigh

Role separation is undone if one *process* holds every credential. An
orchestrator that could execute all eight stages would need all eight aliases,
making the process a super-authority even though no single role is one.

Recommended shape: **the orchestrator holds the `reseed` alias plus the two
authoring aliases (`remediate`, `boilerplate`), and stops at `COMPLETE`.**
Stages 3–8 continue to run through their existing separately-gated commands,
driven deliberately — which is already how approval and promotion are designed
to work. The orchestrator automates only the two steps that operate on stubs
carrying no trust.

---

## 8. Tests, mutation, regression

**`groups/test_reseed_authority.py` — 41 tests, all passing.** Every role is
created in the LOCAL test database, exercised over a real second connection,
and dropped. Nothing touches production.

They prove: the proposed grants are sufficient; **every** proposed grant is
necessary (parametrised minimality); the ledger writer cannot write any
question column; cannot retarget a row; cannot delete; cannot fabricate an
audit action; cannot touch approvals, references, executions or pre-images; an
over-granted ledger writer is *refused* rather than trusted (8 variants); the
two content authorities are mutually disjoint; neither can move status or
trust; the reseed role is disjoint from all nine existing roles; and the
ledger still declares no trusted field.

**Mutation: 12 killed / 13, 0 real survivors.**

```
A1  statement and signature authority unioned into one role      killed
A2  statement generation may also write the starter              killed
A3  ledger writer granted hidden-test writes                     killed
A4  ledger writer granted expected-output writes                 killed
A5  ledger writer granted status/trust writes                    killed
A6  ledger writer may append its own audit actions               killed
A7  a ledger row may be retargeted to another question           killed
A8  ledger writer may delete rows                                killed
A9  ledger writer may write question content directly            killed
A10 ledger gains a digest field a writer could trust             killed
A11 ledger gains a trust_state field                             killed
A12 signature declaration allowed through the annotation-only    killed
    command
E1  EQUIVALENT: comment wording                                  survived
```

A12 is worth noting: the existing `remediate_boilerplate` tests already kill
it unaided. The annotation-only refusal is genuinely load-bearing today, which
is why signature declaration must be a separate command rather than a relaxed
flag on that one.

**Regression: 2765 passed** (2724 before this phase, plus the 41 new authority
probes), 2 warnings, 251s. `makemigrations --check`: no changes detected — the
design added constants and tests, no model or schema change.

**Production: unchanged** — 11 roles (no
`learnlm_reseed_rw`), 1 grant on the ledger (census SELECT), 0 ledger rows,
1 batch, 17 actions, 2,926 questions, fingerprint
`3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6`.

---

## 9. The `learnlm_pilot_rw` finding — kept separate, unmodified

Restated from `P2_7_RESEED_MIGRATION_VERIFIED.md` §3, unchanged and untouched
in this phase:

```
learnlm_pilot_rw  MEMBER OF  learnlm_census_ro   (rolinherit = true)
```

It therefore inherits every read privilege census holds, bank-wide, and will
automatically read any future table census is granted — including
`groups_reseedledger`, which it can already read despite holding zero direct
grants on any table.

**Should it be resolved before the pilot? Yes** — but as its own decision, not
as part of reseed authority. Two reasons: a role named for a write workflow
that holds only inherited bank-wide read is a naming/intent mismatch that will
mislead whoever reads the role list next; and the credential is live in `.env`
while no `settings.py` alias uses it.

This is why the proposed `CREATE ROLE` in §4 specifies **`NOINHERIT`** and no
membership of any other role — the new role must not repeat the pattern.

It is deliberately **not** fixed here: the brief forbids grant changes, and
untangling it is a privilege decision of its own.

---

## 10. What is deliberately NOT built yet

- `reseed_statement` and `declare_signature` commands (this phase designed
  their gates; implementing them is the next phase)
- the marker-refusal in `remediate_statement`
- the `reseed` alias in `settings.py`
- the orchestrator
- any role, grant, batch, ledger row or content
