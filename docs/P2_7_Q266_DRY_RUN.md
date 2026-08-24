# P2.7 — q266 boolean-output repair: dry-run clean, not applied

The last mechanical repair in the frozen pilot. Dry-run only; **production is
byte-for-byte where it was**, verified before and after with the same script.

---

## 1. Baseline (census role)

```
batch        p27-pilot-1   CAPTURED, frozen
membership   [17, 264, 266, 963, 1436, 1689, 3309]   (7)

q264   396d211e893103ce…   at its pre-image   (SAFE control)
q266   1ba2e68f49b16317…   at its pre-image
q963   8da0eb14d1a98d41…   at its statement-repair digest
q17    704a1652751f5043…   at its hidden-test-repair digest

actions      2 — q17 HIDDEN_TEST_REPAIR, q963 STATEMENT_REPAIR, and no others
fingerprint  0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
```

## 2. q266

```
pre-image  1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
live       1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
pre-image recomputes to its recorded digest: yes
```

## 3. Dry-run

```
HIDDEN-TEST REPAIR  (DRY RUN)
  database        neondb
  role            learnlm_hidden_test_rw
  production      True
  operator        Suhas

  batch           p27-pilot-1 (CAPTURED)
  question        266 — Palindrome Permutation
  pre-image       1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
  current digest  1ba2e68f49b16317eb00a2876d9d1c0494df3dcd271e3d6d94a71c4eade26411
  projected after 4df2af2733546b7c208a0407f91254f36244a2361065f6827c14299825a8f704
  field           hidden_test_cases (the ONLY field this command can change)
  cases           4 (unchanged count; stdin values are held fixed)

    case 1: stdin 'code'      'False' -> 'false'
    case 2: stdin 'aabbccee'  'True'  -> 'true'
    case 3: stdin 'abcba'     'True'  -> 'true'
    case 4: stdin 'carerac'   'True'  -> 'true'

DRY RUN — nothing was written.
```

The projected digest `4df2af27…a8f704` is the value computed two phases ago,
before the role existed. Current digest equals the pre-image digest, so the
repair starts from exactly the captured state.

### Recomputed from outside the command

The command enforces the stdin invariant itself; these were recomputed
independently rather than taking its word:

```
live suite == captured suite   yes
cases 4 -> 4

case 1  stdin 'code'      unchanged   case_identity same   input_identity same
case 2  stdin 'aabbccee'  unchanged   case_identity same   input_identity same
case 3  stdin 'abcba'     unchanged   case_identity same   input_identity same
case 4  stdin 'carerac'   unchanged   case_identity same   input_identity same

every case:  canonical_output(True)  == 'true'      matches the proposal
             canonical_output(False) == 'false'     matches the proposal
             is_canonical_output      False -> True
```

Each new value is `execution_adapter.canonical_output` applied to the boolean
the stored text was expressing — the contract's own printer, not a retyped
equivalent. Nothing is added, removed, or reordered.

### One honest difference from q17

For q17 the **output identities were unchanged**, because the stored list
already digested through the same canonical rendering the repair wrote. **Here
they change**, and they should:

```
case 1  output_identity changes    'False' -> 'false'
case 2-4 output_identity changes   'True'  -> 'true'
```

`normalize_output` does not fold case, so `True` and `true` are genuinely
different recorded answers. That is the defect: the wrapper prints
`str(res).lower()`, so a stored `True` could never match a correct submission —
these four cases were unpassable, not merely untidy. The repair changes the
recorded *text* to the only form the contract can produce; the meaning it was
always intended to express is unchanged, which is why `stdin` and the input
identities stay fixed and only the output rendering moves.

Worth stating plainly: this is the one place where a form repair and a
key change look alike at the digest level. The distinction rests on
`canonical_output` — the new text is mechanically derived from the old, and the
check above is what proves it rather than asserting it.

## 4. Zero writes

The same read-only script was run before and after — one definition of
"unchanged", not two:

```
q266 still equals its pre-image      yes  (1ba2e68f…de26411)
q264 / q963 / q17                    all at their recorded digests
remediation actions                  2
fingerprint                          0a75f10a2ac0bc49cabf3c5e88934656ac12250d44e98b7c5c4434e5fc4a0034
                                     unchanged
```

## 5. Status — stopped, awaiting instruction

```
q963 STATEMENT_REPAIR   = COMPLETE
q17  HIDDEN_TEST_REPAIR = COMPLETE
q266 HIDDEN_TEST_REPAIR = DRY-RUN CLEAN, NOT APPLIED
KEY_REPAIR              = NOT STARTED
ORACLE                  = NOT STARTED
APPROVAL                = NOT STARTED
PROMOTION               = NOT STARTED
BATCH                   = FROZEN
RESEED                  = NO
```

The apply command, for when you authorise it:

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py remediate_hidden_tests --alias hiddentest --batch p27-pilot-1 --question 266 --cases-file remediation/q266_approved_cases.json --reason "adjudication record: boolean casing True/False -> true/false to match the wrapper (form only)" --operator Suhas --apply --confirm
```
