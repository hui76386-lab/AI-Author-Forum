from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone, translation

from ai_author_forum.journals.editor_forms import AppointEditorForm, ReplaceEditorForm
from ai_author_forum.journals.editor_services import (
    appoint_journal_editor,
    end_journal_editor_assignment,
    replace_chief_editor,
    update_editor_assignment_profile,
    update_journal_profile,
)
from ai_author_forum.journals.frontend import get_public_editorial_team
from ai_author_forum.journals.models import (
    Journal,
    JournalEditorAssignment,
    JournalStatus,
)
from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_journals,
    get_journal_editor_assignment,
)
from ai_author_forum.site_settings.models import AuditLog
from ai_author_forum.users.services import (
    JOURNAL_EDITOR_ACCESS_GROUP_NAME,
    SUPER_ADMIN_GROUP_NAME,
    grant_super_admin,
    suspend_account,
)


class JournalEditorAccessAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.User = get_user_model()
        cls.admin = cls.User.objects.create_user(
            username="editor-access-admin",
            email="editor-access-admin@example.com",
            display_name="Editor Access Admin",
            password="Editor-access-admin-2026!",
            is_staff=True,
        )
        cls.admin.groups.add(Group.objects.get(name=SUPER_ADMIN_GROUP_NAME))
        cls.journal_a = Journal.objects.create(
            name="Editor Access Journal A",
            slug="editor-access-journal-a",
            status=JournalStatus.ACTIVE,
            az_group="E",
        )
        cls.journal_b = Journal.objects.create(
            name="Editor Access Journal B",
            slug="editor-access-journal-b",
            status=JournalStatus.ACTIVE,
            az_group="E",
        )

    def make_user(self, username):
        return self.User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            display_name=username.replace("-", " ").title(),
            password="Editor-password-2026!",
            is_staff=True,
        )

    def appoint(self, user, journal, role, responsibilities=()):
        return appoint_journal_editor(
            actor=self.admin,
            user=user,
            journal=journal,
            role=role,
            responsibilities=responsibilities,
            public_profile={
                "public_name": user.display_name,
                "public_affiliation": "Editorial Institute",
                "public_role_label": (
                    JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role]
                ),
                "display_order": 1,
                "show_publicly": True,
            },
        )

    def test_chief_and_executive_are_unique_and_associates_can_be_multiple(self):
        chief = self.make_user("unique-chief")
        executive = self.make_user("unique-executive")
        associate_a = self.make_user("associate-a")
        associate_b = self.make_user("associate-b")
        self.appoint(chief, self.journal_a, JournalEditorAssignment.Role.CHIEF_EDITOR)
        self.appoint(
            executive,
            self.journal_a,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        )
        for user in (associate_a, associate_b):
            self.appoint(
                user,
                self.journal_a,
                JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
                [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE],
            )
        with self.assertRaises(ValidationError):
            self.appoint(
                self.make_user("second-chief"),
                self.journal_a,
                JournalEditorAssignment.Role.CHIEF_EDITOR,
            )
        with self.assertRaises(ValidationError):
            self.appoint(
                self.make_user("second-executive"),
                self.journal_a,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
            )
        self.assertEqual(
            JournalEditorAssignment.objects.effective()
            .filter(
                journal=self.journal_a,
                role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            )
            .count(),
            2,
        )

    def test_associate_requires_at_least_one_fixed_responsibility(self):
        associate = self.make_user("no-responsibility-associate")
        with self.assertRaises(ValidationError):
            self.appoint(
                associate,
                self.journal_a,
                JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            )
        assignment = JournalEditorAssignment(
            user=associate,
            journal=self.journal_a,
            role=JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            responsibilities=["invented_responsibility"],
            public_name=associate.display_name,
            public_role_label="副编辑",
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_journal_scope_is_filtered_at_queryset_and_service_layers(self):
        editor = self.make_user("journal-a-profile-editor")
        self.appoint(
            editor,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.JOURNAL_PROFILE],
        )
        self.assertTrue(
            can_manage_journal(
                editor,
                self.journal_a,
                JournalEditorAssignment.Responsibility.JOURNAL_PROFILE,
            )
        )
        self.assertFalse(
            can_manage_journal(
                editor,
                self.journal_b,
                JournalEditorAssignment.Responsibility.JOURNAL_PROFILE,
            )
        )
        self.assertEqual(
            list(
                filter_accessible_journals(editor, Journal.objects.all()).values_list(
                    "pk", flat=True
                )
            ),
            [self.journal_a.pk],
        )

    def test_chief_updates_associate_responsibilities_but_cannot_manage_accounts(self):
        chief = self.make_user("team-chief")
        associate = self.make_user("team-associate")
        self.appoint(chief, self.journal_a, JournalEditorAssignment.Role.CHIEF_EDITOR)
        assignment = self.appoint(
            associate,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE],
        )
        update_editor_assignment_profile(
            actor=chief,
            assignment=assignment,
            responsibilities=[
                JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
                JournalEditorAssignment.Responsibility.MEDIA_ASSETS,
            ],
            public_profile={"display_order": 8, "show_publicly": True},
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.display_order, 8)
        self.assertIn(
            JournalEditorAssignment.Responsibility.MEDIA_ASSETS,
            assignment.responsibilities,
        )
        with self.assertRaises(PermissionDenied):
            grant_super_admin(actor=chief, user=associate)

    def test_profile_update_does_not_change_login_identity(self):
        associate = self.make_user("public-profile-editor")
        assignment = self.appoint(
            associate,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.MEDIA_ASSETS],
        )
        original_identity = (
            associate.username,
            associate.email,
            associate.display_name,
        )
        update_editor_assignment_profile(
            actor=associate,
            assignment=assignment,
            public_profile={
                "public_name": "Published Pen Name",
                "public_affiliation": "Public Affiliation",
                "public_role_label": "副主编",
            },
        )
        associate.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(
            (associate.username, associate.email, associate.display_name),
            original_identity,
        )
        self.assertEqual(assignment.public_name, "Published Pen Name")
        self.assertEqual(assignment.public_affiliation, "Public Affiliation")

    def test_replacement_ends_old_assignment_and_records_both_sides(self):
        old_chief = self.make_user("old-chief")
        new_chief = self.make_user("new-chief")
        old_assignment = self.appoint(
            old_chief, self.journal_a, JournalEditorAssignment.Role.CHIEF_EDITOR
        )
        new_assignment = replace_chief_editor(
            actor=self.admin,
            journal=self.journal_a,
            new_user=new_chief,
            reason="Scheduled handoff",
        )
        old_assignment.refresh_from_db()
        self.assertFalse(old_assignment.is_active)
        self.assertEqual(old_assignment.replaced_by_assignment, new_assignment)
        self.assertEqual(old_assignment.end_reason, "Scheduled handoff")
        self.assertEqual(
            JournalEditorAssignment.objects.effective()
            .filter(
                journal=self.journal_a,
                role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            )
            .count(),
            1,
        )
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="JournalEditorAssignment",
                target_id__in=(str(old_assignment.pk), str(new_assignment.pk)),
            ).count()
            >= 2
        )

    def test_ending_one_of_multiple_assignments_revokes_session_and_audits(self):
        editor = self.make_user("multi-journal-session-editor")
        first = self.appoint(
            editor,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE],
        )
        self.appoint(
            editor,
            self.journal_b,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.MEDIA_ASSETS],
        )
        self.client.force_login(editor)
        session_key = self.client.session.session_key

        ended = end_journal_editor_assignment(
            actor=self.admin,
            assignment=first,
            reason="Move the editor to another journal.",
        )

        self.assertFalse(ended.is_active)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="JournalEditorAssignment",
                target_id=str(first.pk),
                metadata__sessions_revoked__gte=1,
            ).exists()
        )

    def test_replacement_audit_failure_rolls_back_old_and_new_assignments(self):
        old_chief = self.make_user("rollback-old-chief")
        new_chief = self.make_user("rollback-new-chief")
        old_assignment = self.appoint(
            old_chief, self.journal_a, JournalEditorAssignment.Role.CHIEF_EDITOR
        )
        original_record = AuditLog.record

        def fail_new_assignment_audit(*args, **kwargs):
            if kwargs.get("message") == "完成子期刊编辑角色交接。":
                raise RuntimeError("audit unavailable")
            return original_record(*args, **kwargs)

        with patch.object(AuditLog, "record", side_effect=fail_new_assignment_audit):
            with self.assertRaises(RuntimeError):
                replace_chief_editor(
                    actor=self.admin,
                    journal=self.journal_a,
                    new_user=new_chief,
                    reason="Must roll back",
                )
        old_assignment.refresh_from_db()
        self.assertTrue(old_assignment.is_active)
        self.assertIsNone(old_assignment.replaced_by_assignment_id)
        self.assertFalse(
            JournalEditorAssignment.objects.filter(
                user=new_chief,
                journal=self.journal_a,
                role=JournalEditorAssignment.Role.CHIEF_EDITOR,
            ).exists()
        )

    def test_active_journal_chief_cannot_be_ended_without_replacement(self):
        chief = self.make_user("protected-chief")
        assignment = self.appoint(
            chief, self.journal_a, JournalEditorAssignment.Role.CHIEF_EDITOR
        )
        with self.assertRaises(ValidationError):
            end_journal_editor_assignment(
                actor=self.admin,
                assignment=assignment,
                reason="Would leave no chief",
            )
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    def test_expiry_and_account_suspension_remove_access_immediately(self):
        expired = self.make_user("expired-editor")
        assignment = self.appoint(
            expired,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.JOURNAL_PROFILE],
        )
        now = timezone.now()
        JournalEditorAssignment.objects.filter(pk=assignment.pk).update(
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1),
        )
        self.assertIsNone(get_journal_editor_assignment(expired, self.journal_a))

        active = self.make_user("suspended-editor")
        self.appoint(
            active,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.JOURNAL_PROFILE],
        )
        self.client.force_login(active)
        session_key = self.client.session.session_key
        suspend_account(actor=self.admin, user=active, reason="Temporary suspension")
        active.refresh_from_db()
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
        self.assertIsNone(get_journal_editor_assignment(active, self.journal_a))
        self.assertFalse(
            can_manage_journal(
                active,
                self.journal_a,
                JournalEditorAssignment.Responsibility.JOURNAL_PROFILE,
            )
        )
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="User",
                target_id=str(active.pk),
                metadata__reason="Temporary suspension",
                metadata__sessions_revoked__gte=1,
            ).exists()
        )

    def test_editors_cannot_change_sensitive_journal_fields(self):
        editor = self.make_user("sensitive-field-editor")
        self.appoint(
            editor,
            self.journal_a,
            JournalEditorAssignment.Role.CHIEF_EDITOR,
        )
        for field_name in (
            "slug",
            "status",
            "static_site_path",
            "target_article_count",
            "static_output_root",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(PermissionDenied):
                    update_journal_profile(
                        actor=editor,
                        journal=self.journal_a,
                        values={field_name: "forbidden-change"},
                    )

    def test_public_team_is_grouped_sorted_and_hides_invalid_members(self):
        chief = self.make_user("public-chief")
        executive = self.make_user("public-executive")
        associate_a = self.make_user("public-associate-a")
        associate_b = self.make_user("public-associate-b")
        self.appoint(chief, self.journal_a, JournalEditorAssignment.Role.CHIEF_EDITOR)
        self.appoint(
            executive,
            self.journal_a,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        )
        first = self.appoint(
            associate_a,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.MEDIA_ASSETS],
        )
        second = self.appoint(
            associate_b,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.MEDIA_ASSETS],
        )
        JournalEditorAssignment.objects.filter(pk=first.pk).update(display_order=20)
        JournalEditorAssignment.objects.filter(pk=second.pk).update(display_order=10)
        first.show_publicly = False
        first.save(update_fields=["show_publicly", "updated_at"])

        team = get_public_editorial_team(self.journal_a)
        self.assertEqual(
            [group["role"] for group in team["groups"]],
            [
                JournalEditorAssignment.Role.CHIEF_EDITOR,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
                JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            ],
        )
        associates = team["groups"][2]["members"]
        self.assertEqual([item.pk for item in associates], [second.pk])
        with translation.override("en"):
            english_team = get_public_editorial_team(self.journal_a)
        self.assertEqual(english_team["heading"], "Editorial team")
        self.assertEqual(
            [group["label"] for group in english_team["groups"]],
            ["Chief Editor", "Executive Editor", "Associate Editor"],
        )

    def test_editor_forms_exclude_super_administrator_accounts(self):
        ordinary = self.make_user("appointable-editor")
        appoint_ids = set(
            AppointEditorForm().fields["user"].queryset.values_list("pk", flat=True)
        )
        replace_ids = set(
            ReplaceEditorForm().fields["user"].queryset.values_list("pk", flat=True)
        )
        self.assertIn(ordinary.pk, appoint_ids)
        self.assertIn(ordinary.pk, replace_ids)
        self.assertNotIn(self.admin.pk, appoint_ids)
        self.assertNotIn(self.admin.pk, replace_ids)

    def test_editor_gets_only_technical_access_group(self):
        editor = self.make_user("technical-group-editor")
        self.appoint(
            editor,
            self.journal_a,
            JournalEditorAssignment.Role.ASSOCIATE_EDITOR,
            [JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE],
        )
        self.assertTrue(
            editor.groups.filter(name=JOURNAL_EDITOR_ACCESS_GROUP_NAME).exists()
        )
        self.assertFalse(editor.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists())
        self.assertFalse(editor.has_perm("journals.change_journal"))
