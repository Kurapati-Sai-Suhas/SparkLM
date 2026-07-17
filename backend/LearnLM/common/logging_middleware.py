"""
common.logging_middleware — one concise access-log line per API request
(M8 observability): method, path, status, duration. Daphne emits no
access log of its own, so without this the Render log tail shows only
application prints.
"""

import logging
import time

logger = logging.getLogger("access")


class AccessLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        response = self.get_response(request)
        # Health checks every few seconds would drown the tail.
        if request.path != "/healthz":
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s -> %d (%.0fms)",
                request.method, request.get_full_path(),
                response.status_code, duration_ms,
            )
        return response
