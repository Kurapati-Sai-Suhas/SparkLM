# SparkLM Technical Interview Handbook
## Part 5 — Machine Learning, Adaptive Learning & Recommendation Systems

**Questions 169–212 of 254**
**Companion:** Document 03 (AI Pipeline), Document 01 §7–8

---

## Section P — Adaptive Learning Fundamentals (Q169–Q182)

---

### Q169. What makes SparkLM "adaptive"?

**Ideal answer.** Every submission updates two estimates simultaneously — how strong the student
is, and how hard the problem is — and the next problem is chosen from those estimates rather than
from a fixed syllabus order. Concretely: an Elo update on both the user's rating and the
question's rating, a half-life update on the topic's retention estimate, a mastery recomputation,
and a graph-decay pass on downstream topics if the attempt failed. The router then combines
recent accuracy, a streak statistic, and the Elo band to pick what comes next.

**Why we chose this.** A difficulty slider is not adaptation — it responds to what the student
*says*, not what they *do*. Estimating from behaviour means the system can disagree with the
student, which is the point.

**Alternatives.** Fixed curriculum order; self-selected difficulty; simple accuracy thresholds;
deep knowledge tracing with an LSTM.

**Tradeoffs.** Statistical estimation needs data, so a brand-new user gets essentially a default
experience until enough submissions accumulate — the cold-start problem, which I handle with a
deterministic first problem rather than pretending to personalise.

**Follow-ups.** "How is that different from a difficulty slider?" · "What about a new user?" ·
"Why not one model instead of four?"

**What interviewers expect.** Precision about what is modelled and what is not. The **learner**
side is adaptive — Elo, HLR, mastery, streak detection all update from behaviour. The **item**
side is not: `Question.base_difficulty` is a static author prior written only by seed commands,
so a mislabelled problem stays mislabelled. Two-sided calibration is specified in the
architecture (§4.2), is roadmap M10, and is Phase A of Milestone 4. Say the gap out loud —
claiming self-calibrating content that the schema cannot support collapses on the first
follow-up.

---

### Q170. Explain Elo and why it applies to a learning platform.

**Ideal answer.** Elo models a contest between two rated entities and updates both ratings by how
surprising the outcome was. Here the contest is a student attempting a problem: expected score
comes from the rating difference through a logistic function, and the update is
`K × (actual − expected)` with `K = 32`. Beating a hard problem moves your rating a lot; beating
an easy one barely moves it. Symmetrically, a problem that strong students fail drifts upward.

**Why we chose this.** It is the simplest model that calibrates both sides from the same
observation, it is online — no batch retraining — and it is interpretable, which matters because
students see their rating.

**Alternatives.** Glicko or Glicko-2 (adds rating deviation and volatility); TrueSkill (Bayesian,
handles multiplayer); raw accuracy; IRT fitted offline.

**Tradeoffs.** Elo assumes a single scalar skill, which is false — someone strong at arrays can
be weak at graphs. That is precisely why `UserTopicMastery` exists alongside the global rating.
Elo also converges slowly with a small user base, and I have no rating-deviation term to
express that uncertainty — Glicko's main advantage over Elo, and a gap the Milestone 4 plan
closes.

**Follow-ups.** "What is K and why 32?" · "Why not Glicko?" · "What does a rating of 1200 mean?"

**What interviewers expect.** K explained as a learning rate: high K means fast adaptation and
noisy ratings, low K means stable ratings that lag real change. 32 is chess's standard for
provisional players, which suits a system where most users have few games.

---

### Q171. Should per-question difficulty be a learned rating rather than a label?

**Ideal answer.** Yes, and it is not one yet — that is the single biggest gap in the adaptive
system. Difficulty is `base_difficulty`, an author-assigned prior set at seed time and never
updated. It should be learned, because authors cannot estimate difficulty: they know the
solution, so they cannot judge how hard it is to find. The planned design gives every question
a `rating` updated by the same Elo pass that updates the student, plus `rating_deviation` and
`attempt_count` so confidence is explicit — a question attempted twice has a very different
epistemic status from one attempted two thousand times.

**Why we chose this.** The one-sided version shipped because it was the smaller change and the
user base was too small for item ratings to converge anyway. That reasoning has an expiry date
and the debt is now recorded.

**Alternatives.** Static labels (current); empirical pass rate; expert review; IRT difficulty
parameters fitted offline.

**Tradeoffs.** Raw pass rate is the tempting shortcut and it is **confounded** — a problem only
strong users attempt looks easy. Elo accounts for who attempted, because losing to a weak player
costs more than losing to a strong one. That is the whole argument for Elo over a success-rate
metric, and it is why the planned fix is Elo rather than a counter.

**Follow-ups.** "Why not just pass rate?" · "Why has it not been done?" · "How many attempts
before you trust an item rating?"

**What interviewers expect.** The confounding argument, and then the reason this is Phase A work
rather than an afternoon: updating a question's rating inline takes a lock on a row **shared
across every user**, while every existing lock in the system is per-user and ordered
profile → mastery ascending. Every submission to the deterministic first problem would contend
on one row. Deferred batch aggregation avoids it. Spotting that a schema addition breaks a
concurrency contract is the answer.

---

### Q172. What is half-life regression and why do you use it?

**Ideal answer.** HLR models forgetting: each user-topic pair has an estimated half-life — the
time until recall probability drops to 50% — which lengthens with successful recall and shortens
with failure. It turns the system from a difficulty ladder into a spaced-repetition system,
because it can decide that a *mastered* topic needs revisiting since estimated retention has
decayed below threshold. Without it, "mastered" would be permanent, which is empirically false.

**Why we chose this.** Learning is not monotonic. A model that only ever moves a student forward
will confidently serve advanced material to someone who has forgotten the prerequisites.

**Alternatives.** SM-2 (the classic Anki algorithm); Leitner boxes; fixed review intervals; no
decay model.

**Tradeoffs.** HLR is a regression over features and needs data to fit well; SM-2 is a
hand-tuned heuristic that works immediately. SparkLM actually uses both — `_apply_sm2_update`
handles the per-submission scheduling while HLR provides the decay estimate — which is
belt-and-braces and arguably redundant.

**Follow-ups.** "How is that different from SM-2?" · "What features go into HLR?" · "Who computes
the decay?"

**What interviewers expect.** The operational gap volunteered: `calculate_decay` and
`send_spaced_repetition` are management commands that **nothing schedules**. So the decay model
is only as fresh as the last manual invocation, and the review queue reads from that. A
time-dependent model with no scheduler is a real defect and it is on the debt register.

---

### Q173. How do you define mastery?

**Ideal answer.** Two conditions, both required: accuracy at or above 0.8 and at least 3 reviews
on that topic. Three reviews is deliberately a small sample — the gate is a *progression signal*,
not a certification. Requiring 20 reviews would be statistically better and would make the
curriculum feel glacial.

**Why we chose this.** It is a shared definition used by both the DAG traversal and the mastery
map, defined once as module constants rather than duplicated.

**Alternatives.** Bayesian posterior over mastery; a fixed number of consecutive correct answers;
IRT ability threshold; time-weighted accuracy.

