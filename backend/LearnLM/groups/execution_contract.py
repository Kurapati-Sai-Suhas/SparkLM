"""
The execution contract: what a learner's source is guaranteed to execute as
(M2 P2.6).

The audit found the three reflection harnesses disagreeing with each other in
ways that produce wrong verdicts for correct code:

  * METHOD SELECTION differed per language and was never specified. Python took
    `dir(sol)[0]` (alphabetical), Java took the first of `getDeclaredMethods()`
    (the JLS guarantees NO order — non-deterministic between runs), JavaScript
    took definition order. A `Solution` with an ordinary helper method was
    graded by whichever method won that language's lottery.

  * OUTPUT diverged. Python and JavaScript emitted `[0,1]` via JSON; Java
    emitted `0 1` space-joined. One stored `expected_output` cannot satisfy
    both, so at most one language group could ever be graded correctly.

  * INPUT diverged. Python/JS ran `json.loads` per line while the seeded data
    is space-separated (`"2 7 11 15\\n9"` — see the generation prompt in
    ai_services). That parse fails, the whole blob is passed as ONE argument
    to a two-parameter method, and the resulting TypeError was printed to
    stdout and graded as Wrong Answer.

  * RUNTIME ERRORS were swallowed. All three caught the exception, printed it
    to stdout and exited 0, so Judge0 reported status_id 3 and the
    final-status scan for ids 7-12 could never fire.

── Versioning ───────────────────────────────────────────────────────────────

Fixing input/output means previously-correct stored outputs become wrong, so
those fixes are VERSIONED, not applied in place. `Question.execution_contract_version`
selects the harness:

    v1  exactly what shipped. Byte-identical. Every existing row defaults here,
        so no learner's grading changes when this lands.
    v2  the canonical contract below.

Questions carrying their own `hidden_wrapper_code` are governed by that
wrapper and are unaffected by either version — `wrapper_for()` already takes
precedence in `_build_executable`, and those questions define their own I/O
(seed_problems.py, for instance, is comma-separated).

Three fixes are NOT versioned because they cannot change a pass/fail outcome:
runtime-error classification (the submission fails either way; only the label
changes), added Java imports (strictly more code compiles), and explicit Judge0
limits (set to the documented defaults).

── The v2 contract ──────────────────────────────────────────────────────────

INPUT   One line per parameter. Within a line, whitespace-separated tokens.
        A line with several tokens is a sequence; a line with one token is a
        scalar. This is the format the seeded data already uses and the format
        Java's harness already reads.

OUTPUT  Space-separated tokens on one line. Chosen over JSON because C and C++
        cannot emit JSON without a library, and because the stored data and
        Java's harness already use it — the smaller reconciliation.

METHOD  Exactly one public method on `Solution`. Zero or several is an error
        reported to the learner, never a guess. Helpers must be `_private`
        (Python), `private` (Java) or `#private` (JavaScript).

ERRORS  Propagate. The process exits non-zero so Judge0 classifies the run and
        `GradingService` can distinguish runtime_error from wrong_answer.

── The v2 singleton-array divergence (Phase 1 M8) ──────────────────────────

v2's input rule above is "a line with several tokens is a sequence; a line with
one token is a scalar" — a rule about LENGTH. Python never actually applied it
as written: `_sparklm_parse` reads the parameter's annotation first, and a
parameter declared `list[int]` gets a list however many tokens the line holds.
The length rule is Python's fallback for an UNDECLARED parameter.

JavaScript had no annotations to read, so it applied that fallback as its only
rule. Measured by executing both harnesses:

    declared      line      python      javascript
    list[int]     ""        []          []            agree
    list[int]     "5"       [5]         5             DIVERGE
    list[int]     "1 2 3"   [1,2,3]     [1,2,3]       agree
    list[str]     "cat"     ["cat"]     "cat"         DIVERGE
    int           "5"       5           5             agree

So the defect is neither "the singleton case" nor JavaScript's tokeniser: it is
that one harness knew the declared type and the other did not. A one-element
line is merely where a length rule and a type rule first disagree — `["cat"]`
is the same defect with no digit in it.

The repair gives JavaScript the declared types Python already reads.
`v2_parameter_kinds` derives them server-side from the question's Python
starter — the signature that IS the declared contract, and the same source
`prepare_stdin` already reads to build a v3 envelope — and `render_v2`
substitutes them into the harness. The stored canonical representation is
untouched, and no rule anywhere asks how long a line is before deciding what
type it holds.
"""

