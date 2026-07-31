"""
groups.wrapper_contract — does a starter template satisfy its question's
custom execution wrapper?

Exists because of a shipped production defect (M2-D1). Question 3307's Java
wrapper calls

    ListNode res = sol.addTwoNumbers(l1, l2);

while its starter template declared

    class Solution { public String addTwoNumbers(String data) }

Every new user receives that question first, so every new user who chose Java
got a compile error from the platform's own template. Nothing could detect it:
the wrapper and the template are separate JSON columns, and neither the
serializer nor the grader compares them.

Scope, deliberately narrow: this compares the CONTRACT SURFACE only -- method
name, parameter count, return type, and helper classes the wrapper uses but
does not declare. It is not a compiler and does not attempt type inference.
The defect class it prevents is "the template cannot possibly satisfy the
wrapper", which is exactly what shipped.

Pure functions over strings: no DB access, no I/O, so the checks are
exercised directly by unit tests.
"""

import re

# Types every wrapper may reference without the user declaring them.
_JAVA_BUILTINS = {
    "String", "Scanner", "Integer", "Double", "Boolean", "Long", "Character",
    "System", "Main", "Object", "Exception", "Math", "Arrays", "List",
    "ArrayList", "Map", "HashMap", "Set", "HashSet", "StringBuilder",
}


def wrapper_call_contract(wrapper: str):
    """
    What the wrapper demands of the user's code.

    Returns {"method", "arg_count", "return_type", "required_types"} or None
    when no `sol.method(...)` call site is present (nothing to verify).
    """
    if not isinstance(wrapper, str) or not wrapper.strip():
        return None

    # `Type name = sol.method(a, b);`  (typed result)
    match = re.search(r"(\w+)\s+\w+\s*=\s*sol\.(\w+)\s*\(([^)]*)\)", wrapper)
    return_type = None
    if match:
        return_type, method, raw_args = match.group(1), match.group(2), match.group(3)
    else:
        # `sol.method(a, b);` (result unused / printed inline)
        match = re.search(r"\bsol\.(\w+)\s*\(([^)]*)\)", wrapper)
        if not match:
            return None
        method, raw_args = match.group(1), match.group(2)

    args = [a for a in raw_args.split(",") if a.strip()]

    declared = set(re.findall(r"\b(?:class|interface|enum)\s+(\w+)", wrapper))
    used = set(re.findall(r"new\s+([A-Z]\w*)\s*\(", wrapper))
    used |= set(re.findall(r"\b([A-Z]\w*)\s+\w+\s*[=;)]", wrapper))
    required_types = sorted(used - declared - _JAVA_BUILTINS)

    return {
        "method": method,
        "arg_count": len(args),
        "return_type": return_type,
        "required_types": required_types,
    }


# Languages whose method declarations carry an explicit receiver parameter
# that the caller never passes. Counting it produced a false "parameter count
# mismatch" on two real questions during the first audit run: a Python
# `def m(self, l1, l2)` correctly serves a `sol.m(l1, l2)` call.
_IMPLICIT_RECEIVER = {"python": ("self", "cls")}


def template_declaration(template: str, method: str, language: str = "java"):
    """
    The user-facing declaration of `method` in the template, or None if the
    template never declares it.

    `language` selects the declaration syntax; the two supported shapes are
    Python's `def m(...)` and the C-family `Type m(...)`.
    """
    if not isinstance(template, str) or not template.strip():
        return None

    lang = (language or "java").lower()
    declared_types = sorted(set(re.findall(r"\b(?:class|interface|enum)\s+(\w+)", template)))

    if lang == "python":
        match = re.search(r"def\s+" + re.escape(method) + r"\s*\(([^)]*)\)", template)
        if not match:
            return None
        params = [p.strip() for p in match.group(1).split(",") if p.strip()]
        # Drop the implicit receiver: it is declared but never passed.
        if params and params[0].split(":")[0].split("=")[0].strip() in _IMPLICIT_RECEIVER["python"]:
            params = params[1:]
        return {
            # Python return annotations are optional and not comparable to a
            # dynamically typed call site, so no return type is reported.
            "return_type": None,
            "param_count": len(params),
            "param_types": [p.split(":")[0].strip() for p in params],
            "declared_types": declared_types,
        }

    match = re.search(
        r"(?:public\s+|private\s+|protected\s+|static\s+)*"
        r"([\w<>\[\]]+)\s+" + re.escape(method) + r"\s*\(([^)]*)\)",
        template,
    )
    if not match:
        return None

    params = [p.strip() for p in match.group(2).split(",") if p.strip()]
    return {
        "return_type": match.group(1),
        "param_count": len(params),
        "param_types": [p.split()[0] for p in params if p.split()],
        "declared_types": declared_types,
    }


def check_pair(wrapper: str, template: str, language: str = "java"):
    """
    Compare one wrapper/template pair.

    Returns a list of human-readable mismatch strings; empty means compatible
    (or that there was nothing to check).
    """
    contract = wrapper_call_contract(wrapper)
    if contract is None:
        return []  # no custom call site: the generic harness applies

    declaration = template_declaration(template, contract["method"], language)
    if declaration is None:
        return [
            f"template does not declare the method {contract['method']}() the wrapper calls"
        ]

    problems = []
    if declaration["param_count"] != contract["arg_count"]:
        problems.append(
            f"parameter count mismatch: wrapper passes {contract['arg_count']}, "
            f"template accepts {declaration['param_count']}"
        )
    if (
        contract["return_type"]
        and declaration["return_type"]
        and declaration["return_type"] != contract["return_type"]
    ):
        problems.append(
            f"return type mismatch: wrapper assigns to {contract['return_type']}, "
            f"template returns {declaration['return_type']}"
        )
    # Helper-class declaration is a static-language concern; Python templates
    # conventionally document the shape in a comment instead.
    if (language or "java").lower() != "python":
        missing = [t for t in contract["required_types"] if t not in declaration["declared_types"]]
        if missing:
            problems.append(
                "template must declare helper type(s) the wrapper uses but never "
                f"defines: {', '.join(missing)}"
            )
    return problems
