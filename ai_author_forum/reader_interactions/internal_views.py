"""Short-lived service-token protected internal projection endpoint."""

from __future__ import annotations

import hmac
import json
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .capabilities import ProjectionConflict, apply_capability_projection
from .observability import render_metrics


def _authorized(request):
    expected = settings.READER_INTERNAL_SERVICE_TOKEN
    supplied = request.headers.get("Authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    return bool(expected) and hmac.compare_digest(supplied, expected)


@require_http_methods(["GET"])
def metrics(request):
    if not _authorized(request):
        return JsonResponse({"error": {"code": "forbidden"}}, status=403)
    response = HttpResponse(
        render_metrics(), content_type="text/plain; version=0.0.4; charset=utf-8"
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_http_methods(["PUT"])
def article_capability_projection(request, article_public_id):
    if not _authorized(request):
        return JsonResponse({"error": {"code": "forbidden"}}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        if str(payload.get("article_public_id")) != str(article_public_id):
            raise ValueError("article id mismatch")
        status, projection = apply_capability_projection(payload)
    except ProjectionConflict:
        return JsonResponse({"error": {"code": "stale_version"}}, status=409)
    except (ValueError, TypeError, ValidationError, json.JSONDecodeError):
        return JsonResponse({"error": {"code": "invalid_request"}}, status=422)
    return JsonResponse(
        {
            "data": {
                "status": status,
                "projection_version": projection.projection_version,
            }
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def comment_snapshot_rebuild(request):
    if not _authorized(request):
        return JsonResponse({"error": {"code": "forbidden"}}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        article_public_id = str(payload["article_public_id"])
        UUID(article_public_id)
    except (KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": {"code": "invalid_request"}}, status=422)
    from .tasks import refresh_comment_snapshot

    refresh_comment_snapshot.apply_async(
        args=[article_public_id],
        queue="reader_comments",
        argsrepr="(<redacted>,)",
    )
    return JsonResponse({"data": {"status": "accepted"}}, status=202)


@csrf_exempt
@require_http_methods(["POST"])
def moderation_command_create(request):
    if not _authorized(request):
        return JsonResponse({"error": {"code": "forbidden"}}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        actor = get_user_model().objects.using("default").get(pk=payload["actor_id"])
        from ai_author_forum.reader_access.moderation import create_moderation_command

        result = create_moderation_command(
            actor=actor,
            comment_public_id=payload["comment_public_id"],
            action=payload["action"],
            expected_version=payload["expected_version"],
            reason=payload.get("reason", ""),
            note=payload.get("note", ""),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        return JsonResponse({"error": {"code": "invalid_request"}}, status=422)
    except PermissionDenied:
        return JsonResponse({"error": {"code": "forbidden"}}, status=403)
    return JsonResponse({"data": result.body}, status=202)


@csrf_exempt
@require_http_methods(["GET"])
def moderation_command_status(request, command_id):
    if not _authorized(request):
        return JsonResponse({"error": {"code": "forbidden"}}, status=403)
    from ai_author_forum.reader_access.models import ModerationCommand

    command = (
        ModerationCommand.objects.using("default").filter(command_id=command_id).first()
    )
    if command is None:
        return JsonResponse({"error": {"code": "not_found"}}, status=404)
    return JsonResponse(
        {
            "data": {
                "command_id": str(command.command_id),
                "status": command.status,
                "error_code": command.error_code or None,
                "result": command.result_body or {},
            }
        }
    )
