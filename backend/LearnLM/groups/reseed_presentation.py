"""
Presentation quality for a generated statement (M2 P2.7h-22).

Phase 7 produced five artifacts that were 5/5 deterministic, 5/5 conformant
and 5/5 faithful to their specifications — and only 1/5 fit to show a learner.
Four had transcribed the specification's internal structure into the
learner-facing text:

    <h3>Objective</h3> … <h3>Required operation</h3> … <h3>Input semantics</h3>

and one carried the specification's own meta-commentary through to the
learner: "The greatest common divisor of the whole list is NOT what is wanted
and is a different quantity." That sentence exists to steer the generator away
from a Phase 5 defect. A learner should never see it.

── Why this is a SEPARATE validator ────────────────────────────────────────

Conformance scores an artifact on retaining the specification's vocabulary, so
perfect transcription scores perfectly. The incentive is structural: the
cheapest way to pass a term-overlap check is to paste. Weakening conformance
to fix that would trade a correctness check for a style one.

So this check is independent and pulls the other way. Conformance asks "did
you keep the requirements?"; this asks "did you write a problem, or copy a
form?" An artifact has to satisfy both, and copying satisfies only the first.

── What it detects, and what it cannot ─────────────────────────────────────

It detects LEAKAGE and SHAPE: the specification's field labels used as
headings, prose addressed to the author rather than the learner, internal
tooling vocabulary, a missing or malformed worked example, and a statement
that is a form rather than an explanation.

It does NOT judge whether the problem is well posed, whether the example's
output is arithmetically right, or whether the statement is pleasant to read.
The example check is INTERNAL CONSISTENCY only — that an example exists, has
both halves, and refers to the declared parameters. It proves nothing about
algorithmic correctness and this module does not claim otherwise.
"""

import re

from groups import reseed_specification

#: Schema field names rendered the way a heading would render them.
#: Derived from the specification schema rather than hand-listed, so a new
#: field cannot leak simply because nobody remembered to blacklist it.
_SCHEMA_LABELS = {
    field: field.replace("_", " ").lower()
    for field in reseed_specification.REQUIRED_PROSE
}

#: Labels that no real problem statement would ever use as a heading. These
#: are our internal vocabulary; seeing one means the form leaked.
_INTERNAL_ONLY = {"required operation", "input semantics", "output semantics",
                  "load bearing", "load-bearing", "load bearing requirements",
                  "method behaviour", "method behavior"}

#: Labels that ARE natural in a learner-facing statement. "Constraints" is a
#: section of every competitive-programming problem ever written. They are not
#: refused on their own — they only count toward transcription.
_LEGITIMATE_HEADINGS = {"objective", "constraints", "edge cases", "example",
                        "examples", "problem", "problem statement", "input",
                        "output", "notes", "note", "task", "description",
                        "follow up", "follow-up"}

#: Prose that addresses the author or the tooling instead of the learner.
#: Anchored on constructions that only make sense to someone WRITING the
#: question — a learner is never told what "is not wanted" or warned off a
#: previous problem they have not seen.
_AUTHOR_PATTERNS = (
    (r"\bis\s+not\s+what\s+(is|we)\s+want", "tells the reader what is 'not wanted'"),
    (r"\bnot\s+what\s+is\s+wanted\b", "tells the reader what is 'not wanted'"),
    (r"\bdo\s+not\s+confuse\b", "warns against confusing this with something else"),
    (r"\bunlike\s+the\s+(previous|earlier|other)\s+(problem|question|task)",
     "refers to another problem the learner has not seen"),
    (r"\bthis\s+wording\s+is\s+(intended|meant)", "explains its own wording"),
    (r"\bthe\s+(specification|spec)\s+(requires|says|states|asks|above|"
     r"describes|mentions)", "refers to the specification"),
    (r"\b(per|according\s+to)\s+the\s+(specification|spec)\b",
     "refers to the specification"),
    (r"\bthe\s+(model|generator|assistant|llm)\s+(should|must|will|needs)",
     "instructs the model"),
    (r"\bthe\s+(operator|author)\s+(should|must|supplied|wrote|intends)",
     "refers to the author"),
    (r"\bnote\s+to\s+(the\s+)?(author|reviewer|editor|self)\b",
     "is a note to the author"),
    (r"\bas\s+(stated|specified)\s+in\s+the\s+(specification|spec)\b",
     "refers to the specification"),
    (r"\bthis\s+(statement|artifact|question)\s+(was|is)\s+(generated|"
     r"produced|written)\s+", "describes its own production"),
)

