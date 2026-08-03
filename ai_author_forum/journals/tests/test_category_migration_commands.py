import csv
import hashlib
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from wagtail.models import Page

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.journals.management.commands.plan_article_category_migration import (
    FIELDS,
    RULE_VERSION,
)
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.placements.category_services import sync_category_placements
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.models import AuditLog


class CategoryMigrationCommandTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base_dir = Path(self.tempdir.name)
        self.settings_override = override_settings(BASE_DIR=self.base_dir, DEBUG=True)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_superuser(
            "migration-admin", "migration@example.com", "test"
        )
        self.journal = Journal.objects.create(
            name="Migration Journal", slug="migration-journal", az_group="M"
        )
        self.other_journal = Journal.objects.create(
            name="Other Journal", slug="other-migration-journal", az_group="O"
        )
        self.old_category = self.create_category("Old", "OLD", "old")
        self.new_category = self.create_category("New", "NEW", "new")
        self.related_category = self.create_category("Related", "RELATED", "related")
        self.disabled_category = self.create_category(
            "Disabled", "DISABLED", "disabled", status="disabled"
        )
        self.foreign_category = self.create_category(
            "Foreign", "FOREIGN", "foreign", journal=self.other_journal
        )

    def create_category(self, name, code, slug, *, status="active", journal=None):
        return JournalCategory.objects.create(
            journal=journal or self.journal,
            name=name,
            code=code,
            slug=slug,
            depth=1,
            path_cache=slug,
            status=status,
        )

    def create_article(self, title, *, category=None, publish=False):
        slug = title.casefold().replace(" ", "-")
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Abstract",
            body=[("paragraph", "<p>Body</p>")],
            authors="Author",
            keywords="AI",
            primary_journal=self.journal,
            owner=self.user,
            review_status=ArticlePage.ReviewStatus.APPROVED,
        )
        Page.get_first_root_node().add_child(instance=article)
        if category is not None:
            ArticleCategoryAssignment.objects.create(
                article=article, category=category, is_primary=True
            )
        if publish:
            revision = article.save_revision(
                user=self.user, bypass_article_permission_check=True
            )
            revision.publish(user=self.user, skip_permission_checks=True)
            ArticlePage.objects.filter(pk=article.pk).update(
                review_status=ArticlePage.ReviewStatus.APPROVED
            )
            article.refresh_from_db()
            sync_category_placements(article_id=article.pk, actor=self.user)
        return article

    def plan(self, *, article=None, batch_id="batch-001"):
        output = self.base_dir / f"{batch_id}.csv"
        call_command(
            "plan_article_category_migration",
            journal=self.journal.slug,
            output=str(output),
            batch_id=batch_id,
            stdout=StringIO(),
        )
        rows = self.read_rows(output)
        if article is not None:
            rows = [row for row in rows if int(row["article_id"]) == article.pk]
        return output, rows

    def read_rows(self, path):
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def write_rows(self, path, rows):
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def reviewed_plan(self, article, *, batch_id="batch-apply", primary="NEW"):
        output, rows = self.plan(article=article, batch_id=batch_id)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        row["suggested_primary_category_code"] = primary
        row["suggested_related_category_codes"] = "RELATED"
        row["confidence"] = "0.95"
        row["requires_manual_review"] = "false"
        row["manual_confirmed"] = "false"
        row["error_code"] = ""
        self.write_rows(output, [row])
        return output

    def run_apply(self, path, batch_id, *, dry_run=False):
        options = {
            "input": str(path),
            "batch_id": batch_id,
            "stdout": StringIO(),
        }
        if dry_run:
            options["dry_run"] = True
        return call_command("apply_article_category_migration", **options)

    @override_settings(DEBUG=True)
    def test_plan_contains_contract_metadata_and_flags_low_confidence(self):
        article = self.create_article("Unmatched historical article")
        output, rows = self.plan(article=article, batch_id="batch-plan")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["migration_batch_id"], "batch-plan")
        self.assertEqual(row["rule_version"], RULE_VERSION)
        self.assertTrue(row["generated_at"])
        self.assertEqual(row["confidence"], "0.00")
        self.assertEqual(row["requires_manual_review"], "true")
        self.assertEqual(row["error_code"], "ARTICLE_PRIMARY_CATEGORY_REQUIRED")
        metadata = json.loads(
            output.with_suffix(output.suffix + ".meta.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["migration_batch_id"], "batch-plan")
        self.assertEqual(metadata["rule_version"], RULE_VERSION)
        self.assertEqual(metadata["row_count"], 1)
        self.assertEqual(
            metadata["sha256"], hashlib.sha256(output.read_bytes()).hexdigest()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="ArticleCategoryMigration", request_id="batch-plan"
            ).exists()
        )

    @override_settings(DEBUG=True)
    def test_dry_run_is_zero_write_and_apply_creates_draft_revision_only(self):
        article = self.create_article(
            "Apply historical article", category=self.old_category, publish=True
        )
        old_live_revision_id = article.live_revision_id
        old_revision_count = article.revisions.count()
        old_placement_ids = set(
            ArticlePlacement.objects.filter(
                article=article, source="system"
            ).values_list("target_category_id", flat=True)
        )
        path = self.reviewed_plan(article)

        self.run_apply(path, "batch-apply", dry_run=True)
        article.refresh_from_db()
        self.assertEqual(article.revisions.count(), old_revision_count)
        self.assertEqual(
            set(article.category_assignments.values_list("category_id", flat=True)),
            {self.old_category.pk},
        )
        self.assertEqual(
            set(
                ArticlePlacement.objects.filter(
                    article=article, source="system"
                ).values_list("target_category_id", flat=True)
            ),
            old_placement_ids,
        )
        receipt = json.loads(
            (
                self.base_dir
                / "backups/category-migrations/batch-apply/validation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            receipt["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
        )
        self.assertEqual(receipt["failed"], 0)

        self.run_apply(path, "batch-apply")
        article.refresh_from_db()
        self.assertEqual(article.live_revision_id, old_live_revision_id)
        self.assertEqual(article.revisions.count(), old_revision_count + 1)
        self.assertEqual(
            list(
                article.category_assignments.order_by("sort_order").values_list(
                    "category_id", "is_primary"
                )
            ),
            [(self.new_category.pk, True), (self.related_category.pk, False)],
        )
        self.assertEqual(
            set(
                ArticlePlacement.objects.filter(
                    article=article, source="system", is_active=True
                ).values_list("target_category_id", flat=True)
            ),
            old_placement_ids,
        )

    @override_settings(DEBUG=True)
    def test_apply_rejects_changed_hash_and_batch_mismatch(self):
        article = self.create_article(
            "Hash article", category=self.old_category, publish=True
        )
        path = self.reviewed_plan(article, batch_id="batch-hash")
        self.run_apply(path, "batch-hash", dry_run=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesMessage(CommandError, "Input hash or batch ID"):
            self.run_apply(path, "batch-hash")

        mismatch = self.reviewed_plan(article, batch_id="batch-csv")
        with self.assertRaisesMessage(CommandError, "migration_batch_id"):
            self.run_apply(mismatch, "different-batch", dry_run=True)

    @override_settings(DEBUG=True)
    def test_apply_reparses_codes_and_blocks_inactive_category(self):
        article = self.create_article(
            "Inactive article", category=self.old_category, publish=True
        )
        path = self.reviewed_plan(
            article, batch_id="batch-inactive", primary="DISABLED"
        )
        with self.assertRaisesMessage(CommandError, "dry-run failed"):
            self.run_apply(path, "batch-inactive", dry_run=True)
        receipt = json.loads(
            (
                self.base_dir
                / "backups/category-migrations/batch-inactive/validation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["failed"], 1)
        self.assertEqual(receipt["failures"][0]["code"], "CATEGORY_INACTIVE")

    @override_settings(DEBUG=True)
    def test_apply_is_idempotent_and_rollback_does_not_require_input(self):
        article = self.create_article(
            "Rollback article", category=self.old_category, publish=True
        )
        path = self.reviewed_plan(article, batch_id="batch-rollback")
        self.run_apply(path, "batch-rollback", dry_run=True)
        self.run_apply(path, "batch-rollback")
        revision_count = article.revisions.count()
        self.run_apply(path, "batch-rollback")
        article.refresh_from_db()
        self.assertEqual(article.revisions.count(), revision_count)

        call_command(
            "apply_article_category_migration",
            batch_id="batch-rollback",
            rollback=True,
            stdout=StringIO(),
        )
        article.refresh_from_db()
        self.assertEqual(
            list(article.category_assignments.values_list("category_id", flat=True)),
            [self.old_category.pk],
        )
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="ArticleCategoryMigration",
                request_id="batch-rollback",
                metadata__operation="rollback",
            ).exists()
        )

    @override_settings(DEBUG=True)
    def test_low_confidence_requires_explicit_manual_confirmation(self):
        article = self.create_article(
            "Manual review article", category=self.old_category, publish=True
        )
        path = self.reviewed_plan(article, batch_id="batch-manual")
        rows = self.read_rows(path)
        rows[0]["confidence"] = "0.60"
        rows[0]["requires_manual_review"] = "true"
        rows[0]["manual_confirmed"] = "false"
        self.write_rows(path, rows)
        with self.assertRaisesMessage(CommandError, "dry-run failed"):
            self.run_apply(path, "batch-manual", dry_run=True)
        receipt = json.loads(
            (
                self.base_dir
                / "backups/category-migrations/batch-manual/validation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["failures"][0]["code"], "MANUAL_REVIEW_REQUIRED")

    @override_settings(DEBUG=True)
    def test_apply_resumes_after_partial_failure_without_reapplying_completed_rows(
        self,
    ):
        first = self.create_article(
            "Resume first article", category=self.old_category, publish=True
        )
        second = self.create_article(
            "Resume second article", category=self.old_category, publish=True
        )
        batch_id = "batch-resume"
        path, rows = self.plan(batch_id=batch_id)
        selected = []
        for row in rows:
            article_id = int(row["article_id"])
            if article_id not in {first.pk, second.pk}:
                continue
            row["suggested_primary_category_code"] = (
                "NEW" if article_id == first.pk else "RELATED"
            )
            row["suggested_related_category_codes"] = ""
            row["confidence"] = "0.95"
            row["requires_manual_review"] = "false"
            row["manual_confirmed"] = "false"
            row["error_code"] = ""
            selected.append(row)
        self.write_rows(path, selected)
        self.run_apply(path, batch_id, dry_run=True)
        JournalCategory.objects.filter(pk=self.related_category.pk).update(
            status="disabled"
        )
        with self.assertRaisesMessage(CommandError, "CATEGORY_INACTIVE"):
            self.run_apply(path, batch_id)
        progress_path = (
            self.base_dir / f"backups/category-migrations/{batch_id}/progress.json"
        )
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual(progress["completed_article_ids"], [first.pk])
        first_revision_count = first.revisions.count()

        JournalCategory.objects.filter(pk=self.related_category.pk).update(
            status="active"
        )
        self.run_apply(path, batch_id)
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual(progress["completed_article_ids"], [first.pk, second.pk])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.revisions.count(), first_revision_count)
        self.assertEqual(
            list(first.category_assignments.values_list("category_id", flat=True)),
            [self.new_category.pk],
        )
        self.assertEqual(
            list(second.category_assignments.values_list("category_id", flat=True)),
            [self.related_category.pk],
        )

    @override_settings(DEBUG=True)
    def test_sync_command_dry_run_apply_and_retry_are_idempotent(self):
        article = self.create_article(
            "Placement sync article", category=self.old_category, publish=True
        )
        ArticlePlacement.objects.filter(article=article, source="system").delete()
        call_command(
            "sync_category_placements",
            journal=self.journal.slug,
            dry_run=True,
            stdout=StringIO(),
        )
        self.assertFalse(
            ArticlePlacement.objects.filter(article=article, source="system").exists()
        )
        call_command(
            "sync_category_placements",
            journal=self.journal.slug,
            apply=True,
            stdout=StringIO(),
        )
        call_command(
            "sync_category_placements",
            journal=self.journal.slug,
            apply=True,
            stdout=StringIO(),
        )
        self.assertEqual(
            ArticlePlacement.objects.filter(
                article=article,
                source="system",
                target_category=self.old_category,
            ).count(),
            1,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                request_id=f"sync-category-placements:{self.journal.slug}"
            ).exists()
        )
