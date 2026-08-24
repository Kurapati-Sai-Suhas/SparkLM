# P2.7 — Remediation plans for q1436 and q3309

**Plans only. Nothing was implemented, no role was created, and production is
byte-identical to where the last phase left it.** Every digest below was
computed in memory from the frozen pre-images.

Three things need your decision before any of this is built — they are marked
**⚠ DECISION** and collected in §11.

---

## 1. q1436 — BOILERPLATE_REPAIR (approved)

**Column: `boilerplate_code`.**

```diff
 class Solution:
-    def destCity(self, paths):
+    def destCity(self, paths: list[list[str]]):
         # Write your code here
         pass
```

Exactly the annotation you approved, and nothing else. No return annotation:
the adapter binds *inputs* from the signature and never reads the return type,
so `-> str` would be a change with no effect on grading — and every character
of a starter is code a learner is handed.

```
before  0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
after   8090b3d3c6f3cb2e41510a6db4fc37bd48da378b592b212644cd1b297aa1d65f
```

**⚠ DECISION 1 — the annotation alone already unblocks the migration.** After
this step all four cases bind cleanly, *including the cyclic case 2*:

```
case 2  OK  [[["B","C"],["D","B"],["C","D"]]]   ->  paths = [['B','C'],['D','B'],['C','D']]
```

The feasibility gate is **structural**: it proves the stored input can be
delivered as declared, not that the input is a sensible question. It will not
stop a contract repair on a question whose case 2 has no answer. The input
repair is required by your judgement, not by the tool — so the order below is an
instruction, not an enforced constraint.

## 2. q1436 — INPUT_REPAIR (case 2 replacement proposed)

**Column: `hidden_test_cases`** (the `stdin` of case 2 only).

```
before  [["B","C"],["D","B"],["C","D"]]     cities B,C,D · sources B,C,D · destinations: NONE
after   [["B","D"],["A","B"],["C","D"]]     cities A,B,C,D · sources A,B,C · destinations: [D]
expected_output  'D'  — UNCHANGED
```

### Proof there is exactly one destination

A destination city is one with no outgoing edge. In the proposal the sources are
`{B, A, C}` and the cities are `{A, B, C, D}`, so `D` is the only city absent
from the source set — one destination, and it is `D`, which is what the stored
key already says. Every path terminates there: `A→B→D` and `C→D`.

The statement's guarantee ("the graph is guaranteed to have a destination city")
is satisfied, where the current input violates it: `B→C→D→B` is a cycle in which
every city has an outgoing edge, so no destination exists and the stored key `D`
is not derivable from the input.

### Why this shape

It is the smallest edit that both breaks the cycle and keeps the statement's
Example-2 explanation — *"There are multiple paths but all of them end at D"* —
literally true: two distinct paths (`A→B→D`, `C→D`), both terminating at `D`.
Two of three pairs change; pair 3 is untouched.

Two alternatives, with their projected final digests, so you can choose without
another round trip:

| candidate | pairs edited | paths | note | final digest (after all 3 steps) |
|---|---|---|---|---|
| **`[["B","D"],["A","B"],["C","D"]]`** *(recommended)* | 2 of 3 | `A→B→D`, `C→D` | keeps "multiple paths" true | `f2b9bcf7…7cb5a74` |
| `[["B","C"],["A","B"],["C","D"]]` | **1 of 3** | `A→B→C→D` | smallest possible edit; a single chain, so "multiple paths" would be false | `dbbb5031…3cffb091` |
| `[["B","D"],["C","D"],["A","B"]]` | 3 of 3 | `B→D`, `C→D`, `A→B` | also defeats a "return the last edge's target" solver | `66422314…a1c59863` |

An observation, not a recommendation: **no case in this suite defeats a "return
the last pair's destination" heuristic** — not case 1, not case 4, and not the
current case 2. The third candidate would be the only one that does.
Strengthening the suite is a separate action class and is not proposed here.

