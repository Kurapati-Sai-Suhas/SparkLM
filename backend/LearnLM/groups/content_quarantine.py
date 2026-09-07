"""
Questions withheld from trust promotion pending content review (Phase 1 M9).

PURE. A registry and a lookup — no ORM, no I/O. Nothing here changes a
question; it only refuses to let one become trusted.

── What this is for ────────────────────────────────────────────────────────

A milestone that executes real content sometimes discovers that the CONTENT is
wrong rather than the code. That finding has to go somewhere. Left as prose in
a report it is lost by the next milestone; applied as a data fix it would mint
grading truth from an engineer's reading of a problem statement, which is
exactly what the oracle exists to prevent.

So it is recorded here, and the approval gate reads it. The question keeps
executing exactly as it does today — learners are not blocked from practising
it — but it cannot cross into ORACLE_VERIFIED until someone resolves the
finding and removes the entry.

── What this is NOT ────────────────────────────────────────────────────────

Not a fix, not a verdict, and not a way to make a readiness number look
better. A quarantined question's stored data, status, trust state and adaptive
eligibility are untouched. The entry is a REASON TO LOOK, addressed to a
person, with the evidence that produced it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Quarantine:
    question_id: int
    #: What was observed, in enough detail to re-check without this report.
    finding: str
    #: What has to happen for the entry to be removed.
    resolution: str
    #: The milestone that recorded it.
    found_in: str


#: The registry. Deliberately a literal: an entry is added by a person who
#: looked at the evidence, and removed by a person who resolved it.
QUARANTINED = (
    Quarantine(
        question_id=98,
        finding=(
            "hidden test case 4 stores stdin `[1,null,2,null,3]` with expected "
            "output `false`. That tree is 1 -> right 2 -> right 3, whose "
            "in-order traversal is 1, 2, 3 — strictly increasing, so it IS a "
            "valid binary search tree. The Python and JavaScript adapters "
            "independently produce `true` from operator-written references. "
            "The other three cases of this question reproduce exactly."),
        resolution=(
            "operator review of the stored expected output, then oracle "
            "re-execution. The answer key must be corrected by the "
            "authoritative flow, never by an execution milestone."),
        found_in="Phase 1 M5 pilot"),
)

_BY_ID = {entry.question_id: entry for entry in QUARANTINED}


def entry_for(question_id):
    """The quarantine entry for a question, or None."""
    try:
        return _BY_ID.get(int(question_id))
    except (TypeError, ValueError):
        return None


def is_quarantined(question_id):
    return entry_for(question_id) is not None


def blocker_for(question_id):
    """
    The approval blocker for a quarantined question, or None.

    Phrased as a refusal an operator can act on: what was seen, and what has
    to happen before approval is possible.
    """
    entry = entry_for(question_id)
    if entry is None:
        return None
    return (f"question {entry.question_id} is quarantined for content review "
            f"({entry.found_in}): {entry.finding} Resolution: "
            f"{entry.resolution}")
