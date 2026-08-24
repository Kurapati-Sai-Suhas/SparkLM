"""
Offline content generation for reseed candidates (M2 P2.7h-19, rewired h-21).

Produces exactly two files per question — `<id>.statement.html` and
`<id>.starter.py` — plus a manifest. It holds **no database write authority
of any kind**: it reads, it calls a provider, it validates, it writes files.

── The model is a formatter, not an author ─────────────────────────────────

Version 1 generated from the title alone, because a reseed candidate carries
no specification — its stored statement is a template wrapping its name. That
was measured in Phase 5 and it failed: five artifacts passed every structural
check and two described a materially different problem. One widened "two
adjacent cells" into "any cell"; another swapped "the smallest and largest"
for "all elements". Both were well-formed, self-consistent, and wrong.

Phase 6 then established that no authoritative specification exists to be
fetched — not in the database, not in the backup, not in git, not in the CSVs,
and not reachably from the canonical source. So the specification is SUPPLIED,
by an operator, per question, and this module refuses to generate without one.

The model's job is now to reword, structure and mark up a requirement that has
already been written down. Everything it needs is in the prompt, so anything it
adds is something it made up — and `validate_artifact` composes a conformance
check that looks for exactly that.

── The limit of the claim ──────────────────────────────────────────────────

Conformance is a REQUIREMENT-LOSS DETECTOR, not a proof of semantic
equivalence: it catches a statement that drops or substitutes a load-bearing
term from its specification. It cannot tell you the specification itself is
right. Nothing here can. That remains a human reading it.

── What it will not generate ───────────────────────────────────────────────

No hidden tests, no expected outputs, no oracle results, no approval, no
trust state, no status, no publication, no adaptive eligibility. Two files.
The suite is written later, by a different authority, and bound against the
signature declared here.
"""

import ast
import datetime
import hashlib
import html.parser
import json
import os
import re

from groups import (
    execution_adapter, pre_image, reseed_authoring,
    reseed_conformance, reseed_presentation, reseed_specification,
)
from groups.models import Question

#: Bumped when the code that shapes an artifact changes.
GENERATOR_VERSION = "1.0.0"
#: Bumped when the prompt changes. Recorded per artifact so a bad batch can be
#: identified by the instructions that produced it, not just by its date.
PROMPT_TEMPLATE_VERSION = "specification-formatter/v4"

MANIFEST_SCHEMA_VERSION = 1

#: The bank uses three discrete ratings, which map exactly onto the usual
#: three bands. Anything else is a question this generator has not seen.
DIFFICULTY_BANDS = {1000.0: "easy", 1300.0: "medium", 1600.0: "hard"}

STATUS_READY = "READY_FOR_REVIEW"
STATUS_REJECTED = "REJECTED"

#: Tags a statement may use. A problem statement is prose, a list, a table and
#: a code sample; anything else is either styling or an attack surface.
_ALLOWED_TAGS = {
    "p", "strong", "em", "b", "i", "code", "pre", "ul", "ol", "li", "br",
    "h3", "h4", "table", "thead", "tbody", "tr", "th", "td", "span", "sup",
    "sub", "blockquote", "hr",
}
_VOID_TAGS = {"br", "hr", "img"}


class GenerationRefused(Exception):
    """The question may not be generated for, or the artifact is unusable."""


class GenerationSpec:
    """Everything generation is allowed to know. Frozen at build time."""

    __slots__ = ("question_id", "title", "topic", "base_difficulty",
                 "difficulty_band", "class_name", "method_name",
                 "current_starter", "input_digest", "batch_key", "frozen",
                 "specification", "specification_digest")

    def __init__(self, **fields):
        for name in self.__slots__:
            object.__setattr__(self, name, fields[name])

    def __setattr__(self, *_a):
        raise AttributeError("a generation spec is frozen once built")

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