import json
import os

from groups import execution_adapter, structural_types

CONTRACT_V1 = "v1"
CONTRACT_V2 = "v2"

# v3 is v1's harness driven by a CANONICAL STDIN ENVELOPE (M2 P2.7 follow-up).
#
# It introduces no new template. The v1 wrapper already splats a JSON array
# positionally, which is a complete calling convention, so `execution_adapter`
# builds the arguments server-side from the declared signature and hands the
# unchanged harness a JSON array. That is the whole difference:
#
#     v1   stdin "110"          -> wrapper guesses -> int 110
#     v3   stdin '["110"]'      -> wrapper splats  -> str "110"
#
# ZERO questions declare v3. Adoption is per question, after oracle
# verification — never a bulk migration, because switching a question's
# contract changes what its stored expected outputs mean.
CONTRACT_V3 = "v3"

#: Every version the harness can execute. A question naming anything else is a
#: data error and must not be graded — silently falling back to a default is
#: how a question ends up graded by a contract it was not written for.
KNOWN_CONTRACTS = (CONTRACT_V1, CONTRACT_V2, CONTRACT_V3)

DEFAULT_CONTRACT = CONTRACT_V1


class UnknownExecutionContract(Exception):
    """A question declares a contract version this harness cannot execute."""


# ── Judge0 resource policy ───────────────────────────────────────────────────
#
# OPT-IN, and unset by default. This is a correction made during P2.6's own
# adversarial review, and the reasoning matters more than the values.
#
# The first version of this phase sent cpu_time_limit=5.0 and
# memory_limit=128000 on every request, on the argument that they were Judge0
# CE's documented defaults and therefore changed nothing. That argument is
# wrong. Judge0 enforces `max_cpu_time_limit` server-side and REJECTS any
# submission asking for more, and the limit belongs to whichever deployment
# answers — ours is configured through JUDGE0_API_HOST, which render.yaml marks
# `sync: false`, so its value is UNKNOWN from this repository.
#
# The failure mode is total: a rejected submission raises for status, becomes
# GradingUnavailable, and every learner gets a 503. Trading a certain outage
# risk for reproducibility we cannot verify is a bad trade, so the fields are
# omitted entirely unless an operator sets them — which restores exactly the
# behaviour that shipped, while still allowing the limits to be pinned once
# someone can confirm what the deployment accepts.
def judge0_resource_limits():
    """
    The limit fields to merge into a Judge0 submission, or {} when unset.

    Empty is the default and means "whatever the instance is configured for",
    which is what production has always done.
    """
    limits = {}
    cpu = os.getenv("JUDGE0_CPU_TIME_LIMIT", "").strip()
    memory = os.getenv("JUDGE0_MEMORY_LIMIT", "").strip()
    if cpu:
        limits["cpu_time_limit"] = float(cpu)
    if memory:
        limits["memory_limit"] = int(memory)
    return limits


# ─────────────────────────────────────────────────────────────
# v2 harnesses
# ─────────────────────────────────────────────────────────────

