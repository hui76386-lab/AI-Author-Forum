import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.exceptions import (
    PermissionDenied,
    SuspiciousFileOperation,
    ValidationError,
)
from django.db import connection, transaction
from django.utils import timezone
from django.utils.module_loading import import_string
from filelock import FileLock, Timeout

from ai_author_forum.articles.publication import (
    prepare_articles_for_build,
    record_article_build_failure,
    record_article_build_success,
    sync_articles_to_active_manifest,
)
from ai_author_forum.site_settings.access_control import is_super_admin
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event
from ai_author_forum.static_publish.category_services import (
    CategoryPublicationConsistencyError,
    validate_category_publication_consistency,
)
from ai_author_forum.static_publish.readiness import (
    check_content_readiness,
    check_homepage_readiness,
    format_blockers,
    requires_content_readiness,
    requires_homepage_readiness,
)
from ai_author_forum.static_publish.render_context import static_release_context

from .models import (
    StaticBuildLog,
    StaticManifest,
    StaticPublishJob,
    StaticPublishTarget,
)

logger = logging.getLogger(__name__)

NGINX_DIRECT_MARKER = ".nginx-direct-ready"
NGINX_REDIRECT_ROOT = ".nginx-redirects"
# These markers are the public article-page contract.  A selective release must
# refresh carried-forward pages that predate (or only partially contain) the
# reader interaction mount.
READER_INTERACTIONS_MARKERS = (
    b"data-reader-interactions",
    b"data-reader-comments",
    b"data-reader-share",
    b"data-reader-copy",
    b"data-reader-download",
)

TARGET_TYPE_LABELS = {
    "journal_index": "子期刊总目录",
    "journal_page": "子期刊主页",
    "category_page": "本刊栏目页",
    "category_redirect": "栏目历史地址",
    "article_page": "文章详情页",
    "journal_current_issue": "本刊当前期次",
    "journal_issue_archive": "本刊期次归档",
    "issue_detail": "期次详情页",
    "managed_content_column": "导航内容栏目",
    "managed_navigation_info": "导航信息页",
    "wagtail_page": "站点内容页",
    "search_page": "搜索页",
}


class PublishError(RuntimeError):
    def __init__(self, message, status=StaticPublishJob.Status.FAILED):
        super().__init__(message)
        self.status = status


class PublishLocked(PublishError):
    pass


class AuditWriteError(PublishError):
    pass


@dataclass(frozen=True)
class RenderedTargetSnapshot:
    target: object
    record: StaticPublishTarget
    content: bytes | None
    error: Exception | None
    duration_ms: int


class AssetReferenceParser(HTMLParser):
    attributes = {"src", "href", "poster", "srcset"}

    def __init__(self):
        super().__init__()
        self.references = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name not in self.attributes or not value:
                continue
            values = value.split(",") if name == "srcset" else [value]
            for item in values:
                self.references.add(item.strip().split(" ", 1)[0])


def require_static_permission(user, permission):
    if not is_super_admin(user):
        raise PermissionDenied("Only an active super administrator may publish.")


def require_publish_job_permission(job):
    if is_super_admin(job.triggered_by):
        return
    from ai_author_forum.placements.publishing import (
        require_automatic_placement_publish_job,
    )

    require_automatic_placement_publish_job(job)


def create_publish_job(*, scope, paths, actor):
    require_static_permission(actor, "static_publish.publish_static_site")
    require_static_permission(actor, "static_publish.publish_category_pages")
    return StaticPublishJob.objects.create(
        scope=scope, requested_paths=list(paths), triggered_by=actor
    )


def create_retry_job(
    *, failed_job, actor, paths, target_ids=None, scope=StaticPublishJob.Scope.RETRY
):
    require_static_permission(actor, "static_publish.retry_category_publish")
    selected_paths = list(paths or [])
    if not selected_paths and scope != StaticPublishJob.Scope.FULL:
        raise PublishError("No failed targets were selected for retry")
    target_ids = list(target_ids or [])
    with transaction.atomic():
        job = StaticPublishJob.objects.create(
            scope=scope,
            requested_paths=selected_paths,
            retry_of=failed_job,
            triggered_by=actor,
            summary={"retry_target_ids": target_ids},
        )
        # A retry supersedes the failed publish attempt for any placement batch
        # that initiated it, while the original job remains in the audit trail.
        from ai_author_forum.placements.models import PlacementBatch

        PlacementBatch.objects.filter(publish_job=failed_job).update(
            publish_job=job,
            publish_status=PlacementBatch.PublishStatus.QUEUED,
            updated_at=timezone.now(),
        )
        record_audit_event(
            action=AuditAction.RETRY,
            status=AuditStatus.STARTED,
            actor=actor,
            target=job,
            message="Static retry queued",
            metadata={
                "retry_of": failed_job.pk,
                "paths": selected_paths,
                "target_ids": target_ids,
                "stage": "queued",
            },
        )
    return job


def create_rollback_job(*, version, actor, reason):
    require_static_permission(actor, "static_publish.rollback_category_publish")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("回滚原因至少需要 5 个字符。")
    version = str(version)
    with transaction.atomic():
        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.ROLLBACK,
            status=StaticPublishJob.Status.PENDING,
            rollback_version=version,
            rollback_reason=reason,
            version=version,
            triggered_by=actor,
        )
        record_audit_event(
            action=AuditAction.ROLLBACK,
            status=AuditStatus.STARTED,
            actor=actor,
            target=job,
            message="Static rollback queued",
            metadata={"version": version, "reason": reason, "stage": "queued"},
        )
    return job


