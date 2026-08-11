import hashlib

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

    # ── Trust boundary (M2 P2.7c) ────────────────────────────────────────
    #
    # Two INDEPENDENT axes, because "we serve it" and "we believe its answers"
    # are different facts and collapsing them is what let unverified grading
    # data look trusted:
    #
    #   status       lifecycle — where the question is in the pipeline
    #   trust_state  evidence  — has an oracle ever confirmed its outputs
    #
    # servable        = status == PUBLISHED
    # adaptive-eligible = status == PUBLISHED and trust_state == ORACLE_VERIFIED
    #
    # A legacy question is PUBLISHED + UNVERIFIED: a learner may practise on it
    # and see a verdict, but that verdict may be wrong — no oracle has ever
    # checked it — so it must never teach the adaptive engine. When P2.7d
    # verifies one, `trust_state` flips and it starts counting. No status
    # churn, one flag, fully auditable.
    #
    # Both default to the SAFE value. Nothing is published or trusted by
    # accident, and the migration therefore promotes nothing.
    STATUS_DRAFT = "DRAFT"
    STATUS_PENDING_REVIEW = "PENDING_REVIEW"
    STATUS_PUBLISHED = "PUBLISHED"
    STATUS_BLOCKED = "BLOCKED"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PENDING_REVIEW, "Pending review"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_BLOCKED, "Blocked"),
    ]

    TRUST_UNVERIFIED = "UNVERIFIED"
    TRUST_ORACLE_VERIFIED = "ORACLE_VERIFIED"
    TRUST_CHOICES = [
        (TRUST_UNVERIFIED, "Unverified"),
        (TRUST_ORACLE_VERIFIED, "Oracle verified"),
    ]

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT
    )
    trust_state = models.CharField(
        max_length=20, choices=TRUST_CHOICES, default=TRUST_UNVERIFIED
    )

    @property
    def is_adaptive_eligible(self):
        """
        Whether a submission against this question may teach the learner
        model. Read at submission time and FROZEN onto the row — never
        recomputed, so verifying a question later cannot retroactively turn
        past evidence into trusted evidence.
        """
        return (
            self.status == self.STATUS_PUBLISHED
            and self.trust_state == self.TRUST_ORACLE_VERIFIED
        )

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


def compute_source_hash(source_code):
    """
    The approval fingerprint of a reference implementation (M2 P2.7d).

    SHA-256 over the UTF-8 bytes of the source, and nothing else. Deliberately
    NOT over timestamps, ids, language or any other metadata: the question it
    answers is "is this the same code a human read and approved?", and mixing
    in mutable fields would make an untouched implementation look modified.

    Must stay byte-identical to the digest PostgreSQL computes in the
    `reference_approved_source_unmodified` constraint. If one is ever changed,
    the other has to change with it or every approval becomes unwritable.
    """
    return hashlib.sha256((source_code or "").encode("utf-8")).hexdigest()


#: Review states, defined at module level as well as on the model. A nested
#: `Meta` class cannot see its enclosing class's attributes, so the CHECK
#: constraints below would otherwise have to repeat the string literals — two
#: spellings of the same value, one of which the constraints silently depend on.
_REVIEW_DRAFT = "DRAFT"
_REVIEW_IN_REVIEW = "IN_REVIEW"
_REVIEW_APPROVED = "APPROVED"
_REVIEW_REJECTED = "REJECTED"


