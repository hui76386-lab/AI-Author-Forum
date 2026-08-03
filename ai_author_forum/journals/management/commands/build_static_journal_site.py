from __future__ import annotations

import shutil
from collections import defaultdict
from html import escape
from string import Template

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.articles.services import get_approved_articles
from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.journals.publishing import (
    resolve_static_file_path,
    resolve_static_output_dir,
)
from ai_author_forum.placements.models import ArticlePlacement

ARTICLE_TEMPLATE = Template("""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>$title</title>
  <style>
    body { margin: 0; font-family: Arial, Helvetica, sans-serif; background: #f5f7fb; color: #111827; }
    main { max-width: 1080px; margin: 0 auto; padding: 32px 20px 56px; }
    header { border-bottom: 1px solid #d8dee9; padding-bottom: 18px; margin-bottom: 28px; }
    article { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 24px; box-shadow: 0 8px 24px rgba(15, 23, 42, .06); }
  </style>
</head>
<body>
  <main>
    <header>
      <div>$journal_name</div>
      <h1>$title</h1>
    </header>
    <article>$body</article>
  </main>
</body>
</html>
""")

JOURNAL_TEMPLATE = Template("""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>$title</title>
  <style>
    body { margin: 0; font-family: Arial, Helvetica, sans-serif; background: #f8fafc; color: #111827; }
    .page { max-width: 1200px; margin: 0 auto; padding: 32px 20px 64px; }
    .hero { background: #0f172a; color: #fff; border-radius: 18px; padding: 28px; margin-bottom: 24px; }
    section { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 24px; margin-bottom: 20px; }
    ul { padding-left: 20px; }
    a { color: #0f766e; text-decoration: none; }
  </style>
</head>
<body>
  <div class="page">
    <div class="hero">
      <h1>$title</h1>
      <p>$intro</p>
    </div>
    <section>
      <h2>Featured</h2>
      <ul>$featured_markup</ul>
    </section>
    <section>
      <h2>All articles</h2>
      <ul>$article_markup</ul>
    </section>
  </div>
</body>
</html>
""")

HOME_TEMPLATE = Template("""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Author Forum</title>
  <style>
    body { margin: 0; font-family: Arial, Helvetica, sans-serif; background: #f8fafc; color: #111827; }
    .page { max-width: 1200px; margin: 0 auto; padding: 32px 20px 64px; }
    section { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 24px; margin-bottom: 20px; }
    a { color: #0f766e; text-decoration: none; }
    ul { padding-left: 20px; }
  </style>
</head>
<body>
  <div class="page">
    <section>
      <h1>AI Author Forum</h1>
      <p>Static publishing shell generated from imported journals and articles.</p>
    </section>
    <section>
      <h2>Featured articles</h2>
      <ul>$featured_markup</ul>
    </section>
    <section>
      <h2>Journals</h2>
      <ul>$journal_markup</ul>
    </section>
  </div>
</body>
</html>
""")


def _source_article(article):
    try:
        return article.source_static_article
    except ArticlePage.source_static_article.RelatedObjectDoesNotExist:
        return None


def _read_article_source(article) -> str:
    source_article = _source_article(article)
    if not source_article or not source_article.html_source:
        return ""
    with source_article.html_source.open("rb") as handle:
        return handle.read().decode("utf-8")


def _article_output_path(article):
    return article.get_static_output_path()


def _article_url(article):
    return article.get_absolute_url()


def _article_sort_order(article):
    source_article = _source_article(article)
    return source_article.sort_order if source_article else 0


def _article_is_pinned(article):
    source_article = _source_article(article)
    return bool(source_article and source_article.is_pinned)


def _wrap_fragment(article, body: str) -> str:
    title = escape(article.title)
    journal = article.primary_journal
    return ARTICLE_TEMPLATE.substitute(
        title=title,
        journal_name=escape(journal.name_cn or journal.name),
        body=body,
    )


def _ensure_full_document(article, source: str) -> str:
    if "<html" in source.lower():
        return source
    return _wrap_fragment(article, source)


def _build_journal_page(journal: Journal, articles, featured) -> str:
    featured_markup = (
        "".join(
            f'<li><a href="{escape(_article_url(article))}">{escape(article.title)}</a></li>'
            for article in featured
        )
        or "<li>No featured placements</li>"
    )
    article_markup = "".join(
        f'<li><a href="{escape(_article_url(article))}">{escape(article.title)}</a></li>'
        for article in articles
    )
    title = escape(journal.name_cn or journal.name)
    return JOURNAL_TEMPLATE.substitute(
        title=title,
        intro=escape(journal.homepage_intro or journal.seo_description or ""),
        featured_markup=featured_markup,
        article_markup=article_markup,
    )


