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

# Common Wagtail field labels and controls that are supplied by model metadata
# rather than gettext.  Keeping these in one catalogue lets legacy Chinese
# forms share the same English surface while the underlying stored values stay
# unchanged.
ADMIN_ENGLISH_LABELS.update(
    {
        "选择": "Select",
        "选择期刊": "Select journal",
        "选择另一个期刊": "Select another journal",
        "选择另一个 journal": "Select another journal",
        "选择子期刊": "Select journal",
        "子期刊资料": "Journal profile",
        "期刊资料": "Journal profile",
        "期刊导航": "Journal navigation",
        "期次管理": "Issue management",
        "编辑团队": "Editorial team",
        "图片与素材": "Images and media",
        "选择文章": "Select article",
        "选择栏目": "Select category",
        "创建": "Create",
        "新建": "New",
        "编辑": "Edit",
        "删除": "Delete",
        "保存": "Save",
        "保存草稿": "Save draft",
        "保存草稿并返回列表": "Save draft and return to list",
        "保存后继续编辑": "Save and continue editing",
        "保存并新建": "Save and create another",
        "提交": "Submit",
        "取消": "Cancel",
        "关闭": "Close",
        "返回": "Back",
        "上一页": "Previous",
        "下一页": "Next",
        "首页": "Home",
        "摘要": "Summary",
        "作者": "Author",
        "期刊": "Journal",
        "期刊信息": "Journal information",
        "栏目": "Category",
        "动态栏目": "Dynamic category",
        "基础信息": "Basic information",
        "基本信息": "Basic information",
        "正文内容": "Content",
        "封面与摘要": "Cover and summary",
        "作者或编辑": "Author or editor",
        "通讯作者": "Corresponding author",
        "相关期刊": "Related journals",
        "文章类型": "Article type",
        "归属与分类": "Classification",
        "归属与文章类型": "Classification and article type",
        "审核状态": "Review status",
        "审核与责任": "Review and responsibility",
        "待初审": "Pending initial review",
        "待终审": "Pending final review",
        "初审": "Initial review",
        "终审": "Final review",
        "提交初审": "Submit for initial review",
        "初审通过": "Initial review approved",
        "初审通过并填写意见": "Approve initial review with comment",
        "初审退回": "Returned from initial review",
        "初审拒绝": "Rejected in initial review",
        "终审通过": "Final review approved",
        "终审通过并填写意见": "Approve final review with comment",
        "终审退回": "Returned from final review",
        "终审拒绝": "Rejected in final review",
        "待同步": "Pending synchronization",
        "已同步": "Synchronized",
        "同步失败": "Synchronization failed",
        "文章封面": "Article cover",
        "封面替代文本": "Cover alternative text",
        "用于无障碍访问；留空时依次使用图片标题和文章标题。": (
            "Used for accessibility; when blank, the image title and article "
            "title are used in that order."
        ),
        "正文": "Body",
        "默认使用可视化正文段落，也可按需插入章节标题、图片、引用、列表、表格和附件；无需手写 HTML。": (
            "Use visual body paragraphs by default. Add headings, images, "
            "quotes, lists, tables, and attachments as needed; no handwritten "
            "HTML is required."
        ),
        "多个作者请使用英文逗号分隔。": (
            "Separate multiple authors with English commas."
        ),
        "多个关键词请使用英文逗号分隔。": (
            "Separate multiple keywords with English commas."
        ),
        "AI 参与说明": "AI contribution statement",
        "作者声明": "Author statement",
        "静态交付状态；文章审核通过与前台发布分别记录。": (
            "Static delivery status; article approval and public publication "
            "are tracked separately."
        ),
        "最近一次成功生成该文章的静态发布版本。": (
            "The latest static release that successfully built this article."
        ),
        "当前包含该文章的活动静态发布版本。": (
            "The active static release that currently contains this article."
        ),
        "静态 HTML 输出路径片段；留空时根据文章标题自动生成。": (
            "Static HTML output path segment; generated from the article title "
            "when blank."
        ),
        "文章审核通过时对应的 revision。": (
            "The revision associated with article approval."
        ),
        "文章审核驳回时对应的 revision。": (
            "The revision associated with article rejection."
        ),
        "添加文章作者和编辑信息。可选择预设身份，或选择“自定义身份”后填写新身份名称。": (
            "Add article authors and editors. Choose a predefined identity, or "
            "select Custom identity and enter a new identity label."
        ),
        "审核前必填：": "Required before review:",
        "请至少选择一个动态栏目，并且仅勾选一个“主栏目”。": (
            "Select at least one dynamic category and mark exactly one as the "
            "primary category."
        ),
        "当前主属期刊没有可选栏目时，请先在“栏目管理”中配置栏目；导入流程不会自动创建栏目。": (
            "If the primary journal has no available categories, configure them "
            "in Category management first; import does not create categories."
        ),
        "保存草稿后，请在“所有文章”中提交审核；导入不会自动审核、投放或静态发布。": (
            "After saving the draft, submit it for review from All articles; "
            "import does not automatically review, place, or publish content."
        ),
        "动态栏目（只有一个栏目时自动设为主栏目）": (
            "Dynamic categories (a single category is made primary automatically)"
        ),
        "设为主栏目（只有一个栏目时自动勾选）": (
            "Set as primary (selected automatically when there is one category)"
        ),
        "流程提示：": "Workflow note:",
        "审核通过不等于前台发布，仍需进入投放管理，并由发布管理员执行静态发布。": (
            "Approval does not publish the article. Placement and a static "
            "release by a publishing administrator are still required."
        ),
        "纯文本字段；可留空，HTML、脚本和 iframe 不会作为标记渲染。": (
            "Plain text; may be blank. HTML, scripts, and iframes are not "
            "rendered as markup."
        ),
        "交付状态（只读）": "Delivery status (read only)",
        "下列字段由投放和静态发布流程维护，编辑表单不能修改。": (
            "The following fields are maintained by placement and static "
            "publishing and cannot be changed in the editor."
        ),
        "交付摘要": "Delivery summary",
        "诊断信息": "Diagnostic information",
        "交付状态": "Delivery status",
        "正文段落（可视化编辑）": "Body paragraph (visual editor)",
        "直接输入和排版正文，可使用标题、加粗、列表和链接。": (
            "Enter and format body text with headings, bold text, lists, and links."
        ),
        "用于文章正文中的章节标题。": "A section heading within the article body.",
        "章节标题": "Section heading",
        "替代文本": "Alternative text",
        "用于无障碍访问；留空时使用图片库标题。": (
            "Used for accessibility; the image library title is used when blank."
        ),
        "图片说明": "Image caption",
        "图片与说明": "Image and caption",
        "引用内容": "Quote text",
        "出处 / 作者": "Source / author",
        "引用": "Quote",
        "无序列表": "Unordered list",
        "有序列表": "Ordered list",
        "列表类型": "List type",
        "列表项": "List items",
        "在表格中直接添加、删除和编辑行列；建议首行作为表头。": (
            "Add, remove, and edit rows and columns directly; use the first row "
            "as the table header."
        ),
        "表格": "Table",
        "链接文字": "Link text",
        "留空时使用文档标题。": "The document title is used when blank.",
        "附件 / 文档": "Attachment / document",
        "高级：Raw HTML（需权限）": "Advanced: Raw HTML (permission required)",
        "仅限获授权人员处理历史内容或特殊嵌入；常规文章请使用上方内容块。": (
            "For authorized staff handling legacy content or special embeds; "
            "use the content blocks above for regular articles."
        ),
        "文章正文": "Article body",
        "主编": "Editor-in-chief",
        "执行主编": "Executive editor",
        "副编辑": "Associate editor",
        "发布与 SEO": "Publishing and SEO",
        "静态路径": "Static path",
        "预览": "Preview",
        "动态预览": "Dynamic preview",
        "图片": "Image",
        "文档": "Document",
        "推荐": "Recommended",
        "版位": "Slot",
        "投放": "Placement",
        "投放目标": "Placement target",
        "目标": "Target",
        "目标子期刊": "Target journal",
        "目标栏目": "Target category",
        "状态": "Status",
        "错误": "Error",
        "原因": "Reason",
        "说明": "Description",
        "操作": "Actions",
        "名称": "Name",
        "姓名": "Name",
        "中文名": "Chinese name",
        "英文名": "English name",
        "搜索": "Search",
        "筛选": "Filter",
        "应用筛选": "Apply filters",
        "清除筛选": "Clear filters",
        "刷新": "Refresh",
        "确认": "Confirm",
        "导入": "Import",
        "导出": "Export",
        "下载": "Download",
        "上传": "Upload",
        "创建时间": "Created",
        "更新时间": "Updated",
        "最近更新": "Last updated",
        "暂无": "None",
        "暂未": "Not yet",
        "无": "None",
        "全部": "All",
        "总计": "Total",
        "列表": "List",
        "根级栏目": "Root category",
        "新状态": "New status",
        "操作理由": "Action reason",
        "公开姓名": "Public name",
        "公开单位": "Public institution",
        "前台角色名称": "Public role label",
        "展示顺序": "Display order",
        "在编辑团队中公开": "Show on the editorial team",
        "用户名": "Username",
        "邮箱": "Email",
        "单位": "Institution",
        "职务/职称": "Job title",
        "临时密码": "Temporary password",
        "角色": "Role",
        "日常维护职责": "Maintenance responsibilities",
        "身份": "Identity",
        "自定义身份": "Custom identity",
        "仅在身份选择“自定义身份”时填写。": (
            "Complete this only when Custom identity is selected."
        ),
        "编辑这个期刊": "Edit this journal",
        "编辑这个 journal": "Edit this journal",
        "留空": "Leave blank",
        "启用": "Enabled",
        "停用": "Disabled",
        "归档": "Archived",
        "草稿": "Draft",
    }
)

