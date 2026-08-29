# P2.13 — TA-GTKT: temporal features and gated fusion

**Status:** complete. Four-rung ablation, three seeds each, on the frozen
P2.12 corpus and partition.

**Production changes: none.** `EloEngine` still owns every learner-visible
rating, Glicko-2 is still unarmed, and no learner state was read or written.
A test walks the parsed imports of `kt_research` and fails on `groups`,
`django`, `kt_dataset` or `LearnLM`.

**TA-GTKT is a name for this configuration, not a novelty claim.** Temporal
encoding and gated fusion both appear in the knowledge-tracing literature.
What this ladder can honestly claim is a controlled comparison of them on one
corpus with one split.

---

## 23A — What was frozen, and the proof it stayed frozen

The P2.12 corpus, partition, sequence construction, scoring protocol, seed
and baseline hyperparameters are unchanged. The evidence, not the assurance:

| Check | Result |
| --- | --- |
| Corpus rows, learners, questions, concepts | identical |
| `split_hash` | `f894bcc691d010d5…` — **byte-identical to P2.12** |
| Held-out interactions scored | 44,254, the same rows |
| BKT re-scored from its P2.12 checkpoint | AUC 0.7086 — identical |
| DKT re-scored from its P2.12 checkpoint | AUC 0.7476 — identical |
| Transformer re-scored from its P2.12 checkpoint | AUC 0.7454 — identical |

One thing did change and it is not silent: the canonical schema went to **v2**
to carry `response_time_ms`, so the *corpus* hash moved from
`d0cbd2bf2500f758…` to `423bcaa2cfe60600…`. Adding a column cannot move the
partition — it is a function of learner and sequence position — and a test
pins `split_hash` at its v1 value independently of the golden-hash test, so a
change that *did* move the partition would fail loudly rather than quietly
invalidating every baseline.

The network preserves P2.12's **module construction order** for the same
reason. torch draws from the global RNG as each module is built, so a new
module inserted before the old ones would change their initial weights and
the baseline would stop reproducing. Every P2.13 module is appended.

---

## 23B — What temporal information this corpus actually has

Measured on the raw file, not assumed:

| Field | Verdict | Evidence |
| --- | --- | --- |
| `ms_first_response` | **available** | 0 nulls, 8 negative, median 19.5 s. A genuine response duration. |
| `overlap_time` | available, unused | A second duration, highly correlated with the first. Adding it would not answer a question the first cannot. |
| absolute timestamp | **does not exist** | No such column. Both fields above are DURATIONS, not points in time. |
| inter-event interval | **unavailable** | Requires a wall clock. |
| `attempt_count` | **refused — leaks** | Records how many tries the learner *ultimately* needed. Reading it at its own position leaks the outcome. |
| `hint_count` | **refused — leaks** | Same defect: how many hints they ended up needing. |
| `opportunity` | unused | Redundant with `attempt_number`, which the build already computes from history. |
| `attempt_number` | **available** | PRIOR attempts on this learner+question, computed by the corpus build. Knowable before answering. |

Two of these are worth stating plainly because they look like temporal
features and are not. `attempt_count` and `hint_count` are **outcome-side**:
they describe how the engagement ended. A model reading either would score
extremely well and have learned nothing.

### The schema change

Response duration exists upstream and canonical schema v1 simply did not
transport it. The honest options were to extend the schema or to invent a
temporal signal; P2.13 extended the schema. `response_time_ms` is carried
**verbatim** apart from impossible values:

- 8 raw rows have a *negative* duration. A duration cannot be negative; that
  is a source defect, not a fast answer. Those go to `None` and are counted
  in the manifest (`field_issues`), never silently zeroed.
- The longest value is **23 hours** — a session left open rather than a
  response. It is **not capped in the corpus**, deliberately. What to do
  about it is a modelling decision, and a corpus that quietly squashed it
  would be asserting a number nobody measured. The model applies
  `log1p` instead, which is where that decision belongs.

Coverage after the build: **283,100 of 283,105** interactions (99.998%).

---

## 23C — The temporal representation

Four numbers, and the rule that governs all of them:

> A feature may inform the prediction at position *t* if and only if it is
> knowable **before the learner answers item t**.

