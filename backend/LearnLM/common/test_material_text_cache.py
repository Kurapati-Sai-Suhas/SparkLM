"""
Extracted-text caching for RAG (M4 Phase C).

Phase C's approved mechanism was a `DocumentChunk` table with a
`VectorField(512)` and "exact vector search filtered by material and owner".
Measurement showed that solves the wrong half of the problem:

    extract_text_from_file : 398.0 ms   <-- needs the FILE
    chunk (500/50)         :   0.5 ms
    join + 100k cap        :   0.0 ms   (RAGService rejoins every chunk)

Extraction is 99.9% of the per-request preparation, and `answer_with_rag`
concatenates all chunks and sends them to Groq — there is no retrieval step
for a vector index to accelerate. So the persisted artifact is the extracted
TEXT, not chunks, and no embedding is involved.

It also fixes a second problem: Render's filesystem is ephemeral, so the
uploaded file disappears on the next deploy and RAG broke for every material
older than the last restart. Text in Postgres survives.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from groups.models import StudyGroup, StudyMaterial
from groups.views import cache_extracted_text

User = get_user_model()
LONG_TEXT = "Networking notes. " * 50          # comfortably over the 50-char floor


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="rag_user", password="RagCache#2026x", email="rag@t.com"
    )


@pytest.fixture
def material(db, user):
    group = StudyGroup.objects.create(name="G", creator=user, capacity=10)
    m = StudyMaterial.objects.create(
        title="Notes", uploaded_by=user, study_group=group,
        file=ContentFile(b"%PDF-1.4 fake", name="notes.txt"),
    )
    return m


def auth(user):
    from common.tokens import issue_token_pair
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user).access_token}")
    return c


# ── the cache itself ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCacheExtractedText:
    def test_extracted_text_is_persisted(self, material, monkeypatch):
        monkeypatch.setattr("groups.views.extract_text_from_file", lambda p: LONG_TEXT)

        returned = cache_extracted_text(material)

        material.refresh_from_db()
        assert returned == LONG_TEXT
        assert material.extracted_text == LONG_TEXT

    def test_a_new_material_starts_empty(self, material):
        assert material.extracted_text == ""

    def test_extraction_failure_returns_empty_and_does_not_raise(self, material, monkeypatch):
        """
        Runs inside an upload AND inside a question. Neither may fail because
        a PDF is malformed or its file was swept away by a deploy.

        This test also caught a real defect before it shipped: the except
        block called `logger`, which was never defined in views.py — so a
        handled extraction failure would have raised NameError instead.
        """
        def boom(path):
            raise OSError("No such file or directory")

        monkeypatch.setattr("groups.views.extract_text_from_file", boom)

        assert cache_extracted_text(material) == ""
        material.refresh_from_db()
        assert material.extracted_text == ""

    def test_empty_extraction_is_not_persisted(self, material, monkeypatch):
        # Writing "" would be indistinguishable from "never tried", and would
        # stop the lazy path from retrying once the file is readable again.
        monkeypatch.setattr("groups.views.extract_text_from_file", lambda p: "")

        cache_extracted_text(material)

        material.refresh_from_db()
        assert material.extracted_text == ""

    def test_only_the_text_column_is_written(self, material, monkeypatch):
        """`update_fields` so a concurrent title edit is not clobbered."""
        monkeypatch.setattr("groups.views.extract_text_from_file", lambda p: LONG_TEXT)

        StudyMaterial.objects.filter(pk=material.pk).update(title="Renamed elsewhere")
        cache_extracted_text(material)

        material.refresh_from_db()
        assert material.title == "Renamed elsewhere"
        assert material.extracted_text == LONG_TEXT


# ── the read path ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRagUsesTheCache:
    def _ask(self, user, material):
        return auth(user).post(
            reverse("ai-doubt-rag"),
            {"materialId": material.pk, "question": "What is TCP?"},
            format="json",
        )

    def test_a_cached_material_never_touches_the_file(self, user, material, monkeypatch):
        """
        The headline behaviour. Once cached, RAG must not read from disk —
        which is what makes it survive the file being deleted.
        """
        material.extracted_text = LONG_TEXT
        material.save(update_fields=["extracted_text"])

        def must_not_be_called(path):
            raise AssertionError("read the file despite having cached text")

        monkeypatch.setattr("groups.views.extract_text_from_file", must_not_be_called)
        monkeypatch.setattr(
            "groups.views.RAGService.answer_with_rag",
            lambda q, chunks: {"answer": "TCP is a protocol.", "citations": []},
        )

        response = self._ask(user, material)
        assert response.status_code == 200
        assert response.data["mode"] == "rag"

    def test_rag_still_works_when_the_file_is_gone(self, user, material, monkeypatch):
        """
        The ephemeral-filesystem case, which is the state of every material
        older than the last deploy. Before Phase C this returned 400.
        """
        material.extracted_text = LONG_TEXT
        material.save(update_fields=["extracted_text"])
        material.file.delete(save=True)          # file gone, row remains

        monkeypatch.setattr(
            "groups.views.RAGService.answer_with_rag",
            lambda q, chunks: {"answer": "still works", "citations": []},
        )

        assert self._ask(user, material).status_code == 200

    def test_an_uncached_material_extracts_once_then_caches(self, user, material, monkeypatch):
        calls = []

        def counting(path):
            calls.append(path)
            return LONG_TEXT

        monkeypatch.setattr("groups.views.extract_text_from_file", counting)
        monkeypatch.setattr(
            "groups.views.RAGService.answer_with_rag",
            lambda q, chunks: {"answer": "a", "citations": []},
        )

        assert self._ask(user, material).status_code == 200
        assert self._ask(user, material).status_code == 200

        assert len(calls) == 1, (
            f"extracted {len(calls)} times; the lazy path must cache after the first"
        )

    def test_an_unreadable_uncached_material_still_returns_400(self, user, material, monkeypatch):
        """No regression: the pre-Phase-C behaviour for a genuinely unreadable file."""
        monkeypatch.setattr(
            "groups.views.extract_text_from_file",
            lambda p: (_ for _ in ()).throw(OSError("gone")),
        )

        response = self._ask(user, material)
        assert response.status_code == 400
        assert "Could not extract text" in response.data["error"]

    def test_chunking_still_happens_on_the_cached_text(self, user, material, monkeypatch):
        """
        Chunking costs 0.5 ms and RAGService rejoins the chunks anyway, so it
        is left exactly as it was — this change is about extraction only.
        """
        material.extracted_text = LONG_TEXT
        material.save(update_fields=["extracted_text"])
        seen = {}

        def capture(question, chunks):
            seen["n"] = len(chunks)
            return {"answer": "a", "citations": []}

        monkeypatch.setattr("groups.views.RAGService.answer_with_rag", capture)

        response = self._ask(user, material)
        assert response.status_code == 200
        assert seen["n"] >= 1
        assert response.data["chunks_searched"] == seen["n"]


# ── upload path ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_upload_caches_text_while_the_file_definitely_exists(user, monkeypatch):
    """
    Upload is the only window where the file is guaranteed present. After the
    next deploy it is gone, so extracting later is best-effort at best.
    """
    monkeypatch.setattr("groups.views.extract_text_from_file", lambda p: LONG_TEXT)
    monkeypatch.setattr("groups.views.extract_images_from_document", lambda f, n: [])
    group = StudyGroup.objects.create(name="G2", creator=user, capacity=10)

    response = auth(user).post(
        reverse("studymaterial-list"),
        {"title": "Notes", "study_group": group.pk,
         "file": ContentFile(b"hello world", name="notes.txt")},
        format="multipart",
    )

    assert response.status_code == 201
    assert StudyMaterial.objects.get(pk=response.data["id"]).extracted_text == LONG_TEXT


@pytest.mark.django_db
def test_a_failed_extraction_does_not_fail_the_upload(user, monkeypatch):
    monkeypatch.setattr(
        "groups.views.extract_text_from_file",
        lambda p: (_ for _ in ()).throw(OSError("malformed")),
    )
    monkeypatch.setattr("groups.views.extract_images_from_document", lambda f, n: [])
    group = StudyGroup.objects.create(name="G3", creator=user, capacity=10)

    response = auth(user).post(
        reverse("studymaterial-list"),
        {"title": "Broken", "study_group": group.pk,
         "file": ContentFile(b"x", name="broken.pdf")},
        format="multipart",
    )

    assert response.status_code == 201, "a malformed document broke the upload"