def build_spec(question, *, specification, batch=None, using=None):
    """
    Verify the question may be generated for, and freeze what it may know.

    `specification` is REQUIRED and there is no title-only path. Phase 5
    generated from titles alone and produced two artifacts out of five that
    described a materially different problem while passing every structural
    check; Phase 6 established that no authoritative specification exists to
    be fetched. So one is supplied, or nothing is generated.

    With a batch, membership of that FROZEN batch is required — the
    production path, and the only one whose artifacts may ever be applied.
    Without one, eligibility is still checked against live state, the spec is
    marked `frozen=False`, and every artifact built from it is stamped
    unusable by the reseed writer until it is rebuilt against a real batch.
    """
    using = using or question._state.db

    if not specification:
        raise GenerationRefused(
            f"question {question.pk} has no operator specification. This "
            f"pipeline reseeds only questions that have one — generating "
            f"from a title is what Phase 5 proved unsafe.")
    spec_refusals = reseed_specification.validate_specification(
        specification, question_id=question.pk)
    if spec_refusals:
        raise GenerationRefused(
            f"the specification for question {question.pk} was refused:"
            + "".join(chr(10) + "  - " + refusal
                      for refusal in spec_refusals))

    blockers = reseed_authoring.stub_blockers(question, using=using)
    if Question.PLACEHOLDER_MARKER not in (question.content or ""):
        blockers.append("the question carries no placeholder marker")
    if blockers:
        raise GenerationRefused(
            f"question {question.pk} is not an eligible reseed candidate:\n  - "
            + "\n  - ".join(blockers))

    if batch is not None:
        # Raises unless the batch is frozen, this question has a pre-image in
        # it, and that pre-image still verifies.
        pre_image.require_pre_image(batch, question)

    source = (question.boilerplate_code or {}).get("python") or ""
    declared = execution_adapter.declared_signature(source)
    if declared is None:
        raise GenerationRefused(
            f"question {question.pk} has no callable python starter to "
            f"declare a signature on")
    classes = [node.name for node in ast.parse(source).body
               if isinstance(node, ast.ClassDef)]
    if len(classes) != 1:
        raise GenerationRefused(
            f"question {question.pk} declares {len(classes)} classes; "
            f"expected exactly one")

    band = DIFFICULTY_BANDS.get(float(question.base_difficulty))
    if band is None:
        raise GenerationRefused(
            f"question {question.pk} has difficulty "
            f"{question.base_difficulty}, which is outside the three bands "
            f"this generator understands")

    return GenerationSpec(
        question_id=question.pk,
        title=(question.title or "").strip(),
        topic=question.topic.name if question.topic_id else None,
        base_difficulty=float(question.base_difficulty),
        difficulty_band=band,
        class_name=classes[0],
        method_name=declared[0],
        current_starter=source,
        input_digest=pre_image.live_digest(question),
        batch_key=batch.batch_key if batch is not None else None,
        frozen=batch is not None,
        specification=specification,
        specification_digest=reseed_specification.specification_digest(
            specification))


# ═════════════════════════════════════════════════════════════
# The prompt
# ═════════════════════════════════════════════════════════════

