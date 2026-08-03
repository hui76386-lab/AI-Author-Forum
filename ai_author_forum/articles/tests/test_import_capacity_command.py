from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import ArticleImportJob, Journal, StaticArticle


class ArticleImportCapacityCommandTests(TestCase):
    def test_preview_benchmark_records_metrics_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir, "preview.json")
            call_command(
                "benchmark_article_import",
                rows=5,
                journals=2,
                scope="global",
                invalid_every=3,
                output=output,
                verbosity=0,
            )
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result["parsed_rows"], 5)
        self.assertEqual(result["preview"]["total_rows"], 5)
        self.assertEqual(result["preview"]["success_rows"], 4)
        self.assertEqual(result["preview"]["failed_rows"], 1)
        self.assertGreater(result["preview"]["error_report_bytes"], 0)
        self.assertGreater(result["preview"]["database_queries"], 0)
        self.assertEqual(ArticleImportJob.objects.count(), 0)
        self.assertFalse(
            Journal.objects.filter(slug__startswith="article-capacity-").exists()
        )

    def test_execute_benchmark_records_draft_write_and_terminal_recovery(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir, "execute.json")
            call_command(
                "benchmark_article_import",
                rows=2,
                journals=1,
                scope="journal",
                execute=True,
                output=output,
                verbosity=0,
            )
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result["write"]["status"], "completed")
        self.assertEqual(result["write"]["static_articles"], 2)
        self.assertEqual(result["write"]["article_pages"], 2)
        self.assertTrue(result["recovery"]["terminal_reexecution_is_noop"])
        self.assertEqual(ArticleImportJob.objects.count(), 0)
        self.assertEqual(StaticArticle.objects.count(), 0)
        self.assertEqual(ArticlePage.objects.count(), 0)

    def _run_document_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir, f"{scenario}.json")
            call_command(
                "benchmark_article_import",
                document_suite=True,
                scenario=scenario,
                output=output,
                stdout=io.StringIO(),
                verbosity=0,
            )
            return json.loads(output.read_text(encoding="utf-8"))

    def test_document_benchmark_rejects_201_documents_and_records_schema(self):
        counts_before = {
            "jobs": ArticleImportJob.objects.count(),
            "journals": Journal.objects.count(),
            "static_articles": StaticArticle.objects.count(),
            "article_pages": ArticlePage.objects.count(),
        }

        result = self._run_document_scenario("201-documents")

        self.assertEqual(result["suite"], "article-document-import")
        self.assertEqual(result["selected_scenarios"], ["201-documents"])
        self.assertTrue(result["all_passed"])
        self.assertTrue(
            {
                "measurement",
                "limit_bytes",
                "peak_bytes",
                "peak_percent_of_limit",
            }.issubset(result["runtime_memory"])
        )
        scenario = result["scenarios"][0]
        self.assertTrue(scenario["passed"])
        self.assertEqual(scenario["observed"], "rejected")
        self.assertEqual(
            scenario["preview"]["error_code"], "ARTICLE_DOCX_LIMIT_EXCEEDED"
        )
        self.assertTrue(
            {
                "upload_save_seconds",
                "format_detection_seconds",
                "extraction_preflight_seconds",
                "document_conversion_seconds",
                "html_validation_seconds",
                "preview_total_seconds",
                "write_total_seconds",
                "peak_memory_bytes",
                "sql_queries",
                "temporary_disk_peak_bytes",
                "generated_image_count",
                "error_report_bytes",
            }.issubset(scenario["metrics"])
        )
        self.assertEqual(ArticleImportJob.objects.count(), counts_before["jobs"])
        self.assertEqual(Journal.objects.count(), counts_before["journals"])
        self.assertEqual(
            StaticArticle.objects.count(), counts_before["static_articles"]
        )
        self.assertEqual(ArticlePage.objects.count(), counts_before["article_pages"])

    def test_document_benchmark_terminal_reentry_is_rejected_without_mutation(self):
        counts_before = {
            "jobs": ArticleImportJob.objects.count(),
            "journals": Journal.objects.count(),
            "static_articles": StaticArticle.objects.count(),
            "article_pages": ArticlePage.objects.count(),
        }

        result = self._run_document_scenario("terminal-reentry")

        self.assertTrue(result["all_passed"])
        scenario = result["scenarios"][0]
        self.assertTrue(scenario["passed"])
        self.assertEqual(scenario["preview"]["status"], "ready")
        self.assertEqual(scenario["preview"]["success_rows"], 1)
        self.assertEqual(scenario["observed"], "completed")
        self.assertEqual(scenario["write"]["status"], "completed")
        self.assertEqual(scenario["write"]["summary"]["created_rows"], 1)
        self.assertEqual(
            scenario["reentry"]["error_code"], "ARTICLE_IMPORT_STATE_INVALID"
        )
        self.assertTrue(scenario["reentry"]["terminal_state_unchanged"])
        self.assertEqual(ArticleImportJob.objects.count(), counts_before["jobs"])
        self.assertEqual(Journal.objects.count(), counts_before["journals"])
        self.assertEqual(
            StaticArticle.objects.count(), counts_before["static_articles"]
        )
        self.assertEqual(ArticlePage.objects.count(), counts_before["article_pages"])

    def test_document_benchmark_enforces_converted_html_boundary(self):
        result = self._run_document_scenario("10mb-converted-html")

        self.assertTrue(result["all_passed"])
        scenario = result["scenarios"][0]
        self.assertTrue(scenario["passed"])
        self.assertEqual(scenario["observed"], "boundary-accepted")
        self.assertEqual(scenario["boundary_bytes"], 10 * 1024 * 1024)
        self.assertEqual(
            scenario["over_limit_error_code"], "ARTICLE_DOCUMENT_HTML_TOO_LARGE"
        )
