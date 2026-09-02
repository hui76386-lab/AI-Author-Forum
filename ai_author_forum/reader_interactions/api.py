"""Same-origin reader identity API."""

from __future__ import annotations

import json
from ipaddress import ip_address
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .capabilities import get_effective_capabilities
from .comments import (
    CommentServiceError,
    create_comment,
    list_comments,
    report_comment,
    withdraw_comment,
)
from .crypto import EmailProtector, ReaderCryptoError
from .downloads import DownloadError, consume_filesystem_grant, issue_download_grant
from .models import EmailVerificationChallenge
from .observability import request_id_for
from .rate_limits import RedisAtomicRateLimiter
from .services import (
    IdempotencyConflict,
    RateLimited,
    ReaderServiceError,
    ReaderSuspended,
    StaleVersion,
    VerificationInvalid,
    cancel_device_flow,
    claim_device_flow,
    consume_email_verification,
    get_device_flow_status,
    request_body_digest,
    request_email_verification,
    resolve_session,
    revoke_session,
    update_reader_profile,
)
from .sharing import ShareEventError, record_share_event


def _request_id(request):
    return request_id_for(request)


def _json_body(request):
    if len(request.body) > 64 * 1024:
        raise ValidationError("Request body is too large.")
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    return body


def _request_body(request):
    if request.content_type == "application/json":
        return _json_body(request)
    if request.method == "POST":
        return request.POST.dict()
    return _json_body(request)


def _envelope(request, data, *, status=200, headers=None):
    request_id = _request_id(request)
    response = JsonResponse({"data": data, "request_id": request_id}, status=status)
    response["X-Request-ID"] = request_id
    response["Cache-Control"] = "no-store"
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def _error(request, code, message, *, status=400, headers=None):
    request_id = _request_id(request)
    response = JsonResponse(
        {
            "error": {"code": code, "message": message, "field_errors": {}},
            "request_id": request_id,
        },
        status=status,
    )
    response["X-Request-ID"] = request_id
    response["Cache-Control"] = "no-store"
    response.reader_error_code = code
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def _remote_address(request):
    candidate = request.META.get("REMOTE_ADDR", "")
    if settings.READER_TRUST_PROXY_CLIENT_IP:
        candidate = request.headers.get("X-Real-IP", candidate)
    try:
        return str(ip_address(candidate))
    except ValueError:
        return "0.0.0.0"


def _reader_enabled(request, *, verification=False):
    if not settings.READER_INTERACTIONS_ENABLED:
        return _error(
            request,
            "service_degraded",
            "Reader interactions are unavailable.",
            status=503,
        )
    if verification and not settings.READER_EMAIL_VERIFICATION_ENABLED:
        return _error(
            request,
            "service_degraded",
            "Email verification is unavailable.",
            status=503,
        )
    return None


def _same_origin(request):
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return origin == f"{request.scheme}://{request.get_host()}"


def _device_flow_cookie(response, secret, *, delete=False):
    name = settings.READER_DEVICE_FLOW_COOKIE_NAME
    if delete:
        response.delete_cookie(name, path="/reader-api/", samesite="Lax")
        response.cookies[name]["secure"] = settings.READER_DEVICE_FLOW_COOKIE_SECURE
        response.cookies[name]["httponly"] = True
        return
    response.set_cookie(
        name,
        secret,
        max_age=min(
            settings.READER_DEVICE_FLOW_COOKIE_MAX_AGE,
            settings.READER_DEVICE_FLOW_TTL_SECONDS,
        ),
        httponly=True,
        secure=settings.READER_DEVICE_FLOW_COOKIE_SECURE,
        samesite="Lax",
        path="/reader-api/",
    )


@ensure_csrf_cookie
@require_http_methods(["GET"])
def session_view(request):
    context = None
    if settings.READER_INTERACTIONS_ENABLED:
        context = resolve_session(
            request.COOKIES.get(settings.READER_SESSION_COOKIE_NAME)
        )
    if context is None:
        return _envelope(
            request,
            {"authenticated": False, "verification_required": True},
        )
    return _envelope(
        request,
        {
            "authenticated": True,
            "reader": {
                "id": str(context.reader.public_id),
                "display_name": context.reader.display_name,
                "version": context.reader.version,
            },
            "session": {"expires_at": context.session.absolute_expires_at.isoformat()},
        },
    )


