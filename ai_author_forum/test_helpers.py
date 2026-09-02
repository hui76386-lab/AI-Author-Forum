"""Test-only builders for business states that production services own."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from ai_author_forum.articles.models import ArticleCategoryAssignment, ArticlePage
from ai_author_forum.articles.review_services import (
    final_review_article,
    initial_review_article,
    submit_article_for_initial_review,
)
from ai_author_forum.journals.editor_services import appoint_journal_editor
from ai_author_forum.journals.models import JournalCategory, JournalEditorAssignment
from ai_author_forum.users.services import SUPER_ADMIN_GROUP_NAME


def grant_business_super_admin(user):
    group, _created = Group.objects.get_or_create(name=SUPER_ADMIN_GROUP_NAME)
    user.groups.add(group)
    if not user.is_staff:
        user.is_staff = True
        user.save(update_fields=("is_staff",))
    return user


def create_test_user(username, *, is_staff=True, password="test-password"):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        display_name=username.replace("-", " ").title(),
        password=password,
        is_staff=is_staff,
    )


def ensure_test_journal_chief(*, journal, actor):
    assignment = (
        JournalEditorAssignment.objects.effective()
        .filter(
            journal=journal,
            role=JournalEditorAssignment.Role.CHIEF_EDITOR,
        )
        .select_related("user")
        .first()
    )
    if assignment is not None:
        return assignment.user
    username = f"test-chief-journal-{journal.pk}"
    chief, created = get_user_model().objects.get_or_create(
        username=username,
        defaults={
            "email": f"{username}@example.com",
            "display_name": f"Test Chief Journal {journal.pk}",
            "is_staff": True,
        },
    )
    if created or not chief.has_usable_password():
        chief.set_password("test-chief-password")
        chief.save(update_fields=("password",))
    appoint_journal_editor(
        actor=grant_business_super_admin(actor),
        user=chief,
        journal=journal,
        role=JournalEditorAssignment.Role.CHIEF_EDITOR,
        responsibilities=(),
        public_profile={
            "public_name": chief.display_name,
            "public_role_label": "主编",
            "display_order": 1,
            "show_publicly": True,
        },
    )
    return chief


def ensure_test_primary_category(article):
    primary = article.category_assignments.filter(is_primary=True).first()
    if primary is not None:
        return primary.category
    existing = article.category_assignments.order_by("sort_order", "pk").first()
    if existing is not None:
        existing.is_primary = True
        existing.save(update_fields=("is_primary",))
        return existing.category
    category = (
        JournalCategory.objects.filter(journal=article.primary_journal)
        .order_by("sort_order", "pk")
        .first()
    )
    if category is not None:
        ArticleCategoryAssignment.objects.create(
            article=article,
            category=category,
            is_primary=True,
        )
        return category
    code = f"test-review-{article.primary_journal_id}"
    category, _created = JournalCategory.objects.get_or_create(
        journal=article.primary_journal,
        code=code,
        defaults={
            "name": "Test review category",
            "slug": code,
            "depth": 1,
            "path_cache": code,
        },
    )
    ArticleCategoryAssignment.objects.create(
        article=article,
        category=category,
        is_primary=True,
    )
    return category


def formally_approve_test_article(article, *, actor):
    """Create an approved fixture through the same two-stage production services."""
    actor = grant_business_super_admin(actor)
    chief = ensure_test_journal_chief(journal=article.primary_journal, actor=actor)
    ensure_test_primary_category(article)
    article.refresh_from_db()
    revision = article.save_revision(
        user=chief,
        bypass_article_permission_check=True,
    )
    submit_article_for_initial_review(
        actor=chief,
        article=article,
        expected_state=ArticlePage.ReviewStatus.DRAFT,
        expected_revision_id=revision.pk,
        request_id=uuid4(),
        comment="Test fixture submitted through the formal review workflow.",
    )
    article.refresh_from_db()
    initial_review_article(
        actor=chief,
        article=article,
        action="approve",
        comment="Test fixture initial review approved.",
        expected_state=ArticlePage.ReviewStatus.SUBMITTED,
        expected_revision_id=revision.pk,
        request_id=uuid4(),
    )
    article.refresh_from_db()
    final_review_article(
        actor=chief,
        article=article,
        action="approve",
        comment="Test fixture final review approved.",
        expected_state=ArticlePage.ReviewStatus.PENDING_FINAL,
        expected_revision_id=revision.pk,
        request_id=uuid4(),
    )
    article.refresh_from_db()
    return article
