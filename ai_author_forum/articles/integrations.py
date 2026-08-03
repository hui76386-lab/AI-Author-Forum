import inspect

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.utils.module_loading import import_string
from wagtail.models import Site

ACTIVE_JOURNAL_SERVICE_PATHS = (
    "journals.services.get_active_journals",
    "ai_author_forum.journals.services.get_active_journals",
    "ai_author_forum.journals.services.get_active_journals",
)

SITE_SETTINGS_MODEL_NAME = "SiteSettings"
AUDIT_LOG_MODEL_NAME = "AuditLog"


def get_active_journal_queryset(journal_model=None):
    get_active_journals = _import_first(ACTIVE_JOURNAL_SERVICE_PATHS)
    if get_active_journals:
        journals = get_active_journals()
        return _normalise_journal_queryset(journals, journal_model)

    if journal_model is None:
        return None

    queryset = journal_model.objects.all()
    field_names = {field.name for field in journal_model._meta.fields}

    if "status" in field_names:
        queryset = queryset.filter(status="enabled")
    elif "enabled" in field_names:
        queryset = queryset.filter(enabled=True)
    elif "is_enabled" in field_names:
        queryset = queryset.filter(is_enabled=True)

    return queryset


def get_article_fallback_context(article, request=None):
    from .display import resolve_article_image
    from ai_author_forum.utils.public_i18n import (
        localized_article_abstract,
        localized_article_title,
    )

    site_settings = get_site_settings(request)
    article_image = resolve_article_image(
        article,
        request=request,
        site_settings=site_settings,
    )
    fallback_image = article_image.image
    seo_description = (
        article.search_description
        or _first_present(
            site_settings,
            (
                "default_search_description",
                "default_meta_description",
                "default_seo_description",
                "seo_description",
                "site_description",
                "description",
            ),
        )
        or ""
    )
    title_suffix = _first_present(
        site_settings,
        (
            "default_title_suffix",
            "seo_title_suffix",
            "title_suffix",
        ),
    )
    seo_title = article.seo_title or localized_article_title(article)
    if not article.seo_title and not seo_description:
        seo_description = localized_article_abstract(article)
    if title_suffix and title_suffix not in seo_title:
        seo_title = f"{seo_title} {title_suffix}"

    return {
        "site_settings": site_settings,
        "fallback_image": fallback_image,
        "article_image": article_image,
        "seo": {
            "title": seo_title,
            "description": seo_description,
            "image": article_image.image,
            "image_alt": article_image.alt,
            "canonical_url": article.get_absolute_url(),
        },
    }


def get_site_settings(request=None):
    site_settings_model = _get_model_by_class_name(SITE_SETTINGS_MODEL_NAME)
    if site_settings_model is None:
        return None

    site = _get_site(request)
    if site and hasattr(site_settings_model, "for_site"):
        try:
            return site_settings_model.for_site(site)
        except (AttributeError, site_settings_model.DoesNotExist):
            return None

    queryset = site_settings_model.objects.all()
    if site and _model_has_field(site_settings_model, "site"):
        queryset = queryset.filter(site=site)

    return queryset.first()


def log_article_audit(action, article, user=None, comment="", metadata=None):
    audit_log_model = _get_model_by_class_name(AUDIT_LOG_MODEL_NAME)
    if audit_log_model is None:
        return None

    metadata = metadata or {}
    for logger in _get_audit_log_callables(audit_log_model):
        result = _call_audit_logger(
            logger,
            action=action,
            article=article,
            user=user,
            comment=comment,
            metadata=metadata,
        )
        if result is not None:
            return result

    field_values = _build_audit_log_field_values(
        audit_log_model,
        action=action,
        article=article,
        user=user,
        comment=comment,
        metadata=metadata,
    )
    if field_values is None:
        return None

    try:
        return audit_log_model.objects.create(**field_values)
    except Exception:
        return None


def _import_first(paths):
    for path in paths:
        try:
            return import_string(path)
        except (ImportError, AttributeError):
            continue

    return None


