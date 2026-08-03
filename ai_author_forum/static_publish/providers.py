from collections import defaultdict
from dataclasses import dataclass, field, replace
from html import escape
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q
from django.test import Client
from django.utils import timezone, translation
from django.utils.text import slugify
from wagtail.fields import StreamField
from wagtail.models import Page, Site

from ai_author_forum.articles.display import resolve_article_image
from ai_author_forum.articles.integrations import get_site_settings
from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.articles.services import get_approved_articles
from ai_author_forum.images.models import CustomImage
from ai_author_forum.journals.models import (
    IssueArticle,
    JournalCategory,
    JournalCategoryPathRedirect,
    JournalCategoryStatus,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)
from ai_author_forum.journals.services import get_active_journals
from ai_author_forum.news.models import ArticlePage as LegacyNewsArticlePage
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.models import (
    ContentColumnConfig,
    NavigationEntryStatus,
    NavigationItem,
    NavigationItemPathRedirect,
    NavigationScope,
    NavigationSet,
    NavigationSetStatus,
    NavigationTargetType,
)
from ai_author_forum.static_publish.frontend import get_static_info_pages
from ai_author_forum.utils.i18n import (
    DEFAULT_LANGUAGE,
    localize_path,
    normalize_language,
)


def output_path_for_url(url):
    path = urlsplit(url).path.strip("/")
    if not path:
        return "index.html"
    if path.endswith(".html"):
        return path
    return f"{path}/index.html"


def _empty_dependencies():
    return {
        "journal_ids": [],
        "category_ids": [],
        "article_ids": [],
        "placement_ids": [],
        "content_column_config_ids": [],
        "image_ids": [],
        "issue_ids": [],
        "issue_article_ids": [],
        "navigation_set_ids": [],
        "navigation_item_ids": [],
    }


@dataclass(frozen=True)
class PublishTarget:
    url: str
    source: str
    target_type: str = "page"
    target_id: str = ""
    canonical_path: str = ""
    dependencies: dict = field(default_factory=_empty_dependencies)
    action: str = "upsert"
    http_status: int | None = None
    redirect_to: str = ""

    @property
    def output_path(self):
        return output_path_for_url(self.url)

    def render(self):
        if self.action == "delete":
            return b""
        if self.action == "redirect":
            target = escape(self.redirect_to, quote=True)
            return (
                '<!doctype html><html><head><meta charset="utf-8">'
                f'<title>Moved permanently</title><link rel="canonical" href="{target}">'
                f'<meta http-equiv="refresh" content="0;url={target}"></head>'
                f'<body><p>This page has moved permanently to <a href="{target}">{target}</a>.</p>'
                f"<script>location.replace({target!r});</script></body></html>"
            ).encode()
        # LocaleMiddleware activates the language requested by this render.
        # Restore the caller's thread-local language once the static target is captured.
        with translation.override(DEFAULT_LANGUAGE):
            response = Client().get(
                self.url,
                HTTP_HOST=self.host,
                HTTP_X_FORWARDED_PROTO="https",
            )
            if hasattr(response, "render"):
                response.render()
        if response.status_code != 200:
            raise RuntimeError(f"GET {self.url} returned HTTP {response.status_code}")
        return bytes(response.content)

    @property
    def host(self):
        site = Site.objects.filter(is_default_site=True).first() or Site.objects.first()
        return site.hostname if site else "localhost"


