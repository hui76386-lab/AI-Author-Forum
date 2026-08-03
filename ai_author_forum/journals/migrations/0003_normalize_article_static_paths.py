from django.db import migrations


def normalize_article_static_paths(apps, schema_editor):
    StaticArticle = apps.get_model("journals", "StaticArticle")
    ArticlePage = apps.get_model("articles", "ArticlePage")

    canonical_slugs = dict(
        ArticlePage.objects.exclude(source_static_article_id__isnull=True).values_list(
            "source_static_article_id", "static_slug"
        )
    )
    for article in StaticArticle.objects.all().iterator():
        static_slug = canonical_slugs.get(article.pk) or article.slug
        canonical_path = f"/articles/{static_slug}/index.html"
        if article.static_output_path != canonical_path:
            StaticArticle.objects.filter(pk=article.pk).update(
                static_output_path=canonical_path
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("articles", "0003_publication_status_sync"),
        ("journals", "0002_alter_journal_sort_order_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize_article_static_paths, noop),
    ]
