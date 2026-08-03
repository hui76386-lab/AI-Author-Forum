# AI Author Forum CMS 项目进度第二次全量审计

> 审计日期：2026 年 7 月 20 日
> 审计仓库：`E:\AI Author Forum\news-template`
> 审计分支：`codex/wagtail-admin-registration`
> 审计基线：`9c61a71 feat: complete member E static publishing delivery`
> 审计性质：在第一次审计及补全开发之后，对 A–E 负责人模块、核心业务闭环、原缺陷与风险、自动化测试、Git、CI 和生产配置进行第二次全量复核。
> 结论优先级：**本文第二次复审结论覆盖本文旧版中的数据、百分比和缺陷状态。**

## 1. 执行摘要

补全开发已解决第一次审计中的大部分核心业务缺口。首页投放、旧文章入口退役、实时搜索移除、数据库迁移、静态发布闭环、演示数据、CI/生产配置等均有实质性进展。

当前项目已经从“核心开发基本完成、P0 集成缺口较多”推进到：

> **核心业务功能基本完成，后端自动化验证充分；当前主要问题已转为 Git 交付收口、视觉基线同步和生产环境实机验收。**

| 评估维度 | 第二次复审完成度 | 结论 |
|---|---:|---|
| 核心功能实现度 | 约 93% | 导入、审核、投放、固定 HTML 发布、失败重试与回滚闭环均已实现 |
| 后端自动化验证度 | 约 95% | 159 个 Python 测试和 93 个 subtests 全部通过 |
| 当前本地演示可用度 | 约 88% | 迁移已应用，已有 Journal、文章、投放和成功发布数据 |
| Git 可交付度 | 约 58% | 分支已与远端同步，但仍有 20 个 tracked 修改和大量生成文件未收口 |
| 生产配置就绪度 | 约 80% | Compose、Nginx、CI、生产环境示例和运维文档已补全 |
| 真实上线就绪度 | 约 70% | Docker/Redis/Celery/PostgreSQL/Nginx 尚未在本机完成联合实机验收 |
| **综合进度** | **约 85%** | 功能层面接近完成，但仍不建议直接正式上线 |

### 1.1 原 7 个 P0 的复核统计

| 状态 | 数量 | 项目 |
|---|---:|---|
| 已完成 | 4 | 首页投放、旧文章入口退役、实时搜索移除、数据库迁移 |
| 部分完成 | 2 | 生产静态目录切流、lint/CI 收口 |
| 未完成 | 1 | Git 工作区交付收口 |

### 1.2 当前三个上线阻塞项

1. **Git 工作区和生成产物未收口**：用户业务改动尚未提交，`published/`、`output/`、截图、日志和 PID 等本地产物未完全忽略。
2. **Playwright 视觉基线与当前 UI 不同步**：功能断言通过，但 6 个正式页面截图比较失败，当前 CI 不能判定为全绿。
3. **生产基础设施缺少实机联合验收**：本机没有 Docker，尚未真实验证 Compose、PostgreSQL、Redis、Celery、Nginx、静态切流和回滚。

## 2. 审计方法和实际执行结果

本次不是只读取代码，而是执行了系统检查、迁移检查、完整 Python 测试、代码质量检查、前端生产构建、Playwright、数据库数据检查、发布记录检查、Git 与部署配置检查。

### 2.1 Django、迁移与 Python 测试

```text
Django 6.0.7
Wagtail 7.4.2

python manage.py check
System check identified no issues

python manage.py makemigrations --check --dry-run
No changes detected

pytest
159 passed, 93 subtests passed in 265.26s
```

结论：Django 系统检查通过；模型与迁移文件一致；当前数据库中的项目迁移已全部应用；第一次审计记录的“6 个迁移未应用”已经修复。

### 2.2 代码质量

按项目当前配置直接执行：

```text
ruff check ai_author_forum tests scripts
All checks passed

black --check ai_author_forum tests scripts
198 files unchanged

isort --check-only ai_author_forum tests scripts
passed
```

