# 文章一键导入运营、发布与回退说明

本文档对应文章后台现有的“全局文章导入”和“本刊文章导入”入口，覆盖 XLSX、CSV、ZIP、DOCX、MD、Markdown。DOCX/Markdown 能力只扩展正文来源，不改变文章审核、投放和静态发布闭环。

## 1. 不可变业务规则

- 一键导入不等于直接发布。
- 所有新增和更新结果都必须是草稿；文档中的 status、approved、published、placement、is_pinned、sort_order、build_version 等字段一律忽略并产生警告。
- 导入不创建 ArticlePlacement，不自动投放，也不启动静态发布。
- 文章须由人工完成审核、投放和静态发布后，才会进入固定 HTML 前台。
- 本刊入口由服务端锁定目标子期刊，文档或清单不能跨刊覆盖。
- 全局入口直传 DOCX/Markdown 时必须选择默认子期刊。
- 预览阶段不得写入文章、图片库、栏目、投放或静态发布数据。

## 2. 支持格式与入口

| 上传形式 | 支持内容 | 说明 |
|---|---|---|
| 单篇直传 | .docx、.md、.markdown | 一个上传文件只生成一条预览行；可在页面补充标题、slug、文章类型和作者 |
| 表格直传 | .xlsx、.csv | 保持原有模板兼容规则 |
| ZIP 批量包 | 清单 + DOCX/Markdown/HTML/图片 | ZIP 内必须且只能有一个 articles.xlsx 或 articles.csv |

明确不支持 .doc、RTF、ODT、PDF；上传后返回明确格式错误，不进行隐式转换。

## 3. 单篇 DOCX/Markdown 直传步骤

1. 进入 Wagtail 后台“文章”列表。
2. 选择“全局文章导入”或目标子期刊下的“本刊文章导入”。
3. 上传一个 .docx、.md 或 .markdown 文件。
4. 全局模式选择默认子期刊；本刊模式确认页面显示的服务端锁刊信息。
5. 按需填写标题、slug、文章类型和作者。中文或其他不能稳定生成 ASCII slug 的文件名必须显式填写 slug。
6. 提交预检；页面依次显示 PENDING、VALIDATING、READY 或 FAILED。
7. 检查元数据来源、正文来源、转换器、统计信息、图片和警告。
8. READY 后人工确认执行。执行结果仍为草稿。
9. 如需上前台，继续走审核、投放和静态发布流程。

元数据优先级为：页面/清单显式值 > Markdown Front Matter 或 DOCX 核心属性 > 标题与文件名的受控推断。冲突不静默覆盖，预览行会显示警告。

## 4. ZIP 批量文档包

推荐结构：

```text
article-package.zip
  articles.csv
  documents/
    article-a.docx
    article-b.md
    article-c.html
  media/
    figure-1.png
    figure-2.webp
```

模板版本 2 的正文来源字段为：

- body_html：清单内联 HTML；
- html_file：ZIP 内 HTML 文件；
- docx_file：ZIP 内 DOCX；
- markdown_file：ZIP 内 Markdown。

每一行必须严格四选一：0 个来源或同时出现 2 个及以上来源都失败。ZIP 可以混合 DOCX、Markdown 和 HTML，但路径必须是包内相对路径，不能含绝对路径、盘符、 ..、反斜杠穿越、符号链接或重复归一化路径。

## 5. Markdown Front Matter

支持 UTF-8 和 UTF-8-SIG。示例：

```yaml
---
title: "A Safe Imported Article"
slug: "safe-imported-article"
journal_slug: "ai-journal"
article_type: "news"
authors:
  - "Alice"
  - "Bob"
ai_co_authors:
  - "Model Assistant"
abstract: "Short abstract"
keywords:
  - "AI"
  - "publishing"
publication_date: 2026-07-29
cover_image: "media/cover.png"
primary_category_code: "research"
related_category_codes:
  - "news"
notes: "Operator note"
---

# A Safe Imported Article

Article body.
```

Front Matter 限制：最大 64 KB、最多 50 个键、最大嵌套深度 5；禁止重复键、自定义 YAML 标签、锚点、别名和非映射顶层。未知键被忽略并警告；审核、投放和发布字段被忽略并警告。

正文支持 CommonMark、标题、强调、列表、表格、脚注、任务列表和围栏代码。原始 HTML 仍进入统一 HTML 安全校验，不能绕过脚本、危险标签、危险属性和危险 URL 检查。

## 6. DOCX 支持与降级

支持基础标题、段落、粗体、斜体、列表、普通 HTTP/HTTPS/mailto 超链接、表格及内嵌 JPG/PNG/WebP/GIF。候选元数据来自核心属性：

