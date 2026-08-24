# P2.7h-19 — Content generation layer + 5-question dry run

Offline generation is implemented and has produced five artifacts for review.
**Nothing was applied.** No production question, ledger row, batch or action
was written; the bank fingerprint is unchanged.

---

## The finding that matters most

**Every deterministic validator passed all five artifacts. Human review found
two of them describe the wrong problem.**

| id | title | validators | semantic review |
|---|---|---|---|
| 1959 | Find the Longest Valid Obstacle Course at Each Position | PASS | ✅ correct |
| 1970 | Maximum Matrix Sum | PASS | ❌ **wrong problem** |
| 1974 | Find Greatest Common Divisor of Array | PASS | ❌ **wrong problem** |
| 1975 | Find Unique Binary String | PASS | ✅ correct |
| 1986 | Find the Middle Index in Array | PASS | ✅ correct |

**q1970** — the canonical problem lets you pick **two adjacent cells** and flip
**both** signs. The generated statement says "choose any cell and multiply its
value by -1", which makes the answer trivially the sum of absolute values. A
solvable, well-posed, *different, much easier* question.

**q1974** — the canonical problem asks for the GCD of the **smallest and
largest** elements. The generated statement asks for the GCD of **all**
elements. Different answers: `[3, 6, 4]` gives 3 canonically and 1 as
generated.

Worth noting: q1974 came out wrong under **both** providers independently
(Gemini in an earlier run, then Groq). The title reads naturally as "GCD of the
array", and the canonical problem is the surprising one. That is not a bad
model — it is the task being genuinely ambiguous from a title alone.

This is the limitation the module docstring states up front, now measured: the
checks prove an artifact is **well-formed and self-consistent**, never that it
**describes the problem the title names**. A provider that misremembers
produces a statement and signature that agree with each other and are both
wrong. Only human review closes that gap — which is why nothing here is
production-ready on its own.

**Recommendation before any pilot:** treat human semantic review as a required
gate, not an optional one. On this sample the pass rate is 3/5 (60%).

---

## The generator

`groups/reseed_generation.py` + `manage.py reseed_generate`. Reads, calls a
provider, validates, writes files. **Zero database write authority**, enforced
three independent ways:

1. On production the command calls `gate_no_write_privilege`, which refuses to
   start if the connected role holds INSERT, UPDATE or DELETE on any of the
   nine relevant tables. The guarantee offered is not "it chooses not to
   write" but "it cannot".
2. Reading and generating are **separate phases**. Every spec is built and
   frozen first, the connection is then closed, and no provider call happens
   while a connection exists.
3. A test asserts the module's source contains no `.save(`, `objects.create(`,
   `objects.update(`, `.delete(`, `record_action` or `select_for_update`.

**Input** (frozen at build time, `GenerationSpec.__slots__`): question id,
title, topic, base difficulty and band, class name, method name, current
starter, input digest, batch key. Nothing else.

**Output**: exactly `<id>.statement.html` and `<id>.starter.py`, plus
`<id>.manifest.json`. A test asserts the directory contains exactly those
three files.

**Both halves come from one provider call** — asserted by test. Two calls
would let the parameter list drift from the prose describing it, and the
parameters are what every hidden case is later bound against.

**Never generated**: hidden tests, expected outputs, oracle results, approval,
trust state, status, publication, adaptive eligibility.

---

## Validators

Statement: balanced HTML from a fixed tag vocabulary; ≥60 characters of
visible text; no placeholder marker; no `<script>`; no embedded worked
solution.

Starter (via `reseed_authoring.validate_signature`, shared with
`declare_signature`): parses and compiles; class and method names preserved;
exactly one public method; ≥1 named parameter; no `*args`, `**kwargs` or
keyword-only parameters; no duplicate names; every parameter annotated with a
type the adapter can classify; body remains a stub; no module-level statements;
no literal test-case data.

**Semantic consistency**: the method name must appear verbatim in the
statement, and **each parameter must appear in the prose with the signature
line removed first**. That second detail was a real defect found while
building: the prompt *requires* an API signature line, so checking parameters
against the whole statement was self-satisfying — every parameter "appeared"
merely because the signature had been pasted in. The check now strips the
signature before looking. The title's significant words must also overlap the
statement by ≥50%.

**On failure**: no production file is emitted, the manifest records status
`REJECTED` with every refusal, and the rejected text is kept beside it under a
`.rejected` suffix so a human can see what happened. Regeneration discards and
re-runs from the same spec — **an artifact is never silently repaired**,
because a patched artifact is one no prompt version explains.

---

## Manifest

Records: schema version, question id, title, topic, band, **input digest**,
batch key, whether the batch was frozen, generator version, prompt template
version, provider and provider version, generation timestamp, output
filenames, status, attempts, regeneration count, refusals, **attempt history**
(why each discarded attempt failed), artifact digest, and `applicable`.