def build_prompt(spec):
    """
    One prompt, producing both halves together, from a SUPPLIED specification.

    The model's job changed in this version. In v1 it was the author: given a
    title, invent the problem. That produced two wrong questions out of five.
    Here it is a formatter — the specification states the requirement and the
    model turns it into a statement and a signature. Everything it needs is in
    the prompt, so anything it adds is something it made up.

    Both halves still come from one call. Two would let the parameter list
    drift from the prose describing it, and the parameters are what every
    hidden case is later bound against.
    """
    specification = reseed_specification.canonical_text(spec.specification)
    # The conformance gate demands these words survive into the statement.
    # Naming them is not a relaxation of that rule — it is the same rule,
    # stated instead of guessed at. Without it the model must infer which of
    # its words are load-bearing, and v3 showed what happens: told to write
    # naturally, it paraphrased and dropped requirements it had no way to
    # know were being counted.
    required = sorted(reseed_conformance.terms_in(
        reseed_specification.requirement_text(spec.specification)))
    return f"""You are formatting an ALREADY-WRITTEN specification into a question. Return ONLY a JSON object, no markdown fence, with exactly two string keys: "statement_html" and "starter_python".

THE SPECIFICATION IS THE SOURCE OF TRUTH. Reword it, structure it and mark it up. Do NOT add, remove or alter any requirement it states. If it does not say something, neither do you.

SPECIFICATION
{specification}

REQUIRED API — reproduce these names EXACTLY, they are already referenced elsewhere in the system and may not be renamed:
  class:  {spec.class_name}
  method: {spec.method_name}

current starter (the signature is a placeholder to be replaced):
{spec.current_starter}

WRITE A PROBLEM, NOT A FORM. The specification above is an internal
document. Do NOT reproduce its field names as headings — no "Objective",
"Required operation", "Input semantics", "Output semantics", "Load bearing",
"Method behaviour". Do NOT carry across any sentence that talks about the
specification, about what is "not wanted", or about how the question should be
written. The learner sees only a problem.

RULES FOR "statement_html"
  - Valid, balanced HTML using only: {", ".join(sorted(_ALLOWED_TAGS))}
  - Open by describing the problem in prose, not by labelling a field.
  - End with a worked example under an <h3>Example</h3> heading, showing a
    concrete input (assigning each parameter a value) and the resulting
    output. Construct it yourself; the specification contains none.
  - These exact words MUST appear somewhere in your statement, because the
    specification uses them and dropping one reads as dropping a requirement:
    {", ".join(required)}
  - Express every requirement the specification states, using ITS vocabulary.
    Where the specification says "smallest and largest", "both neighbours",
    "at most three" or "adjacent", use those words. A synonym reads as a
    changed requirement and will be rejected.
  - State the input, the output, the constraints and the edge cases the
    specification gives.
  - Include at least one worked example consistent with the specification.
  - Include a line, in a <p><code>...</code></p>, naming the exact method signature you declare below.
  - Every parameter name you declare MUST appear in the prose, outside that
    signature line.
  - Do NOT include the phrase "{Question.PLACEHOLDER_MARKER}".
  - Do NOT include a worked solution or any implementation.

RULES FOR "starter_python"
  - Exactly one class, named {spec.class_name}.
  - Exactly one public method, named {spec.method_name}.
  - Real named parameters with type annotations on every one, matching the
    input the specification describes.
  - NO *args, NO **kwargs, NO keyword-only parameters.
  - The method body must be exactly `pass` — no logic, no return, no example.
  - Annotations must be plain builtin types or typing generics (int, str, bool, float, list[int], list[list[str]], dict[str, int]).
"""


# ═════════════════════════════════════════════════════════════
# Providers
# ═════════════════════════════════════════════════════════════

def _stub_value(annotation):
    """A plausible literal for a declared type, for the stub's example."""
    kind = (annotation or "").lower()
    if "list[list" in kind:
        return "[[1, 2], [3, 4]]"
    if "list" in kind:
        return "[1, 2, 2, 3]"
    if "str" in kind:
        return '"abc"'
    if "bool" in kind:
        return "true"
    return "2"


