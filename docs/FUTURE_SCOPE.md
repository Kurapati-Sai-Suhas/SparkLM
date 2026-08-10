# SparkLM — Future Scope (1–3 years)

**Updated:** 2026-08-06. Nothing here is scheduled. Items move into
`MASTER_ENGINEERING_ROADMAP.md` only when their entry condition is met.

**Entry rule:** an item leaves this document when there is (a) a user problem
it demonstrably solves and (b) enough traffic that the answer is not obvious
without it. Building personalisation machinery before there are learners to
personalise for is how the current dormant ML stack came to exist.

---

## Deprecated from the previous roadmap

These were scheduled in `ROADMAP_V2.md` as M11–M14. They are research-grade
personalisation on a product whose dashboard cannot yet count a quiz, and whose
GNN has never been measured against the Elo baseline.

| Was | Item | Entry condition |
|---|---|---|
| M11 | Belief layer (GDCP retirement) | GDCP shown to be measurably wrong |
| M12 | Propensity logging + calibration study | ≥10k logged recommendations |
| M13 | Thompson router + OPE harness | ≥2 routing policies with measured lift |
| M14 | Misconception fingerprints | Question bank tagged at E6 depth |

**Also on probation:** the GNN/GCN stack (9 artifacts in `models_data/`), SHAP
XAI, and visual-search embeddings. None can execute on the web tier —
`requirements-ml.txt` is deliberately not installed there. Entry condition:
an offline eval harness shows routing lift over Elo. Otherwise, retire.

---

## Tier 1 — Natural extensions (12–18 months)

**AI Interviewer.** Timed mock interviews with follow-up questioning and a
rubric-scored transcript. Depends on E6 (interview notes, company tags).
Highest product differentiation per unit of effort. ~20 ed.

**Contest platform.** Time-boxed rounds, live leaderboard, per-question
scoring. `CodeSubmission` is already partitioned and Elo already exists; the
blocker is grading throughput (E3) — a contest is a thundering herd by design.
~25 ed, gated on async grading.

**Career readiness.** A composite score from topic coverage, difficulty
distribution and company tags, with a gap analysis. Cheap once E6 lands, and it
turns practice into a narrative. ~10 ed.

**Real vector search.** `pgvector` and `Document.feature_vector` already exist
and are unused for retrieval; today's "RAG" stuffs every chunk into the model
context. Replacing it cuts token cost and removes a hard document-size limit.
~8 ed.

**Mobile.** PWA first — installable, offline problem reading, background sync
of submissions. Native only with evidence of demand. ~15 ed PWA.

---

## Tier 2 — Platform (18–30 months)

**Knowledge graph.** Generalise `TopicPrerequisite` (already a validated DAG
with cycle detection) into a concept graph spanning questions, materials and
misconceptions. Enables "why am I stuck" explanations. ~30 ed.

**AI agents / MCP.** An agentic tutor that can read your submission history,
run code and assign practice. Only valuable once the data it would reason over
is real — i.e. after E2. ~25 ed.

**Voice tutor.** Speech in/out over the existing doubt-solver. Accessibility
win, modest engineering. ~12 ed.

**Whiteboard.** Collaborative diagramming for system-design practice; the
CRDT code-collab consumer is a partial precedent. ~20 ed.

**Multimodal learning.** Handwritten notes and photographed problems into the
same pipeline; `VisualSearch` and Gemini vision are the seed. ~15 ed.

---

## Tier 3 — Business (24–36 months)

**Recruiter dashboard / company assessments / placement portal.** Employer-side
surfaces built on candidate performance. This is a different product with a
different buyer and different compliance obligations (candidate consent, data
retention, anti-discrimination review of any ranking). Do not start without a
commercial commitment.

**Enterprise.** SSO/SAML, org hierarchies, audit logs, per-tenant isolation,
SLAs. Each item is weeks; together they are a re-platforming. The current
single-instance free-tier topology is not a starting point for this.

**Offline mode.** Full offline practice with conflict-free sync. Expensive and
rarely used on desktop-first study products; the PWA covers most of the value.

---

## Standing infrastructure candidates

| Item | Trigger |
|---|---|
| Move off Render free tier | first sustained concurrency complaint, or Phase 2 measurement |
| Celery/queue tier | when async grading is justified by data |
| Read replica | when dashboard queries measurably contend with writes |
| CDN for media | when object storage egress becomes visible |
| Feature-flag service | when flags exceed ~10 and outlive their rollout |
