import hashlib

from django.db import DEFAULT_DB_ALIAS, models
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

    #: The legal status edges (M2 P2.7h-8).
    #:
    #: Until this milestone the four status values were a VOCABULARY with no
    #: graph: nothing in the repository ever wrote `status`, and no rule said
    #: which value may follow which. This is that graph, and it is deliberately
    #: the smallest one that lets a verified question be published:
    #:
    #:     DRAFT ──> PENDING_REVIEW ──> PUBLISHED
    #:                     <────────────────┘
    #:
    #: PENDING_REVIEW sits between DRAFT and PUBLISHED because `is_adaptive_
    #: eligible` is `PUBLISHED and ORACLE_VERIFIED`. Going DRAFT -> PUBLISHED
    #: first would make a question start teaching the adaptive model at the
    #: moment `question_promote` runs — eligibility flipping as a side effect
    #: of the trust write. Routing through PENDING_REVIEW (which satisfies the
    #: DRAFT/ORACLE_VERIFIED CHECK just as well) makes promotion change nothing
    #: observable, and leaves publication as the single deliberate act that
    #: turns a question on.
    #:
    #: PUBLISHED -> PENDING_REVIEW is the withdrawal edge. It only ever REDUCES
    #: eligibility, so it needs no evidence of its own, and without it the most
    #: consequential switch in the system would be one-way.
    #:
    #: BLOCKED has no edges yet: `census` and `oracle_pipeline` READ it, nothing
    #: writes it, and inventing a quarantine authority was out of scope.
    STATUS_TRANSITIONS = {
        ("DRAFT", "PENDING_REVIEW"),
        ("PENDING_REVIEW", "PUBLISHED"),
        ("PUBLISHED", "PENDING_REVIEW"),
    }

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
        constraints = [
            # A DRAFT question cannot have a proven answer key (M2 P2.7g-3).
            #
            # The two axes are independent by design, but not ALL four
            # combinations are meaningful: DRAFT means the question is still
            # being written, and an answer key proven against a statement that
            # is still changing proves nothing. Every other pairing is
            # legitimate and stays legal — including BLOCKED + ORACLE_VERIFIED,
            # which is a question with a trustworthy key withdrawn for an
            # unrelated reason, and PUBLISHED + UNVERIFIED, which is every
            # legacy question in the bank.
            models.CheckConstraint(
                condition=~(
                    models.Q(status="DRAFT")
                    & models.Q(trust_state="ORACLE_VERIFIED")
                ),
                name="question_draft_cannot_be_oracle_verified",
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
#: Module-level so a CheckConstraint can reference it without touching the
#: class body (M2 P2.7h-34), matching the _REVIEW_* convention below.
_ORIGIN_LLM = "llm"

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

    # ── Provenance (M2 P2.7h-34) ────────────────────────────────────────
    #
    # Who wrote this answer key, and how. Before these fields a reference
    # written by a model and one written by a person were the same row, and
    # `ReferenceCandidate` — which REFUSES an unattributed LLM reference —
    # could not carry its own provenance across persistence. Phase 20.5 hit
    # exactly that: four candidates could not be stored honestly, so they
    # were not stored at all.
    #
    # UNRECORDED exists for the rows that predate this field. Back-filling
    # them as `human` would be a guess asserted as provenance, which is the
    # failure these fields exist to prevent.
    ORIGIN_HUMAN = "human"
    ORIGIN_LLM = "llm"
    ORIGIN_TRUSTED_SOURCE = "trusted_source"
    ORIGIN_UNRECORDED = "unrecorded"
    ORIGIN_CHOICES = [
        (ORIGIN_HUMAN, "Written by a person"),
        (ORIGIN_LLM, "Generated by a language model"),
        (ORIGIN_TRUSTED_SOURCE, "Taken from a trusted external source"),
        (ORIGIN_UNRECORDED, "Predates provenance recording"),
    ]

    origin = models.CharField(max_length=20, choices=ORIGIN_CHOICES,
                              default=ORIGIN_UNRECORDED)
    #: The service that produced an LLM reference (e.g. "gemini").
    provider = models.CharField(max_length=64, blank=True, default="")
    #: The exact model identifier, so a later regression can be attributed.
    model_name = models.CharField(max_length=128, blank=True, default="")
    #: The prompt template version, for the same reason.
    prompt_version = models.CharField(max_length=64, blank=True, default="")
    #: The operator-verified specification this reference was written from.
    specification_digest = models.CharField(max_length=64, blank=True,
                                            default="")

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
            # An LLM reference must name the model that wrote it (M2
            # P2.7h-34). `ReferenceCandidate` already refuses an unattributed
            # one in memory; without this the same reference could be stored
            # attribution-free and become indistinguishable from a human's.
            # Enforced in the database because it is a property of the row,
            # not of the code path that happened to create it.
            models.CheckConstraint(
                condition=(
                    ~models.Q(origin=_ORIGIN_LLM)
                    | (
                        ~models.Q(provider="")
                        & ~models.Q(prompt_version="")
                    )
                ),
                name="reference_llm_origin_requires_attribution",
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
        # FK by ID, not by object. The reviewer is resolved on the default
        # connection while the reference is read and written through the
        # operator alias, and Django refuses to relate objects it believes live
        # on different databases. Assigning the id states the same fact without
        # asking the router's opinion — the same pattern `pre_image` uses.
        self.approved_by_id = by.pk
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

# ── Shadow adaptive model (M2 P2.9a) ─────────────────────────────────────
#
# UNARMED. Nothing in these two tables reaches a learner. Production routing,
# Elo and mastery are untouched and remain the live system; this runs beside
# them so the two can be compared on the same evidence before anything is
# promoted.
#
# Deliberately NEW TABLES rather than new columns on UserCodingProfile /
# UserTopicMastery / Question. Those columns have live semantics that other
# code depends on — `elo_rating` IS the routing engine's ability estimate,
# `base_difficulty` IS the ordering key — and repurposing any of them would
# make "shadow" a lie the first time something read a changed value.


class LearnerTopicSkill(models.Model):
    """
    Shadow Glicko-2 state for one (learner, topic).

    PER TOPIC on purpose. The production system has exactly one global ability
    number, so a learner strong in Arrays and weak in Dynamic Programming is
    represented by a single scalar that is wrong for both. `UserTopicMastery`
    already has an `elo_rating` column that looks like per-topic ability — it
    is never incremented by anything, only decremented by `calculate_decay`,
    and no router reads it, so it is not reused here.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="shadow_skills")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE,
                              related_name="shadow_skills")

    rating = models.FloatField(default=1200.0)
    #: Rating deviation. High means "we do not know yet" — which is the honest
    #: state for a new learner and the reason exploration is possible at all.
    rating_deviation = models.FloatField(default=350.0)
    volatility = models.FloatField(default=0.06)

    #: Counts only adaptive_eligible, conceptually-evaluable submissions.
    evidence_count = models.PositiveIntegerField(default=0)
    #: Drives RD inflation. Distinct from UserTopicMastery.last_practiced,
    #: which counts any activity; this counts only evidence.
    last_evidence_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "topic")
        indexes = [
            models.Index(fields=["user", "topic"], name="shadow_skill_user_topic_idx"),
        ]

    def __str__(self):
        return (f"{self.user_id}/{self.topic_id}: "
                f"{self.rating:.0f}±{self.rating_deviation:.0f}")


class QuestionSkill(models.Model):
    """
    Shadow Glicko-2 state for one question — the other side of the two-sided
    rating.

    `Question.base_difficulty` is NOT overwritten and NOT treated as
    calibrated. It seeds `rating` as a PRIOR and nothing more: it came from a
    three-valued CSV label (`Easy/Medium/Hard` -> 1000/1300/1600) that no code
    has ever updated from an outcome. A fresh row therefore carries the prior
    with MAXIMUM uncertainty, which is the honest encoding of "somebody typed
    this once".
    """

    question = models.OneToOneField(Question, on_delete=models.CASCADE,
                                    related_name="shadow_skill")

    rating = models.FloatField(default=1200.0)
    rating_deviation = models.FloatField(default=350.0)
    volatility = models.FloatField(default=0.06)

    evidence_count = models.PositiveIntegerField(default=0)
    last_evidence_at = models.DateTimeField(null=True, blank=True)

    #: The prior this row started from, kept so a later audit can tell a
    #: learned rating apart from an untouched seed.
    prior_rating = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["rating"], name="shadow_qskill_rating_idx"),
        ]

    def __str__(self):
        return (f"Q{self.question_id}: {self.rating:.0f}"
                f"±{self.rating_deviation:.0f} (prior {self.prior_rating:.0f})")


# ── Output provenance (M2 P2.7g-1) ───────────────────────────────────────
#
# The missing last link in the trust chain. P2.7d made a reference's approval
# auditable; nothing yet records which reference produced which answer. Without
# that a later revocation cannot identify what to invalidate — the F5 finding —
# and "verified" would mean "somebody ran something once".
#
# This records EXECUTIONS, not truth. A row means "this ran and produced this
# output". It never means the output is correct, and nothing here touches
# Question.status, trust_state or adaptive_eligible. Promotion is a later,
# separate, human-gated step.


class OracleExecution(models.Model):
    """
    One append-only record of one reference execution against one input.

    Answers, for any future expected_output: which approved reference produced
    it, for which question, from which input, under which execution contract,
    at what time, and from exactly which revision of the reference source.

    ── Two digests, deliberately ────────────────────────────────────────────

    `case_digest` identifies WHICH hidden-test case this is, and is stable
    under reordering — the JSON blob has no per-case id, so array position is
    not an identity. It uses `normalize_output(stdin)`, the same comparison
    `reseed_questions` and the P2.7h-1 quality gate already use for duplicate
    detection, so "the same case" means one thing across the codebase.

    `input_digest` is the exact byte sequence handed to the executor, which is
    NOT the same thing: both `GradingService.grade` and `OracleService._execute`
    convert a literal two-character backslash-n into a real newline before
    running, so the stored stdin and the executed input genuinely differ.
    Reproducing an execution needs the bytes that ran; identifying a case needs
    the normalized form. Collapsing them would make one of the two questions
    unanswerable.

    ── Immutability ─────────────────────────────────────────────────────────

    Every column except `is_authoritative` is frozen by a database trigger.
    A correction is a NEW row, never an edit — the point is that history
    survives, including history somebody later wishes were different.
    """

    #: Bumped when the MEANING of a recorded field changes, so an old row is
    #: never silently reinterpreted under new rules.
    PROVENANCE_SCHEMA_VERSION = 1

    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"
    STATUS_TIMEOUT = "TIMEOUT"
    STATUS_ERROR = "ERROR"
    STATUS_NONDETERMINISTIC = "NONDETERMINISTIC"
    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Ran cleanly"),
        (STATUS_FAILED, "Reference did not run cleanly"),
        (STATUS_TIMEOUT, "Timed out"),
        (STATUS_ERROR, "Execution service error"),
        (STATUS_NONDETERMINISTIC, "Disagreed with an identical run"),
    ]

    # PROTECT on both: provenance that outlives what it describes is not
    # provenance. Deleting a question or a reference is blocked while any
    # execution record points at it, exactly as `approved_by` is protected.
    question = models.ForeignKey(Question, on_delete=models.PROTECT,
                                 related_name="oracle_executions")
    reference = models.ForeignKey(ReferenceSolution, on_delete=models.PROTECT,
                                  related_name="oracle_executions")

    #: The reference's `source_hash` AT EXECUTION TIME. Denormalised on
    #: purpose: a reference is superseded rather than edited, so this pins the
    #: exact revision that ran even after a newer one becomes canonical.
    #: Produced by `compute_source_hash` — the P2.7d function, not a second
    #: hashing scheme.
    reference_source_hash = models.CharField(max_length=64)
    language = models.CharField(max_length=20)

    case_digest = models.CharField(max_length=64)
    input_digest = models.CharField(max_length=64)

    produced_output = models.TextField(blank=True)
    output_digest = models.CharField(max_length=64)

    execution_contract_version = models.CharField(max_length=8)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    executed_at = models.DateTimeField()

    #: Everything needed to reproduce the run that is not already a column —
    #: runner identity, resource limits, judge version. A dict rather than
    #: columns because the executor's own shape is not ours to fix.
    executor = models.JSONField(default=dict, blank=True)

    provenance_schema_version = models.PositiveSmallIntegerField(
        default=PROVENANCE_SCHEMA_VERSION)

    #: The ONE mutable column. An execution is a fact; whether its output is
    #: the accepted answer is a later decision (P2.7g-2), and recording that
    #: decision must not require rewriting the fact or duplicating the row.
    is_authoritative = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # At most one accepted answer per (question, case). Repeated runs
            # for determinism checking are welcome; two contradictory
            # authoritative answers are not. Same partial-unique idiom as
            # `one_active_reference_solution_per_language`.
            models.UniqueConstraint(
                fields=["question", "case_digest"],
                condition=models.Q(is_authoritative=True),
                name="one_authoritative_output_per_case",
            ),
            # The recorded digest must match the recorded output, checked by
            # PostgreSQL itself so no write path — including raw SQL — can
            # store an output that disagrees with its own fingerprint.
            models.CheckConstraint(
                condition=models.Q(
                    output_digest=Sha256Hex(models.F("produced_output"))),
                name="oracle_execution_output_digest_matches",
            ),
        ]
        indexes = [
            # "Which outputs came from this reference?" — the revocation query
            # (P2.7d F5). An index, not a free-text scan.
            models.Index(fields=["reference", "-executed_at"],
                         name="prov_reference_ts_idx"),
            models.Index(fields=["question", "case_digest"],
                         name="prov_question_case_idx"),
            models.Index(fields=["reference_source_hash"],
                         name="prov_source_hash_idx"),
        ]

    def __str__(self):
        return (f"exec q{self.question_id}/ref{self.reference_id} "
                f"case={self.case_digest[:12]} {self.status}"
                f"{' AUTHORITATIVE' if self.is_authoritative else ''}")

    def clean(self):
        """
        Application-layer mirror of the database guards.

        A reference may only ever answer for its OWN question — the invariant
        P2.7d's adversarial review found missing in OracleService, restated
        here because provenance that crossed questions would attribute an
        answer to a problem it was never written for.
        """
        super().clean()
        if self.reference_id and self.question_id:
            if self.reference.question_id != self.question_id:
                raise ValidationError({
                    "reference": (
                        f"reference {self.reference_id} belongs to question "
                        f"{self.reference.question_id}, not {self.question_id}; "
                        f"provenance may not cross questions")
                })

    def save(self, *args, **kwargs):
        """
        Append-only at the application layer; the trigger is the backstop.

        Only `is_authoritative` may change after creation. Everything else is a
        recorded fact about something that already happened.
        """
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields).issubset(
                    {"is_authoritative"}):
                raise ValidationError(
                    "OracleExecution is append-only: only `is_authoritative` "
                    "may be updated after creation. Record a NEW execution "
                    "instead of editing history.")
        super().save(*args, **kwargs)


class GlickoSnapshot(models.Model):
    """
    The Glicko state that existed IMMEDIATELY BEFORE one interaction (M2 P2.9b).

    P2.10b found the gap this closes: `LearnerTopicSkill` and `QuestionSkill`
    store only CURRENT state with `updated_at`. Nothing records what a
    learner's rating was at the moment they attempted a question, so a future
    knowledge-tracing model cannot use "the learner's ability at that time" as
    a feature — the value is simply not in the database, and never was.

    ── Why reconstruction is not an option ─────────────────────────────────

    Replaying the rating history from submissions looks tempting and is wrong.
    `glicko.rate` takes `periods_inactive`, derived from wall-clock gaps at
    update time; the periods actually applied were a function of when the
    update RAN, which is not recorded. A replay would produce a plausible
    history, not the one that happened, and a plausible history presented as
    fact is worse than an admitted gap.

    So: **every row predating this model is historical-unknown, permanently.**
    No backfill, no default, no inferred value.

    ── BEFORE and AFTER are separated on purpose ───────────────────────────

    `*_before` is the state fed into the update. `*_after` is the state it
    produced, and **`*_after` encodes the outcome** — a rating that went up
    means the learner was correct. Feeding it to a model predicting that same
    interaction is handing over the label.

    Both are stored, because `rating_after(n) == rating_before(n+1)` exactly
    for a given (learner, topic) — rating does not drift between updates, only
    RD inflates — which makes a missing snapshot detectable rather than
    invisible. The separation is enforced in `glicko_history.kt_features`,
    which refuses to emit any `*_after` field.

    ── Why `submission` is not a ForeignKey ────────────────────────────────

    MEASURED, not assumed: `groups_codesubmission` is RANGE PARTITIONED by
    `submitted_at`, so its primary key is `(id, submitted_at)` and there is no
    unique constraint on `id` alone. PostgreSQL rejects
    `REFERENCES groups_codesubmission(id)` with "there is no unique constraint
    matching given keys for referenced table". The id is stored as a plain
    column with its own uniqueness, alongside the partition key so a join can
    reach the right partition.
    """

    #: The interaction. Not an FK — see the class docstring.
    submission_id_value = models.BigIntegerField(unique=True)
    #: The partition key, so joining back to CodeSubmission can prune.
    submission_submitted_at = models.DateTimeField()

    #: Denormalised for the KT query "this learner's history in this topic".
    #: Derivable through submission -> question -> topic; stored so the lookup
    #: is an index scan rather than a three-table join, the same argument
    #: P2.7g-1 made for `reference_source_hash`.
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name="glicko_snapshots")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE,
                              related_name="glicko_snapshots")
    question = models.ForeignKey(Question, on_delete=models.CASCADE,
                                 related_name="glicko_snapshots")

    # ── PRE-interaction state: the only half admissible as a KT feature ──
    learner_rating_before = models.FloatField()
    learner_rd_before = models.FloatField()
    learner_volatility_before = models.FloatField()
    #: Fractional rating periods of inactivity fed to `glicko.rate`. This IS
    #: the rating-period information the update used; without it the update
    #: cannot be re-derived from the snapshot.
    learner_periods_inactive = models.FloatField()

    question_rating_before = models.FloatField()
    question_rd_before = models.FloatField()
    question_volatility_before = models.FloatField()
    question_periods_inactive = models.FloatField()

    # ── POST-interaction state: AUDIT ONLY, never a feature ──
    learner_rating_after = models.FloatField()
    learner_rd_after = models.FloatField()
    question_rating_after = models.FloatField()
    question_rd_after = models.FloatField()

    #: The `now` the update used, not the row's insert time. Two snapshots
    #: written by one backfill-style replay would share an insert time but
    #: differ here, and the update's clock is what the arithmetic used.
    recorded_at = models.DateTimeField()
    #: Which implementation produced these numbers. A tuning change to TAU or
    #: the RD bounds makes older snapshots incomparable, and silently mixing
    #: them would be invisible without this.
    glicko_version = models.CharField(max_length=32)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # The KT sequence query: one learner's history in one topic.
            models.Index(fields=["user", "topic", "recorded_at"],
                         name="glicko_snap_user_topic_idx"),
            models.Index(fields=["submission_submitted_at"],
                         name="glicko_snap_submitted_idx"),
        ]

    def __str__(self):
        return (f"glicko snapshot sub={self.submission_id_value} "
                f"u={self.user_id} t={self.topic_id} "
                f"r={self.learner_rating_before:.1f}")

    def save(self, *args, **kwargs):
        """
        Append-only. A snapshot is a statement about a moment that has passed.

        No `update_fields` exemption exists, unlike `OracleExecution` — there
        is no later decision to record here, so nothing about a snapshot may
        change after it is written.
        """
        if self.pk is not None:
            raise ValidationError(
                "GlickoSnapshot is append-only: a snapshot records state that "
                "existed at a past instant and can never be corrected. Record "
                "a new interaction instead.")
        super().save(*args, **kwargs)


#: Single source of truth for the artifact schema version.
#:
#: Defined here rather than in `question_artifact` because that module imports
#: this one; re-exported there as `ARTIFACT_SCHEMA_VERSION`, which is the name
#: callers should use. Two independently-maintained copies of this number would
#: eventually disagree, and a disagreement means an approval validated under a
#: schema it was not computed under.
_ARTIFACT_SCHEMA_VERSION = 1


class QuestionApproval(models.Model):
    """
    "Operator X approved artifact digest Y for question Q at time T."

    The missing link in the trust chain (M2 P2.7g-3). Reference approval
    (P2.7d) attests to one artifact — a blob of source code. This attests to a
    COMPOSITE: the statement, the harness, every hidden case, the reference
    that produced the answers, the provenance proving it ran, and the quality
    gate's verdict. `question_artifact` reduces all of that to `artifact_digest`.

    ── This row does not promote anything ──────────────────────────────────

    Creating it changes no question. `trust_state` is written only by
    `question_promote`, which independently rebuilds the digest from live state
    and refuses unless it matches. An approval is evidence that a human looked;
    promotion is a separate act that re-proves the artifact has not moved since
    they looked. Splitting them is what makes "approve" safe to perform: the
    worst outcome of a mistaken approval is a row nobody acts on.

    ── Append-only ─────────────────────────────────────────────────────────

    An approval is a statement a person made at a moment. Editing it would
    rewrite what they said. Superseding is expressed by recording a NEW
    approval; `current_for` reads the latest.
    """

    #: Denormalised deliberately. Revocation (future P2.7g-7) asks "which
    #: approvals rest on this reference revision?", and answering it by
    #: parsing digests would be a full scan of opaque hashes. These columns
    #: make it an indexed lookup. They are also INSIDE the digest, so they
    #: cannot drift from what was approved without invalidating it.
    question = models.ForeignKey(Question, on_delete=models.PROTECT,
                                 related_name="approvals")
    reference = models.ForeignKey(ReferenceSolution, on_delete=models.PROTECT,
                                  related_name="question_approvals")
    reference_source_hash = models.CharField(max_length=64)

    artifact_digest = models.CharField(max_length=64)
    artifact_schema_version = models.PositiveSmallIntegerField(
        default=_ARTIFACT_SCHEMA_VERSION)

    #: The quality verdict as it stood at approval, frozen.
    #:
    #: Re-running the P2.7h-1 gate needs Judge0, so requiring it at promotion
    #: would make trust depend on an external service being reachable. Freezing
    #: it here instead means promotion reuses the evidence that was actually
    #: approved and cannot have fresh, unreviewed quality numbers substituted.
    #: Suite drift is still caught: changing a hidden test changes its case
    #: digest, and that IS in the artifact digest.
    quality_outcome = models.JSONField(default=dict)

    #: Three separately-recorded actors (M2 P2.7g-3, decision B5). Four-eyes
    #: is NOT enforced today — approver and executor may be the same person —
    #: but the columns exist so the rule can be added later as a constraint
    #: rather than a schema migration plus a backfill of unknowable history.
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
        blank=True, related_name="oracle_executions_operated")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
        blank=True, related_name="question_reviews")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="question_approvals")

    executed_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField()

    #: Set by `question_promote`, never by approval. Records that this specific
    #: approval is the one trust was granted on.
    promoted_at = models.DateTimeField(null=True, blank=True)
    promoted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
        blank=True, related_name="question_promotions")

    created_at = models.DateTimeField(auto_now_add=True)

    #: Only these may change after creation, and only via `question_promote`.
    _MUTABLE_AFTER_CREATION = frozenset({"promoted_at", "promoted_by"})

    class Meta:
        indexes = [
            models.Index(fields=["question", "-approved_at"],
                         name="approval_question_ts_idx"),
            models.Index(fields=["reference_source_hash"],
                         name="approval_source_hash_idx"),
            models.Index(fields=["artifact_digest"],
                         name="approval_digest_idx"),
        ]

    def __str__(self):
        return (f"approval q{self.question_id} "
                f"digest={self.artifact_digest[:12]} "
                f"by={self.approved_by_id}"
                f"{' PROMOTED' if self.promoted_at else ''}")

    def clean(self):
        """A reference may only ever approve its OWN question."""
        super().clean()
        if self.reference_id and self.question_id:
            if self.reference.question_id != self.question_id:
                raise ValidationError({
                    "reference": (
                        f"reference {self.reference_id} belongs to question "
                        f"{self.reference.question_id}, not {self.question_id}; "
                        f"an approval may not cross questions")
                })

    def save(self, *args, **kwargs):
        """Append-only; only the promotion stamp may be added later."""
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields).issubset(
                    self._MUTABLE_AFTER_CREATION):
                raise ValidationError(
                    "QuestionApproval is append-only: only the promotion stamp "
                    "may be added after creation. Record a NEW approval "
                    "instead of editing what someone approved.")
        super().save(*args, **kwargs)

    @classmethod
    def current_for(cls, question, using=None):
        """
        The most recent approval, or None. Not necessarily still valid.

        Read on the question's OWN connection by default: an approval fetched
        from one database and acted on against a question read from another is
        a judgement about something that may not be there.
        """
        alias = using or question._state.db or DEFAULT_DB_ALIAS
        return (cls.objects.using(alias).filter(question=question)
                .order_by("-approved_at", "-pk").first())


# ═════════════════════════════════════════════════════════════
# Pre-image capture and rollback (M2 P2.7, blocker J8)
#
# Nothing may write production grading truth until the prior state can be
# restored exactly. These three models are that prerequisite. The logic lives
# in `groups/pre_image.py`; what is here is storage, plus the immutability the
# storage itself has to guarantee.
# ═════════════════════════════════════════════════════════════

#: Bumped whenever CAPTURED_FIELDS or the pre-image encoding changes. Emitted
#: FIRST in the digest, so a pre-image taken under one field set can never be
#: mistaken for one taken under another.
PRE_IMAGE_SCHEMA_VERSION = 1


class RemediationBatch(models.Model):
    """
    A named, frozen set of questions that one remediation may touch.

    Membership is frozen before any modification. Without that, "roll back the
    batch" has no fixed referent: a batch that can still grow after work began
    cannot say what it would restore.
    """

    STATE_OPEN = "OPEN"
    STATE_CAPTURED = "CAPTURED"
    STATE_APPLIED = "APPLIED"
    STATE_ROLLED_BACK = "ROLLED_BACK"
    STATE_CHOICES = [
        (STATE_OPEN, "Open - capturing"),
        (STATE_CAPTURED, "Captured - membership frozen"),
        (STATE_APPLIED, "Applied"),
        (STATE_ROLLED_BACK, "Rolled back"),
    ]

    batch_key = models.CharField(max_length=64, unique=True)
    purpose = models.TextField()
    state = models.CharField(max_length=16, choices=STATE_CHOICES,
                             default=STATE_OPEN)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.PROTECT,
                                   related_name="remediation_batches")
    created_at = models.DateTimeField(auto_now_add=True)

    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                  on_delete=models.PROTECT, null=True,
                                  blank=True,
                                  related_name="remediation_batches_frozen")

    def __str__(self):
        return f"batch {self.batch_key} ({self.state})"


class QuestionPreImage(models.Model):
    """
    A complete, immutable copy of one question before a remediation touched it.

    The WHOLE prior value of every mutable field, not a diff. A diff is only
    sufficient if its base is still available and unchanged, and both of those
    fail precisely when rollback matters.

    IMMUTABLE AFTER CAPTURE. `save()` refuses every update: a second
    remediation of the same question must not be able to overwrite the record
    of what the first one found, because that record is the only route back to
    the original state.
    """

    batch = models.ForeignKey(RemediationBatch, on_delete=models.PROTECT,
                              related_name="pre_images")
    question = models.ForeignKey(Question, on_delete=models.PROTECT,
                                 related_name="pre_images")

    schema_version = models.PositiveSmallIntegerField(
        default=PRE_IMAGE_SCHEMA_VERSION)

    content = models.TextField()
    status = models.CharField(max_length=20)
    trust_state = models.CharField(max_length=20)
    execution_contract_version = models.CharField(max_length=8)
    boilerplate_code = models.JSONField(default=dict)
    hidden_wrapper_code = models.JSONField(default=dict)
    hidden_test_cases = models.JSONField(default=list)

    #: [{case, input, expected}] using `provenance.case_identity` - the SAME
    #: identity the oracle and the approved artifact use, so a case cannot be
    #: one thing to provenance and another to rollback.
    case_identities = models.JSONField(default=list)

    #: Derived from status+trust_state at capture time. Stored as a cross-check
    #: that a restore reproduces the trust boundary's own verdict.
    was_adaptive_eligible = models.BooleanField(default=False)

    state_digest = models.CharField(max_length=64)

    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.PROTECT,
                                    related_name="pre_images_captured")
    captured_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "question"],
                name="pre_image_one_per_question_per_batch"),
        ]
        indexes = [
            models.Index(fields=["question", "-captured_at"],
                         name="pre_image_question_ts_idx"),
            models.Index(fields=["state_digest"], name="pre_image_digest_idx"),
        ]

    def captured_state(self):
        """The captured fields as a dict, in `CAPTURED_FIELDS` shape."""
        return {
            "content": self.content,
            "status": self.status,
            "trust_state": self.trust_state,
            "execution_contract_version": self.execution_contract_version,
            "boilerplate_code": self.boilerplate_code,
            "hidden_wrapper_code": self.hidden_wrapper_code,
            "hidden_test_cases": self.hidden_test_cases,
        }

    def save(self, *args, **kwargs):
        """Write-once. There is no legitimate edit to a captured prior state."""
        if self.pk is not None:
            raise ValidationError(
                "QuestionPreImage is immutable: a captured prior state is the "
                "only route back to it. Capture a NEW pre-image in a NEW batch "
                "instead of editing this one.")
        super().save(*args, **kwargs)

    def __str__(self):
        return (f"pre-image q{self.question_id} batch={self.batch_id} "
                f"digest={self.state_digest[:12]}")


class RemediationAction(models.Model):
    """
    Append-only record of one applied remediation, or one rollback.

    `post_digest` is what rollback compares live state against. If a question
    has moved since the action, someone else edited it, and restoring blindly
    would discard their work without saying so.
    """

    CLASS_CONTRACT_REPAIR = "CONTRACT_REPAIR"
    CLASS_STATEMENT_REPAIR = "STATEMENT_REPAIR"
    CLASS_BOILERPLATE_REPAIR = "BOILERPLATE_REPAIR"
    CLASS_HIDDEN_TEST_REPAIR = "HIDDEN_TEST_REPAIR"
    CLASS_EXPECTED_OUTPUT_REPAIR = "EXPECTED_OUTPUT_REPAIR"

    #: The stored INPUT of a case changed — the question being asked, not the
    #: answer being recorded (M2 P2.7, migration 0045).
    #:
    #: Distinct from HIDDEN_TEST_REPAIR ("the stored answer's FORM changed") and
    #: from EXPECTED_OUTPUT_REPAIR ("the answer changed") even though all three
    #: write `hidden_test_cases`. Recording an input change under either of the
    #: others would make the audit trail describe the one thing this batch has
    #: been most careful to keep separate: whether a repair moved the question
    #: or moved the answer.
    CLASS_INPUT_REPAIR = "INPUT_REPAIR"

    #: Cases were ADDED to a suite, and existing cases were labelled with the
    #: coverage category the quality gate reads (M2 P2.7h-3, migration 0046).
    #:
    #: A fourth class over `hidden_test_cases`, and the first that grows the
    #: suite rather than correcting it. HIDDEN_TEST_REPAIR means "the stored
    #: answer's FORM changed", INPUT_REPAIR "the question being asked changed",
    #: EXPECTED_OUTPUT_REPAIR "the answer changed" — none of them says "there
    #: are now more questions than there were", which is what a reader of the
    #: audit trail most needs to know before trusting an oracle run: evidence
    #: is scoped to case digests, and a suite that grew has cases no execution
    #: covers.
    CLASS_SUITE_EXPANSION = "SUITE_EXPANSION"

    #: A stub's placeholder statement was replaced by a generated one
    #: (M2 P2.7h-14, migration 0048).
    #:
    #: NOT `STATEMENT_REPAIR`. A repair replaces a defective statement with one
    #: a human adjudicated; this replaces a templated placeholder that was
    #: never a statement at all, with text a model produced and a human
    #: approved. A reader of the audit trail needs to tell those apart —
    #: "somebody fixed the wording" and "the wording was generated" carry
    #: different weight when a learner later disputes what the question asked.
    CLASS_STATEMENT_GENERATION = "STATEMENT_GENERATION"

    #: A stub's `*args, **kwargs` starter was replaced by a declared signature
    #: (M2 P2.7h-14, migration 0048).
    #:
    #: NOT `BOILERPLATE_REPAIR`, which is annotation-only by construction — it
    #: refuses a renamed or reordered parameter, so it cannot express "this
    #: method had no arity and now has one". That is a larger change and it
    #: gets its own name: the declared signature is what every hidden case is
    #: later bound against, so the audit trail must say when arity first
    #: existed.
    CLASS_SIGNATURE_DECLARATION = "SIGNATURE_DECLARATION"

    #: A reseed candidate's execution contract was CHOSEN from its newly
    #: declared signature, before any hidden case existed (M2 P2.7h-27).
    #:
    #: NOT `CONTRACT_REPAIR`, and the difference is the evidence, not the
    #: column — both write `execution_contract_version` under the same role.
    #: `remediate_contract` justifies its write by EXECUTION: it refuses a
    #: question with no stored cases, because nothing would demonstrate the
    #: contract actually runs. This class is the opposite by construction —
    #: `hidden_test_cases` must be empty, so the write is justified by the
    #: DECLARED SHAPE alone and no execution has occurred.
    #:
    #: Filed under one label they would be indistinguishable, and the first
    #: question a reviewer asks of this table is precisely "which contract
    #: changes were made without execution evidence?"
    CLASS_CONTRACT_DECLARATION = "CONTRACT_DECLARATION"

    #: A question moved along the status lifecycle (M2 P2.7h-8, migration
    #: 0047).
    #:
    #: Recorded here rather than in a new table because `status` is already one
    #: of the seven CAPTURED_FIELDS: the pre-image machinery holds the previous
    #: value, `post_digest` holds the resulting one, and `preimage_rollback`
    #: can already restore it. A separate status-history table would duplicate
    #: all three and give rollback a second thing to know about.
    #:
    #: Distinct from every repair class because it changes no grading truth at
    #: all. A reader of the audit trail needs "this question became publishable"
    #: to look nothing like "this question's answers changed".
    CLASS_STATUS_TRANSITION = "STATUS_TRANSITION"

    CLASS_MANUAL_REVIEW = "MANUAL_REVIEW"
    CLASS_COMPLETE_REBUILD = "COMPLETE_REBUILD"
    CLASS_ROLLBACK = "ROLLBACK"
    CLASS_CHOICES = [(c, c) for c in (
        CLASS_CONTRACT_REPAIR, CLASS_STATEMENT_REPAIR,
        CLASS_BOILERPLATE_REPAIR, CLASS_HIDDEN_TEST_REPAIR,
        CLASS_EXPECTED_OUTPUT_REPAIR, CLASS_INPUT_REPAIR,
        CLASS_SUITE_EXPANSION, CLASS_STATEMENT_GENERATION,
        CLASS_SIGNATURE_DECLARATION, CLASS_CONTRACT_DECLARATION,
        CLASS_STATUS_TRANSITION,
        CLASS_MANUAL_REVIEW, CLASS_COMPLETE_REBUILD, CLASS_ROLLBACK)]

    batch = models.ForeignKey(RemediationBatch, on_delete=models.PROTECT,
                              related_name="actions")
    question = models.ForeignKey(Question, on_delete=models.PROTECT,
                                 related_name="remediation_actions")
    pre_image = models.ForeignKey(QuestionPreImage, on_delete=models.PROTECT,
                                  related_name="actions")

    action_class = models.CharField(max_length=32, choices=CLASS_CHOICES)
    detail = models.TextField(blank=True, default="")
    post_digest = models.CharField(max_length=64)

    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                   on_delete=models.PROTECT,
                                   related_name="remediation_actions")
    applied_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["batch", "question"],
                         name="action_batch_question_idx"),
            models.Index(fields=["question", "-applied_at"],
                         name="action_question_ts_idx"),
        ]

    def save(self, *args, **kwargs):
        """Append-only: what was done is not editable after the fact."""
        if self.pk is not None:
            raise ValidationError(
                "RemediationAction is append-only: record a NEW action "
                "instead of editing the record of what was done.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.action_class} q{self.question_id} batch={self.batch_id}"


class ReseedLedger(models.Model):
    """
    How far one question got through a reseed slice (M2 P2.7h-14).

    ── Why this exists at all ──────────────────────────────────────────────

    Reseed's candidate selector is `content__icontains=PLACEHOLDER_MARKER`, and
    reseed's first write REMOVES that marker. The selector therefore erases the
    question from its own candidate set on success, so progress cannot be
    derived from it: a question that received a statement but not a signature
    is finished by the selector's reckoning and half-done in fact. Every other
    remediation in this milestone targeted a hand-picked question and needed no
    such record. A 50-question slice that can fail in the middle does.

    ── What it is NOT ──────────────────────────────────────────────────────

    Orchestration state. Nothing else may read it to decide anything.

    It deliberately holds **no digest**. A resumable writer is exactly the
    place a digest handshake could rot into "the ledger says this was the
    value", so there is no field here for a command to trust instead of live
    state. Every write re-reads the question, re-computes its digest and
    compares against the operator's `--expect-digest` — the ledger can say
    which stage to attempt, never what the row contained.

    It also holds no `status`, no `trust_state`, no cases, no expected output,
    no execution, approval or publication. A row here cannot make a question
    trusted, published, servable or adaptive-eligible; those live behind their
    own commands, roles and digests, and none of them consults this table.

    ── Rollback does not need it ───────────────────────────────────────────

    Restoration reads `QuestionPreImage` and compares captured state against
    live state (`pre_image.differing_fields`). A question with no ledger row,
    or a wrong one, rolls back identically. The ledger records what was
    ATTEMPTED; the pre-image records what was TRUE, and only the second is
    authoritative.
    """

    #: Nothing has been attempted for this question in this slice.
    STAGE_PENDING = "PENDING"
    #: `content` written and audited; the placeholder is gone.
    STAGE_STATEMENT = "STATEMENT_WRITTEN"
    #: `boilerplate_code` written and audited; the method declares an arity.
    STAGE_SIGNATURE = "SIGNATURE_WRITTEN"
    #: `execution_contract_version` chosen from that signature and audited
    #: (M2 P2.7h-27). The question now declares the harness its future cases
    #: will be authored against.
    #:
    #: This is deliberately NOT `COMPLETE`. Reseed's three writes have landed,
    #: but the question still has no hidden cases, no oracle execution, no
    #: approval and DRAFT/UNVERIFIED trust — it is invisible to learners and
    #: ineligible for adaptive selection. Naming this stage COMPLETE would
    #: make the ledger assert a readiness no other table agrees with.
    STAGE_CONTRACT = "CONTRACT_SET"
    #: Every reseed write landed. Still NOT the same as the question being
    #: usable: it has no cases, no verified answer key, and is invisible to
    #: learners. Whether anything should advance INTO this stage is an open
    #: design question — see `ADVANCES`.
    STAGE_COMPLETE = "COMPLETE"
    #: A stage refused or failed. `last_error` says which and why.
    STAGE_FAILED = "FAILED"

    STAGE_CHOICES = [(s, s) for s in (
        STAGE_PENDING, STAGE_STATEMENT, STAGE_SIGNATURE, STAGE_CONTRACT,
        STAGE_COMPLETE, STAGE_FAILED)]

    #: The stage each write ADVANCES TO, given it succeeded. Used by the
    #: orchestrator to decide what remains; expressed here so the order lives
    #: with the model rather than in a command.
    #:
    #: `STAGE_CONTRACT` is a TERMINAL stage in this map, and that is a
    #: deliberate omission rather than an oversight. Nothing advances out of
    #: it, because the next thing that happens to a reseeded question — suite
    #: authoring — is not a reseed write, does not run under a reseed role,
    #: and has not been built. Wiring CONTRACT_SET -> COMPLETE now would let
    #: the orchestrator mark questions finished on the strength of a stage
    #: transition nothing has verified the semantics of.
    ADVANCES = {
        STAGE_PENDING: STAGE_STATEMENT,
        STAGE_STATEMENT: STAGE_SIGNATURE,
        STAGE_SIGNATURE: STAGE_CONTRACT,
    }

    batch = models.ForeignKey(RemediationBatch, on_delete=models.PROTECT,
                              related_name="reseed_ledger")
    question = models.ForeignKey(Question, on_delete=models.PROTECT,
                                 related_name="reseed_ledger")

    stage = models.CharField(max_length=24, choices=STAGE_CHOICES,
                             default=STAGE_PENDING)
    #: Why the last attempt failed, verbatim. Blank while nothing has failed.
    last_error = models.TextField(blank=True, default="")
    #: How many times a stage has been attempted for this question. A retry
    #: budget is an operator's concern, not a correctness one — recorded so a
    #: question that keeps failing is visible rather than silently re-tried.
    attempts = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "question"],
                name="reseed_ledger_one_per_question_per_batch"),
        ]
        indexes = [
            models.Index(fields=["batch", "stage"],
                         name="reseed_ledger_batch_stage_idx"),
        ]

    def __str__(self):
        return f"reseed q{self.question_id} {self.stage} batch={self.batch_id}"

    @property
    def is_resumable(self):
        """Whether an orchestrator should attempt another stage."""
        return self.stage in self.ADVANCES or self.stage == self.STAGE_FAILED

    def next_stage(self):
        """
        The stage a successful write would advance to, or None.

        FAILED does not advance on its own: a failed question is retried at
        whatever stage it actually reached, which the orchestrator re-derives
        from live state and the action trail — never from this row alone.
        """
        return self.ADVANCES.get(self.stage)
