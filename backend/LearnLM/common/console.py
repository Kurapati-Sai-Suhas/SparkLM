"""
Console encoding for management commands (M2 P2.28).

── The defect ──────────────────────────────────────────────────────────────

Django's `OutputWrapper.write` hands text straight to `sys.stdout`. On
Windows that stream defaults to the console code page — cp1252 — which cannot
represent the punctuation this project's commands actually print: em dashes,
arrows, bullets, ellipses. Writing one raises

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

and the command dies mid-output.

`question_approve` is the case that matters. Its `QuestionApproval` row is
committed inside `transaction.atomic`, and the summary line printed
afterwards contains an em dash. So on Windows the approval **succeeds, is
committed, and then the operator is shown a traceback** — the one outcome
guaranteed to make someone re-run a trust transition they already completed.
The append-only guard catches the duplicate, so no data is corrupted, but the
operator has no confirmation their approval landed.

34 management commands print non-ASCII, including the whole trust chain:
approve, promote, status, demote, reference_create, remediate_*. Patching
them one at a time would fix `question_approve` and leave the operator
blocked one step later at `question_promote`.

── Why the fix belongs here and not in the commands ────────────────────────

The text is not the bug. The em dash is correct English and the arrow is the
clearest way to render "alias → database"; replacing them with ASCII would
hide the defect rather than fix it, and would have to be redone every time
someone writes a sentence naturally.

The bug is the encoding of one boundary — the process's stdout — so that is
where it is fixed, once, for every command. Nothing about what the commands
say changes; only how the bytes are produced.

`manage.py` is the single entry point every management command passes
through, which is why it calls this. Production is unaffected: Render runs
Linux, where the streams are already UTF-8, so this is a no-op there and
merely makes Windows behave the same way.

── What this deliberately does not do ──────────────────────────────────────

It does not use `errors="replace"` or `"backslashreplace"`. UTF-8 can encode
every character these commands emit, so nothing is substituted and no output
is lossy. If a stream cannot be reconfigured at all, it is left exactly as it
was — a console fix must never be the reason a command fails.

An explicit `PYTHONIOENCODING` is honoured rather than overridden: an
operator who set it meant it, and the historical workaround for this very
bug was to set it to utf-8.
"""

import os
import sys

#: Encodings that already represent everything the commands print.
_ALREADY_FINE = {"utf8", "utf8mb4", "utf16", "utf32"}


def _normalise(encoding):
    return (encoding or "").lower().replace("-", "").replace("_", "")


def use_utf8_console(streams=None):
    """
    Make stdout/stderr encode as UTF-8 when they are not already able to.

    Returns the list of stream names actually reconfigured, so a caller — or
    a test — can tell what happened rather than inferring it. Never raises.
    """
    if os.environ.get("PYTHONIOENCODING"):
        # The operator has stated an encoding. Overriding it would be the
        # library deciding it knows better, and this is the exact variable
        # people were told to set to work around the bug.
        return []

    if streams is None:
        streams = (("stdout", sys.stdout), ("stderr", sys.stderr))

    reconfigured = []
    for name, stream in streams:
        if stream is None:
            continue
        if _normalise(getattr(stream, "encoding", None)) in _ALREADY_FINE:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Not a TextIOWrapper — a pytest capture object, a pipe wrapper,
            # something replaced by a test. Leave it alone.
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:                                     # noqa: BLE001
            # A stream that refuses to be reconfigured is not a reason to
            # stop the command the operator actually asked for.
            continue
        reconfigured.append(name)
    return reconfigured
