"""
common.authorization — the single definition of "what may this user reach?"

Milestone 4 found the same defect eight times: a queryset that described
what EXISTS rather than what the CALLER MAY REACH. `StudyMaterial.objects.all()`
on a ModelViewSet, `Document.objects.filter(file_type='image')` with no group,
`StudyGroup.objects.get(id=group_id)` in four separate views. Each was fixed
locally, and each local fix was a fresh opportunity to get the predicate
subtly wrong.

This module exists so there is exactly one predicate per resource, imported
everywhere, and so a reviewer can answer "is this endpoint scoped?" by
looking for one import rather than reading a queryset.

It is a CONSOLIDATION, not a redesign. The predicates below are byte-for-byte
the ones StudyGroupViewSet has always enforced:

    a group  is reachable if you are a member OR its creator
    a material is reachable if its group is reachable OR you uploaded it

`uploaded_by` is in the material predicate so leaving a group does not lock
you out of a document you uploaded yourself.

Resolvers vs querysets
----------------------
`accessible_*` return querysets, for list endpoints and `get_queryset`.
`resolve_*` return one object or None, for detail lookups by client-supplied
id. Always prefer the resolvers for single objects: they centralise the
`(TypeError, ValueError)` guard that a non-numeric id would otherwise turn
into a 500, and they return None for "absent" and "not yours" alike so
callers cannot accidentally build an enumeration oracle by distinguishing
the two.
"""

from django.db.models import Q

from groups.models import Document, StudyGroup, StudyMaterial


def accessible_groups(user):
    """StudyGroups `user` belongs to. The membership predicate."""
    return StudyGroup.objects.filter(
        Q(members=user) | Q(creator=user)
    ).distinct()


def accessible_materials(user):
    """
    StudyMaterials `user` may read.

    `.distinct()` is defensive, not load-bearing today: a user appears at
    most once in a group's `members`, so the OR across the many-to-many join
    yields one row even when they are simultaneously member, creator and
    uploader. Mutation testing confirmed removing it breaks no test. It is
    kept because that guarantee is a property of the data, not of the query
    — a second membership row, or another OR'd join added later, would
    duplicate and turn `.get()` into MultipleObjectsReturned.
    """
    return StudyMaterial.objects.filter(
        Q(study_group__members=user)
        | Q(study_group__creator=user)
        | Q(uploaded_by=user)
    ).distinct()


def accessible_documents(user):
    """Visual-search Documents `user` may reach, via their group."""
    return Document.objects.filter(
        Q(group__members=user) | Q(group__creator=user)
    ).distinct()


def _resolve(queryset, pk):
    """
    One row from an already-scoped queryset, or None.

    The `(TypeError, ValueError)` guard is the reason this is a function and
    not an inline `.filter(pk=pk).first()`: `.filter(pk='abc')` raises when
    the queryset is evaluated, which without this became a 500 on an
    endpoint whose whole job was to answer 404.
    """
    if pk is None or pk == '':
        return None
    try:
        return queryset.filter(pk=pk).first()
    except (TypeError, ValueError):
        return None


def resolve_group(user, group_id):
    """A group `user` may reach, or None. None means absent OR forbidden."""
    return _resolve(accessible_groups(user), group_id)


def resolve_material(user, material_id):
    """A material `user` may read, or None."""
    return _resolve(accessible_materials(user), material_id)


def is_group_member(user, group_id):
    """
    Membership as a boolean, for callers that are not querysets — the
    WebSocket consumers, which have a group id from the URL route and need
    an accept/reject decision rather than a row.
    """
    if not user or not user.is_authenticated:
        return False
    return accessible_groups(user).filter(pk=group_id).exists()
