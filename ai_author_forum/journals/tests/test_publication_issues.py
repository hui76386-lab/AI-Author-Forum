from __future__ import annotations

from datetime import date
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PillowImage
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.issues import (
    archive_issue,
    publish_issue,
    rollback_issue,
    set_current_issue,
)
from ai_author_forum.journals.models import (
    IssueArticle,
    Journal,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider


def uploaded_image(name="issue-cover.png"):
    stream = BytesIO()
    PillowImage.new("RGB", (32, 24), "navy").save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class PublicationIssueTests(TestCase):
    def setUp(self):
        self.media_temporary = TemporaryDirectory()
        self.addCleanup(self.media_temporary.cleanup)
        media_override = override_settings(MEDIA_ROOT=self.media_temporary.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.actor = get_user_model().objects.create_superuser(
            username="issue-lead",
            email="issue-lead@example.com",
            password="password",
        )
        self.journal = Journal.objects.create(
            name="Issue Journal",
            slug="issue-journal",
            az_group="I",
        )
        self.other_journal = Journal.objects.create(
            name="Other Issue Journal",
            slug="other-issue-journal",
            az_group="O",
        )

    def create_article(
        self,
        *,
        title="Issue Article",
        journal=None,
        review_status=ArticlePage.ReviewStatus.APPROVED,
    ):
        slug = title.lower().replace(" ", "-")
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Issue article abstract",
            body=[("paragraph", "<p>Issue article body</p>")],
            authors="Issue author",
            keywords="issue",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=journal or self.journal,
            review_status=review_status,
        )
        Page.get_first_root_node().add_child(instance=article)
        article.save_revision().publish()
        return article

    def create_issue(self, **overrides):
        values = {
            "scope": PublicationIssueScope.MAIN_SITE,
            "journal": None,
            "slug": "volume-one",
            "title": "Volume one",
            "publication_date": date(2026, 7, 31),
            "status": PublicationIssueStatus.DRAFT,
            "is_current": False,
        }
        values.update(overrides)
        return PublicationIssue.objects.create(**values)

    def test_scope_and_current_status_database_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_issue(slug="main-with-journal", journal=self.journal)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_issue(
                slug="journal-without-journal",
                scope=PublicationIssueScope.JOURNAL,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_issue(slug="draft-current", is_current=True)

    def test_current_issue_is_unique_per_scope(self):
        self.create_issue(
            slug="main-current",
            status=PublicationIssueStatus.PUBLISHED,
            is_current=True,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_issue(
                slug="second-main-current",
                status=PublicationIssueStatus.PUBLISHED,
                is_current=True,
            )

        self.create_issue(
            slug="journal-current",
            scope=PublicationIssueScope.JOURNAL,
            journal=self.journal,
            status=PublicationIssueStatus.PUBLISHED,
            is_current=True,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_issue(
                slug="second-journal-current",
                scope=PublicationIssueScope.JOURNAL,
                journal=self.journal,
                status=PublicationIssueStatus.PUBLISHED,
                is_current=True,
            )
        other = self.create_issue(
            slug="other-journal-current",
            scope=PublicationIssueScope.JOURNAL,
            journal=self.other_journal,
            status=PublicationIssueStatus.PUBLISHED,
            is_current=True,
        )
        self.assertTrue(other.is_current)

    def test_issue_article_requires_reviewed_article_and_matching_journal(self):
        issue = self.create_issue(
            slug="journal-issue",
            scope=PublicationIssueScope.JOURNAL,
            journal=self.journal,
        )
        approved = self.create_article(title="Approved Issue Article")
        published = self.create_article(
            title="Published Issue Article",
            review_status=ArticlePage.ReviewStatus.PUBLISHED,
        )
        draft = self.create_article(
            title="Draft Issue Article",
            review_status=ArticlePage.ReviewStatus.DRAFT,
        )
        outside = self.create_article(
            title="Outside Issue Article",
            journal=self.other_journal,
        )

        IssueArticle(issue=issue, article=approved).full_clean()
        IssueArticle(issue=issue, article=published).full_clean()
        with self.assertRaisesMessage(ValidationError, "Only reviewed articles"):
            IssueArticle(issue=issue, article=draft).full_clean()
        with self.assertRaisesMessage(ValidationError, "owned by or related"):
            IssueArticle(issue=issue, article=outside).full_clean()

    def test_empty_or_invalid_issue_cannot_be_published_and_failure_is_audited(self):
        empty_issue = self.create_issue(slug="empty-issue")
        with self.assertRaisesMessage(ValidationError, "at least one approved article"):
            publish_issue(empty_issue, actor=self.actor)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(empty_issue.pk),
            ).exists()
        )

        invalid_issue = self.create_issue(
            slug="invalid-journal-issue",
            scope=PublicationIssueScope.JOURNAL,
            journal=self.journal,
        )
        outside = self.create_article(
            title="Invalid Published Issue Article",
            journal=self.other_journal,
        )
        IssueArticle.objects.create(issue=invalid_issue, article=outside)
        with self.assertRaisesMessage(ValidationError, "owned by or related"):
            publish_issue(invalid_issue, actor=self.actor)
        invalid_issue.refresh_from_db()
        self.assertEqual(invalid_issue.status, PublicationIssueStatus.DRAFT)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(invalid_issue.pk),
            ).exists()
        )

    def test_publish_current_archive_and_rollback_are_audited(self):
        issue = self.create_issue(slug="audited-issue")
        article = self.create_article(title="Audited Issue Article")
        assignment = IssueArticle.objects.create(issue=issue, article=article)
        assignment.full_clean()

        issue = publish_issue(issue, actor=self.actor)
        self.assertEqual(issue.status, PublicationIssueStatus.PUBLISHED)
        issue = set_current_issue(issue, actor=self.actor)
        self.assertTrue(issue.is_current)
        issue = archive_issue(issue, actor=self.actor)
        self.assertEqual(issue.status, PublicationIssueStatus.ARCHIVED)
        self.assertFalse(issue.is_current)
        issue = rollback_issue(issue, actor=self.actor)
        self.assertEqual(issue.status, PublicationIssueStatus.DRAFT)

        logs = AuditLog.objects.filter(
            status=AuditStatus.SUCCESS,
            target_id=str(issue.pk),
        )
        self.assertEqual(logs.filter(action=AuditAction.PUBLISH).count(), 1)
        self.assertEqual(logs.filter(action=AuditAction.CONFIGURE).count(), 2)
        self.assertEqual(logs.filter(action=AuditAction.ROLLBACK).count(), 1)

    def test_failed_current_archive_and_rollback_actions_are_audited(self):
        draft = self.create_issue(slug="failed-current")
        with self.assertRaisesMessage(ValidationError, "Only a published issue"):
            set_current_issue(draft, actor=self.actor)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.FAILURE,
                target_id=str(draft.pk),
            ).exists()
        )

        restricted_actor = get_user_model().objects.create_user(
            username="issue-restricted", password="password"
        )
        published = self.create_issue(
            slug="failed-protected-actions",
            status=PublicationIssueStatus.PUBLISHED,
        )
        with self.assertRaises(PermissionDenied):
            archive_issue(published, actor=restricted_actor)
        with self.assertRaises(PermissionDenied):
            rollback_issue(published, actor=restricted_actor)

        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.FAILURE,
                target_id=str(published.pk),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.ROLLBACK,
                status=AuditStatus.FAILURE,
                target_id=str(published.pk),
            ).exists()
        )

    def test_current_archive_and_detail_pages_render_real_issue_data(self):
        current = self.create_issue(
            slug="volume-two",
            title="Volume two",
            volume_label="Volume 2",
            issue_number="Issue 7",
            summary="Current issue summary",
            status=PublicationIssueStatus.PUBLISHED,
            is_current=True,
        )
        previous = self.create_issue(
            slug="volume-one-archive",
            title="Volume one archive",
            volume_label="Volume 1",
            issue_number="Issue 6",
            publication_date=date(2026, 6, 30),
            status=PublicationIssueStatus.PUBLISHED,
        )
        research = self.create_article(title="Issue research article")
        news = self.create_article(title="Issue news article")
        IssueArticle.objects.create(
            issue=current, article=research, section_label="Research", sort_order=1
        )
        IssueArticle.objects.create(
            issue=current, article=news, section_label="News", sort_order=2
        )
        IssueArticle.objects.create(
            issue=previous, article=research, section_label="Research", sort_order=1
        )
        slot = LayoutSlot.objects.get(code="column_list")
        for position, article in enumerate((research, news), start=1):
            ArticlePlacement.objects.create(
                article=article,
                slot=slot,
                target_type=ArticlePlacement.TargetType.SECTION,
                target_slug="ai-article",
                sort_order=position,
            )

        response = self.client.get(reverse("main_current_issue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, current.title)
        self.assertContains(response, current.volume_label)
        self.assertContains(response, current.issue_number)
        self.assertContains(response, "Research")
        self.assertContains(response, "News")
        self.assertContains(response, research.title)
        self.assertContains(response, news.title)
        self.assertContains(response, f'href="{previous.scope_path}"', html=False)
        self.assertContains(
            response, 'href="/explore-content/browse-issues/"', html=False
        )
        self.assertContains(response, 'id="toc-heading"', html=False)

        archive = self.client.get(reverse("main_issue_archive"))
        self.assertEqual(archive.status_code, 200)
        self.assertContains(archive, f'href="{current.scope_path}"', html=False)
        self.assertContains(archive, f'href="{previous.scope_path}"', html=False)

        detail = self.client.get(
            reverse("main_issue_detail", kwargs={"issue_slug": previous.slug})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, previous.title)
        self.assertContains(detail, research.title)
        canonical_url = research.get_absolute_url()
        self.assertContains(response, f'href="{canonical_url}"', html=False)
        self.assertContains(detail, f'href="{canonical_url}"', html=False)

        paths = {
            target.output_path for target in WagtailPageTargetProvider().get_targets()
        }
        self.assertIn("explore-content/current-issue/index.html", paths)
        self.assertIn("explore-content/browse-issues/index.html", paths)
        self.assertIn(f"issues/{current.slug}/index.html", paths)
        self.assertIn(f"issues/{previous.slug}/index.html", paths)

    def test_journal_issue_routes_render_current_archive_and_detail(self):
        issue = self.create_issue(
            slug="journal-volume-one",
            title="Journal volume one",
            scope=PublicationIssueScope.JOURNAL,
            journal=self.journal,
            status=PublicationIssueStatus.PUBLISHED,
            is_current=True,
        )
        article = self.create_article(title="Journal issue article")
        IssueArticle.objects.create(
            issue=issue, article=article, section_label="Articles", sort_order=1
        )

        current_url = reverse(
            "journal_current_issue", kwargs={"journal_slug": self.journal.slug}
        )
        archive_url = reverse(
            "journal_issue_archive", kwargs={"journal_slug": self.journal.slug}
        )
        detail_url = reverse(
            "journal_issue_detail",
            kwargs={"journal_slug": self.journal.slug, "issue_slug": issue.slug},
        )
        for url in (current_url, archive_url, detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, issue.title)

    def test_cover_and_issue_article_references_are_protected(self):
        cover = CustomImage.objects.create(title="Issue cover", file=uploaded_image())
        issue = self.create_issue(slug="protected-issue", cover_image=cover)
        article = self.create_article(title="Protected Issue Article")
        IssueArticle.objects.create(issue=issue, article=article)

        with self.assertRaises(ProtectedError):
            cover.delete()
        with self.assertRaises(ProtectedError):
            article.delete()

    def test_admin_permission_denied_redirects_instead_of_returning_500(self):
        issue = self.create_issue(
            slug="admin-restricted-issue",
            status=PublicationIssueStatus.PUBLISHED,
        )
        user = get_user_model().objects.create_user(
            username="issue-viewer", password="password", is_staff=True
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin", codename="access_admin"
            ),
            Permission.objects.get(
                content_type__app_label="journals",
                codename="view_publicationissue",
            ),
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("journals_publication_issue_admin"),
            {"issue_id": issue.pk, "action": "archive"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("journals_publication_issue_admin"))
        issue.refresh_from_db()
        self.assertEqual(issue.status, PublicationIssueStatus.PUBLISHED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.CONFIGURE,
                status=AuditStatus.FAILURE,
                target_id=str(issue.pk),
            ).exists()
        )

    def test_admin_missing_issue_redirects_instead_of_returning_500(self):
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("journals_publication_issue_admin"),
            {"issue_id": 999999, "action": "publish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("journals_publication_issue_admin"))

    def test_admin_validation_error_redirects_instead_of_returning_500(self):
        issue = self.create_issue(slug="admin-empty-issue")
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("journals_publication_issue_admin"),
            {"issue_id": issue.pk, "action": "publish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("journals_publication_issue_admin"))
        issue.refresh_from_db()
        self.assertEqual(issue.status, PublicationIssueStatus.DRAFT)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditAction.PUBLISH,
                status=AuditStatus.FAILURE,
                target_id=str(issue.pk),
            ).exists()
        )
