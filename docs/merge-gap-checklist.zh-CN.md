# 合并后功能缺口与修复清单

> 项目：AI Author Forum CMS
> 检查日期：2026-07-19
> 检查范围：成员 A、B、C、D、E 合并后的统一代码库
> 当前结论：核心模型和服务已基本合并，但仍存在多个上线阻塞项。

## 1. 检查结论

当前代码已经完成以下基础能力：

- `Journal` 子期刊模型和批量导入服务。
- canonical `ArticlePage` 文章模型。
- 文章审核状态和 Wagtail Workflow 接入。
- canonical `LayoutSlot`、`ArticlePlacement` 投放模型。
- 静态发布任务、manifest、失败重试和版本回滚基础能力。
- `StaticArticle` 到 `ArticlePage` 的兼容迁移。
- 相关测试共 `134 passed, 91 subtests passed`。

但当前仍不能直接判定为完整上线版本，主要原因是：

## 2. P0：上线前必须完成

### 2.1 统一文章正式模板

- [x] 新增正式的 `ArticlePage` 页面模板。
- [x] 修复 `ArticlePage` 默认模板缺失问题。
- [x] 统一预览模板、Wagtail 页面模板和静态输出模板。
- [x] 验证文章正式访问返回 `200`，而不是模板错误。

完成位置：

- 正式统一模板：`templates/articles/article_page.html`
- 兼容入口：`templates/articles/article_detail.html`（继承正式模板）
- 模型入口：`ArticlePage.template` 与 `serve_preview()` 均使用正式统一模板
- 验证：文章正式 HTTP 访问与 Wagtail 预览均返回 `200`

### 2.2 清理模板中的 `{% verbatim %}`

- [x] 移除静态发布中心模板中的 `{% verbatim %}`。
- [x] 移除首页、新闻列表、标准页和组件模板中的 `{% verbatim %}`。
- [x] 验证模板继承、变量、权限判断和 `{% include %}` 正常执行。
- [x] 增加后台模板渲染测试。

重点文件：

- `templates/static_publish/center.html:1`
- `templates/static_publish/job_detail.html:1`
- `templates/pages/home_page.html:1`
- `templates/pages/news_listing_page.html`

完成情况：

- 已清理 `templates/` 下 38 个模板的顶层 `{% verbatim %}` 包装，当前 53 个项目模板全部可以由 Django loader 编译。
- 已增加首页、新闻列表、新闻文章、标准页的真实 HTTP 渲染测试，验证变量、模板继承、导航/页脚和文章卡片组件正常输出。
- 已增加静态发布中心和任务详情的后台渲染测试，覆盖查看、发布、回滚和失败重试权限分支。
- 已将失效的 `wagtailadmin/shared/field_as_li.html` 替换为当前 Wagtail 的 `{% formattedfield %}`，避免发布管理员打开中心时返回 500。

### 2.3 合并两套静态发布链路

- [x] 废弃导入后直接调用 `build_static_journal_site` 的独立发布路径。
- [x] 导入完成后统一创建 `StaticPublishJob`。
- [x] 所有发布统一经过 `StaticPublisher`。
- [x] 统一生成 release、`current`、manifest、失败记录和审计日志。
- [x] 确认导入后的发布任务可以在静态发布中心查看。
- [x] 确保导入发布支持重试和回滚。

完成情况：

- `import_journal_package --publish-static-site` 在导入成功后创建 `scope=FULL` 的中央 `StaticPublishJob`，并调用 `StaticPublisher.build()`；正式导入流程不再调用 `build_static_journal_site`。
- 后台导入会校验 `static_publish.publish_static_site` 权限，并通过 `--operator-id` 将操作者同时写入导入审计和发布审计。
- `JournalImportJob.summary` 与 `ArticleImportJob.summary` 会记录关联发布任务的 ID、状态和版本，失败任务也不会丢失关联。
- release、`current`、manifest、逐页面结果和失败记录统一写入 `STATIC_PUBLISH_ROOT`；导入时不再允许自定义静态输出目录。
- 静态发布中心可查看导入产生的任务及失败详情，并沿用统一的重试和版本回滚能力。
- 已增加命令级集成测试，覆盖导入发布成功、中心可见、失败记录、失败重试和旧版本回滚；同时修复 Windows 绝对包路径写入导入任务文件字段的问题。

