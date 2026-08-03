from __future__ import annotations

from django.conf import settings

ARTICLE_DOCUMENT_IMPORT_ENABLED = getattr(
    settings, "ARTICLE_DOCUMENT_IMPORT_ENABLED", True
)