class Sha256Hex(models.Func):
    """
    `compute_source_hash` expressed in SQL, for use inside a CHECK constraint.

    Both `sha256()` and `convert_to()` are IMMUTABLE in PostgreSQL, which is
    what makes this legal in a constraint at all — verified against
    PostgreSQL 15 before the constraint was written.
    """
    template = "encode(sha256(convert_to(%(expressions)s, 'UTF8')), 'hex')"
    output_field = models.CharField()


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

    # ── Lifecycle (M2 P2.7d) ─────────────────────────────────────────────
    #
    # TWO fields, deliberately not one, because they answer different
    # questions:
    #
    #   review_state  "has a human approved this implementation?"
    #   is_active     "is this the canonical reference selected for execution?"
    #
    # Collapsing them is the defect P2.7d exists to fix. Before this phase
    # `is_active` was the ONLY lifecycle field, and it defaulted to True —
    # so a reference created by any tooling was instantly canonical, and the
    # oracle would have executed it as grading truth without a human ever
    # having read it. There was no state in which a reference existed but was
    # not yet trusted.
    #
    # APPROVED + is_active=False is a legitimate, expected state: a reviewed
    # implementation that is not the currently selected oracle — either
    # superseded by a newer one, or approved for a language that is not this
    # problem's canonical oracle language.
    REVIEW_DRAFT = _REVIEW_DRAFT
    REVIEW_IN_REVIEW = _REVIEW_IN_REVIEW
    REVIEW_APPROVED = _REVIEW_APPROVED
    REVIEW_REJECTED = _REVIEW_REJECTED
    REVIEW_STATE_CHOICES = [
        (REVIEW_DRAFT, "Draft"),
        (REVIEW_IN_REVIEW, "In review"),
        (REVIEW_APPROVED, "Approved"),
        (REVIEW_REJECTED, "Rejected"),
    ]

    review_state = models.CharField(
        max_length=20, choices=REVIEW_STATE_CHOICES, default=REVIEW_DRAFT
    )

    # A superseded solution is deactivated, never edited in place: the outputs
    # currently stored in `hidden_test_cases` were produced by SOME version of
    # this code, and losing which one makes a mismatch impossible to explain.
    #
    # Defaults to False as of P2.7d. The safe answer to "is this the canonical
    # source of grading truth?" is No — the same reasoning that makes
    # Question.status default to DRAFT and CodeSubmission.adaptive_eligible
    # default to False. A creation path that does not decide produces an inert
    # row rather than an authoritative one.
    is_active = models.BooleanField(default=False)

    # ── Approval provenance ──────────────────────────────────────────────
    #
    # All three are NULL unless review_state is APPROVED, and all three are
    # NOT NULL when it is — enforced by a database constraint, not by
    # convention. "Approved by nobody at no time" is not an approval.
    #
    # PROTECT rather than SET_NULL: this is the provenance of grading truth.
    # A database that can silently forget who approved the answer key has
    # lost the only thing the field was added for. The cost — an operator
    # account cannot be deleted while it is the recorded approver of a
    # reference — is loud, and is resolved by superseding the reference,
    # which is the workflow this model already prescribes.
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="approved_reference_solutions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    # SHA-256 of the source code AS APPROVED — a frozen fingerprint, not a
    # live one. That distinction is the whole point: if it tracked the current
    # source it could never detect that the source had changed, which is
    # exactly what it exists to detect.
    source_hash = models.CharField(max_length=64, null=True, blank=True)

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
            # An unapproved reference must not be canonical. This is the
            # single most important invariant in the model: without it,
            # "approved" is advice and the oracle can execute anything.
            models.CheckConstraint(
                condition=(
                    models.Q(is_active=False)
                    | models.Q(review_state=_REVIEW_APPROVED)
                ),
                name="reference_active_requires_approval",
            ),
            # Approval carries provenance or it is not an approval — and,
            # symmetrically, a non-approved row must not retain the metadata
            # of an approval it no longer holds.
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(review_state=_REVIEW_APPROVED)
                        & models.Q(approved_by__isnull=False)
                        & models.Q(approved_at__isnull=False)
                        & models.Q(source_hash__isnull=False)
                    )
                    | (
                        ~models.Q(review_state=_REVIEW_APPROVED)
                        & models.Q(approved_by__isnull=True)
                        & models.Q(approved_at__isnull=True)
                        & models.Q(source_hash__isnull=True)
                    )
                ),
                name="reference_approval_provenance",
            ),
            # The stored fingerprint must still match the stored source.
            #
            # Deliberately a DATABASE check rather than a save() guard.
            # `save()` is bypassed by `QuerySet.update()`, by `bulk_update`,
            # by loaddata and by raw SQL — every one of which could otherwise
            # rewrite an approved reference's source while leaving the
            # approval intact, which is silent corruption of grading truth.
            # Postgres evaluates the digest itself, so no write path escapes.
            #
            # sha256() and convert_to() are IMMUTABLE, which is what makes
            # them legal inside a CHECK; verified against PostgreSQL 15.
            models.CheckConstraint(
                condition=(
                    ~models.Q(review_state=_REVIEW_APPROVED)
                    | models.Q(source_hash=Sha256Hex(models.F("source_code")))
                ),
                name="reference_approved_source_unmodified",
            ),
        ]
        indexes = [
            models.Index(fields=["question", "is_active"],
                         name="refsol_question_active_idx"),
        ]

    def __str__(self):
        state = "active" if self.is_active else "superseded"
        return (f"Reference[{self.language}] for {self.question_id} "
                f"({self.review_state}, {state})")

    # ── Derived state ────────────────────────────────────────────────────

    @property
    def has_valid_approval_provenance(self):
        """
        Whether this row is approved AND its source still matches what was
        approved.

        The database constraint makes a mismatch unwritable, so this can only
        be False for an in-memory object whose `source_code` was reassigned
        but not saved. That is precisely the path the oracle must not trust:
        it receives model instances, not rows.
        """
        return (
            self.review_state == self.REVIEW_APPROVED
            and self.source_hash is not None
            and self.source_hash == compute_source_hash(self.source_code)
        )

    @property
    def is_canonical(self):
        """Approved, active, and unmodified since approval."""
        return self.is_active and self.has_valid_approval_provenance

    # ── Transitions ──────────────────────────────────────────────────────
    #
    # The sanctioned way to move through the lifecycle. They are thin — the
    # database constraints are what make the invariants true — but they are
    # the only place that knows the ORDER, and they keep provenance and the
    # source fingerprint written together rather than by three separate
    # callers who might each remember two of the three.

    def submit_for_review(self):
        """DRAFT → IN_REVIEW."""
        if self.review_state != self.REVIEW_DRAFT:
            raise ValidationError(
                f"only a DRAFT reference may be submitted for review; "
                f"this one is {self.review_state}"
            )
        self.review_state = self.REVIEW_IN_REVIEW
        self.save(update_fields=["review_state", "updated_at"])

    def approve(self, by):
        """
        IN_REVIEW → APPROVED, stamping who, when, and what was approved.

        Approval does NOT activate. Choosing which approved implementation is
        the canonical oracle is a separate decision — see `activate`.
        """
        if self.review_state != self.REVIEW_IN_REVIEW:
            raise ValidationError(
                f"only an IN_REVIEW reference may be approved; "
                f"this one is {self.review_state}"
            )
        if by is None or by.pk is None:
            raise ValidationError("approval requires a persisted approver")
        self.review_state = self.REVIEW_APPROVED
        self.approved_by = by
        self.approved_at = timezone.now()
        self.source_hash = compute_source_hash(self.source_code)
        self.save(update_fields=["review_state", "approved_by", "approved_at",
                                 "source_hash", "updated_at"])

    def reject(self):
        """
        IN_REVIEW → REJECTED. Terminal.

        There is deliberately no REJECTED → DRAFT transition. Reopening a
        rejected reference is only useful in order to edit its source, and
        this model's stated contract is that a reference is superseded rather
        than edited in place — losing which version of the code produced the
        stored expected outputs makes a later mismatch impossible to explain.
        The sanctioned path is to create a new reference; the rejected row
        stays as the record of why the old one was not used.
        """
        if self.review_state != self.REVIEW_IN_REVIEW:
            raise ValidationError(
                f"only an IN_REVIEW reference may be rejected; "
                f"this one is {self.review_state}"
            )
        self.review_state = self.REVIEW_REJECTED
        self.save(update_fields=["review_state", "updated_at"])

    def activate(self):
        """
        APPROVED → canonical. Refuses anything else.

        The database constraint refuses it too; this exists so the failure is
        a readable ValidationError at the service boundary rather than an
        IntegrityError from a constraint name.
        """
        if not self.has_valid_approval_provenance:
            raise ValidationError(
                f"only an APPROVED reference with intact provenance may be "
                f"activated; this one is {self.review_state}"
            )
        self.is_active = True
        self.save(update_fields=["is_active", "updated_at"])

    def deactivate(self):
        """Supersede: stop being canonical. The row and its source are kept."""
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])

    def clean(self):
        """
        Model-level mirror of the database constraints.

        Not the enforcement layer — `full_clean()` is only called by forms,
        and this model has no form, no serializer and no admin registration
        (`test_reference_solution_secrecy` fails if any appears). It is here
        so that if one is ever added, it reports the invariant instead of
        surfacing a raw IntegrityError.
        """
        super().clean()
        approved = self.review_state == self.REVIEW_APPROVED

        if self.is_active and not approved:
            raise ValidationError(
                {"is_active": "an unapproved reference cannot be active"})

        if approved:
            missing = [
                name for name, value in (
                    ("approved_by", self.approved_by_id),
                    ("approved_at", self.approved_at),
                    ("source_hash", self.source_hash),
                ) if value is None
            ]
            if missing:
                raise ValidationError(
                    {name: "required once the reference is APPROVED"
                     for name in missing})
            if self.source_hash != compute_source_hash(self.source_code):
                raise ValidationError({
                    "source_code": "differs from the source that was approved; "
                                   "supersede this reference instead of editing it"
                })
        else:
            stale = [
                name for name, value in (
                    ("approved_by", self.approved_by_id),
                    ("approved_at", self.approved_at),
                    ("source_hash", self.source_hash),
                ) if value is not None
            ]
            if stale:
                raise ValidationError(
                    {name: "must be empty unless the reference is APPROVED"
                     for name in stale})


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

    # Whether this result may teach the adaptive engine (M2 P2.7c).
    #
    # DENORMALISED and FROZEN at creation, deliberately. Two reasons:
    #
    #   * Correctness. If a question is verified on day 20, a submission
    #     graded on day 1 against unverified data must NOT retroactively
    #     become trusted evidence — the learner really was judged by an
    #     answer key nobody had checked. Recomputing from the current
    #     Question row would rewrite history in the learner's disfavour or
    #     favour at random.
    #   * Safety. There are seven learning consumers of this table. Filtering
    #     each one on a join to Question is a design that fails silently the
    #     first time an eighth is added; filtering on one local boolean does
    #     not.
    #
    # Defaults FALSE: a row created by any path that does not explicitly
    # decide is inert, which is the safe direction. Existing rows keep False
    # for the same reason — nothing in the bank has ever been oracle-verified,
    # so marking historical submissions eligible would be a false claim.
    adaptive_eligible = models.BooleanField(default=False)

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