相关位置：

- `ai_author_forum/journals/management/commands/import_journal_package.py`
- `ai_author_forum/journals/wagtail_hooks.py`
- `ai_author_forum/journals/publishing.py`
- `ai_author_forum/static_publish/services.py`
- `ai_author_forum/journals/tests/test_import_publish_command.py`
- `docs/static-publishing.md`

### 2.4 补齐静态生成对象

- [x] 生成 `/journals/index.html` A-Z 期刊页。
- [x] 生成 `/journals/{slug}/index.html` 子期刊首页。
- [x] 生成 `/explore-content/{section}/index.html` 栏目页。
- [x] 生成 `/articles/{slug}/index.html` 文章详情页。
- [x] 生成 `/search/index.html` 静态推荐页。
- [x] 生成 `/manifest.json`。
- [x] 让 `StaticPublisher` 的默认 provider 能够发现并发布这些对象。

完成情况：

- 默认 provider 在保留普通 Wagtail 页面发现能力的同时，显式发现活动期刊 A-Z、每个活动子期刊、固定栏目配置、有效投放文章和 Search 静态推荐页。
- canonical 文章目标统一输出到 `/articles/{static_slug}/index.html`，不再按文章在 Wagtail 页面树中的父级路径输出；未投放文章不会被发现或通过正式静态路由访问。
- `StaticPublisher` 仍是唯一发布链路，逐目标渲染成功后由 publisher 自身生成 `manifest.json`，并继续保留 release、current、失败记录、重试、回滚和审计能力。
- 已增加默认 provider 与真实发布集成测试，验证 A-Z、子期刊、栏目、文章、Search 和 manifest 在同一 release 中生成。

相关位置：

- `ai_author_forum/static_publish/providers.py`
- `ai_author_forum/static_publish/frontend.py`
- `ai_author_forum/static_publish/frontend_views.py`
- `ai_author_forum/static_publish/tests/test_frontend.py`

### 2.5 修复未投放文章被静态发布

- [x] 静态文章列表必须要求存在有效 `ArticlePlacement`。
- [x] 检查版位是否启用。
- [x] 检查开始时间和结束时间。
- [x] 检查文章和主期刊状态。
- [x] 防止“审核通过但未投放”的文章出现在首页、期刊页和详情页。
- [x] 让 `get_article_context()` 和 `get_articles_by_journal()` 遵守相同规则。

完成情况：

- `ArticlePlacement.objects.available()` 统一检查投放、版位、时间窗、文章审核/上线状态和主期刊状态。
- `get_approved_articles()`、`get_article_context()`、`get_articles_by_journal()` 均只返回当前存在有效投放的文章。
- 默认静态发布 provider 和兼容静态导出命令共用有效投放规则，未投放文章不会生成详情页或进入首页、期刊页。
- 未投放、未来生效、已经失效、停用投放、停用版位和暂停期刊均已有回归测试。

相关位置：

- `ai_author_forum/placements/models.py`
- `ai_author_forum/articles/services.py`
- `ai_author_forum/static_publish/providers.py`
- `ai_author_forum/journals/management/commands/build_static_journal_site.py`
- `ai_author_forum/articles/tests/test_articles.py`
- `ai_author_forum/static_publish/tests/test_providers.py`
- `ai_author_forum/journals/tests/test_static_site_export.py`

### 2.6 修复子期刊精选文章串站

- [x] 按 `target_slug` 区分不同子期刊的投放。
- [x] 期刊 A 的精选文章不能出现在期刊 B。
- [x] 增加多个期刊同时投放时的集成测试。
- [x] 检查 `starts_at`、`ends_at` 和 `slot.is_active`。

完成情况：

