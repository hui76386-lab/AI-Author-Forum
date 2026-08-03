from __future__ import annotations

import csv
import io
import json
import tempfile
import tracemalloc
import uuid
from pathlib import Path
from time import perf_counter

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test.utils import override_settings

from ai_author_forum.articles.document_benchmarks import (
    DOCUMENT_SCENARIOS,
    DocumentCapacityRunner,
)
from ai_author_forum.articles.import_services import (
    MAX_ROWS,
    ArticleImportContext,
    ArticleImportValidationError,
    confirm_article_import,
    execute_confirmed_article_import,
    preview_article_import,
)
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    ArticleImportScope,
    Journal,
    JournalStatus,
    StaticArticle,
)

FIELDS = (
    "journal_slug",
    "title",
    "slug",
    "article_type",
    "authors",
    "body_html",
)


class QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


def _read_cgroup_memory_value(filename: str) -> int | None:
    """Return a cgroup memory counter without exposing host paths or errors."""

    try:
        value = (Path("/sys/fs/cgroup") / filename).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value or value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _container_memory_metrics() -> dict[str, int | float | str | None]:
    """Capture cgroup-v2 memory values when the benchmark runs in a container.

    Per-scenario ``tracemalloc`` values remain useful for Python allocations.  The
    cgroup values record the container-wide peak required by the preproduction
    acceptance criterion, including native converter allocations.
    """

    limit = _read_cgroup_memory_value("memory.max")
    peak = _read_cgroup_memory_value("memory.peak")
    metrics: dict[str, int | float | str | None] = {
        "measurement": "cgroup-v2" if peak is not None else "unavailable",
        "limit_bytes": limit,
        "peak_bytes": peak,
        "peak_percent_of_limit": None,
    }
    if limit and peak is not None:
        metrics["peak_percent_of_limit"] = round(peak / limit * 100, 4)
    return metrics


