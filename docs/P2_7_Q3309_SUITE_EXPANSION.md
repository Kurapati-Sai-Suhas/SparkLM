# P2.7 — q3309 suite expansion: designed, built, dry-run, not applied

Five cases become twelve, the existing five gain coverage labels, and no
existing input, answer or explanation moves. **Nothing was written**, and the
oracle has not run.

---

## A. The current five cases

```
digest 8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6   pre-image verifies

 1  'hello\nll\n'            → '2'   category (missing)   binds ["hello","ll"]
 2  'aaaaa\nbba\n'           → '-1'  category (missing)   binds ["aaaaa","bba"]
 3  'abc\na\n'               → '0'   category (missing)   binds ["abc","a"]
 4  '["",""]'                → '0'   category (missing)   binds ["",""]
 5  'mississippi\nissip\n'   → '4'   category (missing)   binds ["mississippi","issip"]
```

Every case carries `stdin`, `expected_output`, `explanation` — and **no
`category`**, which is why the gate reported all five required categories
missing: it reads coverage from an explicit label, so an unlabelled suite is
unmeasurable rather than uncovered. Case 4 genuinely is the minimum-boundary
case; the gate simply cannot see that.

The pre-image still holds the pre-repair suite (case 4 as `'\n\n'`), so rollback
restores the state before the input repair as well.

## B. The seven additions

| # | stdin | → | category |
|---|---|---|---|
| 6 | `["","a"]` | `-1` | `empty_input` |
| 7 | `a\na\n` | `0` | `singleton` |
| 8 | 10,000-char haystack + `ab` | `9998` | `maximum_boundary` |
| 9 | `aaaa\naa\n` | `0` | `duplicate_values` |
| 10 | `abcabc\nabc\n` | `0` | `first_occurrence` |
| 11 | `abc\nabcd\n` | `-1` | `needle_longer_than_haystack` |
| 12 | `abcab\nabd\n` | `-1` | `partial_match_backtrack` |

All seven bind cleanly under v3 with no warnings.

## C. Category mapping

The gate's required set is derived from the **input contract**, and the contract
is a claim about q3309's inputs that I had to make honestly:

```
accepts_empty_input  True   the repaired statement now permits |s| = 0
is_sequence          True   a string is a sequence of characters
has_size_bounds      True   0 ≤ |s| ≤ 10^4 is stated
allows_duplicates    True   characters repeat, and overlapping matches follow
order_sensitive      True   'abc' ≠ 'cba'
numeric              False  → negative_values and zero are NOT applicable
overflow_sensitive   False  → not applicable
```

That makes exactly five categories required, all now present:

```
empty_input       → case 6   (case 4 is empty/empty; case 6 is empty haystack)
singleton         → case 7
minimum_boundary  → case 4   (label only)
maximum_boundary  → case 8
duplicate_values  → case 9
```

The other seven labels (`typical`, `no_match`, `match_at_start`,
`overlapping_prefixes`, `first_occurrence`, `needle_longer_than_haystack`,
`partial_match_backtrack`) are descriptive. They satisfy no requirement and were
not invented to.

## D. Semantic rationale — three independent derivations per case

Each expected output was derived **from the statement**, in a written argument,
then cross-checked against `str.find` **and** a naive O(n·m) scan written for
the check. All three agreed on all seven. The reference alone would not have
been evidence — it is one implementation of a statement that this milestone has
already had to repair.

```
 6  ["","a"] → -1   an empty haystack has no index at which a 1-char window exists
 7  a/a      → 0    haystack[0:1] == needle, and 0 is the smallest index
 8  max      → 9998 9999 'a's then 'b'; "ab" exists only at 9998; every earlier
                    window is "aa" — and the haystack is exactly 10^4, the bound
 9  aaaa/aa  → 0    matches at 0, 1, 2 — overlapping; the FIRST is 0. Catches a
                    solution that counts occurrences or skips past a match
10  abcabc   → 0    matches at 0 and 3; catches a solution returning the LAST
11  abc/abcd → -1   no 4-character window exists in a 3-character string
12  abcab/abd→ -1   'ab' matches twice then fails; catches returning a partial
```

None duplicates an existing input, by case identity and by the gate's own
normalised duplicate check.

**Nothing here is ambiguous enough to need human adjudication** — every answer
follows from one clause of the repaired statement. The judgement that *does*
need your eye is the input contract in §C, because it decides which categories
the gate will demand for this and any future run.

## E. Projected digest

```
current    8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6
projected  ebb26e7f0f1ff127abfd99adc6903c66f9b4660c6df9b3c3d99a265d4f23a61e
```

All twelve case, input and output identities are listed in the dry-run output.
The five existing identities are unchanged — adding a `category` key does not
touch `stdin`, so `case_identity` and `output_identity` are untouched; only the
suite (and therefore the state digest) moves.

## F. The remediation path — a fourth class over one column