- Journal 投放要求 `target_slug` 与文章主期刊或关联期刊匹配，无效目标不会进入发布查询。
- 静态导出按每个期刊的 `target_slug` 独立分组普通文章和精选文章，不再使用跨期刊共享的文章 ID 集合。
- 多期刊集成测试验证文章只出现在目标子期刊，且不会回流到其主期刊或串到其他期刊。
- 子期刊投放沿用统一时间窗、投放启用状态和版位启用状态检查。

相关位置：

- `ai_author_forum/placements/models.py`
- `ai_author_forum/articles/services.py`
- `ai_author_forum/journals/management/commands/build_static_journal_site.py`
- `ai_author_forum/articles/tests/test_articles.py`
- `ai_author_forum/journals/tests/test_static_site_export.py`

### 2.7 修复发布管理员权限

- [x] 统一使用 `static_publish.publish_static_site` 或统一使用 `site_settings.publish_static_site`。
- [x] 发布管理员可以执行发布。
- [x] 发布管理员可以执行失败重试。
- [x] 发布管理员可以执行回滚。
- [x] 模板中的权限判断与视图权限保持一致。
- [x] 增加发布管理员登录后的权限验收测试。

完成情况：

- 发布、失败重试、回滚和模板控件统一使用 canonical 权限 `static_publish.publish_static_site`。
- `seed_roles` 为发布管理员显式分配 canonical 权限，不再依赖 `site_settings` 下的同名权限。
- 数据迁移将既有组和用户的 legacy 发布/回滚权限迁移到 canonical 权限，并删除重复权限记录。
- 发布中心、失败重试和回滚端点均通过真实发布管理员登录后的权限验收测试。

相关位置：

- `ai_author_forum/static_publish/views.py`
- `templates/static_publish/center.html`
- `templates/static_publish/job_detail.html`
- `ai_author_forum/site_settings/management/commands/seed_roles.py`
- `ai_author_forum/site_settings/migrations/0003_alter_adminrolepreset_options.py`
- `ai_author_forum/site_settings/tests/test_foundation.py`
- `ai_author_forum/static_publish/tests/test_admin.py`

## 3. P1：一期功能需要补齐

### 3.1 完善导入中心

- [x] 提供 Excel 模板下载。
- [x] 增加上传后的预校验页面。
- [x] 增加新增、更新、跳过、失败预览。
- [x] 增加管理员确认导入按钮。
- [x] 增加导入任务状态查询或轮询。
- [x] 增加错误报告下载。
- [x] 导入成功后显示对应静态发布任务。
- [x] 保留逐行错误，不因单行错误导致整批回滚。

相关位置：

- `ai_author_forum/journals/forms.py:7`
- `ai_author_forum/journals/wagtail_hooks.py:28`
- `ai_author_forum/journals/templates/journals/admin/import_dashboard.html`

### 3.2 完善投放管理中心

- [x] 提供审核通过文章筛选。
- [x] 支持主站、栏目、子期刊和 Search 投放目标。
- [x] 支持版位选择。
- [x] 支持排序、置顶、生效时间和失效时间。
- [x] 支持展示标题和摘要覆盖。
- [x] 支持投放预览。
- [x] 支持查看当前生效内容。

完成情况：

- 投放菜单已替换为受控业务工作台，只展示已上线且审核通过的文章，并支持按标题、静态 slug、作者或子期刊筛选。
- 投放目标统一为主站、固定栏目、启用中的子期刊和 Search 静态推荐页；版位必须与目标 scope 匹配，子期刊投放必须符合文章归属。
- 新增和编辑流程支持版位、排序、置顶、生效/失效时间、启用状态以及标题、摘要和图片覆盖，保存动作写入 `AuditLog`。
- 预览复用正式文章卡片且不写入数据库、不触发静态发布；当前生效内容复用 `get_slot_items()` 和正式可用性规则。
- 已增加后台、权限、模型边界和四类投放目标测试。

完成位置：