# Exactly one public method, tokens in, space-separated tokens out, exceptions
# propagate. `inspect.signature` types the arguments where the learner
# annotated them; without annotations a multi-token line is a list and a
# single-token line is a scalar.
V2_PYTHON_WRAPPER = '''{structural_prelude_python}{user_code}

import sys as _sys
import inspect as _inspect


def _sparklm_public_methods(obj):
    return sorted(
        name for name in dir(obj)
        if not name.startswith("_") and callable(getattr(obj, name))
    )


def _sparklm_token(text):
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if text == "true":
        return True
    if text == "false":
        return False
    return text


def _sparklm_structural_kind(annotation):
    # Asked before anything else: a declared structure is BUILT, not
    # tokenised, and its stored form is a JSON array rather than a token line.
    text = str(annotation).lower()
    if annotation is _inspect.Parameter.empty:
        return None
    if "treenode" in text:
        return "tree"
    if "listnode" in text:
        return "linked_list"
    return None


def _sparklm_parse(line, annotation):
    kind = _sparklm_structural_kind(annotation)
    if kind is not None and "_sparklm_build_structure" in globals():
        return _sparklm_build_structure(kind, line)

    tokens = line.split()
    values = [_sparklm_token(t) for t in tokens]
    wants_sequence = annotation is not _inspect.Parameter.empty and (
        "list" in str(annotation).lower() or "sequence" in str(annotation).lower()
    )
    if wants_sequence:
        return values
    if len(values) == 1:
        return values[0]
    return values


#: The structural kind this question's method RETURNS, or "" for none. Read
#: server-side from the starter, because the empty structure and "no value"
#: are the SAME object here: `-> Optional[TreeNode]` returning None is the
#: empty tree and must print `[]`, while `-> None` returning None must print
#: nothing.
_SPARKLM_RETURN_KIND = "{return_kind}"


def _sparklm_render(value):
    # A returned structure is normalised to its canonical serialised form
    # BEFORE anything else looks at it, so grading never compares a language's
    # object identity. Rendered as compact JSON rather than space-joined
    # tokens: a tree carries `null` for an absent child, and space-joining
    # would make an absent child indistinguishable from a missing value.
    if "_sparklm_serialize_structure" in globals():
        structural = _sparklm_serialize_structure(value)
        if structural is not None:
            return _sparklm_json_module.dumps(structural, separators=(",", ":"))
        if value is None and _SPARKLM_RETURN_KIND:
            return "[]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(_sparklm_render(v) for v in value)
    if value is None:
        return ""
    return str(value)


def _sparklm_main():
    solution = Solution()
    names = _sparklm_public_methods(solution)
    if len(names) != 1:
        # Never guess. Reported on stderr with a non-zero exit so the learner
        # sees the real problem instead of an unexplained Wrong Answer.
        print(
            "Execution contract error: Solution must expose exactly one public "
            "method, found " + str(len(names))
            + ". Prefix helpers with an underscore.",
            file=_sys.stderr,
        )
        raise SystemExit(2)

    method = getattr(solution, names[0])
    parameters = list(_inspect.signature(method).parameters.values())
    lines = _sys.stdin.read().split(chr(10))

    args = []
    for index, parameter in enumerate(parameters):
        line = lines[index] if index < len(lines) else ""
        args.append(_sparklm_parse(line, parameter.annotation))

    print(_sparklm_render(method(*args)))


_sparklm_main()
'''

