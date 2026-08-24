# P2.7 — Approved remediation plan: q1436 and q3309

All three outstanding decisions are signed off. This supersedes the projections
in [P2_7_CONTRACT_REPAIR_PLANS.md](LearnLM/docs/P2_7_CONTRACT_REPAIR_PLANS.md) —
q1436 now has **four** steps rather than three, and every digest after its first
step has moved.

**Production writes this phase: 0.** Nothing was built, no role was created, no
migration was written.

---

## 1. q1436 — Example-2 statement diff

**Column: `content`.** The cyclic input appears **exactly once** in the
statement, and the proposed input appears **zero** times, so the replacement is
unambiguous.

```diff
 **Input 1:** `[["London","New York"],["New York","Paris"],["Paris","Rome"]]`
 **Output 1:** `Rome`
 **Explanation:** Rome is the destination of all paths

-**Input 2:** `[["B","C"],["D","B"],["C","D"]]`
+**Input 2:** `[["B","D"],["A","B"],["C","D"]]`
 **Output 2:** `D`
 **Explanation:** There are multiple paths but all of them end at D

 **Input 3:** `[["A","Z"]]`
```

```
characters differing   2      (C->D in pair 1, D->A in pair 2)
length                 1048 -> 1048   unchanged
Output 2 line          unchanged, verified present after the edit
Explanation line       unchanged, verified present after the edit
```

Two characters. Nothing else in the statement is touched — not the HTML block,
not Example 1 or 3, not the constraints, not the input/output format lines.

## 2. q1436 — approved input-repair payload

**Column: `hidden_test_cases`**, the `stdin` of case 2 only.

```json
[
  {"stdin": "[[\"London\",\"New York\"],[\"New York\",\"Paris\"],[\"Paris\",\"Rome\"]]", "expected_output": "Rome"},
  {"stdin": "[[\"B\",\"D\"],[\"A\",\"B\"],[\"C\",\"D\"]]",                               "expected_output": "D"},
  {"stdin": "[[\"A\",\"Z\"]]",                                                           "expected_output": "Z"},
  {"stdin": "[[\"A\",\"B\"],[\"A\",\"C\"],[\"B\",\"D\"],[\"C\",\"D\"]]",                 "expected_output": "D"}
]
```

```
case 2 stdin   [["B","C"],["D","B"],["C","D"]]  ->  [["B","D"],["A","B"],["C","D"]]
expected_output  "D"  — unchanged, as approved
case_identity  ca98a35032745542…  ->  991a0528a61ec16f…
cases 1, 3, 4  byte-identical (derived from the pre-image, not retyped)
```

After both steps the statement's Example 2 and the graded case 2 hold the **same
input** — verified, not assumed.

## 3. q1436 — projected digests

```
step 0  current / pre-image     0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
step a  STATEMENT_REPAIR        a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641
step b  INPUT_REPAIR            4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
step c  BOILERPLATE_REPAIR      333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
step d  CONTRACT_REPAIR         0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a
```

Bindings at the final state — every case one decoded `list[list[str]]`
argument, no warnings:

```
case 1  [[["London","New York"],["New York","Paris"],["Paris","Rome"]]]
case 2  [[["B","D"],["A","B"],["C","D"]]]
case 3  [[["A","Z"]]]
case 4  [[["A","B"],["A","C"],["B","D"],["C","D"]]]
```

**The intermediate digests assume this exact order; the final one does not
depend on it.** `state_digest` is a function of the final field values, verified
previously by reaching the same end state two ways.

## 4. q3309 — complete statement diff

**Column: `content`.** Both bounds, one line:

```diff
- Constraints: 1 &le; |<i>haystack</i>| &le; 10<sup>4</sup>, 1 &le; |<i>needle</i>| &le; 10<sup>4</sup>, where |<i>string</i>| denotes the length of <i>string</i>.
+ Constraints: 0 &le; |<i>haystack</i>| &le; 10<sup>4</sup>, 0 &le; |<i>needle</i>| &le; 10<sup>4</sup>, where |<i>string</i>| denotes the length of <i>string</i>.
```