def mark_publish_job_queue_failure(job, error):
    message = f"发布任务无法进入队列：{error}"
    job.status = StaticPublishJob.Status.FAILED
    job.error = message
    job.finished_at = timezone.now()
    job.save(update_fields=("status", "error", "finished_at"))
    action = {
        StaticPublishJob.Scope.RETRY: AuditAction.RETRY,
        StaticPublishJob.Scope.ROLLBACK: AuditAction.ROLLBACK,
    }.get(job.scope, AuditAction.PUBLISH)
    try:
        record_audit_event(
            action=action,
            status=AuditStatus.FAILURE,
            actor=job.triggered_by,
            target=job,
            message="Static publish task queueing failed",
            metadata={"stage": "queue", "error": str(error)},
        )
    except Exception as exc:
        logger.exception("Unable to persist static queue failure audit")
        job.error = f"{message}; audit failure: {exc}"[:8000]
        job.save(update_fields=("error",))
    return job


def estimate_publish_targets(paths=None):
    provider_class = import_string(settings.STATIC_PUBLISH_TARGET_PROVIDER)
    targets = list(provider_class().get_targets(paths=paths or None))
    by_type = {}
    for target in targets:
        target_type = getattr(target, "target_type", "page") or "page"
        by_type[target_type] = by_type.get(target_type, 0) + 1
    return {
        "total": len(targets),
        "types": [
            (TARGET_TYPE_LABELS.get(target_type, target_type), count)
            for target_type, count in sorted(by_type.items())
        ],
        "paths": [target.output_path for target in targets[:20]],
        "truncated": len(targets) > 20,
    }


def get_journal_publish_paths(journal):
    """Return all static targets owned by one active journal.

    The provider remains the source of truth for localized paths and target
    dependencies. This helper only turns that target graph into the explicit
    path list understood by the selective publisher.
    """
    provider_class = import_string(settings.STATIC_PUBLISH_TARGET_PROVIDER)
    targets = list(provider_class().get_targets())
    language_codes = {
        str(code).lower().split("-", 1)[0]
        for code, _label in getattr(settings, "LANGUAGES", ())
    }
    paths = set()
    for target in targets:
        clean_path = urlsplit(target.url).path.strip("/").lower()
        parts = clean_path.split("/") if clean_path else []
        is_journal_path = (
            len(parts) >= 2 and parts[0] == "journals" and parts[1] == journal.slug
        ) or (
            len(parts) >= 3
            and parts[0] in language_codes
            and parts[1] == "journals"
            and parts[2] == journal.slug
        )
        # A new journal must also become discoverable from the public A-Z
        # directory. Keep the directory target in the same selective release.
        is_journal_directory = target.target_type == "journal_index"
        is_related_article = target.target_type == "article_page" and journal.pk in (
            target.dependencies or {}
        ).get("journal_ids", ())
        if is_journal_path or is_journal_directory or is_related_article:
            paths.add(target.output_path)
    if not paths:
        raise PublishError(
            f"子期刊 {journal.name} 尚未生成可发布目标。请确认状态为启用，并重新加载页面。"
        )
    return sorted(paths)


def classify_publish_error(exc):
    code = str(getattr(exc, "code", "") or "").upper()
    message = str(exc).lower()
    if "template" in code or "template" in message:
        return StaticPublishTarget.ErrorCategory.TEMPLATE
    if "missing" in message and any(
        word in message for word in ("asset", "media", "image", "static")
    ):
        return StaticPublishTarget.ErrorCategory.ASSET
    if isinstance(exc, FileNotFoundError | PermissionError | OSError):
        return StaticPublishTarget.ErrorCategory.FILE_WRITE
    if (
        isinstance(exc, ValidationError | SuspiciousFileOperation)
        or "validat" in message
    ):
        return StaticPublishTarget.ErrorCategory.VALIDATION
    if isinstance(exc, KeyError | AttributeError) or "does not exist" in message:
        return StaticPublishTarget.ErrorCategory.DATA
    return StaticPublishTarget.ErrorCategory.UNKNOWN


def manifest_diff(current_manifest, target_manifest):
    def file_map(manifest):
        return {item.get("path"): item.get("sha256") for item in (manifest.files or [])}

    current = file_map(current_manifest) if current_manifest else {}
    target = file_map(target_manifest) if target_manifest else {}
    current_paths, target_paths = set(current), set(target)
    added = sorted(target_paths - current_paths)
    deleted = sorted(current_paths - target_paths)
    common = current_paths & target_paths
    modified = sorted(path for path in common if current[path] != target[path])
    unchanged = sorted(path for path in common if current[path] == target[path])
    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
        "counts": {
            "added": len(added),
            "modified": len(modified),
            "deleted": len(deleted),
            "unchanged": len(unchanged),
        },
    }


