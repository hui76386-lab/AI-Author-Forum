# 成员 C 文章模块最终交付清单

## 1. 交付范围

成员 C 负责 `articles` 应用，用于管理文章内容、审核状态、Wagtail 工作流审核、后台审核页面、静态生成查询接口、预览能力与基础测试。

主要交付文件：

- `project_name/articles/apps.py`
- `project_name/articles/__init__.py`
- `project_name/articles/models.py`
- `project_name/articles/blocks.py`
- `project_name/articles/forms.py`
- `project_name/articles/integrations.py`
- `project_name/articles/services.py`
- `project_name/articles/views.py`
- `project_name/articles/wagtail_hooks.py`
- `project_name/articles/panels.py`
- `project_name/articles/admin.py`
- `project_name/articles/management/commands/list_article_revisions.py`
- `project_name/articles/tests/test_articles.py`
- `templates/articles/article_detail.html`
- `templates/wagtailadmin/articles/list.html`
- `templates/wagtailadmin/articles/review_detail.html`
- `templates/wagtailadmin/articles/preview_button_panel.html`

应用注册：

- `project_name/settings/base.py` 已加入 `{{ project_name }}.articles.apps.ArticlesConfig`。
- `project_name/articles/__init__.py` 保留 `default_app_config = "{{ project_name }}.articles.apps.ArticlesConfig"`，兼容旧式加载。
- `ArticlesConfig.ready()` 显式导入 `wagtail_hooks`，确保后台菜单、权限、工作流 hook 注册。

## 2. 新增模型、字段与方法

### ArticlePage

位置：`project_name/articles/models.py`

`ArticlePage` 继承自 Wagtail `Page`，用于承载文章详情页内容。

基础字段：

- `abstract`：`TextField`，文章摘要，必填。
- `body`：`StreamField(ArticleBodyBlock(), min_num=1)`，正文，至少一块内容。
- `authors`：`CharField(max_length=255)`，作者，当前用逗号分隔。
- `ai_co_authors`：`CharField(max_length=255, blank=True)`，AI 合著人，可选。
- `ai_contribution_statement`：`TextField(blank=True)`，AI 参与说明，可选。
- `responsibility_statement`：`TextField(blank=True)`，责任说明，可选。
- `article_type`：`CharField(max_length=32, choices=ArticleType.choices)`。
- `primary_journal`：`ForeignKey("journals.Journal", on_delete=PROTECT)`，主属子期刊。
- `related_journals`：`ManyToManyField("journals.Journal", blank=True)`，关联多个子期刊。
- `keywords`：`CharField(max_length=255)`，关键词，当前用逗号分隔。
- `review_status`：`CharField(max_length=16, choices=ReviewStatus.choices)`。
- `static_slug`：`CharField(max_length=255, blank=True, unique=True, db_index=True)`，静态 HTML 输出路径片段。
- `approved_version`：`ForeignKey("wagtailcore.Revision", null=True, blank=True, editable=False)`，记录审核通过时对应版本。
- `rejected_version`：`ForeignKey("wagtailcore.Revision", null=True, blank=True, editable=False)`，记录审核驳回时对应版本。

文章类型枚举：

- `AI Article`
- `News`
- `Opinion`
- `Research Analysis`

审核状态枚举：

- `draft`
- `submitted`
- `approved`
- `rejected`
- `published`

Wagtail 面板：

- `content_panels` 组织文章内容、AI 披露、期刊归属、只读审核状态、预览按钮。
- `promote_panels` 保留 Wagtail SEO 字段，并增加 `static_slug` 静态发布配置。

核心方法：