V2_JS_WRAPPER = '''{structural_prelude_javascript}{user_code}

function __sparklmPublicMethods(instance) {
    // Walks the prototype CHAIN, stopping before Object.prototype. Own-
    // prototype-only made JavaScript disagree with Python on inherited
    // methods: Python's dir() sees them and blocks the ambiguity, while this
    // did not see them and silently picked. Found in P2.6's own adversarial
    // review — the same learner code must not be treated differently by
    // language, which is the whole point of a shared contract.
    const names = new Set();
    let proto = Object.getPrototypeOf(instance);
    while (proto && proto !== Object.prototype) {
        for (const name of Object.getOwnPropertyNames(proto)) {
            if (name === 'constructor' || name.startsWith('_')) continue;
            // Read the descriptor rather than the value: touching a getter
            // would execute learner code during discovery.
            const descriptor = Object.getOwnPropertyDescriptor(proto, name);
            if (descriptor && typeof descriptor.value === 'function') {
                names.add(name);
            }
        }
        proto = Object.getPrototypeOf(proto);
    }
    return [...names].sort();
}

function __sparklmToken(text) {
    if (text === 'true') return true;
    if (text === 'false') return false;
    const asNumber = Number(text);
    return text !== '' && !Number.isNaN(asNumber) ? asNumber : text;
}

// The DECLARED kind of each parameter, in signature order, computed
// server-side by `v2_parameter_kinds` from the question's Python starter —
// the signature that is this question's contract in every language.
//
// JavaScript carries no annotations, so without this the harness had only the
// token count to go on and collapsed a one-element sequence to a scalar while
// Python, reading the annotation, did not. The vector is what makes the two
// harnesses answer the same question rather than two different ones.
const __sparklmKinds = {parameter_kinds};

function __sparklmParse(line, kind) {
    // A declared structure is BUILT, not tokenised, and its stored form is a
    // JSON array rather than a token line. Asked first, for the same reason
    // the sequence kind is asked before the length rule: the declared type
    // decides, never the shape of the text.
    if (typeof __sparklmBuildStructure === 'function'
            && (kind === '{tree_kind}' || kind === '{linked_list_kind}')) {
        return __sparklmBuildStructure(kind, line);
    }
    const values = line.split(/\\s+/).filter((t) => t.length > 0).map(__sparklmToken);
    // A declared sequence is a sequence at every length, INCLUDING one. Asked
    // before the length rule, never as an exception to it.
    if (kind === '{sequence_kind}') return values;
    // Undeclared: no type to honour, so the legacy shape guess stands — which
    // is exactly what Python does for a parameter with no annotation. Matching
    // Python's fallback matters as much as matching its rule.
    return values.length === 1 ? values[0] : values;
}

// The structural kind this question's method RETURNS, or '' for none. Read
// server-side from the starter, because the empty structure and "no value"
// are the same value here: a method declared to return a tree that returns
// null returned the EMPTY tree and must print `[]`.
const __sparklmReturnKind = '{return_kind}';

function __sparklmRender(value) {
    // A returned structure is normalised to its canonical serialised form
    // before anything else looks at it — compact JSON, not space-joined
    // tokens, because a tree carries `null` for an absent child and joining
    // would make an absent child indistinguishable from a missing value.
    if (typeof __sparklmSerializeStructure === 'function') {
        const structural = __sparklmSerializeStructure(value);
        if (structural !== null) return JSON.stringify(structural);
        if ((value === null || value === undefined) && __sparklmReturnKind) {
            return '[]';
        }
    }
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (Array.isArray(value)) return value.map(__sparklmRender).join(' ');
    if (value === null || value === undefined) return '';
    return String(value);
}

(function __sparklmMain() {
    const solution = new Solution();
    const names = __sparklmPublicMethods(solution);
    if (names.length !== 1) {
        process.stderr.write(
            `Execution contract error: Solution must expose exactly one public `
            + `method, found ${names.length}. Prefix helpers with an underscore.\\n`
        );
        process.exit(2);
    }

    const method = solution[names[0]].bind(solution);
    const lines = require('fs').readFileSync(0, 'utf8').split(String.fromCharCode(10));
    const args = [];
    for (let i = 0; i < method.length; i += 1) {
        args.push(__sparklmParse(i < lines.length ? lines[i] : '', __sparklmKinds[i]));
    }

    console.log(__sparklmRender(method(...args)));
})();
'''

