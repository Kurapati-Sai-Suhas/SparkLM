from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import RecommendationLog

class MLOpsTelemetryView(APIView):
    """
    GET /api/mlops/telemetry/
    Returns recent logs from Phase 1 Data Flywheel.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        logs = RecommendationLog.objects.all().order_by('-created_at')[:50]
        
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