- `get_absolute_url()`：返回 `/articles/{static_slug}/`。
- `save()`：当 `static_slug` 为空时基于标题自动生成唯一 slug。
- `get_context()`：注入 `article`、`static_url`、状态 label、关联期刊、成员 A `SiteSettings` fallback SEO 信息。
- `serve_preview(request, mode_name)`：使用 `templates/articles/article_detail.html` 渲染预览。
- `get_preview_context()`：预览时传入文章实例和空版位数据，如 `placements`、`related_articles`、`recommended_articles`。
- `submit_for_review(user, comment)`：状态更新为 `submitted`，生成审核记录和审计日志。
- `approve(user, comment)`：状态更新为 `approved`，保存 `approved_version`，生成审核记录和审计日志。
- `reject(user, comment)`：状态更新为 `rejected`，保存 `rejected_version`，生成审核记录和审计日志。
- `save_revision()`：沿用 Wagtail 版本记录，并加入文章编辑权限检查。

自定义权限：

- `articles.edit_article`
- `articles.review_article`
- `articles.trigger_article_placement`

### ArticleReviewRecord

位置：`project_name/articles/models.py`

非 Page 模型，用于记录每次审核动作。

字段：

- `article`：`ForeignKey(ArticlePage, related_name="review_records")`
- `reviewer`：`ForeignKey(settings.AUTH_USER_MODEL, related_name="article_review_records")`
- `action`：`CharField(max_length=16, choices=Action.choices)`，支持 `submitted`、`approved`、`rejected`
- `comment`：`TextField()`，审核意见
- `created_at`：`DateTimeField(auto_now_add=True)`

排序：

- 默认按 `created_at` 倒序。

### ArticleReviewTask

位置：`project_name/articles/models.py`

继承 `wagtail.models.AbstractGroupApprovalTask`，用于 Wagtail 内置 Workflow。

行为：

- 只允许拥有 `articles.review_article` 且属于任务组的用户执行审核动作。
- 审核者默认不可进入编辑器修改文章内容。
- 未授权用户不能 lock 或处理 task state。

## 3. StreamField 块定义

位置：`project_name/articles/blocks.py`

### ParagraphBlock

- 继承 `wagtail.blocks.RichTextBlock`
- 支持 `h2`、`h3`、`bold`、`italic`、`link`、`ol`、`ul`
- 用于正文段落

### ImageBlock

- 继承 `wagtail.blocks.StructBlock`
- 字段：
  - `image`：`ImageChooserBlock`
  - `caption`：`CharBlock(required=False, max_length=255)`

### QuoteBlock

- 继承 `wagtail.blocks.StructBlock`
- 字段：
  - `quote`：`TextBlock`
  - `attribution`：`CharBlock(required=False, max_length=255)`

### ListBlock

- 继承 `wagtail.blocks.StructBlock`
- 字段：
  - `list_type`：`ChoiceBlock`，支持 `unordered` 和 `ordered`
  - `items`：`blocks.ListBlock(blocks.RichTextBlock(...), min_num=1)`

### ArticleBodyBlock

- 继承 `wagtail.blocks.StreamBlock`
- 包含：
  - `paragraph`
  - `image`
  - `quote`
  - `list`

## 4. 成员 A/B 集成

位置：`project_name/articles/integrations.py`

### SiteSettings fallback

`get_article_fallback_context(article, request=None)` 会动态查找名为 `SiteSettings` 的模型，并为文章详情页和静态生成上下文提供：

- `site_settings`
- `fallback_image`
- `seo.title`
- `seo.description`
- `seo.image`
- `seo.canonical_url`

兼容字段名包括：

- 默认图片：`default_image`、`fallback_image`、`default_seo_image`、`seo_default_image`、`default_social_image`、`default_og_image`、`og_image`
- 默认描述：`default_search_description`、`default_meta_description`、`default_seo_description`、`seo_description`、`site_description`、`description`
- 标题后缀：`default_title_suffix`、`seo_title_suffix`、`title_suffix`

`templates/articles/article_detail.html` 已读取 `seo` fallback 渲染 `<title>`、`meta description` 与 `og:image`。

### get_active_journals()

`ArticlePageForm` 会通过成员 B 的 `get_active_journals()` 限制期刊字段选择项。

尝试导入路径：

