"""Control-plane policy updates and desired capability publication."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.publication import article_output_path
from ai_author_forum.journals.models import Journal
from ai_author_forum.reader_interactions.capabilities import (
    CapabilityDenyStore,
    CapabilityStoreUnavailable,
)
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.models import StaticManifest

from .models import (
    ArticleInteractionPolicy,
    ControlPlaneOutbox,
    JournalInteractionPolicy,
)
from .permissions import require_policy_permission

logger = logging.getLogger(__name__)


class StalePolicy(ValidationError):
    code = "stale_policy"


@dataclass(frozen=True)
class PolicyStatus:
    desired: dict
    applying: bool
    effective: dict
    projection_version: int


def _policy_version(journal_version, article_version):
    return (int(journal_version) << 32) + int(article_version)


def _active_release():
    return StaticManifest.objects.filter(is_active=True).first()


def _artifact_for(article, release):
    if release is None or not article.approved_version_id:
        return None
    from .models import ProtectedArtifact, ProtectedManifest

    protected = ProtectedManifest.objects.filter(
        static_manifest=release,
        validation_status=ProtectedManifest.ValidationStatus.ACTIVATED,
    ).first()
    if protected is None:
        return None
    return ProtectedArtifact.objects.filter(
        article_public_id=article.public_id,
        release_version=release.version,
        approved_revision_id=article.approved_version_id,
        status=ProtectedArtifact.Status.ACTIVATED,
    ).first()


def article_is_active(article, release=None):
    release = release or _active_release()
    if release is None:
        return False
    if (
        article.publication_status != ArticlePage.PublicationStatus.PUBLISHED
        or article.published_version != release.version
        or article.review_status
        not in (ArticlePage.ReviewStatus.APPROVED, ArticlePage.ReviewStatus.PUBLISHED)
    ):
        return False
    return article_output_path(article) in {
        item.get("path") for item in (release.files or []) if item.get("path")
    }


def effective_policy(article, *, journal_policy=None, article_policy=None):
    journal_policy = (
        journal_policy
        or JournalInteractionPolicy.objects.filter(
            journal_id=article.primary_journal_id
        ).first()
    )
    article_policy = (
        article_policy
        or ArticleInteractionPolicy.objects.filter(article=article).first()
    )
    journal_comments = (
        journal_policy.default_comments_mode
        if journal_policy
        else JournalInteractionPolicy.CommentsMode.OPEN
    )
    journal_download = (
        journal_policy.default_pdf_download_enabled if journal_policy else True
    )
    comments_mode = journal_comments
    download_enabled = journal_download
    article_version = article_policy.version if article_policy else 0
    journal_version = journal_policy.version if journal_policy else 0
    if (
        article_policy
        and article_policy.comments_policy
        != ArticleInteractionPolicy.CommentsPolicy.INHERIT
    ):
        comments_mode = article_policy.comments_policy
    if (
        article_policy
        and article_policy.pdf_download_policy
        != ArticleInteractionPolicy.PdfDownloadPolicy.INHERIT
    ):
        download_enabled = (
            article_policy.pdf_download_policy
            == ArticleInteractionPolicy.PdfDownloadPolicy.ENABLED
        )
    release = _active_release()
    active = article_is_active(article, release)
    artifact = _artifact_for(article, release) if active and download_enabled else None
    if not active:
        comments_mode = JournalInteractionPolicy.CommentsMode.HIDDEN
        download_enabled = False
    if artifact is None:
        download_enabled = False
    return {
        "article_public_id": str(article.public_id),
        "journal_id": article.primary_journal_id,
        "active_release": release.version if active and release else "",
        "approved_revision_id": article.approved_version_id or 0,
        "comments_mode": comments_mode,
        "download_enabled": bool(download_enabled),
        "protected_artifact_public_id": str(artifact.public_id) if artifact else None,
        "policy_version": _policy_version(journal_version, article_version),
    }


def _enqueue_projection(event_id):
    from .tasks import apply_capability_projection

    apply_capability_projection.apply_async(
        args=[str(event_id)],
        queue="reader_comments",
        argsrepr="(<redacted>,)",
    )


def _assign_projection_versions(payloads):
    from ai_author_forum.reader_interactions.models import ArticleCapabilityProjection

    seed = int(timezone.now().timestamp() * 1_000_000)
    for offset, payload in enumerate(payloads):
        article_id = str(payload["article_public_id"])
        current = (
            ArticleCapabilityProjection.objects.using("interactions")
            .filter(article_public_id=article_id)
            .values_list("projection_version", flat=True)
            .first()
            or 0
        )
        latest_event = (
            ControlPlaneOutbox.objects.filter(
                event_type="reader.capability.desired",
                aggregate_id=article_id,
            )
            .order_by("-created_at", "-pk")
            .values_list("payload", flat=True)
            .first()
            or {}
        )
        latest = int(latest_event.get("projection_version") or 0)
        payload["projection_version"] = max(seed + offset, current + 1, latest + 1)
    return payloads


def _build_events(articles):
    payloads = _assign_projection_versions(
        [effective_policy(article) for article in articles]
    )
    return [
        ControlPlaneOutbox(
            event_type="reader.capability.desired",
            aggregate_type="article_capability",
            aggregate_id=str(payload["article_public_id"]),
            aggregate_version=payload["projection_version"],
            payload=payload,
        )
        for payload in payloads
    ]


def publish_active_capability_projections(articles):
    payloads = _assign_projection_versions(
        [effective_policy(article) for article in articles]
    )
    if not payloads:
        return 0
    try:
        CapabilityDenyStore().set_many_desired(payloads)
    except CapabilityStoreUnavailable as exc:
        raise ValidationError("互动能力安全状态不可用，静态版本未激活。") from exc
    events = [
        ControlPlaneOutbox(
            event_type="reader.capability.desired",
            aggregate_type="article_capability",
            aggregate_id=payload["article_public_id"],
            aggregate_version=payload["projection_version"],
            payload=payload,
        )
        for payload in payloads
    ]
    ControlPlaneOutbox.objects.bulk_create(events)
    event_ids = [str(event.event_id) for event in events]
    transaction.on_commit(
        lambda: [_enqueue_projection(event_id) for event_id in event_ids]
    )
    return len(events)


def _write_policy_change(
    *, actor, journal, article=None, values, expected_version, reason
):
    require_policy_permission(actor, journal, reason=reason)
    if article is not None and article.primary_journal_id != journal.pk:
        raise PermissionDenied("文章不属于该期刊。")
    model = article and ArticleInteractionPolicy or JournalInteractionPolicy
    lookup = {"article": article} if article is not None else {"journal": journal}
    with transaction.atomic(using="default"):
        locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
        locked_policy = model.objects.select_for_update().filter(**lookup).first()
        current_version = locked_policy.version if locked_policy else 0
        if int(expected_version) != current_version:
            raise StalePolicy("互动政策版本已变化，请刷新后重试。")
        if locked_policy is None:
            locked_policy = model(**lookup, version=1, updated_by=actor, **values)
        else:
            locked_policy.version = current_version + 1
            locked_policy.updated_by = actor
            for field, value in values.items():
                setattr(locked_policy, field, value)
        locked_policy.updated_at = timezone.now()
        locked_policy.save(using="default")

        articles = (
            [article]
            if article is not None
            else list(
                ArticlePage.objects.filter(
                    primary_journal=locked_journal
                ).select_related("primary_journal")
            )
        )
        payloads = []
        for item in articles:
            item_policy = locked_policy if article is not None else None
            payloads.append(
                effective_policy(
                    item,
                    journal_policy=locked_policy if article is None else None,
                    article_policy=item_policy,
                )
            )
        _assign_projection_versions(payloads)
        try:
            CapabilityDenyStore().set_many_desired(payloads)
        except CapabilityStoreUnavailable as exc:
            raise ValidationError("互动能力安全状态不可用，政策未保存。") from exc
        events = [
            ControlPlaneOutbox(
                event_type="reader.capability.desired",
                aggregate_type="article_capability",
                aggregate_id=payload["article_public_id"],
                aggregate_version=payload["projection_version"],
                payload=payload,
            )
            for payload in payloads
        ]
        ControlPlaneOutbox.objects.using("default").bulk_create(events)
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            actor=actor,
            target=locked_policy,
            message="Reader interaction policy updated",
            metadata={
                "journal_id": locked_journal.pk,
                "article_id": article.pk if article is not None else None,
                "version": locked_policy.version,
                "reason": str(reason or "")[:500],
                "projection_events": len(events),
            },
        )
        event_ids = [str(event.event_id) for event in events]
        transaction.on_commit(
            lambda: [_enqueue_projection(event_id) for event_id in event_ids]
        )
        return locked_policy, len(events)


def update_journal_policy(
    *, actor, journal, expected_version, comments_mode, download_enabled, reason=""
):
    if not getattr(journal, "pk", None):
        raise ValidationError("期刊不存在或不可用。")
    if comments_mode not in JournalInteractionPolicy.CommentsMode.values:
        raise ValidationError("无效的评论模式。")
    return _write_policy_change(
        actor=actor,
        journal=journal,
        values={
            "default_comments_mode": comments_mode,
            "default_pdf_download_enabled": bool(download_enabled),
        },
        expected_version=expected_version,
        reason=reason,
    )


def update_article_policy(
    *, actor, article, expected_version, comments_policy, pdf_download_policy, reason=""
):
    if comments_policy not in ArticleInteractionPolicy.CommentsPolicy.values:
        raise ValidationError("无效的文章评论政策。")
    if pdf_download_policy not in ArticleInteractionPolicy.PdfDownloadPolicy.values:
        raise ValidationError("无效的文章 PDF 政策。")
    return _write_policy_change(
        actor=actor,
        journal=article.primary_journal,
        article=article,
        values={
            "comments_policy": comments_policy,
            "pdf_download_policy": pdf_download_policy,
        },
        expected_version=expected_version,
        reason=reason,
    )


def policy_status(article):
    desired = effective_policy(article)
    from ai_author_forum.reader_interactions.models import ArticleCapabilityProjection

    projection = (
        ArticleCapabilityProjection.objects.using("interactions")
        .filter(article_public_id=article.public_id)
        .first()
    )
    effective = {
        "comments_mode": projection.comments_mode if projection else "hidden",
        "download_enabled": bool(projection and projection.download_enabled),
        "active_release": projection.active_release if projection else "",
    }
    return PolicyStatus(
        desired=desired,
        applying=projection is None
        or projection.policy_version != desired["policy_version"]
        or projection.active_release != desired["active_release"]
        or projection.approved_revision_id != desired["approved_revision_id"]
        or projection.protected_artifact_public_id
        != (
            UUID(desired["protected_artifact_public_id"])
            if desired["protected_artifact_public_id"]
            else None
        ),
        effective=effective,
        projection_version=projection.projection_version if projection else 0,
    )
