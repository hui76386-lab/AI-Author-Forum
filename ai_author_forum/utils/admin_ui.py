from __future__ import annotations

import re

from django import forms
from django.utils import translation

from ai_author_forum.utils.admin_i18n import ADMIN_TRANSLATIONS
from ai_author_forum.utils.i18n import (
    DEFAULT_LANGUAGE,
    ENGLISH_LANGUAGE,
    UI_TRANSLATIONS,
)

ADMIN_ENGLISH_LABELS = {
    "所有文章": "All articles",
    "待审核文章": "Articles pending review",
    "审核详情": "Review details",
    "AI 文章": "AI Article",
    "新闻": "News",
    "观点": "Opinion",
    "研究分析": "Research analysis",
    "草稿": "Draft",
    "待审核": "Pending review",
    "审核通过": "Approved",
    "已驳回": "Rejected",
    "已发布（兼容状态）": "Published (legacy status)",
    "已通过，待投放": "Approved, awaiting placement",
    "已投放": "Placed",
    "静态 HTML 已构建": "Static HTML built",
    "静态版本已发布": "Static version published",
    "已下线": "Offline",
    "最近更新": "Recently updated",
    "最早更新": "Oldest updated",
    "标题 A-Z": "Title A-Z",
    "标题 Z-A": "Title Z-A",
    "审核状态": "Review status",
    "交付状态": "Delivery status",
    "投放目标": "Placement target",
    "全部目标": "All targets",
    "主站": "Main site",
    "主站首页": "Main-site homepage",
    "栏目": "Section",
    "子期刊": "Journal",
    "Search 静态推荐页": "Search static recommendations",
    "审核通过文章": "Approved article",
    "版位": "Slot",
    "展示标题覆盖": "Display title override",
    "展示摘要覆盖": "Display summary override",
    "展示图片覆盖": "Display image override",
    "置顶": "Pinned",
    "排序值": "Sort order",
    "生效时间": "Starts at",
    "失效时间": "Ends at",
    "启用投放": "Enable placement",
    "目标子期刊": "Target journal",
    "批量选择文章": "Select articles in bulk",
    "子期刊版位": "Journal slot",
    "批量置顶": "Pin selected articles",
    "文章筛选": "Article filter",
    "全部版位": "All slots",
    "按 Ctrl（Windows）或 Command（macOS）可连续选择多篇文章。": "Use Ctrl (Windows) or Command (macOS) to select multiple articles.",
    "所选文章会作为同一批次置顶，但仍保持批次内顺序。": "Selected articles are pinned as one batch while preserving their internal order.",
    "标题、静态 slug、作者或子期刊": "Title, static slug, author, or journal",
}


# These labels are also used for values returned by model choices and import jobs.
# Keeping them here makes the English admin contract independent from the active
# database content language.
ADMIN_ENGLISH_LABELS.update(
    {
        "\u4e2d\u6587": "Chinese",
        "\u6240\u6709\u6587\u7ae0": "All articles",
        "AI \u6587\u7ae0": "AI Article",
        "\u6587\u7ae0": "Article",
        "\u5b50\u671f\u520a": "Journal",
        "\u5f85\u5904\u7406": "Pending",
        "\u7b49\u5f85\u5904\u7406": "Waiting to be processed",
        "\u6821\u9a8c\u4e2d": "Validating",
        "\u5f85\u786e\u8ba4": "Ready for confirmation",
        "\u5bfc\u5165\u4e2d": "Importing",
        "\u5df2\u5b8c\u6210": "Completed",
        "\u5931\u8d25": "Failed",
        "\u6210\u529f": "Success",
        "\u5df2\u542f\u7528": "Enabled",
        "\u5df2\u505c\u7528": "Disabled",
        "\u5ba1\u6838\u901a\u8fc7": "Approved",
        "\u9759\u6001\u7248\u672c\u5df2\u53d1\u5e03": "Static version published",
        "\u7acb\u5373": "Immediately",
        "\u957f\u671f": "No end date",
        "\u7f3a\u5931": "Missing",
        "\u65e0\u53ef\u7528\u56fe\u7247": "No image available",
        "\u4e3b\u7ad9": "Main site",
        "\u5f53\u524d\u7248\u4f4d\u8986\u76d6\u56fe": "Current slot override",
        "\u6587\u7ae0\u5c01\u9762": "Article cover",
        "\u7ad9\u70b9\u9ed8\u8ba4\u56fe": "Site default image",
        "\u5360\u4f4d\u56fe": "Placeholder image",
        "\u6ca1\u6709\u6570\u636e\u884c\u3002": "No data rows.",
        "\u6ca1\u6709\u963b\u65ad\u9879\u3002": "No blockers.",
        "\u6ca1\u6709\u8b66\u544a\u9879\u3002": "No warnings.",
        "\u6700\u8fd1\u66f4\u65b0": "Recently updated",
        "\u6700\u65b0\u66f4\u65b0": "Most recently updated",
        "\u66f4\u65b0\u65f6\u95f4": "Last updated",
        "\u5e74/\u6708/\u65e5": "mm/dd/yyyy",
        "\u4e3b\u680f\u76ee": "Main section",
        "\u673a\u5668\u667a\u80fd": "Machine intelligence",
        "\u5f53\u524d\u6ca1\u6709\u5df2\u542f\u7528\u6295\u653e\u3002": "There are no enabled placements.",
    }
)


