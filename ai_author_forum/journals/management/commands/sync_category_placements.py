from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai_author_forum.journals.models import Journal
from ai_author_forum.placements.category_services import repair_category_placement_drift
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus


class Command(BaseCommand):
    help = (
        "Plan or apply idempotent system category Placement synchronization by Journal."
    )

    def add_arguments(self, parser):
        parser.add_argument("--journal", required=True)
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--article-id-from", type=int)
        parser.add_argument("--article-id-to", type=int)
        parser.add_argument("--confirm-production", action="store_true")

    def handle(self, *args, **options):
        try:
            journal = Journal.objects.get(slug=options["journal"])
        except Journal.DoesNotExist as exc:
            raise CommandError("CATEGORY_NOT_FOUND: journal does not exist") from exc
        if (
            options["apply"]
            and not settings.DEBUG
            and not options["confirm_production"]
        ):
            raise CommandError("Production apply requires --confirm-production.")
        article_ids = journal.primary_articles.order_by("pk").values_list(
            "pk", flat=True
        )
        if options["article_id_from"] is not None:
            article_ids = article_ids.filter(pk__gte=options["article_id_from"])
        if options["article_id_to"] is not None:
            article_ids = article_ids.filter(pk__lte=options["article_id_to"])
        ids = list(article_ids)
        result = repair_category_placement_drift(
            journal_id=journal.pk,
            article_ids=ids,
            dry_run=not options["apply"],
        )
        summary = {"journal": journal.slug, "article_count": len(ids), **result}
        AuditLog.record(
            action=AuditAction.RETRY if options["apply"] else AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            target=journal,
            request_id=f"sync-category-placements:{journal.slug}",
            message="Category Placement synchronization completed.",
            metadata=summary,
        )
        self.stdout.write(json.dumps(summary, ensure_ascii=False, default=str))
