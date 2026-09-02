"""Transactional reader verification and session services."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .crypto import (
    EmailProtector,
    ReaderCryptoError,
    email_lookup_digest,
    normalize_email,
    security_fingerprint,
    token_digest,
)
from .models import (
    EmailVerificationChallenge,
    IdempotencyRecord,
    InteractionOutbox,
    ReaderDeviceFlow,
    ReaderIdentity,
    ReaderSession,
)
from .rate_limits import RateLimitUnavailable, RedisAtomicRateLimiter

logger = logging.getLogger(__name__)

_RETURN_PATH_PREFIXES = (
    "/articles/",
    "/en/articles/",
    "/journals/",
    "/en/journals/",
)
_DEVICE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ReaderServiceError(RuntimeError):
    code = "service_degraded"
    status = 503


class VerificationInvalid(ReaderServiceError):
    code = "verification_invalid"
    status = 400


class RateLimited(ReaderServiceError):
    code = "rate_limited"
    status = 429

    def __init__(self, retry_after):
        super().__init__(self.code)
        self.retry_after = max(1, int(retry_after))


class ReaderSuspended(ReaderServiceError):
    code = "reader_suspended"
    status = 403


class StaleVersion(ReaderServiceError):
    code = "stale_version"
    status = 409


class IdempotencyConflict(ReaderServiceError):
    code = "idempotency_conflict"
    status = 409


@dataclass(frozen=True)
class VerificationRequestResult:
    accepted: bool
    challenge_public_id: object | None = None
    event_id: object | None = None
    enqueued: bool = False
    flow_public_id: object | None = None
    user_code: str | None = None
    origin_cookie_secret: str | None = None
    expires_at: object | None = None


@dataclass(frozen=True)
class ConsumedVerification:
    reader: ReaderIdentity
    session: ReaderSession
    session_secret: str
    return_path: str
    intent: str
    paired: bool = False
    flow_public_id: object | None = None


@dataclass(frozen=True)
class DeviceFlowClaim:
    flow: ReaderDeviceFlow
    session: ReaderSession | None
    session_secret: str | None
    already_claimed: bool = False


@dataclass(frozen=True)
class SessionContext:
    session: ReaderSession
    reader: ReaderIdentity


def validate_return_path(value):
    candidate = str(value or "/").strip()
    parsed = urlsplit(candidate)
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "\\" in candidate
        or any(ord(char) < 32 for char in candidate)
    ):
        raise ValidationError("Invalid return path.")
    path = parsed.path
    if path not in {"/", "/en/"} and not path.startswith(_RETURN_PATH_PREFIXES):
        raise ValidationError("Return path is outside the reader allowlist.")
    return candidate


def validate_display_name(value):
    display_name = str(value or "").strip()
    if not 1 <= len(display_name) <= 80:
        raise ValidationError("Display name must contain 1 to 80 characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in display_name):
        raise ValidationError("Display name contains unsupported characters.")
    return display_name


def normalize_user_code(value):
    """Normalize the human-readable pairing code without retaining it."""

    normalized = "".join(str(value or "").upper().split()).replace("-", "")
    if len(normalized) != 8 or any(
        char not in _DEVICE_CODE_ALPHABET for char in normalized
    ):
        raise ValidationError("Invalid device pairing code.")
    return normalized


def _new_user_code():
    raw = "".join(secrets.choice(_DEVICE_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _user_code_digest(value):
    return token_digest(f"reader-device-code:{normalize_user_code(value)}")


def _origin_cookie_digest(value):
    return token_digest(f"reader-device-origin:{str(value or '')[:512]}")


def _audit_device_flow(flow, *, from_status, to_status, message=""):
    """Write a redacted control-plane audit event for every flow transition."""

    try:
        from ai_author_forum.site_settings.models import (
            AuditAction,
            AuditLog,
            AuditStatus,
        )

        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            target_type="ReaderDeviceFlow",
            target_id=str(flow.public_id),
            target_label="reader device flow",
            message=message or f"Reader device flow {from_status} -> {to_status}",
            metadata={
                "from_status": from_status,
                "to_status": to_status,
                "purpose": flow.purpose,
                "attempts": flow.attempts,
            },
        )
    except Exception as exc:  # noqa: BLE001 - interaction state must remain available
        logger.warning(
            "reader_device_flow_audit_failed flow_id=%s error_type=%s",
            flow.public_id,
            type(exc).__name__,
        )


def _rate_limit_or_raise(rate_limiter, dimensions, *, window_seconds):
    try:
        decision = rate_limiter.check(dimensions, window_seconds=window_seconds)
    except RateLimitUnavailable as exc:
        raise ReaderServiceError("Sensitive reader action is unavailable.") from exc
    if not decision.allowed:
        raise RateLimited(decision.retry_after)


def _enqueue_magic_link(event_id):
    from .tasks import send_magic_link

    send_magic_link.apply_async(
        args=[str(event_id)],
        queue="reader_email",
        argsrepr="(<redacted>,)",
    )


def request_email_verification(
    *,
    email,
    purpose,
    return_path,
    remote_address,
    user_agent,
    rate_limiter=None,
    enqueue=None,
):
    rate_limiter = rate_limiter or RedisAtomicRateLimiter()
    enqueue = enqueue or _enqueue_magic_link
    normalized = None
    valid_input = True
    try:
        normalized = normalize_email(email)
        normalized_return_path = validate_return_path(return_path)
        valid_purposes = {
            choice for choice, _label in EmailVerificationChallenge.Purpose.choices
        }
        if purpose not in valid_purposes:
            raise ValidationError("Invalid verification purpose.")
    except ValidationError:
        valid_input = False
        normalized_return_path = "/"

    lookup_source = normalized or str(email or "")[:254]
    lookup_hmac = security_fingerprint("invalid-email", lookup_source)
    if normalized:
        lookup_hmac = email_lookup_digest(normalized)
    ip_hmac = security_fingerprint("ip", remote_address)
    fingerprint_hmac = security_fingerprint(remote_address, user_agent)
    _rate_limit_or_raise(
        rate_limiter,
        (
            ("verification-ip", ip_hmac, settings.READER_VERIFICATION_IP_LIMIT),
            (
                "verification-email",
                lookup_hmac,
                settings.READER_VERIFICATION_EMAIL_LIMIT,
            ),
            (
                "verification-global",
                "all",
                settings.READER_VERIFICATION_GLOBAL_LIMIT,
            ),
        ),
        window_seconds=settings.READER_VERIFICATION_WINDOW_SECONDS,
    )
    if not valid_input or normalized is None:
        # Keep invalid requests on the same crypto path without persisting input.
        EmailProtector.from_settings().encrypt_text(lookup_source)
        token_digest(secrets.token_urlsafe(32))
        return VerificationRequestResult(accepted=True)

    protector = EmailProtector.from_settings()
    protected_email = protector.encrypt_text(normalized)
    token = secrets.token_urlsafe(32)
    protected_token = protector.encrypt_text(token)
    now = timezone.now()
    event_id = uuid4()
    challenge = None
    flow = None
    user_code = None
    origin_cookie_secret = None
    for attempt in range(3):
        try:
            with transaction.atomic(using="interactions"):
                previous_challenges = EmailVerificationChallenge.objects.filter(
                    email_lookup_hmac=lookup_hmac,
                    purpose=purpose,
                    status=EmailVerificationChallenge.Status.ISSUED,
                )
                previous_challenge_ids = list(
                    previous_challenges.values_list("pk", flat=True)
                )
                previous_challenges.update(
                    status=EmailVerificationChallenge.Status.SUPERSEDED
                )
                superseded_flows = list(
                    ReaderDeviceFlow.objects.filter(
                        challenge_id__in=previous_challenge_ids,
                        status=ReaderDeviceFlow.Status.PENDING,
                    )
                )
                ReaderDeviceFlow.objects.filter(
                    pk__in=[item.pk for item in superseded_flows]
                ).update(
                    status=ReaderDeviceFlow.Status.SUPERSEDED,
                    updated_at=now,
                )
                for previous_flow in superseded_flows:
                    previous_flow.status = ReaderDeviceFlow.Status.SUPERSEDED
                    _audit_device_flow(
                        previous_flow,
                        from_status=ReaderDeviceFlow.Status.PENDING,
                        to_status=previous_flow.status,
                    )
                user_code = _new_user_code()
                origin_cookie_secret = secrets.token_urlsafe(32)
                challenge = EmailVerificationChallenge.objects.create(
                    email_ciphertext=protected_email.ciphertext,
                    email_lookup_hmac=lookup_hmac,
                    token_hash=token_digest(token),
                    purpose=purpose,
                    return_path=normalized_return_path,
                    expires_at=now
                    + timedelta(seconds=settings.READER_MAGIC_LINK_TTL_SECONDS),
                    request_fingerprint_hmac=fingerprint_hmac,
                )
                flow = ReaderDeviceFlow.objects.create(
                    challenge=challenge,
                    user_code_hash=_user_code_digest(user_code),
                    origin_cookie_hash=_origin_cookie_digest(origin_cookie_secret),
                    purpose=purpose,
                    return_path=normalized_return_path,
                    expires_at=now
                    + timedelta(seconds=settings.READER_DEVICE_FLOW_TTL_SECONDS),
                )
                InteractionOutbox.objects.create(
                    event_id=event_id,
                    event_type="reader.email.magic_link.requested",
                    aggregate_type="email_verification_challenge",
                    aggregate_id=str(challenge.public_id),
                    aggregate_version=1,
                    payload={
                        "challenge_id": str(challenge.public_id),
                        "delivery_token_ciphertext": protected_token.ciphertext,
                    },
                )
            break
        except IntegrityError as exc:
            if attempt == 2:
                raise ReaderServiceError(
                    "Reader verification could not be issued."
                ) from exc
    enqueued = False
    try:
        enqueue(event_id)
        enqueued = True
    except Exception as exc:  # noqa: BLE001 - provider details must stay redacted
        logger.warning(
            "reader_magic_link_enqueue_failed event_id=%s error_type=%s",
            event_id,
            type(exc).__name__,
        )
    _audit_device_flow(
        flow,
        from_status="",
        to_status=flow.status,
        message="Reader device flow created",
    )
    return VerificationRequestResult(
        True,
        challenge.public_id,
        event_id,
        enqueued,
        flow.public_id,
        user_code,
        origin_cookie_secret,
        flow.expires_at,
    )


def _get_or_create_reader(*, challenge, display_name, protector, now):
    reader = (
        ReaderIdentity.objects.select_for_update()
        .filter(email_lookup_hmac=challenge.email_lookup_hmac)
        .first()
    )
    try:
        protected_email = protector.encrypt_text(
            protector.decrypt_text(challenge.email_ciphertext)
        )
    except ReaderCryptoError as exc:
        raise ReaderServiceError("Reader email protection is unavailable.") from exc
    if reader is None:
        try:
            with transaction.atomic(using="interactions"):
                return ReaderIdentity.objects.create(
                    email_ciphertext=protected_email.ciphertext,
                    email_lookup_hmac=challenge.email_lookup_hmac,
                    email_key_version=protected_email.key_version,
                    email_verified_at=now,
                    display_name=display_name or "Reader",
                )
        except IntegrityError:
            reader = ReaderIdentity.objects.select_for_update().get(
                email_lookup_hmac=challenge.email_lookup_hmac
            )
    if reader.status != ReaderIdentity.Status.ACTIVE:
        raise ReaderSuspended("Reader identity is not active.")
    reader.email_ciphertext = protected_email.ciphertext
    reader.email_key_version = protected_email.key_version
    reader.email_verified_at = now
    reader.version += 1
    update_fields = [
        "email_ciphertext",
        "email_key_version",
        "email_verified_at",
        "version",
        "updated_at",
    ]
    if display_name:
        reader.display_name = display_name
        update_fields.append("display_name")
    reader.save(update_fields=update_fields)
    return reader


def consume_email_verification(
    *,
    challenge_public_id,
    token,
    display_name=None,
    user_code=None,
    remote_address,
    user_agent,
    existing_session_secret=None,
    rate_limiter=None,
):
    rate_limiter = rate_limiter or RedisAtomicRateLimiter()
    challenge_key = security_fingerprint("challenge", str(challenge_public_id))
    ip_key = security_fingerprint("ip", remote_address)
    _rate_limit_or_raise(
        rate_limiter,
        (
            (
                "consume-challenge",
                challenge_key,
                settings.READER_VERIFICATION_CONSUME_LIMIT,
            ),
            ("consume-ip", ip_key, settings.READER_VERIFICATION_CONSUME_LIMIT),
        ),
        window_seconds=settings.READER_MAGIC_LINK_TTL_SECONDS,
    )
    if display_name:
        display_name = validate_display_name(display_name)
    supplied_digest = token_digest(str(token or "")[:512])
    now = timezone.now()
    failure = None
    consumed = None
    with transaction.atomic(using="interactions"):
        challenge = (
            EmailVerificationChallenge.objects.select_for_update()
            .filter(public_id=challenge_public_id)
            .first()
        )
        if challenge is None:
            failure = VerificationInvalid("Invalid verification challenge.")
        elif challenge.status == EmailVerificationChallenge.Status.CONSUMED:
            if not hmac.compare_digest(challenge.token_hash, supplied_digest):
                failure = VerificationInvalid(
                    "Verification challenge is no longer valid."
                )
            else:
                protector = EmailProtector.from_settings()
                try:
                    session_secret = protector.decrypt_text(
                        challenge.consumed_session_ciphertext or ""
                    )
                except ReaderCryptoError as exc:
                    raise ReaderServiceError(
                        "Reader session protection is unavailable."
                    ) from exc
                reader = challenge.consumed_reader
                session = (
                    ReaderSession.objects.select_related("reader")
                    .filter(secret_hash=token_digest(session_secret))
                    .first()
                )
                if reader is None or session is None:
                    failure = VerificationInvalid(
                        "Verification challenge is no longer valid."
                    )
                else:
                    flow = ReaderDeviceFlow.objects.filter(challenge=challenge).first()
                    consumed = ConsumedVerification(
                        reader,
                        session,
                        session_secret,
                        challenge.return_path,
                        challenge.purpose,
                        flow is not None
                        and flow.status
                        in (
                            ReaderDeviceFlow.Status.APPROVED,
                            ReaderDeviceFlow.Status.CLAIMED,
                        ),
                        flow.public_id if flow is not None else None,
                    )
        elif challenge.status != EmailVerificationChallenge.Status.ISSUED:
            failure = VerificationInvalid("Verification challenge is no longer valid.")
        elif challenge.expires_at <= now:
            challenge.status = EmailVerificationChallenge.Status.EXPIRED
            challenge.save(update_fields=("status",))
            failure = VerificationInvalid("Verification challenge has expired.")
        elif not hmac.compare_digest(challenge.token_hash, supplied_digest):
            challenge.attempts += 1
            update_fields = ["attempts"]
            if challenge.attempts >= settings.READER_VERIFICATION_CONSUME_LIMIT:
                challenge.status = EmailVerificationChallenge.Status.BLOCKED
                update_fields.append("status")
            challenge.save(update_fields=update_fields)
            failure = VerificationInvalid("Invalid verification token.")
        else:
            flow = (
                ReaderDeviceFlow.objects.select_for_update()
                .filter(challenge=challenge)
                .first()
            )
            pairing_ok = False
            if flow is not None:
                if flow.status != ReaderDeviceFlow.Status.PENDING:
                    failure = VerificationInvalid(
                        "Device pairing flow is no longer valid."
                    )
                elif flow.expires_at <= now:
                    previous_status = flow.status
                    flow.status = ReaderDeviceFlow.Status.EXPIRED
                    flow.save(update_fields=("status", "updated_at"))
                    _audit_device_flow(
                        flow, from_status=previous_status, to_status=flow.status
                    )
                    failure = VerificationInvalid("Device pairing flow has expired.")
                elif user_code:
                    try:
                        supplied_code = _user_code_digest(user_code)
                    except ValidationError:
                        supplied_code = ""
                    if not hmac.compare_digest(flow.user_code_hash, supplied_code):
                        flow.attempts += 1
                        update_fields = ["attempts", "updated_at"]
                        if flow.attempts >= settings.READER_DEVICE_FLOW_ATTEMPT_LIMIT:
                            previous_status = flow.status
                            flow.status = ReaderDeviceFlow.Status.DENIED
                            update_fields.append("status")
                            _audit_device_flow(
                                flow,
                                from_status=previous_status,
                                to_status=flow.status,
                                message="Reader device pairing denied after failed codes",
                            )
                        flow.save(update_fields=update_fields)
                        failure = VerificationInvalid("Invalid device pairing code.")
                    else:
                        pairing_ok = True
                else:
                    # The single-use email token is the phone's proof. The
                    # origin cookie still gates the computer-side claim.
                    pairing_ok = True
            protector = EmailProtector.from_settings()
            if failure is None:
                reader = _get_or_create_reader(
                    challenge=challenge,
                    display_name=display_name or "Reader",
                    protector=protector,
                    now=now,
                )
                if existing_session_secret:
                    ReaderSession.objects.filter(
                        secret_hash=token_digest(existing_session_secret),
                        revoked_at__isnull=True,
                    ).update(revoked_at=now)
                absolute_expires_at = now + timedelta(
                    seconds=settings.READER_SESSION_ABSOLUTE_SECONDS
                )
                idle_expires_at = min(
                    absolute_expires_at,
                    now + timedelta(seconds=settings.READER_SESSION_IDLE_SECONDS),
                )
                session_secret = secrets.token_urlsafe(32)
                reader_session = ReaderSession.objects.create(
                    reader=reader,
                    secret_hash=token_digest(session_secret),
                    last_seen_at=now,
                    idle_expires_at=idle_expires_at,
                    absolute_expires_at=absolute_expires_at,
                    risk_metadata={
                        "fingerprint": security_fingerprint(remote_address, user_agent)
                    },
                )
                challenge.status = EmailVerificationChallenge.Status.CONSUMED
                challenge.consumed_at = now
                challenge.consumed_reader = reader
                challenge.consumed_session_ciphertext = protector.encrypt_text(
                    session_secret
                ).ciphertext
                challenge.save(
                    update_fields=(
                        "status",
                        "consumed_at",
                        "consumed_reader",
                        "consumed_session_ciphertext",
                    )
                )
                if flow is not None and pairing_ok:
                    previous_status = flow.status
                    flow.status = ReaderDeviceFlow.Status.APPROVED
                    flow.reader = reader
                    flow.approved_at = now
                    flow.save(
                        update_fields=("status", "reader", "approved_at", "updated_at")
                    )
                    _audit_device_flow(
                        flow, from_status=previous_status, to_status=flow.status
                    )
                consumed = ConsumedVerification(
                    reader,
                    reader_session,
                    session_secret,
                    challenge.return_path,
                    challenge.purpose,
                    pairing_ok,
                    flow.public_id if flow is not None else None,
                )
    if failure:
        raise failure
    return consumed


def _device_flow_for_request(public_id, origin_cookie_secret, *, lock=False):
    if not origin_cookie_secret:
        return None
    queryset = ReaderDeviceFlow.objects
    if lock:
        queryset = queryset.select_for_update()
    return queryset.filter(
        public_id=public_id,
        origin_cookie_hash=_origin_cookie_digest(origin_cookie_secret),
    ).first()


def get_device_flow_status(
    *, flow_public_id, origin_cookie_secret, remote_address=None, rate_limiter=None
):
    if rate_limiter is not None:
        _rate_limit_or_raise(
            rate_limiter,
            (
                (
                    "device-flow-status",
                    security_fingerprint("device-flow", str(flow_public_id)),
                    settings.READER_DEVICE_FLOW_STATUS_LIMIT,
                ),
            ),
            window_seconds=settings.READER_DEVICE_FLOW_STATUS_WINDOW_SECONDS,
        )
    now = timezone.now()
    with transaction.atomic(using="interactions"):
        flow = _device_flow_for_request(flow_public_id, origin_cookie_secret, lock=True)
        if flow is None:
            raise VerificationInvalid("Invalid device flow.")
        if (
            flow.status
            in {
                ReaderDeviceFlow.Status.PENDING,
                ReaderDeviceFlow.Status.APPROVED,
            }
            and flow.expires_at <= now
        ):
            previous_status = flow.status
            flow.status = ReaderDeviceFlow.Status.EXPIRED
            flow.save(update_fields=("status", "updated_at"))
            _audit_device_flow(flow, from_status=previous_status, to_status=flow.status)
        expires_in = max(0, int((flow.expires_at - now).total_seconds()))
        return {
            "flow_id": str(flow.public_id),
            "status": flow.status,
            "expires_in": expires_in,
            "retry_after": settings.READER_DEVICE_FLOW_POLL_INTERVAL_SECONDS,
        }


def claim_device_flow(
    *,
    flow_public_id,
    origin_cookie_secret,
    remote_address,
    user_agent,
    rate_limiter=None,
):
    if rate_limiter is not None:
        _rate_limit_or_raise(
            rate_limiter,
            (
                (
                    "device-flow-claim",
                    security_fingerprint("device-flow", str(flow_public_id)),
                    settings.READER_DEVICE_FLOW_CLAIM_LIMIT,
                ),
            ),
            window_seconds=settings.READER_DEVICE_FLOW_STATUS_WINDOW_SECONDS,
        )
    now = timezone.now()
    with transaction.atomic(using="interactions"):
        flow = _device_flow_for_request(flow_public_id, origin_cookie_secret, lock=True)
        if flow is None:
            raise VerificationInvalid("Invalid device flow.")
        if (
            flow.status
            in {
                ReaderDeviceFlow.Status.PENDING,
                ReaderDeviceFlow.Status.APPROVED,
            }
            and flow.expires_at <= now
        ):
            previous_status = flow.status
            flow.status = ReaderDeviceFlow.Status.EXPIRED
            flow.save(update_fields=("status", "updated_at"))
            _audit_device_flow(flow, from_status=previous_status, to_status=flow.status)
        if flow.status == ReaderDeviceFlow.Status.CLAIMED:
            session_secret = None
            if flow.claimed_session_ciphertext:
                try:
                    session_secret = EmailProtector.from_settings().decrypt_text(
                        flow.claimed_session_ciphertext
                    )
                except ReaderCryptoError:
                    session_secret = None
            session = (
                ReaderSession.objects.select_related("reader")
                .filter(secret_hash=token_digest(session_secret or ""))
                .first()
                if session_secret
                else None
            )
            return DeviceFlowClaim(flow, session, session_secret, already_claimed=True)
        if flow.status != ReaderDeviceFlow.Status.APPROVED or flow.reader_id is None:
            raise VerificationInvalid("Device flow is not approved.")
        reader = ReaderIdentity.objects.select_for_update().get(pk=flow.reader_id)
        if reader.status != ReaderIdentity.Status.ACTIVE:
            raise ReaderSuspended("Reader identity is not active.")
        absolute_expires_at = now + timedelta(
            seconds=settings.READER_SESSION_ABSOLUTE_SECONDS
        )
        idle_expires_at = min(
            absolute_expires_at,
            now + timedelta(seconds=settings.READER_SESSION_IDLE_SECONDS),
        )
        session_secret = secrets.token_urlsafe(32)
        session = ReaderSession.objects.create(
            reader=reader,
            secret_hash=token_digest(session_secret),
            last_seen_at=now,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            risk_metadata={
                "fingerprint": security_fingerprint(remote_address, user_agent),
                "device_flow": str(flow.public_id),
            },
        )
        previous_status = flow.status
        flow.status = ReaderDeviceFlow.Status.CLAIMED
        flow.claimed_at = now
        flow.claimed_session_ciphertext = (
            EmailProtector.from_settings().encrypt_text(session_secret).ciphertext
        )
        flow.save(
            update_fields=(
                "status",
                "claimed_at",
                "claimed_session_ciphertext",
                "updated_at",
            )
        )
        _audit_device_flow(flow, from_status=previous_status, to_status=flow.status)
        return DeviceFlowClaim(flow, session, session_secret)


def cancel_device_flow(*, flow_public_id, origin_cookie_secret):
    with transaction.atomic(using="interactions"):
        flow = _device_flow_for_request(flow_public_id, origin_cookie_secret, lock=True)
        if flow is None:
            raise VerificationInvalid("Invalid device flow.")
        if flow.status == ReaderDeviceFlow.Status.PENDING:
            previous_status = flow.status
            flow.status = ReaderDeviceFlow.Status.CANCELLED
            flow.save(update_fields=("status", "updated_at"))
            _audit_device_flow(flow, from_status=previous_status, to_status=flow.status)
        return {"flow_id": str(flow.public_id), "status": flow.status}


def resolve_session(secret, *, touch=True):
    if not secret:
        return None
    now = timezone.now()
    reader_session = (
        ReaderSession.objects.select_related("reader")
        .filter(secret_hash=token_digest(secret))
        .first()
    )
    if reader_session is None or reader_session.revoked_at is not None:
        return None
    if (
        reader_session.idle_expires_at <= now
        or reader_session.absolute_expires_at <= now
        or reader_session.reader.status != ReaderIdentity.Status.ACTIVE
    ):
        ReaderSession.objects.filter(
            pk=reader_session.pk, revoked_at__isnull=True
        ).update(revoked_at=now)
        return None
    touch_before = now - timedelta(
        seconds=settings.READER_SESSION_TOUCH_INTERVAL_SECONDS
    )
    if touch and reader_session.last_seen_at <= touch_before:
        idle_expires_at = min(
            reader_session.absolute_expires_at,
            now + timedelta(seconds=settings.READER_SESSION_IDLE_SECONDS),
        )
        updated = ReaderSession.objects.filter(
            pk=reader_session.pk, revoked_at__isnull=True
        ).update(last_seen_at=now, idle_expires_at=idle_expires_at)
        if not updated:
            return None
        reader_session.last_seen_at = now
        reader_session.idle_expires_at = idle_expires_at
    return SessionContext(reader_session, reader_session.reader)


def revoke_session(secret):
    if not secret:
        return 0
    return ReaderSession.objects.filter(
        secret_hash=token_digest(secret), revoked_at__isnull=True
    ).update(revoked_at=timezone.now())


def update_reader_profile(
    *, reader, display_name, expected_version, idempotency_key, request_hash
):
    display_name = validate_display_name(display_name)
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValidationError("Idempotency-Key is required.")
    key_hash = security_fingerprint("idempotency", idempotency_key)
    now = timezone.now()
    with transaction.atomic(using="interactions"):
        locked = ReaderIdentity.objects.select_for_update().get(pk=reader.pk)
        previous = IdempotencyRecord.objects.filter(
            reader=locked,
            scope="session.profile",
            key_hash=key_hash,
        ).first()
        if previous:
            if not hmac.compare_digest(previous.request_hash, request_hash):
                raise IdempotencyConflict("Idempotency key payload mismatch.")
            return previous.response_body
        if locked.version != expected_version:
            raise StaleVersion("Reader profile version is stale.")
        locked.display_name = display_name
        locked.version += 1
        locked.save(update_fields=("display_name", "version", "updated_at"))
        response_body = {
            "id": str(locked.public_id),
            "display_name": locked.display_name,
            "version": locked.version,
        }
        IdempotencyRecord.objects.create(
            reader=locked,
            scope="session.profile",
            key_hash=key_hash,
            request_hash=request_hash,
            response_status=200,
            response_body=response_body,
            expires_at=now + timedelta(days=1),
        )
        return response_body


def request_body_digest(raw_body):
    return hashlib.sha256(raw_body).hexdigest()
