from __future__ import annotations

from django import forms
from django.db.models import Count, Q

from ai_author_forum.journals.models import Journal, JournalStatus
from ai_author_forum.static_publish.frontend import get_static_sections

from .models import ArticlePlacement, LayoutSlot
from .services import get_journal_placeable_articles, get_placeable_articles

TARGET_SEPARATOR = ":"
TARGET_SCOPE_MAP = {
    ArticlePlacement.TargetType.MAIN_SITE: LayoutSlot.Scope.HOME,
    ArticlePlacement.TargetType.SECTION: LayoutSlot.Scope.SECTION,
    ArticlePlacement.TargetType.JOURNAL: LayoutSlot.Scope.JOURNAL,
    ArticlePlacement.TargetType.SEARCH: LayoutSlot.Scope.SEARCH,
}


def make_target_value(target_type: str, target_slug: str = "") -> str:
    return f"{target_type}{TARGET_SEPARATOR}{target_slug}"


def split_target_value(value: str) -> tuple[str, str]:
    target_type, separator, target_slug = (value or "").partition(TARGET_SEPARATOR)
    if not separator:
        raise forms.ValidationError("请选择有效的投放目标。")
    return target_type, target_slug.strip().strip("/")


def get_target_choices(*, include_blank: bool = False):
    choices = []
    if include_blank:
        choices.append(("", "全部目标"))
    choices.extend(
        [
            (
                "主站",
                [
                    (
                        make_target_value(ArticlePlacement.TargetType.MAIN_SITE),
                        "主站首页",
                    )
                ],
            ),
            (
                "栏目",
                [
                    (
                        make_target_value(
                            ArticlePlacement.TargetType.SECTION, section["slug"]
                        ),
                        section["title"],
                    )
                    for section in get_static_sections()
                ],
            ),
            (
                "子期刊",
                [
                    (
                        make_target_value(
                            ArticlePlacement.TargetType.JOURNAL, journal.slug
                        ),
                        journal.name_cn or journal.name,
                    )
                    for journal in Journal.objects.filter(
                        status=JournalStatus.ACTIVE
                    ).order_by("name", "pk")
                ],
            ),
            (
                "Search",
                [
                    (
                        make_target_value(ArticlePlacement.TargetType.SEARCH, "search"),
                        "Search 静态推荐页",
                    )
                ],
            ),
        ]
    )
    return choices


def get_target_label(target_type: str, target_slug: str = "") -> str:
    value = make_target_value(target_type, target_slug)
    for choice in get_target_choices():
        if isinstance(choice[1], list | tuple):
            for candidate, label in choice[1]:
                if candidate == value:
                    return str(label)
        elif choice[0] == value:
            return str(choice[1])
    return target_slug or dict(ArticlePlacement.TargetType.choices).get(
        target_type, target_type
    )


