# P2.7 — q3309 statement repair verified

**Step 1 of the approved plan is complete.** Two characters of one field of one
question changed, proven across all 2,926 questions. Nothing else in the plan
was started: no input repair, no contract change, no new role, no migration.

---

## Step 1 — pre-write verification (census)

```
q3309 live        2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
q3309 pre-image   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a   verifies
batch             CAPTURED, frozen, 7 members
q264              unchanged
actions           3
fingerprint       8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
```

All six preconditions held, so the phase proceeded.

## Step 2 — the approved statement file

`remediation/q3309_approved_statement.txt` — 980 bytes, sha256
`5bd1b4c63e0a07842a8abe27a95abf3f2e4d8bb9499c717d8e6764ce4b75a5de`.

**Derived from the frozen pre-image, not retyped.** Two replacements against
captured bytes, each asserted to match exactly once before being applied.
Retyping 980 characters of HTML by hand is how an invisible entity or a
collapsed space becomes part of grading truth.

The file was written only after its projected digest was computed and compared
against the signed-off plan:

```
projected  bbd46d58964966f0e96e601c560ff826092a18a09e23c103adb88cd0953bc2cd
matches the approved plan: yes
```

## Step 3 — dry-run review

```
role            learnlm_remediate_rw
pre-image       2395b945…d47a0a
current digest  2395b945…d47a0a        (identical — starting from the capture)
field           content (the ONLY field this command can change)
size            980 -> 980 bytes
```

```diff
-…Constraints: 1 &le; |<i>haystack</i>| &le; 10<sup>4</sup>, 1 &le; |<i>needle</i>| &le; 10<sup>4</sup>, where…
+…Constraints: 0 &le; |<i>haystack</i>| &le; 10<sup>4</sup>, 0 &le; |<i>needle</i>| &le; 10<sup>4</sup>, where…
```

One line in the diff. The `### Examples` section does not appear at all, which
is the evidence that it was untouched.

## Step 4 — applied

```
before digest   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
after digest    bbd46d58964966f0e96e601c560ff826092a18a09e23c103adb88cd0953bc2cd
```

The after-digest equals the projection computed two phases ago, before the file
existed.

## Step 5 — verification

### The action — exactly one new

```
total actions      4
  q963   STATEMENT_REPAIR     2026-08-17 07:09:53
  q17    HIDDEN_TEST_REPAIR   2026-08-17 07:48:12
  q266   HIDDEN_TEST_REPAIR   2026-08-17 11:24:41
  q3309  STATEMENT_REPAIR     2026-08-18 09:05:18   <- new

question           3309
batch              p27-pilot-1
action_class       STATEMENT_REPAIR
operator           Suhas
post_digest        bbd46d58964966f0e96e601c560ff826092a18a09e23c103adb88cd0953bc2cd
linked pre-image   2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
```

### The exact change

```
content                      CHANGED     <- the only one
status                       identical
trust_state                  identical
execution_contract_version   identical
boilerplate_code             identical
hidden_wrapper_code          identical
hidden_test_cases            identical
```

**Two characters, located:**

```
offset 478   '1' -> '0'     the haystack lower bound
offset 524   '1' -> '0'     the needle lower bound
length 980 -> 980
```

Present after the repair, each checked by substring: both relaxed bounds, the
empty-needle rule, the input-format line, the output-format line, the
`### Examples` section, Example 1's output and Example 3's output. Absent after
the repair: both old `1 &le;` bounds. The upper bounds (`&le; 10<sup>4</sup>`)
are untouched — only the two lower ones moved.

### The intended intermediate state

```
case 4 stdin              '\n\n'   unrepaired, as instructed
still refuses under v3    CONTRACT_MISMATCH
contract column           'v1'
```

**q3309's statement now permits the empty case its own rule promises an answer
for, while the stored input still cannot express it.** That disagreement is
deliberate and visible until `INPUT_REPAIR`, in the same way q963's repaired
statement still disagrees with its keys.

### The other six

```
q264    byte-identical to pre-image    <- SAFE control
q1436   byte-identical to pre-image
q1689   byte-identical to pre-image
q17     at 704a1652…893cb5
q266    at 4df2af27…a8f704
q963    at 8da0eb14…7ab688
```

The control has now survived capture, freeze, and four live production writes.

### Batch

```
CAPTURED, frozen, membership [17, 264, 266, 963, 1436, 1689, 3309]
```

### Whole-bank impact

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to baseline, including **zero questions declaring v3** —
no contract moved.

```
prior     8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
current   596c057847ce928362818c8e2c7618f9d9f9be090836ad4c472337e60f93873d
```

Recomputing the whole-bank fingerprint with q3309's content substituted back
from its pre-image, every other value left as production holds it now:

```
q3309 content md5   c12850a8… (before)  ->  17a944df… (now)
reconstructed       8c13c637b972d6fdaf1a611e58daa31f8e6256ea7520633a09f47844b92598a8
                    == the prior baseline, exactly
```

Since the fingerprint covers six fields across all 2,926 questions, this proves
`q3309.content` is the only field in the bank that moved.

**New baseline:**

```
596c057847ce928362818c8e2c7618f9d9f9be090836ad4c472337e60f93873d
```

### Rollback readiness — ready, not executed

```
pre-image verifies         yes
pre-image holds            the ORIGINAL '1 &le;' bounds
restore target             2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
live == recorded post      true (no divergence)
```

Rollback was **not** run; it has still never executed against real data.

---

```
q3309 STATEMENT_REPAIR   = COMPLETE
q3309 INPUT_REPAIR       = NOT STARTED
q3309 CONTRACT_REPAIR    = READY/BLOCKED   (blocked by the input repair)
q1436 STATEMENT_REPAIR   = NOT STARTED
q1436 INPUT_REPAIR       = NOT STARTED
q1436 BOILERPLATE_REPAIR = NOT STARTED
q1436 CONTRACT_REPAIR    = READY/BLOCKED
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```

Next in the approved order: **q1436 STATEMENT_REPAIR** — same role, same
command, the two-character Example-2 sync, projected digest
`a1187744cc637f4173ef0918f02d2a51b147fcb2e14d5d65e9e77649ae835641`. Not started.