但 `pre-commit run --all-files` 仍未通过：

- pre-commit 中的 Ruff 版本或规则与直接执行环境不一致，报告 5 个 `UP038`。
- pre-commit Black 报告 2 个文件需要格式化：
  - `ai_author_forum/journals/management/commands/build_static_journal_site.py`
  - `scripts/build_journal_import_package.py`
- `mixed-line-ending` 会批量改写仓库换行符，当前不适合作为稳定门禁直接运行。

结论：第一次审计中的大规模 lint 问题已基本修复，但 **pre-commit 配置与 CI 工具链版本尚未完全统一**。

### 2.3 前端构建与 Playwright

```text
npm run build:prod
构建成功

npm run test:e2e
2 passed / 6 failed
```

- 构建只有 `nature-home.png` 约 799 KiB 和 Browserslist 数据过期等非阻断警告。
- 失败的 6 项均为当前页面与旧截图基线差异较大。
- 受测页面均返回 HTTP 200，本地静态资源没有 4xx，主要内容与功能断言可见并通过。
- manifest、失败发布不切换 `current`、重试和回滚相关功能断言通过。

因此当前问题是 **视觉基线过期**，不是静态发布功能失效；但在更新和人工审查截图基线前，CI 仍不能视为通过。

### 2.4 npm 安全审计

`npm audit` 未能取得审计结果，原因是当前 npm 镜像的安全审计 API 返回 `NOT_IMPLEMENTED`，并非已经确认“没有漏洞”。CI 应使用支持 audit API 的 registry，或明确调整审计策略。

### 2.5 生产与 Docker 验证

代码中已存在：

- `.github/workflows/ci.yml`
- `docker-compose.production.yml`
- `docker/nginx.conf`
- `.env.production.example`
- `docs/static-publishing-operations.zh-CN.md`

Nginx 已配置普通前台请求直接服务静态发布目录：

```nginx
location / {
    root /srv/published/current;
    try_files $uri $uri/index.html =503;
}
```

后台和健康检查请求转发至 Django，普通前台不依赖运行时文章数据库查询。

本机没有 Docker，执行环境返回 `docker: command not found`。因此只能确认**代码和配置已补全**，不能确认容器网络、PostgreSQL、Redis、Celery、Nginx、卷挂载、健康检查和真实切流在生产组合中已经运行通过。

## 3. 当前数据库与演示状态

当前本地数据库已经不是第一次审计时的空演示库：

```text
Site = 1
Page = 5
Journal = 1
journals.StaticArticle = 3
articles.ArticlePage = 3
LayoutSlot = 14
ArticlePlacement = 5
JournalImportJob = 3
StaticPublishJob = 6
StaticManifest = 6
StaticPublishTarget = 56
AdminRolePreset = 6
Group = 11
User = 13
NavigationItem = 4
AuditLog = 26
```

最新静态发布任务：

```text
job = 6
status = succeeded
files = 2140
bytes = 23560967
pages = 11
failed = 0
```

同时已经新增可重复执行的 Journal 演示数据 seed 命令及测试。第一次审计中的“演示数据库为空、缺少可重复初始化流程”已经修复。

## 4. 各负责人开发程度

| 负责人 | 主要职责 | 功能实现度 | 可交付度 | 第二次复审判断 |
|---|---|---:|---:|---|
| A 康辉 | 总负责、Wagtail 底座、权限菜单、配置、合并、部署 | 92% | 72% | 架构、权限、迁移、菜单、CI/部署配置基本完成；Git、pre-commit 和生产实机未收口 |
| B 尹星皓 | Journal、导入、素材匹配、A–Z | 94% | 86% | 导入闭环、边界校验、120 Journal 和演示 seed 完成；兼容模型策略待文档化 |
| C 王锃毅 | ArticlePage、审核流、文章内容业务 | 95% | 89% | 审核、权限、预览、发布校验及旧模型退役完成；本地后台/UI 改动仍需提交 |
| D 郭乃彪 | LayoutSlot、ArticlePlacement、排序置顶、前台版位 | 93% | 84% | 首页、栏目、期刊和 Search 投放及 AUTO 补位完成；视觉验收和 Git 作者信息待收口 |
| E 倪康杰 | 静态生成、manifest、重试、回滚、Playwright | 94% | 78% | 发布闭环、Nginx、CI、健康检查完成；视觉基线和生产基础设施实机验收未完成 |

