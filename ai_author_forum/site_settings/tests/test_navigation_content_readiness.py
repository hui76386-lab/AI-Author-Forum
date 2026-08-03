from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from wagtail.models import Page, Site

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import (
    Journal,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.site_settings.admin_views import content_readiness_admin
from ai_author_forum.site_settings.models import (
    ColumnEmptyBehavior,
    NavigationItem,
    NavigationTargetType,
)
from ai_author_forum.site_settings.navigation import (
    ensure_main_navigation_set,
    get_navigation_context,
)


class PublicNavigationReadinessTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.navigation_set = ensure_main_navigation_set(site=self.site)
        self.journal = Journal.objects.create(
            name="Navigation Readiness Journal",
            slug="navigation-readiness-journal",
            az_group="N",
        )

    def visible_codes(self):
        context = get_navigation_context(site=self.site)
        return {item["code"] for group in context["groups"] for item in group["items"]}

    def activate_only(self, *items):
        ids = [item.pk for item in items]
        NavigationItem.objects.filter(
            group__navigation_set=self.navigation_set
        ).exclude(pk__in=ids).update(is_active=False)
        NavigationItem.objects.filter(pk__in=ids).update(
            is_active=True,
            is_visible=True,
            status="active",
        )
        for item in items:
            item.refresh_from_db()

    def create_article(self, title="Navigation Readiness Article"):
        slug = title.lower().replace(" ", "-")
        article = ArticlePage(
            title=title,
            slug=slug,
            static_slug=slug,
            abstract="Navigation readiness abstract",
            body=[("paragraph", "<p>Navigation readiness body</p>")],
            authors="Navigation readiness author",
            keywords="navigation",
            article_type=ArticlePage.ArticleType.NEWS,
            primary_journal=self.journal,
            review_status=ArticlePage.ReviewStatus.APPROVED,
        )
        Page.get_first_root_node().add_child(instance=article)
        article.save_revision().publish()
        return article

    def test_issue_links_are_hidden_until_published_content_exists(self):
        current_item = NavigationItem.objects.get(
            group__navigation_set=self.navigation_set,
            target_type=NavigationTargetType.CURRENT_ISSUE,
        )
        archive_item = NavigationItem.objects.get(
            group__navigation_set=self.navigation_set,
            target_type=NavigationTargetType.ISSUE_ARCHIVE,
        )
        self.activate_only(current_item, archive_item)

        self.assertNotIn(current_item.managed_code, self.visible_codes())
        self.assertNotIn(archive_item.managed_code, self.visible_codes())

        issue = PublicationIssue.objects.create(
            scope=PublicationIssueScope.MAIN_SITE,
            slug="navigation-issue",
            title="Navigation issue",
            publication_date=date(2026, 7, 31),
            status=PublicationIssueStatus.PUBLISHED,
        )
        self.assertNotIn(current_item.managed_code, self.visible_codes())
        self.assertIn(archive_item.managed_code, self.visible_codes())

        issue.is_current = True
        issue.save(update_fields=("is_current",))
        self.assertIn(current_item.managed_code, self.visible_codes())
        self.assertIn(archive_item.managed_code, self.visible_codes())

    def test_hide_navigation_column_appears_only_after_minimum_is_met(self):
        item = NavigationItem.objects.get(
            group__navigation_set=self.navigation_set,
            target_type=NavigationTargetType.CONTENT_COLUMN,
            code="news",
        )
        self.activate_only(item)
        item.code = "non-core-news"
        item.slug = "non-core-news"
        item.save(update_fields=("code", "slug"))
        config = item.content_column_config
        config.empty_behavior = ColumnEmptyBehavior.HIDE_NAVIGATION
        config.minimum_publish_items = 1
        config.save(update_fields=("empty_behavior", "minimum_publish_items"))

        self.assertNotIn(item.managed_code, self.visible_codes())

        article = self.create_article()
        ArticlePlacement.objects.create(
            article=article,
            slot=LayoutSlot.objects.get(code="column_list"),
            target_type=ArticlePlacement.TargetType.SECTION,
            target_slug=item.placement_target_slug,
        )
        self.assertIn(item.managed_code, self.visible_codes())

    def test_block_publish_column_is_not_hidden_by_navigation_layer(self):
        item = NavigationItem.objects.get(
            group__navigation_set=self.navigation_set,
            target_type=NavigationTargetType.CONTENT_COLUMN,
            code="news",
        )
        self.activate_only(item)
        config = item.content_column_config
        config.empty_behavior = ColumnEmptyBehavior.BLOCK_PUBLISH
        config.minimum_publish_items = 10
        config.save(update_fields=("empty_behavior", "minimum_publish_items"))

        self.assertIn(item.managed_code, self.visible_codes())

    def test_non_live_wagtail_page_is_hidden(self):
        item = NavigationItem.objects.filter(
            group__navigation_set=self.navigation_set,
            target_type=NavigationTargetType.WAGTAIL_PAGE,
        ).first()
        self.assertIsNotNone(item)
        self.activate_only(item)
        Page.objects.filter(pk=item.page_id).update(live=False)

        self.assertNotIn(item.managed_code, self.visible_codes())


class ContentReadinessAdminPermissionTests(TestCase):
    def test_superuser_can_view_content_readiness(self):
        user = get_user_model().objects.create_superuser(
            username="readiness-superuser",
            email="readiness-superuser@example.com",
            password="test-password",
        )
        client = Client()
        client.force_login(user)

        response = client.get(reverse("content_readiness_admin"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("result", response.context)

    def test_admin_user_without_readiness_permission_gets_403(self):
        user = get_user_model().objects.create_user(
            username="readiness-no-permission",
            email="readiness-no-permission@example.com",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin",
                codename="access_admin",
            )
        )
        user = get_user_model().objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("wagtailadmin.access_admin"))
        request = RequestFactory().get(reverse("content_readiness_admin"))
        request.user = user

        with self.assertRaises(PermissionDenied):
            content_readiness_admin(request)
