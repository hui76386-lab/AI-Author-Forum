from dataclasses import dataclass

from django.templatetags.static import static

from .integrations import get_site_settings


@dataclass(frozen=True)
class ArticleImageResult:
    image: object | None
    alt: str
    source: str
    placeholder_url: str

    @property
    def is_placeholder(self):
        return self.image is None


def resolve_article_image(article, *, placement=None, request=None, site_settings=None):
    """Resolve one article visual using the project-wide fallback order.

    Priority: placement override -> article featured image -> site default -> fixed
    placeholder. This function is intentionally owned by articles so static rendering,
    previews and cards all use the same decision.
    """
    override_image = getattr(placement, "override_image", None) if placement else None
    featured_image = getattr(article, "featured_image", None) if article else None
    if site_settings is None:
        site_settings = get_site_settings(request)
    default_image = getattr(site_settings, "default_image", None)

    image = override_image or featured_image or default_image
    if override_image:
        source = "placement"
    elif featured_image:
        source = "article"
    elif default_image:
        source = "site"
    else:
        source = "placeholder"

    alt = _resolve_alt(article=article, placement=placement, image=image)
    return ArticleImageResult(
        image=image,
        alt=alt,
        source=source,
        placeholder_url=static("images/reference/article-1.png"),
    )


def _resolve_alt(*, article, placement, image):
    placement_alt = ""
    if placement is not None:
        placement_alt = (
            getattr(placement, "override_image_alt", "")
            or getattr(placement, "image_alt", "")
            or ""
        )
    article_alt = getattr(article, "featured_image_alt", "") if article else ""
    image_title = getattr(image, "title", "") if image else ""
    article_title = getattr(article, "title", "") if article else ""
    return placement_alt or article_alt or image_title or article_title or "文章图片"


def get_article_featured_image_references(image):
    """Return article-owned image references for integration by images protection.

    The images module can consume this without articles importing or modifying its
    deletion workflow in this task.
    """
    image_id = getattr(image, "pk", image)
    if not image_id:
        return []

    from .models import ArticlePage

    return [
        {
            "reference_type": "article.featured_image",
            "article_id": article.pk,
            "title": article.title,
            "static_path": article.get_static_output_path(),
        }
        for article in ArticlePage.objects.filter(featured_image_id=image_id).only(
            "pk", "title", "path", "depth", "static_slug"
        )
    ]
