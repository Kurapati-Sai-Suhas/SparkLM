# SparkLM Technical Interview Handbook
## Part 4 — Performance, Scalability & Deployment

**Questions 127–168 of 254**
**Companion:** Document 06 (Performance Engineering), Document 01 §13–14
**Previous:** Part 1 (Backend & Concurrency) · Part 2 (Database, Caching & Redis) · Part 3 (Auth, JWT & Security)

---

## Section L — Measurement & Methodology (Q127–Q136)

---

### Q127. How do you approach a performance problem?

**Ideal answer.** Measure, form a falsifiable hypothesis, run a control, and be willing to
throw the hypothesis away. Concretely: I build a ladder of endpoints that differ by one known
stage each, difference their p50s to attribute cost per stage, then validate the resulting
additive model against endpoints it was not fitted on. My model reproduces nine production
endpoints with a **4.7% mean absolute error**, which is what gives me confidence the attribution
is real rather than a story that happens to fit.

**Why we chose this.** Because I got it wrong twice by reasoning instead of measuring, and both
times the wrong answer was more plausible than the right one.

**Alternatives.** Profiling with cProfile or py-spy; APM tracing; reading the code and reasoning;
flame graphs from a sampling profiler.

**Tradeoffs.** The ladder approach only gives p50 attribution — you cannot difference p95s,
because tail percentiles do not decompose additively. So I have precise median attribution and
no per-stage tail data, which I state as a limitation rather than papering over.

**Follow-ups.** "Why not just profile?" · "What is a control?" · "Why can you not difference
p95?"

**What interviewers expect.** The methodological caveat about percentiles, because most people
would quietly subtract p95s and present the result. Also that profiling in production was not
available to me — no APM on a 512 MB free instance — so external black-box measurement was
forced, and I made it rigorous instead of guessing.

---

### Q128. Walk me through your latency breakdown.

**Ideal answer.** For a login, server-side p50 of about 628 ms before the pooling fix, decomposed
as: Django middleware 8 ms, DRF dispatch plus the Redis throttle read 50 ms, first database
touch 390 ms, the user row SELECT 33 ms, Argon2id 127 ms, and JWT signing plus serialisation
20 ms. Network round-trip from my client added about 250 ms on top. The dominant stage was not
the cryptography everyone assumes — it was **connection establishment**, at 62% of server time.

**Why we chose this.** Because the intuitive answer was wrong. Everybody, including me, expected
the password hash to dominate.

**Alternatives.** N/A — it is a measurement.

**Tradeoffs.** N/A.

**Follow-ups.** "How did you separate connection cost from query cost?" · "How did you get the
Argon2 number?" · "What is the breakdown now?"

**What interviewers expect.** How you separated the stages, since that is the hard part. The
404-on-an-unrouted-path gives pure middleware with no database. A **429 response** gives
middleware plus DRF dispatch plus the Redis read, because the throttle short-circuits before the
view body runs. `/healthz` gives middleware plus exactly one query. Using an *error response* as
a measurement instrument is the trick worth mentioning.

---

### Q129. How did you measure Argon2's cost on a machine you cannot profile?

**Ideal answer.** Two independent methods that agreed. First, calibration from a known workload
change: swapping PBKDF2 at 1,000,000 iterations for Argon2 cut production login by 2,120 ms, and
the same swap on my laptop is 312 ms, so Render's CPU is **6.8× slower** than commodity hardware.
Applying that factor to a locally-measured Argon2 cost of 18.7 ms projects **127 ms** on Render.
Second, the endpoint ladder implies about 132 ms for the same stage by subtraction. Two methods
within 4% of each other is what makes the number trustworthy.

**Why we chose this.** No in-container profiler, so I had to infer — and a single inference is a
guess, while two agreeing inferences from different premises is evidence.

**Alternatives.** Deploy a timing endpoint; use Render's shell; accept an unmeasured estimate.

**Tradeoffs.** Both methods share an assumption — that the CPU factor is uniform across
workloads — which is not strictly true, since Argon2 is memory-bandwidth-bound while PBKDF2 is
compute-bound. So the agreement is slightly less independent than it appears, and I would say so
rather than overclaim.

**Follow-ups.** "Is the CPU factor uniform?" · "Why is that a fair calibration?" · "Could you
have deployed a probe?"

**What interviewers expect.** The self-critique. Volunteering that your two "independent" methods
share a hidden assumption is exactly the kind of rigour that distinguishes a real measurement
story, and it costs you nothing because the conclusion still holds.

---

### Q130. Tell me about a measurement you got wrong.

**Ideal answer.** The cold-start figure. I first measured 43.8 seconds and reported it. It was
**confounded by a deploy** — a push had landed at 09:30:37Z and the container I timed was warming
from a deployment, not from idle. I redesigned the test: a 21-minute quiesce with no deploys, no
keepalive pings, then a single cold request. The real number is **92.9 seconds**, more than twice
my original figure and much worse. I corrected the record and the number is now the justification
for the whole warm-keeper milestone.

**Why we chose this.** Because a measurement with an uncontrolled variable is not a measurement.

**Alternatives.** Average many cold starts; measure from platform logs; trust the platform's
documentation.

**Tradeoffs.** The clean test is expensive — 21 minutes of deliberate downtime per data point —
so I have few samples. I would rather have one trustworthy number than ten contaminated ones.

**Follow-ups.** "How did you notice?" · "Why does the direction matter?" · "How many samples?"

**What interviewers expect.** That the error made things look **better** than they were, which is
the dangerous direction. An optimistic wrong number gets accepted; a pessimistic one gets
challenged. Being the person who challenged their own favourable result is a strong signal.

---

### Q131. What was your contaminated control?

**Ideal answer.** During the concurrency investigation I ran a 20-way control against a hash-free
endpoint to test whether Argon2 was responsible for the collapse. It came back at 75 seconds, so
I concluded the endpoint collapses identically and Argon2 contributes almost nothing. That was
**wrong**: I had run the control immediately after the auth burst, while the service was still
saturated, so I was measuring the recovery from the previous test. A clean 40-way control on a
stable service returns **3.1 seconds with zero failures**. That reversed my published attribution.

**Why we chose this.** The lesson: a control must run from a known-clean state, and "immediately
after" is not clean.

**Alternatives.** Randomise test order; interleave arms; enforce a quiesce period between runs.

**Tradeoffs.** Interleaving is what I did in later A/B work — alternating requests between the
two arms so drift hits both equally — and it is strictly better than sequential blocks.

**Follow-ups.** "How did you catch it?" · "What did the clean control show?" · "How do you avoid
this now?"

**What interviewers expect.** The reversal stated plainly: the clean control showed the instance
handles 40 concurrent connections fine, so the problem was the *work*, not the concurrency
capacity. And the process fix — interleaving — which I actually adopted in the pooling A/B rather
than just resolving to be careful.

---

### Q132. How do you know your tests can actually fail?

