"""Middleware for recording public-facing usage events.

Records one UsageEvent per qualifying request. Internal paths (manage, admin,
static, media, healthcheck) are skipped so only genuine public traffic is
logged.  All exceptions are swallowed to ensure the middleware never breaks
the request cycle.
"""

import logging
import re
import time

logger = logging.getLogger('campaigns.usage')

# Prefixes that should never be recorded.
_SKIP_PREFIXES = (
    '/manage/',
    '/admin/',
    '/static/',
    '/media/',
)

# Patterns whose full path should be skipped.
_SKIP_PATTERNS = re.compile(
    r'(favicon|robots\.txt|\.well-known|healthz|health-check|ping)',
    re.IGNORECASE,
)

# Pattern to extract campaign slug from public paths like /c/<slug>/...
_CAMPAIGN_PATH = re.compile(r'^/c/([^/]+)/')


def _should_skip(path):
    for prefix in _SKIP_PREFIXES:
        if path.startswith(prefix):
            return True
    if _SKIP_PATTERNS.search(path):
        return True
    return False


def _slug_from_path(path):
    m = _CAMPAIGN_PATH.match(path)
    return m.group(1) if m else ''


class UsageEventMiddleware:
    """Records a UsageEvent for every qualifying public request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if _should_skip(path):
            return self.get_response(request)

        start = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        try:
            from .models import UsageEvent
            UsageEvent.objects.create(
                event_type='page_view',
                path=path,
                method=request.method,
                status_code=response.status_code,
                campaign_slug=_slug_from_path(path),
                response_time_ms=elapsed_ms,
                metadata={},
            )
        except Exception:
            logger.exception('UsageEventMiddleware failed for %s %s', request.method, path)

        return response
