from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ai_author_forum.test_helpers import grant_business_super_admin


class BuildStaticSiteCommandTests(TestCase):
    def setUp(self):
        self.admin = grant_business_super_admin(
            get_user_model().objects.create_user(
                username="static-command-admin",
                email="static-command-admin@example.com",
                display_name="Static Command Admin",
                password="test-password",
                is_staff=True,
            )
        )

    def test_rollback_requires_reason(self):
        with self.assertRaisesMessage(
            CommandError,
            "使用 --rollback 时必须提供至少 5 个字符的 --rollback-reason",
        ):
            call_command(
                "build_static_site", rollback="release-one", actor=self.admin.username
            )

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
            actor=self.admin.username,
        )

        publisher_class.return_value.rollback.assert_called_once_with(
            "release-one",
            user=self.admin,
            reason="回滚到已验证的静态版本",
        )
