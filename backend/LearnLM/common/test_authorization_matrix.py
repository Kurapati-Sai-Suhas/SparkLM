"""
The repository-wide Authorization Matrix (M4 security sprint, WP6 + WP7).

Milestone 4 found the same defect eight times across four separate reviews.
Every one was fixed with a good local test — and the next endpoint with the
identical bug shipped anyway, because per-endpoint tests only cover the
endpoint someone thought to look at.

This file inverts that. Instead of asserting things about endpoints a human
remembered, it walks the URLconf and asserts invariants about *every* route,
so a new endpoint is covered the moment it is registered:

    test_no_endpoint_is_publicly_readable_by_accident
        every route rejects anonymous access unless declared PUBLIC

    test_no_view_declares_allow_any_without_being_declared_public
        AllowAny cannot be added without editing PUBLIC_ENDPOINTS

    test_every_route_is_classified
        a new route must be placed in the matrix — drift fails the build

Plus parameterised tenant-isolation tests over the group-scoped endpoints,
and a serializer PII guard.

The categories are the ones in the sprint brief. Every route belongs to
exactly one:

    PUBLIC          reachable without a token, by design
    AUTHENTICATED   any logged-in user; no tenant-owned resource
    SELF_SCOPED     operates on request.user's own rows only
    GROUP_SCOPED    reads/writes rows owned by a StudyGroup
    MATERIAL_SCOPED reads/writes a StudyMaterial
    ADMIN_ONLY      staff only
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import get_resolver, reverse
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient

from common.tokens import issue_token_pair
from groups.models import (
    AssignedQuiz, Connection, Document, StudyGroup, StudyMaterial,
)

User = get_user_model()

PUBLIC = "public"
AUTHENTICATED = "authenticated"
SELF_SCOPED = "self"
GROUP_SCOPED = "group"
MATERIAL_SCOPED = "material"
ADMIN_ONLY = "admin"


# The six routes allowed to answer an anonymous caller. Adding to this set
# is the deliberate, reviewable act that WP0's default-deny exists to force.
PUBLIC_ENDPOINTS = {
    "token_obtain_pair",
    "token_refresh",
    "register",
    "auth-google",
    "health-check",
    "healthz",
}


# Every route in the URLconf, classified. `test_every_route_is_classified`
# fails if a route is added and not listed here.
MATRIX = {
    # ── public ──────────────────────────────────────────────────────────
    "token_obtain_pair": PUBLIC,
    "token_refresh": PUBLIC,
    "register": PUBLIC,
    "auth-google": PUBLIC,
    "health-check": PUBLIC,
    "healthz": PUBLIC,
    # ── self-scoped ─────────────────────────────────────────────────────
    "auth-logout-all": SELF_SCOPED,
    "dashboard-stats": SELF_SCOPED,
    "dashboard-bootstrap": SELF_SCOPED,
    "user-profile": SELF_SCOPED,
    "settings_profile": SELF_SCOPED,
    "settings_email": SELF_SCOPED,
    "schedule": SELF_SCOPED,
    "notifications": SELF_SCOPED,
    "update_user_activity": SELF_SCOPED,
    "analytics-charts": SELF_SCOPED,
    "review-queue": SELF_SCOPED,
    "mastery-map": SELF_SCOPED,
    "hybrid-router": SELF_SCOPED,
    "save-quiz": SELF_SCOPED,
    "code-profile": SELF_SCOPED,
    "code-next-problem": SELF_SCOPED,
    "code-onboard": SELF_SCOPED,
    "gamification-dashboard": SELF_SCOPED,
    "friends-list": SELF_SCOPED,
    "friend-request": SELF_SCOPED,
    "friend-action": SELF_SCOPED,
    "messages_friends": SELF_SCOPED,
    "messages": SELF_SCOPED,
    "user-search": AUTHENTICATED,
    # ── authenticated, no tenant-owned resource ─────────────────────────
    # DefaultRouter's index. Lists the registered viewset URLs and nothing
    # else — no rows, no ids. Found by this file's own drift guard, which
    # is the point: it is a route, so it must be classified.
    "api-root": AUTHENTICATED,
    "coding-portals-list": AUTHENTICATED,
    "code-run": AUTHENTICATED,
    "code-submit": AUTHENTICATED,
    "process_document": AUTHENTICATED,
    # ── group-scoped ────────────────────────────────────────────────────
    "studygroup-list": GROUP_SCOPED,
    "studygroup-detail": GROUP_SCOPED,
    "studygroup-join": GROUP_SCOPED,
    "studygroup-leave": GROUP_SCOPED,
    "group-members": GROUP_SCOPED,
    "group-messages": GROUP_SCOPED,
    "list-assigned-quizzes": GROUP_SCOPED,
    "manage-assigned-quiz": GROUP_SCOPED,
    "assign-quiz": GROUP_SCOPED,
    "visual-search-query": GROUP_SCOPED,
    "visual-search-upload": GROUP_SCOPED,
    # ── material-scoped ─────────────────────────────────────────────────
    "studymaterial-list": MATERIAL_SCOPED,
    "studymaterial-detail": MATERIAL_SCOPED,
    "ai-flashcards": MATERIAL_SCOPED,
    "ai-quiz": MATERIAL_SCOPED,
    "ai-doubt": MATERIAL_SCOPED,
    "ai-doubt-rag": MATERIAL_SCOPED,
    # ── admin ───────────────────────────────────────────────────────────
    "mlops-telemetry": ADMIN_ONLY,
}


# ── URLconf walker ───────────────────────────────────────────────────────

def _iter_routes():
    """(url_name, view_class) for every named route, admin excluded."""
    seen = {}
    for pattern in get_resolver().url_patterns:
        _walk(pattern, seen, prefix="")
    return seen


def _walk(pattern, seen, prefix):
    from django.urls.resolvers import URLPattern, URLResolver

    if isinstance(pattern, URLResolver):
        new_prefix = prefix + str(pattern.pattern)
        if new_prefix.startswith("admin/"):
            return
        for sub in pattern.url_patterns:
            _walk(sub, seen, new_prefix)
        return

    if isinstance(pattern, URLPattern) and pattern.name:
        callback = pattern.callback
        # DRF exposes the class on .cls for both APIView.as_view() and the
        # @api_view decorator; plain Django views have neither.
        view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        seen.setdefault(pattern.name, view_cls)


ROUTES = _iter_routes()


# ── WP7: drift and AllowAny enforcement ──────────────────────────────────

def test_every_route_is_classified():
    """
    A new endpoint must be placed in the matrix. This is the guard that
    makes the rest of this file complete rather than merely thorough — an
    endpoint nobody classified is an endpoint nobody authorised.
    """
    unclassified = sorted(set(ROUTES) - set(MATRIX))
    assert not unclassified, (
        f"Routes missing from the authorization matrix: {unclassified}. "
        f"Classify each one, then add the tenant-isolation test it needs."
    )


def test_the_matrix_has_no_stale_entries():
    stale = sorted(set(MATRIX) - set(ROUTES))
    assert not stale, f"Matrix lists routes that no longer exist: {stale}"


@pytest.mark.parametrize("name", sorted(n for n, c in ROUTES.items() if c is not None))
def test_no_view_declares_allow_any_without_being_declared_public(name):
    """
    The WP7 CI guard. DRF's built-in default was AllowAny and the settings
    key was absent, so every view was public-by-omission. Now the default is
    IsAuthenticated and opting out is a two-place change: the view AND
    PUBLIC_ENDPOINTS. One alone fails this test.
    """
    view_cls = ROUTES[name]
    permissions = getattr(view_cls, "permission_classes", None)
    if permissions is None:
        return
    declares_allow_any = any(p is AllowAny for p in permissions)
    if declares_allow_any:
        assert name in PUBLIC_ENDPOINTS, (
            f"{name} ({view_cls.__name__}) declares AllowAny but is not in "
            f"PUBLIC_ENDPOINTS. Either scope it or declare it public."
        )
    assert permissions != [], (
        f"{name} sets permission_classes = [] — an empty list disables all "
        f"checks and is indistinguishable from an oversight. Use AllowAny."
    )


def test_public_endpoints_all_exist():
    """Stops PUBLIC_ENDPOINTS becoming a graveyard that silences the guard."""
    missing = sorted(PUBLIC_ENDPOINTS - set(ROUTES))
    assert not missing, f"PUBLIC_ENDPOINTS names non-existent routes: {missing}"


# ── WP6: anonymous access ────────────────────────────────────────────────

def _url_for(name):
    """Reverse a route, filling required kwargs with a plausible id."""
    for args in ((), (1,), (1, 1)):
        try:
            return reverse(name, args=args)
        except Exception:
            continue
    return None


@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(n for n in ROUTES if n not in PUBLIC_ENDPOINTS))
def test_no_endpoint_is_publicly_readable_by_accident(name):
    """
    The invariant that would have caught /api/upload-pdf/ on the day it
    shipped: no anonymous request to a non-public route may succeed.

    Both verbs are tried because the point is the absence of a 2xx, not the
    presence of any particular status. 401/403 are the intent; 404/405/400
    are fine too (unreachable or rejected before the handler). A 2xx means
    an anonymous caller got served.
    """
    url = _url_for(name)
    if url is None:
        pytest.skip(f"cannot reverse {name}")

    anon = APIClient()
    for method in ("get", "post"):
        response = getattr(anon, method)(url)
        assert not (200 <= response.status_code < 300), (
            f"{method.upper()} {url} ({name}) served an ANONYMOUS caller "
            f"with {response.status_code}"
        )


# ── fixtures for the tenant matrix ───────────────────────────────────────

def make_user(name, staff=False):
    return User.objects.create_user(
        username=name, password="Matrix#2026x", email=f"{name}@t.com",
        is_staff=staff,
    )


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user).access_token}")
    return c


@pytest.fixture
def tenants(db):
    """
    Two tenants with a full set of owned resources, plus the five actors the
    brief names: owner, member, non-member, admin, anonymous.
    """
    owner = make_user("mx_owner")
    member = make_user("mx_member")
    outsider = make_user("mx_outsider")
    admin = make_user("mx_admin", staff=True)

    group = StudyGroup.objects.create(
        name="Owner group", description="d", creator=owner, capacity=10,
    )
    group.members.add(owner, member)

    material = StudyMaterial.objects.create(
        title="SECRET-MATERIAL", uploaded_by=owner, study_group=group,
        file=ContentFile(b"x", name="m.txt"), extracted_text="SECRET-TEXT " * 20,
    )
    quiz = AssignedQuiz.objects.create(
        study_group=group, assigned_by=owner, topic="SECRET-QUIZ",
        quiz_data={"answers": ["SECRET-ANSWER"]},
        deadline="2027-01-01T00:00:00Z",
    )
    Document.objects.create(
        group=group, uploaded_by=owner, title="SECRET-DIAGRAM",
        file=ContentFile(b"i", name="d.jpg"), file_type="image",
        feature_vector=[0.1] * 512,
    )

    # The outsider is a real user with a group of their own, so every denial
    # below is about the resource and not about being a nobody.
    own = StudyGroup.objects.create(
        name="Outsider group", description="d", creator=outsider, capacity=10,
    )
    own.members.add(outsider)

    return {
        "owner": owner, "member": member, "outsider": outsider, "admin": admin,
        "group": group, "material": material, "quiz": quiz,
        "outsider_group": own,
    }


# Every group-scoped read, as (route name, how to build the request).
# Parameterised so a new group-scoped endpoint is one line, not a new test.
GROUP_SCOPED_READS = [
    ("group-members", lambda t: ("get", reverse("group-members", args=[t["group"].pk]), None)),
    ("group-messages", lambda t: ("get", reverse("group-messages", args=[t["group"].pk]), None)),
    ("list-assigned-quizzes", lambda t: ("get", f'{reverse("list-assigned-quizzes")}?study_group={t["group"].pk}', None)),
    ("manage-assigned-quiz", lambda t: ("get", reverse("manage-assigned-quiz", args=[t["quiz"].pk]), None)),
    ("studygroup-detail", lambda t: ("get", reverse("studygroup-detail", args=[t["group"].pk]), None)),
    ("studymaterial-detail", lambda t: ("get", reverse("studymaterial-detail", args=[t["material"].pk]), None)),
]

# Values that must never appear in a non-member's response body.
#
# The usernames are here because mutation testing found them missing: with
# the roster left unscoped, every content assertion still passed, because a
# group roster's secret is not a document title or an email address — it is
# WHO IS IN THE GROUP. A test that checks the wrong secret cannot fail for
# the reason it claims.
SECRETS = ("SECRET-MATERIAL", "SECRET-QUIZ", "SECRET-ANSWER", "SECRET-TEXT",
           "SECRET-DIAGRAM", "mx_owner@t.com", "mx_owner", "mx_member")


def _call(client, method, url, payload):
    fn = getattr(client, method)
    return fn(url, payload, format="json") if payload else fn(url)


@pytest.mark.django_db
@pytest.mark.parametrize("name,build", GROUP_SCOPED_READS, ids=[n for n, _ in GROUP_SCOPED_READS])
@pytest.mark.parametrize("actor", ["owner", "member"])
def test_group_scoped_reads_allow_owner_and_member(tenants, name, build, actor):
    method, url, payload = build(tenants)
    response = _call(client_for(tenants[actor]), method, url, payload)
    assert 200 <= response.status_code < 300, (
        f"{actor} was denied their own group's {name}: {response.status_code}"
    )


@pytest.mark.django_db
@pytest.mark.parametrize("name,build", GROUP_SCOPED_READS, ids=[n for n, _ in GROUP_SCOPED_READS])
def test_group_scoped_reads_deny_non_members(tenants, name, build):
    """
    The whole milestone in one assertion. A 200 with an empty body is
    acceptable for list endpoints; leaking a value is not.
    """
    method, url, payload = build(tenants)
    response = _call(client_for(tenants["outsider"]), method, url, payload)

    body = str(getattr(response, "data", ""))
    for secret in SECRETS:
        assert secret not in body, (
            f"{name} leaked {secret!r} to a non-member (status "
            f"{response.status_code})"
        )


@pytest.mark.django_db
@pytest.mark.parametrize("name,build", GROUP_SCOPED_READS, ids=[n for n, _ in GROUP_SCOPED_READS])
def test_group_scoped_reads_deny_staff_without_membership(tenants, name, build):
    """
    Staff is not a tenant bypass. `is_staff` gates the MLOps telemetry
    endpoint and Django admin; it must not silently widen a group queryset,
    because that is how an admin flag turns into a data-exfiltration path.
    """
    method, url, payload = build(tenants)
    response = _call(client_for(tenants["admin"]), method, url, payload)

    body = str(getattr(response, "data", ""))
    for secret in SECRETS:
        assert secret not in body, f"{name} leaked {secret!r} to a non-member admin"


# ── enumeration ──────────────────────────────────────────────────────────

ENUMERABLE = [
    ("studymaterial-detail", lambda pk: reverse("studymaterial-detail", args=[pk])),
    ("manage-assigned-quiz", lambda pk: reverse("manage-assigned-quiz", args=[pk])),
    ("studygroup-detail", lambda pk: reverse("studygroup-detail", args=[pk])),
    ("group-messages", lambda pk: reverse("group-messages", args=[pk])),
]


@pytest.mark.django_db
@pytest.mark.parametrize("name,url_for", ENUMERABLE, ids=[n for n, _ in ENUMERABLE])
def test_denial_is_indistinguishable_from_absence(tenants, name, url_for):
    """
    A different status or body for "exists but not yours" vs "does not
    exist" turns any detail route into an id oracle.
    """
    client = client_for(tenants["outsider"])
    real_pk = {
        "studymaterial-detail": tenants["material"].pk,
        "manage-assigned-quiz": tenants["quiz"].pk,
        "studygroup-detail": tenants["group"].pk,
        "group-messages": tenants["group"].pk,
    }[name]

    forbidden = client.get(url_for(real_pk))
    missing = client.get(url_for(999999))

    assert forbidden.status_code == missing.status_code, (
        f"{name}: {forbidden.status_code} for an existing id vs "
        f"{missing.status_code} for a nonexistent one — an enumeration oracle"
    )
    assert str(getattr(forbidden, "data", "")) == str(getattr(missing, "data", ""))


# ── write / delete authorization ─────────────────────────────────────────

@pytest.mark.django_db
class TestWriteAndDeleteAreScoped:
    def test_a_non_member_cannot_delete_a_material(self, tenants):
        client = client_for(tenants["outsider"])
        url = reverse("studymaterial-detail", args=[tenants["material"].pk])

        assert client.delete(url).status_code == 404
        assert StudyMaterial.objects.filter(pk=tenants["material"].pk).exists()

    def test_a_non_member_cannot_edit_a_quiz(self, tenants):
        client = client_for(tenants["outsider"])
        url = reverse("manage-assigned-quiz", args=[tenants["quiz"].pk])

        assert client.patch(url, {"topic": "PWNED"}, format="json").status_code == 404
        tenants["quiz"].refresh_from_db()
        assert tenants["quiz"].topic == "SECRET-QUIZ"

    def test_a_non_member_cannot_delete_a_quiz(self, tenants):
        client = client_for(tenants["outsider"])
        url = reverse("manage-assigned-quiz", args=[tenants["quiz"].pk])

        assert client.delete(url).status_code == 404
        assert AssignedQuiz.objects.filter(pk=tenants["quiz"].pk).exists()

    def test_a_plain_member_cannot_edit_the_owners_quiz(self, tenants):
        """Membership grants read; only the group creator may write."""
        client = client_for(tenants["member"])
        url = reverse("manage-assigned-quiz", args=[tenants["quiz"].pk])

        assert client.patch(url, {"topic": "X"}, format="json").status_code == 403

    def test_the_owner_can_still_edit_their_quiz(self, tenants):
        client = client_for(tenants["owner"])
        url = reverse("manage-assigned-quiz", args=[tenants["quiz"].pk])

        assert client.patch(url, {"topic": "Updated"}, format="json").status_code == 200

    def test_a_non_member_cannot_assign_a_quiz_into_another_group(self, tenants):
        """
        Found by the sprint's own sweep. The group was resolved unscoped and
        the creator check raised AttributeError (500), so this failed closed
        but for the wrong reason — 400 is the correct answer.
        """
        before = AssignedQuiz.objects.count()

        response = client_for(tenants["outsider"]).post(
            reverse("assign-quiz"),
            {"study_group": tenants["group"].pk, "topic": "Injected",
             "quiz_data": {}, "deadline": "2027-01-01T00:00:00Z"},
            format="json",
        )

        assert response.status_code == 400
        assert AssignedQuiz.objects.count() == before

    def test_a_member_who_is_not_the_creator_cannot_assign_a_quiz(self, tenants):
        """Membership is not enough; assigning is a creator action (403)."""
        response = client_for(tenants["member"]).post(
            reverse("assign-quiz"),
            {"study_group": tenants["group"].pk, "topic": "Nope",
             "quiz_data": {}, "deadline": "2027-01-01T00:00:00Z"},
            format="json",
        )

        assert response.status_code == 403

    def test_the_group_creator_can_still_assign_a_quiz(self, tenants):
        response = client_for(tenants["owner"]).post(
            reverse("assign-quiz"),
            {"study_group": tenants["group"].pk, "topic": "Legitimate",
             "quiz_data": {"answers": []}, "deadline": "2027-01-01T00:00:00Z"},
            format="json",
        )

        assert response.status_code == 201
        assert AssignedQuiz.objects.filter(topic="Legitimate").exists()


# ── WP3: serializer PII ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestSerializersDoNotLeakPii:
    def test_user_search_does_not_return_email(self, tenants):
        response = client_for(tenants["outsider"]).get(
            reverse("user-search"), {"q": "mx_owner"}
        )

        assert response.status_code == 200
        assert "mx_owner@t.com" not in str(response.data)

    def test_group_roster_does_not_return_email(self, tenants):
        response = client_for(tenants["member"]).get(
            reverse("group-members", args=[tenants["group"].pk])
        )

        assert response.status_code == 200
        assert "mx_owner@t.com" not in str(response.data), (
            "the roster published a member's email address"
        )
        assert "mx_owner" in str(response.data), "the roster must still work"

    def test_friends_list_does_not_return_email(self, tenants):
        Connection.objects.create(
            sender=tenants["owner"], receiver=tenants["member"], status="accepted"
        )
        response = client_for(tenants["member"]).get(reverse("friends-list"))

        assert response.status_code == 200
        assert "mx_owner@t.com" not in str(response.data)

    def test_the_cross_tenant_serializers_declare_no_email_field(self):
        """
        Guards the mechanism, not one payload. UserBasicSerializer and
        UserDisplaySerializer render OTHER people; UserSerializer renders
        you to yourself and keeps email deliberately.
        """
        from groups.serializers import UserBasicSerializer, UserDisplaySerializer

        for serializer in (UserBasicSerializer, UserDisplaySerializer):
            fields = serializer.Meta.fields
            assert fields != "__all__", f"{serializer.__name__} must list fields"
            assert "email" not in fields, (
                f"{serializer.__name__} renders other users — it must not carry email"
            )


# ── WP5: relationship and entropy ────────────────────────────────────────

@pytest.mark.django_db
class TestRelationshipAndEntropy:
    def test_a_stranger_cannot_send_a_direct_message(self, tenants):
        from groups.models import DirectMessage

        response = client_for(tenants["outsider"]).post(
            reverse("messages", args=[tenants["owner"].pk]),
            {"text": "unsolicited"}, format="json",
        )

        assert response.status_code == 404
        assert not DirectMessage.objects.filter(sender=tenants["outsider"]).exists()

    def test_friends_can_still_message_each_other(self, tenants):
        Connection.objects.create(
            sender=tenants["owner"], receiver=tenants["member"], status="accepted"
        )

        response = client_for(tenants["member"]).post(
            reverse("messages", args=[tenants["owner"].pk]),
            {"text": "hello"}, format="json",
        )

        assert response.status_code == 200
        assert response.data["text"] == "hello"

    def test_a_pending_request_is_not_a_friendship(self, tenants):
        Connection.objects.create(
            sender=tenants["outsider"], receiver=tenants["owner"], status="pending"
        )

        response = client_for(tenants["outsider"]).post(
            reverse("messages", args=[tenants["owner"].pk]),
            {"text": "still unsolicited"}, format="json",
        )

        assert response.status_code == 404

    def test_join_codes_are_server_generated_and_unguessable(self, tenants):
        response = client_for(tenants["owner"]).post(
            reverse("studygroup-list"),
            {"name": "New", "description": "d", "capacity": 5,
             "join_code": "AAA1"},
            format="json",
        )

        assert response.status_code == 201
        code = StudyGroup.objects.get(name="New").join_code
        assert code != "AAA1", "a client-supplied join code was honoured"
        assert len(code) == StudyGroup.JOIN_CODE_LENGTH
        assert set(code) <= set(StudyGroup.JOIN_CODE_ALPHABET)

    def test_join_codes_are_unique_across_many_groups(self, tenants):
        codes = {
            StudyGroup.objects.create(
                name=f"G{i}", description="d", creator=tenants["owner"], capacity=5
            ).join_code
            for i in range(25)
        }
        assert len(codes) == 25

    def test_joining_by_a_valid_code_still_works(self, tenants):
        code = tenants["group"].join_code

        response = client_for(tenants["outsider"]).post(
            reverse("studygroup-join"), {"code": code}, format="json"
        )

        assert response.status_code == 200
        assert tenants["group"].members.filter(pk=tenants["outsider"].pk).exists()

    def test_the_health_payload_carries_no_infrastructure_detail(self):
        response = APIClient().get(reverse("health-check"))

        assert response.status_code == 200
        assert set(response.data) == {"status", "db"}

    def test_the_health_FAILURE_payload_carries_no_infrastructure_detail(
        self, monkeypatch
    ):
        """
        The branch that actually leaked. The happy-path test above cannot
        reach it — the database is up in tests — so mutation testing found
        it surviving a restored `str(e)`. A connection error string carries
        the host, port and database user to an unauthenticated caller.
        """
        class Unreachable:
            class objects:
                @staticmethod
                def exists():
                    raise RuntimeError(
                        "connection failed: host=db-prod.internal port=5432 user=admin"
                    )

        monkeypatch.setattr(
            "django.contrib.auth.get_user_model", lambda: Unreachable
        )

        response = APIClient().get(reverse("health-check"))

        assert response.status_code == 503
        body = str(response.data)
        assert "db-prod.internal" not in body
        assert "5432" not in body
        assert "admin" not in body
        assert set(response.data) == {"status", "db"}


@pytest.mark.django_db
def test_a_non_member_cannot_read_a_group_roster(tenants):
    """
    Explicit companion to the parameterised sweep. A roster's secret is the
    membership list itself, so this asserts on usernames rather than on
    document contents.
    """
    response = client_for(tenants["outsider"]).get(
        reverse("group-members", args=[tenants["group"].pk])
    )

    body = str(getattr(response, "data", ""))
    assert "mx_owner" not in body
    assert "mx_member" not in body
