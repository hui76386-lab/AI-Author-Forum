import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..capabilities import CapabilityStoreUnavailable
from ..comments import (
    AlreadyReported,
    CommentRateLimited,
    CommentsClosed,
    CommentsHidden,
    IdempotencyConflict,
    InvalidComment,
    ReaderSuspended,
    ReplyDepthExceeded,
    RiskAssessment,
    StalePolicy,
    assess_comment_risk,
    create_comment,
    list_comments,
    normalize_comment_body,
    report_comment,
    withdraw_comment,
)
from ..crypto import token_digest
from ..models import (
    ArticleCapabilityProjection,
    Comment,
    CommentModerationEvent,
    CommentReport,
    CommentSnapshot,
    ReaderIdentity,
    ReaderSession,
)
from ..rate_limits import RateLimitDecision
from ..snapshots import read_comment_snapshot, rebuild_comment_snapshot


class AllowCommentLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(True)


class DenyCommentLimiter:
    def check(self, dimensions, *, window_seconds):
        return RateLimitDecision(False, 17)


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_COMMENTS_WRITE_ENABLED=True,
    READER_COMMENT_RISK_ENABLED=True,
    READER_COMMENT_CACHE_REDIS_URL="",
)
class ReaderCommentServiceTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.article_id = uuid4()
        self.reader = self.make_reader("Reader One")
        self.other_reader = self.make_reader("Reader Two")
        ArticleCapabilityProjection.objects.create(
            article_public_id=self.article_id,
            journal_id=42,
            active_release="release-1",
            approved_revision_id=7,
            comments_mode=ArticleCapabilityProjection.CommentsMode.OPEN,
            download_enabled=False,
            policy_version=3,
            projection_version=3,
            applied_at=timezone.now(),
        )
        self.allow_limiter = AllowCommentLimiter()
        self.public_change = patch(
            "ai_author_forum.reader_interactions.comments._public_change"
        )
        self.public_change.start()
        self.capability_store = patch(
            "ai_author_forum.reader_interactions.comments.CapabilityDenyStore.get_desired",
            return_value=None,
        )
        self.capability_store.start()
        self.addCleanup(self.public_change.stop)
        self.addCleanup(self.capability_store.stop)

    def make_reader(self, name):
        return ReaderIdentity.objects.create(
            email_ciphertext="ciphertext",
            email_lookup_hmac=(uuid4().hex + uuid4().hex),
            email_key_version=1,
            email_verified_at=timezone.now(),
            display_name=name,
        )

    def create(
        self,
        reader=None,
        *,
        body="A useful comment.",
        key=None,
        parent=None,
        rate_limiter=None,
    ):
        return create_comment(
            article_public_id=self.article_id,
            reader=reader or self.reader,
            body=body,
            expected_policy_version=3,
            idempotency_key=key or f"key-{uuid4()}",
            parent_public_id=parent,
            rate_limiter=rate_limiter or self.allow_limiter,
        )

    def test_plaintext_normalization_rejects_controls_and_enforces_unicode_limits(self):
        self.assertEqual(
            normalize_comment_body(" <b>Cafe\u0301</b>\r\nnext\tline "),
            "Café\nnext line",
        )
        with self.assertRaises(InvalidComment):
            normalize_comment_body("a\x00comment")
        with self.assertRaises(InvalidComment):
            normalize_comment_body("x")
        with override_settings(READER_COMMENT_MAX_BYTES=4):
            with self.assertRaises(InvalidComment):
                normalize_comment_body("中文")

    def test_normal_comment_is_published_with_event_and_idempotent_replay(self):
        first = self.create(key="same-key")
        replay = self.create(key="same-key")
        self.assertEqual(first.status, 201)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.body, replay.body)
        comment = Comment.objects.get(public_id=first.body["id"])
        self.assertEqual(comment.state, Comment.State.PUBLISHED)
        self.assertEqual(
            CommentModerationEvent.objects.filter(comment=comment).count(), 1
        )
        self.assertEqual(replay.status, 201)
        with self.assertRaises(IdempotencyConflict):
            self.create(body="different payload", key="same-key")
        with self.assertRaises(InvalidComment):
            self.create(body="A useful comment.", key="duplicate-body-key")

    def test_risk_or_provider_failure_creates_pending_visible_only_to_author(self):
        # The deterministic built-in rule treats a link flood as high risk.
        pending = self.create(
            body="https://a.example https://b.example https://c.example https://d.example",
            key="risk-link-key",
        )
        self.assertEqual(pending.status, 202)
        self.assertEqual(pending.body["state"], Comment.State.PENDING)
        self.assertTrue(pending.body["pending_for_viewer"])
        anonymous = list_comments(article_public_id=self.article_id, cache=None)
        self.assertEqual(anonymous["items"], [])
        own = list_comments(
            article_public_id=self.article_id, viewer_reader=self.reader
        )
        self.assertEqual(len(own["items"]), 1)
        failing = assess_comment_risk(
            "safe", assessor=lambda _: (_ for _ in ()).throw(TimeoutError())
        )
        self.assertTrue(failing.pending)
        self.assertIn("risk_unavailable", failing.labels)
        with override_settings(READER_COMMENT_RISK_TIMEOUT_SECONDS=0.01):
            timed_out = assess_comment_risk(
                "slow",
                assessor=lambda _: (time.sleep(0.1), RiskAssessment())[1],
            )
        self.assertTrue(timed_out.pending)

    def test_replies_are_one_level_and_parent_must_be_published(self):
        root = self.create(key="root-key")
        reply = self.create(
            reader=self.other_reader,
            body="A reply.",
            key="reply-key",
            parent=root.body["id"],
        )
        self.assertEqual(reply.status, 201)
        with self.assertRaises(ReplyDepthExceeded):
            self.create(
                body="Nested reply.",
                key="nested-key",
                parent=reply.body["id"],
            )
        result = list_comments(article_public_id=self.article_id, cache=None)
        self.assertEqual([item["id"] for item in result["items"]], [root.body["id"]])
        self.assertEqual(result["items"][0]["replies"][0]["id"], reply.body["id"])

    def test_policy_modes_and_expected_version_fail_closed(self):
        projection = ArticleCapabilityProjection.objects.get(
            article_public_id=self.article_id
        )
        projection.comments_mode = ArticleCapabilityProjection.CommentsMode.READ_ONLY
        projection.save(using="interactions", update_fields=("comments_mode",))
        with self.assertRaises(CommentsClosed):
            self.create(key="closed-key")
        projection.comments_mode = ArticleCapabilityProjection.CommentsMode.HIDDEN
        projection.policy_version = 4
        projection.projection_version = 4
        projection.save(
            using="interactions",
            update_fields=("comments_mode", "policy_version", "projection_version"),
        )
        with self.assertRaises(CommentsHidden):
            list_comments(article_public_id=self.article_id, cache=None)
        with self.assertRaises(StalePolicy):
            self.create(key="stale-key")

    def test_withdrawal_keeps_placeholder_and_checks_owner_and_version(self):
        comment = self.create(key="withdraw-create")
        result = withdraw_comment(
            article_public_id=self.article_id,
            comment_public_id=comment.body["id"],
            reader=self.reader,
            expected_version=1,
            idempotency_key="withdraw-key",
        )
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.body["body"])
        self.assertTrue(result.body["withdrawn"])
        repeated = withdraw_comment(
            article_public_id=self.article_id,
            comment_public_id=comment.body["id"],
            reader=self.reader,
            expected_version=1,
            idempotency_key="withdraw-repeat",
        )
        self.assertTrue(repeated.body["withdrawn"])
        self.assertEqual(CommentModerationEvent.objects.count(), 2)
        public = list_comments(article_public_id=self.article_id, cache=None)
        self.assertTrue(public["items"][0]["withdrawn"])
        self.assertIsNone(public["items"][0]["body"])

    def test_report_is_idempotent_and_open_duplicate_is_rejected(self):
        comment = self.create(key="report-create")
        first = report_comment(
            article_public_id=self.article_id,
            comment_public_id=comment.body["id"],
            reader=self.other_reader,
            reason=CommentReport.Reason.SPAM,
            details="spam",
            idempotency_key="report-key",
            rate_limiter=self.allow_limiter,
        )
        replay = report_comment(
            article_public_id=self.article_id,
            comment_public_id=comment.body["id"],
            reader=self.other_reader,
            reason=CommentReport.Reason.SPAM,
            details="spam",
            idempotency_key="report-key",
            rate_limiter=self.allow_limiter,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(first.body, replay.body)
        with self.assertRaises(AlreadyReported):
            report_comment(
                article_public_id=self.article_id,
                comment_public_id=comment.body["id"],
                reader=self.other_reader,
                reason=CommentReport.Reason.SPAM,
                details="spam",
                idempotency_key="report-key-2",
                rate_limiter=self.allow_limiter,
            )

    def test_cursor_pagination_and_etag_are_stable(self):
        first = self.create(body="First page comment.", key="page-1")
        second = self.create(body="Second page comment.", key="page-2")
        page = list_comments(article_public_id=self.article_id, limit=1, cache=None)
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["next_cursor"])
        next_page = list_comments(
            article_public_id=self.article_id,
            cursor=page["next_cursor"],
            limit=1,
            cache=None,
        )
        self.assertEqual(
            [item["id"] for item in next_page["items"]], [second.body["id"]]
        )
        self.assertNotEqual(page["etag"], next_page["etag"])
        self.assertEqual(first.body["id"], page["items"][0]["id"])

    def test_snapshot_is_versioned_immutable_and_public_payload_has_no_email(self):
        self.create(key="snapshot-key")
        with tempfile.TemporaryDirectory() as directory:
            with override_settings(READER_COMMENT_SNAPSHOT_ROOT=directory):
                snapshot = rebuild_comment_snapshot(self.article_id)
                same = rebuild_comment_snapshot(self.article_id)
                self.assertEqual(snapshot.pk, same.pk)
                self.assertEqual(
                    CommentSnapshot.objects.filter(
                        article_public_id=self.article_id
                    ).count(),
                    1,
                )
                path = Path(directory) / snapshot.object_key
                self.assertTrue(path.exists())
                payload = json.loads(path.read_text())
                self.assertNotIn("email", json.dumps(payload).lower())
                read = read_comment_snapshot(self.article_id)
                self.assertEqual(read["snapshot_version"], 1)

    def test_sensitive_write_fails_closed_when_capability_or_rate_limit_redis_is_down(
        self,
    ):
        with patch(
            "ai_author_forum.reader_interactions.comments.CapabilityDenyStore.get_desired",
            side_effect=CapabilityStoreUnavailable("offline"),
        ):
            with self.assertRaisesRegex(Exception, "safety state"):
                self.create(key="capability-offline")
        with self.assertRaises(CommentRateLimited) as limited:
            self.create(key="rate-limited", rate_limiter=DenyCommentLimiter())
        self.assertEqual(limited.exception.retry_after, 17)

    def test_suspended_reader_is_rechecked_inside_write_transaction(self):
        self.reader.status = ReaderIdentity.Status.SUSPENDED
        self.reader.save(using="interactions", update_fields=("status",))
        with self.assertRaises(ReaderSuspended):
            self.create(key="suspended-reader")

    def test_hidden_policy_still_allows_author_to_withdraw_hidden_comment(self):
        created = self.create(key="hidden-withdraw-create")
        comment = Comment.objects.get(public_id=created.body["id"])
        comment.state = Comment.State.HIDDEN
        comment.save(using="interactions", update_fields=("state", "updated_at"))
        projection = ArticleCapabilityProjection.objects.get(
            article_public_id=self.article_id
        )
        projection.comments_mode = ArticleCapabilityProjection.CommentsMode.HIDDEN
        projection.save(using="interactions", update_fields=("comments_mode",))

        result = withdraw_comment(
            article_public_id=self.article_id,
            comment_public_id=comment.public_id,
            reader=self.reader,
            expected_version=1,
            idempotency_key="hidden-withdraw",
        )

        self.assertTrue(result.body["withdrawn"])


