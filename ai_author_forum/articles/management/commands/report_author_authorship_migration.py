from __future__ import annotations

import csv
import json

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from ai_author_forum.articles.models import ArticleAuthorship, ArticlePage


class Command(BaseCommand):
    help = (
        "Read-only report of explicit historical owner candidates, unmapped articles, "
        "duplicate public corresponding authors, and invalid author accounts."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--format", choices=("json", "csv"), default="json", dest="output_format"
        )

    def handle(self, *args, **options):
        active_owner_ids = (
            ArticleAuthorship.objects.effective()
            .filter(role=ArticleAuthorship.Role.OWNER)
            .values("article_id")
        )
        articles = ArticlePage.objects.exclude(pk__in=active_owner_ids).select_related(
            "owner", "primary_journal"
        )
        mapped_candidates = []
        unmapped_articles = []
        for article in articles.iterator():
            owner = article.owner
            row = {
                "article_id": article.pk,
                "journal_id": article.primary_journal_id,
                "page_owner_id": article.owner_id,
            }
            if (
                owner is not None
                and owner.is_active
                and owner.account_status == owner.AccountStatus.ACTIVE
                and owner.is_author
            ):
                mapped_candidates.append({**row, "proposed_user_id": owner.pk})
            else:
                reason = "no_explicit_page_owner"
                if owner is not None:
                    reason = "page_owner_is_not_an_active_author"
                unmapped_articles.append({**row, "reason": reason})

        duplicate_corresponding = list(
            ArticlePage.objects.annotate(
                corresponding_count=Count(
                    "contributors",
                    filter=Q(contributors__is_corresponding=True),
                )
            )
            .filter(corresponding_count__gt=1)
            .values("id", "primary_journal_id", "corresponding_count")
        )
        invalid_authorships = list(
            ArticleAuthorship.objects.filter(revoked_at__isnull=True)
            .filter(
                Q(user__is_active=False)
                | ~Q(user__account_status="active")
                | Q(user__is_author=False)
            )
            .values("id", "article_id", "user_id", "role")
        )
        report = {
            "read_only": True,
            "mapped_candidates": mapped_candidates,
            "unmapped_articles": unmapped_articles,
            "duplicate_corresponding_authors": duplicate_corresponding,
            "invalid_authorships": invalid_authorships,
        }
        if options["output_format"] == "json":
            self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return

        writer = csv.writer(self.stdout)
        writer.writerow(("section", "article_id", "user_id", "detail"))
        for row in mapped_candidates:
            writer.writerow(
                ("mapped_candidate", row["article_id"], row["proposed_user_id"], "")
            )
        for row in unmapped_articles:
            writer.writerow(("unmapped", row["article_id"], "", row["reason"]))
        for row in duplicate_corresponding:
            writer.writerow(
                (
                    "duplicate_corresponding",
                    row["id"],
                    "",
                    row["corresponding_count"],
                )
            )
        for row in invalid_authorships:
            writer.writerow(
                ("invalid_authorship", row["article_id"], row["user_id"], row["id"])
            )
