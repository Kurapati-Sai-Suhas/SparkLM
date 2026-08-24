# P2.7 — Migration 0049 and pilot readiness

**Phase 13 · M2 P2.7h-28 · schema change and orchestration decisions**

Status: migration 0049 is **verified and BLOCKED on a credential**, not on
doubt. Everything provable without the owner role is proven below; the apply
step needs a role this environment does not hold.

---

## 1. Migration 0049 — inspected and proven a no-op (13A)

```python
dependencies = [('groups', '0048_reseed_action_classes_and_ledger')]

operations = [
    AlterField('remediationaction', 'action_class',  # + CONTRACT_DECLARATION
               CharField(choices=[...], max_length=32)),
    AlterField('reseedledger', 'stage',              # + CONTRACT_SET
               CharField(choices=[...], max_length=24, default='PENDING')),
]
```

Two `AlterField`s and nothing else. No `RunSQL`, no `RunPython`, no
`AddField`, no `RemoveField`, no constraint or index operation.

### The proof it changes nothing physically

| check | 0048 | 0049 | production now |
|---|---|---|---|
| `action_class` width | `max_length=32` | `max_length=32` | `varchar(32)` |
| `stage` width | `max_length=24` | `max_length=24` | `varchar(24)` |
| `stage` default | `'PENDING'` | `'PENDING'` | *(Django-level)* |

A `max_length` change **would** be real DDL — `ALTER TYPE varchar(n)` — and
would end the no-op claim. Both widths are identical.

`sqlmigrate` against the production schema:

```sql
BEGIN;
--
-- Alter field action_class on remediationaction
--
-- (no-op)
--
-- Alter field stage on reseedledger
--
-- (no-op)
COMMIT;
```

And the reason it is a no-op is structural, not incidental: **PostgreSQL has
no CHECK constraint on either column.** The only check constraint on those two
tables is `groups_reseedledger_attempts_check (attempts >= 0)`. Choices are
modelled in Django and nowhere else, so adding one changes zero schema and zero
rows.

`migrate --plan` confirms **0049 is the only pending migration across all
apps**, so applying it cannot drag anything else along.

---

## 2. Why it is not applied — a credential, not a doubt

```
role                       INSERT on django_migrations
learnlm_census_ro          False   ← the `default` alias
learnlm_remediate_rw       False
learnlm_preimage_rw        False
learnlm_contract_rw        False
learnlm_boilerplate_rw     False
learnlm_hidden_test_rw     False
learnlm_oracle_rw          False
learnlm_approve_rw         False
learnlm_promote_rw         False
learnlm_status_rw          False
```

**No configured alias can record a migration.** Every role is a narrow
column-scoped grant and `default` is read-only. This is the least-privilege
design working exactly as intended — the same wall Phase 2 hit with 0047/0048,
which you applied with the owner credential.

### The command to run

With the **owner** credential (the one used for 0047/0048), from
`backend/LearnLM`:

```bash
python manage.py migrate groups 0049
```

Because the migration emits no DDL, `--fake` would produce a byte-identical
result. **Use the real command anyway** — the outcome is the same, and not
forming the `--fake` habit is worth more than the two seconds it saves.

Afterwards, re-run the verification in §7. I can run it the moment you say the
apply is done.

---

## 3. CONTRACT_SET semantics — recommendation: **A** (13C)

> **A. `CONTRACT_SET` remains terminal for stages 1–3.**

`CONTRACT_SET → COMPLETE` is **not** added, and the evidence says it must not
be. A question at `CONTRACT_SET` has:

- no hidden suite · no oracle execution · no approval
- `status = DRAFT` · `trust_state = UNVERIFIED`
- `is_adaptive_eligible = False`

Nothing about it is finished. `COMPLETE` would be a claim no other table
supports, and the ledger would be the single component asserting a readiness
the question does not have.

**Option B** — a further explicit suite-authoring stage — is the right *eventual*
shape but cannot be added now: suite authoring is not a reseed write, does not
run under a reseed role, and does not exist. Adding the stage before the writer
would create a slot only a future command can fill, and an enum value nothing
can reach is indistinguishable from a bug.

**Option C** was considered and rejected for the same reason: any richer
lifecycle representation has to model stages that have no implementation yet.

The honest position is that reseed's own responsibility ends at
`CONTRACT_SET`. When suite authoring is built, it brings its own stage with it
— and `ADVANCES` gains an entry at the same moment the writer does, not before.

**A terminal invented for orchestrator convenience is exactly what this
avoids.**

---

## 4. Orchestrator design — recommendation: **C** (13D)

