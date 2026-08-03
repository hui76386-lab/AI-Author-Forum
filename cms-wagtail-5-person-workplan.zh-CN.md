# AI Author Forum CMS 二次开发 5 人分工方案

> 基础依据：`wagtail/news-template`、CMS 需求文档、`cms-wagtail-core-business-design.zh-CN.md`
> 项目口径：管理端基于 Wagtail CMS 二次开发；前台文章详情以固定 HTML 静态页面形式访问，不走前台运行时文章库查询，不建设真实搜索系统。
> 团队目标：5 人并行完成子期刊批量导入、文章审核、文章投放、主站/子期刊版位管理、静态 HTML 发布闭环。

## 1. 总体分工原则

本项目不是从零开发一个 CMS，而是在 `wagtail/news-template` 基础上做二次开发。分工必须围绕 Wagtail 的能力边界来拆：

| 层级 | 主要能力 | 负责人类型 |
|---|---|---|
| Wagtail 基础层 | Page、StreamField、Images、Workflow、权限、后台菜单 | 架构负责人 |
| 子期刊业务层 | 120 子期刊、Excel 导入、A-Z、封面图、统计图表 | 子期刊负责人 |
| 文章业务层 | 文章模型、审核流、AI 合著信息、版本、预览 | 内容审核负责人 |
| 版位编排层 | 主站版位、子期刊版位、文章投放、前台模板接入 | 版位与前台负责人 |
| 静态发布层 | HTML 生成、manifest、发布、回滚、失败重试、验收 | 发布与质量负责人 |

5 人建议按“模块所有权”分，而不是按前后端粗分。每个人负责一个闭环，避免互相等接口。

## 2. 统一技术栈

### 2.1 后端与 CMS

| 技术 | 用途 |
|---|---|
| Python 3.11 / 3.12 | Wagtail/Django 项目运行语言，具体版本跟随 `wagtail/news-template` 锁定版本 |
| Django | 数据模型、后台扩展、权限、任务记录、管理视图 |
| Wagtail CMS | 页面树、内容编辑、图片库、预览、发布、审核流 |
| wagtail/news-template | 项目基础模板，复用新闻站页面、文章、图片和模板结构 |
| Wagtail Page | 主站首页、栏目页、文章页、A-Z 页面等页面类型 |
| Wagtail StreamField | 文章正文、栏目正文、灵活内容块 |
| Wagtail Images/Documents | 子期刊封面图、统计图表、文章配图、附件 |
| Wagtail Workflow / Moderation | 文章提交审核、审核通过、驳回 |
| Wagtail Snippets / Django Model | 子期刊、版位、投放关系、Search 静态配置、发布任务 |
| PostgreSQL | CMS 主数据库 |
| Redis | 异步任务队列、缓存、发布任务状态，可选但建议使用 |
| Celery / Django-Q / RQ | 静态生成、批量导入、发布任务异步执行，三选一，建议 Celery + Redis |

### 2.2 前台模板与静态输出

| 技术 | 用途 |
|---|---|
| Django Templates / Wagtail Templates | 生成 Nature 风格主站、子期刊、栏目、文章详情 HTML |
| HTML5 / CSS3 | 静态页面结构与样式 |
| JavaScript | 少量导航交互、菜单展开、静态搜索页输入表现 |
| Tailwind CSS 或 SCSS | 样式组织，二选一；如果 news-template 已有样式体系，优先沿用 |
| Static HTML Generator | 自定义 Django management command 或 service，用已发布内容渲染 HTML 文件 |
| manifest.json | 记录每次静态发布输出文件、资源、版本和回滚信息 |
| Nginx / CDN / 对象存储 | 线上只服务静态 HTML、图片、CSS、JS |

### 2.3 导入、文件与质量

| 技术 | 用途 |
|---|---|
| openpyxl | Excel 子期刊导入解析 |
| Pillow | 图片尺寸、格式校验，配合 Wagtail Images |
| pytest / Django TestCase | 模型、导入、审核、投放、静态生成测试 |
| Playwright | 管理端核心流程和前台静态页截图验收 |
| Ruff / Black / isort | Python 代码格式和静态检查 |
| Git / Gitee | 分支协作、代码评审、版本管理 |
| Docker Compose | 本地 PostgreSQL、Redis、MinIO/S3 兼容存储环境 |

## 3. 5 人角色总览