ADMIN_ENGLISH_LABELS.update(
    {
        "投放步骤 / Placement steps": "Placement steps",
        "内容 / Content": "Content",
        "目标 / Target": "Target",
        "规则 / Rules": "Rules",
        "复核 / Review": "Review",
        "结果 / Result": "Result",
        "选择文章 / Select articles": "Select articles",
        "只显示审核通过且所属子期刊启用的文章。搜索支持标题、slug、摘要、作者、关键词和子期刊名称。": (
            "Only approved articles from active journals are shown. Search "
            "by title, slug, abstract, author, keyword, or journal name."
        ),
        "关键词 / Keyword": "Keyword",
        "标题、slug、摘要、作者或关键词": ("Title, slug, abstract, author, or keyword"),
        "名称、中文名或 slug": "Name, Chinese name, or slug",
        "搜索文章": "Search articles",
        "清空": "Clear",
        "选择当前页": "Select current page",
        "主属子期刊 / Journal": "Primary journal",
        "审核 / Review": "Review",
        "投放状态 / Placement": "Placement status",
        "可投放": "Eligible for placement",
        "未投放 · 优先": "Not placed - priority",
        "没有符合条件的文章": "No eligible articles found",
        "请尝试标题、slug、子期刊中文名或英文名。": (
            "Try a title, slug, or the journal's Chinese or English name."
        ),
        "投放前仍会再次校验审核状态、子期刊归属和版位容量。": (
            "Review status, journal ownership, and slot capacity are checked "
            "again before placement."
        ),
        "保存选择并继续": "Save selection and continue",
        "已有": "Existing",
        "个投放": "placements",
        "共": "Total",
        "已选": "selected",
        "篇": "articles",
    }
)