### 4.1 负责人 A：康辉

**已完成：**

- 建立并维护 `journals`、`articles`、`placements`、`static_publish`、`site_settings` 等统一应用边界。
- 实现 `SiteSettings`、`NavigationItem`、`AdminRolePreset`、`AuditLog` 等底座模型和能力。
- 建立七类用户组和权限基线，后台菜单覆盖子期刊、文章、审核、投放、版位、静态发布及基础配置。
- 项目总负责人具备全局最高权限，业务模块接入统一权限和审计体系。
- 已应用全部迁移，系统检查和迁移一致性检查通过。
- 已补全 CI、生产 Compose、Nginx 静态入口、生产环境变量示例和运维文档。
- HEAD 已与 `gitee/main` 同步，当前功能补全提交已进入远端基线。

**未完成或风险：**

- 审计开始时（不含本审计文档更新）仍有 20 个 tracked 修改，主要为后台/UI/模板和 E2E 改动，尚未形成可审查提交。
- 有 1,581 个未跟踪文件，其中约 1,574 个为生成或本地文件；`.gitignore` 未充分收口。
- 直接 lint 已通过，但 pre-commit 版本、规则和换行处理不稳定。
- 本地 `main` 比 `gitee/main` 落后 4 个提交，后续切换主线时应先同步。
- 生产配置已完成，但缺少真实 Docker 环境的联合验收证据。

### 4.2 负责人 B：尹星皓

**已完成：**

- `Journal` 使用统一配置模型，支持 120 个子期刊并可扩展至 200 个。
- Excel/ZIP 导入、预览、确认、素材匹配、校验、导入记录和异步发布流程已经实现。
- A–Z、子期刊上下文和统一模板静态输出能力已经建立。
- 120 个 Journal 首页可在单次 release 中生成，并有自动化测试覆盖。
- 已覆盖异常数据、重复 slug、缺失素材、路径穿越、单行失败隔离等边界场景。
- 已新增可重复执行的演示数据 seed 命令和测试。

**未完成或风险：**

- `journals.StaticArticle` 仍作为导入源和兼容桥接存在，需要正式说明与 canonical `articles.ArticlePage` 的同步关系及未来退役条件。
- 生产环境的大批量导入性能、容量、失败恢复和文件存储配置尚未做真实演练。

### 4.3 负责人 C：王锃毅

**已完成：**

- canonical `articles.ArticlePage`、StreamField 内容、AI 字段和后台编辑能力已经实现。
- 已覆盖草稿、提交审核、审批、驳回、审核意见、权限和预览。
- 未审核文章不能进入有效投放和静态发布链路。
- 旧 `news.ArticlePage` 已退役：禁止创建、旧 URL 返回 410 或重定向 canonical、静态 provider 排除旧模型，并有迁移命令和测试。

**未完成或风险：**

- 旧模型仍物理保留用于兼容和数据迁移，需防止未来重新注册为可创建入口。
- 当前工作区中的文章后台列表、审核面板和页面 UI 改动尚未全部提交。

### 4.4 负责人 D：郭乃彪

**已完成：**

- `LayoutSlot`、`ArticlePlacement`、作用域、排序、置顶、生效时间、覆盖标题和覆盖图片已实现。
- 主站首页已经接入 `home_hero`、`home_featured`、`latest_ai_article`。
- 栏目页、子期刊页和静态 Search 推荐均使用受控投放结果。
- `fill_mode=AUTO` 自动补位已有服务实现和自动化测试。
- 已覆盖跨期刊校验、时间范围、排序置顶、作用域和无效投放限制。

