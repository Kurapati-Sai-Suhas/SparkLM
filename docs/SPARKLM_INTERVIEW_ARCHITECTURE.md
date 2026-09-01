# SparkLM — Agent Orchestrator Architecture

**Verified 2026-09-01.** Every claim below traces to code in `groups/agent/`,
`groups/coding_views.py`, `groups/shadow.py` or the production database.

> **Boundary, stated first.** The agent is **experimental**. It is reachable,
> tested and demoable, and `AGENT_ORCHESTRATOR_ENABLED` defaults to **`false`**.
> The authoritative production router is the deterministic Traffic Cop path in
> `coding_views.NextProblemView`, and this phase did not change it.

---

## 1. Why agentic orchestration exists

A learner's real question is open-ended — *"what should I work on and why?"* —
and answering it means composing several backend reads: current rating,
uncertainty, curriculum position, and what the bank is actually willing to
serve. Without an orchestrator, every phrasing of that question needs its own
endpoint, and each new signal means touching all of them.

The agent inverts that: the backend exposes a small set of **read** tools, and
the model decides which to call and in what order. The backend keeps every
decision that matters.

## 2. Why a thin orchestrator, and not LangChain/LangGraph

The loop is `observe → plan → tool → result → next action → final`, in about
thirty lines. A framework would add a dependency, a vocabulary and an upgrade
treadmill in exchange for indirection over `while`.

The concrete reasons:

1. **Every guarantee is mine to test anyway.** Bounded calls, stripped
   `commit`, session-held candidates, answer-boundary validation — none of
   these come free from a framework, and testing them through an abstraction
   is harder than testing a `while`.
2. **`planner(observation) -> dict` is a plain callable.** Tests pass a
   scripted planner, so **77 agent tests run with no provider, no key and no
   network**. That is the single biggest reason this component is testable.
3. **Nothing here needs durable execution.** If I needed checkpoint-and-resume
   across process restarts, or multi-agent coordination, I would take
   LangGraph rather than build it. I do not.

## 3. The chain

```
learner request
  → POST /api/ai/agent/            IsAuthenticated · SELF_SCOPED · 12/min
  → AGENT_ORCHESTRATOR_ENABLED?    false → deterministic recommender, done
  → Orchestrator.run()
        get_learner_state          Elo (live) + Glicko-2 (advisory)
                                   + TA-GTKT (research, file-read)
        get_prerequisites          production curriculum DAG
        get_candidate_problems     PUBLISHED + ORACLE_VERIFIED only
        get_problem_context        only for an offered id
  → model returns {"final": ..., "recommend": <id>}
  → BACKEND VALIDATION of that id  ← the answer boundary
  → recommendation object, or the deterministic floor
```

Bounds, all enforced in `orchestrator.py`: `MAX_TOOL_CALLS = 8`,
`TIMEOUT_SECONDS = 30.0` (checked before each call), `MAX_CONSECUTIVE_ERRORS = 3`.

## 4. Candidate restriction — two boundaries, not one

This is the part worth explaining carefully, because the model can reach a
learner **two** ways and each needs its own guard.

**Boundary 1 — the tool call.** `Session.offered_question_ids` holds only ids
the backend returned from `get_candidate_problems` *in this session*, capped at
`max_candidates = 20`. `require_offered()` guards `get_problem_context`,
`get_tutor_context` and `grade_submission`. A guessed id raises `ToolDenied`,
which is **never retried** — a denial is a decision, not a hiccup.

**Boundary 2 — the answer.** A plan may also carry `recommend: <id>`, which has
been nowhere near `require_offered`. `validate_recommendation()` re-checks it
with four independent conditions:

| # | Check | Why it is not redundant |
|---|---|---|
| 1 | offered in this session | stops an invented id |
| 2 | the question exists | stops a stale id |
| 3 | still `PUBLISHED` + `ORACLE_VERIFIED` | **the offered set records what *was* offered.** Trust can change underneath it — promotion and demotion are a separate write path — so membership alone is not enough |
| 4 | still passes `_servable_questions()` | the backend's own serving rule, not a second copy of it |

If any fails, the **whole plan is refused** and the deterministic floor answers.
The learner never sees the rejected id.

> Both the agent answer and the deterministic answer return the same
> `recommendation` object, through the same validator. A caller cannot tell
> "validated" from "we picked it ourselves, so it must be fine".

## 5. How Glicko-2 is used — advisory, read-only

`shadow.current_ability(user, topic)` returns rating, RD and evidence count.
`_glicko_signal` derives a 0–1 `confidence` from RD (fresh learner RD 350 →
well-evidenced RD 50), and the payload is labelled
`"available, UNARMED — not the learner-visible rating"`.

- **Elo remains the routing authority.** Glicko is an extra uncertainty channel
  for the model to reason over, not a second opinion that wins.
- **Reading does not mutate.** `current_ability` applies inactivity inflation
  *for the caller* and persists nothing — merely looking at a learner does not
  age them.
- Do **not** say Glicko replaced Elo. It has not.

## 6. How TA-GTKT is used — research, file boundary

