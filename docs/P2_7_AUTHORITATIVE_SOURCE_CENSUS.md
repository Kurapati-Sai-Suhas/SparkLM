# P2.7h-20 — Authoritative source census; pilot BLOCKED

Read-only throughout. No production question, batch, ledger row or action was
written; the bank fingerprint is unchanged.

**Result: no accessible authoritative specification exists for the 1,141
candidates. The pilot is blocked on a sourcing decision that is yours, not an
engineering one.**

---

## 1. Source census (6A)

Everything searched, and what it turned out to hold:

| source | holds | verdict |
|---|---|---|
| `groups_question` columns | title, content, difficulty, boilerplate, tests, status, trust | **no provenance field at all** — no source URL, external id or slug |
| `db_backups/backup_pre_M2_…sql` (2026-07-16) | 2,926 question rows, **1,257 already carrying the placeholder** | nothing to recover — placeholders predate M2 |
| `QuestionPreImage` | 7 rows, pilot batch only | irrelevant to the candidates |
| git history (153 commits) | no dataset with statements ever tracked | nothing to recover |
| `data/Leetcode_Questions.csv` | 2,913 rows: no, title, acceptance, premium, difficulty, link, solution-link | **identity only** |
| `data/Leetcode_Questions_updated (2024-11-02).csv` | 3,306 rows: the above **plus topic tags** | **identity only** |
| `seed_leetcode.py`, `update_db*.py`, `scripts/fix_seed_data.py`, `enrich_all.py` | hand-written statements for a handful of individual questions (Pascal's Triangle, Missing Ranges…) | ~5–10 questions, not a dataset |
| `remediation/q{1436,3309,963}_approved_statement.txt` | 3 statements | already applied; not candidates |

The decisive search — every file in the tree containing canonical statement
phrasing (`Example 1:`, `Constraints:`, `Given an integer array nums`) —
returns only the backup (placeholders), the Phase 5 artifacts I generated, and
those per-question scripts. **No local corpus of problem specifications
exists.**

### A/B/C/D/E counts

```
A =     0   authoritative ORIGINAL STATEMENT available locally
B =     0   authoritative STRUCTURED SPECIFICATION available locally
C =  1141   PARTIAL — canonical identity (number + slug) but no specification
D =     0   TITLE/METADATA ONLY
E =     0   CONFLICTING SOURCES
    total 1141
```

Every candidate matched a CSV row on exact normalised title. Zero unmatched,
zero duplicate titles in either CSV, **zero difficulty conflicts** across 1,141
rows — the DB band and the CSV difficulty agree in every single case, which is
itself the strongest evidence that this CSV is what seeded the bank.

**157 of the 1,141 are premium** (`isPremium = TRUE`) — not publicly readable
even by a human without a subscription.

---

## 2. Source quality (6B)

For the only source that covers the whole population:

| dimension | `Leetcode_Questions_updated (2024-11-02).csv` |
|---|---|
| provenance | third-party scrape of LeetCode's problem index, vintage 2024-11-02, author unknown, present in-tree since 2026-06-02 |
| original or reconstructed | **index metadata, original** — but of the catalogue, not of the problems |
| contains examples | ❌ |
| contains constraints | ❌ |
| contains expected behaviour | ❌ |
| contains input/output format | ❌ |
| contains starter signature | ❌ |
| independently verifiable | ✅ identity is — problem number and slug are stable public identifiers |
| licensing / usage | **unknown and unresolved.** The CSV is an unattributed third-party scrape with no stated licence. The problems it points at are LeetCode's copyrighted content. |

sha256 `2174a103…ba05167` (new), `097e79f0…e9e3679c` (old).

**What the CSV is good for:** turning a title into a canonical identifier
deterministically. That is a real contribution — it removes the guesswork from
any future retrieval. **What it is not:** a specification.

---

## 3. The mandatory test (6D) — the source FAILS it

The brief's own criterion: *if the source cannot distinguish these, it is not
authoritative enough.*

```
q1970  Maximum Matrix Sum                     -> LeetCode 1975
q1974  Find Greatest Common Divisor of Array  -> LeetCode 1979
```

The CSV row for each contains exactly: number, title, topic tags, acceptance
rate, premium flag, difficulty, and two URLs. **There is no field carrying
behavioural text.** It cannot distinguish "two adjacent cells" from "any
cell", nor "GCD of smallest and largest" from "GCD of all elements", because
it says nothing about either. The local source is definitively **not
authoritative enough**.

### The linked source is not reachable either

`https://leetcode.com/problems/find-greatest-common-divisor-of-array/description`
returns **HTTP 403 Forbidden** to automated fetching. I did not attempt to work
around that: a 403 is an access control, and bulk-retrieving 1,141 statements
would also mean copying copyrighted problem text into the question bank and
serving it to learners.

So the position is:

- local sources — **cannot** distinguish (no text)
- the canonical source — **can** distinguish, but is not programmatically
  accessible, is copyrighted, and 157 of the candidates sit behind a paywall

---

## 4. Candidate-to-source mapping (6C)

Built and written to a scratch file — **not into production.** 1,141 rows,
each recording: question id, title, source identifier, provenance, match
confidence, matching fields, conflicts, and the source file digest.

```json
{"question_id": 1974, "title": "Find Greatest Common Divisor of Array",
 "class": "C", "source": "leetcode_csv_2024_11_02",
 "slug": "find-greatest-common-divisor-of-array", "problem_number": "1979",
 "csv_difficulty": "Easy", "db_band": "Easy", "is_premium": false,
 "conflicts": [], "match_fields": ["title"], "confidence": "exact-title"}
```

Matching is deterministic: normalise case, punctuation and whitespace, then
exact-match the title. No fuzzy matching, no nearest-neighbour — a title that
does not match exactly is class D, and none were.

---

## 5. Generator redesign (6E)

The input becomes:

```
candidate  +  AUTHORITATIVE SPECIFICATION  +  frozen input digest  +  language metadata
```

with the model demoted from **author** to **formatter**. It may reword,
structure and mark up the specification. It may not add an algorithmic
requirement the specification does not contain.

`GenerationSpec` gains `specification` and `specification_digest`; the manifest
records the digest so an artifact is traceable to the exact text it was built
from. **Without a specification the generator refuses** — the Phase 5 mode
(title-only) is removed as a production path, not left as a default.

---

## 6. Semantic validator (6E) — and proof it is meaningful

`groups/reseed_conformance.py`. The check is **containment, not equivalence**,
and the module says so in its own docstring. It asks two questions a machine
can answer: does the statement **omit** a load-bearing term the specification
uses, or **substitute** one for another from the same group?

The vocabulary is deliberately narrow — quantifiers, extremal selectors,
adjacency/ordering structure, operations, outcomes. Not whole-text similarity,
which would fire on every legitimate rewording and be switched off within a
week.

### Proof, on the real Phase 5 artifacts

Run against the five artifacts whose correctness was established by human
review, with operator-written specifications:

```
q1959  correct  flagged=False   true negative
q1970  WRONG    flagged=True    true positive   omits ['adjacent','both','two']
q1974  WRONG    flagged=True    true positive   omits ['largest','return','smallest']
q1975  correct  flagged=False   true negative
q1986  correct  flagged=False   true negative
```

**2/2 true positives, 3/3 true negatives, zero false results** — and the
diagnosis names the exact words that changed. That is the 6D demonstration:
with a specification present, the check distinguishes adjacent-cell from
any-cell and GCD(min,max) from GCD(all).

### What was measured and discarded

The first version blocked on **additions** as well. Against the same five
artifacts it flagged **all five — three of them wrongly**, because a statement
legitimately says more than a specification does (constraints, examples, a
restated output). Addition is now advisory. Invented numbers are advisory for
the same reason: worked examples bring their own.

### What this does NOT prove

Containment is not equivalence. Two texts can share every term in this
vocabulary and still mean different things — reordered quantifiers, a negated
condition, a different tie-break. **The check cannot certify correctness; it
can only catch a requirement going missing.** Human review stays mandatory.
Five artifacts is also a small sample: it establishes the mechanism works, not
a false-positive rate.

---

## 7. Provider configuration (6F)

No Gemini quota was spent this phase.

Model ids are now explicit, in one place, environment-overridable:

```python
PROVIDER_MODELS = {
    "gemini": os.environ.get("RESEED_GEMINI_MODEL", "gemini-2.5-flash"),
    "groq":   os.environ.get("RESEED_GROQ_MODEL", "openai/gpt-oss-120b"),
}
```

`probe_provider(name)` reports the configured model and whether the key can
actually reach it, **by listing models rather than generating** — listing is
free, generating is not, and a run that will fail on a withdrawn model should
fail before it spends a day's quota discovering that.

```
groq     configured='openai/gpt-oss-120b'   reachable=True    offers 13 models
gemini   configured='gemini-2.5-flash'      reachable=True    offers 37 models
```

**Finding, unchanged from Phase 5 and still unfixed in the app:**
`ai_services.py` hard-codes `llama-3.3-70b-versatile` at two call sites. That
model is withdrawn and returns 404 on this key, so every Groq path in the
application is currently failing. It is outside this phase's scope to change;
flagged for a decision.

---

## 8. Mutation

**8 killed / 9, 0 real survivors**, on the new validator.

```
D1 a dropped requirement no longer refuses        killed
D2 a substituted operation no longer refuses      killed
D3 a missing specification is treated as a pass   killed
D4 an accepted paraphrase accepts everything      killed
D5 extremal selectors leave the vocabulary        killed
D6 adjacency/ordering leaves the vocabulary       killed
D7 the exponent rewrite eats plain integers again killed
D8 plural and singular stop matching              killed
E1 EQUIVALENT: comment wording                    survived
```

Three real bugs were found by the tests before the sweep ran: an accepted
paraphrase did not suppress the substitution refusal; plural and singular forms
did not match; and `numbers_in` silently rewrote plain `100` as `10^0`,
destroying the constraint it was meant to preserve. D7 and D8 are the
regression guards for two of them.

### Mutation plan for the phase that follows

When the generator is rewired to require a specification, sweep:
missing specification accepted · specification digest not recorded ·
specification digest not verified before hand-off · conformance not called by
`validate_artifact` (the exact gap that survived in Phase 5) · `allow_omitted`
defaulting to "everything" · statement accepted when the specification is
empty · manifest marked applicable without a conformance pass · a stale
specification file accepted.

---

## 8b. Integration status — stated plainly

The conformance validator is **built, tested and mutation-swept, but not yet
wired into the generator.** `validate_artifact` does not call it, and
`GenerationSpec` does not yet carry a specification.

That is deliberate: wiring it in means making a specification mandatory, and a
mandatory input that does not exist for any of the 1,141 candidates would
break the generator rather than improve it. The rewiring belongs in the phase
that follows the sourcing decision — and Phase 5's C5 survivor is the standing
reminder that the composition itself needs its own test when it happens.

Until then the Phase 5 generator remains title-only, which this census
establishes is **not an acceptable production path**.

## 8c. Regression

```
2898 passed, 2 warnings in 273s      (pytest --ignore=scripts)
groups/test_reseed_conformance.py: 13 passed
makemigrations --check: No changes detected
```

The first run was **not** green, and both failures are worth recording:

- `common/test_feature_flags.py` correctly refused `RESEED_GEMINI_MODEL` and
  `RESEED_GROQ_MODEL` as undocumented. Both are now in
  `docs/FEATURE_FLAGS.md`. That guard has now caught three separate additions
  in this milestone and been right every time.
- `groups/test_kt_readiness.py::test_split_is_temporally_ordered` failed once,
  then passed in isolation and passed on a full re-run. It references nothing
  from this phase. **It is intermittent** — flagged rather than dismissed,
  because a test that fails one run in two is a test nobody will trust the
  next time it goes red.

## 9. Pilot readiness

**NOT READY. Blocked on sourcing.**

| gate | state |
|---|---|
| authoring path implemented | ✅ Phase 4 |
| structural validation | ✅ Phase 5 |
| semantic conformance check | ✅ this phase — but requires a specification |
| **authoritative specification for 1,141 candidates** | ❌ **does not exist** |
| licensing position | ❌ unresolved |
| 157 premium candidates | ❌ no access path |

Everything mechanical is built. The missing input is the one thing engineering
cannot manufacture: a trustworthy statement of what each question asks.

---

## 10. The decision

Four options, with what each costs:

**a. Author specifications in-house.** A human writes one or two sentences per
question stating the requirement; the model formats it into a statement and the
conformance check holds it to that. No licensing exposure — the algorithmic
task is not copyrightable, the prose becomes yours. Cost: ~1,141 short
specifications. This is the only option that clears every gate.

**b. Licence a problem corpus.** Buy or use an openly-licensed set with real
statements. Cost: money or a changed catalogue; the titles stop matching what
is in the bank.

**c. Narrow the bank.** Reseed only the questions someone will write
specifications for, and retire the rest. The bank shrinks to what can be
trusted — which is the whole point of this milestone.

**d. Operator-supplied per slice.** The human pastes a specification for each
question in a slice; the pipeline formats, checks conformance, and applies.
This is exactly what the Phase 6E design supports, and it is (a) done
incrementally rather than up front.

I recommend **(d) as the mechanism and (c) as the policy**: run slices only
where a specification exists, and let the trusted population grow rather than
targeting all 1,141. It matches the serving boundary already agreed — PUBLISHED
questions are the ones learners see, and there are two.

**What I do not recommend:** scraping LeetCode. It is a 403, it is copyrighted,
157 candidates are paywalled, and a question bank built on it cannot be
published or defended.
