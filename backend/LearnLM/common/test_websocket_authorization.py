"""
WebSocket authorization (M4 security sprint, WP4).

`CodeCollabConsumer` checked only `is_authenticated`, so any logged-in user
could open `ws://host/ws/code/<any_group_id>/`, read the shared editor
buffer, and broadcast arbitrary code into another group's session.
`GroupChatConsumer` — same file, written earlier — had the membership check
all along. Consumers sat outside every previous review because the audit
surface was "endpoints", and a consumer is not a URL in urls.py.

The four properties the brief names:

    connect    a non-member is closed with code 4003
    read       a refused socket never reaches `group_add`, so the channel
               layer has no route to it and it receives nothing
    write      `receive` is unreachable without a completed handshake
    broadcast  a member's message reaches members and nobody else

read/write/broadcast follow from the connect gate, but they are asserted as
observable behaviour rather than by reasoning —
`test_a_non_member_never_receives_a_members_broadcast` is what fails if
`group_add` is ever moved before the membership check.

Driven with `async_to_sync` rather than pytest-asyncio, which is not a
dependency of this project and is not worth adding for one file. Each test
runs its whole scenario inside a single async function so the communicator's
event loop survives for the life of the socket.
"""

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from groups.models import StudyGroup


@pytest.fixture(autouse=True)
def _in_memory_channel_layer(settings):
    """No Redis in tests; broadcast still goes through a real channel layer."""
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }


@database_sync_to_async
def _make_world():
    from django.contrib.auth import get_user_model
    User = get_user_model()

    owner = User.objects.create_user(
        username="ws_owner", password="Ws#2026xyz", email="o@t.com")
    member = User.objects.create_user(
        username="ws_member", password="Ws#2026xyz", email="m@t.com")
    outsider = User.objects.create_user(
        username="ws_outsider", password="Ws#2026xyz", email="x@t.com")

    group = StudyGroup.objects.create(
        name="WS group", description="d", creator=owner, capacity=10)
    group.members.add(owner, member)

    # A real group of their own, so a refusal is about THIS group rather
    # than about the user being a nobody.
    own = StudyGroup.objects.create(
        name="Outsider", description="d", creator=outsider, capacity=10)
    own.members.add(outsider)

    return owner, member, outsider, group.pk


async def _open(consumer_cls, kind, group_pk, user):
    comm = WebsocketCommunicator(consumer_cls.as_asgi(), f"/ws/{kind}/{group_pk}/")
    comm.scope["url_route"] = {"kwargs": {"group_id": str(group_pk)}}
    comm.scope["user"] = user
    connected, close_code = await comm.connect()
    return comm, connected, close_code


async def _open_code(group_pk, user):
    from groups.consumers import CodeCollabConsumer
    return await _open(CodeCollabConsumer, "code", group_pk, user)


async def _open_chat(group_pk, user):
    from groups.consumers import GroupChatConsumer
    return await _open(GroupChatConsumer, "chat", group_pk, user)


pytestmark = pytest.mark.django_db(transaction=True)


# ── CodeCollabConsumer: connect ──────────────────────────────────────────

def test_a_member_can_connect_to_the_collab_editor():
    async def scenario():
        _, member, _, group_pk = await _make_world()
        comm, connected, _ = await _open_code(group_pk, member)
        await comm.disconnect()
        return connected

    assert async_to_sync(scenario)(), "a group member was refused their own editor"


def test_the_creator_can_connect_to_the_collab_editor():
    async def scenario():
        owner, _, _, group_pk = await _make_world()
        comm, connected, _ = await _open_code(group_pk, owner)
        await comm.disconnect()
        return connected

    assert async_to_sync(scenario)()


def test_a_non_member_is_refused_the_collab_editor():
    """The vulnerability. Before the fix this connected successfully."""
    async def scenario():
        _, _, outsider, group_pk = await _make_world()
        comm, connected, close_code = await _open_code(group_pk, outsider)
        await comm.disconnect()
        return connected, close_code

    connected, close_code = async_to_sync(scenario)()

    assert not connected, "a non-member joined another group's live editor"
    assert close_code == 4003


