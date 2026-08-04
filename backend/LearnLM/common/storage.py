"""
common.storage — durable object storage and authorized access to it
(M5 Phase 3, F1).

Why this exists
---------------
Render's filesystem is ephemeral. Every deploy discards uploaded files, so
the platform has been losing user data continuously: download, preview and
the vision path all read the file directly and break on the next restart,
and four materials uploaded before M4 Phase C have already lost their files
permanently. Phase C cached `extracted_text` so RAG survives, which fixed
one consumer and left the rest broken.

The second problem is that `/media/` is served with no authentication at
all. The M4 audit proved this: a URL leaked from any response was a
permanent, unrevocable handle to the bytes. That is why the visual-search
endpoint had `file_url` removed rather than scoped — there was nothing to
scope it to.

Both are the same missing primitive: storage that outlives the process and
is reachable only through an authorization check. One private bucket plus
short-lived signed URLs solves both.

Design
------
Provider-agnostic. `django-storages`' S3 backend speaks the S3 API, so
Cloudflare R2, Backblaze B2, AWS S3 and MinIO are all the same code and
differ only in `AWS_S3_ENDPOINT_URL`. Nothing here names a vendor.

Unconfigured, everything falls back to `FileSystemStorage`, so development
and CI need no credentials and no network. `object_storage_enabled()` is
the single switch, and every signing path degrades to the local URL rather
than raising — a missing bucket must not turn a page into a 500.

Signed URLs are minted ONLY after the caller has passed the same
`common.authorization` predicate that governs the API. The signature is
what makes the URL work; the authorization check is what decides whether
one is minted at all. Expiry is deliberately short: a signed URL that
outlives the session is the unauthenticated `/media/` problem again with
extra steps.
"""

import logging

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

# Short by design. Long enough to start a download on a slow connection,
# short enough that a URL pasted into a chat is dead before it travels.
DEFAULT_SIGNED_URL_TTL = 300


def object_storage_enabled():
    """
    True when a bucket is configured.

    One switch, read at call time rather than import time so tests and the
    settings override machinery can flip it without reloading the module.
    """
    return bool(getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""))


def signed_url(file_field, ttl=None, filename=None):
    """
    A time-limited URL for `file_field`, or None if there is nothing to serve.

    CALLERS MUST AUTHORIZE FIRST. This function deliberately takes a file
    field and not a request: it has no idea who is asking, and giving it
    that responsibility would put two authorization models in the codebase.
    Every call site sits behind `accessible_materials` / `accessible_groups`.

    Returns None for an empty field so callers can omit the key entirely
    rather than emitting a null the frontend has to special-case.
    """
    if not file_field:
        return None

    ttl = ttl or getattr(settings, "SIGNED_URL_TTL", DEFAULT_SIGNED_URL_TTL)

    if not object_storage_enabled():
        # Local development: FileSystemStorage has no concept of signing.
        # The URL is only reachable because DEBUG serves MEDIA_ROOT, and it
        # is not reachable in production, where nothing serves /media/.
        try:
            return file_field.url
        except Exception:
            logger.warning("No URL available for %r", getattr(file_field, "name", None))
            return None

    try:
        params = {}
        if filename:
            # Content-Disposition rides INSIDE the signed parameters, so the
            # browser saves the material's title rather than the opaque
            # storage key — and tampering with it invalidates the signature.
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{_sanitise(filename)}"'
            )
        return _generate_presigned(file_field.name, params, int(ttl))
    except Exception:
        # Never 500 a page because signing failed. The caller renders a
        # result without a link; the operator sees the exception.
        logger.exception("Failed to sign URL for %r", getattr(file_field, "name", None))
        return None


def _generate_presigned(key, params, ttl):
    """
    Presign through the storage backend's own public API.

    `S3Storage.url(name, parameters, expire)` normalises the key, fills in
    bucket/key, and presigns with the configured credentials, endpoint,
    region and addressing style — so signing cannot drift from whatever
    actually wrote the object.

    Deliberately NOT reaching into `storage.connection.meta.client` and
    `_normalize_name`/`_clean_name`: an earlier draft did exactly that and
    broke, because `_clean_name` is not a method on S3Storage in
    django-storages 1.14. Private helpers move between releases; `url()` is
    the contract.
    """
    return default_storage.url(key, parameters=params or None, expire=ttl)


def _sanitise(filename):
    """
    Strip anything that could break out of the Content-Disposition header.

    The title is user-supplied, and it is being interpolated into a response
    header — quotes, CR and LF are a header-injection primitive.
    """
    cleaned = "".join(
        ch for ch in str(filename) if ch.isprintable() and ch not in '"\\\r\n'
    )
    return cleaned[:120] or "download"