class PlacementAdminForm(forms.ModelForm):
    target = forms.ChoiceField(label="投放目标")

    class Meta:
        model = ArticlePlacement
        fields = (
            "article",
            "target",
            "slot",
            "override_title",
            "override_summary",
            "override_image",
            "override_image_alt",
            "is_pinned",
            "sort_order",
            "starts_at",
            "ends_at",
            "is_active",
        )
        labels = {
            "article": "审核通过文章",
            "slot": "版位",
            "override_title": "展示标题覆盖",
            "override_summary": "展示摘要覆盖",
            "override_image": "仅覆盖当前首页版位图片",
            "override_image_alt": "图片替代文本（Alt）",
            "is_pinned": "置顶",
            "sort_order": "排序值",
            "starts_at": "生效时间",
            "ends_at": "失效时间",
            "is_active": "启用投放",
        }
        help_texts = {
            "article": (
                "默认使用文章封面。修改文章封面会影响文章详情页和所有未设置投放覆盖图的版位。"
            ),
            "override_image": (
                "仅影响当前版位，不修改文章详情页封面；适用于 Hero 横版裁切或当前版位独立构图。"
            ),
            "override_image_alt": (
                "请描述图片在当前版位中的信息；未填写时按文章封面 Alt、图片描述或图片标题回退。正式发布前必须有可用 Alt，不得仅重复完整文章标题。"
            ),
        }
        widgets = {
            "override_summary": forms.Textarea(attrs={"rows": 4}),
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, article_search: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].choices = get_target_choices()
        self.fields["article"].queryset = get_placeable_articles(article_search)
        if self.instance.pk and self.instance.article_id:
            self.fields["article"].queryset = (
                self.fields["article"].queryset
                | self.instance.article.__class__.objects.filter(
                    pk=self.instance.article_id
                )
            ).distinct()
        self.fields["slot"].queryset = LayoutSlot.objects.filter(
            Q(is_active=True) | Q(pk=getattr(self.instance, "slot_id", None))
        ).order_by("scope", "sort_order", "code")
        self.fields["starts_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        self.fields["ends_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        if self.instance.pk:
            self.initial["target"] = make_target_value(
                self.instance.target_type, self.instance.target_slug
            )

    def clean(self):
        cleaned_data = super().clean()
        target_value = cleaned_data.get("target")
        if not target_value:
            return cleaned_data

        target_type, target_slug = split_target_value(target_value)
        allowed_target_types = set(TARGET_SCOPE_MAP)
        if target_type not in allowed_target_types:
            self.add_error("target", "该投放目标类型不在本期受控范围内。")
            return cleaned_data

        slot = cleaned_data.get("slot")
        expected_scope = TARGET_SCOPE_MAP[target_type]
        if slot and slot.scope != expected_scope:
            self.add_error(
                "slot",
                f"所选目标只能使用 {dict(LayoutSlot.Scope.choices)[expected_scope]} 版位。",
            )

        article = cleaned_data.get("article")
        if target_type == ArticlePlacement.TargetType.JOURNAL and article:
            related = article.related_journals.filter(slug=target_slug).exists()
            if article.primary_journal.slug != target_slug and not related:
                self.add_error("target", "文章不属于所选子期刊，不能跨期刊投放。")

        self.instance.target_type = target_type
        self.instance.target_slug = target_slug
        return cleaned_data

    def save(self, commit=True):
        placement = super().save(commit=False)
        placement.target_type, placement.target_slug = split_target_value(
            self.cleaned_data["target"]
        )
        if commit:
            placement.save()
            self.save_m2m()
        return placement


class BulkJournalPlacementForm(forms.Form):
    journal = forms.ModelChoiceField(
        label="目标子期刊",
        queryset=Journal.objects.none(),
        to_field_name="slug",
        empty_label=None,
    )
    articles = forms.ModelMultipleChoiceField(
        label="批量选择文章",
        queryset=get_placeable_articles().none(),
        widget=forms.SelectMultiple(attrs={"size": 12}),
        help_text="按 Ctrl（Windows）或 Command（macOS）可连续选择多篇文章。",
    )
    slot = forms.ModelChoiceField(
        label="子期刊版位",
        queryset=LayoutSlot.objects.none(),
        empty_label=None,
    )
    is_pinned = forms.BooleanField(
        required=False,
        label="批量置顶",
        help_text="所选文章会作为同一批次置顶，但仍保持批次内顺序。",
    )
    starts_at = forms.DateTimeField(
        required=False,
        label="生效时间",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )
    ends_at = forms.DateTimeField(
        required=False,
        label="失效时间",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        journals = Journal.objects.filter(status=JournalStatus.ACTIVE).order_by(
            "name", "pk"
        )
        self.fields["journal"].queryset = journals
        self.fields["slot"].queryset = LayoutSlot.objects.filter(
            scope=LayoutSlot.Scope.JOURNAL,
            is_active=True,
        ).order_by("sort_order", "code")
        self.fields["starts_at"].input_formats = ("%Y-%m-%dT%H:%M",)
        self.fields["ends_at"].input_formats = ("%Y-%m-%dT%H:%M",)

        journal = None
        journal_value = (
            self.data.get(self.add_prefix("journal")) if self.is_bound else None
        )
        if journal_value:
            journal = journals.filter(slug=journal_value).first()
        if journal is None:
            initial_journal = self.initial.get("journal")
            if isinstance(initial_journal, Journal):
                journal = initial_journal
            elif initial_journal:
                journal = journals.filter(slug=initial_journal).first()
        if journal is None:
            journal = journals.first()
        if journal is not None:
            self.initial.setdefault("journal", journal)
            self.fields["articles"].queryset = get_journal_placeable_articles(journal)

    def clean(self):
        cleaned_data = super().clean()
        journal = cleaned_data.get("journal")
        slot = cleaned_data.get("slot")
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        if slot and slot.scope != LayoutSlot.Scope.JOURNAL:
            self.add_error("slot", "批量投放只能选择子期刊版位。")
        if slot and not slot.is_active:
            self.add_error("slot", "所选子期刊版位已停用。")
        if journal and journal.status != JournalStatus.ACTIVE:
            self.add_error("journal", "目标子期刊未启用。")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "失效时间必须晚于生效时间。")
        return cleaned_data


class PlacementFilterForm(forms.Form):
    article_query = forms.CharField(
        required=False,
        label="文章筛选",
        widget=forms.TextInput(attrs={"placeholder": "标题、静态 slug、作者或子期刊"}),
    )
    target = forms.ChoiceField(required=False, label="投放目标")
    slot = forms.ModelChoiceField(
        required=False,
        label="版位",
        queryset=LayoutSlot.objects.none(),
        empty_label="全部版位",
    )
    expires_within = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=365,
        widget=forms.HiddenInput(),
    )
    expired = forms.ChoiceField(
        required=False,
        choices=(("", ""), ("1", "1")),
        widget=forms.HiddenInput(),
    )
    active = forms.ChoiceField(
        required=False,
        choices=(("", ""), ("0", "0"), ("1", "1")),
        widget=forms.HiddenInput(),
    )
    capacity = forms.ChoiceField(
        required=False,
        choices=(("", ""), ("over", "over")),
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].choices = get_target_choices(include_blank=True)
        self.fields["slot"].queryset = LayoutSlot.objects.filter(
            is_active=True
        ).order_by("scope", "sort_order", "code")

    def clean_target(self):
        value = self.cleaned_data["target"]
        if value:
            split_target_value(value)
        return value


