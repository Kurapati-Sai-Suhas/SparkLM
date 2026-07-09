import os

artifact_path = r"C:\Users\Suhas\.gemini\antigravity\brain\c5cac0e0-0a4d-457e-b32a-e17f773ce5f5\hybrid_router_srs_extended.md"
base_dir = r"C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backend\LearnLM\groups"

def read_file(name):
    with open(os.path.join(base_dir, name), "r", encoding="utf-8") as f:
        return f.read()

models_code = read_file("models.py")
views_code = read_file("views.py")
coding_views_code = read_file("coding_views.py")
hybrid_router_code = read_file("hybrid_router.py")

part1 = """# Comprehensive Software Requirements Specification (SRS)
## LearnLM Hybrid Router & Adaptive Execution Engine

**Version:** 4.0.0 (Extended Research Edition - Dynamically Generated)
**Date:** July 4, 2026
**Scope:** Core Architecture, Methodology, Deep Learning Orchestration, and Execution Codebase.

---

## 1. Executive Summary
The LearnLM platform is an advanced, AI-driven educational portal designed to teach algorithmic problem-solving and software engineering concepts through an adaptive, gamified interface. At the core of this platform lies the **Hybrid Router Engine**—a sophisticated orchestration layer that dynamically routes students through a curriculum graph based on their instantaneous cognitive state, historical performance, and psychometric profiles.

This document serves as the definitive reference for the mathematical models, algorithmic workflows, theoretical research foundations, and raw source code that power the LearnLM routing and execution ecosystem.

---

## 2. Research Methodology & Gap Analysis

The development of the Hybrid Router was driven by identifying critical gaps in modern Educational Data Mining (EDM) and Intelligent Tutoring Systems (ITS). 

### 2.1 Theoretical Foundations
The architecture is heavily inspired by four primary paradigms in EDM:

1. **Item Response Theory (IRT)** 
   - *Reference: Lord, F. M. (1980). Applications of Item Response Theory to Practical Testing Problems.*
   - *Concept:* Models the probability of a correct response as a function of the student's latent ability (\\theta) and item parameters (difficulty b, discrimination a, guessing c).
2. **Deep Knowledge Tracing (DKT)**
   - *Reference: Piech, C., et al. (2015). "Deep Knowledge Tracing." NIPS.*
   - *Concept:* Uses Long Short-Term Memory (LSTM) networks to model the sequence of a student's learning trajectory, capturing hidden cognitive states over time.
3. **Graph Convolutional Networks (GCN) for Prerequisite Propagation**
   - *Reference: Chen, P., et al. (2018). "Prerequisite-driven Deep Knowledge Tracing."*
   - *Concept:* Embeds domain knowledge as a Directed Acyclic Graph (DAG) and uses spectral convolutions to predict mastery on unseen nodes based on spatial proximity.
4. **Bayesian Knowledge Tracing (BKT)**
   - *Reference: Corbett, A. T., & Anderson, J. R. (1994). "Knowledge tracing: Modeling the acquisition of procedural knowledge."*
   - *Concept:* A Hidden Markov Model updating the probability that a student has mastered a specific skill.

### 2.2 The Research Gap & LearnLM's Solution
**The Gap:** Traditional platforms (like LeetCode or standard LMSs) use static difficulty tags (Easy/Medium/Hard) and rely on the student to self-direct their learning (Flat Routing). Advanced EDM systems use DKT or IRT but treat questions as isolated entities, failing to account for *concept drift* (forgetting a prerequisite topic, which logically guarantees failure on dependent topics). 

**The Solution:** LearnLM introduces the **Hybrid Router**. Instead of forcing a single paradigm, it uses a shallow Machine Learning "Traffic Cop" (Logistic Regression with Gemini Embeddings) to evaluate a student's variance. 
- If variance is high (erratic performance), it routes to the **Hierarchical Engine** to rebuild foundational knowledge via DB-backed DAG traversal (`TopicPrerequisite`).
- If variance is low and accuracy is high, it routes to the **Flat Elo Engine** to aggressively scale difficulty and maintain flow state.
- Furthermore, it introduces the **Graph-Decay Cross-Pollination (GDCP)** engine to mathematically decay mastery down the graph.

---

## 3. Global Workflow Architecture

The entire lifecycle of a user interaction follows this strict pipeline:

1. **User Action:** The student writes code for a specific `Question` and submits it via the frontend sandbox.
2. **Execution (`coding_views.py`):** The `SubmitCodeView` intercepts the payload. It fetches the `Question` and its `hidden_wrapper_code` from the DB. It parses the user's code, merges it with the wrapper, and dispatches it to the Judge0 Sandbox API.
3. **Evaluation & Metric Tracking:** Judge0 returns stdout/stderr. The system normalizes the output, compares it to the DB `test_cases`, and calculates execution time and memory. It then invokes the `EloEngine` to update the user's Elo rating.
4. **Data Persistence (`models.py`):** The system logs a `CodeSubmission` row, capturing the raw code, outcome, time spent, and Elo delta.
5. **Adaptive Routing (`views.py` -> `hybrid_router.py`):** The frontend queries the `HybridRouterView` for the next logical step. The router's Traffic Cop evaluates the updated Elo and historical metrics, deciding whether to traverse the dynamic graph or just pick a harder question.

---

## 4. Data Layer (`models.py`)

The relational database architecture is designed to support rapid querying of test cases and longitudinal tracking of user attempts. The latest revision consolidates all `UserProgress` models into `UserTopicMastery` and removes hardcoded graphs in favor of `TopicPrerequisite`.

### 4.1 Models Source Code

```python
"""

