# P2.7 — Oracle validation for q3309 and q1436: STOPPED at Step 2

**Both questions are ready. The lifecycle cannot be entered.** Per §2 of the
brief — "if the current implementation cannot safely associate a reference with
the repaired question state, STOP and report the gap" — nothing was created and
nothing was executed.

Two independent gaps, both structural, neither fixable by writing a reference
solution more carefully.

---

## Step 1 — preconditions: all pass

```
batch  p27-pilot-1  CAPTURED, frozen, members [17,264,266,963,1436,1689,3309]
```

### q3309 — `8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6`

```
pre-image verifies · in batch · contract 'v3' (effective v3)
status DRAFT · trust UNVERIFIED · adaptive_eligible False
declared  strStr(haystack: str, needle: str)

1. 'hello\nll\n'            expected '2'   binds  ["hello","ll"]
2. 'aaaaa\nbba\n'           expected '-1'  binds  ["aaaaa","bba"]
3. 'abc\na\n'               expected '0'   binds  ["abc","a"]
4. '["",""]'                expected '0'   binds  ["",""]
5. 'mississippi\nissip\n'   expected '4'   binds  ["mississippi","issip"]
```

### q1436 — `0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a`

```
pre-image verifies · in batch · contract 'v3' (effective v3)
status DRAFT · trust UNVERIFIED · adaptive_eligible False
declared  destCity(paths: list[list[str]])

1. [["London","New York"],…]        expected 'Rome'  binds (one list)
2. [["B","D"],["A","B"],["C","D"]]  expected 'D'     binds (one list)
3. [["A","Z"]]                      expected 'Z'     binds (one list)
4. [["A","B"],["A","C"],…]          expected 'D'     binds (one list)
```

Zero adapter warnings on either. q266 and q264 are both at their pre-images.

## Step 2 — the lifecycle, and where it stops

The design is sound and I am not proposing to change it:

```
required fields   question, language, source_code
lifecycle         review_state DRAFT -> IN_REVIEW -> APPROVED / REJECTED
                  is_active is a SEPARATE field, default False
provenance        approved_by / approved_at / source_hash — all NULL unless
                  APPROVED, all NOT NULL when it is (database constraints)
uniqueness        one ACTIVE reference per (question, language)
canonical         approved + active + byte-identical to what was approved
```

```
ReferenceSolution rows in production: 1
  q1779  python  APPROVED  active=True  canonical=True

q3309   references 0 · canonical None ("no active reference solution")
        OracleExecution 0 · QuestionApproval 0
q1436   references 0 · canonical None ("no active reference solution")
        OracleExecution 0 · QuestionApproval 0
```

Nothing exists to reuse: the one reference in the bank belongs to q1779, and a
reference is tied to a question by foreign key, so it cannot be borrowed.

### ⚠ Gap 1 — no role can create a reference or record an execution

```
                                       groups_referencesolution   groups_oracleexecution
learnlm_census_ro                      ['SELECT']                 ['SELECT']
learnlm_preimage_rw                    []                         []
learnlm_remediate_rw                   []                         []
learnlm_hidden_test_rw                 []                         []
learnlm_boilerplate_rw                 []                         []
learnlm_contract_rw                    []                         []
```

Every write role in the system was scoped to one column of `groups_question`.
None was ever granted anything on the two tables this phase must write. This is
the same shape as the rollback blocker: the least-privilege work was done per
phase, and the phase that needed these tables had not happened yet.

### ⚠ Gap 2 — the commands cannot use a write role even if one existed

```
reference_create   '--alias' present: False    'using(' present: False
oracle_execute     '--alias' present: False    'using(' present: False
```

Both predate the operator-alias machinery (they are P2.5 / P2.7d; the aliases
arrived with the pre-image work). They write through `default` — which is
`learnlm_census_ro`, read-only. So creating the role is necessary but **not
sufficient**: both commands need an `--alias` and `.using(alias)` threading,
which is exactly the alias-threading defect already fixed once inside
`pre_image.py`.

Judge0 itself is configured (`JUDGE0_API_HOST`, `JUDGE0_API_KEY`, and both
limits are set), so execution is not the obstacle.

## Steps 3–7 — not performed

No reference solution was written for either question, no oracle ran, no
`OracleExecution` row exists, and no comparison was made. Steps 5–7 depend
entirely on a canonical reference existing, and one cannot be created.

## Step 8 — classification

Not applicable: with no oracle output there is nothing to classify as AGREE,
CONFLICT or UNASSESSABLE. Recording anything here would be inventing evidence.

Worth restating for when it is applicable, because it governs how the results
must be read: **the oracle proves what the reference implementation returns. It
does not prove the reference answers the statement correctly.** An AGREE
between a stored key and a reference output means they agree — if both were
derived from the same misreading, they will agree and both be wrong.

## Production safety

```
q3309   8a342568…   q1436  0b2a79f2…    both unchanged
q266, q264, q1689   at their pre-images
q963  8da0eb14…   q17  704a1652…
batch   CAPTURED, frozen, 7 members
actions 11 (10 repairs + 1 rollback) — nothing added
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 2
adaptive_eligible 0
```

The only production traffic this phase was census reads.

## Exact next action

**1. Create the oracle role.** It needs, and should hold, nothing else:

```sql
CREATE ROLE learnlm_oracle_rw LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE neondb TO learnlm_oracle_rw;
GRANT USAGE ON SCHEMA public TO learnlm_oracle_rw;

GRANT SELECT ON groups_question TO learnlm_oracle_rw;
GRANT SELECT, INSERT, UPDATE ON groups_referencesolution TO learnlm_oracle_rw;
GRANT SELECT, INSERT ON groups_oracleexecution TO learnlm_oracle_rw;
GRANT USAGE, SELECT ON SEQUENCE groups_referencesolution_id_seq,
                                groups_oracleexecution_id_seq
  TO learnlm_oracle_rw;
```

`UPDATE` on the reference table is required by the lifecycle itself — review and
approval move `review_state`, `is_active` and the provenance fields on an
existing row. It holds **no** UPDATE on `groups_question`, so an oracle run
cannot rewrite a key, a statement or a contract; and nothing on
`groups_questionapproval`, so it cannot approve a question either.

**2. Thread the alias through `reference_create` and `oracle_execute`** — add
`--alias`, route every read and write with `.using(alias)`, and give each its own
role list and privilege probe, as the five repair commands have. Then tests and
a mutation sweep, as with every other write path.

**3. Then the phase as briefed**: draft reference → human review → approve →
oracle dry-run → execute q3309 → stop → q1436 → stop → reconcile.

I would do (2) as its own phase with its own review rather than folding it into
the first oracle run: it is the first code that will execute a reference
implementation against production grading truth, and the last three phases have
each found a privilege or alias defect in exactly this kind of seam.

---

```
ROLLBACK        = PROVEN ON REAL PRODUCTION
q3309 ORACLE    = BLOCKED (no role, no alias support)
q1436 ORACLE    = BLOCKED (no role, no alias support)
REFERENCES      = NONE CREATED
KEY_REPAIR      = NOT STARTED
APPROVAL        = NOT STARTED
PROMOTION       = NOT STARTED
RESEED          = NO
```