**Tradeoffs.** Accuracy over three attempts has wide error bars — two correct out of three is 67%
and fails; three of three is 100% and passes, on one extra data point. A Bayesian posterior with
a credible-interval threshold would be more principled, and the architecture anticipates it with
a planned `TopicBelief` table.

**Follow-ups.** "Is three reviews enough?" · "Why 0.8?" · "What replaces this later?"

**What interviewers expect.** That the shared-definition point is not cosmetic. The DAG route
originally gated on per-topic `elo_rating >= 1300` — a field **never updated anywhere** — so the
condition could never be satisfied and the recommender returned the root topic forever. The
shared accuracy definition replaced a gate that was silently always closed.

---

### Q174. Tell me about the per-topic Elo bug.

**Ideal answer.** `UserTopicMastery` carries an `elo_rating` field that nothing ever writes.
Someone — me — reasonably assumed it was maintained and wrote the hierarchical routing gate
against it: recommend the next topic once per-topic Elo reaches 1300. Since the field stays at
its default forever, the gate never opened, and the DAG engine recommended the root topic
indefinitely. Nothing errored; the recommendations were just silently wrong.

**Why we chose this.** The fix was to use the shared mastery definition. The deeper fix is that a
field which exists but is never updated is worse than an absent one, because code gets written
against it.

**Alternatives.** Actually implement per-topic Elo; delete the field; document it as unused.

**Tradeoffs.** Per-topic Elo is genuinely desirable — it would give localised skill estimates
rather than deriving everything from accuracy. Implementing it properly means deciding how
per-topic and global ratings interact, which is a real modelling question.

**Follow-ups.** "How did you find it?" · "Why not implement it?" · "Is the field still there?"

**What interviewers expect.** The generalisation: **an unused field looks exactly like a usable
field.** This is the same failure class as the dead password validators and the silent cache —
something present, plausible, and inert. Three instances of one pattern is worth naming as a
pattern.

---

### Q175. How do you handle the cold-start problem?

**Ideal answer.** For a new user, the recommender has no submissions, so routing telemetry is
empty and Elo is at its default. Rather than pretending to personalise, the first problem is
deterministic — every new user gets the same one, which is why a broken template on that specific
question was so damaging. From there each submission adds signal, and the router becomes
meaningful after roughly the 20-submission window the telemetry uses.

**Why we chose this.** A deterministic entry point is testable, reviewable, and can be curated to
be a good first experience. A randomly-chosen first problem is none of those.

**Alternatives.** An onboarding quiz to estimate initial ability; ask the user to self-report;
cold-start from cohort averages.

**Tradeoffs.** `CodingOnboardingView` exists and seeds initial profile and portal selection, so
there is a placement step — but it does not produce a calibrated ability estimate. A short
adaptive placement test would give a much better starting Elo and costs the user five minutes
before they see any value.

**Follow-ups.** "Why does a deterministic first problem matter?" · "What about a new question?" ·
"Could you use cohort priors?"

**What interviewers expect.** Item cold-start as the symmetric case — and here it is permanent,
not just initial: a question is served on its seed prior forever, because nothing updates it. A
`rating_deviation` term should widen the
band for uncertain items so they get exploratory traffic — which connects directly to the
explore/exploit question in Q200.

---

### Q176. What is graph-decay cross-pollination?

**Ideal answer.** When a student fails a problem, the penalty does not stay on that topic — a
smaller penalty propagates to downstream topics that depend on it in the curriculum DAG. The
reasoning: if you have just demonstrated weakness in recursion, your recorded mastery of
dynamic programming is less trustworthy than it was, even though you have not attempted DP
recently. It runs as best-effort enrichment inside a savepoint, so a failure loses the decay and
never the submission.

**Why we chose this.** Mastery estimates on prerequisite topics are evidence about dependent
topics, and ignoring that treats each topic as independent when the DAG says they are not.

**Alternatives.** Independent per-topic estimates; a full Bayesian network over topics; no
propagation.

**Tradeoffs.** The propagation is heuristic — a fixed penalty over graph distance rather than a
learned coupling. A Bayesian network would be principled and needs far more data to fit. The
heuristic is defensible precisely because it is small and best-effort.

**Follow-ups.** "How far does it propagate?" · "Why a savepoint?" · "Is the penalty learned?"

**What interviewers expect.** The engineering detail alongside the modelling: penalties are
resolved **pre-lock** because they are a pure walk over the cached graph reading no learner
state, then applied inside the transaction under the mastery locks. Separating the read-only
graph walk from the locked write is what keeps the critical section short.

---

### Q177. Why is the curriculum a DAG rather than a list?

**Ideal answer.** Because prerequisites are genuinely a partial order, not a total one. Arrays
and strings are both prerequisites for two-pointer techniques, and neither depends on the other,
so any linear ordering imposes a fake sequence. A DAG lets the recommender pick any topic whose
prerequisites are satisfied, which gives the student real choice while still enforcing structure.
Acyclicity is enforced in `TopicPrerequisite.clean()` using NetworkX, and `save()` calls `clean()`
explicitly because Django does not.

**Why we chose this.** A cycle would make the traversal non-terminating or arbitrary, so it must
be impossible to create rather than detected later.

**Alternatives.** Linear syllabus; tree (single prerequisite per topic); tags with no structure.

**Tradeoffs.** A DAG is harder to author and harder to visualise. And validating acyclicity in
Python means loading every edge on every write — fine at tens of topics, wrong at thousands.

**Follow-ups.** "What about `bulk_create`?" · "Why NetworkX?" · "How do you visualise it?"

**What interviewers expect.** The enforcement hole named unprompted: `bulk_create`, `update()`
and raw SQL all bypass `save()`, so the invariant holds only on the ORM path. A recursive-CTE
trigger would be database-authoritative. Knowing where your own constraint stops applying is the
answer.

---

### Q178. Why is the curriculum gate disabled in production?

**Ideal answer.** `CURRICULUM_GATE_ENFORCE=false`, so prerequisites are computed and surfaced but
not enforced — students see the DAG and their unlock state, and nothing blocks them from starting
a locked topic. That is a product decision: a hard gate on a noisy signal frustrates users, and
the mastery signal is noisy at three reviews. The frontend already discourages locked topics, so
the server-side gate is defence rather than the primary mechanism.

**Why we chose this.** It was also a timing decision — the code shipped disabled deliberately
rather than flipping request behaviour immediately before a demo window, which the code comment
records.

**Alternatives.** Enforce strictly; soft warning only; enforce after a confidence threshold.

**Tradeoffs.** Disabled means the DAG is decorative in production, which undercuts the argument
for having built it. Enforcing it with a Bayesian confidence threshold rather than a point
estimate would be the principled version.

**Follow-ups.** "So why build the gate?" · "When would you enable it?" · "Is that not dead code?"

**What interviewers expect.** Recognising this is a feature flag used properly — code shipped in
a disabled state, ready to enable with a config change and no deploy. That is deploy/release
decoupling, and it is the same pattern as `ENABLE_SHAP_XAI`.

