# P2.7 — q1436 boilerplate repair verified

**The last blocker on q1436's contract migration is cleared.** One language's
starter of one question changed, by one annotation. Both contract migrations are
now unblocked and neither has been performed.

---

## A. The role

Created by you; verified here **as the role itself**, not through the owner.

```
endpoint       ep-blue-hat-aj7p2x8v-pooler   (the canonical production endpoint)
database       neondb                        server 17.10 (29ad1b7)
current_user   learnlm_boilerplate_rw
session_user   learnlm_boilerplate_rw        — not a SET ROLE over something wider

LOGIN True · SUPERUSER False · CREATEDB False · CREATEROLE False
REPLICATION False · BYPASSRLS False · memberships NONE
```

Every column of `groups_question`, asked of the database as the role:

```
table-level:  SELECT=T  INSERT=F  UPDATE=F  DELETE=F  TRUNCATE=F

boilerplate_code             T   <- the only one
id, title, content, base_difficulty, topic_id, hidden_test_cases,
hidden_wrapper_code, execution_contract_version, status, trust_state    all F
```

```
groups_remediationaction    SELECT, INSERT
groups_remediationbatch     SELECT
groups_questionpreimage     SELECT          <- cannot modify its own undo
groups_questionapproval     (none)
groups_referencesolution    (none)
groups_oracleexecution      (none)
groups_codesubmission       (none)
```

Neither over- nor under-granted. The other four aliases still connect as
`learnlm_census_ro`, `learnlm_preimage_rw`, `learnlm_remediate_rw` and
`learnlm_hidden_test_rw` — nothing was broadened.

**One correction, on my own tooling.** The first run of this check reported full
privileges on approvals, references, oracle executions and submissions. That was
a bug in my verification script, not an over-grant: it used
`cursor.execute(...) or cursor.fetchone()[0]` inside a comprehension, and
**psycopg 3 returns the Cursor from `execute()`**, which is truthy — so the `or`
short-circuited before ever reading the answer, and every privilege looked held.
Re-measured with an explicit execute-then-fetch: all four are empty. The script
is fixed; the reading it produced was never true of the database.

## B. The alias

```
aliases configured   ['boilerplate', 'default', 'hiddentest', 'preimage', 'remediate']
separate credentials from default: True
```

Before the credentials existed, `--alias boilerplate` raised
`ConnectionDoesNotExist` — it fails loudly rather than falling back to the census
connection. `POSTGRES_*`, `PREIMAGE_*`, `REMEDIATE_*` and `HIDDENTEST_*` were not
touched.

## C. Real-role dry-run

Identical to the census-role dry-run in every line except the role in the header:

```
current digest  4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
projected after 333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
field           boilerplate_code['python']
languages       ['python'] -> ['python']  (unchanged)
size            90 -> 107 bytes
annotations changed:  destCity(paths): (none) -> list[list[str]]

-    def destCity(self, paths):
+    def destCity(self, paths: list[list[str]]):
```

`--expect-digest` was passed, so the command would have refused had q1436 moved
between the approval and the write.

## D. Applied

```
before digest   4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
after digest    333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
```

Equal to the projection computed before the role existed.

## E. Verification

### The action — exactly one new

```
total actions      8            BOILERPLATE_REPAIR actions: exactly 1

question           1436
action_class       BOILERPLATE_REPAIR
batch              p27-pilot-1
operator           Suhas
post_digest        333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
linked pre-image   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
```

### Fields, against the frozen pre-image

```
boilerplate_code             CHANGED — this repair
content                      CHANGED — the statement repair, 2 characters
hidden_test_cases            CHANGED — the input repair, case 2 stdin
status                       identical
trust_state                  identical
execution_contract_version   identical   'v1'
hidden_wrapper_code          identical
```

Three of seven captured fields have now moved on q1436, each by its own
reviewed, verified action. The three that decide *trust* have not moved at all.

### The starter

```
class Solution:
    def destCity(self, paths: list[list[str]]):
        # Write your code here
        pass
```

