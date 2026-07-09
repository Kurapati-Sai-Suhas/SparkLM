import os

def read_file(path):
    if not os.path.exists(path):
        return f"// File not found: {path}\n"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

base_dir = r"C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backend\LearnLM\groups"

files_to_compile = {
    "1. Core Router Files": [
        "hybrid_router.py",
        "ai_services.py",
        "engines/elo_engine.py",
        "engines/hlr_engine.py",
        "engines/mirt_engine.py",
        "engines/tensor_builder.py",
    ],
    "2. Data Models": [
        "models.py",
    ],
    "3. Serializer and Validation Layer": [
        "serializers.py",
    ],
    "6. Tests": [
        "tests.py",
        "test_coding_views.py"
    ]
}

output_path = r"C:\Users\Suhas\.gemini\antigravity\brain\c5cac0e0-0a4d-457e-b32a-e17f773ce5f5\HybridRouter_Review_Context.md"

with open(output_path, "w", encoding="utf-8") as out:
    out.write("# Hybrid AI Educational Router - Review Context\n\n")
    
    for section, files in files_to_compile.items():
        out.write(f"## {section}\n\n")
        if section == "3. Serializer and Validation Layer":
            out.write("Note: There is no dedicated serializer for `HybridRouterView`. Payload extraction is handled directly via `request.data.get()` inside the view.\n\n")
            
        for file in files:
            path = os.path.join(base_dir, file.replace("/", "\\"))
            content = read_file(path)
            
            out.write(f"### {file}\n")
            if "File not found" in content:
                out.write(f"{content}\n")
            else:
                out.write("```python\n")
                out.write(content)
                out.write("\n```\n\n")
                
    # Section 4
    out.write("## 4. Subject Graph / Curriculum Definitions\n\n")
    out.write("The prerequisite graphs are defined in `hybrid_router.py` using `networkx.DiGraph()`.\n")
    out.write("See `hybrid_router.py` (lines 14-69) in the core files section for `DSA_GRAPH`, `OS_GRAPH`, and `CN_GRAPH`.\n\n")
    
    # Section 5
    out.write("## 5. Hybrid Routing Logic Explanation\n")
    out.write("""
- **Route Selection**: The `RoutingClassifier.predict_route` takes average accuracy, variance in accuracy, and current Elo. If the scikit-learn model predicts '1', it uses the `hierarchical` route. Otherwise, it uses `flat` (Elo).
- **Logistic Regression Usage**: Yes, `routing_classifier.pkl` is loaded via `joblib`.
- **Missing Model Fallback**: If the `.pkl` file is missing, it falls back to a heuristic: `var_acc > 0.15 or avg_acc < 0.5` goes to `flat` (to re-evaluate boundaries), else `hierarchical`.
- **DKT, IRT, GDCP**: Currently, these are implemented as functional math classes/modules in the codebase (`SequentialKnowledgeTracer`, `IRTEngine`, `GDCPEngine`), but they are *not* actively wired into the `route_recommendation` response payload yet. They are experimental/V2 modules.
- **Mastery Updates**: Updates happen via `UserProgress` (saving Elo) and `UserTopicMastery` (saving accuracy) when code submissions or quiz results are evaluated (e.g., via `elo_engine.py` processing submissions).
- **Persistence**: Real recommendations are persisted into the `RecommendationLog` model for the Phase 1 Flywheel.
""")
    
    # Section 7
    out.write("\n## 7. Example Runtime Flow\n")
    out.write("""
**1. Input Request:**
```json
{
  "subject": "Data Structures",
  "mastered_topics": ["Variables", "Arrays"],
  "elo_rating": 1250,
  "got_correct": true
}
```
**2. Routing Decision:** The `RoutingClassifier` falls back (if no model) and sees consistent accuracy. It selects the `hierarchical` engine because variance is low.
**3. Engine Output:** `HierarchicalEngine` parses `DSA_GRAPH`. Since `Arrays` is mastered, it recommends `Strings` or `LinkedList`.
**4. Final Response:**
```json
{
  "engine_used": "hierarchical_prerequisite_graph",
  "subject": "Data Structures",
  "recommendation": {
    "recommended_topic": "Strings",
    "reason": "All prerequisites satisfied...",
    "prerequisites_needed": ["Arrays"],
    "unlocks": [],
    "mastery_percentage": 11.1
  }
}
```
""")

    # Section 8
    out.write("\n## 8. Known Limitations\n")
    out.write("""
- **Missing Serializer Validation**: The endpoint lacks DRF serializer schemas, risking 500 errors on malformed payloads.
- **Experimental Engines**: DKT, IRT, and GDCP are mathematically implemented but not fully wired into the main API response loop.
- **Hardcoded Graphs**: The subject DAGs are hardcoded dictionaries rather than dynamic database entities.
- **Contradiction in Heuristic**: The heuristic fallbacks to 'flat' for high variance, while the documentation stated hierarchical.
- **Lack of Test Coverage**: There are no unit tests covering the `/api/ai/recommend/` endpoint or the `hybrid_router.py` internals.
""")

print("Successfully compiled Review Context.")
