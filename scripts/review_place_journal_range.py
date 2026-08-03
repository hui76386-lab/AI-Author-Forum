#!/usr/bin/env python
# ruff: noqa: E402
"""Review and place one indexed range of the 1,200-article test batch.

The target journal index is one-based and inclusive. Journals are selected from
exactly 120 active journals ordered by ``sort_order``, ``slug``, then ``pk``.
Each journal is processed in its own database transaction so disjoint ranges can
run in parallel and a failed journal can be resumed safely.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Django must be initialized before importing project models.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_author_forum.settings.dev")
# Placement normally queues a selective static build. This batch stops at
# placement unless the caller explicitly overrides this environment variable.
os.environ.setdefault("STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE", "false")

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from ai_author_forum.articles.models import (
    ArticleCategoryAssignment,
    ArticlePage,
)
from ai_author_forum.journals.category_services import (
    create_category,
    update_category,
)
from ai_author_forum.journals.models import (
    Journal,
    JournalCategory,
    JournalCategoryStatus,
    JournalStatus,
)
from ai_author_forum.placements.models import ArticlePlacement, LayoutSlot
from ai_author_forum.placements.services import bulk_place_articles_in_journal
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

BATCH_CODE = "aaf1200-20260730"
TARGET_JOURNAL_COUNT = 120
ARTICLES_PER_JOURNAL = 10
TARGET_SLOT_CODE = "journal_latest"


@dataclass
class JournalResult:
    index: int
    journal_slug: str
    category_created: bool = False
    category_reactivated: bool = False
    category_assignments_fixed: int = 0
    submitted: int = 0
    approved: int = 0
    placements_created: int = 0
    placements_reactivated: int = 0
    placements_skipped: int = 0
    placement_call_skipped: bool = False


def log(message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "补齐 aaf1200-20260730 批次的 research 主分类，正式审核并投放到 "
            "journal_latest。期刊索引从 1 开始，范围首尾均包含。"
        )
    )
    parser.add_argument(
        "--journal-start",
        type=int,
        default=1,
        help="起始期刊索引（1-based，包含；默认 1）。",
    )
    parser.add_argument(
        "--journal-end",
        type=int,
        default=TARGET_JOURNAL_COUNT,
        help="结束期刊索引（1-based，包含；默认 120）。",
    )
    parser.add_argument(
        "--operator",
        default="project_lead_a",
        help="执行审核、栏目维护和投放的启用中超级管理员用户名。",
    )
    return parser.parse_args()


def validate_range(start: int, end: int) -> None:
    if start < 1 or end < 1:
        raise ValueError("--journal-start and --journal-end must be at least 1")
    if start > end:
        raise ValueError("--journal-start cannot be greater than --journal-end")
    if end > TARGET_JOURNAL_COUNT:
        raise ValueError(
            f"--journal-end cannot exceed {TARGET_JOURNAL_COUNT}; received {end}"
        )


def get_operator(username: str):
    try:
        operator = get_user_model().objects.get(username=username)
    except get_user_model().DoesNotExist as exc:
        raise RuntimeError(f"Operator {username!r} does not exist") from exc
    if not operator.is_active or not operator.is_superuser:
        raise RuntimeError(
            f"Operator {username!r} must be an active superuser for this batch"
        )
    return operator


def get_target_journals() -> list[Journal]:
    journals = list(
        Journal.objects.filter(status=JournalStatus.ACTIVE).order_by(
            "sort_order", "slug", "pk"
        )
    )
    if len(journals) != TARGET_JOURNAL_COUNT:
        raise RuntimeError(
            "Expected exactly "
            f"{TARGET_JOURNAL_COUNT} active target journals, found {len(journals)}"
        )
    return journals


def get_target_slot() -> LayoutSlot:
    try:
        slot = LayoutSlot.objects.get(code=TARGET_SLOT_CODE)
    except LayoutSlot.DoesNotExist as exc:
        raise RuntimeError(f"Layout slot {TARGET_SLOT_CODE!r} does not exist") from exc
    if not slot.is_active:
        raise RuntimeError(f"Layout slot {TARGET_SLOT_CODE!r} is not active")
    if slot.scope != LayoutSlot.Scope.JOURNAL:
        raise RuntimeError(
            f"Layout slot {TARGET_SLOT_CODE!r} must use journal scope; "
            f"found {slot.scope!r}"
        )
    if slot.max_items < ARTICLES_PER_JOURNAL:
        raise RuntimeError(
            f"Layout slot {TARGET_SLOT_CODE!r} capacity is {slot.max_items}; "
            f"at least {ARTICLES_PER_JOURNAL} is required"
        )
    return slot


def expected_article_slugs(journal_slug: str) -> list[str]:
    return [
        f"{BATCH_CODE}-{journal_slug}-{number:02d}"
        for number in range(1, ARTICLES_PER_JOURNAL + 1)
    ]


def get_or_restore_research_category(
    *, journal: Journal, operator, request_id: str
) -> tuple[JournalCategory, bool, bool]:
    category = (
        JournalCategory.objects.select_for_update()
        .filter(journal=journal, code="research")
        .first()
    )
    created = False
    reactivated = False
    if category is None:
        result = create_category(
            journal=journal,
            data={
                "name": "Research",
                "code": "research",
                "slug": "research",
                "description": f"Primary research category for {journal.name}",
                "status": JournalCategoryStatus.ACTIVE,
                "show_in_navigation": False,
                "generate_static_page": True,
                "sort_order": 10,
            },
            actor=operator,
            request_id=request_id,
        )
        category = result.category
        created = True
    elif category.status not in {
        JournalCategoryStatus.ACTIVE,
        JournalCategoryStatus.HIDDEN,
    }:
        result = update_category(
            category_id=category.pk,
            changes={"status": JournalCategoryStatus.ACTIVE},
            actor=operator,
            request_id=request_id,
        )
        category = result.category
        reactivated = True
    return category, created, reactivated


def ensure_research_is_primary(
    *, article: ArticlePage, research: JournalCategory, operator, request_id: str
) -> bool:
    assignments = list(
        ArticleCategoryAssignment.objects.select_for_update()
        .filter(article=article)
        .select_related("category")
        .order_by("sort_order", "pk")
    )
    research_assignment = next(
        (item for item in assignments if item.category_id == research.pk), None
    )
    primary_assignments = [item for item in assignments if item.is_primary]
    if (
        len(primary_assignments) == 1
        and primary_assignments[0].category_id == research.pk
    ):
        return False

    related_after_fix = len(assignments) - 1
    if research_assignment is None:
        related_after_fix = len(assignments)
    if related_after_fix > 10:
        raise ValidationError(
            f"Article {article.static_slug!r} would exceed 10 related categories "
            "after making research primary"
        )

    previous_primary_ids = [item.category_id for item in primary_assignments]
    for assignment in primary_assignments:
        assignment.is_primary = False
        assignment.save(update_fields=("is_primary",))

    if research_assignment is None:
        next_sort_order = (
            max((item.sort_order or 0 for item in assignments), default=-1) + 1
        )
        research_assignment = ArticleCategoryAssignment(
            article=article,
            category=research,
            is_primary=True,
            sort_order=next_sort_order,
        )
        research_assignment.full_clean()
        research_assignment.save()
    else:
        research_assignment.is_primary = True
        research_assignment.save(update_fields=("is_primary",))

    revision = article.save_revision(
        user=operator,
        changed=True,
        bypass_article_permission_check=True,
    )
    record_audit_event(
        action=AuditAction.CONFIGURE,
        status=AuditStatus.SUCCESS,
        actor=operator,
        target=article,
        request_id=request_id,
        message=f"Ensured {BATCH_CODE} article uses research as primary category",
        metadata={
            "operation": "ensure_batch_research_primary_category",
            "batch_code": BATCH_CODE,
            "journal_slug": article.primary_journal.slug,
            "research_category_id": research.pk,
            "previous_primary_category_ids": previous_primary_ids,
            "revision_id": revision.pk,
        },
    )
    return True


def load_journal_articles(*, journal: Journal) -> list[ArticlePage]:
    expected_slugs = expected_article_slugs(journal.slug)
    articles = list(
        ArticlePage.objects.select_for_update()
        .filter(static_slug__in=expected_slugs)
        .select_related("primary_journal")
        .order_by("static_slug")
    )
    found_by_slug = {article.static_slug: article for article in articles}
    missing = [slug for slug in expected_slugs if slug not in found_by_slug]
    if missing:
        raise RuntimeError(
            f"{journal.slug}: expected {ARTICLES_PER_JOURNAL} batch articles; "
            f"missing {missing}"
        )
    wrong_journal = [
        article.static_slug
        for article in articles
        if article.primary_journal_id != journal.pk
    ]
    if wrong_journal:
        raise RuntimeError(
            f"{journal.slug}: articles belong to a different primary journal: "
            f"{wrong_journal}"
        )
    return [found_by_slug[slug] for slug in expected_slugs]


def review_articles(
    *,
    articles: list[ArticlePage],
    research: JournalCategory,
    operator,
    request_id: str,
    result: JournalResult,
) -> None:
    approved_statuses = {
        ArticlePage.ReviewStatus.APPROVED,
        ArticlePage.ReviewStatus.PUBLISHED,
    }
    for article in articles:
        category_changed = ensure_research_is_primary(
            article=article,
            research=research,
            operator=operator,
            request_id=request_id,
        )
        if category_changed:
            result.category_assignments_fixed += 1

        must_submit = category_changed or article.review_status not in {
            ArticlePage.ReviewStatus.SUBMITTED,
            *approved_statuses,
        }
        if must_submit:
            article.submit_for_review(
                operator,
                comment=f"{BATCH_CODE}: formal range-worker submission",
            )
            result.submitted += 1
            article.refresh_from_db()

        if article.review_status not in approved_statuses:
            article.approve(
                operator,
                comment=f"{BATCH_CODE}: approved for journal_latest placement",
            )
            result.approved += 1
            article.refresh_from_db()


def place_articles(
    *,
    articles: list[ArticlePage],
    journal: Journal,
    slot: LayoutSlot,
    operator,
    result: JournalResult,
) -> None:
    active_placement_count = ArticlePlacement.objects.filter(
        article__in=articles,
        slot=slot,
        target_type=ArticlePlacement.TargetType.JOURNAL,
        target_slug=journal.slug,
        source=ArticlePlacement.Source.MANUAL,
        is_active=True,
    ).count()
    if active_placement_count == ARTICLES_PER_JOURNAL:
        result.placement_call_skipped = True
        result.placements_skipped = ARTICLES_PER_JOURNAL
        return

    placement_result = bulk_place_articles_in_journal(
        articles=articles,
        journal=journal,
        slot=slot,
        actor=operator,
    )
    result.placements_created = len(placement_result["created"])
    result.placements_reactivated = len(placement_result["reactivated"])
    result.placements_skipped = len(placement_result["skipped"])

    final_count = ArticlePlacement.objects.filter(
        article__in=articles,
        slot=slot,
        target_type=ArticlePlacement.TargetType.JOURNAL,
        target_slug=journal.slug,
        source=ArticlePlacement.Source.MANUAL,
        is_active=True,
    ).count()
    if final_count != ARTICLES_PER_JOURNAL:
        raise RuntimeError(
            f"{journal.slug}: expected {ARTICLES_PER_JOURNAL} active manual "
            f"placements after service call; found {final_count}"
        )


def process_journal(
    *, index: int, journal: Journal, slot: LayoutSlot, operator
) -> JournalResult:
    request_id = f"{BATCH_CODE}:journal:{journal.slug}"
    result = JournalResult(index=index, journal_slug=journal.slug)
    with transaction.atomic():
        locked_journal = Journal.objects.select_for_update().get(pk=journal.pk)
        if locked_journal.status != JournalStatus.ACTIVE:
            raise RuntimeError(f"{journal.slug}: journal is no longer active")
        research, created, reactivated = get_or_restore_research_category(
            journal=locked_journal,
            operator=operator,
            request_id=request_id,
        )
        result.category_created = created
        result.category_reactivated = reactivated
        articles = load_journal_articles(journal=locked_journal)
        review_articles(
            articles=articles,
            research=research,
            operator=operator,
            request_id=request_id,
            result=result,
        )
        place_articles(
            articles=articles,
            journal=locked_journal,
            slot=slot,
            operator=operator,
            result=result,
        )
    return result


def main() -> None:
    args = parse_args()
    validate_range(args.journal_start, args.journal_end)
    operator = get_operator(args.operator)
    journals = get_target_journals()
    slot = get_target_slot()
    selected = journals[args.journal_start - 1 : args.journal_end]

    log(
        f"Starting {BATCH_CODE} journals {args.journal_start}-{args.journal_end} "
        f"({len(selected)} journals); auto static publish="
        f"{settings.STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE}"
    )
    totals = JournalResult(index=0, journal_slug="TOTAL")
    for index, journal in enumerate(journals, start=1):
        if index < args.journal_start or index > args.journal_end:
            continue
        result = process_journal(
            index=index,
            journal=journal,
            slot=slot,
            operator=operator,
        )
        totals.category_created += int(result.category_created)
        totals.category_reactivated += int(result.category_reactivated)
        totals.category_assignments_fixed += result.category_assignments_fixed
        totals.submitted += result.submitted
        totals.approved += result.approved
        totals.placements_created += result.placements_created
        totals.placements_reactivated += result.placements_reactivated
        totals.placements_skipped += result.placements_skipped
        totals.placement_call_skipped = (
            totals.placement_call_skipped or result.placement_call_skipped
        )
        log(
            f"Journal {index:03d}/{TARGET_JOURNAL_COUNT} {journal.slug}: "
            f"category_fixed={result.category_assignments_fixed}, "
            f"submitted={result.submitted}, approved={result.approved}, "
            f"placements(created={result.placements_created}, "
            f"reactivated={result.placements_reactivated}, "
            f"skipped={result.placements_skipped})"
        )

    log(
        f"Completed journals {args.journal_start}-{args.journal_end}: "
        f"categories_created={totals.category_created}, "
        f"categories_reactivated={totals.category_reactivated}, "
        f"category_assignments_fixed={totals.category_assignments_fixed}, "
        f"submitted={totals.submitted}, approved={totals.approved}, "
        f"placements_created={totals.placements_created}, "
        f"placements_reactivated={totals.placements_reactivated}, "
        f"placements_skipped={totals.placements_skipped}"
    )


if __name__ == "__main__":
    main()