**未完成或风险：**

- 当前 UI 重构导致视觉截图大幅变化，需设计确认后更新基线，而不是直接无审查覆盖截图。
- Git 历史中仍存在 `郭乃彪 <userEmail>` 的不规范作者信息。
- 生产规模下的版位查询和批量静态生成性能尚未专项压测。

### 4.5 负责人 E：倪康杰

**已完成：**

- 静态发布已形成 release、staging、`current`、manifest、逐页面结果和文件清单闭环。
- 已实现失败记录、失败不切换 `current`、失败重试、选择性发布和回滚。
- 发布、重试和回滚等高风险动作已接入 `AuditLog`。
- 已实现图片/rendition 输出、manifest 引用检查及静态素材删除保护。
- 已实现发布任务、Celery 任务、健康检查和状态记录。
- 已补全 Nginx、生产 Compose、生产环境示例、CI 和发布运维文档。

**未完成或风险：**

- 6 个视觉截图基线与当前 UI 不一致，导致 E2E/CI 尚未全绿。
- Docker、PostgreSQL、Redis、Celery、Nginx 没有在本次机器上完成联合运行验证。
- 尚未执行生产性能、容量、故障切换和灾备演练。

## 5. 核心业务功能完成度

| 核心能力 | 完成度 | 复核结论 |
|---|---:|---|
| Wagtail 工程底座与模块边界 | 95% | 结构清晰，业务模块没有被塞入 settings 或模板层 |
| 用户组、角色、菜单与权限 | 94% | 七类角色基线和差异化菜单/权限已有测试，实机角色验收仍可补充 |
| SiteSettings、导航和审计 | 94% | 后台可维护，普通角色限制和高风险操作审计已建立 |
| Journal 与 120 子期刊 | 96% | 统一模型、120 首页生成、扩展性和测试符合要求 |
| Excel/ZIP 导入与素材匹配 | 94% | 预览、确认、异常隔离、路径安全和演示 seed 已完成 |
| 文章内容与审核 | 96% | 草稿、提交、审批、驳回、权限、预览和发布约束完整 |
| ArticlePlacement 与版位编排 | 94% | 首页/栏目/期刊/Search、排序置顶、时间和 AUTO 已接通 |
| 固定 HTML 静态发布 | 95% | 页面 provider、release、manifest、原子切换、失败和回滚完整 |
| 图片引用与删除保护 | 93% | manifest、rendition、回滚和后台单删/批删均有测试 |
| 静态 Search 推荐 | 98% | `/search/live/` 已移除，`/search/` 不做实时数据库查询 |
| CI 与代码质量门禁 | 78% | CI 配置已重写，直接 lint 通过；pre-commit、视觉基线和 npm audit 未收口 |
| 生产部署与运维 | 75% | 配置和文档已完成，缺 Docker 生产组合实机验收 |

## 6. 不可破坏业务口径符合度

| 业务技术口径 | 当前状态 | 判断 |
|---|---|---|
| 子期刊使用统一 `Journal`，支持 120 并扩展到 200 | 统一模型和统一模板，已有 120 Journal 测试 | 符合 |
| 文章先审核，再通过 `ArticlePlacement` 投放 | canonical 链路已实现，旧创建入口已封闭 | 符合 |
| 前台固定 Nature 风格，后台不提供自由拖拽 | 使用固定 `LayoutSlot` 和受控字段 | 符合 |
| 首页、文章、A–Z、子期刊最终为固定 HTML | 静态 provider 和 Nginx `current` 配置已完成 | 配置符合，生产实机待验 |
| 本期不建设真实搜索 | `/search/live/` 返回 404，`/search/` 使用静态推荐且测试禁止实时查询 | 符合 |
| 发布有逐页结果、manifest、失败、重试和回滚 | 功能和自动化测试完整 | 符合 |
| 高风险操作写入 `AuditLog` | 发布、导入、重试、回滚均接入审计 | 符合 |
| 已被静态页面引用的素材不能无提示删除 | 引用检查、后台单删/批删保护和测试已建立 | 符合 |