---

### Q179. What is the Wald-Wolfowitz runs test and why does it matter here?

**Ideal answer.** It tests whether a binary sequence has more or fewer *runs* — consecutive
identical outcomes — than randomness would predict, returning a z-score. It replaced variance as
the struggle-detection statistic, and the reason is decisive: variance cannot distinguish
`1010101010` from `1111100000`. Both have identical variance and mean, and they describe
completely different students — one oscillating at their level, one who hit a wall. The runs test
separates them, because the second has two runs and the first has ten.

**Why we chose this.** It was mandate one of the frozen architecture's Phase A engineering list,
which tells you how fundamental the original defect was considered.

**Alternatives.** Variance; exponentially weighted moving average; change-point detection;
a Markov model over outcome sequences.

**Tradeoffs.** The runs test needs a reasonable window — I use the last 20 submissions — and is
undefined for very short histories, so new users fall through to the flat Elo route.
Change-point detection would be more powerful and much harder to explain.

**Follow-ups.** "Why 20?" · "What does the z-score feed?" · "What about a new user?"

**What interviewers expect.** The `1010101010` versus `1111100000` example, delivered concretely.
It is the clearest possible demonstration that a summary statistic can be blind to the structure
you actually care about, and it lands with any interviewer regardless of their ML background.

---

### Q180. How is the runs-test output used?

**Ideal answer.** `compute_routing_telemetry(user, window=20)` returns mean accuracy, the runs
z-score, and the sample size. Those three plus normalised Elo feed `RoutingClassifier.predict_route`,
which returns either `hierarchical` or `flat`. Hierarchical means "this student is streaking —
walk the DAG and change the topic"; flat means "keep them in this topic at their Elo band." So
the statistic selects a *strategy*, not a difficulty.

**Why we chose this.** Detecting struggle and choosing a response are separate concerns, and
keeping them separate means the detector can improve without touching the routing.

**Alternatives.** Directly map the z-score to a difficulty adjustment; a single end-to-end model;
rules on raw accuracy.

**Tradeoffs.** Two-stage means two things to get right and two things to debug. It also means the
decision is inspectable — the log line records route, accuracy, z-score, sample size and Elo, so
a bad recommendation can be traced to its inputs.

**Follow-ups.** "What is `RoutingClassifier`?" · "Is it learned or rules?" · "How do you debug a
bad route?"

**What interviewers expect.** That the routing decision is **logged with its inputs** at info
level. An adaptive system whose decisions cannot be reconstructed after the fact is
unmaintainable, and one log line per decision is the cheapest possible version of that.

---

### Q181. What does the hierarchical engine actually do?

**Ideal answer.** `HierarchicalEngine.get_next_topic(portal, mastered_topics)` walks the cached
curriculum DAG and returns the next topic whose prerequisites are all in the mastered set, plus a
human-readable reason. The recommender then filters servable questions in that topic, excludes
already-solved ones, and orders by `ABS(base_difficulty − target_elo)` to pick the nearest to
the student's level.

**Why we chose this.** When a student is stuck, changing the topic is more useful than changing
the difficulty — often the blocker is a missing prerequisite rather than the problem being hard.

**Alternatives.** Drop difficulty within the topic; serve a worked example; pick a random easier
topic.

**Tradeoffs.** Topic-switching can feel disorienting if the reason is not communicated, which is
why the engine returns a `reason` string surfaced as the XAI explanation.

**Follow-ups.** "What if all prerequisites are unmastered?" · "How is the DAG cached?" · "What is
the `reason` for?"

**What interviewers expect.** The Elo-band ordering done in SQL — `Func(F('base_difficulty') −
target_elo, function='ABS')` — rather than fetching candidates and sorting in Python. Pushing the
distance computation into the database is the difference between a query and a scan.

---

### Q182. How do you explain a recommendation to a student?

**Ideal answer.** Every recommendation carries an `xai_explanation` string. The flat route returns
something like "matched to your current skill level (Elo 1240)"; the hierarchical route returns
the engine's reason for the topic change. There is also a SHAP-based explainer for feature
attribution, but it is **disabled in production** — `ENABLE_SHAP_XAI=false` — because the web tier
is deliberately torch-free and the dependency would not fit in 512 MB.

**Why we chose this.** A student who does not understand why they got a problem assumes the
system is random, and stops trusting it.

**Alternatives.** No explanation; full SHAP attributions; show the raw numbers.

**Tradeoffs.** The string explanations are templated, not generated from the actual decision
path, so they describe the route rather than the specific weighting. That is honest but shallow —
real attribution is what SHAP would give, and it is switched off.

**Follow-ups.** "Is that real explainability?" · "Why is SHAP disabled?" · "What would you show?"

**What interviewers expect.** Candour that templated strings are explanation *theatre* compared
to genuine attribution. They are still worth having — a plausible reason beats no reason for user
trust — but calling them explainable AI would be overclaiming, and saying so is the stronger
position.

---

## Section Q — The Engine Stack (Q183–Q196)

---

### Q183. Why five engines instead of one model?

**Ideal answer.** Because they model genuinely different things and disagree usefully. Elo models
relative skill from contest outcomes. HLR models forgetting over time. MIRT models ability across
multiple latent dimensions. GDCP models dependency between topics. The GCN models graph structure
over the curriculum. A single model would have to represent all of that in one parameterisation,
need far more data to fit, and be uninterpretable when it went wrong.

**Why we chose this.** Each engine is separately testable, separately explainable, and separately
disableable — which is exactly what happened with the GCN.

**Alternatives.** One deep knowledge-tracing model (DKT/LSTM); a single Bayesian network; pure
heuristics.

**Tradeoffs.** Multiple models mean multiple things to maintain and no principled way to combine
their outputs — the router arbitrates with rules rather than a learned ensemble. A single model
would give coherent joint estimates and be a black box.

**Follow-ups.** "How do you combine them?" · "Is that not over-engineered?" · "Why not DKT?"

**What interviewers expect.** The honest note in the code: an LSTM-based deep knowledge tracing
path existed and was **removed for production** to reduce cold starts and memory. So the
alternative was tried and rejected on operational grounds, not dismissed — which is a much better
answer than never having considered it.

---

### Q184. What is IRT and how does MIRT differ?

**Ideal answer.** Item response theory models the probability of a correct response as a function
of latent ability and item parameters. The three-parameter logistic model uses difficulty,
discrimination — how sharply the item separates ability levels — and a guessing floor. MIRT is
the multidimensional extension: ability is a vector rather than a scalar, so an item can load on
several skills at once. That matches coding problems well, since a graph problem might require
both traversal and dynamic programming.

**Why we chose this.** Elo's single scalar is the model's main weakness, and MIRT is the
principled fix.

**Alternatives.** 1PL/Rasch (difficulty only); 2PL (adds discrimination); Elo alone.

**Tradeoffs.** MIRT needs substantially more data to fit than Elo and is typically fitted in
batch rather than online. With a small user base the parameters are poorly identified, which is
the honest constraint on how much weight it can carry.

