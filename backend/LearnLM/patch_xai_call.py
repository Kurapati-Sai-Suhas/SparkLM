import os

filepath = r"C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backend\LearnLM\groups\coding_views.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Replace the giant if-else block in NextProblemView with the clean call:
import re
# We need to compute hlr_state right before XAI payload
new_xai_call = '''        from .engines.hlr_engine import HLREngine
        hlr_state = HLREngine.calculate_memory_state(
            time_since_last_review_days=0,
            halflife=mastery.hlr_halflife if hasattr(mastery, 'hlr_halflife') else 1.0,
        )
        xai_payload = self._compute_xai(request.user, question.topic.name if question else 'fallback', hlr_state)
        advanced_data = {"xai": xai_payload, "decay_info": {"decay_percent": 0}}
'''

# Find the start of XAI COMPONENT for hierarchical and flat
content = re.sub(r'# 🚀 XAI COMPONENT: Dynamic Computation.*?(?=question = Question\.objects\.filter)', new_xai_call, content, flags=re.DOTALL)
content = re.sub(r'# 🚀 XAI COMPONENT \(FLAT ELO ENGINE\): Dynamic Computation.*?(?=if topic:)', new_xai_call, content, flags=re.DOTALL)


with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