| Feature | Side | What it is |
| --- | --- | --- |
| `prev_log_duration` | past | `log1p(seconds)` of the **previous** interaction |
| `prev_duration_missing` | past | flag — a missing duration is not a zero-second answer |
| `query_log_attempts` | query | `log1p` prior attempts on this item |
| `query_log_gap` | query | `log1p` ordinal distance from the previous interaction |

Continuous features are standardised with a scaler **fitted on training
sequences only**; a mean taken over the whole corpus would carry test-set
information into a training feature.

### `query_log_gap` is not elapsed time, and is never called that

§23C forbids manufacturing a Δt from a row index and calling it real elapsed
time. This corpus has no clock, so the gap counts how many **other logged
interactions, by anyone**, fell between one learner's two. It correlates with
real elapsed time *and* with how busy the platform was that day, and nothing
in this dataset can separate the two.

It is included as the strongest legitimate ordering signal available, under
its own name, with the confound recorded. Any result that leans on it should
be read accordingly.

---

## 23D — Gated fusion

```
past side, step t              query side, step t
────────────────────           ──────────────────
interaction(c,y)@t-1           concept @t
question       @t-1            question @t
duration       @t-1            attempts, ordinal gap @t
        │                              │
   content ─┬─ temporal                │
            │                          │
     g = σ(W[content;temporal])        │
     fused = g⊙content + (1-g)⊙temporal│
            │                          │
      + position embedding             │
            │                          │
     causal Transformer encoder        │
            │                          │
            └──────── concat ──────────┘
                      │
                 prediction head
                      │
              P(next response correct)
```

The gate is a **learned per-dimension sigmoid**, so the model can keep the
temporal channel where it helps and shut it off where it does not, one
feature dimension at a time. Its mean value is reported with every run: a
gate pinned near 1 means the temporal channel was learned away and the rung
is the one below it wearing extra parameters. That is worth knowing before
anyone reads a difference in AUC as evidence the mechanism did something.

### Causality is structural on every rung

Inputs are shifted so a label — and now a duration — is fed one step *after*
the position it describes. A model predicting position *t* cannot see its own
answer or its own response time even if the attention mask were wrong,
because the information is not in the tensor. A test flips an answer
mid-sequence, and another lengthens a response time, and both assert every
earlier prediction is bit-identical. Both run against **all four rungs**.

---

## 23E — Why there are four rungs and not three

§23E asks for three: Transformer, +temporal, +temporal+gating. §23D's
architecture also introduces a **question embedding**. Adding it together
with the temporal features would leave the first step changing two things at
once, and any gain would be unattributable.

So there is a control rung:

| Rung | Adds | Purpose |
| --- | --- | --- |
| `Transformer` | — | rung 1, frozen from P2.12 |
| `Transformer+T` | response time | rung 2 — "Transformer + response time" |
| `Transformer+TG` | learned gated fusion | rung 3 — isolates the gate |
| `TA-GTKT` | question embedding | the full §23C input list |

The question embedding is added **last, on its own step**. It is 1.08M of the
model's 1.17M parameters against 17,751 mostly-rare items, so folding it in
earlier would have made every later number unattributable.

Each rung switches on exactly one component, enforced by a test that walks
the ladder and fails if any rung adds two things or removes one. All four
share the corpus, the partition, the sequence construction, the scoring
protocol and the training budget, and the widths are constant by
construction — query-side signals are summed into one vector rather than
concatenated, so the prediction head is the same shape in every rung and the
parameter differences are only the new tables themselves.

Nothing was tuned. Every rung uses the P2.12 baseline's hyperparameters.

---

## Results

44,254 held-out interactions. Baseline at three seeds; the rungs above it at
one, under the accelerated brief.

| Model | AUC | Accuracy | Log loss | Params | Train (s) | Gate |
| --- | --- | --- | --- | --- | --- | --- |
| Transformer (3 seeds) | 0.7439 ± 0.0017 | 0.7189 ± 0.0015 | 0.5591 ± 0.0009 | 93,441 | 1,038 | — |
| Transformer+T | 0.7465 | 0.7237 | **0.5552** | 93,825 | 1,048 | — |
| Transformer+TG | 0.7485 | 0.7224 | 0.5554 | 102,081 | 1,083 | 0.633 |
| **TA-GTKT** | **0.7624** | 0.7234 | 0.5558 | 1,174,721 | 7,885 | 0.582 |

### The seed noise floor, measured rather than assumed