**Follow-ups.** "What is discrimination?" · "How much data does 3PL need?" · "Is it actually
fitted?"

**What interviewers expect.** A straight answer that the MIRT engine exists and its parameters
are not well-fitted at current data volume — so it contributes structure and machinery rather
than reliable estimates today. Claiming a fitted 3PL model on a handful of users would not
survive one follow-up.

---

### Q185. What is the GCN for, and why is it disabled?

**Ideal answer.** `TrueGCNKnowledgeGraph` is a graph convolutional network over the topic DAG — it
learns topic embeddings from graph structure plus learner outcomes, so mastery signal propagates
along real dependencies rather than a hand-tuned decay. It is disabled in production because it
requires PyTorch, and the web tier is deliberately torch-free: the dependency alone would not fit
alongside a 202 MB resident set on a 512 MB instance.

**Why we chose this.** The `requirements.txt` used by Render excludes torch entirely. The code is
retained for the future worker tier the architecture specifies.

**Alternatives.** ONNX export for lightweight inference; a separate inference service;
hand-tuned propagation (what GDCP does today).

**Tradeoffs.** `export_onnx.py` exists, which is the right path — export the trained model and
run inference with onnxruntime, which is far lighter than full torch. That is unfinished, and it
is the concrete route to enabling this without a bigger instance.

**Follow-ups.** "Could you use ONNX?" · "What does the GCN buy over GDCP?" · "Is that dead code?"

**What interviewers expect.** The distinction between dead code and staged code. This is behind a
flag, has an export path, and has a documented reason for being off — that is deferred, not dead.
But be willing to concede that code which has never run in production is code you cannot claim
works.

---

### Q186. What is SHAP and what would it give you?

**Ideal answer.** SHAP assigns each input feature a contribution to a specific prediction, based
on Shapley values from cooperative game theory — it answers "why *this* recommendation for *this*
student" rather than "which features matter in general." `XAIEngine` wraps it with an
`_EdgeFixedModel` adapter so the graph model can be explained with fixed edges. It is off in
production for the same torch reason as the GCN.

**Why we chose this.** Genuine per-decision attribution is what would replace the templated
explanation strings with something defensible.

**Alternatives.** LIME; attention weights; feature importance from a tree model; counterfactual
explanations.

**Tradeoffs.** SHAP is expensive — exact Shapley values are exponential in feature count, and the
approximations still cost far more than a forward pass. On a serialised worker at 0.1 vCPU,
running SHAP per recommendation is not viable regardless of the memory question.

**Follow-ups.** "Is SHAP too slow?" · "What about LIME?" · "Would you precompute?"

**What interviewers expect.** Recognising that even with unlimited memory, per-request SHAP is
too slow here — so the realistic design is precomputing attributions asynchronously or explaining
a sample rather than every decision. Knowing that a feature is blocked by *two* independent
constraints is better than knowing one.

---

### Q187. How does the agentic coach work?

**Ideal answer.** After a failed submission, `trigger_agentic_coach` posts the code, error logs
and failed-attempt count to an n8n webhook, which runs an LLM workflow and returns a hint. If the
webhook is unavailable or unconfigured, `_get_fallback_hint(failed_attempts)` returns a static
hint keyed on how many times the student has failed — escalating from "check your edge cases" to
more direct guidance. Crucially it fires **strictly after** the learner-state transaction commits,
because network calls inside a row lock would serialise the user base.

**Why we chose this.** Hints are enrichment. They must never be able to fail a submission or hold
a lock.

**Alternatives.** Inline LLM call; no coaching; precomputed hints per problem.

**Tradeoffs.** Offloading to n8n means the hint logic lives outside the repository and outside the
test suite — a workflow change is invisible to CI. That is a real coupling cost for the
flexibility of editing prompts without a deploy.

**Follow-ups.** "Why n8n?" · "What if it is slow?" · "How is the fallback tiered?"

**What interviewers expect.** The escalation design in the fallback — a hint that changes with
attempt count is pedagogically better than a constant one, and it means the *degraded* path is
still useful rather than merely non-broken. Designing the fallback to be good, not just present,
is the detail.

---

### Q188. How does the Elo update handle execution time and memory?

**Ideal answer.** `EloEngine.calculate_new_rating` takes `execution_time_ms` and `memory_used_kb`
from the first test case alongside correctness, so efficiency can modulate the rating change — a
correct but slow solution is not identical to a correct and fast one. Those stats come from
`first_case_stats(results)`, which reads Judge0's reported time and memory.

**Why we chose this.** In competitive programming, correctness alone is an incomplete signal;
complexity matters and Judge0 gives measurable proxies for free.

**Alternatives.** Correctness only; static complexity analysis; compare against a reference
solution's runtime.

**Tradeoffs.** Judge0's timings are noisy — shared infrastructure, cold JIT for JVM languages,
and the first test case is not necessarily the largest input. So the signal is weak, and
weighting it heavily would inject noise into the rating.

**Follow-ups.** "Is Judge0 timing reliable?" · "Why the first case only?" · "Does language
matter?"

**What interviewers expect.** The cross-language fairness problem raised unprompted: a Python
solution will always be slower than an equivalent C++ one, so any efficiency term either
penalises language choice or needs per-language normalisation. That is a real modelling flaw
worth naming.

---

### Q189. Why is the Elo farming guard a learning decision, not just a security one?

**Ideal answer.** Because the interesting part is what it *permits*. Re-solving an
already-accepted problem still updates mastery and half-life — it is legitimate spaced
repetition, and blocking it would break the review loop. What it does not do is change the
rating, and the response says so explicitly: repeat solves keep your memory fresh but do not
change your rating. That distinction between "disallowed" and "allowed but unscored" is a
pedagogical choice.

**Why we chose this.** A rating system that can be farmed is meaningless; a review system that
blocks review is useless. Separating the two lets both be correct.

**Alternatives.** Block resubmission; allow full rating gain; diminishing returns per attempt.

**Tradeoffs.** Diminishing returns would be smoother than a binary cutoff and harder to explain to
a user. The binary rule is legible, which for a student-facing score matters more than elegance.

**Follow-ups.** "Why not diminishing returns?" · "Where is the guard enforced?" · "What does the
user see?"

**What interviewers expect.** The concurrency detail linked to the pedagogy: the guard sits inside
the profile row lock, because outside it two simultaneous first-solves would both read "not yet
solved" and both score. A learning rule that is only correct single-threaded is not correct.

---

### Q190. How does a submission close the recommendation loop?

**Ideal answer.** Inside the transaction, after inserting the submission, the code finds the most
recent `RecommendationLog` for that user and problem with `actual_result_correct` still null, and
sets it to whether the attempt passed. That converts a recommendation into a **labelled training
example**: the router chose this problem for this state, and here is what happened. Without that
write, every recommendation would be an unlabelled action and the routing classifier would have
nothing to learn from.

**Why we chose this.** It is the data flywheel. The system generates its own training data as a
by-product of being used.

**Alternatives.** Join submissions to recommendations at training time; log outcomes separately;
do not label.

