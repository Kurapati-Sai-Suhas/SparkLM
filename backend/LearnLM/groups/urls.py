# groups/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from common.auth_views import ThrottledTokenObtainPairView, ThrottledTokenRefreshView
from common.google_auth_views import GoogleAuthView
from common.review_views import ReviewQueueView

from .views import (
    # Auth & User
    CreateUserView, UserDashboardStats, UserProfileView,
    # Groups & Materials
    StudyGroupViewSet, MaterialViewSet, getGroupMembers,
    # AI
    AIFlashcardView, AIDoubtView, AIQuizView, RAGDoubtView,
    analytics_data,
    # Module B: Visual Search
    VisualSearchUploadView, VisualSearchQueryView,
    # Module D: Hybrid Router
    HybridRouterView, MasteryMapView,
    # WebSocket history
    GroupMessageHistoryView,
    # Quiz
    QuizResultCreateView, AssignedQuizCreateView,
    ListAssignedQuizView, ManageAssignedQuizView,
    # Social
    UserSearchView, FriendRequestView, FriendRequestActionView, FriendsListView,
    # Legacy
    process_document,
    HealthCheckView,
)

from .settings_views import ProfileSettingsView, TestEmailView
from .schedule_views import ScheduleView
from .notification_views import NotificationView
from .messages_views import DirectMessageFriendsView, DirectMessageView
from .coding_views import CodeRunView, CodeSubmitView, CodingOnboardingView, CodingProfileView, NextProblemView, CodingPortalListView, GamificationDashboardView
from .mlops_views import MLOpsTelemetryView
from . import views

# ── ViewSet router ───────────────────────────────────────────
router = DefaultRouter()
router.register(r'groups',    StudyGroupViewSet, basename='studygroup')
router.register(r'materials', MaterialViewSet,   basename='studymaterial')

urlpatterns = [
    path('', include(router.urls)),

    # ── Auth ────────────────────────────────────────────────
    path('token/',         ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', ThrottledTokenRefreshView.as_view(),    name='token_refresh'),
    path('register/',      CreateUserView.as_view(),      name='register'),
    path('auth/google/',   GoogleAuthView.as_view(),       name='auth-google'),

    # ── User & Dashboard ────────────────────────────────────
    path('dashboard/stats/', UserDashboardStats.as_view(), name='dashboard-stats'),
    path('user/profile/',    UserProfileView.as_view(),    name='user-profile'),

    # ── Group extras ────────────────────────────────────────
    path('groups/<int:group_id>/members/',  getGroupMembers.as_view(),        name='group-members'),
    path('groups/<int:group_id>/messages/', GroupMessageHistoryView.as_view(), name='group-messages'),

    # ── AI Features ─────────────────────────────────────────
    path('ai/flashcards/',  AIFlashcardView.as_view(), name='ai-flashcards'),
    path('ai/quiz/',        AIQuizView.as_view(),       name='ai-quiz'),
    path('ai/doubt/',       AIDoubtView.as_view(),      name='ai-doubt'),
    path('ai/doubt/rag/',   RAGDoubtView.as_view(),     name='ai-doubt-rag'),

    # ── Module D: Hybrid Router ──────────────────────────────
    path('ai/recommend/',   HybridRouterView.as_view(), name='hybrid-router'),
    path('ai/mastery-map/', MasteryMapView.as_view(),   name='mastery-map'),
    path('review/queue/',   ReviewQueueView.as_view(),  name='review-queue'),

    # ── Module C: Coding Portal (V2) ─────────────────────────
    # 👇 NEW: Fetch Global Portals
    path('coding-portals/', CodingPortalListView.as_view(), name='coding-portals-list'),
    path('coding-portals/gamification/', GamificationDashboardView.as_view(), name='gamification-dashboard'),
    
    path('code/run/',       CodeRunView.as_view(),        name='code-run'),
    path('code/submit/',    CodeSubmitView.as_view(),      name='code-submit'),
    path('code/profile/',   CodingProfileView.as_view(),   name='code-profile'),
    path('code/next/',      NextProblemView.as_view(),     name='code-next-problem'),
    path('code/onboard/',   CodingOnboardingView.as_view(), name='code-onboard'),
    path('mlops/telemetry/', MLOpsTelemetryView.as_view(), name='mlops-telemetry'),

    # ── Module B: Visual Search ──────────────────────────────
    path('visual-search/upload/', VisualSearchUploadView.as_view(), name='visual-search-upload'),
    path('visual-search/query/',  VisualSearchQueryView.as_view(),  name='visual-search-query'),

    # ── Analytics & Quiz ────────────────────────────────────
    path('analytics/charts/',          analytics_data,                   name='analytics-charts'),
    path('quiz/save/',                  QuizResultCreateView.as_view(),   name='save-quiz'),
    path('quizzes/assign/',             AssignedQuizCreateView.as_view(), name='assign-quiz'),
    path('quizzes/assigned/',           ListAssignedQuizView.as_view(),   name='list-assigned-quizzes'),
    path('quizzes/assigned/<int:pk>/',  ManageAssignedQuizView.as_view(), name='manage-assigned-quiz'),

    # ── Social ──────────────────────────────────────────────
    path('users/search/',                               UserSearchView.as_view(),          name='user-search'),
    path('friends/',                                    FriendsListView.as_view(),         name='friends-list'),
    path('friends/request/',                            FriendRequestView.as_view(),       name='friend-request'),
    path('friends/request/<int:connection_id>/action/', FriendRequestActionView.as_view(), name='friend-action'),

    # ── Legacy ──────────────────────────────────────────────
    path('upload-pdf/', process_document, name='process_document'),
    path("user/activity/", views.update_user_activity, name="update_user_activity"),

    # ── Observability ───────────────────────────────────────
    path('health/', HealthCheckView.as_view(), name='health-check'),

    # Day 4: UI Polish Endpoints
    path("settings/profile/", ProfileSettingsView.as_view(), name="settings_profile"),
    path("settings/email/", TestEmailView.as_view(), name="settings_email"),
    path("schedule/", ScheduleView.as_view(), name="schedule"),
    path("notifications/", NotificationView.as_view(), name="notifications"),
    
    # Day 5: Direct Messaging
    path("messages/friends/", DirectMessageFriendsView.as_view(), name="messages_friends"),
    path("messages/<int:friend_id>/", DirectMessageView.as_view(), name="messages"),
]