@csrf_protect
@require_http_methods(["POST"])
def request_verification_view(request):
    disabled = _reader_enabled(request, verification=True)
    if disabled:
        return disabled
    try:
        body = _json_body(request)
        result = request_email_verification(
            email=body.get("email", ""),
            purpose=body.get("intent", "session"),
            return_path=body.get("return_to", "/"),
            remote_address=_remote_address(request),
            user_agent=request.headers.get("User-Agent", ""),
        )
    except RateLimited as exc:
        return _error(
            request,
            exc.code,
            "Too many requests. Please try again later.",
            status=exc.status,
            headers={"Retry-After": str(exc.retry_after)},
        )
    except ReaderServiceError as exc:
        return _error(
            request,
            exc.code,
            "Reader verification is temporarily unavailable.",
            status=exc.status,
        )
    except ValidationError:
        return _error(
            request, "invalid_request", "The request could not be accepted.", status=422
        )
    data = {"accepted": result.accepted}
    if result.flow_public_id:
        expires_in = max(
            0,
            int((result.expires_at - timezone.now()).total_seconds()),
        )
        data.update(
            {
                "flow_id": str(result.flow_public_id),
                "expires_in": expires_in,
                "interval": settings.READER_DEVICE_FLOW_POLL_INTERVAL_SECONDS,
            }
        )
    response = _envelope(request, data, status=202)
    if result.origin_cookie_secret:
        _device_flow_cookie(response, result.origin_cookie_secret)
    return response


