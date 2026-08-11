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
    # M4 Phase A — JWT revocation. Every issued token carries this value as a
    # claim; common.authentication rejects any token whose claim does not
    # match. Bumping it invalidates every token for this user in one write.
    # Free to check, because SimpleJWT already loads this row on every
    # authenticated request. See common/tokens.py.
    token_version = models.PositiveIntegerField(default=0)

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

    # Alphabet excludes look-alikes (0/O, 1/I/L) so a code read aloud or
    # copied off a screen still works — this is shared verbally.
    JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    JOIN_CODE_LENGTH = 10

    @classmethod
    def generate_join_code(cls):
        """
        A unique join code (M4 WP5).

        31**10 ≈ 8.2e14 possibilities, drawn from `secrets`. The codes this
        replaces were chosen by the client — creators typed short memorable
        ones, and one frontend path used Math.random() — and a guessed code
        grants full membership to a group's materials, chat and quizzes.

        Retries on collision rather than trusting the loop: the unique
        constraint on the column is still the authority, and at this
        cardinality a collision is a curiosity, not a design concern.
        """
        import secrets
        for _ in range(10):
            code = "".join(
                secrets.choice(cls.JOIN_CODE_ALPHABET)
                for _ in range(cls.JOIN_CODE_LENGTH)
            )
            if not cls.objects.filter(join_code=code).exists():
                return code
        raise RuntimeError("Could not allocate a unique join code")

    def save(self, *args, **kwargs):
        # Generated here rather than only in the serializer so every creation
        # path gets a code — API, admin, management commands, fixtures.
        # An explicitly-set code is still honoured, which is what lets
        # fixtures and tests pin a known value; the API cannot reach that
        # path any more because the serializer marks the field read-only.
        # Existing groups keep their codes — nothing is rotated.
        if not self.join_code:
            self.join_code = self.generate_join_code()
        super().save(*args, **kwargs)

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
    # Text extracted from `file` once, at upload (M4 Phase C).
    #
    # Two problems, one field. Measured on a 212 KB PDF: extraction costs
    # 398 ms and chunking costs 0.5 ms, so re-reading the file was 99.9% of
    # the per-request preparation in RAGDoubtView — and it was paid on every
    # question. And because Render's filesystem is ephemeral (no persistent
    # disk; see docs/FEATURE_FLAGS.md), the file itself disappears on the
    # next deploy, which broke RAG for every material older than the last
    # restart. Text held here is in Postgres, so it survives.
    #
    # Blank means "not extracted yet" — the read path falls back to reading
    # the file, so this is additive and older rows keep working exactly as
    # before.
    extracted_text = models.TextField(blank=True, default="")

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


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver(post_save, sender=TopicPrerequisite)
@receiver(post_delete, sender=TopicPrerequisite)
def _bust_dag_cache_on_edge_change(sender, instance, **kwargs):
    """
    Curriculum edges changed — invalidate every cached NetworkX DAG so the
    HierarchicalEngine doesn't serve a stale graph for up to 30 minutes.
    Signals (rather than an overridden delete()) also cover queryset
    deletes like the one in seed_dsa_dag.
    """
    from groups.hybrid_router import invalidate_dag_cache
    try:
        subject = instance.topic.portal.name if instance.topic.portal else None
    except Exception:
        # topic may already be gone in a cascade delete
        subject = None
    invalidate_dag_cache(subject)


class Question(models.Model):
    # Content marker identifying unseeded placeholder questions (written by
    # bulk imports / restore_questions, replaced by reseed_questions).
    # Questions still carrying this marker are excluded from
    # recommendations until they receive real content and test cases.
    PLACEHOLDER_MARKER = "In this problem, you are tasked with solving the"

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    content = models.TextField() 
    base_difficulty = models.FloatField(default=1200.0) 
    boilerplate_code = models.JSONField(default=dict)
    hidden_test_cases = models.JSONField(default=list)
    hidden_wrapper_code = models.JSONField(default=dict, blank=True)

    # Which execution harness grades this question (M2 P2.6). Defaults to "v1"
    # — the harness exactly as it shipped — so this migration changes no
    # learner's grading. v2 is the canonical contract (one line per parameter,
    # space-separated output, exactly one public method); questions move to it
    # deliberately, after reconciliation, never by default.
    #
    # Not a `choices` field on purpose: the authority is
    # groups.execution_contract.KNOWN_CONTRACTS, which raises on an unknown
    # value rather than letting a typo fall back to a default and grade a
    # question under a contract it was not written for.
    execution_contract_version = models.CharField(max_length=8, default="v1")

    class Meta:
        indexes = [
            # §4.4 index catalog: Elo-nearest selection filters by topic and
            # ranks by |base_difficulty − user_elo| within it.
            models.Index(
                fields=["topic", "base_difficulty"],
                name="question_topic_diff_idx",
            ),
        ]

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


