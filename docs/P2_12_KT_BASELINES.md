# P2.12 — Real KT dataset and the Transformer baseline

**Status:** complete. Three baselines trained on a real public benchmark, with
a leakage-verified split and a reproducible pipeline.

**Production changes: none.** `EloEngine` still owns every learner-visible
rating, Glicko-2 is still unarmed, and nothing in this phase reads or writes
learner state. The research package cannot import the application — that is
asserted by a test that walks the parsed imports, not by convention.

---

## 22A — Dataset selection

**ASSISTments 2009–2010 "skill builder", corrected release.**

| Criterion | ASSISTments 2009 | EdNet KT1 |
| --- | --- | --- |
| Availability | one 8.7 MB archive, canonically mirrored | ~1.5 GB, per-user files |
| Interactions | 401,756 | ~131,000,000 |
| Learners | 4,217 | ~780,000 |
| Concept labels | yes (`skill_id`, 123 skills) | tags, coarser |
| Wall-clock time | **no** | yes |
| Response time | yes (`ms_first_response`) | yes |
| Fits a sprint on CPU | yes — minutes | no |

EdNet is the better corpus for anything temporal, and that is exactly what
this phase is forbidden from building (§22E). What §22D and §22E need is a
corpus small enough that the whole pipeline — build, split, leakage audit,
three models, checkpoints, re-scoring — can be exercised end to end and
re-run on demand. ASSISTments 2009 is also the most reported KT benchmark
in the literature, so a number from it is comparable to published work in a
way an EdNet subset would not be.

One dataset was downloaded. No second corpus was fetched.

### Licence position — read this before redistributing anything

`kt_dataset` was deliberately written in P2.10b **not** to download anything:
ASSISTments' terms require a written agreement, prohibit redistribution, and
require publishing your source code. That decision stands, and this phase did
not weaken it — `kt_dataset_build` still refuses to fetch, and still explains
why when the input file is absent.

What changed is that the operator directed acquisition (§22A) and the file was
obtained. It is **gitignored**, along with every subset of it. What travels
with a result is the file's sha256, so anyone holding their own copy can
confirm it is the same revision without either party sending the data.

---

## 22B — Schema mapping

The corpus is mapped by the **existing `kt_dataset` package**, not by anything
new. It already owns row validation, the duplicate policy, `attempt_number`,
per-learner ordering, the partition and the hashes. A second mapping would be
a second answer to questions that must have one.

The boundary between the application and the research pipeline is a
**directory**, not an import:

```
manage.py kt_dataset_build  ->  interactions.csv
                                split_assignment.csv
                                manifest.json
                                      |
                                      v
                        kt_research.datasets.load_build()
```

| §22B field | Status | What actually happened |
| --- | --- | --- |
| `learner_id` | available | `user_id` |
| `question_id` | available | `problem_id` |
| `concept_id` | available | `skill_id`; 63,755 raw rows have none |
| `correctness` | available | `correct`, validated as strict 0/1 — a partial score is rejected, never rounded |
| `timestamp` | **ordinal only** | This corpus has **no absolute time column**. `ms_first_response` and `overlap_time` are durations, not points in time. Ordering is `sequence_position`, derived from `order_id`, and nothing treats it as a clock. |
| `response_time` | **unavailable** | Present in the raw file as `ms_first_response`, absent from canonical schema v1, so it does not survive the build. No baseline in this phase consumes it and §22E forbids temporal features, so the schema change is deferred rather than smuggled in. |
| `difficulty` | **unavailable** | Not a source column. It *could* be estimated as each item's empirical success rate — and that would be a leak: computed over the whole corpus it carries test labels into a training feature, and computed over train alone it is undefined for every test-only item. AKT learns Rasch difficulty as a parameter instead of being handed one. |

Nothing in the unavailable rows was invented. `kt_research.datasets.FIELD_AVAILABILITY`
holds these reasons in code, and a test fails if any field is declared
unavailable without one.

### What the build rejected, and why

| Reason | Rows | Meaning |
| --- | --- | --- |
| `missing_concept_id` | 63,755 | `--require-concept`; ~16% of the corpus has no skill tag |
| `source_duplicate` | 54,896 | multi-skill items, emitted once per skill with an identical identity tuple — collapsed to the first skill, the standard single-skill preprocessing |

283,105 interactions survive, across 4,163 learners, 17,751 questions and
112 concepts. 65.8% correct.

---

## 22C — Split and leakage

**Per-learner temporal.** Each learner's own history is cut by fraction:
earliest 70% trains, next 15% validates, last 15% tests.

This is the question a deployed knowledge tracer answers — *given this
learner's history so far, what happens next*. A global cut is not even well
defined here: with no shared clock, two learners' positions are not
comparable.

