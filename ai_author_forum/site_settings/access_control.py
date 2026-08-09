from __future__ import annotations

from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Value, When

from ai_author_forum.journals.models import (
    JournalEditorAssignment,
    JournalStatus,
    PublicationIssueScope,
)
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME

SENSITIVE_JOURNAL_FIELDS = frozenset(
    {
        "slug",
        "status",
        "static_site_path",
        "target_article_count",
        "static_output_root",
    }
)


def _active_user(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "account_status", "active") == "active"
    )


def is_super_admin(user) -> bool:
    if not _active_user(user):
        return False
    return user.groups.filter(name=SUPER_ADMIN_GROUP_NAME).exists()


def get_journal_editor_assignment(user, journal):
    if not _active_user(user) or journal is None:
        return None
    role_order = Case(
        When(role=JournalEditorAssignment.Role.CHIEF_EDITOR, then=Value(0)),
        When(role=JournalEditorAssignment.Role.EXECUTIVE_EDITOR, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    return (
        JournalEditorAssignment.objects.effective()
        .filter(user=user, journal=journal)
        .order_by(role_order, "pk")
        .first()
    )


def can_manage_accounts(user) -> bool:
    return is_super_admin(user)


def can_manage_journal(user, journal, responsibility=None) -> bool:
    if not _active_user(user):
        return False
    if is_super_admin(user):
        return True
    assignment = get_journal_editor_assignment(user, journal)
    if assignment is None:
        return False
    if assignment.role in {
        JournalEditorAssignment.Role.CHIEF_EDITOR,
        JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
    }:
        return True
    return bool(
        responsibility
        and responsibility in (assignment.responsibilities or [])
        and responsibility in JournalEditorAssignment.ALL_RESPONSIBILITIES
    )


def can_initial_review(user, article) -> bool:
    if not _active_user(user) or article is None:
        return False
    if article.review_status != article.ReviewStatus.SUBMITTED:
        return False
    if is_super_admin(user):
        return True
    assignment = get_journal_editor_assignment(user, article.primary_journal)
    if assignment is None:
        return False
    if article.assigned_initial_editor_id is None:
        return assignment.role in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
    assigned_editor_is_effective = (
        JournalEditorAssignment.objects.effective()
        .filter(
            user_id=article.assigned_initial_editor_id,
            journal=article.primary_journal,
        )
        .exists()
    )
    if not assigned_editor_is_effective:
        return assignment.role in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
    if assignment.role in {
        JournalEditorAssignment.Role.CHIEF_EDITOR,
        JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
    }:
        return True
    return article.assigned_initial_editor_id == user.pk


def can_final_review(user, article) -> bool:
    if not _active_user(user) or article is None:
        return False
    if article.review_status != article.ReviewStatus.PENDING_FINAL:
        return False
    assignment = get_journal_editor_assignment(user, article.primary_journal)
    return bool(
        assignment and assignment.role == JournalEditorAssignment.Role.CHIEF_EDITOR
    )


def can_manage_article(user, article) -> bool:
    if not _active_user(user) or article is None:
        return False
    if is_super_admin(user):
        return True
    return can_manage_journal(
        user,
        article.primary_journal,
        JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE,
    )


def get_article_authorship(user, article):
    if not _active_user(user) or not getattr(user, "is_author", False):
        return None
    if article is None:
        return None
    from ai_author_forum.articles.models import ArticleAuthorship

    return (
        ArticleAuthorship.objects.effective()
        .filter(
            user=user,
            article=article,
            article__primary_journal__status=JournalStatus.ACTIVE,
        )
        .select_related("article", "article__primary_journal", "user")
        .first()
    )


def can_access_author_workbench(user) -> bool:
    if not _active_user(user) or not getattr(user, "is_author", False):
        return False
    from ai_author_forum.articles.models import ArticleAuthorship

    return (
        ArticleAuthorship.objects.effective()
        .filter(
            user=user,
            article__primary_journal__status=JournalStatus.ACTIVE,
        )
        .exists()
    )


def can_create_submission(user, journal) -> bool:
    if not can_access_author_workbench(user):
        return False
    from ai_author_forum.journals.submission_services import (
        journal_accepts_author_submission,
    )

    return journal_accepts_author_submission(journal)


def can_view_submission(user, article) -> bool:
    return get_article_authorship(user, article) is not None


def can_edit_submission(user, article) -> bool:
    authorship = get_article_authorship(user, article)
    if authorship is None or not authorship.can_edit:
        return False
    if article.review_status != article.ReviewStatus.DRAFT:
        return False
    locked_delivery_states = {
        article.PublicationStatus.PLACED,
        article.PublicationStatus.BUILT,
        article.PublicationStatus.PUBLISHED,
    }
    return article.publication_status not in locked_delivery_states


def can_submit_submission(user, article) -> bool:
    return can_edit_submission(user, article)


def can_view_author_review_feedback(user, article) -> bool:
    return can_view_submission(user, article)


def filter_author_submissions(user, queryset):
    if not _active_user(user) or not getattr(user, "is_author", False):
        return queryset.none()
    from ai_author_forum.articles.models import ArticleAuthorship

    article_ids = (
        ArticleAuthorship.objects.effective()
        .filter(
            user=user,
            article__primary_journal__status=JournalStatus.ACTIVE,
        )
        .values("article_id")
    )
    return queryset.filter(pk__in=article_ids)


def can_manage_journal_field(user, journal, field_name) -> bool:
    if not _active_user(user):
        return False
    if field_name in SENSITIVE_JOURNAL_FIELDS:
        return is_super_admin(user)
    responsibility = JournalEditorAssignment.Responsibility.JOURNAL_PROFILE
    if field_name in {
        "navigation",
        "columns",
        "categories",
        "hero_quick_links",
    }:
        responsibility = JournalEditorAssignment.Responsibility.COLUMN_NAVIGATION
    return can_manage_journal(user, journal, responsibility)


def _placement_target_journal(target_type, target):
    if target is None:
        return None
    if target_type in {"journal", "journal_home", "journal_article", "article"}:
        return getattr(target, "journal", None) or getattr(
            target, "primary_journal", target
        )
    if target_type in {"journal_category", "category"}:
        return getattr(target, "journal", None)
    journal = getattr(target, "journal", None)
    if journal is not None:
        return journal
    category = getattr(target, "target_category", None)
    return getattr(category, "journal", None)


def can_manage_placement_target(user, article, target_type, target) -> bool:
    if not _active_user(user) or article is None:
        return False
    if is_super_admin(user):
        return True
    assignment = get_journal_editor_assignment(user, article.primary_journal)
    if (
        assignment is None
        or assignment.role != JournalEditorAssignment.Role.CHIEF_EDITOR
    ):
        return False
    if target_type in {
        "main",
        "main_site",
        "global",
        "global_column",
        "search",
        "cross_journal",
    }:
        return False
    target_journal = _placement_target_journal(target_type, target)
    return bool(
        target_journal
        and target_journal.pk == article.primary_journal_id
        and target_journal.pk == assignment.journal_id
    )


def can_maintain_placement_target(user, article, target_type, target) -> bool:
    """Return whether an editor may maintain an existing in-journal placement."""
    if not _active_user(user) or article is None:
        return False
    if is_super_admin(user):
        return True
    assignment = get_journal_editor_assignment(user, article.primary_journal)
    if assignment is None:
        return False
    if assignment.role == JournalEditorAssignment.Role.ASSOCIATE_EDITOR and (
        JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
        not in (assignment.responsibilities or [])
    ):
        return False
    if target_type in {
        "main",
        "main_site",
        "global",
        "global_column",
        "search",
        "cross_journal",
    }:
        return False
    target_journal = _placement_target_journal(target_type, target)
    return bool(
        target_journal
        and target_journal.pk == article.primary_journal_id
        and target_journal.pk == assignment.journal_id
    )


def can_publish_issue(user, issue) -> bool:
    if not _active_user(user) or issue is None:
        return False
    if is_super_admin(user):
        return True
    if issue.scope != PublicationIssueScope.JOURNAL or issue.journal_id is None:
        return False
    assignment = get_journal_editor_assignment(user, issue.journal)
    return bool(
        assignment
        and assignment.role
        in {
            JournalEditorAssignment.Role.CHIEF_EDITOR,
            JournalEditorAssignment.Role.EXECUTIVE_EDITOR,
        }
    )


def filter_accessible_journals(user, queryset):
    if not _active_user(user):
        return queryset.none()
    if is_super_admin(user):
        return queryset
    journal_ids = (
        JournalEditorAssignment.objects.effective()
        .filter(user=user)
        .values("journal_id")
    )
    return queryset.filter(pk__in=journal_ids)


def filter_accessible_articles(user, queryset):
    if not _active_user(user):
        return queryset.none()
    if is_super_admin(user):
        return queryset
    journal_ids = (
        JournalEditorAssignment.objects.effective()
        .filter(user=user)
        .values("journal_id")
    )
    return queryset.filter(primary_journal_id__in=journal_ids)


def filter_accessible_placements(user, queryset):
    if not _active_user(user):
        return queryset.none()
    if is_super_admin(user):
        return queryset

    from ai_author_forum.articles.models import ArticlePage

    assignments = list(
        JournalEditorAssignment.objects.effective()
        .filter(user=user)
        .values("journal_id", "role", "responsibilities")
    )
    chief_journal_ids = {
        row["journal_id"]
        for row in assignments
        if row["role"] == JournalEditorAssignment.Role.CHIEF_EDITOR
    }
    deputy_journal_ids = {
        row["journal_id"]
        for row in assignments
        if row["role"] == JournalEditorAssignment.Role.EXECUTIVE_EDITOR
        or (
            row["role"] == JournalEditorAssignment.Role.ASSOCIATE_EDITOR
            and JournalEditorAssignment.Responsibility.ARTICLE_MAINTENANCE
            in (row["responsibilities"] or [])
        )
    }
    journal_ids = chief_journal_ids | deputy_journal_ids
    same_journal_article_target = ArticlePage.objects.filter(
        static_slug=OuterRef("target_slug"),
        primary_journal_id=OuterRef("article__primary_journal_id"),
    )
    return (
        queryset.filter(article__primary_journal_id__in=journal_ids)
        .filter(
            Q(article__primary_journal_id__in=chief_journal_ids)
            | Q(
                article__primary_journal_id__in=deputy_journal_ids,
                article__last_static_published_at__isnull=False,
            )
        )
        .annotate(_same_journal_article_target=Exists(same_journal_article_target))
        .filter(
            Q(
                target_type="journal",
                target_slug=F("article__primary_journal__slug"),
            )
            | Q(
                target_type="category",
                target_category__journal_id=F("article__primary_journal_id"),
            )
            | Q(target_type="article", _same_journal_article_target=True)
        )
    )
