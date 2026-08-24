# P2.7 — Migration 0045 + the INPUT_REPAIR path

Built, tested, mutation-verified and dry-run against production. **Nothing was
written and 0045 is not applied.** Production is byte-identical to where the
statement repairs left it.

---

## A. Migration 0045 — design

`groups/migrations/0045_input_repair_action_class.py`, one operation:

```python
migrations.AlterField(
    model_name='remediationaction',
    name='action_class',
    field=models.CharField(choices=[…, ('INPUT_REPAIR', 'INPUT_REPAIR'), …],
                           max_length=32),
)
```

`makemigrations --check --dry-run` was run **before** any model change and
reported "No changes detected", so 0045 carries this change and nothing that
had accumulated.

Why the class exists rather than reusing one: `HIDDEN_TEST_REPAIR` means *the
stored answer's form changed*, `EXPECTED_OUTPUT_REPAIR` means *the answer
changed*, and an input repair means *the question changed*. All three write
`hidden_test_cases`; recording one as another would destroy the distinction this
batch has been most careful to keep.

## B. Migration safety — 22 tests

```
operations                    exactly 1, an AlterField on remediationaction.action_class
forbidden operation types     none — no RunSQL, RunPython, CreateModel, AddField,
                              RemoveField, Rename*, AlterModelTable, Add/RemoveIndex,
                              Add/RemoveConstraint
sqlmigrate output             BEGIN; -- (no-op) COMMIT;   no ALTER/CREATE/DROP/UPDATE/INSERT
dependencies                  exactly [0044_pre_image_rollback]
vocabulary added              exactly {INPUT_REPAIR}; nothing removed
existing classes              same values, same labels, same relative order
field otherwise               deconstructed kwargs identical apart from `choices`; max_length 32
other models                  no operation names any model but remediationaction
Question schema               field-by-field identical between the 0044 and 0045 states;
                              execution_contract_version still max_length 8
QuestionPreImage / Batch      field-by-field identical
PRE_IMAGE_SCHEMA_VERSION      1
CAPTURED_FIELDS               unchanged, asserted as a literal tuple
existing rows                 all 8 pre-existing classes still validate and store
INPUT_REPAIR                  validates and stores; unknown classes still rejected
append-only                   still enforced after the change
model vs migration            choices agree exactly, in order
```

The state comparison is done through `MigrationLoader.project_state` at 0044 and
at 0045, so it measures the migration graph rather than the model file.

Migration *drift* is already guarded by `test_reference_lifecycle.py`
(`makemigrations --check` as a test), so it is not duplicated.

## C. `remediate_inputs` — design

`groups/management/commands/remediate_inputs.py`. Dry-run by default; writes one
column of one question, changing only the `stdin` of explicitly named cases.

**Input is a change spec, not a suite.** The file names each case, its `before`
text and its `after` text:

```json
{"question": 3309,
 "changes": [{"case": 4, "before": "\n\n", "after": "[\"\",\"\"]"}]}
```

The proposal is then **built** from the stored suite — every case carried over
by value, only the named `stdin` substituted — so an untouched case is
byte-identical by construction rather than by inspection. A `before` that does
not match what is stored is refused: an approval written against a stale reading
cannot land.

**One deliberate departure from the brief.** The carry-over source is the LIVE
suite, not the pre-image. Deriving from the pre-image would silently revert an
earlier answer repair in the same batch — q17 and q266 both have one — turning
an input repair into an undo nobody asked for. The dry-run prints whether the
live suite still matches the pre-image so the operator can see which they are
working from, and a test (`test_a_prior_answer_repair_is_not_reverted`) holds the
behaviour. Nothing is retyped either way.

Safety, unchanged from the other repair commands: frozen batch + verified
pre-image (`require_pre_image`), `select_for_update`, alias-scoped
`transaction.atomic(using=alias)`, `update_fields=["hidden_test_cases"]`,
every other captured field compared inside the transaction, an append-only
`RemediationAction` with the resulting digest, and rollback still available from
the untouched pre-image.

