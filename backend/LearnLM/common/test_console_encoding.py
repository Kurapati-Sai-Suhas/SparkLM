"""
Console encoding for management commands (M2 P2.28).

The defect: Django's `OutputWrapper.write` hands text to `sys.stdout`, which
on Windows defaults to cp1252 and cannot encode the em dashes, arrows and
bullets these commands print. `question_approve` commits its `QuestionApproval`
row inside `transaction.atomic` and prints an em dash AFTERWARDS, so the
operator saw a traceback on a trust transition that had already succeeded.

These tests do not depend on running under Windows. A stream whose encoding
is cp1252 is constructed explicitly, so the regression is reproduced on any
platform — the alternative is a test that silently passes everywhere except
the one machine that has the bug.
"""

import io
import sys

import pytest

from common.console import use_utf8_console

#: Exactly the characters `question_approve` prints and cp1252 cannot encode.
#: Sourced from the command's own output, not invented for the test.
UNPRINTABLE_UNDER_CP1252 = "→"          # → alias / database
COMMAND_PUNCTUATION = "alias → db — bullet • ellipsis …"


@pytest.fixture(autouse=True)
def _no_inherited_encoding_override(monkeypatch):
    """
    The helper honours an explicit PYTHONIOENCODING, and this suite is often
    run with it set — the historical workaround for the very bug under test.
    Clear it by default so each test states its own premise; the test that
    asserts the override is honoured sets it back.
    """
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)


def _cp1252_stream():
    """A text stream that behaves like a legacy Windows console."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")


def _utf8_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="")


# ── The regression itself ──────────────────────────────────────────────────

def test_a_cp1252_stream_genuinely_cannot_encode_the_command_output():
    """
    Establishes the bug is real before asserting it is fixed. A test that
    only checks the fix would still pass if the premise evaporated.
    """
    stream = _cp1252_stream()

    with pytest.raises(UnicodeEncodeError):
        stream.write(UNPRINTABLE_UNDER_CP1252)


def test_reconfiguring_lets_the_same_stream_carry_the_same_text():
    stream = _cp1252_stream()

    reconfigured = use_utf8_console(streams=[("stdout", stream)])

    assert reconfigured == ["stdout"]
    stream.write(COMMAND_PUNCTUATION)          # must not raise
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_nothing_is_substituted_or_lost():
    """
    The fix must not be errors='replace' in disguise: every character has to
    survive, or the operator is reading altered output.
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="")

    use_utf8_console(streams=[("stdout", stream)])
    stream.write(COMMAND_PUNCTUATION)
    stream.flush()

    assert buffer.getvalue().decode("utf-8") == COMMAND_PUNCTUATION
    assert "?" not in buffer.getvalue().decode("utf-8")


# ── Restraint: what it must leave alone ───────────────────────────────────

def test_a_utf8_stream_is_left_untouched():
    stream = _utf8_stream()

    assert use_utf8_console(streams=[("stdout", stream)]) == []


def test_an_explicit_pythonioencoding_is_honoured(monkeypatch):
    """
    The documented workaround for this very bug was to set PYTHONIOENCODING.
    An operator who set it meant it; overriding would be the library deciding
    it knows better.
    """
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    stream = _cp1252_stream()

    assert use_utf8_console(streams=[("stdout", stream)]) == []


def test_a_stream_that_cannot_be_reconfigured_is_skipped_not_crashed():
    """pytest's capture object, a pipe wrapper, anything a test replaced."""
    class NotAStream:
        encoding = "cp1252"

    assert use_utf8_console(streams=[("stdout", NotAStream())]) == []


def test_a_stream_that_refuses_reconfiguration_never_raises():
    class Stubborn:
        encoding = "cp1252"

        def reconfigure(self, **kwargs):
            raise OSError("stream does not support reconfiguration")

    # A console fix must never be the reason a command fails.
    assert use_utf8_console(streams=[("stdout", Stubborn())]) == []


def test_a_none_stream_is_skipped():
    """`sys.stdout` is None under pythonw and some service hosts."""
    assert use_utf8_console(streams=[("stdout", None)]) == []


def test_both_standard_streams_are_covered():
    out, err = _cp1252_stream(), _cp1252_stream()

    assert use_utf8_console(
        streams=[("stdout", out), ("stderr", err)]) == ["stdout", "stderr"]


# ── The wiring ─────────────────────────────────────────────────────────────

def test_manage_py_installs_the_fix_before_any_command_can_write():
    """
    Fixing the stream after Django starts writing is too late, so the call
    must precede `execute_from_command_line`. Asserted on the source: this
    is an ordering property, and importing manage.py would run it.
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent
              / "manage.py").read_text(encoding="utf-8")

    assert "use_utf8_console()" in source
    assert source.index("use_utf8_console()") < source.index(
        "execute_from_command_line(sys.argv)")


def test_the_fix_is_not_windows_only_code():
    """
    Requirement I: cover the Windows regression without a platform branch.
    The helper reads encodings, never `sys.platform`, so it behaves
    identically everywhere and CI exercises the same path an operator does.
    """
    import inspect
    import re

    from common import console

    source = inspect.getsource(console)
    # Word boundaries, not substrings: `"nt" in source` also matches "entry
    # point". Short-token substring matching is exactly the mistake
    # `reseed_generation._is_exhausted` documents in its own docstring.
    patterns = [r"sys\.platform", r"platform\.system",
                           r"os\.name", r"\bnt\b", r"\bwin32\b"]

    # The patterns must be capable of matching something, or this test
    # passes for the wrong reason. An earlier revision of this file had
    # every `\b` mangled into a literal backspace, so no pattern could
    # match and the assertion below could never fail.
    assert re.search(r"\bnt\b", "on nt systems"), "pattern unmatchable"
    assert re.search(r"\bwin32\b", "the win32 api"), "pattern unmatchable"

    for platform_check in patterns:
        assert not re.search(platform_check, source), platform_check
