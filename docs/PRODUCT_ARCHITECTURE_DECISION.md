# SparkLM — Product Architecture Decision (Epic E1)

**Date:** 2026-08-06 · **Status:** decision of record · **Supersedes:** the
implicit "keep everything" scope in `ARCHITECTURE_V2.md`.

This is the blocking decision the revised roadmap identified. It determines
what SparkLM *is*, and therefore what every later epic costs.

---

## The thesis

> **SparkLM is an adaptive coding-practice platform where every submission
> updates a learner model, and the next problem is chosen by that model, with
> the reasoning shown to the learner.**

That combination — real code execution, an Elo/IRT skill estimate, SM-2/HLR
memory decay, a prerequisite DAG, and an explainability layer — is not what
LeetCode, HackerRank or Duolingo do. It is the only differentiated thing in
this repository.

**Everything that serves the thesis is CORE. Everything else is overhead**,
and overhead is what has prevented anything from being finished.

## The problem being solved

One engineer built a social network, a study-group platform, a quiz engine, a
flashcard system, a document RAG system, a visual search system, a
collaborative editor and an adaptive coding platform. 11 sidebar items, three
coding surfaces, three AI surfaces, and at least one route
(`/collab/:groupId`) linked from nowhere. The result is that almost nothing is
finished, and the dashboard shows hardcoded zeros.

---

## Classification

| System | Verdict | One-line reason |
|---|---|---|
| Adaptive Coding (solve surface) | ✅ CORE | The thesis |
| Coding Hub (browse surface) | ✅ CORE | Entry point to the thesis |
| Coding Portal `/code` | 🔴 REMOVE | Third door to the same room; last raw-axios consumer |
| Judge0 integration | ✅ CORE | Execution is non-negotiable |
| Question Bank | ✅ CORE | 7 fields today; the ceiling on everything |
| Adaptive Learning (Elo/IRT/HLR/SM-2) | ✅ CORE | The learner model |
| Recommendation Engine (router/DAG) | ✅ CORE | Measured in E5, kept on merit |
| Knowledge Graph (`TopicPrerequisite`) | ✅ CORE | Runs in production; curriculum backbone |
| Dashboard | ✅ CORE | Where the learner model becomes visible |
| Progress Tracking | ✅ CORE | Same |
| Analytics | ✅ CORE | Rebuilt on submissions, not quiz buckets |
| Gamification / Achievements | ✅ CORE | Cheap retention; currently inert |
| Notifications | ✅ CORE | Rescoped to learning events only |
| AI Tutor | ✅ CORE | **Repositioned**: from document Q&A to code tutoring |
| Admin Portal | ✅ CORE | Becomes the question-authoring surface in E4 |
| WebSockets | ✅ CORE | Notifications consumer only |
| Career Readiness | ✅ CORE *(future)* | Unblocked by E4 tags |
| Calendar / Schedule | 🟡 MERGE | Becomes the review schedule |
| Study Groups | 🟠 FREEZE | Works; undifferentiated; authorization anchor |
| Friends | 🟠 FREEZE | With Study Groups |
| Group Chat | 🟠 FREEZE | With Study Groups |
| Quiz + QuizTaking | 🟠 FREEZE | Works; redundant with solving problems |
| RAG | 🟠 FREEZE | Real but not the thesis; rename to stop overclaiming |
| Document Processing | 🟠 FREEZE | Feeds frozen RAG |
| File Library | 🟠 FREEZE | Feeds frozen RAG |
| MLOps telemetry | 🟠 FREEZE | Routing telemetry survives; GNN half dies |
| Flashcards | 🔴 REMOVE | Second spaced-repetition loop competing with Review Queue |
| Direct Chat | 🔴 REMOVE | Duplicates group chat; not an authorization anchor |
| Collaborative Workspace | 🔴 REMOVE | Orphaned, unlinked, unpersisted |
| Visual Search | 🔴 REMOVE | `transformers` absent from the web tier — cannot run |
| ML Models / GNN / SHAP / ONNX | 🔴 REMOVE | `torch` absent from the web tier — cannot run; never measured |

## Removal budget

| Removal | Lines | Also removes |
|---|---|---|
| GNN + SHAP + ONNX + MIRT engines | 494 | 256 KB artifacts |
| `retrain_ai` + `synthetic_data_generator` | 378 | offline training path |
| Collaborative Workspace + `CodeCollabConsumer` | ~460 | 1 WS consumer, 1 route |
| Direct Chat + `messages_views` | ~430 | 1 model, 2 endpoints |
| Flashcards + `AIFlashcardView` | ~390 | 1 endpoint |
| Visual Search + `VectorSearchService` | ~370 | 2 endpoints, CLIP |
| Coding Portal `/code` | ~200 | last raw-axios file |
| **Total** | **≈ 2,700** | **plus `requirements-ml.txt` (~2 GB): torch, torch-geometric, onnxruntime, shap, transformers** |

`pgvector` is **retained** — real retrieval (E6) will want it.

**Nav goes from 11 items to 6.** Three coding surfaces become two with distinct
jobs. Three AI surfaces become one.

## Merges

**Coding Portal → Adaptive Coding.** Duplication exists because `/code` was the
original portal and `/coding-portal` replaced it without deleting it.
*Survives:* `AdaptiveCodingPortal.tsx` (598 lines, carries the XAI payload).
*Deleted:* `components/CodingPortal.tsx`. *Migration:* redirect `/code`; ~1 ed.
Side benefit: this is the only file still importing axios directly, so the
Phase 1b allowlist shrinks by one for free.

**Schedule → Review Schedule.** A generic calendar (`StudySession`: title,
start, duration) is peripheral; a *review* schedule is thesis-aligned and the
HLR engine already computes due dates. *Survives:* the Schedule page shell.
*Deleted:* nothing yet — it gains review items as its primary content.
*Migration:* ~2 ed.

---

## Impact on the roadmap

| Epic | Change |
|---|---|
| E2 Product truth | **−3 ed.** Fewer surfaces to make truthful. Notifications rescoped to learning events. |
| E3 Coding platform | **−1 ed.** Two surfaces instead of three. |
| E4 Question bank | unchanged (25 ed) — now the largest epic, correctly |
| E5 Adaptive learning | **−4 ed.** Measurement gate only; GNN removal is the default outcome |
| E6 AI quality | **−4 ed.** RAG frozen; AI effort redirected to code tutoring |
| E8 Frontend | **−4 ed.** Consolidation partly achieved by deletion |
| **E1a Execute removals** | **NEW: 5 ed** |

Net: ~118 ed → **~107 ed**, with a materially smaller surface to maintain.

---

## What this buys

- **~2,700 fewer lines** and a ~2 GB dependency tier gone from CI and dev setup.
- **One coherent product story** instead of eight partial ones.
- **Honesty**: nothing shipped that cannot run in production.
- Every remaining feature is either the thesis, or explicitly frozen and
  labelled as such.