`verify_manifest` re-derives the artifact digest from the files on disk, so a
manifest edited after generation — or files edited after the manifest — is
detected as stale. That is what makes the manifest evidence rather than a
claim.

**`applicable` is false for all five.** They were generated without a frozen
batch, because creating one is a production write and this phase permits none.
The reseed writer must refuse them until they are rebuilt against a real
frozen batch. The `input_digest` is the handshake: `reseed_statement
--expect-digest` must equal it, so a question that moves between generation and
application refuses the write.

---

## The five artifacts

Directory: `LearnLM/artifacts/reseed-dryrun-1/`. Provider
`openai/gpt-oss-120b`, generator `1.0.0`, prompt
`statement-and-signature/v1`, all accepted on the first attempt, q2201
explicitly excluded.

```
id    band    input digest (prefix)  artifact digest (prefix)  signature
1959  hard    8eac7fd9bb495b68…      dc67e5019f8e027e…  findTheLongestValidObstacleCourseAtEachPosition(obstacles: list[int]) -> list[int]
1970  medium  19ed59c142760efe…      8ef1062ef8245618…  maximumMatrixSum(matrix: list[list[int]]) -> int
1974  easy    793d6dbd90cee5db…      94ff835f6e8a6940…  findGreatestCommonDivisorOfArray(nums: list[int]) -> int
1975  medium  5feb5142e9ee0600…      6bebd5bf364868c0…  findUniqueBinaryString(nums: list[str]) -> str
1986  easy    acb312b3c78ea13a…      cc7e2370ebed829a…  findTheMiddleIndexInArray(nums: list[int]) -> int
```

Selection: one easy (1974), one medium (1970), one hard (1959), plus 1986
(easy) and 1975 (medium) chosen for input-shape diversity — flat int list, 2-D
matrix, and list of strings.

Minor cosmetic defect no validator catches: q1986's statement contains a
literal `\n` inside its example text rather than markup.

---

## Mutation

**14 killed / 15, 0 real survivors.** All 14 required boundaries covered.

```
C1  missing digest verification            killed    C8  database write by the generator   killed
C2  wrong question id                      killed    C9  invalid Python accepted           killed
C3  stale manifest accepted                killed    C10 keyword-only accepted             killed
C4  placeholder survives generation        killed    C11 multiple public methods accepted  killed
C5  mismatched statement/starter accepted  killed    C12 executable body accepted          killed
C6  hidden-test data accepted              killed    C13 *args accepted                    killed
C7  expected-output data accepted          killed    C14 **kwargs accepted                 killed
E1  EQUIVALENT: comment wording            survived
```

**C5 survived the first sweep** — a genuine test gap. Dropping
`semantic_refusals` from `validate_artifact` changed nothing, because every
semantic test called that function directly rather than through the
composition. The check could have been unwired without a single test noticing.
A test now exercises the composition itself.

---

## Regression

```
2885 passed, 2 warnings in 257s        (pytest --ignore=scripts)
groups/test_reseed_generation.py: 52 passed
makemigrations --check: No changes detected
```

## Production verification

```
questions 2926 · candidates 1141 · ledger rows 0 · batches 1 (p27-pilot-1)
actions 17 · submissions 44 (adaptive-eligible 0) · approvals 2
executions 70 · pre-images 7
q1959/1970/1974/1975/1986: all DRAFT/UNVERIFIED, placeholder intact, 0 cases
bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  unchanged
```

---

## Other findings

**a. A latent trap in `gate_write_privilege`.** It reads
`required = required or CAPTURE_PROBE`, so a caller passing `required=()` —
meaning "check only the forbidden list" — silently gets "must be able to INSERT
a pre-image", and a read-only caller is refused for lacking a write it must
never hold. This bit the generator on its first production run. I added a
separate `gate_no_write_privilege` rather than change shared semantics; the
one-line fix would be `CAPTURE_PROBE if required is None else required`, but
that touches every existing caller so I have not made it unilaterally. It is
the same shape of bug that function's own docstring describes.

**b. `ai_services.py` is broken for Groq.** It hard-codes
`llama-3.3-70b-versatile`, which returns 404 on the current key — the model is
no longer offered. Available models on that key are `openai/gpt-oss-120b`,
`openai/gpt-oss-20b`, `qwen/qwen3.6-27b`, `groq/compound` and a few
speech/guard models. Any app feature routing through Groq is currently failing.

**c. Gemini free tier is 20 requests/day.** Exhausted during this phase (four
questions at three attempts is 12). The generator now fails fast on quota
errors rather than retrying — retrying a 429 just spends the next question's
budget — and a second provider was added so a single upstream cannot stop the
run.

**d. A dropped connection killed an early run.** Holding a pooled Neon
connection open across seconds-long model calls let the server close it
underneath the loop. Fixed by the read-then-generate phase split, which also
makes the read-only claim structural.

---

## Not done, deliberately

No pilot. No production batch. No hand-off to `reseed_statement` or
`declare_signature`. No hidden tests, expected outputs, oracle, approval,
promotion or publication.
