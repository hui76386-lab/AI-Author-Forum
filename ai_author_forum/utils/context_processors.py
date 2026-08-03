from django.conf import settings
from django.utils import translation

from ai_author_forum.utils.i18n import (
    LANGUAGE_NAMES,
    SUPPORTED_LANGUAGE_CODES,
    language_switch_options,
    normalize_language,
)


def global_vars(request):
    return {
        "SEO_NOINDEX": getattr(settings, "SEO_NOINDEX", False),
        "LANGUAGE_CODE": normalize_language(translation.get_language()),
    }


def language_context(request):
    """Expose the public/admin language state to shared templates."""
    current_language = normalize_language(translation.get_language())
    return {
        "CURRENT_LANGUAGE_CODE": current_language,
        "SUPPORTED_PUBLIC_LANGUAGES": SUPPORTED_LANGUAGE_CODES,
        "LANGUAGE_SWITCH_OPTIONS": language_switch_options(
            getattr(request, "path", "/"),
            getattr(request, "META", {}).get("QUERY_STRING", ""),
        ),
        "CURRENT_LANGUAGE_NAME": LANGUAGE_NAMES[current_language]["native_name"],
    }


def site_frontend(request):
    """Shared frontend configuration for the Nature-style public shell."""
    site_settings = None
    header_journals = []
    managed_navigation = {"navigation_set": None, "groups": (), "scope": "main_site"}
    try:
        from ai_author_forum.articles.integrations import get_site_settings
        from ai_author_forum.journals.models import Journal
        from ai_author_forum.journals.services import get_active_journals
        from ai_author_forum.site_settings.navigation import get_navigation_context
        from ai_author_forum.utils.i18n import strip_public_language_prefix

        site_settings = get_site_settings(request)
        header_journals = list(get_active_journals()[:120])
        path = strip_public_language_prefix(getattr(request, "path", "") or "")
        journal = None
        if path.startswith("/journals/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2 and parts[0] == "journals":
                journal = Journal.objects.filter(slug=parts[1]).first()
        managed_navigation = get_navigation_context(
            journal=journal, current_path=path, strict=False
        )
    except Exception:
        # Public pages must remain renderable during first install/migrations.
        pass
    return {
        "site_settings": site_settings,
        "header_journals": header_journals,
        "managed_navigation": managed_navigation,
    }
