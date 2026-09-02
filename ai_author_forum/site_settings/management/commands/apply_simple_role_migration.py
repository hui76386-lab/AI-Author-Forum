import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.models import GroupCollectionPermission, GroupPagePermission

from ai_author_forum.articles.wagtail_hooks import (
    _assign_workflow_to_existing_article_pages,
    _get_or_create_article_workflow,
)
from ai_author_forum.journals.editor_services import (
    appoint_journal_editor,
    replace_chief_editor,
    replace_executive_editor,
    update_editor_assignment_profile,
)
from ai_author_forum.journals.models import Journal, JournalEditorAssignment
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus
from ai_author_forum.site_settings.role_migration import (
    LEGACY_GROUP_NAMES,
    build_report,
    load_mapping,
    validate_mapping,
)
from ai_author_forum.users.services import (
    SUPER_ADMIN_GROUP_NAME,
    deactivate_account,
    grant_super_admin,
    initialize_super_admin_group,
)


class Command(BaseCommand):
    help = "Dry-run or idempotently apply an explicit simple-RBAC mapping."

    def add_arguments(self, parser):
        parser.add_argument("mapping")
        parser.add_argument("--actor", required=True)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the mapping. Without this flag the command is read-only.",
        )

    @staticmethod
    def _resolve_user(identifier):
        User = get_user_model()
        return (
            User.objects.filter(username=identifier).first()
            or User.objects.filter(email__iexact=identifier).first()
        )

    def _resolve_actor(self, identifier, mapping, *, apply):
        actor = self._resolve_user(identifier)
        if actor is None or not actor.is_active or actor.account_status != "active":
            raise CommandError("--actor must identify an active account")
        if actor.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists():
            return actor
        active_super_exists = (
            get_user_model()
            .objects.filter(
                is_active=True,
                account_status="active",
                groups__name=SUPER_ADMIN_GROUP_NAME,
            )
            .exists()
        )
        identifiers = set(mapping.get("super_admins") or [])
        actor_is_mapped = actor.username in identifiers or actor.email in identifiers
        if active_super_exists or not actor.is_superuser or not actor_is_mapped:
            raise CommandError(
                "--actor must be an active business super administrator; technical "
                "recovery bootstrap is allowed only when none exists and the actor is mapped"
            )
        if apply:
            initialize_super_admin_group(actor)
        return actor

    def handle(self, *args, **options):
        try:
            mapping = load_mapping(options["mapping"])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        validation = validate_mapping(mapping)
        if validation["errors"]:
            raise CommandError(
                "Mapping validation failed:\n- " + "\n- ".join(validation["errors"])
            )
        actor = self._resolve_actor(options["actor"], mapping, apply=options["apply"])
        if not options["apply"]:
            self.stdout.write(
                json.dumps(
                    build_report(mapping), ensure_ascii=False, indent=2, default=str
                )
            )
            self.stdout.write(
                self.style.WARNING("Dry-run only; no changes were written.")
            )
            return

        with transaction.atomic():
            self._apply_super_admins(actor, mapping)
            actor.refresh_from_db()
            self._apply_assignments(actor, mapping)
            self._deactivate_accounts(actor, mapping)
            cleanup = self._remove_legacy_authority()
            workflow = _get_or_create_article_workflow()
            _assign_workflow_to_existing_article_pages(workflow)
            AuditLog.record(
                action=AuditAction.PERMISSION,
                status=AuditStatus.SUCCESS,
                actor=actor,
                target_type="SimpleRoleMigration",
                target_label="simple-journal-rbac",
                message="应用简化账号与子期刊角色迁移。",
                metadata={
                    "mapping_counts": {
                        "super_admins": len(mapping.get("super_admins") or []),
                        "assignments": len(mapping.get("assignments") or []),
                        "deactivated": len(mapping.get("deactivate_users") or []),
                    },
                    "legacy_cleanup": cleanup,
                    "workflow_id": workflow.pk,
                },
            )
        self.stdout.write(self.style.SUCCESS("Simple role migration applied."))

    def _apply_super_admins(self, actor, mapping):
        for identifier in mapping.get("super_admins") or []:
            user = self._resolve_user(identifier)
            if (
                user.pk == actor.pk
                and user.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists()
            ):
                continue
            grant_super_admin(actor=actor, user=user)

    def _apply_assignments(self, actor, mapping):
        for row in mapping.get("assignments") or []:
            user = self._resolve_user(row["user"])
            journal = Journal.objects.get(slug=row["journal"])
            role = row["role"]
            profile = {
                "public_name": row.get("public_name") or user.display_name,
                "public_affiliation": row.get("public_affiliation") or user.institution,
                "public_role_label": row.get("public_role_label")
                or JournalEditorAssignment.DEFAULT_PUBLIC_ROLE_LABELS[role],
                "display_order": row.get("display_order", 0),
                "show_publicly": row.get("show_publicly", True),
            }
            existing = (
                JournalEditorAssignment.objects.effective()
                .filter(user=user, journal=journal, role=role)
                .first()
            )
            if existing is None and role in {
                JournalEditorAssignment.Role.CHIEF_EDITOR,
                JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
            }:
                current = JournalEditorAssignment.objects.filter(
                    journal=journal, role=role, is_active=True
                ).first()
                if current:
                    service = (
                        replace_chief_editor
                        if role == JournalEditorAssignment.Role.CHIEF_EDITOR
                        else replace_executive_editor
                    )
                    existing = service(
                        actor=actor,
                        journal=journal,
                        new_user=user,
                        reason="显式角色迁移映射",
                    )
            if existing is None:
                existing = appoint_journal_editor(
                    actor=actor,
                    user=user,
                    journal=journal,
                    role=role,
                    responsibilities=row.get("responsibilities") or [],
                    public_profile=profile,
                )
            update_editor_assignment_profile(
                actor=actor,
                assignment=existing,
                responsibilities=(
                    row.get("responsibilities") or []
                    if role == JournalEditorAssignment.Role.ASSOCIATE_EDITOR
                    else None
                ),
                public_profile=profile,
            )

    def _deactivate_accounts(self, actor, mapping):
        for identifier in mapping.get("deactivate_users") or []:
            user = self._resolve_user(identifier)
            if user.account_status == user.AccountStatus.DEACTIVATED:
                continue
            deactivate_account(
                actor=actor,
                user=user,
                reason="简化角色迁移映射确认停用",
            )

    @staticmethod
    def _remove_legacy_authority():
        review_permission = Permission.objects.filter(
            content_type__app_label="articles", codename="review_article"
        ).first()
        direct_users = 0
        if review_permission:
            direct_users = (
                get_user_model()
                .objects.filter(user_permissions=review_permission)
                .count()
            )
            for user in get_user_model().objects.filter(
                user_permissions=review_permission
            ):
                user.user_permissions.remove(review_permission)
        groups = list(Group.objects.filter(name__in=LEGACY_GROUP_NAMES))
        for group in groups:
            group.permissions.clear()
            GroupPagePermission.objects.filter(group=group).delete()
            GroupCollectionPermission.objects.filter(group=group).delete()
        return {
            "legacy_groups_cleared": [group.name for group in groups],
            "direct_review_permissions_removed": direct_users,
        }