def safe_relative_path(value):
    raw = unquote(urlsplit(str(value)).path).replace("\\", "/").lstrip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise SuspiciousFileOperation(f"Unsafe static output path: {value!r}")
    return Path(*path.parts)


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StaticPublisher:
    def __init__(self, output_root=None):
        self.root = Path(output_root or settings.STATIC_PUBLISH_ROOT).resolve()
        self.releases = self.root / "releases"
        self.current = self.root / "current"
        self.lock = FileLock(str(self.root / ".publish.lock"), timeout=0)

    def build(self, job, *, audit_action=AuditAction.PUBLISH, audit_context=None):
        require_publish_job_permission(job)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock:
                return self._build_locked(
                    job,
                    audit_action=audit_action,
                    audit_context=audit_context or {},
                )
        except Timeout as exc:
            raise PublishLocked(
                "Another static publish operation is already running"
            ) from exc

    def _build_locked(self, job, *, audit_action, audit_context):
        job.status = StaticPublishJob.Status.RUNNING
        job.started_at = timezone.now()
        job.error = ""
        job.version = self._version(job.pk)
        job.save(update_fields=("status", "started_at", "error", "version"))
        staging = self.root / f".staging-{job.version}"
        if staging.exists():
            shutil.rmtree(staging)

        action_label = (
            audit_action.value if hasattr(audit_action, "value") else str(audit_action)
        )
        try:
            self._audit(
                audit_action,
                AuditStatus.STARTED,
                job,
                f"Static {action_label} started",
                audit_context,
            )
            if job.scope != StaticPublishJob.Scope.FULL:
                if not self.current.exists():
                    raise PublishError("A selective publish requires an active release")
                shutil.copytree(self.current, staging)
            else:
                staging.mkdir(parents=True)

            snapshots, snapshot_at = self._snapshot_targets(
                job, job.requested_paths or None
            )
            targets = [snapshot.target for snapshot in snapshots]
            self._copy_assets(staging)
            succeeded = self._render_targets(job, snapshots, staging)
            failed = job.targets.filter(
                status=StaticPublishTarget.Status.FAILED
            ).count()
            if failed:
                status = (
                    StaticPublishJob.Status.PARTIAL
                    if succeeded
                    else StaticPublishJob.Status.FAILED
                )
                raise PublishError(
                    f"{failed} target(s) failed; active release was not changed", status
                )
            # Wagtail creates image renditions lazily while templates render.
            self._copy_assets(staging)
            self._validate_assets(staging)
            try:
                validation_journal_ids = None
                if job.scope != StaticPublishJob.Scope.FULL:
                    validation_journal_ids = sorted(
                        {
                            int(value)
                            for target in targets
                            for value in (
                                getattr(target, "dependencies", None) or {}
                            ).get("journal_ids", ())
                        }
                    )
                    journal_id = (job.summary or {}).get("journal_id")
                    if journal_id:
                        validation_journal_ids = sorted(
                            {*validation_journal_ids, int(journal_id)}
                        )
                validate_category_publication_consistency(
                    version_id=job.version,
                    targets=targets,
                    staging=staging,
                    journal_ids=validation_journal_ids,
                )
            except CategoryPublicationConsistencyError as exc:
                raise PublishError(str(exc)) from exc
            manifest_preview = self._manifest(
                job, staging, input_snapshot_at=snapshot_at
            )
            self._write_nginx_release_metadata(
                staging,
                version=job.version,
                targets=manifest_preview["targets"],
            )
            manifest_data = self._manifest(job, staging, input_snapshot_at=snapshot_at)
            (staging / "manifest.json").write_text(
                json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._validate_release_integrity_data(staging, manifest_data)
            release = self.releases / job.version
            release.parent.mkdir(parents=True, exist_ok=True)
            self._move_directory(staging, release)
            manifest = self._record_candidate_manifest(job, manifest_data)
            protected_manifest = None
            if settings.READER_PDF_GRANTS_ENABLED:
                from ai_author_forum.reader_access.pdfs import (
                    wait_for_protected_release,
                )

                protected_manifest = wait_for_protected_release(manifest)
            activation = self._activate(release)
            try:
                with transaction.atomic():
                    if settings.READER_PDF_GRANTS_ENABLED:
                        from ai_author_forum.reader_access.pdfs import (
                            activate_protected_manifest,
                        )

                        protected_manifest = activate_protected_manifest(manifest)
                    manifest = self._record_manifest(job, manifest_data)
                    changed_articles = sync_articles_to_active_manifest(manifest)
                    if settings.READER_INTERACTIONS_ENABLED:
                        from ai_author_forum.reader_access.services import (
                            publish_active_capability_projections,
                        )

                        publish_active_capability_projections(changed_articles)
                    self._finish(
                        job,
                        StaticPublishJob.Status.SUCCEEDED,
                        manifest_data["summary"],
                    )
                    self._audit(
                        audit_action,
                        AuditStatus.SUCCESS,
                        job,
                        f"Static {action_label} release activated",
                        {
                            **audit_context,
                            "version": job.version,
                            "summary": manifest_data["summary"],
                            "protected_manifest_sha256": (
                                protected_manifest.sha256
                                if protected_manifest is not None
                                else ""
                            ),
                        },
                    )
            except Exception:
                self._restore_activation(activation)
                raise
            self._finalize_activation(activation)
            self._log(job, "info", "Static release activated", {"version": job.version})
            self._prune()
            return manifest
        except Exception as exc:
            status = getattr(exc, "status", StaticPublishJob.Status.FAILED)
            self._finish(job, status, summary=job.summary, error=str(exc))
            self._log(job, "error", "Static publish failed", {"error": str(exc)})
            try:
                self._audit(
                    audit_action,
                    AuditStatus.FAILURE,
                    job,
                    f"Static {action_label} failed",
                    {**audit_context, "error": str(exc)},
                )
            except AuditWriteError as audit_exc:
                logger.exception("Failed to persist the terminal static publish audit")
                job.error = f"{job.error}; audit failure: {audit_exc}"[:8000]
                job.save(update_fields=("error",))
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def mark_worker_preflight_failure(self, job, *, action, error, metadata=None):
        error_text = str(error).strip() or type(error).__name__
        context = {
            **(metadata or {}),
            "stage": "worker_preflight",
            "error": error_text,
        }
        self._finish(
            job,
            StaticPublishJob.Status.FAILED,
            summary=job.summary,
            error=error_text,
        )
        self._log(job, "error", "Static worker preflight failed", context)
        try:
            self._audit(
                action,
                AuditStatus.FAILURE,
                job,
                "Static worker preflight failed",
                context,
            )
        except AuditWriteError as audit_exc:
            logger.exception("Failed to persist the worker preflight failure audit")
            job.error = f"{job.error}; audit failure: {audit_exc}"[:8000]
            job.save(update_fields=("error",))
        return job

    def run_retry(self, job, user=None):
        selected_paths = list(job.requested_paths or [])
        target_ids = list((job.summary or {}).get("retry_target_ids", []))
        audit_context = {
            "retry_of": job.retry_of_id,
            "paths": selected_paths,
            "target_ids": target_ids,
        }
        try:
            require_static_permission(user, "static_publish.retry_category_publish")
            if not job.retry_of_id or job.scope not in {
                StaticPublishJob.Scope.RETRY,
                StaticPublishJob.Scope.FULL,
            }:
                raise PublishError("Retry job is missing its source job")
            if not selected_paths and job.scope != StaticPublishJob.Scope.FULL:
                raise PublishError("No failed targets were selected for retry")
        except Exception as exc:
            self.mark_worker_preflight_failure(
                job,
                action=AuditAction.RETRY,
                error=exc,
                metadata=audit_context,
            )
            self._sync_retrying_placement_batches(job)
            raise
        try:
            self.build(
                job,
                audit_action=AuditAction.RETRY,
                audit_context={**audit_context, "stage": "worker"},
            )
        finally:
            self._sync_retrying_placement_batches(job)
        return job

    def retry(self, failed_job, user=None, *, paths=None, target_ids=None):
        failed_targets = failed_job.targets.filter(
            status=StaticPublishTarget.Status.FAILED
        )
        if target_ids:
            failed_targets = failed_targets.filter(pk__in=target_ids)
        selected_paths = list(
            paths
            or failed_targets.values_list("path", flat=True)
            or failed_job.requested_paths
            or []
        )
        job = create_retry_job(
            failed_job=failed_job,
            actor=user,
            paths=selected_paths,
            target_ids=target_ids,
            scope=(
                StaticPublishJob.Scope.RETRY
                if selected_paths and self.current.exists()
                else StaticPublishJob.Scope.FULL
            ),
        )
        return self.run_retry(job, user)

    @staticmethod
    def _sync_retrying_placement_batches(job):
        from ai_author_forum.placements.publishing import sync_batch_publish_status

        for batch in job.placement_batches.select_related("publish_job"):
            sync_batch_publish_status(batch)

    def run_rollback(self, job, user=None):
        version = str(job.rollback_version or job.version)
        reason = (job.rollback_reason or "").strip()
        stage = "worker_preflight"
        try:
            require_static_permission(user, "static_publish.rollback_category_publish")
            if job.scope != StaticPublishJob.Scope.ROLLBACK:
                raise PublishError("Rollback job has an invalid scope")
            if len(reason) < 5:
                raise ValidationError("回滚原因至少需要 5 个字符")
            job.status = StaticPublishJob.Status.RUNNING
            job.started_at = timezone.now()
            job.error = ""
            job.save(update_fields=("status", "started_at", "error"))
            self._audit(
                AuditAction.ROLLBACK,
                AuditStatus.STARTED,
                job,
                "Static rollback started",
                {"version": version, "reason": reason, "stage": "worker"},
            )
            stage = "worker"
            release = (self.releases / version).resolve()
            if release.parent != self.releases.resolve() or not release.is_dir():
                raise PublishError(f"Release {version!r} does not exist")
            try:
                manifest = StaticManifest.objects.get(version=version)
            except StaticManifest.DoesNotExist as exc:
                raise PublishError(
                    f"Manifest for release {version!r} does not exist"
                ) from exc
            self._validate_release_integrity(release, manifest)
            protected_manifest = None
            if settings.READER_PDF_GRANTS_ENABLED:
                from ai_author_forum.reader_access.pdfs import (
                    require_activated_protected_pair,
                )

                protected_manifest = require_activated_protected_pair(manifest)

            with self.lock:
                activation = self._activate(release)
                try:
                    with transaction.atomic():
                        StaticManifest.objects.filter(is_active=True).update(
                            is_active=False
                        )
                        manifest.is_active = True
                        manifest.save(update_fields=("is_active",))
                        changed_articles = sync_articles_to_active_manifest(manifest)
                        if settings.READER_INTERACTIONS_ENABLED:
                            from ai_author_forum.reader_access.services import (
                                publish_active_capability_projections,
                            )

                            publish_active_capability_projections(changed_articles)
                        self._finish(
                            job,
                            StaticPublishJob.Status.ROLLED_BACK,
                            {"version": version, "reason": reason},
                        )
                        self._audit(
                            AuditAction.ROLLBACK,
                            AuditStatus.SUCCESS,
                            job,
                            "Static release rolled back",
                            {
                                "version": version,
                                "reason": reason,
                                "protected_manifest_sha256": (
                                    protected_manifest.sha256
                                    if protected_manifest is not None
                                    else ""
                                ),
                            },
                        )
                except Exception:
                    self._restore_activation(activation)
                    raise
                self._finalize_activation(activation)
                self._log(
                    job, "info", "Static release rolled back", {"version": version}
                )
        except Exception as exc:
            if stage == "worker_preflight":
                self.mark_worker_preflight_failure(
                    job,
                    action=AuditAction.ROLLBACK,
                    error=exc,
                    metadata={"version": version, "reason": reason},
                )
            else:
                error_text = str(exc).strip() or type(exc).__name__
                self._finish(job, StaticPublishJob.Status.FAILED, error=error_text)
                try:
                    self._audit(
                        AuditAction.ROLLBACK,
                        AuditStatus.FAILURE,
                        job,
                        "Static rollback failed",
                        {
                            "version": version,
                            "reason": reason,
                            "stage": "worker",
                            "error": error_text,
                        },
                    )
                except AuditWriteError as audit_exc:
                    logger.exception("Failed to persist the terminal rollback audit")
                    job.error = f"{job.error}; audit failure: {audit_exc}"[:8000]
                    job.save(update_fields=("error",))
            raise
        return job

    def rollback(self, version, user=None, *, reason=""):
        job = create_rollback_job(version=version, actor=user, reason=reason)
        return self.run_rollback(job, user)

    def _targets(self, paths):
        provider_class = import_string(settings.STATIC_PUBLISH_TARGET_PROVIDER)
        return provider_class().get_targets(paths=paths)

    def _refresh_stale_reader_article_targets(self, job, targets, paths):
        """Re-render carried-forward article pages missing reader interactions.

        Selective releases start from a copy of ``current``.  That is normally
        desirable, but it also means an article rendered before the interaction
        mount was introduced can survive indefinitely when it is not one of the
        explicitly requested paths.  The immutable release remains the source
        of truth; we only add a provider-owned article target when the existing
        file is missing the required contract marker.
        """
        if not paths or not self.current.is_dir():
            return targets

        manifest_path = self.current / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            # Release integrity validation will report a malformed current
            # release.  Do not make target discovery fail with a second error.
            return targets

        stale_paths = set()
        for entry in manifest.get("targets") or ():
            if not isinstance(entry, dict):
                continue
            if (
                entry.get("target_type") != "article_page"
                or entry.get("action") == StaticPublishTarget.Action.DELETE
            ):
                continue
            output_path = str(entry.get("output_path") or "")
            if not output_path:
                continue
            try:
                content = (self.current / safe_relative_path(output_path)).read_bytes()
            except (OSError, SuspiciousFileOperation):
                continue
            # The reader bootstrap rejects a page rendered from an older
            # release, even when that page already contains every interaction
            # marker.  Carry-forward releases therefore need a fresh render
            # whenever their embedded release differs from this job.
            release_matches = (
                f'data-release="{job.version}"'.encode("utf-8") in content
            )
            if not all(marker in content for marker in READER_INTERACTIONS_MARKERS) or not release_matches:
                stale_paths.add(output_path)

        if not stale_paths:
            return targets

        # A journal-scoped publish must not unexpectedly publish another
        # journal's stale pages.  Selective and retry jobs intentionally repair
        # all carried-forward article pages because they are the compatibility
        # path used for template/static contract rollouts.
        selected_paths = {target.output_path for target in targets}
        candidates = {
            target.output_path: target
            for target in self._targets(None)
            if getattr(target, "target_type", "") == "article_page"
        }
        if job.scope == StaticPublishJob.Scope.JOURNAL:
            journal_id = (job.summary or {}).get("journal_id")
            try:
                journal_id = int(journal_id) if journal_id else None
            except (TypeError, ValueError):
                journal_id = None
            if journal_id:
                stale_paths = {
                    path
                    for path in stale_paths
                    if path in candidates
                    and journal_id
                    in (candidates[path].dependencies or {}).get("journal_ids", ())
                }
            else:
                stale_paths = set()
        if not stale_paths:
            return targets

        refreshed = [
            candidates[path]
            for path in sorted(stale_paths - selected_paths)
            if path in candidates
        ]
        return [*targets, *refreshed]

    def _snapshot_targets(self, job, paths):
        had_outer_transaction = connection.in_atomic_block
        with transaction.atomic():
            self._configure_snapshot_transaction(had_outer_transaction)
            snapshot_at = timezone.now()
            targets = list(self._targets(paths))
            targets = self._refresh_stale_reader_article_targets(job, targets, paths)
            if paths and not targets:
                raise PublishError("没有发布目标匹配所请求的路径")
            self._validate_target_paths(targets)
            readiness = None
            if requires_content_readiness(targets):
                if job.scope == StaticPublishJob.Scope.FULL:
                    readiness = check_content_readiness(targets=targets, at=snapshot_at)
                elif requires_homepage_readiness(targets):
                    readiness = check_homepage_readiness(at=snapshot_at)
                elif job.scope == StaticPublishJob.Scope.JOURNAL:
                    journal_id = (job.summary or {}).get("journal_id")
                    journal_ids = (
                        [int(journal_id)]
                        if journal_id
                        else sorted(
                            {
                                int(value)
                                for target in targets
                                for value in (target.dependencies or {}).get(
                                    "journal_ids", ()
                                )
                                if str(value).isdigit()
                            }
                        )
                    )
                    readiness = check_content_readiness(
                        targets=targets,
                        at=snapshot_at,
                        journal_ids=journal_ids or None,
                    )
            if readiness is not None:
                job.summary = {
                    **(job.summary or {}),
                    "content_readiness": readiness.to_dict(),
                }
                job.save(update_fields=("summary",))
                if not readiness.is_ready:
                    raise PublishError(
                        "Content readiness check failed: "
                        f"{format_blockers(readiness)}"
                    )
            prepare_articles_for_build(targets)

        records = []
        for target in targets:
            records.append(
                StaticPublishTarget.objects.create(
                    job=job,
                    path=target.output_path,
                    source=target.source,
                    target_type=getattr(target, "target_type", "page"),
                    target_id=getattr(target, "target_id", "") or target.source,
                    canonical_path=(
                        getattr(target, "canonical_path", "")
                        or getattr(target, "url", "")
                    ),
                    action=getattr(target, "action", StaticPublishTarget.Action.UPSERT),
                    dependencies=getattr(target, "dependencies", {}) or {},
                    http_status=getattr(target, "http_status", None),
                    redirect_to=getattr(target, "redirect_to", "") or "",
                )
            )

        snapshots = []
        for target, record in zip(targets, records, strict=True):
            started = time.monotonic()
            content = None
            error = None
            record.status = StaticPublishTarget.Status.RUNNING
            record.save(update_fields=("status",))
            try:
                if record.action != StaticPublishTarget.Action.DELETE:
                    with static_release_context(job.version):
                        content = bytes(target.render())
                    if not content.strip():
                        raise PublishError("Rendered page is empty")
            except Exception as exc:
                error = exc
            snapshots.append(
                RenderedTargetSnapshot(
                    target=target,
                    record=record,
                    content=content,
                    error=error,
                    duration_ms=max(1, int((time.monotonic() - started) * 1000)),
                )
            )
        return tuple(snapshots), snapshot_at

    @staticmethod
    def _configure_snapshot_transaction(had_outer_transaction):
        if connection.vendor != "postgresql":
            return
        if had_outer_transaction:
            raise PublishError(
                "静态发布必须在现有数据库事务之外启动，" "以建立可重复读的输入快照。"
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE"
            )

    def _validate_target_paths(self, targets):
        paths = [target.output_path for target in targets]
        duplicates = sorted({path for path in paths if paths.count(path) > 1})
        if duplicates:
            raise PublishError(
                "CATEGORY_PUBLICATION_DRIFT: manifest output path conflict: "
                + ", ".join(duplicates[:20])
            )

    def _render_targets(self, job, snapshots, staging):
        succeeded = 0
        for snapshot in snapshots:
            target = snapshot.target
            record = snapshot.record
            write_started = time.monotonic()
            action = record.action
            try:
                if snapshot.error is not None:
                    raise snapshot.error
                relative = safe_relative_path(target.output_path)
                destination = staging / relative
                if action == StaticPublishTarget.Action.DELETE:
                    if destination.is_file():
                        destination.unlink()
                    record.status = StaticPublishTarget.Status.SUCCEEDED
                    record.size = 0
                else:
                    content = snapshot.content or b""
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                    record.status = StaticPublishTarget.Status.SUCCEEDED
                    record.checksum = checksum(destination)
                    record.size = destination.stat().st_size
                    record_article_build_success(target.source, job.version)
                record.generated_at = timezone.now()
                record.error = ""
                record.error_code = ""
                record.error_category = ""
                succeeded += 1
            except Exception as exc:
                record.status = StaticPublishTarget.Status.FAILED
                record.error = str(exc)
                record.error_code = getattr(exc, "code", None) or "STATIC_RENDER_FAILED"
                record.error_category = classify_publish_error(exc)
                record_article_build_failure(target.source, exc)
                self._log(
                    job,
                    "error",
                    f"Failed to render {target.output_path}",
                    {
                        "error": str(exc),
                        "error_code": record.error_code,
                        "error_category": record.error_category,
                        "target_id": record.pk,
                    },
                )
            record.duration_ms = snapshot.duration_ms + max(
                0, int((time.monotonic() - write_started) * 1000)
            )
            record.save(
                update_fields=(
                    "status",
                    "checksum",
                    "size",
                    "duration_ms",
                    "error",
                    "error_code",
                    "error_category",
                    "generated_at",
                )
            )
        return succeeded

    def _copy_assets(self, staging):
        static_destination = staging / settings.STATIC_URL.strip("/")
        static_destination.mkdir(parents=True, exist_ok=True)

        # Production uses ManifestStaticFilesStorage, so rendered templates may
        # reference hashed names that are only present in STATIC_ROOT after
        # collectstatic. Copy that collected tree first; finder output is kept
        # as a fallback for development and uncollected assets.
        static_root = Path(settings.STATIC_ROOT)
        if static_root.is_dir():
            # ``web`` and ``worker`` share STATIC_ROOT.  A container restart
            # may run collectstatic while a publish job is copying the tree;
            # copytree then observes a file between unlink and rewrite and
            # raises a raw ``shutil.Error``.  A short retry window preserves
            # the atomic release contract without masking genuinely missing
            # assets (the later reference validation still reports those).
            copy_error = None
            for attempt in range(5):
                try:
                    shutil.copytree(
                        static_root,
                        static_destination,
                        dirs_exist_ok=True,
                    )
                    copy_error = None
                    break
                except (FileNotFoundError, shutil.Error) as exc:
                    copy_error = exc
                    if attempt < 4:
                        time.sleep(0.1 * (attempt + 1))
            if copy_error is not None:
                raise PublishError(
                    "Collected static assets changed during copy; retry the publish"
                ) from copy_error

        for finder in finders.get_finders():
            for relative, storage in finder.list([]):
                destination = static_destination / safe_relative_path(relative)
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with (
                    storage.open(relative, "rb") as source,
                    destination.open("wb") as output,
                ):
                    shutil.copyfileobj(source, output)
        media_root = Path(settings.MEDIA_ROOT)
        if media_root.is_dir():
            shutil.copytree(
                media_root, staging / settings.MEDIA_URL.strip("/"), dirs_exist_ok=True
            )

    def _validate_assets(self, staging):
        missing = []
        prefixes = (settings.STATIC_URL, settings.MEDIA_URL)
        for html_file in staging.rglob("*.html"):
            parser = AssetReferenceParser()
            parser.feed(html_file.read_text(encoding="utf-8"))
            for reference in parser.references:
                path = urlsplit(reference).path
                if path.startswith(prefixes):
                    relative = safe_relative_path(path)
                    if not (staging / relative).is_file():
                        missing.append(f"{html_file.relative_to(staging)} -> {path}")
        if missing:
            raise PublishError(
                "Missing local assets: " + "; ".join(sorted(missing)[:20])
            )

    def _asset_references(self, staging):
        media_prefix = urlsplit(settings.MEDIA_URL).path.rstrip("/")
        references = {}
        for html_file in staging.rglob("*.html"):
            parser = AssetReferenceParser()
            parser.feed(html_file.read_text(encoding="utf-8"))
            for reference in parser.references:
                path = urlsplit(reference).path
                if media_prefix and not path.startswith(f"{media_prefix}/"):
                    continue
                relative = safe_relative_path(path).as_posix()
                references.setdefault(relative, set()).add(
                    html_file.relative_to(staging).as_posix()
                )
        return [
            {"path": path, "pages": sorted(pages)}
            for path, pages in sorted(references.items())
        ]

    def _manifest_target_entry(self, record):
        generated_at = record.generated_at or timezone.now()
        return {
            "target_type": record.target_type or "page",
            "target_id": record.target_id or record.source,
            "canonical_path": record.canonical_path,
            "output_path": record.path,
            "content_hash": f"sha256:{record.checksum}" if record.checksum else None,
            "status": (
                "generated"
                if record.status == StaticPublishTarget.Status.SUCCEEDED
                else "failed"
            ),
            "action": record.action,
            "dependencies": record.dependencies
            or {
                "journal_ids": [],
                "category_ids": [],
                "article_ids": [],
                "placement_ids": [],
            },
            "generated_at": generated_at.isoformat(),
            "error_code": record.error_code or None,
            **(
                {"http_status": record.http_status, "redirect_to": record.redirect_to}
                if record.action == StaticPublishTarget.Action.REDIRECT
                else {}
            ),
        }

    def _manifest(self, job, staging, *, input_snapshot_at):
        files = []
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            if path.relative_to(staging).as_posix() == "manifest.json":
                continue
            files.append(
                {
                    "path": path.relative_to(staging).as_posix(),
                    "sha256": checksum(path),
                    "size": path.stat().st_size,
                }
            )
        active = StaticManifest.objects.filter(is_active=True).first()
        previous_targets = (
            list((active.metadata or {}).get("targets", [])) if active else []
        )
        current_targets = [
            self._manifest_target_entry(record)
            for record in job.targets.order_by("path", "pk")
        ]
        if job.scope == StaticPublishJob.Scope.FULL:
            target_entries = list(current_targets)
            current_paths = {item["output_path"] for item in current_targets}
            generated_at = timezone.now().isoformat()
            for prior in previous_targets:
                if (
                    prior.get("target_type") in {"category_page", "category_redirect"}
                    and prior.get("output_path") not in current_paths
                    and prior.get("action") != StaticPublishTarget.Action.DELETE
                ):
                    target_entries.append(
                        {
                            **prior,
                            "content_hash": None,
                            "status": "generated",
                            "action": "delete",
                            "generated_at": generated_at,
                            "error_code": None,
                        }
                    )
        else:
            merged = {item.get("output_path"): item for item in previous_targets}
            for item in current_targets:
                merged[item["output_path"]] = item
            target_entries = list(merged.values())
        target_entries.sort(
            key=lambda item: (item.get("output_path", ""), item.get("target_id", ""))
        )
        created_at = datetime.now(UTC).isoformat()
        summary = {
            "files": len(files),
            "bytes": sum(item["size"] for item in files),
            "pages": sum(
                1 for item in current_targets if item["status"] == "generated"
            ),
            "generated": sum(
                1
                for item in target_entries
                if item.get("action") == "upsert" and item.get("status") == "generated"
            ),
            "redirected": sum(
                1
                for item in target_entries
                if item.get("action") == "redirect"
                and item.get("status") == "generated"
            ),
            "deleted": sum(
                1
                for item in target_entries
                if item.get("action") == "delete" and item.get("status") == "generated"
            ),
            "failed": sum(
                1 for item in target_entries if item.get("status") == "failed"
            ),
        }
        content_readiness = (job.summary or {}).get("content_readiness")
        if content_readiness is not None:
            summary["content_readiness"] = content_readiness
        return {
            "schema_version": 2,
            "version": job.version,
            "previous_version": active.version if active else None,
            "created_at": created_at,
            "activated_at": created_at,
            "input_snapshot_at": input_snapshot_at.isoformat(),
            "built_by": job.triggered_by_id,
            "overall_status": "active",
            "rollback_version": active.version if active else None,
            "targets": target_entries,
            "files": files,
            "asset_references": self._asset_references(staging),
            "summary": summary,
        }

    @staticmethod
    def _write_nginx_release_metadata(staging, *, version, targets):
        marker = staging / NGINX_DIRECT_MARKER
        redirects_root = staging / NGINX_REDIRECT_ROOT
        if marker.exists():
            marker.unlink()
        if redirects_root.exists():
            shutil.rmtree(redirects_root)

        marker.write_text(
            json.dumps({"schema_version": 1, "version": version}, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        for target in targets:
            if not (
                target.get("action") == StaticPublishTarget.Action.REDIRECT
                and target.get("status") == "generated"
            ):
                continue
            relative_path = safe_relative_path(target["output_path"])
            destination = redirects_root / f"{relative_path.as_posix()}.redirect"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                f"{target.get('redirect_to') or ''}\n",
                encoding="utf-8",
            )

    def _load_release_manifest(self, release):
        manifest_path = release / "manifest.json"
        if not manifest_path.is_file():
            raise PublishError(
                "Release integrity check failed: manifest.json is missing"
            )
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PublishError(
                f"Release integrity check failed: invalid manifest.json ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise PublishError(
                "Release integrity check failed: manifest.json must contain an object"
            )
        return data

    def _validate_release_integrity_data(self, release, data, *, disk_data=None):
        disk_data = disk_data or self._load_release_manifest(release)
        if disk_data != data:
            raise PublishError(
                "Release integrity check failed: on-disk manifest differs from the "
                "expected immutable manifest"
            )

        entries = list(data.get("files") or [])
        paths = [str(item.get("path") or "") for item in entries]
        if len(paths) != len(set(paths)):
            raise PublishError(
                "Release integrity check failed: manifest contains duplicate paths"
            )
        if "manifest.json" in paths:
            raise PublishError(
                "Release integrity check failed: manifest.json must not hash itself"
            )
        release_root = release.resolve()
        expected_paths = set(paths)
        actual_paths = {
            item.relative_to(release).as_posix()
            for item in release.rglob("*")
            if item.is_file()
            and item.relative_to(release).as_posix() != "manifest.json"
        }
        if actual_paths != expected_paths:
            missing = sorted(expected_paths - actual_paths)
            extra = sorted(actual_paths - expected_paths)
            raise PublishError(
                "Release integrity check failed: file inventory mismatch "
                f"(missing={missing[:10]}, extra={extra[:10]})"
            )
        for item in entries:
            relative = safe_relative_path(item["path"])
            candidate = release / relative
            resolved = candidate.resolve()
            if release_root not in resolved.parents:
                raise PublishError(
                    f"Release integrity check failed: unsafe file {item['path']!r}"
                )
            if candidate.is_symlink() or not candidate.is_file():
                raise PublishError(
                    f"Release integrity check failed: file {item['path']!r} is missing or linked"
                )
            actual_size = candidate.stat().st_size
            actual_checksum = checksum(candidate)
            if actual_size != item.get("size") or actual_checksum != item.get("sha256"):
                raise PublishError(
                    "Release integrity check failed for "
                    f"{item['path']!r}: size or sha256 mismatch"
                )
        return disk_data

    def _validate_release_integrity(self, release, manifest):
        disk_data = self._load_release_manifest(release)
        self._validate_release_integrity_data(
            release,
            disk_data,
            disk_data=disk_data,
        )
        metadata = manifest.metadata or {}
        if disk_data.get("version") != manifest.version:
            raise PublishError(
                "Release integrity check failed: database and disk versions differ"
            )
        if (disk_data.get("previous_version") or "") != manifest.previous_version:
            raise PublishError(
                "Release integrity check failed: previous_version differs from the database"
            )
        if disk_data.get("files") != manifest.files:
            raise PublishError(
                "Release integrity check failed: file manifest differs from the database"
            )
        for key in (
            "schema_version",
            "created_at",
            "activated_at",
            "input_snapshot_at",
            "built_by",
            "overall_status",
            "rollback_version",
            "summary",
            "targets",
            "asset_references",
        ):
            if disk_data.get(key) != metadata.get(key):
                raise PublishError(
                    "Release integrity check failed: database metadata differs for "
                    f"{key}"
                )
        return disk_data

    def _record_candidate_manifest(self, job, data):
        with transaction.atomic():
            existing = StaticManifest.objects.filter(version=data["version"]).first()
            if existing is not None:
                return existing
            return StaticManifest.objects.create(
                version=data["version"],
                job=job,
                previous_version=data["previous_version"] or "",
                files=data["files"],
                metadata={
                    "schema_version": data["schema_version"],
                    "created_at": data["created_at"],
                    "activated_at": data["activated_at"],
                    "input_snapshot_at": data["input_snapshot_at"],
                    "built_by": data["built_by"],
                    "overall_status": data["overall_status"],
                    "rollback_version": data["rollback_version"],
                    "summary": data["summary"],
                    "targets": data["targets"],
                    "asset_references": data["asset_references"],
                },
                is_active=False,
            )

    @staticmethod
    def _activate_manifest_record(manifest):
        StaticManifest.objects.filter(is_active=True).exclude(pk=manifest.pk).update(
            is_active=False
        )
        if not manifest.is_active:
            manifest.is_active = True
            manifest.save(update_fields=("is_active",))

    def _record_manifest(self, job, data):
        manifest = self._record_candidate_manifest(job, data)
        self._activate_manifest_record(manifest)
        return manifest

    def _activate(self, release):
        candidate = self.root / ".current-next"
        previous = self.root / ".current-previous"
        if candidate.exists():
            shutil.rmtree(candidate)
        shutil.copytree(release, candidate)
        if previous.exists():
            shutil.rmtree(previous)
        had_previous = self.current.exists()
        if had_previous:
            self._move_directory(self.current, previous)
        try:
            self._move_directory(candidate, self.current)
        except Exception:
            if previous.exists() and not self.current.exists():
                self._move_directory(previous, self.current)
            raise
        return {"previous": previous, "had_previous": had_previous}

    def _restore_activation(self, activation):
        previous = activation["previous"]
        failed = self.root / ".current-failed"
        if failed.exists():
            shutil.rmtree(failed)
        if self.current.exists():
            self._move_directory(self.current, failed)
        try:
            if activation["had_previous"] and previous.exists():
                self._move_directory(previous, self.current)
        finally:
            if failed.exists():
                shutil.rmtree(failed)

    def _finalize_activation(self, activation):
        previous = activation["previous"]
        if previous.exists():
            shutil.rmtree(previous)

    def _move_directory(self, source, destination):
        try:
            os.replace(source, destination)
        except PermissionError:
            shutil.move(str(source), str(destination))

    def _finish(self, job, status, summary=None, error=""):
        job.status = status
        job.finished_at = timezone.now()
        job.summary = summary or {}
        job.error = error
        job.save(update_fields=("status", "finished_at", "summary", "error"))

    def _log(self, job, level, message, context=None):
        StaticBuildLog.objects.create(
            job=job, level=level, message=message, context=context or {}
        )

    def _audit(self, action, status, job, message, metadata=None):
        try:
            return record_audit_event(
                action=action,
                status=status,
                actor=job.triggered_by,
                target=job,
                message=message,
                metadata=metadata or {},
            )
        except Exception as exc:
            logger.exception(
                "Unable to persist static publishing audit",
                extra={
                    "job_id": job.pk,
                    "audit_action": str(action),
                    "audit_status": str(status),
                },
            )
            raise AuditWriteError(
                f"Audit log write failed for static job #{job.pk}: {exc}"
            ) from exc

    def _version(self, job_id):
        return f"{timezone.now():%Y%m%dT%H%M%S%fZ}-job{job_id}"

    def _prune(self):
        keep = max(2, settings.STATIC_PUBLISH_KEEP_RELEASES)
        protected = set(
            StaticManifest.objects.order_by("-created_at").values_list(
                "version", flat=True
            )[:keep]
        )
        for release in self.releases.iterdir():
            if release.is_dir() and release.name not in protected:
                shutil.rmtree(release)


# Dynamic category publication contract (static_publish.services.categories).
import sys as _sys  # noqa: E402

from . import category_services as categories  # noqa: E402

_sys.modules[__name__ + ".categories"] = categories
