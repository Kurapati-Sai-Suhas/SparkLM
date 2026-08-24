# P2.7 — Contract remediation pilot: q1436 + q3309

The path is built, tested, mutation-verified and dry-run. **Nothing was
written**, and the headline finding is a refusal:

> **Neither question can be repaired by a contract change alone.** q1436 needs
> its starter annotated first; q3309 needs a decision about case 4. The command
> refuses both rather than declaring a contract the question cannot execute
> under — and the q1436 dry-run below is that refusal, in full.

---

## A. q1436 — Destination City

Read from the frozen pre-image `0d442588…340508` (live still matches it).

| | |
|---|---|
| statement | **VALID** — unchanged by your sign-off |
| declared signature | `destCity(self, paths)` — arity **1**, `paths` **UNANNOTATED** |
| actual argument structure | one `list[list[str]]`: a list of `[from, to]` pairs |
| stored contract column | `'v1'` |

### Why the current contract is insufficient

```
case 1 stdin  [["London","New York"],["New York","Paris"],["Paris","Rome"]]
  v1 today    JSON list of 3  ->  splatted into 3 positional arguments
              -> TypeError: destCity() takes 2 positional arguments but 4 were given
```

All four cases fail this way (3, 3, 1 and 4 arguments respectively). The v1
wrapper's splat is a complete calling convention — it is simply the wrong one
for a function whose single parameter *is* the list.

### Why v3 alone would make it worse

`classify_annotation('')` is `UNDECLARED`, so with arity 1 the adapter hands the
**whole stored text** over as a single **string** and records a warning:

```
binds to  ('[["B","C"],["D","B"],["C","D"]]')      <- a str, not a list
envelope  ["[[\"B\",\"C\"],[\"D\",\"B\"],[\"C\",\"D\"]]"]
WARNING   undeclared_parameter_type
```

A correct solution iterating that argument would walk **characters**. Today's
defect is a loud `TypeError`; v3-without-annotation would convert it into a
quiet wrong answer. **That is a regression, not a repair**, and the command
refuses it (§G).

### The exact intended contract after repair

```python
class Solution:
    def destCity(self, paths: list[list[str]]) -> str:
```
```
execution_contract_version : 'v1' -> 'v3'
case 2 then binds to  ([['B','C'], ['D','B'], ['C','D']],)      one list argument
envelope              [[["B","C"],["D","B"],["C","D"]]]
```

Two columns: **`boilerplate_code`** (the annotation) and
**`execution_contract_version`** (the declaration). The annotation is
`BOILERPLATE_REPAIR` — a different action class, a different column, and a role
that does not exist yet. It must land **first**; the declaration is meaningless
before it and misleading after a failure.

### The case-2 input decision — **still open**

Your sign-off reads, in full: *"case 2 requires input repair/drop decision"*.
**No decision was recorded**, so there is no approved case-2 input change and
this phase applies none. Restating the problem exactly as adjudicated:

```
case 2 stdin  [["B","C"],["D","B"],["C","D"]]     the cycle B -> C -> D -> B
```

Every city has an outgoing edge, so no destination city exists, yet the
statement guarantees one. The stored key `D` is not derivable and the stored
explanation ("all of them end at D") is false — `D -> B` is an edge.

The three options remain what the adjudication listed: repair the input to a
non-cyclic graph, drop the case, or relax the stated guarantee. **The contract
command cannot do any of them** — it holds no privilege on `hidden_test_cases`
or `content` — which is why the decision can wait without blocking anything
else.

## B. q3309 — First Occurrence in a String

Read from the frozen pre-image `2395b945…d47a0a` (live still matches it).

| | |
|---|---|
| statement decision | **approved**: preserve the empty-needle rule, relax `1 <= \|needle\|` to `0 <=` |
| empty-needle behaviour | statement says return `0`; case 4 stores `'0'`; the adjudication verified the key is correct |
| declared signature | `strStr(self, haystack: str, needle: str) -> int` — arity **2**, both annotated `str` |
| stored contract column | `'v1'` |

### Current contract

```
case 1 stdin  'hello\nll\n'
  v1 today    not JSON -> the whole blob passed as ONE raw value
              -> TypeError: strStr() missing 1 required positional argument
```

**All five cases are broken today**, not just case 4 — the annotated two-line
form is exactly what v1 cannot read.

### The exact contract change required

