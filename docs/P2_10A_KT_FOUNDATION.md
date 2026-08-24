# P2.10a — Knowledge Tracing: research foundation and data gate

**Status:** COMPLETE (research + instrumentation only). No model, no training,
no migration, no production access.
**Gate result for LearnLM today: `NOT_READY`.**
**Companion command:** `python manage.py kt_data_readiness` (read-only).

---

## 1. What this phase concluded

LearnLM cannot yet train a knowledge-tracing model of any kind, and the reason
is not volume — it is **trust**. Zero questions have reached `ORACLE_VERIFIED`,
so `adaptive_eligible` is false on every submission, so the count of
trustworthy training labels is **zero**. Volume is a second, independent
blocker behind it.

This is a successful result. The phase existed to establish whether a
Transformer could be trained *honestly*, and the answer is a defensible no with
a reproducible number attached.

---

## 2. Corrections to the previous P2.10 report

Two findings changed on re-audit.

**2.1 — P1.1 has been partially executed; `requirements-ml.txt` survives.**

The previous report said P1.1 "exists to delete the last deep-learning stack",
implying torch would leave the repository. The file now records the opposite
conclusion, reached by measurement:

> *"torch and transformers REMAIN, and the roadmap's expectation that this file
> could be deleted outright in P1.1 was wrong: both are still imported by
> VectorSearchService (`groups/ai_services.py`) for CLIP."*

`groups/engines/` confirms it: `gnn_engine`, `shap_explainer`, `export_onnx`
and `mirt_engine` are gone; `synthetic_data_generator.py` is gone;
`retrain_ai.py` remains. torch 2.12.0 and transformers 5.9.0 are still pinned
in `requirements-ml.txt`, which now leaves with **P9.3**, not P1.1.

**This strengthens the P2.10 architecture rather than contradicting it.**
`requirements-ml.txt` is an established, CI-covered home for offline ML
dependencies that the web tier does not install. The offline-training half of
P2.10 has somewhere to live that already exists.

**2.2 — The torch-free web-tier constraint is confirmed and now test-enforced.**

`render.yaml:10` (`plan: free`), `DEPLOYMENT.md:226` (~202 MiB RSS on a 512 MB
instance), `requirements.txt:53` ("the web tier ships torch-free"). Two tests
in `test_kt_readiness.py` now assert that no KT module imports torch and that
torch is absent from `requirements.txt`.

**Everything else in the previous report verified unchanged**: `CodeSubmission`
fields, `adaptive_eligible` frozen-at-write, the `TopicPrerequisite` DAG,
`LearnerTopicSkill`/`QuestionSkill` shadow Glicko, `RecommendationLog` logging
only the chosen question, and the absence of `evals/` (P4.1 not started).

---

## 3. What was built

| Artifact | Purpose |
|---|---|
| `groups/kt_readiness.py` | Filtering contract, census, configurable gate |
| `groups/kt_leakage.py` | Causality audit, temporal split, split audit |
| `groups/kt_features.py` | Feature inventory (machine + human readable) |
| `groups/management/commands/kt_data_readiness.py` | Read-only report |
| `groups/test_kt_readiness.py` | 46 tests |

**No new table.** Every field the eventual dataset needs is a `CodeSubmission`
column or a pure causal function of existing rows, so the projection is
*derived* and cannot disagree with its source. A materialised table would add a
migration, a backfill and a second thing that can be wrong, for a consumer that
does not exist. It becomes justified in P2.10b when sequence construction makes
repeated full scans expensive.

---

## 4. The filtering contract

An interaction is KT-eligible only if it survives all four:

1. `adaptive_eligible IS TRUE` — P2.7c trust gate, frozen at write
2. `question_id IS NOT NULL` — the column is nullable; orphans exist
3. `question.topic_id IS NOT NULL` — no concept, no tracing
4. `status IN ('accepted', 'wrong_answer')` — P2.8b's `LEARNER_EVIDENCE_STATUSES`

Filter 4 reuses an existing decision rather than making a new one.
`compile_error`, `time_limit` and `runtime_error` conflate not-knowing with
mistyping; P2.8b excluded them from the learner signal, and minting a second
definition of "evidence" would guarantee the two eventually disagree.

The report prints this contract every run, because the failure mode is a future
reader seeing a number and not knowing which rows produced it.

---

## 5. Literature review

Assessed against LearnLM's constraints, not summarised in the abstract. Scale
figures are approximate orders of magnitude from the papers' experimental
sections; exact dataset revisions vary between publications and should be
re-verified before any benchmark run.

### 5.1 DKT — *Deep Knowledge Tracing* (Piech et al., NeurIPS 2015)
RNN/LSTM over one-hot (skill, correctness) pairs; outputs P(correct) for every
skill at each step. **Contribution:** established that a sequence model beats
BKT without hand-specified skill structure. **Limitations:** no question
identity (all items in a skill are interchangeable); the well-documented
reconstruction problem — predicted mastery oscillates and can fall after a
correct answer; no uncertainty. **For LearnLM:** baseline only. With topic-level
concepts it degenerates to a per-topic accuracy tracker.

