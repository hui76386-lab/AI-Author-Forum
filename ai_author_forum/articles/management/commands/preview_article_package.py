from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from ai_author_forum.articles.import_services import (
    execute_article_import_preview,
    fail_pending_article_import_job,
)
from ai_author_forum.journals.models import ArticleImportJob
from ai_author_forum.journals.publishing import get_import_queue_root
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event


class Command(BaseCommand):
    help = "Preview a locked article import job from the background queue."

    def add_arguments(self, parser):
        parser.add_argument("--package", required=True)
        parser.add_argument("--job-id", type=int, required=True)
        parser.add_argument("--operator-id", type=int)

    def handle(self, *args, **options):
        try:
            job = ArticleImportJob.objects.get(pk=options["job_id"])
        except ArticleImportJob.DoesNotExist as exc:
            raise CommandError("Article import preview job does not exist.") from exc

        operator = job.operator
        package = None
        package_is_safe = False
        queue_root = get_import_queue_root().resolve()
        try:
            package = Path(options["package"]).resolve()
            try:
                package.relative_to(queue_root)
            except ValueError as exc:
                raise CommandError(
                    "Package path must stay inside the import queue root."
                ) from exc
            package_is_safe = True
            if not package.is_file():
                raise CommandError("Package file does not exist.")

            if options.get("operator_id"):
                operator = (
                    get_user_model().objects.filter(pk=options["operator_id"]).first()
                )
                if operator is None:
                    raise CommandError(
                        "Article import preview operator does not exist."
                    )

            job = execute_article_import_preview(
                job, operator=operator, package_path=package
            )
        except CommandError as exc:
            fail_pending_article_import_job(
                job,
                operator=operator or job.operator,
                message=str(exc),
                code="ARTICLE_IMPORT_COMMAND_INPUT_INVALID",
            )
            raise
        except (PermissionDenied, ValidationError) as exc:
            messages = getattr(exc, "messages", None)
            message = "; ".join(messages) if messages else str(exc)
            raise CommandError(message) from exc
        finally:
            removed = False
            if package_is_safe and package is not None and package.is_file():
                package.unlink()
                removed = True
            try:
                record_audit_event(
                    action=AuditAction.IMPORT,
                    status=AuditStatus.SUCCESS,
                    actor=operator or job.operator,
                    target=job,
                    message="文章导入预检队列文件清理完成",
                    metadata={
                        "job_id": job.pk,
                        "queue_file_removed": removed,
                        "source_format": job.source_format,
                    },
                )
            except Exception:
                self.stderr.write(
                    "Article preview queue cleanup audit could not be recorded."
                )

        self.stdout.write(self.style.SUCCESS(f"Article preview job {job.pk} is ready."))