# Java already emitted space-separated output and already read one line per
# parameter, so v2 changes only what was actually wrong: the undefined method
# order becomes a hard single-method requirement, the exception is rethrown so
# the run is classified, and the import list covers the packages a learner
# reasonably reaches for (java.util.* does NOT cover java.util.stream).
V2_JAVA_WRAPPER = '''import java.util.*;
import java.util.stream.*;
import java.util.function.*;
import java.math.*;
import java.lang.reflect.*;

public class Main {
    public static void main(String[] args) throws Exception {
        Scanner scanner = new Scanner(System.in);
        StringBuilder sb = new StringBuilder();
        while (scanner.hasNextLine()) {
            sb.append(scanner.nextLine()).append("\\n");
        }
        String input = sb.toString().trim();

        Solution sol = new Solution();
        List<Method> publicMethods = new ArrayList<>();
        for (Method m : Solution.class.getDeclaredMethods()) {
            if (Modifier.isPublic(m.getModifiers()) && !m.isSynthetic()) {
                publicMethods.add(m);
            }
        }
        if (publicMethods.size() != 1) {
            System.err.println("Execution contract error: Solution must expose "
                + "exactly one public method, found " + publicMethods.size()
                + ". Make helpers private.");
            System.exit(2);
        }
        Method targetMethod = publicMethods.get(0);

        Class<?>[] paramTypes = targetMethod.getParameterTypes();
        Object[] argsToPass = new Object[paramTypes.length];
        String[] inputs = input.split("\\\\n");
        for (int i = 0; i < paramTypes.length; i++) {
            String val = i < inputs.length ? inputs[i].trim() : "";
            Class<?> pType = paramTypes[i];
            if (pType == int.class || pType == Integer.class) {
                argsToPass[i] = Integer.parseInt(val);
            } else if (pType == long.class || pType == Long.class) {
                argsToPass[i] = Long.parseLong(val);
            } else if (pType == int[].class) {
                String[] parts = val.isEmpty() ? new String[0] : val.split("\\\\s+");
                int[] arr = new int[parts.length];
                for (int j = 0; j < parts.length; j++) arr[j] = Integer.parseInt(parts[j]);
                argsToPass[i] = arr;
            } else if (pType == double.class || pType == Double.class) {
                argsToPass[i] = Double.parseDouble(val);
            } else if (pType == boolean.class || pType == Boolean.class) {
                argsToPass[i] = Boolean.parseBoolean(val);
            } else if (pType == String[].class) {
                argsToPass[i] = val.isEmpty() ? new String[0] : val.split("\\\\s+");
            } else {
                argsToPass[i] = val;
            }
        }

        // NOT caught. An exception here must reach the JVM so the process exits
        // non-zero and Judge0 reports a runtime error instead of the harness
        // swallowing it into an empty stdout that grades as Wrong Answer.
        Object result = targetMethod.invoke(sol, argsToPass);
        System.out.println(render(result));
    }

    static String render(Object value) {
        if (value == null) return "";
        if (value instanceof boolean[]) {
            boolean[] a = (boolean[]) value;
            StringJoiner j = new StringJoiner(" ");
            for (boolean v : a) j.add(v ? "true" : "false");
            return j.toString();
        }
        if (value instanceof int[]) {
            return Arrays.stream((int[]) value).mapToObj(String::valueOf)
                    .collect(Collectors.joining(" "));
        }
        if (value instanceof long[]) {
            return Arrays.stream((long[]) value).mapToObj(String::valueOf)
                    .collect(Collectors.joining(" "));
        }
        if (value instanceof double[]) {
            return Arrays.stream((double[]) value).mapToObj(String::valueOf)
                    .collect(Collectors.joining(" "));
        }
        if (value instanceof Object[]) {
            StringJoiner j = new StringJoiner(" ");
            for (Object v : (Object[]) value) j.add(render(v));
            return j.toString();
        }
        if (value instanceof Iterable) {
            StringJoiner j = new StringJoiner(" ");
            for (Object v : (Iterable<?>) value) j.add(render(v));
            return j.toString();
        }
        return String.valueOf(value);
    }
}

{user_code}
'''

#: v2 harnesses by canonical language key. C and C++ are absent deliberately:
#: they are self-contained under every version — the learner writes a complete
#: program that reads stdin and prints the answer, so there is nothing to wrap.
V2_WRAPPERS = {
    "python": V2_PYTHON_WRAPPER,
    "java": V2_JAVA_WRAPPER,
    "javascript": V2_JS_WRAPPER,
}


# ─────────────────────────────────────────────────────────────
# Declared parameter kinds — the v2 input contract, server-side
# ─────────────────────────────────────────────────────────────

#: The kind vector's values. Deliberately coarser than `execution_adapter`'s
#: six: v2's input rule branches on sequence-or-not, and — since M5 — on
#: whether the parameter is a structure the platform can build. A vector
#: carrying `integer` vs `float` would imply a distinction no harness makes.
SEQUENCE_KIND = "sequence"
SCALAR_KIND = "scalar"

