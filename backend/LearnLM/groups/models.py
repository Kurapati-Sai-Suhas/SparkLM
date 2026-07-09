from django.db import models
from django.contrib.auth.models import AbstractUser
from datetime import timedelta, timezone
from django.conf import settings
from django.utils import timezone
from pgvector.django import VectorField
import networkx as nx
from django.core.exceptions import ValidationError


def default_deadline():
    return timezone.now() + timedelta(days=7)


class User(AbstractUser):
    email = models.EmailField(unique=True)
    skillset = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True, null=True)
    university = models.CharField(max_length=100, blank=True, null=True)
    ROLE_CHOICES = (
        ('student', 'Student'),
        ('admin', 'Admin'),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='student')

    def __str__(self):
        return self.username


class StudyGroup(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_groups')
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='joined_groups', blank=True)
    join_code = models.CharField(max_length=10, unique=True)
    capacity = models.IntegerField(default=50)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # 🚀 V2 ARCHITECTURE: Groups can now subscribe to multiple global portals
    active_portals = models.ManyToManyField('CodingPortal', related_name='subscribed_groups', blank=True)

    def __str__(self):
        return self.name


class Subject(models.Model):
    name = models.CharField(max_length=50)
    groups = models.ManyToManyField(StudyGroup, related_name='subjects')

    def __str__(self):
        return self.name


class StudyMaterial(models.Model):
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to='study_materials/')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='files')
    upload_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class UserActivityLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    activity_type = models.CharField(max_length=100)
    timestamp = models.DateTimeField(default=timezone.now)
    last_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.activity_type} : {self.timestamp}"


class UserActivity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    section_name = models.CharField(max_length=255)
    time_spent = models.DurationField(default=timedelta(seconds=0))
    last_active = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'section_name')

    def __str__(self):
        return f"{self.user.username} - {self.section_name}: {self.time_spent}"


class QuizResult(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, null=True, blank=True)
    score = models.IntegerField()
    total_questions = models.IntegerField(default=5)
    topic = models.CharField(max_length=200)
    date_taken = models.DateTimeField(auto_now_add=True)
    AssignedQuiz = models.ForeignKey('AssignedQuiz', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.topic}: {self.score}"


class AssignedQuiz(models.Model):
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE)
    topic = models.CharField(max_length=200)
    quiz_data = models.JSONField()
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField(default=default_deadline)

    def __str__(self):
        return f"{self.study_group.name} - {self.topic}"


class DoubtChatHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    material = models.ForeignKey(StudyMaterial, on_delete=models.CASCADE)
    question = models.TextField()
    answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class Connection(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    )
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_requests', on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_requests', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('sender', 'receiver')

    def __str__(self):
        return f"{self.sender.username} -> {self.receiver.username} ({self.status})"


class DirectMessage(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_messages', on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_messages', on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"From {self.sender.username} to {self.receiver.username}"


class Document(models.Model):
    FILE_TYPE_CHOICES = [
        ("image", "Image"),
        ("pdf", "PDF"),
        ("code", "Code Snippet"),
        ("other", "Other"),
    ]
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="documents")
    title = models.CharField(max_length=300)
    file_url = models.URLField(max_length=500, blank=True)
    file = models.FileField(upload_to="documents/", blank=True, null=True)
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default="other")
    feature_vector = VectorField(dimensions=512, null=True, blank=True)
    vector_extracted_at = models.DateTimeField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.title} ({self.file_type})"

    @property
    def has_vector(self):
        return self.feature_vector is not None and self.feature_vector != ""

    def is_image(self):
        return self.file_type == "image"


# ── Module C: Adaptive Coding Portal (V2 Global Architecture) ────────────────────────

class CodingPortal(models.Model):
    """
    The Global Hub Entity. Holds master courses like "DSA Masterclass" or "Deep Learning Fundamentals".
    """
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Topic(models.Model):
    # 🚀 Topics now belong to a specific Global Portal
    portal = models.ForeignKey(CodingPortal, on_delete=models.CASCADE, related_name='topics', null=True, blank=True)
    
    STRUCTURE_CHOICES = [
        ('hierarchical', 'Hierarchical (GNN)'),
        ('flat', 'Flat (Elo)')
    ]
    name = models.CharField(max_length=100, unique=True)
    structure_type = models.CharField(max_length=20, choices=STRUCTURE_CHOICES)
    
    def __str__(self):
        portal_name = self.portal.name if self.portal else "Unassigned"
        return f"[{portal_name}] {self.name}"