`SUITE_EXPANSION`, added by **migration 0046** (state-only `AlterField`, same
shape as 0045) and written by
`groups/management/commands/expand_hidden_tests.py`.

Why not reuse an existing class:

```
HIDDEN_TEST_REPAIR      the stored answer's FORM changed
INPUT_REPAIR            the question being asked changed
EXPECTED_OUTPUT_REPAIR  the answer changed
SUITE_EXPANSION         there are now MORE questions than there were
```

The last one is not a repair. It is the only class that **invalidates evidence
rather than correcting data**: oracle executions are scoped to case digests, so
a suite that grew has cases no execution covers. Recording it under any of the
other three would hide precisely the fact a later reviewer needs before trusting
an artifact — which is why the command prints that warning after a successful
apply.

Invariants: existing cases are carried over **by value** (the plan supplies only
labels and additions, so preservation is structural); `category` is the only key
that may be added to an existing case; an existing label cannot be silently
rewritten; additions are appended, never inserted; every added case must have a
non-empty category, a text `expected_output`, and must **bind under the
question's contract with no adapter warnings** — a case the adapter cannot
deliver is not coverage. Plus the established four: frozen batch, verified
pre-image, `--expect-digest`, row lock, alias-scoped transaction,
`update_fields=["hidden_test_cases"]`, post-write field comparison, post-write
re-check of the invariants against what landed, append-only action, rollback.

## G. Privilege contract

`learnlm_hidden_test_rw` — **unchanged, not broadened**. Same column, same
probe, same forbidden list.

This is now the **fourth** action class over `hidden_test_cases`, and the
database still cannot separate them: one grant authorises answer-form repair,
input repair, expected-output repair and expansion alike. The separation is the
command's invariant plus the `action_class`, exactly as recorded when
`remediate_inputs` was built. That is a standing limitation of this design, not
a new one, and it is the reason each of these commands carries its own narrow
invariant rather than a shared "edit the suite" helper.

## H. Tests — 38, all passing

Additions, preservation, labelling, ordering, rollback, and the refusals:
changed answer, changed stdin, removed case, reordered suite, duplicate input,
missing/empty category, relabelling, no additions, wrong question, non-text
answer, unbindable addition, addition that binds only by guessing, stale
`--expect-digest`, stray key on an existing case, drifted field, corrupt
pre-image, unfrozen batch, missing pre-image, untouched control, dry-run writes
nothing — plus AST guards for `update_fields`, the action class, the role and
probe, alias-scoped transaction and row lock.

## I. Mutation — 20 killed / 21, **0 real survivors**

One planted equivalent (a heading) survived. Four real survivors were found and
closed: the stray-key check, the added-case label backstop, the
binds-only-by-guessing check (the fixture's starter was annotated, so no case
ever produced a warning) and the post-write re-check.

One pre-existing test also had to be corrected: `test_migration_0045` pinned the
model-vs-migration comparison to 0045, which 0046 legitimately extends. It now
resolves the latest migration dynamically.

## J. Regression

```
2403 passed, 0 failed, 0 errors   (groups, common, learning — 3m17s)
```

## K. Production after the dry-run

```
q3309   5 cases, digest unchanged, DRAFT / UNVERIFIED
q1436, q963 unchanged · q264, q266, q1689 at their pre-images
OracleExecution q3309 = 0 · QuestionApproval = 0 · actions = 11
fingerprint  e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8   unchanged
latest applied migration: 0045 — **0046 is written but NOT applied**
```

## L. The apply command

Migration 0046 must be applied first — the write records an action class the
production database's vocabulary does not yet list. It is state-only and emits
no DDL, like 0045.

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py expand_hidden_tests --alias hiddentest --batch p27-pilot-1 --question 3309 --plan remediation/q3309_suite_expansion.json --expect-digest 8a34256831cd74fd8ee1c0ee0885bc998aaa6dafbf19f6c99b17529aa17086e6 --reason "reach the coverage floor and label existing cases before recording oracle evidence" --operator Suhas --apply --confirm
```

The plan file is `remediation/q3309_suite_expansion.json` (11,721 bytes — the
10^4-character maximum-boundary case dominates it).

## M. The oracle has NOT run

```
OracleExecution rows for q3309: 0
```

The 5/5 dry-run recorded nothing and remains planning input only. After the
expansion lands, the oracle should be run once against the **twelve**-case
suite; running it before would produce evidence for five case digests and leave
seven uncovered.

---

```
q3309 SUITE EXPANSION = DRY-RUN READY / NOT APPLIED
0046                  = WRITTEN, NOT APPLIED
q3309 ORACLE          = NOT STARTED
QUALITY GATE          = CURRENTLY FAILING (5 < 12; unlabelled)
QUESTION APPROVAL     = NOT STARTED
PROMOTION             = NOT STARTED
RESEED                = NO
```