class WagtailPageTargetProvider:
    """Discover Wagtail pages and every canonical fixed-HTML business target."""

    def _prepare_navigation_dependency_index(self):
        nav_sets = list(
            NavigationSet.objects.filter(
                Q(journal__isnull=True) | Q(journal__status="active"),
                status=NavigationSetStatus.ACTIVE,
                is_template=False,
            ).select_related("journal")
        )
        self._main_navigation_set_ids = [
            nav_set.pk
            for nav_set in nav_sets
            if nav_set.scope == NavigationScope.MAIN_SITE
        ]
        self._journal_navigation_set_ids = {
            nav_set.journal_id: [nav_set.pk]
            for nav_set in nav_sets
            if nav_set.scope == NavigationScope.JOURNAL and nav_set.journal_id
        }
        self._journal_slug_to_id = {
            nav_set.journal.slug: nav_set.journal_id
            for nav_set in nav_sets
            if nav_set.journal_id
        }
        page_navigation_set_ids = defaultdict(set)
        page_navigation_item_ids = defaultdict(set)
        for page_id, item_id, nav_set_id in NavigationItem.objects.filter(
            group__navigation_set__in=nav_sets,
            target_type=NavigationTargetType.WAGTAIL_PAGE,
            page__isnull=False,
            status__in=(NavigationEntryStatus.ACTIVE, NavigationEntryStatus.HIDDEN),
        ).values_list("page_id", "pk", "group__navigation_set_id"):
            page_navigation_set_ids[page_id].add(nav_set_id)
            page_navigation_item_ids[page_id].add(item_id)
        self._page_navigation_set_ids = page_navigation_set_ids
        self._page_navigation_item_ids = page_navigation_item_ids

    def _navigation_dependencies(self, *, journal_id=None, path="", page_id=None):
        if not hasattr(self, "_main_navigation_set_ids"):
            return {"navigation_set_ids": [], "navigation_item_ids": []}
        if journal_id is None and path.startswith("/journals/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2:
                journal_id = self._journal_slug_to_id.get(parts[1])
        nav_set_ids = set(
            self._journal_navigation_set_ids.get(journal_id, ())
            if journal_id
            else self._main_navigation_set_ids
        )
        item_ids = set()
        if page_id:
            nav_set_ids.update(self._page_navigation_set_ids.get(page_id, ()))
            item_ids.update(self._page_navigation_item_ids.get(page_id, ()))
        return {
            "navigation_set_ids": sorted(nav_set_ids),
            "navigation_item_ids": sorted(item_ids),
        }

    @staticmethod
    def _homepage_placement_dependencies(publication_time):
        slot_codes = (
            "home_hero",
            "home_visual_stories",
            "home_featured",
            "latest_ai_article",
        )
        placements = list(
            ArticlePlacement.objects.available(at=publication_time)
            .filter(
                target_type=ArticlePlacement.TargetType.MAIN_SITE,
                target_slug="",
                slot__code__in=slot_codes,
            )
            .select_related(
                "slot",
                "article",
                "article__featured_image",
                "override_image",
            )
            .order_by("slot__sort_order", "-is_pinned", "sort_order", "pk")
        )
        site_settings = get_site_settings()
        image_ids = set()
        for placement in placements:
            visual = resolve_article_image(
                placement.article,
                placement=placement,
                site_settings=site_settings,
            )
            if visual.image is not None and getattr(visual.image, "pk", None):
                image_ids.add(visual.image.pk)
        return {
            "article_ids": sorted({item.article_id for item in placements}),
            "placement_ids": sorted({item.pk for item in placements}),
            "image_ids": sorted(image_ids),
        }

    @staticmethod
    def _wagtail_page_image_ids(page):
        image_ids = {
            image_id
            for image_id in (
                getattr(page, "listing_image_id", None),
                getattr(page, "social_image_id", None),
            )
            if image_id
        }
        for stream_field in page._meta.get_fields():
            if not isinstance(stream_field, StreamField):
                continue
            value = getattr(page, stream_field.name, None)
            if value is None:
                continue
            for (
                model,
                object_id,
                _model_path,
                _content_path,
            ) in stream_field.extract_references(value):
                if model is CustomImage:
                    image_ids.add(int(object_id))
        return sorted(image_ids)

    def get_targets(self, paths=None):
        publication_time = timezone.now()
        targets = self._localized_targets(
            self._all_targets(publication_time=publication_time)
        )
        if paths:
            normalized = self._requested_output_paths(paths)
            selected = [
                target for target in targets if target.output_path in normalized
            ]
            selected = self._expand_article_reverse_dependencies(targets, selected)
            matched = {target.output_path for target in selected}
            selected.extend(self._deletion_targets(normalized - matched))
            return selected
        return targets

    @staticmethod
    def _expand_article_reverse_dependencies(targets, selected):
        """Include every static page that references a directly requested article.

        Article save events request the canonical article detail path. The provider owns
        dependency expansion so callers do not hardcode homepage, journal, category or
        managed-column paths. Placement-only homepage requests do not fan out through
        every article shown on the homepage.
        """
        article_ids = set()
        image_ids = set()
        for target in selected:
            if target.target_type != "article_page":
                continue
            dependencies = target.dependencies or {}
            article_ids.update(dependencies.get("article_ids", ()))
            image_ids.update(dependencies.get("image_ids", ()))
        if not article_ids and not image_ids:
            return selected

        selected_paths = {target.output_path for target in selected}
        expanded = list(selected)
        for target in targets:
            if target.output_path in selected_paths:
                continue
            dependencies = target.dependencies or {}
            if article_ids.intersection(dependencies.get("article_ids", ())) or (
                image_ids and image_ids.intersection(dependencies.get("image_ids", ()))
            ):
                expanded.append(target)
                selected_paths.add(target.output_path)
        return expanded

    def _language_codes(self):
        return tuple(
            dict.fromkeys(
                normalize_language(code)
                for code, _label in getattr(settings, "LANGUAGES", ())
            )
        ) or (DEFAULT_LANGUAGE,)

    def _localized_targets(self, targets):
        localized = []
        for target in targets:
            for language_code in self._language_codes():
                url = localize_path(target.url, language_code)
                canonical_path = localize_path(
                    target.canonical_path or target.url, language_code
                )
                redirect_to = (
                    localize_path(target.redirect_to, language_code)
                    if target.redirect_to
                    else ""
                )
                if language_code == DEFAULT_LANGUAGE:
                    localized.append(
                        replace(
                            target,
                            url=url,
                            canonical_path=canonical_path,
                            redirect_to=redirect_to,
                        )
                    )
                else:
                    localized.append(
                        replace(
                            target,
                            url=url,
                            source=f"{target.source}:lang:{language_code}",
                            target_id=f"{target.target_id}:lang:{language_code}",
                            canonical_path=canonical_path,
                            redirect_to=redirect_to,
                        )
                    )
        return localized

    def _requested_output_paths(self, paths):
        normalized = {output_path_for_url(path) for path in paths}
        variants = set()
        for output_path in normalized:
            if output_path == "index.html":
                url = "/"
            elif output_path.endswith("/index.html"):
                url = f"/{output_path[: -len('/index.html')]}/"
            else:
                url = f"/{output_path}"
            for language_code in self._language_codes():
                variants.add(output_path_for_url(localize_path(url, language_code)))
        return variants

    def _all_targets(self, *, publication_time):
        self._prepare_navigation_dependency_index()
        pages = Page.objects.live().public().specific().order_by("path")
        homepage_dependencies = self._homepage_placement_dependencies(publication_time)
        targets = []
        for page in pages:
            # Wagtail's depth-one root is a container, not a public page target.
            if page.depth == 1:
                continue
            # ArticlePage has one canonical production path independent of its page-tree parent.
            if isinstance(page, ArticlePage | LegacyNewsArticlePage):
                continue
            page_url = page.url or "/"
            targets.append(
                PublishTarget(
                    page_url,
                    f"wagtail.Page:{page.pk}",
                    target_type="wagtail_page",
                    target_id=f"page:{page.pk}",
                    canonical_path=page_url,
                    dependencies={
                        **_empty_dependencies(),
                        **self._navigation_dependencies(path=page_url, page_id=page.pk),
                        "article_ids": (
                            homepage_dependencies["article_ids"]
                            if page_url == "/"
                            else []
                        ),
                        "placement_ids": (
                            homepage_dependencies["placement_ids"]
                            if page_url == "/"
                            else []
                        ),
                        "image_ids": sorted(
                            set(self._wagtail_page_image_ids(page))
                            | (
                                set(homepage_dependencies["image_ids"])
                                if page_url == "/"
                                else set()
                            )
                        ),
                    },
                )
            )

        targets.append(
            PublishTarget(
                "/journals/",
                "journals.a-z",
                target_type="journal_index",
                target_id="journals:a-z",
                canonical_path="/journals/",
                dependencies={
                    **_empty_dependencies(),
                    **self._navigation_dependencies(path="/journals/"),
                },
            )
        )
        journals = list(get_active_journals())
        targets.extend(self._journal_targets(journals))

        targets.extend(self._category_targets(publication_time=publication_time))
        targets.extend(self._category_redirect_targets())
        managed_navigation_targets = self._managed_navigation_targets(
            publication_time=publication_time
        )
        targets.extend(self._issue_detail_targets())
        targets.extend(self._navigation_redirect_targets())
        targets.extend(
            PublishTarget(
                page["path"],
                f"pages.static-info:{page['group_slug']}:{page['slug']}",
                target_type="static_info_page",
                target_id=f"info:{page['group_slug']}:{page['slug']}",
                canonical_path=page["path"],
                dependencies={
                    **_empty_dependencies(),
                    **self._navigation_dependencies(path=page["path"]),
                },
            )
            for page in get_static_info_pages()
        )
        searchable_articles = list(
            get_approved_articles(at=publication_time)
            .select_related("primary_journal", "live_revision")
            .order_by("pk")
        )
        self._prepare_article_dependency_index(searchable_articles)
        targets.extend(
            PublishTarget(
                f"/articles/{article.static_slug}/",
                f"articles.ArticlePage:{article.pk}",
                target_type="article_page",
                target_id=f"article:{article.pk}",
                canonical_path=f"/articles/{article.static_slug}/",
                dependencies=self._article_dependencies(article),
            )
            for article in searchable_articles
        )
        searchable_article_ids = [article.pk for article in searchable_articles]
        search_placement_ids = list(
            ArticlePlacement.objects.available(at=publication_time)
            .filter(article_id__in=searchable_article_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
            .distinct()
        )
        targets.append(
            PublishTarget(
                "/search/",
                "search.static-article-index",
                target_type="search_page",
                target_id="search:articles",
                canonical_path="/search/",
                dependencies={
                    **_empty_dependencies(),
                    "journal_ids": sorted(
                        {article.primary_journal_id for article in searchable_articles}
                    ),
                    "article_ids": searchable_article_ids,
                    "placement_ids": search_placement_ids,
                    **self._navigation_dependencies(path="/search/"),
                },
            )
        )
        # Internal navigation needs a static target only where no canonical
        # business target already owns that output path. Existing targets carry
        # the navigation dependencies for their path, so emitting both would
        # create an invalid manifest collision without improving invalidation.
        owned_output_paths = {target.output_path for target in targets}
        targets.extend(
            target
            for target in managed_navigation_targets
            if target.output_path not in owned_output_paths
        )
        return targets

    def _journal_targets(self, journals):
        """Build journal targets with one navigation query for any journal count."""
        if not journals:
            return []
        navigation_by_journal = defaultdict(list)
        navigation_rows = (
            JournalCategory.objects.filter(
                journal_id__in=[journal.pk for journal in journals],
                status=JournalCategoryStatus.ACTIVE,
                show_in_navigation=True,
                depth__lte=2,
            )
            .order_by("journal_id", "parent_id", "sort_order", "name", "pk")
            .values_list("journal_id", "pk")
        )
        for journal_id, category_id in navigation_rows:
            navigation_by_journal[journal_id].append(category_id)
        return [
            PublishTarget(
                f"/journals/{journal.slug}/",
                f"journals.Journal:{journal.pk}",
                target_type="journal_page",
                target_id=f"journal:{journal.pk}",
                canonical_path=f"/journals/{journal.slug}/",
                dependencies={
                    **_empty_dependencies(),
                    "journal_ids": [journal.pk],
                    "category_ids": navigation_by_journal[journal.pk],
                    **self._navigation_dependencies(journal_id=journal.pk),
                },
            )
            for journal in journals
        ]

    def _category_targets(self, *, publication_time):
        """Build all category pages from bounded bulk queries.

        The previous implementation queried articles and placements once per
        category/page. At the documented ceiling of 200 journals x 100
        categories that became an unbounded N+1. This implementation loads the
        Journal-partitioned trees, eligible placements, and article ordering in
        bulk, then calculates descendants, pagination, and dependencies in memory.
        """
        page_size = int(getattr(settings, "STATIC_CATEGORY_PAGE_SIZE", 20))
        if page_size < 1:
            raise ValueError("STATIC_CATEGORY_PAGE_SIZE must be at least 1")

        all_categories = list(
            JournalCategory.objects.filter(journal__status="active")
            .select_related("journal")
            .order_by("journal_id", "depth", "path_cache", "pk")
        )
        target_categories = [
            category
            for category in all_categories
            if category.status
            in (JournalCategoryStatus.ACTIVE, JournalCategoryStatus.HIDDEN)
            and category.generate_static_page
        ]
        if not target_categories:
            return []

        children_by_parent = defaultdict(list)
        for category in all_categories:
            children_by_parent[category.parent_id].append(category.pk)

        descendants_by_category = {}

        def descendant_ids(category_id):
            cached = descendants_by_category.get(category_id)
            if cached is not None:
                return cached
            result = [category_id]
            for child_id in children_by_parent.get(category_id, ()):
                result.extend(descendant_ids(child_id))
            descendants_by_category[category_id] = result
            return result

        placements_by_category = defaultdict(list)
        placement_rows = list(
            ArticlePlacement.objects.available(at=publication_time)
            .filter(
                target_type=ArticlePlacement.TargetType.CATEGORY,
                target_category_id__in=[category.pk for category in all_categories],
                source=ArticlePlacement.Source.SYSTEM,
                placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
            )
            .order_by("target_category_id", "article_id", "pk")
            .values("pk", "article_id", "target_category_id")
        )
        for placement in placement_rows:
            placements_by_category[placement["target_category_id"]].append(placement)

        placed_article_ids = {row["article_id"] for row in placement_rows}
        ordered_article_ids = (
            list(
                ArticlePage.objects.filter(pk__in=placed_article_ids)
                .order_by("-first_published_at", "-pk")
                .values_list("pk", flat=True)
            )
            if placed_article_ids
            else []
        )

        targets = []
        for category in target_categories:
            category_ids = (
                descendant_ids(category.pk)
                if category.aggregate_descendants
                else [category.pk]
            )
            relevant_placements = [
                placement
                for category_id in category_ids
                for placement in placements_by_category.get(category_id, ())
            ]
            relevant_article_ids = {
                placement["article_id"] for placement in relevant_placements
            }
            article_ids = [
                article_id
                for article_id in ordered_article_ids
                if article_id in relevant_article_ids
            ]
            page_count = max(1, (len(article_ids) + page_size - 1) // page_size)
            canonical = category.get_absolute_url()
            for page_number in range(1, page_count + 1):
                offset = (page_number - 1) * page_size
                page_article_ids = article_ids[offset : offset + page_size]
                page_article_id_set = set(page_article_ids)
                placement_ids = sorted(
                    placement["pk"]
                    for placement in relevant_placements
                    if placement["article_id"] in page_article_id_set
                )
                url = (
                    canonical if page_number == 1 else f"{canonical}page/{page_number}/"
                )
                targets.append(
                    PublishTarget(
                        url,
                        f"journals.JournalCategory:{category.pk}:page:{page_number}",
                        target_type="category_page",
                        target_id=f"category:{category.pk}:page:{page_number}",
                        canonical_path=url,
                        dependencies={
                            **_empty_dependencies(),
                            "journal_ids": [category.journal_id],
                            "category_ids": category_ids,
                            "article_ids": page_article_ids,
                            "placement_ids": placement_ids,
                            **self._navigation_dependencies(
                                journal_id=category.journal_id
                            ),
                        },
                    )
                )
        return targets

    def _managed_navigation_targets(self, *, publication_time):
        targets = []
        seen_urls = set()
        nav_sets = NavigationSet.objects.filter(
            Q(journal__isnull=True) | Q(journal__status="active"),
            status=NavigationSetStatus.ACTIVE,
            is_template=False,
        ).select_related("journal")
        items = list(
            NavigationItem.objects.filter(
                group__navigation_set__in=nav_sets,
                is_active=True,
                status__in=(NavigationEntryStatus.ACTIVE, NavigationEntryStatus.HIDDEN),
            )
            .select_related(
                "group__navigation_set__journal",
                "content_column_config",
                "content_column_config__cover_image",
            )
            .order_by(
                "group__navigation_set_id", "group__sort_order", "sort_order", "pk"
            )
        )
        column_target_slugs = [
            item.placement_target_slug
            for item in items
            if item.target_type == NavigationTargetType.CONTENT_COLUMN
        ]
        placements_by_target = defaultdict(list)
        for row in (
            ArticlePlacement.objects.available(at=publication_time)
            .filter(
                slot__code__in=(
                    "column_featured",
                    "column_secondary",
                    "column_list",
                    "column_sidebar",
                ),
                target_type=ArticlePlacement.TargetType.SECTION,
                target_slug__in=column_target_slugs,
            )
            .order_by(
                "target_slug",
                "slot__code",
                "-is_pinned",
                "sort_order",
                "-created_at",
                "pk",
            )
            .values(
                "pk",
                "article_id",
                "target_slug",
                "slot__code",
                "article__article_type",
                "article__first_published_at",
                "article__featured_image_id",
                "override_image_id",
            )
        ):
            placements_by_target[row["target_slug"]].append(row)
        published_issues = list(
            PublicationIssue.objects.filter(status=PublicationIssueStatus.PUBLISHED)
            .select_related("cover_image")
            .order_by("-publication_date", "-pk")
        )
        issues_by_scope = defaultdict(list)
        for issue in published_issues:
            issues_by_scope[(issue.scope, issue.journal_id)].append(issue)
        issue_assignments = defaultdict(list)
        for row in IssueArticle.objects.filter(issue__in=published_issues).values(
            "pk", "issue_id", "article_id"
        ):
            issue_assignments[row["issue_id"]].append(row)

        for item in items:
            if not item.is_visible and not item.allow_direct_access:
                continue
            nav_set = item.group.navigation_set
            journal_id = nav_set.journal_id
            base_dependencies = {
                **_empty_dependencies(),
                "journal_ids": [journal_id] if journal_id else [],
                "navigation_set_ids": [nav_set.pk],
                "navigation_item_ids": [item.pk],
            }
            if item.target_type == NavigationTargetType.CONTENT_COLUMN:
                try:
                    config = item.content_column_config
                except ContentColumnConfig.DoesNotExist as exc:
                    raise RuntimeError(
                        f"Content column navigation item {item.pk} has no ContentColumnConfig"
                    ) from exc
                placements = placements_by_target[item.placement_target_slug]

                def add_column_pages(
                    *,
                    article_type="",
                    year=None,
                    placements=placements,
                    config=config,
                    item=item,
                    base_dependencies=base_dependencies,
                ):
                    selected = [
                        row
                        for row in placements
                        if (
                            not article_type
                            or slugify(row["article__article_type"]) == article_type
                        )
                        and (
                            not year
                            or (
                                row["article__first_published_at"]
                                and row["article__first_published_at"].year == year
                            )
                        )
                    ]
                    list_count = sum(
                        row["slot__code"] == "column_list" for row in selected
                    )
                    page_size = max(1, int(config.page_size or 20))
                    page_count = max(1, (list_count + page_size - 1) // page_size)
                    base_url = item.target_url
                    if article_type:
                        base_url += f"type/{article_type}/"
                    if year:
                        base_url += f"year/{year}/"
                    shared_rows = [
                        row for row in selected if row["slot__code"] != "column_list"
                    ]
                    list_rows = [
                        row for row in selected if row["slot__code"] == "column_list"
                    ]
                    for page_number in range(1, page_count + 1):
                        url = (
                            base_url
                            if page_number == 1
                            else f"{base_url}page/{page_number}/"
                        )
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        offset = (page_number - 1) * page_size
                        page_rows = shared_rows + list_rows[offset : offset + page_size]
                        # Hero, secondary, and sidebar dependencies apply to every
                        # fixed page; list dependencies are limited to that page slice.
                        image_ids = {
                            row["override_image_id"]
                            or row["article__featured_image_id"]
                            for row in page_rows
                            if row["override_image_id"]
                            or row["article__featured_image_id"]
                        }
                        targets.append(
                            PublishTarget(
                                url,
                                f"site_settings.NavigationItem:{item.pk}:page:{page_number}",
                                target_type="managed_content_column",
                                target_id=(
                                    f"navigation_item:{item.pk}:type:{article_type or 'all'}:"
                                    f"year:{year or 'all'}:page:{page_number}"
                                ),
                                canonical_path=url,
                                dependencies={
                                    **base_dependencies,
                                    "category_ids": (
                                        [config.category_id]
                                        if config.category_id
                                        else []
                                    ),
                                    "content_column_config_ids": [config.pk],
                                    "article_ids": sorted(
                                        {row["article_id"] for row in page_rows}
                                    ),
                                    "placement_ids": sorted(
                                        row["pk"] for row in page_rows
                                    ),
                                    "image_ids": sorted(image_ids),
                                },
                            )
                        )

                add_column_pages()
                types = sorted(
                    {
                        slugify(row["article__article_type"])
                        for row in placements
                        if row["article__article_type"]
                    }
                )
                years = sorted(
                    {
                        row["article__first_published_at"].year
                        for row in placements
                        if row["article__first_published_at"]
                    },
                    reverse=True,
                )
                if config.enable_type_filter:
                    for article_type in types:
                        add_column_pages(article_type=article_type)
                if config.enable_year_filter:
                    for year in years:
                        add_column_pages(year=year)
                if config.enable_type_filter and config.enable_year_filter:
                    for article_type in types:
                        for year in years:
                            if any(
                                slugify(row["article__article_type"]) == article_type
                                and row["article__first_published_at"]
                                and row["article__first_published_at"].year == year
                                for row in placements
                            ):
                                add_column_pages(article_type=article_type, year=year)
            elif item.target_type == NavigationTargetType.INTERNAL_PATH:
                url = item.target_url
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                targets.append(
                    PublishTarget(
                        url,
                        f"site_settings.NavigationItem:{item.pk}",
                        target_type="managed_navigation_info",
                        target_id=f"navigation_item:{item.pk}",
                        canonical_path=url,
                        dependencies=base_dependencies,
                    )
                )
            elif item.target_type in {
                NavigationTargetType.CURRENT_ISSUE,
                NavigationTargetType.ISSUE_ARCHIVE,
            }:
                scope_key = (
                    (
                        PublicationIssueScope.JOURNAL
                        if journal_id
                        else PublicationIssueScope.MAIN_SITE
                    ),
                    journal_id,
                )
                issues = list(issues_by_scope[scope_key])
                if item.target_type == NavigationTargetType.CURRENT_ISSUE:
                    issues = [issue for issue in issues if issue.is_current]
                if not issues:
                    continue
                url = item.target_url
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                issue_ids = [issue.pk for issue in issues]
                assignments = [
                    row for issue_id in issue_ids for row in issue_assignments[issue_id]
                ]
                target_type = (
                    "journal_current_issue"
                    if item.target_type == NavigationTargetType.CURRENT_ISSUE
                    else "journal_issue_archive"
                )
                if not journal_id:
                    target_type = target_type.replace("journal_", "main_")
                targets.append(
                    PublishTarget(
                        url,
                        f"site_settings.NavigationItem:{item.pk}",
                        target_type=target_type,
                        target_id=f"navigation_item:{item.pk}",
                        canonical_path=url,
                        dependencies={
                            **base_dependencies,
                            "issue_ids": issue_ids,
                            "issue_article_ids": sorted(
                                row["pk"] for row in assignments
                            ),
                            "article_ids": sorted(
                                {row["article_id"] for row in assignments}
                            ),
                            "image_ids": sorted(
                                {
                                    issue.cover_image_id
                                    for issue in issues
                                    if issue.cover_image_id
                                }
                            ),
                        },
                    )
                )
        return targets

    def _issue_detail_targets(self):
        targets = []
        issues = list(
            PublicationIssue.objects.filter(status=PublicationIssueStatus.PUBLISHED)
            .select_related("journal", "cover_image")
            .order_by("scope", "journal_id", "-publication_date", "-pk")
        )
        scope_groups = defaultdict(list)
        for issue in issues:
            scope_groups[(issue.scope, issue.journal_id)].append(issue)
        assignments = defaultdict(list)
        for row in IssueArticle.objects.filter(issue__in=issues).values(
            "pk", "issue_id", "article_id"
        ):
            assignments[row["issue_id"]].append(row)
        for issue in issues:
            group = scope_groups[(issue.scope, issue.journal_id)]
            position = next(i for i, value in enumerate(group) if value.pk == issue.pk)
            neighbour_ids = []
            if position > 0:
                neighbour_ids.append(group[position - 1].pk)
            if position + 1 < len(group):
                neighbour_ids.append(group[position + 1].pk)
            rows = assignments[issue.pk]
            targets.append(
                PublishTarget(
                    issue.scope_path,
                    f"journals.PublicationIssue:{issue.pk}",
                    target_type="issue_detail",
                    target_id=f"issue:{issue.pk}",
                    canonical_path=issue.scope_path,
                    dependencies={
                        **_empty_dependencies(),
                        "journal_ids": [issue.journal_id] if issue.journal_id else [],
                        "issue_ids": [issue.pk, *neighbour_ids],
                        "issue_article_ids": sorted(row["pk"] for row in rows),
                        "article_ids": sorted({row["article_id"] for row in rows}),
                        "image_ids": (
                            [issue.cover_image_id] if issue.cover_image_id else []
                        ),
                        **self._navigation_dependencies(
                            journal_id=issue.journal_id,
                            path=issue.scope_path,
                        ),
                    },
                )
            )
        return targets

    def _navigation_redirect_targets(self):
        targets = []
        for redirect in NavigationItemPathRedirect.objects.filter(
            is_active=True
        ).select_related("navigation_item__group__navigation_set"):
            targets.append(
                PublishTarget(
                    redirect.old_path,
                    f"site_settings.NavigationItemPathRedirect:{redirect.pk}",
                    target_type="managed_navigation_redirect",
                    target_id=f"navigation_redirect:{redirect.pk}",
                    canonical_path=redirect.new_path,
                    dependencies={
                        **_empty_dependencies(),
                        "navigation_set_ids": (
                            [redirect.navigation_item.group.navigation_set_id]
                            if redirect.navigation_item.group_id
                            else []
                        ),
                        "navigation_item_ids": [redirect.navigation_item_id],
                    },
                    action="redirect",
                    http_status=redirect.http_status,
                    redirect_to=redirect.new_path,
                )
            )
        return targets

    def _category_redirect_targets(self):
        targets = []
        redirects = (
            JournalCategoryPathRedirect.objects.filter(is_active=True)
            .select_related("journal", "category")
            .order_by("journal_id", "old_path", "pk")
        )
        for redirect in redirects:
            targets.append(
                PublishTarget(
                    redirect.old_path,
                    f"journals.JournalCategoryPathRedirect:{redirect.pk}",
                    target_type="category_redirect",
                    target_id=f"category_redirect:{redirect.pk}",
                    canonical_path=redirect.new_path,
                    dependencies={
                        **_empty_dependencies(),
                        "journal_ids": [redirect.journal_id],
                        "category_ids": [redirect.category_id],
                    },
                    action="redirect",
                    http_status=redirect.http_status,
                    redirect_to=redirect.new_path,
                )
            )
        return targets

    def _prepare_article_dependency_index(self, articles):
        article_ids = [article.pk for article in articles]
        category_ids_by_article = defaultdict(list)
        articles_without_live_revision = []

        for article in articles:
            if not article.live_revision_id:
                articles_without_live_revision.append(article.pk)
                continue
            revision_content = article.live_revision.content or {}
            for assignment in revision_content.get("category_assignments", ()) or ():
                if not isinstance(assignment, dict):
                    continue
                category_id = assignment.get("category_id", assignment.get("category"))
                if category_id is not None:
                    category_ids_by_article[article.pk].append(category_id)

        if articles_without_live_revision:
            for article_id, category_id in (
                ArticleCategoryAssignment.objects.filter(
                    article_id__in=articles_without_live_revision
                )
                .order_by("article_id", "sort_order", "pk")
                .values_list("article_id", "category_id")
            ):
                category_ids_by_article[article_id].append(category_id)

        placement_ids_by_article = defaultdict(list)
        for article_id, placement_id in (
            ArticlePlacement.objects.filter(
                article_id__in=article_ids,
                target_type=ArticlePlacement.TargetType.CATEGORY,
                source=ArticlePlacement.Source.SYSTEM,
                placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
                is_active=True,
            )
            .order_by("article_id", "pk")
            .values_list("article_id", "pk")
        ):
            placement_ids_by_article[article_id].append(placement_id)

        self._article_category_ids = {
            article_id: list(dict.fromkeys(category_ids))
            for article_id, category_ids in category_ids_by_article.items()
        }
        self._article_placement_ids = dict(placement_ids_by_article)

    def _article_dependencies(self, article):
        if hasattr(self, "_article_category_ids"):
            category_ids = self._article_category_ids.get(article.pk, [])
        else:
            source = (
                article.live_revision.as_object()
                if article.live_revision_id
                else article
            )
            category_ids = list(
                source.category_assignments.values_list("category_id", flat=True)
            )
        if hasattr(self, "_article_placement_ids"):
            placement_ids = self._article_placement_ids.get(article.pk, [])
        else:
            placement_ids = list(
                ArticlePlacement.objects.filter(
                    article_id=article.pk,
                    target_type=ArticlePlacement.TargetType.CATEGORY,
                    source=ArticlePlacement.Source.SYSTEM,
                    placement_kind=ArticlePlacement.PlacementKind.AUTOMATIC_LISTING,
                    is_active=True,
                ).values_list("pk", flat=True)
            )
        return {
            "journal_ids": [article.primary_journal_id],
            "category_ids": category_ids,
            "article_ids": [article.pk],
            "placement_ids": placement_ids,
            **self._navigation_dependencies(journal_id=article.primary_journal_id),
        }

    def _deletion_targets(self, output_paths):
        if not output_paths:
            return []
        from ai_author_forum.static_publish.models import StaticManifest

        active = StaticManifest.objects.filter(is_active=True).first()
        prior_targets = (active.metadata or {}).get("targets", []) if active else []
        by_path = {item.get("output_path"): item for item in prior_targets}
        targets = []
        for output_path in sorted(output_paths):
            prior = by_path.get(output_path)
            if not prior or prior.get("target_type") not in {
                "category_page",
                "category_redirect",
                "managed_content_column",
                "managed_navigation_redirect",
                "journal_current_issue",
                "journal_issue_archive",
                "main_current_issue",
                "main_issue_archive",
                "issue_detail",
            }:
                continue
            canonical = prior.get("canonical_path") or f"/{output_path}"
            targets.append(
                PublishTarget(
                    f"/{output_path}",
                    prior.get("target_id", "static.delete"),
                    target_type=prior.get("target_type", "page"),
                    target_id=prior.get("target_id", f"delete:{output_path}"),
                    canonical_path=canonical,
                    dependencies=prior.get("dependencies") or _empty_dependencies(),
                    action="delete",
                )
            )
        return targets
