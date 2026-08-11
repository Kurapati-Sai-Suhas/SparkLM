"""
ReferenceSolution is grading truth and must be unreachable (M2 P2.5, Phase 5).

The model was split out of `Question` on the argument that a model with no
serializer, no viewset and no route is STRUCTURALLY unable to leak. That
argument is only worth anything if something checks it, because the leak this
phase fixed was in a model with no secret fields at all — nobody was careless,
the risk simply was not being asserted.

So these tests are mostly structural, and deliberately so. A behavioural test
can only probe endpoints someone thought to write; the structural ones fail on
the *introduction* of a serializer or route, which is the moment the mistake is
cheap to undo.

They scan real source and the live URLconf rather than a hand-maintained list.
"""

import inspect
import json
import pkgutil
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import get_resolver, reverse
from rest_framework import serializers as drf_serializers
from rest_framework.test import APIClient

from groups.conftest import approved_reference
from groups.models import CodingPortal, Question, ReferenceSolution, Topic

User = get_user_model()

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SECRET_SOURCE = "SECRET_REFERENCE_IMPLEMENTATION_8823"


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Ref Portal")
    topic, _ = Topic.objects.get_or_create(
        name="RefTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return Question.objects.create(
        title="Ref Problem", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )


@pytest.fixture
def reference(question):
    return approved_reference(
        question, language="python",
        source_code=f"# {SECRET_SOURCE}\nprint(input())",
    )


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="refsnoop", password="Ref#2026x", email="r@t.com"
    )


