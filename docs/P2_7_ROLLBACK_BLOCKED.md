# P2.7 — Real rollback exercise: STOPPED before the write

**A defect was found in the rollback path, so per §3 of the brief nothing was
restored.** q266 is the right target and its state is exactly as expected; the
dry-run is clean. What is broken is the privilege wiring: **no configured role
can execute a restore**, and the command's gate demands the one role that can do
it least.

Production is unchanged. Rollback has still never run against real data.

---

## A. Implementation trace

```
preimage_rollback.handle
  -> ops.run_gates(alias, operator, needs_write=writing)
  -> batch  = RemediationBatch.objects.using(alias)…            correct alias
  -> _plan(batch, alias, questions)                             read-only
  -> pre_image.rollback(batch, operator, questions=…)
       -> using = alias_of(batch)                               correct alias
       -> transaction.atomic(using=using)                       alias-scoped
       -> for each pre-image:  verify()                         BEFORE any write
                               select_for_update()              row lock
                               divergence check vs latest post_digest
       -> refuse if any diverged and not allow_divergence       nothing written
       -> for each: restore captured fields
                    question.save(update_fields=list(CAPTURED_FIELDS))
                    live_digest(question) == pre_image.state_digest
                       else raise -> the WHOLE rollback reverts
       -> RemediationAction(CLASS_ROLLBACK, …)                  append-only
       -> batch.state = STATE_ROLLED_BACK
```

Confirmed sound: correct alias throughout, alias-scoped transaction, row lock,
verification before any write, post-restore digest check inside the transaction
so a failure reverts everything, an append-only audit record, and
`ROLLBACK_SCOPE = ('groups_question',)` — references, oracle executions,
approvals, submissions and recommendation logs are never touched.

### ⚠ The blocker

`pre_image._rollback` restores by writing **all seven captured fields**:

```python
question.save(using=using, update_fields=list(CAPTURED_FIELDS))
```

```
CAPTURED_FIELDS = content, status, trust_state, execution_contract_version,
                  boilerplate_code, hidden_wrapper_code, hidden_test_cases
```

Measured against every configured connection:

```
boilerplate  learnlm_boilerplate_rw   1/7   cannot restore
contract     learnlm_contract_rw      1/7   cannot restore
hiddentest   learnlm_hidden_test_rw   1/7   cannot restore
remediate    learnlm_remediate_rw     1/7   cannot restore
preimage     learnlm_preimage_rw      0/7   cannot restore
default      learnlm_census_ro        0/7   cannot restore
```

**No role can perform a restore.** The least-privilege design that made each
repair safe also made the undo impossible, and nothing detected it because
rollback had never been run.

It would fail in a safe direction — the transaction reverts — but it would fail
*at the server, mid-write*, which is not a proof of anything.

### ⚠ And the gate points at the wrong role

`preimage_rollback` calls `run_gates` **without** `allowed_roles` or
`required_privileges`, so both fall back to the capture defaults:

```
allowed_roles        -> {'learnlm_preimage_rw'}
required_privileges  -> (('groups_questionpreimage', None, 'INSERT'),)
```

So the command demands the **capture** role — which holds nothing at all on
`groups_question` — and probes for INSERT on the pre-image table, a privilege
rollback never uses. Every other repair command names its own role list and
probes the exact column it will write; this one was never updated when those
were introduced. It is the same class of bug as the statement-repair gate that
demanded INSERT, found in an earlier phase.

### Two lesser findings, recorded not fixed

- **A partial rollback relabels the whole batch.** `_rollback` sets
  `batch.state = ROLLED_BACK` even when restoring one question of seven, and no
  command can set it back. Nothing gates on `state` — every reader only prints
  it (verified across the codebase) — so this is audit legibility, not
  behaviour. But after restoring q266 the batch would read "Rolled back" while
  nine of ten actions still stand.
- **A multi-question rollback records one digest.** The ROLLBACK action stores
  `post_digest=pre_images[0].state_digest`, i.e. the first question's. Correct
  for a single-question restore like this one; misleading for a batch-wide one.

## B. Target selection

q266, as directed, and it qualifies on every stated ground:

```
only hidden_test_cases differs from the capture      confirmed field-by-field
the repair was mechanical casing normalisation       'False'/'True' -> 'false'/'true'
the pre-image is intact                              recomputes to its digest
the post-digest is known                             4df2af27…a8f704
no statement or contract dependency                  those fields are identical
```

q264 (control), q1689 (manual review) and q963 (intentional
statement/key disagreement) were excluded as instructed.

## C. Pre-rollback state

