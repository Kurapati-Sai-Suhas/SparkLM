"""
Conformance of a generated statement to an authoritative specification
(M2 P2.7h-20).

Phase 5 measured the problem this exists to solve: five artifacts passed every
structural validator and two of them described a materially different problem
from the one their title named. Structural validity says an artifact is
well-formed and self-consistent. It cannot say the artifact is *right*, because
nothing it was checked against knew what right was.

So this module checks a generated statement against a SUPPLIED specification,
and the check it performs is deliberately narrow and honest:

    CONTAINMENT, not equivalence.

It asks two questions a machine can actually answer:

    OMISSION  — does the statement drop a load-bearing term the spec uses?
    ADDITION  — does the statement introduce one the spec never uses?

That is not semantic equivalence and this module does not claim it is. Two
statements can share every term here and still mean different things. What the
check does buy is exactly the failure mode Phase 5 hit: an operation quietly
widened ("adjacent cells" -> "any cell") or an objective quietly swapped
("smallest and largest" -> "all elements") moves the load-bearing vocabulary,
every time, because those phrases ARE the requirement.

── The vocabulary ──────────────────────────────────────────────────────────

Not every word. A statement is prose and prose is paraphrased freely; demanding
whole-text similarity would fire on every rewrite and be switched off within a
week. These are the words that, changed, change the problem: quantifiers,
adjacency and ordering constraints, extremal selectors, and comparison
operators. Numbers are checked separately and exactly, because an invented
constraint or a fabricated example is always a defect.
"""

import re

#: Terms that carry an algorithmic requirement. Grouped only for readability;
#: the check treats them as one set.
_QUANTIFIERS = {
    "all", "any", "each", "every", "exactly", "at most", "at least", "some",
    "one", "two", "three", "both", "distinct", "unique", "same", "different",
}
_SELECTORS = {
    "smallest", "largest", "minimum", "maximum", "min", "max", "leftmost",
    "rightmost", "first", "last", "shortest", "longest", "closest", "kth",
}
_STRUCTURE = {
    "adjacent", "neighbouring", "neighboring", "contiguous", "consecutive",
    "subsequence", "subarray", "substring", "sorted", "non-decreasing",
    "nondecreasing", "non-increasing", "increasing", "decreasing", "strictly",
    "ascending", "descending", "row", "column", "diagonal", "cell", "pair",
}
_OPERATIONS = {
    "sum", "product", "count", "reverse", "rotate", "swap", "flip", "negate",
    "multiply", "divide", "add", "subtract", "concatenate", "merge", "sort",
    "remove", "insert", "replace", "gcd", "lcm", "modulo", "xor", "absolute",
}
_OUTCOME = {
    "return", "output", "print", "true", "false", "empty", "index", "indices",
    "length", "size", "-1",
}

VOCABULARY = (_QUANTIFIERS | _SELECTORS | _STRUCTURE | _OPERATIONS | _OUTCOME)

#: Multi-word terms must be found before the text is split into words.
_PHRASES = {term for term in VOCABULARY if " " in term or "-" in term}
_WORDS = VOCABULARY - _PHRASES


def _visible(text):
    body = re.sub(r"<[^>]+>", " ", text or "")
    body = body.replace("&nbsp;", " ").replace("&le;", "<=")
    body = body.replace("&ge;", ">=").replace("&amp;", "&")
    body = body.replace("≤", "<=").replace("≥", ">=")
    body = body.replace("‑", "-").replace("–", "-")
    return re.sub(r"\s+", " ", body).lower()


def terms_in(text):
    """The load-bearing vocabulary this text uses."""
    body = _visible(text)
    found = {phrase for phrase in _PHRASES if phrase in body}
    words = set(re.findall(r"[a-z]+", body))
    # Naive singularisation. A specification saying "two adjacent cells" and a
    # statement saying "each adjacent cell" state the same requirement, and a
    # vocabulary that cannot see that would report a difference that is purely
    # grammatical.
    singulars = {word[:-1] for word in words
                 if word.endswith("s") and word[:-1] in _WORDS}
    return found | (words & _WORDS) | singulars


def numbers_in(text):
    """Every numeric literal, exponents normalised away from prose."""
    body = _visible(text)
    # Only with an explicit caret. Without this guard the rule rewrote plain
    # "100" as 10^0 and silently lost the constraint it was meant to preserve.
    body = re.sub(r"10\s*\^\s*(\d+)", lambda m: "1e" + m.group(1), body)
    return set(re.findall(r"-?\d+(?:\.\d+)?", body))