```
execution_contract_version : 'v1' -> 'v3'

case 1  'hello\nll\n'         -> ('hello', 'll')          envelope ["hello","ll"]
case 2  'aaaaa\nbba\n'        -> ('aaaaa', 'bba')         envelope ["aaaaa","bba"]
case 3  'abc\na\n'            -> ('abc', 'a')             envelope ["abc","a"]
case 5  'mississippi\nissip\n'-> ('mississippi','issip')  envelope ["mississippi","issip"]

case 4  '\n\n'                -> REFUSED  CONTRACT_MISMATCH
```

Four of five cases are repaired by the declaration alone. **Case 4 is not**: two
blank lines give zero non-blank lines, are not a JSON array, and yield zero
whitespace tokens, so nothing can supply two parameters. Under v3 the canonical
stored form for empty/empty is the array `["",""]` — but that is a change to a
case's **stdin**, which is `hidden_test_cases`: not this command's column, and
explicitly refused by the hidden-test command's stdin invariant.

So q3309 needs three separate acts, in this order:

1. **statement relaxation** `1 <=` → `0 <=` — approved, `content`, the statement
   role, its own review;
2. **case 4** — repair the stored form to `["",""]` (a new `INPUT_REPAIR` class)
   or drop the case. **Decision required.**
3. **contract → v3** — this command, blocked until (2) resolves.

## C. Contract-repair design

`groups/management/commands/remediate_contract.py` — a **third** remediation
command, writing exactly one column: `execution_contract_version`.

Statement repair changes what is asked. Hidden-test repair changes what answer
is recorded. This changes neither — it changes **how stored inputs are delivered
to the learner's code**, which is a distinct authority over the same row.

Everything the brief required, and where it lives:

| requirement | mechanism |
|---|---|
| frozen batch | `pre_image.require_pre_image` raises on an unfrozen batch |
| matching immutable pre-image | same call; the digest is recomputed, not trusted |
| question inside the batch | same call — no pre-image, no write |
| row lock | `select_for_update()` inside the transaction |
| before/after verification | every other captured field compared **inside** the transaction; a difference reverts |
| the write actually landed | the column is re-read and compared to the declared target before the action is recorded |
| append-only action | `RemediationAction.CLASS_CONTRACT_REPAIR` with the post-image digest |
| rollback readiness | the pre-image still holds `'v1'`; `pre_image.rollback` restores it |
| column-scoped at the database | `learnlm_contract_rw`, §D |

Plus two rules this class needs and the others do not:

**Only v3 is a repair.** `--to-version v1`/`v2`/anything else is refused.
Re-declaring a question under a contract with different input semantics is a
migration, not a repair, and a silent downgrade to v1 would re-enable the splat
bug this milestone exists to remove.

**The feasibility rule.** Before proposing anything, every stored case is bound
through `execution_adapter` under the target contract. The migration is refused
if any case cannot bind, if any case is structurally unusable, if the starter
has no callable or more than one public method, if the question carries its own
wrapper (which overrides the contract version, so declaring it would change
nothing), if there are no cases at all — **or if any case binds only with a
warning**. That last one is q1436: `undeclared_parameter_type` means the adapter
guessed, and a contract declaration is exactly the wrong place to make a guess
official.

## D. Role and privileges

`learnlm_remediate_rw` and `learnlm_hidden_test_rw` were **not broadened**. A
fourth least-privilege role is required:

```sql
CREATE ROLE learnlm_contract_rw LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE neondb TO learnlm_contract_rw;
GRANT USAGE ON SCHEMA public TO learnlm_contract_rw;

GRANT SELECT ON groups_question TO learnlm_contract_rw;
GRANT UPDATE (execution_contract_version) ON groups_question TO learnlm_contract_rw;

GRANT SELECT, INSERT ON groups_remediationaction TO learnlm_contract_rw;
GRANT SELECT ON groups_remediationbatch, groups_questionpreimage
  TO learnlm_contract_rw;
```

Three roles, three columns, each forbidding the other two:

| | statement | hidden-test | contract |
|---|---|---|---|
| `content` UPDATE | **yes** | no | no |
| `hidden_test_cases` UPDATE | no | **yes** | no |
| `execution_contract_version` UPDATE | no | no | **yes** |
| status / trust_state / boilerplate / wrapper | no | no | no |
| INSERT / DELETE / TRUNCATE | no | no | no |

Verify after creating it:

