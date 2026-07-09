import os

filepath = r"C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backend\LearnLM\groups\coding_views.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Add _compute_xai at the bottom of NextProblemView
compute_xai_code = '''
    def _compute_xai(self, user, topic_name, hlr_state):
        if USE_REAL_SHAP:
            from .engines.gnn_engine import TrueGCNKnowledgeGraph
            from .engines.shap_explainer import XAIEngine
            import torch
            gnn_model = TrueGCNKnowledgeGraph()
            background = torch.zeros((10, 4))
            xai_engine = XAIEngine(gnn_model, background)
            user_tensor = TensorBuilder.build_user_feature_tensor(user, topic_name)
            feature_names = ['Time Complexity', 'Space Complexity', 'Logic Accuracy', 'Topic Recency']
            payload = xai_engine.generate_radar_data(user_tensor, feature_names)
            payload['source'] = 'shap'
            return payload

        tensor = TensorBuilder.build_user_feature_tensor(user, topic_name)
        time_score = round(float(tensor[0]) * 100, 1)
        space_score = round(float(tensor[1]) * 100, 1)
        logic_score = round(float(tensor[2]) * 100, 1)
        recency_score = round(float(tensor[3]) * 100, 1)

        if hlr_state < 0.50:
            dominant = 'Topic Recency'
        else:
            scores = {
                'Time Complexity': time_score,
                'Space Complexity': space_score,
                'Logic Accuracy': logic_score,
                'Topic Recency': recency_score,
            }
            dominant = max(scores, key=scores.get)

        return {
            'source': 'heuristic',
            'dominant_factor': dominant,
            'success_probability': round(logic_score * hlr_state, 1),
            'shap_values': [
                {'subject': 'Time Complexity', 'A': time_score, 'fullMark': 100},
                {'subject': 'Space Complexity', 'A': space_score, 'fullMark': 100},
                {'subject': 'Logic Accuracy', 'A': logic_score, 'fullMark': 100},
                {'subject': 'Topic Recency', 'A': recency_score, 'fullMark': 100},
            ],
        }
'''
if "_compute_xai" not in content:
    content = content.replace("class CodingOnboardingView(APIView):", compute_xai_code + "\nclass CodingOnboardingView(APIView):")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
