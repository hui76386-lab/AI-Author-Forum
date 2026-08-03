from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai_author_forum.articles.category_services import get_live_article_categories
from ai_author_forum.articles.models import ArticlePage
from ai_author_forum.journals.models import Journal, JournalCategory
from ai_author_forum.placements.models import ArticlePlacement
from ai_author_forum.site_settings.models import AuditAction, AuditLog, AuditStatus

RULE_VERSION = "2026-07-category-v1"
FIELDS = [
    "migration_batch_id",
    "rule_version",
    "generated_at",
    "article_id",
    "article_title",
    "primary_journal_code",
    "article_type",
    "keywords",
    "legacy_section_slugs",
    "suggested_primary_category_code",
    "suggested_related_category_codes",
    "match_reason",
    "confidence",
    "requires_manual_review",
    "manual_confirmed",
    "error_code",
]


class Command(BaseCommand):
    help = "Generate a reviewable CSV plan for historical article category migration."

    def add_arguments(self, parser):
        parser.add_argument("--journal", required=True, help="Journal slug/code")
        parser.add_argument("--output", required=True)
        parser.add_argument("--article-id-from", type=int)
        parser.add_argument("--article-id-to", type=int)
        parser.add_argument("--batch-id")

    def handle(self, *args, **options):
        journal = self._journal(options["journal"])
        queryset = ArticlePage.objects.filter(primary_journal=journal).order_by("pk")
        if options["article_id_from"] is not None:
            queryset = queryset.filter(pk__gte=options["article_id_from"])
        if options["article_id_to"] is not None:
            queryset = queryset.filter(pk__lte=options["article_id_to"])
        output = Path(options["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        batch_id = (
            options["batch_id"]
            or f"category-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        )
        generated_at = datetime.now(UTC).isoformat()
        rows = [
            self._row(article, journal, batch_id, generated_at)
            for article in queryset.iterator(chunk_size=200)
        ]
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        metadata = {
            "migration_batch_id": batch_id,
            "rule_version": RULE_VERSION,
            "generated_at": generated_at,
            "journal": journal.slug,
            "article_id_from": options["article_id_from"],
            "article_id_to": options["article_id_to"],
            "row_count": len(rows),
            "sha256": digest,
            "csv": output.name,
        }
        output.with_suffix(output.suffix + ".meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        AuditLog.record(
            action=AuditAction.CONFIGURE,
            status=AuditStatus.SUCCESS,
            target_type="ArticleCategoryMigration",
            target_id=batch_id,
            target_label=journal.slug,
            request_id=batch_id,
            message="Historical article category migration plan generated.",
            metadata=metadata,
        )
        self.stdout.write(json.dumps(metadata, ensure_ascii=False))

    def _journal(self, code):
        try:
            return Journal.objects.get(slug=code)
        except Journal.DoesNotExist as exc:
            raise CommandError(
                f"CATEGORY_NOT_FOUND: journal {code!r} does not exist"
            ) from exc

    def _row(self, article, journal, batch_id, generated_at):
        live = get_live_article_categories(article_id=article.pk)
        primary = live.primary
        related = list(live.related)
        reason = "existing_live_assignment" if primary else ""
        confidence = 1.0 if primary else 0.0
        if primary is None:
            primary, confidence, reason = self._match(article, journal)
        legacy_sections = list(
            ArticlePlacement.objects.filter(
                article=article,
                target_type=ArticlePlacement.TargetType.SECTION,
            ).values_list("target_slug", flat=True)
        )
        if primary is None and legacy_sections:
            confidence = 0.60
            reason = "legacy_section_requires_manual_mapping"
        requires_manual = confidence < 0.80 or primary is None
        return {
            "migration_batch_id": batch_id,
            "rule_version": RULE_VERSION,
            "generated_at": generated_at,
            "article_id": article.pk,
            "article_title": article.title,
            "primary_journal_code": journal.slug,
            "article_type": article.article_type,
            "keywords": article.keywords,
            "legacy_section_slugs": ";".join(filter(None, legacy_sections)),
            "suggested_primary_category_code": primary.code if primary else "",
            "suggested_related_category_codes": ";".join(item.code for item in related),
            "match_reason": reason or "no_reliable_candidate",
            "confidence": f"{confidence:.2f}",
            "requires_manual_review": str(requires_manual).lower(),
            "manual_confirmed": "false",
            "error_code": (
                "ARTICLE_PRIMARY_CATEGORY_REQUIRED" if primary is None else ""
            ),
        }

    def _match(self, article, journal):
        haystack = " ".join(
            (
                article.title,
                article.abstract or "",
                article.keywords or "",
                article.article_type or "",
                article.get_article_type_display() or "",
            )
        ).casefold()
        matches = []
        for category in JournalCategory.objects.filter(
            journal=journal, status__in=("active", "hidden")
        ):
            tokens = {
                category.name.casefold(),
                category.slug.casefold(),
                category.code.casefold(),
            }
            if any(token and token in haystack for token in tokens):
                matches.append(category)
        if len(matches) == 1:
            return matches[0], 0.85, "title_or_keyword_rule"
        if len(matches) > 1:
            return None, 0.50, "ambiguous_title_or_keyword_rule"
        return None, 0.0, "no_reliable_candidate"
