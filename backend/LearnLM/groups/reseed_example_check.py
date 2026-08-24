"""
Early example check for generated artifacts (M2 P2.7h-25).

Phase 9.6 measured the gap this closes: roughly one artifact in three carried
a worked example that contradicted its own specification, and structural,
conformance and presentation validation all passed it. q2027 claimed
`colors = "AABAA"` yields `true`; under the specification's own rule Alice has
no legal move and the answer is `false`.

No amount of text analysis settles that. Deciding whether an example's output
is right means COMPUTING it, which means running an implementation.

── EARLY EXAMPLE CHECK ≠ FULL ORACLE VERIFICATION ──────────────────────────

This module runs code and compares one answer. That is the only thing it does,
and it is emphatically NOT oracle evidence. The differences are structural,
not a matter of degree:

    full oracle                          early example check
    ─────────────────────────────────    ──────────────────────────────────
    APPROVED, ACTIVE ReferenceSolution   a REFERENCE_CANDIDATE, unreviewed
    with intact approval provenance      and possibly LLM-written
    every hidden case                    one example
    REQUIRED_RUNS identical runs,        one run; determinism NOT established
    non-determinism is a hard failure
    OracleExecution provenance written   nothing written, anywhere
    can support ORACLE_VERIFIED          can support NOTHING in the lifecycle

So a pass here means: "an implementation someone wrote produced the number the
artifact claims". It does not mean the implementation is right, that the
question is right, or that any trust transition is earned.

The refusal to be mistaken for oracle evidence is enforced, not just
documented: this module imports no provenance writer, records nothing, and
every result it produces is stamped with its evidence class.
"""

import hashlib
import json
import re

EVIDENCE_CLASS = "EARLY_EXAMPLE_CHECK"

#: Deliberately NOT any value the lifecycle understands. A developer who tries
#: to feed one of these into promotion finds a string nothing matches.
EXAMPLE_PASS = "EXAMPLE_PASS"
EXAMPLE_WRONG_OUTPUT = "EXAMPLE_WRONG_OUTPUT"
EXAMPLE_RUNTIME_ERROR = "EXAMPLE_RUNTIME_ERROR"
EXAMPLE_INVALID_INPUT = "EXAMPLE_INVALID_INPUT"
EXAMPLE_UNRESOLVED = "EXAMPLE_UNRESOLVED"

#: An example's VERDICT and its EXPLANATION are separate claims. q2027's
#: regenerated artifact had a correct verdict and an explanation that pointed
#: at the wrong character; treating the first as evidence for the second is
#: how that would have shipped.
EXPLANATION_VERIFIED = "EXPLANATION_VERIFIED"
EXPLANATION_UNVERIFIED = "EXPLANATION_UNVERIFIED"

#: A reference is a candidate until a human has read it. Nothing downgrades
#: this automatically and nothing upgrades it without a recorded reviewer.
REFERENCE_CANDIDATE = "REFERENCE_CANDIDATE"
REFERENCE_REVIEWED = "REFERENCE_REVIEWED"


class ReferenceCandidate:
    """
    An implementation offered as a way to compute an answer — not as truth.

    Provenance is mandatory at construction. A reference whose origin nobody
    recorded is one nobody can later assess, and an LLM-written reference that
    looks like a human-written one is the failure this class exists to make
    impossible to reach by accident.
    """

    __slots__ = ("source", "language", "origin", "provider", "prompt_version",
                 "author", "reviewed_by", "notes")

    def __init__(self, source, language="python", *, origin, provider=None,
                 prompt_version=None, author=None, reviewed_by=None,
                 notes=""):
        if not (source or "").strip():
            raise ValueError("a reference candidate needs source")
        if origin not in ("human", "llm", "trusted_source"):
            raise ValueError(f"unknown reference origin {origin!r}")
        if origin == "llm" and not (provider and prompt_version):
            raise ValueError(
                "an LLM-written reference must record its provider and prompt "
                "version; an unattributed one cannot be assessed later")
        self.source = source
        self.language = language
        self.origin = origin
        self.provider = provider
        self.prompt_version = prompt_version
        self.author = author
        self.reviewed_by = reviewed_by
        self.notes = notes

    @property
    def digest(self):
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    @property
    def status(self):
        return REFERENCE_REVIEWED if self.reviewed_by else REFERENCE_CANDIDATE

    def as_record(self):
        return {"reference_digest": self.digest, "language": self.language,
                "origin": self.origin, "provider": self.provider,
                "prompt_version": self.prompt_version, "author": self.author,
                "reviewed_by": self.reviewed_by, "status": self.status,
                "notes": self.notes}