- `journals.services.get_active_journals`
- `{{ project_name }}.journals.services.get_active_journals`
- `project_name.journals.services.get_active_journals`

如果成员 B 服务暂不存在，则 fallback 为：

- 如果 `Journal` 有 `status` 字段，过滤 `status="enabled"`。
- 如果有 `enabled` 字段，过滤 `enabled=True`。
- 如果有 `is_enabled` 字段，过滤 `is_enabled=True`。
- 若都不存在，则返回全部期刊，避免后台不可用。

编辑已有文章时，当前已选择的期刊会并入 queryset，即使后续被禁用，也不会导致旧文章编辑表单无法校验。

### AuditLog

提交审核、通过、驳回时会调用成员 A 的 `AuditLog`。

接入方式：

- 动态查找类名为 `AuditLog` 的模型。
- 优先调用模型或 manager 上的 `log()`、`record()`、`create_entry()`。
- 自动按函数签名过滤参数，兼容较窄的日志 API。
- 如果没有日志方法，则尝试根据常见字段名使用 `AuditLog.objects.create(...)`。
- 如果成员 A 模型尚未提供，则静默跳过，不影响审核主流程。

关键动作已接入：

- `ArticlePage.submit_for_review()`
- `ArticlePage.approve()`
- `ArticlePage.reject()`
- Wagtail `workflow_submitted`
- Wagtail `workflow_approved`
- Wagtail `workflow_rejected`

## 5. Wagtail Workflow 与权限

位置：`project_name/articles/wagtail_hooks.py`

自动创建的 Workflow：

- 名称：`Article Moderation`
- 任务名称：`Article Review`
- 任务类型：`ArticleReviewTask`

自动创建的用户组：

- `Content Editors`
- `Content Reviewers`
- `Content Publishers`

权限策略：

- `Content Editors`：拥有 `articles.edit_article` 和 Wagtail admin access，可创建/编辑文章，不具备审核权限。
- `Content Reviewers`：拥有 `articles.review_article` 和 Wagtail admin access，不具备文章编辑权限。
- `Content Publishers`：拥有 `articles.trigger_article_placement` 和 Wagtail admin access，不具备审核权限。

Workflow 信号折中方案：

- 使用 Wagtail 内置 Workflow 执行审核。
- 通过 `workflow_submitted`、`workflow_approved`、`workflow_rejected` 信号同步 `ArticlePage.review_status`。
- 通过/驳回时同步保存 `approved_version` 或 `rejected_version`。
- 同步创建 `ArticleReviewRecord` 和 `AuditLog`。

## 6. 管理后台页面

截图当前未生成，原因是本地项目仍保留 `{{ project_name }}` 模板占位符，`manage.py` 无法加载 Django settings，后台服务暂不能启动。以下为功能描述，待项目名替换并补齐 `journals.Journal` 后可按第 10 节命令启动并截图。

### 所有文章

入口：

- Wagtail 后台菜单：`所有文章`
- URL namespace：`article_admin:index`
- 路径：`/admin/articles/`

功能：

- 展示所有 `ArticlePage`。
- 支持按审核状态、文章类型、主属期刊、作者关键词筛选。
- 列表字段包括：
  - 标题
  - 状态
  - 类型
  - 主属期刊
  - 作者
  - 创建时间
  - 上次修改时间
- 点击标题进入审核详情页。

### 待审核

入口：

- Wagtail 后台菜单：`待审核`
- URL namespace：`article_admin:pending`
- 路径：`/admin/articles/pending/`

功能：

- 只展示 `review_status="submitted"` 的文章。
- 复用文章列表筛选能力，但固定待审核状态。
- 用于审核人员快速进入待处理文章。

### 审核详情

入口：

- 从 `所有文章` 或 `待审核` 点击文章标题进入。
- URL namespace：`article_admin:review_detail`
- 路径：`/admin/articles/<page_id>/review/`

功能：