**Ideal answer.** Mutation testing. I break the thing deliberately and confirm the test goes red.
It came from a real failure: I wrote seven tests for the warm-keeper workflow, they all passed,
and when I mutated the logic they **still all passed** — they were asserting on configuration
rather than behaviour. The fix was to extract the logic from inline YAML into
`scripts/keepalive.sh` and test it against a stub server with programmable delays and status
sequences. Now mutation catches it. I have applied the same discipline since: reverting
`anon: 30` fails the capacity test with `assert 35 <= 20`, and reverting the pooling config fails
all four pooling tests.

**Why we chose this.** A test that cannot fail is worse than no test, because it produces
confidence without coverage.

**Alternatives.** Coverage metrics; `mutmut` or `cosmic-ray` for automated mutation; code review.

**Tradeoffs.** Manual mutation is targeted and cheap but only checks the mutations you think of.
Automated mutation testing is thorough and slow. For a suite of 220 tests, running full automated
mutation would be minutes per change.

**Follow-ups.** "Why not coverage?" · "Why not automated mutation?" · "Which tests have you
mutated?"

**What interviewers expect.** The distinction between coverage and mutation: **coverage tells you
a line ran, not that anything checked what it did.** My seven green tests had good coverage of
the workflow and asserted nothing meaningful. That is the crispest possible argument against
coverage as a quality metric.

---

### Q133. What is p50 versus p95 versus p99, and which do you optimise for?

**Ideal answer.** p50 is the median — half of requests are faster. p95 and p99 are tail latencies:
5% and 1% of requests are slower than those. I optimise p50 here, deliberately, because on a
**serialised** backend the tail is dominated by queueing rather than by the work itself. If
requests queue, the p99 request is one that waited behind 20 others, so improving per-request
work improves the tail more than any tail-specific optimisation would. On a parallel system I
would optimise the tail directly, because there the tail usually means a slow path rather than a
queue.

**Why we chose this.** The queueing model changes which percentile is actionable.

**Alternatives.** Optimise p99 for user experience; use trimmed means; track a latency budget.

**Tradeoffs.** p50-focus can hide a genuinely pathological slow path. My `/register/` p95 was
2,185 ms against a p50 of 980 ms — that gap is real and I have not chased it.

**Follow-ups.** "Which matters more to users?" · "What is your worst p95?" · "Why is the tail
usually worse?"

**What interviewers expect.** The standard point that users experience the tail — a user making
20 requests per session hits p95 roughly once — plus the system-specific reason your priority
differs. Having a defensible reason to deviate from the standard advice is better than reciting
it.

---

### Q134. How do you load test without taking production down?

**Ideal answer.** Badly, the first time — I took production down. A 40-concurrent auth burst
produced 32 of 40 returning 502 and roughly 60 seconds of unavailability. That was within the
remit of a stress test and the finding justified it, but it was not risk-free and I should have
flagged it before running rather than after. Since then: concurrency capped at levels already
measured safe, sequential ladders instead of bursts where sequential answers the question, local
Daphne against the production database for anything I only need mechanism from, and clean
controls with interleaving.

**Why we chose this.** Because the alternative — never testing at scale — means discovering the
ceiling from a user report instead of a controlled experiment.

**Alternatives.** A staging environment; synthetic traffic in off-hours; shadow traffic;
extrapolation from smaller loads.

**Tradeoffs.** A staging environment is the correct answer and I do not have one, because it
doubles the hosting cost of a free-tier project. Extrapolation is cheap and is exactly what would
have missed the 502 cliff — the failure is non-linear, so 20 concurrent tells you nothing about
40.

**Follow-ups.** "Would you do it again?" · "What is the non-linearity?" · "What would staging
cost?"

**What interviewers expect.** Owning the outage without either minimising it or over-apologising.
The honest framing: the finding was worth having, the method was defensible, and the failure was
not warning the stakeholder first. That is the specific thing to say.

---

### Q135. What is the difference between throughput and latency in your system?

**Ideal answer.** Latency is how long one request takes; throughput is how many complete per
second. On a serialised backend they are directly coupled: throughput equals 1 divided by
per-request server time. My measured sequential throughput went from 1.43 to 2.67 requests per
second when p50 latency halved — the improvement is arithmetically the same event. On a parallel
system they decouple: you can improve throughput by adding workers without touching latency at
all.

**Why we chose this.** The coupling is why latency work was the *only* available lever. I could
not add workers, so reducing per-request time was the only way to increase capacity.

**Alternatives.** N/A — conceptual.

**Tradeoffs.** The coupling also means latency work has amplified value here: shaving 300 ms off
a request does not just make it faster, it raises the ceiling for every concurrent user.

**Follow-ups.** "Which did you optimise?" · "What is Little's Law?" · "Why did they decouple
after pooling?"

**What interviewers expect.** Little's Law connected to your numbers: concurrency equals
throughput multiplied by latency. At 2.67 requests per second and 0.39 seconds each, the system
sustains about one request in flight — which is exactly the serialisation finding, arrived at
from queueing theory instead of instrumentation. Two derivations agreeing is worth pointing out.

---

### Q136. How do you decide what to optimise?

**Ideal answer.** By measured contribution, not by intuition or by what is interesting to fix. The
attribution said connection establishment was 62% of a login and Argon2 was 20%, so pooling came
first even though the cryptography was the more exciting problem. And I ran the counterfactual
before committing: removing Argon2 *entirely* would drop server time from 628 ms to 501 ms, which
at 40 concurrent still produces an outage. That number is what told me the hasher was not the
problem — you cannot fix this by tuning the thing everyone assumes is expensive.

**Why we chose this.** The counterfactual is the discipline. It converts "this is 20% of latency"
into "removing it entirely would not solve the problem," which is a much stronger statement.

**Alternatives.** Optimise the biggest number; optimise what is easiest; optimise what users
complain about.

**Tradeoffs.** Measured-contribution ordering can miss compounding effects — several small wins
may beat one large one, and the model does not tell you that directly.

**Follow-ups.** "What is a counterfactual?" · "What would you optimise next?" · "Where does that
model break down?"

**What interviewers expect.** The counterfactual reasoning, because it is the step most people
skip. Knowing a component's share is not the same as knowing that eliminating it would help — and
being able to state the difference with your own numbers is the strongest version of that point.

---

## Section M — Optimisations Delivered (Q137–Q150)

---

### Q137. What was Milestone 1 and why did it matter?

**Ideal answer.** Keeping the free instance warm. Render stops a free web service after about 15
minutes idle, and a cold wake was measured at 92.9 seconds to first byte — which for a user is
indistinguishable from the site being down. The fix was a GitHub Actions workflow pinging
`/healthz`. Measured duty cycle went from 10.7% to 42.7%, verified over a 46.75-minute run.

**Why we chose this.** No other optimisation matters if the first request takes 93 seconds. It is
the cheapest large win available.

