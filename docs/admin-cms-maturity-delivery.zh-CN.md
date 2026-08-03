# AI Author Forum CMS 后台成熟度补齐交付说明

> 依据：`docs/admin-cms-maturity-development-tasks.zh-CN.md`
> 收口日期：2026-07-24
> 范围：任务书定义的 P0/P1，不扩展真实搜索、多语言、自由拖拽布局或运行时文章查询。

## 1. 交付结论

本轮已完成 ADM-001、PERM-001、ART-001～ART-005、JOU-001、DATA-001、CAT-001、REV-001、PLC-001、PUB-001、PUB-002、DASH-001 的后台成熟度补齐，并保持以下架构不变量：

- 文章审核与前台投放分离，审核通过不会直接成为前台内容。
- 子期刊仍由单一 `Journal` 配置模型承载，没有复制页面树或模板。
- 投放只允许在受控版位内排序、置顶和配置生效时间，不提供自由拖拽模板。
- 前台交付继续依赖固定 HTML、manifest 和静态发布版本，不增加文章数据库运行时查询。
- Search 仍是静态推荐入口，不建设实时搜索系统。
- 导入、批量业务操作、投放配置、发布、重试和回滚均接入权限与审计边界。

## 2. 任务对应实现

| 任务 | 交付内容 | 主要实现位置 |
|---|---|---|
| ADM-001 | 合并重复后台入口；统一中文菜单、状态、按钮和模型显示名称；静态发布只保留一个入口 | `ai_author_forum/*/wagtail_hooks.py`、`templates/static_publish/`、`templates/wagtailadmin/` |
| PERM-001 | 建立菜单、页面、按钮、POST/服务层三层权限检查；角色种子可重复执行 | `ai_author_forum/site_settings/permissions.py`、`ai_author_forum/site_settings/management/commands/seed_roles.py` |
| ART-001 | 增加文章封面图和 alt；统一文章详情及卡片图片 fallback；图片引用采用保护/引用检查 | `ai_author_forum/articles/models.py`、`display.py`、`image_references.py`、`templatetags/article_images.py`、`ai_author_forum/images/references.py` |
| ART-002 | 文章列表支持组合搜索、状态/类型/期刊筛选、排序、分页和权限感知操作列 | `ai_author_forum/articles/admin_filters.py`、`admin_services.py`、`views.py`、`templates/wagtailadmin/articles/list.html` |
| ART-003 | 受控批量操作，单次最多 100 篇；逐项校验并返回成功/失败明细 | `ai_author_forum/articles/bulk_services.py`、`views.py`、`templates/wagtailadmin/articles/list.html` |
| ART-004 | 使用 Wagtail TabbedInterface 重组内容、归属、审核、SEO/静态发布信息；Raw HTML 独立权限 | `ai_author_forum/articles/models.py`、`forms.py`、`editor_services.py` |
| ART-005 | 离开提醒、本地草稿恢复、多标签编辑冲突提示和安全自动保存保护 | `ai_author_forum/articles/static/articles/js/editor-protection.js`、`css/admin-maturity.css` |
| REV-001 | 审核队列、审核详情、版本差异、审核意见和 revision 并发保护 | `ai_author_forum/articles/models.py`、`services.py`、`templates/wagtailadmin/articles/review_*.html` |
| JOU-001 | 子期刊搜索、状态/A-Z 筛选、排序、25/50/100 分页、静态主页状态和权限操作 | `ai_author_forum/journals/viewsets.py`、`templates/wagtailadmin/journals/index.html` |
| DATA-001 | UTF-8/乱码检测、阻断可疑导入、存量审计 CSV、可信映射修复及审计日志 | `ai_author_forum/journals/validators.py`、`importers.py`、`management/commands/audit_imported_text.py`、`repair_imported_text.py` |
| CAT-001 | 左树右详情栏目工作台，支持查看、移动、排序、归档、状态变更与影响审计 | `ai_author_forum/journals/category_admin.py`、`category_services.py`、`templates/wagtailadmin/journals/categories.html` |
| PLC-001 | 三栏投放工作台；容量、重复、时间冲突校验；受控排序、预览、停用和审计 | `ai_author_forum/placements/forms.py`、`services.py`、`viewsets.py`、`static/placements/placements-workbench.js` |
| PUB-001 | 唯一中文静态发布中心；任务筛选、发布概览、权限和审计 | `ai_author_forum/static_publish/views.py`、`wagtail_hooks.py`、`templates/static_publish/center.html` |
| PUB-002 | JSON 进度、3 秒轮询、逐页结果、失败目标重试、manifest diff、回滚原因必填 | `ai_author_forum/static_publish/models.py`、`services.py`、`tasks.py`、`static/static_publish/static-publish-progress.js`、`templates/static_publish/job_detail.html`、`rollback_confirm.html` |
| DASH-001 | 按角色和权限显示待办、快捷入口、发布状态和审计摘要 | `ai_author_forum/site_settings/dashboard.py`、`templates/wagtailadmin/home/role_dashboard.html` |

## 3. 权限基线

本轮新增或收口的业务权限包括：

- 文章：`articles.edit_article`、`articles.review_article`、`articles.trigger_article_placement`、`articles.use_raw_html`。
- 静态发布：`static_publish.publish_static_site`、`static_publish.retry_category_publish`、`static_publish.rollback_category_publish`。
- 模块访问：`site_settings.access_articles`、`access_article_review`、`access_journals`、`access_placements`、`access_slots`、`access_static_publish`、`access_audit_log`。
- 权限 helper：`can_edit_article`、`can_review_article`、`can_manage_placement`、`can_publish_static`、`can_retry_publish`、`can_rollback_publish` 及对应只读能力。