def _build_home_page(journals: list[Journal], featured_articles) -> str:
    journal_markup = "".join(
        f'<li><a href="{escape(journal.static_site_path)}">{escape(journal.name_cn or journal.name)}</a></li>'
        for journal in journals
    )
    featured_markup = (
        "".join(
            f'<li><a href="{escape(_article_url(article))}">{escape(article.title)}</a></li>'
            for article in featured_articles
        )
        or "<li>No featured placements</li>"
    )
    return HOME_TEMPLATE.substitute(
        journal_markup=journal_markup,
        featured_markup=featured_markup,
    )


class Command(BaseCommand):
    help = "Render journals and static article pages into a static site tree."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            required=True,
            help="Directory where static HTML files will be written.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove the output directory before rendering.",
        )

    def handle(self, *args, **options):
        try:
            output_dir = resolve_static_output_dir(options["output_dir"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc

        if options["clear"] and output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        journals = list(
            Journal.objects.filter(status=JournalStatus.ACTIVE).order_by(
                "sort_order", "name"
            )
        )
        if not journals:
            raise CommandError("No journals are available to render.")

        publish_at = timezone.now()
        articles = list(
            get_approved_articles(at=publish_at)
            .select_related("primary_journal", "source_static_article")
            .order_by("primary_journal__sort_order", "pk")
        )
        for article in articles:
            try:
                resolve_static_file_path(output_dir, _article_output_path(article))
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc

        featured_articles = []
        featured_article_ids = set()
        main_placements = (
            ArticlePlacement.objects.available(at=publish_at)
            .for_target(ArticlePlacement.TargetType.MAIN_SITE, "")
            .filter(is_pinned=True)
            .select_related("article", "article__primary_journal")
            .ordered_for_display()
        )
        for placement in main_placements:
            if placement.article_id not in featured_article_ids:
                featured_articles.append(placement.article)
                featured_article_ids.add(placement.article_id)

        articles_by_journal: dict[str, list[ArticlePage]] = defaultdict(list)
        featured_by_journal: dict[str, list[ArticlePage]] = defaultdict(list)
        journal_article_ids: dict[str, set[int]] = defaultdict(set)
        journal_featured_ids: dict[str, set[int]] = defaultdict(set)
        journal_placements = (
            ArticlePlacement.objects.available(at=publish_at)
            .filter(
                target_type=ArticlePlacement.TargetType.JOURNAL,
                target_slug__in=[journal.slug for journal in journals],
            )
            .select_related("article", "article__primary_journal")
            .ordered_for_display()
        )
        for placement in journal_placements:
            target_slug = placement.target_slug
            if placement.article_id not in journal_article_ids[target_slug]:
                articles_by_journal[target_slug].append(placement.article)
                journal_article_ids[target_slug].add(placement.article_id)
            if (
                placement.is_pinned
                and placement.article_id not in journal_featured_ids[target_slug]
            ):
                featured_by_journal[target_slug].append(placement.article)
                journal_featured_ids[target_slug].add(placement.article_id)
        rendered_articles = 0
        for article in articles:
            html = _ensure_full_document(article, _read_article_source(article))
            try:
                article_path = resolve_static_file_path(
                    output_dir, _article_output_path(article)
                )
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc
            article_path.parent.mkdir(parents=True, exist_ok=True)
            article_path.write_text(html, encoding="utf-8")
            rendered_articles += 1

        rendered_journals = 0
        for journal in journals:
            journal_articles = articles_by_journal.get(journal.slug, [])
            journal_featured = featured_by_journal.get(journal.slug, [])
            try:
                journal_path = resolve_static_file_path(
                    output_dir,
                    journal.static_site_path or f"/journals/{journal.slug}/index.html",
                )
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(
                _build_journal_page(journal, journal_articles, journal_featured),
                encoding="utf-8",
            )
            rendered_journals += 1

        home_path = resolve_static_file_path(output_dir, "index.html")
        home_path.write_text(
            _build_home_page(journals, featured_articles), encoding="utf-8"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Rendered {rendered_articles} articles, {rendered_journals} journals, and 1 home page into {output_dir}"
            )
        )