```
live digest       4df2af2733546b7c208a0407f91254f36244a2361065f6827c14299825a8f704
pre-image         1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411   verifies
batch             CAPTURED, frozen; q266 is a member
q266 actions      exactly one — HIDDEN_TEST_REPAIR, post_digest 4df2af27…
divergence        none (live == recorded post_digest)

content · status · trust_state · execution_contract_version ·
boilerplate_code · hidden_wrapper_code        all identical to the capture
hidden_test_cases                             CHANGED — the repair

live suite        ['false', 'true', 'true', 'true']
pre-image suite   ['False', 'True', 'True', 'True']
every stdin       identical
```

## D. Dry-run

```
PRE-IMAGE ROLLBACK  (DRY RUN)
  database        neondb        role  learnlm_preimage_rw
  batch           p27-pilot-1 (CAPTURED)
  to restore      1
    q266    4df2af2733546b7c -> 1ba2e68f49b16317

DRY RUN — nothing was restored.
```

Exactly one question targeted; before matches live; after matches the pre-image;
no note of corruption, divergence, or already-restored. The plan is right — the
write is what cannot happen.

## E–G. Not performed

No rollback was applied, so there is no post-rollback digest and no ROLLBACK
action. q266 remains at `4df2af27…a8f704` with its single HIDDEN_TEST_REPAIR.

## H. A correction to the expected fingerprint

The brief expects the post-rollback bank fingerprint to be
`0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034` — "the state
immediately before q266's HIDDEN_TEST_REPAIR". **That is no longer reachable by
rolling back q266.** Seven further actions have landed since, on q3309 and
q1436, and rolling back one question does not undo them.

Computed prediction for when the rollback does run:

```
current                        8dcea5053a365916c15022a9cbb4ef42310b7e435ee3fce4208d8c80636edb24
predicted after q266 rollback  e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8
brief's expectation            0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
```

The prediction is the current fingerprint with q266's `hidden_test_cases`
replaced by the captured value — verifiable the same way every other phase has
been.

## I. Regression

Not re-run: no code was changed and nothing was written. The last full run, at
the current commit state, was **2294 passed, 0 failed, 0 errors**, including the
pre-image and rollback suites — which pass because they exercise rollback
against a test database owned by a role that holds everything. That is precisely
why the gap survived to now.

## J. Production safety

```
q266            4df2af27…a8f704   unchanged, still repaired
q264, q1689     byte-identical to their pre-images
q963, q17, q3309, q1436   at their recorded digests
batch           CAPTURED, frozen, 7 members
actions         10 — no ROLLBACK among them
fingerprint     8dcea5053a365916c15022a9cbb4ef42310b7e435ee3fce4208d8c80636edb24
```

The only production traffic this phase was census reads and one dry-run.

## K. Is the rollback foundation proven on real data?

**No — and now we know why it never was.** The mechanism is sound: verification
before writing, locking, atomicity, digest confirmation, append-only audit,
question-only scope. What is missing is a connection permitted to perform it.
Until that is resolved, every "this can be rolled back" line in ten verification
reports rests on a path that cannot currently execute.

## L. Exact next phase — the fix, then the exercise

Three options, and my recommendation is the second.

**1. Create `learnlm_rollback_rw`** with UPDATE on all seven captured columns.
Honest but blunt: that role is the union of every repair privilege, which
undoes the separation the last several phases built.

**2. Restore only the fields that actually differ** *(recommended)*. Compute
the differing fields and pass those to `update_fields`. Then a rollback needs
exactly the privileges of the repairs it is undoing — q266's needs only
`hidden_test_cases`, so `learnlm_hidden_test_rw` could perform it today. The
whole-state digest check still proves the complete captured state was
reproduced, so the guarantee does not weaken. Requires the gate fix below and
its own tests and mutation sweep.

**3. Use the owner connection.** It would work, and it would make the single
most powerful write in the system the one with no least-privilege story.

Either way the gate must be corrected: `preimage_rollback` should name its own
allow-list and probe the columns it will write, instead of inheriting the
capture defaults. I would also record `batch.state` handling for a partial
rollback rather than flipping the whole batch.

I have not changed any of this — the brief authorised a rollback exercise, not a
redesign of the rollback path.

---

```
REAL ROLLBACK              = BLOCKED, NOT EXECUTED (defect found, reported)
ROLLBACK FOUNDATION        = SOUND IN LOGIC, UNEXECUTABLE AS WIRED
EXECUTION CONTRACT PILOT   = COMPLETE
q266                       = STILL REPAIRED, UNCHANGED
ORACLE                     = NOT STARTED
SEMANTIC KEY REMEDIATION   = NOT STARTED
BATCH                      = FROZEN
RESEED                     = NO
```