> **C. `reseed_orchestrate` remains coordinator-only; contract selection stays
> a deliberate, separately-invoked step.**

| criterion | B: drive it as stage 3 | **C: keep it deliberate** |
|---|---|---|
| privilege separation | orchestrator gains a third alias to hold | unchanged — two aliases, both already audited |
| auditability | contract choices arrive in slices of 50 | one operator, one question, one reason |
| accidental selection | a `--limit 50` typo migrates 50 contracts | impossible; each needs its own invocation |
| recovery | must resume mid-slice across three writers | each write is independently reversible |
| human review | the v3 decision is never seen by a person | operator reads the dry-run before applying |
| batch scale | fast, and that is the problem | slower, and the slowness is a feature |
| resume | ledger already models it | ledger already models it |

The deciding argument is **accidental contract selection risk** weighed against
what the automation actually saves.

Statement generation and signature declaration are *transcription* — content
already authored offline is copied into a column, and getting one wrong
produces a visibly wrong question. Contract selection is a *judgement* whose
error mode is silent: a wrongly-chosen contract produces a question that looks
completely normal and grades every submission against arguments the author
never intended. q1974 is exactly that failure, and it took an execution to
find.

The Phase 11 census says roughly **276 of 1,140** candidates will need v3 —
about a quarter. That is a minority decision applied to a majority population,
which is precisely the shape where bulk automation is least defensible: 864
invocations would be uneventful and the 276 that matter would scroll past in
the same output.

Automating it saves an operator perhaps two minutes per slice. It costs the
only human checkpoint between a declared signature and a suite authored against
the wrong harness. That is a bad trade.

**Option A** (stop after signature declaration) is what the orchestrator
already does and is not in tension with C — C simply names why the gap after it
is deliberate rather than unfinished.

Not implemented, per the brief.

---

## 5. Pilot readiness — what actually blocks it (13E)

The five questions are in good order. Live state, read-only:

| | status / trust | contract | cases | actions | marker | starter |
|---|---|---|---|---|---|---|
| q1940 | DRAFT / UNVERIFIED | v1 | 0 | 0 | yes | `*args, **kwargs` |
| q1974 | DRAFT / UNVERIFIED | v1 | 0 | 0 | yes | `*args, **kwargs` |
| q2027 | DRAFT / UNVERIFIED | v1 | 0 | 0 | yes | `*args, **kwargs` |
| q2057 | DRAFT / UNVERIFIED | v1 | 0 | 0 | yes | `*args, **kwargs` |
| q2290 | DRAFT / UNVERIFIED | v1 | 0 | 0 | yes | `*args, **kwargs` |

All five are untouched, genuine candidates. Nothing has drifted.

### Blocker 1 — no pre-image covers any pilot question *(new, and hard)*

```
batch p27-pilot-1   state=CAPTURED (frozen)   pre-images=7
  covers:  [17, 264, 266, 963, 1436, 1689, 3309]
  pilot:   []           missing: [1940, 1974, 2027, 2057, 2290]
```

The one existing batch was captured for **earlier remediation work** and does
not contain a single pilot question. It is also already frozen, and a frozen
batch refuses further capture — by design, because freezing is what makes the
pre-images trustworthy.

So the pilot needs a **new batch**, captured over the five and then frozen.
Every reseed command refuses without a verified pre-image
(`require_pre_image`), so this blocks step 1. **Creating that batch is
forbidden in this phase**, so it is reported, not done.

### Blocker 2 — migration 0049 unapplied

§2. Needed before `reseed_contract` can run against production.

### Blocker 3 — q2027's artifact remains REJECTED

Unchanged and **not silently revised**. The specification was operator-approved;
the *artifact* was rejected because it introduced a worked example that
execution proved wrong, and regeneration showed the defect is probabilistic
(~1 in 3). q2027 needs a fresh artifact that passes the early example check
before it can enter the pilot.

The specification is fine. The generated statement is not. Those are different
objects and the distinction is the reason the rejection stands.

### Blocker 4 — q1974's style revision is still only a proposal

Unchanged and **not silently adopted**. Recorded as a proposal; no approval has
been given, so the pilot must either run against the currently-approved text or
wait for a decision. Not mine to make.

### Not blockers

Contract selection is ready for all five — 1 v3 (q1974), 4 v1 — computed from
declared signatures held offline, written nowhere.

---

## 6. The first-production five-question sequence (13F)

Designed, **not executed**.