```bash
psql "$OWNER_URL" -c "select has_column_privilege('learnlm_contract_rw','groups_question','execution_contract_version','UPDATE') as can_declare, has_column_privilege('learnlm_contract_rw','groups_question','content','UPDATE') as can_edit_statement, has_column_privilege('learnlm_contract_rw','groups_question','hidden_test_cases','UPDATE') as can_edit_keys, has_column_privilege('learnlm_contract_rw','groups_question','boilerplate_code','UPDATE') as can_edit_starter;"
```

Expect `t`, **`f`**, **`f`**, **`f`**. The gate also refuses an *over-granted*
role, so a fourth `f` that turned into `t` would stop the command rather than be
used.

Credentials go in `.env` as `CONTRACT_*` (five variables, documented in
[FEATURE_FLAGS.md](LearnLM/docs/FEATURE_FLAGS.md)); the `contract` alias exists
only when `CONTRACT_USER` is set.

**Note on the starter.** Annotating q1436 needs `boilerplate_code` UPDATE, which
**no role holds**. That is deliberate: `BOILERPLATE_REPAIR` changes the code
every learner is handed, and it should not arrive as a side effect of a contract
phase.

## E. Tests — 36, all passing

| requirement | covered |
|---|---|
| only `execution_contract_version` changes | yes, field-by-field |
| statement / keys / starter / wrapper cannot change | yes, one test each |
| status, trust_state, `is_adaptive_eligible` unchanged | yes |
| pre-image intact and still holding `'v1'` | yes |
| rollback restores the original contract | yes |
| action records the resulting digest and links the pre-image | yes |
| unannotated parameter refused (q1436) | yes, apply **and** dry-run |
| unbindable case refused (q3309 case 4) | yes |
| own-wrapper question refused | yes |
| several public methods refused | yes |
| no Python starter refused | yes |
| no cases refused | yes |
| structurally broken case refused | yes |
| only v3 accepted (`v1`, `v2`, `v4`, `''` refused) | yes |
| blank column treated as v1 and migratable | yes |
| no-op refused | yes |
| out-of-batch, unfrozen, corrupt pre-image refused | yes |
| unexpected field change reverts | yes |
| a write that did not land reverts | yes |
| the control question is never moved | yes |
| dry-run writes nothing | yes |

Plus structural guards: every `save()` names `update_fields=[REPAIRABLE_FIELD]`
and nothing else; no captured field other than the contract column is ever
assigned; no queryset `.update()`/`.delete()`; the four role lists are disjoint;
each probe forbids the other two columns; and the forbidden list covers **every**
other captured field, so adding a field to `CAPTURED_FIELDS` without adding it
there fails the suite.

The guards are AST-based, not text searches. This command legitimately *reads*
the statement, the starter and the cases to judge feasibility, so a "the word
`content` must not appear" guard would be both wrong and — as an earlier phase
found — defeated by its own docstring.

**Full regression: 2,189 passed, 0 failed** (`pytest groups common`), up from
2,153 by exactly the 36 new tests.

## F. Mutation — 23 killed / 24

One planted equivalent survivor (`S1`, a label string in the dry-run plan).

Attacked and killed: wrong column (`content`, `hidden_test_cases`, `status`),
broad table UPDATE (`save()` without `update_fields`), deleted untouched-field
check, missing pre-image requirement, missing audit record, dropped row lock,
transaction opened on the wrong alias, the statement role allowed to migrate,
the probe pointed at the wrong column, `hidden_test_cases` removed from the
forbidden list, the role list collided with the statement one, every feasibility
check disabled one at a time (warnings, unbindable case, own wrapper, several
methods, broken case, refusals ignored), no-op accepted, any version accepted,
and dry-run writing anyway.

**Two real survivors were found and closed:**

- **the row lock could be deleted and every test still passed.** A lost
  `select_for_update()` is invisible single-threaded — the write succeeds, and
  only a concurrent remediation shows the race. Closed with a structural guard.
- **the post-write "did it land" check could be deleted unnoticed**, because it
  cannot fire in normal operation. It matters: the action's `post_digest` is
  what rollback later compares against, so an action claiming v3 over a row
  still holding v1 would make the audit trail describe a repair that did not
  happen. Closed with a test that forces it to fire.

## G. q1436 dry-run — refused, as designed

Run read-only through `learnlm_census_ro`; the contract role does not exist yet
and a dry-run needs no write privilege.

