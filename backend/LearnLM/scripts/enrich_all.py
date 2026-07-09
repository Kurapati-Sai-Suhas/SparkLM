import os
import django
import sys
import re

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

from groups.models import Question

def camel_case(s):
    s = re.sub(r'[^a-zA-Z0-9 ]', '', s)
    words = s.split()
    if not words: return "solve"
    return words[0].lower() + ''.join(w.capitalize() for w in words[1:])

def enrich_all():
    print("Enriching ALL questions in DB...")
    questions = Question.objects.all()
    count = 0
    for q in questions:
        # 1. Update Content (Description)
        if "Problem description not provided" in q.content or not q.content:
            q.content = f"""<p>In this problem, you are tasked with solving the <strong>{q.title}</strong> challenge.</p>
<p>You must optimize your approach to handle edge cases and scale efficiently. Consider the time and space complexity of your algorithm.</p>
<br/>
<p><strong>Example 1:</strong></p>
<pre><strong>Input:</strong> Check hidden test cases in sandbox
<strong>Output:</strong> Expected optimal result
</pre>"""

        # 2. Update Boilerplate
        if not q.boilerplate_code or "solve" in q.boilerplate_code.get('python', ''):
            func_name = camel_case(q.title)
            q.boilerplate_code = {
                "python": f"class Solution:\n    def {func_name}(self, *args, **kwargs):\n        # TODO: Implement your solution for {q.title.strip()}\n        pass",
                "java": f"class Solution {{\n    public Object {func_name}(Object... args) {{\n        // TODO: Implement your solution for {q.title.strip()}\n        return null;\n    }}\n}}",
                "cpp": f"class Solution {{\npublic:\n    void {func_name}() {{\n        // TODO: Implement your solution for {q.title.strip()}\n    }}\n}};"
            }
        
        q.save()
        count += 1
        
    print(f"Successfully enriched {count} questions!")

if __name__ == "__main__":
    enrich_all()
