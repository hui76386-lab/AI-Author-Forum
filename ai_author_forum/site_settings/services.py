from .models import AuditLog


def record_audit_event(*, request=None, actor=None, **kwargs):
    if actor is None and request is not None:
        actor = request.user if request.user.is_authenticated else None
    if request is not None:
        kwargs.setdefault("request_id", request.headers.get("X-Request-ID", ""))
        kwargs.setdefault("ip_address", request.META.get("REMOTE_ADDR"))
    return AuditLog.record(actor=actor, **kwargs)