# Custom dashboard copy predates the shared translation catalogs. Keep complete
# phrase translations here so the English dashboard remains useful instead of
# degrading into generic placeholders.
ADMIN_DASHBOARD_ENGLISH_LABELS = {
    "\u5ba1\u6838": "Review",
    "\u6295\u653e": "Placement",
    "\u6587\u7ae0": "Article",
    "\u6682\u65e0": "None",
    "\u8fd1 ": "Last ",
    "\u5b50\u671f\u520a": "Journals",
    "\u5f85\u5ba1\u6838": "Pending review",
    "\u5f85\u6295\u653e": "Awaiting placement",
    "\u88ab\u9a73\u56de": "Rejected",
    " \u5929\u53d1\u5e03": " days published",
    " \u5929\u5ba1\u6838": " days reviewed",
    " \u5929\u7f16\u8f91": " days edited",
    "\u53d1\u5e03\u5931\u8d25": "Publishing failed",
    "\u53ea\u8bfb\u6458\u8981": "Read-only summary",
    "\u5931\u8d25\u5ba1\u8ba1": "Failed audit events",
    "\u5931\u8d25\u76ee\u6807": "Failed targets",
    "\u5ba1\u8ba1\u65e5\u5fd7": "Audit logs",
    "\u6211\u7684\u8349\u7a3f": "My drafts",
    "\u6279\u91cf\u5bfc\u5165": "Bulk import",
    "\u6295\u653e\u7ba1\u7406": "Placement management",
    "\u6587\u7ae0\u7ba1\u7406": "Article management",
    "\u680f\u76ee\u5f02\u5e38": "Category issues",
    "\u680f\u76ee\u7ba1\u7406": "Category management",
    "\u6d3b\u52a8\u7248\u672c": "Active version",
    "\u7248\u4f4d\u7ba1\u7406": "Slot management",
    "\u7b49\u5f85\u53d1\u5e03": "Awaiting publication",
    "\u8d85\u65f6\u5f85\u5ba1": "Overdue reviews",
    "\u9759\u6001\u53d1\u5e03": "Static publishing",
    " \u5929\u5185\u5230\u671f": " days until expiry",
    "\u5185\u5bb9\u5de5\u4f5c\u53f0": "Content workspace",
    "\u53ef\u56de\u6eda\u7248\u672c": "Rollback versions",
    "\u542f\u7528\u5b50\u671f\u520a": "Active journals",
    "\u5ba1\u6838\u5de5\u4f5c\u53f0": "Review workspace",
    "\u6309\u6587\u7ae0\u5de5\u4f5c": "Work by article",
    "\u6682\u505c\u5b50\u671f\u520a": "Paused journals",
    "\u8d85\u5bb9\u91cf\u7248\u4f4d": "Over-capacity slots",
    "\u9009\u62e9\u5b50\u671f\u520a": "Select a journal",
    " \u5c0f\u65f6\u8ba1\u7b97\u3002": " hours.",
    "\u542f\u7528\u4e3b\u7ad9\u7248\u4f4d": "Active main-site slots",
    "\u5931\u8d25\u53d1\u5e03\u4efb\u52a1": "Failed publishing jobs",
    "\u5df2\u5230\u671f\u4ecd\u542f\u7528": "Expired but still enabled",
    "\u5f53\u524d\u6d3b\u52a8\u7248\u672c": "Current active version",
    "\u6309\u5b50\u671f\u520a\u5de5\u4f5c": "Work by journal",
    "\u8fdb\u5165\u6587\u7ae0\u7ba1\u7406": "Open article management",
    "\u8fdb\u5165\u9759\u6001\u53d1\u5e03": "Open static publishing",
    "\u914d\u7f6e\u6587\u7ae0\u6295\u653e": "Configure article placements",
    "\u9759\u6001\u53d1\u5e03\u4e2d\u5fc3": "Static publishing center",
    "\u5904\u7406\u5f85\u5ba1\u6838\u6587\u7ae0": "Process pending reviews",
    "\u6279\u91cf\u5bfc\u5165\u5b50\u671f\u520a": "Import journals in bulk",
    "\u6309\u53d1\u5e03\u7248\u672c\u5de5\u4f5c": "Work by publishing version",
    "\u6309\u7f16\u6392\u76ee\u6807\u5de5\u4f5c": "Work by layout target",
    "\u7ad9\u70b9\u8fd0\u8425\u5de5\u4f5c\u53f0": "Site operations workspace",
    "\u9759\u6001\u53d1\u5e03\u5de5\u4f5c\u53f0": "Static publishing workspace",
    "\u8d85\u65f6\u5f85\u5ba1\u6309\u63d0\u4ea4\u540e\u8d85\u8fc7 ": "Reviews become overdue after ",
    "\u5148\u9884\u89c8\u6821\u9a8c\uff0c\u518d\u786e\u8ba4\u5bfc\u5165\u3002": "Preview and validate before confirming the import.",
    "\u65b0\u5efa\u3001\u7ee7\u7eed\u7f16\u8f91\u6216\u63d0\u4ea4\u6587\u7ae0\u3002": "Create, continue editing, or submit articles.",
    "\u6784\u5efa\u3001\u53d1\u5e03\u5e76\u68c0\u67e5\u9010\u9875\u9762\u7ed3\u679c\u3002": "Build, publish, and inspect per-page results.",
    "\u67e5\u770b\u6b63\u6587\u5dee\u5f02\u5e76\u8bb0\u5f55\u5ba1\u6838\u610f\u89c1\u3002": "Review content differences and record feedback.",
    "\u4ec5\u5ba1\u6838\u901a\u8fc7\u7684\u6587\u7ae0\u53ef\u8fdb\u5165\u53d7\u63a7\u7248\u4f4d\u3002": "Only approved articles can enter controlled slots.",
    "\u5148\u786e\u5b9a\u5185\u5bb9\u5f52\u5c5e\u3001\u680f\u76ee\u548c\u671f\u520a\u8d44\u6599\u3002": "Set content ownership, categories, and journal details first.",
    "\u5ba1\u6838\u901a\u8fc7\u53ea\u4ee3\u8868\u6587\u7ae0\u5177\u5907\u6295\u653e\u8d44\u683c\u3002": "Approval only makes an article eligible for placement.",
    "\u6587\u7ae0\u5f52\u5c5e\u5b50\u671f\u520a\uff0c\u4f46\u4e0d\u4f1a\u56e0\u6b64\u81ea\u52a8\u5c55\u793a\u3002": "A journal assignment does not automatically display an article.",
    "\u6c47\u603b\u5b50\u671f\u520a\u3001\u680f\u76ee\u548c\u53d7\u63a7\u7248\u4f4d\u7684\u8fd0\u884c\u5f02\u5e38\u3002": "Summarize operational issues across journals, categories, and controlled slots.",
    "\u9009\u62e9\u76ee\u6807\u9875\u9762\u3001\u56fa\u5b9a\u7248\u4f4d\u3001\u6392\u5e8f\u548c\u751f\u6548\u65f6\u95f4\u3002": "Choose the target page, fixed slot, order, and activation time.",
    "\u805a\u5408\u5c55\u793a\u4e2a\u4eba\u7f16\u8f91\u5f85\u529e\u548c\u5df2\u5ba1\u6838\u4f46\u5c1a\u672a\u5b8c\u6210\u6295\u653e\u7684\u6587\u7ae0\u3002": "Show personal editing tasks and approved articles that still need placement.",
    "\u4ec5\u63d0\u4f9b\u67e5\u770b\u5165\u53e3\uff0c\u4e0d\u63d0\u4f9b\u4fdd\u5b58\u3001\u5bfc\u5165\u3001\u53d1\u5e03\u3001\u91cd\u8bd5\u6216\u56de\u6eda\u64cd\u4f5c\u3002": "Provides view-only access without save, import, publish, retry, or rollback actions.",
    "\u6784\u5efa\u56fa\u5b9a HTML\uff0c\u5e76\u901a\u8fc7 manifest \u53d1\u5e03\u6216\u56de\u6eda\u3002": "Build fixed HTML and publish or roll back through the manifest.",
    "\u5728\u4e3b\u7ad9\u3001\u680f\u76ee\u6216\u5b50\u671f\u520a\u7684\u56fa\u5b9a\u7248\u4f4d\u4e2d\u914d\u7f6e\u6587\u7ae0\u3001\u6392\u5e8f\u3001\u7f6e\u9876\u548c\u65f6\u95f4\u3002": "Configure articles, ordering, pinning, and timing in fixed main-site, section, or journal slots.",
    "\u6b63\u6587\u3001\u4e3b\u5c5e\u671f\u520a\u548c\u5ba1\u6838\u72b6\u6001\u5c5e\u4e8e\u6587\u7ae0\uff1b\u524d\u53f0\u51fa\u73b0\u4f4d\u7f6e\u7531\u6295\u653e\u5355\u72ec\u63a7\u5236\u3002": "Article content, primary journal, and review status belong to the article; placement separately controls where it appears.",
    "\u5c55\u793a\u53d1\u5e03\u7ed3\u679c\u548c\u53ef\u6062\u590d\u7248\u672c\uff1b\u5b9e\u9645\u53d1\u5e03\u3001\u91cd\u8bd5\u548c\u56de\u6eda\u4ecd\u5728\u53d1\u5e03\u4e2d\u5fc3\u6267\u884c\u3002": "Show publishing results and recoverable versions; publishing, retries, and rollbacks remain in the publishing center.",
    "\u67e5\u770b\u9010\u9875\u9762\u7ed3\u679c\u3001\u5931\u8d25\u91cd\u8bd5\u3001\u6d3b\u52a8 manifest \u548c\u53ef\u56de\u6eda\u7248\u672c\u3002": "View per-page results, failed retries, the active manifest, and rollback versions.",
    "\u4ee5\u4e00\u4e2a\u5b50\u671f\u520a\u4e3a\u4e3b\u5bf9\u8c61\uff0c\u518d\u8fdb\u5165\u5b83\u7684\u8d44\u6599\u3001\u680f\u76ee\u3001\u6587\u7ae0\u3001\u6295\u653e\u548c\u9759\u6001\u4e3b\u9875\u3002": "Choose a journal first, then manage its profile, categories, articles, placements, and static homepage.",
    "\u4e1a\u52a1\u5de5\u4f5c\u53f0": "Business workspace",
    "\u6309\u201c\u4e3b\u5bf9\u8c61 \u2192 \u4ece\u5c5e\u64cd\u4f5c\u201d\u5b8c\u6210\u5185\u5bb9\u4ea4\u4ed8": "Deliver content through a primary-object workflow",
    "\u5148\u9009\u62e9\u5b50\u671f\u520a\u6216\u6587\u7ae0\uff0c\u518d\u5904\u7406\u5b83\u4e0b\u9762\u7684\u680f\u76ee\u3001\u5ba1\u6838\u3001\u6295\u653e\u4e0e\u53d1\u5e03\u3002\u6587\u7ae0\u5f52\u5c5e\u548c\u524d\u53f0\u5c55\u793a\u5206\u5f00\u7ba1\u7406\u3002": "Choose a journal or article first, then manage its categories, review, placement, and publishing. Content ownership and public display are managed separately.",
    "\u5feb\u6377\u64cd\u4f5c": "Quick actions",
    "\u7edf\u4e00\u6d41\u7a0b": "Unified workflow",
    "\u4ece\u5185\u5bb9\u5f52\u5c5e\u5230\u56fa\u5b9a HTML": "From content ownership to fixed HTML",
    "\u5ba1\u6838\u901a\u8fc7\u4e0d\u4f1a\u81ea\u52a8\u4e0a\u7ebf\uff0c\u5fc5\u987b\u7ecf\u8fc7\u6295\u653e\u548c\u9759\u6001\u53d1\u5e03\u3002": "Approval does not publish automatically; placement and static publishing are still required.",
    "\u4e3b\u4ece\u5173\u7cfb": "Primary-object relationships",
    "\u5148\u9009\u4e3b\u5bf9\u8c61\uff0c\u518d\u505a\u76f8\u5173\u64cd\u4f5c": "Choose the primary object before related actions",
    "\u6bcf\u5f20\u5361\u7247\u53ea\u5c55\u793a\u5f53\u524d\u89d2\u8272\u53ef\u4ee5\u8bbf\u95ee\u7684\u5165\u53e3\u3002": "Each card shows only the entries available to the current role.",
    "\u5f53\u524d\u89d2\u8272": "Current role",
    "\u5e38\u7528\u64cd\u4f5c": "Common actions",
    "\u6570\u636e\u4e0e\u5f85\u529e": "Data and tasks",
    "\u9700\u8981\u5173\u6ce8\u7684\u5de5\u4f5c": "Work requiring attention",
    "\u6700\u8fd1\u8bb0\u5f55": "Recent records",
}


