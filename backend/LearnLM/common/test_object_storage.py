"""
Durable object storage and authorized media access (M5 Phase 3, F1).

Two problems, one primitive:

* Render's filesystem is ephemeral, so every deploy discarded uploaded
  files. Download, preview and the vision path broke on each restart, and
  four pre-Phase-C materials lost their bytes permanently.
* `/media/` was served with no authentication. The M4 audit proved a leaked
  URL was a permanent, unrevocable handle — which is why visual search had
  `file_url` deleted rather than scoped.

A private bucket plus short-lived signed URLs fixes both. The tests below
are mostly about the SECOND property, because that is the one that can
regress silently: signing is what makes a URL work, but authorization is
what decides whether one is minted at all, and only tests keep those two
facts attached to each other.

Everything here runs without credentials or network. `object_storage_enabled()`
is False in CI, so the signing path degrades to a local URL — which is
exactly the configuration developers run, and therefore the one most likely
to hide an authorization mistake. The authorization assertions are written
so they hold in BOTH configurations.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from rest_framework.test import APIClient

from common.storage import _sanitise, object_storage_enabled, signed_url
from common.tokens import issue_token_pair
from groups.models import Document, StudyGroup, StudyMaterial

User = get_user_model()


def make_user(name):
    return User.objects.create_user(
        username=name, password="Storage#2026x", email=f"{name}@t.com"
    )


def auth(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user).access_token}")
    return c


@pytest.fixture
def world(db):
    owner = make_user("st_owner")
    member = make_user("st_member")
    outsider = make_user("st_outsider")

    group = StudyGroup.objects.create(
        name="Owner group", description="d", creator=owner, capacity=10)
    group.members.add(owner, member)

    material = StudyMaterial.objects.create(
        title="Secret Notes", uploaded_by=owner, study_group=group,
        file=ContentFile(b"pdf-bytes", name="secret.pdf"))

    doc = Document.objects.create(
        group=group, uploaded_by=owner, title="Secret Diagram",
        file=ContentFile(b"img", name="diagram.jpg"), file_type="image",
        feature_vector=[0.1] * 512)

    # A real user with a group of their own, so denials are about the
    # resource rather than about being a nobody.
    own = StudyGroup.objects.create(
        name="Outsider", description="d", creator=outsider, capacity=10)
    own.members.add(outsider)

    return dict(owner=owner, member=member, outsider=outsider,
                group=group, material=material, doc=doc)


def download_url(pk):
    return reverse("studymaterial-download", args=[pk])


# ── the authorization contract ───────────────────────────────────────────

@pytest.mark.django_db
class TestDownloadAuthorization:
    def test_the_owner_can_get_a_download_url(self, world):
        response = auth(world["owner"]).get(download_url(world["material"].pk))

        assert response.status_code == 200
        assert response.data["url"]

    def test_a_group_member_can_get_a_download_url(self, world):
        response = auth(world["member"]).get(download_url(world["material"].pk))

        assert response.status_code == 200

    def test_a_non_member_cannot(self, world):
        """
        The property that must never regress. A signed URL is a bearer
        credential for the bytes; minting one for a non-member would hand
        out exactly what the M4 audit removed.
        """
        response = auth(world["outsider"]).get(download_url(world["material"].pk))

        assert response.status_code == 404
        assert "url" not in response.data

    def test_an_anonymous_caller_cannot(self, world):
        assert APIClient().get(download_url(world["material"].pk)).status_code == 401

    def test_denial_is_indistinguishable_from_absence(self, world):
        """A different answer for 'exists but not yours' is an id oracle."""
        client = auth(world["outsider"])

        forbidden = client.get(download_url(world["material"].pk))
        missing = client.get(download_url(999999))

        assert forbidden.status_code == missing.status_code == 404
        assert str(forbidden.data) == str(missing.data)

    def test_the_download_route_uses_the_same_queryset_as_retrieve(self, world):
        """
        Pins the mechanism. The action calls get_object(), so it inherits
        get_queryset() — there is no second authorization path to drift.
        If someone gives download its own lookup, these two diverge.
        """
        client = auth(world["outsider"])
        pk = world["material"].pk

        assert client.get(download_url(pk)).status_code == \
               client.get(reverse("studymaterial-detail", args=[pk])).status_code


# ── the ephemeral-filesystem case ────────────────────────────────────────

@pytest.mark.django_db
class TestMissingBytes:
    def test_a_material_whose_file_is_gone_returns_410_not_500(self, world):
        """
        The exact state of every material older than the last deploy. The
        row survives, the bytes do not. 410 Gone says so precisely; a 500
        would look like a server fault the user should retry.
        """
        material = world["material"]
        material.file.delete(save=True)

        response = auth(world["owner"]).get(download_url(material.pk))

        assert response.status_code == 410
        assert "no longer available" in response.data["error"]

    def test_a_non_member_still_gets_404_for_a_material_with_no_file(self, world):
        """
        410 must not become an existence oracle: a non-member has to get the
        same 404 whether or not the bytes are there.
        """
        world["material"].file.delete(save=True)

        response = auth(world["outsider"]).get(download_url(world["material"].pk))

        assert response.status_code == 404


# ── thumbnails ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestVisualSearchThumbnails:
    @pytest.fixture(autouse=True)
    def _stub_vector(self, monkeypatch):
        monkeypatch.setattr(
            "groups.views.VectorSearchService.extract_vector", lambda i: [0.1] * 512)

    def _search(self, user, group_pk):
        return auth(user).post(
            reverse("visual-search-query"),
            {"image": ContentFile(b"q", name="q.jpg"), "group_id": group_pk},
            format="multipart")

    def test_a_member_gets_a_thumbnail_url(self, world):
        """M4 removed file_url entirely; Phase 3 restores it as a signed URL."""
        response = self._search(world["member"], world["group"].pk)

        assert response.status_code == 200
        result = response.data["query_results"][0]
        assert "thumbnail_url" in result
        assert result["thumbnail_url"]

    def test_a_non_member_gets_no_results_and_therefore_no_url(self, world):
        """
        Thumbnails are minted per result, so scoping the RESULTS is what
        keeps URLs out of the wrong hands. There is no separate check to
        forget.
        """
        response = self._search(world["outsider"], world["group"].pk)

        assert response.status_code == 404
        assert "Secret Diagram" not in str(response.data)
        assert "thumbnail_url" not in str(response.data)

    def test_the_raw_media_path_is_never_returned(self, world):
        """
        With a bucket configured this is a signed URL. Without one it is the
        local path — which is only reachable because DEBUG serves it, and is
        unreachable in production. Either way the response must not contain
        a bare, permanent handle when storage IS configured.
        """
        response = self._search(world["member"], world["group"].pk)
        body = str(response.data)

        if object_storage_enabled():
            assert "/media/" not in body


# ── the signing helper ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestSignedUrlHelper:
    def test_an_empty_field_signs_to_none(self):
        assert signed_url(None) is None

    def test_a_signing_failure_returns_none_rather_than_raising(self, world, monkeypatch):
        """
        A broken bucket must degrade to a result without a link, not a 500.
        The page still renders; the operator sees the exception in Sentry.
        """
        monkeypatch.setattr("common.storage.object_storage_enabled", lambda: True)
        monkeypatch.setattr(
            "common.storage._generate_presigned",
            lambda key, params, ttl: (_ for _ in ()).throw(RuntimeError("no bucket")))

        assert signed_url(world["material"].file) is None

    def test_a_search_still_returns_results_when_signing_fails(self, world, monkeypatch):
        """The degradation path end-to-end: results without thumbnails."""
        monkeypatch.setattr(
            "groups.views.VectorSearchService.extract_vector", lambda i: [0.1] * 512)
        monkeypatch.setattr("common.storage.object_storage_enabled", lambda: True)
        monkeypatch.setattr(
            "common.storage._generate_presigned",
            lambda key, params, ttl: (_ for _ in ()).throw(RuntimeError("down")))

        response = auth(world["member"]).post(
            reverse("visual-search-query"),
            {"image": ContentFile(b"q", name="q.jpg"), "group_id": world["group"].pk},
            format="multipart")

        assert response.status_code == 200
        assert response.data["total_found"] == 1
        assert response.data["query_results"][0]["thumbnail_url"] is None


class TestFilenameSanitisation:
    """
    The material title is user-supplied and is interpolated into a
    Content-Disposition header. Quotes and CRLF there are a header-injection
    primitive, so they never reach the signature.
    """

    @pytest.mark.parametrize("raw,banned", [
        ('report".pdf', '"'),
        ("a\r\nSet-Cookie: x=1", "\r"),
        ("a\nX-Injected: 1", "\n"),
        ('back\\slash', "\\"),
    ])
    def test_dangerous_characters_are_stripped(self, raw, banned):
        assert banned not in _sanitise(raw)

    def test_a_title_of_only_junk_falls_back(self):
        assert _sanitise('"\r\n') == "download"

    def test_an_overlong_title_is_bounded(self):
        assert len(_sanitise("x" * 500)) == 120

    def test_an_ordinary_title_survives(self):
        assert _sanitise("Week 3 Notes.pdf") == "Week 3 Notes.pdf"


# ── configuration ────────────────────────────────────────────────────────

class TestTheRealSigningPath:
    """
    Exercises the S3 code path itself, not the filesystem fallback.

    Everything above runs with object storage DISABLED, because that is how
    CI and development are configured — which means none of it would notice
    if the enabled path were broken. Presigning is a purely local
    computation (HMAC over the request, no call to the service), so the real
    backend can be driven here with dummy credentials, no bucket and no
    network.

    This is the test that would have caught a wrong bucket name, a missing
    signature version, or a `_normalize_name` mismatch between what was
    written and what is signed.
    """

    @staticmethod
    def _storage():
        from storages.backends.s3 import S3Storage

        return S3Storage(
            bucket_name="sparklm-test-bucket",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            endpoint_url="https://accountid.r2.cloudflarestorage.com",
            region_name="auto",
            signature_version="s3v4",
            addressing_style="virtual",
            querystring_auth=True,
            default_acl=None,
            file_overwrite=False,
        )

    def _sign(self, monkeypatch, filename=None):
        from django.core.files.base import ContentFile

        import common.storage as storage_mod

        monkeypatch.setattr(storage_mod, "object_storage_enabled", lambda: True)
        monkeypatch.setattr(storage_mod, "default_storage", self._storage())

        field = ContentFile(b"x", name="study_materials/notes.pdf")
        field.name = "study_materials/notes.pdf"
        return storage_mod.signed_url(field, ttl=300, filename=filename)

    def test_a_real_signature_is_produced(self, monkeypatch):
        url = self._sign(monkeypatch)

        assert url is not None
        assert "X-Amz-Signature=" in url, "the URL is not signed"
        assert "X-Amz-Credential=" in url

    def test_the_url_points_at_the_configured_bucket_and_key(self, monkeypatch):
        url = self._sign(monkeypatch)

        assert "sparklm-test-bucket" in url
        assert "study_materials/notes.pdf" in url
        assert "r2.cloudflarestorage.com" in url

    def test_the_requested_ttl_is_carried_into_the_signature(self, monkeypatch):
        assert "X-Amz-Expires=300" in self._sign(monkeypatch)

    def test_a_download_filename_is_signed_into_content_disposition(self, monkeypatch):
        """
        The filename rides inside the signed parameters, so it cannot be
        tampered with after the fact — changing it invalidates the signature.
        """
        url = self._sign(monkeypatch, filename="Week 3 Notes.pdf")

        assert "response-content-disposition" in url.lower()
        assert "X-Amz-Signature=" in url

    def test_a_hostile_filename_cannot_inject_a_header(self, monkeypatch):
        """
        The invariant is that no LINE BREAK survives into the header, not
        that a particular word is absent — "X-Injected" is legal text in a
        filename, and asserting on it would be testing the wrong property.
        Without CR/LF the value cannot terminate the header, so it stays a
        filename however it reads.
        """
        url = self._sign(monkeypatch, filename='evil".pdf\r\nX-Injected: 1')

        assert url is not None
        upper = url.upper()
        assert "%0D" not in upper and "%0A" not in upper, "a CR/LF reached the header"
        assert "\r" not in url and "\n" not in url
        # Exactly two encoded quotes: the delimiters this code writes around
        # the filename. A third would mean the caller's quote survived and
        # closed the value early.
        assert upper.count("%22") == 2, "a user-supplied quote reached the header"

    def test_signing_is_offline(self, monkeypatch):
        """
        Guards a performance property, not just correctness: signing must
        not make a network round trip per result. Visual search mints one
        URL per hit, and a call to the service per thumbnail would be a
        latency disaster on a 0.1 vCPU instance.
        """
        import socket

        def no_network(*a, **kw):
            raise AssertionError("signing attempted a network connection")

        monkeypatch.setattr(socket.socket, "connect", no_network)

        assert self._sign(monkeypatch) is not None


class TestStorageConfiguration:
    def test_storage_is_disabled_without_a_bucket(self, settings):
        settings.AWS_STORAGE_BUCKET_NAME = ""
        assert object_storage_enabled() is False

    def test_storage_is_enabled_with_a_bucket(self, settings):
        settings.AWS_STORAGE_BUCKET_NAME = "sparklm-media"
        assert object_storage_enabled() is True

    def test_the_bucket_is_never_configured_public(self):
        """
        The whole point of the phase, and it must be assertable EVERYWHERE.

        An earlier version read STORAGES["default"]["OPTIONS"] and skipped
        when no bucket was configured — which is CI and every developer
        machine, i.e. everywhere this check would ever run. Mutation testing
        showed flipping `default_acl` to "public-read" was caught by
        nothing. The options now live in a constant that exists whether or
        not a bucket is set, so the guard has no way to opt out.
        """
        from django.conf import settings as dj

        options = dj.S3_STORAGE_OPTIONS

        assert options["default_acl"] is None, (
            "objects would be stamped public-read — this recreates the "
            "unauthenticated /media/ hole in a new location"
        )
        assert options["querystring_auth"] is True, (
            ".url would return a bare, permanent public URL instead of a "
            "signed one"
        )
        assert options["file_overwrite"] is False, (
            "two uploads with the same name would silently destroy the first"
        )

    def test_the_signed_url_ttl_is_short(self):
        """A URL that outlives the session is the old problem with extra steps."""
        from django.conf import settings as dj

        assert 0 < dj.SIGNED_URL_TTL <= 3600
        assert dj.S3_STORAGE_OPTIONS["querystring_expire"] == dj.SIGNED_URL_TTL

    def test_the_configured_backend_is_used_when_a_bucket_is_set(self):
        """
        Pins the wiring itself: the constant is only worth asserting if it
        is what actually reaches STORAGES.
        """
        from django.conf import settings as dj

        if not dj.AWS_STORAGE_BUCKET_NAME:
            assert dj.STORAGES["default"]["BACKEND"].endswith("FileSystemStorage")
        else:
            assert dj.STORAGES["default"]["BACKEND"] == "storages.backends.s3.S3Storage"
            assert dj.STORAGES["default"]["OPTIONS"] == dj.S3_STORAGE_OPTIONS
