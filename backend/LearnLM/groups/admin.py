from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.db.models import F

# Register your models here.
from .models import (
    StudyGroup, StudyMaterial, Topic, Question,
    CodingPortal, CodeSubmission, UserCodingProfile
)

User = get_user_model()


@admin.register(User)
class SparkLMUserAdmin(UserAdmin):
    """
    Django's stock UserAdmin plus one action.

    Registered here because `groups.User` is the AUTH_USER_MODEL and was not
    previously in the admin at all — so the only way to revoke a compromised
    account's sessions was a shell. Subclassing UserAdmin rather than
    ModelAdmin keeps the password-hash widget, the permissions layout, and
    the add/change form split that Django provides; a bare ModelAdmin would
    expose the raw password field as an editable text input.
    """

    actions = ["revoke_all_sessions"]

    @admin.action(description="Revoke all sessions (invalidate every JWT)")
    def revoke_all_sessions(self, request, queryset):
        """
        The counterpart to /api/auth/logout-all/ for when the account holder
        cannot act — compromise, offboarding, abuse. Atomic increment for the
        same reason as the endpoint: two concurrent revocations must not
        collapse into one.
        """
        updated = queryset.update(token_version=F("token_version") + 1)
        self.message_user(
            request,
            f"Revoked all sessions for {updated} user(s). Their next request "
            f"with an existing token will return 401.",
        )

# Register your models so they show up in the Admin Dashboard!
admin.site.register(StudyGroup)
admin.site.register(StudyMaterial)
admin.site.register(Topic)
admin.site.register(CodingPortal)
admin.site.register(UserCodingProfile)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    search_fields = ['title']
    list_display = ['title', 'topic', 'base_difficulty', 'status', 'trust_state']
    list_filter = ['topic', 'status', 'trust_state']

    # Read-only (M2 P2.7c). Publishing a question and declaring its answers
    # oracle-verified are the outputs of a pipeline — static reconciliation,
    # an approved reference solution, oracle agreement, the mutation gate and
    # human approval. A form field would let any staff account assert all of
    # that with a dropdown, which is the single easiest way to reintroduce
    # untrusted grading truth.
    readonly_fields = ['status', 'trust_state']


@admin.register(CodeSubmission)
class CodeSubmissionAdmin(admin.ModelAdmin):
    search_fields = ['question__title', 'user__username']
    list_display = ['user', 'question', 'status', 'language', 'submitted_at']
    list_filter = ['status', 'language']