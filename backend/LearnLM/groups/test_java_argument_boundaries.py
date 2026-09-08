"""
Java argument boundaries (Phase 1 M16).

THE DEFECT
    The Java v2 harness read stdin with a Scanner, appended "\\n" after every
    line, and then called `.trim()` on the whole blob before splitting it into
    arguments. `trim()` strips BOTH ends — and a LEADING empty line is exactly
    how the canonical contract spells "the first argument is an empty list".
    Deleting it shifted every argument one place left:

        stdin "\\n[0]"    python [[],[0]]   javascript [[],[0]]   java [[0],[]]

    Python and JavaScript split the raw blob and never had this.

WHY trim() WAS THERE
    Not carelessness: the read loop appends "\\n" after the LAST line too, so
    without something the blob always ended in a separator. `trim()` removed
    it — and took the leading one with it. The fix removes exactly the one
    separator the loop added, which is the only one that was ever spurious.

    The PER-ARGUMENT `inputs[i].trim()` is untouched. That is the trim doing
    real work — whitespace around a JSON token is harmless under the contract
    and must keep being ignored — and a test below pins it.

HOW IT WAS FOUND
    M14's read-only migration parity validation, on q21 (Merge Two Sorted
    Lists), whose second stored case merges an empty list with [0]. 21 of 22
    candidates agreed across all three languages; q21 did not.

WHAT THESE TESTS DO
    Compile and run the REAL harness with javac/java 21 and compare the
    ARGUMENTS ACTUALLY BUILT against Python and JavaScript on the same
    canonical fixture. Behavioural, not source-text matching: a mutation that
    restores `trim()` must fail because the arguments come out wrong, not
    because a string stopped appearing in a template.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from groups import execution_contract as ec

JAVAC, JAVA, NODE = (shutil.which("javac"), shutil.which("java"),
                     shutil.which("node"))

needs_java = pytest.mark.skipif(
    not (JAVAC and JAVA),
    reason=("no local JDK on this shell's PATH — winget writes the user PATH, "
            "so a shell started before M10's install will not see it"))
needs_node = pytest.mark.skipif(not NODE, reason="no local node")


# ═════════════════════════════════════════════════════════════
# One probe shape, three languages: echo EVERY argument
# ═════════════════════════════════════════════════════════════
#
# Echoing all arguments is what makes an argument SHIFT visible. A probe that
# returned only the first would have reported `[0]` for both the broken and
# the fixed harness on some inputs.

def probe_sources(kinds):
    names = ", ".join(f"a{i}" for i in range(len(kinds)))

    python_echo = ", ".join(
        f"_ser(a{i})" if k in ec.STRUCTURAL_KINDS else f"a{i}"
        for i, k in enumerate(kinds))
    python = (
        "import json\n"
        "def _ser(n):\n"
        "    if n is None: return []\n"
        "    if hasattr(n, 'left'): return _sparklm_serialize_tree(n)\n"
        "    return _sparklm_serialize_list(n)\n"
        f"class Solution:\n    def probe(self, {names}):\n"
        f"        return json.dumps([{python_echo}], separators=(',', ':'))\n")

    js_echo = ", ".join(
        f"__ser(a{i})" if k in ec.STRUCTURAL_KINDS else f"a{i}"
        for i, k in enumerate(kinds))
    javascript = (
        "function __ser(n) {\n"
        "  if (n === null || n === undefined) return [];\n"
        "  if ('left' in n) return __sparklmSerializeTree(n);\n"
        "  return __sparklmSerializeList(n);\n}\n"
        f"class Solution {{\n  probe({names}) "
        f"{{ return JSON.stringify([{js_echo}]); }}\n}}\n")

    jtype = {"tree": "TreeNode", "linked_list": "ListNode"}
    jparams = ", ".join(
        f"{jtype[k]} a{i}" if k in ec.STRUCTURAL_KINDS else f"Object a{i}"
        for i, k in enumerate(kinds))
    jecho = ' + "," + '.join(
        (f"SparkLMStructures.serializeTree(a{i})" if k == "tree"
         else f"SparkLMStructures.serializeList(a{i})" if k == "linked_list"
         else f"String.valueOf(a{i})")
        for i, k in enumerate(kinds))
    java = (f"class Solution {{\n  public String probe({jparams}) "
            f'{{ return "[" + {jecho} + "]"; }}\n}}\n')

    return {"python": python, "javascript": javascript, "java": java}


def execute(language, kinds, stdin, returns=""):
    source = probe_sources(kinds)[language]
    executable = ec.render_v2(ec.V2_WRAPPERS[language], source, kinds, returns)
    if language == "java":
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Main.java").write_text(executable,
                                                    encoding="utf-8")
            build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                                   capture_output=True, text=True, timeout=300)
            assert build.returncode == 0, build.stderr[:400]
            proc = subprocess.run([JAVA, "-cp", directory, "Main"],
                                  input=stdin, capture_output=True, text=True,
                                  timeout=90)
    else:
        runner = ([sys.executable, "-c"] if language == "python"
                  else [NODE, "-e"])
        proc = subprocess.run(runner + [executable], input=stdin,
                              capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:400]
    return json.loads(proc.stdout.strip())


# ═════════════════════════════════════════════════════════════
# THE regression
# ═════════════════════════════════════════════════════════════

@needs_java
def test_a_leading_empty_argument_is_preserved():
    """
    The exact input that exposed the defect — q21's second stored case, an
    empty list merged with [0]. Java built `[[0],[]]`: the argument shifted.
    """
    assert execute("java", ["linked_list", "linked_list"], "\n[0]") == \
        [[], [0]]


@needs_java
def test_the_shift_does_not_happen_with_three_arguments_either():
    """
    A shift of one is easy to mistake for a value bug. With three arguments
    and an empty first, a shift is unmistakable.
    """
    assert execute("java", ["linked_list", "scalar", "scalar"],
                   "\n5\n7") == [[], 5, 7]


@needs_java
def test_an_empty_argument_in_the_middle_is_preserved():
    assert execute("java", ["linked_list", "linked_list", "linked_list"],
                   "[1]\n\n[3]") == [[1], [], [3]]


# ═════════════════════════════════════════════════════════════
# Everything that already worked must keep working
# ═════════════════════════════════════════════════════════════

BOUNDARY_CASES = (
    ("empty second argument", ["linked_list", "linked_list"], "[0]\n",
     [[0], []]),
    ("two non-empty arguments", ["linked_list", "linked_list"], "[1]\n[2]",
     [[1], [2]]),
    ("three arguments", ["tree", "scalar", "scalar"], "[1,2]\n5\n7",
     [[1, 2], 5, 7]),
    ("scalar then array", ["scalar", "linked_list"], "5\n[1,2]",
     [5, [1, 2]]),
    ("array then scalar", ["linked_list", "scalar"], "[1,2]\n5",
     [[1, 2], 5]),
    ("empty array", ["linked_list"], "[]", [[]]),
    ("singleton array", ["linked_list"], "[5]", [[5]]),
    ("multi-element array", ["linked_list"], "[1,2,3]", [[1, 2, 3]]),
    ("tree canonical", ["tree"], "[1,null,2,null,3]",
     [[1, None, 2, None, 3]]),
    ("tree with absent children", ["tree"], "[5,1,4,null,null,3,6]",
     [[5, 1, 4, None, None, 3, 6]]),
    ("negative and duplicate values", ["linked_list"], "[-1,-1,2]",
     [[-1, -1, 2]]),
    ("completely empty stdin", ["linked_list"], "", [[]]),
    ("both arguments empty", ["linked_list", "linked_list"], "\n", [[], []]),
)


@pytest.mark.parametrize("label,kinds,stdin,expected", BOUNDARY_CASES,
                         ids=[row[0] for row in BOUNDARY_CASES])
@needs_java
def test_java_argument_boundaries(label, kinds, stdin, expected):
    assert execute("java", kinds, stdin) == expected


@needs_java
def test_whitespace_around_a_token_is_still_ignored():
    """
    The per-argument trim, which is the one that was always correct. Removing
    it along with the blob-level trim would have traded one defect for
    another.
    """
    assert execute("java", ["linked_list"], "  [1,2]  ") == [[1, 2]]
    assert execute("java", ["linked_list", "linked_list"],
                   "  [1]  \n  [2]  ") == [[1], [2]]


@needs_java
def test_malformed_structural_input_still_fails_clearly():
    """
    A representation the contract does not use must refuse visibly, not be
    guessed at. Unchanged by M16, and worth pinning: a parser change is
    exactly when a refusal quietly becomes a best effort.
    """
    source = probe_sources(["linked_list"])["java"]
    executable = ec.render_v2(ec.V2_WRAPPERS["java"], source,
                              ["linked_list"], "")
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "Main.java").write_text(executable, encoding="utf-8")
        build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[:400]
        proc = subprocess.run([JAVA, "-cp", directory, "Main"],
                              input="1 2 3", capture_output=True, text=True,
                              timeout=90)

    assert proc.returncode != 0
    assert "must be a JSON array" in proc.stderr


# ═════════════════════════════════════════════════════════════
# Cross-language parity, executed
# ═════════════════════════════════════════════════════════════

PARITY_CASES = (
    ("leading empty first argument", ["linked_list", "linked_list"], "\n[0]"),
    ("empty second argument", ["linked_list", "linked_list"], "[0]\n"),
    ("empty middle argument", ["linked_list", "linked_list", "linked_list"],
     "[1]\n\n[3]"),
    ("two non-empty", ["linked_list", "linked_list"], "[1]\n[2]"),
    ("three arguments", ["tree", "scalar", "scalar"], "[1,2]\n5\n7"),
    ("scalar then array", ["scalar", "linked_list"], "5\n[1,2]"),
    ("array then scalar", ["linked_list", "scalar"], "[1,2]\n5"),
    ("empty array", ["linked_list"], "[]"),
    ("singleton array", ["linked_list"], "[5]"),
    ("multi array", ["linked_list"], "[1,2,3]"),
    ("tree canonical", ["tree"], "[1,null,2,null,3]"),
    ("tree absent children", ["tree"], "[5,1,4,null,null,3,6]"),
    ("whitespace around token", ["linked_list"], "  [1,2]  "),
    ("completely empty stdin", ["linked_list"], ""),
    ("both arguments empty", ["linked_list", "linked_list"], "\n"),
)


@pytest.mark.parametrize("label,kinds,stdin", PARITY_CASES,
                         ids=[row[0] for row in PARITY_CASES])
@needs_java
@needs_node
def test_all_three_languages_build_the_same_arguments(label, kinds, stdin):
    """
    Semantic comparison of the ARGUMENTS BUILT — not of source text, and not
    of language-specific object identity. This is the check that caught the
    defect, so it is the check that keeps it caught.
    """
    outputs = {language: execute(language, kinds, stdin)
               for language in ("python", "javascript", "java")}

    assert outputs["python"] == outputs["javascript"] == outputs["java"], \
        outputs


# ═════════════════════════════════════════════════════════════
# The structural contract is unchanged
# ═════════════════════════════════════════════════════════════

def test_m16_changed_only_how_the_blob_is_terminated():
    """
    The per-argument trim survives, and the blob-level one is gone. Named so
    that a future edit removing the per-argument trim has to argue with this
    test.

    Comments are stripped before searching: the harness DOCUMENTS the old
    defect by quoting `sb.toString().trim()` in a comment, and a raw substring
    search cannot tell an explanation from a directive. The M2 provenance test
    and the M4 stub-generator test were both caught by exactly this and fixed
    the same way.
    """
    code = "\n".join(line for line in ec.V2_JAVA_WRAPPER.splitlines()
                     if not line.strip().startswith("//"))

    assert "inputs[i].trim()" in code          # per-argument, still correct
    assert "sb.toString().trim()" not in code  # blob-level, removed
    assert 'input.endsWith("\\n")' in code


def test_the_execution_models_are_untouched():
    from common import languages

    assert {lang.key for lang in languages.REGISTRY
            if lang.self_contained} == {"c", "cpp"}
    for key in ("c", "cpp"):
        assert ec.V2_WRAPPERS.get(key) is None
    assert "{structural_prelude_java}" in ec.V2_JAVA_WRAPPER
