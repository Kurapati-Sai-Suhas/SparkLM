# P2.7h-24 — Operator specification verification recorded

Five specifications carry an operator verification. No canonical prose was
altered, no digest moved, and production is untouched.

---

## STEP 1 — the files reviewed are the files on disk

Every digest checked three ways before anything was written: against the
Phase 9.5 report, against the file's own recorded digest, and against the
freeze manifest.

```
q1940  3e837b04…f8e4ac   report=True file=True freeze=True
q1974  ad2a9eb0…502a0f   report=True file=True freeze=True
q2027  f6937c82…2b4536   report=True file=True freeze=True
q2057  d8febbe3…a55d26   report=True file=True freeze=True
q2290  37f42307…6e4a20   report=True file=True freeze=True
```

The script aborts before writing if any of the three disagree. None did.

---

## STEP 2 — verification metadata, digest proven stable

```
q1940  before 3e837b04ce81d24f2f3c…  after 3e837b04ce81d24f2f3c…  UNCHANGED
q1974  before ad2a9eb09a60a7a85d2e…  after ad2a9eb09a60a7a85d2e…  UNCHANGED
q2027  before f6937c82a0f38eec99bf…  after f6937c82a0f38eec99bf…  UNCHANGED
q2057  before d8febbe3bb5f02ae0cf7…  after d8febbe3bb5f02ae0cf7…  UNCHANGED
q2290  before 37f4230790e3c76074bc…  after 37f4230790e3c76074bc…  UNCHANGED
```

For each file the canonical text was compared byte-for-byte before and after,
and the validator re-run. All five: canonical prose identical, digest
identical, validator accepts.

This holds structurally rather than by luck: `canonical_text()` covers only
the eight prose fields, so verification metadata cannot reach the digest.

Recorded per specification and mirrored into the freeze manifest:

```
verified_by          Suhas
verified_at          2026-08-24 (UTC, per file)
verification_source  operator_manual_review
verified_digest      <the exact digest reviewed>
verification_notes   <the operator's note for that question>
```

The freeze manifest also carries an explicit scope statement:

> SPECIFICATIONS ONLY. Not an approval of generated artifacts, signatures,
> hidden tests, expected outputs, oracle results, publication or adaptive
> eligibility.

and an artifact-status block recording q2027 as rejected and q1974 as pending
style review.

**q1974's note records the operator's semantic ruling explicitly:** the
intended operation is `GCD(min(nums), max(nums))`, not `GCD(all nums)`. That
resolves the question Phase 9 could not, on the only basis that could resolve
it — an operator's assertion rather than the assistant's recall.

---

## STEP 3 — q2027 regenerated offline

The rejected artifact was **not** applied and the specification was **not**
touched. Regeneration ran into the offline directory `artifacts-v3/` from the
same approved specification (`f6937c82…`).

### The first regeneration reproduced the identical defect

`colors = "AABAA"`, claiming `true`, narrating "Alice can remove the middle A".
Executing the specification's own rule: the middle character is `B`, no `A` has
two `A` neighbours, so Alice has zero moves, moves first, and loses — `false`.

### It is probabilistic, not systematic

Three further single-attempt regenerations:

```
colors='AAABAAA'  claims true  spec True   CONSISTENT
colors='AABAA'    claims true  spec False  CONTRADICTED
colors='AABAAAB'  claims true  spec True   CONSISTENT
```

`"AABAA"` is a specific attractor: it looks symmetric, and the model reaches
for "the middle A" without checking that the middle character is `B`. Roughly
one attempt in three lands on it.

**This is the important operational finding.** Because the defect is
probabilistic and no gate detects it, regeneration is not a fix — it is a coin
toss, and a wrong example ships whenever the toss goes badly.

### The artifact now in `artifacts-v3/`

```
artifact digest    52c006c838734ae8498231d8f57379f3b774d46450aa8acc5b60fc55ffe2cb30
specification      f6937c82…2b4536  (the approved one)
structural         PASS
signature          PASS   removeColoredPiecesIfBothNeighborsAreTheSameColor(colors: str)
conformance        PASS
presentation       PASS
example verdict    colors="AABAAAB" → Alice-moves=1, Bob-moves=0 → true.  CONSISTENT
```

