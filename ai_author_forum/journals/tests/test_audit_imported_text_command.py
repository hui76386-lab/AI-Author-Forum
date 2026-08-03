from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from ai_author_forum.journals.models import (
    Journal,
    JournalImportJob,
    JournalImportRow,
    StaticArticle,
)


class AuditImportedTextCommandTests(TestCase):
    def setUp(self):
        self.journal = Journal.objects.create(
            name="Clean Journal",
            slug="clean-journal",
            az_group="C",
            notes="Legacy???journal",
            hero_quick_links=[
                {
                    "type": "link",
                    "value": {
                        "label": "Broken�link",
                        "url": "/clean-link/",
                        "open_in_new_tab": False,
                    },
                }
            ],
        )
        self.article = StaticArticle.objects.create(
            journal=self.journal,
            title="Clean article",
            slug="clean-article",
            ai_co_authors="AI???assistant",
        )
        job = JournalImportJob.objects.create(package_name="legacy-import")
        self.import_row = JournalImportRow.objects.create(
            job=job,
            row_no=7,
            raw_data={
                "metadata": {
                    "labels": ["normal", "Nested???value"],
                    "_private": "Ignored???value",
                }
            },
        )

    def test_command_uses_real_fields_and_scans_nested_json_values(self):
        with TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "suspicious.csv"
            stdout = StringIO()

            call_command(
                "audit_imported_text",
                output=str(output_path),
                stdout=stdout,
            )

            with output_path.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.DictReader(source))

        findings = {
            (row["model"], row["field"], row["rule"], row["import_row_no"])
            for row in rows
        }
        self.assertIn(
            (
                "journals.Journal",
                "notes",
                "repeated_question_marks",
                "",
            ),
            findings,
        )
        self.assertIn(
            (
                "journals.Journal",
                "hero_quick_links[0].value.label",
                "unicode_replacement_character",
                "",
            ),
            findings,
        )
        self.assertIn(
            (
                "journals.StaticArticle",
                "ai_co_authors",
                "repeated_question_marks",
                "",
            ),
            findings,
        )
        self.assertIn(
            (
                "journals.JournalImportRow",
                "raw_data.metadata.labels[1]",
                "repeated_question_marks",
                "7",
            ),
            findings,
        )
        self.assertFalse(any("_private" in row["field"] for row in rows))
        self.assertIn("只读审计完成", stdout.getvalue())

    def test_command_does_not_modify_scanned_records(self):
        original_notes = self.journal.notes
        original_raw_data = self.import_row.raw_data

        with TemporaryDirectory() as temp_dir:
            call_command(
                "audit_imported_text",
                output=str(Path(temp_dir) / "suspicious.csv"),
                stdout=StringIO(),
            )

        self.journal.refresh_from_db()
        self.import_row.refresh_from_db()
        self.assertEqual(self.journal.notes, original_notes)
        self.assertEqual(self.import_row.raw_data, original_raw_data)