- 顶部展示文章状态、类型、主属期刊。
- 通过 iframe 只读展示前台文章详情页预览。
- 提供“打开预览”按钮，使用前台模板和真实 CSS/JS。
- 若当前用户有编辑权限，展示“编辑页面”入口。
- 展示当前版本、批准版本、驳回版本。
- 展示审核记录时间线，包括动作、审核人、时间、意见。
- 对具备审核权限的用户展示通过/驳回按钮和审核意见输入框。
- 展示当前版本与上一版本的字段级差异。

## 7. 预览功能

文章编辑页：

- `ArticlePage.content_panels` 中加入 `PreviewButton`。
- 按钮指向 Wagtail 默认预览 URL：`/admin/pages/{page_id}/preview/`。

预览渲染：

- `ArticlePage.serve_preview(request, mode_name)` 使用 `templates/articles/article_detail.html`。
- 预览上下文包含：
  - `article`
  - `page`
  - `placements=[]`
  - `related_articles=[]`
  - `recommended_articles=[]`
  - `preview_mode`
  - `SiteSettings` fallback SEO 数据

说明：

- 预览使用前台模板、前台 CSS/JS。
- 当前不预览主站版位，只展示文章详情页内容。

## 8. 服务函数与使用示例

位置：`project_name/articles/services.py`

### get_approved_articles()

返回 `review_status` 为 `approved` 或 `published` 的文章，不返回草稿、待审核或驳回文章。

排序：

- 按文章创建时间倒序。
- 创建时间优先取首个 revision 时间，其次取 `first_published_at`，再次取 `latest_revision_created_at`。

示例：

```python
from project_name.articles.services import get_approved_articles

articles = get_approved_articles()

for article in articles[:10]:
    print(article.title, article.get_absolute_url())
```

成员 D 版位投放示例：

```python
from project_name.articles.services import get_approved_articles

homepage_candidates = (
    get_approved_articles()
    .filter(article_type="News")
    .select_related("primary_journal")
)[:5]
```

### get_article_context(slug)

按 `static_slug` 获取已审核文章，并返回静态生成可直接使用的上下文字典。

返回数据包括：

- `article`
- `page`
- `static_url`
- `primary_journal`
- `related_journals`
- `journals`
- `authors`
- `authors_text`
- `keywords`
- `keywords_text`
- `article_type`
- `article_type_label`
- `review_status`
- `review_status_label`
- `ai`
- `site_settings`
- `fallback_image`
- `seo`

示例：

```python
from django.template.loader import render_to_string

from project_name.articles.services import get_article_context

context = get_article_context("ai-governance-report")
html = render_to_string("articles/article_detail.html", context)
```

成员 E 静态生成示例：

```python
from pathlib import Path
from django.template.loader import render_to_string

from project_name.articles.services import get_article_context

slug = "ai-governance-report"
context = get_article_context(slug)
html = render_to_string("articles/article_detail.html", context)

output_path = Path("dist") / "articles" / slug / "index.html"
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(html, encoding="utf-8")
```

### get_articles_by_journal(journal_slug)

返回某个期刊下所有已审核文章，匹配 `primary_journal__slug` 或 `related_journals__slug`。

示例：

```python
from project_name.articles.services import get_articles_by_journal

articles = get_articles_by_journal("ai-journal")
```

## 9. 版本记录与差异

Wagtail Page 默认启用 revisions，`ArticlePage` 沿用该机制。

增强内容：

- 审核通过时保存当前版本为 `approved_version`。
- 审核驳回时保存当前版本为 `rejected_version`。
- 审核详情页显示当前版本与上一版本的字段级差异。
- 管理命令 `list_article_revisions` 可查看指定文章全部版本。

管理命令示例：

```bash
python manage.py list_article_revisions 123
python manage.py list_article_revisions 123 --show-body
```

差异字段：

- `title`
- `abstract`
- `authors`
- `ai_co_authors`
- `ai_contribution_statement`
- `responsibility_statement`
- `article_type`
- `primary_journal_id`
- `keywords`
- `body`

## 10. 本地启动、预览与测试说明

