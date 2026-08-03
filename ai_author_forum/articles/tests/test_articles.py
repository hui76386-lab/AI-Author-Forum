from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import SkipTest

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from wagtail.models import GroupPagePermission, Page, Site, Workflow, WorkflowTask

from ai_author_forum.journals.models import JournalCategory
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot

from ..admin_filters import ArticleAdminFilters, build_article_admin_queryset
from ..bulk_services import execute_bulk_article_action
from ..display import resolve_article_image
from ..editor_services import validate_raw_html_permission
from ..forms import ArticlePageForm
from ..models import (
    ARTICLE_EDIT_PERMISSION,
    ARTICLE_PAGE_TEMPLATE,
    ARTICLE_REVIEW_PERMISSION,
    ArticleCategoryAssignment,
    ArticlePage,
    ArticleReviewRecord,
    ArticleReviewTask,
    ArticleRevisionConflict,
)
from ..services import (
    get_approved_articles,
    get_article_context,
    get_articles_by_journal,
)
from ..views import ArticleReviewDetailView


class ArticlePageWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.Journal = cls.get_journal_model()
        cls.root_page = Page.get_first_root_node()
        cls.editor = cls.create_user("editor")
        cls.reviewer = cls.create_user("reviewer")
        cls.configure_editor_permissions(cls.editor)
        cls.configure_reviewer_permissions(cls.reviewer)
        cls.journal = cls.create_journal("AI Journal", "ai-journal")

    @staticmethod
    def get_journal_model():
        try:
            return apps.get_model("journals", "Journal")
        except LookupError as error:
            raise SkipTest(
                "journals.Journal is required for ArticlePage tests."
            ) from error

    @staticmethod
    def create_user(username):
        return get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="test-password",
        )

    @classmethod
    def configure_editor_permissions(cls, user):
        group = Group.objects.create(name="Test Content Editors")
        group.permissions.add(cls.get_admin_access_permission())
        group.permissions.add(cls.get_article_permission(ARTICLE_EDIT_PERMISSION))
        group.user_set.add(user)

        GroupPagePermission.objects.create(
            group=group,
            page=cls.root_page,
            permission=cls.get_wagtail_page_permission("add_page"),
        )

    @classmethod
    def configure_reviewer_permissions(cls, user):
        group = Group.objects.create(name="Test Content Reviewers")
        group.permissions.add(cls.get_admin_access_permission())
        group.permissions.add(cls.get_article_permission(ARTICLE_REVIEW_PERMISSION))
        group.user_set.add(user)

    @staticmethod
    def get_admin_access_permission():
        return Permission.objects.get(
            content_type__app_label="wagtailadmin",
            codename="access_admin",
        )

    @staticmethod
    def get_wagtail_page_permission(codename):
        return Permission.objects.get(
            content_type__app_label="wagtailcore",
            codename=codename,
        )

    @staticmethod
    def get_article_permission(permission_name):
        content_type = ContentType.objects.get_for_model(
            ArticlePage,
            for_concrete_model=False,
        )
        codename = permission_name.split(".", maxsplit=1)[1]
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": f"Can {codename.replace('_', ' ')}"},
        )
        return permission

    @classmethod
    def create_journal(cls, title, slug):
        field_values = {}

        for field in cls.Journal._meta.fields:
            if field.primary_key or field.auto_created:
                continue

            if field.name in {"title", "name"}:
                field_values[field.name] = title
            elif field.name == "slug":
                field_values[field.name] = slug
            elif not field.blank and not field.null and not field.has_default():
                field_values[field.name] = cls.default_field_value(field, slug)

        return cls.Journal.objects.create(**field_values)

    @staticmethod
    def default_field_value(field, slug):
        if isinstance(field, models.CharField | models.SlugField):
            return f"{slug}-{field.name}"
        if isinstance(field, models.TextField):
            return f"{slug} {field.name}"
        if isinstance(field, models.BooleanField):
            return False
        if isinstance(field, models.IntegerField):
            return 0
        raise SkipTest(
            f"Cannot infer required value for journals.Journal.{field.name}."
        )

    def create_article(
        self,
        title,
        status=None,
        owner=None,
        save_revision=True,
        parent=None,
        assign_primary_category=True,
    ):
        article = ArticlePage(
            title=title,
            slug=title.lower().replace(" ", "-"),
            abstract=f"{title} abstract.",
            body=[("paragraph", f"<p>{title} body.</p>")],
            authors="Human Author",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=self.journal,
            keywords="ai, publishing",
            owner=owner or self.editor,
        )

        if status:
            article.review_status = status

        (parent or self.root_page).add_child(instance=article)

        if assign_primary_category:
            category, _ = JournalCategory.objects.get_or_create(
                journal=self.journal,
                code="GENERAL",
                defaults={
                    "name": "General",
                    "slug": "general",
                    "depth": 1,
                    "path_cache": "general",
                },
            )
            ArticleCategoryAssignment.objects.create(
                article=article, category=category, is_primary=True
            )

        if save_revision:
            article.save_revision(user=owner or self.editor)

        return ArticlePage.objects.get(pk=article.pk)

    def test_dashboard_owner_and_updated_from_filters_match_recent_edits(self):
        recent = self.create_article("Recent Mine", owner=self.editor)
        old = self.create_article("Old Mine", owner=self.editor)
        other = self.create_article("Recent Other", owner=self.editor)
        ArticlePage.objects.filter(pk=other.pk).update(owner_id=self.reviewer.pk)
        ArticlePage.objects.filter(pk=old.pk).update(
            latest_revision_created_at=timezone.now() - timedelta(days=10)
        )
        filters = ArticleAdminFilters(
            owner=str(self.editor.pk),
            updated_from=(timezone.localdate() - timedelta(days=7)).isoformat(),
        )

        self.assertEqual(
            set(build_article_admin_queryset(filters).values_list("pk", flat=True)),
            {recent.pk},
        )
        self.assertNotEqual(recent.pk, other.pk)

    def test_dashboard_reviewer_and_date_filters_match_recent_decisions(self):
        recent = self.create_article("Reviewed Recent")
        old = self.create_article("Reviewed Old")
        other = self.create_article("Reviewed Other")
        other_reviewer = self.create_user("other-reviewer")
        recent_record = ArticleReviewRecord.objects.create(
            article=recent,
            reviewer=self.reviewer,
            action=ArticleReviewRecord.Action.APPROVED,
            revision=recent.latest_revision,
        )
        old_record = ArticleReviewRecord.objects.create(
            article=old,
            reviewer=self.reviewer,
            action=ArticleReviewRecord.Action.REJECTED,
            revision=old.latest_revision,
        )
        ArticleReviewRecord.objects.create(
            article=other,
            reviewer=other_reviewer,
            action=ArticleReviewRecord.Action.APPROVED,
            revision=other.latest_revision,
        )
        ArticleReviewRecord.objects.filter(pk=old_record.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        filters = ArticleAdminFilters(
            reviewed_by=str(self.reviewer.pk),
            reviewed_from=(timezone.localdate() - timedelta(days=7)).isoformat(),
        )

        self.assertEqual(
            set(build_article_admin_queryset(filters).values_list("pk", flat=True)),
            {recent.pk},
        )
        self.assertIsNotNone(recent_record.pk)

    def test_dashboard_waiting_filter_uses_submission_time(self):
        overdue = self.create_article(
            "Overdue Submission",
            status=ArticlePage.ReviewStatus.SUBMITTED,
        )
        recent = self.create_article(
            "Recent Submission",
            status=ArticlePage.ReviewStatus.SUBMITTED,
        )
        overdue_record = ArticleReviewRecord.objects.create(
            article=overdue,
            reviewer=self.editor,
            action=ArticleReviewRecord.Action.SUBMITTED,
            revision=overdue.latest_revision,
        )
        ArticleReviewRecord.objects.create(
            article=recent,
            reviewer=self.editor,
            action=ArticleReviewRecord.Action.SUBMITTED,
            revision=recent.latest_revision,
        )
        ArticleReviewRecord.objects.filter(pk=overdue_record.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )
        filters = ArticleAdminFilters(
            review_status=ArticlePage.ReviewStatus.SUBMITTED,
            waiting="48h",
        )

        self.assertEqual(
            set(build_article_admin_queryset(filters).values_list("pk", flat=True)),
            {overdue.pk},
        )

    def test_create_article_draft_saves_successfully(self):
        article = self.create_article("Draft Article")

        self.assertIsNotNone(article.pk)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertIsNotNone(article.get_latest_revision())

    def test_submit_for_review_updates_status_and_records_action(self):
        article = self.create_article("Submitted Article")

        record = article.submit_for_review(self.editor, "Ready for review.")
        article.refresh_from_db()

        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertEqual(record.action, ArticleReviewRecord.Action.SUBMITTED)
        self.assertEqual(record.reviewer, self.editor)
        self.assertEqual(article.review_records.count(), 1)

    def test_submit_review_admin_action_moves_draft_to_pending(self):
        superuser = get_user_model().objects.create_superuser(
            username="submit-action-superuser",
            email="submit-action-superuser@example.com",
            password="test-password",
        )
        article = self.create_article("Submit Action Article")
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("article_admin:submit_review", args=[article.pk]),
            {
                "expected_revision_id": article.latest_revision_id,
                "comment": "Ready from row action.",
            },
        )

        article.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertTrue(
            article.review_records.filter(
                action=ArticleReviewRecord.Action.SUBMITTED,
                reviewer=superuser,
            ).exists()
        )

    def test_submit_review_admin_action_requires_primary_category(self):
        superuser = get_user_model().objects.create_superuser(
            username="submit-action-no-category",
            email="submit-action-no-category@example.com",
            password="test-password",
        )
        article = self.create_article(
            "Submit Action Missing Category",
            assign_primary_category=False,
        )
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("article_admin:submit_review", args=[article.pk]),
            {"expected_revision_id": article.latest_revision_id},
        )

        article.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertFalse(article.review_records.exists())

    def test_business_review_permission_can_moderate_wagtail_workflow_task(self):
        task_group = Group.objects.create(name="Narrow Wagtail Task Reviewers")
        task = ArticleReviewTask.objects.create(name="Business Permission Review Task")
        task.groups.add(task_group)
        workflow = Workflow.objects.create(name="Business Permission Workflow")
        WorkflowTask.objects.create(workflow=workflow, task=task, sort_order=0)
        article = self.create_article("Business Permission Workflow Article")
        workflow.start(article, user=self.editor)
        ArticlePage.objects.filter(pk=article.pk).update(
            review_status=ArticlePage.ReviewStatus.SUBMITTED
        )
        article.refresh_from_db()

        view = ArticleReviewDetailView()
        view.article = article
        view.request = SimpleNamespace(user=self.reviewer)

        self.assertFalse(task_group.user_set.filter(pk=self.reviewer.pk).exists())
        self.assertTrue(self.reviewer.has_perm(ARTICLE_REVIEW_PERMISSION))
        self.assertEqual(
            {action[0] for action in task.get_actions(article, self.reviewer)},
            {"approve", "reject"},
        )
        self.assertTrue(view.can_execute_review())
        self.assertTrue(view.can_execute_review("approve"))
        self.assertTrue(view.can_execute_review("reject"))

        expected_revision = article.get_latest_revision()
        view.perform_review_action(
            "approve",
            "Approved without publishing.",
            expected_revision.pk,
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.APPROVED)
        self.assertEqual(article.approved_version_id, expected_revision.pk)

    def test_approve_updates_status_and_records_reviewer(self):
        article = self.create_article("Approved Article")
        article.submit_for_review(self.editor, "Ready for approval.")

        record = article.approve(self.reviewer, "Approved.")
        article.refresh_from_db()

        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.APPROVED)
        self.assertEqual(record.action, ArticleReviewRecord.Action.APPROVED)
        self.assertEqual(record.reviewer, self.reviewer)
        self.assertEqual(article.approved_version, article.get_latest_revision())

    def test_reject_updates_status_and_can_be_resubmitted(self):
        article = self.create_article("Rejected Article")
        article.submit_for_review(self.editor, "Initial submission.")

        record = article.reject(self.reviewer, "Needs changes.")
        article.refresh_from_db()

        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.REJECTED)
        self.assertEqual(record.action, ArticleReviewRecord.Action.REJECTED)
        self.assertEqual(record.reviewer, self.reviewer)
        self.assertEqual(article.rejected_version, article.get_latest_revision())

        article.submit_for_review(self.editor, "Resubmitted.")
        article.refresh_from_db()

        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertEqual(
            article.review_records.filter(
                action=ArticleReviewRecord.Action.SUBMITTED,
            ).count(),
            2,
        )

    def test_formal_page_renders_with_the_canonical_article_template(self):
        site_root = Site.objects.get(is_default_site=True).root_page
        article = self.create_article("Formal Article", parent=site_root)
        ArticlePage.objects.filter(pk=article.pk).update(
            review_status=ArticlePage.ReviewStatus.APPROVED
        )
        article.refresh_from_db()
        ArticlePlacement.objects.create(
            article=article,
            slot=LayoutSlot.objects.get(code="section_article_list"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="news",
        )
        canonical_url = reverse("article_detail", kwargs={"slug": article.static_slug})
        request = RequestFactory().get(canonical_url)
        request.user = self.editor

        template_response = article.serve(request)
        template_response.render()
        http_response = self.client.get(canonical_url)
        tree_response = self.client.get(article.url)

        self.assertEqual(article.template, ARTICLE_PAGE_TEMPLATE)
        self.assertEqual(article.get_absolute_url(), canonical_url)
        self.assertEqual(
            article.get_static_output_path(),
            f"/articles/{article.static_slug}/index.html",
        )
        self.assertNotEqual(article.url, canonical_url)
        self.assertEqual(template_response.template_name, ARTICLE_PAGE_TEMPLATE)
        self.assertEqual(template_response.status_code, 200)
        self.assertEqual(http_response.status_code, 200)
        self.assertRedirects(
            tree_response,
            canonical_url,
            status_code=301,
            fetch_redirect_response=False,
        )
        self.assertIn(b"Formal Article", http_response.content)
        self.assertIn(b"Formal Article abstract.", http_response.content)
        self.assertIn(b"Formal Article body.", http_response.content)
        self.assertNotIn(b"{% verbatim %}", http_response.content)
        self.assertNotIn(b"{{ current_site.site_name }}", http_response.content)

    def test_structured_body_blocks_render_in_the_default_article_template(self):
        article = self.create_article("Structured Body Article")
        article.body = [
            ("paragraph", "<p>Visually edited introduction.</p>"),
            ("heading", "Results"),
            (
                "list",
                {
                    "list_type": "ordered",
                    "items": ["<p>First finding</p>", "<p>Second finding</p>"],
                },
            ),
            (
                "quote",
                {
                    "quote": "Evidence remains reviewable.",
                    "attribution": "Editorial team",
                },
            ),
            (
                "table",
                {
                    "data": [["Metric", "Value"], ["Accuracy", "98%"]],
                    "first_row_is_table_header": True,
                    "first_col_is_header": False,
                    "table_header_choice": "row",
                    "table_caption": "Evaluation results",
                },
            ),
        ]
        request = RequestFactory().get("/admin/pages/1/edit/preview/")
        request.user = self.editor

        response = article.serve_preview(request, article.default_preview_mode)
        response.render()

        self.assertContains(response, "Visually edited introduction.")
        self.assertContains(
            response, '<h2 class="c-article-section-heading">Results</h2>'
        )
        self.assertContains(response, '<ol class="c-article-content-list">')
        self.assertNotContains(response, '<ol class="c-article-list">')
        self.assertContains(response, "First finding")
        self.assertContains(response, '<blockquote class="c-article-quote">')
        self.assertContains(response, "Evidence remains reviewable.")
        self.assertContains(response, '<div class="c-article-table"')
        self.assertContains(response, "Evaluation results")
        self.assertContains(response, "Accuracy")
        self.assertEqual(response.content.count(b"</main>"), 1)

    def test_editor_protection_targets_wagtail_block_options(self):
        script_source = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "articles"
            / "js"
            / "editor-protection.js"
        ).read_text(encoding="utf-8")

        self.assertIn('[role="option"]', script_source)
        self.assertIn('text.indexOf("raw html") !== -1', script_source)
        self.assertNotIn('[data-block-type="html"], button, a', script_source)

    def test_article_template_closes_the_body_container_before_the_right_rail(self):
        template_source = (
            Path(__file__).resolve().parents[3]
            / "templates"
            / "articles"
            / "article_page.html"
        ).read_text(encoding="utf-8")

        body_close = template_source.index('</div>\n  <aside class="c-right-rail">')
        self.assertGreater(body_close, template_source.index('id="full-text"'))
        self.assertNotIn("  </main>", template_source)
        self.assertIn('class="c-article-content-list"', template_source)
        self.assertIn('href="{{ block.value.document.file.url }}"', template_source)

    def test_legacy_raw_html_body_still_renders_in_the_default_template(self):
        article = self.create_article("Legacy HTML Article")
        article.body = [
            (
                "html",
                '<section data-legacy="true"><p>Legacy imported body.</p></section>',
            )
        ]
        request = RequestFactory().get("/admin/pages/1/edit/preview/")
        request.user = self.editor

        response = article.serve_preview(request, article.default_preview_mode)
        response.render()

        self.assertContains(response, '<div class="c-article-raw-html">')
        self.assertContains(response, 'data-legacy="true"')
        self.assertContains(response, "Legacy imported body.")

    def test_preview_renders_without_error(self):
        article = self.create_article("Preview Article")
        request = RequestFactory().get("/admin/pages/1/edit/preview/")
        request.user = self.editor

        response = article.serve_preview(request, article.default_preview_mode)
        response.render()

        self.assertEqual(response.template_name, ARTICLE_PAGE_TEMPLATE)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Preview Article", response.content)
        self.assertIn(b'content="noindex,nofollow"', response.content)

    def test_preview_renders_when_static_slug_contains_unicode(self):
        article = self.create_article("Unicode Static Slug Article")
        ArticlePage.objects.filter(pk=article.pk).update(static_slug="测试")
        article.refresh_from_db()
        request = RequestFactory().get("/admin/articles/1/review/preview/")
        request.user = self.editor

        response = article.serve_preview(request, article.default_preview_mode)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unicode Static Slug Article", response.content)

    def test_get_approved_articles_only_returns_reviewed_articles(self):
        draft = self.create_article("Draft Service Article")
        submitted = self.create_article(
            "Submitted Service Article",
            ArticlePage.ReviewStatus.SUBMITTED,
            save_revision=False,
        )
        approved = self.create_article(
            "Approved Service Article",
            ArticlePage.ReviewStatus.APPROVED,
            save_revision=False,
        )
        published = self.create_article(
            "Published Service Article",
            ArticlePage.ReviewStatus.PUBLISHED,
            save_revision=False,
        )
        rejected = self.create_article(
            "Rejected Service Article",
            ArticlePage.ReviewStatus.REJECTED,
            save_revision=False,
        )

        slot = LayoutSlot.objects.get(code="home_featured")
        ArticlePlacement.objects.create(slot=slot, article=approved)
        ArticlePlacement.objects.create(slot=slot, article=published)

        articles = set(get_approved_articles())

        self.assertIn(approved, articles)
        self.assertIn(published, articles)
        self.assertNotIn(draft, articles)
        self.assertNotIn(submitted, articles)
        self.assertNotIn(rejected, articles)

    def test_article_services_require_an_effective_placement_and_active_journal(self):
        article = self.create_article(
            "Scheduled Service Article",
            ArticlePage.ReviewStatus.APPROVED,
            save_revision=False,
        )
        slot = LayoutSlot.objects.get(code="home_featured")
        now = timezone.now()

        self.assertNotIn(article, get_approved_articles(at=now))
        with self.assertRaises(ArticlePage.DoesNotExist):
            get_article_context(article.static_slug, at=now)

        placement = ArticlePlacement.objects.create(
            slot=slot,
            article=article,
            starts_at=now + timedelta(hours=1),
        )
        self.assertNotIn(article, get_approved_articles(at=now))

        placement.starts_at = now - timedelta(hours=1)
        placement.ends_at = now + timedelta(hours=1)
        placement.save(update_fields=("starts_at", "ends_at"))
        self.assertIn(article, get_approved_articles(at=now))
        self.assertEqual(
            get_article_context(article.static_slug, at=now)["article"], article
        )

        slot.is_active = False
        slot.save(update_fields=("is_active",))
        self.assertNotIn(article, get_approved_articles(at=now))

        slot.is_active = True
        slot.save(update_fields=("is_active",))
        self.journal.status = "paused"
        self.journal.save(update_fields=("status",))
        self.assertNotIn(article, get_approved_articles(at=now))

    def test_get_articles_by_journal_uses_the_exact_placement_target(self):
        second_journal = self.create_journal("Second Journal", "second-journal")
        slot = LayoutSlot.objects.get(code="journal_featured")
        article_for_second = self.create_article(
            "Placed On Second Journal",
            ArticlePage.ReviewStatus.APPROVED,
            save_revision=False,
        )
        article_for_second.related_journals.add(second_journal)
        ArticlePlacement.objects.create(
            slot=slot,
            article=article_for_second,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=second_journal.slug,
        )

        article_for_first = self.create_article(
            "Placed On First Journal",
            ArticlePage.ReviewStatus.APPROVED,
            save_revision=False,
        )
        article_for_first.primary_journal = second_journal
        article_for_first.save(update_fields=("primary_journal",))
        article_for_first.related_journals.add(self.journal)
        ArticlePlacement.objects.create(
            slot=slot,
            article=article_for_first,
            target_type=ArticlePlacement.TargetType.JOURNAL,
            target_slug=self.journal.slug,
        )

        self.assertEqual(
            set(get_articles_by_journal(self.journal.slug)),
            {article_for_first},
        )
        self.assertEqual(
            set(get_articles_by_journal(second_journal.slug)),
            {article_for_second},
        )

    def test_editor_cannot_review_article(self):
        article = self.create_article("Editor Cannot Review")
        article.submit_for_review(self.editor, "Ready.")

        with self.assertRaises(PermissionDenied):
            article.approve(self.editor, "Should fail.")

    def test_reviewer_cannot_edit_article(self):
        article = self.create_article("Reviewer Cannot Edit")
        article.title = "Reviewer Edited"

        self.assertFalse(article.permissions_for_user(self.reviewer).can_edit())

        with self.assertRaises(PermissionDenied):
            article.save_revision(user=self.reviewer)

    def test_bulk_actions_cover_every_action_available_in_the_list(self):
        superuser = get_user_model().objects.create_superuser(
            username="bulk-all-actions",
            email="bulk-all-actions@example.com",
            password="test-password",
        )
        target_journal = self.create_journal("Target Journal", "target-journal")
        main_category = JournalCategory.objects.create(
            journal=target_journal,
            name="???",
            code="MAIN",
            slug="main",
            depth=1,
            path_cache="main",
        )
        related_category = JournalCategory.objects.create(
            journal=target_journal,
            name="????",
            code="RELATED",
            slug="related",
            depth=1,
            path_cache="related",
        )
        article = self.create_article(
            "Bulk Every Edit Action",
            owner=superuser,
            assign_primary_category=False,
        )

        edit_cases = (
            ("set_primary_journal", {"primary_journal": target_journal.pk}),
            ("set_primary_category", {"category": main_category.pk}),
            ("add_category", {"category": related_category.pk}),
            ("set_article_type", {"article_type": ArticlePage.ArticleType.OPINION}),
            ("submit_review", {}),
        )
        for action, params in edit_cases:
            with self.subTest(action=action):
                result = execute_bulk_article_action(
                    user=superuser,
                    article_ids=[article.pk],
                    action=action,
                    params=params,
                )
                self.assertEqual(result.success_count, 1)
                self.assertEqual(result.failure_count, 0)
                article.refresh_from_db()

        self.assertEqual(article.primary_journal_id, target_journal.pk)
        self.assertEqual(article.article_type, ArticlePage.ArticleType.OPINION)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertEqual(
            set(article.category_assignments.values_list("category_id", "is_primary")),
            {(main_category.pk, True), (related_category.pk, False)},
        )

        approve_result = execute_bulk_article_action(
            user=superuser,
            article_ids=[article.pk],
            action="approve",
            comment="?????",
            expected_revisions={article.pk: article.get_latest_revision().pk},
        )
        self.assertEqual(approve_result.success_count, 1)
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.APPROVED)

        rejected_article = self.create_article(
            "Bulk Reject Action",
            owner=superuser,
        )
        rejected_article.submit_for_review(superuser, "?????")
        reject_result = execute_bulk_article_action(
            user=superuser,
            article_ids=[rejected_article.pk],
            action="reject",
            comment="?????",
            expected_revisions={
                rejected_article.pk: rejected_article.get_latest_revision().pk
            },
        )
        self.assertEqual(reject_result.success_count, 1)
        rejected_article.refresh_from_db()
        self.assertEqual(
            rejected_article.review_status, ArticlePage.ReviewStatus.REJECTED
        )

    def test_bulk_action_view_uses_action_specific_category_fields(self):
        superuser = get_user_model().objects.create_superuser(
            username="bulk-category-view",
            email="bulk-category-view@example.com",
            password="test-password",
        )
        article = self.create_article("Bulk Category View", owner=superuser)
        additional_category = JournalCategory.objects.create(
            journal=self.journal,
            name="Additional",
            code="ADDITIONAL",
            slug="additional",
            depth=1,
            path_cache="additional",
        )
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("article_admin:bulk_action"),
            {
                "action": "add_category",
                "article_ids[]": [article.pk],
                "primary_category": "",
                "additional_category": additional_category.pk,
            },
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success_count"], 1)
        self.assertTrue(
            article.category_assignments.filter(
                category=additional_category, is_primary=False
            ).exists()
        )

    def test_bulk_action_template_avoids_named_form_action_collision(self):
        superuser = get_user_model().objects.create_superuser(
            username="bulk-template",
            email="bulk-template@example.com",
            password="test-password",
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse("article_admin:index"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('form.getAttribute("action")', content)
        self.assertNotIn("fetch(form.action", content)
        self.assertIn('name="primary_category"', content)
        self.assertIn('name="additional_category"', content)

    def test_bulk_action_rejects_more_than_one_hundred_articles(self):
        with self.assertRaisesMessage(ValidationError, "一次最多处理 100 篇文章"):
            execute_bulk_article_action(
                user=self.editor,
                article_ids=range(1, 102),
                action="set_article_type",
                params={"article_type": ArticlePage.ArticleType.NEWS},
            )

    def test_bulk_action_keeps_success_when_another_item_is_missing(self):
        superuser = get_user_model().objects.create_superuser(
            username="bulk-superuser",
            email="bulk-superuser@example.com",
            password="test-password",
        )
        article = self.create_article("Bulk Partial Failure")

        result = execute_bulk_article_action(
            user=superuser,
            article_ids=[article.pk, 999999],
            action="set_article_type",
            params={"article_type": ArticlePage.ArticleType.OPINION},
        )

        article.refresh_from_db()
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(article.article_type, ArticlePage.ArticleType.OPINION)
        self.assertTrue(result.items[0].success)
        self.assertFalse(result.items[1].success)
        self.assertIn("文章不存在或已删除", result.items[1].message)

    def test_bulk_review_rejects_missing_expected_revision(self):
        for action in ("approve", "reject"):
            with self.subTest(action=action):
                article = self.create_article(f"Bulk Missing Revision {action}")
                article.submit_for_review(self.editor, "请审核。")

                result = execute_bulk_article_action(
                    user=self.reviewer,
                    article_ids=[article.pk],
                    action=action,
                    comment="审核意见。",
                    expected_revisions={},
                )

                article.refresh_from_db()
                self.assertEqual(result.success_count, 0)
                self.assertEqual(result.failure_count, 1)
                self.assertIn(
                    "批量审核必须提供 expected revision",
                    result.items[0].message,
                )
                self.assertEqual(
                    article.review_status, ArticlePage.ReviewStatus.SUBMITTED
                )
                self.assertFalse(article.review_records.filter(action=action).exists())

    def test_bulk_review_rejects_invalid_expected_revision(self):
        for action in ("approve", "reject"):
            with self.subTest(action=action):
                article = self.create_article(f"Bulk Invalid Revision {action}")
                article.submit_for_review(self.editor, "请审核。")

                result = execute_bulk_article_action(
                    user=self.reviewer,
                    article_ids=[article.pk],
                    action=action,
                    comment="审核意见。",
                    expected_revisions={article.pk: "not-a-revision"},
                )

                article.refresh_from_db()
                self.assertEqual(result.success_count, 0)
                self.assertEqual(result.failure_count, 1)
                self.assertIn("expected revision 非法", result.items[0].message)
                self.assertEqual(
                    article.review_status, ArticlePage.ReviewStatus.SUBMITTED
                )
                self.assertFalse(article.review_records.filter(action=action).exists())

    def test_bulk_review_rejects_stale_expected_revision(self):
        for action in ("approve", "reject"):
            with self.subTest(action=action):
                article = self.create_article(f"Bulk Stale Revision {action}")
                article.submit_for_review(self.editor, "请审核。")
                stale_revision = article.get_latest_revision()
                article.title = f"Bulk Stale Revision Updated {action}"
                article.save_revision(
                    user=self.editor,
                    bypass_article_permission_check=True,
                )

                result = execute_bulk_article_action(
                    user=self.reviewer,
                    article_ids=[article.pk],
                    action=action,
                    comment="审核意见。",
                    expected_revisions={article.pk: stale_revision.pk},
                )

                article.refresh_from_db()
                self.assertEqual(result.success_count, 0)
                self.assertEqual(result.failure_count, 1)
                self.assertIn("revision 已变化", result.items[0].message)
                self.assertEqual(
                    article.review_status, ArticlePage.ReviewStatus.SUBMITTED
                )
                self.assertFalse(article.review_records.filter(action=action).exists())

    def test_bulk_review_accepts_matching_expected_revision(self):
        cases = (
            (
                "approve",
                ArticlePage.ReviewStatus.APPROVED,
                ArticleReviewRecord.Action.APPROVED,
            ),
            (
                "reject",
                ArticlePage.ReviewStatus.REJECTED,
                ArticleReviewRecord.Action.REJECTED,
            ),
        )
        for action, expected_status, expected_record_action in cases:
            with self.subTest(action=action):
                article = self.create_article(f"Bulk Current Revision {action}")
                article.submit_for_review(self.editor, "请审核。")
                expected_revision = article.get_latest_revision()

                result = execute_bulk_article_action(
                    user=self.reviewer,
                    article_ids=[article.pk],
                    action=action,
                    comment="审核意见。",
                    expected_revisions={article.pk: expected_revision.pk},
                )

                article.refresh_from_db()
                self.assertEqual(result.success_count, 1)
                self.assertEqual(result.failure_count, 0)
                self.assertTrue(result.items[0].success)
                self.assertEqual(article.review_status, expected_status)
                decision = article.review_records.get(action=expected_record_action)
                self.assertEqual(decision.revision_id, expected_revision.pk)
                self.assertEqual(decision.reviewer, self.reviewer)

    def test_new_article_form_starts_with_a_visual_paragraph(self):
        form_class = ArticlePage.get_edit_handler().get_form_class()
        form = form_class(
            instance=ArticlePage(),
            parent_page=self.root_page,
            for_user=self.editor,
        )

        initial_body = form.initial["body"]
        self.assertEqual(len(initial_body), 1)
        self.assertEqual(initial_body[0].block_type, "paragraph")
        self.assertEqual(str(initial_body[0].value), "")

    def test_raw_html_permission_is_enforced_by_service_and_page_form(self):
        body = [{"type": "html", "value": "<p>受控 HTML</p>"}]

        with self.assertRaisesMessage(ValidationError, "没有使用 Raw HTML"):
            validate_raw_html_permission(user=self.editor, body=body)
        validate_raw_html_permission(
            user=self.editor,
            body=body,
            original_body=body,
        )
        validate_raw_html_permission(
            user=self.editor,
            body=[],
            original_body=body,
        )
        with self.assertRaisesMessage(ValidationError, "没有使用 Raw HTML"):
            validate_raw_html_permission(
                user=self.editor,
                body=[{"type": "html", "value": "<p>被修改</p>"}],
                original_body=body,
            )
        with self.assertRaisesMessage(ValidationError, "没有使用 Raw HTML"):
            ArticlePageForm.clean_body(
                SimpleNamespace(for_user=self.editor, cleaned_data={"body": body})
            )

        superuser = get_user_model().objects.create_superuser(
            username="raw-html-superuser",
            email="raw-html-superuser@example.com",
            password="test-password",
        )
        validate_raw_html_permission(user=superuser, body=body)
        self.assertEqual(
            ArticlePageForm.clean_body(
                SimpleNamespace(for_user=superuser, cleaned_data={"body": body})
            ),
            body,
        )

    def test_review_decision_rejects_a_stale_revision(self):
        article = self.create_article("Revision Conflict")
        article.submit_for_review(self.editor, "请审核。")
        stale_revision = article.get_latest_revision()
        article.title = "Revision Conflict Updated"
        article.save_revision(
            user=self.editor,
            bypass_article_permission_check=True,
        )

        with self.assertRaises(ArticleRevisionConflict):
            article.approve(
                self.reviewer,
                "同意。",
                expected_revision_id=stale_revision.pk,
            )

        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertFalse(
            article.review_records.filter(
                action=ArticleReviewRecord.Action.APPROVED
            ).exists()
        )

    def test_article_image_fallback_order_and_templates_use_shared_helper(self):
        override = SimpleNamespace(title="投放覆盖图")
        featured = SimpleNamespace(title="文章封面")
        default = SimpleNamespace(title="站点默认图")
        article = SimpleNamespace(
            title="统一图片回退",
            featured_image=featured,
            featured_image_alt="文章封面替代文本",
        )
        placement = SimpleNamespace(
            override_image=override,
            override_image_alt="投放替代文本",
        )
        site_settings = SimpleNamespace(default_image=default)

        resolved = resolve_article_image(
            article,
            placement=placement,
            site_settings=site_settings,
        )
        self.assertIs(resolved.image, override)
        self.assertEqual(resolved.source, "placement")
        self.assertEqual(resolved.alt, "投放替代文本")

        resolved = resolve_article_image(article, site_settings=site_settings)
        self.assertIs(resolved.image, featured)
        self.assertEqual(resolved.source, "article")
        self.assertEqual(resolved.alt, "文章封面替代文本")

        article.featured_image = None
        article.featured_image_alt = ""
        resolved = resolve_article_image(article, site_settings=site_settings)
        self.assertIs(resolved.image, default)
        self.assertEqual(resolved.source, "site")
        self.assertEqual(resolved.alt, "站点默认图")

        resolved = resolve_article_image(
            article,
            site_settings=SimpleNamespace(default_image=None),
        )
        self.assertTrue(resolved.is_placeholder)
        self.assertEqual(resolved.source, "placeholder")
        self.assertEqual(resolved.alt, article.title)
        self.assertTrue(resolved.placeholder_url.endswith("/article-1.png"))

        template_root = Path(__file__).resolve().parents[3] / "templates"
        template_paths = (
            template_root / "articles" / "article_page.html",
            template_root / "components" / "placements" / "article-card.html",
            template_root / "components" / "canonical_article_card.html",
            template_root / "components" / "static_article_card.html",
        )
        for template_path in template_paths:
            source = template_path.read_text(encoding="utf-8")
            self.assertIn("{% article_image", source)
        detail_source = template_paths[0].read_text(encoding="utf-8")
        self.assertNotIn("{% static 'images/reference/article-1.png' %}", detail_source)


class ArticleWorkflowRoleMigrationTests(TestCase):
    def test_workflow_uses_business_reviewer_group_and_retires_legacy_access(self):
        from ai_author_forum.articles.wagtail_hooks import (
            ARTICLE_REVIEW_TASK_NAME,
            CONTENT_REVIEWERS_GROUP_NAME,
            LEGACY_CONTENT_REVIEWERS_GROUP_NAME,
            _get_or_create_article_workflow,
        )

        user = get_user_model().objects.create_user(
            username="legacy-reviewer",
            email="legacy-reviewer@example.com",
            password="test-password",
        )
        legacy_group, _ = Group.objects.get_or_create(
            name=LEGACY_CONTENT_REVIEWERS_GROUP_NAME
        )
        legacy_group.user_set.add(user)
        content_type = ContentType.objects.get_for_model(
            ArticlePage, for_concrete_model=False
        )
        article_permissions = [
            Permission.objects.get_or_create(
                content_type=content_type,
                codename=permission_name.split(".", maxsplit=1)[1],
                defaults={"name": permission_name},
            )[0]
            for permission_name in (
                ARTICLE_EDIT_PERMISSION,
                ARTICLE_REVIEW_PERMISSION,
                "articles.trigger_article_placement",
            )
        ]
        legacy_group.permissions.add(*article_permissions)
        GroupPagePermission.objects.get_or_create(
            group=legacy_group,
            page=Page.get_first_root_node(),
            permission=Permission.objects.get(
                content_type__app_label="wagtailcore", codename="add_page"
            ),
        )

        _get_or_create_article_workflow()

        reviewers = Group.objects.get(name=CONTENT_REVIEWERS_GROUP_NAME)
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(pk=reviewers.pk).exists())
        self.assertEqual(
            set(
                reviewers.permissions.filter(content_type=content_type).values_list(
                    "codename", flat=True
                )
            ),
            {ARTICLE_REVIEW_PERMISSION.split(".", maxsplit=1)[1]},
        )
        self.assertFalse(
            legacy_group.permissions.filter(
                pk__in=[item.pk for item in article_permissions]
            ).exists()
        )
        self.assertFalse(
            GroupPagePermission.objects.filter(group=legacy_group).exists()
        )
        task = ArticleReviewTask.objects.get(name=ARTICLE_REVIEW_TASK_NAME)
        self.assertEqual(list(task.groups.all()), [reviewers])
