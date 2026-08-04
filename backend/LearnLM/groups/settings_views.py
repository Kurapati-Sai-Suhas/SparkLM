import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Profile
from .serializers import UserSerializer
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)

class ProfileSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # `bio` is a field on User, not Profile (Profile has skills,
        # achievements, major, graduation_year, email_alerts). Reading
        # profile.bio raised AttributeError, so this endpoint returned 500
        # on GET *and* PUT — the settings page has never worked.
        #
        # Fixed by pointing at the real field rather than adding a second
        # one: a Profile.bio column would leave two places to write the same
        # value, and UserSerializer already exposes User.bio.
        profile, _ = Profile.objects.get_or_create(user=request.user)
        return Response({
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "email": request.user.email,
            "bio": request.user.bio or "",
            "email_alerts": profile.email_alerts
        })

    def put(self, request):
        profile, _ = Profile.objects.get_or_create(user=request.user)
        user = request.user

        user.first_name = request.data.get("first_name", user.first_name)
        user.last_name = request.data.get("last_name", user.last_name)
        user.bio = request.data.get("bio", user.bio)

        if "email" in request.data:
            # Validated through UserSerializer rather than assigned raw.
            # A duplicate address used to reach the database and surface as
            # an IntegrityError 500; the unique constraint held, so this was
            # never a takeover, but the caller got a server error instead of
            # a field error. `partial` so this stays a single-field update.
            serializer = UserSerializer(
                user, data={"email": request.data["email"]}, partial=True
            )
            serializer.is_valid(raise_exception=True)
            user.email = serializer.validated_data["email"]

        user.save()

        profile.email_alerts = request.data.get("email_alerts", profile.email_alerts)
        profile.save()

        return Response({"status": "success"})

class TestEmailView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            send_mail(
                subject='LearnLM Alert - Email Connected!',
                message='Your LearnLM account is now successfully connected to Gmail SMTP for notifications.',
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[request.user.email],
                fail_silently=False,
            )
            return Response({"status": "Email sent successfully!"})
        except Exception as e:
            return Response({"status": "error", "message": str(e)}, status=500)