def _catalog_translations():
    translations = {}
    for catalog in (UI_TRANSLATIONS, ADMIN_TRANSLATIONS):
        source_catalog = catalog.get(DEFAULT_LANGUAGE, {})
        target_catalog = catalog.get(ENGLISH_LANGUAGE, {})
        for key, source in source_catalog.items():
            target = target_catalog.get(key)
            if source and target and source != target:
                translations[str(source)] = str(target)
    translations.update(ADMIN_ENGLISH_LABELS)
    translations.update(ADMIN_DASHBOARD_ENGLISH_LABELS)
    return translations


_ADMIN_FULL_TEXT_TRANSLATIONS = _catalog_translations()
_ADMIN_FULL_TEXT_REPLACEMENTS = tuple(
    sorted(
        _ADMIN_FULL_TEXT_TRANSLATIONS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)
_CHINESE_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\uff0c": ",",
        "\u3002": ".",
        "\uff1a": ":",
        "\uff1b": ";",
        "\u3001": ",",
        "\uff08": "(",
        "\uff09": ")",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\uff01": "!",
        "\uff1f": "?",
        "\u3010": "[",
        "\u3011": "]",
        "\u300a": '"',
        "\u300b": '"',
    }
)

_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def _translate_english_surface_text(content: str) -> str:
    translated = content
    for source, target in _ADMIN_FULL_TEXT_REPLACEMENTS:
        translated = translated.replace(source, target)
    translated = _HAN_RE.sub(
        lambda match: _ADMIN_FULL_TEXT_TRANSLATIONS.get(
            match.group(0), "Content unavailable in English"
        ),
        translated,
    )
    return translated.translate(_CHINESE_PUNCTUATION_TRANSLATION)


