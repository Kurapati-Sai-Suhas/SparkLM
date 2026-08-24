# P2.7h-18 — Reseed authoring path implemented (stages 1–2)

Implements the Phase 3 design under decision **Option C**: signature
declaration reuses `learnlm_boilerplate_rw`, gated by a separate command with
a state precondition. No production question, batch or ledger row was written.

---

## A. `reseed_statement` — STATEMENT_GENERATION

Writes `Question.content` and nothing else, under `learnlm_remediate_rw`
(unchanged, no new grant). Records exactly one
`RemediationAction(STATEMENT_GENERATION)` carrying the resulting digest.

Refuses unless **all** hold: live placeholder marker present; `DRAFT`;
`UNVERIFIED`; `hidden_test_cases == []`; no `QuestionApproval`; no
`OracleExecution`; frozen batch; verified pre-image; `--expect-digest`
matches. `--expect-digest` is **required**, not optional as it is on the
repair commands — an orchestrated write is unattended and must prove it is
acting on the state it was planned against.

Three independent things keep the write to one column: `update_fields=["content"]`,
a before/after comparison of every other captured field inside the
transaction, and a role holding column-level UPDATE on `content` alone.

It also refuses an authored statement that still contains the marker — that
would leave the question a candidate for its own reseed forever.

### The back door is shut

`remediate_statement` now refuses a question that still carries the
placeholder marker, pointing the operator at `reseed_statement`. The two
preconditions are exact complements, so **every question is eligible for
exactly one of them** and neither can be used as the other's back door. This
is what keeps the audit trail honest: `STATEMENT_REPAIR` means a human
adjudicated a defective statement; `STATEMENT_GENERATION` means text was
authored where none existed.

---

## B/C. `declare_signature` — SIGNATURE_DECLARATION

A new command, entirely separate from `remediate_boilerplate`, which is
**unchanged and unweakened**. Writes `boilerplate_code` and nothing else,
under `learnlm_boilerplate_rw`. Records one
`RemediationAction(SIGNATURE_DECLARATION)`.

Refuses unless: `DRAFT`; `UNVERIFIED`; empty suite; no approval; no oracle
execution; frozen batch; verified pre-image; digest matches; the current
starter is variadic or declares nothing; **and the question was a placeholder
stub when the batch was frozen**.

### One design correction, found by the tests

The brief specified `PLACEHOLDER_MARKER present` for signature declaration
and the pipeline order `statement → signature`. Those two are in direct
tension: statement generation *removes* the marker, so a live-marker check
would make signature declaration impossible on exactly the questions it
exists for. The second stage could never run after the first.

The question that actually needs answering is not "is this a stub now" but
**"was this a stub when the batch was frozen"** — and the pre-image is the
frozen, verified, immutable record of precisely that. Eligibility is anchored
there instead.

This is strictly stronger than the live check, not a relaxation: it binds the
authority to one frozen batch, so a question that was already a real question
when the slice opened can never acquire a declared signature through this
command, whatever happens to its statement in between. A mutation confirms it
is load-bearing (B8).

### Signature validation

Before any write, the proposed starter must: parse and compile; keep the class
name and method name unchanged; expose exactly one public method; declare at
least one named parameter; carry no `*args`, `**kwargs` or keyword-only
parameters; have no duplicate parameter names; annotate every parameter with a
type the adapter can classify into a coercion kind.

**One check beyond the brief:** the method body must remain a stub
(`pass`/`...`/docstring). A starter carrying logic is how a reference solution
reaches the learner who was asked to write it. Flagged here because it was not
requested.

`binding_blockers(source, cases)` is implemented and tested but **not called
during authoring** — no cases exist yet. It is the handshake the hidden-test
phase runs once a suite exists, defined alongside the declaration so both
halves of the contract live in one place. **No hidden test or expected output
is created anywhere in this path.**

---

## D. Audit

Exactly one append-only action per successful write, carrying the post-image
digest; the pre-image digest is on the linked pre-image row. The orchestrator
never calls `record_action` and cannot: the ledger role holds no INSERT on
`groups_remediationaction`, verified against the live production role.

---

## E/F. Ledger and orchestrator

`PENDING → STATEMENT_WRITTEN → SIGNATURE_WRITTEN → COMPLETE`, with `FAILED`
recording an error and advancing nothing (`FAILED` has no entry in `ADVANCES`;
mutation B20 confirms).

