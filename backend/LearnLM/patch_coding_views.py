import os

filepath = r"C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backend\LearnLM\groups\coding_views.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Imports
imports = '''from .engines.agentic_coach import trigger_agentic_coach
from .ai_services import generate_test_cases
from .utils import normalize_output
from .engines.tensor_builder import TensorBuilder
USE_REAL_SHAP = os.environ.get('ENABLE_SHAP_XAI', 'false') == 'true'
'''
content = content.replace('from .engines.agentic_coach import trigger_agentic_coach\nfrom .ai_services import generate_test_cases', imports)

# 2. Normalize output
old_norm = '''            # 🚀 AI FORMATTING FIX: Split by line and trim trailing whitespace to avoid masking actual bugs
            expected_lines = [line.rstrip() for line in expected.splitlines()]
            actual_lines = [line.rstrip() for line in actual.splitlines()]
            expected_norm = "\\n".join(expected_lines)
            actual_norm = "\\n".join(actual_lines)'''
new_norm = '''            # 🚀 AI FORMATTING FIX: Split by line and trim trailing whitespace to avoid masking actual bugs
            expected_norm = normalize_output(expected)
            actual_norm = normalize_output(actual)'''
content = content.replace(old_norm, new_norm)

# 3. GDCP
old_gdcp = '''                penalties = GDCPEngine.propagate_decay(graph, question.topic.name, base_decay=0.15) # Increased base decay for faster fallback
                for desc_node, penalty in penalties.items():
                    desc_topic = Topic.objects.filter(name=desc_node).first()
                    if desc_topic:
                        desc_mastery, _ = UserTopicMastery.objects.get_or_create(user=request.user, topic=desc_topic)
                        desc_mastery.accuracy = max(0.0, desc_mastery.accuracy - penalty)
                        desc_mastery.save()'''
new_gdcp = '''                penalties = GDCPEngine.propagate_decay(graph, question.topic.name, base_decay=0.1) 
                for desc_node, penalty in penalties.items():
                    desc_topic = Topic.objects.filter(name=desc_node).first()
                    if desc_topic:
                        desc_mastery, _ = UserTopicMastery.objects.get_or_create(user=request.user, topic=desc_topic)
                        desc_mastery.accuracy = max(0.0, desc_mastery.accuracy - penalty)
                        desc_mastery.save(update_fields=['accuracy'])'''
content = content.replace(old_gdcp, new_gdcp)

# 4. Exhaustion Loop
old_exhaustion = '''        if not question:
            question = Question.objects.exclude(id__in=solved_ids).first()
            if not question:
                return Response({"error": "You have solved every problem in the database!"}, status=404)'''
new_exhaustion = '''        if not question:
            unsolved_qs = Question.objects.exclude(id__in=solved_ids)
            if not unsolved_qs.exists():
                return Response({
                    'status': 'completed',
                    'message': 'You have solved all available problems! New problems coming soon.',
                    'mastery_percentage': 100.0,
                    'next_problem': None,
                }, status=200)
            question = unsolved_qs.first()'''
content = content.replace(old_exhaustion, new_exhaustion)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