ADMIN_ENGLISH_LABELS.update(
    {
        "选择目标子期刊 / Select target journal": "Select target journal",
        "子期刊编排 / Journal curation": "Journal curation",
        "通过名称或 Slug 进行服务端搜索；支持每位用户独立的收藏和最近使用。 / Search server-side by name or slug, with per-user favourites and recency.": (
            "Search server-side by name or slug, with per-user favourites "
            "and recency."
        ),
        "名称或 Slug / Name or slug": "Name or slug",
        "范围 / Scope": "Scope",
        "全部 / All": "All",
        "我的收藏 / Favorites": "My favourites",
        "最近使用 / Recent": "Recently used",
        "每页 / Page size": "Page size",
        "搜索 / Search": "Search",
        "子期刊 / Journal": "Journal",
        "状态 / Status": "Status",
        "偏好 / Preference": "Preference",
        "操作 / Action": "Action",
        "收藏 / Favorite": "Favourite",
        "选择 / Select": "Select",
        "打开 / Open": "Open",
        "没有找到子期刊 / No journals found.": "No journals found.",
        "已收藏 / Favorited": "Favourited",
        "投放清单 / Placement list": "Placement list",
        "文章标题或 Slug / Article title or slug": "Article title or slug",
        "子期刊 Slug / Journal slug": "Journal slug",
        "目标类型 / Target type": "Target type",
        "目标 Slug / Target slug": "Target slug",
        "版位 / Slot": "Slot",
        "启用 / Active": "Active",
        "停用 / Inactive": "Inactive",
        "筛选 / Filter": "Filter",
        "导出 CSV / Export CSV": "Export CSV",
        "高级筛选 / Advanced filters": "Advanced filters",
        "生效不早于 / Starts after": "Starts after",
        "生效不晚于 / Starts before": "Starts before",
        "失效不早于 / Ends after": "Ends after",
        "失效不晚于 / Ends before": "Ends before",
        "即将失效天数 / Expires within days": "Expires within days",
        "已失效 / Expired": "Expired",
        "置顶 / Pinned": "Pinned",
        "是 / Yes": "Yes",
        "否 / No": "No",
        "发布状态 / Publish status": "Publish status",
        "批次编号 / Batch number": "Batch number",
        "操作者 / Operator": "Operator",
        "应用高级筛选 / Apply advanced filters": "Apply advanced filters",
        "文章 / Article": "Article",
        "目标 / Target": "Target",
        "生效区间 / Schedule": "Schedule",
        "发布状态 / Publish": "Publish status",
        "立即 / Now": "Now",
        "长期 / Open ended": "Open ended",
        "未创建 / Not started": "Not started",
        "没有符合条件的投放 / No placements found.": "No placements found.",
        "批量操作 / Batch action": "Batch action",
        "停用 / Deactivate": "Deactivate",
        "重新启用 / Reactivate": "Reactivate",
        "修改时间 / Update schedule": "Update schedule",
        "置顶 / Pin": "Pin",
        "取消置顶 / Unpin": "Unpin",
        "移动版位 / Move": "Move to another slot",
        "复制到子期刊 / Copy": "Copy to journal",
        "取消未来投放 / Cancel future": "Cancel future placement",
        "重新发布 / Republish": "Republish",
        "原因 / Reason": "Reason",
        "复核操作 / Review action": "Review action",
    }
)

