from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from django.test import SimpleTestCase, override_settings
from redis.exceptions import ConnectionError

from ..comments import _check_rate_limit
from ..rate_limits import RateLimitUnavailable, RedisAtomicRateLimiter


class ReaderRateLimitTests(SimpleTestCase):
    def test_all_dimensions_are_checked_in_one_lua_evaluation(self):
        limiter = RedisAtomicRateLimiter("redis://redis:6379/1", namespace="test")
        limiter._client = Mock()
        limiter._client.eval.return_value = [1, 0]

        decision = limiter.check(
            (("ip", "hashed-ip", 5), ("email", "hashed-email", 3)),
            window_seconds=3600,
        )

        self.assertTrue(decision.allowed)
        args = limiter._client.eval.call_args.args
        self.assertEqual(args[1], 2)
        self.assertFalse(any("192.0.2" in str(value) for value in args))
        self.assertTrue(all("{reader-rate-limit}" in key for key in args[2:4]))

    def test_redis_failure_is_reported_as_unavailable(self):
        limiter = RedisAtomicRateLimiter("redis://redis:6379/1")
        limiter._client = Mock()
        limiter._client.eval.side_effect = ConnectionError("offline")
        with self.assertRaises(RateLimitUnavailable):
            limiter.check((("ip", "hashed", 5),), window_seconds=60)

    def test_windowed_limits_are_checked_in_one_lua_evaluation(self):
        limiter = RedisAtomicRateLimiter("redis://redis:6379/1", namespace="comments")
        limiter._client = Mock()
        limiter._client.eval.return_value = [1, 0]

        decision = limiter.check_windowed(
            (
                ("reader", "hashed-reader", 1, 10),
                ("reader-hour", "hashed-reader", 20, 3600),
            )
        )

        self.assertTrue(decision.allowed)
        args = limiter._client.eval.call_args.args
        self.assertEqual(args[1], 2)
        self.assertEqual(args[4:], (1, 10, 20, 3600))

    @override_settings(
        READER_COMMENT_INTERVAL_SECONDS=7,
        READER_COMMENT_HOURLY_LIMIT=11,
        READER_COMMENT_DAILY_LIMIT=22,
        READER_COMMENT_ARTICLE_HOURLY_LIMIT=4,
        READER_COMMENT_IP_HOURLY_LIMIT=33,
    )
    def test_comment_limits_are_configured_multi_dimension_and_hash_ip(self):
        limiter = Mock()
        limiter.check_windowed.return_value.allowed = True
        reader = SimpleNamespace(public_id=uuid4())
        article_id = uuid4()

        _check_rate_limit(
            reader,
            action="comment",
            article_public_id=article_id,
            remote_address="192.0.2.44",
            rate_limiter=limiter,
        )

        dimensions = limiter.check_windowed.call_args.args[0]
        self.assertEqual(
            [item[2:] for item in dimensions],
            [(1, 7), (11, 3600), (22, 86400), (4, 3600), (33, 3600)],
        )
        self.assertNotIn("192.0.2.44", str(dimensions))
        self.assertIn(str(article_id), str(dimensions))