def _normalise_journal_queryset(journals, journal_model):
    if journals is None:
        return None

    if hasattr(journals, "all"):
        return journals

    if journal_model is None:
        return journals

    journal_ids = [
        journal.pk for journal in journals if getattr(journal, "pk", None) is not None
    ]
    return journal_model.objects.filter(pk__in=journal_ids)


def _get_model_by_class_name(class_name):
    for model in apps.get_models():
        if model.__name__ == class_name:
            return model

    return None


def _get_site(request=None):
    if request is not None and getattr(request, "site", None):
        return request.site

    try:
        return Site.objects.get(is_default_site=True)
    except Site.DoesNotExist:
        return None


def _first_present(obj, field_names):
    if obj is None:
        return None

    for field_name in field_names:
        value = getattr(obj, field_name, None)
        if value:
            return value

    return None


def _model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return False

    return True


def _get_audit_log_callables(audit_log_model):
    callables = []

    for name in ("log", "record", "create_entry"):
        logger = getattr(audit_log_model, name, None)
        if callable(logger):
            callables.append(logger)

    for name in ("log", "record", "create_entry"):
        logger = getattr(audit_log_model.objects, name, None)
        if callable(logger):
            callables.append(logger)

    return callables


def _call_audit_logger(logger, action, article, user, comment, metadata):
    kwargs = {
        "action": action,
        "status": "success",
        "article": article,
        "page": article,
        "target": article,
        "user": user,
        "actor": user,
        "comment": comment,
        "message": comment,
        "description": comment,
        "content_object": article,
        "target_object": article,
        "object": article,
        "object_id": article.pk,
        "object_repr": str(article),
        "target_type": "ArticlePage",
        "target_id": str(article.pk),
        "target_label": str(article),
        "metadata": {"article_event": action, **metadata},
        "data": metadata,
        "details": metadata,
        "extra": metadata,
        "extra_data": metadata,
    }

    try:
        return logger(**_filter_logger_kwargs(logger, kwargs))
    except TypeError:
        return None
    except Exception:
        return None


def _filter_logger_kwargs(logger, kwargs):
    try:
        signature = inspect.signature(logger)
    except (TypeError, ValueError):
        return kwargs

    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return kwargs

    return {
        name: kwargs[name]
        for name, parameter in signature.parameters.items()
        if name in kwargs
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }


def _build_audit_log_field_values(
    audit_log_model,
    action,
    article,
    user,
    comment,
    metadata,
):
    content_type = ContentType.objects.get_for_model(
        article,
        for_concrete_model=False,
    )
    field_values = {}

    for field in audit_log_model._meta.fields:
        if field.primary_key or getattr(field, "auto_now", False):
            continue

        value = _get_audit_field_value(
            field,
            action=action,
            article=article,
            user=user,
            comment=comment,
            metadata=metadata,
            content_type=content_type,
        )
        if value is not None:
            field_values[field.name] = value
            continue

        if (
            getattr(field, "auto_now_add", False)
            or field.blank
            or field.null
            or field.has_default()
        ):
            continue

        return None

    return field_values


def _get_audit_field_value(
    field,
    action,
    article,
    user,
    comment,
    metadata,
    content_type,
):
    name = field.name

    if name in {"action", "event", "operation"}:
        return action
    if name in {"status"}:
        return "success"
    if name in {"user", "actor", "reviewer", "created_by"}:
        return user
    if name in {"article", "page", "target", "content_object"}:
        return article
    if name in {"object_id", "target_id", "page_id", "article_id"}:
        return str(article.pk)
    if name in {"target_label", "object_repr"}:
        return str(article)
    if name in {"content_type"}:
        return content_type
    if name in {"object_type", "target_type", "model_name"}:
        return "articles.ArticlePage"
    if name in {"comment", "message", "description"}:
        return comment
    if name in {"metadata", "data", "details", "extra"}:
        return metadata

    return None