```
CONTRACT REPAIR  (DRY RUN)
  database        neondb
  role            learnlm_census_ro
  production      True
  operator        Suhas

  batch           p27-pilot-1 (CAPTURED)
  question        1436 — Destination City
  pre-image       0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
  current digest  0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
  projected after e1ffe8dfdadd96c2323937db5090bb30944ababd67d9df00fdc18eb2735edaba
  field           execution_contract_version (the ONLY field this command can change)
  stored value    'v1'  (grades as v1)
  proposed value  'v3'

  declared        destCity(paths  <-- UNANNOTATED)
  arity           1

  cases — inputs are READ, never written:
    case 1: stdin '[["London","New York"],["New York","Paris"],["Paris","Rome"]]'
            case_identity 27d637edaf67a4d5…  (unchanged — this command cannot write hidden_test_cases)
            binds to  ('[["London","New York"],["New York","Paris"],["Paris","Rome"]]')
            WARNING   undeclared_parameter_type
    case 2: stdin '[["B","C"],["D","B"],["C","D"]]'
            case_identity ca98a35032745542…  (unchanged)
            binds to  ('[["B","C"],["D","B"],["C","D"]]')
            WARNING   undeclared_parameter_type
    case 3: stdin '[["A","Z"]]'
            case_identity 34928cbd31f148f5…  (unchanged)
            binds to  ('[["A","Z"]]')
            WARNING   undeclared_parameter_type
    case 4: stdin '[["A","B"],["A","C"],["B","D"],["C","D"]]'
            case_identity 9d4f0fb3d9288ece…  (unchanged)
            binds to  ('[["A","B"],["A","C"],["B","D"],["C","D"]]')
            WARNING   undeclared_parameter_type

CommandError: question 1436 cannot be migrated to v3 yet:
  - case 1 binds only by guessing (undeclared_parameter_type); annotate the
    starter first — that is a BOILERPLATE_REPAIR, a different column and a
    different review
  - case 2 … case 3 … case 4 (same)
Nothing was written.
```

**Stdin semantics are preserved, in the strongest available sense: they are not
writable from here at all.** The command holds no privilege on
`hidden_test_cases`, names no other column in any `update_fields`, and the plan
prints each case's `provenance.case_identity` to make the point checkable rather
than promised. The **only** input question in this batch — case 2's cycle —
remains open and untouched, exactly as your sign-off left it.

For the record, the projected digest had the migration been permissible:
`e1ffe8df…5edaba`. It is a projection only; nothing was written.

## H. Production unchanged

```
batch          CAPTURED, frozen, 7 members
q17    704a1652…  q264   396d211e…  q266   4df2af27…  q963   8da0eb14…
q1436  0d442588…  (contract column 'v1')
q1689  dec99939…
q3309  2395b945…  (contract column 'v1')
actions        3 — q17 + q266 HIDDEN_TEST_REPAIR, q963 STATEMENT_REPAIR
questions declaring v3   0
fingerprint    8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
               unchanged
```

## I. Exact next action

Nothing here is applicable without a decision from you. In dependency order:

1. **q1436** — authorise `BOILERPLATE_REPAIR` to annotate
   `paths: list[list[str]]`. That needs a fifth role with column UPDATE on
   `boilerplate_code` and its own review; the contract migration is only
   meaningful afterwards. Separately, case 2's input decision is still open.
2. **q3309** — decide case 4: repair its stored form to `["",""]` (a new
   `INPUT_REPAIR` action class, since the hidden-test command refuses stdin
   changes by design) or drop the case. The approved statement relaxation is a
   separate `STATEMENT_REPAIR` through the existing role.
3. Only then create `learnlm_contract_rw` and migrate — q3309 first, since its
   signature is already annotated and four of its five cases bind today.

I would not create the contract role yet. It has nothing it can legally do until
one of the two blockers above clears, and a role that exists before its work
does is a privilege sitting idle.

---

```
q1436 CONTRACT_REPAIR = DRY-RUN READY (refused: starter unannotated)
q3309 CONTRACT_REPAIR = NOT STARTED   (blocked: case 4 cannot be expressed)
q963 KEY_REPAIR       = NOT STARTED
ORACLE                = NOT STARTED
APPROVAL              = NOT STARTED
PROMOTION             = NOT STARTED
BATCH                 = FROZEN
RESEED                = NO
```