def encode_stdin(arguments):
    """
    The stdin the generic v1 python harness expects: one JSON value per line,
    one per parameter.

    Not invented here — it is what `GENERIC_PYTHON_WRAPPER` parses, and using
    the same encoding is the point. An early check fed differently from the
    grader would answer a question nobody is going to ask.
    """
    return "\n".join(json.dumps(value) for value in arguments)


def extract_example(statement_html, parameters):
    """
    (arguments, claimed_output) read out of a generated statement, or None.

    Returns None rather than guessing. An example the checker cannot read is
    `EXAMPLE_UNRESOLVED`, never a pass — a parser that shrugs and approves is
    worse than no parser.
    """
    body = re.sub(r"<[^>]+>", " ", statement_html or "")
    body = (body.replace("&nbsp;", " ").replace("&quot;", '"')
                .replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("“", '"').replace("”", '"'))
    body = re.sub(r"\s+", " ", body)

    section = body
    heading = re.search(r"\bexamples?\b", body, re.I)
    if heading:
        section = body[heading.end():]

    arguments = []
    for name in parameters:
        match = re.search(
            rf"\b{re.escape(name)}\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|"
            rf"-?\d+(?:\.\d+)?|true|false)", section, re.I)
        if not match:
            return None
        parsed = _literal(match.group(1))
        if parsed is _UNPARSED:
            return None
        arguments.append(parsed)

    claimed = None
    for pattern in (r"(?:output|result|answer|returns?)\s*(?:is|=|:)?\s*"
                    r"(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|-?\d+|true|false)\b",
                    r"(?:→|->|=>)\s*(\[[^\]]*\]|\"[^\"]*\"|-?\d+|true|false)\b"):
        found = re.findall(pattern, section, re.I)
        if found:
            parsed = _literal(found[-1])
            if parsed is not _UNPARSED:
                claimed = parsed
                break
    if claimed is None:
        return None
    return arguments, claimed


_UNPARSED = object()


def _literal(text):
    raw = (text or "").strip()
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return json.loads(raw.replace("'", '"'))
    except Exception:                                         # noqa: BLE001
        return _UNPARSED


def _same(expected, actual_text):
    """
    Does the reference's stdout mean the artifact's claimed output?

    Compared as values where both parse, and as normalised text otherwise, so
    `true` matches `True` and `3` matches `3\\n` — while `3` never matches `1`.
    """
    text = (actual_text or "").strip()
    parsed = _literal(text)
    if parsed is not _UNPARSED:
        if isinstance(expected, bool) or isinstance(parsed, bool):
            return bool(expected) == bool(parsed) and \
                type(expected) is type(parsed)
        return expected == parsed
    return str(expected).strip().lower() == text.lower()


def contract_binding_problem(question, starter_source, arguments):
    """
    Can the question's DECLARED CONTRACT even invoke this signature?

    Found by running the check, not by reasoning about it: q1974 declares
    `findGreatestCommonDivisorOfArray(nums: list[int])` — one list parameter —
    and the question is contract v1. The v1 generic harness parses stdin, sees
    a list, and splats it, so the method is called with three arguments
    instead of one and dies with "takes 2 positional arguments but 4 were
    given".

    That is not a bad example, a bad reference or a bad artifact. It is the
    stored contract being unable to express the signature the reseed
    declares, and reporting it as a runtime error would send someone hunting
    a bug in the wrong place. Under v3 the same call succeeds, because
    `prepare_stdin` wraps the arguments in a canonical envelope.
    """
    from groups import execution_adapter, execution_contract

    version = execution_contract.contract_version(question)
    if version == execution_contract.CONTRACT_V3:
        return None

    declared = execution_adapter.declared_signature(starter_source or "")
    if declared is None:
        return ("the starter declares nothing callable, so no contract can "
                "bind it")
    arity = len(declared[1])
    if arity == 1 and isinstance(arguments, (list, tuple)) and             len(arguments) == 1 and isinstance(arguments[0], (list, dict)):
        return (f"contract {version} cannot invoke a one-parameter signature "
                f"whose argument is a container: the generic harness splats "
                f"it into {len(arguments[0])} positional arguments. This "
                f"question needs contract v3 (or a per-question wrapper) "
                f"before any example can be executed.")
    return None


