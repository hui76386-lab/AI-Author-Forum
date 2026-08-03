from django.db import migrations
from django.utils.text import slugify


def migrate_legacy_placements(apps, schema_editor):
    LegacyPlacement = apps.get_model("journals", "ArticlePlacement")
    ArticlePage = apps.get_model("articles", "ArticlePage")
    LayoutSlot = apps.get_model("placements", "LayoutSlot")
    ArticlePlacement = apps.get_model("placements", "ArticlePlacement")

    target_map = {
        "main": "main_site",
        "journal": "journal",
        "section": "section",
        "search": "search",
    }
    slot_scope_map = {
        "main": "home",
        "journal": "journal",
        "section": "section",
        "search": "search",
    }
    for legacy in (
        LegacyPlacement.objects.select_related("article__journal").all().iterator()
    ):
        article = ArticlePage.objects.filter(
            source_static_article_id=legacy.article_id,
        ).first()
        if article is None:
            continue
        slot_code = slugify(legacy.slot_code, allow_unicode=True)
        slot, _ = LayoutSlot.objects.get_or_create(
            code=slot_code,
            defaults={
                "title": legacy.slot_name or slot_code,
                "scope": slot_scope_map.get(legacy.scope, "home"),
                "max_items": 20,
            },
        )
        target_type = target_map.get(legacy.scope, "main_site")
        target_slug = (
            ""
            if target_type == "main_site"
            else "search" if target_type == "search" else article.primary_journal.slug
        )
        ArticlePlacement.objects.update_or_create(
            article_id=article.pk,
            slot_id=slot.pk,
            target_type=target_type,
            target_slug=target_slug,
            defaults={
                "override_title": legacy.display_title,
                "override_summary": legacy.display_summary,
                "is_pinned": legacy.pinned,
                "sort_order": legacy.sort_order,
                "starts_at": legacy.start_at,
                "ends_at": legacy.end_at,
                "is_active": legacy.status == "active",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0002_unify_imported_articles"),
        ("journals", "0002_alter_journal_sort_order_and_more"),
        ("placements", "0002_seed_default_slots"),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_placements, migrations.RunPython.noop),
    ]
