# P2.7 — The oracle write path: built and verified

The alias and privilege work is done. **`learnlm_oracle_rw` does not exist yet**
— Step 1 needs the DDL connection, which is yours — so Step 2 is the only part
of this phase left open. Everything downstream of it is built, tested and
mutation-verified, and no reference or execution was created.

---

## A. Oracle role security — specified, not yet created

```
learnlm_oracle_rw   ABSENT from pg_roles
ORACLE_*            ABSENT from .env
```

Existing roles: `boilerplate`, `census_ro`, `contract`, `hidden_test`,
`pilot_rw`, `preimage`, `remediate`. To create:

```sql
CREATE ROLE learnlm_oracle_rw LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE neondb TO learnlm_oracle_rw;
GRANT USAGE ON SCHEMA public TO learnlm_oracle_rw;
GRANT SELECT ON groups_question TO learnlm_oracle_rw;
GRANT SELECT, INSERT, UPDATE ON groups_referencesolution TO learnlm_oracle_rw;
GRANT SELECT, INSERT ON groups_oracleexecution TO learnlm_oracle_rw;
GRANT USAGE, SELECT ON SEQUENCE groups_referencesolution_id_seq,
                                groups_oracleexecution_id_seq
  TO learnlm_oracle_rw;
```

The `oracle` alias is wired (gated on `ORACLE_USER`) and the five variables are
documented in [FEATURE_FLAGS.md](LearnLM/docs/FEATURE_FLAGS.md). Until the
credentials exist, `--alias oracle` raises `ConnectionDoesNotExist` rather than
falling back to census.

**Seven roles, seven scopes.** This is the first whose writes are not
`groups_question` at all:

| role | may write |
|---|---|
| `learnlm_remediate_rw` | `groups_question.content` |
| `learnlm_hidden_test_rw` | `groups_question.hidden_test_cases` |
| `learnlm_boilerplate_rw` | `groups_question.boilerplate_code` |
| `learnlm_contract_rw` | `groups_question.execution_contract_version` |
| `learnlm_preimage_rw` | the pre-image tables only |
| `learnlm_oracle_rw` | **references and executions — nothing on the question** |
| `learnlm_census_ro` | nothing |

## B. `reference_create` — alias routing

`--alias` added; every access routed:

```
Question.objects.using(alias)                  the question read
ReferenceSolution.objects.using(alias)         duplicate check, active check
ReferenceSolution.objects.using(alias).create  the write
transaction.atomic(using=alias)                the write's transaction
```

The question is now read through the selected connection rather than
`_question_trust.resolve_question`, which uses the default manager — reading a
question from one connection and attaching its reference to another is how a row
ends up pointing at something that is not there.

## C. `oracle_execute` — alias routing

`--alias` added and threaded the whole way down, which required a parameter on
three functions that did not have one:

```
oracle_execute.handle(--alias)
  -> Question.objects.using(alias)                     both selection paths
  -> oracle_pipeline.run_question(..., using=alias)
       -> execute_case(..., using=using)
            -> _record(..., using)
                 -> provenance.record_execution(..., using=using)
                      -> execution.save(using=using)
```

The alias controls Django database access only — Judge0 is reached over HTTP and
is unaffected, which the docstring now says explicitly.

`reference_review` was threaded too, though the brief named only the other two:
without it the lifecycle stops at DRAFT, since submit/approve/activate all write
through the default connection. Two details that fell out of it:

- **`approve()` now sets `approved_by_id`, not `approved_by`.** The reviewer is
  resolved on `default` while the reference lives on the operator alias, and
  Django refuses to relate objects across databases — the same FK-by-id pattern
  `pre_image` already uses.
- **The approver's *name* is read through `default`.** The oracle role holds no
  privilege on the user table, so following that FK on its connection would make
  an ordinary `list` fail on a permission error. The id lives on the row; the
  name is a display convenience.

## D. Privilege gates

Each command names its own role list and its own probes:

```
ALLOWED_ORACLE_ROLES     {learnlm_oracle_rw}   — disjoint from all five others
REFERENCE_WRITE_PROBE    INSERT + UPDATE on groups_referencesolution
ORACLE_EXECUTE_PROBE     INSERT on groups_oracleexecution
ORACLE_FORBIDDEN         INSERT/UPDATE/DELETE/TRUNCATE on groups_question,
                         INSERT/UPDATE on groups_questionapproval,
                         INSERT/UPDATE on groups_remediationaction,
                         INSERT/UPDATE/DELETE on groups_questionpreimage,
                         DELETE on groups_referencesolution,
                         UPDATE/DELETE on groups_oracleexecution
```

`groups_question` UPDATE is checked at **table** level deliberately: the repair
roles hold column-level grants, so a table-level question is true for them; for
this role it must be false for every column, and that is exactly what the
table-level check asks.

