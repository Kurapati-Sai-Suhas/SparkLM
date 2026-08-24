# P2.7 — Full DB-backed regression + coding execution integration audit

**Status:** complete. One real defect found and fixed. RESEED = NO.

---

## A. Local DB availability

Docker Desktop was not running; its daemon was started, then:

```bash
docker compose up -d db
```

`learnlm_postgres` (`ankane/pgvector:latest`) up, `pg_isready` accepting
connections on 5432. Credentials match `sparklm_test_isolation.py` exactly
(`learnlm_db` / `postgres` / 127.0.0.1:5432), so pytest redirects to it before
Django loads and `.env`'s production values are never read.

**Neon was never used for tests.** The only production contact in this phase
was the read-only `learnlm_census_ro` count in §J.

One cosmetic warning: `database "learnlm_db" has a collation version mismatch`
(volume created under a newer glibc than the current image). It does not affect
the run; worth a `REFRESH COLLATION VERSION` at some point.

## B. Full DB-backed regression

Three runs, all `groups common`:

| run | tests | result |
|---|---:|---|
| baseline, `--create-db`, adapter changes only | 1950 | **1950 passed**, 1 error |
| with the 409 fix + new view tests | 1955 | **1955 passed**, 0 errors |
| final, all changes | 1964 | **1964 passed, 0 failed, 0 errors** |

The 1,950-test baseline passing is the important number: **the execution-adapter
work introduced no regression**, which had been unverified for three phases.

### The one error, classified

`common/test_websocket_authorization.py::test_two_members_can_still_collaborate`
— `IntegrityError: Key (content_type_id, codename)=(534, add_logentry) already
exists`.

**Category C — environment/infrastructure.** It appeared only in the
`--create-db` run, passes in isolation (10/10), and did not reproduce in either
`--reuse-db` run. It is a permission/content-type collision during fresh test-DB
creation, in a websocket suite with no path to any code this phase touched.

No category A (adapter regression), B, or D failures.

### Tests specifically covering `execution_adapter.py`

408 in the phase-relevant suites (adapter, contract impact, input contract,
routing hygiene, learning-signal hygiene, shadow/Glicko, coding views, loop
smoke) — all passing.

## C. Execution-adapter regressions

**None.** The baseline run above is the evidence: 1,950 pre-existing tests
passed unmodified with the adapter, `prepare_stdin`, and the shared oracle seam
already in place.

## D. Was `ExecutionContractError → HTTP 500` a real defect?

**Yes — confirmed by a real DB-backed integration test, not by reading code.**
Written red-first against the live view:

```
groups.services.ExecutionContractError: question 999 test case 1:
    expected_output is a list, not text; there is nothing to compare against
INFO access: POST /api/code/submit/ -> 500 (15ms)
```

`coding_views.py` caught only `GradingUnavailable`. Any of the 48 production
questions storing a list in `stdin`/`expected_output` produced an unhandled 500.

Full mapping traced through submission → GradingService → adapter → response:

| condition | HTTP | source |
|---|---|---|
| accepted / wrong_answer / compile_error / runtime_error / timeout | **200**, verdict in `status` | `final_status` from Judge0 `status_id` |
| question not found | 404 | view |
| no hidden test cases | 409 `question_not_gradable` | view (pre-existing) |
| **contract mismatch / invalid input** | **409 `question_not_gradable`** | **added this phase** |
| Judge0 outage | 503 | `GradingUnavailable` |
| unexpected programming fault | 500 | uncaught, deliberately |

## E. Was a 409 mapping required?

**Yes**, and the repository already stated why. Twenty lines above, the
no-test-cases branch reads:

> 409, not 500 (M4 Phase B). A question with no test cases is a data-integrity
> condition, not a server fault … Reporting it as 500 polluted error metrics
> with a content problem and made real faults harder to see.

Unexecutable stored test data is the same category. The fix catches **only**
`ExecutionContractError`, logs `exc.details` server-side, and returns the
existing body shape.

