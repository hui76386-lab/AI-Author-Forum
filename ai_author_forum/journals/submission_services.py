from __future__ import annotations

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import Journal, JournalEditorAssignment, JournalStatus


def filter_open_author_submission_journals(queryset=None, *, at=None):
    """Return journals that are operationally able to accept an author draft."""

    at = at or timezone.now()
    queryset = queryset if queryset is not None else Journal.objects.all()
    effective_chief = JournalEditorAssignment.objects.effective(at=at).filter(
        journal_id=OuterRef("pk"),
        role=JournalEditorAssignment.Role.CHIEF_EDITOR,
    )
    return (
        queryset.filter(
            status=JournalStatus.ACTIVE,
            accepts_author_submissions=True,
        )
        .filter(Q(submission_opened_at__isnull=True) | Q(submission_opened_at__lte=at))
        .filter(Q(submission_closed_at__isnull=True) | Q(submission_closed_at__gt=at))
        .annotate(_has_effective_submission_chief=Exists(effective_chief))
        .filter(_has_effective_submission_chief=True)
    )


def journal_accepts_author_submission(journal, *, at=None) -> bool:
    if journal is None or journal.pk is None:
        return False
    return filter_open_author_submission_journals(
        Journal.objects.filter(pk=journal.pk), at=at
    ).exists()


def public_submission_journals(*, at=None):
    return filter_open_author_submission_journals(
        Journal.objects.only(
            "id",
            "name",
            "name_cn",
            "slug",
            "homepage_intro",
            "seo_description",
            "submission_guidelines_url",
            "submission_opened_at",
            "submission_closed_at",
            "sort_order",
        ),
        at=at,
    ).order_by("sort_order", "name", "pk")