SLOT_SCOPE_CHOICES = (
    (LayoutSlot.Scope.HOME, "主站"),
    (LayoutSlot.Scope.SECTION, "栏目"),
    (LayoutSlot.Scope.JOURNAL, "子期刊"),
    (LayoutSlot.Scope.SEARCH, "Search"),
)


class LayoutSlotFilterForm(forms.Form):
    active = forms.ChoiceField(
        required=False,
        choices=(("", ""), ("0", "0"), ("1", "1")),
        widget=forms.HiddenInput(),
    )
    scope = forms.ChoiceField(
        label="版位范围",
        choices=SLOT_SCOPE_CHOICES,
        initial=LayoutSlot.Scope.HOME,
    )
    slot = forms.ModelChoiceField(
        required=False,
        label="具体版位",
        queryset=LayoutSlot.objects.none(),
        empty_label="自动选择该范围首个版位",
    )
    target = forms.ChoiceField(required=False, label="预览目标")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slot"].queryset = LayoutSlot.objects.filter(
            scope__in=dict(SLOT_SCOPE_CHOICES)
        ).order_by("scope", "sort_order", "code")
        self.fields["target"].choices = get_target_choices(include_blank=True)

    def clean(self):
        cleaned_data = super().clean()
        scope = cleaned_data.get("scope") or LayoutSlot.Scope.HOME
        slot = cleaned_data.get("slot")
        if slot and slot.scope != scope:
            self.add_error("slot", "所选版位不属于当前版位范围。")

        target_value = cleaned_data.get("target")
        if target_value:
            target_type, _target_slug = split_target_value(target_value)
            if TARGET_SCOPE_MAP.get(target_type) != scope:
                self.add_error("target", "所选预览目标不属于当前版位范围。")
        elif scope == LayoutSlot.Scope.HOME:
            cleaned_data["target"] = make_target_value(
                ArticlePlacement.TargetType.MAIN_SITE
            )
        elif scope == LayoutSlot.Scope.SEARCH:
            cleaned_data["target"] = make_target_value(
                ArticlePlacement.TargetType.SEARCH, "search"
            )
        return cleaned_data


class LayoutSlotAdminForm(forms.ModelForm):
    class Meta:
        model = LayoutSlot
        fields = (
            "title",
            "max_items",
            "fill_mode",
            "description",
            "is_active",
            "sort_order",
        )
        labels = {
            "title": "后台显示名称",
            "max_items": "最多展示数量",
            "fill_mode": "取数模式",
            "description": "运营说明",
            "is_active": "启用版位",
            "sort_order": "后台排序值",
        }
        help_texts = {
            "title": "仅修改后台显示名称；稳定版位编码和模板范围不可编辑。",
            "max_items": "限制同一目标在该版位中的启用投放数量。",
            "fill_mode": "手动模式只展示投放内容；自动模式保留统一规则补位能力。",
            "description": "说明版位用途和运营约束，不会改变前台模板结构。",
            "is_active": "停用后正式查询和静态页面将不再读取该版位内容。",
            "sort_order": "仅控制后台版位列表顺序，不改变前台固定布局。",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_max_items(self):
        max_items = self.cleaned_data["max_items"]
        if not self.instance.pk:
            return max_items

        largest_group = (
            self.instance.placements.filter(is_active=True)
            .values("target_type", "target_slug")
            .annotate(total=Count("pk"))
            .order_by("-total")
            .first()
        )
        if largest_group and largest_group["total"] > max_items:
            raise forms.ValidationError(
                f"该版位同一目标当前最多有 {largest_group['total']} 条启用投放，"
                "请先在投放管理中调整内容后再降低上限。"
            )
        return max_items
