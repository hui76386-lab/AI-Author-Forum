from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase
from wagtail.models import Locale, Page

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import Journal, JournalEditorAssignment
from ai_author_forum.reader_interactions.capabilities import apply_capability_projection
from ai_author_forum.reader_interactions.models import ArticleCapabilityProjection

from ..models import ArticleInteractionPolicy
from ..services import StalePolicy, update_article_policy


class ThreadDenyStore:
    lock = Lock()
    values = {}

    def set_many_desired(self, payloads):
        with self.lock:
            for payload in payloads:
                self.values[payload["article_public_id"]] = payload["policy_version"]


@skipUnless(
    connections["default"].vendor == "postgresql"
    and connections["interactions"].vendor == "postgresql",
    "Policy concurrency acceptance requires PostgreSQL for both databases.",
)
class PolicyConcurrencyTests(TransactionTestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.journal = Journal.objects.create(
            name="Concurrent Reader Policy",
            slug="concurrent-reader-policy",
            az_group="C",
        )
        self.editor = get_user_model().objects.create_user(
            username="concurrent-reader-editor",
            email="concurrent-reader-editor@example.com",
            display_name="Concurrent Reader Editor",
            is_staff=True,
        )
        JournalEditorAssignment.objects.create(
            user=self.editor,
            journal=self.journal,
            role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            responsibilities=[JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT],
            public_name="Concurrent Reader Editor",
            public_role_label="副主编",
            created_by=self.editor,
        )
        self.article = ArticlePage(
            title="Concurrent policy article",
            slug="concurrent-policy-article",
            static_slug="concurrent-policy-article",
            abstract="Concurrent policy abstract",
            body=[("paragraph", "Concurrent policy body")],
            authors="Concurrent Author",
            keywords="concurrency",
            responsibility_statement="Authors retain responsibility.",
            primary_journal=self.journal,
        )
        root = Page.get_first_root_node()
        if root is None:
            locale, _created = Locale.objects.get_or_create(
                language_code=settings.LANGUAGE_CODE
            )
            root = Page.add_root(
                instance=Page(title="Root", slug="root", locale=locale)
            )
        root.add_child(instance=self.article)

    def tearDown(self):
        for connection in connections.all():
            connection.close()

    def test_expected_version_allows_exactly_one_concurrent_policy_write(self):
        barrier = Barrier(2)

        def update(mode):
            connections.close_all()
            barrier.wait()
            try:
                update_article_policy(
                    actor=get_user_model().objects.get(pk=self.editor.pk),
                    article=ArticlePage.objects.select_related("primary_journal").get(
                        pk=self.article.pk
                    ),
                    expected_version=0,
                    comments_policy=mode,
                    pdf_download_policy="inherit",
                )
                return "applied"
            except StalePolicy:
                return "stale"
            finally:
                connections.close_all()

        with (
            patch(
                "ai_author_forum.reader_access.services.CapabilityDenyStore",
                return_value=ThreadDenyStore(),
            ),
            patch("ai_author_forum.reader_access.services._enqueue_projection"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(update, ("hidden", "read_only")))
        self.assertCountEqual(results, ["applied", "stale"])
        self.assertEqual(
            ArticleInteractionPolicy.objects.get(article=self.article).version, 1
        )


@skipUnless(
    connections["interactions"].vendor == "postgresql",
    "Projection concurrency acceptance requires PostgreSQL.",
)
class ProjectionConcurrencyTests(TransactionTestCase):
    databases = {"default", "interactions"}

    def tearDown(self):
        for connection in connections.all():
            connection.close()

    def test_projection_race_finishes_at_highest_version(self):
        article_id = uuid4()
        barrier = Barrier(2)

        def apply(version):
            connections.close_all()
            barrier.wait()
            try:
                return apply_capability_projection(
                    {
                        "article_public_id": str(article_id),
                        "journal_id": 1,
                        "active_release": "release-race",
                        "approved_revision_id": 1,
                        "comments_mode": "hidden" if version == 2 else "open",
                        "download_enabled": False,
                        "protected_artifact_public_id": None,
                        "policy_version": version,
                        "projection_version": version,
                    }
                )[0]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(apply, (1, 2)))
        projection = ArticleCapabilityProjection.objects.get(
            article_public_id=article_id
        )
        self.assertEqual(projection.projection_version, 2)
        self.assertEqual(projection.comments_mode, "hidden")
