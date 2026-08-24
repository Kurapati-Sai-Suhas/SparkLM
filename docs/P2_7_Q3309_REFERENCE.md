# P2.7 — q3309 reference: authored, reviewed, approved, canonical

The first reference solution created since the pilot began, and the first thing
in this milestone that can produce an answer. **No oracle has run.** q3309 is
still `DRAFT / UNVERIFIED` and the question was not touched.

---

## A. Oracle role verification

`learnlm_oracle_rw`, verified as itself through the `oracle` alias:

```
endpoint       ep-blue-hat-aj7p2x8v-pooler   (canonical)
database       neondb                        server 17.10 (29ad1b7)
current_user / session_user   learnlm_oracle_rw

LOGIN True · SUPERUSER False · CREATEDB False · CREATEROLE False
REPLICATION False · BYPASSRLS False · memberships NONE
```

```
groups_question              ['SELECT']                        ok
groups_referencesolution     ['INSERT','SELECT','UPDATE']      ok
groups_oracleexecution       ['INSERT','SELECT']               ok
groups_questionapproval      []                                ok
groups_questionpreimage      []                                ok
groups_remediationaction     []                                ok
groups_remediationbatch      []                                ok
groups_codesubmission        []                                ok

every groups_question column, UPDATE:   writable NONE
sequences (reference, execution)        USAGE + SELECT
```

Neither over- nor under-granted, and the other six aliases still connect as
their own roles. **This role can say what a reference produced and can change
nothing about the question it produced it for.**

### One grant it turned out NOT to need

The first `reference_review inspect` failed with `permission denied for table
groups_topic`: the command was joining `question__topic` to print a heading.
Rather than grant the role a table it has no business reading, the join was
removed and the topic name is now read through `default`, exactly as the
approver's username already was. A display convenience must not widen a
least-privilege role.

## B. The reference source

`ReferenceSolution #2`, python, 1203 bytes, sha256
`18ad8f390642c315ef78e52fecaf97ab31fd6fc69fca2a02b3114e03d77a341b`.

```python
class Solution:
    def strStr(self, haystack: str, needle: str) -> int:
        """…written against q3309's repaired statement, clause by clause…"""
        if needle == "":
            return 0
        return haystack.find(needle)
```

Checked before it was stored:

```
declared signature   strStr(haystack: str, needle: str)   — matches the starter
public methods       ['strStr']                            — the wrapper needs exactly one
variadic             False
deterministic        identical output over repeated runs
contract             v3
```

Written against the **repaired** statement clause by clause: the empty-needle
branch exists because the statement states that rule explicitly, even though
`str.find` already returns 0 — a reader should see the rule honoured rather than
inferred from a library's edge case.

**The source file was not committed to the repository.** It was authored in the
session scratchpad and passed to `--source-file`; the database row is the record
of truth, and the answer key is not multiplied across places it does not need to
be. Say the word if you would rather it lived in `remediation/`.

Worth stating for the reconciliation phase: this implementation delegates to
`str.find`, so an AGREE will prove the stored keys match Python's substring
semantics — which is what the statement describes. It is not independent
evidence that the *statement* is the right problem.

## C. Review

`reference_review inspect 2 --alias oracle --operator Suhas --show-source`
printed the metadata and the full source (the only way to read it — the model
has no serializer, no viewset and no admin registration):

```
question          3309 · Find the Index of the First Occurrence in a String
topic             Two Pointers
language          python
review state      DRAFT      is_active False     canonical False
provenance intact False      source hash -       source length 1203
```

Then `submit`: `DRAFT/active=False -> IN_REVIEW/active=False`.

## D. Approval

```
reference 2 (question 3309): IN_REVIEW/active=False -> APPROVED/active=False
  approved_by  Suhas
  approved_at  2026-08-19 09:11:25 UTC
  source_hash  18ad8f390642c315ef78e52fecaf97ab31fd6fc69fca2a02b3114e03d77a341b
```

**Naming what this means.** The approval is recorded against your account, and
the implementation is one I wrote — you authorised the transition in the brief
rather than after reading the source. The source is reproduced in full in §B and
in the review output above; if it is not what you would approve, the lifecycle's
answer is to supersede it (`reference_create` a replacement and reject this one),
never to edit it: the row is now frozen by a database constraint that recomputes
its digest.

## E. Activation and canonical status

```
reference 2 (question 3309): APPROVED/active=False -> APPROVED/active=True

is_canonical            True
canonical_reference()   #2
canonical blocker       none
```

Verified against the model's own rules rather than asserted: approved, active,
and byte-identical to what was approved (`compute_source_hash` recomputes to the
stored hash). The bank now holds two references — q1779's and this one — and
q3309 has exactly one active reference in one language, which is what
`canonical_reference` requires to return anything at all.

## F. Provenance

```
approved_by_id   1 (Suhas)      approved_at   2026-08-19 09:11:25 UTC
source_hash      18ad8f39…d77a341b   recomputes from the stored source: True
```

All three provenance fields are set together, enforced by the
`reference_approval_provenance` CHECK — "approved by nobody at no time" is not
writable.

## G. Production safety

```
q3309 digest       8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6   unchanged
q3309 status/trust DRAFT / UNVERIFIED       contract 'v3'      adaptive False
pre-image verifies True

q1436 unchanged · q264, q266, q1689 at their pre-images
batch              CAPTURED, frozen
remediation actions 11        OracleExecution 20 (q3309: 0)
QuestionApproval    0
fingerprint        e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8   unchanged
```

The only rows written this phase were the reference and its lifecycle
transitions. **The bank fingerprint did not move**, which is the point: a
reference is evidence about a question, not part of it.

Suites re-run after the topic-join fix: 103 passed (reference create, review,
oracle write path).

## H. The next command — oracle DRY RUN

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py oracle_execute --question 3309 --alias oracle
```

No `--execute`, so it writes nothing: it runs the canonical reference against
all five hidden cases on Judge0 and prints, per case, the produced output, the
stored answer, and AGREE / CONFLICT / ABSENT. That is the first time this
milestone will have executed anything.

Two things to expect from it. The question will be flagged `1 hidden test below
the floor of 12` style advisories — q3309 has five cases, and the pipeline's
advisory floor is 12; that is advisory, not blocking. And `ready_for_quality_
gate` is **not** readiness for `ORACLE_VERIFIED`: the P2.7h-1 quality gate and a
human question-approval step still do not exist.

---

```
q3309 REFERENCE        = ACTIVE / CANONICAL
q3309 ORACLE           = NOT STARTED
q1436 REFERENCE        = NOT STARTED
KEY_REPAIR             = NOT STARTED
APPROVAL OF QUESTION   = NOT STARTED
PROMOTION              = NOT STARTED
RESEED                 = NO
```