def test_an_anonymous_socket_is_refused_the_collab_editor():
    async def scenario():
        _, _, _, group_pk = await _make_world()
        comm, connected, close_code = await _open_code(group_pk, AnonymousUser())
        await comm.disconnect()
        return connected, close_code

    connected, close_code = async_to_sync(scenario)()

    assert not connected
    assert close_code == 4001


def test_a_nonexistent_group_is_refused():
    """Absent and forbidden give the same close code — no room-id oracle."""
    async def scenario():
        _, member, _, _ = await _make_world()
        comm, connected, close_code = await _open_code(999999, member)
        await comm.disconnect()
        return connected, close_code

    connected, close_code = async_to_sync(scenario)()

    assert not connected
    assert close_code == 4003


# ── CodeCollabConsumer: read, write, broadcast ───────────────────────────

def test_a_non_member_never_receives_a_members_broadcast():
    """
    read + broadcast. The refused socket never reaches `group_add`, so the
    channel layer has no route to it. This is the test that fails if the
    membership check is ever moved after the join.
    """
    async def scenario():
        _, member, outsider, group_pk = await _make_world()

        insider, ok, _ = await _open_code(group_pk, member)
        intruder, joined, _ = await _open_code(group_pk, outsider)

        await insider.send_to(text_data='{"code": "print(42)"}')
        silent = await intruder.receive_nothing(timeout=0.4)

        await insider.disconnect()
        await intruder.disconnect()
        return ok, joined, silent

    ok, joined, silent = async_to_sync(scenario)()

    assert ok and not joined
    assert silent, "a non-member received another group's code broadcast"


def test_a_refused_socket_cannot_write_into_the_room():
    """
    write. A rejected socket is closed, so `receive` is unreachable —
    anything it sends must not reach the room. Proven by having a
    legitimate member listen and observe nothing.
    """
    async def scenario():
        _, member, outsider, group_pk = await _make_world()

        intruder, joined, _ = await _open_code(group_pk, outsider)
        listener, ok, _ = await _open_code(group_pk, member)

        try:
            await intruder.send_to(text_data='{"code": "malicious()"}')
        except Exception:
            pass  # already closed; what matters is what the member sees

        silent = await listener.receive_nothing(timeout=0.4)

        await listener.disconnect()
        await intruder.disconnect()
        return ok, joined, silent

    ok, joined, silent = async_to_sync(scenario)()

    assert ok and not joined
    assert silent, "a refused socket broadcast into the room"


def test_two_members_can_still_collaborate():
    """The feature must survive the fix."""
    async def scenario():
        owner, member, _, group_pk = await _make_world()

        a, ok_a, _ = await _open_code(group_pk, owner)
        b, ok_b, _ = await _open_code(group_pk, member)

        await a.send_to(text_data='{"code": "shared-edit"}')
        received = await b.receive_from(timeout=2)

        await a.disconnect()
        await b.disconnect()
        return ok_a, ok_b, received

    ok_a, ok_b, received = async_to_sync(scenario)()

    assert ok_a and ok_b
    assert "shared-edit" in received


# ── GroupChatConsumer: unchanged after the WP1 refactor ──────────────────

def test_group_chat_still_admits_members():
    async def scenario():
        _, member, _, group_pk = await _make_world()
        comm, connected, _ = await _open_chat(group_pk, member)
        await comm.disconnect()
        return connected

    assert async_to_sync(scenario)()


def test_group_chat_still_refuses_non_members():
    """
    Regression guard for WP1: `check_membership` was rewritten to call
    common.authorization.is_group_member. Same predicate, so the answer must
    be identical.
    """
    async def scenario():
        _, _, outsider, group_pk = await _make_world()
        comm, connected, close_code = await _open_chat(group_pk, outsider)
        await comm.disconnect()
        return connected, close_code

    connected, close_code = async_to_sync(scenario)()

    assert not connected
    assert close_code == 4003