class StubProvider:
    """
    Deterministic, offline, no network. The provider the tests use.

    It produces a well-formed artifact for any spec, so a test that fails is
    failing on the code under test rather than on what a model happened to
    say that afternoon.
    """

    name = "stub"
    version = "1.0.0"

    def __init__(self, parameters=(("nums", "list[int]"), ("target", "int"))):
        self.parameters = tuple(parameters)

    def produce(self, spec):
        signature = ", ".join(f"{name}: {kind}"
                              for name, kind in self.parameters)
        names = ", ".join(f"<code>{name}</code>"
                          for name, _kind in self.parameters)
        # The stub is a formatter too, and the least interesting one: it
        # restates the specification verbatim. That is enough to conform,
        # which is the point — a deterministic provider that FAILED
        # conformance would make every test about the stub instead of the
        # code under test.
        # The stub is a formatter too, and it has to be a GOOD one: it must
        # satisfy conformance AND presentation, or every test about the code
        # under test becomes a test about the stub. So it restates the
        # requirement fields as flowing prose — no field labels — and builds
        # a worked example, which is exactly what the real providers are
        # asked to do.
        prose = " ".join(
            (spec.specification.get(field) or "").strip()
            for field in reseed_specification.REQUIREMENT_PROSE)
        example = ", ".join(f"{name} = {_stub_value(kind)}"
                            for name, kind in self.parameters)
        statement = (
            f"<h3>{spec.title}</h3>"
            f"<p>Given {names}, solve the following. {prose}</p>"
            f"<p><code>{spec.method_name}({signature})</code></p>"
            f"<h3>Example</h3><p>{example} → Result: 1</p>")
        starter = (f"class {spec.class_name}:\n"
                   f"    def {spec.method_name}(self, {signature}):\n"
                   f"        pass\n")
        return statement, starter


#: Model ids, per provider, overridable by environment.
#:
#: Explicit and in one place on purpose. `ai_services.py` hard-codes
#: `llama-3.3-70b-versatile` in two separate call sites; that model was
#: withdrawn and now returns 404 on this project's key, so every Groq path in
#: the app fails and fixing it means editing code. A model id is
#: configuration: when a provider retires one, this should be an environment
#: change, not a patch.
PROVIDER_MODELS = {
    "gemini": os.environ.get("RESEED_GEMINI_MODEL", "gemini-2.5-flash"),
    "groq": os.environ.get("RESEED_GROQ_MODEL", "openai/gpt-oss-120b"),
}


def available_models(provider):
    """
    What this key can actually reach, without generating anything.

    Listing models is free; generating is not. A run that is going to fail on
    a withdrawn model should fail before it spends a day's quota discovering
    that — which is exactly how Phase 5 lost its Gemini budget.
    """
    if provider == "groq":
        from django.conf import settings
        from groq import Groq

        client = Groq(api_key=settings.GROQ_API_KEY)
        return sorted(model.id for model in client.models.list().data)
    if provider == "gemini":
        import google.generativeai as genai
        from django.conf import settings

        genai.configure(api_key=settings.GEMINI_API_KEY)
        return sorted(model.name.removeprefix("models/")
                      for model in genai.list_models()
                      if "generateContent" in
                      getattr(model, "supported_generation_methods", []))
    raise ValueError(f"no model listing for provider {provider!r}")


def probe_provider(provider):
    """(configured model, is it reachable, what is) — no generation call."""
    configured = PROVIDER_MODELS.get(provider)
    try:
        models = available_models(provider)
    except Exception as exc:                                  # noqa: BLE001
        return {"provider": provider, "configured": configured,
                "reachable": False, "error": f"{type(exc).__name__}: {exc}",
                "models": []}
    return {"provider": provider, "configured": configured,
            "reachable": configured in models,
            "error": None if configured in models else
                     f"configured model {configured!r} is not offered",
            "models": models}


def _is_exhausted(exc):
    """
    Is this a supply problem rather than a bad response?

    Matched on whole words. An earlier version tested `"rate" in message`,
    which matched "Failed to gene*rate* JSON" — reporting a malformed model
    response as a quota exhaustion and skipping the retries that would have
    fixed it. Substring matching on short words is how that happens.
    """
    message = str(exc).lower()
    if re.search(r"\b(429|404)\b", message):
        return True
    return bool(re.search(r"\b(quota|rate limit|rate_limit|"
                          r"resource_exhausted|model_not_found)\b", message))

