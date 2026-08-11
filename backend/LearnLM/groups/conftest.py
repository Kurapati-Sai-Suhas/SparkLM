"""
Shared test support for the groups suite.

Exists because of one P2.7d change: `ReferenceSolution.is_active` no longer
defaults to True, and an active row must now be APPROVED with intact
provenance — enforced by database constraints, so there is no way to
shortcut it with a single `objects.create(...)` any more.

That is deliberate. It also means every test that wants a usable oracle has to
walk the real lifecycle, which is exactly the property we want: if the
lifecycle ever becomes awkward enough that tests want to bypass it, that is a
signal about the production API, not a reason for a back door.
"""

from django.contrib.auth import get_user_model

from groups.models import ReferenceSolution


def approved_reference(question, language="python", source_code="print(input())",
                       *, active=True, approver=None):
    """
    A reference solution driven through DRAFT → IN_REVIEW → APPROVED (→ ACTIVE).

    `active=False` yields the APPROVED-but-not-canonical state, which is a
    legitimate resting place: a reviewed implementation that has been
    superseded, or one approved in a language that is not this problem's
    canonical oracle language.
    """
    User = get_user_model()
    if approver is None:
        approver, _ = User.objects.get_or_create(
            username="reference-approver",
            defaults={"email": "reference-approver@sparklm.test"},
        )

    reference = ReferenceSolution.objects.create(
        question=question, language=language, source_code=source_code
    )
    reference.submit_for_review()
    reference.approve(by=approver)
    if active:
        reference.activate()
    return reference
