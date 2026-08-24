# P2.7h-22 — Presentation gate

Phase 7 shipped five artifacts that were 5/5 conformant and **1/5 fit to show
a learner**. This phase adds an independent presentation gate, proves it
composed, and regenerates the slice. Conformance was not weakened; the
specifications were not edited.

No production write of any kind; bank fingerprint unchanged.

---

## Rates

| | Phase 7 | Phase 8 |
|---|---|---|
| deterministic | 5/5 | **5/5** |
| conformance | 5/5 | **5/5** |
| presentation (automated) | — (1/5 by human) | **5/5** |
| human presentation review | 1/5 | **4/5 clean, 1/5 stilted** |
| false positives on legitimate prose | — | **0** (24 fixtures) |
| mutation | — | **10 killed / 11, 0 real survivors** |

---

## 8B — the gate against the Phase 7 artifacts

Run with no expected results hard-coded. The outcome reproduced the Phase 7
human review exactly:

```
q1940  PASS
q1974  REFUSED (3)  internal labels ['input semantics','load bearing',
                    'method behaviour','output semantics','required operation']
                    + 8 schema labels as headings
                    + "…is NOT what is wanted…"
q2027  REFUSED (2)  internal label ['method behaviour'] + 4 schema labels
q2057  REFUSED (2)  internal labels ['load bearing','method behaviour',
                    'required operation'] + 6 schema labels
q2290  REFUSED (2)  internal label ['method behaviour'] + 4 schema labels
```

Two false positives had to be fixed first, both mine, and both the same
mistake — **measuring vocabulary instead of presence**:

- `nums = [12, 15, 9, 30]` presents an input as surely as the word "Input:",
  but the cue was a word list. It refused the pilot's best artifact.
- "Constraints" then "Edge cases" is the shape of every problem statement ever
  written. Both happen to be schema fields; neither is a leak.

A third surfaced later: `→ 1` presents an output with no cue word in sight,
and "Return the number of values in `nums`" says what the learner is given
without using "given" at all.

---

## 8A — what the gate checks

**Field labels, derived from the schema rather than blacklisted.** The labels
come from `reseed_specification.REQUIRED_PROSE`, so a field added to the schema
is detected without anyone remembering to list it. Labels split into two sets:
*internal-only* (`required operation`, `input semantics`, `output semantics`,
`load bearing`, `method behaviour`) refused on sight, and *legitimate*
(`objective`, `constraints`, `edge cases`) which only count toward
transcription. A `<p><strong>Objective</strong>` is caught as a heading in
disguise, and `Input semantics:` inline as the same leak.

**Transcription**, counted rather than judged one label at a time: three or
more schema labels as headings is a filled-in form; two in schema order counts
only if one is not a natural heading in its own right.

**Author-directed commentary**, anchored on constructions that only make sense
to whoever is *writing* the question — "is not what is wanted", "do not
confuse", "the specification requires", "the model should", "unlike the
previous problem". Ten legitimate instructional sentences are tested as
must-not-refuse: "Do not modify the input array", "The objective is to
minimise the number of moves", "Apply the operation until no further moves are
possible".

**Internal metadata**, refused only in an authoring construction, because
`operator`, `model` and `digest` are ordinary problem-domain words. "Apply the
XOR operator to each pair" and "Return a digest of the input string using the
given hash model" must pass, and are tested.

**A worked example** with an input, an output and concrete data, naming at
least one declared parameter, and not lifted verbatim from the specification.

**Structure**, deliberately loose: no template is enforced, and three quite
different shapes are tested as acceptable. It asks only that the statement not
open with a field label, that it say what comes back, and that it say what is
given — the latter satisfied either by words or by naming the parameters.

---

## 8E — composition proof

`validate_artifact` now ends with `+ presentation_refusals(...)`. Removing that
one call:

```
5 tests fail
  test_validate_artifact_calls_the_presentation_gate
  test_the_composition_catches_author_directed_commentary
  test_the_composition_catches_a_missing_example
  test_the_composition_catches_internal_metadata
  test_the_composition_catches_transcription
```