### 启动前置条件

当前仓库仍处于 Wagtail 模板项目状态，多个位置使用 `{{ project_name }}` 占位符。启动前需要先完成：

- 将 `{{ project_name }}` 替换为实际 Python 包名。
- 确认 `DJANGO_SETTINGS_MODULE` 指向真实 settings 模块。
- 补齐成员 B 的 `journals` app 和 `journals.Journal` 模型。
- 执行 migrations。
- 安装测试覆盖率工具时，需安装 `coverage` 或 `pytest-cov`。

### 启动后台服务

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

后台入口：

```text
http://127.0.0.1:8000/admin/
```

文章预览入口：

```text
/admin/pages/{page_id}/preview/
```

审核详情预览入口：

```text
/admin/articles/{page_id}/review/
```

### 本地测试

Django TestCase：

```bash
python manage.py test project_name.articles
```

覆盖率：

```bash
python -m coverage run manage.py test project_name.articles
python -m coverage report
```

语法验证：

```bash
python -m compileall project_name\articles
```

## 11. 测试覆盖率报告

已编写测试文件：

- `project_name/articles/tests/test_articles.py`

覆盖场景：

- 创建文章草稿，保存成功。
- 提交审核后状态变为 `submitted`，生成 `ArticleReviewRecord`。
- 审核通过后状态变为 `approved`，记录 reviewer，并保存 `approved_version`。
- 审核驳回后状态变为 `rejected`，记录 reviewer，并允许重新提交。
- 预览页面可渲染且不报错。
- `get_approved_articles()` 只返回 `approved` 和 `published` 文章。
- 编辑者无法审核。
- 审核者无法编辑。

本次实际执行结果：

```text
python -m compileall project_name\articles
结果：通过
```

```text
python -m coverage --version
结果：失败，当前环境未安装 coverage
错误：No module named coverage
```

```text
python manage.py test project_name.articles
结果：失败，当前模板项目无法加载 settings
错误：ModuleNotFoundError: No module named '{{ project_name }}'
```

覆盖率百分比：

- 当前未产出有效 coverage 百分比。
- 阻塞原因不是测试用例缺失，而是项目仍未完成模板变量替换，且 `journals.Journal` 依赖尚未落地。
- `test_articles.py` 中对 `journals.Journal` 做了 `SkipTest` 防护；成员 B 合入后可正常执行。

建议验收命令：

```bash
python -m coverage run manage.py test project_name.articles
python -m coverage report --include="project_name/articles/*"
```

## 12. 遗留问题与已知限制

- 当前项目仍包含 `{{ project_name }}` 模板占位符，完整 Django 启动、测试、截图都依赖成员 A 完成项目名固化。
- `journals.Journal` 当前仓库未提供，`primary_journal` 和 `related_journals` 依赖成员 B 合入后才能完整迁移和测试。
- 尚未生成 `articles` 的数据库迁移文件；模型稳定后需要执行 `python manage.py makemigrations articles`。
- `SiteSettings` 和 `AuditLog` 当前仓库未提供，集成层采用动态查找与安全降级。成员 A 模型字段确定后，可将兼容逻辑收紧为明确接口。
- StreamField diff 当前是简化处理：将 `body.raw_data` JSON 化后做 `difflib.unified_diff`，不是富文本 HTML 级视觉 diff。
- 审核详情页中的文章预览通过 iframe 加载前台模板，当前未接入主站版位投放数据。
- `authors`、`ai_co_authors`、`keywords` 当前使用逗号分隔字符串，后续可升级为 Snippet、Contributor 模型或 taggit。
- `Content Reviewers` 默认不授予编辑权限，但仍有 Wagtail 内置权限交叉影响的可能，需要在成员 A 权限框架最终稳定后做一次端到端权限验收。
- `ArticlePage.save()` 的权限检查只在传入 `user` 时强约束；Django 内部无 user 的保存路径会放行，以避免系统任务、迁移和 Wagtail 内部流程被误拦截。