| # | step | writes prod | human review | role | dry-run | resumable | rollback-safe |
|---|---|---|---|---|---|---|---|
| 1 | create + select batch | yes | yes | `preimage_rw` | yes | n/a | n/a — it is the anchor |
| 2 | capture pre-images, then freeze | yes | yes | `preimage_rw` | yes | yes | n/a — it is the anchor |
| 3 | apply statement | yes | yes (artifact) | `remediate_rw` | yes | yes | yes |
| 4 | declare signature | yes | yes (artifact) | `boilerplate_rw` | yes | yes | yes |
| 5 | **set contract** | yes | yes (dry-run) | `contract_rw` | yes | yes | yes |
| 6 | verify digest + audit | no | yes | `census_ro` | n/a | n/a | n/a |
| 7 | author hidden tests | yes | yes | `hidden_test_rw` | yes | yes | yes |
| 8 | quality gate | no | yes | `census_ro` | n/a | yes | n/a |
| 9 | reference candidate review | yes | **yes — mandatory** | `oracle_rw` | yes | yes | yes |
| 10 | example verification | no | yes | `census_ro` | n/a | yes | n/a |
| 11 | oracle | yes | no (2 agreeing runs) | `oracle_rw` | yes | yes | **no** — append-only |
| 12 | approval | yes | **yes — mandatory** | `approve_rw` | yes | no | **no** — append-only |
| 13 | promotion | yes | **yes — mandatory** | `promote_rw` | yes | no | yes |
| 14 | publication | yes | **yes — mandatory** | `status_rw` | yes | no | yes |

Three properties worth stating plainly:

1. **Steps 1–8 are fully reversible.** `QuestionPreImage` captures every
   mutable column and `pre_image.rollback` restores them.
2. **Steps 11–12 are not.** An oracle execution happened and an approval was
   made by a person; undoing the question does not un-happen either. They are
   marked superseded by an append-only action, never deleted. **Step 11 is the
   point of no return.**
3. **Step 5 must sit where it is.** Moving it after step 7 would leave every
   stored expected output bound to a contract chosen afterwards.

---

## 7. Post-migration verification — the exact checks (13B)

Not yet runnable. Baseline captured **before** any apply, so the comparison is
real:

```
migration head        0048_reseed_action_classes_and_ledger (2026-08-23)
action_class          varchar(32)   default NULL
stage                 varchar(24)   default NULL
check constraints     only groups_reseedledger_attempts_check (attempts >= 0)

Question           2926      RemediationAction    17
ReseedLedger          0      RemediationBatch      1  (p27-pilot-1, CAPTURED)
ReferenceSolution     3      QuestionApproval      2
OracleExecution      70      QuestionPreImage      7
reseed candidates  1140

bank fingerprint    3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6
action rows sha256  2b16c4568eee195f1ba22ce9c0fc63cbae1874e4734cc7bdbbabc20ed87e1068
```

After the apply, every line above must be identical except the migration head,
which must read `0049_reseed_contract_stage`. `migrate --check` must then
report nothing outstanding.

---

## 8. Tests (13G)

Nine tests were added pinning the migration's safety, so it is asserted rather
than reviewed by eye:

- contains no data-altering operation (AST, against a forbidden-operation set)
- is exactly two `AlterField`s
- depends on `0048`
- does not move either column width — the check that keeps it a no-op
- preserves the ledger default
- is *required* by the deployed choices — `CONTRACT_DECLARATION` and
  `CONTRACT_SET` are present in 0049 and absent from 0048, so a deployment
  that skips it has models disagreeing with recorded state
- its choices match the models exactly
- no further migration is outstanding (autodetector, no connection needed)
- `CONTRACT_SET` remains distinct from `COMPLETE`

`test_reseed_contract.py`: **69 passed.**

---

## 9. Production safety (13H)

```
questions          2926   OK        actions       17   OK
candidates         1140   OK        references     3   OK
ledger                0   OK        executions    70   OK
batches               1   OK        approvals      2   OK
submissions          44   OK        pre-images     7   OK

fingerprint  3c886bc4…f49f6   UNCHANGED
PRODUCTION QUESTION WRITES = 0
```

No migration applied. No batch created. No pre-image captured. No question row
touched. No hidden test, no oracle run, nothing approved, promoted or
published. The pilot has not begun.

---

## 10. What I need from you

1. **Apply migration 0049** with the owner credential, then tell me — I will
   run the §7 verification immediately.
2. **Confirm recommendation A** (CONTRACT_SET terminal) and **C**
   (orchestrator stays coordinator-only), or redirect.
3. **Decide q2027** — regenerate the artifact, or drop it from the pilot and
   run four.
4. **Decide q1974's style revision** — adopt, or run against the approved text.
5. **Authorise the new pilot batch** when ready. It is the first production
   write of the pilot and I have not created it.
