"""
Operator-supplied specifications for a reseed slice (M2 P2.7h-21).

Phase 6 established that no authoritative specification exists for the 1,141
candidates — not locally, and not reachably. The decision taken was to supply
one per question, by hand, and to reseed only questions that have one.

This module is the frozen half of that: it loads a specification, canonicalises
it, digests it, and refuses one that is incomplete. Generation reads from here
and may not proceed without it.

── What a specification is, and what it is not ─────────────────────────────

It is ORIGINAL PROSE written by an operator, stating what a question asks. It
is not a copy of anyone's problem text; the algorithmic requirement is a fact
about an algorithm, the wording is the operator's own.

It is also **not verified by anything in this codebase.** The conformance check
proves a generated statement did not drift from its specification. Nothing here
proves the specification is right. That check is a human reading it against
what they know the question should ask, and it is the one step in this pipeline
that cannot be automated — which is precisely why the specification is written
down, digested and frozen rather than held in someone's head.
"""

import hashlib
import json
import re

#: Every prose field a specification must carry, in the order they are
#: canonicalised. Order is fixed so the digest is stable.
REQUIRED_PROSE = (
    "objective",
    "required_operation",
    "input_semantics",
    "output_semantics",
    "constraints",
    "edge_cases",
    "load_bearing",
    "method_behaviour",
)

REQUIRED_FIELDS = ("question_id", "canonical_identity", "provenance",
                   "author", "written_at") + REQUIRED_PROSE

PROVENANCE_OPERATOR = "OPERATOR_SUPPLIED"

#: Optional. A list of load-bearing terms the operator accepts a statement may
#: omit — for a paraphrase the conformance vocabulary cannot see, such as a
#: specification saying "least" where the statement says "smallest" and the
#: synonym table does not carry the pair.
#:
#: Deliberately per-specification and written down. An operator who needs it
#: has recorded which requirement they waived and why it is still the same
#: question; a global switch would waive all of them silently.
OPTIONAL_FIELDS = ("conformance_allow_omitted", "author_confidence")

#: Below this, a "specification" is a title with extra steps.
MIN_PROSE_CHARS = 240


class SpecificationRefused(Exception):
    """The specification is missing, incomplete, or not what it claims."""


def canonical_text(specification):
    """
    The exact text the digest covers and conformance runs against.

    Fixed field order, whitespace collapsed, no punctuation stripped. Two
    specifications that differ only in how their JSON was formatted produce the
    same digest; two that differ in a single load-bearing word do not.
    """
    parts = []
    for field in REQUIRED_PROSE:
        value = (specification.get(field) or "").strip()
        parts.append(f"{field}: {re.sub(r'\\s+', ' ', value)}")
    return "\n".join(parts)


def specification_digest(specification):
    return hashlib.sha256(canonical_text(specification).encode("utf-8")
                          ).hexdigest()


def validate_specification(specification, *, question_id=None):
    """Refusals for a specification that may not be generated from."""
    refusals = []

    missing = [field for field in REQUIRED_FIELDS
               if not str(specification.get(field) or "").strip()]
    if missing:
        refusals.append(f"the specification is missing required fields: "
                        f"{missing}")

    provenance = specification.get("provenance")
    if provenance and provenance != PROVENANCE_OPERATOR:
        refusals.append(
            f"provenance is {provenance!r}; this pipeline accepts only "
            f"{PROVENANCE_OPERATOR}. A specification reconstructed from a "
            f"title is the thing Phase 5 proved unsafe.")

    if question_id is not None and specification.get("question_id") != question_id:
        refusals.append(
            f"the specification is for question "
            f"{specification.get('question_id')}, not {question_id}")

    prose = canonical_text(specification)
    if len(prose) < MIN_PROSE_CHARS:
        refusals.append(
            f"the specification is {len(prose)} characters of prose; below "
            f"{MIN_PROSE_CHARS} it is a title with extra steps, not a "
            f"statement of what the question asks")

    identity = specification.get("canonical_identity") or {}
    if isinstance(identity, dict):
        for key in ("source", "problem_number"):
            if not str(identity.get(key) or "").strip():
                refusals.append(f"canonical_identity is missing {key!r}")
    else:
        refusals.append("canonical_identity must be an object")

    return refusals


def load_specification(path, *, question_id=None):
    """Read, validate and digest one specification file."""
    try:
        specification = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SpecificationRefused(
            f"no specification at {path}. This pipeline reseeds only "
            f"questions that have one; there is no title-only path.")
    except json.JSONDecodeError as exc:
        raise SpecificationRefused(f"{path.name} is not valid JSON: {exc.msg}")

    refusals = validate_specification(specification, question_id=question_id)
    if refusals:
        raise SpecificationRefused(
            f"{path.name} was refused:\n  - " + "\n  - ".join(refusals))

    specification["specification_digest"] = specification_digest(specification)
    return specification


def freeze_record(specification, path):
    """What is recorded about a specification when a slice is frozen."""
    return {
        "question_id": specification["question_id"],
        "specification_file": path.name,
        "specification_digest": specification["specification_digest"],
        "provenance": specification["provenance"],
        "author": specification["author"],
        "written_at": specification["written_at"],
        "canonical_identity": specification["canonical_identity"],
        "prose_characters": len(canonical_text(specification)),
        # Recorded so a reviewer sees the author's own hedging rather than
        # having to infer it. A specification nobody was sure of should not
        # quietly become a published question.
        "author_confidence": specification.get("author_confidence", "unstated"),
    }


#: The fields conformance compares. `method_behaviour` is absent: it states
#: the method's shape, which `reseed_authoring.validate_signature` enforces
#: structurally, and its natural phrasing is negative ("does not print"),
#: which a term-presence check reads backwards.
REQUIREMENT_PROSE = tuple(field for field in REQUIRED_PROSE
                          if field != "method_behaviour")


def requirement_text(specification):
    """Canonical text of the REQUIREMENT fields only."""
    parts = []
    for field in REQUIREMENT_PROSE:
        value = (specification.get(field) or "").strip()
        parts.append(f"{field}: {re.sub(r'\s+', ' ', value)}")
    return chr(10).join(parts)