Every fixture in those tests is **deliberately conformant** — it keeps every
load-bearing term — so only the presentation check can refuse it. That is what
proves the gates independent, and
`test_the_two_gates_are_independent` states it directly: a transcription passes
conformance and fails presentation; a fluent paraphrase that drops a
requirement does the reverse.

---

## 8D — mutation

**10 killed / 11, 0 real survivors.**

```
P1  field-label detection removed                       killed
P2  meta-commentary detection removed                   killed
P3  example requirement removed                         killed
P4  specification transcription allowed                 killed
P5  internal metadata leakage allowed                   killed
P6  presentation bypassed in validate_artifact          killed
P7  example with no concrete value accepted             killed (see below)
P8  the "not what is wanted" pattern dropped            killed
P9  presentation validation made optional               killed
P10 gate weakened until a Phase 7 bad artifact passes    killed
E1  EQUIVALENT: comment wording                          survived
```

**P7 survived the first sweep** and the reason is worth keeping. My test
asserted only that *some* refusal fired, and the fixture ("some input gives
some result") also failed the names-a-parameter check — which masked the
literal check completely. Disabling it changed nothing any test could see. The
fixture now names both parameters and carries both cues, so the absence of data
is the only thing wrong with it.

---

## 8F — regeneration, and the finding that came with it

Same five specifications, **unmodified** — every recorded digest still matches
its file.

The first regeneration produced **1 of 5**, and this time **conformance** was
refusing, not presentation. Told to write naturally rather than transcribe, the
model paraphrased and dropped load-bearing terms: q1974 lost `different` and
`three`, q2290 lost `every`, `exactly` and `same`.

That exposed the architecture underneath the Phase 7 defect. Conformance
derives its required vocabulary from every load-bearing word in the
specification — including words that appear in **negations, meta-commentary and
illustrative asides**. q1974's specification requires 19 terms, among them:

- `different` — from "is NOT what is wanted and is a **different** quantity"
- `adjacent`, `pair` — from "Not any **adjacent pair**", a *negative* example

So a specification that carefully rules things out makes those very words
mandatory in the learner-facing statement. **That is what created the
transcription incentive in the first place**: the cheapest way to keep 19
scattered terms is to paste the specification.

The fix weakens nothing. The prompt (v4) now **names the required terms
explicitly**. It is the same rule, stated instead of guessed at — previously
the model had to infer which of the specification's words were being counted,
and when told to write naturally it inferred wrong.

Result: **5/5, four of them on the first attempt.**

```
q     spec digest    artifact digest  attempts  conformance  presentation
1940  3e837b04ce81   20ecccadf58e     1         PASS         PASS
1974  ad2a9eb09a60   eaa203f2891a     3         PASS         PASS
2027  f6937c82a0f3   914357f26632     1         PASS         PASS
2057  d8febbe3bb5f   a247d2ed8f17     1         PASS         PASS
2290  37f4230790e3   f899ae69039a     1         PASS         PASS
```

All: provider `openai/gpt-oss-120b`, prompt `specification-formatter/v4`,
status `READY_FOR_REVIEW`, `applicable=false` (no frozen batch exists).

---

## 8G — human review, presentation only

**This section judges presentation. It says nothing about whether the
specifications are correct — those remain unverified.**

| | reads naturally | exposes authoring info | real example | example clear | task clear | preserves spec | model/author instructions |
|---|---|---|---|---|---|---|---|
| q1940 | ✅ | ✅ none | ✅ | ✅ | ✅ | ✅ | ✅ none |
| q1974 | ⚠️ stilted | ✅ none | ✅ | ✅ | ✅ | ✅ | ✅ none |
| q2027 | ✅ | ✅ none | ⚠️ **wrong** | ✅ | ✅ | ✅ | ✅ none |
| q2057 | ✅ | ✅ none | ✅ | ✅ | ✅ | ✅ | ✅ none |
| q2290 | ✅ | ✅ none | ✅ | ✅ | ✅ | ✅ | ✅ none |

Every statement now opens in prose, carries a single `Example` heading, and
contains no field label and no meta-commentary. The Phase 7 defect is gone.

**q1974 is stilted**, and traceably so: "no other pair, not any adjacent pair,
and not all elements are considered". That sentence exists because
`adjacent` and `pair` are required terms — inherited from a *negative* example
in the specification. The statement is correct and a learner would understand
it; it reads defensively because the specification does.

**q2027's example is factually wrong.** `colors = "AABAA"` — the middle
character is `B`, so no `A` has two `A` neighbours and Alice has no legal move
at all. The statement claims "Alice can remove the middle A". The gate passed
it, **exactly as documented**: the module states that the example check is
internal consistency only and "proves nothing about algorithmic correctness".
A test,`test_the_example_check_does_not_claim_correctness`, asserts an example
with a wrong output still passes.

This is the documented limitation firing on the first real regeneration. It is
not a gate failure — it is the gate working to its stated boundary, and the
boundary being narrower than one might hope.

---

## Regression

```
2973 passed, 2 warnings in 265s     (pytest --ignore=scripts)
presentation suite: 48 passed · generation suite: 78 passed
makemigrations --check: No changes detected
```

**A long-standing flake was diagnosed and fixed rather than re-flagged.**
`test_kt_readiness.py::test_split_is_temporally_ordered` failed once in Phase 6
and again here. Its helper called `timezone.now()` on *every* invocation, so
the thirty rows and the two split boundaries were each anchored to a slightly
different clock reading. The test builds the rows first: a tick landing between
row 20 and the `days=20` boundary put that row microseconds *before* the
boundary and into `train` instead of `validation` — 21/4/5 rather than 20/5/5.
It passed only while all thirty-two calls fell inside one tick. The base is now
fixed once at import; twelve consecutive runs, zero failures.

Infrastructure note: Docker Desktop had stopped mid-phase, taking the local
Postgres with it (`PoolTimeout: couldn't get a connection`). Restarted; not a
code defect. It is the fourth occurrence in this milestone.

---

## Production

```
questions 2926 · candidates 1141 · ledger rows 0 · batches 1 · actions 17
bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  unchanged
```

No `reseed_statement`, no `declare_signature`, no hidden tests, no oracle, no
approval, no promotion, no publication.

---

## Remaining architectural blockers

**1. Nothing validates example correctness.** q2027 proves this is not
theoretical. An example is the first thing a learner reads and the fastest way
to teach them the wrong problem. It cannot be checked deterministically without
executing the problem — which is the oracle's job, and the oracle needs hidden
tests, which come later. Until then, **examples need human checking**, and that
is now a known cost per question rather than a surprise.

**2. Specification prose style is load-bearing in a way nobody would guess.**
Negations and asides in a specification become mandatory vocabulary in the
statement. q1974's defensive "Not any adjacent pair" produced a stilted
question. Specifications should state requirements **positively**; a future
phase should either exclude negated spans from term extraction or lint
specifications for them. This is the single biggest quality lever found so far.

**3. The specifications are still unverified.** Unchanged from Phase 7 and
still the gate that no amount of validator work can pass. They were drafted by
the assistant, not an operator.

**4. `ai_services.py` remains broken for Groq** — hard-codes the withdrawn
`llama-3.3-70b-versatile`. Unrelated to this phase, still unfixed.

---

## Verdict

**The presentation gate is robust.** It reproduces the Phase 7 human review
unaided, is composed into `validate_artifact` with an adversarial proof, kills
every real mutant, and produces no false positive across 24 legitimate-prose
fixtures. The regenerated slice passes both gates at 5/5 with four artifacts
accepted first time.

It is robust **within its stated boundary**, which q2027 has now demonstrated
is narrower than "the question is right".