| 人员 | 角色 | 核心模块 | 最终交付 |
|---|---|---|---|
| 成员 A | 技术负责人 / Wagtail 架构负责人 | 项目架构、Wagtail 基础模型、权限、后台菜单、代码规范 | 可扩展的 Wagtail 二开底座 |
| 成员 B | 子期刊与导入负责人 | `Journal`、Excel 导入、A-Z、素材匹配、子期刊配置 | 120 子期刊批量导入和管理闭环 |
| 成员 C | 文章审核与内容负责人 | `ArticlePage`、审核流、AI 合著信息、文章状态、预览 | 文章从草稿到审核通过的内容闭环 |
| 成员 D | 版位投放与前台模板负责人 | `LayoutSlot`、`ArticlePlacement`、主站/子刊版位、Nature 模板 | 管理端可调整主站和子期刊文章位置 |
| 成员 E | 静态发布与质量负责人 | 静态生成器、manifest、发布任务、回滚、测试验收 | 前台固定 HTML 发布闭环和质量保障 |

## 4. 成员 A：技术负责人 / Wagtail 架构负责人

### 4.1 职责定位

成员 A 负责项目技术底座，确保所有成员的模块能在同一套 Wagtail 架构下协作。这个人不应该陷入单个页面细节，而是管模型边界、目录结构、权限、后台菜单、基础配置和合并质量。

### 4.2 负责模块

| 模块 | 说明 |
|---|---|
| 项目初始化 | 基于 `wagtail/news-template` 建立二开工程结构 |
| 应用拆分 | 建议拆分 `journals`、`articles`、`placements`、`static_publish`、`site_settings` |
| Wagtail 后台菜单 | 把子期刊、文章审核、投放、版位、发布中心挂进 Wagtail Admin |
| 权限体系 | 角色、用户组、后台菜单访问权限、发布权限 |
| 基础配置 | 主站配置、导航基线、环境变量、存储配置 |
| 代码规范 | Black、Ruff、isort、pytest、pre-commit 可选 |

### 4.3 关键技术栈

- Python
- Django
- Wagtail
- Wagtail Admin customization
- Wagtail permissions / groups
- PostgreSQL
- Django settings / environment config
- Git/Gitee 分支管理

### 4.4 主要数据模型

| 模型 | 所属应用 | 说明 |
|---|---|---|
| `SiteSettings` | `site_settings` | 主站名称、Logo、SEO、默认图片、静态输出根目录 |
| `NavigationItem` | `site_settings` | 四大导航及子菜单基线配置 |
| `AdminRolePreset` | `site_settings` | 角色权限预设，可用 Wagtail Group 实现 |
| `AuditLog` | `site_settings` | 高风险动作记录，发布、导入、回滚都要记 |

### 4.5 交付物

1. 基于 `wagtail/news-template` 的项目目录结构。
2. Wagtail 后台菜单结构：子期刊、文章、审核、投放、版位、静态发布。
3. 基础角色：超级管理员、内容管理员、审核人员、站点运营、发布管理员。
4. 主站配置模型和导航基线模型。
5. 团队开发规范文档和本地启动说明。

### 4.6 验收标准

- 其他 4 人可以在统一工程结构下开发各自模块。
- Wagtail 后台能看到清晰的业务菜单。
- 不同角色登录后看到的菜单和操作权限不同。
- 主站配置和导航基线可在后台维护，但普通编辑不能误改核心导航。

## 5. 成员 B：子期刊与批量导入负责人

### 5.1 职责定位

成员 B 负责 120 个子期刊站点的批量管理。这是项目规模化的第一道门槛。重点不是做一个列表页，而是让 120 条期刊配置能稳定导入、校验、更新、生成 A-Z 和子期刊首页。

### 5.2 负责模块

| 模块 | 说明 |
|---|---|
| `Journal` 模型 | 子期刊核心配置 |
| Excel 导入 | 解析、校验、预览、确认导入、错误报告 |
| A-Z 分组 | 根据期刊名生成 A-Z 索引 |
| 素材匹配 | 封面图、统计图表与 Wagtail Images 关联 |
| 子期刊状态 | 待补资料、启用、停用 |
| 子期刊首页数据源 | 给静态生成器提供期刊首页上下文 |

### 5.3 关键技术栈

- Django Model
- Wagtail Snippets 或 ModelAdmin/ViewSet
- Wagtail Images
- openpyxl
- PostgreSQL
- Celery/Redis，处理大批量导入可选
- Django forms / Wagtail admin views