#: The two structural kinds, taken from `structural_types` rather than spelled
#: again here. A third name for the same concept is how the generator and its
#: validator came to disagree for 293 questions.
TREE_KIND = structural_types.TREE
LINKED_LIST_KIND = structural_types.LINKED_LIST
STRUCTURAL_KINDS = (TREE_KIND, LINKED_LIST_KIND)

#: Annotation spellings v2 treats as a sequence.
#:
#: EXACTLY the two the Python harness tests for — see `_sparklm_parse`'s
#: `"list" in ... or "sequence" in ...`. `execution_adapter._SEQUENCE_HINTS` is
#: wider (it also accepts `tuple`, `iterable`, `array`, `set[`) and using it
#: here would be an improvement that lands in ONE language: JavaScript would
#: read `tuple[int]` as a sequence while Python v2 still read it as a scalar,
#: which is the divergence this repair exists to remove, reintroduced from the
#: other side. The wider set belongs to v3, where both languages would read it
#: from the same server-built envelope.
V2_SEQUENCE_HINTS = ("list", "sequence")


def declares_sequence(annotation):
    """Whether one annotation makes v2 hand the parameter a sequence."""
    text = (annotation or "").lower()
    return bool(text) and any(hint in text for hint in V2_SEQUENCE_HINTS)


def v2_parameter_kinds(source):
    """
    The declared kind of each parameter, in signature order, from a Python
    starter. `[]` when the starter declares nothing readable.

    Pure. The Python starter is the source because it is the only one of the
    five that carries types, and because it is already what the contract treats
    as authoritative — `prepare_stdin` builds every v3 envelope from it. An
    empty vector is not a failure: it means "undeclared", and every harness
    already has a documented fallback for that.

    A structural type is checked FIRST. `Optional[TreeNode]` contains no
    sequence hint and would otherwise read as a scalar, but the difference
    that matters is that it is a structure the harness must BUILD, not a value
    it can tokenise.
    """
    signature = execution_adapter.declared_signature(source or "")
    if signature is None:
        return []
    _name, parameters = signature
    return [_kind_of(annotation) for _parameter, annotation in parameters]


def _kind_of(annotation):
    structural = structural_types.by_annotation(annotation)
    if structural is not None:
        return structural.kind
    return SEQUENCE_KIND if declares_sequence(annotation) else SCALAR_KIND


def v2_return_kind(source):
    """
    The structural kind the starter RETURNS, or "" when it returns no
    structure.

    Needed because the empty structure and "no value" are the same object in
    every one of these languages. `-> Optional[TreeNode]` returning None is the
    EMPTY TREE and must render as `[]`; `-> None` returning None is a method
    with no result and must render as nothing. Without the declaration there is
    no way to tell them apart, and an empty tree would be graded against `[]`
    while printing "".

    Read from the same starter as the parameter kinds, and injected into both
    harnesses, so the two languages cannot answer this differently.
    """
    signature = execution_adapter.declared_signature(source or "")
    if signature is None:
        return ""
    node, _is_method = execution_adapter.chosen_function(source or "")
    if node is None or node.returns is None:
        return ""
    structural = structural_types.by_annotation(
        execution_adapter._annotation_text(node.returns))
    return structural.kind if structural is not None else ""


# ─────────────────────────────────────────────────────────────
# Structural preludes (Phase 1 M5)
# ─────────────────────────────────────────────────────────────
#
# Injected ABOVE the learner's code, never below. Two reasons, both load-
# bearing:
#
#   1. `Optional[TreeNode]` is evaluated when the learner's class is DEFINED.
#      A node class defined after their code raises NameError before their
#      first line — exactly the M6 defect, from the harness side.
#   2. A learner who defines their own `TreeNode` must win. Theirs is defined
#      later, so it shadows the prelude's; the platform never overwrites the
#      code someone submitted.
#
# This is the layout q2's shipped per-question wrapper already uses — the only
# structural deserializer in production — so the ordering is precedent, not
# invention.
#
# Empty when no parameter needs it. A question with no structure gets a
# byte-identical harness to the one M8 left behind.

