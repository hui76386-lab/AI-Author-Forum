import threading
from queue import Queue
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from wagtail.models import Page

from ai_author_forum.articles.models import (
    ArticleCategoryAssignment,
    ArticlePage,
    ArticleReviewRecord,
)
from ai_author_forum.articles.review_services import (
    ArticleStateConflict,
    final_review_article,
    initial_review_article,
    submit_article_for_initial_review,
)
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


@skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL is required to verify review row locks.",
)
class PostgreSQLReviewConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(
            username="concurrent-review-admin",
            email="concurrent-review-admin@example.com",
            display_name="Concurrent Review Admin",
            is_staff=True,
        )
        group, _ = Group.objects.get_or_create(name=SUPER_ADMIN_GROUP_NAME)
        self.admin.groups.add(group)
        self.journal = Journal.objects.create(
            name="Concurrent Review Journal",
            slug="concurrent-review-journal",
            status=JournalStatus.ACTIVE,
            az_group="C",
        )
        self.category = JournalCategory.objects.create(
            journal=self.journal,
            name="Concurrent Review",
            code="concurrent-review",
            slug="concurrent-review",
        )
        self.chief = self._make_editor(
            "concurrent-review-chief",
            JournalEditorAssignment.Role.CHIEF_EDITOR,
        )
        self.executive = self._make_editor(
            "concurrent-review-executive",
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        )
        self.article = ArticlePage(
            title="Concurrent review article",
            slug="concurrent-review-article",
            static_slug="concurrent-review-article",
            abstract="Concurrent review acceptance.",
            body=[("paragraph", "<p>Concurrent review body.</p>")],
            authors="Concurrent Author",
            keywords="concurrency",
            responsibility_statement="Authors retain responsibility.",
            article_type=ArticlePage.ArticleType.RESEARCH_ANALYSIS,
            primary_journal=self.journal,
        )
        Page.get_first_root_node().add_child(instance=self.article)
        ArticleCategoryAssignment.objects.create(
            article=self.article,
            category=self.category,
            is_primary=True,
        )
        self.revision = self.article.save_revision(
            user=self.chief,
            bypass_article_permission_check=True,
        )
        submit_article_for_initial_review(
            actor=self.chief,
            article=self.article,
            expected_state=ArticlePage.ReviewStatus.DRAFT,
            expected_revision_id=self.revision.pk,
            request_id=uuid4(),
            comment="Ready for concurrent review.",
        )

    def _make_editor(self, username, role):
        user = self.User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username.replace("-", " ").title(),
            is_staff=True,
        )
        appoint_journal_editor(
            actor=self.admin,
            user=user,
            journal=self.journal,
            role=role,
            responsibilities=[],
            public_profile={
                "public_name": user.display_name,
                "public_role_label": (
                    JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
                ),
                "show_publicly": True,
            },
        )
        return user

    def _run_concurrently(self, workers):
        barrier = threading.Barrier(len(workers))
        results = Queue()

        def run(worker):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                results.put(("success", worker()))
            except ArticleStateConflict as exc:
                results.put(("conflict", str(exc)))
            except Exception as exc:  # pragma: no cover - surfaced by assertion
                results.put(("unexpected_error", repr(exc)))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        return [results.get_nowait() for _ in workers]

    def test_concurrent_initial_and_final_reviews_each_write_one_result(self):
        article_id = self.article.pk
        revision_id = self.revision.pk

        def initial_worker(user_id):
            request_id = uuid4()

            def worker():
                return initial_review_article(
                    actor=self.User.objects.get(pk=user_id),
                    article=ArticlePage.objects.get(pk=article_id),
                    action="approve",
                    comment="Concurrent initial approval.",
                    expected_state=ArticlePage.ReviewStatus.SUBMITTED,
                    expected_revision_id=revision_id,
                    request_id=request_id,
                ).pk

            return worker

        initial_results = self._run_concurrently(
            [initial_worker(self.chief.pk), initial_worker(self.executive.pk)]
        )
        self.assertEqual(
            sorted(status for status, _value in initial_results),
            ["conflict", "success"],
        )
        self.assertEqual(
            ArticleReviewRecord.objects.filter(
                article_id=article_id,
                action=ArticleReviewRecord.Action.INITIAL_APPROVE,
            ).count(),
            1,
        )

        def final_worker():
            request_id = uuid4()

            def worker():
                return final_review_article(
                    actor=self.User.objects.get(pk=self.chief.pk),
                    article=ArticlePage.objects.get(pk=article_id),
                    action="approve",
                    comment="Concurrent final approval.",
                    expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
                    expected_revision_id=revision_id,
                    request_id=request_id,
                ).pk

            return worker

        final_results = self._run_concurrently([final_worker(), final_worker()])
        self.assertEqual(
            sorted(status for status, _value in final_results),
            ["conflict", "success"],
        )
        self.assertEqual(
            ArticleReviewRecord.objects.filter(
                article_id=article_id,
                action=ArticleReviewRecord.Action.FINAL_APPROVE,
            ).count(),
            1,
        )
        self.article.refresh_from_db()
        self.assertEqual(self.article.review_status, ArticlePage.ReviewStatus.APPROVED)
        self.assertEqual(self.article.approved_version_id, revision_id)
        self.assertEqual(
            AuditLog.objects.filter(
                target_type="ArticlePage",
                target_id=str(article_id),
                metadata__action__in=(
                    ArticleReviewRecord.Action.INITIAL_APPROVE,
                    ArticleReviewRecord.Action.FINAL_APPROVE,
                ),
            ).count(),
            2,
        )
