from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.db.models.deletion import ProtectedError


@dataclass(frozen=True)
class ImageReference:
    source: str
    label: str
    object_id: str = ""
    field_name: str = ""
    page_path: str = ""

    @property
    def description(self):
        details = []
        if self.field_name:
            details.append(self.field_name)
        if self.page_path:
            details.append(self.page_path)
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{self.label}{suffix}"


class ImageReferenceProtectedError(ProtectedError):
    def __init__(self, image, references):
        self.image = image
        self.references = tuple(references)
        summary = "; ".join(reference.description for reference in self.references[:5])
        if len(self.references) > 5:
            summary += f"; and {len(self.references) - 5} more"
        super().__init__(
            f"Image '{image.title}' cannot be deleted because it is referenced: {summary}",
            {image},
        )


def _normalise_asset_path(value):
    if not value:
        return ""
    path = unquote(urlsplit(str(value)).path).replace("\\", "/").lstrip("/")
    return Path(path).as_posix() if path else ""


def get_image_asset_paths(image):
    paths = set()
    media_prefix = _normalise_asset_path(settings.MEDIA_URL).rstrip("/")

    def add(value, *, storage_name=False):
        path = _normalise_asset_path(value)
        if not path:
            return
        paths.add(path)
        if storage_name and media_prefix and not path.startswith(f"{media_prefix}/"):
            paths.add(f"{media_prefix}/{path}")

    add(getattr(image.file, "name", ""), storage_name=True)
    try:
        add(image.file.url)
    except (NotImplementedError, ValueError):
        pass

    for rendition in image.renditions.all().only("file"):
        add(getattr(rendition.file, "name", ""), storage_name=True)
        try:
            add(rendition.file.url)
        except (NotImplementedError, ValueError):
            pass
    return paths


def _model_references(image):
    from ai_author_forum.journals.models import Journal, PublicationIssue, StaticArticle
    from ai_author_forum.placements.models import ArticlePlacement
    from ai_author_forum.site_settings.models import ContentColumnConfig, SiteSettings

    references = []
    try:
        from ai_author_forum.articles.image_references import (
            get_article_featured_image_references,
        )
    except ImportError:
        article_featured_references = []
    else:
        article_featured_references = get_article_featured_image_references(image)

    for reference in article_featured_references:
        references.append(
            ImageReference(
                source=reference["reference_type"],
                label=f"Article cover: {reference['title']}",
                object_id=str(reference["article_id"]),
                field_name="featured_image",
                page_path=reference.get("static_path", ""),
            )
        )

    for journal in Journal.objects.filter(cover_image_id=image.pk).only("pk", "name"):
        references.append(
            ImageReference(
                source="journal.cover_image",
                label=f"Journal: {journal.name}",
                object_id=str(journal.pk),
                field_name="cover_image",
            )
        )
    for journal in Journal.objects.filter(metrics_image_id=image.pk).only("pk", "name"):
        references.append(
            ImageReference(
                source="journal.metrics_image",
                label=f"Journal: {journal.name}",
                object_id=str(journal.pk),
                field_name="metrics_image",
            )
        )
    for journal in Journal.objects.filter(hero_image_id=image.pk).only("pk", "name"):
        references.append(
            ImageReference(
                source="journal.hero_image",
                label=f"Journal: {journal.name}",
                object_id=str(journal.pk),
                field_name="hero_image",
                page_path=journal.static_site_path,
            )
        )
    for issue in PublicationIssue.objects.filter(cover_image_id=image.pk).only(
        "pk", "title", "slug", "scope", "journal_id"
    ):
        references.append(
            ImageReference(
                source="publication_issue.cover_image",
                label=f"Publication issue: {issue.title}",
                object_id=str(issue.pk),
                field_name="cover_image",
                page_path=issue.scope_path,
            )
        )
    for placement in (
        ArticlePlacement.objects.filter(override_image_id=image.pk)
        .select_related("article", "slot")
        .only("pk", "article__title", "slot__code")
    ):
        references.append(
            ImageReference(
                source="placement.override_image",
                label=f"Placement: {placement.article.title}",
                object_id=str(placement.pk),
                field_name="override_image",
                page_path=placement.slot.code,
            )
        )
    for article in (
        StaticArticle.objects.filter(cover_image_id=image.pk)
        .select_related("journal")
        .only("pk", "title", "journal__slug")
    ):
        references.append(
            ImageReference(
                source="static_article.cover_image",
                label=f"Imported article: {article.title}",
                object_id=str(article.pk),
                field_name="cover_image",
                page_path=article.get_absolute_url(),
            )
        )
    for site_settings in (
        SiteSettings.objects.filter(logo_id=image.pk)
        .select_related("site")
        .only("pk", "site__hostname")
    ):
        references.append(
            ImageReference(
                source="site_settings.logo",
                label=f"Site settings: {site_settings.site.hostname}",
                object_id=str(site_settings.pk),
                field_name="logo",
            )
        )
    for site_settings in (
        SiteSettings.objects.filter(default_image_id=image.pk)
        .select_related("site")
        .only("pk", "site__hostname")
    ):
        references.append(
            ImageReference(
                source="site_settings.default_image",
                label=f"Site settings: {site_settings.site.hostname}",
                object_id=str(site_settings.pk),
                field_name="default_image",
            )
        )
    for config in (
        ContentColumnConfig.objects.filter(cover_image_id=image.pk)
        .select_related("navigation_item__group__navigation_set__journal")
        .only(
            "pk",
            "navigation_item__label",
            "navigation_item__code",
            "navigation_item__slug",
            "navigation_item__group__navigation_set__journal__slug",
        )
    ):
        references.append(
            ImageReference(
                source="content_column_config.cover_image",
                label=f"Content column: {config.navigation_item.label}",
                object_id=str(config.pk),
                field_name="cover_image",
                page_path=config.navigation_item.target_url,
            )
        )
    return references