### 5.4 主要数据模型

| 模型 | 说明 |
|---|---|
| `Journal` | 子期刊配置，120 个子站的核心数据 |
| `JournalImportJob` | 一次 Excel 导入任务 |
| `JournalImportRow` | 每一行导入结果，记录成功、失败和原因 |
| `JournalAssetBinding` | 可选，记录封面图、统计图表等素材绑定关系 |

### 5.5 `Journal` 关键字段

| 字段 | 说明 |
|---|---|
| `name` | 子期刊英文名 |
| `name_cn` | 中文辅助名 |
| `slug` | URL 唯一路径 |
| `az_group` | A-Z 分组 |
| `cover_image` | Wagtail Image，封面图 |
| `metrics_image` | Wagtail Image，统计图表 |
| `seo_title` | SEO 标题 |
| `seo_description` | SEO 描述 |
| `status` | 待补资料、启用、停用 |
| `sort_order` | 后台排序 |

### 5.6 管理端页面

| 页面 | 功能 |
|---|---|
| 子期刊列表 | 查询、筛选、查看状态、进入编辑 |
| 子期刊编辑 | 修改名称、slug、封面图、统计图表、SEO |
| Excel 导入页 | 上传 Excel、预校验、展示结果、确认导入 |
| 导入结果页 | 查看成功行、失败行、错误原因、下载报告 |
| A-Z 预览页 | 查看导入后 A-Z 期刊列表效果 |

### 5.7 交付物

1. `Journal` 模型和后台管理页。
2. Excel 导入模板。
3. Excel 解析、校验和导入逻辑。
4. 导入预览和错误报告。
5. A-Z 分组生成逻辑。
6. 给静态生成器使用的 `get_active_journals()`、`get_journal_context(slug)` 等服务函数。

### 5.8 验收标准

- 120 条子期刊 Excel 可一次导入。
- 单行错误不影响其他正确行。
- Slug 重复、缺少必填字段、图片不存在都能给出明确错误。
- 启用期刊能出现在 A-Z；停用期刊不出现在前台。
- 修改封面图或统计图表后，对应子期刊首页进入待重新生成状态。

## 6. 成员 C：文章审核与内容负责人

### 6.1 职责定位

成员 C 负责文章从创建、编辑、提交审核、审核通过到可投放的全过程。注意，这里管理的是后台内容生产流程，不代表前台运行时查文章数据库。

### 6.2 负责模块

| 模块 | 说明 |
|---|---|
| `ArticlePage` | 基于 Wagtail Page 扩展文章页类型 |
| 文章正文 | 使用 StreamField 管理标题、摘要、正文、图片、引用等 |
| AI 合著字段 | AI 合著人、AI 参与声明、责任说明 |
| 审核流 | 草稿、待审核、通过、驳回 |
| 版本记录 | Wagtail revision + 审核意见 |
| 文章预览 | 审核前预览文章详情页 |

### 6.3 关键技术栈

- Wagtail Page
- Wagtail StreamField
- Wagtail Workflow / Moderation
- Wagtail Revisions
- Django Model
- Wagtail Images
- Python validators

### 6.4 主要数据模型

| 模型 | 说明 |
|---|---|
| `ArticlePage` | 文章内容主体，可以继承 news-template 原有文章页 |
| `ArticleReviewRecord` | 审核记录，记录审核人、意见、结果 |
| `ArticleContributor` | 作者和 AI 合著人，可选独立模型 |
| `ArticleStaticMeta` | 静态输出相关元信息，如 static_slug、canonical 路径 |

### 6.5 文章关键字段

| 字段 | 说明 |
|---|---|
| `title` | 标题 |
| `abstract` | 摘要 |
| `body` | 正文，StreamField |
| `authors` | 作者 |
| `ai_co_authors` | AI 合著人 |
| `ai_contribution_statement` | AI 参与说明 |
| `responsibility_statement` | 责任说明 |
| `article_type` | AI Article、News、Opinion、Research Analysis 等 |
| `primary_journal` | 主属子期刊 |
| `related_journals` | 可关联多个子期刊 |
| `keywords` | 前台静态展示用关键词 |
| `review_status` | 审核状态 |
| `static_slug` | 静态 HTML 输出 slug |

### 6.6 审核状态

```text
草稿 -> 提交审核 -> 审核通过 -> 可投放 -> 已生成静态页 -> 已发布
             ↘ 驳回修改
```

