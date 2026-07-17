"""
common.review_views — the Review Queue (M7, frozen §6.2).

One endpoint feeds two surfaces:

* the "Due for Review" card — the user's practiced topics ranked by
  predicted recall (worst first), with due flags from learning.memory;
* the Learning Path router panel — the REAL routing decision and
  streakiness telemetry (replacing the static placeholder text that
  previously shipped in the frontend).
"""

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from learning.memory import effective_mastery, is_due, retention


class ReviewQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from groups.hybrid_router import RoutingClassifier, compute_routing_telemetry
        from groups.models import UserCodingProfile, UserTopicMastery

        now = timezone.now()

        items = []
        rows = (
            UserTopicMastery.objects.filter(user=request.user, reviews__gt=0)
            .select_related('topic')
        )
        for m in rows:
            days = max((now - m.last_practiced).total_seconds() / 86400.0, 0.0)
            r = retention(m.hlr_halflife, days)
            items.append({
                "topic": m.topic.name,
                "retention_pct": round(r * 100, 1),
                "accuracy_pct": round(m.accuracy * 100, 1),
                "effective_mastery_pct": round(effective_mastery(m.accuracy, r) * 100, 1),
                "days_since_practice": round(days, 1),
                "due": is_due(m.reviews, r),
            })
        items.sort(key=lambda x: x["retention_pct"])

        # Real router status for the Learning Path panel.
        profile = UserCodingProfile.objects.filter(user=request.user).first()
        elo = profile.elo_rating if profile else 1200.0
        avg_acc, runs_z, sample_size = compute_routing_telemetry(request.user)
        route = RoutingClassifier().predict_route(avg_acc, runs_z, elo / 2000.0)

        return Response({
            "queue": items,
            "due_count": sum(1 for i in items if i["due"]),
            "router": {
                "route": route,
                "avg_acc": round(avg_acc, 3),
                "runs_z": round(runs_z, 2),
                "sample_size": sample_size,
                "elo": round(elo),
            },
        })