Three baseline seeds give **±0.0017 AUC** (1σ; range 0.7420–0.7454). That
number is what makes the rest of the table readable, and it retrospectively
corrected P2.12: the DKT-over-Transformer gap of 0.0022 was 1.3σ, i.e. noise.

### Step-by-step attribution — one change per step

| Step | ΔAUC | σ | Δlog loss | σ |
| --- | --- | --- | --- | --- |
| Transformer → +T (response time) | +0.0011 | 0.6 | **−0.0046** | **−5.4** |
| +T → +TG (the gate) | +0.0020 | 1.2 | +0.0002 | 0.2 |
| +TG → TA-GTKT (question embedding) | **+0.0140** | **8.1** | +0.0004 | 0.5 |

**The gate — the thing this phase is named for — did essentially nothing.**
+0.0020 AUC is 1.2σ, inside the noise, and log loss did not move. Its learned
value settled at 0.633, i.e. it kept roughly two thirds content and one third
temporal rather than shutting either off.

**Response time helped, but not where AUC could see it.** +0.6σ on AUC and
**−5.4σ on log loss**: it did not reorder learners, it made the probabilities
better calibrated, for 384 extra parameters. For deciding what to show a
learner that is the more useful of the two.

**The question embedding did all the AUC work** — and only the AUC work.
+8.1σ ranking, nothing on calibration, for 12.5× the parameters and 7.5× the
training time. Its validation curve tells the rest: training loss fell from
0.624 to 0.478 while validation log loss bottomed at epoch 9 and then *rose*.
That is overfitting, and because early stopping selects on validation AUC —
which kept climbing — it chose the most overfit checkpoint by calibration.

### Honest reading

TA-GTKT beats the frozen baseline decisively on AUC and not at all on log
loss or accuracy. The gain is attributable to a standard technique (per-item
embeddings, as AKT uses) and **not** to temporal gating, which is what the
name claims. A model that ranks better while being no better calibrated, at
12.5× the cost, is not obviously the one to deploy.

`Transformer+T` is the interesting rung: the best calibration in the table,
best accuracy in the table, for 384 parameters over the baseline.

---

## 23G — Error analysis

DKT vs TA-GTKT over the same 44,254 interactions, at threshold 0.5:

| | Count |
| --- | --- |
| Both right | 27,447 |
| Both wrong | 7,609 |
| DKT only | 4,632 |
| TA-GTKT only | 4,566 |
| **Net to TA-GTKT** | **−66** |

Despite +0.015 AUC over DKT, TA-GTKT gets **fewer** interactions right at
threshold. The ranking improved; the decisions did not.

Where it differs:

| Stratum | Net to TA-GTKT | Reading |
| --- | --- | --- |
| Hard items (<40% train accuracy) | **+160** (+2.3%) | Per-item embeddings help most where item identity carries the signal |
| Items unseen in training | **−37** (−3.4%) | And hurt where there is no embedding to look up |
| Medium items (40–70%) | −301 (−1.7%) | The bulk of the loss |
| Concept unseen by this learner | +71 (+1.9%) | Better at cold start |

That is a coherent story rather than noise: the model learned items, so it
wins on items it has seen a lot of and loses on items it has not.

---

## 23I — Production separation

`kt_research` imports no application module, no web framework, no database
client. The corpus arrives as a directory of files written by
`kt_dataset_build`; the boundary is a filesystem path, not an import. A test
walks every parsed import statement in the package and fails on `groups`,
`django`, `kt_dataset` or `LearnLM`.

Nothing in this phase reads learner submissions, writes learner state,
touches Glicko-2, or changes what SparkLM recommends.

---

## A measurement that changed my mind

P2.12 recorded "batch the scoring path" as the obvious next optimisation, on
the assumption that per-learner forward passes dominated the wall clock.
Measured here on 400 real sequences, batching ran **1.2× faster**, not the
5–10× assumed — the cost is the encoder arithmetic itself, not per-call
overhead.

It also was not free: padding changes the reduction order inside the matmuls,
so predictions moved by up to `2.4e-07` and test AUC moved in the seventh
decimal. That is enough to break the bit-identical baseline reproduction
§23A requires. A 1.2× speedup is not worth paying for in reproducibility, so
the optimisation was **rejected** and the measurement is recorded in its
place.