- title -> 标题；
- creator -> 作者；
- keywords -> 关键词；
- description 优先、subject 备选 -> 摘要；
- lastModifiedBy、created、modified 只记录为审计元数据。

以下内容拒绝：

- 加密或密码保护；
- 宏、VBA、ActiveX；
- OLE、embeddings、嵌入对象；
- altChunk；
- 外部图片、外部模板、外部附件、外部 OLE、file: 关系；
- 可执行成员；
- 路径穿越、符号链接、重复归一化路径；
- DTD、实体、外部实体或超限 XML。

以下内容降级或警告，不执行：

- 修订、批注、隐藏文本；
- 未映射或不支持的 Word 元素、复杂样式和布局；
- 允许的外部普通超链接。

预览以转换后的安全正文为准，不保证保留 Word 的像素级排版。

## 7. Markdown 图片规则

- 直接上传 .md/.markdown 时，不允许引用本地相对图片；请改用 ZIP 包。
- ZIP 内 Markdown 只能引用同一 ZIP 中存在的相对图片。
- 禁止 http/https 远程图片、data:、file:、协议相对 URL、绝对路径、UNC、盘符、查询字符串、fragment、编码后的路径穿越和 .. 穿越。
- 图片实际内容必须是 JPG、PNG、WebP 或 GIF，扩展名和实际 MIME 必须一致。
- 预览只生成隔离临时资源，不写图片库；确认执行后才写入图片库。
- 同一任务按 SHA-256 复用相同图片。

## 8. 文件、字符、XML、图片和并发限制

| 限制项 | 值 |
|---|---:|
| 上传源文件总大小 | 50 MB |
| 单任务文档数 | 200；第 201 个拒绝 |
| 单个 DOCX / ZIP 成员 | 25 MB |
| 单个 Markdown | 5 MB |
| ZIP / 文档逻辑解压总量 | 250 MB |
| ZIP 文件数 | 500 |
| 单个 DOCX 内部成员 | 2,000 |
| 单任务嵌套 DOCX 成员 | 20,000 |
| XML 节点数 | 200,000 |
| XML 最大深度 | 128 |
| Markdown 行数 | 200,000 |
| Markdown 字符警告 | 800,000 |
| Markdown 字符拒绝 | 900,000 |
| 转换后 HTML | 10 MB |
| 可见字符警告 | 100,000 |
| 可见字符拒绝 | 1,000,000 |
| 单任务图片数 | 500 |
| 单图片文件 | 10 MB |
| 单图片宽/高 | 12,000 x 12,000 |
| 单图片像素 | 50,000,000 |
| 单任务图片总像素 | 250,000,000 |
| 同一用户并发预检 | 2 |
| VALIDATING 超时 | 600 秒 |

文件大小按字节计算，逻辑解压量按未压缩成员大小累计。更严格的字符、HTML、XML、图片或安全限制可以在文件大小上限之前触发拒绝。

## 9. 常见错误码与处理