Learners with fewer than 3 interactions go entirely to train. Scoring a
learner with no history measures the cold-start prior, which is a different
experiment with a different claim.

**The partition is checked twice, by two independent implementations:**

1. `kt_dataset` audits its own split through `groups.kt_leakage.audit_split`,
   embedding ordinal positions into synthetic datetimes inside the audit call
   only. → `SAFE — 283,105 interactions across 4,163 learners audited`
2. `kt_research` re-verifies the partition it loads, per learner, before any
   model is fitted. → `PASS`

Check (2) *reads* the partition rather than recomputing it. Recomputing would
give a second implementation of the split rule, and a second implementation
agrees with itself — which is not the same as being right. An independent
check has an independent failure mode; a test moves one row between buckets in
the file and asserts the loaded partition follows the file.

**An unsound split yields no number at all.** The guard runs before fitting,
not after scoring, and a test asserts the guard actually fires.

---

## 22D/22E — The models

All three score **the same 44,254 test interactions**, with the same context.

A learner's history is *not* restarted at the test boundary: to score a
held-out interaction, the model is fed that learner's earlier interactions —
training ones included — and then asked about the held-out one. Restarting at
the boundary would have compared three cold-start priors and called it
knowledge tracing.

### Causality is structural, not a mask

Every sequence model here consumes a **shifted** input:

```
position      0       1        2        3
scored        c0      c1       c2       c3
fed in       BOS   (c0,y0)  (c1,y1)  (c2,y2)
```

The label `y_t` is fed at step `t+1` and nowhere earlier, so a model
predicting position `t` cannot see its own answer *even if the attention mask
were wrong* — the information is not in the tensor. A test flips one answer
mid-sequence and asserts every earlier prediction is bit-identical.

### Transformer baseline (§22E)

```
interaction embedding + learned position embedding
        |
   causal encoder  (2 layers, 4 heads, d=64)
        |
   head( encoded history , embedding of the item being asked )
        |
   P(next response correct)
```

Positional information is **order only**. No temporal gating, no elapsed
time, no response time, no prerequisite graph. A test strips docstrings and
asserts none of those names appear anywhere in the code — a baseline that
quietly included half of its successor's additions would make them look free.

Neither neural model was tuned. They share hidden size, embedding size,
context window, learning rate, dropout, batch size and patience, so the
comparison is between architectures rather than between search budgets.

---

## 22F — Comparison

Full corpus, 44,254 held-out interactions, per-learner temporal split,
seed 20260827.

| Model | AUC | Accuracy | Log Loss | RMSE |
| --- | --- | --- | --- | --- |
| BKT | 0.7086 | 0.6506 | 0.6442 | 0.4719 |
| **DKT** | **0.7476** | **0.7249** | **0.5559** | **0.4321** |
| Transformer | 0.7454 | 0.7196 | 0.5599 | 0.4338 |

**The Transformer did not beat DKT.** It is behind on every one of the four
metrics — AUC by 0.0022, log loss by 0.0040. Reported as measured.

Two things make that result weaker than it looks, and both cut against the
Transformer rather than for it:

1. **The Transformer had not converged.** DKT early-stopped at epoch 14
   (best epoch 11, validation AUC 0.7446). The Transformer used its entire
   15-epoch budget and its validation AUC was **still rising** at the last
   epoch (0.7362, up from 0.7357 at epoch 14). It ran out of budget, not out
   of improvement.
2. **Neither model was tuned at all.** A Transformer with 2 layers, 4 heads
   and d=64 on 196k training interactions is small, and attention models are
   the more hyperparameter-sensitive of the two.

So the honest statement is: *at equal and untuned budget, the plain
Transformer encoder bought nothing over an LSTM on this corpus.* That is a
useful baseline result — it is the number the next phase's temporal gating
and prerequisite signal have to beat — but it is not evidence that attention
is worse here, and it should not be quoted as one.

What is solid in the table: **both sequence models clearly beat BKT** (+0.039
AUC, −0.088 log loss), which is what a working pipeline should show, and the
gap is far larger than the gap between them.

### Subset result (§22I), for comparison

500 learners, 5,355 held-out interactions — the pipeline proof, not a finding.

| Model | AUC | Accuracy | Log Loss | RMSE |
| --- | --- | --- | --- | --- |
| BKT | 0.7070 | 0.6452 | 0.6505 | 0.4746 |
| DKT | 0.7375 | 0.7064 | 0.5723 | 0.4403 |
| Transformer | 0.7234 | 0.6980 | 0.5794 | 0.4442 |

The ordering is the same at 1/8 the data, and every model improves with the
full corpus — which is the sanity check the subset existed to provide.

