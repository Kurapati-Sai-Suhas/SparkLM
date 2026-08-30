# P2.21 — Controlled bulk slice: selected, then blocked at §31C

**Status:** BLOCKED. A 24-candidate slice was selected and characterised. **All
24 were skipped for the same reason: no operator-supplied specification
exists.** No question was written, no batch was created, no artifact was
generated.

**Production mutations: 0.**

---

## The binding constraint

§31C: *"Only process candidates with valid operator-supplied specifications.
Do NOT invent authoritative specifications from titles. For missing
specifications: SKIP, record reason."*

**Five specifications exist in the entire repository:**

```
artifacts/pilot-slice-1/specifications/
  1940.spec.json  1974.spec.json  2027.spec.json  2057.spec.json  2290.spec.json
```

Four are the pilot four, already reseeded and published. The fifth, q2027, is
excluded by its own freeze record:

> *"EXCLUDED from the first production pilot (Phase 14 decision 3).
> Specification remains operator-approved; the ARTIFACT remains REJECTED …
> example contradicts the specification; regeneration required."*

So after §31A's six exclusions, **zero candidates have a specification.** A
20–25 question slice is not possible under §31C, and the prohibition is
exactly right: a specification invented from a title is how ~1,100 unusable
questions entered this bank in the first place.

---

## §31A — the slice, selected anyway

Selecting it is still worth doing: it is the operator's specification-writing
queue, and it is deterministic, so it can be picked up unchanged later.

**Population:** 1,136 reseed candidates (census clauses: DRAFT, UNVERIFIED, no
hidden cases, no approval, no oracle execution, placeholder marker present).
None of the six excluded ids is a candidate, so the exclusion removed nothing.

**Method:** stratified by (topic × difficulty band), ordered by id within each
stratum, taken round-robin. Deterministic — the same 24 come back every run.

| id | Topic | Band | Difficulty | Statement chars |
| --- | --- | --- | --- | --- |
| 1947 | Math | easy | 1000 | 401 |
| 1949 | Math | medium | 1300 | 436 |
| 1952 | String | easy | 1000 | 425 |
| 1955 | String | hard | 1600 | 446 |
| 1959 | Array | hard | 1600 | 442 |
| 1961 | Array | medium | 1300 | 433 |
| 1962 | Array | easy | 1000 | 438 |
| 1971 | Dynamic Programming | medium | 1300 | 426 |
| 2013 | String | medium | 1300 | 420 |
| 2023 | Hash Table | medium | 1300 | 410 |
| 2034 | Breadth-First Search | hard | 1600 | 427 |
| 2035 | Linked List | medium | 1300 | 440 |
| 2051 | Hash Table | easy | 1000 | 421 |
| 2066 | Graph | medium | 1300 | 423 |
| 2070 | Math | hard | 1600 | 410 |
| 2081 | Depth-First Search | hard | 1600 | 414 |
| 2162 | Depth-First Search | medium | 1300 | 438 |
| 2173 | Graph | hard | 1600 | 436 |
| 2217 | Dynamic Programming | hard | 1600 | 423 |
| 2224 | Hash Table | hard | 1600 | 416 |
| 2415 | Tree | medium | 1300 | 417 |
| 2449 | Tree | hard | 1600 | 430 |
| 3323 | Bit Manipulation | medium | 1300 | 284 |
| 3343 | Bit Manipulation | easy | 1000 | 248 |

**Spread:** 11 topics · easy 5 / medium 10 / hard 9 · **0 learner submissions
across the whole slice**, so nothing here is in front of a learner today.

### Two axes §31A asked for that cannot be spread across yet

§31A asks for spread across *input shapes* and *contracts/signature types*.
Neither is knowable before reseeding:

- **All 1,136 candidates carry the VARIADIC placeholder** (`*args, **kwargs`).
  There is no declared signature to vary.
- **The census returns `UNKNOWN` for all 1,136** on the v3 question, because
  its rule reads the declared signature — which does not exist yet.

Signature and contract are **outputs** of reseeding, not inputs to selecting a
slice. The census's population projection (≈275 of the 1,136 will need v3, 95%
CI 250–300) is explicitly a population estimate and classifies no individual
candidate.

---

## What was not done, and why

| Section | Status | Reason |
| --- | --- | --- |
| §31B batch + pre-images | **not created** | Pre-images anchor a rollback for mutations. There are no mutations, and an unused frozen batch is noise in the ledger. |
| §31D statements | not run | Requires a specification. |
| §31E signatures | not run | Requires a generated artifact. |
| §31F contracts | **UNKNOWN ×24** | The census rule needs a declared signature. Flagged as UNKNOWN rather than guessed, per §31F. |
| §31G hidden tests | not run | Requires artifact + signature + contract. |
| §31H quality gate | not run | Requires a suite. |
| §31I references | not run | Requires a specification digest to bind to. |
| §31J subset ready for Oracle | **none** | Nothing new reached authoring. |

---

## What would unblock this

The pilot's five specifications were produced by a workflow this project has
already run once, and the freeze file records it plainly:

> *"Specifications were drafted by the assistant standing in for the operator.
> Nothing in this pipeline verifies that a specification is correct — only
> that a generated statement does not drift from it. Each must be read by a
> human against what the question should ask before any artifact is applied."*

So the unblock is: **draft 24 specifications, then have each read and verified
by the operator** — `provenance: OPERATOR_SUPPLIED`, `verification_source:
operator_manual_review`, exactly as the pilot five carry.

That was not done in this phase because §31C forbids inventing specifications,
and because 24 specifications is a phase of work whose gate is human reading
time, not compute. It is offered, not assumed.

**Rough shape of the remaining work per question**, from the pilot: one
specification (operator-verified), one generated statement passing structural
/ conformance / presentation validation, one signature declaration, one
contract decision, ~12–16 hidden cases, one mutant catalogue, one reference,
one oracle run, one approval, one promotion, one publication.

---

## §31L — Git security

| Check | Result |
| --- | --- |
| `.env` or credentials tracked | none — only two `.env.example` files |
| Rotation plans / answer keys tracked | none — all confirmed ignored by `git check-ignore` |
| Hidden inputs / expected outputs in tracked reports | none — all seven quality reports carry rates, blockers and provenance only |
| Secret scan over staged content | clean |
| Working tree | clean at the time of the check |

---

## Tests

**3,400 passed.** The local Docker Postgres that was down during P2.19 is running again, so the
DB-backed suites execute. The verification P2.19 recorded as *absent* is now
present, and covers P2.19 and P2.20 as well.

---

## Final state

Nothing in the bank moved. The six protected questions are untouched:

| | |
| --- | --- |
| Candidates selected | 24 |
| Specifications available for them | **0** |
| Questions skipped | **24** — no operator-supplied specification |
| Artifacts / statements / signatures / contracts written | 0 |
| Hidden suites generated | 0 |
| Reference candidates | 0 |
| Oracle runs | 0 |
| Promotions / publications | 0 |
| Production mutations | **0** |
| Adaptive-eligible trusted count | 6, unchanged |
