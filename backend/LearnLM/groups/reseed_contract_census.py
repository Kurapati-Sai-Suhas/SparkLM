"""
The reseed contract census (M2 P2.7h-26 / Phase 11).

READ-ONLY. Nothing here writes, and nothing here calls Judge0. The module
answers one question about the reseed candidate population:

    how many candidates can be executed under the contract they carry today,
    how many will need v3 once their signature is declared, and how many
    cannot be decided yet because the signature does not exist?

The third number is the interesting one and it is not a failure of the census.
A reseed candidate stores a VARIADIC PLACEHOLDER — `def solve(self, *args,
**kwargs)`. There is no arity to reason about, so any contract verdict taken
from the stored starter is a verdict about a signature that has not been
written. `declare_signature` is what creates the fact; this module refuses to
invent it early.

Signature reading is imported from `execution_adapter` rather than repeated.
The census must describe what the adapter will actually do, and a second parser
would eventually describe something else.
"""

from groups import execution_adapter, execution_contract

# ── The v3 requirement ──────────────────────────────────────────────────────

V3_REQUIRED = "V3_REQUIRED"
V1_SUFFICIENT = "V1_SUFFICIENT"
UNKNOWN = "UNKNOWN"

#: The kinds whose JSON encoding is itself a list, which is what makes v1's
#: splat misfire. A `list[int]` argument arrives as `[3, 6, 4]`, and the v1
#: harness reads that as "three arguments", not "one list".
CONTAINER_KINDS = (execution_adapter.SEQUENCE, execution_adapter.MAPPING)


def v3_requirement(source):
    """
    Whether a declared signature needs contract v3, as a three-valued answer.

    The rule is narrow on purpose. v1 json-parses the whole stdin blob and, if
    the result is a list, splats it positionally. That is CORRECT whenever the
    number of parameters equals the number of elements — which is every
    multi-parameter signature. It is wrong in exactly one shape: a single
    parameter whose value is itself a container, where the one argument gets
    torn into several.

        (nums: list[int])        stdin "[3, 6, 4]"  -> f(3, 6, 4)   WRONG
        (s: str, k: int)         stdin "abc\\n2"     -> f("abc", 2)  right
        (colors: str)            stdin "aabbcc"     -> f("aabbcc")  right

    UNKNOWN is returned rather than a verdict when the signature cannot support
    one: no callable, variadic, keyword-only, or a lone parameter with no
    annotation. A lone unannotated parameter is the subtle case — its arity is
    known but its KIND is not, and the whole rule turns on the kind, so
    answering V1_SUFFICIENT there would be a guess wearing a verdict's clothes.
    """
    if not (isinstance(source, str) and source.strip()):
        return UNKNOWN
    if execution_adapter.accepts_variable_arity(source) or \
            execution_adapter.has_keyword_only_parameters(source):
        return UNKNOWN

    signature = execution_adapter.declared_signature(source)
    if signature is None or not signature[1]:
        return UNKNOWN

    kinds = [execution_adapter.classify_annotation(annotation)
             for _name, annotation in signature[1]]

    if len(kinds) == 1:
        if kinds[0] == execution_adapter.UNDECLARED:
            return UNKNOWN
        return V3_REQUIRED if kinds[0] in CONTAINER_KINDS else V1_SUFFICIENT

    # Two or more parameters: the splat delivers one element per parameter, so
    # the arity is right whatever the kinds turn out to be.
    return V1_SUFFICIENT


# ── Signature shape classes ─────────────────────────────────────────────────
#
# Mutually exclusive, FIRST MATCH WINS, ordered so that a question is described
# by the earliest thing that stops the harness from binding it. A question with
# no python starter is not also "unannotated"; reporting it twice would inflate
# the census past the number of questions that exist.
#
# The letters are stable identifiers. `execution_adapter` already refers to
# "class E" and "class G" in its own docstrings, and those two are pinned here.

SHAPE_CLASSES = (
    ("A", "NO_PYTHON_STARTER",
     "no python entry in boilerplate_code; nothing to bind"),
    ("B", "UNPARSEABLE",
     "the stored starter is not valid Python"),
    ("C", "NO_CALLABLE",
     "parses, but declares no function the harness could call"),
    ("D", "AMBIGUOUS_ENTRY_POINT",
     "more than one public method; v1 picks alphabetically, v2 refuses"),
    ("E", "VARIADIC",
     "*args/**kwargs — no arity is declared (the reseed placeholder)"),
    ("F", "KEYWORD_ONLY",
     "keyword-only parameters; stdin cannot supply argument names"),
    ("G", "ZERO_PARAMETERS",
     "takes no arguments; stdin is not delivered anywhere"),
    ("H", "UNANNOTATED",
     "parameters declared, none annotated; the kind is unknown"),
    ("I", "SINGLE_CONTAINER",
     "one parameter, container kind — the shape v1 mis-splats"),
    ("J", "SINGLE_SCALAR",
     "one parameter, scalar kind"),
    ("K", "MULTI_PARAMETER",
     "two or more parameters; the splat arity is correct"),
)

SHAPE_NAMES = {letter: name for letter, name, _ in SHAPE_CLASSES}
SHAPE_DESCRIPTIONS = {letter: text for letter, _, text in SHAPE_CLASSES}