---

## 22G — Reproducibility, demonstrated

`evaluate` loads the checkpoints, rebuilds the same corpus and partition, and
re-scores without fitting anything. Its numbers are **bit-identical** to the
training run's, to the last decimal place, for all three models:

```
BKT          auc 0.7085912721493446   log loss 0.6442076563589797
DKT          auc 0.7476117154899254   log loss 0.5559139031509864
Transformer  auc 0.7454252596925494   log loss 0.5598623055673081
```

Re-scoring the full test set takes 30 seconds.

The whole phase was then **trained a second time from scratch** — corpus
build, split, three fits, scoring — and every number in the table above came
back identical, on both the subset and the full corpus. Checkpoint re-scoring
proves the scoring path is stable; a fresh retrain proves the *training* path
is too, which is the stronger of the two claims and the one a seed is supposed
to buy.

### The four commands

```bash
# 1. documented subset (optional — §22I)
python -m kt_research.run_experiment subsample \
    --source kt_research/data/raw/skill_builder_data_corrected.csv \
    --out kt_research/data/raw/assist2009_subset500.csv \
    --learners 500 --seed 20260827

# 2. build the corpus (the ONLY step that runs through the application)
python manage.py kt_dataset_build \
    --source assistments-2009-2010-skill-builder \
    --input kt_research/data/raw/skill_builder_data_corrected.csv \
    --out build/kt/assist09 --require-concept

# 3. train
python -m kt_research.run_experiment train \
    --config kt_research/configs/assist2009_baselines.json

# 4. re-score from checkpoints
python -m kt_research.run_experiment evaluate \
    --config kt_research/configs/assist2009_baselines.json
```

### Artifacts

| Artifact | Where | Committed |
| --- | --- | --- |
| Config + seed | `kt_research/configs/*.json` | yes |
| Corpus metadata, hashes | `build/kt/assist09/manifest.json` | no (`build/`) |
| Per-run record, epoch history | `kt_research/results/*.json` | yes |
| Metrics table | `kt_research/results/*_metrics.csv` | yes |
| Model checkpoints | `kt_research/checkpoints/` | no — regenerable |
| Raw corpus + subset | `kt_research/data/raw/` | no — licence |

Every result record carries the dataset fingerprint, the corpus hashes, the
seed, the preprocessing version, the split summary and the environment.

### Known cost, worth fixing next

Validation is scored **one learner-sequence at a time**, which dominates the
wall clock — the Transformer's full run took roughly 20 minutes on CPU, most
of it in per-epoch validation rather than in training. Batching the scoring
path is the obvious next improvement and changes no number.

---

## 22H — Isolation

`kt_research` imports no application module, no web framework, no database
client. A test walks every parsed import statement in the package and fails on
`groups`, `django`, `kt_dataset` or `LearnLM`. The corpus arrives as files.

This matters more than it looks: LearnLM has 44 learner submissions. That is
not a training set, and a research pipeline that *can* reach production is one
that will eventually train on it.

---

## 22I — Performance

The pipeline was proven on a documented subset first: **500 learners sampled
whole**, seed 20260827, written as a real file with its own sha256 so a subset
result is reproducible exactly the way a full-corpus result is.

Sampling is by **learner, never by row**. A learner missing a random 40% of
their history is not a smaller sample of the same problem — it is a different
and easier one, where the model is asked to bridge gaps it was never told
exist.

One thing had to be fixed before the full corpus was runnable at all: the
P2.10b split audit rescanned all three buckets once per learner, which is
`4,163 × 283,105` ≈ 1.2 billion comparisons. Grouped once per bucket instead,
the same audit runs in seconds. Same partition, same comparisons, same result
— a test pins it against a deliberately naive implementation.

---

## Honest reading of these numbers

- **Skill builder is mastery-learning data.** A sequence terminates when the
  learner answers three in a row correctly. Sequences end on success *by
  construction*, which inflates the positive rate near the end of every
  sequence and caps sequence length for strong learners. `kt_dataset` records
  this on the source itself. Every number below inherits it.
- **These are untuned baselines**, not a leaderboard entry. Published
  ASSISTments-2009 AUCs vary by several points purely on preprocessing and
  split choice, and a per-learner-history split is harder than the
  sequence-level random split much of the literature uses. Compare these three
  numbers to each other, not to a paper.
- **BKT sees an unbounded history; the neural models see 200 interactions.**
  That is an inherent property of a fixed context window, not an evaluation
  asymmetry, and it is reported rather than papered over.
- **No claim is made about TA-GTKT.** It remains a name for a configuration,
  not a novelty claim, and it was not built in this phase.