STRUCTURAL_PRELUDE_PYTHON = '''import json as _sparklm_json_module
from collections import deque as _sparklm_deque


class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right


class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next


def _sparklm_build_tree(values):
    if not values or values[0] is None:
        return None
    root = TreeNode(values[0])
    queue = _sparklm_deque([root])
    index = 1
    while queue and index < len(values):
        node = queue.popleft()
        for attribute in ("left", "right"):
            if index >= len(values):
                break
            value = values[index]
            index += 1
            if value is None:
                continue
            child = TreeNode(value)
            setattr(node, attribute, child)
            queue.append(child)
    return root


def _sparklm_build_list(values):
    head = None
    for value in reversed(values or []):
        head = ListNode(value, head)
    return head


def _sparklm_serialize_tree(root):
    if root is None:
        return []
    out = []
    queue = _sparklm_deque([root])
    while queue:
        node = queue.popleft()
        if node is None:
            out.append(None)
            continue
        out.append(node.val)
        queue.append(getattr(node, "left", None))
        queue.append(getattr(node, "right", None))
    while out and out[-1] is None:
        out.pop()
    return out


def _sparklm_serialize_list(head):
    out = []
    seen = set()
    node = head
    while node is not None:
        if id(node) in seen:
            raise ValueError("linked list contains a cycle")
        seen.add(id(node))
        out.append(node.val)
        node = getattr(node, "next", None)
    return out


def _sparklm_build_structure(kind, line):
    values = _sparklm_json_module.loads(line.strip() or "[]")
    if not isinstance(values, list):
        raise ValueError(
            "structural input must be a JSON array, got "
            + type(values).__name__)
    if kind == "tree":
        return _sparklm_build_tree(values)
    return _sparklm_build_list(values)


def _sparklm_serialize_structure(value):
    if isinstance(value, TreeNode):
        return _sparklm_serialize_tree(value)
    if isinstance(value, ListNode):
        return _sparklm_serialize_list(value)
    return None

'''

STRUCTURAL_PRELUDE_JS = '''class TreeNode {
    constructor(val = 0, left = null, right = null) {
        this.val = val;
        this.left = left;
        this.right = right;
    }
}

class ListNode {
    constructor(val = 0, next = null) {
        this.val = val;
        this.next = next;
    }
}

function __sparklmBuildTree(values) {
    if (!values.length || values[0] === null) return null;
    const root = new TreeNode(values[0]);
    const queue = [root];
    let head = 0;
    let index = 1;
    while (head < queue.length && index < values.length) {
        const node = queue[head];
        head += 1;
        for (const attribute of ['left', 'right']) {
            if (index >= values.length) break;
            const value = values[index];
            index += 1;
            if (value === null) continue;
            const child = new TreeNode(value);
            node[attribute] = child;
            queue.push(child);
        }
    }
    return root;
}

function __sparklmBuildList(values) {
    let head = null;
    for (let i = values.length - 1; i >= 0; i -= 1) {
        head = new ListNode(values[i], head);
    }
    return head;
}

function __sparklmSerializeTree(root) {
    if (root === null || root === undefined) return [];
    const out = [];
    const queue = [root];
    let head = 0;
    while (head < queue.length) {
        const node = queue[head];
        head += 1;
        if (node === null || node === undefined) {
            out.push(null);
            continue;
        }
        out.push(node.val);
        queue.push(node.left === undefined ? null : node.left);
        queue.push(node.right === undefined ? null : node.right);
    }
    while (out.length && out[out.length - 1] === null) out.pop();
    return out;
}

function __sparklmSerializeList(head) {
    const out = [];
    const seen = new Set();
    let node = head;
    while (node !== null && node !== undefined) {
        if (seen.has(node)) throw new Error('linked list contains a cycle');
        seen.add(node);
        out.push(node.val);
        node = node.next === undefined ? null : node.next;
    }
    return out;
}

function __sparklmBuildStructure(kind, line) {
    const values = JSON.parse(line.trim() === '' ? '[]' : line.trim());
    if (!Array.isArray(values)) {
        throw new Error('structural input must be a JSON array');
    }
    return kind === 'tree'
        ? __sparklmBuildTree(values)
        : __sparklmBuildList(values);
}

function __sparklmSerializeStructure(value) {
    if (value instanceof TreeNode) return __sparklmSerializeTree(value);
    if (value instanceof ListNode) return __sparklmSerializeList(value);
    return null;
}

'''