### 6.7 管理端页面

| 页面 | 功能 |
|---|---|
| 文章列表 | 按状态、类型、期刊、作者筛选 |
| 文章编辑 | 编辑正文、作者、AI 合著信息、所属期刊 |
| 待审核列表 | 审核人员查看待处理文章 |
| 审核详情 | 查看正文、版本差异、通过或驳回 |
| 文章预览 | 使用前台文章详情模板预览静态效果 |

### 6.8 交付物

1. `ArticlePage` 或基于 news-template 的文章页扩展。
2. AI 合著信息字段。
3. 审核状态和审核记录。
4. 文章预览模板。
5. 审核通过后向投放模块暴露“可投放文章”查询接口。

### 6.9 验收标准

- 文章可以保存草稿，不影响前台。
- 文章可以提交审核、驳回、再次提交。
- 审核记录能看到审核人、时间、意见。
- 审核通过的文章可以进入投放中心。
- 未审核通过的文章不能被投放到前台版位。

## 7. 成员 D：版位投放与前台模板负责人

### 7.1 职责定位

成员 D 负责解决“审核后的文章放到前台哪里”的问题。这个模块是管理端和前台排版之间的关键层。后台不能让运营直接改模板，但必须让运营能调整主站文章位置、子期刊文章位置和栏目页文章顺序。

### 7.2 负责模块

| 模块 | 说明 |
|---|---|
| `LayoutSlot` | 主站、栏目、子期刊、文章详情的版位定义 |
| `ArticlePlacement` | 文章投放到版位的关系 |
| 主站版位 | Hero、Latest AI Article、News、Opinion 等 |
| 子期刊版位 | journal_hero、journal_featured、journal_latest 等 |
| 栏目版位 | 栏目首条、文章列表、侧栏推荐 |
| 前台模板接入 | 模板读取 slot 和 placement 渲染页面 |

### 7.3 关键技术栈

- Django Model
- Wagtail Snippets / Admin ViewSet
- Django Templates / Wagtail Templates
- HTML / CSS / JavaScript
- Wagtail Preview
- Query service / selector pattern

### 7.4 主要数据模型

| 模型 | 说明 |
|---|---|
| `LayoutSlot` | 版位定义 |
| `ArticlePlacement` | 文章投放关系 |
| `SlotRule` | 可选，自动取文章规则 |
| `PlacementPreview` | 可选，记录预览快照 |

### 7.5 主站版位

| 版位编码 | 展示位置 | 数量建议 |
|---|---|---:|
| `home_hero` | 首页主推 | 1 |
| `home_featured` | 首页精选 | 3-5 |
| `latest_ai_article` | Latest AI Article | 5-8 |
| `news_block` | News 区块 | 4-6 |
| `opinion_block` | Opinion 区块 | 4-6 |
| `research_analysis_block` | Research Analysis | 4-6 |
| `journal_highlights` | 子期刊推荐 | 3-6 |

### 7.6 子期刊版位

| 版位编码 | 展示位置 | 数量建议 |
|---|---|---:|
| `journal_hero` | 子期刊首页主推 | 1 |
| `journal_featured` | Featured in this journal | 3-5 |
| `journal_latest` | Latest Articles | 10-20 |
| `journal_about` | About this journal | 1 个内容块 |
| `journal_metrics` | 统计图表 | 1-3 张图 |

### 7.7 管理端页面

| 页面 | 功能 |
|---|---|
| 版位列表 | 查看主站、栏目、子期刊版位 |
| 版位编辑 | 设置名称、最大数量、手动/自动模式 |
| 文章投放 | 选择文章、目标站点、目标版位、排序、置顶 |
| 版位预览 | 预览主站或子期刊页面布局 |
| 冲突检查 | 检查同版位数量超限、重复置顶、过期投放 |

### 7.8 前台模板规则

- 模板结构固定，不能由运营任意拖拽。
- 模板通过 `slot_code` 获取文章列表。
- 手动置顶优先，自动规则补齐剩余位置。
- 文章可以覆盖前台展示标题和摘要，但不改变原文标题。
- 子期刊页面只展示与该期刊关联或被投放到该期刊的文章。

### 7.9 交付物

1. `LayoutSlot` 和 `ArticlePlacement` 模型。
2. 主站、子期刊、栏目默认版位初始化数据。
3. 文章投放后台页面。
4. 版位预览页面。
5. 前台模板读取版位数据的服务函数。