class Exhausted(Exception):
    """The provider is rate-limited or out of quota. Retrying will not help."""


class GeminiProvider:
    """The real one. Network, non-deterministic, never used by a test."""

    name = "gemini-2.5-flash"
    version = "1.0.0"

    def __init__(self, model_name=None):
        model_name = model_name or PROVIDER_MODELS["gemini"]
        import google.generativeai as genai
        from django.conf import settings

        genai.configure(api_key=settings.GEMINI_API_KEY)
        self._model = genai.GenerativeModel(model_name)
        self.name = model_name

    def produce(self, spec):
        try:
            response = self._model.generate_content(build_prompt(spec))
        except Exception as exc:                              # noqa: BLE001
            if _is_exhausted(exc):
                raise Exhausted(str(exc)[:200]) from exc
            raise
        payload = _parse_json_response(response.text)
        return payload["statement_html"], payload["starter_python"]


class GroqProvider:
    """
    A second real provider, on the key this project already carries.

    Two providers is not redundancy for its own sake: a free-tier quota of 20
    requests a day is exhausted by four questions at three attempts each, and
    a generator with exactly one upstream is a generator that stops.
    """

    name = "openai/gpt-oss-120b"
    version = "1.0.0"

    def __init__(self, model_name=None):
        model_name = model_name or PROVIDER_MODELS["groq"]
        from django.conf import settings
        from groq import Groq

        self._client = Groq(api_key=settings.GROQ_API_KEY)
        self._model = model_name
        self.name = model_name

    def produce(self, spec):
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": build_prompt(spec)}],
                response_format={"type": "json_object"},
                temperature=0.2)
        except Exception as exc:                              # noqa: BLE001
            if _is_exhausted(exc):
                raise Exhausted(str(exc)[:200]) from exc
            raise
        payload = _parse_json_response(completion.choices[0].message.content)
        return payload["statement_html"], payload["starter_python"]


def _parse_json_response(text):
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n", "", body)
        body = re.sub(r"\n```$", "", body).strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GenerationRefused(
            f"the provider did not return JSON ({exc.msg}); "
            f"first 200 chars: {body[:200]!r}")
    missing = {"statement_html", "starter_python"} - set(payload)
    if missing:
        raise GenerationRefused(
            f"the provider omitted {sorted(missing)}")
    if not all(isinstance(payload[key], str) for key in
               ("statement_html", "starter_python")):
        raise GenerationRefused("the provider returned non-string values")
    return payload


# ═════════════════════════════════════════════════════════════
# Deterministic validation
# ═════════════════════════════════════════════════════════════

