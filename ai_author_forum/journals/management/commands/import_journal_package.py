import argparse

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.services import get_approved_articles
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ImportJobStatus,
    ImportRowStatus,
    JournalImportJob,
)
from ai_author_forum.journals.services import import_package
from ai_author_forum.static_publish.models import StaticPublishJob
from ai_author_forum.static_publish.services import StaticPublisher


class Command(BaseCommand):
    help = (
        "Import journals and articles from a zip package and optionally publish them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--package", required=True, help="Path to the import zip package."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the package without writing journal or article records.",
        )
        parser.add_argument(
            "--publish-static-site",
            action="store_true",
            help="Create and run a centralized full-site static publish job after import.",
        )
        parser.add_argument(
            "--csv-encoding",
            choices=("auto", "gb18030"),
            default="auto",
            help="CSV encoding policy; Excel files ignore this option.",
        )
        parser.add_argument(
            "--allow-suspicious-text", action="store_true", help=argparse.SUPPRESS
        )
        parser.add_argument("--override-reason", default="", help=argparse.SUPPRESS)
        parser.add_argument(
            "--operator-id",
            type=int,
            help="User ID recorded as the import and static publish operator.",
        )
        parser.add_argument(
            "--preview-journal-job-id", type=int, help=argparse.SUPPRESS
        )
        parser.add_argument(
            "--preview-article-job-id", type=int, help=argparse.SUPPRESS
        )
        parser.add_argument("--static-output-dir", help=argparse.SUPPRESS)
        parser.add_argument(
            "--clear-static-output",
            action="store_true",
            help=argparse.SUPPRESS,
        )

    def handle(self, *args, **options):
        if options.get("static_output_dir") or options.get("clear_static_output"):
            raise CommandError(
                "--static-output-dir and --clear-static-output are no longer supported. "
                "Use --publish-static-site; releases are managed under STATIC_PUBLISH_ROOT."
            )

        package_path = options["package"]
        dry_run = options["dry_run"]
        operator = self._get_operator(options.get("operator_id"))
        if options["allow_suspicious_text"]:
            if operator is None or not operator.is_superuser:
                raise CommandError(
                    "Only a superuser may import suspicious text unchanged."
                )
            if len(options["override_reason"].strip()) < 8:
                raise CommandError(
                    "A reason of at least 8 characters is required for suspicious text override."
                )
        preview_jobs = self._get_preview_jobs(options)
        self._update_preview_jobs(preview_jobs, ImportJobStatus.IMPORTING)

        try:
            with open(package_path, "rb") as source_file:
                result = import_package(
                    source_file,
                    operator=operator,
                    dry_run=dry_run,
                    allow_suspicious_text=options["allow_suspicious_text"],
                    csv_encoding=options["csv_encoding"],
                )
        except Exception as exc:
            self._update_preview_jobs(
                preview_jobs, ImportJobStatus.FAILED, error=str(exc)
            )
            if isinstance(exc, FileNotFoundError):
                raise CommandError(str(exc)) from exc
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported package {result.package_name}: "
                f"journals={result.journal_created}/{result.journal_updated}, "
                f"articles={result.article_created}/{result.article_updated}"
            )
        )

        if not options["publish_static_site"] or dry_run:
            self._update_preview_jobs(
                preview_jobs, ImportJobStatus.COMPLETED, result=result
            )
            if dry_run and options["publish_static_site"]:
                self.stdout.write(
                    self.style.NOTICE(
                        "Dry run completed; no static publish job was created."
                    )
                )
            return

        try:
            self._validate_static_publish_preconditions(result)
        except CommandError as exc:
            self._update_preview_jobs(
                preview_jobs,
                ImportJobStatus.FAILED,
                result=result,
                error=str(exc),
            )
            raise

        job = StaticPublishJob.objects.create(
            scope=StaticPublishJob.Scope.FULL,
            triggered_by=operator,
        )
        try:
            StaticPublisher().build(job)
        except Exception as exc:
            job.refresh_from_db()
            self._link_publish_job(result, job)
            self._update_preview_jobs(
                preview_jobs,
                ImportJobStatus.FAILED,
                result=result,
                publish_job=job,
                error=str(exc),
            )
            raise CommandError(f"Static publish job #{job.pk} failed: {exc}") from exc

        job.refresh_from_db()
        self._link_publish_job(result, job)
        self._update_preview_jobs(
            preview_jobs,
            ImportJobStatus.COMPLETED,
            result=result,
            publish_job=job,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Static publish job #{job.pk} completed: "
                f"status={job.status}, version={job.version}"
            )
        )

    def _validate_static_publish_preconditions(self, result):
        successful_source_ids = []
        if result.article_job is not None:
            successful_source_ids = list(
                result.article_job.rows.filter(
                    status=ImportRowStatus.SUCCESS, article_id__isnull=False
                ).values_list("article_id", flat=True)
            )
        canonical_source_ids = set(
            ArticlePage.objects.filter(
                source_static_article_id__in=successful_source_ids
            ).values_list("source_static_article_id", flat=True)
        )
        missing_source_ids = sorted(set(successful_source_ids) - canonical_source_ids)
        if missing_source_ids:
            raise CommandError(
                "Static publishing refused because imported rows are missing canonical "
                f"ArticlePage drafts: {missing_source_ids}. Repair the conversion first."
            )
        if not get_approved_articles().exists():
            raise CommandError(
                "Static publishing refused: no approved formal ArticlePage has an "
                "effective placements.ArticlePlacement. Imported articles remain drafts; "
                "review and place them before publishing."
            )

    def _get_operator(self, operator_id):
        if not operator_id:
            return None
        user_model = get_user_model()
        try:
            return user_model.objects.get(pk=operator_id)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"Operator user {operator_id} does not exist") from exc

    def _get_preview_jobs(self, options):
        jobs = []
        lookups = (
            (JournalImportJob, options.get("preview_journal_job_id")),
            (ArticleImportJob, options.get("preview_article_job_id")),
        )
        for model, job_id in lookups:
            if not job_id:
                jobs.append(None)
                continue
            try:
                jobs.append(model.objects.get(pk=job_id))
            except model.DoesNotExist as exc:
                raise CommandError(
                    f"Preview import job {job_id} does not exist"
                ) from exc
        return tuple(jobs)

    def _update_preview_jobs(
        self,
        preview_jobs,
        status,
        *,
        result=None,
        publish_job=None,
        error="",
    ):
        actual_jobs = (
            result.journal_job if result else None,
            result.article_job if result else None,
        )
        for preview_job, actual_job in zip(preview_jobs, actual_jobs, strict=True):
            if preview_job is None:
                continue
            summary = dict(preview_job.summary or {})
            if actual_job is not None:
                summary["confirmed_import_job"] = {
                    "id": actual_job.pk,
                    "status": actual_job.status,
                    "created": actual_job.summary.get("created", 0),
                    "updated": actual_job.summary.get("updated", 0),
                    "skipped": actual_job.summary.get("skipped", 0),
                    "failed": actual_job.summary.get("failed", 0),
                }
            if publish_job is not None:
                summary["static_publish_job"] = {
                    "id": publish_job.pk,
                    "status": publish_job.status,
                    "version": publish_job.version,
                }
            if error:
                summary["process_error"] = error
            preview_job.summary = summary
            preview_job.status = status
            if status == ImportJobStatus.IMPORTING:
                preview_job.started_at = timezone.now()
                preview_job.finished_at = None
            elif status in {ImportJobStatus.COMPLETED, ImportJobStatus.FAILED}:
                preview_job.finished_at = timezone.now()
            preview_job.save(
                update_fields=(
                    "summary",
                    "status",
                    "started_at",
                    "finished_at",
                    "updated_at",
                )
            )

    def _link_publish_job(self, result, job):
        publish_summary = {
            "id": job.pk,
            "status": job.status,
            "version": job.version,
        }
        for import_job in (result.journal_job, result.article_job):
            if import_job is None:
                continue
            summary = dict(import_job.summary or {})
            summary["static_publish_job"] = publish_summary
            import_job.summary = summary
            import_job.save(update_fields=("summary",))