class TopicPrerequisite(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='prerequisites')
    prerequisite = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='unlocks')
    
    class Meta:
        unique_together = ('topic', 'prerequisite')

    def clean(self):
        if self.topic_id == self.prerequisite_id:
            raise ValidationError("A topic cannot be its own prerequisite.")
            
        graph = nx.DiGraph(
            list(TopicPrerequisite.objects.exclude(pk=self.pk).values_list('prerequisite_id', 'topic_id'))
        )
        graph.add_edge(self.prerequisite_id, self.topic_id)
        if not nx.is_directed_acyclic_graph(graph):
            raise ValidationError("This prerequisite would create a cycle in the curriculum graph.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.prerequisite.name} -> {self.topic.name}"


class Question(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    content = models.TextField() 
    base_difficulty = models.FloatField(default=1200.0) 
    boilerplate_code = models.JSONField(default=dict)
    hidden_test_cases = models.JSONField(default=list)
    hidden_wrapper_code = models.JSONField(default=dict, blank=True)
    
    def __str__(self):
        return self.title


class UserCodingProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coding_profile")
    elo_rating = models.FloatField(default=1200.0)
    irt_latent_logic = models.FloatField(default=0.0) # Theta value for logic
    irt_latent_syntax = models.FloatField(default=0.0) # Theta value for syntax
    irt_latent_optimization = models.FloatField(default=0.0) # Theta value for optimization
    total_submissions = models.IntegerField(default=0)
    successful_submissions = models.IntegerField(default=0)
    current_streak = models.IntegerField(default=0)
    highest_streak = models.IntegerField(default=0)
    last_active_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} — Elo: {self.elo_rating:.0f}"

    @property
    def success_rate(self):
        if self.total_submissions == 0:
            return 0.0
        return round(self.successful_submissions / self.total_submissions * 100, 1)


class CodeSubmission(models.Model):
    STATUS_CHOICES = [
        ("accepted",      "Accepted"),
        ("wrong_answer",  "Wrong Answer"),
        ("time_limit",    "Time Limit Exceeded"),
        ("runtime_error", "Runtime Error"),
        ("compile_error", "Compile Error"),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="submissions", null=True, blank=True)
    language = models.CharField(max_length=20)
    code = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    execution_time_ms = models.IntegerField(null=True, blank=True)
    memory_used_kb = models.IntegerField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]


# ── AI Analytics ──────────────────────────────────────────────

class UserTopicMastery(models.Model):
    """
    Tracks a user's specific performance on individual topics to feed the PyTorch Tensor.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='topic_mastery')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    
    accuracy = models.FloatField(default=0.0)  
    reviews = models.IntegerField(default=0)
    elo_rating = models.FloatField(default=1200.0)
    
    # Half-Life Regression Fields
    hlr_halflife = models.FloatField(default=1.0)
    hlr_alpha = models.FloatField(default=1.0)

    last_practiced = models.DateTimeField(default=timezone.now)
    # Checkpoint for inactivity Elo decay (FIX-05): when a penalty was last
    # charged, so repeated decay sweeps don't re-charge the same window.
    last_decay_applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'topic')

    def __str__(self):
        return f"{self.user.username} - {self.topic} ({self.elo_rating})"


class AgenticCoachLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='coach_logs')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="coach_logs_on_question", null=True, blank=True)
    failed_attempts_count = models.IntegerField(default=3)
    generated_hint = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    webhook_fired = models.BooleanField(default=False)
    # Observability for the coach pipeline (FIX-06): where the hint came
    # from ('llm' via n8n webhook, or 'fallback') and how long n8n took.
    hint_source = models.CharField(max_length=20, default='fallback')
    webhook_latency_ms = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"Hint for {self.user.username} on problem {self.question.id if self.question else 'Unknown'}"


class RecommendationLog(models.Model):
    """
    Phase 1 Production Flywheel: Captures real AI recommendations and user outcomes.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recommendation_logs')
    recommended_topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    engine_used = models.CharField(max_length=50) # 'hierarchical' or 'flat'
    predicted_success_prob = models.FloatField(null=True, blank=True)
    problem_id = models.CharField(max_length=100, null=True, blank=True) # The specific problem recommended
    actual_result_correct = models.BooleanField(null=True, blank=True) # Populated after they submit
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} -> {self.recommended_topic} ({self.engine_used})"


# ── WebSocket Group Chat ─────────────────────────────────────

class GroupMessage(models.Model):
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='group_messages')
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"[{self.group.name}] {self.sender.username}: {self.content[:40]}"
    
class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    skills = models.CharField(max_length=255, blank=True, null=True)
    achievements = models.TextField(blank=True, null=True)
    major = models.CharField(max_length=100, blank=True, null=True)
    graduation_year = models.IntegerField(blank=True, null=True)
    email_alerts = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    description = models.TextField()
    type = models.CharField(max_length=50) # 'system', 'course', 'message', 'achievement'
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

class StudySession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='study_sessions')
    title = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    duration_minutes = models.IntegerField(default=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']

    def __str__(self):
        return f"{self.user.username} - {self.title} at {self.start_time}"

# ── Gamification ──────────────────────────────────────────────

class Badge(models.Model):
    badge_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    color = models.CharField(max_length=20, default="primary")
    icon_name = models.CharField(max_length=50, default="Award") 

    def __str__(self):
        return self.name


class UserBadge(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='earned_badges')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE)
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'badge')
        ordering = ['-awarded_at']

    def __str__(self):
        return f"{self.user.username} earned {self.badge.name}"