- `ai_author_forum/placements/forms.py`
- `ai_author_forum/placements/viewsets.py`
- `ai_author_forum/placements/models.py`
- `ai_author_forum/placements/services.py`
- `ai_author_forum/placements/templates/placements/admin/dashboard.html`
- `ai_author_forum/placements/tests/test_admin_dashboard.py`
- `ai_author_forum/placements/tests/test_services.py`

### 3.3 完善版位管理中心

- [x] 支持主站版位管理。
- [x] 支持子期刊版位管理。
- [x] 支持栏目版位管理。
- [x] 支持 `max_items` 配置。
- [x] 支持手动/自动取数模式。
- [x] 支持查看某个版位当前上线内容。
- [x] 支持版位预览。
- [x] 保持受控配置，不提供破坏统一模板的自由拖拽。

完成情况：

- 将 `/admin/layout-slots/` 从说明页升级为统一版位管理中心，按主站、栏目、子期刊和 Search 固定范围查看并选择版位与具体目标。
- 版位编辑只开放后台名称、`max_items`、手动/自动取数模式、运营说明、启停和后台排序；稳定 `code`、固定 `scope` 和模板结构不可修改。
- 移除 `LayoutSlot` 的通用 Snippet 编辑入口，避免绕过受控工作台新增模板结构或修改稳定编码。
- 当前上线内容和版位预览统一复用 `get_slot_items()`、正式生效/审核/期刊隔离规则及 `static_article_card.html`，预览不写库且不触发静态发布。
- 降低 `max_items` 时会检查同一目标的启用投放数量并给出明确冲突提示；保存配置写入 `AuditLog`。
- 内容管理员获得版位访问和修改能力，只读人员获得版位查看入口；模块访问权限与 `change_layoutslot` 写权限保持分离。
- 新增主站、栏目、子期刊管理、受控字段、上线内容、预览、时效过滤、数量冲突、审计和权限测试。

完成位置：

- `ai_author_forum/placements/forms.py`
- `ai_author_forum/placements/viewsets.py`
- `ai_author_forum/placements/models.py`
- `ai_author_forum/placements/templates/placements/admin/slots_dashboard.html`
- `ai_author_forum/placements/tests/test_slot_dashboard.py`
- `ai_author_forum/site_settings/management/commands/seed_roles.py`

### 3.4 实现栏目页

- [x] 增加栏目模型或统一栏目配置。
- [x] 增加栏目版位上下文。
- [x] 增加栏目页模板。
- [x] 增加栏目静态生成。
- [x] 接通 `SECTION` 类型 `ArticlePlacement`。
- [x] 增加栏目页排序和投放测试。

完成情况：

- 使用受控固定配置提供 `ai-article`、`news`、`opinion`、`research-analysis` 四个一期栏目，不新增自由拖拽或重复栏目系统。
- 按文档建立 `section_top_story`、`section_article_list`、`section_sidebar` 默认版位，并通过 `target_type=SECTION`、精确 `target_slug` 获取栏目上下文。
- 新增统一 Nature 风格栏目模板和 `/explore-content/{section}/` 正式渲染入口，默认 provider 将每个栏目加入静态 release。
- 集成测试覆盖置顶优先、排序、跨栏目隔离和静态文件输出。

相关位置：

- `ai_author_forum/static_publish/frontend.py`
- `templates/sections/section_detail.html`
- `ai_author_forum/placements/defaults.py`
- `ai_author_forum/placements/migrations/0004_static_page_targets.py`
- `ai_author_forum/static_publish/tests/test_frontend.py`

### 3.5 实现 Search 静态推荐

- [x] 增加静态推荐配置模型或固定配置。
- [x] 支持推荐关键词。
- [x] 支持推荐文章列表。
- [x] 生成静态 `/search/index.html`。
- [x] 静态页不调用实时搜索接口。
- [x] 保留现有实时搜索作为后续扩展，不参与本期静态发布。

完成情况：