### 7.10 验收标准

- 审核通过文章可以投放到主站首页指定版位。
- 同一篇文章可以投放到多个子期刊版位。
- 后台调整排序后，前台静态页面顺序正确。
- 超出版位最大数量时有明确提示。
- 模板不被运营破坏，前台 Nature 风格布局保持统一。

## 8. 成员 E：静态发布与质量负责人

### 8.1 职责定位

成员 E 负责把 Wagtail 后台内容、子期刊配置、版位投放结果变成前台可访问的固定 HTML 文件。这是本项目“降低服务器压力和资源消耗”的关键。

### 8.2 负责模块

| 模块 | 说明 |
|---|---|
| 静态生成器 | 渲染主站、A-Z、子期刊、栏目、文章详情 HTML |
| `StaticPublishJob` | 发布任务记录 |
| manifest | 输出文件清单、版本、回滚信息 |
| 发布中心 | 预览影响范围、执行生成、查看结果 |
| 失败重试 | 单页面、单子期刊、整批重试 |
| 回滚 | 根据 manifest 回滚上一版本 |
| 自动化测试 | 单元测试、集成测试、Playwright 截图验收 |

### 8.3 关键技术栈

- Django management command
- Celery + Redis
- Django Templates rendering
- pathlib / shutil / filelock
- JSON manifest
- Nginx / CDN / 对象存储发布目录
- pytest / Django TestCase
- Playwright
- Docker Compose

### 8.4 主要数据模型

| 模型 | 说明 |
|---|---|
| `StaticPublishJob` | 一次发布任务 |
| `StaticPublishTarget` | 每个页面或站点的生成结果 |
| `StaticManifest` | 发布清单版本 |
| `StaticBuildLog` | 生成日志，便于排查 |

### 8.5 静态输出对象

| 对象 | 输出路径 |
|---|---|
| 主站首页 | `/index.html` |
| A-Z 期刊页 | `/journals/index.html` |
| 子期刊首页 | `/journals/{journal_slug}/index.html` |
| 栏目页 | `/explore-content/{section_slug}/index.html` |
| 文章详情页 | `/articles/{article_slug}/index.html` |
| Search 静态页 | `/search/index.html` |
| manifest | `/manifest.json` |

### 8.6 发布流程

```text
收集变更对象
  -> 计算受影响页面
  -> 生成预览清单
  -> 管理员确认
  -> 异步生成 HTML
  -> 校验资源引用
  -> 写入 manifest
  -> 切换发布版本
  -> 记录审计日志
```

### 8.7 交付物

1. 静态生成 management command。
2. `StaticPublishJob` 和 manifest 模型。
3. 发布中心后台页面。
4. 失败重试和回滚逻辑。
5. Playwright 验收脚本。
6. 发布质量报告模板。

### 8.8 验收标准

- 单篇文章能生成固定 HTML。
- 调整主站版位后，只重新生成受影响页面。
- 120 个子期刊首页能批量生成。
- 生成失败能定位到具体页面和错误原因。
- manifest 能记录本次输出文件。
- 可按 manifest 回滚到上一版本。
- 前台访问不依赖文章数据库运行时查询。

## 9. 五人协作接口约定

### 9.1 成员之间的数据接口

| 提供方 | 使用方 | 接口/服务 | 说明 |
|---|---|---|---|
| A | 全员 | 权限、菜单、基础 settings | 所有人基于同一后台结构开发 |
| B | D/E | `get_active_journals()` | 获取启用子期刊 |
| B | E | `get_journal_context(slug)` | 静态生成子期刊首页 |
| C | D | `get_approved_articles()` | 投放中心选择已审核文章 |
| C | E | `get_article_context(slug)` | 静态生成文章详情页 |
| D | E | `get_slot_items(slot_code, journal=None)` | 静态生成主站和子刊版位 |
| E | A | 发布状态、审计日志 | 回写后台工作台和日志 |

### 9.2 代码目录建议

```text
ai_author_forum/
  settings/
  home/
  articles/
    models.py
    blocks.py
    workflows.py
  journals/
    models.py
    importers.py
    validators.py
  placements/
    models.py
    services.py
  static_publish/
    models.py
    services.py
    management/commands/build_static_site.py
  site_settings/
    models.py
    navigation.py
  templates/
    home/
    articles/
    journals/
    sections/
  static/
```

