# Agent / KT integration map

**Phase 20.11F · read-only audit · nothing modified**

Where the pieces are today, and the smallest seam an orchestrator could use.

---

## What exists

| Concern | File | Lines | Notes |
|---|---|---|---|
| **Glicko-2 rating** | `groups/glicko.py` | 220 | `rate()` consumes `periods_inactive` |
| Rating history | `groups/glicko_history.py` | 166 | idempotent on `submission_id` |
| **Shadow adaptive model** | `groups/shadow.py` | — | **UNARMED — reaches no learner** |
| **Routing / selection** | `groups/hybrid_router.py` | 459 | carries `ROUTING_POLICY_VERSION` |
| Selection endpoint | `groups/coding_views.py::NextProblemView` | — | still P2.8a ordering |
| **Learner state** | `groups/models.py` | — | see the five models below |
| **Curriculum DAG** | `groups/hybrid_router.py` + `seed_dsa_dag.py` | — | NetworkX, cached; `invalidate_dag_cache(subject)` on edge change |
| **Judge0 execution** | `groups/services.py` (`GradingService`) | 948 | the shared seam: `_build_executable` + `prepare_stdin` |
| Contract/harness | `groups/execution_contract.py`, `groups/execution_adapter.py` | — | v1/v2/v3 |
| **LLM client** | `groups/ai_services.py` | 588 | Gemini + Groq, quota-aware |
| Coach (agent-ish) | `groups/engines/agentic_coach.py` | — | posts to an n8n webhook |
| Other engines | `groups/engines/{elo,hlr,tensor_builder}.py` | — | |

### Learner-state models

```
UserCodingProfile      groups_usercodingprofile     elo_rating lives here
CodeSubmission         groups_codesubmission        carries adaptive_eligible (frozen at write)
UserTopicMastery       groups_usertopicmastery
LearnerTopicSkill      groups_learnertopicskill
GlickoSnapshot         groups_glickosnapshot
Profile                groups_profile
```

### KT — a project already exists

**`kt_dataset/`** is a full package, not a stub:

```
schema.py  sources.py  adapters.py  validation.py  pipeline.py (371)  stats.py
```

with supporting modules in `groups/`:

```
kt_features.py (241)   kt_readiness.py (436)   kt_leakage.py (232)
management/commands/kt_data_readiness.py
management/commands/kt_dataset_build.py
```

`kt_leakage.py` and `kt_readiness.py` matter most: there is already an
explicit position on **temporal leakage** and on whether the data is ready to
train on. Any KT work should start by reading those rather than re-deriving
them — `test_kt_readiness.py` is the file whose flake was fixed in an earlier
phase by pinning a module-level clock.

---

## The smallest integration seam

Two functions, one write path and one read path.

### Write — where a submission becomes learner signal

```
groups/coding_views.py:504
    submission, elo_result, profile = ProgressionService.apply_submission(...)
                                      groups/services.py:568
```

Everything a learner does converges here. An orchestrator that wants to
observe or enrich learner state attaches at `ProgressionService.apply_submission`
and nowhere else — that single call is what turns a graded run into rating,
mastery and history.

`groups/shadow.py::apply_submission` is the precedent: a parallel consumer of
the same evidence that reaches no learner. It already enforces the two rules
any new consumer needs — **only `adaptive_eligible` submissions**, and only
**conceptually evaluable verdicts** (a compile error is not evidence about a
concept).

### Read — where a question is chosen

```
groups/coding_views.py:576   NextProblemView
groups/hybrid_router.py      the policy, versioned
```

An orchestrator that wants to influence *what comes next* attaches at the
router, behind `ROUTING_POLICY_VERSION`, not inside the view.

### Why these two and not more

They are the only places where the trust boundary is already enforced.
`adaptive_eligible` is frozen onto `CodeSubmission` at write time, so a
consumer reading submissions inherits the P2.7c guarantee for free. A seam
anywhere else would have to re-derive it, and a consumer that re-derives a
trust rule is a consumer that can get it wrong.

---

## Recommended shape, if an orchestrator is built

1. **Start unarmed**, as `shadow.py` did. Run beside production on the same
   evidence, compare, promote nothing until the comparison is boring.
2. **Consume, do not compute trust.** Read `adaptive_eligible`; never
   recompute it.
3. **Version the policy**, as the router already does, so a change in
   behaviour is attributable.
4. **One LLM client.** `ai_services.py` already handles provider fallback and
   quota exhaustion; a second client would be a second place for a quota bug.

Nothing above is implemented, and this phase modified no file.
