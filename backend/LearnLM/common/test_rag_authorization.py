"""
Authorization on /api/ai/doubt/rag/ (M4 Phase C security fix).

RAGDoubtView resolved its material with `StudyMaterial.objects.get(id=...)`
and no access check, so any authenticated user could pass any material id and
have that document's full text sent to the LLM and returned to them. Proven
before the fix: a user who was not a member of the owning group received the
document's confidential content with HTTP 200.

The fix reuses the model StudyGroupViewSet already enforces
(`Q(members=user) | Q(creator=user)`), applied to the material's group, plus
the uploader. No new authorization concept, no API change.

`test_the_original_exploit_is_closed` is the regression test for the exact
attack; if it ever passes with content again, the scoping has been removed.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from common.tokens import issue_token_pair
from groups.models import StudyGroup, StudyMaterial
from groups.views import accessible_materials

User = get_user_model()

# Long enough to clear RAGDoubtView's 50-character extraction floor, so a
# rejection can only come from authorization.
SECRET = "CONFIDENTIAL: private exam answers. " * 20


def user(name):
    return User.objects.create_user(
        username=name, password="RagAuth#2026x", email=f"{name}@t.com"
    )


def auth(u):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(u).access_token}")
    return c


def group_with_material(owner, *, members=(), name="G", code="Z1"):
    grp = StudyGroup.objects.create(
        name=name, creator=owner, capacity=10, join_code=code
    )
    grp.members.add(owner, *members)
    material = StudyMaterial.objects.create(
        title=f"{owner.username} notes", uploaded_by=owner, study_group=grp,
        file=ContentFile(b"x", name="notes.txt"), extracted_text=SECRET,
    )
    return grp, material


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """No network. Records whatever context reached the model."""
    seen = {}

    def spy(question, chunks):
        seen["context"] = "\n\n".join(chunks)
        return {"answer": "answered", "citations": []}

    monkeypatch.setattr("groups.views.RAGService.answer_with_rag", spy)
    return seen


def ask(u, material_id):
    return auth(u).post(
        reverse("ai-doubt-rag"),
        {"materialId": material_id, "question": "Summarise this"},
        format="json",
    )


# ── the vulnerability ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_the_original_exploit_is_closed(_stub_llm):
    """
    The exact attack, verbatim. Before the fix this returned 200 with
    'CONFIDENTIAL: private exam answers...' in the answer.
    """
    alice, mallory = user("alice"), user("mallory")
    _, material = group_with_material(alice, name="Alice private", code="AAA1")

    response = ask(mallory, material.pk)

    assert response.status_code == 404
    assert "context" not in _stub_llm, (
        "another user's document text reached the LLM — the scoping is gone"
    )


@pytest.mark.django_db
def test_cross_user_access_is_denied_even_with_a_valid_token(_stub_llm):
    alice, mallory = user("alice2"), user("mallory2")
    # Mallory has a group of her own, so she is a legitimate user — this is
    # about the material, not about being unauthenticated.
    group_with_material(mallory, name="Mallory own", code="MMM1")
    _, alices = group_with_material(alice, name="Alice own", code="AAA2")

    assert ask(mallory, alices.pk).status_code == 404


@pytest.mark.django_db
def test_denial_is_indistinguishable_from_a_missing_material(_stub_llm):
    """
    403 would confirm the id exists and turn this into an enumeration oracle.
    """
    alice, mallory = user("alice3"), user("mallory3")
    _, alices = group_with_material(alice, code="AAA3")

    forbidden = ask(mallory, alices.pk)
    missing = ask(mallory, 999999)

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.data == missing.data


# ── legitimate access is unchanged ───────────────────────────────────────

@pytest.mark.django_db
def test_the_owner_can_still_read_their_own_material(_stub_llm):
    alice = user("alice4")
    _, material = group_with_material(alice, code="AAA4")

    response = ask(alice, material.pk)

    assert response.status_code == 200
    assert response.data["mode"] == "rag"
    assert "CONFIDENTIAL" in _stub_llm["context"], "the owner's own text must reach the LLM"


@pytest.mark.django_db
def test_a_group_member_can_read_a_material_shared_with_them(_stub_llm):
    """Materials are group-shared; membership is the point of a study group."""
    alice, bob = user("alice5"), user("bob5")
    group_with_material(alice, members=[bob], code="AAA5")
    material = StudyMaterial.objects.get(uploaded_by=alice)

    assert ask(bob, material.pk).status_code == 200


@pytest.mark.django_db
def test_the_uploader_keeps_access_after_leaving_the_group(_stub_llm):
    """
    `uploaded_by` is in the filter so a user cannot be locked out of a
    document they uploaded themselves.
    """
    alice, carol = user("alice6"), user("carol6")
    grp = StudyGroup.objects.create(
        name="Shared", creator=carol, capacity=10, join_code="AAA6"
    )
    grp.members.add(carol, alice)
    material = StudyMaterial.objects.create(
        title="Alice upload", uploaded_by=alice, study_group=grp,
        file=ContentFile(b"x", name="n.txt"), extracted_text=SECRET,
    )
    grp.members.remove(alice)

    assert ask(alice, material.pk).status_code == 200


# ── the sibling AI endpoints ─────────────────────────────────────────────
#
# Found by trying to bypass the RAG fix rather than by reading the diff.
# AIFlashcardView (retired in M1/P1.2-B), AIDoubtView and AIQuizView each
# carried the identical
# unscoped `StudyMaterial.objects.get(id=material_id)`, and each returns
# something derived from the document. /api/ai/doubt/ is the same "ask a
# question about a document" feature without retrieval, so scoping only the
# RAG variant achieved nothing — measured before the fix, all three returned
# 200 with another user's confidential text in the response.

@pytest.mark.django_db
class TestSiblingAiEndpointsAreScopedToo:
    @pytest.fixture(autouse=True)
    def _stub_ai(self, monkeypatch):
        """Every generator echoes the text it was given, so a leak is visible."""
        monkeypatch.setattr("groups.views.extract_text_from_file", lambda p: SECRET)
        monkeypatch.setattr(
            "groups.views.AIService.get_answer", lambda q, text: f"From: {text[:40]}")
        monkeypatch.setattr(
            "groups.views.AIService.generate_quiz",
            lambda text, num_questions=5: [{"question": text[:40]}])

    @pytest.mark.parametrize(
        "url_name, payload",
        [("ai-doubt", {"question": "what is the answer to Q1"}),
         ("ai-quiz", {})],
    )
    def test_a_non_member_gets_404_and_no_content(self, url_name, payload):
        alice = user(f"alice_s_{url_name}")
        mallory = user(f"mallory_s_{url_name}")
        _, material = group_with_material(alice, code=url_name[:6])
        group_with_material(mallory, name="Mallory own", code=f"m{url_name[:5]}")

        response = auth(mallory).post(
            reverse(url_name), {"materialId": material.pk, **payload}, format="json"
        )

        assert response.status_code == 404
        assert "CONFIDENTIAL" not in str(response.data), (
            f"{url_name} returned another user's document content"
        )

    @pytest.mark.parametrize(
        "url_name, payload",
        [("ai-doubt", {"question": "summarise"}),
         ("ai-quiz", {})],
    )
    def test_the_owner_is_unaffected(self, url_name, payload):
        alice = user(f"owner_s_{url_name}")
        _, material = group_with_material(alice, code=f"o{url_name[:5]}")

        response = auth(alice).post(
            reverse(url_name), {"materialId": material.pk, **payload}, format="json"
        )

        assert response.status_code == 200

    def test_denial_is_indistinguishable_from_a_missing_material(self):
        """The 404 body was already 'File not found' for both — keep it that way."""
        alice, mallory = user("alice_or"), user("mallory_or")
        _, material = group_with_material(alice, code="ORC1")
        client = auth(mallory)

        forbidden = client.post(
            reverse("ai-doubt"), {"materialId": material.pk, "question": "q"}, format="json")
        missing = client.post(
            reverse("ai-doubt"), {"materialId": 999999, "question": "q"}, format="json")

        assert forbidden.status_code == missing.status_code == 404
        assert forbidden.data == missing.data


# ── the queryset itself ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestAccessibleMaterials:
    def test_the_owner_resolves_to_exactly_one_row(self):
        """
        The ordinary case — uploader, creator and member are the same person,
        so all three OR'd conditions match. `.get()` must still resolve.

        Honest scope note: mutation testing showed this passes with
        `.distinct()` removed, because a user appears at most once in a
        group's `members`. So this asserts the property (`.get()` resolves),
        not the mechanism — claiming it proves `.distinct()` is necessary
        would be a test that cannot fail for its stated reason.
        """
        alice = user("alice7")
        _, material = group_with_material(alice, code="AAA7")

        assert accessible_materials(alice).get(id=material.pk) == material
        assert accessible_materials(alice).filter(id=material.pk).count() == 1

    def test_excludes_other_users_materials(self):
        alice, mallory = user("alice8"), user("mallory8")
        _, alices = group_with_material(alice, code="AAA8")

        assert not accessible_materials(mallory).filter(id=alices.pk).exists()

    def test_includes_group_shared_materials(self):
        alice, bob = user("alice9"), user("bob9")
        _, material = group_with_material(alice, members=[bob], code="AAA9")

        assert accessible_materials(bob).filter(id=material.pk).exists()