@override_settings(
    READER_INTERACTIONS_ENABLED=True,
    READER_COMMENTS_WRITE_ENABLED=True,
    READER_COMMENT_CACHE_REDIS_URL="",
)
class ReaderCommentApiTests(TestCase):
    databases = {"default", "interactions"}

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.get(reverse("reader_session"), secure=True)
        self.csrf = self.client.cookies["csrftoken"].value
        self.article_id = uuid4()
        self.reader = ReaderIdentity.objects.create(
            email_ciphertext="ciphertext",
            email_lookup_hmac=(uuid4().hex + uuid4().hex),
            email_key_version=1,
            email_verified_at=timezone.now(),
            display_name="API Reader",
        )
        self.secret = "session-secret"
        now = timezone.now()
        ReaderSession.objects.create(
            reader=self.reader,
            secret_hash=token_digest(self.secret),
            last_seen_at=now,
            idle_expires_at=now.replace(year=now.year + 1),
            absolute_expires_at=now.replace(year=now.year + 1),
        )
        self.client.cookies["reader_session"] = self.secret
        ArticleCapabilityProjection.objects.create(
            article_public_id=self.article_id,
            journal_id=42,
            active_release="release-1",
            approved_revision_id=7,
            comments_mode=ArticleCapabilityProjection.CommentsMode.OPEN,
            policy_version=3,
            projection_version=3,
            applied_at=now,
        )
        self.capability = patch(
            "ai_author_forum.reader_interactions.comments.CapabilityDenyStore.get_desired",
            return_value=None,
        )
        self.capability.start()
        self.public_change = patch(
            "ai_author_forum.reader_interactions.comments._public_change"
        )
        self.public_change.start()
        self.limiter = patch(
            "ai_author_forum.reader_interactions.comments.RedisAtomicRateLimiter",
            return_value=AllowCommentLimiter(),
        )
        self.limiter.start()
        self.addCleanup(self.capability.stop)
        self.addCleanup(self.public_change.stop)
        self.addCleanup(self.limiter.stop)

    def post_json(self, url, payload, **headers):
        headers.setdefault("HTTP_X_CSRFTOKEN", self.csrf)
        headers.setdefault("HTTP_ORIGIN", "https://testserver")
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            secure=True,
            **headers,
        )

    def test_comments_api_requires_csrf_for_write_and_supports_etag(self):
        url = reverse("reader_article_comments", args=[self.article_id])
        no_csrf = Client(enforce_csrf_checks=True).post(
            url,
            data=json.dumps({"body": "hello", "expected_policy_version": 3}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(no_csrf.status_code, 403)
        created = self.post_json(
            url,
            {"body": "hello", "expected_policy_version": 3},
            HTTP_IDEMPOTENCY_KEY="api-comment-1",
        )
        self.assertEqual(created.status_code, 201)
        listed = self.client.get(url, secure=True)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed["Cache-Control"], "no-store")
        self.assertTrue(listed["ETag"])
        not_modified = self.client.get(
            url, secure=True, HTTP_IF_NONE_MATCH=listed["ETag"]
        )
        self.assertEqual(not_modified.status_code, 304)

    @override_settings(READER_INTERNAL_SERVICE_TOKEN="internal-token")
    @patch(
        "ai_author_forum.reader_interactions.tasks.refresh_comment_snapshot.apply_async"
    )
    def test_internal_snapshot_rebuild_is_authenticated_and_queued(self, apply_async):
        url = reverse("reader_internal_comment_snapshot_rebuild")
        forbidden = self.client.post(
            url,
            data=json.dumps({"article_public_id": str(self.article_id)}),
            content_type="application/json",
            secure=True,
        )
        accepted = self.client.post(
            url,
            data=json.dumps({"article_public_id": str(self.article_id)}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer internal-token",
            secure=True,
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(accepted.status_code, 202)
        apply_async.assert_called_once_with(
            args=[str(self.article_id)],
            queue="reader_comments",
            argsrepr="(<redacted>,)",
        )