`seed_roles` 按项目总负责人、超级管理员、内容管理员、审核人员、站点运营、发布管理员、只读人员建立幂等权限预设。超级用户/项目总负责人继续拥有最高权限；普通业务角色无法绕过 Wagtail 后台访问权限、模型权限或服务层校验。

## 4. 审计覆盖

高风险动作通过 `site_settings.AuditLog` 记录操作者、动作、目标、状态、说明和元数据。覆盖范围：

- 子期刊包导入、可疑文本拦截和可信映射修复：`IMPORT`。
- 文章批量修改和审核相关业务动作：`CONFIGURE`，并保留逐项结果。
- 栏目移动、排序、归档、状态变更：`CONFIGURE`。
- 投放创建、修改、停用、排序：`CONFIGURE`。
- 静态发布：`PUBLISH`。
- 失败目标重试：`RETRY`。
- 版本回滚：`ROLLBACK`，且要求至少五个字符的回滚原因。
- 权限预设和角色调整：`PERMISSION`。

## 5. 数据迁移与备份

开发前已保留数据库备份：

- `E:\AI Author Forum\news-template\backups\db-pre-admin-maturity-20260724-162006.sqlite3`
- `E:\AI Author Forum\news-template\backups\db-pre-plc-pub-20260724-162213.sqlite3`
- `E:\AI Author Forum\news-template\db.sqlite3.art-cms-backup-20260724-162324`

本轮新增并已在当前开发数据库应用：

- `ai_author_forum/articles/migrations/0008_alter_articlecategoryassignment_options_and_more.py`
- `ai_author_forum/static_publish/migrations/0004_staticpublishjob_rollback_reason_and_more.py`
- 当前工作区已有的 `journals.0007_alter_staticarticle_html_source` 也在本次集成迁移时被应用。

迁移验证结果：

- `manage.py check`：通过。
- `manage.py makemigrations --check --dry-run`：`No changes detected`。
- `manage.py migrate`：成功。
- Articles 0001～0008、Static Publish 0001～0004：均已应用。

## 6. 数据治理命令

### 6.1 审计

`python manage.py audit_imported_text`

当前开发库审计生成：

- `E:\AI Author Forum\news-template\output\suspicious-text.csv`
- 共 960 条可疑历史文本记录。

这些记录中的大量“三个问号”占位文本已丢失原字符，无法通过编码猜测可靠恢复。本轮没有启发式覆盖历史数据。

### 6.2 修复

`python manage.py repair_imported_text --mapping <可信映射文件>`

- 默认 dry-run，不写库。
- 只有提供可信原始数据或人工确认映射并显式使用 `--apply` 才会修改数据。
- 写库修复会记录 AuditLog。

## 7. 验证结果

2026-07-24 实际执行结果：

- Ruff：Articles、Journals、Placements、Static Publish、Site Settings、Images 全部通过。
- `git diff --check`：通过；仅有既有 CRLF/LF 转换提示。
- Articles：24 passed。
- Journals：77 passed、2 skipped、16 subtests passed；跳过项要求 PostgreSQL 验证行锁/并发约束。
- Placements：30 passed、14 subtests passed。
- Site Settings/Images/News/Search/Users/Utils/模板与生产配置组合：87 passed、117 subtests passed。
- Static Publish 重点管理端、任务、健康检查和图片引用：34 passed。
- Static Publish capacity：5 passed。
- Static Publish frontend/providers/static server：19 passed、20 subtests passed。
- Static Publish 分类页静态发布：8 passed。
- Static Publish 回滚管理命令：2 passed。
- 前端 `npm run build`：webpack 编译成功。
- 全仓业务代码“三个问号”字面量扫描：仅 DATA-001 测试夹具保留故障样本，业务代码和业务文案无该字面量。

验证边界：

- 全量 pytest 曾运行，但因 Static Publish 的完整静态生成/回滚测试耗时超过 10 分钟而超时，没有将其标记为全量通过。
- Static Publish 的管理端、任务、健康检查、容量、前台/provider/static server、分类静态发布等重点域已分别通过；完整 `test_services.py` 在本机运行超过 10 分钟，仍应在 CI 的长时任务中继续执行。
- Playwright 静态验收：使用本机 Chrome、`STATIC_E2E_PORT=4174` 运行，23 passed；未更新基线截图。

## 8. 上线顺序

1. 冻结内容导入、投放和静态发布写操作，确认数据库与静态输出目录备份可用。
2. 部署代码和前端构建产物。
3. 执行 `python manage.py migrate`。
4. 执行 `python manage.py seed_roles`，复核各角色菜单和按钮矩阵。
5. 执行 `python manage.py check` 和针对性 pytest。
6. 先在非生产静态根目录执行一次全量发布，检查逐页结果、manifest 和 diff。
7. 由发布管理员切换活动版本；抽查首页、文章详情、A-Z、子期刊首页和栏目页固定 HTML。
8. 开放导入和日常运营操作，监控失败任务与 AuditLog。

## 9. 回滚要求

- 代码回滚必须与数据库迁移和静态活动版本分别处理，禁止仅删除当前静态目录。
- 静态回滚必须由具备 `rollback_category_publish` 权限的用户执行，填写明确原因，并保留 manifest 和 AuditLog。
- 数据库回滚前先确认 0008/0004 中新增字段是否已被生产数据使用；如已使用，优先代码前向修复，不直接逆向迁移丢数据。
- DATA-001 修复必须保留映射文件、dry-run 输出和审计记录；无可信源时不得自动覆盖 960 条历史可疑文本。
