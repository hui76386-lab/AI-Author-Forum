from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.users.middleware import record_credential_failure
from ai_author_forum.users.services import (
    JOURNAL_EDITOR_ACCESS_GROUP_NAME,
    SUPER_ADMIN_GROUP_NAME,
    create_account,
    deactivate_account,
    reset_account_password,
    revoke_super_admin,
    suspend_account,
)


class AccountAcceptanceTests(TestCase):
    admin_password = "Admin-confirmation-2026!"
    temporary_password = "Temporary-editor-2026!"

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.User = get_user_model()
        cls.admin = cls.User.objects.create_user(
            username="account-admin",
            email="account-admin@example.com",
            display_name="Account Administrator",
            password=cls.admin_password,
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.journal_a = Journal.objects.create(
            name="Account Journal A",
            slug="account-journal-a",
            status=JournalStatus.ACTIVE,
            az_group="A",
        )
        cls.journal_b = Journal.objects.create(
            name="Account Journal B",
            slug="account-journal-b",
            status=JournalStatus.ACTIVE,
            az_group="A",
        )

    def assignment_payload(self, journal, responsibility):
        return {
            "journal": journal,
            "role": JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            "responsibilities": [responsibility],
            "public_profile": {
                "public_name": "Named Editor",
                "public_affiliation": "Editorial Institute",
                "public_role_label": "副编辑",
                "display_order": 2,
                "show_publicly": True,
            },
        }

    def create_editor(self, username="named-editor"):
        return create_account(
            actor=self.admin,
            username=username,
            email=f"{username}@example.com",
            display_name="Named Editor",
            temporary_password=self.temporary_password,
            assignments=(
                self.assignment_payload(
                    self.journal_a,
                    JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
                ),
            ),
        )

    def test_super_admin_creates_named_editor_with_multiple_journal_roles(self):
        editor = create_account(
            actor=self.admin,
            username="multi-journal-editor",
            email="Multi-Journal@Example.com",
            display_name="Multi Journal Editor",
            institution="AI Institute",
            temporary_password=self.temporary_password,
            assignments=(
                self.assignment_payload(
                    self.journal_a,
                    JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
                ),
                self.assignment_payload(
                    self.journal_b,
                    JournalEditorAssignment.Responsibility.MEDIA_ASSETS,
                ),
            ),
        )

        self.assertEqual(editor.email, "multi-journal@example.com")
        self.assertTrue(editor.must_change_password)
        self.assertTrue(editor.is_staff)
        self.assertEqual(editor.journal_editor_assignments.count(), 2)
        self.assertTrue(
            editor.groups.filter(name=JOURNAL_EDITOR_ACCESS_GROUP_NAME).exists()
        )
        self.assertFalse(editor.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists())
        self.assertFalse(editor.has_perm("articles.change_articlepage"))
        self.assertFalse(editor.has_perm("journals.change_journal"))
        self.assertEqual(
            AuditLog.objects.filter(
                target_type="User", target_id=str(editor.pk)
            ).count(),
            1,
        )

    def test_creating_super_admin_requires_actor_password_confirmation(self):
        values = {
            "actor": self.admin,
            "username": "second-platform-admin",
            "email": "second-platform-admin@example.com",
            "display_name": "Second Platform Admin",
            "temporary_password": self.temporary_password,
            "is_super_admin_account": True,
        }
        with self.assertRaises(ValidationError):
            create_account(**values)
        created = create_account(
            **values,
            confirming_password=self.admin_password,
        )
        self.assertTrue(created.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists())

    def test_super_admin_creates_pure_author_without_editor_or_wagtail_access(self):
        author = create_account(
            actor=self.admin,
            username="pure-author-account",
            email="pure-author-account@example.com",
            display_name="Pure Author Account",
            temporary_password=self.temporary_password,
            is_author_account=True,
            assignments=(),
        )

        self.assertTrue(author.is_author)
        self.assertFalse(author.is_staff)
        self.assertTrue(author.must_change_password)
        self.assertFalse(author.journal_editor_assignments.exists())
        self.assertFalse(
            author.groups.filter(name=JOURNAL_EDITOR_ACCESS_GROUP_NAME).exists()
        )

    def test_non_super_admin_service_and_direct_url_are_forbidden(self):
        editor = self.create_editor("forbidden-editor")
        with self.assertRaises(PermissionDenied):
            create_account(
                actor=editor,
                username="not-created",
                email="not-created@example.com",
                display_name="Not Created",
                temporary_password=self.temporary_password,
                assignments=(
                    self.assignment_payload(
                        self.journal_a,
                        JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
                    ),
                ),
            )
        editor.must_change_password = False
        editor.save(update_fields=["must_change_password"])
        client = Client()
        client.force_login(editor)
        self.assertEqual(client.get("/admin/accounts/").status_code, 403)
        self.assertEqual(client.get("/admin/accounts/new/").status_code, 403)

    def test_email_is_case_insensitively_unique_and_cannot_be_empty(self):
        self.create_editor("case-email")
        with self.assertRaises(ValidationError):
            create_account(
                actor=self.admin,
                username="case-email-duplicate",
                email="CASE-EMAIL@EXAMPLE.COM",
                display_name="Duplicate Email",
                temporary_password=self.temporary_password,
                assignments=(
                    self.assignment_payload(
                        self.journal_b,
                        JournalEditorAssignment.Responsibility.MEDIA_ASSETS,
                    ),
                ),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.User.objects.create_user(
                username="empty-email",
                email="",
                display_name="Empty Email",
                password=self.temporary_password,
            )

    def test_first_login_is_restricted_until_password_change(self):
        editor = self.create_editor("first-login-editor")
        client = Client()
        client.force_login(editor)
        response = client.get("/admin/")
        self.assertRedirects(
            response,
            "/account/change-password/",
            fetch_redirect_response=False,
        )
        response = client.post(
            "/account/change-password/",
            {
                "old_password": self.temporary_password,
                "new_password1": "Changed-editor-password-2026!",
                "new_password2": "Changed-editor-password-2026!",
            },
        )
        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)
        editor.refresh_from_db()
        self.assertFalse(editor.must_change_password)
        self.assertTrue(editor.check_password("Changed-editor-password-2026!"))

    def test_first_login_password_change_can_load_wagtail_resources(self):
        editor = self.create_editor("first-login-assets-editor")
        client = Client()
        client.force_login(editor)

        for path in ("/admin/jsi18n/", "/admin/sprite/"):
            response = client.get(path)
            self.assertNotEqual(response.status_code, 302, path)

        response = client.post("/admin/jsi18n/")
        self.assertRedirects(
            response,
            "/account/change-password/",
            fetch_redirect_response=False,
        )
        response = client.get("/admin/articles/")
        self.assertRedirects(
            response,
            "/account/change-password/",
            fetch_redirect_response=False,
        )

    def test_editor_login_scope_returns_to_admin_after_neutral_password_change(self):
        editor = self.create_editor("scope-editor")
        client = Client()
        login_response = client.post(
            "/admin/login/",
            {"username": editor.username, "password": self.temporary_password},
        )
        self.assertRedirects(login_response, "/admin/", fetch_redirect_response=False)
        self.assertEqual(client.session.get("login_scope"), "admin")
        self.assertRedirects(
            client.get("/admin/"),
            "/account/change-password/",
            fetch_redirect_response=False,
        )
        change_response = client.post(
            "/account/change-password/",
            {
                "old_password": self.temporary_password,
                "new_password1": "Unrelated-editor-password-2026!",
                "new_password2": "Unrelated-editor-password-2026!",
            },
        )
        self.assertRedirects(change_response, "/admin/", fetch_redirect_response=False)
        self.assertIsNone(client.session.get("login_scope"))

    def test_deactivation_revokes_session_and_writes_reasoned_audit(self):
        editor = self.create_editor("deactivated-editor")
        client = Client()
        client.force_login(editor)
        session_key = client.session.session_key

        deactivate_account(actor=self.admin, user=editor, reason="Editor departed")

        editor.refresh_from_db()
        self.assertEqual(editor.account_status, self.User.AccountStatus.DEACTIVATED)
        self.assertFalse(editor.is_active)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        log = AuditLog.objects.filter(
            target_type="User", target_id=str(editor.pk)
        ).latest("created_at")
        self.assertEqual(log.metadata["reason"], "Editor departed")

    def test_reset_revokes_session_requires_first_change_and_never_audits_secret(self):
        editor = self.create_editor("reset-editor")
        editor.must_change_password = False
        editor.save(update_fields=["must_change_password"])
        client = Client()
        client.force_login(editor)
        session_key = client.session.session_key
        password = "Reset-editor-password-2026!"

        reset_account_password(
            actor=self.admin,
            user=editor,
            temporary_password=password,
        )

        editor.refresh_from_db()
        self.assertTrue(editor.must_change_password)
        self.assertTrue(editor.check_password(password))
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        serialized_logs = " ".join(
            f"{log.message} {log.metadata}" for log in AuditLog.objects.all()
        )
        self.assertNotIn(password, serialized_logs)
        self.assertNotIn(editor.password, serialized_logs)

    def test_resetting_super_admin_requires_actor_password_confirmation(self):
        with self.assertRaises(ValidationError):
            reset_account_password(
                actor=self.admin,
                user=self.admin,
                temporary_password="Reset-admin-password-2026!",
            )
        reset_account_password(
            actor=self.admin,
            user=self.admin,
            temporary_password="Reset-admin-password-2026!",
            confirming_password=self.admin_password,
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.must_change_password)

    def test_last_active_super_admin_cannot_be_suspended(self):
        with self.assertRaises(ValidationError):
            suspend_account(
                actor=self.admin,
                user=self.admin,
                reason="Unsafe lockout",
                confirming_password=self.admin_password,
            )
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.account_status, self.User.AccountStatus.ACTIVE)

    def test_last_active_super_admin_cannot_be_deactivated_or_revoked(self):
        with self.assertRaises(ValidationError):
            deactivate_account(
                actor=self.admin,
                user=self.admin,
                reason="Unsafe lockout",
                confirming_password=self.admin_password,
            )
        with self.assertRaises(ValidationError):
            revoke_super_admin(
                actor=self.admin,
                user=self.admin,
                confirming_password=self.admin_password,
            )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertTrue(self.admin.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists())

    def test_credential_limits_cover_login_reset_and_first_password_change(self):
        editor = self.create_editor("rate-limited-editor")
        ip_address = "198.51.100.42"
        for _ in range(5):
            response = self.client.post(
                "/admin/login/",
                {"username": editor.username, "password": "wrong-password"},
                REMOTE_ADDR=ip_address,
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/admin/login/",
            {"username": "different-user", "password": "wrong-password"},
            REMOTE_ADDR=ip_address,
        )
        self.assertEqual(response.status_code, 429)
        response = self.client.post(
            "/admin/login/",
            {"username": editor.username, "password": "wrong-password"},
            REMOTE_ADDR="198.51.100.43",
        )
        self.assertEqual(response.status_code, 429)

        for _ in range(5):
            record_credential_failure(
                "reset", editor.pk, ip_address, actor=self.admin, target=editor
            )
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/admin/accounts/{editor.pk}/reset-password/",
            {"temporary_password": "Another-temporary-password-2026!"},
            REMOTE_ADDR=ip_address,
        )
        self.assertEqual(response.status_code, 429)

        for _ in range(5):
            record_credential_failure(
                "first_change", editor.pk, ip_address, actor=editor, target=editor
            )
        self.client.force_login(editor)
        response = self.client.post(
            "/account/change-password/",
            {
                "old_password": self.temporary_password,
                "new_password1": "Changed-rate-limited-password-2026!",
                "new_password2": "Changed-rate-limited-password-2026!",
            },
            REMOTE_ADDR=ip_address,
        )
        self.assertEqual(response.status_code, 429)
        self.assertTrue(
            AuditLog.objects.filter(
                message="凭据验证连续失败，账号或来源 IP 进入短时冷却。",
                metadata__kind="login",
            ).exists()
        )

    def test_self_suspension_requires_another_admin_and_password(self):
        second = self.User.objects.create_user(
            username="other-active-admin",
            email="other-active-admin@example.com",
            display_name="Other Active Admin",
            password="Other-admin-password-2026!",
            is_staff=True,
        )
        second.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        with self.assertRaises(ValidationError):
            suspend_account(
                actor=self.admin,
                user=self.admin,
                reason="Planned handoff",
            )
        suspend_account(
            actor=self.admin,
            user=self.admin,
            reason="Planned handoff",
            confirming_password=self.admin_password,
        )
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.account_status, self.User.AccountStatus.SUSPENDED)

    def test_audit_failure_rolls_back_account_and_assignments(self):
        with patch(
            "ai_author_forum.journals.editor_services.AuditLog.record",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.create_editor("rolled-back-editor")
        self.assertFalse(
            self.User.objects.filter(username="rolled-back-editor").exists()
        )
        self.assertFalse(
            JournalEditorAssignment.objects.filter(
                user__username="rolled-back-editor"
            ).exists()
        )