## 7. 第一次审计 P0 缺陷逐项复核

### P0-1 主站首页接通 ArticlePlacement

**状态：已完成。** `home_hero`、`home_featured`、`latest_ai_article` 均已接入，模板、服务、预览、AUTO 补位和静态首页输出有自动化测试。

### P0-2 退役旧 `news.ArticlePage`

**状态：已完成，兼容模型保留。** 禁止新建；静态 provider 排除旧模型；旧 URL 采用 410 或 canonical 重定向；已有迁移命令和测试。物理模型保留用于迁移兼容，不等于仍可绕过审核与投放。

### P0-3 删除真实搜索入口

**状态：已完成。** `/search/live/` 返回 404，旧实时搜索 view 已删除，`/search/` 只使用静态推荐，测试确认不会调用 `Page.objects.live()`。

### P0-4 备份并应用迁移

**状态：已完成。** 所有迁移均已应用；无待生成迁移；系统检查和完整 Python 测试通过。

### P0-5 完成生产静态目录切流

**状态：部分完成。**

已完成 Nginx 静态 Web Root、后台转发、Compose、生产环境示例和运维文档；未完成真实容器、卷、网络、Redis/Celery、数据库和 Nginx 联合验收，也没有生产切流与回滚演练记录。

### P0-6 整理并提交 Git 工作区

**状态：未完成。**

- 当前分支 HEAD 与 `gitee/main` 为 `0 ahead / 0 behind`。
- 审计开始时（不含本审计文档更新）仍有 20 个 tracked 修改。
- 有 17 个顶层未跟踪条目，展开为 1,581 个文件。
- 其中约 1,574 个是发布产物、输出目录、截图、日志、PID 和参考资源等本地/生成文件。
- 仍有 7 个应审查是否纳入版本控制的源文件或文档。

### P0-7 修复 lint 并更新 CI

**状态：部分完成。**

直接 Ruff、Black、isort 已通过；CI 已覆盖 Django check、迁移、pytest、lint、构建、Playwright 和 Compose。pre-commit 仍不一致，Playwright 为 2/8，npm audit 镜像 API 不可用，Docker 步骤未在本机复现。

## 8. P1 与 P2 风险复核

### 8.1 P1：上线稳定性

| 第一次审计风险 | 第二次复审状态 | 说明 |
|---|---|---|
| Journal 异常导入、重复数据和缺失素材测试 | 已完成 | 已覆盖重复 slug、缺素材、路径穿越和单行失败隔离 |
| 可重复演示数据和初始化脚本 | 已完成 | 已有 Journal demo seed 命令和测试 |
| 静态素材引用删除保护验收 | 已完成 | manifest、rendition、回滚、后台单删和批删均有测试 |
| slug 冲突和路径规范化测试 | 已完成 | 已有迁移和路径安全测试 |
| 发布、失败重试、回滚运维文档 | 已完成 | 已新增静态发布运维文档 |
| Playwright 视觉回归基线 | 部分完成 | 基线存在，但与当前 UI 不同步，6 项失败 |
| Redis/Celery 生产部署与恢复 | 未完成 | 仅有代码/配置，未做生产组合实机验证 |

### 8.2 P2：后续整理

| 第一次审计风险 | 第二次复审状态 | 说明 |
|---|---|---|
| 明确 `journals.StaticArticle` 去留 | 部分完成 | 已作为导入源/兼容桥接使用，正式保留策略仍需文档化 |
| 统一 Article 与 Placement 接口 | 基本完成 | canonical 链路和服务层已统一，仍应补接口文档 |
| 清理历史 Search 代码和路由 | 已完成 | 实时 view 和有效路由已移除 |
| 规范成员 Git 作者信息 | 未完成 | 仍存在 `郭乃彪 <userEmail>` |
| 跨模块接口和上线运维文档 | 部分完成 | 运维文档已补，跨模块接口文档仍可加强 |
| 性能、容量和灾备演练 | 未完成 | 尚无生产级演练证据 |