`exc.details` is deliberately **not** in the response: it describes the hidden
test data, and grading data never leaves the server (M2 P2.5).

## F. Mutation result for the integration fix

**7 killed / 7. Zero survivors.**

| id | attack | killed by |
|---|---|---|
| M1 | remove the 409 mapping | 500 resurfaces |
| M2 | broaden to `except Exception` | structural AST guard |
| M3 | narrow it — mismatch becomes 500 | `test_broken_test_data_is_a_409_not_a_500` |
| M4 | Judge0 outage becomes 409 | `test_judge0_unavailable_returns_503` |
| M5 | detail string stops matching the convention | body assertion |
| M6 | drop diagnostic logging | `test_the_409_is_logged_for_diagnosis` |
| M7 | leak `exc.details` to the client | secrecy assertion |

Three survivors were found and closed. **M2 is worth recording**: broadening the
catch to `Exception` survived at first, and my behavioural test
`test_an_unexpected_error_is_still_a_500` passed *for the wrong reason* — a
`RuntimeError` has no `.details`, so the handler crashed on attribute access and
produced a 500 by accident. Only an AST guard on the handler types rules it out.
Two mutants had ambiguous anchors (both 409 branches share a suffix) and were
re-anchored and re-run rather than dropped.

## G. P2.8 coding/adaptive smoke test

New `groups/test_coding_loop_smoke.py` — 6 tests, all passing — walks the whole
circuit: next problem → submit → grade → ProgressionService → learner state →
next problem. No existing test crossed all those joins in one run.

Every Step-5 bullet is covered, mostly by suites that already existed:

| behaviour | covering tests |
|---|---|
| solved question permanently excluded | `test_a_solved_question_is_never_served_again`, `test_solved_exclusion_ignores_adaptive_eligibility`, loop smoke |
| recent-failure cooldown | `test_a_failed_question_is_not_served_again_immediately`, `test_cooldown_expiry_returns_a_question_to_the_normal_pool`, `test_cooldown_outranks_attempt_count` |
| attempt-count ordering | `test_attempt_count_outranks_last_attempt`, `test_the_least_attempted_question_wins_a_tie` |
| recency ordering | `test_last_attempt_outranks_question_id`, `test_a_recently_failed_question_is_demoted_below_untried_ones` |
| topic substitution / exhaustion | `test_a_dag_substitution_is_reported`, `test_an_exhausted_topic_returns_a_typed_response`, loop smoke |
| unknown topic | `test_an_unknown_topic_is_rejected`, `test_an_unknown_topic_never_serves_a_foreign_question` |
| compile/runtime/timeout evidence | `test_an_execution_failure_does_not_enter_the_accuracy_statistic`, `test_an_execution_failure_still_counts_as_practice`, `test_compile_error_records_no_snapshot`, loop smoke |
| accepted / wrong-answer evidence | `test_the_evidence_set_is_exactly_accepted_and_wrong_answer`, `test_a_partial_pass_is_a_wrong_answer_and_counts` |
| onboarding manufactures no Elo | `test_onboarding_with_ten_topics_still_leaves_elo_at_the_default` |
| `recompute_mastery` agrees with the writer | `test_recompute_ignores_execution_failures_exactly_as_the_writer_does`, `test_rebuild_counters_agrees_with_the_live_writer` |
| GDCP does not corrupt accuracy | `test_failing_a_topic_does_not_decay_descendant_accuracy`, `test_no_failure_of_any_kind_decays_descendants` |

The loop smoke also pins this phase's join: a question with unexecutable test
data returns 409, writes no `CodeSubmission`, and leaves the learner able to
continue.

## H. P2.9b Glicko / routing compatibility

**Compatible. No stale field drives routing unexpectedly.**

Production selection (`coding_views._select_question`) ranks on:

```
1. |base_difficulty − target_elo|   2. in_cooldown
3. attempt_count   4. last_attempt_at   5. id
```