**But it is not clean, and I am not presenting it as such.** The example's
*verdict* is right; its *explanation* is not. It says "Alice can remove the
middle A" — for `"AABAAAB"` the middle is index 3, an `A` whose left neighbour
is `B`, so it is not removable. The removable `A` is at index 4. The model has
a persistent weakness in identifying *which* piece is removable, and reaches
for "the middle" every time.

**q2027 artifact status: regenerated offline, NOT accepted.** It needs either
operator review of the explanation or another regeneration. No production
approval is implied.

---

## STEP 4 — q1974 style revision: PROPOSED, NOT APPLIED

The file on disk is unchanged and its digest still reads `ad2a9eb0…502a0f`.

### The three fields that would change

**required_operation**
> OLD: …compute the greatest common divisor of those two numbers only. **The
> greatest common divisor of the whole list is NOT what is wanted and is a
> different quantity.**
> NEW: Find the minimum element and the maximum element of the list, then
> compute the greatest common divisor of those two numbers only.

**load_bearing**
> OLD: Exactly two values participate: the smallest and the largest. **Not all
> elements. Not any adjacent pair. The distinction changes the answer: for
> [3, 6, 4]** the smallest is 3 and the largest is 6, giving 3, whereas the
> divisor common to all three values is 1.
> NEW: Exactly two values participate: the smallest value present and the
> largest value present. Every other element of the list is ignored, including
> any element lying between them. The answer is the greatest common divisor of
> that single pair.

**input_semantics**
> OLD: The list is **not sorted** and may contain repeated values.
> NEW: The list may be in any order and may contain repeated values.

### SEMANTIC DIFFERENCE = NONE

```
operation before : gcd(min(nums), max(nums))
operation after  : gcd(min(nums), max(nums))
```

### Conformance is not weakened

```
required terms before : 19
required terms after  : 14
no longer required    : ['adjacent', 'all', 'different', 'sorted', 'three']
newly required        : []

genuine requirement terms KEPT    : divide, each, every, exactly, largest,
                                    maximum, minimum, one, smallest, two
genuine requirement terms DROPPED : none
```

Every term that carries a requirement survives. The five that go were never
requirements of this problem — `adjacent` and `pair` came from "Not any
adjacent pair", `all` and `different` from the meta-commentary, `sorted` from
"not sorted", `three` from the illustrative aside. Removing false requirements
is not the same as relaxing real ones, and this is why the Phase 8 artifact
read "no other pair, not any adjacent pair, and not all elements are
considered".

### The digest would move

```
current  ad2a9eb09a60a7a85d2e0daf04bc21a1bb6d661ab5a7acf87c068d6899502a0f
proposed eb8458fd460a64b62bc129e0662f3af509523dace43db127afdb0e90d01ffc84
```

**That is why this is a proposal and not an edit.** Applying it invalidates the
verification just recorded, because that verification is bound to the exact
text reviewed. It needs explicit re-approval of the new wording, and a
re-verification against the new digest.

---

## STEP 5 — status

```
q1940 = SPEC_VERIFIED
q1974 = SPEC_VERIFIED          (semantics confirmed: GCD(min, max))
q2027 = SPEC_VERIFIED
q2057 = SPEC_VERIFIED
q2290 = SPEC_VERIFIED

q2027 ARTIFACT = REJECTED / regenerated offline, still NOT accepted
                 (verdict consistent, explanation misidentifies the piece)
q1974 ARTIFACT = STYLE REVISION PROPOSED, awaiting approval of new wording
```

---

## STEP 6 — production untouched

```
Question rows          2926   unchanged      ReseedLedger rows       0   unchanged
RemediationAction      17     unchanged      RemediationBatch        1   unchanged
QuestionPreImage       7      unchanged      ReferenceSolution       3   unchanged
OracleExecution        70     unchanged      QuestionApproval        2   unchanged
CodeSubmission         44     unchanged

q1940/1974/2027/2057/2290 — all DRAFT/UNVERIFIED, placeholder intact, 0 hidden tests

bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  unchanged
```

---

## The gap that this phase makes concrete

Example correctness now has a name and a measured failure rate: **roughly one
regeneration in three produces an example that contradicts its own
specification**, and every gate passes it.

Detecting it requires executing the problem, and executing the problem requires
a reference implementation — which is precisely what the oracle phase exists to
produce and approve. So example verification is not a missing validator; **it
is the oracle, needed earlier in the pipeline than the current design places
it.** That is an architectural finding for whichever phase comes next, not
something to patch with another regex.

Until then, examples need human eyes, and the cost is now known rather than
assumed.