#: Terms that are genuinely interchangeable in this domain. A statement using
#: one where the specification used the other has not changed a requirement.
#:
#: Kept short and specific on purpose. A generous synonym table would let a
#: real substitution through under cover of "near enough"; these are pairs
#: that a reviewer would call the same word. `neighbouring`/`adjacent` and
#: `concatenate`/`join` both cost a pilot artifact before being added.
SYNONYMS = (
    {"adjacent", "neighbouring", "neighboring"},
    {"concatenate", "join"},
    {"return", "output", "report"},
    {"count", "number"},
    {"minimum", "smallest"},
    {"maximum", "largest"},
)


def _expand(terms):
    """Every term, plus the synonyms that would satisfy it."""
    expanded = set(terms)
    for group in SYNONYMS:
        if expanded & group:
            expanded |= group
    return expanded


#: Groups within which a swap is a SUBSTITUTION rather than elaboration —
#: losing one member while gaining another changes the requirement.
_GROUPS = (_QUANTIFIERS, _SELECTORS, _STRUCTURE, _OPERATIONS)


def conformance_refusals(specification, statement_html, *,
                         allow_omitted=frozenset()):
    """
    Refusals for a statement that does not conform to `specification`.

    ── Why omission blocks and addition does not ───────────────────────────

    Measured against the five Phase 5 artifacts, whose correctness was
    established by human review:

        omission   flagged both wrong artifacts, neither right one   2/2, 0/3
        addition   flagged all five                                  3 false

    Addition fires on every artifact because a statement legitimately says
    more than a specification does — it carries constraints, examples and a
    restatement of the output. Blocking on it would mean blocking everything,
    and a check that always fires is a check that gets turned off.

    Omission is different: a specification's load-bearing terms ARE its
    requirements, and a statement that drops one has dropped a requirement.
    q1970 lost `adjacent`, `both` and `two`; q1974 lost `smallest` and
    `largest`. Those are precisely the two defects, named exactly.

    Additions are therefore reported as advisory, except where one lands in
    the same group as an omitted term — losing `adjacent` while gaining `any`
    is a substitution, not elaboration, and that does block.

    `allow_omitted` lets an operator accept a paraphrase the vocabulary cannot
    see (a spec saying `maximum` restated as `largest`) — a deliberate,
    recorded exception rather than a silent one.
    """
    if not (specification or "").strip():
        return ["no authoritative specification was supplied; conformance "
                "cannot be checked and the statement must not be trusted"]

    spec_terms = terms_in(specification)
    statement_terms = terms_in(statement_html)

    # Synonyms satisfy a requirement. `minimum` and `smallest` are the same
    # instruction; refusing one because the specification chose the other
    # teaches operators to fight the validator rather than use it.
    satisfied = _expand(statement_terms)
    omitted = sorted(spec_terms - satisfied - set(allow_omitted))
    added = statement_terms - _expand(spec_terms)

    refusals = []
    if omitted:
        refusals.append(
            f"the statement omits load-bearing terms the specification uses: "
            f"{omitted}. A dropped requirement is a different problem.")

    for group in _GROUPS:
        # `omitted` already has the operator's accepted paraphrases removed.
        # Recomputing the difference here would reinstate them and refuse a
        # substitution the operator explicitly allowed.
        lost = sorted(set(omitted) & group)
        gained = sorted(added & group)
        if lost and gained:
            refusals.append(
                f"the statement substitutes {gained} for {lost}; these carry "
                f"the same kind of requirement, so this is a changed "
                f"operation rather than a rewording.")
    return refusals


def advisory_additions(specification, statement_html):
    """Terms the statement adds. Reported for review, never blocking."""
    return sorted(terms_in(statement_html) - terms_in(specification))


def invented_numbers(specification, statement_html):
    """
    Numbers in the statement that the specification does not contain.

    Advisory for the same reason additions are: a specification states the
    requirement, and a good statement adds worked examples with numbers of
    their own. Worth a reviewer's eye, not a refusal.
    """
    return sorted(numbers_in(statement_html) - numbers_in(specification))


def conformance_report(specification, statement_html):
    """The same comparison, as evidence rather than a verdict."""
    spec_terms = terms_in(specification)
    statement_terms = terms_in(statement_html)
    return {
        "specification_terms": sorted(spec_terms),
        "statement_terms": sorted(statement_terms),
        "omitted": sorted(spec_terms - statement_terms),
        "added": sorted(statement_terms - spec_terms),
        "shared": sorted(spec_terms & statement_terms),
        "conforms": not (spec_terms - statement_terms)
                    and not (statement_terms - spec_terms),
    }
