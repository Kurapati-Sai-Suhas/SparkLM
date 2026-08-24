# P2.7 — The status lifecycle is a vocabulary, not a graph

**Stopping before code, per Step 5 of the brief.** The repository defines four
status values, one constraint between status and trust, and one consumer that
reads `PUBLISHED`. It defines **no transitions, no writer, and no rule about
when a question may be published**. Choosing a target status now would be
inventing the lifecycle, not implementing it.

The specific trap the brief named is real and worth stating plainly: `PUBLISHED`
is the value that makes `question_promote` succeed, and it is *not* obviously
the right value to pick — see §B.

---

## A. The actual status lifecycle

**Vocabulary** (`groups/models.py:364-373`, migration `0038`):

```
DRAFT · PENDING_REVIEW · PUBLISHED · BLOCKED     default DRAFT
```

**Every consumer in the repository:**

| where | what it does with status |
|---|---|
| `models.py:397` `is_adaptive_eligible` | `PUBLISHED and ORACLE_VERIFIED` — the only place `PUBLISHED` means anything |
| `census.py:387` | classifies a `BLOCKED` question and stops analysing it |
| `oracle_pipeline.py:196` | adds "question is BLOCKED" to the blocker list |
| `census.py:115` | counts `DRAFT` rows for the contradiction report |
| `question_promote.py` | refuses `DRAFT`; reports whether promotion would make the question eligible |
| DB CHECK `question_draft_cannot_be_oracle_verified` | forbids `DRAFT` + `ORACLE_VERIFIED` |

**Every writer in the repository:** none. `Question.status` is assigned nowhere
outside test fixtures. There is no transition method on the model, no state
machine, no management command, no view, no serializer field, and no migration
that moves an existing row.

**`PENDING_REVIEW` is referenced nowhere at all** except its own choice tuple
and one parametrised test case asserting it is *not* adaptive-eligible. Nothing
produces it, nothing consumes it.

**The graph the repository implies:**

```
   DRAFT ──?──> PENDING_REVIEW ──?──> PUBLISHED
     │                                    │
     └──────────────?─────────────────────┘        BLOCKED ──?──> (nothing)

   nodes: defined      edges: none defined      writer: none
```

The only edge constraint that exists at all is negative: whatever moves a
question to `ORACLE_VERIFIED` cannot leave it `DRAFT`.

**Live state:** all **2,926** questions are `DRAFT` / `UNVERIFIED`. Nothing has
ever been published.

## B. What publication currently does — and does not — do

This is the part that makes the decision a product decision rather than a
mechanical one.

**Status does not gate delivery.** `coding_views.servable_questions` excludes
placeholder content and questions with no hidden tests. It does **not** filter
on `status`. So today, DRAFT questions are already served to learners, graded,
and shown verdicts. That is deliberate and documented in
`test_trust_boundary.py`: *a possibly-wrong verdict may be shown, but must never
teach the model.*

So `PUBLISHED` has exactly one effect in this codebase: **combined with
`ORACLE_VERIFIED`, it makes submissions teach the adaptive model.**

Which means the ordering question is not cosmetic:

- **Publish first, then promote** (`DRAFT → PUBLISHED`, then trust): the question
  becomes adaptive-eligible **at the moment promotion lands**. Eligibility flips
  as a side effect of the trust write, and `question_promote` — which is
  carefully written *not* to publish — would in practice be the command that
  turns the question on.
- **Move to PENDING_REVIEW, promote, then publish** (`DRAFT → PENDING_REVIEW`,
  trust, `PENDING_REVIEW → PUBLISHED`): this satisfies the CHECK constraint
  equally well, promotion changes nothing observable, and **publication is the
  single deliberate act** that starts the question teaching. It also gives
  `PENDING_REVIEW` the meaning its name implies for the first time.

Both are legal under every constraint the repository has. The second is the one
I would choose, because it keeps "this question now shapes learner models" as an
explicit act by a human rather than a consequence of a different act. But the
repository does not decide it, so I have not.

## C. What must be true before a status change — derived, not invented

Nothing in the repository states a prerequisite for publication. What the
repository *does* establish, and what therefore constrains any answer:

1. **The CHECK forbids `DRAFT` + `ORACLE_VERIFIED`.** Status must leave DRAFT
   before promotion — but `PENDING_REVIEW` satisfies this as well as
   `PUBLISHED`.
2. **`PUBLISHED` + `UNVERIFIED` is an explicitly legitimate state** — the
   documented "legacy question" (practise on it, see a verdict, teach nothing).
   So publication does **not** require oracle evidence, an approval, or a
   passing quality gate. Requiring them would be a new rule, not an existing
   one.
3. **`BLOCKED` is a real signal** produced by the census and the oracle
   pipeline, and nothing acts on it. Any status writer should presumably refuse
   to publish a BLOCKED question — but again, that rule does not exist yet.

The brief asked whether publication happens before or after promotion. The
answer from the code is: **before, necessarily** (the CHECK forces status out of
DRAFT first) — but it does not tell us *to what*, and that is the whole
question.

## D. The role, which the decision does not change

Whatever transition is chosen, the writer's privileges are the same and are
already implied by the existing pattern:

```sql
GRANT SELECT ON groups_question                TO learnlm_status_rw;
GRANT UPDATE (status) ON groups_question       TO learnlm_status_rw;
```

Column-scoped, disjoint from `learnlm_promote_rw` (which is explicitly denied
`UPDATE (status)` — P2.7h-7), no `trust_state`, no grading-truth column, no
INSERT on `groups_questionapproval`, nothing on the reference or the executions.
What the *command* enforces on top of that — which source states, which target
states, which prerequisites — is exactly what the decision determines, so I have
not written it.

## E–I. Not applicable this phase

No command, tests, mutation sweep, or dry-run were produced: the transition they
would encode is the thing that is undefined. Nothing was written to production;
q3309 remains

```
DRAFT / UNVERIFIED   adaptive_eligible false
digest ebb26e7f…     QuestionApproval 1 (unstamped)   OracleExecution 24
bank fingerprint 9cc3c8d8b0d050d0cebb6a43a44adbb68612d2e5077129c915481c1b66715acf
```

## J. The decision required

1. **Target status for q3309 now** — `PENDING_REVIEW` (promotion becomes
   invisible; publication is a later, separate act) or `PUBLISHED` (promotion
   immediately makes the question teach the model).
2. **Prerequisites the status command must enforce** — the repository requires
   none, so these must be stated: does publishing require an approval? oracle
   evidence? a passing quality gate? `ORACLE_VERIFIED`? Or is
   `PUBLISHED + UNVERIFIED` (the documented legacy state) something an operator
   may still deliberately create?
3. **Whether `BLOCKED` blocks publication**, and whether `BLOCKED` gets a writer
   at all in this phase.
4. **Separately, and larger:** `status` does not gate delivery today. If
   `PUBLISHED` is meant to mean "learners may see this", that is a change to
   `servable_questions` affecting all 2,926 currently-served DRAFT questions —
   a much bigger decision than q3309, and one I have not touched.
