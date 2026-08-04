from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import DirectMessage, User, Connection
from django.db.models import Q


def are_connected(user, other):
    """
    True if `user` and `other` have an accepted Connection (M4 WP5).

    Direction-agnostic: a friendship is one row, and which side sent the
    request is irrelevant to whether they may message each other.
    """
    return Connection.objects.filter(
        (Q(sender=user, receiver=other) | Q(sender=other, receiver=user)),
        status='accepted',
    ).exists()

class DirectMessageFriendsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        connections = Connection.objects.filter(
            (Q(sender=request.user) | Q(receiver=request.user)) & Q(status='accepted')
        )
        friends_data = []
        for conn in connections:
            friend = conn.receiver if conn.sender == request.user else conn.sender
            
            last_msg = DirectMessage.objects.filter(
                (Q(sender=request.user, receiver=friend) | Q(sender=friend, receiver=request.user))
            ).order_by('-timestamp').first()

            friends_data.append({
                "id": str(friend.id),
                "name": f"{friend.first_name} {friend.last_name}".strip() or friend.username,
                "lastMessage": last_msg.content if last_msg else "Say hi!",
                "time": last_msg.timestamp.strftime("%I:%M %p") if last_msg else "",
                "unread": DirectMessage.objects.filter(sender=friend, receiver=request.user, is_read=False).count(),
                "online": True
            })
            
        if not friends_data:
            friends_data.append({
                "id": "999999",
                "name": "LearnLM Assistant",
                "lastMessage": "Welcome! I'm your AI study buddy.",
                "time": "Just now",
                "unread": 1,
                "online": True
            })
            
        return Response(friends_data)

class DirectMessageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, friend_id):
        if str(friend_id) == "999999":
            return Response([{
                "id": "m_system_1",
                "text": "Welcome to LearnLM Direct Messaging! This is a preview of the chat interface since you don't have any connections yet.",
                "time": "Just now",
                "fromMe": False,
                "read": False
            }])

        try:
            friend = User.objects.get(id=friend_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        # Reading was already safe — the query below is filtered to the
        # pair, so a stranger's thread returns an empty list rather than
        # anyone else's messages. The connection check is applied anyway so
        # read and write agree on who counts as a correspondent, and so a
        # blocked/removed friend cannot keep polling the thread.
        if not are_connected(request.user, friend):
            return Response({"error": "User not found"}, status=404)

        DirectMessage.objects.filter(sender=friend, receiver=request.user, is_read=False).update(is_read=True)

        messages = DirectMessage.objects.filter(
            (Q(sender=request.user, receiver=friend) | Q(sender=friend, receiver=request.user))
        ).order_by('timestamp')

        return Response([{
            "id": f"m{msg.id}",
            "text": msg.content,
            "time": msg.timestamp.strftime("%I:%M %p"),
            "fromMe": msg.sender == request.user,
            "read": msg.is_read
        } for msg in messages])

    def post(self, request, friend_id):
        content = request.data.get("text")
        if not content:
            return Response({"error": "Text is required"}, status=400)
            
        if str(friend_id) == "999999":
            return Response({
                "id": "m_dummy_new",
                "text": content,
                "time": "Just now",
                "fromMe": True,
                "read": True
            })

        try:
            friend = User.objects.get(id=friend_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        # An accepted Connection is required to send (M4 WP5). Without it,
        # any authenticated user could deliver messages to any user id —
        # measured: 200, and the row was written. 404 rather than 403 so a
        # stranger's id is indistinguishable from a nonexistent one.
        if not are_connected(request.user, friend):
            return Response({"error": "User not found"}, status=404)

        msg = DirectMessage.objects.create(
            sender=request.user,
            receiver=friend,
            content=content
        )
        return Response({
            "id": f"m{msg.id}",
            "text": msg.content,
            "time": msg.timestamp.strftime("%I:%M %p"),
            "fromMe": True,
            "read": False
        })
