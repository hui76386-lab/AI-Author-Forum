import hashlib
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from ai_author_forum.articles.import_services import fail_stale_article_import_previews
from ai_author_forum.journals.models import ArticleImportJob, ImportJobStatus
from ai_author_forum.journals.publishing import get_import_queue_root
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event


class Command(BaseCommand):
    help = "Fail timed-out article previews and remove unreferenced stale queue files."

    def handle(self, *args, **options):
        failed_jobs = fail_stale_article_import_previews()
        queue_root = get_import_queue_root().resolve()
        timeout = int(getattr(settings, "ARTICLE_IMPORT_PREVIEW_TIMEOUT_SECONDS", 600))
        cutoff = time.time() - timeout
        active_hashes = set(
            ArticleImportJob.objects.filter(
                status__in={
                    ImportJobStatus.PENDING,
                    ImportJobStatus.VALIDATING,
                    ImportJobStatus.IMPORTING,
                }
            )
            .exclude(source_sha256="")
            .values_list("source_sha256", flat=True)
        )
        removed_files = 0
        cleanup_errors = 0
        if queue_root.is_dir():
            for candidate in queue_root.iterdir():
                if not candidate.is_file():
                    continue
                try:
                    safe_candidate = candidate.resolve()
                    safe_candidate.relative_to(queue_root)
                    if safe_candidate.stat().st_mtime > cutoff:
                        continue
                    digest = hashlib.sha256(safe_candidate.read_bytes()).hexdigest()
                    if digest in active_hashes:
                        continue
                    safe_candidate.unlink()
                    removed_files += 1
                except (OSError, ValueError):
                    cleanup_errors += 1

        record_audit_event(
            action=AuditAction.IMPORT,
            status=(
                AuditStatus.SUCCESS if cleanup_errors == 0 else AuditStatus.FAILURE
            ),
            target_type="ArticleImportQueue",
            target_id="stale-cleanup",
            target_label="文章导入队列清理",
            message="清理遗留文章导入队列文件",
            metadata={
                "timed_out_jobs": failed_jobs,
                "removed_queue_files": removed_files,
                "cleanup_errors": cleanup_errors,
                "retention_seconds": timeout,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Marked "
                f"{failed_jobs} timed-out preview job(s) as failed; "
                f"removed {removed_files} stale queue file(s)."
            )
        )
