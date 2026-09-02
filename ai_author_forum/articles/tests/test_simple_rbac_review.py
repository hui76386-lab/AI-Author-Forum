from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from wagtail.models import Page

from ai_author_forum.articles.models import (
    ArticleCategoryAssignment,
    ArticlePage,
    ArticleReviewRecord,
    ArticleRevisionConflict,
)
from ai_author_forum.articles.review_services import (
    ArticleStateConflict,
    claim_initial_review,
    final_review_article,
    has_valid_final_approval,
    initial_review_article,
    reassign_initial_review,
    reopen_rejected_article,
    submit_article_for_initial_review,
)
from ai_author_forum.articles.services import get_article_context
from ai_author_forum.articles.wagtail_hooks import (
    sync_article_status_on_workflow_approved,
)
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.access_control import (
    can_final_review,
    can_initial_review,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


class SimpleRbacReviewAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.User = get_user_model()
        cls.admin = cls.User.objects.create_user(
            username="review-platform-admin",
            email="review-platform-admin@example.com",
            display_name="Review Platform Admin",
            password="Review-admin-password-2026!",
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.journal_a = Journal.objects.create(
            name="Review Journal A",
            slug="review-journal-a",
            status=JournalStatus.ACTIVE,
            az_group="R",
        )
        cls.journal_b = Journal.objects.create(
            name="Review Journal B",
            slug="review-journal-b",
            status=JournalStatus.ACTIVE,
            az_group="R",
        )
        cls.category_a = JournalCategory.objects.create(
            journal=cls.journal_a,
            name="Review A",
            code="review-a",
            slug="review-a",
        )
        cls.category_b = JournalCategory.objects.create(
            journal=cls.journal_b,
            name="Review B",
            code="review-b",
            slug="review-b",
        )
        cls.chief_a = cls.make_user("review-chief-a")
        cls.executive_a = cls.make_user("review-executive-a")
        cls.associate_a = cls.make_user("review-associate-a")
        cls.associate_a2 = cls.make_user("review-associate-a2")
        cls.chief_b = cls.make_user("review-chief-b")
        cls.appoint(
            cls.chief_a,
            cls.journal_a,
            JournalEditorAssignment.Role.CHIEF_EDITOR,
        )
        cls.appoint(
            cls.executive_a,
            cls.journal_a,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        )
        for associate in (cls.associate_a, cls.associate_a2):
            cls.appoint(
                associate,
                cls.journal_a,
                JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
                [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE],
            )
        cls.appoint(
            cls.chief_b,
            cls.journal_b,
            JournalEditorAssignment.Role.CHIEF_EDITOR,
        )

    @classmethod
    def make_user(cls, username):
        return cls.User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username.replace("-", " ").title(),
            password="Review-editor-password-2026!",
            is_staff=True,
        )

    @classmethod
    def appoint(cls, user, journal, role, responsibilities=()):
        return appoint_journal_editor(
            actor=cls.admin,
            user=user,
            journal=journal,
            role=role,
            responsibilities=responsibilities,
            public_profile={
                "public_name": user.display_name,
                "public_affiliation": "Review Institute",
                "public_role_label": (
                    JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
                ),
                "display_order": 1,
                "show_publicly": True,
            },
        )

    def create_article(
        self,
        title="Review acceptance",
        *,
        journal=None,
        responsibility_statement="Authors accept responsibility.",
    ):
        journal = journal or self.journal_a
        category = self.category_a if journal == self.journal_a else self.category_b
        slug = f"{title.lower().replace(' ', '-')}-{uuid4().hex[:8]}"
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Review acceptance abstract",
            body=[("paragraph", "<p>Review acceptance body</p>")],
            authors="Named Author",
            keywords="review",
            responsibility_statement=responsibility_statement,
            article_type=ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            primary_journal=journal,
        )
        Page.get_first_root_node().add_child(instance=article)
        ArticleCategoryAssignment.objects.create(
            article=article,
            category=category,
            is_primary=True,
        )
        article.save_revision(
            user=self.chief_a if journal == self.journal_a else self.chief_b,
            bypass_article_permission_check=True,
        )
        return article

    def submit(self, article, *, actor=None):
        actor = actor or (
            self.chief_a
            if article.primary_journal_id == self.journal_a.pk
            else self.chief_b
        )
        revision = article.get_latest_revision()
        record = submit_article_for_initial_review(
            actor=actor,
            article=article,
            expected_state=ArticlePage.ReviewStatus.DRAFT,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
            comment="Ready for initial review.",
        )
        article.refresh_from_db()
        return revision, record

    def move_to_final(self, article, *, reviewer=None):
        reviewer = reviewer or self.executive_a
        revision, _ = self.submit(article)
        if reviewer in {self.associate_a, self.associate_a2}:
            claim_initial_review(
                actor=reviewer,
                article=article,
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
        initial_review_article(
            actor=reviewer,
            article=article,
            action="approve",
            comment="Initial review approved.",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        return revision

    def approve_formally(self, article):
        revision = self.move_to_final(article)
        record = final_review_article(
            actor=self.chief_a,
            article=article,
            action="approve",
            comment="Final review approved.",
            expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        return revision, record

    def test_associate_must_claim_before_initial_review_then_chief_finalizes(self):
        article = self.create_article("Associate claim flow")
        revision, _ = self.submit(article)
        self.assertFalse(can_initial_review(self.associate_a, article))
        with self.assertRaises(PermissionDenied):
            initial_review_article(
                actor=self.associate_a,
                article=article,
                action="approve",
                comment="",
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
        claim_initial_review(
            actor=self.associate_a,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertTrue(can_initial_review(self.associate_a, article))
        initial_review_article(
            actor=self.associate_a,
            article=article,
            action="approve",
            comment="",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.PENDING_FINAL)
        self.assertIsNone(article.approved_version_id)
        self.assertTrue(can_final_review(self.chief_a, article))
        final_review_article(
            actor=self.chief_a,
            article=article,
            action="approve",
            comment="",
            expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.APPROVED)
        self.assertEqual(article.approved_version_id, revision.pk)

    def test_chief_and_executive_can_initial_review_unassigned_article(self):
        for index, reviewer in enumerate((self.chief_a, self.executive_a), start=1):
            with self.subTest(reviewer=reviewer.username):
                article = self.create_article(f"Unassigned lead review {index}")
                revision, _ = self.submit(article)
                self.assertTrue(can_initial_review(reviewer, article))
                initial_review_article(
                    actor=reviewer,
                    article=article,
                    action="approve",
                    comment="",
                    expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                    expected_revision_id=revision.pk,
                    request_id=uuid4(),
                )
                article.refresh_from_db()
                self.assertEqual(
                    article.review_status, ArticlePage.ReviewStatus.PENDING_FINAL
                )

    def test_only_current_same_journal_chief_can_final_review(self):
        article = self.create_article("Final permission matrix")
        revision = self.move_to_final(article)
        for forbidden in (
            self.executive_a,
            self.associate_a,
            self.admin,
            self.chief_b,
        ):
            with self.subTest(user=forbidden.username):
                with self.assertRaises(PermissionDenied):
                    final_review_article(
                        actor=forbidden,
                        article=article,
                        action="approve",
                        comment="",
                        expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
                        expected_revision_id=revision.pk,
                        request_id=uuid4(),
                    )
        self.assertFalse(can_final_review(self.admin, article))
        final_review_article(
            actor=self.chief_a,
            article=article,
            action="approve",
            comment="",
            expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )

    def test_claim_is_first_writer_wins_idempotent_and_recovers_expired_assignment(
        self,
    ):
        article = self.create_article("Claim concurrency")
        revision, _ = self.submit(article)
        request_id = uuid4()
        first = claim_initial_review(
            actor=self.associate_a,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=request_id,
        )
        repeated = claim_initial_review(
            actor=self.associate_a,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=request_id,
        )
        self.assertEqual(first.pk, repeated.pk)
        with self.assertRaises(ArticleStateConflict):
            claim_initial_review(
                actor=self.associate_a2,
                article=article,
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
        self.assertEqual(
            AuditLog.objects.filter(
                target_id=str(article.pk),
                metadata__operation="claim_initial_review",
            ).count(),
            1,
        )

        assignment = JournalEditorAssignment.objects.get(
            user=self.associate_a,
            journal=self.journal_a,
            role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
        )
        JournalEditorAssignment.objects.filter(pk=assignment.pk).update(is_active=False)
        recovered = claim_initial_review(
            actor=self.associate_a2,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        self.assertEqual(recovered.assigned_initial_editor_id, self.associate_a2.pk)

    def test_reassign_requires_reason_and_writes_audit(self):
        article = self.create_article("Reassign review")
        revision, _ = self.submit(article)
        claim_initial_review(
            actor=self.associate_a,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        with self.assertRaises(ValidationError):
            reassign_initial_review(
                actor=self.executive_a,
                article=article,
                new_editor=self.associate_a2,
                reason="",
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
        reassign_initial_review(
            actor=self.executive_a,
            article=article,
            new_editor=self.associate_a2,
            reason="Balance editorial workload",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.assigned_initial_editor, self.associate_a2)
        self.assertTrue(
            AuditLog.objects.filter(
                target_id=str(article.pk),
                metadata__operation="reassign_initial_review",
                metadata__reason="Balance editorial workload",
            ).exists()
        )

    def test_stale_revision_fails_initial_and_final_review(self):
        article = self.create_article("Stale initial revision")
        revision, _ = self.submit(article)
        claim_initial_review(
            actor=self.associate_a,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.title = "Stale initial revision updated"
        article.save_revision(
            user=self.associate_a,
            bypass_article_permission_check=True,
        )
        with self.assertRaises(ArticleRevisionConflict):
            initial_review_article(
                actor=self.associate_a,
                article=article,
                action="approve",
                comment="",
                expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )

        final_article = self.create_article("Stale final revision")
        approved_initial_revision = self.move_to_final(final_article)
        final_article.title = "Stale final revision updated"
        final_article.save_revision(
            user=self.chief_a,
            bypass_article_permission_check=True,
        )
        with self.assertRaises(ArticleRevisionConflict):
            final_review_article(
                actor=self.chief_a,
                article=final_article,
                action="approve",
                comment="",
                expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
                expected_revision_id=approved_initial_revision.pk,
                request_id=uuid4(),
            )

    def test_duplicate_review_request_returns_first_record_without_new_audit(self):
        article = self.create_article("Idempotent initial review")
        revision, _ = self.submit(article)
        request_id = uuid4()
        first = initial_review_article(
            actor=self.executive_a,
            article=article,
            action="approve",
            comment="",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=request_id,
        )
        audit_count = AuditLog.objects.filter(request_id=str(request_id)).count()
        repeated = initial_review_article(
            actor=self.executive_a,
            article=article,
            action="approve",
            comment="",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=request_id,
        )
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(
            ArticleReviewRecord.objects.filter(request_id=request_id).count(), 1
        )
        self.assertEqual(
            AuditLog.objects.filter(request_id=str(request_id)).count(), audit_count
        )

    def test_review_record_is_immutable_and_projection_cannot_be_written_directly(self):
        article = self.create_article("Immutable review record")
        _, record = self.approve_formally(article)
        self.assertFalse(article.permissions_for_user(self.admin).can_delete())
        with self.assertRaises(ValidationError):
            ArticleReviewRecord.objects.filter(pk=record.pk).update(comment="changed")
        with self.assertRaises(ValidationError):
            record.delete()
        article.refresh_from_db()
        article.review_status = ArticlePage.ReviewStatus.PENDING_FINAL
        with self.assertRaises(ValidationError):
            article.save(user=self.chief_a)

    def test_approved_content_or_author_declaration_change_resets_review(self):
        article = self.create_article("Approved content reset")
        _, _ = self.approve_formally(article)
        approved_revision = article.approved_version
        article.responsibility_statement = "Updated author declaration."
        article.save(user=self.chief_a)
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertIsNone(article.approved_version_id)
        self.assertEqual(
            article.publication_status, ArticlePage.PublicationStatus.OFFLINE
        )
        self.assertFalse(has_valid_final_approval(article, approved_revision))

    def test_review_audit_failure_rolls_back_final_state_and_record(self):
        article = self.create_article("Final audit rollback")
        revision = self.move_to_final(article)
        with patch(
            "ai_author_forum.articles.review_services.AuditLog.record",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                final_review_article(
                    actor=self.chief_a,
                    article=article,
                    action="approve",
                    comment="",
                    expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
                    expected_revision_id=revision.pk,
                    request_id=uuid4(),
                )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.PENDING_FINAL)
        self.assertIsNone(article.approved_version_id)
        self.assertFalse(
            article.review_records.filter(
                action=ArticleReviewRecord.Action.FINAL_APPROVE
            ).exists()
        )

    def test_rejected_article_requires_reasoned_reopen_and_new_revision(self):
        article = self.create_article("Rejected reopen")
        revision, _ = self.submit(article)
        initial_review_article(
            actor=self.executive_a,
            article=article,
            action="reject",
            comment="Not suitable in current form.",
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        with self.assertRaises(ValidationError):
            reopen_rejected_article(
                actor=self.chief_a,
                article=article,
                reason="",
                expected_state=ArticlePage.ReviewStatus.REJECTED,
                expected_revision_id=revision.pk,
                request_id=uuid4(),
            )
        reopen_rejected_article(
            actor=self.chief_a,
            article=article,
            reason="Author supplied a corrected version.",
            expected_state=ArticlePage.ReviewStatus.REJECTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.review_status, ArticlePage.ReviewStatus.DRAFT)
        self.assertNotEqual(article.get_latest_revision().pk, revision.pk)

    def test_cross_journal_detail_is_404_and_forbidden_final_post_is_403(self):
        article = self.create_article("Cross journal detail")
        revision = self.move_to_final(article)
        detail_url = reverse("article_admin:review_detail", args=[article.pk])
        client = Client()
        client.force_login(self.chief_b)
        self.assertEqual(client.get(detail_url).status_code, 404)
        client.force_login(self.executive_a)
        response = client.post(
            detail_url,
            {
                "action": "approve",
                "expected_state": ArticlePage.ReviewStatus.PENDING_FINAL,
                "expected_revision_id": revision.pk,
                "request_id": uuid4(),
            },
        )
        self.assertEqual(response.status_code, 403)
        client.force_login(self.admin)
        response = client.post(
            detail_url,
            {
                "action": "approve",
                "expected_state": ArticlePage.ReviewStatus.PENDING_FINAL,
                "expected_revision_id": revision.pk,
                "request_id": uuid4(),
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_second_stale_review_post_returns_409_without_duplicate_record(self):
        article = self.create_article("Review post conflict")
        revision, _ = self.submit(article)
        claim_initial_review(
            actor=self.associate_a,
            article=article,
            expected_state=ArticlePage.ReviewStatus.SUBMITTED,
            expected_revision_id=revision.pk,
            request_id=uuid4(),
        )
        article.refresh_from_db()
        self.assertEqual(article.assigned_initial_editor_id, self.associate_a.pk)
        self.assertTrue(can_initial_review(self.associate_a, article))
        task_state = article.current_workflow_task_state
        self.assertIsNotNone(task_state)
        self.assertEqual(task_state.status, task_state.STATUS_IN_PROGRESS)
        client = Client()
        client.force_login(self.associate_a)
        detail_url = reverse("article_admin:review_detail", args=[article.pk])
        payload = {
            "action": "approve",
            "expected_state": ArticlePage.ReviewStatus.SUBMITTED,
            "expected_revision_id": revision.pk,
            "request_id": uuid4(),
        }
        self.assertEqual(client.post(detail_url, payload).status_code, 302)
        article.refresh_from_db()
        self.assertEqual(
            article.review_status,
            ArticlePage.ReviewStatus.PENDING_FINAL,
        )
        payload["request_id"] = uuid4()
        self.assertEqual(client.post(detail_url, payload).status_code, 409)
        self.assertEqual(
            article.review_records.filter(
                action=ArticleReviewRecord.Action.INITIAL_APPROVE
            ).count(),
            1,
        )

    def test_claim_reassign_and_final_list_routes_enforce_roles(self):
        article = self.create_article("Review action routes")
        revision, _ = self.submit(article)
        client = Client()
        client.force_login(self.associate_a)
        claim_url = reverse("article_admin:claim_review", args=[article.pk])
        response = client.post(
            claim_url,
            {
                "expected_state": ArticlePage.ReviewStatus.SUBMITTED,
                "expected_revision_id": revision.pk,
                "request_id": uuid4(),
            },
        )
        self.assertEqual(response.status_code, 302)
        client.force_login(self.associate_a2)
        response = client.post(
            claim_url,
            {
                "expected_state": ArticlePage.ReviewStatus.SUBMITTED,
                "expected_revision_id": revision.pk,
                "request_id": uuid4(),
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(client.get(reverse("article_admin:final")).status_code, 403)
        client.force_login(self.chief_a)
        self.assertEqual(client.get(reverse("article_admin:final")).status_code, 200)

    def test_workflow_approved_without_final_record_is_rejected_and_audited(self):
        article = self.create_article("Workflow approval guard")
        fake_workflow_state = SimpleNamespace(content_object=article, pk=99991)
        with self.assertRaises(PermissionDenied):
            sync_article_status_on_workflow_approved(
                sender=type(fake_workflow_state),
                instance=fake_workflow_state,
                user=self.chief_a,
            )
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="ArticlePage",
                target_id=str(article.pk),
                status="failure",
                message__contains="缺少同 revision 终审记录",
            ).exists()
        )

    def test_author_declaration_is_plain_text_in_preview_and_static_output(self):
        dangerous_declaration = (
            "<script>alert('x')</script>\nAuthors retain responsibility."
        )
        article = self.create_article(
            "Author declaration output",
            responsibility_statement=dangerous_declaration,
        )
        self.approve_formally(article)
        article.refresh_from_db()
        ArticlePlacement.objects.create(
            article=article,
            slot=LayoutSlot.objects.get(code="section_article_list"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug="research-analysis",
        )

        self.assertEqual(
            article._meta.get_field("responsibility_statement").verbose_name,
            "作者声明",
        )
        with self.assertRaises(FieldDoesNotExist):
            article._meta.get_field("author_declaration")

        request = RequestFactory().get("/admin/pages/preview/")
        request.user = self.chief_a
        preview_context = article.get_preview_context(request, "default")
        preview_context.setdefault("article_display_body", article.body)
        preview_context.setdefault("article_display_title", article.title)
        preview_context.setdefault("article_display_abstract", article.abstract)
        preview_context.setdefault("authors_text", article.authors)
        preview_context.setdefault("keywords", ["review"])
        preview_context.setdefault("contributors", ())
        preview_context.setdefault("related_categories", ())
        preview_context.setdefault("related_journals", ())
        preview_html = render_to_string(
            "articles/article_page.html",
            preview_context,
            request=request,
        )

        static_context = get_article_context(article.static_slug)
        self.assertEqual(
            preview_context["author_declaration"],
            static_context["author_declaration"],
        )
        target = next(
            target
            for target in WagtailPageTargetProvider().get_targets()
            if target.target_type == "article_page"
            and target.target_id == f"article:{article.pk}"
        )
        static_html = target.render().decode()

        for rendered in (preview_html, static_html):
            self.assertNotIn("<script>alert('x')</script>", rendered)
            self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", rendered)
            declaration_position = rendered.index('class="c-author-declaration"')
            team_position = rendered.index('class="c-editorial-team"')
            related_position = rendered.index('class="c-related"')
            self.assertLess(declaration_position, team_position)
            self.assertLess(team_position, related_position)

        self.assertEqual(
            target.output_path,
            f"articles/{article.static_slug}/index.html",
        )
        self.assertEqual(target.dependencies["article_ids"], [article.pk])