`target_elo` is `UserCodingProfile.elo_rating`. Glicko is consulted nowhere in
that path, which is P2.9b's design and is pinned by
`test_production_selection_is_untouched_by_shadow_state`,
`test_the_shadow_model_does_not_change_production_elo`, and
`test_the_shadow_model_does_not_change_production_mastery`.

**One observation, reported not acted on:** `Question.base_difficulty` has no
writer anywhere in the codebase — it is the static seeded prior, defaulting to
1200.0. Meanwhile the shadow model maintains a *learned* per-question rating
that nothing consumes. So the question-side term in routing never improves with
evidence. That is the intended shadow-only posture today, but it means "nearest
difficulty" is nearest-to-a-guess for any question whose seeded prior was wrong.
Worth a decision when Glicko is promoted out of shadow. **No routing changes
made.**

## I. Final regression

**1,964 passed, 0 failed, 0 errors** (`pytest groups common`, 2m31s).

Phase-relevant subset: **408 passed** across adapter, contract-impact,
input-contract, routing-hygiene, learning-signal-hygiene, shadow-adaptive,
glicko-history, coding-views and loop-smoke suites.

## J. Production safety

Read-only check via `learnlm_census_ro`:

```
database                     neondb
role                         learnlm_census_ro
questions                    2926
reference solutions          1        (pilot 1779 only)
oracle executions            20       (pilot: 10 cases x 2 runs)
question approvals           0
questions PUBLISHED          0
questions ORACLE_VERIFIED    0
questions declaring v3       0
write privileges on groups_question: NONE (read-only role)
```

Identical to the state recorded at the end of the pilot phase. No production
write, no new `ReferenceSolution`, no new `OracleExecution`, no
`QuestionApproval`, no `expected_output` change, no hidden-test change, no
promotion, no reseed.

## K. Is the randomized semantic audit unblocked?

**Yes.** Every prerequisite from the previous brief is now satisfied:

- full DB-backed regression green — **1,964 passed, 0 failures**;
- adapter finalized, both services share it;
- zero-argument / input-contract semantics settled and tested;
- P2.7h-1 floor semantics documented (as incompatible, with the integration
  point);
- historical grading truth untouched.

Sample configuration confirmed unchanged and **not executed**: seed
`20250815` (still the command default), pilot 1779 excluded, same stratified
round-robin design.

## L. Remaining blockers before answer-key remediation

1. **162 `CONTRACT_MISMATCH` + 43 `INVALID_INPUT`** — stored test data needs
   human repair; no adapter can infer intent.
2. **457 `NEEDS_MANUAL_REVIEW`** — no declared parameter types. Annotating
   starters is content work.
3. **49 ambiguous entry points** — `dir(sol)[0]` is alphabetical; not fixable
   server-side.
4. **v2's coercion defect** — unfixable from the server; needs a decision on
   changing `V2_PYTHON_WRAPPER`.
5. **v3 adoption mechanism** — v3 exists and is wired, zero questions declare
   it. Flipping a contract changes what stored outputs mean, so adoption must
   be per question via reference → oracle → adjudication.
6. **The semantic error rate is still unmeasured** — that is the next phase.

## M. Remaining blockers before reseed

**RESEED = NO.**

1. execution semantics corrected — done for v1 via v3; **not** for v2;
2. semantic answer-key error rate measured on the randomized sample — **not
   started** (now unblocked);
3. remediation strategy approved — not started;
4. oracle/reference/adjudication workflow proven — pilot 1779 only (1 reference,
   20 executions);
5. a small batch through the complete pipeline — not started.

---

## Changes made this phase

- `groups/coding_views.py` — `ExecutionContractError` → 409
  `question_not_gradable`, with server-side logging and no detail leakage.
- `groups/test_coding_views.py` — 8 tests: the 409, the two boundaries (503
  stays 503, unexpected stays 500), secrecy, logging, and the structural
  handler guard.
- `groups/test_coding_loop_smoke.py` — new, 6 end-to-end loop tests.

No template changes. No model or migration changes. No production writes.
