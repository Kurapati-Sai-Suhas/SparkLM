# Execution models (M2 P2.37 / Phase 1 M4)

SparkLM has **two** execution models, and every language uses exactly one.
There is no third, and adding one would be an architecture change, not a fix.

| model | languages | what the learner writes | what the platform does |
|---|---|---|---|
| **Reflection** | python, java, javascript | a `Solution` class with exactly one public method | wraps it in a harness that reads stdin, types the arguments from the signature, invokes the method, renders the return value |
| **Self-contained** | **c, cpp** | a complete program: includes, `main()`, reads stdin, prints the answer | **nothing** — the source is sent to Judge0 verbatim |

The registry is the single source of truth: `common.languages.Language.self_contained`.

## The C/C++ contract

```
learner writes a complete program
        ↓
GradingService._build_executable returns it UNCHANGED
        ↓
Judge0 (cpp = language id 54, c = 50)
        ↓
stdin from the hidden test case, stdout compared to expected_output
```

Three consequences worth stating, because each was violated somewhere:

1. **No wrapper exists at any contract version.** `V2_WRAPPERS` has no `c` or
   `cpp` entry. That absence *is* the contract — a wrapper appearing there
   would silently switch the language to the reflection model.
2. **A `Solution` class cannot work.** It has no entry point, so the
   translation unit does not link. This is not a style preference.
3. **The platform never repairs a submission.** An empty or syntactically
   invalid program reaches Judge0 as written and fails there. Injecting a
   `main()` would grade code the learner never wrote.

## Evidence this is the intended model — not inferred from a flag

- `ai_services.generate_full_question`'s prompt: *"c and cpp are
  SELF-CONTAINED: they are compiled and run exactly as written, with no
  wrapper… A 'Solution' class for c/cpp has no entry point, cannot link, and
  will be rejected."*
- its fallback C++ template is `#include <bits/stdc++.h> … int main() { … }`
- `reseed_questions._validate_starter_code` **rejects** a c/cpp starter with
  no `main()`
- `contract_reconciliation.SELF_CONTAINED_LANGUAGES = ("c", "cpp", "c++")`
- `services._build_executable` returns `raw_code` unchanged for both

Five independent places agree. The model was never in doubt; the content was.

## What was actually broken

**An infrastructure defect that kept producing broken content.**
`ai_services.generate_starter_stubs` — the generator `backfill_boilerplate`
used to populate the bank — described the *opposite* contract:

> "Mirror the same method name and parameters, using a **Solution class for
> the object-oriented languages (java/cpp/javascript)**"

and its validator required the word `Solution` in every language except C:

```python
and (lang == "c" or "Solution" in code)
```

So for C++ it asked for the wrong shape **and discarded the right one** — a
correct self-contained stub has `main()`, not necessarily `Solution`. Its C
note was wrong too, asking for "a single free function", when C is equally
self-contained.

Two generators, two contradictory contracts, and the bank is the evidence:

| | starters | with `main()` | with `#include` |
|---|---|---|---|
| cpp | 638 | **0** | **0** |
| c | 20 | **0** | **0** |

Both prompts now derive the reflection/self-contained split from
`common.languages.REGISTRY`, and the stub validator checks the marker each
model actually requires: an entry point for self-contained languages, a
`Solution` class for reflection ones.

## The three problems, separated

M4 fixed the first. The other two are content, and are *not* the same job:

| # | problem | scale | fix |
|---|---|---|---|
| 1 | **infrastructure** — the generator produced unlinkable C/C++ | — | done: both prompts agree, validator matches the model |
| 2 | **broken starters** — exist but cannot link | 638 cpp, 20 c | regenerate through the corrected path |
| 3 | **missing starters** — no C/C++ content at all | 1,150 cpp, 1,768 c | author content; a much larger job |

Reporting these as one number ("C++ readiness 0/1788") hid that (2) is a
regeneration of existing questions while (3) is new content for questions
that may not warrant it.

## Why bulk repair has NOT been done

The stop conditions in the milestone brief are unmet:

- **Judge0 is unavailable** (429, and 403 on some requests — quota
  exhaustion, not throttling). Nothing regenerated could be *executed*, so a
  bulk rewrite would replace 638 unverifiable starters with 638 different
  unverifiable starters.
- Regeneration is an **LLM call per question**, and its output is exactly the
  artifact class this project refuses to trust unreviewed.

The correct sequence is: fix the generator (done) → pilot a small set →
validate against Judge0 → expand in waves. The pilot cannot start until
Judge0 capacity returns.

## Measured readiness

`python manage.py language_readiness_report --sample 3`

| language | READY | UNKNOWN | NOT_READY |
|---|---|---|---|
| python | 1,495 | 0 | 293 |
| java | 0 | 635 | 1,153 |
| cpp | 0 | 0 | 1,788 |
| c | 0 | 0 | 1,788 |
| javascript | 0 | 624 | 1,164 |

C++ `NOT_READY` by cause: `no_starter` **1,150**, `no_entry_point` **638**.
C `NOT_READY` by cause: `no_starter` **1,768**, `no_entry_point` **20**.

`UNKNOWN` is not a failure: the starter shape is right and the checker cannot
decide more without a compiler. It counts as servable, because refusing what
a checker cannot prove would hide failures behind the checker's limits.

**Phase 1 M17 correction.** For 47 structural v1 questions the JavaScript
`UNKNOWN` was decidable: the JS starter carries no types, but the question's
Python starter declares the structure, and the harness is chosen by the
contract, not the language — no v1/v3 harness builds a `TreeNode` anywhere.
`assess(question, lang)` now reports those `NOT_READY` (cause
`structural_type` / `structural_unsupported`) with the Python signature as
evidence. Only an undecided starter consults the declaration; v2 with a wired
adapter stays `UNKNOWN`; C/C++ are unaffected; `assess_source` without a
question is unchanged. Measured over the 1,788: JavaScript UNKNOWN→NOT_READY
47 (all structural, 10 of them SAFE_TO_MIGRATE); Python, Java, C and C++
unchanged.

## What is NOT verified

There is no `g++` in the development environment and Judge0 is refusing
requests, so **no C or C++ program has been compiled or executed** as part of
this milestone. The tests pin the contract *boundary* — that the source
reaches Judge0 unaltered under the right language id, and that both
generators now agree. Compilation, runtime-failure and timeout behaviour
belong to Judge0 and remain unverified.