def check_example(question, reference, arguments, claimed_output, runner, *,
                  starter_source=None, specification_digest=None,
                  artifact_digest=None):
    """
    Execute `reference` on `arguments` and compare with `claimed_output`.

    `runner(source, language, stdin) -> verdict dict` is the SAME callable the
    grader and the oracle take, so the early check cannot drift from the
    semantics it is predicting. Nothing is written: this function has no
    database access and no provenance writer in scope.
    """
    from groups.services import GradingService, normalize_output

    record = {
        "evidence_class": EVIDENCE_CLASS,
        "question_id": question.pk,
        "specification_digest": specification_digest,
        "artifact_digest": artifact_digest,
        "arguments": arguments,
        "claimed_output": claimed_output,
        "reference": reference.as_record(),
        "actual_output": None,
        "status_id": None,
        "stderr": None,
        "verdict": EXAMPLE_UNRESOLVED,
        "detail": "",
        # Restated on every record so a reader who sees only this dict cannot
        # mistake it for something the lifecycle accepts.
        "is_oracle_evidence": False,
        "supports_trust_transition": False,
    }

    if arguments is None or claimed_output is None:
        record["verdict"] = EXAMPLE_UNRESOLVED
        record["detail"] = "the example could not be read from the artifact"
        return record

    # The ARTIFACT's declared starter, not the question's stored one. The
    # stored starter is still the variadic placeholder until
    # `declare_signature` runs, and checking against it would ask whether the
    # contract can bind a signature that does not exist yet.
    starter = starter_source or (question.boilerplate_code or {}).get(
        reference.language)
    binding = contract_binding_problem(question, starter, arguments)
    if binding:
        record["verdict"] = EXAMPLE_UNRESOLVED
        record["detail"] = binding
        return record

    try:
        stdin = encode_stdin(arguments)
    except (TypeError, ValueError) as exc:
        record["verdict"] = EXAMPLE_INVALID_INPUT
        record["detail"] = f"the example input is not encodable: {exc}"
        return record

    executable, _stored = GradingService._build_executable(
        question, reference.language, reference.source)
    prepared = GradingService.prepare_stdin(
        question, reference.language, stdin)

    try:
        verdict = runner(executable, reference.language, prepared)
    except Exception as exc:                                  # noqa: BLE001
        record["verdict"] = EXAMPLE_UNRESOLVED
        record["detail"] = f"the runner failed: {type(exc).__name__}: {exc}"
        return record

    record["status_id"] = verdict.get("status_id")
    record["stderr"] = (verdict.get("stderr") or "")[:400]

    if "error" in verdict:
        record["verdict"] = EXAMPLE_UNRESOLVED
        record["detail"] = f"execution unavailable: {verdict['error']}"
        return record

    if verdict.get("status_id") != 3:            # 3 = Accepted, per Judge0
        record["verdict"] = EXAMPLE_RUNTIME_ERROR
        record["detail"] = (
            f"the reference did not run cleanly: "
            f"status={verdict.get('status')!r} "
            f"stderr={(verdict.get('stderr') or '')[:160]!r} "
            f"compile={(verdict.get('compile_output') or '')[:160]!r}")
        return record

    actual = normalize_output(verdict.get("stdout") or "")
    record["actual_output"] = actual

    if _same(claimed_output, actual):
        record["verdict"] = EXAMPLE_PASS
        record["detail"] = (
            "the reference produced the claimed output. This is not oracle "
            "evidence: one run, one case, an unreviewed reference.")
    else:
        record["verdict"] = EXAMPLE_WRONG_OUTPUT
        record["detail"] = (
            f"the artifact claims {claimed_output!r} but the reference "
            f"produced {actual!r}")
    return record


def explanation_status(record, reviewed_by=None):
    """
    Explanation review is a HUMAN act and defaults to unverified.

    A correct verdict says nothing about the prose around it. q2027's
    regenerated artifact computed the right answer and told the learner to
    remove a character that could not be removed.
    """
    if reviewed_by:
        return EXPLANATION_VERIFIED
    return EXPLANATION_UNVERIFIED
