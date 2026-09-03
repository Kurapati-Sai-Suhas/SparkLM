# Traffic Cop — Code Audit, Literature Review, Architecture Synthesis

**Created:** 2026-09-01 · **HEAD:** `097f67d` · **Status:** research, no code changed

Three stages: what the code actually does, what the literature says, and what
follows from putting the two together.

---

# Stage 1 — Current implementation audit

Verified by reading `groups/hybrid_router.py`, `learning/router.py`,
`groups/coding_views.py`, `groups/management/commands/retrain_ai.py`, and by
executing the real functions against the production database.

| # | Item | Verified value |
|---|---|---|
| 1 | Routing inputs | `avg_acc`, `runs_z`, sample size from the last 20 **adaptive-eligible** submissions; `target_elo` |
| 2 | Feature engineering | `outcome_stats()` → mean of binary outcomes + Wald–Wolfowitz runs-test z. `avg_elo = elo / 2000.0` |
| 3 | Routing rules | `runs_z > 1.96` **or** `avg_acc < 0.60` → `flat`; else `hierarchical` |
| 4 | RF implementation | `RandomForestClassifier(n_estimators=100, random_state=42)`; all other params sklearn defaults |
| 5 | RF artifact exists | **NO.** `models_data/` is empty, gitignored, never committed |
| 6 | RF executed | **NO.** `RoutingClassifier().clf is None` verified live |
| 7 | Heuristic | `decide_route()`, `learning/router.py:76` |
| 8 | Thresholds | `OSCILLATION_Z_THRESHOLD = 1.96`, `ACCURACY_THRESHOLD = 0.60`, `MIN_RUNS_TEST_N = 5` |
| 9 | Routing target | RF predicts **`actual_result_correct`** (binary success), scored once per engine; the route is `argmax`. Not a route class |
| 10 | Training labels | `RecommendationLog.actual_result_correct`, written only when `adaptive_eligible` |
| 11 | Dataset size | **35 labelled** of 215 logs (9 positive / 26 negative). Gate requires ≥100 |
| 12 | Evaluation results | **NONE.** `docs/evals/` contains only `.gitkeep` |
| 13 | Production path | `coding_views.py:640-644`; also `hybrid_router.py:399`, `common/review_views.py:52` |
| 14 | Fallback | `decide_route()` whenever `clf` is absent or fails the `n_features_in_ == 4` guard |
| 15 | Latency | **Now instrumented** (M2 P2.24) — `latency_ms` on every routing decision. No historical figure exists: instrumentation post-dates every recommendation served so far |
| 16 | Observability | **Structured since M2 P2.24** — `routing decision {json}` carrying route, telemetry, `decided_by`, `cold_start`, `policy_version`, `elo`, `latency_ms`. Was one unstructured sentence |
| 17 | Known weaknesses | See §Stage 1 findings below |
| 18 | Data limitations | 0 adaptive-eligible submissions ⇒ telemetry is constant for every learner |
| 19 | Leakage risks | `avg_elo` uses **current** Elo, not point-in-time — rating history is not persisted. Documented in `retrain_ai.py` |
| 20 | Agent ↔ Traffic Cop | **The agent NEVER calls Traffic Cop.** It imports `hybrid_router` only for `get_curriculum_graphs()`. No `RoutingClassifier`, no `compute_routing_telemetry`, no `decide_route` |

### The finding that matters most

`compute_routing_telemetry` filters on `adaptive_eligible=True`. Production has
**zero** such submissions. So `statuses` is always empty and the function
returns its cold-start triple `(0.7, 0.0, 0)` for **every learner**.

Then `decide_route(0.7, 0.0)` → `0.0 > 1.96` is false, `0.7 < 0.60` is false →
`hierarchical`.

> **Verified by executing against production: all 22 users return
> `(0.7, 0.0, 0)` and all 22 route `hierarchical`. The Traffic Cop is currently
> a constant function.**

Historically it did discriminate — `RecommendationLog.engine_used` shows 160
hierarchical / 55 flat — from before the trust gate closed the telemetry input
on 2026-08-11.