```
languages                ['python'] -> ['python']   unchanged
old declaration present  False        new declaration present  True
lines 4 -> 4             bytes 90 -> 107
return annotation added  no           import added  no
declared signature       destCity(paths: list[list[str]])
```

### v3 feasibility — through the real adapter

```
case 1  OK  warnings=()  1 argument, list, == json.loads(stdin)
case 2  OK  warnings=()  1 argument, list, == json.loads(stdin)
case 3  OK  warnings=()  1 argument, list, == json.loads(stdin)
case 4  OK  warnings=()  1 argument, list, == json.loads(stdin)

[[["London","New York"],["New York","Paris"],["Paris","Rome"]]]
[[["B","D"],["A","B"],["C","D"]]]
[[["A","Z"]]]
[[["A","B"],["A","C"],["B","D"],["C","D"]]]
```

Zero `undeclared_parameter_type` warnings, one decoded list per case, no input
mutated. **q1436's contract migration is no longer blocked.** Nothing was
executed on Judge0.

### The rest of the bank

```
q264, q1689   byte-identical to their pre-images
q963, q17, q266, q3309   at their recorded digests
batch         CAPTURED, frozen, 7 members

1. q963  STATEMENT_REPAIR      5. q1436  STATEMENT_REPAIR
2. q17   HIDDEN_TEST_REPAIR    6. q3309  INPUT_REPAIR
3. q266  HIDDEN_TEST_REPAIR    7. q1436  INPUT_REPAIR
4. q3309 STATEMENT_REPAIR      8. q1436  BOILERPLATE_REPAIR
```

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

**The six-field bank fingerprint is unchanged at
`296627192435888cd8791bd2bfa2e73428cab8ae343bd34efb27a977cb6e6047`** — and that
is expected, not evidence of a no-op: that fingerprint covers `id`,
`hidden_test_cases`, `content`, `status`, `trust_state` and
`execution_contract_version`, and `boilerplate_code` is not among them. The
starter change is confined instead by a direct comparison against every
captured starter in the batch:

```
starters differing from their capture:  1     (q1436, as intended)
whole-bank boilerplate fingerprint:     f94491c78c8e3fc10c6e06745d81d624
```

That count is the proof for this column, and it is recorded here as the new
starter baseline.

## F. Mutation — 26 killed / 27, 0 real survivors

Extended for this phase's attack list, with one mutant per invariant rather than
one for all of them: renamed method, renamed parameter, edited body, and
added/removed import each got a mutant that blinds the structural comparison to
exactly that class — all killed. Plus hidden-test-role reuse and contract-role
reuse (killed), alongside wrong column, broad UPDATE, statement-role reuse,
missing pre-image, missing frozen check, missing row lock, wrong alias,
transaction on default, language dropped, second language changed, audit removed
and post-write verification removed. One planted equivalent survived by design.

## G. Full regression

```
2294 passed, 0 failed, 0 errors   (groups, common, learning — 3m14s)
  boilerplate remediation   41
```

## H. Rollback readiness — ready, not executed

```
pre-image verifies      yes
pre-image starter       'def destCity(self, paths):'  — the ORIGINAL
rollback target         0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
live == recorded post   true (no divergence)
batch                   frozen
q1436 status / trust    DRAFT / UNVERIFIED
```

Rolling q1436 back would undo all three of its repairs at once — the pre-image
predates every one. Rollback has still never run against real data.

---

```
q1436 BOILERPLATE_REPAIR = COMPLETE + VERIFIED
q3309 CONTRACT_REPAIR    = READY
q1436 CONTRACT_REPAIR    = READY
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```

Both contract migrations are now unblocked and both need the same thing:
`learnlm_contract_rw`, which does not exist. The command is built and
mutation-tested; §B of
[P2_7_CONTRACT_REPAIR_PLANS.md](LearnLM/docs/P2_7_CONTRACT_REPAIR_PLANS.md)
carries the grants. q3309 first, then q1436, one at a time.