def _static_article_html_references(image):
    from ai_author_forum.journals.models import StaticArticle
    from ai_author_forum.static_publish.services import AssetReferenceParser

    image_paths = get_image_asset_paths(image)
    if not image_paths:
        return []
    references = []
    articles = (
        StaticArticle.objects.exclude(html_source="")
        .select_related("journal")
        .only("pk", "title", "slug", "journal__slug", "html_source")
    )
    for article in articles:
        try:
            article.html_source.open("rb")
            content = article.html_source.read()
        except (OSError, ValueError):
            continue
        finally:
            try:
                article.html_source.close()
            except (OSError, ValueError):
                pass
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
        parser = AssetReferenceParser()
        parser.feed(content)
        matched_paths = sorted(
            {
                path
                for value in parser.references
                if (path := _normalise_asset_path(value)) in image_paths
            }
        )
        if not matched_paths:
            continue
        references.append(
            ImageReference(
                source="static_article.html_source",
                label=f"Imported article body: {article.title}",
                object_id=str(article.pk),
                field_name="html_source",
                page_path=article.get_absolute_url(),
            )
        )
    return references


def _streamfield_references(image):
    from ai_author_forum.articles.models import ArticlePage
    from ai_author_forum.home.models import HomePage
    from ai_author_forum.images.models import CustomImage
    from ai_author_forum.news.models import ArticlePage as NewsArticlePage
    from ai_author_forum.standardpages.models import IndexPage, StandardPage

    references = []
    image_id = str(image.pk)
    field_specs = (
        (ArticlePage, "body", "article.body", "Article body"),
        (HomePage, "body", "home.body", "Home page body"),
        (StandardPage, "body", "standard_page.body", "Standard page body"),
        (IndexPage, "body", "index_page.body", "Index page body"),
        (NewsArticlePage, "image", "news_article.image", "News article image"),
        (NewsArticlePage, "body", "news_article.body", "News article body"),
    )
    for model_class, field_name, source, label_prefix in field_specs:
        field = model_class._meta.get_field(field_name)
        queryset = model_class.objects.only("pk", "title", field_name)
        if model_class is ArticlePage:
            queryset = queryset.only("static_slug")
        for page in queryset:
            value = getattr(page, field_name)
            for model, object_id, model_path, _content_path in field.extract_references(
                value
            ):
                if model is not CustomImage or str(object_id) != image_id:
                    continue
                if isinstance(page, ArticlePage):
                    page_path = page.get_absolute_url()
                else:
                    page_path = page.url or ""
                references.append(
                    ImageReference(
                        source=source,
                        label=f"{label_prefix}: {page.title}",
                        object_id=str(page.pk),
                        field_name=model_path or field_name,
                        page_path=page_path,
                    )
                )
    return references


def _manifest_asset_references():
    from ai_author_forum.static_publish.models import StaticManifest

    manifest = StaticManifest.objects.filter(is_active=True).only("metadata").first()
    if manifest is None:
        return None
    metadata = manifest.metadata or {}
    if "asset_references" not in metadata:
        return None
    references = {}
    for item in metadata.get("asset_references") or []:
        if not isinstance(item, dict):
            continue
        path = _normalise_asset_path(item.get("path"))
        if path:
            references[path] = tuple(str(page) for page in item.get("pages") or [])
    return references


def _current_release_asset_references():
    from ai_author_forum.static_publish.services import AssetReferenceParser

    current = Path(settings.STATIC_PUBLISH_ROOT) / "current"
    if not current.is_dir():
        return {}
    media_prefix = _normalise_asset_path(settings.MEDIA_URL).rstrip("/")
    references = {}
    for html_file in current.rglob("*.html"):
        parser = AssetReferenceParser()
        parser.feed(html_file.read_text(encoding="utf-8"))
        for value in parser.references:
            path = _normalise_asset_path(value)
            if media_prefix and not path.startswith(f"{media_prefix}/"):
                continue
            references.setdefault(path, set()).add(
                html_file.relative_to(current).as_posix()
            )
    return {path: tuple(sorted(pages)) for path, pages in references.items()}


def _published_page_references(image):
    asset_references = _manifest_asset_references()
    if asset_references is None:
        asset_references = _current_release_asset_references()
    references = []
    for path in sorted(get_image_asset_paths(image)):
        for page in asset_references.get(path, ()):
            references.append(
                ImageReference(
                    source="static_page.asset",
                    label="Published static page",
                    field_name=path,
                    page_path=page,
                )
            )
    return references


def get_image_references(image):
    references = [
        *_model_references(image),
        *_static_article_html_references(image),
        *_streamfield_references(image),
        *_published_page_references(image),
    ]
    unique = {}
    for reference in references:
        key = (
            reference.source,
            reference.object_id,
            reference.field_name,
            reference.page_path,
        )
        unique[key] = reference
    return tuple(unique.values())


def image_is_referenced(image):
    return bool(get_image_references(image))


def assert_image_can_be_deleted(image):
    references = get_image_references(image)
    if references:
        raise ImageReferenceProtectedError(image, references)
    return references