**Tradeoffs.** Joining at training time would avoid the write, and it is fragile — matching by
user, problem and timestamp proximity is guesswork when a student attempts the same problem
repeatedly. Writing the label at the moment it is unambiguous is worth one UPDATE.

**Follow-ups.** "Why the most recent null one?" · "What if they never attempt it?" · "Is the
classifier retrained?"

**What interviewers expect.** Honesty on retraining: `retrain_ai` exists as a management command
and is **not scheduled**, so the flywheel accumulates labelled data that nothing currently
consumes. The pipeline is built and the loop is not closed operationally.

---

### Q191. What is propensity, and do you log it?

**Ideal answer.** Propensity is the probability the policy assigned to the action it took, and
**no, I do not log it** — that is the most time-sensitive gap in the system. `RecommendationLog`
records `engine_used`, `predicted_success_prob` and `actual_result_correct`, which is enough for
a *calibration* study but not for **off-policy evaluation**. Without propensity recorded at
decision time you cannot compute inverse-propensity weights, so "how would policy B have
performed on the traffic policy A actually saw" is permanently unanswerable for every request
logged to date. It costs one float and it cannot be backfilled.

**Why we chose this.** It was not a decision, it was an omission — and it is the one I would
fix first, because every day of traffic makes the gap larger and none of it is recoverable.

**Alternatives.** Live A/B testing; simulation; offline accuracy metrics on held-out data.

**Tradeoffs.** IPS estimators have high variance when propensities are small and need
corrections — self-normalised IPS or doubly-robust methods. And propensity is only meaningful if
the logging policy is **stochastic**: the current router is deterministic given its inputs, so
adding the column alone would log a constant 1.0 and buy nothing. Exploration and propensity
are one change, not two.

**Follow-ups.** "Why is a deterministic policy a problem?" · "What is IPS variance?" · "How
would you add it?"

**What interviewers expect.** The coupling insight — that adding epsilon-greedy exploration
*simultaneously* fixes item calibration (uncertain questions get traffic) and makes the logged
data analytically useful (propensity becomes non-degenerate). One change, two problems. And the
distinction that matters: I have data that supports calibration analysis and does not support
counterfactual analysis, and knowing which questions your data can answer is the point.

---

### Q192. How would you A/B test a recommendation change?

**Ideal answer.** Randomise at the user level, not the request level, because a student switching
policies mid-session confounds everything — the whole point is that recommendations depend on
history. Assign a policy on first contact, record a `policy_version` on every recommendation, and
compare cohorts on a metric that reflects learning rather than engagement. Run it long enough for
the mastery signal to move, which is weeks not days.

**Why we chose this.** Not implemented — `policy_version` is a planned column, not an existing
one, so today I could not tell which policy produced a historical recommendation.

**Alternatives.** Request-level randomisation; interleaving; switchback tests; pure off-policy.

**Tradeoffs.** User-level randomisation needs far more users than SparkLM has to reach
significance on a learning outcome. With a small user base, off-policy evaluation on logged data
would be more practical than a live test — which is exactly why the missing propensity column
hurts, and why adding it comes before any A/B design.

**Follow-ups.** "What metric?" · "How long?" · "Do you have enough users?"

**What interviewers expect.** The metric question answered carefully. Completion rate rewards
serving easy problems. Accuracy rewards the same. The right target is something like **mastery
gained per unit time** or retention on later review, and picking a metric that cannot be gamed by
making the product worse is the actual difficulty.

---

### Q193. What features feed the routing classifier?

**Ideal answer.** Three: mean accuracy over the last 20 submissions, the runs-test z-score over
the same window, and normalised Elo (`target_elo / 2000`). Deliberately few and deliberately
interpretable — with a small user base, a high-dimensional feature set would overfit
immediately, and the log line recording all three plus the decision means any route can be
reconstructed.

**Why we chose this.** Each feature captures a distinct thing: level, stability, and absolute
skill. Adding correlated features would add variance without information.

**Alternatives.** Full submission history embeddings; time-since-last-attempt; per-topic
features; language choice.

**Tradeoffs.** Three features cannot capture topic-specific struggle — a student strong overall
but failing one topic looks fine to the router, and only the DAG route catches that indirectly.
Time-based features are the most obvious omission given that HLR models forgetting.

**Follow-ups.** "Why normalise Elo by 2000?" · "What is missing?" · "Is it a real classifier?"

**What interviewers expect.** The normalisation constant explained as a scaling choice to bring
Elo into roughly the same range as the other features — arbitrary but harmless for a model with
this few inputs, and the kind of magic number worth flagging as such rather than defending.

---

### Q194. How do you evaluate whether the adaptive system works?

**Ideal answer.** Honestly, I do not have a rigorous answer, and that is the biggest gap in the ML
side. Some instrumentation exists — `engine_used`, `predicted_success_prob`, labelled outcomes
via `actual_result_correct`, per-topic mastery over time — and no evaluation has been run
against it. Propensity and policy version, which off-policy evaluation would need, are not
logged at all. What I *can* say is what would
constitute evidence: mastery gained per submission, retention on delayed review, and the
calibration of predicted versus actual pass rates, which is the cleanest test because a
well-calibrated recommender should produce a target success rate by construction.

**Why we chose this.** The data is being collected; the analysis is not.

**Alternatives.** Learning-gain studies; controlled trials; proxy metrics like engagement.

**Tradeoffs.** Engagement metrics are available immediately and measure the wrong thing — a system
that serves easy problems maximises engagement and teaches nothing.

**Follow-ups.** "So does it work?" · "What is calibration?" · "What would you measure first?"

**What interviewers expect.** Calibration as the first thing to check, because it is cheap and
diagnostic: bucket recommendations by predicted pass probability and compare against observed
pass rate. If the model predicts 70% and observes 40%, the difficulty estimates are wrong and
nothing downstream can be trusted. Naming a concrete first experiment beats a philosophy of
evaluation.

---

### Q195. What is your target success rate and why?

**Ideal answer.** The design intent is to serve problems the student will pass most but not all of
the time — the Elo band selection picks problems near the student's rating, which by construction
targets roughly a 50% expected score. In learning-science terms that is the desirable-difficulty
zone: too easy is not learning, too hard is demoralising. Whether the delivered rate matches the
intent is exactly the calibration question I have not measured.

**Why we chose this.** Elo's expected-score function gives you a target success rate for free —
matching ratings means matching to a probability.

**Alternatives.** Target 80% for confidence-building; adaptive target based on frustration
signals; let the student choose.

**Tradeoffs.** 50% is a lot of failure for a beginner and may be demotivating even if it is
optimal for learning rate. Many commercial systems deliberately target higher for retention
reasons — a pedagogically worse choice that keeps users.

**Follow-ups.** "Is 50% too harsh?" · "What does the literature say?" · "Does the runs test relate
to this?"

**What interviewers expect.** The connection back to the runs test: a student on a long failure
run is *outside* the target zone, and the streak detector is what notices and switches strategy.
The two mechanisms are the same idea at different timescales — Elo targets the right difficulty
on average, the runs test catches when the average is wrong for this student right now.

---

### Q196. Where does the adaptive system fall short?

