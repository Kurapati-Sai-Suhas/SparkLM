# P2.7 — q3309 suite expansion applied and verified

Five cases became twelve. **Every structural blocker on the quality gate is
gone**; what remains is the mutant work, which is measurement, not data. The
oracle has still not run.

---

## Final review before the write

Re-derived from live state and the plan file, not carried over from the previous
report:

```
q3309 digest      8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6
projected         ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e   == approved
preservation      5/5 — stdin, expected_output and explanation unchanged;
                  the only key added is `category`, none lost
deletions         0        reordering  none (existing 1-5, additions 6-12)
duplicate ids     none     normalised duplicates  none
required missing  none
```

The input-contract judgement, re-printed because it is the claim that decides
what the gate demands:

```
accepts_empty_input  True    the repaired statement permits |s| = 0
is_sequence          True    a string is a sequence of characters
has_size_bounds      True    0 ≤ |s| ≤ 10^4 is stated
allows_duplicates    True    characters repeat; overlapping matches follow
order_sensitive      True    'abc' ≠ 'cba'
numeric              False   → negative_values and zero NOT applicable
overflow_sensitive   False   → NOT applicable
```

## Migration 0046

Already applied when this phase began — recorded at 2026-08-19 12:32:10 UTC,
one row, nothing beyond it. My `migrate` call was a no-op ("No migrations to
apply"), which is the right outcome: the census role could not have written that
row, so it came from the DDL connection.

```
[X] 0044_pre_image_rollback   [X] 0045_input_repair_action_class
[X] 0046_suite_expansion_action_class
action_class  character varying(32)   — unchanged, as a state-only migration should leave it
vocabulary    …, INPUT_REPAIR, SUITE_EXPANSION, MANUAL_REVIEW, …
```

## The write

```
before digest   8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6
after digest    ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e
```

Equal to the projection. The command's closing warning is worth repeating:

> Any oracle evidence recorded against the OLD suite is now incomplete: the new
> cases have no executions.

Nothing was recorded against the old suite, so nothing was invalidated — which
is exactly why the expansion was sequenced before the oracle run.

## Verification

### The suite — 12 cases

```
EXISTING FIVE — stdin and expected_output unchanged, one key added
  1  'hello\nll\n'           → '2'    [typical]
  2  'aaaaa\nbba\n'          → '-1'   [no_match]
  3  'abc\na\n'              → '0'    [match_at_start]
  4  '["",""]'               → '0'    [minimum_boundary]
  5  'mississippi\nissip\n'  → '4'    [overlapping_prefixes]

THE SEVEN NEW — each matches the plan byte for byte
  6  '["","a"]'              → '-1'   [empty_input]
  7  'a\na\n'                → '0'    [singleton]
  8  10^4-char haystack      → '9998' [maximum_boundary]
  9  'aaaa\naa\n'            → '0'    [duplicate_values]
 10  'abcabc\nabc\n'         → '0'    [first_occurrence]
 11  'abc\nabcd\n'           → '-1'   [needle_longer_than_haystack]
 12  'abcab\nabd\n'          → '-1'   [partial_match_backtrack]

12/12 bind under v3 with zero warnings
required categories missing: none      duplicates: none
every existing case has exactly {stdin, expected_output, explanation, category}
```

### Fields, against the frozen pre-image

```
content                      CHANGED (statement repair, earlier phase)
execution_contract_version   CHANGED (contract repair, earlier phase)
hidden_test_cases            CHANGED (input repair + this expansion)
status                       identical      DRAFT
trust_state                  identical      UNVERIFIED
boilerplate_code             identical
hidden_wrapper_code          identical
```

Four of seven captured fields have now moved on q3309, each by its own reviewed
action written by its own role. The two that decide trust have not moved at all.

### The action

```
total actions    12        SUITE_EXPANSION: exactly 1
question 3309 · operator Suhas
post_digest      ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e   == the final suite
linked pre-image 2395b94572381243f2a9b99161349f2290e937244fbd827a5b76fc8dc0d47a0a
```

### Rollback

```
pre-image verifies   yes
holds                5 cases, case 4 stdin '\n\n'  — the ORIGINAL, pre-input-repair
```

A rollback now undoes four repairs at once; the pre-image predates every one.

### Everything else

```
q963, q17, q266, q264, q1689, q1436   all unchanged
batch     CAPTURED, frozen
OracleExecution q3309  0   (total 20)      QuestionApproval  0
references             q1779 APPROVED/active · q3309 APPROVED/active
```

## Digests

```
q3309 digest     ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e
q3309 suite md5  5a1d8f5707f0c25aaab087b93a09ee1c

bank fingerprint  e79306d9…906b58b8  →  9cc3c8d8b0d050d0cebb6a43a44adbb68612d2e5077129c915481c1b66715acf
```

## Quality gate on the final suite

```
hidden tests        12          (floor 12)
malformed           0
duplicates          0
missing categories  none
canonical reference #2

  BLOCKER: no Tier-1 curated wrong solutions were supplied
  BLOCKER: no Tier-2 mutants were supplied

QUALITY_GATE = FAIL
```

**Both structural blockers are cleared.** The two that remain are artifacts of
`--structural-only`, which supplies no mutants — they are a statement about what
has not been *measured*, not about the suite. The gate cannot pass until someone
writes Tier-1 misconceptions for this problem and the mutants are executed on
Judge0; that is the next piece of human work, and it is deliberately not
automatable: Tier-1 mutants are the mistakes a person expects a learner to make.

---

```
SUITE_EXPANSION   = COMPLETE + VERIFIED
q3309 cases       = 12
ORACLE            = NOT STARTED (0 executions for q3309)
QUALITY_GATE      = structural checks PASS; awaiting a mutant set
APPROVAL          = NOT STARTED
PROMOTION         = NOT STARTED
RESEED            = NO
```

Next, in the order they unblock each other:

1. **Write the q3309 mutant set** — Tier-1 misconceptions (returns the last
   occurrence, returns a count, off-by-one on the window, mishandles the empty
   needle) plus Tier-2 mechanical flips, in a spec file; then run
   `quality_gate` for real and keep the report.
2. **Run the oracle** against the twelve-case suite with `--execute`, producing
   the provenance the artifact needs.
3. Only then `question_review` → `question_approve` → `question_promote`.