```
characters differing   2      (both leading 1 -> 0)
length                 980 -> 980   unchanged
each bound text        occurs exactly once, so both replacements are unambiguous
empty-needle rule      "If needle is an empty string, return 0" — present, unchanged
```

The statement now permits the case it already promised an answer for. Nothing
else changes: the examples, the input/output format lines and the upper bounds
are untouched.

## 5. q3309 — approved input-repair payload

**Column: `hidden_test_cases`**, the `stdin` of case 4 only.

```json
[
  {"stdin": "hello\nll\n",           "expected_output": "2"},
  {"stdin": "aaaaa\nbba\n",          "expected_output": "-1"},
  {"stdin": "abc\na\n",              "expected_output": "0"},
  {"stdin": "[\"\",\"\"]",           "expected_output": "0"},
  {"stdin": "mississippi\nissip\n",  "expected_output": "4"}
]
```

```
case 4 stdin   "\n\n"  ->  "[\"\",\"\"]"
expected_output  "0"  — unchanged, as approved
case_identity  e3b0c44298fc1c14…  ->  439083f38956ba51…
cases 1, 2, 3, 5  byte-identical
```

The before-identity is the digest of the empty string: the current input
normalises to nothing at all, which is the defect in one line.

## 6. q3309 — projected digests

```
step 0  current / pre-image     2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
step a  STATEMENT_REPAIR        bbd46d58964966f0e96e601c560ff826092a18a09e23c103adb88cd0953bc2cd
step b  INPUT_REPAIR            1125858c76abea1d22b93d1d6d0491e84ca46d09deacee273194cdfa811c63fb
step c  CONTRACT_REPAIR         8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6
```

Bindings at the final state — all five, no warnings:

```
case 1  ["hello","ll"]          case 4  ["",""]
case 2  ["aaaaa","bba"]         case 5  ["mississippi","issip"]
case 3  ["abc","a"]
```

## 7. Dependency order

Hard dependencies (a step is meaningless or unsafe before the one above it):

```
q1436:  STATEMENT ─┐
                   ├─ both must land before CONTRACT is honest
        INPUT ─────┤        (statement/case agreement, and a real answer)
        BOILERPLATE ┘       ← the only HARD blocker: without it the
                              adapter guesses and the command refuses

q3309:  STATEMENT ──── independent, can go first at any time
        INPUT ───────── HARD blocker for CONTRACT: case 4 cannot bind
        CONTRACT
```

Precisely which are enforced and which are judgement:

| step | blocked by the tool? |
|---|---|
| q1436 CONTRACT before BOILERPLATE | **yes** — the feasibility gate refuses on `undeclared_parameter_type` |
| q3309 CONTRACT before INPUT | **yes** — case 4 refuses with `CONTRACT_MISMATCH` |
| q1436 CONTRACT before INPUT | **no** — the cyclic input binds structurally; only judgement forbids it |
| q1436 STATEMENT before/after INPUT | **no** — different columns, no interaction |

Recommended execution sequence, riskiest new machinery last:

1. **q3309 STATEMENT_REPAIR** — existing role, existing command, buildable today.
2. **q1436 STATEMENT_REPAIR** — same role and command, immediately after.
3. **Migration 0045** — `CLASS_INPUT_REPAIR` only (§9).
4. **Build `remediate_inputs`** — tests + mutation sweep, then dry-run both.
5. **Apply q3309 case 4**, verify; then **q1436 case 2**, verify. One at a time.
6. **Build `remediate_boilerplate` + `learnlm_boilerplate_rw`**; dry-run; apply
   q1436's annotation; verify.
7. **Create `learnlm_contract_rw`**; migrate **q3309 → v3**, verify; then
   **q1436 → v3**, verify.

Both statement repairs come first because they need nothing new — the same
statement-first ordering the pipeline design fixed, and the same role that
repaired q963.

## 8. Role and column per step

