"""Unauthenticated liveness / readiness probe.

Served at ``/healthz/`` (routed in :mod:`waypost.urls`). Three consumers need a
cheap, credential-free way to ask "is this deployment healthy?":

1. The container ``HEALTHCHECK`` in ``docker/Dockerfile``.
2. ``docker/bin/ci-deploy.sh``, which polls the loopback port after a deploy and
   rolls the release back when the probe does not come good.
3. Host monitoring (CloudMonitor site monitor, the cron probe), which needs a
   URL that answers directly instead of redirecting to a login page.

Deliberate constraints:

* **No authentication.** A probe that needs a session cannot detect the failure
  modes it exists for: the app not booting, the database being unreachable, the
  cache being down.
* **No sensitive detail.** Each dependency reports ``ok`` or, on failure, only
  the exception *class* name. Never the message - a database error message can
  carry the DSN, host or role name, and this endpoint is public by design.
* **Reachable over plain HTTP.** ``SECURE_REDIRECT_EXEMPT`` in settings exempts
  this path so the loopback poll works with no proxy in front to set
  ``X-Forwarded-Proto``.

Answers ``200`` when every dependency is healthy and ``503`` otherwise, so a
monitor can alert on the status code alone without parsing the body.

The deployed version is reported from ``WAYPOST_VERSION`` / ``WAYPOST_GIT_SHA``,
which ``docker-compose.yml`` populates from the release tag being deployed. Both
default to empty, which is what a hand-started development container shows.
"""
import os

from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET

# Round-tripped through the configured cache backend so the probe exercises the
# real client (django-redis in production, locmem in development) rather than
# merely importing it.
_CACHE_PROBE_KEY = 'healthz:probe'
_CACHE_PROBE_TTL_SECONDS = 10


def _database_status():
    """Return 'ok' when a trivial query round-trips, else the error class name."""
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:
        return type(exc).__name__
    return 'ok'


def _cache_status():
    """Return 'ok' when a value survives a write/read cycle, else a short reason."""
    try:
        cache.set(_CACHE_PROBE_KEY, '1', _CACHE_PROBE_TTL_SECONDS)
        if cache.get(_CACHE_PROBE_KEY) != '1':
            return 'read-back-mismatch'
    except Exception as exc:
        return type(exc).__name__
    return 'ok'


@require_GET
def healthz(request):
    """Report dependency health as JSON; 200 when healthy, 503 when not."""
    database = _database_status()
    cache_status = _cache_status()
    healthy = database == 'ok' and cache_status == 'ok'

    response = JsonResponse(
        {
            'status': 'ok' if healthy else 'degraded',
            'database': database,
            'cache': cache_status,
            'version': os.environ.get('WAYPOST_VERSION', ''),
            'revision': os.environ.get('WAYPOST_GIT_SHA', ''),
        },
        status=200 if healthy else 503,
    )
    # Never cached (a stale 200 would hide an outage) and never indexed.
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