**Ideal answer.** Four places. **Item difficulty never calibrates** — `base_difficulty` is a
static author prior, so the Elo is one-sided and a mislabelled question stays mislabelled.
**No propensity logging**, so off-policy evaluation is impossible for all traffic to date and
the gap cannot be backfilled. **Decay jobs unscheduled**, so the forgetting model is only as
fresh as the last manual run. And the **curriculum gate disabled**, so the DAG is advisory. The
first two are the ones that change how much I trust the rest.

**Why we chose this.** Reflection.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "Which would you fix first?" · "How long would each take?" · "Does that undermine
the project?"

**What interviewers expect.** An ordering with reasoning: scheduling the decay jobs is a cron
entry and makes an existing model actually function, so it is first by value-per-effort.
Propensity plus exploration is next, because the cost of *not* having it grows with every day of
traffic. Two-sided Elo is the largest and unblocks uncertainty-weighted selection. And a straight
answer to the last question — no, it does
not undermine the project, because the *machinery* is real and testable; what is missing is
operational and analytical follow-through, which is a different kind of incompleteness from
"it does not work."

---

## Section R — Recommendation & Routing (Q197–Q206)

---

### Q197. Is this a recommender system in the usual sense?

**Ideal answer.** Not really, and the difference is instructive. Classical recommenders optimise
*preference* — you want the user to like the item. This optimises *learning*, which frequently
means recommending something the student will find unpleasant and fail. There is no
collaborative filtering, no user-user similarity, no matrix factorisation. It is closer to
adaptive testing or curriculum sequencing than to a product recommender.

**Why we chose this.** The objective is different, so the standard toolkit mostly does not apply.

**Alternatives.** Collaborative filtering over solved-problem sets; content-based similarity;
a hybrid.

**Tradeoffs.** Collaborative filtering would actually add something — "students like you found
this problem useful next" is real signal, and it is the obvious extension once there are enough
users. The blocker is data volume, not suitability.

**Follow-ups.** "Would collaborative filtering help?" · "What is the objective function?" ·
"How is this like adaptive testing?"

**What interviewers expect.** The preference-versus-learning distinction, stated as the reason
engagement metrics are the wrong target. It is the sharpest framing available for why this
problem is not a Netflix problem, and it generalises to any educational or health product.

---

### Q198. Walk me through `/api/code/next/` end to end.

**Ideal answer.** Throttle at 30 per minute. Load or create the user's coding profile for the
target Elo. Resolve the requested topic, falling back to the first topic if unknown. Load
already-solved question IDs, casting defensively to int because legacy rows hold non-numeric
values. Optionally apply the curriculum gate. Compute routing telemetry over the last 20
submissions, get a route from the classifier, then either walk the DAG for a new topic or stay
flat, and in both cases select from `_servable_questions()` ordered by Elo proximity. Log the
recommendation with its engine and predicted success probability, and return the question with a
sample case and boilerplate — never
the hidden test cases.

**Why we chose this.** The ordering matters: cheap guards first, expensive routing last.

**Alternatives.** Precompute recommendations; simplify to Elo-only.

**Tradeoffs.** It is roughly 16 queries, which at 33 ms each is about 530 ms of round-trips —
the endpoint is database-bound, not compute-bound, so batching queries would help far more than
faster routing.

**Follow-ups.** "Why cast IDs defensively?" · "Why 16 queries?" · "What does the client get?"

**What interviewers expect.** The hidden-test-case exclusion called out as a security property,
not an implementation detail — it is frozen-spec mandate three, and `hiddenTestCases` survives in
the codebase as what the architecture calls "the cautionary fossil" of leaking them to the
client.

---

### Q199. How do you avoid recommending the same problem repeatedly?

**Ideal answer.** Solved problems are excluded by ID — accepted submissions are loaded up front
and filtered out of the candidate set. The gap is that *attempted but not solved* problems are
not excluded, which is arguably correct for a learning system since retrying a failed problem is
valuable, but it means a student can be served the same unsolved problem repeatedly with no
recency dampening.

**Why we chose this.** Excluding solved problems is unambiguous. Excluding attempted ones would
prevent legitimate retries.

**Alternatives.** Exclude anything attempted recently; a cooldown window; diversity constraints
over topics.

**Tradeoffs.** A recency penalty rather than a hard exclusion is the right answer — decay the
candidate score by how recently it was served, so repeats are possible but not immediate. That
is a small change and not implemented.

**Follow-ups.** "What about repeated failures?" · "Is there diversity?" · "How do you handle
exhaustion?"

**What interviewers expect.** The exhaustion case: if a student solves everything in a topic at
their Elo band, the candidate set empties and the query returns nothing. That path exists and is
handled by fallbacks, but "what happens when you run out" is a question adaptive systems
routinely fail, and having thought about it is the signal.

---

### Q200. How do you balance exploration and exploitation?

**Ideal answer.** There is no exploration at all — selection is
`ORDER BY ABS(base_difficulty − elo)`, pure exploitation of a difficulty estimate that is itself
a static author prior. So the problem compounds: there is nothing to explore *toward*, because
item ratings do not move, and no exploration term that would move them if they did. The fix is
one change with two effects — add `rating` plus `rating_deviation`, then select with an
uncertainty bonus so high-deviation items get traffic. That calibrates content and widens the
difficulty band a student sees.

**Why we chose this.** Deterministic nearest-Elo is simple and predictable. Exploration was
deferred.

**Alternatives.** Epsilon-greedy; UCB; Thompson sampling over the rating posterior.

**Tradeoffs.** Exploration means deliberately serving suboptimal problems, which costs the
individual student to benefit the population. In education that trade is less comfortable than
in ad serving, and it needs to be small.

**Follow-ups.** "What would you use?" · "Is exploration ethical here?" · "How does propensity
relate?"

**What interviewers expect.** The connection to Q191: a deterministic policy makes off-policy
evaluation nearly useless, because propensity is always 1. Adding stochastic exploration would
*simultaneously* fix content calibration and make the logged data analytically useful. One change,
two problems — spotting that coupling is the strong answer.

---

### Q201. How does the DAG cache affect recommendations?

**Ideal answer.** The curriculum graph is built from every topic and prerequisite edge into a
NetworkX `DiGraph` and cached in Redis, because rebuilding it per recommendation is pure waste
and it changes almost never. Invalidation is via `post_save` and `post_delete` signals on
`TopicPrerequisite`, using signals rather than an overridden `delete()` specifically so that
queryset-level deletes — like the one in `seed_dsa_dag` — are covered.

**Why we chose this.** A stale curriculum is a silent correctness bug: the engine would serve
recommendations from an outdated structure and nothing would report it.

**Alternatives.** Short TTL; rebuild per request; versioned cache keys.

**Tradeoffs.** This cache was one of the three features that silently died when the production
cache was misconfigured — the graph was rebuilt from Postgres on every single recommendation
request, with nothing reporting a problem. It was slow, not wrong, which is why nobody noticed.

**Follow-ups.** "What if invalidation fails?" · "Why signals?" · "How did the cache failure
manifest?"

