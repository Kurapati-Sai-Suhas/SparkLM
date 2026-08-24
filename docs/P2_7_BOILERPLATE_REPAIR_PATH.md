# P2.7 — q1436 boilerplate repair: built, dry-run, not applied

The path is built, tested, mutation-verified and dry-run against production.
**Nothing was written, and `learnlm_boilerplate_rw` does not exist yet** — the
role is specified below for you to create, as with every previous one.

---

## A. Design

`groups/management/commands/remediate_boilerplate.py`. Dry-run by default;
writes one column of one question, replacing exactly one language's starter.

```
--language python  --source-file <path>  [--expect-digest <sha256>]
```

**ANNOTATION-ONLY**, enforced structurally. The current and proposed starters
are parsed, every annotation is stripped from both, and the trees are compared:
if anything else moved the repair is refused. So a renamed class, method or
parameter, an edited body, a reordered or added parameter, a new import or an
extra method cannot pass — regardless of how the text is arranged.

Comparison is structural rather than textual on purpose: a text diff cannot tell
`paths: list[list[str]]` from a renamed parameter, and a whitespace-only change
would slip past a naive equality check while still changing what the learner
sees. A whitespace-only edit is refused too, with its own message: nothing about
the contract moved, so it is not a repair.

**A return annotation is refused with its own reason** — the adapter binds
inputs and never reads the return type, so adding one changes what the learner
is handed with no effect on grading. (Returns are stripped alongside parameter
annotations precisely so that this check is *reachable*; otherwise the generic
comparison would raise first and the specific message would be dead code.)

Everything the other repair commands have: frozen batch + verified pre-image,
optional `--expect-digest` so an approval cannot land on a question that moved,
`select_for_update`, alias-scoped `transaction.atomic(using=alias)`,
`update_fields=["boilerplate_code"]`, every other captured field compared inside
the transaction, plus two checks specific to this column — the set of languages
must not change and every other language's starter must be byte-identical — an
append-only `RemediationAction`, and rollback from the untouched pre-image.

The dry-run also previews the v3 bindings the annotation would produce. It does
not change the contract, and says so.

## B. Role and privilege scope

**To create — it does not exist yet:**

```sql
CREATE ROLE learnlm_boilerplate_rw LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE neondb TO learnlm_boilerplate_rw;
GRANT USAGE ON SCHEMA public TO learnlm_boilerplate_rw;

GRANT SELECT ON groups_question TO learnlm_boilerplate_rw;
GRANT UPDATE (boilerplate_code) ON groups_question TO learnlm_boilerplate_rw;

GRANT SELECT, INSERT ON groups_remediationaction TO learnlm_boilerplate_rw;
GRANT SELECT ON groups_remediationbatch, groups_questionpreimage
  TO learnlm_boilerplate_rw;
```

Then add `BOILERPLATE_USER/PASSWORD/DB/HOST/PORT` to `.env` yourself — do not
send them to me. The alias appears only when `BOILERPLATE_USER` is set, and the
five variables are documented in
[FEATURE_FLAGS.md](LearnLM/docs/FEATURE_FLAGS.md).

Verify after creating:

```bash
psql "$OWNER_URL" -c "select has_column_privilege('learnlm_boilerplate_rw','groups_question','boilerplate_code','UPDATE') as can_edit_starter, has_column_privilege('learnlm_boilerplate_rw','groups_question','content','UPDATE') as can_edit_statement, has_column_privilege('learnlm_boilerplate_rw','groups_question','execution_contract_version','UPDATE') as can_edit_contract, has_table_privilege('learnlm_boilerplate_rw','groups_question','DELETE') as can_delete;"
```

Expect `t`, **`f`**, **`f`**, `f`. The gate refuses an over-granted role as well
as an under-granted one: `content`, `hidden_test_cases`, `status`,
`trust_state`, `execution_contract_version` and `hidden_wrapper_code` UPDATE are
all on its forbidden list.

**Five roles, five columns**, and each one's forbidden list names the others:

| role | may UPDATE |
|---|---|
| `learnlm_remediate_rw` | `content` |
| `learnlm_hidden_test_rw` | `hidden_test_cases` |
| `learnlm_contract_rw` | `execution_contract_version` |
| `learnlm_boilerplate_rw` | `boilerplate_code` |
| `learnlm_preimage_rw` | nothing on `groups_question` |

`boilerplate_code` is the widest reach per byte in that table: it is the code a
learner is handed *and* the declaration the adapter binds arguments from. That
is the argument for a fifth role rather than folding it into an existing one.

## C. The current starter, and the dry-run

```
q1436 live digest    4b5220a1…95bd2887      pre-image verifies
languages            ['python']
starter identical to the pre-image: True

class Solution:
    def destCity(self, paths):
        # Write your code here
        pass

declaration occurs   exactly 1×      approved declaration present: 0×
declared now         destCity(paths <UNANNOTATED>)
```

```
BOILERPLATE REPAIR  (DRY RUN)
  batch           p27-pilot-1 (CAPTURED)
  question        1436 — Destination City
  pre-image       0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
  current digest  4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
  projected after 333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
  field           boilerplate_code['python'] (the ONLY thing this command can change)
  languages       ['python'] -> ['python']  (unchanged)
  size            90 -> 107 bytes
  starter matches the pre-image: True

  annotations changed:
    destCity(paths): (none) -> list[list[str]]

  diff:
    @@ -1,4 +1,4 @@
     class Solution:
    -    def destCity(self, paths):
    +    def destCity(self, paths: list[list[str]]):
             # Write your code here
             pass
```

