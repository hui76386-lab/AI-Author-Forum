"""Privacy-safe structured logging for application and reader services."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime

_DOWNLOAD_TOKEN_PATH = re.compile(
    r"(/reader-api/v1/downloads/" r"[0-9a-fA-F-]{36}/)" r"[A-Za-z0-9_-]{20,}" r"(/?)"
)
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}")
_COOKIE = re.compile(r"(?i)((?:reader_session|sessionid|csrftoken)=)[^;\s]+")
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:token|signature|x-amz-signature|credential|key)=)[^&\s]+"
)


def redact_log_value(value):
    redacted = _DOWNLOAD_TOKEN_PATH.sub(r"\1<redacted>\2", str(value))
    redacted = _EMAIL.sub("<redacted-email>", redacted)
    redacted = _BEARER.sub(r"\1<redacted>", redacted)
    redacted = _COOKIE.sub(r"\1<redacted>", redacted)
    return _SENSITIVE_QUERY.sub(r"\1<redacted>", redacted)


class RedactReaderBearerPaths(logging.Filter):
    def filter(self, record):
        redacted = redact_log_value(record.getMessage())
        message = record.getMessage()
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonPrivacyFormatter(logging.Formatter):
    """Emit fixed-schema JSON without request bodies, headers, or arbitrary extras."""

    _optional_fields = (
        "event",
        "request_id",
        "event_id",
        "command_id",
        "route",
        "method",
        "status",
        "duration_ms",
        "error_category",
        "task",
        "queue",
    )

    def format(self, record):
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": os.environ.get("READER_SERVICE_NAME", "application"),
            "environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "development"),
            "release": os.environ.get("APPLICATION_RELEASE", "unknown"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_log_value(record.getMessage()),
        }
        for field in self._optional_fields:
            value = getattr(record, field, None)
            if value is not None and value != "":
                payload[field] = redact_log_value(value)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
