from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from .models import RecommendationLog

class MLOpsTelemetryView(APIView):
    """
    GET /api/mlops/telemetry/
    Returns recent logs from Phase 1 Data Flywheel.

    Staff-only: rows contain other users' usernames and success/failure
    outcomes — cross-user performance data that must not be enumerable by
    students (frozen architecture §13.4).
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        logs = (
            RecommendationLog.objects
            .select_related('user', 'recommended_topic')  # one query, not 1+2N
            .order_by('-created_at')[:50]
        )
        
        data = []
        for log in logs:
            data.append({
                "id": log.id,
                "user": log.user.username,
                "topic": log.recommended_topic.name,
                "engine": log.engine_used,
                "predicted_prob": log.predicted_success_prob,
                "actual_result": log.actual_result_correct,
                "created_at": log.created_at.strftime("%H:%M:%S %d-%b"),
            })
            
        stats = {
            "total_logs_captured": RecommendationLog.objects.count(),
            "gnn_routes": RecommendationLog.objects.filter(engine_used='hierarchical').count(),
            "elo_routes": RecommendationLog.objects.filter(engine_used='flat').count()
        }
        
        return Response({
            "stats": stats,
            "recent_logs": data
        })