#: Java's structural contract. DEFINED, NOT EXECUTED — there is no JVM in this
#: environment and Judge0 is refusing requests, so nothing below has been
#: compiled. It is the same algorithm and the same canonical form as the two
#: above, and it is modelled on q2's shipped Java wrapper, which IS the
#: production precedent for building a ListNode from stored input. It is kept
#: here rather than omitted so the contract is written down; the Java v2
#: template does not yet consume it, and M7 is where it gets compiled.
STRUCTURAL_PRELUDE_JAVA = '''class TreeNode {
    int val;
    TreeNode left;
    TreeNode right;
    TreeNode() {}
    TreeNode(int val) { this.val = val; }
}

class ListNode {
    int val;
    ListNode next;
    ListNode() {}
    ListNode(int val) { this.val = val; }
    ListNode(int val, ListNode next) { this.val = val; this.next = next; }
}
'''

#: Which prelude a v2 template needs, by canonical language key. C and C++ are
#: absent and must stay absent: they are self-contained, the learner's program
#: reads the canonical text off stdin itself, and injecting a node class would
#: be the first step of turning them into reflection languages.
STRUCTURAL_PRELUDES = {
    "python": STRUCTURAL_PRELUDE_PYTHON,
    "javascript": STRUCTURAL_PRELUDE_JS,
}


def needs_structural_prelude(parameter_kinds):
    """Whether any declared parameter is a structure the harness must build."""
    return any(kind in STRUCTURAL_KINDS for kind in parameter_kinds)


def render_v2(template, user_code, parameter_kinds, return_kind=""):
    """
    A v2 harness with its declared kinds and the learner's source substituted.

    The kinds go in FIRST and the learner's code LAST. Source is untrusted text
    that may contain the literal `{parameter_kinds}`; substituting it last
    means it is never scanned for a placeholder — the same reason these
    templates have always been `.replace`d rather than `.format`ted, since a
    learner's `{` would otherwise raise inside the grader.
    """
    kinds = list(parameter_kinds)
    rendered = template.replace(
        "{parameter_kinds}", json.dumps(kinds, separators=(",", ":")))
    rendered = rendered.replace("{sequence_kind}", SEQUENCE_KIND)
    rendered = rendered.replace("{tree_kind}", TREE_KIND)
    rendered = rendered.replace("{linked_list_kind}", LINKED_LIST_KIND)
    rendered = rendered.replace("{return_kind}", return_kind or "")

    # Empty unless a parameter actually needs it, so a non-structural question
    # gets the harness it had before M5, byte for byte.
    #
    # The placeholder is language-specific rather than one shared name, so a
    # template can only ever receive ITS OWN prelude — there is no key to get
    # wrong and no way to inject Python source into the JavaScript harness.
    wanted = needs_structural_prelude(kinds) or return_kind in STRUCTURAL_KINDS
    for language, source in STRUCTURAL_PRELUDES.items():
        rendered = rendered.replace(
            "{structural_prelude_%s}" % language, source if wanted else "")

    return rendered.replace("{user_code}", user_code)


def contract_version(question):
    """
    The contract a question is graded under.

    Missing or blank means v1 — a question written before versioning existed
    must keep grading exactly as it did.
    """
    declared = (getattr(question, "execution_contract_version", "") or "").strip()
    if not declared:
        return DEFAULT_CONTRACT
    if declared not in KNOWN_CONTRACTS:
        raise UnknownExecutionContract(
            f"question {getattr(question, 'pk', '?')} declares execution "
            f"contract {declared!r}; this harness knows {KNOWN_CONTRACTS}"
        )
    return declared