# The task-oriented placement workspace predates locale-aware templates and
# intentionally included bilingual copy.  Translate complete phrases before
# the token catalogue runs so English pages do not degrade into mixed strings
# such as ``Placement总览`` or duplicate ``... / Placement overview`` labels.
ADMIN_ENGLISH_LABELS.update(
    {
        "文章投放工作台 / Editorial placement": "Editorial placement",
        "投放总览 / Placement overview": "Placement overview",
        "投放总览": "Placement overview",
        "从已审核文章开始，选择目标子期刊和受控版位，完成预检查后再执行投放。": (
            "Start with an approved article, choose a target journal and "
            "controlled slot, then complete preflight checks before placement."
        ),
        "投放导航 / Placement navigation": "Placement navigation",
        "总览": "Overview",
        "单篇投放": "Single placement",
        "子期刊编排": "Journal curation",
        "批量投放": "Bulk placement",
        "投放清单": "Placement list",
        "投放批次": "Placement batches",
        "查看投放状态、容量风险和最近批次。主编辑投放后自动进入静态发布；副编辑只能维护并重新发布已有文章。": (
            "Review placement status, capacity risks, and recent batches. "
            "Chief-editor placements enter static publishing automatically; "
            "associate editors can maintain and republish existing articles."
        ),
        "新建单篇投放": "New single placement",
        "当前生效 / Active": "Active",
        "即将生效 / Future": "Scheduled",
        "两周内到期 / Expiring": "Expiring within two weeks",
        "容量异常 / Capacity": "Capacity issues",
        "草稿批次 / Drafts": "Draft batches",
        "发布失败 / Failures": "Publishing failures",
        "需要处理 / Needs attention": "Needs attention",
        "需要处理": "Needs attention",
        "选择投放目标": "Select placement target",
        "设置展示与时间": "Set display and schedule",
        "选择文章": "Select articles",
        "预检查与确认": "Preflight review",
        "先处理以下问题，再执行正式静态发布。": (
            "Resolve these issues before starting formal static publishing."
        ),
        "静态发布失败 / Static publish failures": "Static publish failures",
        "已引用文章已停用 / Referenced articles are not live": (
            "Referenced articles are not live"
        ),
        "所属子期刊已停用 / Source journals are inactive": (
            "Source journals are inactive"
        ),
        "图片 Alt 缺失 / Missing image Alt": "Missing image alternative text",
        "即将失效 / Expiring soon": "Expiring soon",
        "长期未处理草稿 / Stale drafts": "Stale drafts",
        "最近批次 / Recent batches": "Recent batches",
        "最近批次": "Recent batches",
        "批次保留完整的选择、校验和执行记录，可从详情页继续处理草稿。": (
            "Each batch retains its selection, validation, and execution "
            "history; continue a draft from its details page."
        ),
        "查看全部批次": "View all batches",
        "暂时没有投放批次": "No placement batches yet",
        "从新建单篇投放开始。": "Start with a new single placement.",
        "可从投放清单维护已发布文章。": (
            "Maintain published articles from the placement list."
        ),
        "批次": "Batch",
        "操作": "Actions",
        "静态发布": "Static publishing",
        "未选择": "Not selected",
        "项": "items",
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


def _translate_english_surface_text(
    content: str, *, unknown_fallback: str | None = None
) -> str:
    translated = content
    for source, target in _ADMIN_FULL_TEXT_REPLACEMENTS:
        translated = translated.replace(source, target)
    translated = _HAN_RE.sub(
        lambda match: _ADMIN_FULL_TEXT_TRANSLATIONS.get(match.group(0))
        or unknown_fallback
        or match.group(0),
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


def sanitize_english_admin_html(
    content: str, *, unknown_fallback: str | None = None
) -> str:
    """Translate known legacy labels without destroying stored content.

    Custom admin pages predate the language switch and contain a mixture of
    hard-coded Chinese labels and database-backed values. This final response
    guard covers pages which do not yet have a dedicated ``.en.html`` template,
    including form placeholders, validation messages, and inline JavaScript.
    Unknown fragments are preserved because they may be article titles, names,
    error details, or other values that operators must be able to distinguish.
    """
    if not is_english_admin():
        return content
    return _translate_english_surface_text(content, unknown_fallback=unknown_fallback)


def admin_english(value):
    if value is None:
        return value
    text = str(value)
    return english_admin_text(text) if is_english_admin() else text


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