**Alternatives.** Pay for a plan that does not sleep; an external uptime monitor; accept cold
starts; a serverless frontend that hides the wake.

**Tradeoffs.** 42.7% is not 100%. Reaching near-100% needs an external uptime monitor, which is a
user action rather than code. And warming consumes Neon's compute budget too, since `/healthz`
round-trips the database.

**Follow-ups.** "Why only 42.7%?" · "Why not a cron job?" · "What does the duty cycle mean?"

**What interviewers expect.** The GitHub Actions detail, which is the interesting engineering:
free-tier cron is **heavily throttled**, and a 5-minute schedule was measured firing at gaps of
54 to 213 minutes — far longer than the 15-minute idle timeout. A naive cron keepalive keeps
nothing alive.

---

### Q138. How did you work around throttled cron?

**Ideal answer.** An in-run loop instead of relying on schedule frequency. Each workflow invocation
runs for 45 minutes, pinging every 5 minutes internally, with `timeout-minutes: 55` and
`concurrency: cancel-in-progress: false` so overlapping runs queue rather than kill each other.
So even if the scheduler fires every 90 minutes, each firing covers 45 minutes of warmth instead
of one instant.

**Why we chose this.** I could not make the scheduler more reliable, so I made each invocation
cover more time.

**Alternatives.** Multiple staggered workflows; an external monitor; self-scheduling via a
webhook.

**Tradeoffs.** Long-running jobs consume GitHub Actions minutes — a 45-minute job every couple of
hours adds up against the free allowance, which is a quota I documented as a trade-off to check
before enabling.