## 10. 开发节奏建议

### 第 1 周：底座和模型

| 成员 | 任务 |
|---|---|
| A | 工程初始化、后台菜单、角色权限、基础配置 |
| B | `Journal` 模型、Excel 模板、导入校验初版 |
| C | `ArticlePage` 字段、AI 合著字段、审核状态 |
| D | `LayoutSlot`、`ArticlePlacement` 模型设计 |
| E | 静态生成器骨架、manifest 格式、发布任务模型 |

### 第 2 周：核心流程打通

| 成员 | 任务 |
|---|---|
| A | 权限联调、菜单联调、审计日志基础能力 |
| B | 120 条 Excel 导入、A-Z 数据源 |
| C | 文章提交审核、通过、驳回、预览 |
| D | 文章投放到主站和子期刊版位 |
| E | 生成首页、子期刊首页、文章详情 HTML |

### 第 3 周：管理端体验和发布闭环

| 成员 | 任务 |
|---|---|
| A | 统一后台样式和权限问题收口 |
| B | 导入报告、素材缺失提示、期刊状态完善 |
| C | 审核记录、版本差异、内容校验 |
| D | 版位预览、排序、置顶、冲突检查 |
| E | 发布中心、失败重试、manifest、回滚 |

### 第 4 周：验收和修正

| 成员 | 任务 |
|---|---|
| A | 合并检查、部署配置、系统文档 |
| B | 120 子期刊导入压测和异常样例 |
| C | 文章审核全流程测试 |
| D | 主站/子刊版位展示验收 |
| E | Playwright 截图验收、静态发布报告 |

## 11. 联调场景

| 场景 | 参与成员 | 验收点 |
|---|---|---|
| 120 子期刊导入 | A、B、E | 导入成功后 A-Z 和子期刊首页可生成 |
| 文章审核到投放 | A、C、D | 审核通过文章能进入投放中心 |
| 主站首页调整 | C、D、E | 投放文章后首页静态 HTML 展示正确 |
| 子期刊首页调整 | B、D、E | 指定期刊版位更新后只影响该子期刊 |
| Search 静态推荐 | C、D、E | 推荐文章可配置，页面不调用搜索接口 |
| 发布回滚 | A、E | manifest 可回滚，审计日志完整 |

## 12. 风险和控制措施

| 风险 | 影响 | 控制措施 |
|---|---|---|
| 120 子期刊被做成 120 套页面 | 后期维护失控 | 必须用 `Journal` 配置模型和统一模板 |
| 运营自由拖拽破坏前台布局 | Nature 风格不统一 | 模板固定，只开放版位内容和排序 |
| 审核通过文章直接上线 | 前台内容不可控 | 必须经过 `ArticlePlacement` 投放 |
| 静态生成任务失败无记录 | 无法排查和回滚 | `StaticPublishJob` 逐页面记录结果 |
| Search 被误做成真实搜索 | 增加服务器压力和开发成本 | 本期只做静态推荐配置 |
| 图片删除导致前台破图 | 静态页资源缺失 | 素材引用检查，已引用图片禁止直接删除 |

## 13. 最终交付清单

| 模块 | 交付物 |
|---|---|
| Wagtail 底座 | 后台菜单、角色权限、主站配置、导航基线 |
| 子期刊管理 | `Journal`、Excel 导入、A-Z、素材绑定、导入报告 |
| 文章审核 | `ArticlePage`、审核流、AI 合著字段、审核记录 |
| 版位投放 | `LayoutSlot`、`ArticlePlacement`、主站/子刊版位管理 |
| 静态发布 | HTML 生成、manifest、发布中心、回滚、失败重试 |
| 测试验收 | 单元测试、集成测试、Playwright 截图、发布报告 |

## 14. 结论

5 人团队的最佳分工不是简单按“前端、后端、测试”分，而是按 Wagtail 二开业务闭环分：

1. 成员 A 管 Wagtail 架构和权限底座。
2. 成员 B 管 120 子期刊和 Excel 导入。
3. 成员 C 管文章内容和审核流。
4. 成员 D 管版位投放和前台模板接入。
5. 成员 E 管静态生成、发布、回滚和质量验收。

这样分工后，每个人都有明确模块所有权，同时又能围绕同一条主线协作：**子期刊导入 -> 文章审核 -> 文章投放 -> 版位编排 -> 静态 HTML 发布。**
