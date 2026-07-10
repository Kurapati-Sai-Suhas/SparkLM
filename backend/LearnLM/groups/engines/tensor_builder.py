from django.utils import timezone
from groups.models import UserTopicMastery, CodeSubmission

class TensorBuilder:
    @staticmethod
    def build_user_feature_tensor(user, topic_name):
        """
        Converts Django database metrics into a normalized feature list:
        [Time_Efficiency, Space_Efficiency, Logic_Accuracy, Topic_Recency]

        Returns a plain list of floats — torch-free on purpose, so the
        default (heuristic) XAI path never loads PyTorch. The SHAP path
        converts to a tensor at its own boundary. This keeps slim
        deployments (without the ML extras installed) bootable.
        """
        # 1. Fetch Mastery Data
        try:
            mastery = UserTopicMastery.objects.get(user=user, topic__name=topic_name)
            accuracy_norm = float(mastery.accuracy)  # Assuming it's already 0.0 to 1.0
            
            # Recency: Normalize based on a 14-day window
            days_since = (timezone.now() - mastery.last_practiced).days
            recency_norm = max(0.0, 1.0 - (days_since / 14.0)) 
        except UserTopicMastery.DoesNotExist:
            accuracy_norm = 0.5
            recency_norm = 0.0  # Cold start

        # 2. Fetch Recent Submissions for Time/Space averages
        recent_subs = CodeSubmission.objects.filter(
            user=user, 
            status='accepted' # Only average successful runs
        ).order_by('-submitted_at')[:5]

        if recent_subs.exists():
            avg_time = sum(s.execution_time_ms for s in recent_subs if s.execution_time_ms) / len(recent_subs)
            avg_space = sum(s.memory_used_kb for s in recent_subs if s.memory_used_kb) / len(recent_subs)
            
            # Normalize Time (Assume 50ms is perfect 1.0, 500ms is worst 0.0)
            time_norm = max(0.0, min(1.0, 1.0 - ((avg_time - 50) / 450)))
            
            # Normalize Space (Assume 20MB is perfect 1.0, 60MB is worst 0.0)
            space_norm = max(0.0, min(1.0, 1.0 - ((avg_space - 20000) / 40000)))
        else:
            time_norm = 0.5
            space_norm = 0.5

        # 3. Compile the feature vector
        # IMPORTANT: The order here MUST match the feature_names array in your views!
        return [float(time_norm), float(space_norm), float(accuracy_norm), float(recency_norm)]