### 5.2 DKVMN — *Dynamic Key-Value Memory Networks* (Zhang et al., WWW 2017)
Static key matrix (concepts) + dynamic value matrix (mastery), read/written by
attention over concepts. **Contribution:** per-concept mastery is directly
readable — genuine interpretability, not post-hoc. **Limitations:** concept
count is a hyperparameter; still recurrent. **For LearnLM:** the interpretable
baseline. Its per-concept state maps naturally onto `UserTopicMastery`, which
matters because any KT output shown to a learner must be explainable.

### 5.3 SAKT — *A Self-Attentive model for Knowledge Tracing* (Pandey & Karypis, EDM 2019)
Single-layer self-attention; the next exercise is the query, past interactions
are keys/values. **Contribution:** first attention-based KT; **explicitly
motivated by data sparsity** — the authors' argument is that attention
generalises better than recurrence when learners have few interactions.
**Limitations:** shallow; no explicit difficulty model. **For LearnLM: this is
the starting point.** It is the published architecture whose motivating
setting is closest to LearnLM's, and it is trainable on CPU.

### 5.4 AKT — *Context-Aware Attentive Knowledge Tracing* (Ghosh et al., KDD 2020)
Two self-attention encoders (question, knowledge) plus a knowledge retriever,
with **monotonic attention** whose weights decay exponentially with context
distance, and **Rasch-model embeddings**: question = concept embedding +
difficulty scalar × concept variation vector. **Contribution:** the Rasch
factorisation is the key idea for sparse data — it collapses per-question
parameters to one scalar plus a shared vector. **For LearnLM: the target
architecture.** The Rasch form is the same factorisation the two-sided Glicko
already computes, so `QuestionSkill.rating` can initialise the difficulty
scalar. The distance decay is a principled version of the forgetting intuition
behind the 24 h cooldown.

### 5.5 SAINT — *Separated Self-AttentIve Neural Knowledge Tracing* (Choi et al., L@S 2020)
Full transformer encoder–decoder: **exercise sequence to the encoder, response
sequence to the decoder**. **Contribution:** the separation prevents the model
conditioning on the response it is predicting; deep stacking then becomes
useful. Trained on EdNet (~10⁸ interactions). **For LearnLM:** import the
separation principle, not the depth. Encoder–decoder capacity at LearnLM scale
is unjustifiable.

### 5.6 SAINT+ — *Integrating Temporal Features* (Shin et al., LAK 2021)
SAINT plus **elapsed time** (how long the learner took) and **lag time** (gap
since the previous interaction). **Contribution:** temporal features gave the
clearest reported gain over SAINT. **For LearnLM — the critical finding:**
LearnLM can compute **lag time** and **cannot compute elapsed time**.
`execution_time_ms` is the *program's* Judge0 runtime, not the learner's
deliberation. Substituting it would feed the model algorithmic efficiency
labelled as cognitive effort, so a fast O(n) and a slow O(n²) solution would
read as different learner states for identical knowledge — and it is further
confounded by Judge0 queue load. This is the phase's **MUST-HAVE
instrumentation gap**.

### 5.7 Rasch/IRT + KT
The Rasch model (single difficulty parameter per item) is what AKT embeds.
Relevant because LearnLM's Glicko-2 (P2.9a) is a two-sided rating system that is
structurally an online Rasch estimator: learner ability and item difficulty on
one scale, with explicit uncertainty. **The two are not competitors — Glicko is
a calibrated, interpretable, uncertainty-bearing prior that a KT model can be
initialised from.**

### 5.8 Calibration of KT probabilities
The literature-wide weakness: KT papers report AUC almost universally and
calibration rarely. **AUC is rank-only and invariant to any monotone
transform** — a model can rank perfectly and still assign probabilities that
are systematically wrong. LearnLM's selector targets a *probability band*
(≈0.75–0.85), so a mis-calibrated model is unusable no matter how well it ranks.
**Brier score and reliability curves are therefore promoted to gating metrics,
not diagnostics.** This is a place where the design deliberately departs from
common practice in the field.

### 5.9 Temporal evaluation
Many published KT results use random interaction-level splits, which leak a
learner's future into their own past. Where papers report both, temporally-split
performance is consistently lower. **Consequence: published AUC figures are not
directly comparable to what a temporally-split LearnLM evaluation will produce,
and the roadmap must not treat a paper's number as a target.**

### 5.10 Cold start and sparsity
The consistent finding across SAKT/AKT/DKVMN ablations: with very short
sequences, attention models converge toward item-difficulty priors — i.e.
toward what a well-calibrated IRT/Elo/Glicko system already gives you.
**This is the literature's own argument for LearnLM's architecture**: Glicko
handles the low-evidence regime, KT contributes only where sequences are long
enough for sequence structure to exist.