## D. Role and invariant separation

`learnlm_hidden_test_rw`, re-verified as the role that would apply this — **no
new role was created**:

```
writable columns of groups_question   ['hidden_test_cases']   (of 11)
INSERT / DELETE / TRUNCATE            all false
groups_remediationaction              SELECT, INSERT
groups_questionpreimage               SELECT      (cannot alter its own undo)
groups_remediationbatch               SELECT
```

**This is the first action class the database cannot separate**, and the code
says so rather than implying otherwise:

| | `remediate_hidden_tests` | `remediate_inputs` |
|---|---|---|
| may rewrite | `expected_output` | `stdin` |
| holds fixed | every `stdin` | every `expected_output` |
| add / remove / reorder | refused | refused |
| role | `learnlm_hidden_test_rw` | **the same role** |
| audit class | `HIDDEN_TEST_REPAIR` | `INPUT_REPAIR` |

Statement-vs-key was a privilege boundary because they are different columns.
Input-vs-answer is the same column, so what separates them is the command's
invariant, its action class, and the tests below. A boundary people believe is
enforced when it is not is worse than one they know they must maintain.

Enforced structurally (AST, not text search — a docstring defeated a text guard
in an earlier phase): every `.save()` names `update_fields=[REPAIRABLE_FIELD]`
and nothing else; no assignment targets any other captured field; no
`.update()`/`.delete()`/`.bulk_update()`; the only `CLASS_*` referenced is
`CLASS_INPUT_REPAIR`; every `transaction.atomic` passes `using`; the row is
locked before it is written; the gate uses the hidden-test role list and probe,
never the statement one.

## E. q3309 dry-run

```
role            learnlm_hidden_test_rw          production True
pre-image       2395b945…d47a0a
current digest  bbd46d58…3bc2cd                 (post statement repair)
projected after 1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
cases           5 (unchanged count and order; every expected_output held fixed)
suite matches the pre-image: True

  case 1: untouched   stdin='hello\nll\n'          expected_output='2'
  case 2: untouched   stdin='aaaaa\nbba\n'         expected_output='-1'
  case 3: untouched   stdin='abc\na\n'             expected_output='0'
  case 4: STDIN CHANGED
    before stdin      '\n\n'
    after stdin       '["",""]'
    expected_output   '0'  UNCHANGED
    case identity     e3b0c44298fc1c14… -> 439083f38956ba51…   (the input changed)
    output identity   5feceb66ffc86f38… -> 5feceb66ffc86f38…   UNCHANGED
    binds as          ["",""]
  case 5: untouched   stdin='mississippi\nissip\n' expected_output='4'
```

## F. q1436 dry-run

```
pre-image       0d442588…340508
current digest  a1187744…835641                 (post statement repair)
projected after 4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
cases           4 (unchanged count and order; every expected_output held fixed)
suite matches the pre-image: True

  case 1: untouched   stdin='[["London","New York"],["New York","Paris"],["Paris","Rome"]]'
  case 2: STDIN CHANGED
    before stdin      '[["B","C"],["D","B"],["C","D"]]'
    after stdin       '[["B","D"],["A","B"],["C","D"]]'
    expected_output   'D'  UNCHANGED
    case identity     ca98a35032745542… -> 991a0528a61ec16f…   (the input changed)
    output identity   3f39d5c348e5b79d… -> 3f39d5c348e5b79d…   UNCHANGED
    binds with warnings ('undeclared_parameter_type',)
  case 3: untouched   stdin='[["A","Z"]]'
  case 4: untouched   stdin='[["A","B"],["A","C"],["B","D"],["C","D"]]'
```

The warning on case 2 is honest and expected: `paths` is still unannotated, so
the adapter would guess. It clears when `BOILERPLATE_REPAIR` lands, and until
then the contract command refuses q1436 for exactly that reason.

Both change files were generated with their `before` values **read from
production**, so a file that disagrees with the stored input cannot be produced
by accident:

```
remediation/q3309_case4_input.json   sha256 8427a1686648f1be…
remediation/q1436_case2_input.json   sha256 b5c9d96c8f284afe…
```

## G. Projected digests

```
q3309 after INPUT_REPAIR   1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
q1436 after INPUT_REPAIR   4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
```

Both equal the approved plan's projections, recomputed independently from
production before the dry-runs ran.

## H. Mutation — 20 killed / 21, **0 real survivors**

One planted equivalent (a label's spacing in the dry-run report) survived, as
intended.

Attacked and killed: an answer may be changed; a case may be added, removed or
reordered; a case nobody named may be modified; the output identity may move; a
stale approval may be applied; every case treated as a target; the frozen-batch
and digest checks skipped; the transaction opened on the wrong connection; the
row written without a lock; the save writing every column; the untouched-field
check disabled; the post-write re-check removed; no audit record; the wrong
action class; the statement role accepted; the probe pointed at the wrong
column; the repairable key becoming `expected_output`; the migration gaining a
data operation; the migration widening the column; the vocabulary reordered.

Three real survivors were found and closed — two of them the same shape as a
finding from the hidden-test phase, which is worth recording:

- **the output-identity check could be deleted unnoticed**, because byte
  equality on `expected_output` is checked first and identity is a pure function
  of it. It is a backstop for the day the byte check is relaxed, so it now has a
  test that forces it to fire.
- **the post-write re-check could be removed** — `update_fields` plus the
  pre-write invariants make it unreachable in practice. Now forced to fire by
  making the state read after the write disagree with what was proposed.
- **reordering the vocabulary on the model alone survived**: every stored row
  stays valid, so no behavioural test noticed, but the model and the migration
  would then describe different states. Closed by asserting they agree exactly,
  in order.

## I. Full regression

```
2253 passed, 0 failed, 0 errors   (groups, common, learning — 4m01s)
  migration 0045              22
  input remediation           42
```

`scripts/test_judge*.py` are excluded: three standalone Judge0 probe scripts
that open a database connection at import time and therefore fail collection
under a bare `pytest`. They are named `test_*.py`, predate this work (last
touched in the pre-repair baseline commit) and are not part of the suite.

## J. Production safety

```
batch          p27-pilot-1  CAPTURED, frozen, 7 members
q17   704a1652…   q264  396d211e…   q266  4df2af27…   q963  8da0eb14…
q1436 a1187744…   q1689 dec99939…   q3309 bbd46d58…
contract columns  q1436 'v1'   q3309 'v1'
actions        5 — and no INPUT_REPAIR among them
declaring v3   0
fingerprint    ef9c8f28ad089836bd76a6f2ac41c9356d5c62d1c2fd65308d56a97c3d321dbb
migration      0045 shows [ ] — NOT applied
```

Unchanged from the end of the statement-repair phase. The only production
traffic this phase was census reads and two dry-runs.

## K. Exact next step

1. **Apply migration 0045 to production.** It emits no DDL, but the ledger
   should record it before a row naming `INPUT_REPAIR` exists. (The write would
   succeed without it — Django does not enforce `choices` in the database — which
   is precisely why the migration should go first rather than be discovered
   missing later.)
2. **Apply q3309 case 4**, verify, and stop.
3. **Apply q1436 case 2**, verify, and stop.

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py remediate_inputs --alias hiddentest --batch p27-pilot-1 --question 3309 --changes-file remediation/q3309_case4_input.json --reason "approved plan: canonical v3 form for the empty-haystack/empty-needle case" --operator Suhas --apply --confirm
```

One at a time with verification between, as with q17 and q266.

---

```
0045                     = BUILT/TESTED, NOT APPLIED
q3309 INPUT_REPAIR       = DRY-RUN READY, NOT APPLIED
q1436 INPUT_REPAIR       = DRY-RUN READY, NOT APPLIED
BOILERPLATE_REPAIR       = NOT STARTED
CONTRACT_REPAIR          = BLOCKED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```