class _Balance(html.parser.HTMLParser):
    """Tag balance and vocabulary. Not a sanitiser — a well-formedness check."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.problems = []
        self.text = []

    def handle_starttag(self, tag, _attrs):
        if tag not in _ALLOWED_TAGS:
            self.problems.append(f"the statement uses a disallowed tag <{tag}>")
            return
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        if not self.stack:
            self.problems.append(f"the statement closes </{tag}> that was "
                                 f"never opened")
        elif self.stack[-1] != tag:
            self.problems.append(
                f"the statement closes </{tag}> while <{self.stack[-1]}> is "
                f"still open")
            self.stack.pop()
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def _statement_refusals(spec, statement_html):
    refusals = []
    parser = _Balance()
    parser.feed(statement_html or "")
    parser.close()
    refusals.extend(parser.problems)
    if parser.stack:
        refusals.append(
            f"the statement leaves {sorted(set(parser.stack))} unclosed")

    text = "".join(parser.text)
    if len(text.strip()) < 60:
        refusals.append(
            f"the statement has {len(text.strip())} characters of visible "
            f"text; that is not a problem description")
    if Question.PLACEHOLDER_MARKER in (statement_html or ""):
        refusals.append(
            "the generated statement still contains the placeholder marker")
    if "<script" in (statement_html or "").lower():
        refusals.append("the statement contains a <script> element")

    # A worked solution smuggled into the prose.
    lowered = (statement_html or "").lower()
    if "class solution" in lowered and "return " in lowered:
        refusals.append(
            "the statement appears to embed a worked solution "
            "(a Solution class with a return)")
    return refusals


def _starter_refusals(spec, starter_source):
    """Shape of the declared signature, plus the things a starter must not be."""
    refusals = list(reseed_authoring.validate_signature(
        spec.current_starter, starter_source))

    try:
        tree = ast.parse(starter_source or "")
    except SyntaxError:
        return refusals            # already reported by validate_signature

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        refusals.append(
            f"the starter has module-level {type(node).__name__}; a starter "
            f"is a class declaration, not a script")

    # An answer key hidden in the starter: literal case data.
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {key.value for key in node.keys
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)}
            if keys & {"stdin", "expected_output", "input", "output"}:
                refusals.append(
                    "the starter contains what looks like test-case data; "
                    "hidden tests and expected outputs are written later, by "
                    "a different authority")
                break
    return refusals


def semantic_refusals(spec, statement_html, starter_source):
    """
    Do the prose and the API agree?

    The declared parameters are what every hidden case is later bound
    against, so a statement describing different inputs from the ones the
    method accepts is a question whose answers cannot mean anything. The check
    is deliberately literal: each declared name, and the method name, must
    appear verbatim in the statement. The prompt requires it, so a provider
    that cannot manage it has produced something that needs a human.
    """
    refusals = []
    declared = execution_adapter.declared_signature(starter_source or "")
    if declared is None:
        return ["the starter declares nothing callable, so nothing can agree "
                "with the statement"]

    method_name, parameters = declared
    text = _visible_text(statement_html)

    if not re.search(rf"\b{re.escape(method_name)}\b", text):
        refusals.append(
            f"the statement never names the method {method_name!r} it is "
            f"supposed to describe")
    if method_name != spec.method_name:
        refusals.append(
            f"the starter renamed the method: {spec.method_name!r} -> "
            f"{method_name!r}")

    # Parameters are checked against the PROSE, with the signature line
    # removed first. The prompt requires that line, so leaving it in would
    # make this check self-satisfying: every parameter would "appear" in the
    # statement merely because the signature was pasted into it, proving
    # nothing about whether the problem text describes those inputs.
    prose = re.sub(rf"{re.escape(method_name)}\s*\([^)]*\)", " ", text)
    for name, _annotation in parameters:
        if not re.search(rf"\b{re.escape(name)}\b", prose):
            refusals.append(
                f"the statement never mentions the parameter {name!r} outside "
                f"the signature line, so the prose does not describe the "
                f"input the method accepts")

    # The title-overlap heuristic exists for title-only generation, where the
    # title was the only source of truth. With a specification attached the
    # specification IS the truth and conformance checks against it directly;
    # keeping this would refuse a faithful statement for spelling the title
    # differently. It cost a pilot artifact: a specification written with
    # "coloured"/"neighbours" against a title reading "Colored"/"Neighbors".
    significant = [] if getattr(spec, "specification", None) else [
        word for word in re.findall(r"[A-Za-z]+", spec.title) if len(word) > 3]
    if significant:
        hits = sum(1 for word in significant
                   if re.search(rf"\b{re.escape(word)}\b", text, re.I))
        if hits / len(significant) < 0.5:
            refusals.append(
                f"the statement shares only {hits}/{len(significant)} "
                f"significant words with the title {spec.title!r}; it may "
                f"describe a different problem")
    return refusals


def validate_artifact(spec, statement_html, starter_source):
    """
    Every deterministic check, all of them, before anything is written.

    The conformance check is composed HERE rather than called alongside,
    because a Phase 5 mutation proved the difference matters: dropping
    `semantic_refusals` from this expression changed nothing any test could
    see, since every semantic test called it directly. A check that is not
    part of the composition is a check that can be unwired in silence.
    """
    return (_statement_refusals(spec, statement_html)
            + _starter_refusals(spec, starter_source)
            + semantic_refusals(spec, statement_html, starter_source)
            + conformance_refusals(spec, statement_html)
            + presentation_refusals(spec, statement_html, starter_source))


def presentation_refusals(spec, statement_html, starter_source):
    """
    Does this read like a problem, or like the form it was written on?

    Independent of conformance and pulling the other way. Conformance rewards
    keeping the specification's vocabulary, so the cheapest way to satisfy it
    is to paste the specification — which is exactly what four of five Phase 7
    artifacts did. This check refuses that, so an artifact has to earn both.
    """
    declared = execution_adapter.declared_signature(starter_source or "")
    parameters = [name for name, _annotation in (declared[1] if declared else [])]
    return reseed_presentation.presentation_refusals(
        statement_html, parameters=parameters,
        specification=getattr(spec, "specification", None))


def conformance_refusals(spec, statement_html):
    """
    Does the statement still say what the specification says?

    A requirement-loss detector, not a proof of semantic equivalence — see
    `groups/reseed_conformance.py`, which is explicit about the boundary of
    the claim.
    """
    specification = getattr(spec, "specification", None)
    if not specification:
        return ["no specification is attached to this generation spec; "
                "conformance cannot be checked"]
    # `method_behaviour` is deliberately excluded. It describes the method's
    # SHAPE — arity, return type, "does not print" — which
    # `validate_signature` already enforces structurally and exactly.
    # Comparing it again as prose only imports its negations: a
    # specification saying "does not print" made `print` a required word, so
    # a statement that correctly never mentioned printing was refused. That
    # cost three of five artifacts on the first pilot run.
    return reseed_conformance.conformance_refusals(
        reseed_specification.requirement_text(specification), statement_html,
        allow_omitted=frozenset(
            (specification.get("conformance_allow_omitted") or ())))


def _visible_text(statement_html):
    parser = _Balance()
    parser.feed(statement_html or "")
    parser.close()
    return "".join(parser.text)


# ═════════════════════════════════════════════════════════════
# Generation and the manifest
# ═════════════════════════════════════════════════════════════

def artifact_digest(statement_html, starter_source):
    """Identity of the pair, so a manifest cannot drift from its files."""
    digest = hashlib.sha256()
    digest.update(b"statement\0")
    digest.update((statement_html or "").encode("utf-8"))
    digest.update(b"\0starter\0")
    digest.update((starter_source or "").encode("utf-8"))
    return digest.hexdigest()


def generate(spec, provider, *, attempts=1):
    """
    Produce and validate, retrying a REJECTED artifact up to `attempts`.

    A failed attempt is never repaired — it is discarded and regenerated from
    the same spec. Silently patching a provider's output would produce an
    artifact no prompt version explains, which is exactly what the manifest
    exists to prevent.
    """
    tries = []
    for attempt in range(1, max(1, attempts) + 1):
        try:
            statement, starter = provider.produce(spec)
        except Exhausted as exc:
            # Retrying a quota error just spends the next question's budget.
            # Record it once and stop: this is a supply problem, not a bad
            # artifact, and the difference matters when reading the manifest.
            tries.append({"attempt": attempt,
                          "refusals": [f"provider exhausted: {exc}"],
                          "statement": None, "starter": None})
            break
        except Exception as exc:                              # noqa: BLE001
            # Deliberately broad. A remote provider fails in ways that are not
            # ours to enumerate — a safety filter, a rate limit, a truncated
            # response — and a slice of fifty questions must not die because
            # the seventh tripped one. The failure is recorded against THIS
            # question and the run continues.
            tries.append({"attempt": attempt,
                          "refusals": [f"{type(exc).__name__}: {exc}"],
                          "statement": None, "starter": None})
            continue
        refusals = validate_artifact(spec, statement, starter)
        tries.append({"attempt": attempt, "refusals": refusals,
                      "statement": statement, "starter": starter})
        if not refusals:
            break
    return tries


def build_manifest(spec, provider, tries, *, filenames, generated_at=None):
    final = tries[-1] if tries else {"refusals": ["no attempt was made"],
                                     "statement": None, "starter": None}
    accepted = bool(final.get("statement")) and not final["refusals"]
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "question_id": spec.question_id,
        "title": spec.title,
        "topic": spec.topic,
        "difficulty_band": spec.difficulty_band,
        "input_digest": spec.input_digest,
        "specification_digest": spec.specification_digest,
        "specification_provenance": spec.specification.get("provenance"),
        "batch_key": spec.batch_key,
        "frozen_batch": spec.frozen,
        "generator_version": GENERATOR_VERSION,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "provider": provider.name,
        "provider_version": provider.version,
        "generated_at": (generated_at
                         or datetime.datetime.now(datetime.timezone.utc)
                         ).isoformat(),
        "outputs": dict(filenames) if accepted else {},
        "status": STATUS_READY if accepted else STATUS_REJECTED,
        "attempts": len(tries),
        "regeneration_count": max(0, len(tries) - 1),
        "refusals": final["refusals"],
        # Why each earlier attempt was thrown away. Without this the manifest
        # records THAT an artifact was regenerated twice and never why, which
        # is the half of the story a reviewer actually needs: a provider that
        # keeps failing the same check is a prompt problem, not bad luck.
        "attempt_history": [{"attempt": attempt["attempt"],
                             "refusals": attempt["refusals"]}
                            for attempt in tries],
        "artifact_digest": (artifact_digest(final["statement"],
                                            final["starter"])
                            if accepted else None),
        # An artifact generated outside a frozen batch may never be applied.
        # The reseed writer re-checks this rather than trusting it.
        "applicable": bool(accepted and spec.frozen),
    }


def write_artifacts(out_dir, spec, tries, provider, *, generated_at=None):
    """
    Write the two files and the manifest. A REJECTED artifact writes NO
    production file — only the manifest recording why, and the rejected text
    beside it under a `.rejected` suffix so a human can see what happened.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    final = tries[-1] if tries else {"refusals": ["no attempt"],
                                     "statement": None, "starter": None}
    accepted = bool(final.get("statement")) and not final["refusals"]

    statement_name = f"{spec.question_id}.statement.html"
    starter_name = f"{spec.question_id}.starter.py"
    suffix = "" if accepted else ".rejected"

    if final.get("statement") is not None:
        (out_dir / (statement_name + suffix)).write_text(
            final["statement"], encoding="utf-8")
    if final.get("starter") is not None:
        (out_dir / (starter_name + suffix)).write_text(
            final["starter"], encoding="utf-8")

    manifest = build_manifest(
        spec, provider, tries,
        filenames={"statement": statement_name, "starter": starter_name},
        generated_at=generated_at)
    (out_dir / f"{spec.question_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def verify_manifest(out_dir, manifest):
    """
    Re-derive the artifact digest from the files on disk.

    A manifest is a claim about two files; this is what makes it evidence. A
    stale manifest — files edited after generation — fails here.
    """
    if manifest.get("status") != STATUS_READY:
        return ["the manifest does not describe an accepted artifact"]
    outputs = manifest.get("outputs") or {}
    statement = out_dir / outputs.get("statement", "")
    starter = out_dir / outputs.get("starter", "")
    if not statement.is_file() or not starter.is_file():
        return ["the manifest names files that are not on disk"]
    live = artifact_digest(statement.read_text(encoding="utf-8"),
                           starter.read_text(encoding="utf-8"))
    if live != manifest.get("artifact_digest"):
        return [f"the files on disk digest to {live} but the manifest claims "
                f"{manifest.get('artifact_digest')}; it is stale"]
    return []
