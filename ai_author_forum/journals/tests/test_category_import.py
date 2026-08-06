import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from wagtail.models import Revision

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.category_services import create_category
from ai_author_forum.journals.models import (
    ArticleImportJob,
    ImportRowStatus,
    Journal,
    JournalCategoryStatus,
    StaticArticle,
    StaticArticleCategoryAssignment,
)
from ai_author_forum.journals.services import (
    ImportIssue,
    _build_error_report,
    import_article_rows,
)
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.test_helpers import grant_business_super_admin


class ArticleCategoryImportTests(TestCase):
    def setUp(self):
        self.actor = grant_business_super_admin(
            get_user_model().objects.create_superuser(
                username="category-import-admin",
                email="category-import-admin@example.com",
                password="test",
            )
        )
        self.journal = Journal.objects.create(
            name="Nature Machine Intelligence",
            slug="nmi",
            az_group="N",
        )
        self.other_journal = Journal.objects.create(
            name="Other Journal",
            slug="other",
            az_group="O",
        )
        self.research = self.create_category(
            self.journal, "NMI-RESEARCH", "research", "Research"
        )
        self.generative_ai = self.create_category(
            self.journal,
            "NMI-RESEARCH-GENAI",
            "generative-ai",
            "Generative AI",
            parent=self.research,
        )
        self.ethics = self.create_category(
            self.journal, "NMI-ETHICS", "ethics", "AI Ethics"
        )
        self.disabled = self.create_category(
            self.journal, "NMI-DISABLED", "disabled", "Disabled"
        )
        self.disabled.status = JournalCategoryStatus.DISABLED
        self.disabled.generate_static_page = False
        self.disabled.save(update_fields=("status", "generate_static_page"))
        self.foreign = self.create_category(
            self.other_journal, "OTHER-CATEGORY", "foreign", "Foreign"
        )

    def create_category(self, journal, code, slug, name, parent=None):
        return create_category(
            journal=journal,
            parent=parent,
            data={"name": name, "code": code, "slug": slug},
            actor=self.actor,
        ).category

    def row(self, *, slug="article", **updates):
        row = {
            "journal_slug": self.journal.slug,
            "title": "Imported article",
            "slug": slug,
            "article_type": "ai_article",
            "status": "draft",
            "body_html": "<p>Body</p>",
            "primary_category_code": "",
            "primary_category_path": "",
            "related_category_codes": "",
            "related_category_paths": "",
        }
        row.update(updates)
        return row

    def run_import(self, rows, *, dry_run=False):
        job = ArticleImportJob.objects.create(package_name="category-import")
        with TemporaryDirectory() as temp_dir:
            result = import_article_rows(
                job,
                rows,
                package_name="category-import",
                extract_root=Path(temp_dir),
                asset_map={},
                dry_run=dry_run,
            )
        job.refresh_from_db()
        return job, result

    def test_only_code_only_path_and_matching_code_path_resolve(self):
        cases = (
            (
                "only-code",
                {"primary_category_code": self.generative_ai.code},
            ),
            (
                "only-path",
                {"primary_category_path": "Research > Generative AI"},
            ),
            (
                "code-and-path",
                {
                    "primary_category_code": self.generative_ai.code,
                    "primary_category_path": "Research > Generative AI",
                },
            ),
        )
        for slug, values in cases:
            with self.subTest(slug=slug):
                _job, (created, updated, issues) = self.run_import(
                    [self.row(slug=slug, **values)]
                )
                self.assertEqual((created, updated, issues), (1, 0, []))
                assignment = StaticArticleCategoryAssignment.objects.get(
                    article__slug=slug
                )
                self.assertEqual(assignment.category, self.generative_ai)
                self.assertTrue(assignment.is_primary)

    def test_related_code_path_pairs_preserve_order(self):
        _job, (_created, _updated, issues) = self.run_import(
            [
                self.row(
                    primary_category_code=self.generative_ai.code,
                    related_category_codes=f"{self.ethics.code};{self.research.code}",
                    related_category_paths="AI Ethics;Research",
                )
            ]
        )
        self.assertEqual(issues, [])
        article = StaticArticle.objects.get(slug="article")
        assignments = list(
            article.category_assignments.order_by("sort_order").values_list(
                "category_id", "is_primary"
            )
        )
        self.assertEqual(
            assignments,
            [
                (self.generative_ai.pk, True),
                (self.ethics.pk, False),
                (self.research.pk, False),
            ],
        )

    def test_invalid_category_inputs_are_row_level_errors(self):
        related = []
        for index in range(11):
            related.append(
                self.create_category(
                    self.journal,
                    f"NMI-R-{index}",
                    f"related-{index}",
                    f"Related {index}",
                )
            )
        cases = (
            (
                "CATEGORY_RESOLUTION_CONFLICT",
                self.row(
                    slug="conflict",
                    primary_category_code=self.generative_ai.code,
                    primary_category_path="AI Ethics",
                ),
            ),
            (
                "CATEGORY_NOT_FOUND",
                self.row(slug="missing", primary_category_code="DOES-NOT-EXIST"),
            ),
            (
                "CATEGORY_INACTIVE",
                self.row(slug="inactive", primary_category_code=self.disabled.code),
            ),
            (
                "CATEGORY_CROSS_JOURNAL",
                self.row(slug="cross", primary_category_code=self.foreign.code),
            ),
            (
                "ARTICLE_DUPLICATE_CATEGORY",
                self.row(
                    slug="duplicate",
                    primary_category_code=self.generative_ai.code,
                    related_category_codes=self.generative_ai.code,
                ),
            ),
            (
                "ARTICLE_MULTIPLE_PRIMARY_CATEGORIES",
                self.row(
                    slug="multiple-primary",
                    primary_category_code=(
                        f"{self.generative_ai.code};{self.ethics.code}"
                    ),
                ),
            ),
            (
                "CATEGORY_RELATED_PAIR_COUNT_MISMATCH",
                self.row(
                    slug="pair-count",
                    primary_category_code=self.generative_ai.code,
                    related_category_codes=(f"{self.ethics.code};{self.research.code}"),
                    related_category_paths="AI Ethics",
                ),
            ),
            (
                "ARTICLE_TOO_MANY_RELATED_CATEGORIES",
                self.row(
                    slug="too-many",
                    primary_category_code=self.generative_ai.code,
                    related_category_codes=";".join(
                        category.code for category in related
                    ),
                ),
            ),
        )
        for expected_code, row in cases:
            with self.subTest(expected_code=expected_code):
                _job, (created, updated, issues) = self.run_import([row])
                self.assertEqual((created, updated), (0, 0))
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0].error_code, expected_code)
                self.assertFalse(
                    StaticArticle.objects.filter(slug=row["slug"]).exists()
                )

    def test_dry_run_writes_no_business_tables_or_revisions_or_placements(self):
        baseline_revisions = Revision.objects.count()
        _job, (created, updated, issues) = self.run_import(
            [
                self.row(
                    primary_category_code=self.generative_ai.code,
                    related_category_codes=self.ethics.code,
                )
            ],
            dry_run=True,
        )
        self.assertEqual((created, updated, issues), (1, 0, []))
        self.assertEqual(StaticArticle.objects.count(), 0)
        self.assertEqual(StaticArticleCategoryAssignment.objects.count(), 0)
        self.assertEqual(ArticlePage.objects.count(), 0)
        self.assertEqual(Revision.objects.count(), baseline_revisions)
        self.assertEqual(ArticlePlacement.objects.count(), 0)

    def test_apply_is_idempotent_and_updates_one_canonical_draft(self):
        row = self.row(
            primary_category_code=self.generative_ai.code,
            related_category_codes=self.ethics.code,
        )
        _first_job, first = self.run_import([row])
        _second_job, second = self.run_import([row])
        self.assertEqual(first[:2], (1, 0))
        self.assertEqual(second[:2], (0, 1))
        self.assertEqual(StaticArticle.objects.count(), 1)
        self.assertEqual(StaticArticleCategoryAssignment.objects.count(), 2)
        self.assertEqual(ArticlePage.objects.count(), 1)
        page = ArticlePage.objects.get(source_static_article__slug="article")
        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertFalse(page.live)
        self.assertGreaterEqual(page.revisions.count(), 2)
        self.assertEqual(ArticlePlacement.objects.count(), 0)

    def test_import_conversion_copies_categories_to_one_draft_revision(self):
        self.run_import(
            [
                self.row(
                    primary_category_code=self.generative_ai.code,
                    related_category_codes=self.ethics.code,
                )
            ]
        )
        static_article = StaticArticle.objects.get(slug="article")
        page = ArticlePage.objects.get(source_static_article=static_article)

        self.assertEqual(page.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertFalse(page.live)
        self.assertEqual(
            list(
                page.category_assignments.order_by("sort_order").values_list(
                    "category_id", "is_primary"
                )
            ),
            [(self.generative_ai.pk, True), (self.ethics.pk, False)],
        )
        revision = page.get_latest_revision().as_object()
        self.assertEqual(
            list(
                revision.category_assignments.order_by("sort_order").values_list(
                    "category_id", "is_primary"
                )
            ),
            [(self.generative_ai.pk, True), (self.ethics.pk, False)],
        )
        self.assertFalse(ArticlePlacement.objects.filter(article=page).exists())

    def test_conversion_failure_is_localized_and_rolls_back_the_import_row(self):
        baseline_revisions = Revision.objects.count()
        with patch(
            "ai_author_forum.articles.category_services.copy_static_article_categories",
            side_effect=ValidationError("simulated conversion failure"),
        ):
            job, (created, updated, issues) = self.run_import(
                [self.row(primary_category_code=self.generative_ai.code)]
            )
        self.assertEqual((created, updated), (0, 0))
        self.assertEqual(len(issues), 1)
        self.assertIn("simulated conversion failure", issues[0].message)
        self.assertEqual(job.rows.filter(status=ImportRowStatus.FAILED).count(), 1)
        self.assertEqual(StaticArticle.objects.count(), 0)
        self.assertEqual(ArticlePage.objects.count(), 0)
        self.assertEqual(Revision.objects.count(), baseline_revisions)
        self.assertEqual(ArticlePlacement.objects.count(), 0)

    def test_apply_assignment_failure_rolls_back_static_article_row(self):
        with patch(
            "ai_author_forum.articles.category_services.assign_static_article_categories",
            side_effect=ValidationError("simulated assignment failure"),
        ):
            _job, (created, updated, issues) = self.run_import(
                [self.row(primary_category_code=self.generative_ai.code)]
            )
        self.assertEqual((created, updated), (0, 0))
        self.assertEqual(len(issues), 1)
        self.assertFalse(StaticArticle.objects.filter(slug="article").exists())
        self.assertEqual(StaticArticleCategoryAssignment.objects.count(), 0)

    def test_error_report_contains_required_contract_columns(self):
        payload = {
            "external_id": "EXT-1",
            "journal_code": "nmi",
            "input_primary_category_code": "BAD",
            "input_primary_category_path": "Research > Missing",
            "resolved_primary_category_id": "",
            "input_related_categories": {"codes": "A;B", "paths": "A;B"},
            "resolved_related_category_ids": [1, 2],
        }
        report = _build_error_report(
            [
                ImportIssue(
                    7,
                    "article",
                    "Category not found",
                    payload,
                    error_code="CATEGORY_NOT_FOUND",
                )
            ]
        ).decode("utf-8-sig")
        rows = list(csv.DictReader(StringIO(report)))
        self.assertEqual(
            set(
                (
                    "row_number",
                    "external_id",
                    "journal_code",
                    "input_primary_category_code",
                    "input_primary_category_path",
                    "resolved_primary_category_id",
                    "input_related_categories",
                    "resolved_related_category_ids",
                    "status",
                    "error_code",
                    "error_message",
                )
            ).difference(rows[0]),
            set(),
        )
        self.assertEqual(rows[0]["row_number"], "7")
        self.assertEqual(rows[0]["error_code"], "CATEGORY_NOT_FOUND")
        self.assertEqual(rows[0]["status"], "failed")