**Follow-ups.** "Why 45 minutes?" · "What about Actions minutes?" · "Why `cancel-in-progress:
false`?"

**What interviewers expect.** The timeout arithmetic. The loop is 45 minutes against a 55-minute
job timeout — an earlier version ran 50 minutes and **overran at 56.6 minutes**, hitting the
timeout and being killed. The margin exists because the loop plus checkout plus setup plus the
final probe is not exactly 45 minutes, and I measured the overrun rather than predicting it.

---

### Q139. Why is the keepalive logic in a shell script instead of the workflow YAML?

**Ideal answer.** Testability. It started as inline YAML with seven tests, all passing, and when I
mutated the logic they **all still passed** — they were asserting on workflow configuration, not
behaviour, because inline YAML cannot be executed by a test. Extracting it to
`scripts/keepalive.sh` let me run it against a stub server with programmable per-request delays
and status sequences. Eighteen tests now, split into a contract layer that reads the workflow
config and a behavioural layer that actually runs the script.

**Why we chose this.** If logic cannot be executed by a test, it cannot be tested.

**Alternatives.** Keep it inline and test with `act`; a Python script; a hosted monitor with no
code at all.

**Tradeoffs.** Two files instead of one, and the workflow now has to stay in sync with the
script's defaults — which is why the contract layer exists, mirroring bash's resolution order so
a divergence fails a test.

**Follow-ups.** "What does the contract layer do?" · "How do you stub a server?" · "What did
mutation catch?"

**What interviewers expect.** The design details that show real care: the loop pings **first**,
so `LOOP_SECONDS=0` performs exactly one ping and acts as a kill switch; there is no sleep after
the final probe; and the job **fails on cold-after-warm**, so a regression is visible rather than
silent. Also millisecond arithmetic via awk, because an earlier integer version reported
"max: 1s" for 1.432 seconds.

---

### Q140. What was Milestone 2?

**Ideal answer.** Fixing the code editor. Students were sometimes shown an **empty editor** and
expected to reconstruct a `Solution` class from nothing, because the question had no template for
their chosen language. Multi-language template coverage is only about 35%. The fix was a
`BOILERPLATE_KEYS` alias map to reconcile the several spellings languages had acquired, an
`EMPTY_STUB` fallback with compilable skeletons, and a `templateMissing` state that **labels and
disables** a language with no template rather than silently opening a blank editor.

**Why we chose this.** An empty editor is not a missing feature, it is a broken one — the student
does not know whether they are looking at a bug or a deliberately blank canvas.

**Alternatives.** Generate templates on demand with an LLM; hide languages without templates
entirely; always show a generic stub.

**Tradeoffs.** Hiding languages would be cleaner UI and worse information — the student cannot
tell whether the language is unsupported or just unseeded for that problem. Labelling is more
honest.

**Follow-ups.** "Why is coverage only 35%?" · "Why not generate on demand?" · "What is
`SELF_CONTAINED_LANGUAGES`?"

**What interviewers expect.** The C and C++ special case: those have no generic wrapper, so
students must write a complete program including `main()`. `SELF_CONTAINED_LANGUAGES` is the set
that gets a full skeleton rather than a method stub, which is a language-model distinction, not a
cosmetic one.

---

### Q141. Tell me about a bug you shipped.

**Ideal answer.** During Milestone 2 I authored a Java template for question 3307 — the
deterministic first problem every new user receives — and it was **broken**. Every submission
returned `compile_error`. The root cause is the instructive part: I wrote the template against
the *problem statement* rather than against the *wrapper*, and I never executed it. So a student's
first ever interaction with the platform was a guaranteed compile failure.

**Why we chose this.** The fix was to correct the template and then to build
`wrapper_contract.py`, which mechanically checks that a template's declared method signature
matches what the wrapper will invoke, plus `manage.py audit_wrapper_templates` to run that check
across production data.

**Alternatives.** Manual review of every template; require an executed submission before
publishing; generate templates from the wrapper automatically.

**Tradeoffs.** Generating templates from the wrapper would make mismatch structurally impossible
and is the better long-term answer. The contract checker catches the error class without
restructuring content generation.

**Follow-ups.** "How did you find it?" · "How does the checker work?" · "Why the first problem?"

**What interviewers expect.** The lesson stated as a rule: **a template that has never been
executed is not a template, it is a guess.** And a second-order detail worth telling — my own
checker initially false-positived on every Python template, because it counted `self` as a
parameter. I made it language-aware and added four tests. The tool that catches your bugs needs
its own tests.

---

### Q142. Milestone 3 Phase A — what did Argon2 actually buy you?

**Ideal answer.** Login p50 dropped from 3,340 ms to 1,220 ms — a 63% reduction — because
PBKDF2 at 1,000,000 iterations cost roughly 2,560 ms of CPU on Render's throttled processor while
Argon2id at the pinned parameters costs about 127 ms. So the security upgrade was also the
single largest latency win of that milestone. Startup cost went up 141 ms from importing the
library, which is a fair trade.

**Why we chose this.** It is the unusual case where the more secure option is also the faster one,
because PBKDF2's security comes purely from iteration count and Argon2's comes from memory
hardness.

**Alternatives.** Lower the PBKDF2 iteration count (faster and weaker); bcrypt; keep PBKDF2.

**Tradeoffs.** Lowering PBKDF2 iterations would have got the latency win without a migration and
without the new dependency — and would have weakened security to do it. Argon2 got both.

**Follow-ups.** "Why is Argon2 faster?" · "Is that not suspicious?" · "What about memory?"

**What interviewers expect.** The explanation for why "more secure and faster" is not a
contradiction: they buy security with different resources. PBKDF2 buys it with time, which you
pay on every login. Argon2 buys it with memory, which is cheap for the defender at 19 MiB and
expensive for an attacker running thousands in parallel. That asymmetry is the whole point of
memory-hard functions.

---

### Q143. What did the connection pooling fix deliver?

**Ideal answer.** In production, `/healthz` p50 went from 699 ms to 391 ms — a 44% reduction —
and the database stage specifically went from 421 ms to 109 ms, a 74% reduction. Sequential
throughput went from 1.43 to 2.67 requests per second, 1.87×. Every database-touching endpoint
improved proportionally to how much of it was handshake: `/healthz` at one query gained 44%,
while `/code/next/` at 16 queries gained 19%.

**Why we chose this.** That gradient is itself the evidence the attribution is correct — the win
scales inversely with query count, which is exactly what removing a *fixed* per-request cost
predicts.

**Alternatives.** External pooling only; fewer queries; a closer database region.

**Tradeoffs.** It introduced a new failure mode: demand above `max_size` waits and raises
`PoolTimeout` if the wait expires, converting overload into 500s where it previously just
queued. I flagged that as a HIGH untested risk in my own review and closed it in the next phase
with an oversubscription test.

**Follow-ups.** "Why does the gradient prove anything?" · "What is the new failure mode?" ·
"Was the test suite affected?"

**What interviewers expect.** The gradient argument, which is a genuinely elegant piece of
evidence, and the incidental proof: the **test suite** went from 107 seconds to 41 seconds,
because tests were paying the same per-connection handshake the application was. A side effect
you did not design for, in the predicted direction, is strong corroboration.

---

### Q144. Why did collapsing five requests into one help so much?

**Ideal answer.** Because on a queueing backend, browser parallelism is not server parallelism.
Five requests fired "in parallel" from the browser still queue for the same single worker, so
they pay the full per-request tax five times — middleware, JWT verification, a user lookup, a
throttle read, connection checkout. The marginal cost of an extra *query* is about 33 ms; the
marginal cost of an extra *request* is the whole pipeline. Dashboard p50 went from 1,207 ms to
833 ms, and one of the five was a staff-only endpoint returning 403 for essentially every user.

**Why we chose this.** Request count, not query count, is the dominant term on this architecture.

**Alternatives.** HTTP/2 multiplexing (does not help — the bottleneck is the server); GraphQL;
caching each endpoint separately.

**Tradeoffs.** The bootstrap endpoint duplicates small queries from two other view modules rather
than importing across them — a documented trade of DRY for deletability, so it can be removed
later without touching what it mirrors.

**Follow-ups.** "Would HTTP/2 help?" · "Why not GraphQL?" · "Is the duplication acceptable?"

**What interviewers expect.** Why HTTP/2 is a red herring here — it removes connection overhead
and head-of-line blocking at the *transport*, and the constraint is server-side compute. Being
able to reject a plausible-sounding optimisation with a reason is as valuable as proposing one.

---

### Q145. What is the biggest remaining performance problem?

**Ideal answer.** The serialisation, and it is not close. Everything else is a constant factor on
per-request work; serialisation is a structural cap on throughput at 1/W regardless of hardware.
Second is the RAG endpoint, which re-reads, re-extracts and re-chunks the source document on
**every single question** with no persisted index — the most expensive uncached operation in the
system. Third is the 0.1 vCPU, which multiplies every CPU-bound stage by 6.8.

**Why we chose this.** Ordering by whether the problem is structural or constant-factor.

**Alternatives.** Chase the `/register/` p95 outlier; reduce query counts; add caching.

**Tradeoffs.** Serialisation is also the most expensive to fix — it needs multiple workers, which
needs memory, which needs a paid plan. The RAG index is a much smaller change with a large local
win, so by value-per-effort it might come first.

**Follow-ups.** "So which do you fix first?" · "What would the RAG fix look like?" · "Is the CPU
fixable?"

**What interviewers expect.** Separating "biggest problem" from "first thing I would do." The
biggest problem is serialisation; the first thing I would do is a paid instance, because it is a
configuration change that both relieves the CPU factor and unlocks the multi-worker fix. Naming
the dependency order matters more than naming the biggest number.

---

### Q146. How much of your performance work was undone by a wrong hypothesis?

**Ideal answer.** None of the work, but a full review round of the attribution. I concluded Argon2
memory was the cause of the concurrency collapse and published that. It was wrong — the
mechanism was real, since memory does scale linearly at 19.1 MiB per hash under a barrier test,
but it was irrelevant because requests never overlap. What corrected it was a clean control:
40 concurrent requests to a hash-free endpoint complete in 3.1 seconds with zero failures. So the
instance handles the concurrency fine and the problem is the work, not the load.

**Why we chose this.** The distinction worth carrying: **confirming a mechanism is not confirming
a cause.** I ran a rigorous experiment that produced a true result and drew a false conclusion
from it.

**Alternatives.** N/A.

**Tradeoffs.** The barrier experiment was not wasted — it produced the memory-per-hash figure
that justifies the pinned parameters and the instance budget formula. A correct measurement
attached to a wrong conclusion is still a correct measurement.

**Follow-ups.** "How long did you believe it?" · "What made you re-check?" · "Would you publish a
hypothesis again?"

**What interviewers expect.** That you labelled it as a hypothesis rather than a finding when the
evidence was incomplete, and that the correction is *in the record* rather than quietly edited
out. Interviewers weight this heavily, because it is the one thing that cannot be faked from a
tutorial.

---

### Q147. Your rate limits are a performance control. Explain.

**Ideal answer.** The previous ceiling let one IP send `anon(30) + auth(10) = 40` requests per
minute, and 40 concurrent auth requests is exactly the load measured returning 32 of 40 as 502
with about 60 seconds of unavailability. **The limit authorised the outage** — a client could
exhaust the service without ever exceeding its rate limit. I lowered `anon` to 15 and `auth` to
5, giving a ceiling of 20 per minute, which is the highest burst measured to complete with zero
failures.

**Why we chose this.** Because throughput does not grow with arrival rate on a serialised backend,
so the limit has to be set from measured capacity rather than from security intuition.

**Alternatives.** Admission control (the correct fix); a bigger instance; leave the limits and
accept the risk.

**Tradeoffs.** Per-IP limits punish shared infrastructure — a campus behind one NAT shares a
bucket, so 15 per minute for a whole lab is tight. That is a deliberate trade of headroom for
survivability, and it is documented in the settings comment.

**Follow-ups.** "Is a rate limit the right tool?" · "What about NAT?" · "How do you know 20 is
safe?"

**What interviewers expect.** The honest concession that a rate limit is the **wrong instrument**
for a burst problem — it caps requests per *minute* while the outage came from requests
*simultaneously*. It narrows the window; it does not close it. Admission control is the actual
fix, and saying so while still defending the change as the right immediate action is the mature
position.

---

### Q148. What did you measure to justify 20 concurrent as the safe ceiling?

**Ideal answer.** A concurrency ladder against production auth: 10 concurrent completed in 7.1
seconds with zero failures; 20 concurrent completed in 103 seconds with zero failures — badly
degraded but correct; 40 concurrent produced 32 of 40 as 502 with roughly a minute of
unavailability. So 20 is the highest level where every request still completes. It is encoded in
a test as `MEASURED_SAFE_CONCURRENCY = 20` with a comment sourcing it to those measurements, and
the test asserts the configured ceiling stays at or below it.

**Why we chose this.** Putting the number in a test with its provenance means someone raising the
limits has to consciously override a documented measurement rather than editing a config value.

**Alternatives.** Pick a round number; derive from CPU count; use the platform's guidance.

**Tradeoffs.** 20 concurrent at 103 seconds is not a good user experience — it is merely not an
outage. A ceiling set for *quality* rather than survival would be closer to 10.

**Follow-ups.** "Is 103 seconds acceptable?" · "Why not set it to 10?" · "Is that test
mutation-tested?"

**What interviewers expect.** Yes on the mutation question — restoring `anon: 30` fails it with
`assert 35 <= 20`. And a straight answer on 103 seconds: it is not acceptable, the ceiling is set
at the failure boundary rather than the quality boundary, and that is a deliberate choice to
avoid over-restricting legitimate users on a platform with a small user base.

---

### Q149. What performance work did you decide *not* to do?

**Ideal answer.** Several things, each for a stated reason. Async views throughout — it would
require rewriting every ORM call and `sync_to_async` reintroduces the same thread-sensitivity, so
the payoff is uncertain and the change is enormous. Multiple workers — blocked by memory, since
four workers at roughly 202 MB resident exceeds 512 MB before a single request arrives. Chasing
the `/register/` p95 outlier — real but small, and registration is not a hot path. Caching
recommendations — the biggest theoretical win and the most dangerous, since a recommendation
depends on state that changes with every submission.

**Why we chose this.** Each was blocked by a constraint, not by effort.

**Alternatives.** N/A.

**Tradeoffs.** Not doing the async rewrite means accepting the ceiling. It is the right call at
this scale and would be the wrong call at ten times the traffic.

**Follow-ups.** "Why is caching recommendations dangerous?" · "What would change your mind?" ·
"Is the async rewrite really that big?"

**What interviewers expect.** A trigger condition for revisiting, not just a refusal. Mine: the
moment there is a paid instance, multiple workers becomes the highest-value change, and it
changes the calculus for everything else — the locking granularity that is currently wasted
starts paying off, and admission control becomes urgent rather than advisable.

---

### Q150. Summarise the performance improvement across all milestones.

**Ideal answer.** Login p50 went from 3,340 ms to about 600 ms, an 82% reduction. The password
hash went from roughly 2,560 ms to 127 ms. `/healthz` went from 699 ms to 391 ms. The database
first-touch stage went from 421 ms to 109 ms. Connections per 20–25 requests went from 21 to 3.
Sequential throughput went from 1.43 to 2.67 requests per second. And throttling went from
provably inert to enforcing at exactly the configured limit. Cold start is unchanged at 92.9
seconds, because no milestone targeted it — the warm-keeper avoids it rather than shortening it.

**Why we chose this.** Reporting what did not improve alongside what did.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "What is still slow?" · "Which change mattered most?" · "What is unmeasured?"

**What interviewers expect.** The unmeasured admission: **memory and CPU inside the container
were never observed.** Every memory conclusion is inference from external symptoms. Closing a
performance summary with what you could not measure — rather than only what you improved — is the
part that makes the rest credible.

---

## Section N — Scalability (Q151–Q160)

---

### Q151. Is SparkLM horizontally scalable today?

**Ideal answer.** Architecturally yes, operationally no. The application is stateless — sessions
are JWTs, the cache and Channels layer are in Redis, and media would need object storage — so
adding instances would work. What blocks it is that migrations run in the start chain, which is
safe only with exactly one instance; with two, a rolling deploy puts old and new code against a
mid-migration schema. So the first step to horizontal scaling is moving migrations to a
pre-deploy step and adopting expand-then-contract, not adding instances.

**Why we chose this.** Statelessness was designed in; the deploy chain was a free-tier
convenience.

**Alternatives.** Sticky sessions; a shared session store; single instance forever.

**Tradeoffs.** Migrations in the start chain mean a failed migration prevents boot, which is
arguably correct — running new code against an old schema is worse. It just does not survive
multiple instances.

**Follow-ups.** "What is expand-then-contract?" · "What else breaks at two instances?" · "Is
media on disk?"

**What interviewers expect.** A specific second-instance failure that is not obvious: the
**Channels in-memory fallback**. If `REDIS_URL` is ever unset, `group_send` stops crossing
processes and a chat message published by one instance is invisible to sockets on another —
silently. It works today only because there is one process, which makes it a latent bug waiting
on a configuration change.

---

### Q152. Walk me through scaling to 10,000 concurrent users.

**Ideal answer.** Ordered by constraint removal. First, a paid instance — 0.1 shared vCPU is 6.8×
slower than commodity, so every CPU-bound millisecond is multiplied, and the memory is what
unblocks the next step. Second, break the serialisation with Gunicorn plus uvicorn workers at N
processes. Third, admission control, so overload sheds fast with 503 rather than queueing to 502.
Fourth, move grading to Celery so submissions return 202 immediately and results arrive over
WebSocket. Then horizontal scaling behind a load balancer, then a read replica for dashboard
aggregates, then partition archival to keep the hot table small.

**Why we chose this.** Each step unblocks the next. Adding workers before adding memory just OOMs
faster.

**Alternatives.** Rewrite in an async framework; serverless; vertical scaling only.

**Tradeoffs.** Async grading changes the client contract from synchronous to eventual, which is
real frontend work — submission states become `queued → running → verdict` rather than a single
blocking call.

**Follow-ups.** "What breaks first?" · "Why Celery over async views?" · "Where does the database
enter?"

**What interviewers expect.** The database entering *late*, and the reason: SparkLM is CPU-bound
before it is database-bound. Resisting the urge to lead with database scaling — the most
interesting problem — in favour of the actual constraint is the answer that demonstrates
judgement.

---

### Q153. What would you extract into a service first?

**Ideal answer.** Grading, and it is already shaped for it. `GradingService` takes an injected
runner callable, so the boundary is a function signature rather than a tangle of imports. It is
also the natural candidate on workload grounds: it is I/O-bound on Judge0, bursty, and its
failure mode is already isolated behind `GradingUnavailable` returning 503. Second would be
content generation — LLM-bound, batch-shaped, already asynchronous in character. Recommendations
third, because they are tightly coupled to learner state and would need the most data access.

**Why we chose this.** Extract along the seam that already exists, not the one you wish existed.

**Alternatives.** Extract by domain; extract the highest-traffic path; do not extract.

**Tradeoffs.** Extracting grading means a network hop where there is currently a function call,
plus a result-delivery contract. Celery inside the modulith gets most of the benefit — async
grading, isolated failure — without the distributed-systems cost, and the architecture specifies
exactly that for a later phase.

**Follow-ups.** "Why not just Celery?" · "What is the contract?" · "When is a service justified?"

**What interviewers expect.** Preferring Celery to a microservice, and saying why: the goal is
**asynchrony and isolation**, and a worker tier delivers both without introducing a network
boundary, a deployment, and a versioned API. Reaching for the smaller tool is the senior answer.

---

### Q154. How would you shard the database?

**Ideal answer.** On `user_id`, because nearly every query is user-scoped — submissions, mastery,
profile, recommendations all filter by user first, which is why `user` is the leading column of
almost every index. The awkward tables are the shared ones: `Question`, `Topic`, and
`TopicPrerequisite` are global reference data, so they would be replicated to every shard rather
than sharded. Cross-shard queries would be the leaderboard and any cohort analytics, which would
need scatter-gather or a separate aggregate store.

**Why we chose this.** The access pattern picks the shard key, and here it is unusually clean.

**Alternatives.** Shard by portal or subject; time-based sharding; do not shard, use a read
replica plus partitioning.

**Tradeoffs.** Sharding is a large operational step for a system that is CPU-bound, so it is far
down the list. Partitioning plus a read replica handles a great deal before sharding is
justified.

**Follow-ups.** "What about the leaderboard?" · "How do you rebalance?" · "Would you shard at
all?"

**What interviewers expect.** "Probably never, at realistic scale for this product" as the honest
conclusion, with the design articulated anyway. Interviewers often ask sharding questions to see
whether a candidate can design it *and* recognise it is premature — answering only the first half
is the more common failure.

---

### Q155. What is the difference between vertical and horizontal scaling here?

**Ideal answer.** Vertical scaling would immediately help more than usual, because two of my
constraints are per-instance: 512 MB caps worker count, and 0.1 shared vCPU multiplies every
CPU-bound stage by 6.8. A larger instance relieves both without any code change. Horizontal
scaling needs the deploy-chain fix first and delivers nothing until serialisation is addressed —
more instances each serialising internally is a poor use of money compared to more workers within
one.

**Why we chose this.** On this deployment, vertical comes first, which is the reverse of the
usual advice.

**Alternatives.** Horizontal-first; autoscaling; hybrid.

**Tradeoffs.** Vertical has a ceiling and a single point of failure. But the standard "scale
horizontally, it is more resilient" advice assumes your instances are already efficiently
utilised, and mine is not — a single worker on a bigger box would beat three single-worker small
boxes for less money.

**Follow-ups.** "Is that not against best practice?" · "Where is the vertical ceiling?" · "What
about availability?"

**What interviewers expect.** Willingness to contradict standard advice with a specific reason.
The general rule assumes efficient per-instance utilisation; mine is structurally inefficient, so
the rule does not apply until the serialisation is fixed. Knowing when a best practice's
preconditions are unmet is a genuinely senior distinction.

---

### Q156. What is a bulkhead and where would you add one?

**Ideal answer.** A bulkhead partitions resources so one failing component cannot consume
everything. SparkLM has essentially none, and the clearest need is around Judge0: a hung
grading call holds a worker and a database connection from a pool of ten, so ten simultaneous
Judge0 hangs would exhaust the pool and take down endpoints that have nothing to do with grading.
A bulkhead would cap concurrent Judge0 calls independently, so grading degrades while
authentication and dashboards keep working.

**Why we chose this.** Not chosen — it is a gap. The connection between "a slow third party" and
"the whole app is down" is exactly what bulkheads prevent.

**Alternatives.** Circuit breaker; separate connection pool for grading; move grading to a worker
tier.

**Tradeoffs.** A circuit breaker and a bulkhead solve adjacent problems — the breaker stops
calling a service that is failing, the bulkhead limits the damage while it is failing slowly.
Slow is the harder case, and it is the one I have.

**Follow-ups.** "Bulkhead versus circuit breaker?" · "How would you implement it?" · "What is the
pool interaction?"

**What interviewers expect.** The pool-exhaustion chain traced explicitly: Judge0 hangs →
transactions held open → connections held → pool exhausted at 10 → every endpoint fails. Tracing
a local failure to a global outage through a shared resource is the reasoning the question is
testing.

---

### Q157. How would you handle a traffic spike tomorrow?

**Ideal answer.** Today the rate limits absorb it by rejecting — a per-IP ceiling of 20 per minute
means a spike from many distinct users is not throttled and would queue to 502, while a spike
from few users is throttled. So the honest answer is that a broad spike is not handled: it
degrades to unavailability rather than to slowness. Immediate mitigations without a deploy would
be tightening the limits further and, if it were a single source, blocking at the edge. The real
answer is admission control plus a bigger instance.

**Why we chose this.** Being straight that the current posture is "fail rather than degrade" for
broad spikes.

**Alternatives.** Autoscaling; a queue with backpressure; a static fallback page; CDN caching for
read paths.

**Tradeoffs.** CDN caching would help the read-heavy public surface and does nothing for
authenticated adaptive traffic, which is the actual product.

**Follow-ups.** "What about a CDN?" · "Could you autoscale?" · "What is your capacity in users?"

**What interviewers expect.** A capacity number rather than an adjective: roughly 2.67 requests
per second sustained, about 20 concurrent before degradation. For an interactive product that is
perhaps 50–100 active users depending on request rate per session. Converting throughput into
users, with the assumption stated, is the answer.

---

### Q158. What breaks first as load increases?

**Ideal answer.** In order: the single worker thread saturates and requests queue; latency grows
linearly with concurrency; at around 20 concurrent latency is bad but nothing fails; somewhere
between 20 and 40 the load balancer starts returning 502 for requests that have already consumed
20-plus seconds. The connection pool at `max_size=10` is the next constraint after that, and CPU
is the underlying limit throughout. What does *not* break early is the database or Redis — they
are barely loaded, because the application cannot generate enough requests to stress them.

**Why we chose this.** Measured, not modelled: 10 concurrent at 7.1 seconds, 20 at 103 seconds,
40 broken.

**Alternatives.** N/A.

**Tradeoffs.** The failure is **non-linear** between 20 and 40, so extrapolating from small loads
would have missed the cliff entirely. That is the argument for testing at the boundary rather
than projecting.

**Follow-ups.** "Why is it non-linear?" · "Where exactly is the cliff?" · "Why is the DB not the
bottleneck?"

**What interviewers expect.** Honesty that the exact cliff location is unknown — I have data at
20 and 40 and nothing between, because narrowing it means more production outages. Knowing the
resolution of your own data is part of reporting it.

---

### Q159. How does the adaptive engine scale?

**Ideal answer.** The engines are pure functions over passed-in state, so they parallelise
trivially — the constraint is entirely in the data access around them. `/code/next/` runs about
16 queries, which at 33 ms each is roughly 530 ms of round-trips, so the recommender is
database-round-trip-bound rather than compute-bound. The DAG is already cached in Redis, which
removes the largest repeated read. Scaling it means reducing query count, not making the
algorithms faster.

**Why we chose this.** The layering pays off here: engines that never touch the database can be
moved, cached, or parallelised without touching the logic.

**Alternatives.** Precompute recommendations offline; cache per-user candidate sets; move to a
worker tier.

**Tradeoffs.** Precomputing conflicts with adaptivity — the recommendation depends on state that
changes with every submission, so a precomputed one is stale by construction. A short-lived cache
invalidated on submission would be the safe middle ground.

**Follow-ups.** "Could you precompute?" · "Why 16 queries?" · "What would you cache?"

**What interviewers expect.** The engine/data split as the reason scaling is tractable, and the
specific target: reducing 16 queries via `select_related` and batching would cut the endpoint
substantially without touching the adaptive logic at all.

---

### Q160. What is your single point of failure?

**Ideal answer.** Several, and they are all the same shape — one of everything. One backend
instance, one database with no replica, one Redis serving cache, throttles and the Channels
layer simultaneously. The Redis one is the most interesting because the three roles fail
*differently*: caching degrades to slow-but-correct, throttling fails **open** which is a
security degradation, and WebSockets fail closed. Three severities behind one dependency.

**Why we chose this.** Free tier. Redundancy costs money and this is a portfolio deployment.

**Alternatives.** Multi-instance; database replica; separate Redis per role; multi-region.

**Tradeoffs.** The architecture already specifies splitting Redis into a cache/channels instance
and a separate Celery broker, so the split is planned rather than overlooked — it just is not
built.

**Follow-ups.** "Which failure is worst?" · "Why does throttling fail open?" · "What would you
add first?"

**What interviewers expect.** Fail-open versus fail-closed analysed per role. A cache miss is
harmless; a throttle that silently stops enforcing is a security control that has vanished
without notice — which is not hypothetical, it is exactly what happened. Being able to rank your
own single points of failure by *failure semantics* rather than by likelihood is the stronger
analysis.

---

## Section O — Deployment & Release Engineering (Q161–Q168)

---

### Q161. Walk me through your deployment.

**Ideal answer.** Push to `main` triggers both Render and Vercel. Render builds from
`render.yaml`: `pip install -r requirements.txt`, then `collectstatic`, then a start chain of
`migrate --noinput`, `ensure_submission_partitions`, and `daphne -b 0.0.0.0 -p $PORT
LearnLM.asgi:application`. `healthCheckPath` is `/healthz`, so the platform waits for a
database-backed health check before routing traffic. Vercel builds the SPA and serves it from a
CDN. GitHub Actions runs the 220-test suite in parallel and separately runs the warm-keeper.

**Why we chose this.** Blueprint-as-code so the topology is reviewable, with secrets marked
`sync: false` so the file is committable while values live only in the dashboard.

**Alternatives.** Docker with a registry; Terraform; manual deploys; a monorepo pipeline
coordinating both.

**Tradeoffs.** Two independent auto-deploys means frontend and backend can be briefly out of
step, which is fine for additive changes and would not be for a breaking API change — and the API
is unversioned, which compounds it.

**Follow-ups.** "What if the frontend deploys first?" · "Why migrations in the start chain?" ·
"How do you roll back?"

**What interviewers expect.** Recognising the unversioned-API-plus-independent-deploys coupling
as a real risk, and the mitigation: keep changes additive, and version the API before that stops
being sufficient.

---

### Q162. How do you roll back?

**Ideal answer.** Render redeploys a previous commit, which handles code. What it does not handle
is migrations — a schema change is not reverted by redeploying old code, so a rollback across a
migration boundary means old code against a new schema. That is survivable only if migrations are
backward-compatible for one release, which the architecture requires. And there is a
domain-specific rollback hazard: reverting the password hasher must be a **reorder, not a
removal**, or every migrated user is locked out.

**Why we chose this.** Platform rollback is free; the discipline around it is what has to be
documented.

**Alternatives.** Blue-green deploys; feature flags; database snapshots before each deploy.

**Tradeoffs.** Blue-green is specified for a later phase and needs two environments, which the
free tier cannot provide. Feature flags would let me decouple deploy from release, which is the
cheaper approximation and I use a form of it already — `CURRICULUM_GATE_ENFORCE` and
`ENABLE_SHAP_XAI` are both flags that ship code in a disabled state.

**Follow-ups.** "What about the database?" · "Have you rolled back?" · "What is
expand-then-contract?"

**What interviewers expect.** The hasher rollback as a concrete example of a rollback that is
*not* just "deploy the old thing" — it has a domain-specific safe procedure documented in the
runbook and pinned by two tests, one of which asserts the unsafe path fails.

---

### Q163. Why do migrations run in the start command?

**Ideal answer.** Because the free plan has no pre-deploy hook, and with exactly one instance it
is safe — there is no window where two versions run against different schemas. It also means a
failed migration prevents boot, which is arguably right: running new code against an old schema
is worse than not starting. The moment a second instance exists this becomes wrong, and the fix
is a pre-deploy step plus expand-then-contract migrations.

**Why we chose this.** Correct under the current constraint, with the constraint written down.

**Alternatives.** Pre-deploy hook; a separate migration job; manual gates.

**Tradeoffs.** A slow migration delays every restart — including restarts triggered by unrelated
crashes, which is when you least want a delay.

**Follow-ups.** "What is expand-then-contract?" · "What if a migration fails?" · "Have you had
one fail?"

**What interviewers expect.** Expand-then-contract described concretely: add the column nullable,
deploy code that writes both old and new, backfill, deploy code that reads new, then drop old.
Each step is independently deployable and independently reversible. And the honest note that
SparkLM has never needed it, because it has never had two instances — do not claim a practice you
have not exercised.

---

### Q164. What runs in CI?

**Ideal answer.** The 220-test suite against real Postgres, not SQLite, because the schema uses
partitioning and pgvector that SQLite cannot represent. There is no frontend test runner — the
architecture specifies Vitest plus React Testing Library on four critical flows and it is not
implemented, which is recorded as a divergence. And there is no linting gate, no type checking in
CI, and no dependency audit.

**Why we chose this.** Backend correctness carried the highest risk, so the testing investment
went there.

**Alternatives.** Add Vitest; add `ruff` and `mypy`; add `pip-audit`; require coverage
thresholds.

**Tradeoffs.** The frontend gap is real — `AdaptiveCodingPortal.tsx` carries genuine domain logic
in `templateFor` and `availableLanguages`, and none of it is tested. That is the highest-value CI
addition available.

**Follow-ups.** "Why no frontend tests?" · "Would you add coverage gates?" · "What about type
checking?"

**What interviewers expect.** Resistance to coverage thresholds, with the reason: my own seven
green keepalive tests had coverage and asserted nothing, so a coverage gate would have passed
while the tests were worthless. Mutation testing on the load-bearing tests is a better use of the
same effort.

---

### Q165. How do you know a deploy succeeded?

**Ideal answer.** `healthCheckPath` gates traffic on `/healthz`, which round-trips the database,
so Render will not route to an instance that cannot reach Postgres. Beyond that I verify
manually: after the pooling deploy I ran a 13-check production battery covering registration,
login, JWT issue and refresh, a protected route, the 401 paths, Google SSO, the adaptive
endpoint, throttle enforcement, and a concurrency check — all passing, plus latency measurements
confirming the improvement landed. The boot probes also log to the deploy log, so a silent cache
or hasher misconfiguration is visible there.

**Why we chose this.** A health check proves the process started; it does not prove the change
worked.

**Alternatives.** Automated smoke tests post-deploy; canary deploys; synthetic monitoring.

**Tradeoffs.** Manual verification does not scale and depends on me remembering. Scripting that
battery as a post-deploy job is an obvious improvement I have not made.

**Follow-ups.** "Is that automated?" · "What is a canary?" · "How long until you would notice a
problem?"

**What interviewers expect.** An honest answer to the last one: without alerting, I would notice
when I looked or when someone told me. Sentry catches unhandled exceptions, but the failures that
have actually mattered in this project were **silent** — no exception, no error, just a control
that stopped working. Sentry would have caught none of them.

---

### Q166. What is in your deployment runbook?

**Ideal answer.** `docs/DEPLOYMENT.md` covers one-time setup for Neon, Upstash, Render and Vercel,
a smoke checklist, and then four deep-dive sections written because each one cost me something:
why `NUM_PROXIES` does not work on Render, why the cache backend is not optional, the password
hashing rollback procedure, and the connection pooling explanation with its "do not restore
`CONN_MAX_AGE`" warning. Each records a measurement and a failure, not just an instruction.

**Why we chose this.** A runbook that says *what* to do gets ignored when someone thinks they know
better. One that says what happened last time does not.

**Alternatives.** A wiki; comments in config; nothing.

**Tradeoffs.** Long documentation goes stale. I mitigate that by putting measurements in it —
they date themselves visibly and can be re-run.

**Follow-ups.** "How do you keep it current?" · "Who is the audience?" · "What is missing?"

**What interviewers expect.** That the runbook contains **corrections of your own earlier
claims** — the rate-limiting section explicitly retracts a previous statement that cited the
throttle as a mitigating control while that control was measured non-functional. Documentation
that corrects itself in place is unusual and reads as trustworthy.

---

### Q167. How do you manage environments?

**Ideal answer.** Two: local development and production. No staging. Local uses the same
`settings.py` with different environment variables and a Docker Postgres, and the production
hardening — secure cookies, the proxy SSL header, the secret-key guard — is gated on `DEBUG`.
The absence of staging is why I ended up load-testing production, which is the direct cost of
that decision.

**Why we chose this.** Staging doubles hosting cost for a free-tier project.

**Alternatives.** A staging environment; ephemeral preview environments per PR; a shared dev
environment.

**Tradeoffs.** Vercel gives preview deployments per branch for the frontend for free, so the
frontend effectively has staging and the backend does not — which is an asymmetry I could close
by pointing previews at a second free Render instance.

**Follow-ups.** "What would staging cost?" · "How do you test risky changes?" · "Could you use
preview environments?"

**What interviewers expect.** Connecting the missing environment to the concrete consequence —
the production outage — rather than treating it as an abstract shortfall. And the cheap partial
fix: a second free Render instance as staging would cost nothing but a second Neon branch, which
Neon supports natively.

---

### Q168. If you had to hand this over tomorrow, what would worry you most?

**Ideal answer.** That someone would make a change that looks obviously correct and is not.
The four candidates: restoring `CONN_MAX_AGE` because pooling with `CONN_MAX_AGE=0` looks
backwards; removing the Argon2 hasher during a rollback; raising the rate limits because they
look restrictive; or "simplifying" the tuned hasher parameters back to Django's defaults. Each is
a one-line change, each looks like a cleanup, and each causes a serious failure. That is exactly
why each has a test that fails, a comment explaining the measurement, and a runbook section.

**Why we chose this.** Defence in depth against the most likely failure, which is not malice or
incompetence — it is a reasonable person making a reasonable-looking change without the context.

**Alternatives.** Rely on review; rely on documentation alone; rely on tests alone.

**Tradeoffs.** Comments and docs go stale; tests do not, but a test failure without explanation
invites someone to delete the test. So each has both — the test fails, and the failure message
explains why the constraint exists.

**Follow-ups.** "Why not just tests?" · "How do you stop someone deleting the test?" · "What is
undocumented?"

**What interviewers expect.** The insight that a failing test with no explanation is an
invitation to delete the test, which is why the messages carry the reasoning — for example
`assert 35 <= 20` accompanied by "one anonymous IP may send 35 req/min, above the 20 concurrent
requests measured to complete without failures." A test that teaches is worth several that merely
fail.

---

## Part 4 Recap — Five More Stories

| # | Story | The one-line hook |
|---|---|---|
| 16 | **The cold start that doubled** | 43.8 seconds was confounded by a deploy; the clean 21-minute quiesce test gave 92.9 — my error made things look better than they were. |
| 17 | **Cron that does not run** | GitHub Actions free-tier cron fired at 54–213 minute gaps against a 15-minute idle timeout, so the fix was a 45-minute in-run loop. |
| 18 | **Seven green tests that asserted nothing** | Mutating the keepalive logic changed no test result, because inline YAML cannot be executed by a test. |
| 19 | **The gradient that proved the attribution** | Pooling gained 44% on a one-query endpoint and 19% on a sixteen-query one — exactly what removing a fixed per-request cost predicts. |
| 20 | **The limit that authorised the outage** | anon(30) + auth(10) = 40/min, and 40 concurrent was precisely the load that returned 32×502. |

Part 1 was silent failures, Part 2 wrong instruments, Part 3 the cost of every control. Part 4 is
**the gap between a number and a conclusion**. A confounded cold start, a control taken while
saturated, a mechanism confirmed but irrelevant, tests that ran without checking — in every case
the data was real and the inference was not. The line to use: *I have more measurements than most
projects, and the thing I learned was how easy it is to measure correctly and conclude wrongly.*

---

*End of Part 4 (Questions 127–168). Part 5 — ML, Adaptive Learning & Recommendation Systems — follows.*