- 使用 `STATIC_SEARCH_RECOMMENDATIONS` 可覆盖的固定配置提供标题、说明、空状态和推荐关键词。
- canonical 投放模型新增 `SEARCH` 目标和 `search_recommended` 受控版位，推荐文章继续遵守审核、有效投放、时间窗、版位和期刊状态规则。
- 正式 `/search/` 路由只渲染静态推荐，不读取查询参数、不调用 Wagtail 数据库搜索；原实时搜索保留在 `/search/live/`，且默认 provider 不发布该入口。
- 测试验证静态 Search 即使收到 `query` 参数也不会调用实时搜索，并验证推荐文章和 `/search/index.html` 输出。

相关位置：

- `ai_author_forum/static_publish/frontend.py`
- `templates/search/static_recommendations.html`
- `ai_author_forum/placements/models.py`
- `ai_author_forum/search/views.py`
- `ai_author_forum/urls.py`
- `ai_author_forum/static_publish/tests/test_frontend.py`

### 3.6 完善文章发布状态同步

- [x] 发布成功后回写文章生成状态。
- [x] 保存文章关联的发布版本。
- [x] 失败时保留失败原因。
- [x] 回滚时同步文章或发布记录状态。
- [x] 明确 `APPROVED`、`PLACED`、`BUILT`、`PUBLISHED`、`OFFLINE` 的状态边界。
- [x] 统一 canonical `ArticlePage` 与旧 `StaticArticle` 的状态迁移规则。

完成情况：

- 将内容审核状态与静态交付状态拆分：`review_status` 只表示审核结论，`publication_status` 统一表示 `APPROVED -> PLACED -> BUILT -> PUBLISHED -> OFFLINE` 静态交付边界。
- `ArticlePage` 新增最近成功构建版本、当前发布版本、失败原因、构建时间和静态发布时间；逐文章渲染成功或失败后立即回写，整版发布成功后再以实际 active manifest 统一校准。
- 回滚不按任务请求范围推测状态，而是读取目标版本 manifest：包含文章正式文件则恢复为 `PUBLISHED` 并关联目标版本，不包含则同步为 `OFFLINE`。
- canonical `ArticlePage` 为唯一状态真源；迁移和首次导入将旧 `StaticArticle` 状态拆分到审核/交付字段，后续只从 canonical 单向兼容回写旧状态和构建版本。
- 有效投放建立时同步为 `PLACED`，静态候选版本渲染成功但尚未激活时为 `BUILT`；已上线文章的失败重建不会错误覆盖当前 `PUBLISHED` 状态。
- 新增发布成功、候选构建、失败原因、投放边界、回滚包含/不包含文章以及旧模型迁移与回写测试。

相关位置：

- `ai_author_forum/articles/models.py`
- `ai_author_forum/articles/publication.py`
- `ai_author_forum/articles/migrations/0003_publication_status_sync.py`
- `ai_author_forum/articles/services.py`
- `ai_author_forum/placements/models.py`
- `ai_author_forum/static_publish/services.py`
- `ai_author_forum/static_publish/tests/test_services.py`
- `docs/static-publishing.md`

### 3.7 统一文章 URL 和静态路径

- [x] 确定唯一生产路径。
- [x] 统一 Wagtail runtime URL。
- [x] 统一静态输出路径。
- [x] 统一文章详情、首页和期刊页中的链接。
- [x] 消除以下路径并存问题：
  - `/provider-article/`
  - `/articles/{slug}/`
  - `/journals/{journal}/articles/{slug}/`

完成情况：

- 唯一生产 URL 统一为 `/articles/{static_slug}/`，唯一静态文件路径统一为 `articles/{static_slug}/index.html`。
- `ArticlePage.get_absolute_url()` 不再读取 Wagtail 页面树 URL；旧页面树入口只执行 301 跳转，不再形成第二份正式文章内容。
- canonical `ArticlePage` 不再接受旧 `StaticArticle.static_output_path` 覆盖，兼容模型、导入服务和数据迁移均归一到文章根目录。
- 首页版位、期刊页、栏目页、Search 推荐和兼容静态导出中的文章链接统一使用 canonical URL，模板不再把 `index.html` 文件路径作为链接。
- 测试覆盖 runtime URL、页面树重定向、静态路径、旧路径覆盖保护、正式模板链接和兼容导出目录。