class Command(BaseCommand):
    help = (
        "Run an isolated article-import capacity benchmark and roll back database data."
    )

    def add_arguments(self, parser):
        parser.add_argument("--rows", type=int, default=100)
        parser.add_argument("--journals", type=int, default=1)
        parser.add_argument(
            "--scope",
            choices=(ArticleImportScope.GLOBAL, ArticleImportScope.JOURNAL),
            default=ArticleImportScope.GLOBAL,
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Also confirm and write valid rows as draft ArticlePage records.",
        )
        parser.add_argument(
            "--invalid-every",
            type=int,
            default=0,
            help="Make every Nth row invalid to measure large error reports.",
        )
        parser.add_argument(
            "--output",
            type=Path,
            help="Optional JSON result path. Parent directories are created.",
        )
        parser.add_argument(
            "--document-suite",
            action="store_true",
            help="Run the DOCX/Markdown capacity scenarios required by the taskbook.",
        )
        parser.add_argument(
            "--scenario",
            choices=DOCUMENT_SCENARIOS,
            help="Run one document-suite scenario instead of the complete suite.",
        )

    def handle(self, *args, **options):
        if options.get("scenario") and not options.get("document_suite"):
            raise CommandError("--scenario requires --document-suite.")
        if options.get("document_suite"):
            return self._handle_document_suite(options)

        rows = options["rows"]
        journal_count = options["journals"]
        invalid_every = options["invalid_every"]
        scope = options["scope"]
        should_execute = options["execute"]

        if rows < 1 or rows > MAX_ROWS:
            raise CommandError(f"--rows must be between 1 and {MAX_ROWS}.")
        if journal_count < 1 or journal_count > 200:
            raise CommandError("--journals must be between 1 and 200.")
        if scope == ArticleImportScope.JOURNAL and journal_count != 1:
            raise CommandError("Journal scope requires --journals=1.")
        if invalid_every < 0:
            raise CommandError("--invalid-every cannot be negative.")

        run_id = uuid.uuid4().hex[:10]
        result = {
            "run_id": run_id,
            "rows": rows,
            "journals": journal_count,
            "scope": scope,
            "execute": should_execute,
            "invalid_every": invalid_every,
            "database_vendor": connection.vendor,
            "database_name": str(connection.settings_dict.get("NAME", "")),
        }

        with tempfile.TemporaryDirectory(prefix="article-import-capacity-") as tempdir:
            temp_root = Path(tempdir)
            media_root = temp_root / "media"
            queue_root = temp_root / "queue"
            with override_settings(
                MEDIA_ROOT=str(media_root),
                AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT=str(queue_root),
            ):
                with transaction.atomic():
                    self._run_benchmark(result, run_id=run_id)
                    transaction.set_rollback(True)

        output = options.get("output")
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if output:
            output = output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Benchmark written to {output}"))
        self.stdout.write(payload)

    def _handle_document_suite(self, options):
        run_id = uuid.uuid4().hex[:10]
        selected = (
            [options["scenario"]]
            if options.get("scenario")
            else list(DOCUMENT_SCENARIOS)
        )
        result = {
            "run_id": run_id,
            "suite": "article-document-import",
            "database_vendor": connection.vendor,
            "database_name": str(connection.settings_dict.get("NAME", "")),
            "selected_scenarios": selected,
        }
        with tempfile.TemporaryDirectory(
            prefix="article-document-capacity-"
        ) as tempdir:
            temp_root = Path(tempdir)
            with override_settings(
                MEDIA_ROOT=str(temp_root / "media"),
                AI_AUTHOR_FORUM_IMPORT_QUEUE_ROOT=str(temp_root / "queue"),
            ):
                with transaction.atomic():
                    user = get_user_model().objects.create_superuser(
                        username=f"article-document-capacity-{run_id}",
                        email=f"article-document-capacity-{run_id}@example.invalid",
                        password=None,
                    )
                    journal = Journal.objects.create(
                        name=f"Article document capacity {run_id}",
                        slug=f"article-document-capacity-{run_id}",
                        az_group="A",
                        status=JournalStatus.ACTIVE,
                    )
                    runner = DocumentCapacityRunner(
                        user=user,
                        journal=journal,
                        temp_root=temp_root / "scenarios",
                    )
                    result["scenarios"] = runner.run(selected)
                    result["all_passed"] = all(
                        scenario["passed"] for scenario in result["scenarios"]
                    )
                    transaction.set_rollback(True)
        result["runtime_memory"] = _container_memory_metrics()
        self._write_result(result, options.get("output"))
        if not result["all_passed"]:
            raise CommandError("One or more document benchmark scenarios failed.")

    def _write_result(self, result, output):
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if output:
            output = output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Benchmark written to {output}"))
        self.stdout.write(payload)

    def _run_benchmark(self, result, *, run_id):
        rows = result["rows"]
        journal_count = result["journals"]
        scope = result["scope"]
        invalid_every = result["invalid_every"]

        user = get_user_model().objects.create_superuser(
            username=f"article-capacity-{run_id}",
            email=f"article-capacity-{run_id}@example.invalid",
            password=None,
        )
        journals = [
            Journal.objects.create(
                name=f"Article capacity journal {run_id}-{index + 1}",
                slug=f"article-capacity-{run_id}-{index + 1}",
                az_group="A",
                status=JournalStatus.ACTIVE,
            )
            for index in range(journal_count)
        ]
        source = self._build_csv(
            rows=rows,
            journals=journals,
            scope=scope,
            invalid_every=invalid_every,
            run_id=run_id,
        )
        result["source_bytes"] = len(source)

        parse_started = perf_counter()
        parsed_rows = list(
            csv.DictReader(io.StringIO(source.decode("utf-8-sig"), newline=""))
        )
        result["parse_seconds"] = round(perf_counter() - parse_started, 6)
        result["parsed_rows"] = len(parsed_rows)

        upload = SimpleUploadedFile(
            f"article-capacity-{run_id}.csv", source, content_type="text/csv"
        )
        context = ArticleImportContext(
            scope=scope,
            target_journal_id=(
                journals[0].pk if scope == ArticleImportScope.JOURNAL else None
            ),
        )

        preview_counter = QueryCounter()
        tracemalloc.start()
        preview_started = perf_counter()
        with connection.execute_wrapper(preview_counter):
            job = preview_article_import(upload, context=context, operator=user)
        preview_seconds = perf_counter() - preview_started
        _, preview_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        job.refresh_from_db()
        result["preview"] = {
            "seconds": round(preview_seconds, 6),
            "peak_memory_bytes": preview_peak,
            "database_queries": preview_counter.count,
            "status": job.status,
            "total_rows": job.total_rows,
            "success_rows": job.success_rows,
            "failed_rows": job.failed_rows,
            "error_report_bytes": (
                job.error_report.size
                if job.error_report and job.error_report.name
                else 0
            ),
        }

        if not result["execute"]:
            return

        confirm_article_import(job, operator=user)
        write_counter = QueryCounter()
        tracemalloc.start()
        write_started = perf_counter()
        with connection.execute_wrapper(write_counter):
            execute_confirmed_article_import(job, operator=user)
        write_seconds = perf_counter() - write_started
        _, write_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        job.refresh_from_db()
        article_count = StaticArticle.objects.filter(
            journal__in=journals,
            slug__startswith=f"capacity-{run_id}-",
        ).count()
        page_count = ArticlePage.objects.filter(
            source_static_article__journal__in=journals,
            static_slug__startswith=f"capacity-{run_id}-",
        ).count()
        before_recovery = (job.status, article_count, page_count)
        recovery_started = perf_counter()
        recovery_result = "completed"
        try:
            execute_confirmed_article_import(job, operator=user)
        except ArticleImportValidationError as exc:
            recovery_result = getattr(exc, "code", "ARTICLE_IMPORT_STATE_INVALID")
        recovery_seconds = perf_counter() - recovery_started
        job.refresh_from_db()
        after_recovery = (
            job.status,
            StaticArticle.objects.filter(
                journal__in=journals,
                slug__startswith=f"capacity-{run_id}-",
            ).count(),
            ArticlePage.objects.filter(
                source_static_article__journal__in=journals,
                static_slug__startswith=f"capacity-{run_id}-",
            ).count(),
        )
        result["write"] = {
            "seconds": round(write_seconds, 6),
            "peak_memory_bytes": write_peak,
            "database_queries": write_counter.count,
            "status": job.status,
            "summary": job.summary,
            "static_articles": article_count,
            "article_pages": page_count,
        }
        result["recovery"] = {
            "seconds": round(recovery_seconds, 6),
            "terminal_reexecution_is_noop": before_recovery == after_recovery,
            "reexecution_result": recovery_result,
            "before": before_recovery,
            "after": after_recovery,
        }

    @staticmethod
    def _build_csv(*, rows, journals, scope, invalid_every, run_id):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for index in range(rows):
            journal = journals[index % len(journals)]
            invalid = bool(invalid_every and (index + 1) % invalid_every == 0)
            writer.writerow(
                {
                    "journal_slug": (
                        journal.slug if scope == ArticleImportScope.GLOBAL else ""
                    ),
                    "title": "" if invalid else f"Capacity article {index + 1}",
                    "slug": f"capacity-{run_id}-{index + 1}",
                    "article_type": "news",
                    "authors": "Capacity benchmark",
                    "body_html": f"<p>Capacity body {index + 1}</p>",
                }
            )
        return stream.getvalue().encode("utf-8-sig")