class ReferenceSolution(models.Model):
    """
    A trusted implementation of a question, used to GENERATE and VERIFY hidden
    test outputs (M2 P2.5, Phase 5). Grading truth, not learner content.

    Kept in its own model rather than as a field on `Question` for one reason
    that outweighs the convenience: a `Question` field is one serializer
    mistake away from shipping the answer to learners, and P2.5 exists because
    exactly that happened — the Submit response was serialising
    `expected_output` for every hidden case. A model with no serializer, no
    viewset and no route anywhere is structurally unable to leak. The
    guarantee comes from absence, not from vigilance.

    Two further reasons the field-on-Question shape does not work here:
    Judge0 needs a language per solution, so a single field forces either one
    language or a second schemaless JSON blob — the same weakness that
    produced the malformed `hidden_test_cases` rows this phase is cleaning up;
    and grading truth needs provenance, which a scalar field cannot carry.

    NOT registered in admin. Django admin is staff-only, but registering it
    would create a rendered HTML surface for the answer key whose only
    protection is the staff flag. Reference solutions are read by seed,
    validation and reconciliation tooling — none of which needs a web view.
    """

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="reference_solutions"
    )

    # The canonical language KEY ("python", "cpp"), not the Judge0 integer id.
    # `common.languages` exists precisely because those two representations
    # drifted apart and caused three production bugs; storing the id here
    # would fork that mapping again. The id is looked up from the registry at
    # execution time, so a Judge0 renumbering is a one-line change there.
    language = models.CharField(max_length=20)

    source_code = models.TextField()

    # A superseded solution is deactivated, never edited in place: the outputs
    # currently stored in `hidden_test_cases` were produced by SOME version of
    # this code, and losing which one makes a mismatch impossible to explain.
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # At most one active solution per language. Without this, two
            # active rows make "the" reference output ambiguous, and the
            # reconciliation report would silently depend on row order.
            models.UniqueConstraint(
                fields=["question", "language"],
                condition=models.Q(is_active=True),
                name="one_active_reference_solution_per_language",
            ),
        ]
        indexes = [
            models.Index(fields=["question", "is_active"],
                         name="refsol_question_active_idx"),
        ]

    def __str__(self):
        state = "active" if self.is_active else "superseded"
        return f"Reference[{self.language}] for {self.question_id} ({state})"


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
        # §4.4 index catalog. The physical indexes are created by migration
        # 0032 on the range-partitioned parent table (partitioned by month
        # of submitted_at, §4.3); they are declared here so the model state
        # and the database schema agree. The DB-level primary key is
        # (id, submitted_at) — Postgres requires the partition key inside
        # the PK — while id uniqueness is guaranteed by its sequence.
        indexes = [
            models.Index(
                fields=["user", "-submitted_at"],
                name="subm_user_ts_idx",
            ),
            models.Index(
                fields=["user", "question", "-submitted_at"],
                name="subm_user_q_ts_idx",
            ),
            models.Index(
                fields=["user", "status"],
                name="subm_user_status_idx",
            ),
        ]


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
    # Which routing policy produced this recommendation (M4 Phase B).
    #
    # The one piece of attribution that cannot be reconstructed later: it
    # depends on which code was running at decision time, and nothing else
    # records that. Item counters were deliberately NOT added alongside it
    # because they ARE derivable — COUNT over CodeSubmission reproduces them
    # exactly, so they can wait for the milestone that uses them.
    #
    # Nullable: the 177 rows that predate this stay valid, and a null means
    # "before policy versioning" rather than "unknown policy".
    policy_version = models.CharField(max_length=32, null=True, blank=True)

    class Meta:
        indexes = [
            # §4.4 index catalog: the flywheel closes an outcome by finding
            # the latest open recommendation for (user, problem).
            models.Index(
                fields=["user", "problem_id", "-created_at"],
                name="reclog_user_prob_ts_idx",
            ),
        ]

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