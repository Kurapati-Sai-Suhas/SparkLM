"""
Pagination correctness on the four list endpoints (M2 P2.1).

`REST_FRAMEWORK['PAGE_SIZE']` was 3. Every list view in the product inherited
it, and not one caller followed the `next` link — the frontend reads
`res.data.results || res.data` and renders that. So a group with 12 members
showed 3, a library with 40 files showed 3, and the remainder was not merely
unpaged but invisible. There was no error and no control to advance, which is
why it survived to production: it looks like an empty-ish list, not a bug.

Three separate defects are covered here, because raising the number alone
would have left two of them standing:

  1. Size.      The global default truncated everything.
  2. Dependence. Three of the four views named no pagination class, so their
                 behaviour was whatever the global setting happened to be.
  3. Stability.  All four ordered on a non-unique column, and `getGroupMembers`
                 ordered on nothing at all (DRF emitted
                 UnorderedObjectListWarning). Without a total order, Postgres
                 may return a row on page 1 and again on page 2 while another
                 row is never returned — the list silently lies even when the
                 page size is generous.

The response envelope is deliberately NOT changed. Five frontend files depend
on the `results` key; `test_envelope_shape_is_unchanged` pins it.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from common.tokens import issue_token_pair
from groups.models import AssignedQuiz, StudyGroup, StudyMaterial
from groups.views import (
    LargePagination,
    ListAssignedQuizView,
    MaterialViewSet,
    StudyGroupViewSet,
    getGroupMembers,
)

User = get_user_model()

# The value the product shipped with. Named so the guard reads as the thing it
# is actually defending against.
TRUNCATING_PAGE_SIZE = 3


def user(name):
    return User.objects.create_user(
        username=name, password="Pagin#2026x", email=f"{name}@t.com"
    )


def auth(u):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(u).access_token}")
    return c


def group_with_members(owner, count):
    """A group whose roster is `count` people including the creator."""
    g = StudyGroup.objects.create(
        name="Roster", description="d", creator=owner, join_code=f"jc{owner.id}",
        capacity=100,
    )
    for i in range(count - 1):
        g.members.add(user(f"{owner.username}m{i}"))
    return g


def queryset_of(view_cls, as_user, *, kwargs=None, query=None):
    """The queryset a view would paginate, for asserting on the SQL it builds."""
    request = Request(APIRequestFactory().get("/", query or {}))
    request.user = as_user
    view = view_cls()
    view.request = request
    view.kwargs = kwargs or {}
    view.format_kwarg = None
    return view.get_queryset()


def collect_all_pages(client, url, params=None):
    """
    Walk the whole list the way a correct client does — follow `next` until it
    is null — and return the concatenated results.
    """
    seen, page, guard = [], 1, 0
    while True:
        guard += 1
        assert guard < 50, "next-link walk did not terminate"
        r = client.get(url, {**(params or {}), "page": page})
        assert r.status_code == 200, r.status_code
        seen.extend(r.data["results"])
        if not r.data["next"]:
            return seen
        page += 1


# ─────────────────────────────────────────────────────────────
# 1. The defect itself
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_a_group_with_twelve_members_shows_twelve():
    """
    The success criterion from the roadmap, stated literally. At PAGE_SIZE=3
    this returned 3.
    """
    owner = user("owner12")
    g = group_with_members(owner, 12)

    r = auth(owner).get(reverse("group-members", args=[g.id]))

    assert r.status_code == 200
    assert r.data["count"] == 12
    assert len(r.data["results"]) == 12, (
        f"roster truncated to {len(r.data['results'])} of 12"
    )
    assert r.data["next"] is None


@pytest.mark.django_db
def test_a_library_with_twelve_files_shows_twelve():
    owner = user("ownerfiles")
    g = StudyGroup.objects.create(
        name="G", description="d", creator=owner, join_code="jcf1"
    )
    for i in range(12):
        StudyMaterial.objects.create(
            title=f"file{i}", study_group=g, uploaded_by=owner, file=f"m/{i}.pdf"
        )

    r = auth(owner).get(reverse("studymaterial-list"))

    assert r.data["count"] == 12
    assert len(r.data["results"]) == 12


@pytest.mark.django_db
def test_twelve_assigned_quizzes_show_twelve():
    owner = user("ownerquiz")
    g = StudyGroup.objects.create(
        name="G", description="d", creator=owner, join_code="jcq1"
    )
    for i in range(12):
        AssignedQuiz.objects.create(study_group=g, topic=f"t{i}", quiz_data={})

    r = auth(owner).get(
        reverse("list-assigned-quizzes"), {"study_group": g.id}
    )

    assert r.data["count"] == 12
    assert len(r.data["results"]) == 12


# ─────────────────────────────────────────────────────────────
# 2. Guards — these fail if the defect is reintroduced
# ─────────────────────────────────────────────────────────────

def test_global_default_page_size_is_not_a_truncating_value(settings):
    """
    Definition of done: this fails the moment PAGE_SIZE goes back to 3.

    The default is a backstop for views that never think about paging, so it
    has to be large enough that inheriting it is not itself a bug.
    """
    size = settings.REST_FRAMEWORK["PAGE_SIZE"]
    assert size != TRUNCATING_PAGE_SIZE
    assert size >= 20, f"global PAGE_SIZE={size} truncates ordinary lists"


@pytest.mark.django_db
def test_list_views_do_not_depend_on_the_global_default(monkeypatch):
    """
    The stronger guard, and the one that catches the original mistake at its
    root: with the fallback page size forced back to 3, every list endpoint
    must still return all 12 rows, because each names its own pagination class.
    Fails if any of the four views loses `pagination_class` — which is how
    three of them were broken in the first place.

    The fallback is patched on PageNumberPagination rather than through
    override_settings(REST_FRAMEWORK=...), which would be inert here: DRF binds
    `page_size = api_settings.PAGE_SIZE` at class-definition time, so
    overriding the setting afterwards does not reach the class and the test
    would pass no matter what the views did. LargePagination sets its own
    page_size, so patching the base class leaves it untouched — which is
    exactly the independence being asserted.
    """
    monkeypatch.setattr(PageNumberPagination, "page_size", TRUNCATING_PAGE_SIZE)
    assert LargePagination.page_size != TRUNCATING_PAGE_SIZE

    owner = user("ownerindep")
    g = group_with_members(owner, 12)
    for i in range(12):
        StudyMaterial.objects.create(
            title=f"f{i}", study_group=g, uploaded_by=owner, file=f"m/{i}.pdf"
        )
        AssignedQuiz.objects.create(study_group=g, topic=f"t{i}", quiz_data={})
    c = auth(owner)

    members = c.get(reverse("group-members", args=[g.id]))
    files = c.get(reverse("studymaterial-list"))
    quizzes = c.get(reverse("list-assigned-quizzes"), {"study_group": g.id})
    groups = c.get(reverse("studygroup-list"))

    assert len(members.data["results"]) == 12, "members fell back to the global default"
    assert len(files.data["results"]) == 12, "materials fell back to the global default"
    assert len(quizzes.data["results"]) == 12, "quizzes fell back to the global default"
    assert len(groups.data["results"]) == 1


@pytest.mark.parametrize(
    "view",
    [StudyGroupViewSet, MaterialViewSet, ListAssignedQuizView, getGroupMembers],
)
def test_every_list_view_names_the_shared_pagination_class(view):
    """
    Invariant: reuse the one project abstraction rather than growing a second.
    """
    assert view.pagination_class is LargePagination


def test_shared_pagination_class_is_configured_for_real_groups():
    """
    StudyGroup.capacity defaults to 50, so a page smaller than that truncates a
    full group's roster on the first page. It was 8.
    """
    assert LargePagination.page_size >= StudyGroup._meta.get_field("capacity").default
    assert LargePagination.page_size_query_param == "page_size"
    assert LargePagination.max_page_size == 1000


# ─────────────────────────────────────────────────────────────
# 3. Compatibility — the envelope five frontend files read
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize("label", ["groups", "materials", "quizzes", "members"])
def test_envelope_shape_is_unchanged(label):
    """
    StudyGroups, GroupDetail, FileLibrary, AIQuiz and DoubtSolver all do
    `res.data.results || res.data`. Renaming or dropping `results` would make
    every one of them silently fall back to rendering the envelope object
    itself — five broken pages, no error.

    Asserted on ALL FOUR endpoints, not one: the pages read four different
    URLs, so pinning a single response would leave three unpinned. Checked on
    a real response rather than by reading the pagination class, because the
    class is not what the frontend consumes.

    StudyGroups additionally reads `next` and `previous` to drive its prev/next
    buttons, so those keys are load-bearing too, not decoration.
    """
    owner = user(f"ownerenv{label}")
    g = group_with_members(owner, 4)
    StudyMaterial.objects.create(
        title="f", study_group=g, uploaded_by=owner, file="m/f.pdf"
    )
    AssignedQuiz.objects.create(study_group=g, topic="t", quiz_data={})

    url, params = {
        "groups": (reverse("studygroup-list"), {}),
        "materials": (reverse("studymaterial-list"), {}),
        "quizzes": (reverse("list-assigned-quizzes"), {"study_group": g.id}),
        "members": (reverse("group-members", args=[g.id]), {}),
    }[label]

    r = auth(owner).get(url, params)

    assert r.status_code == 200
    assert set(r.data.keys()) == {"count", "next", "previous", "results"}, (
        f"{label}: envelope is {sorted(r.data.keys())}"
    )
    assert isinstance(r.data["results"], list)
    assert isinstance(r.data["count"], int)
    assert r.data["results"], f"{label}: fixture produced no rows to check"


@pytest.mark.django_db
def test_paging_still_works_when_a_client_asks_for_a_small_page():
    """
    The size changed; paging did not go away. A caller that opts into a small
    page must get a working `next` chain.
    """
    owner = user("ownersmall")
    g = group_with_members(owner, 12)
    c = auth(owner)
    url = reverse("group-members", args=[g.id])

    first = c.get(url, {"page_size": 5})

    assert first.data["count"] == 12
    assert len(first.data["results"]) == 5
    assert first.data["next"] is not None
    assert first.data["previous"] is None
    assert len(collect_all_pages(c, url, {"page_size": 5})) == 12


# ─────────────────────────────────────────────────────────────
# 4. Stability — a total order, so pages do not overlap or drop rows
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_walking_pages_yields_every_member_exactly_once():
    owner = user("ownerwalk")
    g = group_with_members(owner, 12)

    ids = [m["id"] for m in collect_all_pages(
        auth(owner), reverse("group-members", args=[g.id]), {"page_size": 5}
    )]

    assert len(ids) == 12
    assert len(set(ids)) == 12, "a member appeared on two pages"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "label", ["groups", "materials", "quizzes", "members"]
)
def test_every_paginated_queryset_has_a_total_order(label):
    """
    The guarantee that makes paging trustworthy: the sort must be TOTAL, i.e.
    end in a unique column. All four views sorted on a non-unique one
    (-created_at, -upload_date, deadline) or on nothing (members), which leaves
    tied rows to the database's discretion — free to be resolved differently on
    each query, so a row can land on page 1 and again on page 2 while another
    is never returned.

    This is asserted structurally rather than by paging twice and comparing.
    A behavioural check is not falsifiable here: on a small table Postgres
    happens to return tied rows consistently, so removing the tiebreak leaves
    the walk identical and the test green. Measured — dropping every tiebreak
    kept a two-walk comparison passing. The order is a property of the query,
    so the query is what gets asserted.
    """
    owner = user(f"ownertot{label}")
    g = group_with_members(owner, 3)
    StudyMaterial.objects.create(
        title="f", study_group=g, uploaded_by=owner, file="m/f.pdf"
    )
    AssignedQuiz.objects.create(study_group=g, topic="t", quiz_data={})

    qs = {
        "groups": lambda: queryset_of(StudyGroupViewSet, owner),
        "materials": lambda: queryset_of(MaterialViewSet, owner),
        "quizzes": lambda: queryset_of(
            ListAssignedQuizView, owner, query={"study_group": g.id}
        ),
        "members": lambda: queryset_of(
            getGroupMembers, owner, kwargs={"group_id": g.id}
        ),
    }[label]()

    order = [str(term) for term in qs.query.order_by]
    assert order, f"{label}: queryset is unordered — paging is undefined"
    assert order[-1] in ("id", "-id"), (
        f"{label}: sorts on {order} — the last term is not unique, so tied "
        f"rows have no defined page"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "label,expected",
    [
        # Primary term = the ordering that shipped on main, unchanged. Only the
        # tiebreak is new, and its DIRECTION matches the primary term so ties
        # read the same way as the sort around them: newest-first lists break
        # ties newest-first, the soonest-deadline list breaks ties oldest-first.
        # '-created_at, id' would be deterministic but would order tied rows
        # backwards relative to everything else on the page.
        ("groups", ["-created_at", "-id"]),
        ("materials", ["-upload_date", "-id"]),
        ("quizzes", ["deadline", "id"]),
        # members had NO ordering at all before P2.1, so there is no prior
        # semantic to preserve. `id` is join order — the closest stable order
        # to what the unordered query already returned in practice, and a
        # smaller visible change than re-sorting the roster alphabetically.
        ("members", ["id"]),
    ],
)
def test_ordering_semantics_are_exactly_as_intended(label, expected):
    """
    Guards the *primary* sort, not just totality. A regression that changed
    `-upload_date` to `upload_date` would still be deterministic and would
    still pass the total-order test above, while silently inverting the file
    library for every user.
    """
    owner = user(f"ownersem{label}")
    g = group_with_members(owner, 3)

    qs = {
        "groups": lambda: queryset_of(StudyGroupViewSet, owner),
        "materials": lambda: queryset_of(MaterialViewSet, owner),
        "quizzes": lambda: queryset_of(
            ListAssignedQuizView, owner, query={"study_group": g.id}
        ),
        "members": lambda: queryset_of(
            getGroupMembers, owner, kwargs={"group_id": g.id}
        ),
    }[label]()

    assert [str(t) for t in qs.query.order_by] == expected


@pytest.mark.django_db
def test_pages_do_not_overlap_when_quizzes_share_a_deadline():
    """
    The end-to-end companion to the structural test above: a creator assigning
    a batch of quizzes all due the same Friday is ordinary use, and walking
    that list must still yield each quiz exactly once.
    """
    owner = user("ownertie")
    g = StudyGroup.objects.create(
        name="G", description="d", creator=owner, join_code="jct1"
    )
    friday = timezone.now() + timedelta(days=3)
    for i in range(12):
        AssignedQuiz.objects.create(
            study_group=g, topic=f"t{i}", quiz_data={}, deadline=friday
        )
    assert AssignedQuiz.objects.values("deadline").distinct().count() == 1, (
        "fixture no longer reproduces the tie it exists to test"
    )
    c = auth(owner)
    url = reverse("list-assigned-quizzes")

    walk = [q["id"] for q in collect_all_pages(c, url, {"study_group": g.id, "page_size": 5})]

    assert len(walk) == 12
    assert len(set(walk)) == 12, "a quiz appeared on two pages"


@pytest.mark.django_db
def test_members_queryset_is_ordered():
    """
    DRF warns UnorderedObjectListWarning here because paginating an unordered
    queryset is undefined. Assert the order exists rather than relying on the
    warning being noticed.
    """
    owner = user("ownerord")
    g = group_with_members(owner, 4)

    assert queryset_of(getGroupMembers, owner, kwargs={"group_id": g.id}).ordered


# ─────────────────────────────────────────────────────────────
# 5. Performance — the database pages, not Python
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_pagination_happens_in_sql_not_in_memory():
    """
    A page must cost one bounded query, not "load the group then slice". With
    LIMIT absent this still returns the right rows, so only the SQL shows the
    difference.
    """
    owner = user("ownersql")
    g = group_with_members(owner, 12)

    with CaptureQueriesContext(connection) as ctx:
        auth(owner).get(reverse("group-members", args=[g.id]), {"page_size": 5})

    selects = [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("SELECT")]
    assert any("LIMIT" in s for s in selects), "no LIMIT — rows were sliced in Python"
    assert any("COUNT" in s.upper() for s in selects), "count was computed by loading rows"


@pytest.mark.django_db
@pytest.mark.parametrize("label", ["groups", "materials", "quizzes", "members"])
def test_query_count_does_not_grow_with_page_size(label):
    """
    A page now holds 50 rows where it held 3, so any per-row query in a
    serializer is ~16x more expensive than it was — on a 0.1 vCPU instance,
    and multiplied again by clients that walk every page.

    This was not hypothetical. Measured on this branch before the fix,
    /materials/ cost 9 queries for 3 rows and 103 for 50: StudyMaterialSerializer
    nests `uploaded_by` and `study_group`, two extra queries per row. Fixing
    the page size without this would have traded a correctness bug for a
    latency bug.

    Asserting the SHAPE (constant, not proportional) rather than an exact
    number, so the test survives an unrelated query being added but still
    fails the moment a relation goes unfetched.
    """
    owner = user(f"ownernplus{label}")
    g = StudyGroup.objects.create(
        name="G", description="d", creator=owner, join_code=f"np{label[:4]}",
        capacity=100,
    )
    for i in range(12):
        member = user(f"np{label}{i}")
        g.members.add(member)
        # Related rows point at DIFFERENT users on purpose: reusing one user
        # lets Django's per-instance cache hide an N+1 that production hits.
        StudyMaterial.objects.create(
            title=f"f{i}", study_group=g, uploaded_by=member, file=f"m/{i}.pdf"
        )
        AssignedQuiz.objects.create(
            study_group=g, topic=f"t{i}", quiz_data={}, assigned_by=member
        )
        other = StudyGroup.objects.create(
            name=f"G{i}", description="d", creator=owner, join_code=f"n{label[:3]}{i}"
        )
        other.members.add(member)

    url, params = {
        "groups": (reverse("studygroup-list"), {}),
        "materials": (reverse("studymaterial-list"), {}),
        "quizzes": (reverse("list-assigned-quizzes"), {"study_group": g.id}),
        "members": (reverse("group-members", args=[g.id]), {}),
    }[label]
    c = auth(owner)

    counts = {}
    for size in (2, 12):
        with CaptureQueriesContext(connection) as ctx:
            r = c.get(url, {**params, "page_size": size})
        assert len(r.data["results"]) == size, f"{label}: fixture too small"
        counts[size] = len(ctx.captured_queries)

    assert counts[12] <= counts[2] + 1, (
        f"{label}: {counts[2]} queries for 2 rows but {counts[12]} for 12 — "
        f"a serializer relation is being fetched per row"
    )


# ─────────────────────────────────────────────────────────────
# 6. Adversarial page/page_size input
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
@pytest.mark.parametrize(
    "page,expected",
    [
        ("0", 404),          # page numbers are 1-based
        ("-1", 404),
        ("999999", 404),     # past the end
        ("abc", 404),
        ("", 200),           # empty falls back to page 1
        ("1", 200),
        ("2.5", 404),
        ("1;DROP TABLE groups_studygroup", 404),
    ],
)
def test_hostile_page_values_are_rejected_without_a_server_error(page, expected):
    owner = user(f"ownerpg{abs(hash(page)) % 10000}")
    g = group_with_members(owner, 12)

    r = auth(owner).get(reverse("group-members", args=[g.id]), {"page": page})

    assert r.status_code == expected, f"page={page!r} -> {r.status_code}"
    assert r.status_code < 500
    assert StudyGroup.objects.filter(id=g.id).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "page_size,expected_len",
    [
        ("0", 12),        # invalid -> class default
        ("-1", 12),
        ("abc", 12),
        ("", 12),
        ("5", 5),
        ("99999", 12),    # clamped by max_page_size, still only 12 exist
    ],
)
def test_hostile_page_size_values_fall_back_or_clamp(page_size, expected_len):
    owner = user(f"ownerps{abs(hash(page_size)) % 10000}")
    g = group_with_members(owner, 12)

    r = auth(owner).get(
        reverse("group-members", args=[g.id]), {"page_size": page_size}
    )

    assert r.status_code == 200, f"page_size={page_size!r} -> {r.status_code}"
    assert len(r.data["results"]) == expected_len


@pytest.mark.django_db
def test_page_size_cannot_exceed_max_page_size():
    """
    `page_size` is client-controlled, so it is a resource-exhaustion lever if
    unbounded. max_page_size is the ceiling.
    """
    owner = user("ownercap")
    g = group_with_members(owner, 12)

    r = auth(owner).get(
        reverse("group-members", args=[g.id]), {"page_size": "10000000"}
    )

    assert r.status_code == 200
    paginator = LargePagination()
    assert paginator.max_page_size < 10000000


# ─────────────────────────────────────────────────────────────
# 7. Security — paging changed size, not who can see what
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_a_bigger_page_does_not_widen_what_a_stranger_sees():
    """
    The authorization filter runs in get_queryset(), i.e. before pagination.
    Raising the page size must not turn a 3-row leak into a 50-row leak — a
    non-member must still see nothing.
    """
    owner = user("victimowner")
    stranger = user("stranger")
    g = group_with_members(owner, 12)
    for i in range(12):
        StudyMaterial.objects.create(
            title=f"secret{i}", study_group=g, uploaded_by=owner, file=f"m/{i}.pdf"
        )
    c = auth(stranger)

    members = c.get(reverse("group-members", args=[g.id]))
    files = c.get(reverse("studymaterial-list"))
    quizzes = c.get(reverse("list-assigned-quizzes"), {"study_group": g.id})

    assert members.data["count"] == 0
    assert files.data["count"] == 0
    assert quizzes.data["count"] == 0


@pytest.mark.django_db
def test_a_member_sees_the_roster_a_stranger_cannot():
    """
    Pairs with the test above: proves the zeros there are authorization, not a
    broken fixture that would report zero for everyone.
    """
    owner = user("realowner")
    g = group_with_members(owner, 12)

    assert auth(owner).get(reverse("group-members", args=[g.id])).data["count"] == 12


@pytest.mark.django_db
def test_paging_past_the_end_does_not_bypass_the_authorization_filter():
    owner = user("victimowner2")
    stranger = user("stranger2")
    g = group_with_members(owner, 12)

    r = auth(stranger).get(
        reverse("group-members", args=[g.id]), {"page": 1, "page_size": 1000}
    )

    assert r.data["results"] == []


@pytest.mark.django_db
def test_two_users_paging_the_same_endpoint_never_see_each_others_rows():
    """
    The adversarial case pagination makes possible: authorization is applied
    in get_queryset(), so it is correct only if EVERY page is drawn from the
    filtered queryset. A page-2 request that re-derived its window from an
    unfiltered queryset would leak — and would be invisible on page 1, which
    is the page every casual test checks.

    Both users hold data here, so a filter that returned nothing for everyone
    would fail the completeness half of the assertion.
    """
    alice, bob = user("alicepg"), user("bobpg")
    rows = {}
    for owner, tag in ((alice, "A"), (bob, "B")):
        g = StudyGroup.objects.create(
            name=f"{tag}", description="d", creator=owner, join_code=f"x{tag}1"
        )
        rows[tag] = {
            StudyMaterial.objects.create(
                title=f"{tag}{i}", study_group=g, uploaded_by=owner,
                file=f"m/{tag}{i}.pdf",
            ).id
            for i in range(12)
        }

    url = reverse("studymaterial-list")
    seen = {}
    for owner, tag in ((alice, "A"), (bob, "B")):
        c = auth(owner)
        page1 = c.get(url, {"page_size": 5, "page": 1})
        page2 = c.get(url, {"page_size": 5, "page": 2})
        page3 = c.get(url, {"page_size": 5, "page": 3})
        seen[tag] = {
            m["id"] for r in (page1, page2, page3) for m in r.data["results"]
        }
        assert page1.data["count"] == 12, f"{tag}: count leaks other rows"

    assert seen["A"] == rows["A"]
    assert seen["B"] == rows["B"]
    assert seen["A"].isdisjoint(seen["B"]), "a page leaked another user's rows"


@pytest.mark.django_db
def test_deep_paging_as_a_non_member_returns_nothing_on_every_page():
    """
    Paging past the end must not fall out of the authorization filter — the
    404 for an out-of-range page has to come from an EMPTY authorized
    queryset, not from a populated one the caller should not see.
    """
    owner, stranger = user("victimdeep"), user("strangerdeep")
    g = group_with_members(owner, 12)
    for i in range(12):
        StudyMaterial.objects.create(
            title=f"secret{i}", study_group=g, uploaded_by=owner, file=f"m/{i}.pdf"
        )
    c = auth(stranger)

    for page in (1, 2, 3, 99):
        members = c.get(reverse("group-members", args=[g.id]),
                        {"page": page, "page_size": 5})
        files = c.get(reverse("studymaterial-list"), {"page": page, "page_size": 5})
        for r in (members, files):
            assert r.status_code in (200, 404), f"page={page} -> {r.status_code}"
            if r.status_code == 200:
                assert r.data["results"] == [], f"page={page} leaked rows"
                assert r.data["count"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("total", [0, 1, 2, 49, 50, 51, 101])
def test_every_row_is_reachable_at_page_boundaries(total):
    """
    Off-by-one at a page boundary is the classic pagination defect, and the
    sizes that expose it are the ones either side of the page size — 49/50/51
    against the default of 50. A client that walks `next` must end up with
    exactly `total` distinct rows, no duplicates and none missing.
    """
    owner = user(f"ownerbound{total}")
    g = StudyGroup.objects.create(
        name="G", description="d", creator=owner, join_code=f"bd{total}"
    )
    created = {
        StudyMaterial.objects.create(
            title=f"f{i}", study_group=g, uploaded_by=owner, file=f"m/{i}.pdf"
        ).id
        for i in range(total)
    }

    walked = collect_all_pages(auth(owner), reverse("studymaterial-list"))

    ids = [m["id"] for m in walked]
    assert len(ids) == total, f"{total} rows -> walked {len(ids)}"
    assert len(set(ids)) == total, "a row appeared on two pages"
    assert set(ids) == created, "walked set differs from what was created"


@pytest.mark.django_db
def test_list_endpoints_still_require_authentication():
    owner = user("ownerauth")
    g = group_with_members(owner, 4)
    anon = APIClient()

    for url in (
        reverse("group-members", args=[g.id]),
        reverse("studymaterial-list"),
        reverse("list-assigned-quizzes"),
        reverse("studygroup-list"),
    ):
        assert anon.get(url).status_code == 401, url
