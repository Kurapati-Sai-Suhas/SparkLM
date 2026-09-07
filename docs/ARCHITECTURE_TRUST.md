# Content Trust and language (M2 P2.36 / Phase 1 M3)

## The problem this addresses

`Question.trust_state == ORACLE_VERIFIED` used to be read as "this question's
answer key is trustworthy" — full stop, with no mention of language. That
reading was wrong in a way that could corrupt learner state.

The Oracle runs **one** canonical reference, in **one** language, against the
hidden suite. Every reference in production is Python. But
`adaptive_eligible` carried no language term, so a submission in any language
against a verified question was treated as trustworthy evidence.

The P2.34 multilingual audit measured what that meant in practice:

| language | executable questions (of 1,788 servable) |
|---|---|
| python | 1,495 |
| java | 0 READY, 635 UNKNOWN |
| javascript | 0 READY, 624 UNKNOWN |
| **cpp** | **0** |
| **c** | **0** |

All 638 shipped C++ starters lack `main()`, and C++ is `self_contained` — the
source reaches Judge0 unwrapped. So a C++ submission against a Python-verified
question produced a **link failure that moved the learner's Elo as a genuine
attempt**.

## Current model — question-level trust plus a verified-language marker

Unchanged:

- one `Question.status` and one `Question.trust_state`
- the one-canonical-reference contract (`oracle.canonical_reference` still
  requires exactly one active, approved reference per question)
- `question_promote` remains the sole writer of `trust_state`
- `is_adaptive_eligible` still means `PUBLISHED and ORACLE_VERIFIED`, and is
  still what exposure ordering and the coverage reports use — they have no
  submission language and need the question-level answer

Added:

- `Question.verified_language` — the language of the reference that produced
  the current verification. NULL means *no current verification*, never
  "verified in an unknown language".
- `Question.adaptive_eligible_for(language)` — **the** predicate that gates
  rating.

### The invariant

```
adaptive_eligible_for(question, language) ⟺
      question.status       == PUBLISHED
  ∧   question.trust_state  == ORACLE_VERIFIED
  ∧   canonical(question.verified_language) == canonical(language)
```

Aliases are canonicalised through `common.languages`, so `js` and
`javascript` cannot disagree.

### Why language readiness is NOT a fourth term

A draft of this milestone included `language_readiness(question, language)`.
It was removed, for two reasons:

1. **It judges the wrong artifact.** Readiness describes the *starter the
   platform hands out*; what was graded is *the code the learner wrote*. A
   learner who replaces a starter with a broken annotation by working Python,
   against a Python-verified question, has produced a genuinely trustworthy
   outcome. Refusing to let it move their rating would punish them for a
   content defect they routed around. This is the same error M1 made at the
   submission boundary and corrected there.
2. **It is redundant.** A question verified in language L had a reference
   *execute* in L through Judge0. L demonstrably runs for it.

The C++ vulnerability is closed by the language match alone: a C++ submission
against a Python-verified question fails `python == cpp` whatever readiness
says. Readiness belongs where the language is *chosen* — reported by
`NextProblemView` (P2.35), never used to grade.

### What enforces it

| Guarantee | Where |
|---|---|
| Rating only from a matching language | `services.ProgressionService.apply_submission` — the single enforcement point, frozen onto `CodeSubmission.adaptive_eligible` at write time |
| `ORACLE_VERIFIED` without a language is unrepresentable | DB `CheckConstraint question_oracle_verified_requires_language` |
| The marker matches the reference that was run | `question_promote` writes it from `reference.language`, in the same statement as `trust_state` |
| The marker cannot outlive its trust | `question_demote` clears it in the same write |

Enforcing at the database as well as the command is deliberate: `trust_state`
decides whether a wrong answer key can corrupt a learner model, and "the
writer sets it" is a weaker guarantee than "the row cannot exist otherwise".

### What did NOT change

Practice mode is untouched. A learner may still submit in any language the
platform registers, still gets a real verdict, and still sees the result —
the submission is simply not adaptive-eligible, exactly as an unverified
question's submission already was. `trust_summary()` keeps every existing key
and adds `verified_language`, so the frontend's Verified / Practice-mode split
keeps working unchanged.

## Future model — per-language references and per-language trust

The end state is per-language trust:

- `ReferenceSolution` **already** permits one active row per language; only
  `canonical_reference()`'s one-oracle product contract prevents it
- each language gets its own reference, its own oracle evidence, its own
  quality gate, and its own trust state
- `verified_language` becomes redundant and is **dropped**, not migrated

### Why not now

It touches `canonical_reference`, `question_promote`, `question_approve`, the
quality gate, `CodeSubmission`, and every readiness report, and it needs a
schema change to a table whose immutability guarantees are load-bearing. Doing
that immediately before the trusted-content expansion (Phase 3) would
destabilise promotion at the moment it is most needed.

The interim model satisfies the safety invariant completely — no
cross-language trust leaks — at a fraction of the blast radius. It is a
narrowing, not a weakening: it can only *refuse* eligibility that the previous
model granted.

### Migration path

1. Add per-language trust rows; keep `verified_language` in sync as a
   generated view of the single-reference case.
2. Relax `canonical_reference()` to return the reference *for a language*, and
   thread language through `oracle_execute`, `quality_gate`, and approval.
3. Move `adaptive_eligible_for` to read per-language trust instead of the
   marker.
4. Drop `verified_language` and its CHECK constraint.

Steps 1–3 are additive and independently shippable. Only step 4 is
destructive, and by then the marker has no readers.

## Backfill

Migration `0053_question_verified_language` derives the marker from each
verified question's canonical reference — active, APPROVED, with a
`source_hash`. It does **not** default to `"python"`: every production
reference happens to be Python, so a blanket default would have been right for
the wrong reason and silently wrong the first time a Java reference existed.

If a verified question's language cannot be derived, the migration **raises**
and names the questions. The two safe alternatives — leaving it verified with
an unknown language, or demoting it — respectively violate the new constraint
and usurp `question_demote`'s authority. Verified against production before
writing: all six `ORACLE_VERIFIED` questions resolve to exactly one canonical
reference, all Python.