**What interviewers expect.** That this cache failing degrades *performance* while the throttle
cache failing degrades *security* — same outage, same cause, radically different severities. That
asymmetry is the reason the boot probe exists.

---

### Q202. What is `_servable_questions()` protecting against?

**Ideal answer.** Two classes of broken content. Placeholder rows generated but never filled in,
and — the dangerous class — roughly 1,100 CSV-imported questions with a genuine description and
**zero test cases**. Those look seeded, so the reseed pipeline skips them, and serving one gives
the student an empty sample case followed by a guaranteed submit failure. Both are excluded at the
queryset level, so every recommendation path inherits the guarantee.

**Why we chose this.** Content safety belongs in the query layer. Trusting the data to be clean is
how you serve a broken question at three in the morning.

**Alternatives.** A materialised `is_servable` flag; validation at seed time; a nightly audit.

**Tradeoffs.** The filter costs a slightly more complex query on every recommendation; a flag
would be faster and can go stale. Given the endpoint is already 16 queries, one more predicate is
not the bottleneck.

**Follow-ups.** "How did you find the 1,100?" · "Why not fix the data?" · "Why is partial worse
than empty?"

**What interviewers expect.** The insight that **partially-populated is more dangerous than
empty**, because every automated process treats it as complete and skips it. That generalises
well beyond this codebase and is the kind of observation that comes only from operating a content
pipeline.

---

### Q203. How would you add collaborative filtering?

**Ideal answer.** The signal is already there — `CodeSubmission` gives a user-item interaction
matrix with outcomes, not just clicks, which is richer than most recommender data. The natural
first version is item-item: problems frequently solved successfully in sequence by similar-ability
students suggest a useful ordering. That would complement rather than replace Elo, contributing a
"what tends to help next" term alongside "what is at the right difficulty."

**Why we chose this.** Not implemented — data volume does not support it yet.

**Alternatives.** Matrix factorisation; user-user similarity; a graph embedding over the
submission bipartite graph.

**Tradeoffs.** Collaborative filtering has its own cold-start problem for both new users and new
items, so it cannot replace the content-based Elo approach — it can only augment it. And it
tends to reinforce popular items, which in education means everyone funnelled through the same
problems.

**Follow-ups.** "How much data would you need?" · "Would it replace Elo?" · "What about
popularity bias?"

**What interviewers expect.** That outcome data beats implicit feedback: knowing a student
*succeeded* on a problem is far more informative than knowing they clicked it, so this domain
should need less data than a typical recommender to produce a useful signal.

---

### Q204. How do you handle a student who games the system?

**Ideal answer.** The main vector is rating inflation, and the farming guard closes it — resolving
an accepted problem awards zero rating change, enforced inside the profile row lock so concurrent
first-solves cannot both score. Copy-pasting a solution from elsewhere is not detected at all:
there is no plagiarism check, no timing heuristic, and no similarity comparison against known
solutions. A student who wants to inflate their rating by looking up answers can.

**Why we chose this.** The rating is a learning tool, not a credential, so the incentive to cheat
is mostly self-defeating — the cost of gaming it is falling into problems above your real level.

**Alternatives.** Plagiarism detection via similarity; timing heuristics; proctoring; require an
explanation.

**Tradeoffs.** If the rating ever becomes a credential — a leaderboard prize, a recruiter-facing
score — the incentive changes completely and this posture becomes inadequate.

**Follow-ups.** "What if it became a credential?" · "Could you detect copy-paste?" · "Does the
leaderboard change this?"

**What interviewers expect.** Recognising there *is* a public leaderboard, which already creates
a mild incentive. Submission timing plus paste-length would be a cheap heuristic signal, and
Judge0's execution stats offer another angle. Threat-modelling your own gamification is the
answer.

---

### Q205. How does the recommender interact with spaced repetition?

**Ideal answer.** Two loops with different objectives. The recommender pushes forward — new
problems at the right difficulty. Spaced repetition pulls back — topics whose estimated retention
has decayed. `ReviewQueueView` surfaces the second, driven by HLR decay and SM-2 scheduling
applied per submission. The tension is real: a student with a large review backlog and a
recommender eager to advance gets conflicting signals, and there is no arbitration between them
today.

**Why we chose this.** They were built as separate features and never unified.

**Alternatives.** Interleave reviews into the recommendation stream; block progression on
overdue reviews; let the student choose.

**Tradeoffs.** Interleaving is what a mature system does — the recommender should treat "review
this decayed topic" as one candidate action among the new problems, scored on the same scale.
That is a genuine design improvement and not implemented.

**Follow-ups.** "Which wins?" · "Should reviews be forced?" · "How would you unify them?"

**What interviewers expect.** Framing unification as a single ranking problem over a mixed
candidate set rather than two competing systems. That is the right architecture and describing it
clearly matters more than having built it.

---

### Q206. If you rebuilt the recommender, what would change?

**Ideal answer.** Three things, and they are the Milestone 4 plan. Add **two-sided Elo** with
`rating_deviation` plus an exploration term over it — that fixes content calibration and makes
propensity non-degenerate, so off-policy evaluation becomes possible, in one change. **Unify**
the recommendation and review loops into one ranking over a mixed candidate set. And **close the
evaluation loop** — schedule the decay jobs, run a calibration analysis, and consume the labelled
flywheel data that `retrain_ai` was written for. I would keep the one-sided Elo's expected-score
maths, the runs test, and the engine/data separation, because those are the parts that have
earned their place.

**Why we chose this.** Reflection.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "Why keep the runs test?" · "Is the engine split worth it?" · "What is the biggest
regret?"

**What interviewers expect.** Naming what you would keep, not just what you would change. The
engine/data separation is the structural decision that made everything else testable — 220 tests
run with no network and no model artifacts, because the engines are pure functions over passed-in
state. That is the part worth defending hardest.

---

## Section S — ML Operations & Evaluation (Q207–Q212)

---

### Q207. How are model artifacts versioned?

**Ideal answer.** The frozen architecture requires every artifact versioned — names like
`routing_classifier_v2.pkl` and `gcn_dsa@YYYYMMDD.onnx` in object storage, with loaders pinning
versions and no silent overwrites. In practice the repository holds `prerequisite_model.pth`
committed directly, which is not that. So the policy is specified and the implementation is a
file in git.

**Why we chose this.** Committing a small model file was expedient and works for a single
deployment.

**Alternatives.** MLflow; DVC; object storage with a manifest; Hugging Face Hub.

**Tradeoffs.** Committing binaries bloats the repository and gives no lineage — you cannot tell
which data or code produced that file. It is acceptable at one artifact and unacceptable at ten.

**Follow-ups.** "Why is that bad?" · "What would you use?" · "How do you roll back a model?"

**What interviewers expect.** Not claiming an MLOps pipeline you do not have. The gap between the
architecture's requirement and the repository's reality is documented as a divergence, which is
the right way to carry an unmet standard — visible rather than quietly dropped.

---

### Q208. What is a feature contract and do you have one?

