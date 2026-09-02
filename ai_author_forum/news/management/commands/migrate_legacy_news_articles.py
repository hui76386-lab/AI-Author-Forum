from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify
from wagtail.models import Page

from ai_author_forum.articles.models import ArticlePage as CanonicalArticlePage
from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.news.models import ArticlePage as LegacyArticlePage


class Command(BaseCommand):
    help = (
        "Convert retired news.ArticlePage records into canonical "
        "articles.ArticlePage records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--journal-slug",
            help=(
                "Journal slug assigned to migrated legacy articles. Required unless "
                "there is exactly one active journal."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be migrated without writing changes.",
        )
        parser.add_argument(
            "--keep-legacy-live",
            action="store_true",
            help="Do not unpublish legacy news.ArticlePage records after conversion.",
        )

    def handle(self, *args, **options):
        journal = self.get_journal(options.get("journal_slug"))
        dry_run = options["dry_run"]
        keep_legacy_live = options["keep_legacy_live"]
        legacy_articles = LegacyArticlePage.objects.all().order_by("path")
        created = 0
        updated = 0
        unpublished = 0

        if not legacy_articles.exists():
            self.stdout.write("No legacy news.ArticlePage records found.")
            return

        for legacy_article in legacy_articles:
            if dry_run:
                self.stdout.write(
                    self.describe_action(legacy_article, journal, dry_run=True)
                )
                continue

            with transaction.atomic():
                canonical, was_created = self.convert_article(legacy_article, journal)
                created += int(was_created)
                updated += int(not was_created)
                if legacy_article.live and not keep_legacy_live:
                    legacy_article.live = False
                    legacy_article.has_unpublished_changes = True
                    legacy_article.save(
                        clean=False,
                        update_fields=("live", "has_unpublished_changes"),
                    )
                    unpublished += 1

            self.stdout.write(
                f"{'created' if was_created else 'updated'} "
                f"articles.ArticlePage #{canonical.pk} from legacy "
                f"news.ArticlePage #{legacy_article.pk}"
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run only. {legacy_articles.count()} legacy article(s) "
                    "would be converted."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Converted legacy news articles: created={created}, "
                f"updated={updated}, unpublished_legacy={unpublished}."
            )
        )

    def get_journal(self, journal_slug):
        if journal_slug:
            try:
                return Journal.objects.get(slug=journal_slug)
            except Journal.DoesNotExist as exc:
                raise CommandError(f"Journal not found: {journal_slug}") from exc

        active_journals = Journal.objects.filter(status=JournalStatus.ACTIVE)
        count = active_journals.count()
        if count == 1:
            return active_journals.get()

        raise CommandError(
            "--journal-slug is required unless there is exactly one active journal."
        )

    def describe_action(self, legacy_article, journal, *, dry_run=False):
        action = "would convert" if dry_run else "convert"
        return (
            f"{action} legacy news.ArticlePage #{legacy_article.pk} "
            f"'{legacy_article.title}' to journal '{journal.slug}'"
        )

    def convert_article(self, legacy_article, journal):
        static_slug = self.get_unique_static_slug(legacy_article)
        canonical = (
            CanonicalArticlePage.objects.filter(static_slug=legacy_article.slug).first()
            or CanonicalArticlePage.objects.filter(slug=legacy_article.slug).first()
        )
        was_created = canonical is None
        body_html = self.get_body_html(legacy_article)
        values = {
            "title": legacy_article.title,
            "slug": (
                canonical.slug
                if canonical
                else self.get_unique_page_slug(legacy_article)
            ),
            "static_slug": canonical.static_slug if canonical else static_slug,
            "abstract": self.get_abstract(legacy_article),
            "body": [("html", body_html)],
            "authors": self.get_authors(legacy_article),
            "article_type": CanonicalArticlePage.ArticleType.NEWS,
            "primary_journal": journal,
            "keywords": self.get_keywords(legacy_article),
        }

        if was_created:
            canonical = CanonicalArticlePage(**values)
            Page.get_first_root_node().add_child(instance=canonical)
        else:
            for field, value in values.items():
                setattr(canonical, field, value)
            canonical.save(clean=False, bypass_article_permission_check=True)

        canonical.live = False
        canonical.has_unpublished_changes = True
        canonical.save(
            clean=False,
            bypass_article_permission_check=True,
            update_fields=("live", "has_unpublished_changes"),
        )
        canonical.save_revision(
            changed=True,
            bypass_article_permission_check=True,
        )
        return canonical, was_created

    def get_abstract(self, legacy_article):
        return (
            legacy_article.introduction
            or legacy_article.listing_summary
            or legacy_article.search_description
            or legacy_article.title
        )

    def get_body_html(self, legacy_article):
        if legacy_article.introduction:
            return f"<p>{legacy_article.introduction}</p>"
        return f"<p>{legacy_article.title}</p>"

    def get_authors(self, legacy_article):
        author = getattr(legacy_article, "author", None)
        return getattr(author, "title", "") or "Legacy news migration"

    def get_keywords(self, legacy_article):
        topic = getattr(legacy_article, "topic", None)
        values = [
            getattr(topic, "title", ""),
            getattr(topic, "slug", ""),
            "legacy-news",
        ]
        return ", ".join(value for value in values if value)

    def get_unique_static_slug(self, legacy_article):
        base_slug = slugify(legacy_article.slug or legacy_article.title) or "article"
        return self.get_unique_slug(
            base_slug,
            CanonicalArticlePage.objects.values_list("static_slug", flat=True),
        )

    def get_unique_page_slug(self, legacy_article):
        base_slug = slugify(legacy_article.slug or legacy_article.title) or "article"
        return self.get_unique_slug(
            base_slug,
            CanonicalArticlePage.objects.values_list("slug", flat=True),
        )

    def get_unique_slug(self, base_slug, existing_values):
        existing = set(existing_values)
        if base_slug not in existing:
            return base_slug

        suffix = 2
        while f"{base_slug}-{suffix}" in existing:
            suffix += 1
        return f"{base_slug}-{suffix}"
