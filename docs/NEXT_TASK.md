# SparkLM — Next Task

**Updated:** 2026-08-09 (P1.1 closure) · Regenerate at the end of every session.

---

## Current position

**Roadmap: LOCKED.** Execution plan is [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md);
tracker is [`EXECUTION_CHECKLIST.md`](EXECUTION_CHECKLIST.md). Do not redesign
either unless implementation proves an item impossible or technically incorrect.

**Last completed:** M1 / P1.1 — un-runnable ML stack retired, merged `40b4ba5`
(PR #7). CI green on all three jobs, deployed. **COMPLETE + FULLY VERIFIED**,
including authenticated production verification of the XAI panel on
`/coding-portal` (2026-08-09).

**Phase:** M1 / **P1.2 — Collapse coding surfaces and remove Flashcards.**
**Blocked on:** nothing.

**Premise:** Track A (pre-launch). **Track B trigger:** >20 real users or any
launch announcement — at which point ~52 ed of capacity, compliance and
accessibility work runs *before* remaining feature work.

---

## Next task — P1.2: Collapse coding surfaces; remove Flashcards

- **Branch:** `m1-p2-collapse-surfaces` · **Effort:** 2 ed · **Risk:** LOW
- Redirect `/code` → `/coding-portal`, delete `components/CodingPortal.tsx`
- Remove Flashcards page, `AIFlashcardView`, its URL and nav entry
- Shrink the Phase 1b allowlist 6 → 5

**Carry forward from P1.1:** the roadmap's file lists have already proven
unreliable once. Run the same repo-wide caller proof before deleting anything,
and do not trust a filename to tell you what a module does.

## Observations from the P1.1 production verification

**Not defects, not P1.1 scope, and deliberately not tasks.** Each already has an
owner in the locked plan. Recorded so the next session does not re-diagnose them.

| Observation | Owner |
|---|---|
| Page-load latency on Coding Hub / Adaptive Portal. Warm backend measures 0.25–0.38 s, so this is **not** a P1.1 regression. Cold start dominates: `keepalive.yml` records ping gaps of mean 104.5 min against Render's ~15 min idle timeout — a ~14% warm duty cycle. **Primarily a hosting-tier issue.** | Track B (if triggered) |
| `/api/code/next/` is the warm-path hotspot — materialises every solved id (D11), and D12 | P3.3 |
| The portal fires 4 uncached API calls that serialise on one worker | P9.2 |
| `LiveCollaborativeWorkspace` ships a 2,591 KB dead chunk | P9.3 |
| UI labels the panel "SHAP" though SHAP is deleted and the backend returns `source: "heuristic"` | **M2 / Product Truth** |

The SHAP-label fix is already written: branch `p1-1-followup-xai-labels`
(`4f89c3e`, CI green), **closed PR #8**, deferred to M2 by owner decision.
`gh pr reopen 8` when M2 starts. It also covers "Calibrating PyTorch tensor
state…", "optimized by the PyTorch GNN engine" and "GNN tensor calibration".

## Still open from P0.1 (not P1.1 scope)

- [ ] **Signed-in smoke test of `/schedule`, `/notifications`, `/settings`.**
      Still open — the P1.1 verification covered `/coding-portal` only, so this
      is *not* closed by it. ~30 seconds while signed in. **Owner: you.**
- [ ] **`preserve/settings-email-2xx-test`** (`d7b0e20`) — parked test awaiting
      a keep-or-drop decision.
- [ ] **Pre-existing deployed console errors** — CSP blocks
      `accounts.google.com/gsi/style` (`style-src` in `vercel.json` omits it)
      and Google Sign-In returns 400. Untouched by P0.1 and P1.1; log as debt.

---

## Commit strategy

One logical change per commit; message body explains **why**. No Claude
attribution. One phase = one branch = one PR = one revert. Merge commits only —
never squash, never rebase. CI green before merge; `main` stays deployable.