def python_sources():
    for path in BACKEND_ROOT.rglob("*.py"):
        parts = set(path.parts)
        if parts & {"migrations", "__pycache__", "scripts"}:
            continue
        if path.name.startswith("test_"):
            continue
        yield path, path.read_text(encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────
# Structural: no serializer, no route, no admin
# ─────────────────────────────────────────────────────────────

def test_no_drf_serializer_declares_reference_solution():
    """
    Walks every DRF serializer actually defined in the project and checks its
    Meta.model, rather than grepping — a serializer could be built by a
    factory or subclass and a grep would miss it.
    """
    import groups.serializers  # noqa: F401
    import common  # noqa: F401

    offenders = []
    for module in list(pkgutil.iter_modules([str(BACKEND_ROOT / "groups")])) + \
                  list(pkgutil.iter_modules([str(BACKEND_ROOT / "common")])):
        name = module.name
        if name.startswith("test_"):
            continue
        for package in ("groups", "common"):
            try:
                mod = __import__(f"{package}.{name}", fromlist=["*"])
            except Exception:
                continue
            for attr_name, attr in vars(mod).items():
                if not inspect.isclass(attr):
                    continue
                if not issubclass(attr, drf_serializers.BaseSerializer):
                    continue
                model = getattr(getattr(attr, "Meta", None), "model", None)
                if model is ReferenceSolution:
                    offenders.append(f"{package}.{name}.{attr_name}")

    assert offenders == [], (
        f"ReferenceSolution is serialised by: {offenders}. Grading truth must "
        f"have no serializer at all."
    )


def test_no_url_route_resolves_to_reference_solution():
    """
    Walks the live URLconf. A route is how the model would become reachable
    even with no serializer — a plain view returning `.source_code` is enough.
    """
    offenders = []

    def walk(patterns, prefix=""):
        for entry in patterns:
            if hasattr(entry, "url_patterns"):
                walk(entry.url_patterns, prefix + str(entry.pattern))
                continue
            route = prefix + str(entry.pattern)
            callback = entry.callback
            view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            queryset = getattr(view_class, "queryset", None)
            model = getattr(queryset, "model", None)
            if model is ReferenceSolution or "reference" in route.lower():
                offenders.append(route)

    walk(get_resolver().url_patterns)

    assert offenders == [], f"routes expose ReferenceSolution: {offenders}"


def test_reference_solution_is_not_registered_in_admin():
    """
    Admin is staff-only, but registering the model would create a rendered
    HTML surface for the answer key whose only protection is one boolean on a
    user row. The tooling that reads reference solutions is a management
    command; none of it needs a web view.
    """
    from django.contrib import admin

    assert ReferenceSolution not in admin.site._registry, (
        "ReferenceSolution was registered in admin — the answer key now has a "
        "web surface"
    )


def test_no_view_or_service_returns_reference_source_in_a_response():
    """
    Source-level guard on the dangerous combination: a module that both reads
    `source_code` off a reference solution and builds an HTTP Response.
    Generation and validation tooling may read it; request handlers may not.
    """
    offenders = []
    for path, source in python_sources():
        if "reference_solutions" not in source and "ReferenceSolution" not in source:
            continue
        if "source_code" not in source:
            continue
        if "Response(" in source or "JsonResponse" in source:
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert offenders == [], (
        f"these modules read a reference solution AND build responses: {offenders}"
    )


def test_the_frontend_has_no_reference_solution_consumer():
    frontend = BACKEND_ROOT.parent.parent / "studysphere-ai-11" / "src"
    if not frontend.exists():
        pytest.skip("frontend tree not present")

    hits = [
        str(p) for p in frontend.rglob("*.ts*")
        if "reference_solution" in p.read_text(encoding="utf-8", errors="replace").lower()
        or "referenceSolution" in p.read_text(encoding="utf-8", errors="replace")
    ]

    assert hits == [], f"frontend references the grading oracle: {hits}"


# ─────────────────────────────────────────────────────────────
# Behavioural: a learner cannot pull it out of any live endpoint
# ─────────────────────────────────────────────────────────────

def test_the_problem_endpoint_never_carries_the_reference_source(
    learner, question, reference, monkeypatch
):
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")
    client = APIClient()
    client.force_authenticate(user=learner)

    response = client.get(reverse("code-next-problem"), {"topic": "RefTopic"})

    assert SECRET_SOURCE not in response.content.decode()


def test_the_submit_response_never_carries_the_reference_source(
    learner, question, reference, monkeypatch
):
    from groups import coding_views
    monkeypatch.setattr(coding_views, "_run_on_judge0", lambda *a, **k: {
        "status": "Accepted", "status_id": 3, "stdout": "1", "stderr": "",
        "compile_output": "", "time": "0.01", "memory": 1000,
    })
    client = APIClient()
    client.force_authenticate(user=learner)

    response = client.post(
        reverse("code-submit"),
        {"problem_id": question.id, "language": "python", "code": "print(1)"},
        format="json",
    )

    assert SECRET_SOURCE not in response.content.decode()


@pytest.mark.parametrize("param", [
    "reference_solution", "reference", "solution", "include", "expand", "fields",
])
def test_query_parameter_fishing_does_not_expand_the_payload(
    learner, question, reference, monkeypatch, param
):
    """
    The obvious probe an attacker runs first: ask the endpoint to include it.
    DRF-style `expand`/`fields` parameters are exactly how a model with no
    serializer still ends up in a response.
    """
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")
    client = APIClient()
    client.force_authenticate(user=learner)

    response = client.get(
        reverse("code-next-problem"),
        {"topic": "RefTopic", param: "reference_solutions"},
    )

    assert SECRET_SOURCE not in response.content.decode()


def test_guessing_reference_solution_urls_finds_nothing(learner, reference):
    """
    URL manipulation against the shapes a REST API would plausibly expose.
    404/405 are both acceptable; a 200 carrying the source is not.
    """
    client = APIClient()
    client.force_authenticate(user=learner)

    for path in (
        f"/api/reference-solutions/{reference.id}/",
        f"/api/reference_solutions/{reference.id}/",
        f"/api/code/reference/{reference.question_id}/",
        f"/api/questions/{reference.question_id}/reference/",
        f"/api/code/solution/{reference.question_id}/",
    ):
        response = client.get(path)
        assert response.status_code in (401, 403, 404, 405), (
            f"{path} -> {response.status_code}"
        )
        assert SECRET_SOURCE not in response.content.decode()


def test_the_secret_never_appears_in_the_agentic_hint(
    learner, question, reference, monkeypatch
):
    """
    The coach is the one learner-facing surface that reads freely from
    grading context, so it is the most plausible accidental carrier.
    """
    from groups import coding_views
    monkeypatch.setattr(coding_views, "_run_on_judge0", lambda *a, **k: {
        "status": "Wrong Answer", "status_id": 4, "stdout": "nope", "stderr": "",
        "compile_output": "", "time": "0.01", "memory": 1000,
    })
    client = APIClient()
    client.force_authenticate(user=learner)

    for _ in range(4):
        response = client.post(
            reverse("code-submit"),
            {"problem_id": question.id, "language": "python", "code": "print(0)"},
            format="json",
        )
        assert SECRET_SOURCE not in response.content.decode()


# ─────────────────────────────────────────────────────────────
# Integrity
# ─────────────────────────────────────────────────────────────

def test_only_one_active_reference_per_language(question):
    """
    Two active rows make "the" reference output ambiguous, and the
    reconciliation report would silently depend on row order.
    """
    from django.db import IntegrityError, transaction

    approved_reference(question, language="python", source_code="a")
    # Approved but not yet canonical. Since P2.7d the collision can only
    # happen at ACTIVATION — creating a second row is now harmless, because
    # a new row is DRAFT and inactive.
    second = approved_reference(
        question, language="python", source_code="b", active=False)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            second.activate()


def test_a_superseded_solution_may_coexist_with_an_active_one(question):
    """The other half: history is retained, so a mismatch stays explainable."""
    old = approved_reference(
        question, language="python", source_code="old", active=False)
    new = approved_reference(question, language="python", source_code="new")

    assert ReferenceSolution.objects.filter(question=question).count() == 2
    assert not old.is_active and new.is_active


def test_different_languages_may_both_be_active(question):
    approved_reference(question, language="python", source_code="p")
    approved_reference(question, language="cpp", source_code="c")

    assert ReferenceSolution.objects.filter(
        question=question, is_active=True
    ).count() == 2


def test_deleting_a_question_removes_its_reference_solutions(question, reference):
    """No orphaned grading truth pointing at a question that no longer exists."""
    Question.objects.filter(pk=question.pk).delete()

    assert not ReferenceSolution.objects.filter(pk=reference.pk).exists()