```
before  8090b3d3c6f3cb2e41510a6db4fc37bd48da378b592b212644cd1b297aa1d65f
after   996035158f331ba8d7ce6a8f47386d3e1b68be8a4c939ef5863652944907a83f   (recommended candidate)

case 2 case_identity  ca98a350327455428a172ca862345da3082fc4e2c7a76153dca058684df5a788
                  ->  991a0528a61ec16f6efa1819ca1696a61a171236e9171c18891cd7f60181c970
```

The case identity **changes** — that is the definition of an input repair, and
exactly why the hidden-test command refuses to do it (§7).

**⚠ DECISION 2 — the statement still shows the cyclic example.** q1436's
statement carries the same defective input in its own Example 2:

```
**Input 2:** `[["B","C"],["D","B"],["C","D"]]`
**Output 2:** `D`
**Explanation:** There are multiple paths but all of them end at D
```

Your sign-off left q1436's statement unchanged, so this plan does not touch it.
Repairing only the hidden case leaves a learner reading an example whose input
has no destination city while the grader tests a different, valid one. The
recommended candidate is chosen so that syncing the example later is a pure
input swap with the explanation left as written.

## 3. q1436 — CONTRACT_REPAIR

**Column: `execution_contract_version`.** `'v1'` → `'v3'`. The command exists and
is mutation-tested; only the role is missing.

Projected bindings after steps 1 and 2, from the adapter:

```
case 1  [[["London","New York"],["New York","Paris"],["Paris","Rome"]]]
case 2  [[["B","D"],["A","B"],["C","D"]]]
case 3  [[["A","Z"]]]
case 4  [[["A","B"],["A","C"],["B","D"],["C","D"]]]
```

Every case binds to **one** decoded `list[list[str]]` argument, with no warnings
— which is what lifts the refusal from the last phase.

```
before  996035158f331ba8d7ce6a8f47386d3e1b68be8a4c939ef5863652944907a83f
after   f2b9bcf797ec2bc87ce0a1ac8fcb1662cba952ca3cf9335110c99e5367cb5a74
```

## 4. q3309 — STATEMENT_REPAIR (approved)

**Column: `content`.** A one-character change, verified as such:

```diff
- Constraints: 1 &le; |<i>haystack</i>| &le; 10<sup>4</sup>, 1 &le; |<i>needle</i>| &le; 10<sup>4</sup>, …
+ Constraints: 1 &le; |<i>haystack</i>| &le; 10<sup>4</sup>, 0 &le; |<i>needle</i>| &le; 10<sup>4</sup>, …
```

```
characters changed  1     length 980 -> 980 (unchanged)
before  2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
after   fc55dd9b914ddae2e64d641a0a9c35fc04dfd60c6fac12d2c08c37fd5f5a6c49
```

