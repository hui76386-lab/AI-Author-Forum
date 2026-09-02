import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ..comments import StalePolicy, create_comment
from ..models import ArticleCapabilityProjection, Comment, ReaderIdentity
from ..rate_limits import RateLimitDecision
from ..snapshots import rebuild_comment_snapshot


class AllowConcurrentLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(True)


@override_settings(READER_COMMENTS_WRITE_ENABLED=True)
@skipUnless(
    connections["interactions"].vendor == "postgresql",
    "Comment concurrency acceptance requires PostgreSQL.",
)
class CommentConcurrencyTests(TransactionTestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.article_id = uuid4()
        self.reader = ReaderIdentity.objects.create(
            email_ciphertext="ciphertext",
            email_lookup_hmac=(uuid4().hex + uuid4().hex),
            email_key_version=1,
            email_verified_at=timezone.now(),
            display_name="Concurrent Reader",
        )
        ArticleCapabilityProjection.objects.create(
            article_public_id=self.article_id,
            journal_id=42,
            active_release="release-concurrent",
            approved_revision_id=7,
            comments_mode=ArticleCapabilityProjection.CommentsMode.OPEN,
            policy_version=3,
            projection_version=3,
            applied_at=timezone.now(),
        )

    def tearDown(self):
        for connection in connections.all():
            connection.close()

    def test_same_idempotency_key_creates_exactly_one_comment(self):
        barrier = Barrier(2)

        def submit():
            connections.close_all()
            barrier.wait()
            try:
                result = create_comment(
                    article_public_id=self.article_id,
                    reader=ReaderIdentity.objects.get(pk=self.reader.pk),
                    body="One concurrent comment.",
                    expected_policy_version=3,
                    idempotency_key="concurrent-comment-key",
                    rate_limiter=AllowConcurrentLimiter(),
                )
                return result.replayed
            finally:
                connections.close_all()

        with (
            patch(
                "ai_author_forum.reader_interactions.comments.CapabilityDenyStore.get_desired",
                return_value=None,
            ),
            patch("ai_author_forum.reader_interactions.comments._public_change"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _index: submit(), range(2)))

        self.assertCountEqual(results, [False, True])
        self.assertEqual(
            Comment.objects.filter(article_public_id=self.article_id).count(), 1
        )

    def test_policy_close_commits_before_waiting_submission(self):
        projection_locked = Event()

        def close_policy():
            connections.close_all()
            try:
                with transaction.atomic(using="interactions"):
                    projection = (
                        ArticleCapabilityProjection.objects.select_for_update().get(
                            article_public_id=self.article_id
                        )
                    )
                    projection_locked.set()
                    projection.comments_mode = (
                        ArticleCapabilityProjection.CommentsMode.READ_ONLY
                    )
                    projection.policy_version = 4
                    projection.projection_version = 4
                    projection.applied_at = timezone.now()
                    projection.save(
                        using="interactions",
                        update_fields=(
                            "comments_mode",
                            "policy_version",
                            "projection_version",
                            "applied_at",
                        ),
                    )
            finally:
                connections.close_all()

        def submit():
            connections.close_all()
            projection_locked.wait(timeout=5)
            try:
                create_comment(
                    article_public_id=self.article_id,
                    reader=ReaderIdentity.objects.get(pk=self.reader.pk),
                    body="A comment racing policy closure.",
                    expected_policy_version=3,
                    idempotency_key="policy-race-key",
                    rate_limiter=AllowConcurrentLimiter(),
                )
                return "created"
            except StalePolicy:
                return "stale"
            finally:
                connections.close_all()

        with (
            patch(
                "ai_author_forum.reader_interactions.comments.CapabilityDenyStore.get_desired",
                return_value=None,
            ),
            patch("ai_author_forum.reader_interactions.comments._public_change"),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            close_future = executor.submit(close_policy)
            submit_future = executor.submit(submit)
            close_future.result()
            result = submit_future.result()

        self.assertEqual(result, "stale")
        self.assertFalse(
            Comment.objects.filter(article_public_id=self.article_id).exists()
        )

    def test_concurrent_snapshot_refresh_keeps_one_immutable_version(self):
        Comment.objects.create(
            article_public_id=self.article_id,
            journal_id=42,
            reader=self.reader,
            body_plaintext="Snapshot concurrency comment.",
            body_sha256="a" * 64,
            state=Comment.State.PUBLISHED,
            published_at=timezone.now(),
        )
        barrier = Barrier(2)

        def rebuild():
            connections.close_all()
            barrier.wait()
            try:
                return rebuild_comment_snapshot(self.article_id).version
            finally:
                connections.close_all()

        with tempfile.TemporaryDirectory() as directory:
            with (
                override_settings(READER_COMMENT_SNAPSHOT_ROOT=directory),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                versions = list(executor.map(lambda _index: rebuild(), range(2)))

        self.assertEqual(versions, [1, 1])
        self.assertEqual(self.reader.comments.count(), 1)
