from django import forms
from django.db.models import F

from ai_author_forum.articles.models import ArticlePage, ArticleReviewRecord
from ai_author_forum.site_settings.access_control import (
    can_manage_journal,
    filter_accessible_articles,
    filter_accessible_journals,
    is_super_admin,
)

from .models import (
    IssueArticle,
    Journal,
    JournalEditorAssignment,
    PublicationIssue,
    PublicationIssueScope,
    PublicationIssueStatus,
)


def manageable_issue_journals(user):
    journals = filter_accessible_journals(user, Journal.objects.all())
    if is_super_admin(user):
        return journals
    allowed_ids = [
        journal.pk
        for journal in journals
        if can_manage_journal(
            user,
            journal,
            JournalEditorAssignment.Responsibility.ISSUE_MANAGEMENT,
        )
    ]
    return journals.filter(pk__in=allowed_ids)


class PublicationIssueDraftForm(forms.ModelForm):
    class Meta:
        model = PublicationIssue
        fields = (
            "scope",
            "journal",
            "slug",
            "volume_label",
            "issue_number",
            "title",
            "summary",
            "cover_image",
            "publication_date",
        )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["journal"].queryset = manageable_issue_journals(user).order_by(
            "sort_order", "name", "pk"
        )
        if not is_super_admin(user):
            self.fields["scope"].choices = ((PublicationIssueScope.JOURNAL, "Journal"),)
            self.fields["scope"].initial = PublicationIssueScope.JOURNAL
            self.fields["scope"].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        if not is_super_admin(self.user):
            cleaned["scope"] = PublicationIssueScope.JOURNAL
            journal = cleaned.get("journal")
            if (
                journal is None
                or not self.fields["journal"].queryset.filter(pk=journal.pk).exists()
            ):
                self.add_error("journal", "无权维护该子期刊的期次。")
        return cleaned


class IssueArticleForm(forms.ModelForm):
    class Meta:
        model = IssueArticle
        fields = ("issue", "article", "section_label", "sort_order")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        journals = manageable_issue_journals(user)
        issue_queryset = PublicationIssue.objects.filter(
            status=PublicationIssueStatus.DRAFT
        )
        if not is_super_admin(user):
            issue_queryset = issue_queryset.filter(
                scope=PublicationIssueScope.JOURNAL,
                journal__in=journals,
            )
        self.fields["issue"].queryset = issue_queryset.select_related(
            "journal"
        ).order_by("-publication_date", "-pk")
        self.fields["article"].queryset = (
            filter_accessible_articles(
                user,
                ArticlePage.objects.filter(
                    review_status__in=(
                        ArticlePage.ReviewStatus.APPROVED,
                        ArticlePage.ReviewStatus.PUBLISHED,
                    ),
                    approved_version__isnull=False,
                    review_records__stage=ArticleReviewRecord.Stage.FINAL,
                    review_records__action=ArticleReviewRecord.Action.FINAL_APPROVE,
                    review_records__revision_id=F("approved_version_id"),
                ),
            )
            .distinct()
            .order_by("title", "pk")
        )