The orchestrator **re-derives the true stage from live question state and the
audit trail**, never from the ledger. `derive_stage` also returns
*discrepancies*: if the placeholder is gone with no `STATEMENT_GENERATION`
recorded, or an action is recorded but the write is not present, the question
is **refused rather than advanced** — something else edited it.

Two deliberate incapacities:

- **It authors nothing.** Statements and starters are read from
  `--content-dir` as `<id>.statement.html` / `<id>.starter.py`. A coordinator
  that could also invent content would be the single component able to decide
  both what a question asks and that it was asked correctly.
- **It writes no question.** Its own alias is the ledger role. Each stage runs
  under the alias of the command that owns that column, and `--apply` refuses
  without `--statement-alias` and `--signature-alias`.

It stops at `COMPLETE`. A test asserts it never calls `expand_hidden_tests`,
`remediate_contract`, `oracle_execute`, `question_approve`, `question_promote`
or `question_status`. Slices are capped at `MAX_SLICE = 50`, default 5.

---

## G. Dry run

Default mode. Reports per candidate: id, current digest, pre-image digest or
`MISSING`, derived stage vs what the ledger claims, projected next stage,
intended statement action, intended signature action, whether each artefact
file is present, and — for a refused question — every blocker and every
discrepancy, individually. **Zero writes, not even a ledger row**, asserted by
test.

---

## H/I/J. Tests, mutation, regression

**`groups/test_reseed_authoring.py` — 68 tests, all passing.** All 23 required
areas are covered, plus signature-validation cases and the orchestrator.

**Mutation: 20 killed / 21, 0 real survivors.**

```
B1  placeholder requirement removed              killed
B2  DRAFT requirement removed                    killed
B3  UNVERIFIED requirement removed               killed
B4  empty-suite requirement removed              killed
B5  no-approval requirement removed              killed
B6  no-oracle requirement removed                killed
B7  non-variadic starter accepted                killed
B8  pre-image no longer anchors eligibility      killed
B9  pre-image requirement removed                killed
B10 digest handshake removed                     killed
B11 SELECT FOR UPDATE bypassed                   killed
B12 frozen-batch requirement removed             killed
B13 repair command is a back door again          killed
B14 signature allowed through annotation-only    killed
B15 ledger writer granted hidden-test writes     killed
B16 ledger writer granted expected-output writes killed
B17 ledger writer granted status/trust writes    killed
B18 ledger writer may fabricate audit actions    killed
B19 ledger row may be retargeted                 killed
B20 FAILED advances automatically                killed
E1  EQUIVALENT: docstring wording                survived
```

**Regression: 2833 passed**, 2 warnings, 267s.

### Requirement 23, against the real production population

Read-only, using the shipped eligibility module:

```
candidates 1141   eligible 1140   refused 1
  refused: ['has hidden tests']  x1        (the q2201-shaped candidate)
```

Eligibility turns on the documented conditions and nothing else. The single
refusal is excluded **by the rule itself**, with no hand-maintained exclusion
list.

---

## K. Production

Nothing was written. **One thing changed that I did not do**, and it is
recorded here rather than assumed:

**`learnlm_reseed_rw` now exists on production.** Phase 3's verification found
11 `learnlm_*` roles and explicitly recorded `learnlm_reseed_rw present:
False`; there are now 12. I hold no DDL credential and created nothing — the
Phase 3 SQL was applied by someone with owner rights, consistent with decision 6.

Audited read-only, it matches the Phase 3 proposal exactly:

```
LOGIN · NOINHERIT · no superuser/createdb/createrole · member of nothing
groups_reseedledger      SELECT yes · INSERT yes · DELETE no
  UPDATE stage/last_error/attempts/updated_at   yes
  UPDATE question_id/batch_id                    no
groups_question          SELECT only — no column writable
groups_remediationbatch  SELECT
groups_remediationaction INSERT no
groups_questionpreimage  INSERT no
groups_questionapproval  INSERT no
```

A note on method: `information_schema.role_table_grants` reports **NONE** for
this role, because the reading role cannot see grants made to another. The
`has_table_privilege` / `has_column_privilege` probes are the reliable
instrument — the same finding that shaped every earlier role check in this
milestone.

Everything else is untouched:

```
ledger rows 0 · batches 1 (p27-pilot-1) · actions 17 · grants on ledger 1 (census SELECT)
bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  unchanged
```

---

## Not built, deliberately

The `reseed` alias in `settings.py` (gated on `RESEED_USER`), content
generation of any kind, and stages 3–8. No pilot has been run.
