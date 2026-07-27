"""
common.dashboard_views — one combined payload for the Dashboard page (M8
performance follow-up).

Replaces 5 separate requests (profile, stats, groups, gamification, and a
staff-only MLOps call that 403s for ~everyone) with one. On a CPU-constrained
single instance, requests fired "in parallel" from the browser still queue
for the same limited compute — 5 requests pay the per-request tax 5 times
even though the browser dispatched them together. This view exists purely
to collapse that count to 1; it duplicates a few small, stable queries from
groups/views.py and groups/coding_views.py rather than importing across
those views, so this can be deleted later without touching them.
"""

from django.db.models import Count, Q
from django.core.cache import cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from groups.models import StudyGroup, UserCodingProfile, UserBadge, RecommendationLog


class DashboardBootstrapView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # --- stats (mirrors UserDashboardStats) ---
        active_groups = StudyGroup.objects.filter(Q(members=user) | Q(creator=user)).distinct().count()
        created_groups = StudyGroup.objects.filter(creator=user).count()
        joined_groups = StudyGroup.objects.filter(members=user).count()

        # --- groups list (mirrors the fields Dashboard.tsx actually reads —
        # name, capacity, and a member count. The full /groups/ endpoint
        # nests every member's serialized user object just to let the
        # frontend call .length on it; annotate() gets the same number
        # without materializing those rows at all). ---
        groups_qs = (
            StudyGroup.objects.filter(Q(members=user) | Q(creator=user))
            .distinct()
            .annotate(members_count=Count("members", distinct=True))
            .order_by("-created_at")
            .only("id", "name", "capacity")[:4]
        )
        groups = [
            {"id": g.id, "name": g.name, "capacity": g.capacity, "members_count": g.members_count}
            for g in groups_qs
        ]

        # --- gamification (mirrors GamificationDashboardView, same cache
        # key — whichever endpoint populates it first serves the other). ---
        profile, _ = UserCodingProfile.objects.get_or_create(user=user)
        leaderboard = cache.get("gamification_leaderboard_top3")
        if leaderboard is None:
            top_profiles = UserCodingProfile.objects.select_related("user").order_by("-elo_rating")[:3]
            leaderboard = [
                {
                    "rank": idx + 1,
                    "name": p.user.get_full_name() or p.user.username,
                    "handle": f"@{p.user.username}",
                    "elo": int(p.elo_rating),
                }
                for idx, p in enumerate(top_profiles)
            ]
            cache.set("gamification_leaderboard_top3", leaderboard, timeout=60)

        recent_badges = UserBadge.objects.filter(user=user).select_related("badge").order_by("-awarded_at")[:3]
        badges = [
            {
                "id": ub.badge.badge_id,
                "name": ub.badge.name,
                "description": ub.badge.description,
                "color": ub.badge.color,
                "icon": ub.badge.icon_name,
            }
            for ub in recent_badges
        ]

        payload = {
            "username": user.username,
            "stats": {
                "active_groups": active_groups,
                "created_groups": created_groups,
                "joined_groups": joined_groups,
                "study_hours": 0,
                "quizzes_taken": 0,
                "achievement_points": 100,
            },
            "groups": groups,
            "gamification": {
                "streak": profile.current_streak,
                "leaderboard": leaderboard,
                "badges": badges,
            },
            "mlops": None,
        }

        # Only staff can ever get a non-403 here — decided server-side so
        # the frontend never has to fire a request just to find out.
        if user.is_staff:
            logs = (
                RecommendationLog.objects.select_related("user", "recommended_topic")
                .order_by("-created_at")[:50]
            )
            payload["mlops"] = {
                "stats": {
                    "total_logs_captured": RecommendationLog.objects.count(),
                    "gnn_routes": RecommendationLog.objects.filter(engine_used="hierarchical").count(),
                    "elo_routes": RecommendationLog.objects.filter(engine_used="flat").count(),
                },
                "recent_logs": [
                    {
                        "id": log.id,
                        "user": log.user.username,
                        "topic": log.recommended_topic.name,
                        "engine": log.engine_used,
                        "predicted_prob": log.predicted_success_prob,
                        "actual_result": log.actual_result_correct,
                        "created_at": log.created_at.strftime("%H:%M:%S %d-%b"),
                    }
                    for log in logs
                ],
            }

        return Response(payload)