One deliberate change to how the gate decides where it is. These three commands
are run against throwaway databases by their own suites, so instead of
`gate_production_target` (which refuses whenever the target is not the database
demanded) they use a new `ops.describe_target`, which reports where the
connection is and lets the command apply the production contract only when it is
on production. Without that, adding the gate would have failed 56 existing tests
for being on a test database — which is not a safety property, just a mismatch.

## E. Tests — 27 new, 2347 total

```
A  reference_create accepts --alias                      ✓
B  it writes through the NAMED alias                     ✓ (captured routing)
C  no ReferenceSolution/Question access escapes .using   ✓ AST
D  oracle_execute accepts --alias                        ✓
E  provenance receives the alias end to end              ✓
F  no oracle query uses the default manager              ✓ AST
G  an unconfigured alias fails loudly, writing nothing   ✓
H  a role without the grants cannot author a reference   ✓ real role
I  the oracle role cannot modify a question              ✓ real role, 5 columns
J  …including status, trust_state and the contract       ✓ real role
K  the oracle role cannot approve a question             ✓ real role
L  it cannot touch pre-images or the audit trail         ✓ real role
M  the reference write is alias-scoped                   ✓ AST
N  the approver crosses the alias boundary by id         ✓
O  no silent fallback to default anywhere                ✓ AST
P  the operator is still recorded on the execution       ✓
+  the writer SAVES on the named connection              ✓
+  provenance refuses a cross-question reference         ✓
+  provenance refuses an unapproved reference            ✓
+  both gate calls probe the execution table             ✓ AST
+  a production target triggers the role gate            ✓ ×2 commands
+  the command still cannot write grading truth          ✓ AST + behavioural
```

Privileges are proved against real roles created in the test transaction with
`SET LOCAL ROLE`, each denial in its own SAVEPOINT. Routing is proved by AST
where no single run can demonstrate the absence of a path to `default`.

## F. Mutation — 20 killed / 22

**0 real survivors.** Two survivors, both equivalent and both documented:

- **E1** — planted: punctuation in a help string.
- **M20** — removing the cross-question guard from `record_execution` leaves
  `OracleExecution.clean()` (models.py:1311) enforcing the same rule with the
  same message, and `full_clean` runs before the save. Equivalent in effect:
  the failure moves from before the row is built to during validation, both
  before any write.

Killed along the way: `--alias` removed from either command; the alias ignored
and `default` hard-coded; the reference write, the question read, or the `--all`
read routed to `default`; the pipeline dropping the alias; the writer saving
without it; a bare `transaction.atomic()`; the remediation role accepted; the
forbidden list dropped; the wrong probe table; a role holding `groups_question`
UPDATE accepted; a role able to approve accepted; the role list swapped; the
identity check stubbed out; an execution with no accountable operator; approval
losing its approver; an unapproved reference producing recorded output.

Three real survivors were found and closed during the sweep: the writer's
`using=` was untested (a value threaded but not used), only one of the two gate
calls was checked, and `describe_target` could be replaced by a literal that
disabled every gate.

## G. Regression

```
2347 passed, 0 failed, 0 errors   (groups, common, learning — 3m53s)
reference + oracle suites                      300 passed
oracle write path                               27 new
```

**One infrastructure interruption, not a code failure:** partway through this
phase the Docker daemon stopped and the local Postgres went with it, producing
`PoolTimeout` errors across whole files. Restarted Docker and the container,
re-ran, clean. Same thing happened during the DB-backed regression phase; worth
recognising the signature (`couldn't get a connection after 10.00 sec` on every
test in a file) so it is not mistaken for a code defect.

## H. Production unchanged

```
q3309  8a342568…   q1436  0b2a79f2…      both unchanged
q266, q264, q1689  at their pre-images
actions            11
ReferenceSolution  1   OracleExecution  20   QuestionApproval  0
PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 2
fingerprint        e79306d978a1e5c8d88584d877e7e6bad2184d684119faf0af95e208906b58b8
```

No reference was created, no oracle ran, and the only production traffic was
census reads.

## I. Exact next phase

1. **Create `learnlm_oracle_rw`** with the grants in §A, add `ORACLE_*` to
   `.env` yourself, and tell me.
2. I verify the role as itself across every table and column, exactly as with
   the previous five.
3. Then the semantic phase, unchanged from the last brief: draft a reference for
   q3309 → human review → approve → activate → oracle **dry run** → read the
   per-case comparison → `--execute` → stop → q1436 → stop → reconcile.

Worth carrying forward into that phase: **the oracle proves what the reference
returns, not that the reference answers the statement.** For q1436 in
particular, the stored keys and any reference I write would both be derived from
the same repaired statement — an AGREE there is consistency, not proof, and the
`ready_for_quality_gate` flag the pipeline sets is explicitly not readiness for
`ORACLE_VERIFIED`.

---

```
ORACLE WRITE PATH = BUILT + VERIFIED
learnlm_oracle_rw = NOT CREATED (needs the DDL connection)
q3309 ORACLE      = NOT STARTED
q1436 ORACLE      = NOT STARTED
APPROVAL          = NOT STARTED
RESEED            = NO
```
