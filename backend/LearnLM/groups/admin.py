from django.contrib import admin

# Register your models here.
from .models import (
    StudyGroup, StudyMaterial, Topic, Question,
    CodingPortal, CodeSubmission, UserCodingProfile
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
    list_display = ['title', 'topic', 'base_difficulty']
    list_filter = ['topic']


@admin.register(CodeSubmission)
class CodeSubmissionAdmin(admin.ModelAdmin):
    search_fields = ['question__title', 'user__username']
    list_display = ['user', 'question', 'status', 'language', 'submitted_at']
    list_filter = ['status', 'language']