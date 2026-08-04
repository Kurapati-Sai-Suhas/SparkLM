"""
Authorization on /api/materials/ (M4 security fix, second endpoint).

`MaterialViewSet` carried `queryset = StudyMaterial.objects.all()`. The RAG
investigation flagged this as a metadata leak; measuring it showed it was
considerably worse. DRF resolves list, retrieve, update and destroy through
the same queryset, so an unscoped one meant any authenticated user could:

    LIST     -> 200, every material's title visible
    RETRIEVE -> 200
    PATCH    -> 200, title changed to 'PWNED'
    DELETE   -> 204, row gone

Read, write and destroy — not metadata. The fix reuses `accessible_materials`
from the RAG fix, which encodes the model StudyGroupViewSet already enforces.

`extracted_text` was never in `StudyMaterialSerializer`'s explicit field list
and still must not be; the assertions below pin that, because a later switch
to `fields = '__all__'` would publish every uploaded document's full text.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from common.tokens import issue_token_pair
from groups.models import StudyGroup, StudyMaterial

User = get_user_model()
SECRET_TEXT = "CONFIDENTIAL exam answers. " * 20

LIST = reverse("studymaterial-list")


def detail(pk):
    return reverse("studymaterial-detail", args=[pk])


def user(name):
    return User.objects.create_user(
        username=name, password="MatAuth#2026x", email=f"{name}@t.com"
    )


def auth(u):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(u).access_token}")
    return c


def group_with_material(owner, *, members=(), title=None, code="Z1"):
    grp = StudyGroup.objects.create(
        name=f"{owner.username} group", creator=owner, capacity=10, join_code=code
    )
    grp.members.add(owner, *members)
    material = StudyMaterial.objects.create(
        title=title or f"{owner.username} notes", uploaded_by=owner, study_group=grp,
        file=ContentFile(b"x", name="notes.txt"), extracted_text=SECRET_TEXT,
    )
    return grp, material


# ── read access ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReadAccess:
    def test_owner_sees_their_own_materials(self):
        alice = user("alice")
        _, material = group_with_material(alice, code="AA1")

        response = auth(alice).get(LIST)

        assert response.status_code == 200
        assert material.title in str(response.data)

    def test_group_members_see_shared_materials(self):
        alice, bob = user("alice2"), user("bob2")
        _, material = group_with_material(alice, members=[bob], code="AA2")

        response = auth(bob).get(LIST)

        assert response.status_code == 200
        assert material.title in str(response.data)

    def test_non_members_cannot_enumerate_metadata(self):
        """The reported gap. Mallory has her own group, so she is a real user."""
        alice, mallory = user("alice3"), user("mallory3")
        _, alices = group_with_material(alice, title="Alice secret notes", code="AA3")
        group_with_material(mallory, title="Mallory own notes", code="MM3")

        body = str(auth(mallory).get(LIST).data)

        assert "Alice secret notes" not in body
        assert "Mallory own notes" in body, "scoping must not hide the user's own data"

    def test_retrieve_of_an_inaccessible_material_is_404(self):
        alice, mallory = user("alice4"), user("mallory4")
        _, alices = group_with_material(alice, code="AA4")

        assert auth(mallory).get(detail(alices.pk)).status_code == 404


# ── write access — the part that was not metadata ────────────────────────

@pytest.mark.django_db
class TestWriteAccess:
    def test_a_non_member_cannot_rename_someone_elses_material(self):
        alice, mallory = user("alice5"), user("mallory5")
        _, material = group_with_material(alice, title="Original", code="AA5")

        response = auth(mallory).patch(
            detail(material.pk), {"title": "PWNED"}, format="multipart"
        )

        assert response.status_code == 404
        material.refresh_from_db()
        assert material.title == "Original", "another user's material was modified"

    def test_a_non_member_cannot_delete_someone_elses_material(self):
        alice, mallory = user("alice6"), user("mallory6")
        _, material = group_with_material(alice, code="AA6")

        response = auth(mallory).delete(detail(material.pk))

        assert response.status_code == 404
        assert StudyMaterial.objects.filter(pk=material.pk).exists(), (
            "another user's material was DELETED"
        )

    def test_the_owner_can_still_delete_their_own_material(self):
        alice = user("alice7")
        _, material = group_with_material(alice, code="AA7")

        assert auth(alice).delete(detail(material.pk)).status_code == 204
        assert not StudyMaterial.objects.filter(pk=material.pk).exists()

    def test_a_group_member_can_still_manage_shared_materials(self):
        """Materials are group-shared; scoping must not break collaboration."""
        alice, bob = user("alice8"), user("bob8")
        _, material = group_with_material(alice, members=[bob], code="AA8")

        response = auth(bob).patch(
            detail(material.pk), {"title": "Renamed by Bob"}, format="multipart"
        )

        assert response.status_code == 200
        material.refresh_from_db()
        assert material.title == "Renamed by Bob"


# ── extracted_text must stay hidden ──────────────────────────────────────

@pytest.mark.django_db
class TestExtractedTextIsNeverExposed:
    def test_the_list_payload_omits_extracted_text(self):
        alice = user("alice9")
        group_with_material(alice, code="AA9")

        body = str(auth(alice).get(LIST).data)

        assert "CONFIDENTIAL" not in body
        assert "extracted_text" not in body

    def test_the_detail_payload_omits_extracted_text(self):
        alice = user("alice10")
        _, material = group_with_material(alice, code="AB1")

        response = auth(alice).get(detail(material.pk))

        assert response.status_code == 200
        assert "extracted_text" not in response.data
        assert "CONFIDENTIAL" not in str(response.data)

    def test_the_serializer_field_list_is_explicit(self):
        """
        Guards the mechanism, not just the output. Switching to
        `fields = '__all__'` would publish every document's full text.
        """
        from groups.serializers import StudyMaterialSerializer

        fields = StudyMaterialSerializer.Meta.fields
        assert fields != "__all__", "explicit field list is what keeps text private"
        assert "extracted_text" not in fields


# ── filters and search must not become a side channel ────────────────────

@pytest.mark.django_db
class TestFiltersDoNotBypassScoping:
    def test_filtering_by_another_users_group_returns_nothing(self):
        alice, mallory = user("alice11"), user("mallory11")
        alices_group, alices = group_with_material(alice, title="Alice secret", code="AB2")
        group_with_material(mallory, code="MM2")

        response = auth(mallory).get(LIST, {"study_group": alices_group.pk})

        assert response.status_code == 200
        assert "Alice secret" not in str(response.data)

    def test_filtering_by_another_user_returns_nothing(self):
        alice, mallory = user("alice12"), user("mallory12")
        group_with_material(alice, title="Alice secret", code="AB3")
        group_with_material(mallory, code="MM3b")

        response = auth(mallory).get(LIST, {"uploaded_by": alice.pk})

        assert "Alice secret" not in str(response.data)

    def test_search_cannot_confirm_a_hidden_title(self):
        """
        Search is the obvious oracle: guessing a title and getting a hit would
        confirm it exists. Scoping runs before the filter backends.
        """
        alice, mallory = user("alice13"), user("mallory13")
        group_with_material(alice, title="Quantum Cryptography Notes", code="AB4")
        group_with_material(mallory, code="MM4")

        response = auth(mallory).get(LIST, {"search": "Quantum Cryptography"})

        assert response.status_code == 200
        assert "Quantum" not in str(response.data)


# ── the upload path ──────────────────────────────────────────────────────
#
# `get_queryset` does not govern create. Found while trying to bypass the
# read fix: `study_group` is read_only on the serializer, so the posted id
# went straight through as `study_group_id` with nothing checking it.

@pytest.mark.django_db
class TestUploadPath:
    def test_upload_still_works_and_is_immediately_visible(self):
        alice = user("alice14")
        grp = StudyGroup.objects.create(
            name="G", creator=alice, capacity=10, join_code="AB5"
        )
        grp.members.add(alice)
        client = auth(alice)

        created = client.post(
            LIST,
            {"title": "Fresh upload", "study_group": grp.pk,
             "file": ContentFile(b"hello", name="fresh.txt")},
            format="multipart",
        )

        assert created.status_code == 201
        assert "Fresh upload" in str(client.get(LIST).data)

    def test_the_creator_can_upload_without_being_in_members(self):
        """`creator` is in the predicate; a group owner is not always a member."""
        alice = user("alice15")
        grp = StudyGroup.objects.create(
            name="G", creator=alice, capacity=10, join_code="AB6"
        )  # deliberately no members.add

        response = auth(alice).post(
            LIST,
            {"title": "Owner upload", "study_group": grp.pk,
             "file": ContentFile(b"hello", name="o.txt")},
            format="multipart",
        )

        assert response.status_code == 201

    def test_a_non_member_cannot_upload_into_someone_elses_group(self):
        alice, mallory = user("alice16"), user("mallory16")
        alices_group, _ = group_with_material(alice, code="AB7")

        response = auth(mallory).post(
            LIST,
            {"title": "injected", "study_group": alices_group.pk,
             "file": ContentFile(b"evil", name="e.txt")},
            format="multipart",
        )

        assert response.status_code == 400
        assert not StudyMaterial.objects.filter(
            study_group=alices_group, uploaded_by=mallory
        ).exists(), "a file was planted in another user's group"

    def test_an_unknown_group_is_a_400_not_a_500(self):
        """Previously an IntegrityError from the FK, i.e. a server error."""
        alice = user("alice17")

        response = auth(alice).post(
            LIST,
            {"title": "x", "study_group": 999999,
             "file": ContentFile(b"x", name="x.txt")},
            format="multipart",
        )

        assert response.status_code == 400

    def test_a_non_numeric_group_id_is_a_400_not_a_500(self):
        alice = user("alice18")

        response = auth(alice).post(
            LIST,
            {"title": "x", "study_group": "not-an-id",
             "file": ContentFile(b"x", name="x.txt")},
            format="multipart",
        )

        assert response.status_code == 400

    def test_an_unknown_group_is_indistinguishable_from_an_inaccessible_one(self):
        """Otherwise upload becomes a group-id enumeration oracle."""
        alice, mallory = user("alice19"), user("mallory19")
        alices_group, _ = group_with_material(alice, code="AB8")
        client = auth(mallory)

        def upload(group_id):
            return client.post(
                LIST,
                {"title": "x", "study_group": group_id,
                 "file": ContentFile(b"x", name="x.txt")},
                format="multipart",
            )

        inaccessible, missing = upload(alices_group.pk), upload(999999)

        assert inaccessible.status_code == missing.status_code == 400
        assert inaccessible.data == missing.data


@pytest.mark.django_db
def test_options_does_not_confirm_that_a_material_exists():
    """
    OPTIONS was the one method still returning 200 to a non-member. It is
    DRF's static schema metadata and never calls get_object, so it says the
    same thing for a real id and an invented one — pinned here so a future
    custom `options()` cannot quietly turn it into an oracle.
    """
    alice, mallory = user("alice20"), user("mallory20")
    _, material = group_with_material(alice, title="Secret title", code="AB9")
    client = auth(mallory)

    real = client.options(detail(material.pk))
    invented = client.options(detail(999999))

    assert real.status_code == invented.status_code == 200
    assert real.data == invented.data
    assert "Secret title" not in str(real.data)