**This is not a routing bug.** It is the content-trust gate working as designed:
routing telemetry is the learner model's view of recent performance, and a
verdict from an unverified question may be the platform's defect rather than
the learner's mistake. The router correctly refuses to act on evidence it
cannot trust. **But the consequence is that routing is inert, and no amount of
model work changes that.**

---

# Stage 2 — Literature review

Six sources, each read rather than cited from memory. Anything not stated in
the source is marked NOT STATED rather than filled in.

## S1 — Learning outcomes are rarely what educational recommenders optimise

**Askarbekuly, N., & Luković, I. (2024).** *Learning Outcomes, Assessment, and
Evaluation in Educational Recommender Systems: A Systematic Review.* arXiv
preprint, cs.HC. [arXiv:2407.09500](https://arxiv.org/abs/2407.09500)

| | |
|---|---|
| Dataset | 1,395 papers screened → **28 analysed** |
| Mechanism | Systematic review of ERS evaluation practice |
| Key results | "Rating-based relevance is the most popular target metric"; "**less than a half** of papers optimize learning-based metrics"; "**Only a third** of the papers used outcome-based assessment to measure the pedagogical effect" |
| Strength | Directly quantifies a methodological gap rather than asserting it |
| Weakness | 28 papers is a small final corpus; scope is ERS, not ITS generally |
| **SparkLM borrows** | Validation that `RecommendationLog.actual_result_correct` — a real *outcome* label, not a rating — is the right target. Most of the field does not have this |
| **SparkLM must NOT copy** | Rating-based or engagement proxies as the routing target |

## S2 — Simple KT baselines are extremely hard to beat

**Liu, Z., Liu, Q., Chen, J., Huang, S., & Luo, W. (2023).** *simpleKT: A
Simple But Tough-to-Beat Baseline for Knowledge Tracing.* **ICLR 2023.**
[arXiv:2302.06881](https://arxiv.org/abs/2302.06881)

| | |
|---|---|
| Dataset | 7 public KT datasets |
| Model | Ordinary dot-product attention; deliberately no bespoke forgetting machinery |
| Key result | "**57 wins, 3 ties and 16 loss** against 12 DLKT baseline methods on 7 public datasets"; ranks top-3 on AUC |
| Strength | Large head-to-head sweep, published at a top venue |
| Weakness | AUC-centric; calibration not the focus |
| **SparkLM borrows** | Direct external support for our own measured result — Transformer vs DKT was 1.3σ, i.e. indistinguishable. That is the *expected* outcome, not a failure |
| **SparkLM must NOT copy** | Chasing architectural novelty for AUC |

## S3 — Deep KT models often do not beat traditional ones, and replication is poor

**Sarsa, S., Leinonen, J., & Hellas, A. (2021/2022).** *Empirical Evaluation of
Deep Learning Models for Knowledge Tracing: Of Hyperparameters and Metrics on
Performance and Replicability.* Journal of Educational Data Mining.
[arXiv:2112.15072](https://arxiv.org/abs/2112.15072)

| | |
|---|---|
| Models | Vanilla-DKT, 2× LSTM-DKT, 2× DKVMN, SAKT vs non-learning baselines, logistic regression, BKT |
| Key results | "DLKT models generally outperform more traditional models, **but not necessarily by much and not always**"; "**even simple baselines with little to no predictive value may outperform DLKT models, especially in terms of accuracy**" |
| Also | Examines metric choice, input/output layer variants, hyperparameters, **random seeding**, hardware; documents "issues in replicability… discrepancies in prior reported results and methodology" |
| Strength | Explicitly about seeds and replicability — rare and load-bearing |
| Weakness | Exact AUC figures NOT STATED in the abstract |
| **SparkLM borrows** | Vindicates measuring the seed noise floor (±0.0017 AUC) *before* believing a 0.0022 gap. Also vindicates keeping BKT as an interpretable floor |
| **SparkLM must NOT copy** | Single-seed comparisons |

## S4 — Bandits underperform exactly in SparkLM's regime

**Song, H., Musabirov, I., Bhattacharjee, A., Durand, A., Franklin, M.,
Rafferty, A., & Williams, J. J. (2025).** *Adaptive Experiments Under Data
Sparse Settings: Applications for Educational Platforms.* arXiv preprint,
cs.LG. [arXiv:2501.03999](https://arxiv.org/abs/2501.03999)

| | |
|---|---|
| Problem | "standard adaptive strategies such as **Thompson Sampling often underperform** in real-world educational settings where content variations are numerous and **student participation is limited, resulting in sparse data**" |
| Model | WAPTS — Weighted Allocation Probability Adjusted Thompson Sampling |
| Result | "enables earlier and more reliable identification of promising treatments" in a learnersourcing scenario |
| Exact sample sizes | **NOT STATED** |
| Strength | Names the failure mode SparkLM is in, from the education side |
| Weakness | Learnersourcing ratings, not problem routing |
| **SparkLM borrows** | Strong evidence *against* jumping to a bandit router. The README already cites Thompson (1933) as a future direction — this says the naive version would underperform here |
| **SparkLM must NOT copy** | Deploying Thompson sampling at 44 submissions |

## S5 — LLM agents in education: hallucination and overreliance are the named risks

**Chu, Z., Wang, S., Xie, J., Zhu, T., Yan, Y., Ye, J., Zhong, A., Hu, X.,
Liang, J., Yu, P. S., & Wen, Q. (2025).** *LLM Agents for Education: Advances
and Applications.* **EMNLP 2025 Findings.**
[arXiv:2503.11733](https://arxiv.org/abs/2503.11733)

| | |
|---|---|
| Scope | Survey; feedback generation, curriculum design, domain-specific agents |
| Key result | Names "**hallucination and overreliance**" among the key deployment challenges in educational settings |
| Guardrails / agent-vs-backend split | **NOT STATED** in the abstract |
| Strength | Peer-reviewed venue; confirms the risk SparkLM's two boundaries address |
| Weakness | Survey, not a comparative evaluation. Does not prescribe an architecture |
| **SparkLM borrows** | Confirmation that hallucination is *the* named risk, and that the literature does **not** yet prescribe where the agent belongs — so a conservative, bounded design is defensible rather than behind the curve |
| **SparkLM must NOT copy** | Any claim that agentic tutoring is an established best practice |

## S6 — Optimal difficulty has an empirical anchor, with a scope caveat

**Wilson, R. C., Shenhav, A., Straccia, M., & Cohen, J. D. (2019).** *The
Eighty Five Percent Rule for optimal learning.* **Nature Communications**,
10:4646. [DOI 10.1038/s41467-019-12552-4](https://www.nature.com/articles/s41467-019-12552-4)

| | |
|---|---|
| Key result | Optimal training **error rate ≈ 15.87%**, i.e. accuracy ≈ **85%**, for stochastic gradient-descent based learning algorithms |
| Demonstrated on | Artificial neural networks and biologically plausible neural networks |
| Strength | Gives a mathematical footing to "zone of proximal difficulty" |
| **Scope caveat** | Derived for SGD-class learners on simple perceptual-decision tasks. It is **not** a measured result about humans solving algorithmic programming problems |
| **SparkLM borrows** | A defensible anchor for the target success band. `shadow.TARGET_SUCCESS_PROBABILITY = 0.7` is currently labelled in code as "a convention from the mastery-learning literature, NOT a measured value for this platform" — this is at least a citable reference point |
| **SparkLM must NOT copy** | Silently changing 0.7 → 0.85 as if the paper measured our task. It did not |

---

# Stage 3 — Synthesis

## Capability comparison

| Capability | Current SparkLM | Literature | Gap | Possible improvement |
|---|---|---|---|---|
| Routing target | Binary success, real outcomes | Most ERS optimise ratings (S1) | **SparkLM is ahead** | Keep; do not switch to engagement |
| Learner state | Elo + runs-test + mastery + DAG | KT-state-driven (S2, S3) | KT not in the loop | R2/R3 once labels exist |
| Uncertainty | Glicko RD computed, **unused** in routing | Uncertainty-aware selection is standard | Signal exists, unread | Feed RD as a *confidence* input, not a rating swap |
| Difficulty targeting | Elo-nearest; shadow targets 0.7 | ~85% optimal for SGD learners (S6) | Target is a convention | Measure the realised success rate per band before changing it |
| Exploration | None | Bandits — but they underperform when sparse (S4) | Not a gap yet | Defer |
| Model complexity | Heuristic; RF unused | Simple baselines are tough to beat (S2, S3) | **Not a gap** | Do not add capacity without labels |
| Evaluation | 0 recorded routing experiments | Outcome-based assessment rare (S1) | **Real gap** | Structured logging first (X4) |
| Agent role | Parallel path, bypasses Traffic Cop | No prescribed placement (S5) | Architecturally unresolved | See decision below |

## The ten questions, answered

1. **Is deterministic routing still appropriate?** **Yes.** S2/S3 show complex
   models barely beat simple ones with abundant data; SparkLM has none. S4 shows
   the adaptive alternative underperforms precisely in sparse settings.
2. **What does it do well?** It is interpretable, O(1), needs no artifact,
   cannot fail to load, and encodes a real statistical idea — the runs test
   distinguishes *oscillating* from *streaky* at equal accuracy, which mean
   accuracy structurally cannot.
3. **What does it fail to model?** Per-topic state (it is global), uncertainty
   (RD is computed and ignored), forgetting (half-life exists but does not feed
   routing), item difficulty beyond Elo distance, and any notion of *why* a
   learner is failing.
4. **Is Random Forest worth training?** **Not yet, and not on this data.** 35
   labels, all produced before the trust gate — i.e. from unverified answer
   keys. Training would import the exact corruption the content programme
   exists to remove.
5. **What data does RF require?** ≥100 labelled rows *that are adaptive-eligible*.
   That needs trusted questions first. The gate is doubly closed.
6. **Is an agent useful here?** **Yes, but not as a router.** Its value is
   composing an explanation over several backend reads and answering open
   questions — not choosing between two engines, which is a one-line decision.
7. **Should the agent replace the heuristic?** **No.** S5 names hallucination as
   the key risk; an LLM in the routing path adds latency, non-determinism and a
   provider dependency to a decision that is currently free and provably
   terminating.
8. **Should the agent call Traffic Cop?** **Yes — this is the concrete gap.**
   Today it does not, so the agent and the production router can disagree about
   the same learner with no reconciliation.
9. **Should the agent consume Traffic Cop's decision as one signal?** **Yes.**
   Expose `route`, `avg_acc`, `runs_z`, `n` as a read-only tool alongside
   Glicko/KT/DAG. The agent explains and personalises; it does not overrule.
10. **Should Traffic Cop become a deterministic safety layer beneath the agent?**
    **Effectively yes** — that is already the shape. The backend owns candidates
    and validates the answer; Traffic Cop should own the *route* on the same
    terms.

## Recommended architecture

```
learner state (Elo · Glicko RD · retention · KT-if-available)
        ↓
   TRAFFIC COP            ← deterministic, authoritative, always runs
        ↓
   CURRICULUM DAG
        ↓
   TRUSTED CANDIDATES     ← the single trust filter, both paths
        ↓
   AGENT (optional)       ← consumes the route as a SIGNAL; explains, personalises
        ↓
   BACKEND VALIDATION     ← already built
        ↓
   recommendation
```

**Agent BESIDE / ABOVE-as-explainer, never INSIDE or INSTEAD.** Chosen on
evidence, not novelty: the literature does not prescribe agent placement (S5),
the sparse-data regime punishes learned routers (S2, S3, S4), and the
deterministic path is the one that cannot fail to load.

## What this implies, in priority order

1. ~~**`get_routing_signal` tool**~~ — **DONE** (`3fb78a9`). The agent now reads
   Traffic Cop's decision instead of reasoning past it.
2. ~~**Structured Traffic Cop logging**~~ — **DONE** (M2 P2.24). One JSON event
   per decision. Note what this does and does not buy: every question in the
   observability list below is answerable **from now on**, and answerable for
   **nothing that happened before**, because no historical structured data
   exists.
3. **One trust filter for both paths** — the agent filters on trust, the legacy
   path does not. Now the highest-value remaining routing item.
4. **Do not train the RF.** Revisit when the gate opens on real evidence.
