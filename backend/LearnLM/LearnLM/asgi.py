# LearnLM/asgi.py
import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

# Force the URLconf import at boot. Django resolves ROOT_URLCONF lazily on
# the FIRST request, and ours transitively imports the heavy app stack
# (fitz, langchain, google SDKs, sklearn, networkx) — profiled at 5-12s.
# Paying that at startup means health checks gate on a warm process and no
# user request ever eats the import bill.
from django.urls import get_resolver
get_resolver().url_patterns

# Import AFTER django.setup() so models are ready
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from groups.routing import websocket_urlpatterns
from .ws_middleware import JWTAuthMiddleware

application = ProtocolTypeRouter({
    # Regular HTTP → handled by Django as normal
    "http": get_asgi_application(),

    # WebSocket → JWT auth → URL router → consumers
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        )
    ),
})