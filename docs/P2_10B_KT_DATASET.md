# P2.10b — KT interaction dataset pipeline

**Status:** Pipeline COMPLETE and validated. **Dataset acquisition BLOCKED on an
operator decision** — see §2.
**Command:** `python manage.py kt_dataset_build --source … --out …`
**No dataset is bundled, downloaded, or vendored by this repository.**

---

## 1. Dataset selection

**Primary: ASSISTments 2009–2010 "skill builder", corrected release.**
**Fallback: Junyi Academy.** EdNet not used — no technical reason justifies
10⁸ interactions on a GPU-less environment.

Official source, versions and the duplicate-record defect are documented at the
[ASSISTments skill-builder page](https://sites.google.com/site/assistmentsdata/home/2009-2010-assistment-data/skill-builder-data-2009-2010),
which states plainly: *"Duplicated data records have been detected in the data
sets that located at above links."* Two corrected variants exist — one row per
student-problem-skill, and one row per student-problem with multiple skills
collapsed as `skill1_skill2`. **The pipeline targets the one-row-per-
student-problem variant**, recorded in the manifest as
`dataset_version="corrected-one-row-per-student-problem"`.

## 2. Licence and provenance — THE BLOCKER

The [ASSISTments terms of use](https://sites.google.com/site/assistmentsdata/termsofuseforusingdata)
impose five conditions. Three are decisive:

> 3. *"I agree to not give this data to anyone else. I will only use this data for the purpose I provide."*
> 4. *"I will acknowledge the use of the ASSISTments TestBed in any publications…"*
> 5. *"My data and algorithms will be made public in my papers I publish. [If you want to not share your source code, then you can not use this data.]"*

Access also requires **written agreement** from the researcher.

**I did not download the dataset, and the pipeline cannot download it.** Three
reasons, in order of weight:

1. **A written agreement is a commitment by a person.** Accepting it on your
   behalf is not mine to do. It binds *you*, under your name.
2. **Condition 5 has a real consequence for this repository.** Using the data
   obliges publishing your source code. LearnLM is currently private. That is a
   decision about your project's licensing posture, not a technical step.
3. **Condition 3 forbids redistribution**, so the bytes can never be committed
   here regardless.

Junyi is no easier: it is [CC BY-NC-SA 4.0 on Kaggle](https://www.kaggle.com/datasets/junyiacademy/learning-activity-public-dataset-by-junyi-academy),
which requires a Kaggle account and API credentials — credentials I must not
handle.

**A structural test enforces the position:** `test_no_module_downloads_anything`
asserts no module in `kt_dataset` imports `requests`, `urllib`, `httpx`,
`aiohttp` or `socket`. The pipeline consumes a file you already have and records
its sha256.

## 3. Raw schema (ASSISTments 2009–2010)

| Column | Meaning | Used as |
|---|---|---|
| `order_id` | ordering key | `order_key` → `sequence_position` |
| `user_id` | learner | `learner_id` |
| `problem_id` | item | `question_id` |
| `skill_id` | concept (**~16% missing**) | `concept_id` |
| `skill_name` | concept label (~19% missing) | — |
| `correct` | binary first-attempt outcome | `correct` |
| `attempt_count` | attempts *within one problem log* | **deliberately unused** |
| `ms_first_response`, `overlap_time` | **durations**, not timestamps | — |

### Three findings that shaped the design

**(a) There is no wall-clock timestamp.** Not a missing column — the dataset
has none. `ms_first_response` and `overlap_time` are millisecond *durations*.
Consequently **`lag_seconds` is UNAVAILABLE**, and SAINT+'s lag-time feature —
the temporal feature P2.10a identified as the one LearnLM could compute —
**cannot be reproduced on this benchmark at all**. Worth knowing before a phase
is spent trying.

**(b) `order_id`'s chronology is contested.** The
[EduData schema reference](https://edudata.readthedocs.io/en/latest/build/blitz/ASSISTments/ASSISTments2009-2010.html)
calls it a *"non-chronological record identifier"*; the prevailing convention
in the KT literature sorts by it as a chronological proxy. The pipeline treats
it as an **ordinal only**, never a timestamp, and re-checks the resulting order
with `assert_monotonic_sequences` rather than trusting it.

**(c) Skill-builder data is mastery-learning data.** A sequence *terminates when
the learner answers 3 in a row correctly*. This is a selection effect built into
the data: sequences end on success by construction, inflating the positive rate
near sequence ends and capping length for strong learners. Recorded in
`SourceCapabilities.notes`; any baseline result must be read with it in mind.

## 4. Canonical schema

`learner_id · question_id · concept_id · sequence_position · correct ·
attempt_number · source_row_id · occurred_at · lag_seconds · outcome_label ·
{dataset_name, dataset_version, source_file, raw_dataset_hash, schema_version} ·
glicko_rating_at_time · glicko_rd_at_time`

- **`sequence_position`, not `timestamp`** — always available; the only thing
  the split may order by.
- **`occurred_at` is never synthesised.** None when the source has none.
- **`attempt_number` counts PRIOR attempts**, computed by the pipeline. The
  source's `attempt_count` measures something else and would leak how many
  tries the learner ultimately needed.
- **Provenance is per row**, not only in the manifest — a row separated from its
  manifest is still traceable to file and line.

## 5. Cleaning

Every rejected row yields a machine-readable `Rejection(source_row_id, reason,
detail)` written to `rejections.csv` and counted in the manifest. **Nothing is
silently discarded.** Reasons: `missing_learner_id`, `missing_question_id`,
`missing_concept_id`, `missing_correct`, `malformed_correct`,
`malformed_numeric`, `missing_order_key`, `source_duplicate`,
`non_monotonic_sequence`.

`require_concept` is configurable and recorded in the manifest, because
rejecting ASSISTments' ~16% missing `skill_id` materially changes the dataset.

**A bug this caught in development:** the label check originally used
`int(float(value))`, so a partial-credit `0.5` became `0` — a graded score
silently relabelled as a wrong answer, invisible in every downstream metric.
Now compared as a float against exactly `0.0`/`1.0`.

## 6. Duplicate policy

| Case | Identity | Action |
|---|---|---|
| Source duplicate | same `(learner, question, order_key, correct)` | **drop**, first occurrence wins |
| Legitimate repeat | same `(learner, question)`, **different** `order_key` | **keep** |
| Contradictory | same position, different outcome | **keep both** — not silently collapsed |
| Different learners, same position | — | **keep** |

Dropping repeat attempts would delete the within-learner repetition knowledge
tracing exists to model, and would flatter every baseline by removing its
hardest cases.

## 7. Temporal split

**Per-learner fractional, chronological.** 70/15/15 by default.

A global cut point is wrong here: with no shared clock, `order_id`s are not
comparable across learners, so a global cut would partition by *learner* and
test generalisation to new people rather than to a learner's future. The
production question is the latter.

| Boundary case | Behaviour |
|---|---|
| Equal positions | Cannot occur — rejected upstream as non-monotonic. No tie-break invented |
| < 3 interactions | Entire sequence → TRAIN. A 1-interaction learner has no future to predict |
| Exactly 3 | 1/1/1 — the shortest splittable sequence |
| Rounding | Floored, so short sequences give scarce rows to the later splits |

## 8. Leakage policy

Verified with P2.10a's `audit_split`, reused rather than reimplemented. Because
the audit takes wall-clock boundaries and this data has none, positions are
embedded as `epoch + position seconds` — **strictly order-preserving**, so an
ordering violation in position is one in embedded time and vice versa. The
embedding exists only inside the audit call and is **never written to a row**.

Audited **per learner**, because the split is per learner; a global audit would
flag learner A's late training rows against learner B's early test rows, which
is not leakage.

The report distinguishes **SAFE** from **VACUOUSLY SAFE**: an empty dataset
prints the latter and says explicitly that it is not evidence of safety.

## 9. Feature inventory

Reuses `groups/kt_features.py` classifications.

| Feature | ASSISTments 2009 | LearnLM |
|---|---|---|
| learner / question / concept | AVAILABLE | AVAILABLE |
| correctness | AVAILABLE | AVAILABLE (`accepted`/`wrong_answer`) |
| attempt_number | DERIVABLE | DERIVABLE |
| wall-clock time | **UNAVAILABLE** | AVAILABLE (`submitted_at`) |
| lag_seconds | **UNAVAILABLE** (no clock) | DERIVABLE |
| learner deliberation time | UNAVAILABLE | **MISSING — must-have gap** |
| `execution_time_ms` | n/a | **UNSAFE** — Judge0 program runtime, not thinking time |
| point-in-time Glicko | UNAVAILABLE | **MISSING** — see §10 |

## 10. Glicko

**GLICKO_PRIOR = NOT_AVAILABLE**, for both sources, for different reasons.

- **ASSISTments** has no rating system at all.
- **LearnLM before P2.9b** stored only **current** state, so historical
  point-in-time Glicko is permanently unrecoverable. **Replay is not a safe
  substitute**: `glicko.rate` consumes `periods_inactive` derived from the wall
  clock at update time, which was never recorded, so a replay reconstructs a
  plausible history rather than the actual one.
- **LearnLM from P2.9b onward** records `GlickoSnapshot` per eligible
  interaction: `learner_rating_before`, `learner_rd_before`,
  `learner_periods_inactive` and the question side, taken from the exact values
  `glicko.rate` consumed. **AVAILABLE going forward, MISSING for everything
  earlier** — the gap is admitted, never imputed.

The schema carries `glicko_rating_at_time` and `glicko_rd_at_time` **as columns
that stay None**, so LearnLM can begin logging legitimate point-in-time state
going forward. They are never back-filled.

## 11. Statistics

`stats.describe` reports interactions, learners, questions, concepts,
correctness rate, sequence-length distribution and buckets, per-question and
per-concept coverage, cold-start counts (`<5`, `<10` interactions), and the
per-split correct rates. No plotting dependency added.

## 12. Reproducibility

Manifest fields: dataset name/version/source, **raw hash**, **processed hash**,
**split hash**, full configuration, source capabilities, counts, rejection
counts by reason, statistics, leakage report, canonical column order.

Hashing uses the length-prefixed framing from P2.7g-3 — no field value can
forge a field boundary. **Nothing time-dependent enters the hash.** The
configuration participates, because two builds with identical rows under
different `require_concept` settings are not the same dataset.

A **golden-hash test** pins the hash *definition*, not just its
self-consistency: without it, dropping the schema version or reordering
configuration frames leaves every other determinism test passing while
producing a different dataset identity. Three mutants proved that.

## 13. Limitations

1. **Public benchmark data is not LearnLM data.** It validates preprocessing,
   leakage handling and evaluation methodology. It is **not** evidence that
   LearnLM's learners behave the same way. Coding problems taking minutes are
   not middle-school math items taking seconds.
2. **Mastery-learning selection effect** (§3c) biases the correctness
   distribution.
3. **No wall-clock time** — lag features untestable on this benchmark.
4. **`order_id` chronology is contested** — mitigated, not eliminated.
5. **~16% missing concepts** force a configuration choice with no free option.

## 14. LearnLM adapter

The trust firewall: `adaptive_eligible == True` may become a label;
`False` is rejected with reason `not_adaptive_eligible`. There is **no flag that
admits ineligible rows** and a test asserts none exists. It reuses
`kt_readiness.eligible_interactions()` rather than restating the predicate —
a second definition would eventually disagree with the FILTER_CONTRACT the
readiness report prints.

**It currently yields zero rows**, because no question has reached
`ORACLE_VERIFIED`. Tested entirely on synthetic local fixtures; never run
against production.

## 15. Future KT requirements

Unchanged from P2.10a, sharpened by this phase:

- **MUST HAVE** — learner deliberation time; ≥1 `ORACLE_VERIFIED` question;
  point-in-time Glicko logging (or an explicit decision to treat Glicko as
  live-only).
- **SHOULD HAVE** — candidate-set logging in `RecommendationLog`; session ids.
- **New from this phase** — if lag features matter, the benchmark cannot
  validate them; that capability must be tested on LearnLM data or on Junyi.
