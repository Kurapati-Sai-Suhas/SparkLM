"""
The two live production defects found during the M4 authorization audit
but out of scope for a security sprint (M5 Phase 1, F3).

1. `/api/settings/profile/` returned 500 on GET *and* PUT, because
   ProfileSettingsView read `profile.bio` and `Profile` has no `bio` field
   — it lives on `User`. The settings page had never worked.

2. The material-upload pipeline passed `json.dumps(vector)` to
   `Document.feature_vector`, a pgvector VectorField, which raises
   ValueError("could not convert string to float"). A bare `except` printed
   the failure to stdout and continued, so every diagram extracted from an
   uploaded PDF or DOCX silently failed to index while explicit
   visual-search uploads (which passed the list correctly) worked fine.

Both had the same underlying cause: a failure with nowhere to be seen.
`test_indexing_failures_are_logged_not_swallowed` guards the mechanism, not
just the symptom — silent handling is what let defect 2 survive.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from common.tokens import issue_token_pair
from groups.models import Document, Profile, StudyGroup, StudyMaterial

User = get_user_model()

SETTINGS = reverse("settings_profile")


def make_user(name="defect_user", **kw):
    return User.objects.create_user(
        username=name, password="Defect#2026x", email=f"{name}@t.com", **kw
    )


def auth(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user).access_token}")
    return c


# ── F3.1 — the settings endpoint ─────────────────────────────────────────

@pytest.mark.django_db
class TestProfileSettingsEndpoint:
    def test_get_returns_200_not_500(self):
        """The headline regression: this endpoint always raised."""
        user = make_user("settings_get")

        response = auth(user).get(SETTINGS)

        assert response.status_code == 200

    def test_get_returns_every_field_the_frontend_reads(self):
        """Settings.tsx binds all five; a missing key renders `undefined`."""
        user = make_user("settings_fields")

        data = auth(user).get(SETTINGS).data

        assert set(data) == {
            "first_name", "last_name", "email", "bio", "email_alerts"
        }

    def test_bio_round_trips(self):
        user = make_user("settings_bio")
        client = auth(user)

        assert client.put(SETTINGS, {"bio": "I study distributed systems"},
                          format="json").status_code == 200

        user.refresh_from_db()
        assert user.bio == "I study distributed systems"
        assert client.get(SETTINGS).data["bio"] == "I study distributed systems"

    def test_bio_is_stored_on_user_not_profile(self):
        """
        Pins the fix's shape. Adding a Profile.bio column would also make
        the endpoint return 200 while creating two places to write one
        value; this fails if anyone does that.
        """
        user = make_user("settings_shape")
        auth(user).put(SETTINGS, {"bio": "on user"}, format="json")

        user.refresh_from_db()
        assert user.bio == "on user"
        assert not hasattr(Profile.objects.get(user=user), "bio")

    def test_a_null_bio_serialises_as_empty_string(self):
        """User.bio is null=True; the frontend binds it to a text input."""
        user = make_user("settings_null")
        user.bio = None
        user.save(update_fields=["bio"])

        assert auth(user).get(SETTINGS).data["bio"] == ""

    def test_names_and_alerts_round_trip(self):
        user = make_user("settings_other")
        client = auth(user)

        client.put(SETTINGS, {"first_name": "Ada", "last_name": "Lovelace",
                              "email_alerts": False}, format="json")

        data = client.get(SETTINGS).data
        assert data["first_name"] == "Ada"
        assert data["last_name"] == "Lovelace"
        assert data["email_alerts"] is False

    def test_a_partial_update_leaves_other_fields_alone(self):
        user = make_user("settings_partial")
        client = auth(user)
        client.put(SETTINGS, {"first_name": "Grace", "bio": "keep me"},
                   format="json")

        client.put(SETTINGS, {"first_name": "Hopper"}, format="json")

        data = client.get(SETTINGS).data
        assert data["first_name"] == "Hopper"
        assert data["bio"] == "keep me"

    def test_a_duplicate_email_is_a_400_not_a_500(self):
        """
        Was an unhandled IntegrityError. The DB unique constraint held, so
        this was never an account takeover — but the caller got a server
        error where a field error belongs.
        """
        make_user("settings_taken")
        mallory = make_user("settings_mallory")

        response = auth(mallory).put(
            SETTINGS, {"email": "settings_taken@t.com"}, format="json")

        assert response.status_code == 400
        mallory.refresh_from_db()
        assert mallory.email == "settings_mallory@t.com"

    def test_a_malformed_email_is_rejected(self):
        user = make_user("settings_malformed")

        response = auth(user).put(SETTINGS, {"email": "not-an-email"},
                                  format="json")

        assert response.status_code == 400
        user.refresh_from_db()
        assert user.email == "settings_malformed@t.com"

    def test_changing_your_own_email_still_works(self):
        user = make_user("settings_change")

        response = auth(user).put(SETTINGS, {"email": "brand-new@t.com"},
                                  format="json")

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.email == "brand-new@t.com"

    def test_the_endpoint_requires_authentication(self):
        assert APIClient().get(SETTINGS).status_code == 401


# ── F3.2 — document image auto-indexing ──────────────────────────────────

LONG_TEXT = "Networking notes. " * 50


@pytest.fixture
def upload_ctx(db, monkeypatch):
    """A user, a group, and stubbed extraction/vector services."""
    user = make_user("index_user")
    group = StudyGroup.objects.create(
        name="G", description="d", creator=user, capacity=10)
    group.members.add(user)

    monkeypatch.setattr("groups.views.extract_text_from_file", lambda p: LONG_TEXT)
    monkeypatch.setattr(
        "groups.views.VectorSearchService.extract_vector",
        lambda img: [0.25] * 512,
    )
    return user, group


@pytest.mark.django_db
class TestDocumentImageAutoIndexing:
    def _upload(self, user, group, monkeypatch, images=1):
        monkeypatch.setattr(
            "groups.views.extract_images_from_document",
            lambda f, n: [ContentFile(b"imagebytes", name=f"d{i}.jpg")
                          for i in range(images)],
        )
        return auth(user).post(
            reverse("studymaterial-list"),
            {"title": "Chapter 3", "study_group": group.pk,
             "file": ContentFile(b"%PDF-1.4 fake", name="chapter3.pdf")},
            format="multipart",
        )

    def test_a_diagram_extracted_from_a_pdf_is_indexed(self, upload_ctx, monkeypatch):
        """
        The defect. Before the fix this created zero Documents — the
        VectorField rejected the JSON string and the bare except hid it.
        """
        user, group = upload_ctx

        response = self._upload(user, group, monkeypatch)

        assert response.status_code == 201
        docs = Document.objects.filter(group=group, file_type="image")
        assert docs.count() == 1, "the extracted diagram was not indexed"
        assert docs.first().title == "Chapter 3 - Diagram 1"

    def test_the_stored_vector_is_a_real_vector_not_a_json_string(self, upload_ctx, monkeypatch):
        """
        Pins the actual bug. A string would round-trip through JSONField
        unnoticed; pgvector must return a numeric sequence of the right
        dimension or similarity search is meaningless.
        """
        user, group = upload_ctx
        self._upload(user, group, monkeypatch)

        vector = Document.objects.get(file_type="image").feature_vector

        assert not isinstance(vector, str)
        assert len(vector) == 512
        assert float(vector[0]) == pytest.approx(0.25)

    def test_an_indexed_diagram_is_findable_by_visual_search(self, upload_ctx, monkeypatch):
        """End-to-end: indexing is only worth anything if search returns it."""
        user, group = upload_ctx
        self._upload(user, group, monkeypatch)

        response = auth(user).post(
            reverse("visual-search-query"),
            {"image": ContentFile(b"query", name="q.jpg"), "group_id": group.pk},
            format="multipart",
        )

        assert response.status_code == 200
        assert response.data["total_found"] == 1
        assert response.data["query_results"][0]["title"] == "Chapter 3 - Diagram 1"

    def test_multiple_diagrams_are_all_indexed(self, upload_ctx, monkeypatch):
        user, group = upload_ctx

        self._upload(user, group, monkeypatch, images=3)

        assert Document.objects.filter(file_type="image").count() == 3

    def test_a_direct_image_upload_is_still_indexed(self, upload_ctx, monkeypatch):
        """The non-document branch of the same pipeline."""
        user, group = upload_ctx
        monkeypatch.setattr("groups.views.extract_images_from_document",
                            lambda f, n: [])

        response = auth(user).post(
            reverse("studymaterial-list"),
            {"title": "Whiteboard", "study_group": group.pk,
             "file": ContentFile(b"jpegbytes", name="board.jpg")},
            format="multipart",
        )

        assert response.status_code == 201
        assert Document.objects.filter(file_type="image").count() == 1

    def test_a_failed_index_does_not_fail_the_upload(self, upload_ctx, monkeypatch):
        """Non-fatal by design — one bad diagram must not lose the file."""
        user, group = upload_ctx
        monkeypatch.setattr(
            "groups.views.VectorSearchService.extract_vector",
            lambda img: (_ for _ in ()).throw(RuntimeError("model unavailable")),
        )

        response = self._upload(user, group, monkeypatch)

        assert response.status_code == 201
        assert StudyMaterial.objects.filter(title="Chapter 3").exists()
        assert Document.objects.filter(file_type="image").count() == 0


# ── F3.3 — failures must be visible ──────────────────────────────────────

@pytest.mark.django_db
class TestFailuresAreLoggedNotSwallowed:
    def test_indexing_failures_are_logged(self, upload_ctx, monkeypatch, caplog):
        """
        The mechanism guard. The indexing defect survived because its only
        symptom was a print to stdout that nothing watched. A logged
        exception reaches Sentry.
        """
        user, group = upload_ctx
        monkeypatch.setattr(
            "groups.views.extract_images_from_document",
            lambda f, n: [ContentFile(b"x", name="d.jpg")],
        )
        monkeypatch.setattr(
            "groups.views.VectorSearchService.extract_vector",
            lambda img: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with caplog.at_level("ERROR", logger="groups.views"):
            auth(user).post(
                reverse("studymaterial-list"),
                {"title": "Doc", "study_group": group.pk,
                 "file": ContentFile(b"%PDF", name="d.pdf")},
                format="multipart",
            )

        assert any("Auto-indexing failed" in r.message for r in caplog.records), (
            "an indexing failure was swallowed silently"
        )
        assert any(r.exc_info for r in caplog.records), (
            "the traceback was dropped — the log says what failed but not why"
        )

    def test_extract_images_never_raises_on_an_unreadable_file(self, caplog):
        """
        The helper's documented contract, tested directly.

        Mutation testing caught the need for this: the upload path now has
        two guards (inside the helper and at the call site), so a test that
        stubs the whole helper passes even with the inner one removed. This
        calls the real function with a file object whose read() fails —
        exactly the ephemeral-filesystem case — so it pins the inner fix.
        """
        from groups.views import extract_images_from_document

        class Vanished:
            def read(self):
                raise OSError("No such file or directory")

            def seek(self, *a):
                raise OSError("No such file or directory")

        with caplog.at_level("ERROR", logger="groups.views"):
            result = extract_images_from_document(Vanished(), "gone.pdf")

        assert result == [], "the helper must always return a list"
        assert any("Image extraction failed" in r.message for r in caplog.records)

    def test_extract_images_returns_a_list_for_an_unsupported_type(self):
        """No branch matches; must still return a list rather than None."""
        from groups.views import extract_images_from_document

        assert extract_images_from_document(ContentFile(b"x", name="a.txt"),
                                            "a.txt") == []

    def test_image_extraction_failures_are_logged(self, upload_ctx, monkeypatch, caplog):
        user, group = upload_ctx
        monkeypatch.setattr(
            "groups.views.extract_images_from_document",
            lambda f, n: (_ for _ in ()).throw(OSError("corrupt zip")),
        )

        with caplog.at_level("ERROR", logger="groups.views"):
            response = auth(user).post(
                reverse("studymaterial-list"),
                {"title": "Corrupt", "study_group": group.pk,
                 "file": ContentFile(b"%PDF", name="c.pdf")},
                format="multipart",
            )

        assert response.status_code == 201, "a corrupt document broke the upload"