def is_english_admin() -> bool:
    return (translation.get_language() or "").lower().startswith("en")


def english_admin_text(value):
    """Return a display-safe English value for admin labels and data values."""
    if value is None:
        return value
    text = str(value)
    if not is_english_admin():
        return text
    return _translate_english_surface_text(text)


def sanitize_english_admin_html(content: str) -> str:
    """Guarantee that an English admin response cannot leak Han characters.

    Custom admin pages predate the language switch and contain a mixture of
    hard-coded Chinese labels and database-backed values. This final response
    guard covers pages which do not yet have a dedicated ``.en.html`` template,
    including form placeholders, validation messages, and inline JavaScript.
    Known labels are translated above; unknown source-language fragments use a
    clear English fallback rather than leaking Chinese into the English UI.
    """
    if not is_english_admin():
        return content
    return _translate_english_surface_text(content)


def admin_english(value):
    if value is None:
        return value
    text = str(value)
    return (
        english_admin_text(text)
        if is_english_admin()
        else ADMIN_ENGLISH_LABELS.get(text, text)
    )


def _translate_choices(choices):
    translated = []
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            label = _translate_choices(label)
        else:
            label = admin_english(label)
        translated.append((value, label))
    return translated


def translate_form_to_english(form: forms.BaseForm | None):
    if form is None:
        return form
    for field in form.fields.values():
        field.label = admin_english(field.label)
        field.help_text = admin_english(field.help_text)
        if getattr(field, "empty_label", None):
            field.empty_label = admin_english(field.empty_label)
        if hasattr(field, "choices") and not isinstance(field, forms.ModelChoiceField):
            field.choices = _translate_choices(list(field.choices))
        placeholder = field.widget.attrs.get("placeholder")
        if placeholder:
            field.widget.attrs["placeholder"] = admin_english(placeholder)
    return form
