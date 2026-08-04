import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from .models import DirectMessage, StudyGroup

User = get_user_model()


class GroupChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time Study Group chat.
    
    URL pattern: ws://host/ws/chat/<group_id>/
    Each group gets its own channel group: "chat_<group_id>"
    All members connected to that group receive every message instantly.
    """

    # False until connect() has proved membership. Every inbound frame is
    # gated on it — see receive(). A class attribute so the guard holds even
    # for an instance whose connect() returned early.
    authorized = False

    async def connect(self):
        self.group_id = self.scope['url_route']['kwargs']['group_id']
        self.room_group_name = f"chat_{self.group_id}"
        self.user = self.scope["user"]

        # Reject unauthenticated connections immediately
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        # Check user is actually a member of this group
        is_member = await self.check_membership(self.user, self.group_id)
        if not is_member:
            await self.close(code=4003)
            return

        self.authorized = True

        # Join the channel group (Redis pub/sub room)
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

        # Broadcast join event to the group
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "user_join",
                "username": self.user.username,
                "user_id": self.user.id,
            }
        )

        # Send last 30 messages to the newly connected user
        history = await self.get_chat_history(self.group_id)
        await self.send(text_data=json.dumps({
            "type": "history",
            "messages": history
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            # Broadcast leave event
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "user_leave",
                    "username": self.user.username,
                    "user_id": self.user.id,
                }
            )
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        """Called when a message arrives from the WebSocket client."""
        # `room_group_name` is assigned before the membership check in
        # connect(), so a refused instance still holds a valid room name and
        # would broadcast into it if it were ever handed a frame. An ASGI
        # server will not do that — a handshake closed before accept() never
        # becomes a socket — but that is the server's behaviour, not ours,
        # and authorization must not rest on it (M4 WP4).
        if not self.authorized:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type", "message")

        if msg_type == "message":
            # 👇 CRITICAL FIX: Look for 'message' or 'text' first, fallback to 'content'
            content = data.get("message", data.get("text", data.get("content", ""))).strip()
            
            if not content:
                return

            # Save message to DB
            saved = await self.save_message(
                user=self.user,
                group_id=self.group_id,
                content=content
            )

            # Broadcast to everyone in the group
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "chat_message",
                    "message_id": saved["id"],
                    "content": content,
                    "username": self.user.username,
                    "user_id": self.user.id,
                    "timestamp": saved["timestamp"],
                }
            )

        elif msg_type == "typing":
            # Broadcast typing indicator (not saved to DB)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "typing_indicator",
                    "username": self.user.username,
                    "user_id": self.user.id,
                    "is_typing": data.get("is_typing", False),
                }
            )

    # ── Event handlers (called by channel layer) ─────────────

    async def chat_message(self, event):
        """Sends a chat message to the WebSocket."""
        await self.send(text_data=json.dumps({
            "type": "message",
            "message_id": event["message_id"],
            "message": event["content"], # 👇 CRITICAL FIX: Send it back to React as "message"
            "username": event["username"],
            "user_id": event["user_id"],
            "timestamp": event["timestamp"],
        }))

    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            "type": "user_join",
            "username": event["username"],
            "user_id": event["user_id"],
        }))

    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            "type": "user_leave",
            "username": event["username"],
            "user_id": event["user_id"],
        }))

    async def typing_indicator(self, event):
        # Don't send typing indicator back to the person who is typing
        if event["user_id"] != self.user.id:
            await self.send(text_data=json.dumps({
                "type": "typing",
                "username": event["username"],
                "is_typing": event["is_typing"],
            }))

    # ── DB helpers (run in thread pool) ──────────────────────

    @database_sync_to_async
    def check_membership(self, user, group_id):
        # Delegates to common.authorization (M4 WP1) so the socket and the
        # REST API share one definition of membership. Behaviour is
        # unchanged: the old inline version was the same predicate written
        # by hand, including the missing-group case.
        from common.authorization import is_group_member
        return is_group_member(user, group_id)

    @database_sync_to_async
    def save_message(self, user, group_id, content):
        from .models import GroupMessage
        msg = GroupMessage.objects.create(
            group_id=group_id,
            sender=user,
            content=content,
        )
        return {
            "id": msg.id,
            "timestamp": msg.timestamp.strftime("%H:%M"),
        }

    @database_sync_to_async
    def get_chat_history(self, group_id):
        from .models import GroupMessage
        messages = GroupMessage.objects.filter(
            group_id=group_id
        ).select_related('sender').order_by('-timestamp')[:30]

        return [
            {
                "message_id": m.id,
                "message": m.content, # 👇 CRITICAL FIX: Load history as "message"
                "username": m.sender.username,
                "user_id": m.sender.id,
                "timestamp": m.timestamp.strftime("%H:%M"),
            }
            for m in reversed(list(messages))
        ]


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    Personal notification channel for each user.
    URL: ws://host/ws/notifications/
    Used to push: new friend requests, quiz assignments, group invites.
    """

    async def connect(self):
        self.user = self.scope["user"]

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.personal_group = f"notifications_{self.user.id}"
        await self.channel_layer.group_add(self.personal_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'personal_group'):
            await self.channel_layer.group_discard(self.personal_group, self.channel_name)

    async def receive(self, text_data):
        pass  # Notifications are server-push only

    async def send_notification(self, event):
        await self.send(text_data=json.dumps({
            "type": "notification",
            "title": event["title"],
            "message": event["message"],
            "category": event.get("category", "info"),
        }))

class CodeCollabConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for Real-Time Collaborative Coding (CRDT-ready).
    URL pattern: ws://host/ws/code/<group_id>/

    Authorization is enforced ONLY at connect, deliberately. A WebSocket
    room is a capability: `group_add` is what places this socket in the
    broadcast set, so a socket that never joins can neither read the
    broadcast stream nor write into it, and `receive` cannot be reached
    without a completed handshake. Re-checking membership per frame would
    be theatre on the read/write path and a database round-trip per
    keystroke on a 0.1 vCPU instance.

    The one case that gate does not cover is a membership revoked *during*
    a live session; that socket keeps its room until it reconnects. Same
    behaviour as GroupChatConsumer, and the same as a JWT outliving a
    permission change — noted in the sprint's Remaining Risks rather than
    papered over here.
    """
    # See GroupChatConsumer.authorized — same guard, same reason.
    authorized = False

    async def connect(self):
        self.group_id = self.scope['url_route']['kwargs']['group_id']
        self.room_group_name = f"code_collab_{self.group_id}"
        self.user = self.scope["user"]

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        # Membership check (M4 WP4). Without it, any authenticated user
        # could join any group's live editor by id — reading the shared
        # buffer and broadcasting arbitrary code into it. GroupChatConsumer,
        # 200 lines above, has always done this; this consumer was written
        # later and never got it.
        if not await self.check_membership(self.user, self.group_id):
            await self.close(code=4003)
            return

        self.authorized = True
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    @database_sync_to_async
    def check_membership(self, user, group_id):
        # Same predicate as every REST endpoint (common.authorization), so
        # the socket and the API cannot disagree about who is a member.
        from common.authorization import is_group_member
        return is_group_member(user, group_id)

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data=None, bytes_data=None):
        """
        Receives Yjs or generic sync payloads and broadcasts to all other clients.
        """
        # Membership gate on every inbound frame — see
        # GroupChatConsumer.receive for why the connect check alone is not
        # sufficient to rely on (M4 WP4).
        if not self.authorized:
            return

        if bytes_data is not None:
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "code_sync",
                    "sender_id": self.channel_name,
                    "payload_bytes": bytes_data
                }
            )
            return

        if text_data is not None:
            # Broadcast the text update payload to everyone else in the room
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "code_sync",
                    "sender_id": self.channel_name,
                    "payload_text": text_data
                }
            )

    async def code_sync(self, event):
        """
        Sends the synced code to the client (ignoring the sender to prevent echo loops).
        """
        if event["sender_id"] != self.channel_name:
            if "payload_bytes" in event:
                await self.send(bytes_data=event["payload_bytes"])
            elif "payload_text" in event:
                await self.send(text_data=event["payload_text"])