| # | step | column | role | command | status |
|---|---|---|---|---|---|
| 1 | q3309 STATEMENT_REPAIR | `content` | `learnlm_remediate_rw` | `remediate_statement` | **both exist** |
| 2 | q1436 STATEMENT_REPAIR | `content` | `learnlm_remediate_rw` | `remediate_statement` | **both exist** |
| 3 | q3309 INPUT_REPAIR | `hidden_test_cases` | `learnlm_hidden_test_rw` | `remediate_inputs` | role exists, **command to build** |
| 4 | q1436 INPUT_REPAIR | `hidden_test_cases` | `learnlm_hidden_test_rw` | `remediate_inputs` | role exists, **command to build** |
| 5 | q1436 BOILERPLATE_REPAIR | `boilerplate_code` | `learnlm_boilerplate_rw` | `remediate_boilerplate` | **neither exists** |
| 6 | q3309 CONTRACT_REPAIR | `execution_contract_version` | `learnlm_contract_rw` | `remediate_contract` | command exists, **role to create** |
| 7 | q1436 CONTRACT_REPAIR | `execution_contract_version` | `learnlm_contract_rw` | `remediate_contract` | command exists, **role to create** |

Four columns, four roles, and each role's forbidden list must name the other
three. The one exception is stated plainly: **input repair and hidden-test
repair share a column**, so the database cannot separate them — the boundary is
the command's invariant (`remediate_inputs` holds every `expected_output`
fixed; `remediate_hidden_tests` holds every `stdin` fixed; neither adds or
removes a case), and that will be said in the docstring rather than implied to
be a grant.

## 9. Migration 0045 — scope confirmed

**Yes: adding `INPUT_REPAIR` to the audit vocabulary is the only schema change
this plan needs.** Verified rather than assumed:

```
makemigrations --check --dry-run        "No changes detected"   (exit 0)
latest migration                        0044_pre_image_rollback
action_class max_length                 32   len("INPUT_REPAIR") = 12  -> fits
execution_contract_version max_length   8    "v3" needs 2         -> fits
CAPTURED_FIELDS covers content, hidden_test_cases,
  boilerplate_code, execution_contract_version                   -> True
```

So 0045 is a **state-only `AlterField`** on `RemediationAction.action_class`
(a `choices` change emits no DDL for a `CharField`). No column is added, widened
or re-typed, and no data migrates.

Two consequences worth stating:

- **Rollback already covers every column this plan writes.** All four are in
  `CAPTURED_FIELDS`, so the frozen pre-images can restore any step — no
  pre-image schema change, and `PRE_IMAGE_SCHEMA_VERSION` stays 1.
- **Recording an input repair under an existing class would avoid the migration
  and should not be done.** `HIDDEN_TEST_REPAIR` means "the stored answer form
  changed" and `EXPECTED_OUTPUT_REPAIR` means "the answer changed"; using either
  for a changed *input* would make the audit trail describe the one thing this
  batch has been most careful to keep separate.

## 10. Production unchanged

```
batch          p27-pilot-1  CAPTURED, frozen, 7 members
q17   704a1652…   q264  396d211e…   q266  4df2af27…   q963  8da0eb14…
q1436 0d442588…  (contract column 'v1')
q1689 dec99939…
q3309 2395b945…  (contract column 'v1')
actions        3 — q17 + q266 HIDDEN_TEST_REPAIR, q963 STATEMENT_REPAIR
questions declaring v3   0
fingerprint    8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
```

Both questions still match their pre-images exactly. The only database traffic
this phase was `learnlm_census_ro` reads; every digest above was computed in
memory from the captured state.

---

```
q1436 STATEMENT_REPAIR   = PLANNED
q1436 BOILERPLATE_REPAIR = PLANNED
q1436 INPUT_REPAIR       = PLANNED
q1436 CONTRACT_REPAIR    = READY/BLOCKED
q3309 STATEMENT_REPAIR   = PLANNED
q3309 INPUT_REPAIR       = PLANNED
q3309 CONTRACT_REPAIR    = READY/BLOCKED
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
PRODUCTION WRITES        = 0 this phase
RESEED                   = NO
```
