from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from io import BytesIO, StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PillowImage
from wagtail.models import Page

from ai_author_forum.articles.author_services import (
    change_author_submission_journal,
    controlled_transfer_submission,
    create_author_submission,
    grant_article_authorship,
    save_author_submission,
    submit_author_submission,
)
from ai_author_forum.articles.models import (
    ArticleAuthorship,
    ArticleCategoryAssignment,
    ArticleContributor,
    ArticlePage,
    ArticleReviewRecord,
    AuthorSubmissionAsset,
    AuthorSubmissionOperation,
)
from ai_author_forum.articles.review_services import (
    ArticleRevisionConflict,
    ArticleStateConflict,
    final_review_article,
    initial_review_article,
)
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.access_control import (
    can_access_author_workbench,
    can_edit_submission,
    can_submit_submission,
    can_view_submission,
    filter_author_submissions,
)
from ai_author_forum.site_settings.models import AuditLog, AuditStatus
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


class AuthorSubmissionRoleAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.User = get_user_model()
        cls.admin = cls.User.objects.create_user(
            username="author-role-admin",
            email="author-role-admin@example.com",
            display_name="Author Role Admin",
            password="Author-role-admin-password-2026!",
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.author = cls.make_user("submission-owner", is_author=True)
        cls.other_author = cls.make_user("other-submission-owner", is_author=True)
        cls.co_author = cls.make_user("submission-coauthor", is_author=True)
        cls.chief_a = cls.make_user("chief-role-a", is_staff=True)
        cls.chief_b = cls.make_user("chief-role-b", is_staff=True)
        cls.journal_a = cls.make_journal("Author Journal A", "author-journal-a")
        cls.journal_b = cls.make_journal("Author Journal B", "author-journal-b")
        cls.closed_journal = cls.make_journal(
            "Closed Author Journal", "closed-author-journal"
        )
        cls.no_chief_journal = cls.make_journal("No Chief Journal", "no-chief-journal")
        cls.category_a = cls.make_category(cls.journal_a, "General A", "general-a")
        cls.category_b = cls.make_category(cls.journal_b, "General B", "general-b")
        cls.closed_category = cls.make_category(
            cls.closed_journal, "General Closed", "general-closed"
        )
        cls.no_chief_category = cls.make_category(
            cls.no_chief_journal, "General No Chief", "general-no-chief"
        )
        cls.appoint(cls.chief_a, cls.journal_a)
        cls.appoint(cls.chief_b, cls.journal_b)
        cls.appoint(cls.chief_a, cls.closed_journal)
        cls.journal_a.accepts_author_submissions = True
        cls.journal_a.save(update_fields=["accepts_author_submissions"])
        cls.journal_b.accepts_author_submissions = True
        cls.journal_b.save(update_fields=["accepts_author_submissions"])
        cls.closed_journal.accepts_author_submissions = True
        cls.closed_journal.save(update_fields=["accepts_author_submissions"])
        cls.no_chief_journal.accepts_author_submissions = True
        cls.no_chief_journal.save(update_fields=["accepts_author_submissions"])
        cls.bootstrap = cls.make_bootstrap_article()
        cls.bootstrap_authorship = ArticleAuthorship.objects.create(
            article=cls.bootstrap,
            user=cls.author,
            role=ArticleAuthorship.Role.OWNER,
            can_edit=True,
            is_corresponding=True,
            invited_by=cls.admin,
        )

    @classmethod
    def make_user(cls, username, *, is_author=False, is_staff=False):
        user = cls.User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username.replace("-", " ").title(),
            password="Author-test-password-2026!",
            is_author=is_author,
            is_staff=is_staff,
        )
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        return user

    @classmethod
    def make_journal(cls, name, slug):
        return Journal.objects.create(
            name=name, slug=slug, az_group="A", status=JournalStatus.ACTIVE
        )

    @classmethod
    def make_category(cls, journal, name, slug):
        return JournalCategory.objects.create(
            journal=journal, name=name, code=slug.upper().replace("-", "_"), slug=slug
        )

    @classmethod
    def appoint(cls, user, journal):
        return appoint_journal_editor(
            actor=cls.admin,
            user=user,
            journal=journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            responsibilities=[],
            public_profile={
                "public_name": user.display_name,
                "public_role_label": "主编辑",
            },
        )

    @classmethod
    def make_bootstrap_article(cls):
        article = ArticlePage(
            title="Bootstrap author article",
            slug="bootstrap-author-article",
            static_slug="bootstrap-author-article",
            abstract="Bootstrap abstract",
            body=[("paragraph", "<p>Bootstrap body</p>")],
            keywords="bootstrap",
            responsibility_statement="Bootstrap declaration",
            primary_journal=cls.journal_a,
        )
        Page.get_first_root_node().add_child(instance=article)
        ArticleContributor.objects.create(
            article=article,
            identity=ArticleContributor.Identity.AUTHOR,
            name=cls.author.display_name,
            is_corresponding=True,
            sort_order=0,
        )
        ArticleCategoryAssignment.objects.create(
            article=article, category=cls.category_a, is_primary=True
        )
        article.save_revision(user=cls.chief_a, bypass_article_permission_check=True)
        return article

    def fields(self, title="A complete author submission"):
        return {
            "title": title,
            "abstract": "A complete abstract.",
            "body": [("paragraph", "<p>A complete body.</p>")],
            "keywords": "author, submission",
            "responsibility_statement": "I accept responsibility for this submission.",
            "article_type": ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            "ai_co_authors": "",
            "ai_contribution_statement": "",
            "featured_image_alt": "",
        }

    def contributors(self):
        return [
            {
                "name": "Submission Owner",
                "affiliation": "Institute",
                "is_corresponding": True,
            }
        ]

    def form_payload(self, *, request_id, title="HTTP author submission"):
        return {
            "journal": self.journal_a.pk,
            "category": self.category_a.pk,
            "title": title,
            "abstract": "HTTP integration abstract.",
            "body_json": json.dumps(
                [
                    {
                        "id": "http-paragraph-1",
                        "type": "paragraph",
                        "html": "<p>HTTP integration body.</p>",
                    }
                ]
            ),
            "keywords": "http, author",
            "responsibility_statement": "HTTP integration declaration.",
            "article_type": ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            "ai_co_authors": "",
            "ai_contribution_statement": "",
            "featured_image_alt": "",
            "request_id": request_id,
            "contributors-TOTAL_FORMS": 1,
            "contributors-INITIAL_FORMS": 0,
            "contributors-MIN_NUM_FORMS": 1,
            "contributors-MAX_NUM_FORMS": 20,
            "contributors-0-name": "HTTP Submission Owner",
            "contributors-0-affiliation": "Integration Institute",
            "contributors-0-is_corresponding": "on",
        }

    def create_submission(
        self, *, journal=None, category=None, title="A complete author submission"
    ):
        journal = journal or self.journal_a
        category = category or (
            self.category_a if journal == self.journal_a else self.category_b
        )
        return create_author_submission(
            actor=self.author,
            journal=journal,
            category=category,
            fields=self.fields(title),
            contributors=self.contributors(),
            request_id=uuid4(),
        )

    def test_open_journals_require_active_chief_and_flag(self):
        from ai_author_forum.journals.submission_services import (
            public_submission_journals,
        )

        self.closed_journal.submission_closed_at = timezone.now()
        self.closed_journal.save(update_fields=["submission_closed_at"])
        self.assertEqual(
            set(public_submission_journals().values_list("pk", flat=True)),
            {self.journal_a.pk, self.journal_b.pk},
        )
        self.assertFalse(
            public_submission_journals().filter(pk=self.closed_journal.pk).exists()
        )
        self.assertFalse(
            public_submission_journals().filter(pk=self.no_chief_journal.pk).exists()
        )
        self.journal_b.submission_opened_at = timezone.now() + timedelta(days=1)
        self.journal_b.save(update_fields=["submission_opened_at"])
        self.assertEqual(
            set(public_submission_journals().values_list("pk", flat=True)),
            {self.journal_a.pk},
        )

    def test_forged_closed_or_missing_journal_create_request_fails(self):
        self.closed_journal.submission_closed_at = timezone.now()
        self.closed_journal.save(update_fields=["submission_closed_at"])
        client = Client()
        client.force_login(self.author)
        payload = {
            "journal": self.closed_journal.pk,
            "category": self.closed_category.pk,
            "title": "Closed journal request",
            "abstract": "Abstract",
            "body_text": "Body",
            "keywords": "closed",
            "responsibility_statement": "Declaration",
            "article_type": ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            "request_id": uuid4(),
            "contributors-TOTAL_FORMS": 1,
            "contributors-INITIAL_FORMS": 0,
            "contributors-MIN_NUM_FORMS": 1,
            "contributors-MAX_NUM_FORMS": 20,
            "contributors-0-name": "Owner",
            "contributors-0-affiliation": "Institute",
            "contributors-0-is_corresponding": "on",
        }
        self.assertEqual(client.post(reverse("author:new"), payload).status_code, 403)
        payload["journal"] = ""
        self.assertEqual(client.post(reverse("author:new"), payload).status_code, 400)
        payload["journal"] = 999999
        self.assertEqual(client.post(reverse("author:new"), payload).status_code, 400)

    def test_http_error_contract_and_form_failures_are_audited(self):
        article = self.create_submission(title="HTTP error contract")
        self.closed_journal.submission_closed_at = timezone.now()
        self.closed_journal.save(update_fields=["submission_closed_at"])
        client = Client()
        client.force_login(self.author)

        closed_request_id = uuid4()
        closed_response = client.post(
            reverse("author:change_journal", args=[article.pk]),
            {
                "target_journal": self.closed_journal.pk,
                "target_category": self.closed_category.pk,
                "expected_revision_id": article.get_latest_revision().pk,
                "request_id": closed_request_id,
                "confirmed": "on",
            },
        )
        self.assertEqual(closed_response.status_code, 403)
        self.assertTrue(
            AuditLog.objects.filter(
                request_id=str(closed_request_id),
                status=AuditStatus.FAILURE,
                metadata__operation_source="author_workbench",
                metadata__http_status=403,
            ).exists()
        )

        invalid_request_id = uuid4()
        invalid_response = client.post(
            reverse("author:submit", args=[article.pk]),
            {
                "expected_revision_id": article.get_latest_revision().pk,
                "request_id": invalid_request_id,
            },
        )
        self.assertEqual(invalid_response.status_code, 400)
        invalid_audit = AuditLog.objects.get(request_id=str(invalid_request_id))
        self.assertIn("confirmed", invalid_audit.metadata["invalid_fields"])

        conflict_request_id = uuid4()
        conflict_response = client.post(
            reverse("author:submit", args=[article.pk]),
            {
                "expected_revision_id": 999999,
                "request_id": conflict_request_id,
                "confirmed": "on",
            },
        )
        self.assertEqual(conflict_response.status_code, 409)
        self.assertTrue(
            AuditLog.objects.filter(
                request_id=str(conflict_request_id),
                status=AuditStatus.FAILURE,
                metadata__error_type="ArticleRevisionConflict",
            ).exists()
        )

        admin_client = Client()
        admin_client.force_login(self.chief_a)
        grant_request_id = uuid4()
        grant_response = admin_client.post(
            reverse("article_admin:authorships", args=[article.pk]),
            {
                "user": self.co_author.pk,
                "role": ArticleAuthorship.Role.OWNER,
                "request_id": grant_request_id,
            },
        )
        self.assertEqual(grant_response.status_code, 400)
        self.assertTrue(
            AuditLog.objects.filter(
                request_id=str(grant_request_id),
                status=AuditStatus.FAILURE,
                metadata__operation_source="editor_admin",
            ).exists()
        )

    def test_author_http_create_save_submit_and_lock_flow(self):
        client = Client()
        login_response = client.post(
            reverse("author:login"),
            {
                "username": self.author.username,
                "password": "Author-test-password-2026!",
            },
        )
        self.assertRedirects(
            login_response,
            reverse("author:dashboard"),
            fetch_redirect_response=False,
        )
        new_page = client.get(reverse("author:new"))
        self.assertEqual(new_page.status_code, 200)
        self.assertContains(new_page, "data-contributor-formset")
        self.assertContains(new_page, f'data-journal-id="{self.journal_a.pk}"')

        create_request_id = uuid4()
        create_response = client.post(
            reverse("author:new"),
            self.form_payload(request_id=create_request_id),
        )
        operation = AuthorSubmissionOperation.objects.get(request_id=create_request_id)
        article = operation.article
        self.assertRedirects(
            create_response,
            reverse("author:detail", args=[article.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            client.get(reverse("author:detail", args=[article.pk])).status_code, 200
        )

        revision = article.get_latest_revision()
        edit_payload = self.form_payload(
            request_id=uuid4(), title="HTTP author submission revised"
        )
        edit_payload.pop("journal")
        edit_payload["expected_revision_id"] = revision.pk
        edit_response = client.post(
            reverse("author:edit", args=[article.pk]), edit_payload
        )
        self.assertRedirects(
            edit_response,
            reverse("author:detail", args=[article.pk]),
            fetch_redirect_response=False,
        )
        article.refresh_from_db()
        self.assertEqual(article.title, "HTTP author submission revised")

        latest = article.get_latest_revision()
        submit_page = client.get(reverse("author:submit", args=[article.pk]))
        self.assertContains(submit_page, "必填项与资源检查")
        self.assertContains(submit_page, "已通过")
        submit_response = client.post(
            reverse("author:submit", args=[article.pk]),
            {
                "expected_revision_id": latest.pk,
                "request_id": uuid4(),
                "comment": "HTTP submission ready.",
                "confirmed": "on",
            },
        )
        self.assertRedirects(
            submit_response,
            reverse("author:detail", args=[article.pk]),
            fetch_redirect_response=False,
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertEqual(
            client.get(reverse("author:edit", args=[article.pk])).status_code, 409
        )

    def test_author_first_password_change_uses_neutral_route_and_author_scope(self):
        self.author.set_password("Author-temporary-login-2026!")
        self.author.must_change_password = True
        self.author.save(update_fields=["password", "must_change_password"])

        client = Client()
        login_response = client.post(
            reverse("author:login"),
            {
                "username": self.author.username,
                "password": "Author-temporary-login-2026!",
            },
        )
        self.assertRedirects(
            login_response,
            reverse("account:change_password"),
            fetch_redirect_response=False,
        )
        self.assertEqual(client.session.get("login_scope"), "author")
        change_page = client.get(reverse("account:change_password"))
        self.assertEqual(change_page.status_code, 200)
        self.assertNotContains(change_page, "wagtailadmin/base.html")

        change_response = client.post(
            reverse("account:change_password"),
            {
                "old_password": "Author-temporary-login-2026!",
                "new_password1": "Unrelated-new-author-password-2026!",
                "new_password2": "Unrelated-new-author-password-2026!",
            },
        )
        self.assertRedirects(
            change_response,
            reverse("author:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertIsNone(client.session.get("login_scope"))
        self.assertEqual(client.get(reverse("author:dashboard")).status_code, 200)
        self.assertEqual(client.get("/admin/").status_code, 403)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.author,
                message="完成首次强制密码修改。",
                metadata__operation_source="account_password_change",
                metadata__login_scope="author",
            ).exists()
        )

    def test_author_login_rejects_cross_workspace_and_external_next(self):
        self.author.set_password("Author-normal-login-2026!")
        self.author.must_change_password = False
        self.author.save(update_fields=["password", "must_change_password"])
        client = Client()
        login_page = client.get(
            reverse("author:login") + "?next=https://example.com/admin/"
        )
        self.assertNotContains(login_page, "example.com")
        response = client.post(
            reverse("author:login") + "?next=https://example.com/admin/",
            {
                "username": self.author.username,
                "password": "Author-normal-login-2026!",
            },
        )
        self.assertRedirects(
            response,
            reverse("author:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(client.session.get("login_scope"), "author")

    def test_editor_review_http_requires_separate_author_visible_reason(self):
        article = self.create_submission(title="Separate public review reason")
        revision = article.get_latest_revision()
        submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        client = Client()
        client.force_login(self.chief_a)
        review_url = reverse("article_admin:review_detail", args=[article.pk])
        missing_public = client.post(
            review_url,
            {
                "action": "return",
                "comment": "Internal review discussion.",
                "author_visible_comment": "",
                "expected_state": ArticlePage.ReviewStatus.SUBMITTED,
                "expected_revision_id": revision.pk,
                "request_id": uuid4(),
            },
        )
        self.assertEqual(missing_public.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)

        client.post(
            review_url,
            {
                "action": "return",
                "comment": "Internal review discussion.",
                "author_visible_comment": "Please revise the public data description.",
                "expected_state": ArticlePage.ReviewStatus.SUBMITTED,
                "expected_revision_id": revision.pk,
                "request_id": uuid4(),
            },
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        record = article.review_records.get(
            action=ArticleReviewRecord.Action.INITIAL_RETURN
        )
        self.assertEqual(record.comment, "Internal review discussion.")
        self.assertEqual(
            record.author_visible_comment,
            "Please revise the public data description.",
        )

    def test_create_save_and_submit_are_revision_scoped_and_idempotent(self):
        article = self.create_submission()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        latest = article.get_latest_revision()
        request_id = uuid4()
        revision = save_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=latest.pk,
            fields={**self.fields("Edited author submission")},
            contributors=self.contributors(),
            category=self.category_a,
            request_id=request_id,
        )
        self.assertEqual(
            save_author_submission(
                actor=self.author,
                article=article,
                expected_revision_id=latest.pk,
                fields=self.fields("Edited author submission"),
                contributors=self.contributors(),
                category=self.category_a,
                request_id=request_id,
            ).pk,
            revision.pk,
        )
        record = submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
            comment="Please review this submission.",
        )
        duplicate = submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=record.request_id,
            comment="ignored",
        )
        self.assertEqual(duplicate.pk, record.pk)
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertEqual(
            ArticleReviewRecord.objects.filter(
                article=article, action="submit"
            ).count(),
            1,
        )
        self.assertEqual(record.submission_owner.user_id, self.author.pk)
        self.assertEqual(record.submission_journal_id, self.journal_a.pk)
        self.assertEqual(len(record.content_sha256), 64)
        self.assertTrue(record.authorship_updated_at)

    def test_author_can_change_journal_only_before_first_submission(self):
        article = self.create_submission()
        latest = article.get_latest_revision()
        revision = change_author_submission_journal(
            actor=self.author,
            article=article,
            target_journal=self.journal_b,
            target_category=self.category_b,
            expected_revision_id=latest.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.primary_journal_id, self.journal_b.pk)
        self.assertEqual(article.get_latest_revision().pk, revision.pk)
        submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        with self.assertRaises(ArticleStateConflict):
            change_author_submission_journal(
                actor=self.author,
                article=article,
                target_journal=self.journal_a,
                target_category=self.category_a,
                expected_revision_id=article.get_latest_revision().pk,
                request_id=uuid4(),
            )

    def test_author_and_editor_transfers_reject_the_current_journal(self):
        article = self.create_submission()
        with self.assertRaises(ValidationError):
            change_author_submission_journal(
                actor=self.author,
                article=article,
                target_journal=self.journal_a,
                target_category=self.category_a,
                expected_revision_id=article.get_latest_revision().pk,
                request_id=uuid4(),
            )
        revision = article.get_latest_revision()
        submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        with self.assertRaises(ValidationError):
            controlled_transfer_submission(
                actor=self.chief_a,
                article=article,
                target_journal=self.journal_a,
                target_category=self.category_a,
                reason="This must not manufacture a same-journal transfer.",
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )

    def test_authorship_conflicts_are_validation_errors(self):
        article = self.create_submission()
        with self.assertRaises(ValidationError):
            grant_article_authorship(
                actor=self.chief_a,
                article=article,
                user=self.co_author,
                role=ArticleAuthorship.Role.OWNER,
                request_id=uuid4(),
            )
        with self.assertRaises(ValidationError):
            grant_article_authorship(
                actor=self.chief_a,
                article=article,
                user=self.co_author,
                role=ArticleAuthorship.Role.CO_AUTHOR,
                is_corresponding=True,
                request_id=uuid4(),
            )

    def test_coauthor_default_read_only_and_revocation_is_immediate(self):
        article = self.create_submission()
        relation = grant_article_authorship(
            actor=self.chief_a,
            article=article,
            user=self.co_author,
            role=ArticleAuthorship.Role.CO_AUTHOR,
            can_edit=False,
            request_id=uuid4(),
        )
        self.assertFalse(can_edit_submission(self.co_author, article))
        with self.assertRaises(PermissionDenied):
            save_author_submission(
                actor=self.co_author,
                article=article,
                expected_revision_id=article.get_latest_revision().pk,
                fields=self.fields("not allowed"),
                contributors=self.contributors(),
                category=self.category_a,
                request_id=uuid4(),
            )
        relation.can_edit = True
        relation.accepted_at = relation.accepted_at or timezone.now()
        relation.save()
        self.assertTrue(can_edit_submission(self.co_author, article))
        relation.revoked_at = timezone.now()
        relation.can_edit = False
        relation.save(update_fields=["revoked_at", "can_edit", "updated_at"])
        self.assertFalse(can_access_author_workbench(self.co_author))

    def test_submitted_is_locked_and_review_return_exposes_only_public_reason(self):
        article = self.create_submission()
        revision = article.get_latest_revision()
        submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        with self.assertRaises(ArticleStateConflict):
            save_author_submission(
                actor=self.author,
                article=article,
                expected_revision_id=revision.pk,
                fields=self.fields("locked"),
                contributors=self.contributors(),
                category=self.category_a,
                request_id=uuid4(),
            )
        returned = initial_review_article(
            actor=self.chief_a,
            article=article,
            action="return",
            comment="INTERNAL: the data table needs checking.",
            author_visible_comment="Please check the data table before resubmitting.",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        self.assertEqual(
            returned.author_visible_comment,
            "Please check the data table before resubmitting.",
        )
        self.client.force_login(self.author)
        self.assertNotContains(
            self.client.get(reverse("author:detail", args=[article.pk])), "INTERNAL"
        )

    def test_controlled_transfer_restarts_target_initial_review(self):
        article = self.create_submission()
        revision = article.get_latest_revision()
        submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        new_revision = controlled_transfer_submission(
            actor=self.chief_a,
            article=article,
            target_journal=self.journal_b,
            target_category=self.category_b,
            reason="The scope belongs to the other journal.",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.primary_journal_id, self.journal_b.pk)
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.SUBMITTED)
        self.assertIsNone(article.assigned_initial_editor_id)
        self.assertTrue(
            ArticleReviewRecord.objects.filter(
                article=article,
                action=ArticleReviewRecord.Action.TRANSFER,
                revision=new_revision,
            ).exists()
        )

    def test_author_scope_and_non_author_admin_boundaries(self):
        article = self.create_submission()
        other_article = self.create_submission(title="Other author submission")
        original_relation = other_article.authorships.get(
            role=ArticleAuthorship.Role.OWNER
        )
        original_relation.revoked_at = timezone.now()
        original_relation.can_edit = False
        original_relation.is_corresponding = False
        original_relation.save(
            update_fields=["revoked_at", "can_edit", "is_corresponding", "updated_at"]
        )
        ArticleAuthorship.objects.create(
            article=other_article,
            user=self.other_author,
            role=ArticleAuthorship.Role.OWNER,
            can_edit=True,
            is_corresponding=True,
            invited_by=self.admin,
        )
        client = Client()
        client.force_login(self.author)
        self.assertEqual(
            client.get(reverse("author:detail", args=[other_article.pk])).status_code,
            404,
        )
        self.assertEqual(
            client.get(reverse("author:detail", args=[article.pk])).status_code, 200
        )
        self.assertFalse(self.author.has_perm("articles.review_article"))
        self.assertFalse(self.author.has_perm("articles.trigger_article_placement"))
        self.assertEqual(client.get("/admin/articles/").status_code, 403)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.author,
                status=AuditStatus.FAILURE,
                metadata__operation_source="author_admin_boundary",
                metadata__path="/admin/articles/",
            ).exists()
        )

    def test_expected_revision_conflict_writes_failure_audit_and_no_partial_update(
        self,
    ):
        article = self.create_submission()
        before = article.title
        with self.assertRaises(ArticleRevisionConflict):
            save_author_submission(
                actor=self.author,
                article=article,
                expected_revision_id=999999,
                fields=self.fields("must not save"),
                contributors=self.contributors(),
                category=self.category_a,
                request_id=uuid4(),
            )
        article.refresh_from_db()
        self.assertEqual(article.title, before)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.author,
                status=AuditStatus.FAILURE,
                metadata__operation_source="author_workbench",
            ).exists()
        )

    def test_initial_and_final_review_keep_author_submission_out_of_publishing(self):
        article = self.create_submission()
        revision = article.get_latest_revision()
        submit_author_submission(
            actor=self.author,
            article=article,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        initial_review_article(
            actor=self.chief_a,
            article=article,
            action="approve",
            comment="Initial review complete.",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        final_review_article(
            actor=self.chief_a,
            article=article,
            action="approve",
            comment="Final review complete.",
            expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.APPROVED)
        self.assertEqual(
            article.publication_status, ArticlePage.PublicationStatus.APPROVED
        )
        self.assertFalse(article.placements.exists())

    def test_every_review_and_delivery_lock_state_blocks_author_edits(self):
        article = self.create_submission()
        for review_status in (
            ArticlePage.ReviewStatus.SUBMITTED,
            ArticlePage.ReviewStatus.PENDING_FINAL,
            ArticlePage.ReviewStatus.APPROVED,
        ):
            ArticlePage.objects.filter(pk=article.pk).update(
                review_status=review_status, publication_status=""
            )
            article.refresh_from_db()
            self.assertFalse(can_edit_submission(self.author, article), review_status)
            self.assertFalse(can_submit_submission(self.author, article), review_status)
        for publication_status in (
            ArticlePage.PublicationStatus.PLACED,
            ArticlePage.PublicationStatus.BUILT,
            ArticlePage.PublicationStatus.PUBLISHED,
        ):
            ArticlePage.objects.filter(pk=article.pk).update(
                review_status=ArticlePage.ReviewStatus.DRAFT,
                publication_status=publication_status,
            )
            article.refresh_from_db()
            self.assertFalse(
                can_edit_submission(self.author, article), publication_status
            )

    def test_account_deactivation_relation_revocation_and_journal_archive_remove_access(
        self,
    ):
        self.assertTrue(can_access_author_workbench(self.author))
        self.author.is_active = False
        self.author.account_status = self.author.AccountStatus.DEACTIVATED
        self.author.save(update_fields=["is_active", "account_status"])
        self.assertFalse(can_access_author_workbench(self.author))

        self.author.is_active = True
        self.author.account_status = self.author.AccountStatus.ACTIVE
        self.author.save(update_fields=["is_active", "account_status"])
        self.journal_a.status = JournalStatus.ARCHIVED
        self.journal_a.save(update_fields=["status"])
        self.assertFalse(can_access_author_workbench(self.author))

    def test_archived_journal_submission_is_hidden_when_other_access_remains(self):
        archived_article = self.create_submission()
        active_article = self.create_submission(
            journal=self.journal_b,
            category=self.category_b,
            title="Still active journal submission",
        )
        self.journal_a.status = JournalStatus.ARCHIVED
        self.journal_a.save(update_fields=["status"])

        self.assertTrue(can_access_author_workbench(self.author))
        self.assertFalse(can_view_submission(self.author, archived_article))
        self.assertFalse(can_edit_submission(self.author, archived_article))
        self.assertEqual(
            list(
                filter_author_submissions(
                    self.author, ArticlePage.objects.all()
                ).values_list("pk", flat=True)
            ),
            [active_article.pk],
        )
        client = Client()
        client.force_login(self.author)
        self.assertEqual(
            client.get(
                reverse("author:detail", args=[archived_article.pk])
            ).status_code,
            404,
        )

    def test_cover_upload_uses_scoped_asset_validation_and_rolls_back_spoofed_file(
        self,
    ):
        image_bytes = BytesIO()
        PillowImage.new("RGB", (32, 24), "navy").save(image_bytes, format="PNG")
        upload = SimpleUploadedFile(
            "cover.png", image_bytes.getvalue(), content_type="image/png"
        )
        with (
            tempfile.TemporaryDirectory() as media_root,
            self.settings(MEDIA_ROOT=media_root),
        ):
            article = create_author_submission(
                actor=self.author,
                journal=self.journal_a,
                category=self.category_a,
                fields=self.fields("Submission with validated cover"),
                contributors=self.contributors(),
                cover_file=upload,
                request_id=uuid4(),
            )
            asset = AuthorSubmissionAsset.objects.get(
                article=article, kind=AuthorSubmissionAsset.Kind.COVER, is_active=True
            )
            self.assertEqual(asset.scan_status, AuthorSubmissionAsset.ScanStatus.CLEAN)
            self.assertEqual(asset.content_type, "image/png")
            self.assertEqual(len(asset.sha256), 64)
            self.assertEqual(
                asset.image.collection.name, f"Author submission {article.pk}"
            )

            article_count = ArticlePage.objects.count()
            bad_upload = SimpleUploadedFile(
                "spoofed.png", b"not an image", content_type="image/png"
            )
            with self.assertRaises(ValidationError):
                create_author_submission(
                    actor=self.author,
                    journal=self.journal_a,
                    category=self.category_a,
                    fields=self.fields("Spoofed image must roll back"),
                    contributors=self.contributors(),
                    cover_file=bad_upload,
                    request_id=uuid4(),
                )
            self.assertEqual(ArticlePage.objects.count(), article_count)

    def test_author_structured_body_persists_scoped_images_and_attachments(self):
        image_bytes = BytesIO()
        PillowImage.new("RGB", (32, 24), "navy").save(image_bytes, format="PNG")
        image_block_id = "body-image-1"
        document_block_id = "body-document-1"
        fields = self.fields("Structured author body")
        fields["body"] = [
            {
                "id": "body-paragraph-1",
                "type": "paragraph",
                "html": "<p>Controlled <strong>rich text</strong>.</p>",
            },
            {"id": "body-heading-1", "type": "heading", "text": "Findings"},
            {
                "id": image_block_id,
                "type": "image",
                "image_asset_id": None,
                "upload_key": f"body_image_{image_block_id}",
                "alt_text": "Evaluation chart",
                "caption": "A validated author image",
            },
            {
                "id": "body-quote-1",
                "type": "quote",
                "quote": "Evidence must remain reviewable.",
                "attribution": "Author",
            },
            {
                "id": "body-list-1",
                "type": "list",
                "list_type": "ordered",
                "items": ["<p>First item</p>", "<p>Second item</p>"],
            },
            {
                "id": "body-table-1",
                "type": "table",
                "data": [["Metric", "Value"], ["Coverage", "100%"]],
                "first_row_is_table_header": True,
                "first_col_is_header": False,
                "caption": "Acceptance metrics",
            },
            {
                "id": document_block_id,
                "type": "document",
                "document_asset_id": None,
                "upload_key": f"body_document_{document_block_id}",
                "link_text": "Download appendix",
                "description": "A validated author attachment.",
            },
        ]
        uploads = {
            f"body_image_{image_block_id}": SimpleUploadedFile(
                "inline.png", image_bytes.getvalue(), content_type="image/png"
            ),
            f"body_document_{document_block_id}": SimpleUploadedFile(
                "appendix.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n",
                content_type="application/pdf",
            ),
        }
        with (
            tempfile.TemporaryDirectory() as media_root,
            self.settings(MEDIA_ROOT=media_root),
        ):
            article = create_author_submission(
                actor=self.author,
                journal=self.journal_a,
                category=self.category_a,
                fields=fields,
                contributors=self.contributors(),
                body_uploads=uploads,
                request_id=uuid4(),
            )
            article.refresh_from_db()
            self.assertEqual(
                [block.block_type for block in article.body],
                ["paragraph", "heading", "image", "quote", "list", "table", "document"],
            )
            image_asset = AuthorSubmissionAsset.objects.get(
                article=article,
                kind=AuthorSubmissionAsset.Kind.INLINE_IMAGE,
                is_active=True,
            )
            document_asset = AuthorSubmissionAsset.objects.get(
                article=article,
                kind=AuthorSubmissionAsset.Kind.ATTACHMENT,
                is_active=True,
            )
            self.assertEqual(
                image_asset.scan_status, AuthorSubmissionAsset.ScanStatus.CLEAN
            )
            self.assertEqual(
                document_asset.scan_status, AuthorSubmissionAsset.ScanStatus.CLEAN
            )
            self.assertEqual(
                image_asset.image.collection.name, f"Author submission {article.pk}"
            )
            self.assertEqual(
                document_asset.document.collection.name,
                f"Author submission {article.pk}",
            )
            self.assertEqual(article.body[2].value["image"].pk, image_asset.image_id)
            self.assertEqual(
                article.body[6].value["document"].pk, document_asset.document_id
            )

            forged_fields = self.fields("Forged resource reference")
            forged_fields["body"] = [
                {
                    "id": "foreign-image",
                    "type": "image",
                    "image_asset_id": image_asset.image_id,
                    "upload_key": "",
                    "alt_text": "",
                    "caption": "",
                }
            ]
            with self.assertRaises(ValidationError):
                create_author_submission(
                    actor=self.author,
                    journal=self.journal_a,
                    category=self.category_a,
                    fields=forged_fields,
                    contributors=self.contributors(),
                    request_id=uuid4(),
                )

    def test_author_form_renders_safe_body_editor_without_raw_html_control(self):
        client = Client()
        client.force_login(self.author)
        response = client.get(reverse("author:new"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-body-editor")
        self.assertContains(response, 'data-add-body-block="paragraph"')
        self.assertContains(response, 'data-add-body-block="document"')
        self.assertNotContains(response, "Raw HTML（需权限）")

    def test_historical_authorship_report_is_read_only_and_uses_only_explicit_user_id(
        self,
    ):
        article = self.create_submission(title="Historical report candidate")
        relation = article.authorships.get(role=ArticleAuthorship.Role.OWNER)
        relation.revoked_at = timezone.now()
        relation.can_edit = False
        relation.is_corresponding = False
        relation.save(
            update_fields=["revoked_at", "can_edit", "is_corresponding", "updated_at"]
        )
        before = ArticleAuthorship.objects.count()
        output = StringIO()
        call_command(
            "report_author_authorship_migration",
            output_format="json",
            stdout=output,
        )
        report = json.loads(output.getvalue())
        self.assertTrue(report["read_only"])
        self.assertIn(
            article.pk,
            {row["article_id"] for row in report["unmapped_articles"]},
        )
        self.assertEqual(ArticleAuthorship.objects.count(), before)
