"""
Authorization on /api/visual-search/ (M4, final security fix).

`VisualSearchQueryView` filtered `Document.objects.filter(group_id=group_id)`
with nothing checking that the caller belonged to that group — and when
`group_id` was absent it fell through to `Document.objects.filter(
file_type='image')`, i.e. every image in the system. Measured before the fix,
as a user with no relationship to the victim at all:

    no group_id       -> 200  found=1  leaks-title=True
      {'title': 'Alice Private Diagram',
       'file_url': '/media/documents/d_4r6bkcR.jpg',
       'uploaded_by': 'va', ...}
    group_id=victim's -> 200  found=1  leaks-title=True

Three separate exposures in one response: the title, the uploader's username,
and a working `/media/` URL. MEDIA is served with no authentication, so that
URL was a permanent handle to the file bytes which outlived group membership
— `file_url` is now gone from the payload entirely.

`VisualSearchUploadView` had the write half of the same gap and is covered
here too: it resolved the group with an unscoped `StudyGroup.objects.get`,
so a non-member could index images into a victim's group and thereby inject
documents into that group's search results.

The fix reuses `accessible_groups` — the same predicate as StudyGroupViewSet,
accessible_materials, and the MaterialViewSet upload check. No new
permissions, no change to how similarity search itself works.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from common.tokens import issue_token_pair
from groups.models import Document, StudyGroup

User = get_user_model()

QUERY = reverse("visual-search-query")
UPLOAD = reverse("visual-search-upload")

SECRET_TITLE = "Alice Private Architecture Diagram"


def user(name):
    return User.objects.create_user(
        username=name, password="VisAuth#2026x", email=f"{name}@t.com"
    )


def auth(u):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(u).access_token}")
    return c


def image(name="q.jpg"):
    return ContentFile(b"fake-image-bytes", name=name)


def group_with_image(owner, *, members=(), title=SECRET_TITLE, code="V1"):
    grp = StudyGroup.objects.create(
        name=f"{owner.username} group", creator=owner, capacity=10, join_code=code
    )
    grp.members.add(owner, *members)
    doc = Document.objects.create(
        group=grp, uploaded_by=owner, title=title, file=image("d.jpg"),
        file_type="image", feature_vector=[0.1] * 512,
    )
    return grp, doc


@pytest.fixture(autouse=True)
def _stub_vectors(monkeypatch):
    """
    No CLIP forward pass in tests. `find_similar` is left real so the
    queryset restriction is what decides the results, not a stub.
    """
    monkeypatch.setattr(
        "groups.views.VectorSearchService.extract_vector", lambda img: [0.1] * 512
    )


def search(u, group_id=None, **extra):
    payload = {"image": image(), **extra}
    if group_id is not None:
        payload["group_id"] = group_id
    return auth(u).post(QUERY, payload, format="multipart")


# ── legitimate access ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLegitimateSearches:
    def test_the_owner_can_search_their_own_group(self):
        alice = user("alice")
        grp, doc = group_with_image(alice, code="VA1")

        response = search(alice, grp.pk)

        assert response.status_code == 200
        assert response.data["total_found"] == 1
        assert response.data["query_results"][0]["title"] == SECRET_TITLE

    def test_a_group_member_can_search_a_shared_group(self):
        alice, bob = user("alice2"), user("bob2")
        grp, _ = group_with_image(alice, members=[bob], code="VA2")

        response = search(bob, grp.pk)

        assert response.status_code == 200
        assert response.data["total_found"] == 1

    def test_the_creator_can_search_without_being_in_members(self):
        alice = user("alice3")
        grp = StudyGroup.objects.create(
            name="G", creator=alice, capacity=10, join_code="VA3"
        )  # deliberately no members.add
        Document.objects.create(
            group=grp, uploaded_by=alice, title="Owner doc", file=image("d.jpg"),
            file_type="image", feature_vector=[0.1] * 512,
        )

        assert search(alice, grp.pk).status_code == 200

    def test_results_are_restricted_to_the_requested_group(self):
        """
        The queryset restriction itself, as opposed to the 404 gate in front
        of it. Mallory is a legitimate member of her own group, so she passes
        the access check — the only thing keeping Alice's diagram out of her
        results is `filter(group=group)`.

        This test exists because mutation testing caught its absence: the
        first version of this file passed in full with the queryset left as
        `Document.objects.filter(file_type='image')`, since no test ever had
        two groups' documents in the database at the same time. Every
        assertion below is about a row that must NOT appear.
        """
        alice, mallory = user("alice_iso"), user("mallory_iso")
        group_with_image(alice, title=SECRET_TITLE, code="VC1")
        mgrp, _ = group_with_image(mallory, title="Mallory own diagram", code="VC2")

        response = search(mallory, mgrp.pk)

        assert response.status_code == 200
        assert [r["title"] for r in response.data["query_results"]] == [
            "Mallory own diagram"
        ]
        assert response.data["total_found"] == 1
        assert SECRET_TITLE not in str(response.data)

    def test_a_search_returns_only_the_requested_group_not_every_group_i_am_in(self):
        """
        group_id must narrow, not merely authorise. A user in two groups who
        asks about one of them must not receive the other's documents.
        """
        alice = user("alice_two")
        first, _ = group_with_image(alice, title="First group diagram", code="VC3")
        second, _ = group_with_image(alice, title="Second group diagram", code="VC4")

        response = search(alice, first.pk)

        assert response.status_code == 200
        assert [r["title"] for r in response.data["query_results"]] == [
            "First group diagram"
        ]
        assert "Second group diagram" not in str(response.data)

    def test_results_still_carry_score_and_uploader(self):
        """Scoping must not change the shape of a legitimate result."""
        alice = user("alice4")
        grp, doc = group_with_image(alice, code="VA4")

        result = search(alice, grp.pk).data["query_results"][0]

        assert result["document_id"] == doc.id
        assert result["uploaded_by"] == "alice4"
        assert 0.0 < result["similarity_score"] <= 1.0
        assert "uploaded_at" in result


# ── the vulnerability ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCrossGroupAccessIsDenied:
    def test_a_non_member_cannot_search_another_groups_images(self):
        """The exact attack. Before the fix: 200, with the title returned."""
        alice, mallory = user("alice5"), user("mallory5")
        alices_group, _ = group_with_image(alice, code="VA5")
        group_with_image(mallory, title="Mallory own", code="VM5")

        response = search(mallory, alices_group.pk)

        assert response.status_code == 404
        assert SECRET_TITLE not in str(response.data)

    def test_a_missing_group_id_is_rejected_not_globally_searched(self):
        """
        Before the fix this searched every image in the system. A user with
        images of their own is used deliberately: the endpoint must reject
        the request outright, not quietly fall back to their own groups.
        """
        alice, mallory = user("alice6"), user("mallory6")
        group_with_image(alice, code="VA6")
        group_with_image(mallory, title="Mallory own", code="VM6")

        response = search(mallory, None)

        assert response.status_code == 400
        assert SECRET_TITLE not in str(response.data)
        assert "Mallory own" not in str(response.data)

    def test_an_empty_group_id_is_rejected_too(self):
        """`if not group_id` must catch '' as well as absent."""
        alice, mallory = user("alice7"), user("mallory7")
        group_with_image(alice, code="VA7")

        response = search(mallory, "")

        assert response.status_code == 400
        assert SECRET_TITLE not in str(response.data)

    def test_no_permanent_file_url_is_returned_even_to_an_authorised_member(self):
        """
        UPDATED BY M5 PHASE 3 — the requirement changed, not the principle.

        M4 removed `file_url` outright because MEDIA was unauthenticated: any
        URL in this payload was a permanent, unrevocable handle to the bytes
        that outlived group membership. There was nothing to scope it to.

        Phase 3 gave the system a private bucket, so the payload now carries
        `thumbnail_url` — a signed URL that expires in minutes and is minted
        only for documents the caller has already been authorized to see.
        The invariant that mattered is unchanged and asserted here: the
        legacy permanent field is still gone, and with object storage
        configured no raw /media/ path is ever emitted.

        Without a bucket (CI and development) the helper degrades to the
        local path, which is only reachable because DEBUG serves it and is
        unreachable in production — so that case is excluded rather than
        pretended away.
        """
        from common.storage import object_storage_enabled

        alice = user("alice8")
        grp, _ = group_with_image(alice, code="VA8")

        result = search(alice, grp.pk).data["query_results"][0]

        assert "file_url" not in result, "the permanent legacy field is back"
        assert "thumbnail_url" in result

        if object_storage_enabled():
            assert "/media/" not in str(result)
            assert "X-Amz-Signature" in (result["thumbnail_url"] or ""), (
                "an unsigned URL was returned — that is a permanent handle"
            )


# ── enumeration ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNoEnumerationOracle:
    def test_an_inaccessible_group_is_indistinguishable_from_a_missing_one(self):
        alice, mallory = user("alice9"), user("mallory9")
        alices_group, _ = group_with_image(alice, code="VA9")

        inaccessible = search(mallory, alices_group.pk)
        missing = search(mallory, 999999)

        assert inaccessible.status_code == missing.status_code == 404
        assert inaccessible.data == missing.data

    def test_an_empty_accessible_group_differs_from_an_inaccessible_one(self):
        """
        The other direction: a group the caller IS in but which has no images
        must answer 200/0 results, so scoping is genuinely about membership
        and not about emptiness.
        """
        mallory = user("mallory10")
        own_empty = StudyGroup.objects.create(
            name="Empty", creator=mallory, capacity=10, join_code="VB1"
        )
        own_empty.members.add(mallory)

        response = search(mallory, own_empty.pk)

        assert response.status_code == 200
        assert response.data["total_found"] == 0

    def test_a_non_numeric_group_id_is_a_404_not_a_500(self):
        mallory = user("mallory11")

        assert search(mallory, "not-an-id").status_code == 404

    def test_a_non_numeric_top_k_is_a_400_not_a_500(self):
        """`int(request.data.get('top_k', 5))` was unguarded."""
        alice = user("alice12")
        grp, _ = group_with_image(alice, code="VB2")

        assert search(alice, grp.pk, top_k="abc").status_code == 400

    def test_top_k_is_bounded(self):
        alice = user("alice13")
        grp, _ = group_with_image(alice, code="VB3")

        assert search(alice, grp.pk, top_k=10 ** 9).status_code == 200


# ── the write half ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestUploadIsScopedToo:
    def _upload(self, u, group_id):
        return auth(u).post(
            UPLOAD,
            {"image": image(), "group_id": group_id, "title": "injected"},
            format="multipart",
        )

    def test_a_member_can_still_index_an_image(self):
        alice = user("alice14")
        grp, _ = group_with_image(alice, code="VB4")

        assert self._upload(alice, grp.pk).status_code == 201

    def test_a_non_member_cannot_index_into_another_group(self):
        alice, mallory = user("alice15"), user("mallory15")
        alices_group, _ = group_with_image(alice, code="VB5")

        response = self._upload(mallory, alices_group.pk)

        assert response.status_code == 404
        assert not Document.objects.filter(
            group=alices_group, uploaded_by=mallory
        ).exists(), "an image was planted in another user's search index"

    def test_upload_denial_is_indistinguishable_from_a_missing_group(self):
        alice, mallory = user("alice16"), user("mallory16")
        alices_group, _ = group_with_image(alice, code="VB6")

        inaccessible = self._upload(mallory, alices_group.pk)
        missing = self._upload(mallory, 999999)

        assert inaccessible.status_code == missing.status_code == 404
        assert inaccessible.data == missing.data