def classify_shape(source):
    """The single shape class letter for one python starter."""
    if not (isinstance(source, str) and source.strip()):
        return "A"

    node, _is_method = execution_adapter.chosen_function(source)
    if node is None:
        # `chosen_function` swallows SyntaxError and returns None, so the two
        # cases are separated here by re-parsing rather than by guessing.
        try:
            compile(source, "<starter>", "exec", dont_inherit=True)
        except SyntaxError:
            return "B"
        return "C"

    if len(execution_adapter.public_method_names(source)) > 1:
        return "D"
    if execution_adapter.accepts_variable_arity(source):
        return "E"
    if execution_adapter.has_keyword_only_parameters(source):
        return "F"

    signature = execution_adapter.declared_signature(source)
    parameters = signature[1] if signature else []
    if not parameters:
        return "G"

    kinds = [execution_adapter.classify_annotation(annotation)
             for _name, annotation in parameters]
    if all(kind == execution_adapter.UNDECLARED for kind in kinds):
        return "H"
    if len(kinds) > 1:
        return "K"
    return "I" if kinds[0] in CONTAINER_KINDS else "J"


# ── What binds a question today ─────────────────────────────────────────────

GENERIC_HARNESS = "generic"
CUSTOM_HARNESS = "custom_wrapper"

#: How each contract feeds the harness. v3 shares v1's TEMPLATE and differs
#: only in the stdin it is handed, which is why migrating a question changes
#: what its stored expected outputs mean.
STDIN_TREATMENT = {
    execution_contract.CONTRACT_V1: "raw (literal-\\n expanded)",
    execution_contract.CONTRACT_V2: "raw; wrapper reads one line per parameter",
    execution_contract.CONTRACT_V3: "canonical envelope built from the signature",
}


def has_custom_wrapper(wrappers):
    """A per-question wrapper defines its own I/O contract and is checked
    FIRST by `_build_executable`, so the contract version never reaches it."""
    if not isinstance(wrappers, dict):
        return False
    return any(isinstance(value, str) and value.strip()
               for value in wrappers.values())


def classify(row):
    """
    One candidate's contract position. `row` is a dict of Question values:
    id, execution_contract_version, boilerplate_code, hidden_wrapper_code.

    Returns the stored contract, the harness that would run, the shape class
    and the v3 verdict. No field is derived from the title, and none is
    derived from the topic — a signature is a fact about code, and the only
    code that exists for a candidate is a placeholder.
    """
    boilerplate = row.get("boilerplate_code") or {}
    source = (boilerplate.get("python")
              if isinstance(boilerplate, dict) else None)

    custom = has_custom_wrapper(row.get("hidden_wrapper_code"))
    contract = row.get("execution_contract_version") or \
        execution_contract.DEFAULT_CONTRACT

    shape = classify_shape(source)
    requirement = v3_requirement(source)
    if custom:
        # The generic harness never runs, so the splat defect cannot occur and
        # v3 has nothing to fix. The wrapper's own contract is out of scope.
        requirement = V1_SUFFICIENT

    return {
        "id": row.get("id"),
        "contract": contract,
        "harness": CUSTOM_HARNESS if custom else GENERIC_HARNESS,
        "shape": shape,
        "shape_name": SHAPE_NAMES[shape],
        "v3_requirement": requirement,
        "declares_signature": shape not in ("A", "B", "C", "E", "F"),
    }


def summarise(classifications):
    """Counts by contract, harness, shape and v3 verdict."""
    rows = list(classifications)
    summary = {
        "total": len(rows),
        "by_contract": {},
        "by_harness": {},
        "by_shape": {},
        "by_v3_requirement": {},
        "declares_signature": 0,
    }
    for row in rows:
        for key, field in (("by_contract", "contract"),
                           ("by_harness", "harness"),
                           ("by_shape", "shape"),
                           ("by_v3_requirement", "v3_requirement")):
            bucket = summary[key]
            bucket[row[field]] = bucket.get(row[field], 0) + 1
        if row["declares_signature"]:
            summary["declares_signature"] += 1
    return summary


# ── Projecting the v3 population ────────────────────────────────────────────

def projection(reference_counts, population):
    """
    A POPULATION estimate, never a per-question claim.

    `reference_counts` is {V3_REQUIRED: n, V1_SUFFICIENT: n} measured over
    questions that already declare a signature. The candidates are drawn from
    the same bank and authored by the same pipeline, which makes that the
    defensible reference class — but it is still a rate applied to a set whose
    members are individually UNKNOWN, so the result is reported as an interval
    and is never written to a question.

    The interval is the Wald 95% band, widened to whole questions. It is here
    to make the uncertainty visible in the number itself rather than in a
    footnote nobody reads.
    """
    required = reference_counts.get(V3_REQUIRED, 0)
    sufficient = reference_counts.get(V1_SUFFICIENT, 0)
    determinable = required + sufficient
    if not determinable or not population:
        return None

    rate = required / determinable
    spread = 1.96 * ((rate * (1 - rate) / determinable) ** 0.5)
    low = max(0.0, rate - spread)
    high = min(1.0, rate + spread)
    return {
        "reference_population": determinable,
        "reference_rate": rate,
        "estimate": int(round(rate * population)),
        "low": int(low * population),
        "high": int(round(high * population + 0.5)),
        "basis": "measured shape distribution of questions that already "
                 "declare a signature; NOT a per-question verdict",
    }
