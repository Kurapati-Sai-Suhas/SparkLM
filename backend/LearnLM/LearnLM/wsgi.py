"""
WSGI config for LearnLM project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "LearnLM.settings")

application = get_wsgi_application()

# Same boot-time URLconf warmup as asgi.py: runserver serves through this
# module, and without it the first request after every (auto)reload pays
# the 5-12s heavy-import bill that reads as "the whole site is slow".
from django.urls import get_resolver  # noqa: E402
get_resolver().url_patterns