| 错误码 | 含义与处理 |
|---|---|
| ARTICLE_DOCUMENT_FORMAT_UNSUPPORTED | 格式不支持或功能开关关闭；改用允许格式或联系发布管理员确认开关 |
| ARTICLE_DOCUMENT_MIME_MISMATCH | 扩展名与实际内容不符；重新导出正确文件，禁止仅改扩展名 |
| ARTICLE_DOCX_INVALID_PACKAGE | DOCX 损坏或缺少关键 OPC 结构；用受支持的编辑器重新另存为 DOCX |
| ARTICLE_DOCX_ENCRYPTED | 文档加密或受密码保护；移除保护后重新上传 |
| ARTICLE_DOCX_MACRO_UNSAFE | 包含宏；导出无宏 DOCX |
| ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE | 包含 OLE、ActiveX 或嵌入对象；删除对象并重新导出 |
| ARTICLE_DOCX_EXTERNAL_RELATIONSHIP_UNSAFE | 存在外部图片、模板或附件；把图片内嵌并移除外部关系 |
| ARTICLE_DOCX_LIMIT_EXCEEDED | DOCX 成员、XML 或逻辑解压超限；拆分或精简文档 |
| ARTICLE_MARKDOWN_ENCODING_INVALID | 非 UTF-8、含 NUL；转换为 UTF-8/UTF-8-SIG |
| ARTICLE_MARKDOWN_FRONT_MATTER_INVALID | YAML 重复键、危险标签、锚点、别名、深度或大小超限；按第 5 节修正 |
| ARTICLE_MARKDOWN_LOCAL_IMAGE_REQUIRES_ZIP | 直传 Markdown 引用了本地图片；改用 ZIP |
| ARTICLE_DOCUMENT_IMAGE_UNSAFE | 图片路径、MIME、大小、尺寸、像素或数量不安全；修正后重试 |
| ARTICLE_DOCUMENT_BODY_EMPTY | 转换后无正文；补充可见正文 |
| ARTICLE_DOCUMENT_HTML_TOO_LARGE | 转换后 HTML 超过 10 MB；拆分或精简 |
| ARTICLE_HTML_UNSAFE | 转换结果含危险标签、属性或 URL；删除后重试 |
| ARTICLE_DOCUMENT_SLUG_REQUIRED | 无法稳定推断 ASCII slug；显式填写 slug |
| ARTICLE_IMPORT_HASH_MISMATCH | 队列文件与锁定任务不一致；不要继续执行，重新上传并调查队列存储 |
| ARTICLE_IMPORT_PREVIEW_CONCURRENCY_LIMIT | 同一用户已有 2 个活跃预检；等待终态或由管理员处理超时任务 |
| ARTICLE_IMPORT_PREVIEW_STATE_INVALID / ARTICLE_IMPORT_STATE_INVALID | 任务重入或状态不允许；不得重复启动，检查现有终态和审计 |
| ARTICLE_ZIP_PATH_UNSAFE / ARTICLE_ZIP_SYMLINK | ZIP 路径或链接不安全；重新打包纯相对普通文件 |
| ARTICLE_ZIP_TOO_MANY_FILES / ARTICLE_ZIP_TOO_LARGE / ARTICLE_ZIP_MEMBER_TOO_LARGE | 文件数、解压总量或成员大小超限；拆包或压缩素材 |

错误报告只提供行号、来源相对路径、字段、错误码和修复提示，不应暴露服务器绝对路径或堆栈。

## 10. VALIDATING 超时与队列清理

默认超时 600 秒。任务长时间停留在 VALIDATING 时：

1. 检查后台进程、资源限制和审计日志；不要重复启动同一任务。
2. 执行清理命令：

```powershell
.\.venv\Scripts\python.exe manage.py cleanup_article_import_previews
```

3. 命令会把超过阈值的 VALIDATING 任务标记 FAILED，并写失败审计。
4. 清理超过保留时间、且 SHA-256 不属于 PENDING、VALIDATING、IMPORTING 活跃任务的遗留队列文件。
5. 审计只记录超时任务数、删除文件数、清理错误数和保留秒数，不记录服务器路径。
6. PENDING、VALIDATING、IMPORTING 活跃任务引用的队列文件不得删除。

预检和执行后台命令在成功、失败和异常路径都清理安全队列根内的本次文件；路径越界文件绝不删除。审计写入异常不得覆盖原始业务异常。

## 11. 容量测试

原表格/HTML 通道继续使用：

```powershell
.\.venv\Scripts\python.exe manage.py benchmark_article_import --rows 100 --journals 1 --scope journal --execute --output .dev-logs\article-import-capacity-100.json
```

DOCX/Markdown 完整容量套件：

```powershell
.\.venv\Scripts\python.exe manage.py benchmark_article_import --document-suite --output .dev-logs\article-document-import-capacity.json
```

可单独复测一个场景：

```powershell
.\.venv\Scripts\python.exe manage.py benchmark_article_import --document-suite --scenario 200-docx --output .dev-logs\article-document-import-200-docx.json
```

容量 JSON 必须记录上传保存、格式检测、解压预检、转换、HTML 校验、预览总耗时、写入总耗时、峰值内存、SQL 数、临时磁盘峰值、图片数和错误报告大小。边界场景如先被更严格的 900,000 字符限制拒绝，应在 JSON 中如实记录 error_code，不得为追求“通过”放宽安全阈值。

Web 请求只保存上传并创建 PENDING 任务，不执行批量文档转换。预发布环境必须使用与生产相同的容器、数据库和存储重复运行，并确认后台任务峰值内存不超过容器限制的 70%。

## 12. 发布、监控与回退

### 12.1 发布顺序

1. 合并代码和锁定依赖，验证 mammoth、markdown-it-py、mdit-py-plugins、PyYAML、defusedxml、Pillow、beautifulsoup4 可导入。
2. 构建镜像并备份数据库。
3. 执行迁移，部署 Web 与后台进程。
4. 运行 manage.py check。
5. 分别用最小 Markdown、最小 DOCX 和混合 ZIP 做预检。
6. 验证结果只为草稿、权限生效、审计完整、无投放和静态发布。
7. 逐步开放给内容管理员。