#: Internal tooling vocabulary. Refused only in an authoring construction,
#: because several are ordinary problem-domain words: an "operator" is a
#: mathematical symbol, a "model" is a way of computing, and a "digest" is a
#: hash a question might legitimately ask about.
_METADATA_PATTERNS = (
    (r"\b(specification_digest|artifact_digest|input_digest|"
     r"specification digest|artifact digest)\b", "names an internal digest"),
    (r"\b(prompt[_ ]template|generator[_ ]version|manifest|provenance)\b",
     "names internal generation metadata"),
    (r"\b(conformance|validator|validation\s+gate)\b",
     "names the validation tooling"),
    (r"\bOPERATOR_SUPPLIED\b", "leaks a provenance constant"),
    (r"\bquestion[_ ]id\b", "leaks an internal identifier"),
)

#: A worked example needs both halves. Written loosely: statements say
#: "Input:", "Example 1:", "Given nums = [1,2]" and all of them are fine.
_INPUT_CUE = re.compile(r"\b(input|given|for)\b\s*[:=]?", re.I)
#: `nums = [12, 15, 9, 30]` presents an input as surely as the word "Input:".
#: Both pilot examples that read best did exactly this and were refused by a
#: cue-word-only rule — the check was measuring vocabulary, not presence.
_ASSIGNMENT = re.compile(
    r"\b[A-Za-z_]\w*\s*=\s*(\[|\"|'|“|\d|true|false)", re.I)
_OUTPUT_CUE = re.compile(r"\b(output|returns?|result|answer|yields?|gives?|"
                         r"becomes)\b\s*[:=]?", re.I)
#: `nums = [1,2], target = 2 → 1` presents an output with no cue word in
#: sight. Arrow notation is how a great many worked examples are written.
_ARROW = re.compile(r"(→|->|=>|⇒)\s*\S")
_LITERAL = re.compile(r"(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|“[^”]*”|"
                      r"\b\d+\b|\b(true|false)\b)", re.I)


def _visible(html):
    body = re.sub(r"<[^>]+>", " ", html or "")
    body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&le;", "<=").replace("&ge;", ">="))
    return re.sub(r"\s+", " ", body).strip()


def headings(html):
    """Normalised heading text: <h3>/<h4>, and a bolded line acting as one."""
    found = []
    for match in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html or "",
                             re.I | re.S):
        found.append(_normalise(match.group(1)))
    # `<p><strong>Objective</strong> …` is a heading wearing a disguise.
    for match in re.finditer(
            r"<p[^>]*>\s*<(?:strong|b)>(.*?)</(?:strong|b)>\s*[:.]?", html or "",
            re.I | re.S):
        found.append(_normalise(match.group(1)))
    return [heading for heading in found if heading]