part2 = """
```

---

## 5. Execution Engine (`coding_views.py`)

This module is responsible for safely executing untrusted code via Judge0, applying dynamic wrappers, and strictly enforcing the "Two Sum III" Varargs edge cases. It has been fortified with Judge0 transport error handling and strict output formatting.

### 5.1 Judge0 Wrapper Logic & Varargs Patch

```python
"""

part3 = """
```

---

## 6. The Meta-Router Endpoint (`views.py`)

The frontend interacts with `HybridRouterView` to retrieve the next mathematical action state. It validates payloads via `HybridRouterSerializer` and dynamically loads the user's coding history.

### 6.1 Views Source Code

```python
"""

part4 = """
```

---

## 7. The Core Hybrid Router Engine (`hybrid_router.py`)

The absolute centerpiece of the architecture. It merges Elo scaling, Topological sorting (via DB graph queries), and Logistic Regression, returning rich JSON objects featuring `explainability`.

### 7.1 Hybrid Router Source Code

```python
"""

part5 = """
```

---

## 8. Deep Learning Orchestration (`ai_services.py`)

```python
import google.generativeai as genai
import os

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def get_gemini_embedding(text: str):
    try:
        result = genai.embed_content(
            model="models/embedding-001",
            content=text,
            task_type="retrieval_document"
        )
        return result['embedding']
    except Exception as e:
        return [0.0] * 768
```

---

## 9. Base API Infrastructure (`urls.py` & `serializers.py`)

To ensure exhaustive code representation, here is the baseline infrastructure that binds the Hybrid Router APIs to the client logic, enforcing strict validation parameters.

### 9.1 Payload Serializers (`serializers.py`)
```python
from rest_framework import serializers
from .models import Question

ALLOWED_LANGUAGES = ['python', 'java', 'cpp', 'javascript']

class CodeSubmitSerializer(serializers.Serializer):
    problem_id = serializers.IntegerField()
    code = serializers.CharField()
    language = serializers.CharField()
    test_cases = serializers.ListField(
        child=serializers.DictField(), 
        required=False,
        default=list
    )

    def validate_problem_id(self, value):
        if not Question.objects.filter(id=value).exists():
            raise serializers.ValidationError("Unknown problem_id")
        return value

    def validate_language(self, value):
        lang = value.lower()
        if lang not in ALLOWED_LANGUAGES:
            raise serializers.ValidationError(f"Unsupported language: {value}")
        return lang

class HybridRouterSerializer(serializers.Serializer):
    subject = serializers.CharField()
    mastered_topics = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list
    )
    elo_rating = serializers.FloatField(required=False, default=1200.0)
    question_difficulty = serializers.FloatField(required=False, allow_null=True)
    got_correct = serializers.BooleanField(required=False, allow_null=True)
```

## 10. Conclusion

The Hybrid Router architecture is a robust, mathematically profound system combining standard web APIs (Django/Judge0) with cutting-edge EDM theory. By blending Elo-driven Flat Routing with Topological Hierarchical Routing—governed by Logistic Regression and bolstered by dynamic Java Sandbox Reflection—the platform ensures an optimal, zero-crash flow state for the learner while maintaining granular psychometric profiles.

"""

with open(artifact_path, "w", encoding="utf-8") as out:
    out.write(part1)
    out.write(models_code)
    out.write(part2)
    out.write(coding_views_code)
    out.write(part3)
    out.write(views_code)
    out.write(part4)
    out.write(hybrid_router_code)
    out.write(part5)

print(f"Artifact successfully updated at {artifact_path}")