### 12.2 监控

至少监控：来源格式任务数、VALIDATING 时长、转换失败率、错误码数量、平均/P95 转换时间、峰值内存、临时磁盘、遗留队列文件、超时任务、每任务图片数、DOCX 高风险结构拒绝数。

### 12.3 功能开关

```text
ARTICLE_DOCUMENT_IMPORT_ENABLED=true
```

回退步骤：

1. 设置 ARTICLE_DOCUMENT_IMPORT_ENABLED=false，重启 Web 和后台进程，立即停止新 DOCX/Markdown 上传。
2. XLSX、CSV 和 ZIP HTML 导入继续可用。
3. 已进入 IMPORTING 的任务由发布管理员决定完成或标记失败。
4. 不删除已导入草稿，不自动撤销审核、投放或静态文件。
5. 不回滚会破坏历史任务读取的数据迁移。
6. 保留审计、源文件和错误报告。
7. 修复并完成最小 DOCX、Markdown、混合 ZIP 回归后再重新开启。

## 13. 验收记录

每次发布应在 .dev-logs 保存：

- 完整 document-suite JSON；
- 自动化测试输出；
- Ruff、Black、isort 输出；
- 桌面端和 375px gstack /browse 截图；
- 最小 DOCX、最小 Markdown、混合 ZIP 的任务 ID及审计证据。

不得把 .dev-logs 中可能含环境信息的本地证据提交到公开制品。

### 13.1 2026-07-29 本机最终验收

本次验收严格按 `article-docx-markdown-one-click-import-development-taskbook.zh-CN.md` 第 23、24、25、28 节执行。

环境说明：Windows 本机、Python 3.14.3、Wagtail 7.4.2、SQLite。此结果是开发机验收，不等同于“与生产同规格的预发布环境”；生产发布前仍必须在相同容器、数据库和存储中重跑容量套件，并确认后台峰值内存不超过容器限制的 70%。

自动化结果：

- `manage.py makemigrations --check --dry-run`：无待生成迁移；
- `manage.py check`：0 问题；
- `ai_author_forum/articles/tests`：134 passed，41 subtests passed；
- `journals/tests/test_admin_dashboard.py`：7 passed；
- `site_settings/tests`：64 passed，69 subtests passed；
- `images/tests/test_references.py`：15 passed；
- `articles/tests/test_import_capacity_command.py`：5 passed；
- 任务相关 Python 文件通过 Ruff、Black 和 isort。

容量结果：

- JSON：`E:\AI Author Forum\news-template\.dev-logs\article-document-import-capacity.json`；
- run id：`f83055e5a6`；
- 11 个场景全部通过：5 MB Markdown、25 MB DOCX、100 DOCX、200 DOCX、200 Markdown、201 文档拒绝、200 文档/500 图片、250 MB 逻辑解压、10 MB 转换后 HTML、100% 错误文档、终态重入；
- benchmark 使用事务回滚，结束后 benchmark 用户、期刊、任务、StaticArticle 和 ArticlePage 残留均为 0。

真实后台 UI 使用 gstack `/browse` 验收：

- 全局与本刊入口均支持 DOCX、MD、Markdown；本刊入口由服务端锁定目标子期刊；
- 上传成功使用 POST/Redirect/GET，一次操作只创建一个任务，终态刷新不重复提交；
- 桌面端 1280×900 和移动端 375×812 均无整页横向溢出；
- 条件字段可键盘访问，PENDING/VALIDATING 文案清晰；
- DOCX 修订警告可展开，预览容器实测 `clientWidth=876`、`scrollWidth=2080`、`clientHeight=542`、`scrollHeight=851`，支持双向滚动；
- 含脚本和远程图片的 Markdown 被 `ARTICLE_DOCUMENT_IMAGE_UNSAFE` 拒绝，`window.__articleImportUnsafe` 未执行、页面无 `example.invalid` 图片、网络未请求远程图片，错误报告 CSV 可下载；
- 任务 #205 经 UI 人工确认后为 COMPLETED，只创建 `review_status=draft`、`live=false` 的 ArticlePage/StaticArticle 草稿；ArticlePlacement 为 0，QA 用户触发的 StaticPublishJob 为 0；
- QA 任务 #205、#206、#207、草稿、审计、源文件、错误报告和 QA 用户已定向清理；历史任务 #13 保留。

主要证据：

- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-upload-form-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-validating-immediate.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-ready-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-journal-scope-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-docx-warning-expanded-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-unsafe-rejection-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\desktop-completed-draft-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\mobile-unsafe-rejection-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\mobile-completed-draft-fixed.png`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\docx-warning-scroll-evidence.json`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\unsafe-rejection-evidence.json`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\confirmed-draft-database-evidence.json`
- `E:\AI Author Forum\news-template\.dev-logs\ui-acceptance\qa-cleanup-evidence.json`
- `E:\AI Author Forum\news-template\.dev-logs\final-verification\`

### 13.2 2026-07-29 隔离预发布容量验收

本次容量验收在服务器上的**隔离 Docker 预发布环境**执行，不是开发机验收。部署使用独立的 Compose project、容器、卷和网络；中间件版本对齐该服务器已有产品线（PostgreSQL 15 / Redis 7.2），但未读取、修改、重启或连接服务器上既有 `yingchuang` 项目的容器、卷、数据库、缓存或秘密。

隔离预发布配置与基础检查：

- Web、worker、PostgreSQL、Redis、静态前端和 Nginx 均由独立项目管理；Nginx 仅监听服务器 loopback，避免暴露新的公网入口；
- worker 使用 `mem_limit: 2g`、`concurrency=2`。此前以 1 GiB 运行同一套件时，采样峰值为 785,924,096 bytes（73.1949%），超过第 25 节“低于容器限制 70%”的验收线；该失败记录仅保留作审计，不能作为通过结论；
- 增加至 2 GiB 后，`docker compose config --quiet`、`manage.py check`、`manage.py makemigrations --check --dry-run` 和 loopback `/healthz/` 均通过；
- 预发布配置文件为 `docker-compose.production.yml`、`docker-compose.local-middleware.yml` 和 `docker-compose.preproduction.yml` 的组合。实际部署秘密只写入服务器权限为 0600 的独立环境文件，本文档不记录地址、用户名、密码或连接串。

最终容量结果：

- 完整通过 JSON（本机副本）：`E:\AI Author Forum\news-template\.dev-logs\final-verification\article-document-import-capacity-preproduction-20260729.json`；
- 内存采样原始摘要（本机副本）：`E:\AI Author Forum\news-template\.dev-logs\final-verification\article-document-import-capacity-preproduction-20260729-memory-sampling.json`；
- 同一证据已持久化在隔离预发布工作目录的 `evidence/` 下，供部署验收复核；
- run id：`8a3d8fee44`；数据库供应商：PostgreSQL；11 个场景全部通过：5 MB Markdown、25 MB DOCX、100 DOCX、200 DOCX、200 Markdown、201 文档拒绝、200 文档/500 图片、250 MB 逻辑解压、10 MB 转换后 HTML、100% 错误文档和终态重入；
- 任务书要求记录的上传保存、格式检测、解压预检、转换、HTML 校验、预览、写入、SQL、临时磁盘、图片和错误报告数据均位于完整 JSON 的各场景指标中。

内存验收采用透明且可复核的两层记录。容器 cgroup v2 在该服务器没有暴露 `memory.peak`，因此 benchmark JSON 中如实标记 `runtime_memory.measurement = unavailable`，没有填造容器内峰值。同时，宿主机对该隔离 benchmark 容器对应 cgroup 的 `memory.current` 以 50 ms 间隔采样：共 307 个样本，峰值为 822,358,016 bytes，容器限制为 2,147,483,648 bytes，占比 **38.294%**，低于第 25 节规定的 70%，benchmark 容器退出码为 0。采样方式、间隔、样本数和结果都写入最终 JSON 的 `preproduction_memory_observation` 字段及独立采样摘要。

预发布还发现并修复了 PostgreSQL 特有的行锁兼容性问题。`ArticleImportJob` 的预检和确认启动查询原先在带有可空关联的 `select_related()` 查询上直接使用 `select_for_update()`；PostgreSQL 不允许对 `LEFT OUTER JOIN` 的 nullable side 默认加锁，可能报出“FOR UPDATE cannot be applied to the nullable side of an outer join”。现在两处均使用 `select_for_update(of=("self",))`，仅锁定任务表自身，保持幂等语义且兼容 PostgreSQL。修复后已重新构建隔离预发布镜像并通过完整容量套件。

保留的失败历史证据：`E:\AI Author Forum\news-template\.dev-logs\final-verification\article-document-import-capacity-preproduction-1g-memory-failed-20260729.json`。它说明 1 GiB 不满足内存门槛，不得替代上述最终通过 JSON。发布或清理隔离预发布资源前，应先确认上述本机证据已归档；不得清理或影响既有 `yingchuang` 服务。
