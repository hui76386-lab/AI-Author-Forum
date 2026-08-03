from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .health import get_health_report


def _response(report):
    return JsonResponse(report, status=200 if report["status"] == "ok" else 503)


@never_cache
@require_GET
def healthz(request):
    return _response(get_health_report())


@never_cache
@require_GET
def readyz(request):
    return _response(
        get_health_report(
            include_release=True,
            include_broker=getattr(
                settings, "STATIC_PUBLISH_HEALTHCHECK_BROKER", False
            ),
        )
    )