@require_http_methods(["GET"])
def verify_email_view(request, challenge_id=None):
    # The token is intentionally carried in a fragment, never in the HTTP request.
    if challenge_id is None:
        try:
            challenge_id = UUID(request.GET.get("challenge", ""))
        except (TypeError, ValueError):
            challenge_id = None
    masked_email = ""
    challenge = (
        EmailVerificationChallenge.objects.filter(public_id=challenge_id).first()
        if challenge_id
        else None
    )
    if challenge is not None:
        try:
            email = EmailProtector.from_settings().decrypt_text(
                challenge.email_ciphertext
            )
            local, _, domain = email.partition("@")
            if local and domain:
                masked_email = (
                    f"{local[:1]}{'*' * max(1, min(6, len(local) - 1))}@{domain}"
                )
        except ReaderCryptoError:
            masked_email = ""
    response = render(
        request,
        "reader_interactions/verify_email.html",
        {
            "challenge_id": str(challenge_id or ""),
            "consume_url": (
                f"/reader-api/v1/email-verifications/{challenge_id}/consume/"
                if challenge_id
                else ""
            ),
            "masked_email": masked_email,
        },
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    return response


@csrf_protect
@require_http_methods(["POST"])
def consume_verification_view(request, challenge_id):
    disabled = _reader_enabled(request, verification=True)
    if disabled:
        return disabled
    try:
        body = _request_body(request)
        result = consume_email_verification(
            challenge_public_id=challenge_id,
            token=body.get("token", ""),
            user_code=body.get("user_code"),
            display_name=body.get("display_name"),
            remote_address=_remote_address(request),
            user_agent=request.headers.get("User-Agent", ""),
            existing_session_secret=request.COOKIES.get(
                settings.READER_SESSION_COOKIE_NAME
            ),
        )
    except RateLimited as exc:
        return _error(
            request,
            exc.code,
            "Too many requests. Please try again later.",
            status=exc.status,
            headers={"Retry-After": str(exc.retry_after)},
        )
    except ReaderSuspended as exc:
        return _error(
            request, exc.code, "Reader access is unavailable.", status=exc.status
        )
    except (VerificationInvalid, ReaderServiceError) as exc:
        return _error(
            request,
            exc.code,
            "The verification link is no longer valid.",
            status=exc.status,
        )
    except ValidationError:
        return _error(
            request, "invalid_request", "The request could not be accepted.", status=422
        )
    response = _envelope(
        request,
        {
            "authenticated": True,
            "reader": {
                "id": str(result.reader.public_id),
                "display_name": result.reader.display_name,
                "version": result.reader.version,
            },
            "return_to": result.return_path,
            "intent": result.intent,
            "paired": result.paired,
            "flow_id": str(result.flow_public_id) if result.flow_public_id else None,
        },
    )
    response.set_cookie(
        settings.READER_SESSION_COOKIE_NAME,
        result.session_secret,
        max_age=settings.READER_SESSION_ABSOLUTE_SECONDS,
        httponly=True,
        secure=settings.READER_SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/reader-api/",
    )
    return response


@require_http_methods(["GET"])
def device_flow_status_view(request, flow_id):
    disabled = _reader_enabled(request, verification=True)
    if disabled:
        return disabled
    if not _same_origin(request):
        return _error(
            request, "csrf_failed", "Request origin is not trusted.", status=403
        )
    try:
        result = get_device_flow_status(
            flow_public_id=flow_id,
            origin_cookie_secret=request.COOKIES.get(
                settings.READER_DEVICE_FLOW_COOKIE_NAME
            ),
            remote_address=_remote_address(request),
            rate_limiter=RedisAtomicRateLimiter(),
        )
    except RateLimited as exc:
        return _error(
            request,
            exc.code,
            "Too many requests. Please try again later.",
            status=exc.status,
            headers={"Retry-After": str(exc.retry_after)},
        )
    except (VerificationInvalid, ReaderServiceError):
        return _error(
            request,
            "invalid_device_flow",
            "The device flow is unavailable.",
            status=404,
        )
    return _envelope(request, result)


@csrf_protect
@require_http_methods(["POST"])
def device_flow_claim_view(request, flow_id):
    disabled = _reader_enabled(request, verification=True)
    if disabled:
        return disabled
    try:
        claim = claim_device_flow(
            flow_public_id=flow_id,
            origin_cookie_secret=request.COOKIES.get(
                settings.READER_DEVICE_FLOW_COOKIE_NAME
            ),
            remote_address=_remote_address(request),
            user_agent=request.headers.get("User-Agent", ""),
            rate_limiter=RedisAtomicRateLimiter(),
        )
    except RateLimited as exc:
        return _error(
            request,
            exc.code,
            "Too many requests. Please try again later.",
            status=exc.status,
            headers={"Retry-After": str(exc.retry_after)},
        )
    except ReaderSuspended as exc:
        return _error(
            request, exc.code, "Reader access is unavailable.", status=exc.status
        )
    except (VerificationInvalid, ReaderServiceError) as exc:
        return _error(
            request,
            getattr(exc, "code", "invalid_device_flow"),
            "The device flow is not ready to claim.",
            status=getattr(exc, "status", 400),
        )
    response = _envelope(
        request,
        {
            "status": claim.flow.status,
            "flow_id": str(claim.flow.public_id),
            "authenticated": bool(claim.session_secret),
            "already_claimed": claim.already_claimed,
        },
    )
    if claim.session_secret:
        response.set_cookie(
            settings.READER_SESSION_COOKIE_NAME,
            claim.session_secret,
            max_age=settings.READER_SESSION_ABSOLUTE_SECONDS,
            httponly=True,
            secure=settings.READER_SESSION_COOKIE_SECURE,
            samesite="Lax",
            path="/reader-api/",
        )
    _device_flow_cookie(response, None, delete=True)
    return response


@csrf_protect
@require_http_methods(["POST"])
def device_flow_cancel_view(request, flow_id):
    disabled = _reader_enabled(request, verification=True)
    if disabled:
        return disabled
    try:
        result = cancel_device_flow(
            flow_public_id=flow_id,
            origin_cookie_secret=request.COOKIES.get(
                settings.READER_DEVICE_FLOW_COOKIE_NAME
            ),
        )
    except (VerificationInvalid, ReaderServiceError) as exc:
        return _error(
            request,
            getattr(exc, "code", "invalid_device_flow"),
            "The device flow is unavailable.",
            status=getattr(exc, "status", 400),
        )
    response = _envelope(request, result)
    _device_flow_cookie(response, None, delete=True)
    return response


@csrf_protect
@require_http_methods(["PATCH"])
def profile_view(request):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context = resolve_session(request.COOKIES.get(settings.READER_SESSION_COOKIE_NAME))
    if context is None:
        return _error(
            request,
            "authentication_required",
            "Reader session is required.",
            status=401,
        )
    try:
        body = _json_body(request)
        expected_version = int(body.get("expected_version"))
        result = update_reader_profile(
            reader=context.reader,
            display_name=body.get("display_name", ""),
            expected_version=expected_version,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            request_hash=request_body_digest(request.body),
        )
    except StaleVersion as exc:
        return _error(request, exc.code, "Reader profile is stale.", status=exc.status)
    except IdempotencyConflict as exc:
        return _error(
            request, exc.code, "Idempotency key payload mismatch.", status=exc.status
        )
    except ValidationError:
        return _error(
            request, "invalid_request", "The request could not be accepted.", status=422
        )
    return _envelope(request, result)


@csrf_protect
@require_http_methods(["POST"])
def logout_view(request):
    if settings.READER_INTERACTIONS_ENABLED:
        revoke_session(request.COOKIES.get(settings.READER_SESSION_COOKIE_NAME))
    response = _envelope(request, {"authenticated": False})
    response.delete_cookie(
        settings.READER_SESSION_COOKIE_NAME,
        path="/reader-api/",
        samesite="Lax",
    )
    deleted_cookie = response.cookies[settings.READER_SESSION_COOKIE_NAME]
    deleted_cookie["secure"] = settings.READER_SESSION_COOKIE_SECURE
    deleted_cookie["httponly"] = True
    return response


@require_http_methods(["GET"])
def capabilities_view(request, article_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context = resolve_session(request.COOKIES.get(settings.READER_SESSION_COOKIE_NAME))
    capabilities = get_effective_capabilities(
        article_public_id,
        session_context=context,
    )
    return _envelope(
        request,
        {
            "article_public_id": str(capabilities.article_public_id),
            "active_release": capabilities.active_release,
            "comments_mode": capabilities.comments_mode,
            "pdf_available": capabilities.pdf_available,
            "can_comment": capabilities.can_comment,
            "can_download": capabilities.can_download,
            "share_available": capabilities.share_available,
            "can_share": capabilities.can_share,
            "verification_required": capabilities.verification_required,
            "policy_version": capabilities.policy_version,
            "applying": capabilities.applying,
            "service_degraded": capabilities.service_degraded,
        },
    )


def _comment_session(request):
    context = resolve_session(request.COOKIES.get(settings.READER_SESSION_COOKIE_NAME))
    if context is None:
        return None, _error(
            request,
            "authentication_required",
            "Reader session is required.",
            status=401,
        )
    return context, None


def _comment_error_response(request, exc):
    if isinstance(exc, (ValidationError, ValueError, TypeError)):
        return _error(
            request,
            "invalid_request",
            "The comment request could not be accepted.",
            status=422,
        )
    headers = {}
    if getattr(exc, "retry_after", None):
        headers["Retry-After"] = str(exc.retry_after)
    return _error(
        request,
        getattr(exc, "code", "service_degraded"),
        "The comment request could not be completed.",
        status=getattr(exc, "status", 503),
        headers=headers,
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def comments_view(request, article_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    if request.method == "GET":
        context = resolve_session(
            request.COOKIES.get(settings.READER_SESSION_COOKIE_NAME)
        )
        try:
            result = list_comments(
                article_public_id=article_public_id,
                viewer_reader=context.reader if context else None,
                cursor=request.GET.get("cursor", ""),
                limit=request.GET.get("limit"),
            )
        except (CommentServiceError, ValidationError) as exc:
            return _comment_error_response(request, exc)
        if request.headers.get("If-None-Match") == result.get("etag"):
            response = JsonResponse({}, status=304)
            response["ETag"] = result["etag"]
            response["Cache-Control"] = "no-store"
            response["X-Request-ID"] = _request_id(request)
            return response
        return _envelope(request, result, headers={"ETag": result.get("etag", "")})

    context, error = _comment_session(request)
    if error:
        return error
    try:
        body = _json_body(request)
        result = create_comment(
            article_public_id=article_public_id,
            reader=context.reader,
            body=body.get("body", ""),
            expected_policy_version=int(body.get("expected_policy_version")),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            parent_public_id=body.get("parent_id"),
            remote_address=_remote_address(request),
        )
    except (CommentServiceError, ValidationError, ValueError, TypeError) as exc:
        return _comment_error_response(request, exc)
    return _envelope(request, result.body, status=result.status)


@csrf_protect
@require_http_methods(["POST"])
def comment_reply_view(request, article_public_id, comment_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context, error = _comment_session(request)
    if error:
        return error
    try:
        body = _json_body(request)
        result = create_comment(
            article_public_id=article_public_id,
            reader=context.reader,
            body=body.get("body", ""),
            expected_policy_version=int(body.get("expected_policy_version")),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            parent_public_id=comment_public_id,
            remote_address=_remote_address(request),
        )
    except (CommentServiceError, ValidationError, ValueError, TypeError) as exc:
        return _comment_error_response(request, exc)
    return _envelope(request, result.body, status=result.status)


@csrf_protect
@require_http_methods(["POST"])
def comment_withdrawal_view(request, article_public_id, comment_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context, error = _comment_session(request)
    if error:
        return error
    try:
        body = _json_body(request)
        result = withdraw_comment(
            article_public_id=article_public_id,
            comment_public_id=comment_public_id,
            reader=context.reader,
            expected_version=int(body.get("expected_version")),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
    except (CommentServiceError, ValidationError, ValueError, TypeError) as exc:
        return _comment_error_response(request, exc)
    return _envelope(request, result.body, status=result.status)


@csrf_protect
@require_http_methods(["POST"])
def comment_report_view(request, article_public_id, comment_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context, error = _comment_session(request)
    if error:
        return error
    try:
        body = _json_body(request)
        result = report_comment(
            article_public_id=article_public_id,
            comment_public_id=comment_public_id,
            reader=context.reader,
            reason=body.get("reason", ""),
            details=body.get("details", ""),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            remote_address=_remote_address(request),
        )
    except (CommentServiceError, ValidationError, ValueError, TypeError) as exc:
        return _comment_error_response(request, exc)
    return _envelope(request, result.body, status=result.status)


def _download_error_response(request, exc):
    headers = {}
    if getattr(exc, "retry_after", None):
        headers["Retry-After"] = str(exc.retry_after)
    return _error(
        request,
        getattr(exc, "code", "download_unavailable"),
        "The PDF download request could not be completed.",
        status=getattr(exc, "status", 503),
        headers=headers,
    )


@csrf_protect
@require_http_methods(["POST"])
def download_grant_view(request, article_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context, error = _comment_session(request)
    if error:
        return error
    try:
        issued = issue_download_grant(
            article_public_id=article_public_id,
            reader=context.reader,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            request_hash=request_body_digest(request.body),
            remote_address=_remote_address(request),
        )
    except DownloadError as exc:
        return _download_error_response(request, exc)
    return _envelope(
        request,
        {
            "grant_id": str(issued.grant_public_id),
            "download_url": issued.download_url,
            "expires_at": issued.expires_at.isoformat(),
            "release_version": issued.release_version,
        },
        status=201,
    )


@csrf_protect
@require_http_methods(["POST"])
def share_event_view(request, article_public_id):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context, error = _comment_session(request)
    if error:
        return error
    try:
        body = _json_body(request)
        result = record_share_event(
            article_public_id=article_public_id,
            reader=context.reader,
            payload=body,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            request_hash=request_body_digest(request.body),
        )
    except (ShareEventError, ValidationError) as exc:
        return _error(
            request,
            getattr(exc, "code", "invalid_request"),
            "The share event could not be accepted.",
            status=getattr(exc, "status", 422),
        )
    return _envelope(request, result, status=202)


@require_http_methods(["GET", "HEAD"])
def download_view(request, grant_public_id, token):
    disabled = _reader_enabled(request)
    if disabled:
        return disabled
    context, error = _comment_session(request)
    if error:
        return error
    try:
        download = consume_filesystem_grant(
            grant_public_id=grant_public_id,
            token=token,
            reader=context.reader,
            consume=request.method == "GET",
        )
    except DownloadError as exc:
        return _download_error_response(request, exc)
    response = HttpResponse(b"", content_type="application/pdf")
    response["X-Accel-Redirect"] = download.x_accel_redirect
    response["Content-Disposition"] = (
        "attachment; filename=article.pdf; filename*=UTF-8''" + download.filename
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Request-ID"] = _request_id(request)
    return response
