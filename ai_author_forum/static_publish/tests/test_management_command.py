from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class BuildStaticSiteCommandTests(SimpleTestCase):
    def test_rollback_requires_reason(self):
        with self.assertRaisesMessage(
            CommandError,
            "使用 --rollback 时必须提供至少 5 个字符的 --rollback-reason",
        ):
            call_command("build_static_site", rollback="release-one")

    @patch(
        "ai_author_forum.static_publish.management.commands.build_static_site.StaticPublisher"
    )
    def test_rollback_forwards_reason(self, publisher_class):
        publisher_class.return_value.rollback.return_value = SimpleNamespace(
            pk=9,
            status="rolled_back",
            version="release-one",
        )

        call_command(
            "build_static_site",
            rollback="release-one",
            rollback_reason="回滚到已验证的静态版本",
        )

        publisher_class.return_value.rollback.assert_called_once_with(
            "release-one",
            reason="回滚到已验证的静态版本",
        )
