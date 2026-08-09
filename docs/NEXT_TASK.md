# SparkLM — Next Task

**Updated:** 2026-08-09 · Regenerate at the end of every session.

---

## Current position

**Roadmap: LOCKED.** Execution plan is [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md);
tracker is [`EXECUTION_CHECKLIST.md`](EXECUTION_CHECKLIST.md). Do not redesign
either unless implementation proves an item impossible or technically incorrect.

**Last completed:** M0 / P0.1 — N3 Phase 1a merged to `main` as `5a57565`,
CI green on all three jobs, deployed and verified.

**Phase:** M1 / **P1.1 — Retire the un-runnable ML stack.**
**Blocked on:** nothing.

**Premise:** Track A (pre-launch). **Track B trigger:** >20 real users or any
launch announcement — at which point ~52 ed of capacity, compliance and
accessibility work runs *before* remaining feature work.

---

## Next task — P1.1: Retire the un-runnable ML stack

- **Branch:** `m1-p1-retire-ml-stack`
- **Effort:** 3 ed
- **Risk:** LOW-MEDIUM
- **Goal:** remove deep-learning code that cannot execute on the web tier,
  plus its ~2 GB dependency tier.

**Why this is safe to delete, and why it is independent of the M6 measurement:**
the GCN feeds *explainability only* — it is reached solely through
`_compute_shap_xai`, gated by `ENABLE_SHAP_XAI=false`, with a proven heuristic
fallback. Routing uses sklearn (`RoutingClassifier`) and NetworkX
(`HierarchicalEngine`), both core dependencies that stay. `requirements-ml.txt`
is deliberately not installed by `render.yaml`, so none of this can run in
production today.

**Tasks** (detail in `EXECUTION_ROADMAP.md` §M1):
1. T1.1.1 Delete `gnn_engine`, `shap_explainer`, `export_onnx`, `mirt_engine`
2. T1.1.2 Delete `retrain_ai`, `synthetic_data_generator`, `models_data/*`
3. T1.1.3 Collapse `_compute_xai` to the heuristic branch — **pin the response
   schema first**; the frontend radar chart reads `shap_values`
4. T1.1.4 Delete `requirements-ml.txt`; simplify CI install
5. T1.1.5 Remove `ENABLE_SHAP_XAI` from `settings.py` and `render.yaml`
6. T1.1.6 Record CI install-time before/after

**Highest risk in this phase:** XAI response-schema drift. Write the
schema-pinning test *before* deleting anything.

---

## Open items from P0.1 (not P1.1 scope)

- [ ] **Signed-in smoke test.** `/schedule`, `/notifications`, `/settings`
      rendering real data while logged in was not verified — it needs a login.
      Signed-out verification is complete. **Owner: you.**
- [ ] **`preserve/settings-email-2xx-test`** (`d7b0e20`) — parked test awaiting
      a keep-or-drop decision.
- [ ] **Pre-existing deployed console errors** — CSP blocks
      `accounts.google.com/gsi/style` (`style-src` in `vercel.json` omits it)
      and Google Sign-In returns 400. Untouched by P0.1; log as debt.

---

## Commit strategy

One logical change per commit; message body explains **why**. No Claude
attribution. One phase = one branch = one PR = one revert. Merge commits only —
never squash, never rebase. CI green before merge; `main` stays deployable.