def _normalise(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = re.sub(r"\s+", " ", text).strip().rstrip(":.").lower()
    return text


def label_refusals(html):
    """Specification field labels leaking into the learner-facing text."""
    refusals = []
    present = headings(html)

    internal = sorted({heading for heading in present
                       if heading in _INTERNAL_ONLY})
    if internal:
        refusals.append(
            f"the statement uses internal specification labels as headings: "
            f"{internal}. These name fields of the specification form, not "
            f"parts of a problem.")

    # A label written inline as "Input semantics:" is the same leak.
    body = _visible(html).lower()
    inline = sorted({label for label in _INTERNAL_ONLY
                     if re.search(rf"\b{re.escape(label)}\s*:", body)})
    inline = [label for label in inline if label not in internal]
    if inline:
        refusals.append(
            f"the statement writes internal specification labels inline: "
            f"{inline}.")
    return refusals


def transcription_refusals(html):
    """
    Has the specification's FORM been copied, rather than its content used?

    Counted across schema labels rather than judged one at a time. A single
    "Constraints" heading is what every problem statement has; four schema
    labels in schema order is a filled-in form.
    """
    present = headings(html)
    schema = [label for label in _SCHEMA_LABELS.values() if label in present]

    refusals = []
    if len(schema) >= 3:
        refusals.append(
            f"the statement reproduces the specification's structure: "
            f"{len(schema)} of its field labels appear as headings "
            f"({schema}). A problem statement explains a problem; it is not "
            f"the form the problem was written on.")
    elif (len(schema) == 2 and _in_schema_order(present)
            and any(label not in _LEGITIMATE_HEADINGS for label in schema)):
        # "Constraints" then "Edge cases" is the shape of every problem
        # statement ever written, and both are schema fields by coincidence
        # rather than by leakage. Two ordered labels only read as a
        # transcription when one of them is not a natural heading in its own
        # right — the rule refused the pilot's best artifact before this.
        refusals.append(
            f"the statement's headings follow the specification's field "
            f"order ({schema}), which reads as a transcription rather than an "
            f"explanation.")
    return refusals


def _in_schema_order(present):
    order = list(_SCHEMA_LABELS.values())
    indices = [order.index(heading) for heading in present if heading in order]
    return len(indices) >= 2 and indices == sorted(indices)


def commentary_refusals(html):
    """Prose addressed to the author or the tooling rather than the learner."""
    body = _visible(html)
    refusals, seen = [], set()
    for pattern, description in _AUTHOR_PATTERNS:
        match = re.search(pattern, body, re.I)
        # One finding per description: several patterns deliberately overlap
        # so that a phrasing variant cannot slip between them, and reporting
        # the same sentence twice makes a reviewer distrust the count.
        if match and description not in seen:
            seen.add(description)
            refusals.append(
                f"the statement {description}: \"{_excerpt(body, match)}\". "
                f"This addresses whoever wrote the question, not whoever is "
                f"solving it.")
    return refusals


def metadata_refusals(html):
    """Internal tooling vocabulary leaking into the statement."""
    body = _visible(html)
    refusals = []
    for pattern, description in _METADATA_PATTERNS:
        match = re.search(pattern, body, re.I)
        if match:
            refusals.append(
                f"the statement {description}: \"{_excerpt(body, match)}\".")
    return refusals


def _excerpt(body, match, width=48):
    start = max(0, match.start() - 12)
    end = min(len(body), match.end() + width)
    return ("…" if start else "") + body[start:end].strip() + \
           ("…" if end < len(body) else "")


def example_refusals(html, *, parameters=(), specification=None):
    """
    Is there a usable worked example?

    INTERNAL CONSISTENCY ONLY. This checks that an example exists, has an
    input and an output, carries concrete values, and refers to the declared
    parameters. It does NOT and CANNOT check that the output is the right
    answer — that would require running the problem, which is the oracle's
    job, several phases from here.
    """
    body = _visible(html)
    refusals = []

    section = _example_section(html, body)
    if not section:
        return ["the statement contains no worked example. A specification "
                "states a requirement; a problem statement shows one."]

    if not (_INPUT_CUE.search(section) or _ASSIGNMENT.search(section)):
        refusals.append(
            "the example does not present an input — no input cue and no "
            "parameter assignment.")
    if not (_OUTPUT_CUE.search(section) or _ARROW.search(section)):
        refusals.append("the example does not present an output or result.")
    if not _LITERAL.search(section):
        refusals.append(
            "the example carries no concrete value — an example without data "
            "is a restatement.")

    named = [name for name in parameters if re.search(
        rf"\b{re.escape(name)}\b", section)]
    if parameters and not named:
        refusals.append(
            f"the example never names any declared parameter "
            f"{list(parameters)}, so it cannot be read against the signature.")

    if specification is not None:
        # The pilot specifications carry no examples, so a "copied" example
        # would mean the model lifted prose from a field that was never an
        # example. Detected as a long verbatim overlap rather than a hunch.
        spec_body = reseed_specification.canonical_text(specification)
        if _verbatim_overlap(section, spec_body) >= 12:
            refusals.append(
                "the example is lifted verbatim from the specification rather "
                "than constructed.")
    return refusals


def _example_section(html, body):
    """The text following an Example heading, or a labelled Input/Output pair."""
    match = re.search(r"<h[1-6][^>]*>\s*examples?\b.*?</h[1-6]>(.*)$",
                      html or "", re.I | re.S)
    if match:
        return _visible(match.group(1))
    match = re.search(r"\bexamples?\b\s*\d*\s*[:.]?(.{0,600})$", body, re.I | re.S)
    if match and _LITERAL.search(match.group(1)):
        return match.group(1)
    # An example needs no heading. "For nums = [1,2] and target = 2 the answer
    # is 1" is a worked example in one sentence, and anchoring the search on
    # the literal word "input" missed every example written that way.
    #
    # Anchored on a VALUE BOUND TO A NAME (`nums = [1,2]`) or an explicitly
    # labelled input, never on the loose word "given". "You are given nums and
    # target… the list holds between 1 and 100 integers" has an input cue, a
    # number and the word "return", and is a constraints paragraph, not an
    # example. What makes an example is concrete data, not vocabulary.
    anchor = _ASSIGNMENT.search(body) or re.search(r"\binput\s*[:=]", body,
                                                   re.I)
    if anchor:
        window = body[anchor.start():anchor.start() + 400]
        if _LITERAL.search(window) and (_OUTPUT_CUE.search(window)
                                        or _ARROW.search(window)):
            return window
    return ""


def _verbatim_overlap(section, spec_body):
    """Longest run of consecutive words shared with the specification."""
    words = re.findall(r"[a-z0-9]+", section.lower())
    spec = " ".join(re.findall(r"[a-z0-9]+", spec_body.lower()))
    best = 0
    for start in range(len(words)):
        for length in range(best + 1, len(words) - start + 1):
            if " ".join(words[start:start + length]) in spec:
                best = length
            else:
                break
    return best


def structure_refusals(html, *, parameters=()):
    """
    Does it read like a problem: explanation first, then the details?

    Deliberately loose. There is no required template and no required
    heading — only that the statement opens by describing something rather
    than by labelling a field, and that it says what goes in and what comes
    out somewhere.
    """
    body = _visible(html)
    refusals = []

    # A token floor only. Completeness is decided by the three checks below —
    # does it say what is given, what comes back, and show an example — and a
    # length threshold on top of those measures verbosity, not quality. Set at
    # 160 it refused a terse statement that had all three.
    if len(body) < 80:
        refusals.append(
            f"the statement is {len(body)} characters of visible text; that "
            f"is not a problem description.")

    opening = body[:200].lower()
    first = (headings(html) or [""])[0]
    if first in _SCHEMA_LABELS.values() and first not in {"objective"}:
        refusals.append(
            f"the statement opens with the specification field {first!r} "
            f"rather than a description of the problem.")

    if not _OUTPUT_CUE.search(body):
        refusals.append(
            "the statement never says what should be returned or produced.")
    # Either it says so in words, or it names the parameters it is handed.
    # "Return the number of values in `nums` equal to `target`" says what the
    # learner is given without using a single cue word, and a cue-word-only
    # rule refused exactly that.
    introduces = re.search(r"\b(given|receives?|takes?|input|parameters?)\b",
                           opening + body[:600], re.I)
    names_parameter = any(re.search(rf"\b{re.escape(name)}\b", body)
                          for name in parameters)
    if not (introduces or names_parameter):
        refusals.append("the statement never says what the learner is given.")
    return refusals


def presentation_refusals(html, *, parameters=(), specification=None):
    """Every presentation check, composed."""
    return (label_refusals(html)
            + transcription_refusals(html)
            + commentary_refusals(html)
            + metadata_refusals(html)
            + example_refusals(html, parameters=parameters,
                               specification=specification)
            + structure_refusals(html, parameters=parameters))


def presentation_report(html, *, parameters=(), specification=None):
    """The same checks, itemised, for a reviewer rather than a gate."""
    return {
        "headings": headings(html),
        "labels": label_refusals(html),
        "transcription": transcription_refusals(html),
        "commentary": commentary_refusals(html),
        "metadata": metadata_refusals(html),
        "example": example_refusals(html, parameters=parameters,
                                    specification=specification),
        "structure": structure_refusals(html, parameters=parameters),
    }
