from rest_framework import serializers
from .models import StudyGroup, StudyMaterial, QuizResult, Connection, AssignedQuiz, CodingPortal, Profile
from django.contrib.auth import get_user_model

User = get_user_model()

class StudyGroupMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyGroup
        fields = ['id', 'name']

class UserDisplaySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'university', 'role']

class UserSerializer(serializers.ModelSerializer):
    # M7 registration hardening: previously email was optional and
    # unchecked, so accounts registered with blank, malformed, or
    # duplicate addresses. EmailField enforces the format; uniqueness is
    # checked case-insensitively below.
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'password', 'email', 'university', 'bio', 'role']
        extra_kwargs = {'password': {'write_only': True}}

    def validate_email(self, value):
        qs = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value.lower()

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        instance = self.Meta.model(**validated_data)
        if password is not None:
            instance.set_password(password)
        instance.save()
        return instance

# 👇 NEW: The Global Coding Portal Serializer
class CodingPortalSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodingPortal
        fields = ['id', 'name', 'description', 'is_active']

# --- 3. The Study Group Serializer (Updated) ---
class StudyGroupSerializer(serializers.ModelSerializer):
    creator = UserDisplaySerializer(read_only=True)
    members = UserDisplaySerializer(many=True, read_only=True)
    id = serializers.CharField(read_only=True)
    
    # 👇 NEW: Groups now return their subscribed global portals!
    active_portals = CodingPortalSerializer(many=True, read_only=True)

    class Meta:
        model = StudyGroup
        fields = ['id', 'name', 'description', 'creator', 'members', 'join_code', 'created_at', 'capacity', 'active_portals']

class StudyMaterialSerializer(serializers.ModelSerializer):
    study_group = StudyGroupMiniSerializer(read_only=True)
    uploaded_by = UserDisplaySerializer(read_only=True)

    class Meta:
        model = StudyMaterial
        fields = ['id', 'title', 'file', 'uploaded_by', 'study_group', 'upload_date']
        read_only_fields = ['uploaded_by', 'upload_date']

class QuizResultSerializer(serializers.ModelSerializer):
    user = UserDisplaySerializer(read_only=True)
    study_group = StudyGroupMiniSerializer(read_only=True)

    class Meta:
        model = QuizResult
        fields = ['id', 'user', 'study_group', 'score', 'date_taken']
        read_only_fields = ['user', 'study_group', 'date_taken']

class AssignedQuizSerializer(serializers.ModelSerializer):
    creator_name = serializers.ReadOnlyField(source='assigned_by.username')

    class Meta:
        model = AssignedQuiz
        fields = ['id', 'study_group', 'assigned_by', 'topic', 'quiz_data', 'deadline', 'assigned_at', 'creator_name']
        read_only_fields = ['assigned_by', 'assigned_at'] 

class UserBasicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']

class ProfileSerializer(serializers.ModelSerializer):
    user = UserBasicSerializer(read_only=True)
    class Meta:
        model = Profile
        fields = ['id', 'user', 'skills', 'achievements', 'major', 'graduation_year', 'bio']

class ConnectionSerializer(serializers.ModelSerializer):
    sender = UserBasicSerializer(read_only=True)
    receiver = UserBasicSerializer(read_only=True)

    class Meta:
        model = Connection
        fields = ['id', 'sender', 'receiver', 'status', 'created_at']

# 👇 NEW: API Validation Serializers for AI and Coding
from .models import Question

ALLOWED_LANGUAGES = {'python', 'java', 'cpp', 'javascript'}

class CodeSubmitSerializer(serializers.Serializer):
    # NOTE: no test_cases field. Grading always reads hidden test cases from
    # the Question row; a client-supplied list was dead input and pure
    # attack surface. With the field removed, DRF silently drops the key.
    problem_id = serializers.IntegerField()
    code = serializers.CharField()
    language = serializers.CharField()

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