Four lines before, four after; 90 → 107 bytes; the trees are identical once
annotations are stripped; no return annotation; no import. The file
(`remediation/q1436_approved_boilerplate.py`, 107 bytes, sha256
`a6dcbf99b7b91289414ee9cd431badc1c297d96ff8cd44afb500a7f6dd46b323`) was
**derived from the stored starter**, not retyped.

The dry-run ran through `--alias default` — the read-only census role — because
the boilerplate role does not exist yet and a dry-run writes nothing. `--apply`
through that alias would be refused by the gates.

## D. Projected digest

```
current    4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887
projected  333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
```

Equal to the approved plan's step-c digest, recomputed independently from
production.

## E. v3 feasibility after the annotation

Through the real adapter, all four cases, before and after:

```
                                        before                          after
case 1  [["London","New York"],…]       OK, undeclared_parameter_type   OK, no warnings
case 2  [["B","D"],["A","B"],["C","D"]] OK, undeclared_parameter_type   OK, no warnings
case 3  [["A","Z"]]                     OK, undeclared_parameter_type   OK, no warnings
case 4  [["A","B"],["A","C"],…]         OK, undeclared_parameter_type   OK, no warnings

after, every case:  exactly 1 argument, of type list, equal to json.loads(stdin)
envelopes:  [[["London","New York"],["New York","Paris"],["Paris","Rome"]]]
            [[["B","D"],["A","B"],["C","D"]]]
            [[["A","Z"]]]
            [[["A","B"],["A","C"],["B","D"],["C","D"]]]
```

Before the annotation the adapter *guesses* and passes the JSON **text** as a
string; after it, one decoded list. No input is mutated — each bound argument
equals `json.loads(stdin)` exactly.

For the record, what v1 does today with the same inputs: splats 3, 3, 1 and 4
positional arguments into a one-parameter method. **This is the last blocker on
q1436's contract migration**; with the annotation in place the contract command
would stop refusing it.

Nothing was executed on Judge0.

## F. Mutation — 20 killed / 21, **0 real survivors**

One planted equivalent (a heading's spacing) survived, as intended.

Killed: wrong column; the save writing every column; the untouched-field check
disabled; the frozen-batch and pre-image checks skipped; the transaction on the
wrong connection; no row lock; a renamed method / edited body / new import
permitted; a return annotation permitted; a whitespace-only edit recorded; a
no-op recorded; a stale `--expect-digest` ignored; another language's starter
changed during the write; the audit record dropped; the wrong action class; the
statement role accepted; the probe pointed at the wrong column; the forbidden
list emptied; the probe asking for table-wide UPDATE; annotations no longer
stripped.

One real survivor was found and closed: **the language-set check could be
deleted unnoticed** — the proposal is built by copying the stored dict and
substituting one key, so the set is preserved by construction and no behavioural
test could see the check disappear. It is a backstop against a write that lands
differently from what was proposed, and losing a language would silently take a
starter away from every learner using it. Now forced to fire by a test.

## G. Full regression

```
2294 passed, 0 failed, 0 errors   (groups, common, learning — 3m19s)
  boilerplate remediation   41 new
```

`scripts/test_judge*.py` remain excluded — standalone Judge0 probes that open a
database connection at import time, predating this work.

## H. Production safety

```
q1436 digest         4b5220a1…95bd2887      unchanged
q1436 starter        unannotated            boilerplate md5 identical to the capture
q264, q1689          byte-identical to their pre-images
q963, q17, q266, q3309   at their recorded digests
batch                CAPTURED, frozen
actions              7 — no BOILERPLATE_REPAIR among them
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
fingerprint          296627192435888cd8791bd2bfa2e73428cab8ae343bd34efb27a977cb6e6047
```

Unchanged. The only production traffic this phase was census reads and one
dry-run.

## I. Exact next step

1. Create `learnlm_boilerplate_rw` with the grants in §B and verify with the
   `psql` line there.
2. Add `BOILERPLATE_*` to `.env` yourself.
3. Tell me, and I will re-run the dry-run through the real role for your
   approval, then apply and verify.

```bash
cd LearnLM/backend/LearnLM && ../.venv/Scripts/python.exe manage.py remediate_boilerplate --alias boilerplate --batch p27-pilot-1 --question 1436 --language python --source-file remediation/q1436_approved_boilerplate.py --expect-digest 4b5220a1c5a710a41df283ed2c093273ba554fe624fc4b55fec197e295bd2887 --reason "approved plan: annotate paths as list[list[str]] so the adapter binds one list rather than guessing" --operator Suhas --apply --confirm
```

Alternatively, **q3309's contract migration needs nothing but
`learnlm_contract_rw`** — its chain is complete and all five cases bind. Either
order works; q3309 is the shorter path.

---

```
q1436 BOILERPLATE_REPAIR = DRY-RUN READY, NOT APPLIED   (role not yet created)
q3309 CONTRACT_REPAIR    = READY                        (role not yet created)
q1436 CONTRACT_REPAIR    = BLOCKED UNTIL BOILERPLATE
q963 KEY_REPAIR          = NOT STARTED
ORACLE                   = NOT STARTED
BATCH                    = FROZEN
RESEED                   = NO
```
