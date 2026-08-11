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
"""

CONTRACT_V1 = "v1"
CONTRACT_V2 = "v2"

#: Every version the harness can execute. A question naming anything else is a
#: data error and must not be graded — silently falling back to a default is
#: how a question ends up graded by a contract it was not written for.
KNOWN_CONTRACTS = (CONTRACT_V1, CONTRACT_V2)

DEFAULT_CONTRACT = CONTRACT_V1


class UnknownExecutionContract(Exception):
    """A question declares a contract version this harness cannot execute."""


# ── Judge0 resource policy ───────────────────────────────────────────────────
#
# Previously unset, so whatever the Judge0 instance defaulted to applied and
# nothing in this repository recorded what that was. These are Judge0 CE's
# documented defaults, stated explicitly so the limit is visible, reviewable
# and changeable in one place rather than being a property of someone else's
# deployment.
DEFAULT_CPU_TIME_LIMIT = 5.0      # seconds
DEFAULT_MEMORY_LIMIT = 128000     # KB


# ─────────────────────────────────────────────────────────────
# v2 harnesses
# ─────────────────────────────────────────────────────────────

# Exactly one public method, tokens in, space-separated tokens out, exceptions
# propagate. `inspect.signature` types the arguments where the learner
# annotated them; without annotations a multi-token line is a list and a
# single-token line is a scalar.
V2_PYTHON_WRAPPER = '''{user_code}

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


def _sparklm_parse(line, annotation):
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


def _sparklm_render(value):
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

V2_JS_WRAPPER = '''{user_code}

function __sparklmPublicMethods(instance) {
    return Object.getOwnPropertyNames(Object.getPrototypeOf(instance))
        .filter((name) => name !== 'constructor' && !name.startsWith('_')
                && typeof instance[name] === 'function')
        .sort();
}

function __sparklmToken(text) {
    if (text === 'true') return true;
    if (text === 'false') return false;
    const asNumber = Number(text);
    return text !== '' && !Number.isNaN(asNumber) ? asNumber : text;
}

function __sparklmParse(line) {
    const values = line.split(/\\s+/).filter((t) => t.length > 0).map(__sparklmToken);
    return values.length === 1 ? values[0] : values;
}

function __sparklmRender(value) {
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
        args.push(__sparklmParse(i < lines.length ? lines[i] : ''));
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