**Ideal answer.** A documented specification of a model's input vector — order, meaning, scaling,
and handling of missing values — kept alongside the artifact so training and serving cannot
silently diverge. The architecture calls for it. In practice the routing classifier's three
features are defined by `compute_routing_telemetry` and passed positionally to `predict_route`,
so the contract is implicit in the code rather than explicit in an artifact.

**Why we chose this.** With three features and one call site, implicit is survivable.

**Alternatives.** A feature store; a typed schema; a serialisation format carrying names.

**Tradeoffs.** Positional arguments are exactly how training/serving skew happens — swap two
features during a refactor and the model produces confident nonsense with no error. Named
features would make that a `TypeError` instead.

**Follow-ups.** "What is training/serving skew?" · "How would you detect it?" · "Is three features
enough to worry?"

**What interviewers expect.** Skew described as a silent failure mode — the model still returns a
prediction, it is just wrong — which places it in the same category as every other failure in this
project. Consistency of failure taxonomy across domains reads as genuine understanding.

---

### Q209. How would you monitor a model in production?

**Ideal answer.** Three layers. Input drift — are the feature distributions moving away from
training? Prediction drift — is the route mix changing? And outcome monitoring, which is the one
that matters: calibration of predicted versus actual pass rates, bucketed. I have none of these.
What I do have is `RecommendationLog` with `engine_used`, `predicted_success_prob` and
`actual_result_correct` — enough raw material for outcome monitoring and calibration, which is
the layer that matters most, and not enough for the counterfactual analysis that needs
propensity.

**Why we chose this.** Outcome logging came for free with the flywheel that labels
recommendations. Propensity did not, and that is the omission.

**Alternatives.** Evidently AI; custom drift dashboards; alerting on metric thresholds.

**Tradeoffs.** Drift detection generates false positives on small samples, and with this user base
the noise would exceed the signal. Calibration on accumulated data is the more robust starting
point.

**Follow-ups.** "Why calibration first?" · "What is drift?" · "What alert would you set?"

**What interviewers expect.** Calibration justified as the highest-signal cheap check: if
predicted 70% meets observed 40%, the difficulty estimates are wrong and everything downstream is
untrustworthy. One query against existing data answers it — which makes not having run it a fair
criticism.

---

### Q210. What is the biggest ML risk in this system?

**Ideal answer.** That it is confidently wrong and nobody notices. Every engine produces a number,
every number gets used, and nothing validates that any of them corresponds to reality. The
per-topic Elo bug is the proof of concept: a gate ran against a field that was never updated, and
the system produced plausible-looking recommendations from a condition that could never be
satisfied. Recommendations are never obviously broken the way a 500 is — a bad recommendation
looks exactly like a good one.

**Why we chose this.** It is the defining property of ML failures versus software failures.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "How would you catch it?" · "Is that not true of all ML?" · "What would an alarm
look like?"

**What interviewers expect.** Concrete detection: calibration monitoring, plus invariant tests
like "a student who fails everything should see difficulty decrease" and "a mastered topic should
not be recommended before an unmastered prerequisite." Those are cheap, deterministic, and would
have caught the per-topic Elo bug immediately — a student who mastered everything would still get
the root topic.

---

### Q211. How do you keep the test suite offline with ML components?

**Ideal answer.** The engines are pure functions over passed-in state — they never import DRF,
never open a database connection, and receive their inputs as arguments. External I/O is injected
into services as callables rather than imported, which is the rule that makes it work. So 220
tests run with no network, no Judge0, no LLM, and no model artifacts, and the whole suite
completes in about 41 seconds.

**Why we chose this.** A test suite that reaches the network is a test suite nobody runs, and one
that needs a model artifact is one that breaks when the artifact moves.

**Alternatives.** Recorded fixtures; mocking at the library boundary; a test-only model.

**Tradeoffs.** Pure-function engines cannot be tested against real learner trajectories, so the
tests verify mechanics rather than pedagogical outcomes. Nothing in the suite asserts that the
adaptive system actually teaches anything.

**Follow-ups.** "What do the engine tests assert?" · "Could you test learning outcomes?" · "What
about the torch models?"

**What interviewers expect.** The distinction between testing mechanics and testing efficacy,
stated without embarrassment. Unit tests can prove Elo updates in the right direction with the
right magnitude; only an experiment can prove the system helps someone learn. Conflating the two
is the failure, and keeping them separate is the answer.

---

### Q212. Sell me the ML in this project honestly.

**Ideal answer.** The machinery is real and unusual for a project of this size: Elo skill
estimation with efficiency modulation and an anti-farming clamp, a forgetting model, a streak
detector chosen specifically because variance could not distinguish the cases that matter, a
curriculum DAG with enforced acyclicity, and graph-decay propagation across prerequisites. The
engineering around it is solid — pure engines, offline tests, decisions logged with their inputs.
What is **not** there and is often assumed: item difficulty does not calibrate (the Elo is
one-sided), and propensity is not logged, so off-policy evaluation is unavailable.
What is missing is the evaluation: nothing has proven the recommendations are better than a
sensible heuristic, the decay jobs are unscheduled, and the flywheel data has never been
consumed. So it is well-built and unvalidated, and those are different things.

**Why we chose this.** Overclaiming here is the fastest way to lose an interview, because
evaluation is the first thing an ML interviewer asks about.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "So is it just heuristics?" · "What would validate it?" · "Would you put this in
production for real users?"

**What interviewers expect.** A confident answer to the last question: yes, with monitoring —
because the failure mode is suboptimal recommendations rather than harm, the fallbacks are sane,
and the instrumentation to detect problems already exists. Being able to say "here is what I
would need to see before trusting it" is the answer of someone who understands what ML in
production actually requires.

---

## Part 5 Recap — Five More Stories

| # | Story | The one-line hook |
|---|---|---|
| 21 | **Variance could not see the wall** | `1010101010` and `1111100000` have identical variance and describe completely different students; the runs test separates them. |
| 22 | **The gate that never opened** | Routing checked per-topic Elo ≥ 1300 against a field nothing ever writes, so the DAG recommended the root topic forever. |
| 23 | **Difficulty that never calibrates** | `base_difficulty` is a static author prior written only by seed commands; a mislabelled problem stays mislabelled forever. Two-sided Elo is specified, not built. |
| 24 | **One float you cannot retrofit** | Propensity has to be logged *at decision time*; it is not, so every request to date is describable but not counterfactually evaluable. |
| 25 | **Partially seeded beats empty** | 1,100 questions with descriptions and zero test cases look complete to every automated process, so every process skips them. |

The ML through-line is **plausible output is the failure mode**. A 500 announces itself; a bad
recommendation looks exactly like a good one. Every safeguard here — the shared mastery
definition, the servable-questions filter, logged routing decisions with their inputs — exists
because the system will happily produce confident nonsense and nothing downstream will complain.
The two biggest gaps, uncalibrated item difficulty and unlogged propensity, are the same story
one level up: nothing in the system objects to either, which is exactly why they survived long
enough to end up in this handbook as claims rather than gaps.

---

*End of Part 5 (Questions 169–212). Part 6 — LLMs, RAG, Prompt Engineering, Judge0 & System Design — follows.*