### 5.11 Does the literature contradict the locked design?

**No — it supports it, with one sharpening.** SAKT-then-AKT, Rasch embeddings
seeded from Glicko, evidence-gated fallback and separated response streams are
all directly grounded above. The sharpening is §5.8: **calibration must be a
deployment gate, not a reported statistic.** That has been written into the
evaluation contract below.

---

## 6. Public benchmark comparison

| Dataset | Scale (approx.) | Learners | Concepts | Temporal | Notes |
|---|---|---|---|---|---|
| **ASSISTments 2009–10** | ~3×10⁵ interactions | ~4,000 | ~110 skills | order + timestamps | The most-reported KT benchmark. **Known duplicate-record defect — the corrected release must be used**, and results from the uncorrected version are not comparable |
| **ASSISTments 2017** | ~10⁶ | ~1,700 | ~100 | richer | Deeper per-learner sequences; less widely reported |
| **Junyi Academy** | ~10⁷ | ~10⁵ | ~700 exercises | timestamps | **Ships a prerequisite/knowledge-structure graph** — the one structural feature LearnLM has and most datasets lack |
| **EdNet (KT1)** | ~10⁸ | ~7.8×10⁵ | ~13,000 | full | Largest public KT dataset; SAINT's training set. KT2–4 add behavioural detail |

*License and access terms must be verified per dataset before any download;
they are not restated here from memory.*

**Primary: ASSISTments 2009 (corrected release).** Small enough to train on CPU
in minutes, the most-reported benchmark so reference numbers exist to validate
the pipeline against, and closest in size to what LearnLM could plausibly reach.

**Fallback: Junyi Academy.** Chosen specifically for its prerequisite graph,
which lets the DAG-prior component of the LearnLM design be tested at all —
nothing else on this list can.

**Explicitly not EdNet for P2.10c/d.** At ~10⁸ interactions it would dominate
the timeline and prove nothing that ASSISTments cannot, in an environment with
no GPU. It is the eventual scale check, if the phase ever gets that far.

---

## 7. Evaluation contract (binding on later sub-phases)

### Arms
- **B1** — current deterministic rules (P2.8a ordering)
- **B2** — Glicko + deterministic rules (P2.9)
- **C** — KT + Glicko + deterministic rules

### Model metrics
AUC · log loss · **Brier score** · **reliability curve** · per-topic AUC ·
cold-start slice (< 5 interactions) · temporal generalisation (train on early,
test on late).

### System metrics
Time-to-mastery · repeated-failure rate · exposure diversity · **target-band
accuracy** (fraction of served questions whose realised outcome rate falls in
0.75–0.85) · learner progression.

### Deployment gate — all required
1. **C beats B2 on AUC by more than the bootstrap CI, on a temporal split.**
2. **Calibration no worse than B2** (Brier + reliability). A better-ranking,
   worse-calibrated model is rejected — see §5.8.
3. No cold-start regression against the deterministic fallback.
4. ≥ 30 days and ≥ 10,000 shadow decisions.
5. No torch on the web tier.

**AUC improvement alone is explicitly insufficient.** That sentence is the
contract.

---

## 8. Safety boundary

Enforced by AST-based structural tests in `test_kt_readiness.py`, reusing the
guard developed in P2.7g-2/g-3.

The KT package **must not** write `expected_output`, `hidden_test_cases`,
`content`, `status`, `trust_state`, `adaptive_eligible`, reference lifecycle
fields, or Glicko/mastery state — and must perform no ORM persistence at all.
It **may** produce P(correct), knowledge estimates, embeddings, uncertainty and
ranking scores.

```
GRADING TRUTH → ReferenceSolution → Oracle → Provenance
              → Quality Gate → Human Approval → ORACLE_VERIFIED
```

KT sits entirely outside this chain and consumes only its output.

---

## 9. Gaps

**MUST HAVE** — learner deliberation time (frontend timer; no substitute
exists) · ≥ 1 `ORACLE_VERIFIED` question so any label can be trustworthy ·
P4.1 eval harness · point-in-time Glicko logging **or** a decision to treat
Glicko as live-only.

**SHOULD HAVE** — candidate-set logging in `RecommendationLog` (only the winner
is recorded, so off-policy evaluation is impossible — online A/B is the only
option without it) · session identification · `RecommendationLog.problem_id` is
a `CharField`, not an FK, so joins are string-based and unvalidated.

**NICE TO HAVE** — partial credit · hint usage (P7.2) · finer-grained concept
tags than `Topic`.

---

## 10. Is P2.10b unblocked?

**No.** P2.10b builds the interaction dataset; there is nothing eligible to
build one from. It unblocks when the gate reports at least `RESEARCH_READY`,
which requires the trust pipeline to have promoted real questions first.

**The public-benchmark track is unblocked and is the recommended next work.**
Building the sequence builder, baselines and evaluation loop against
ASSISTments proves the machinery with reference numbers, needs no LearnLM data,
and is directly reusable when the gate opens.
