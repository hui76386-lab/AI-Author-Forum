from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from wagtail.models import Site

from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.site_settings.models import (
    AuditLog,
    ContentColumnConfig,
    NavigationArea,
    NavigationEntryStatus,
    NavigationGroup,
    NavigationItem,
    NavigationItemPathRedirect,
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
    NavigationTargetType,
)
from ai_author_forum.site_settings.navigation import (
    archive_navigation_item,
    copy_template_to_journal,
    duplicate_navigation_item,
    ensure_default_journal_navigation_template,
    hard_delete_navigation_item,
    navigation_item_reference_counts,
    reorder_navigation_tree,
    restore_navigation_item,
    set_navigation_item_visibility,
)
from ai_author_forum.static_publish.models import StaticManifest, StaticPublishJob
from ai_author_forum.static_publish.providers import WagtailPageTargetProvider


class ManagedNavigationFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.site = Site.objects.get(is_default_site=True)
        cls.template = ensure_default_journal_navigation_template(site=cls.site)
        cls.journal = Journal.objects.create(
            name="Managed Navigation Journal",
            slug="managed-navigation-journal",
            az_group="M",
        )
        cls.other_journal = Journal.objects.create(
            name="Other Managed Journal",
            slug="other-managed-journal",
            az_group="O",
        )
        cls.nav_set = NavigationSet.objects.get(
            journal=cls.journal,
            scope=NavigationScope.JOURNAL,
            status=NavigationSetStatus.ACTIVE,
        )
        cls.other_nav_set = NavigationSet.objects.get(
            journal=cls.other_journal,
            scope=NavigationScope.JOURNAL,
            status=NavigationSetStatus.ACTIVE,
        )
        cls.category = JournalCategory.objects.create(
            journal=cls.journal,
            name="Research",
            code="research",
            slug="research",
        )
        cls.other_category = JournalCategory.objects.create(
            journal=cls.other_journal,
            name="Other Research",
            code="other-research",
            slug="other-research",
        )

    def create_group(self, nav_set=None, *, code="group", label="Group", order=100):
        return NavigationGroup.objects.create(
            navigation_set=nav_set or self.nav_set,
            label=label,
            code=code,
            sort_order=order,
        )

    def create_item(
        self,
        group,
        *,
        code="item",
        label="Item",
        target_type=NavigationTargetType.CURRENT_ISSUE,
        status=NavigationEntryStatus.ACTIVE,
        visible=True,
        **kwargs,
    ):
        return NavigationItem.objects.create(
            site=group.navigation_set.site,
            area=(
                NavigationArea.JOURNALS
                if group.navigation_set.scope == NavigationScope.JOURNAL
                else NavigationArea.HOME
            ),
            label=label,
            slug=code,
            code=code,
            group=group,
            target_type=target_type,
            status=status,
            is_visible=visible,
            sort_order=group.items.count() + 1,
            is_core=not group.navigation_set.is_template,
            **kwargs,
        )


