import os

file_path = 'LearnLM/groups/ai_services.py'
with open(file_path, 'a', encoding='utf-8') as f:
    f.write('''

def generate_full_question(title):
    """
    Generates full LeetCode-style problem description, python starter code, and test cases.
    """
    print(f"🤖 Booting up AI to generate full question for: {title}...")
    
    prompt = f"""
You are an expert competitive programming backend judge. 
Generate a complete coding problem definition for a problem titled: {title}

You MUST respond with ONLY a raw, valid JSON object. Do not include markdown formatting like ```json or backticks.
The JSON object must have exactly these three keys:
1. "content": A plain text string (with \\n for newlines, DO NOT use markdown asterisks or backticks) describing the problem, constraints, and 2-3 examples.
2. "starter_code": A python 3 class Solution with the correct method signature.
3. "hidden_test_cases": A JSON array of 4 test cases in the format: [{{"stdin": "input_string", "expected_output": "output_string"}}]

Format Example:
{{
    "content": "Given an array of integers nums and an integer target...\\n\\nExample 1:\\nInput: nums = [2,7,11,15], target = 9\\nOutput: [0,1]\\n...",
    "starter_code": "class Solution:\\n    def twoSum(self, nums: list[int], target: int) -> list[int]:\\n        pass",
    "hidden_test_cases": [
        {{"stdin": "[2,7,11,15]\\n9", "expected_output": "[0,1]"}}
    ]
}}
"""
    
    try:
        from groq import Groq
        from django.conf import settings
        import json
        
        # We try to use Groq first for fast JSON generation
        groq_client = Groq(api_key=settings.GROQ_API_KEY)
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        
        clean_text = response.choices[0].message.content.replace('```json', '').replace('```', '').strip()
        return json.loads(clean_text)
    except Exception as e:
        print(f"❌ AI Question Generation Failed: {e}")
        return None
''')
print('Successfully appended generate_full_question to ai_services.py')
