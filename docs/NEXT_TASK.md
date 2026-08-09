# SparkLM — Next Task

**Updated:** 2026-08-09 · Regenerate at the end of every session.

---

## Current position

**Roadmap: LOCKED.** Execution plan is [`EXECUTION_ROADMAP.md`](EXECUTION_ROADMAP.md);
tracker is [`EXECUTION_CHECKLIST.md`](EXECUTION_CHECKLIST.md). Do not redesign
either unless implementation proves an item impossible or technically incorrect.

**Last completed:** M1 / P1.1 — un-runnable ML stack retired, merged `40b4ba5` (PR #7), CI green, deployed and verified.
CI green on all three jobs, deployed and verified.

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
