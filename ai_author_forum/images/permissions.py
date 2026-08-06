from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from wagtail.models import Collection, Page, ReferenceIndex
from wagtail.permission_policies.collections import (
    CollectionOwnershipPermissionPolicy,
)


class JournalImagePermissionPolicy(CollectionOwnershipPermissionPolicy):
    """Apply journal assignments to Wagtail's shared image library."""

    instance_actions = frozenset({"choose", "change", "delete"})
    manage_actions = frozenset({"add", "change", "delete"})

    @staticmethod
    def _assignment_allows(row, actions) -> bool:
        from ai_author_forum.journals.models import JournalEditorAssignment

        actions = set(actions)
        if "choose" in actions:
            return True
        if not actions.intersection(JournalImagePermissionPolicy.manage_actions):
            return False
        return row["role"] in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        } or JournalEditorAssignment.Responsibility.MEDIA_ASSETS in (
            row["responsibilities"] or []
        )

    def _journal_ids_for_actions(self, user, actions) -> set[int]:
        from ai_author_forum.journals.models import JournalEditorAssignment

        if not (
            user
            and user.is_authenticated
            and user.is_active
            and getattr(user, "account_status", "active") == "active"
        ):
            return set()
        rows = (
            JournalEditorAssignment.objects.effective()
            .filter(user=user)
            .values("journal_id", "role", "responsibilities")
        )
        return {
            row["journal_id"] for row in rows if self._assignment_allows(row, actions)
        }

    def _image_ids_for_journals(self, journal_ids) -> set[int]:
        from ai_author_forum.articles.models import ArticlePage
        from ai_author_forum.journals.models import (
            Journal,
            JournalAssetBinding,
            JournalCategory,
            PublicationIssue,
            StaticArticle,
        )
        from ai_author_forum.placements.models import ArticlePlacement
        from ai_author_forum.site_settings.models import ContentColumnConfig

        journal_ids = set(journal_ids)
        if not journal_ids:
            return set()

        image_ids: set[int] = set()

        def add_ids(values):
            image_ids.update(value for value in values if value is not None)

        articles = ArticlePage.objects.filter(primary_journal_id__in=journal_ids)
        article_ids = list(articles.values_list("pk", flat=True))
        add_ids(articles.values_list("featured_image_id", flat=True))

        if article_ids:
            page_type = ContentType.objects.get_for_model(Page)
            image_type = ContentType.objects.get_for_model(self.model)
            referenced_ids = ReferenceIndex.objects.filter(
                base_content_type=page_type,
                object_id__in=[str(article_id) for article_id in article_ids],
                to_content_type=image_type,
            ).values_list("to_object_id", flat=True)
            image_ids.update(
                int(value) for value in referenced_ids if str(value).isdigit()
            )

        add_ids(
            JournalAssetBinding.objects.filter(journal_id__in=journal_ids).values_list(
                "image_id", flat=True
            )
        )
        for field_name in ("cover_image_id", "metrics_image_id"):
            add_ids(
                Journal.objects.filter(pk__in=journal_ids).values_list(
                    field_name, flat=True
                )
            )
        add_ids(
            JournalCategory.objects.filter(journal_id__in=journal_ids).values_list(
                "cover_image_id", flat=True
            )
        )
        add_ids(
            PublicationIssue.objects.filter(journal_id__in=journal_ids).values_list(
                "cover_image_id", flat=True
            )
        )
        add_ids(
            StaticArticle.objects.filter(journal_id__in=journal_ids).values_list(
                "cover_image_id", flat=True
            )
        )
        add_ids(
            ArticlePlacement.objects.filter(
                article__primary_journal_id__in=journal_ids
            ).values_list("override_image_id", flat=True)
        )
        add_ids(
            ContentColumnConfig.objects.filter(
                category__journal_id__in=journal_ids
            ).values_list("cover_image_id", flat=True)
        )
        return image_ids

    def _scoped_images(self, user, journal_ids):
        image_ids = self._image_ids_for_journals(journal_ids)
        return self.model._default_manager.filter(
            Q(pk__in=image_ids) | Q(uploaded_by_user=user)
        )

    def user_has_permission(self, user, action):
        return super().user_has_permission(user, action) or bool(
            self._journal_ids_for_actions(user, [action])
        )

    def user_has_any_permission(self, user, actions):
        return super().user_has_any_permission(user, actions) or bool(
            self._journal_ids_for_actions(user, actions)
        )

    def user_has_permission_for_instance(self, user, action, instance):
        return self.user_has_any_permission_for_instance(user, [action], instance)

    def user_has_any_permission_for_instance(self, user, actions, instance):
        if super().user_has_any_permission_for_instance(user, actions, instance):
            return True
        instance_actions = set(actions).intersection(self.instance_actions)
        journal_ids = self._journal_ids_for_actions(user, instance_actions)
        return bool(
            journal_ids
            and self._scoped_images(user, journal_ids).filter(pk=instance.pk).exists()
        )

    def instances_user_has_any_permission_for(self, user, actions):
        queryset = super().instances_user_has_any_permission_for(user, actions)
        instance_actions = set(actions).intersection(self.instance_actions)
        journal_ids = self._journal_ids_for_actions(user, instance_actions)
        if journal_ids:
            queryset = queryset | self._scoped_images(user, journal_ids)
        return queryset.distinct()

    def users_with_any_permission(self, actions):
        users = super().users_with_any_permission(actions)
        dynamic_user_ids = self._dynamic_user_ids(actions)
        if dynamic_user_ids:
            users = users | get_user_model().objects.filter(pk__in=dynamic_user_ids)
        return users.distinct()

    def users_with_any_permission_for_instance(self, actions, instance):
        users = super().users_with_any_permission_for_instance(actions, instance)
        instance_actions = set(actions).intersection(self.instance_actions)
        for user_id in self._dynamic_user_ids(instance_actions):
            user = get_user_model().objects.get(pk=user_id)
            journal_ids = self._journal_ids_for_actions(user, instance_actions)
            if self._scoped_images(user, journal_ids).filter(pk=instance.pk).exists():
                users = users | get_user_model().objects.filter(pk=user_id)
        return users.distinct()

    def _dynamic_user_ids(self, actions) -> set[int]:
        from ai_author_forum.journals.models import JournalEditorAssignment

        rows = JournalEditorAssignment.objects.effective().values(
            "user_id", "journal_id", "role", "responsibilities"
        )
        return {row["user_id"] for row in rows if self._assignment_allows(row, actions)}

    def collections_user_has_any_permission_for(self, user, actions):
        collections = super().collections_user_has_any_permission_for(user, actions)
        if self._journal_ids_for_actions(user, actions):
            root = Collection.get_first_root_node()
            collections = collections | Collection.objects.filter(pk=root.pk)
        return collections.distinct()


def install_journal_image_permission_policy():
    from wagtail.images import permissions as wagtail_permissions

    policy = wagtail_permissions.permission_policy
    if not isinstance(policy, JournalImagePermissionPolicy):
        # Keep the same object so Wagtail modules that imported it retain this policy.
        policy.__class__ = JournalImagePermissionPolicy
    return policy
