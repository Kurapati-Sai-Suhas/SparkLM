"""
common.auth_views — throttled JWT endpoints.

First resident of the v2 `common` module (frozen architecture §9: new code
lands in v2 modules; `groups` is frozen legacy).

The token obtain/refresh endpoints previously rode the general anonymous
rate (30/min), which is comfortable for credential stuffing. The dedicated
'auth' scope (10/min, configured in settings.REST_FRAMEWORK) is keyed by IP
for anonymous callers and acts as the brute-force brake required by the
frozen architecture §7/§13.7 without affecting any authenticated flow.
"""

from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'


class ThrottledTokenRefreshView(TokenRefreshView):
    # Separate bucket from login: refresh presents an existing token, so it
    # is not a credential-guessing surface, and behind shared-IP NATs the
    # hourly refresh traffic of many users must never starve sign-ins.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth-refresh'