class ManagedNavigationModelTests(ManagedNavigationFixtureMixin, TestCase):
    def test_navigation_set_scope_rules_are_mutually_exclusive(self):
        invalid_main = NavigationSet(
            site=self.site,
            scope=NavigationScope.MAIN_SITE,
            journal=self.journal,
            name="Invalid main",
        )
        with self.assertRaises(ValidationError):
            invalid_main.full_clean()

        invalid_journal = NavigationSet(
            site=self.site,
            scope=NavigationScope.JOURNAL,
            name="Invalid journal",
        )
        with self.assertRaises(ValidationError):
            invalid_journal.full_clean()

        invalid_template = NavigationSet(
            site=self.site,
            scope=NavigationScope.JOURNAL,
            journal=self.journal,
            name="Invalid template",
            is_template=True,
        )
        with self.assertRaises(ValidationError):
            invalid_template.full_clean()

    def test_only_one_active_navigation_set_exists_per_journal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            NavigationSet.objects.create(
                site=self.site,
                scope=NavigationScope.JOURNAL,
                journal=self.journal,
                name="Duplicate active journal navigation",
                status=NavigationSetStatus.ACTIVE,
            )

    def test_group_and_item_codes_are_unique_inside_their_scope(self):
        group = self.create_group(code="unique-group")
        duplicate_group = NavigationGroup(
            navigation_set=self.nav_set,
            label="Duplicate",
            code=group.code,
        )
        with self.assertRaises(ValidationError):
            duplicate_group.full_clean()

        self.create_item(group, code="unique-item")
        second_group = self.create_group(code="second-group", order=101)
        duplicate_item = NavigationItem(
            site=self.site,
            area=NavigationArea.JOURNALS,
            label="Duplicate item",
            slug="unique-item",
            code="unique-item",
            group=second_group,
            target_type=NavigationTargetType.CURRENT_ISSUE,
        )
        with self.assertRaises(ValidationError):
            duplicate_item.full_clean()

    def test_managed_navigation_rejects_third_level_and_mixed_targets(self):
        group = self.create_group(code="validation-group")
        parent = self.create_item(group, code="parent")
        child = NavigationItem(
            site=self.site,
            area=NavigationArea.JOURNALS,
            label="Child",
            slug="child",
            code="child",
            group=group,
            parent=parent,
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path="/valid/",
        )
        with self.assertRaises(ValidationError):
            child.full_clean()

        mixed = NavigationItem(
            site=self.site,
            area=NavigationArea.JOURNALS,
            label="Mixed",
            slug="mixed",
            code="mixed",
            group=group,
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path="/valid/",
            external_url="https://example.com/",
        )
        with self.assertRaises(ValidationError):
            mixed.full_clean()

    def test_external_and_internal_links_are_strictly_validated(self):
        group = self.create_group(code="link-group")
        insecure = NavigationItem(
            site=self.site,
            area=NavigationArea.JOURNALS,
            label="Insecure",
            slug="insecure",
            code="insecure",
            group=group,
            target_type=NavigationTargetType.EXTERNAL_URL,
            external_url="http://example.com/",
        )
        with self.assertRaises(ValidationError):
            insecure.full_clean()

        double_slash = NavigationItem(
            site=self.site,
            area=NavigationArea.JOURNALS,
            label="Double slash",
            slug="double-slash",
            code="double-slash",
            group=group,
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path="//example.com/path",
        )
        with self.assertRaises(ValidationError):
            double_slash.full_clean()

    def test_content_column_category_must_belong_to_same_journal(self):
        group = self.create_group(code="category-group")
        item = NavigationItem(
            site=self.site,
            area=NavigationArea.JOURNALS,
            label="Wrong category",
            slug="wrong-category",
            code="wrong-category",
            group=group,
            target_type=NavigationTargetType.CONTENT_COLUMN,
            category=self.other_category,
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

        item.category = self.category
        item.full_clean()
        item.save()
        config = ContentColumnConfig(
            navigation_item=item,
            category=self.other_category,
        )
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_eight_and_twenty_are_soft_limits_not_model_limits(self):
        nav_set = NavigationSet.objects.create(
            site=self.site,
            scope=NavigationScope.JOURNAL,
            journal=Journal.objects.create(
                name="Soft Limit Journal", slug="soft-limit-journal", az_group="S"
            ),
            name="Soft limit draft",
            status=NavigationSetStatus.DRAFT,
        )
        groups = []
        for index in range(9):
            group = NavigationGroup(
                navigation_set=nav_set,
                label=f"Group {index}",
                code=f"group-{index}",
            )
            group.full_clean()
            group.save()
            groups.append(group)
        for index in range(21):
            item = NavigationItem(
                site=self.site,
                area=NavigationArea.JOURNALS,
                label=f"Item {index}",
                slug=f"item-{index}",
                code=f"item-{index}",
                group=groups[0],
                target_type=NavigationTargetType.CURRENT_ISSUE,
            )
            item.full_clean()
            item.save()
        self.assertEqual(nav_set.groups.count(), 9)
        self.assertEqual(groups[0].items.count(), 21)

    def test_missing_content_column_config_fails_static_target_discovery(self):
        group = self.create_group(code="missing-config-group")
        item = self.create_item(
            group,
            code="missing-config",
            target_type=NavigationTargetType.CONTENT_COLUMN,
        )
        ContentColumnConfig.objects.filter(navigation_item=item).delete()
        with self.assertRaisesRegex(RuntimeError, "has no ContentColumnConfig"):
            WagtailPageTargetProvider().get_targets()

    def test_slug_change_creates_permanent_redirect_and_audit(self):
        group = self.create_group(code="redirect-group")
        item = self.create_item(
            group,
            code="old-column",
            target_type=NavigationTargetType.CONTENT_COLUMN,
        )
        ContentColumnConfig.objects.create(navigation_item=item)
        old_path = item.target_url
        item.code = "new-column"
        item.slug = "new-column"
        item.save()
        redirect = NavigationItemPathRedirect.objects.get(navigation_item=item)
        self.assertEqual(redirect.old_path, old_path)
        self.assertEqual(redirect.new_path, item.target_url)
        self.assertEqual(redirect.http_status, 301)
        self.assertTrue(
            AuditLog.objects.filter(
                target_type="NavigationItem", target_id=str(item.pk)
            ).exists()
        )


class ManagedNavigationInitializationTests(ManagedNavigationFixtureMixin, TestCase):
    def test_new_journal_gets_independent_copy_of_template(self):
        self.assertEqual(self.nav_set.copied_from_template_id, self.template.pk)
        self.assertNotEqual(self.nav_set.pk, self.other_nav_set.pk)
        template_group_ids = set(self.template.groups.values_list("pk", flat=True))
        copied_group_ids = set(self.nav_set.groups.values_list("pk", flat=True))
        self.assertFalse(template_group_ids & copied_group_ids)
        self.assertEqual(
            list(self.template.groups.values_list("code", flat=True)),
            list(self.nav_set.groups.values_list("code", flat=True)),
        )

    def test_template_changes_do_not_propagate_to_existing_journal(self):
        template_group = self.template.groups.order_by("sort_order", "pk").first()
        original_label = self.nav_set.groups.get(code=template_group.code).label
        template_group.label = "Changed template label"
        template_group.save(update_fields=["label", "updated_at"])
        self.assertEqual(
            self.nav_set.groups.get(code=template_group.code).label,
            original_label,
        )

    def test_overwrite_archives_old_set_and_records_audit(self):
        old_id = self.nav_set.pk
        replacement = copy_template_to_journal(
            template=self.template,
            journal=self.journal,
            overwrite=True,
        )
        self.assertNotEqual(replacement.pk, old_id)
        self.assertEqual(
            NavigationSet.objects.get(pk=old_id).status,
            NavigationSetStatus.ARCHIVED,
        )
        log = AuditLog.objects.filter(
            target_type="NavigationSet", target_id=str(replacement.pk)
        ).latest("created_at")
        self.assertTrue(log.metadata["overwrite"])
        self.assertEqual(log.metadata["journal_id"], self.journal.pk)


class ManagedNavigationServiceTests(ManagedNavigationFixtureMixin, TestCase):
    def test_hide_archive_restore_and_duplicate_are_audited(self):
        group = self.create_group(code="lifecycle-group")
        item = self.create_item(group, code="lifecycle-item")

        set_navigation_item_visibility(item, visible=False)
        item.refresh_from_db()
        self.assertEqual(item.status, NavigationEntryStatus.HIDDEN)
        archive_navigation_item(item)
        item.refresh_from_db()
        self.assertEqual(item.status, NavigationEntryStatus.ARCHIVED)
        restore_navigation_item(item)
        item.refresh_from_db()
        self.assertEqual(item.status, NavigationEntryStatus.ACTIVE)

        duplicate = duplicate_navigation_item(item)
        self.assertEqual(duplicate.status, NavigationEntryStatus.DRAFT)
        self.assertFalse(duplicate.is_visible)
        self.assertNotEqual(duplicate.code, item.code)
        self.assertGreaterEqual(
            AuditLog.objects.filter(target_type="NavigationItem").count(), 4
        )

    def test_reorder_tree_moves_across_groups_once_and_rejects_stale_or_invalid_payloads(
        self,
    ):
        first = self.create_group(code="reorder-first", order=200)
        second = self.create_group(code="reorder-second", order=201)
        first_item = self.create_item(first, code="reorder-first-item")
        second_item = self.create_item(second, code="reorder-second-item")
        self.nav_set.refresh_from_db()
        version = self.nav_set.version

        reorder_navigation_tree(
            self.nav_set,
            ordered_group_ids=[second.pk, first.pk]
            + list(
                self.nav_set.groups.exclude(pk__in=[first.pk, second.pk])
                .order_by("sort_order", "pk")
                .values_list("pk", flat=True)
            ),
            items_by_group={
                str(group.pk): (
                    [first_item.pk, second_item.pk]
                    if group.pk == second.pk
                    else (
                        []
                        if group.pk == first.pk
                        else list(
                            group.items.order_by("sort_order", "pk").values_list(
                                "pk", flat=True
                            )
                        )
                    )
                )
                for group in self.nav_set.groups.all()
            },
            expected_version=version,
        )
        self.nav_set.refresh_from_db()
        first_item.refresh_from_db()
        self.assertEqual(first_item.group_id, second.pk)
        self.assertEqual(self.nav_set.version, version + 1)

        with self.assertRaises(ValidationError):
            reorder_navigation_tree(
                self.nav_set,
                ordered_group_ids=list(
                    self.nav_set.groups.order_by("sort_order", "pk").values_list(
                        "pk", flat=True
                    )
                ),
                items_by_group={
                    str(group.pk): list(group.items.values_list("pk", flat=True))
                    for group in self.nav_set.groups.all()
                },
                expected_version=version,
            )

        current_groups = list(self.nav_set.groups.values_list("pk", flat=True))
        with self.assertRaises(ValidationError):
            reorder_navigation_tree(
                self.nav_set,
                ordered_group_ids=current_groups[:-1],
                items_by_group={},
                expected_version=self.nav_set.version,
            )

    def test_reorder_tree_rejects_item_from_another_navigation_set(self):
        foreign_group = self.other_nav_set.groups.first()
        foreign_item = foreign_group.items.first()
        groups = list(self.nav_set.groups.order_by("sort_order", "pk"))
        items = {
            str(group.pk): list(
                group.items.order_by("sort_order", "pk").values_list("pk", flat=True)
            )
            for group in groups
        }
        items[str(groups[0].pk)].append(foreign_item.pk)
        with self.assertRaises(ValidationError):
            reorder_navigation_tree(
                self.nav_set,
                ordered_group_ids=[group.pk for group in groups],
                items_by_group=items,
                expected_version=self.nav_set.version,
            )

    def test_hard_delete_requires_permission_draft_and_no_references(self):
        group = self.create_group(code="delete-group")
        draft = self.create_item(
            group,
            code="delete-draft",
            status=NavigationEntryStatus.DRAFT,
            visible=False,
        )
        with self.assertRaises(PermissionDenied):
            hard_delete_navigation_item(draft)

        user_model = get_user_model()
        admin = user_model.objects.create_superuser(
            username="managed-delete-admin",
            email="managed-delete@example.com",
            password="password",
        )
        draft_id = draft.pk
        self.assertTrue(hard_delete_navigation_item(draft, actor=admin))
        self.assertFalse(NavigationItem.objects.filter(pk=draft_id).exists())

        active = self.create_item(group, code="delete-active")
        with self.assertRaises(ValidationError):
            hard_delete_navigation_item(active, actor=admin)

    def test_hard_delete_is_blocked_by_manifest_dependency_reference(self):
        group = self.create_group(code="manifest-delete-group")
        draft = self.create_item(
            group,
            code="manifest-delete-draft",
            status=NavigationEntryStatus.DRAFT,
            visible=False,
        )
        job = StaticPublishJob.objects.create(
            status=StaticPublishJob.Status.SUCCEEDED,
            version="manifest-navigation-reference",
        )
        StaticManifest.objects.create(
            version=job.version,
            job=job,
            metadata={
                "targets": [
                    {
                        "target_id": "page:unrelated",
                        "canonical_path": "/unrelated/",
                        "dependencies": {"navigation_item_ids": [draft.pk]},
                    }
                ]
            },
        )
        admin = get_user_model().objects.create_superuser(
            username="manifest-delete-admin",
            email="manifest-delete@example.com",
            password="password",
        )

        with self.assertRaises(ValidationError):
            hard_delete_navigation_item(draft, actor=admin)

        counts = navigation_item_reference_counts(draft)
        self.assertEqual(counts["static_pages"], 1)
        self.assertEqual(counts["historical_versions"], 1)

    def test_reference_counts_include_navigation_reference(self):
        group = self.create_group(code="reference-group")
        target = self.create_item(
            group,
            code="reference-target",
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path="/reference-target/",
        )
        self.create_item(
            group,
            code="reference-source",
            target_type=NavigationTargetType.INTERNAL_PATH,
            internal_path=target.target_url,
        )
        counts = navigation_item_reference_counts(target)
        self.assertEqual(counts["navigation_references"], 1)


class ManagedNavigationAdminTests(ManagedNavigationFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command("seed_roles", verbosity=0)
        cls.user_model = get_user_model()

    def make_client(self, role_name):
        user = self.user_model.objects.create_user(
            username=f"managed-{role_name}-{self.user_model.objects.count()}",
            password="password",
            is_staff=True,
        )
        user.groups.add(Group.objects.get(name=role_name))
        client = Client()
        client.force_login(user)
        return client, user

    def managed_url(self, mode="journal", **params):
        query = {"mode": mode, **params}
        if mode == "journal" and "journal" not in query:
            query["journal"] = self.journal.pk
        return "/admin/managed-navigation/?" + "&".join(
            f"{key}={value}" for key, value in query.items()
        )

    def test_scope_permissions_match_role_matrix(self):
        operator, _ = self.make_client("\u7ad9\u70b9\u8fd0\u8425")
        self.assertEqual(operator.get(self.managed_url("journal")).status_code, 200)
        self.assertEqual(operator.get(self.managed_url("main")).status_code, 200)
        self.assertEqual(operator.get(self.managed_url("template")).status_code, 302)

        content, _ = self.make_client("\u5185\u5bb9\u7ba1\u7406\u5458")
        self.assertEqual(content.get(self.managed_url("journal")).status_code, 302)
        self.assertEqual(content.get(self.managed_url("main")).status_code, 302)
        self.assertEqual(content.get(self.managed_url("template")).status_code, 302)

        publisher, _ = self.make_client("\u53d1\u5e03\u7ba1\u7406\u5458")
        self.assertEqual(publisher.get(self.managed_url("template")).status_code, 302)

        readonly, _ = self.make_client("\u53ea\u8bfb\u4eba\u5458")
        self.assertEqual(readonly.get(self.managed_url("main")).status_code, 200)
        self.assertEqual(readonly.get(self.managed_url("journal")).status_code, 200)
        self.assertEqual(readonly.get(self.managed_url("template")).status_code, 200)

    def test_required_explicit_actions_and_previews_are_rendered(self):
        lead, _ = self.make_client("\u9879\u76ee\u603b\u8d1f\u8d23\u4eba")
        response = lead.get(self.managed_url("journal"))
        for label in (
            "\u9884\u89c8\u680f\u76ee",
            "\u4ece\u6a21\u677f\u590d\u5236",
            "\u53d1\u5e03\u53d8\u66f4",
            "\u6dfb\u52a0\u5bfc\u822a\u5206\u7ec4",
            "\u6dfb\u52a0\u680f\u76ee",
            "\u7f16\u8f91",
            "\u590d\u5236",
            "\u67e5\u770b\u5f15\u7528",
        ):
            self.assertContains(response, label)
        self.assertContains(response, "managed-preview-desktop")
        self.assertContains(response, "managed-preview-mobile")

    def test_content_manager_cannot_view_or_change_managed_navigation(self):
        client, _ = self.make_client("\u5185\u5bb9\u7ba1\u7406\u5458")
        item = (
            self.nav_set.groups.first()
            .items.filter(target_type=NavigationTargetType.CONTENT_COLUMN)
            .first()
        )

        response = client.get(
            self.managed_url("journal", edit_config=item.pk) + "#content-config-form"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")

        response = client.post(
            self.managed_url("journal"),
            {
                "mode": "journal",
                "journal": self.journal.pk,
                "operation": "save_content_config",
                "item_id": item.pk,
                "page_size": 12,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/")

    def test_add_to_group_and_invalid_content_form_preserve_context(self):
        lead, _ = self.make_client("\u9879\u76ee\u603b\u8d1f\u8d23\u4eba")
        group = self.nav_set.groups.order_by("sort_order", "pk").last()
        response = lead.get(self.managed_url("journal", add_to_group=group.pk))
        self.assertEqual(response.context["item_form_group"].pk, group.pk)

        item = (
            self.nav_set.groups.first()
            .items.filter(target_type=NavigationTargetType.CONTENT_COLUMN)
            .first()
        )
        response = lead.post(
            self.managed_url("journal"),
            {
                "mode": "journal",
                "journal": self.journal.pk,
                "operation": "save_content_config",
                "item_id": item.pk,
                "intro": json.dumps({"blocks": [], "entityMap": {}}),
                "category": "",
                "page_size": 0,
                "seo_title": "",
                "seo_description": "",
                "empty_message": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["edit_config_item"].pk, item.pk)
        self.assertTrue(response.context["config_form"].is_bound)
        self.assertTrue(response.context["config_form"].errors)

    def test_soft_limit_requires_confirmation(self):
        lead, _ = self.make_client("\u9879\u76ee\u603b\u8d1f\u8d23\u4eba")
        while (
            self.nav_set.groups.exclude(status=NavigationEntryStatus.ARCHIVED).count()
            < 8
        ):
            index = self.nav_set.groups.count() + 1
            self.create_group(code=f"soft-admin-{index}", order=500 + index)
        payload = {
            "mode": "journal",
            "journal": self.journal.pk,
            "operation": "save_group",
            "label": "Ninth group",
            "code": "ninth-group",
            "is_visible": "on",
            "status": NavigationEntryStatus.ACTIVE,
        }
        response = lead.post(self.managed_url("journal"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "8")
        self.assertFalse(self.nav_set.groups.filter(code="ninth-group").exists())

        response = lead.post(
            self.managed_url("journal"),
            {**payload, "confirm_soft_limit": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.nav_set.groups.filter(code="ninth-group").exists())

    def test_json_reorder_checks_version_and_current_tree(self):
        lead, _ = self.make_client("\u9879\u76ee\u603b\u8d1f\u8d23\u4eba")
        groups = list(self.nav_set.groups.order_by("sort_order", "pk"))
        payload = {
            "operation": "reorder_tree",
            "expected_version": self.nav_set.version,
            "groups": [group.pk for group in reversed(groups)],
            "items": {
                str(group.pk): list(
                    group.items.order_by("sort_order", "pk").values_list(
                        "pk", flat=True
                    )
                )
                for group in groups
            },
        }
        response = lead.post(
            self.managed_url("journal"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        response = lead.post(
            self.managed_url("journal"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)

    def test_template_copy_has_preview_and_confirmation(self):
        lead, _ = self.make_client("\u9879\u76ee\u603b\u8d1f\u8d23\u4eba")
        response = lead.post(
            self.managed_url("journal"),
            {
                "mode": "journal",
                "journal": self.journal.pk,
                "operation": "template_preview",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["confirmation"]["kind"], "template_copy")
        self.assertGreater(response.context["confirmation"]["affected_page_count"], 0)

    @patch("ai_author_forum.site_settings.navigation_admin.run_static_publish.delay")
    def test_publish_creates_selective_job_and_audit(self, delay):
        delay.return_value.id = "managed-navigation-task"
        lead, _ = self.make_client("\u9879\u76ee\u603b\u8d1f\u8d23\u4eba")
        response = lead.post(
            self.managed_url("journal"),
            {
                "mode": "journal",
                "journal": self.journal.pk,
                "operation": "publish_changes",
            },
        )
        self.assertEqual(response.status_code, 302)
        log = AuditLog.objects.filter(
            target_type="NavigationSet",
            target_id=str(self.nav_set.pk),
            action="publish",
        ).latest("created_at")
        self.assertEqual(log.metadata["navigation_version"], self.nav_set.version)
        self.assertGreater(log.metadata["affected_page_count"], 0)
        self.assertTrue(log.metadata["publish_job_id"])