`kt_signal.predict()` **reads a file**. It loads no weights, imports no torch,
and computes nothing.

Loading the checkpoint directly would be wrong twice: it would put torch back
in a 512 MB torch-free web tier, and it would make a research artifact a
deployment dependency — a research experiment could then break a learner's
request.

When no export is configured, the tool returns an explicit
`status: unavailable` with a reason. **It never fabricates a prediction.** Every
available reading also carries `applicability: NOT_TRAINED_ON_SPARKLM` — the
model was trained on ASSISTments 2009 and has never seen a SparkLM learner.

## 7. How the DAG is used

`get_prerequisites` reads `hybrid_router.get_curriculum_graphs()` — the
production graph and its cache. It is never rebuilt here; a second traversal
would be a second answer. Returns `topic`, `subject`, `prerequisites`,
`unlocks`, and a `source` string naming where it came from.

## 8. What the agent can and cannot do

| CAN | CANNOT |
|---|---|
| Choose which of 6 tools to call, and in what order | Widen its own candidate set |
| Phrase the final answer | Recommend a question the backend will not re-validate |
| Recommend a question **from the offered set** | Commit a submission — `commit` is stripped from every payload |
| Read learner state, Glicko, KT, DAG, problem context | Write `expected_output`, `hidden_test_cases`, `status` or `trust_state` |
| Request a grade on an offered question | Change Elo, Glicko or mastery |
| — | Act on another learner (self-scoped endpoint) |
| — | Expose its reasoning |

## 9. Fallback — three layers

1. `{"stop": reason}` from the provider rather than a null answer. *(A real
   bug: `{"final": None}` would have rendered the literal string "None".)*
2. `Orchestrator._stop()` — a backend-only answer with **no model involved**,
   reached on timeout, planner error, unusable plans, too many tool errors,
   a denial, `max_tool_calls`, or a rejected recommendation.
3. Flag off, or the orchestrator itself raises → the deterministic recommender
   in the view.

The response `source` field names which layer answered: `agent`,
`agent_fallback`, `deterministic`, or `unavailable`.

## 10. Observability

One structured line per request, from `_log_decision`:

```
agent decision {"request_id": "310bd43a8475", "learner_id": 1,
  "outcome": "final", "tools_invoked": [...], "tool_call_count": 4,
  "candidates_offered": 5, "selected_question_id": 1436,
  "rejected_recommendation": null, "latency_ms": 5281.0}
```

**Never logged:** secrets, hidden tests, expected outputs, reference source, or
chain-of-thought. The model's reasoning lives in `self._private` and is
deliberately not referenced by the logger — a test asserts a sentinel placed in
`reasoning` never reaches the log. Long string arguments are truncated to
`<N chars>` by `_loggable` so source code cannot leak into a log line.

The whole method is wrapped in `try/except`: **an observability path must never
be able to break a learner's request.**

## 11. Security

- Endpoint requires authentication; registered `SELF_SCOPED` in the
  authorization-matrix test, so it can only act on the caller's own state.
- Provider exceptions are redacted by `_safe()` before logging — `exc_info=True`
  on a provider error can echo a key the SDK included.
- The client receives a `transcript` of short phrases, never reasoning.
- Tool arguments are validated **before** the handler runs.

## 12. Production vs experimental — say this precisely

| Layer | State |
|---|---|
| Traffic Cop → DAG → Elo-matched selection | ✅ **PRODUCTION** — this is what serves learners |
| Elo | ✅ **PRODUCTION** — the live rating |
| Curriculum DAG | ✅ **PRODUCTION** (enforcement flag off) |
| Agent orchestrator | 🧪 **EXPERIMENTAL** — reachable, flag-gated off by default |
| Glicko-2 | 🟡 **SHADOW / ADVISORY** — writes state nothing reads |
| TA-GTKT signal | 🔬 **RESEARCH** — file export, `NOT_TRAINED_ON_SPARKLM` |
| Random Forest routing | ⚠️ **NOT ACTIVE** — no trained artifact exists |
| SAKT / AKT / TA-GTKT-P | ⏳ **FUTURE** — declared `NOT IMPLEMENTED` |

**Do not claim:** that the agent is authoritative production routing; that
Glicko replaced Elo; that TA-GTKT is production-trained on SparkLM; or that the
Random Forest is live.

## 13. The asymmetry worth volunteering

`get_candidate_problems` filters on **`PUBLISHED` + `ORACLE_VERIFIED` only**, so
the agent can offer just the **6** verified questions. The legacy
`_servable_questions()` path serves **1,788**.

**The newer LLM-driven path is the trust-safe one; the older hand-written path
is not.** That is backwards from what an interviewer expects, and it is better
to say it than to have them find it.

## 14. Demo

```bash
python manage.py agent_demo --user <username>
```

Prints the full chain — learner state, Glicko rating/RD/confidence, KT
availability, prerequisite signal, candidate pool, agent loop, **backend
validation**, final recommendation — and ends with `WROTE NOTHING` after
comparing submission and trust-state counts before and after. `--live` uses the
real provider; `--json` emits machine-readable output. No hidden grading data
is printed.