The empty-needle rule already in the statement ("If needle is an empty string,
return 0") is preserved; this only removes the constraint that contradicted it.

**⚠ DECISION 3 — the haystack bound still excludes the repaired case 4.** The
approved repair relaxes the *needle* bound only, but the approved case-4 input
`["",""]` supplies an **empty haystack** too, and the statement will still say
`1 ≤ |haystack|`. The contradiction the repair exists to remove would survive in
a second place. Two ways to close it, both leaving the key `0` correct:

| option | statement | case 4 stdin | digest of the input step alone |
|---|---|---|---|
| **a.** relax the haystack bound as well | `0 &le; |haystack|` and `0 &le; |needle|` | `["",""]` (as approved) | `7d0f35a8…2914b113` |
| **b.** keep `1 ≤ |haystack|`, use a non-empty haystack | needle bound only (as approved) | `["abc",""]` | `0e9bbdbc…d926dd11` |

`strStr("", "")` and `strStr("abc", "")` both return `0`, so the stored
`expected_output` is untouched either way. The plan below assumes the approved
`["",""]`; option (a) would be a one-character addition to the same statement
repair, and I would take it rather than weaken the case the empty-needle rule
exists to exercise.

## 5. q3309 — INPUT_REPAIR (case 4)

**Column: `hidden_test_cases`** (the `stdin` of case 4 only).

```
before  '\n\n'          two blank lines
after   '["",""]'       the canonical v3 envelope, stored as text
expected_output  '0'  — UNCHANGED
```

Why this is the canonical form: with arity 2 the adapter tries N non-blank lines
(zero here), then a JSON array of exactly N elements — `["",""]` decodes to
`['', '']`, each element validated against the declared `str` and passed through
untouched. It is the *only* stored form that can deliver two empty strings; two
blank lines cannot, because blank lines are filtered before mapping.

```
before  fc55dd9b914ddae2e64d641a0a9c35fc04dfd60c6fac12d2c08c37fd5f5a6c49
after   c279ceb4099e24b61f975b30a1395cac884af6ded67bc6344f34d2a65b4911a3

case 4 case_identity  e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
                  ->  439083f38956ba51ece90631552c6ea23c5c29570d3d5710e408e77e01ba7375
```

(The before-identity is the digest of the empty string — the current input
normalises to nothing at all, which is the defect in one line.)

## 6. q3309 — CONTRACT_REPAIR

**Column: `execution_contract_version`.** `'v1'` → `'v3'`.

```
case 1  ["hello","ll"]              -> ('hello', 'll')
case 2  ["aaaaa","bba"]             -> ('aaaaa', 'bba')
case 3  ["abc","a"]                 -> ('abc', 'a')
case 4  ["",""]                     -> ('', '')
case 5  ["mississippi","issip"]     -> ('mississippi', 'issip')
```

All five bind cleanly, no warnings. Worth restating: **all five are broken
today**, not just case 4 — v1 cannot read the two-line form either.

```
before  c279ceb4099e24b61f975b30a1395cac884af6ded67bc6344f34d2a65b4911a3
after   b16032e4b51e5b6136abcc262adf2529037d5bb0e50c4c6a2a65614fb3e3a296
```

## 7. Required privilege per action

| # | action | column | role | command | exists? |
|---|---|---|---|---|---|
| 1 | q1436 BOILERPLATE_REPAIR | `boilerplate_code` | `learnlm_boilerplate_rw` | `remediate_boilerplate` | **neither** |
| 2 | q1436 INPUT_REPAIR | `hidden_test_cases` | `learnlm_hidden_test_rw` | `remediate_inputs` | role yes, **command no** |
| 3 | q1436 CONTRACT_REPAIR | `execution_contract_version` | `learnlm_contract_rw` | `remediate_contract` | command yes, **role no** |
| 4 | q3309 STATEMENT_REPAIR | `content` | `learnlm_remediate_rw` | `remediate_statement` | **both exist** |
| 5 | q3309 INPUT_REPAIR | `hidden_test_cases` | `learnlm_hidden_test_rw` | `remediate_inputs` | role yes, **command no** |
| 6 | q3309 CONTRACT_REPAIR | `execution_contract_version` | `learnlm_contract_rw` | `remediate_contract` | command yes, **role no** |

Three notes on what this table implies.

**Input repair is the first action class the database cannot separate.** It
writes `hidden_test_cases` — the same column as hidden-test repair — so one role
covers both and the boundary has to be the *command*: `remediate_hidden_tests`
holds every `stdin` fixed and may rewrite `expected_output`; `remediate_inputs`
must be its exact mirror, holding every `expected_output` fixed and may rewrite
`stdin`, with no case added or removed by either. Where the other classes get a
privilege boundary, this pair gets an invariant, and I would say so plainly in
its docstring rather than imply the database is enforcing it.

**`RemediationAction` has no `INPUT_REPAIR` class.** The model defines
`CONTRACT_REPAIR`, `STATEMENT_REPAIR`, `BOILERPLATE_REPAIR`,
`HIDDEN_TEST_REPAIR`, `EXPECTED_OUTPUT_REPAIR`, `MANUAL_REVIEW`,
`COMPLETE_REBUILD`, `ROLLBACK`. Adding one is a model change plus migration
0045 — trivial at the database level, but it is a schema change and should
arrive as its own reviewed step rather than inside a repair phase.

**No role holds `boilerplate_code` UPDATE today, deliberately.** That column is
the code every learner is handed; a fifth role for it is the right shape, and it
should not appear as a side effect of a contract phase.

## 8. Projected digests

### q1436

```
current / pre-image              0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
after BOILERPLATE_REPAIR         8090b3d3c6f3cb2e41510a6db4fc37bd48da378b592b212644cd1b297aa1d65f
after INPUT_REPAIR               996035158f331ba8d7ce6a8f47386d3e1b68be8a4c939ef5863652944907a83f
after CONTRACT_REPAIR (final)    f2b9bcf797ec2bc87ce0a1ac8fcb1662cba952ca3cf9335110c99e5367cb5a74
```

### q3309

```
current / pre-image              2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
after STATEMENT_REPAIR           fc55dd9b914ddae2e64d641a0a9c35fc04dfd60c6fac12d2c08c37fd5f5a6c49
after INPUT_REPAIR               c279ceb4099e24b61f975b30a1395cac884af6ded67bc6344f34d2a65b4911a3
after CONTRACT_REPAIR (final)    b16032e4b51e5b6136abcc262adf2529037d5bb0e50c4c6a2a65614fb3e3a296
```

**The intermediate digests are order-dependent; the final one is not.** Verified
rather than assumed — reaching q1436's end state boilerplate-first and
input-first both produce `f2b9bcf7…7cb5a74`, because `state_digest` is a
function of the final field values, not of the path taken. If you approve a
different case-2 candidate, only the last two digests move (§2).

Each projection is what the corresponding command will print as
`projected after` in its dry-run; a mismatch there means production moved
between now and then, and the command should be stopped rather than confirmed.

## 9. Production unchanged

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

No role was created, no command was built, and the only database traffic in this
phase was `learnlm_census_ro` reads.

## 10. Recommended execution order

Ordered so that each step is verifiable on its own and the riskiest new
machinery arrives last:

1. **q3309 STATEMENT_REPAIR** — needs nothing new. Existing role, existing
   command, one character. (Resolve **DECISION 3** first, since it may make this
   a two-character change.)
2. **Model change + migration 0045** — add `CLASS_INPUT_REPAIR`. Schema only, no
   data.
3. **Build `remediate_inputs`** — the mirror command, with tests and a mutation
   sweep, then dry-run both input repairs.
4. **Apply q3309 case 4**, verify, then **apply q1436 case 2** (**DECISION 1**
   candidate), verify. One at a time, as with q17 and q266.
5. **Build `remediate_boilerplate` + create `learnlm_boilerplate_rw`**, dry-run,
   apply q1436's annotation, verify.
6. **Create `learnlm_contract_rw`**, then migrate **q3309 → v3** and verify
   before touching q1436 — q3309's signature is already correct, so it is the
   cleaner first migration.
7. **Migrate q1436 → v3**, verify.

q3309 leads throughout: its chain is shorter, its signature already correct, and
its statement repair can begin immediately. q1436 needs the most new machinery
and carries the one remaining open question about its own statement
(**DECISION 2**).

Nothing above starts an oracle, creates a reference, approves, promotes or
reseeds. Both questions remain DRAFT/UNVERIFIED throughout, and every step is
rollback-covered by the existing frozen pre-images.

## 11. Decisions needed

1. **q1436 case-2 candidate** — the recommended two-edit graph, the one-edit
   chain, or the three-edit strictest one (§2).
2. **q1436 statement Example 2** — leave it showing the cyclic input, or
   authorise a `STATEMENT_REPAIR` to sync it (§2).
3. **q3309 haystack bound** — also relax it to `0 ≤`, or use `["abc",""]` for
   case 4 instead (§4).

---

```
q1436 BOILERPLATE_REPAIR = PLANNED, NOT BUILT
q1436 INPUT_REPAIR       = PLANNED, NOT BUILT
q1436 CONTRACT_REPAIR    = READY, BLOCKED on the two above
q3309 STATEMENT_REPAIR   = PLANNED, buildable today
q3309 INPUT_REPAIR       = PLANNED, NOT BUILT
q3309 CONTRACT_REPAIR    = READY, BLOCKED on the input repair
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```