相关位置：

- `ai_author_forum/articles/models.py`
- `ai_author_forum/journals/models.py`
- `ai_author_forum/journals/services.py`
- `ai_author_forum/journals/migrations/0003_normalize_article_static_paths.py`
- `ai_author_forum/static_publish/providers.py`
- `templates/components/static_article_card.html`
- `templates/components/placements/article-card.html`
- `templates/journals/journal_detail.html`

## 4. P1：素材与质量能力

### 4.1 完善素材引用保护

- [x] 删除图片前检查 `Journal.cover_image` 引用。
- [x] 删除图片前检查 `Journal.metrics_image` 引用。
- [x] 删除图片前检查 `ArticlePlacement.override_image` 引用。
- [x] 删除图片前检查 StreamField 图片引用。
- [x] 删除图片前检查已发布静态页面引用。
- [x] 被引用素材禁止无提示删除。

当前只有静态发布阶段的本地资源存在性检查，不是删除前引用检查：

- `ai_author_forum/static_publish/services.py:294`

### 4.2 增加完整前台验收

- [x] 增加首页访问验收。
- [x] 增加 A-Z 期刊页验收。
- [x] 增加子期刊首页验收。
- [x] 增加文章详情页验收。
- [x] 增加栏目页验收。
- [x] 增加 Search 静态页验收。
- [x] 增加图片和 CSS/JS 引用检查。
- [x] 增加 manifest 页面数量和失败数量检查。
- [x] 增加发布回滚后的页面内容验收。

## 5. P2：后续扩展

- [ ] 更细粒度的子期刊管理员权限。
- [ ] 更复杂的自动推荐规则。
- [ ] 独立域名绑定。
- [ ] 多语言内容。
- [ ] 真实全文搜索。
- [ ] 独立作者、合著人和关键词模型。
- [ ] 富文本 HTML 级版本差异展示。
- [ ] 更完整的 Playwright 截图和发布报告。

## 6. 当前验收结果

- [x] 全量测试：`134 passed, 91 subtests passed`
- [x] Django 测试：`123 tests passed`
- [x] `manage.py makemigrations --check --dry-run` 通过。
- [x] Python 编译检查通过。
- [x] `git diff --check` 通过。
- [x] Playwright 前台验收：`8 passed`。
- [x] `manage.py check` 通过：0 issues（0 silenced）。
- [x] 已达到完整上线验收标准。
- [x] 已补齐并验证统一文章正式模板。
- [x] 已清理全部模板 `{% verbatim %}` 包装，并补齐前后台真实渲染测试。
- [x] 已将导入发布统一接入 `StaticPublishJob` / `StaticPublisher`，并验证中心查看、失败重试和版本回滚。
- [x] 已统一发布管理员权限，并验证真实登录后可执行发布、失败重试和回滚。
- [x] 已统一静态发布有效投放过滤，未审核、未投放或投放失效的文章不会进入静态站点。
- [x] 已按 `target_slug` 隔离子期刊文章和精选内容，并通过多期刊集成测试。

## 7. 推荐修复顺序

1. 补齐 A-Z 页面、栏目页和 Search 静态推荐。
2. 完善导入预览、确认、报告下载和任务状态。
3. 补齐素材引用检查、前台模板验收和 Playwright 测试。

## 8. 完成标准

只有同时满足以下条件，才建议进入上线验收：

- [x] 导入、审核、投放、生成、发布、回滚形成单一闭环。
- [x] 所有正式 Wagtail 页面模板可以真实渲染。
- [x] 未审核或未投放文章不会出现在静态站点。
- [x] A-Z、栏目、子期刊、文章和 Search 静态页面齐全。
- [x] 发布管理员权限可以真实执行发布、重试和回滚。
- [x] 每次发布都有逐页面结果、manifest 和审计日志。
- [x] 素材删除前可以检查引用。
- [x] Playwright 前台验收通过。