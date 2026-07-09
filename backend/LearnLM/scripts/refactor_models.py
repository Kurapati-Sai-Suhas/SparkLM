import os
import re

model_path = r"C:\Users\Suhas\OneDrive\Documents\Notes\Project1683\LearnLM\backend\LearnLM\groups\models.py"

with open(model_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace User foreign keys with settings.AUTH_USER_MODEL
# We only want to replace ForeignKey(User, ManyToManyField(User, OneToOneField(User
content = re.sub(r'ForeignKey\(User,', 'ForeignKey(settings.AUTH_USER_MODEL,', content)
content = re.sub(r'ManyToManyField\(User,', 'ManyToManyField(settings.AUTH_USER_MODEL,', content)
content = re.sub(r'OneToOneField\(User,', 'OneToOneField(settings.AUTH_USER_MODEL,', content)

# 1. Convert CodeSubmission.problem_id to ForeignKey(Question)
# from: problem_id = models.CharField(max_length=100)
# to: question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="submissions", null=True, blank=True)
content = content.replace(
    'problem_id = models.CharField(max_length=100)',
    'question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="submissions", null=True, blank=True)'
)

# 2. Add TopicPrerequisite model below Topic
topic_prereq_code = """
class TopicPrerequisite(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='prerequisites')
    prerequisite = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='unlocks')
    
    class Meta:
        unique_together = ('topic', 'prerequisite')

    def __str__(self):
        return f"{self.prerequisite.name} -> {self.topic.name}"
"""
content = content.replace(
    'class Question(models.Model):',
    topic_prereq_code + '\n\nclass Question(models.Model):'
)

# 3. Consolidate UserTopicMastery and UserProgress
# Remove UserProgress entirely, modify UserTopicMastery to use ForeignKey(Topic) instead of string
content = re.sub(r'class UserProgress\(models\.Model\):.*?class UserTopicMastery\(models\.Model\):', 'class UserTopicMastery(models.Model):', content, flags=re.DOTALL)
content = content.replace(
    "topic = models.CharField(max_length=100)",
    "topic = models.ForeignKey(Topic, on_delete=models.CASCADE)"
)
# We also have subject in UserTopicMastery, let's keep it or remove it? The reviewer said to use ForeignKey(Topic).
content = content.replace(
    "subject = models.CharField(max_length=100, default=\"Data Structures\")\n",
    ""
)
content = content.replace(
    "unique_together = ('user', 'subject', 'topic')",
    "unique_together = ('user', 'topic')"
)

# 4. Remove bio from Profile
content = content.replace(
    "bio = models.TextField(blank=True, null=True)\n",
    ""
)

with open(model_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated models.py!")