## 9. 当前 Git 交付状态

```text
branch = codex/wagtail-admin-registration
HEAD = 9c61a71
HEAD vs gitee/main = 0 ahead / 0 behind
local main vs gitee/main = behind 4 commits
```

近期补全开发提交：

```text
4131f52 退役旧 news.ArticlePage
8b7edb1 可重复 Journal 演示导入
21b8261 主站首页投放补全
9c61a71 静态发布、CI、Nginx、生产配置补全
```

未跟踪但可能属于源文件的内容：

```text
docs/local-test-accounts.zh-CN.md
static_compiled/css/reference.css
static_compiled/js/reference.js
static_src/javascript/reference.js
static_src/sass/reference.css
templates/wagtailadmin/articles/review_dashboard.html
templates/wagtailadmin/journals/index.html
```

这些文件需要逐个评审后决定提交或删除；其余发布产物、测试截图、日志、PID 等应通过 `.gitignore` 或测试清理流程排除。

## 10. 建议的剩余工作顺序

### P0：形成可验收基线

1. 扩充 `.gitignore`，排除 `published/`、`output/`、截图、PID、日志和本地参考产物。
2. 审查 20 个 tracked 修改与 7 个未跟踪源文件，按后台/UI、模板/样式、E2E/基线拆分提交。
3. 同步本地 `main`，创建明确验收 tag 或提交基线。
4. 对 6 个差异页面进行人工设计审查，确认后更新 Playwright snapshots，目标 8/8 通过。
5. 固定 Ruff、Black、isort、pre-commit 版本和规则，调整 mixed-line-ending。
6. 为 npm audit 使用支持的 registry 或明确替代门禁。

### P0：生产实机验收

7. 在具备 Docker 的 Linux 或 CI Runner 上启动生产 Compose。
8. 验证 PostgreSQL 迁移、Redis、Celery worker、健康检查和重启恢复。
9. 验证 Nginx 直接服务 `published/current`，普通前台请求不进入 Django。
10. 执行成功发布、故意失败、失败不切流、重试和回滚演练，并保存日志、manifest、审计记录和截图。

### P1：上线质量提升

11. 正式记录 `journals.StaticArticle` 的兼容桥接和退役策略。
12. 修复 Git 作者信息并补充跨模块接口文档。
13. 优化首页大图和前端构建警告。
14. 执行 120–200 Journal、文章批量导入和静态发布的性能/容量测试。
15. 制定并演练备份、恢复、回滚和灾备流程。

## 11. 最终结论

### 已确认完成

- A–E 五个负责人模块均有完整、可运行的实质性实现。
- 统一 Journal、文章审核、ArticlePlacement 投放和固定 HTML 发布链路已建立。
- 首页三个槽位已经接通投放和静态生成。
- 旧文章模型不再是可创建、可绕过审核的入口。
- 实时数据库搜索已移除，Search 只做静态推荐。
- 迁移全部应用，演示数据库和可重复 seed 已准备。
- manifest、逐页结果、失败记录、重试、回滚、素材保护和审计均有自动化测试。
- Python 全量测试、直接 Ruff/Black/isort 和前端生产构建通过。
- 生产 Nginx/Compose/CI/运维配置已经补全。

### 尚未完成

- 用户工作区未形成干净、可审查的 Git 交付基线。
- Playwright 视觉基线未与当前 UI 同步。
- pre-commit 和 npm audit 门禁不稳定。
- Docker、PostgreSQL、Redis、Celery、Nginx 的生产组合没有实机验收记录。
- 性能、容量、灾备演练和兼容模型策略文档仍待补充。

> **综合判断：项目当前综合完成度约 85%。**
> **结论：补全开发已经解决第一次审计中的绝大多数功能缺陷，但项目仍处于“功能基本完成、交付与生产验收待收口”阶段。完成 Git、视觉基线和 Docker 生产实机验收后，才建议进入正式上线审批。**
