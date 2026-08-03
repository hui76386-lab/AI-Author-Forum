# AI Author Forum CMS：DOCX / Markdown 文章一键导入开发任务书

> 文档状态：待评审，可直接进入开发
> 编制日期：2026-07-29
> 适用项目：`news-template` Wagtail 管理后台
> 功能归属：`articles` 文章导入模块
> 前置基线：`article-one-click-import-development-plan.zh-CN.md` 已实现并通过验收
> 负责人：项目总负责人 A 最终审定；文章模块负责人主实施；权限、审计、图片与静态发布模块协同

---

## 1. 任务目的

在现有文章一键导入能力上增加 DOCX 和 Markdown 正文来源，使运营人员可以：

1. 直接上传一个 `.docx`、`.md` 或 `.markdown` 文件，生成一篇待确认的文章草稿；
2. 使用 ZIP 数据包一次导入多篇 DOCX / Markdown 文章；
3. 在同一 ZIP 中混合使用 HTML、DOCX、Markdown 和表格内联 HTML；
4. 继续使用现有的上传、预校验、逐行预览、人工确认、后台执行、结果轮询、错误报告和审计流程；
5. 无论源文件中是否包含“已发布”“已审核”等信息，导入结果都只能是草稿，不能自动审核、投放或静态发布。

本任务不是新建一套文档导入系统，而是在现有文章一键导入服务中增加“文档转安全 HTML”的输入适配器。

---

## 2. 已有能力与改造原则

现有系统已经支持：

- XLSX、CSV、ZIP；
- `body_html` 和 `html_file` 正文来源；
- 全局多子期刊导入和本刊服务端锁定；
- “子期刊 + slug”幂等新增或更新；
- 文章、图片、栏目逐行校验；
- HTML 清洗、危险 URL 拦截和 ZIP 路径安全校验；
- 预览不写业务数据，确认后后台写入；
- 强制草稿、无自动投放、无自动静态发布；
- 独立文章导入权限和全流程审计；
- 15,000 行、50 MB 上传、ZIP 文件数和解压量限制。

本任务必须遵守以下最小改造原则：

1. 不复制现有导入流程，不建立 DOCX 专用或 Markdown 专用旁路；
2. 转换后的正文必须进入现有 `_validate_html()` 安全处理，转换器输出不能直接写入文章；
3. 继续使用 `ArticleImportJob` 和 `ArticleImportRow`；
4. 继续使用 `site_settings.import_articles` 权限；
5. 继续使用现有全局入口和本刊入口；
6. 预览阶段不得创建或更新文章、图片、栏目、投放和静态发布记录；
7. 转换失败按行隔离，不能使同一批次的其他正确文章回滚；
8. 不引入 Pandoc、LibreOffice、Microsoft Word COM 等外部进程或桌面软件依赖；
9. 不改变前台固定 HTML、无运行时文章数据库查询的架构口径。

---

## 3. 交付范围

### 3.1 P0 必须交付

#### A. 单篇文档直接导入

支持在现有“一键导入文章”页面直接上传：

- `.docx`
- `.md`
- `.markdown`

规则：

- 一个直接上传的文档只生成一条预览行；
- 全局模式必须选择默认子期刊；
- 本刊模式继续由服务端锁定子期刊；
- 用户需提供或确认 `article_type`、`authors` 和合法 `slug`；
- 标题可显式填写，也可按本任务书的规则从文档中推断；
- 预览成功后仍必须人工确认，不能上传后直接写入。

#### B. ZIP 批量文档导入

ZIP 中必须且只能存在一个清单文件：

- `articles.xlsx`，或
- `articles.csv`

清单每行可以使用以下四种正文来源之一：

- `body_html`
- `html_file`
- `docx_file`
- `markdown_file`

必须严格四选一。ZIP 中允许不同文章行使用不同正文来源。

#### C. DOCX 内容转换

P0 支持：

- 标题与普通段落；
- H1 至 H6；
- 粗体、斜体、下划线、删除线；
- 有序列表、无序列表和嵌套列表；
- 超链接；
- 引用块；
- 代码样式和预格式文本；
- 基础表格，包括表头、行列合并；
- 文档内嵌 JPG/JPEG、PNG、GIF、WebP 图片；
- 图片替代文本；
- 脚注和尾注在转换器可稳定输出时转为普通链接/注释结构。

P0 明确不保证原样还原：

- 文本框、艺术字、SmartArt、流程图；
- Word 图表；
- 公式和 MathML；
- 浮动定位、分栏、分页和纸张布局；
- 页眉、页脚、水印；
- 目录域、交叉引用域和动态字段；
- 批注、修订痕迹的审阅界面；
- 嵌入的 Excel、PowerPoint、PDF、OLE 对象；
- 宏、ActiveX 和外部模板。

遇到可降级元素时应生成预览警告；遇到宏、OLE、外部资源或其他高风险结构时必须拒绝。

#### D. Markdown 内容转换

P0 使用 CommonMark 兼容语法，并支持：

- ATX 标题和 Setext 标题；
- 段落、换行、强调、粗体、删除线；
- 有序列表、无序列表、嵌套列表；
- 引用块；
- 行内代码和围栏代码块；
- 链接；
- 表格；
- 水平线；
- 脚注；
- 任务列表；
- YAML Front Matter 元数据；
- ZIP 内相对路径图片。

Markdown 原始 HTML 可以进入解析结果，但必须再经过现有 HTML 安全校验；任何危险标签、事件属性、危险协议、外部图片或文件协议都必须拒绝，不能因为来源是 Markdown 而放宽。

### 3.2 P1 后续增强

以下内容不阻塞 P0：

- 多文件拖拽上传并自动生成批次；
- 没有 XLSX/CSV 清单时自动扫描 ZIP 中的多个文档；
- DOCX 自定义属性映射完整业务字段；
- DOCX 首图可选设为封面；
- 文档转换结果在线逐段编辑；
- 仅重试转换失败的行；
- DOCX 修订模式选择“最终稿”或“显示修订”；
- LaTeX/公式转图片或受控 HTML；
- Markdown 附件链接导入；
- 文档差异预览；
- 导入撤销。

### 3.3 非目标

本期不得实现：

- `.doc` 旧版二进制 Word 文件；
- `.rtf`、`.odt`、`.pages`、PDF；
- 从 URL、网盘或在线文档抓取；
- 自动审核通过；
- 自动创建 `ArticlePlacement`；
- 自动执行静态发布；
- 自动创建不存在的子期刊或栏目；
- 执行 Word 宏或嵌入对象；
- 依赖本机安装 Microsoft Office；
- 完全保真复刻 Word 页面排版。

---

## 4. 产品口径：“一键导入”的定义

“一键导入”表示用户从统一入口上传一个文件，即可进入完整导入闭环；不表示跳过安全与业务流程。

### 4.1 单篇文档流程

```text
进入文章一键导入
  -> 选择 DOCX / Markdown
  -> 填写或确认单篇默认元数据
  -> 上传一次
  -> 后台解析与转换
  -> 查看正文、元数据、图片和警告预览
  -> 人工确认
  -> 后台写入
  -> 结果轮询
  -> 文章草稿
```

### 4.2 批量文档流程

```text
准备 articles.xlsx / articles.csv
  -> 每行引用 docx_file 或 markdown_file
  -> 与 documents/、media/ 一起打包 ZIP
  -> 上传一次
  -> 后台逐行转换和预览
  -> 人工确认
  -> 后台逐行写入
  -> 下载错误报告
```

### 4.3 必须保留的人工确认

以下任一情况都不能自动写入：

- 文档转换成功；
- 只存在警告、没有错误；
- 用户拥有超级管理员权限；
- 文件中声明文章已审核或已发布；
- 同一 slug 已存在并将执行更新。

所有情况都必须先进入 `READY` 预览状态，再由有权限用户确认。

---

## 5. 支持格式与限制矩阵

| 上传方式 | 扩展名 | 文章数量 | 图片方式 | 是否需要清单 |
|---|---|---:|---|---|
| 单篇直传 | `.docx` | 1 | DOCX 内嵌图片 | 否 |
| 单篇直传 | `.md` / `.markdown` | 1 | 直传模式不支持独立本地图片；有图片时使用 ZIP | 否 |
| 批量包 | `.zip` | 1 至 15,000 行 | DOCX 内嵌图片或 ZIP 相对路径图片 | 是 |
| 原有方式 | `.xlsx` / `.csv` | 1 至 15,000 行 | 原有规则 | 文件本身即清单 |

统一限制：

- 上传文件最大 50 MB；
- 单个 DOCX 最大 25 MB；
- 单个 Markdown 最大 5 MB；
- 单个转换后 HTML 最大 10 MB；
- 单篇文档最多 100,000 个可见字符时仅警告，超过 1,000,000 个字符拒绝；
- 单篇最多 500 张图片；
- 图片继续执行现有 10 MB、尺寸、像素和格式限制；
- 外层 ZIP 文件数量继续不超过 500；
- 单个 DOCX 内部成员不超过 2,000，单任务所有 DOCX 的内部成员累计不超过 20,000；
- 外层 ZIP 与内层 DOCX 的所有解压字节必须计入同一个 250 MB 逻辑解压预算；
- 单个 ZIP 中引用的 DOCX/Markdown 文档数量 P0 不超过 200；
- 仍保留清单总行数 15,000 上限；没有文档引用的 HTML 行不受“200 个文档”限制。

说明：文档转换比普通表格解析消耗更多 CPU 和内存，因此 P0 单独限制文档数量。后续只有在容量测试通过并有后台队列限流时才允许提高。

---

## 6. ZIP 数据包规范

### 6.1 推荐结构

```text
article-document-import.zip
  articles.xlsx
  documents/
    article-a.docx
    article-b.md
    article-c.markdown
  media/
    article-b-cover.jpg
    article-b-figure-01.png
```

也允许使用 `articles.csv` 替换 `articles.xlsx`，但不能同时存在两个清单。

### 6.2 清单新增字段

在现有模板版本 1 的基础上，模板版本升级为 2，新增：

| 字段 | 含义 | 规则 |
|---|---|---|
| `docx_file` | ZIP 内 DOCX 正文路径 | 与其他正文来源严格四选一 |
| `markdown_file` | ZIP 内 Markdown 正文路径 | 与其他正文来源严格四选一 |

正文来源集合定义为：

```text
body_html / html_file / docx_file / markdown_file
```

每行必须恰好填写一个。填写 0 个或 2 个以上均失败。

### 6.3 示例清单

| journal_slug | title | slug | article_type | authors | docx_file | markdown_file | cover_image |
|---|---|---|---|---|---|---|---|
| ai-models | 模型能力综述 | model-capability-review | review | 张三 | documents/model-review.docx |  | media/model-cover.jpg |
| ai-safety | 安全治理进展 | safety-governance | news | 李四 |  | documents/safety.md |  |

### 6.4 路径规则

- 所有路径必须使用 ZIP 内相对 POSIX 路径；
- 禁止绝对路径、Windows 盘符、UNC 路径、反斜杠绕过、`..`、NUL 字节和重复归一化路径；
- 禁止符号链接；
- 文件扩展名与真实内容必须匹配；
- ZIP 内不得出现保留目录 `_converted_assets/`；该目录只允许转换服务在隔离临时目录中生成；
- Markdown 图片相对路径以 Markdown 文件所在目录为基准解析，再归一化到数据包根目录；
- DOCX 内嵌图片由转换服务写入隔离临时目录，不直接信任 DOCX 中的原始文件名。

---

## 7. 单篇直接上传元数据

### 7.1 表单字段

在 `ArticleImportUploadForm` 增加以下字段，并仅在选择 DOCX/Markdown 时显示：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `document_title` | CharField(255) | 否 | 空时按推断规则生成 |
| `document_slug` | SlugField(255) | 条件必填 | 无法安全推断时必须填写 |
| `document_article_type` | ChoiceField | 条件必填 | 使用 `ArticleType` 受控值；文档元数据未提供时必须填写 |
| `document_authors` | CharField(500) | 条件必填 | 与现有 `authors` 格式一致；文档元数据未提供时必须填写 |
| `document_ai_co_authors` | CharField(500) | 否 | 与现有字段一致 |
| `document_publication_date` | DateField | 否 | 仅作为内容字段，不代表发布状态 |

直接上传模式允许 `document_article_type` 和 `document_authors` 由安全解析后的文档元数据填补；上传表单不应为了判断这两个字段而同步解析文档。后台预检合并元数据后仍缺失时，该预览行以 `ARTICLE_DOCUMENT_METADATA_MISSING` 失败。由于标准 DOCX 核心属性没有可靠的 `article_type`，DOCX 直接上传时页面应提示用户通常需要显式选择文章类型。`document_title` 和 `document_slug` 可以按后续规则安全推断。

继续复用：

- `default_journal`；
- 全局/本刊 scope；
- 本刊服务端锁定；
- `csv_encoding` 仅对 CSV 生效，DOCX/Markdown 隐藏。

### 7.2 全局模式规则

直接上传 DOCX/Markdown 时，全局模式必须选择 `default_journal`。文档中的 `journal_slug` 元数据不能替代用户选择，也不能跨刊写入。

### 7.3 本刊模式规则

本刊模式忽略直接上传表单中的默认子期刊，并使用 URL 上下文解析出的 `target_journal`。服务端必须再次检查目标期刊为启用状态，不能只依赖禁用的前端控件。

### 7.4 元数据优先级

#### 单篇直接上传

从高到低：

1. 服务端锁定的子期刊；
2. 用户在上传表单中显式填写的值；
3. Markdown Front Matter 或 DOCX 核心属性；
4. 文档正文推断；
5. 文件名推断。

#### ZIP 批量导入

从高到低：

1. 服务端锁定的子期刊；
2. XLSX/CSV 清单行；
3. Markdown Front Matter 或 DOCX 核心属性，仅填补清单中的空值；
4. 文档正文推断；
5. 文件名推断。

清单和文档元数据冲突时，使用清单值并生成 `ARTICLE_DOCUMENT_METADATA_OVERRIDDEN` 警告。任何来源中的 `status`、`approved`、`published`、版位、置顶、排序、构建版本等字段都必须忽略并记录警告，绝不能改变草稿状态。

### 7.5 标题推断

当没有显式标题时，按以下顺序：

1. Markdown Front Matter 的 `title`；
2. DOCX `docProps/core.xml` 的 title；
3. 正文第一个 H1；
4. 第一个非空文本段落，最多取 255 个字符；
5. 去除扩展名后的文件名。

推断标题必须生成 `ARTICLE_DOCUMENT_TITLE_INFERRED` 警告，预览中明确显示来源。

### 7.6 slug 推断

slug 按以下顺序：

1. 上传表单或清单显式值；
2. Markdown Front Matter 的 `slug`；
3. ASCII 文件名；
4. ASCII 标题。

自动推断只能执行小写化、空格转连字符、连续连字符合并和首尾连字符清理，不得使用不透明随机值。推断结果必须通过现有 `validate_slug()`。中文或其他无法稳定转换的文件名不得自动生成拼音；应返回 `ARTICLE_DOCUMENT_SLUG_REQUIRED`，要求用户显式填写。

---

## 8. Markdown Front Matter 规范

### 8.1 支持字段

```yaml
---
title: 安全治理进展
slug: safety-governance
journal_slug: ai-safety
article_type: news
authors:
  - 李四
ai_co_authors:
  - AI Assistant
abstract: 摘要
keywords:
  - AI safety
  - governance
publication_date: 2026-07-29
cover_image: ../media/safety-cover.jpg
primary_category_code: policy
related_category_codes:
  - research
notes: 导入备注
---
```

支持的 Front Matter 键与文章模板字段保持一致，并额外允许：

- `title`
- `slug`
- `journal_slug`
- `article_type`
- `authors`
- `ai_co_authors`
- `abstract`
- `keywords`
- `publication_date`
- `cover_image`
- 主栏目和相关栏目字段
- `notes`

`authors`、`ai_co_authors`、`keywords`、相关栏目可以是字符串或字符串数组，进入现有标准化逻辑前统一转换为既有字段格式。

### 8.2 安全规则

- Front Matter 最大 64 KB；
- 顶层必须是映射；
- 最多 50 个键；
- 最大嵌套深度 5；
- 禁止自定义 YAML 标签；
- 禁止反序列化 Python 对象；
- 禁止重复键；
- 限制锚点和别名数量，拒绝递归别名和别名放大攻击；
- 使用安全加载器，不得调用不安全的 `yaml.load()`；
- 未知键生成警告，不进入业务数据；
- 审核、投放、静态发布字段必须忽略并审计。

---

## 9. DOCX 解析与转换规则

### 9.1 依赖选择

建议新增：

- `mammoth`：DOCX 转语义 HTML；
- `defusedxml`：读取 DOCX XML 元数据和关系时防御不可信 XML；
- 不使用 Word COM、LibreOffice 或 Pandoc。

正式提交时应在当前 Python、Django、Wagtail 环境中锁定通过测试的兼容版本，并更新 `requirements.txt`。不得仅依赖未锁定的间接依赖。

### 9.2 DOCX 包校验

DOCX 本质是 ZIP。转换前必须先做独立安全预检：

1. 扩展名为 `.docx`；
2. MIME/魔数为 ZIP 容器；
3. 必须包含 `[Content_Types].xml` 和 `word/document.xml`；
4. 所有成员路径通过与外层 ZIP 等价的路径安全校验；
5. 禁止符号链接；
6. 单成员、成员数量、单文档解压量和全任务逻辑解压预算均受限；
7. 拒绝 `vbaProject.bin`、ActiveX、OLE、`word/embeddings/`、`altChunk` 和包内可执行内容；
8. 拒绝加密或密码保护文档；
9. 外部关系按类型校验；普通 HTTP/HTTPS/mailto 超链接可保留，外部图片、外部模板、外部附件、外部 OLE 和 `file:` 关系必须拒绝；
10. XML 解析禁止 DTD、外部实体和实体展开。

### 9.3 核心属性

允许读取：

- title -> 候选标题；
- creator / lastModifiedBy -> creator 可作为候选作者，lastModifiedBy 只做审计元数据；
- keywords -> 候选关键词；
- created / modified -> 只记录源文档信息，不映射 `publication_date`；
- subject / description -> 只记录候选摘要，清单或表单为空时才可使用。

任何核心属性都不能映射审核、投放或发布状态。

### 9.4 图片处理

DOCX 图片转换回调必须：

- 按实际 MIME 检测扩展名，不信任原始文件名；
- 使用确定性路径 `_converted_assets/<document-sha256>/<sequence>.<ext>`；
- 计算图片 SHA-256；
- 进入现有图片格式、大小、像素和尺寸验证；
- 预览阶段只在临时目录中生成和校验，不创建 `CustomImage`；
- 执行阶段通过现有 `_materialize_image()` 创建或复用图片；
- 同一任务中相同内容图片按 SHA-256 复用；
- 记录来源文档、原始关系 ID 和转换后引用；
- 继续接入已被静态页面引用图片的删除保护能力。

### 9.5 样式映射

在独立常量中维护受控 Mammoth style map，至少覆盖：

```text
Heading 1 -> h1
Heading 2 -> h2
Heading 3 -> h3
Heading 4 -> h4
Heading 5 -> h5
Heading 6 -> h6
Quote / Intense Quote -> blockquote
Caption -> figcaption 或普通段落并产生警告
Code -> code
```

不得允许文档样式名直接生成任意标签或任意 class。所有输出仍由 HTML 白名单二次处理。

### 9.6 修订、批注与隐藏内容

P0 只导入转换器输出的可见正文：

- 不导入批注内容；
- 不导入页眉和页脚；
- 不把删除修订内容当作正式正文；
- 检测到修订、批注或隐藏文本时生成警告；
- 无法确认最终可见内容时拒绝并提示用户在 Word 中接受修订后重新保存。

---

## 10. Markdown 解析与转换规则

### 10.1 依赖选择

建议新增：

- `markdown-it-py`：CommonMark 兼容解析；
- `mdit-py-plugins`：表格以外的脚注、任务列表等受控插件；
- `PyYAML`：Front Matter 安全解析。

如果开发阶段选择其他库，必须满足：

- 不执行代码；
- 可禁用或限制插件；
- 可稳定输出 HTML；
- 能在当前 Python 版本运行；
- 输出必须进入统一 HTML 安全校验；
- 测试覆盖与本文档一致。

### 10.2 编码

- 仅接受 UTF-8 和 UTF-8-SIG；
- 不为 Markdown 增加 GB18030 自动猜测；
- 解码失败返回 `ARTICLE_MARKDOWN_ENCODING_INVALID`；
- 禁止 NUL 字节；
- 换行统一为 LF 后再计算正文哈希。

### 10.3 相对图片

Markdown 图片规则：

- 只允许 ZIP 内相对路径图片；
- 单篇 `.md` 直接上传模式没有伴随文件，因此相对图片应报错并提示改用 ZIP；
- 禁止 `http:`、`https:`、协议相对 URL、`data:`、`file:`、UNC 和绝对路径图片；
- 普通超链接可以使用 `http`、`https`、`mailto`，仍由现有 URL 规则校验；
- 图片路径解析基于当前 Markdown 文件目录；
- 图片最终通过现有 `_safe_package_file()` 和 `_materialize_image()`。

### 10.4 代码块

- 围栏代码只生成 `<pre><code>`；
- 语言名称只能进入受控 `class="language-*"`，不得生成任意属性；
- 不执行代码，不做服务器端语法运行；
- 不允许 Markdown 插件执行 include、模板表达式或文件读取。

### 10.5 标题去重

如果正文第一个 H1 与最终文章标题在标准化后完全一致：

- 从正文中移除该首个 H1，避免详情页重复显示标题；
- 生成 `ARTICLE_DOCUMENT_DUPLICATE_TITLE_REMOVED` 警告。

如果首个 H1 与文章标题不一致，则保留并生成 `ARTICLE_DOCUMENT_HEADING_TITLE_MISMATCH` 警告。

同一规则同时适用于 DOCX 和 Markdown。

---

## 11. 统一转换服务设计

### 11.1 新模块

新增：

```text
ai_author_forum/articles/document_importers.py
```

该模块只负责：

- 格式识别；
- 文档安全预检；
- 元数据提取；
- DOCX/Markdown 转 HTML；
- 内嵌图片输出到隔离临时目录；
- 生成结构化警告；
- 返回转换结果。

该模块不得：

- 创建或更新文章；
- 创建栏目；
- 创建投放；
- 执行静态发布；
- 决定审核状态；
- 绕过现有 HTML 和图片安全校验。

### 11.2 数据结构

建议定义：

```python
@dataclass(frozen=True)
class DocumentConversionWarning:
    code: str
    message: str
    source_path: str = ""
    element: str = ""


@dataclass
class ConvertedArticleDocument:
    source_format: str
    source_path: str
    source_sha256: str
    converter_name: str
    converter_version: str
    html: str
    metadata: dict
    generated_assets: list[str]
    warnings: list[DocumentConversionWarning]
    statistics: dict
```

### 11.3 服务接口

```python
def detect_document_format(path: Path, declared_suffix: str) -> str:
    """返回 docx 或 markdown；扩展名与内容不一致时抛业务错误。"""


def convert_article_document(
    path: Path,
    *,
    package_root: Path,
    generated_root: Path,
    budget: ImportExtractionBudget,
) -> ConvertedArticleDocument:
    """只转换，不写数据库业务数据。"""


def convert_docx_to_html(...) -> ConvertedArticleDocument:
    ...


def convert_markdown_to_html(...) -> ConvertedArticleDocument:
    ...
```

### 11.4 解压预算对象

建议增加共享预算对象，外层 ZIP 和内层 DOCX 共用：

```python
@dataclass
class ImportExtractionBudget:
    max_total_bytes: int
    max_nested_members: int
    used_bytes: int = 0
    used_nested_members: int = 0

    def consume(self, *, bytes_: int = 0, nested_members: int = 0) -> None:
        ...
```

任何嵌套解压都必须调用同一预算对象，避免“外层 ZIP 25 MB + 内层 DOCX 解压数 GB”的绕过。外层 ZIP 的 500 成员上限、单个 DOCX 的 2,000 成员上限分别执行；预算对象额外累计所有内层 DOCX 成员和所有层级解压字节。

### 11.5 接入现有导入服务

修改 `ArticleImportContext`，新增单篇文档默认值：

```python
@dataclass(frozen=True)
class ArticleImportContext:
    scope: str
    target_journal_id: int | None = None
    default_journal_id: int | None = None
    csv_encoding: str = "auto"
    document_defaults: dict | None = None
```

扩展 `ParsedPackage`：

```python
@dataclass
class ParsedPackage:
    rows: list[dict]
    root: Path
    package_name: str
    template_version: int
    source_format: str
    parser_version: str
    template_warning: str = ""
```

修改 `_load_package()` 分发：

- `.xlsx` -> 原逻辑；
- `.csv` -> 原逻辑；
- `.zip` -> 原逻辑，清单可引用 DOCX/Markdown；
- `.docx` -> 创建一个临时根目录和一条虚拟清单行；
- `.md` / `.markdown` -> 创建一个临时根目录和一条虚拟清单行。

修改 `_normalized_row()`：

1. 检查四种正文来源严格四选一；
2. HTML 来源保持原逻辑；
3. DOCX/Markdown 来源调用 `convert_article_document()`；
4. 合并清单、表单和文档元数据；
5. 将转换 HTML 送入 `_validate_html()`；
6. 合并转换器图片与正文图片；
7. 返回结构化警告、来源格式、来源路径和哈希；
8. 预览与执行阶段使用相同代码路径。

每次预览或执行应按 `绝对安全路径 + 文件 SHA-256` 使用任务内缓存，避免同一文档被重复转换。缓存只能存在于当前进程，不得跨任务共享可变临时文件。

---

## 12. 异步预检与后台执行

### 12.1 为什么必须异步

DOCX 转换和批量 Markdown 渲染比 XLSX/CSV 解析更耗时。不能在 Web 请求中转换 200 个 DOCX，否则容易造成请求超时和 Wagtail 管理进程阻塞。

### 12.2 P0 策略

- 直接 `.docx`、`.md`、`.markdown`：进入后台预检；
- 所有 ZIP：进入后台预检，因为必须在安全解压后才能确定是否包含文档；
- 原有直接 `.xlsx`、`.csv` 可以暂时保留同步预览，避免无关重构；
- 后续可统一所有格式为后台预检。

### 12.3 状态流

复用现有状态：

```text
PENDING
  -> VALIDATING
  -> READY
  -> IMPORTING
  -> COMPLETED
```

失败可发生在 `VALIDATING` 或 `IMPORTING`，均进入 `FAILED`。

### 12.4 新后台预检命令

新增：

```text
python manage.py preview_article_package
  --package <queue-path>
  --job-id <job-id>
  --operator-id <user-id>
```

本刊模式和默认值必须从已锁定的 `ArticleImportJob` / `summary` 读取，不接受命令行覆盖子期刊，防止后台命令参数造成越权。

新增启动函数：

```python
def start_article_import_preview_process(
    *,
    package_path: Path,
    job_id: int,
    operator_id: int | None,
) -> subprocess.Popen:
    ...
```

必须遵守现有 `journals.publishing` 的队列根目录、隐藏窗口、路径边界和哈希校验规则，不得自行拼接不受控 shell 命令。

### 12.5 预览幂等与重入

- 只有 `PENDING` 任务可以原子切换到 `VALIDATING`；
- `VALIDATING`、`READY`、`IMPORTING` 和终态任务再次启动预检必须拒绝；
- 后台进程必须校验队列文件 SHA-256 与任务记录一致；
- 同一任务不得生成重复行；
- 预检失败应清理本次生成的临时文件并写失败审计；
- 后台进程异常退出后任务不能永久停留在 `VALIDATING`，运维命令应能标记超时失败；
- 用户确认只允许 `READY` 状态。

---

## 13. 数据模型与迁移

### 13.1 ArticleImportJob

建议新增：

```python
source_format = models.CharField(
    max_length=16,
    choices=(
        ("xlsx", "XLSX"),
        ("csv", "CSV"),
        ("zip", "ZIP"),
        ("docx", "DOCX"),
        ("markdown", "Markdown"),
    ),
    blank=True,
    db_index=True,
)
parser_version = models.CharField(max_length=64, blank=True)
```

直接文档的 `template_version` 设为 0；表格和 ZIP 清单继续使用模板版本。

单篇表单默认值保存在 `summary["document_defaults"]`，确认后不得被请求参数覆盖。不要为每个默认字段增加独立数据库列。

### 13.2 ArticleImportRow

建议新增：

```python
source_path = models.CharField(max_length=500, blank=True)
source_format = models.CharField(max_length=16, blank=True)
conversion_warnings = models.JSONField(default=list, blank=True)
```

`normalized_data` 至少记录：

- `journal_id`
- `journal_slug`
- `source_sha256`
- `source_format`
- `source_path`
- `converter_name`
- `converter_version`
- `body_sha256`
- `sanitized_sha256`
- `generated_asset_count`
- `warning_codes`
- `metadata_sources`

不得在审计或错误报告中存储整个 DOCX 二进制或完整正文；正文仍由任务源文件和文章修订体系保存。

### 13.3 迁移要求

- 新增一条 `journals` 迁移；
- 字段默认值必须兼容现有任务；
- 迁移不回填或重写历史源文件；
- 历史任务 `source_format` 可保持空，展示时回退为扩展名推断；
- 必须通过 `makemigrations --check --dry-run`。

---

## 14. 模板版本与向后兼容

### 14.1 版本升级

将：

```python
ARTICLE_IMPORT_TEMPLATE_VERSION = 2
```

版本 2 增加 `docx_file` 和 `markdown_file`。

### 14.2 兼容规则

- 版本 1 模板继续接受；
- 版本 1 中不能使用新增文档列；
- 无版本元数据的 CSV 按现有兼容策略处理并警告；
- 高于当前版本拒绝；
- 版本 2 仍支持原有 `body_html` 和 `html_file`；
- 下载模板页面应同时提供 XLSX、CSV 和“文档批量 ZIP 示例包”。

### 14.3 示例包

新增示例包生成函数，建议输出：

```text
article-document-import-template.zip
  articles.xlsx
  documents/example.md
  media/README.txt
  README.txt
```

`README.txt` 必须说明：

- 四种正文来源严格四选一；
- DOCX/Markdown 导入后仍是草稿；
- Markdown 图片相对路径规则；
- DOCX 不支持宏和嵌入对象；
- 50 MB、200 个文档和解压预算限制。

---

## 15. 表单、路由与页面

### 15.1 表单

修改：

```text
ai_author_forum/articles/import_forms.py
```

要求：

- `ALLOWED_ARTICLE_IMPORT_SUFFIXES` 增加 `.docx`、`.md`、`.markdown`；
- FileInput `accept` 同步增加；
- 文件类型和 MIME 必须二次验证；
- DOCX/Markdown 条件字段执行服务端校验，不能只靠 JavaScript；
- `.xlsx`、`.csv`、`.zip` 不应被要求填写单篇字段；
- 直接 DOCX/Markdown 的全局模式必须选择默认子期刊；
- 本刊模式继续服务端锁刊。

### 15.2 路由

复用现有：

- `article_admin:import`
- `article_admin:import_confirm`
- `article_admin:import_status`
- `article_admin:import_errors`

模板下载接口新增格式参数，例如：

```text
?format=xlsx
?format=csv
?format=document-zip
```

不为 DOCX 和 Markdown 新建独立后台菜单。

### 15.3 页面

修改：

```text
templates/wagtailadmin/articles/import_dashboard.html
```

上传区必须明确显示：

- 支持 XLSX、CSV、ZIP、DOCX、MD；
- DOCX/MD 直接上传为单篇文章；
- 多篇 DOCX/MD 必须使用 ZIP + 清单；
- 上传后先预览，不直接写入；
- 所有文章强制草稿；
- DOCX 的复杂排版可能降级；
- Markdown 本地图片需放入 ZIP。

### 15.4 条件表单交互

前端 JavaScript 只负责显示或隐藏单篇字段：

- 选择 `.docx` / `.md` / `.markdown` 时显示；
- 选择其他格式时隐藏；
- 不依赖 JavaScript决定必填性；
- 键盘操作、错误聚焦和移动端布局必须可用；
- 禁止通过前端传入锁定子期刊 ID 覆盖服务端上下文。

### 15.5 预览展示

逐行预览增加：

- 来源格式；
- 来源文件路径；
- 元数据来源；
- 转换器名称和版本；
- 原始字符数、转换后 HTML 大小；
- 图片数量；
- 转换警告数量和可展开列表；
- 新增或更新动作；
- 最终目标子期刊；
- 最终标题、slug、文章类型和作者；
- 正文安全校验结果。

必须提供受控正文片段预览，预览内容本身也只能使用清洗后的 HTML，不得在后台页面直接渲染原始 HTML。

---

## 16. 错误码与警告码

### 16.1 新错误码

| 错误码 | 字段 | 含义 |
|---|---|---|
| `ARTICLE_DOCUMENT_FORMAT_UNSUPPORTED` | source_file | 不支持的文档格式 |
| `ARTICLE_DOCUMENT_MIME_MISMATCH` | source_file | 扩展名和实际内容不匹配 |
| `ARTICLE_DOCUMENT_SOURCE_CONFLICT` | body source | 正文来源不是严格四选一 |
| `ARTICLE_DOCUMENT_FILE_NOT_FOUND` | docx_file/markdown_file | ZIP 内文档不存在 |
| `ARTICLE_DOCUMENT_METADATA_MISSING` | metadata | 单篇导入缺少必要元数据 |
| `ARTICLE_DOCUMENT_SLUG_REQUIRED` | slug | 无法安全推断 slug |
| `ARTICLE_DOCUMENT_BODY_EMPTY` | body | 转换后没有可见正文 |
| `ARTICLE_DOCUMENT_CONVERSION_FAILED` | document | 转换器失败 |
| `ARTICLE_DOCUMENT_HTML_TOO_LARGE` | body | 转换后 HTML 超限 |
| `ARTICLE_DOCX_INVALID_PACKAGE` | docx_file | DOCX 包结构无效 |
| `ARTICLE_DOCX_ENCRYPTED` | docx_file | DOCX 已加密或受密码保护 |
| `ARTICLE_DOCX_MACRO_UNSAFE` | docx_file | 检测到宏或 ActiveX |
| `ARTICLE_DOCX_EMBEDDED_OBJECT_UNSAFE` | docx_file | 检测到 OLE 或嵌入对象 |
| `ARTICLE_DOCX_EXTERNAL_RELATIONSHIP_UNSAFE` | docx_file | 检测到危险外部关系 |
| `ARTICLE_DOCX_LIMIT_EXCEEDED` | docx_file | DOCX 文件数或解压预算超限 |
| `ARTICLE_MARKDOWN_ENCODING_INVALID` | markdown_file | 不是 UTF-8/UTF-8-SIG |
| `ARTICLE_MARKDOWN_FRONT_MATTER_INVALID` | front_matter | Front Matter 无效或不安全 |
| `ARTICLE_MARKDOWN_LOCAL_IMAGE_REQUIRES_ZIP` | body image | 直接 MD 引用了本地图片 |
| `ARTICLE_DOCUMENT_IMAGE_UNSAFE` | body image | 文档图片不满足安全限制 |
| `ARTICLE_IMPORT_PREVIEW_STATE_INVALID` | job | 预检任务状态不允许重入 |

### 16.2 新警告码

| 警告码 | 含义 |
|---|---|
| `ARTICLE_DOCUMENT_TITLE_INFERRED` | 标题由文档内容或文件名推断 |
| `ARTICLE_DOCUMENT_SLUG_INFERRED` | slug 自动推断 |
| `ARTICLE_DOCUMENT_METADATA_OVERRIDDEN` | 清单或表单覆盖文档元数据 |
| `ARTICLE_DOCUMENT_FORBIDDEN_METADATA_IGNORED` | 审核、投放或发布字段被忽略 |
| `ARTICLE_DOCUMENT_UNSUPPORTED_ELEMENT` | 存在未支持元素并已降级 |
| `ARTICLE_DOCUMENT_FORMAT_DEGRADED` | 排版无法完全保留 |
| `ARTICLE_DOCUMENT_EXTERNAL_LINK_PRESENT` | 正文包含允许的外部普通链接 |
| `ARTICLE_DOCUMENT_DUPLICATE_TITLE_REMOVED` | 首个重复 H1 已移除 |
| `ARTICLE_DOCUMENT_HEADING_TITLE_MISMATCH` | 正文首个 H1 与文章标题不同 |
| `ARTICLE_DOCX_REVISIONS_PRESENT` | 检测到修订或批注 |
| `ARTICLE_DOCUMENT_UNKNOWN_METADATA_IGNORED` | 未知元数据字段被忽略 |

错误码必须进入逐行错误报告；警告码必须进入预览、任务 summary 和审计元数据。

---

## 17. 安全要求

### 17.1 通用要求

- 不信任扩展名、MIME、文件名、ZIP 路径、DOCX 关系、Markdown 链接和转换器输出；
- 所有文件读取必须位于隔离临时根目录；
- 临时目录必须在成功、失败和异常退出路径清理；
- 不允许转换器访问网络；
- 不允许读取包外文件；
- 不允许执行宏、脚本、模板或代码；
- 不允许将原始 HTML 直接标记为 safe；
- 不允许通过 Markdown Front Matter 或 DOCX 属性写入审核状态；
- 不允许通过文档图片路径跨目录读取；
- 错误消息不得泄露服务器绝对路径。

### 17.2 HTML 统一出口

无论来源为：

- `body_html`
- `html_file`
- DOCX 转换 HTML
- Markdown 转换 HTML

都必须进入同一 `_validate_html()`。不得出现 `validate_docx_html()` 或 `validate_markdown_html()` 绕过公共策略的分支。

### 17.3 资源消耗防护

必须限制：

- 上传字节；
- 外层 ZIP 文件数量；
- DOCX 内层 ZIP 文件数量；
- 每个成员字节；
- 总逻辑解压字节；
- XML 节点/深度；
- Markdown 行数和字符数；
- Front Matter 大小、键数和别名数；
- 转换后 HTML 字节；
- 图片数量、大小、尺寸和总像素；
- 每个后台预检任务的最长运行时间；
- 同一用户并发预检任务数量。

建议增加设置项，使用 Django settings 管理，不能散落硬编码：

```text
ARTICLE_IMPORT_MAX_DOCUMENTS_PER_JOB=200
ARTICLE_IMPORT_MAX_DOCX_SIZE=26214400
ARTICLE_IMPORT_MAX_MARKDOWN_SIZE=5242880
ARTICLE_IMPORT_MAX_CONVERTED_HTML_SIZE=10485760
ARTICLE_IMPORT_MAX_LOGICAL_EXTRACTED_SIZE=262144000
ARTICLE_IMPORT_MAX_DOCX_MEMBERS=2000
ARTICLE_IMPORT_MAX_NESTED_MEMBERS_PER_JOB=20000
ARTICLE_IMPORT_PREVIEW_TIMEOUT_SECONDS=600
ARTICLE_IMPORT_MAX_CONCURRENT_PREVIEWS_PER_USER=2
```

默认值必须与代码常量一致，并在运维文档中说明。

---

## 18. 权限与业务闭环

### 18.1 权限

继续使用：

```text
site_settings.import_articles
```

要求：

- 无权限用户看不到入口；
- 即使直接请求 URL，也返回拒绝；
- 任务详情和错误报告继续执行任务可见性检查；
- 只有任务创建者、项目总负责人或超级管理员可以查看对应任务；
- 可疑文本强制处理仍仅限既有高权限角色并要求理由；
- 文档格式不得新增默认更高权限。

### 18.2 草稿规则

DOCX/Markdown 中出现以下内容时一律不能改变状态：

- `status: approved`
- `published: true`
- “已审核”“已发布”等文本；
- DOCX 自定义属性中的审核状态；
- 文件名中的 published/final；
- 清单中的 `status` 字段。

新增和更新都必须：

- `ArticleReviewStatus.DRAFT`；
- `ArticlePage.live = False`；
- `has_unpublished_changes = True`；
- 保存新 revision；
- 不创建 `ArticlePlacement`；
- 不进入静态发布任务或 manifest。

### 18.3 幂等

幂等键保持：

```text
目标子期刊 + slug
```

源文件格式、文件名和正文哈希都不能替代幂等键。重复导入已审核文章时仍必须回到草稿，并保留新的文章修订。

---

## 19. 审计要求

以下动作必须写入 `AuditLog`：

1. 上传文档并创建预检任务；
2. 开始后台预检；
3. 预检成功；
4. 预检失败；
5. 检测并忽略禁止元数据；
6. 用户确认导入；
7. 强制处理可疑文本；
8. 后台写入开始；
9. 后台写入成功；
10. 后台写入失败；
11. 超时任务人工标记失败；
12. 清理遗留队列文件。

审计 metadata 至少包括：

- job_id；
- scope；
- target_journal_id；
- source_format；
- source_sha256；
- parser/converter 名称和版本；
- 文档数量；
- 转换成功/失败/警告数量；
- 生成图片数量；
- 新增/更新/失败数量；
- 警告码汇总；
- override_reason；
- 操作人；
- 开始、结束和耗时。

不得在审计日志中保存完整正文、Front Matter 全量内容、DOCX 二进制或敏感本地路径。

---

## 20. 错误报告

现有 CSV 错误报告增加列：

- `source_format`
- `source_path`
- `converter`
- `conversion_warning_codes`
- `error_code`
- `error_field`
- `error_message`
- `suggestion`

错误建议必须可操作，例如：

- 接受 Word 修订后重新另存为 DOCX；
- 移除宏、嵌入对象或外部模板；
- 将 Markdown 保存为 UTF-8；
- 将 Markdown 和本地图片一起放入 ZIP；
- 为中文文件显式填写 slug；
- 保证四种正文来源只填写一个；
- 降低图片尺寸或文件大小。

---

## 21. 开发任务拆分

以下任务按依赖顺序执行。每个任务完成后先运行对应测试，不得等全部代码完成后一次性补测试。

### 阶段 0：基线保护

- [ ] 运行并保存当前文章导入测试结果；
- [ ] 记录当前迁移状态；
- [ ] 确认工作区已有其他成员改动，不覆盖无关文件；
- [ ] 建立 DOCX/Markdown 测试夹具目录；
- [ ] 确认现有 XLSX/CSV/ZIP 验收全部通过。

完成标准：新增功能开发前有可对比的测试基线。

### 阶段 1：依赖、常量和数据模型

- [ ] 在 `requirements.txt` 增加并锁定转换依赖；
- [ ] 新增文档格式 choices；
- [ ] 新增 Job/Row 字段；
- [ ] 生成迁移；
- [ ] 增加格式和限制 settings；
- [ ] 模板版本升级到 2；
- [ ] 保持版本 1 向后兼容。

完成标准：迁移可前进，历史任务可读取，系统检查无错误。

### 阶段 2：文档转换适配器

- [ ] 新建 `document_importers.py`；
- [ ] 实现格式检测；
- [ ] 实现共享解压预算；
- [ ] 实现 DOCX 包安全预检；
- [ ] 实现 DOCX 核心属性读取；
- [ ] 实现 Mammoth 转换和受控 style map；
- [ ] 实现 DOCX 图片回调；
- [ ] 实现 Markdown UTF-8 解码；
- [ ] 实现安全 Front Matter；
- [ ] 实现 Markdown 渲染；
- [ ] 实现相对图片重写；
- [ ] 实现标题去重和警告；
- [ ] 实现转换统计和结构化错误。

完成标准：转换器单元测试通过，且转换器本身不写数据库业务数据。

### 阶段 3：接入现有导入服务

- [ ] 扩展 `KNOWN_COLUMNS`；
- [ ] 实现四种正文来源严格四选一；
- [ ] 扩展 `_load_package()` 支持直传文档；
- [ ] 扩展 `_normalized_row()`；
- [ ] 实现元数据优先级；
- [ ] 实现任务内转换缓存；
- [ ] 转换 HTML 进入 `_validate_html()`；
- [ ] 转换图片进入现有图片校验；
- [ ] 预览不写文章和图片；
- [ ] 执行按行事务写入；
- [ ] 更新错误报告和 summary。

完成标准：服务层可预览和执行 DOCX、Markdown、混合 ZIP。

### 阶段 4：异步预检

- [ ] 新增待预检任务创建服务；
- [ ] 新增 `preview_article_package` 命令；
- [ ] 新增安全后台启动函数；
- [ ] 实现 `PENDING -> VALIDATING -> READY/FAILED` 原子状态流；
- [ ] 实现源文件 SHA-256 锁定；
- [ ] 实现预检重入保护；
- [ ] 实现超时失败处理；
- [ ] 实现队列文件清理；
- [ ] 状态接口支持 validating 进度和警告数量。

完成标准：Web 请求只负责上传和创建任务，DOCX/ZIP 转换在后台完成。

### 阶段 5：表单、模板和示例包

- [ ] 表单允许 `.docx`、`.md`、`.markdown`；
- [ ] 增加单篇条件字段和服务端校验；
- [ ] 更新文件 accept 和帮助文本；
- [ ] 更新上传页面格式说明；
- [ ] 增加文档预览信息；
- [ ] 增加警告展开区；
- [ ] 生成模板版本 2；
- [ ] 增加文档批量 ZIP 示例包；
- [ ] 验证桌面和移动端。

完成标准：运营人员无需理解内部转换器即可完成操作。

### 阶段 6：权限和审计

- [ ] 验证所有新入口继续使用 `import_articles`；
- [ ] 验证本刊锁刊；
- [ ] 验证任务详情可见性；
- [ ] 补齐预检审计；
- [ ] 补齐禁止元数据审计；
- [ ] 补齐失败和超时审计；
- [ ] 审计内容不泄漏全文和绝对路径。

完成标准：新格式没有权限旁路，关键动作可追踪。

### 阶段 7：测试、容量和文档

- [ ] 完成单元测试；
- [ ] 完成安全测试；
- [ ] 完成权限测试；
- [ ] 完成端到端集成测试；
- [ ] 完成容量测试；
- [ ] 使用 gstack `/browse` 完成真实后台 UI 验收；
- [ ] 更新运营说明；
- [ ] 更新发布和回退说明；
- [ ] 记录本机和预发布环境结果。

完成标准：本文档第 24 节所有验收项通过。

---

## 22. 预计改动文件

### 22.1 必改

```text
requirements.txt
ai_author_forum/articles/document_importers.py
ai_author_forum/articles/import_forms.py
ai_author_forum/articles/import_services.py
ai_author_forum/articles/import_templates.py
ai_author_forum/articles/import_views.py
ai_author_forum/articles/import_permissions.py
ai_author_forum/articles/management/commands/preview_article_package.py
ai_author_forum/articles/management/commands/import_article_package.py
ai_author_forum/articles/tests/test_document_importers.py
ai_author_forum/articles/tests/test_import_services.py
ai_author_forum/articles/tests/test_import_security.py
ai_author_forum/articles/tests/test_import_boundaries.py
ai_author_forum/articles/tests/test_import_views.py
ai_author_forum/articles/tests/test_import_permissions.py
ai_author_forum/articles/tests/test_import_integration.py
ai_author_forum/journals/models.py
ai_author_forum/journals/publishing.py
ai_author_forum/journals/migrations/<new_migration>.py
templates/wagtailadmin/articles/import_dashboard.html
docs/article-import-operations.zh-CN.md
```

### 22.2 可能改动

```text
ai_author_forum/settings/base.py
ai_author_forum/articles/management/commands/benchmark_article_import.py
ai_author_forum/site_settings/models.py
ai_author_forum/site_settings/services.py
ai_author_forum/images/references.py
```

原则：只修改任务相关文件，不执行全仓自动格式化，不覆盖工作区其他成员的未提交改动。

---

## 23. 测试方案

### 23.1 DOCX 单元测试

至少覆盖：

- 最小合法 DOCX；
- 标题、段落、列表、链接、表格；
- H1 标题推断；
- 核心属性提取；
- 内嵌 JPG/PNG/WebP/GIF；
- 图片预览不写数据库；
- 转换警告；
- 空正文；
- 损坏 DOCX；
- 扩展名与内容不匹配；
- 加密 DOCX；
- 宏；
- ActiveX；
- OLE/embeddings；
- altChunk；
- 外部图片和外部模板；
- 普通允许的外部超链接；
- 内层路径穿越；
- 内层 ZIP 炸弹；
- XML 外部实体；
- 超大 XML、超多段落、超多图片；
- 修订和批注警告；
- 不支持元素降级。

### 23.2 Markdown 单元测试

至少覆盖：

- UTF-8 和 UTF-8-SIG；
- 非 UTF-8；
- Front Matter 全字段；
- Front Matter 重复键；
- 不安全 YAML 标签；
- 锚点/别名放大；
- 标题、列表、表格、脚注、任务列表；
- 围栏代码；
- 原始 HTML；
- `javascript:` 链接；
- `data:` 图片；
- 远程图片；
- ZIP 相对图片；
- 直接 MD 本地图片报错；
- 路径穿越图片；
- 空正文；
- 超大 Markdown；
- 首 H1 去重；
- 标题不一致警告。

### 23.3 服务测试

至少覆盖：

- 直接 DOCX 生成一条预览行；
- 直接 Markdown 生成一条预览行；
- ZIP 中 DOCX、Markdown、HTML 混合；
- 四种正文来源 0 个、1 个、2 个和 4 个；
- 版本 1 向后兼容；
- 版本 2 新字段；
- 表单/清单覆盖文档元数据；
- 文档禁止元数据被忽略；
- 中文文件名要求显式 slug；
- “子期刊 + slug”新增和更新；
- 更新已审核文章后回到草稿；
- 行失败隔离；
- 错误报告字段完整；
- 预览不写文章、图片、栏目、投放和静态发布数据。

### 23.4 权限测试

至少覆盖：

- 无权限用户看不到入口；
- 无权限用户不能上传 DOCX/Markdown；
- 无权限用户不能确认；
- 无权限用户不能读取状态和错误报告；
- 本刊用户不能借文档元数据跨刊；
- 高权限可查看全局任务；
- 可疑文本强制处理权限与理由不变。

### 23.5 集成测试

必须覆盖真实链路：

```text
上传 -> PENDING -> VALIDATING -> READY
  -> 预览 -> 确认 -> IMPORTING -> COMPLETED
  -> 正确子期刊文章列表 -> 草稿
```

分别覆盖：

- 直接 DOCX；
- 直接 Markdown；
- ZIP 批量混合来源；
- 本刊锁定模式；
- 失败任务和错误报告；
- 后台预检重入；
- 后台执行重入；
- 源文件哈希不匹配；
- 禁止自动投放；
- 禁止自动静态发布；
- 审核通过但未投放仍不进入前台；
- 后续人工审核、投放、静态发布后才生成固定 HTML。

### 23.6 测试夹具

新增小型、可审计的测试夹具：

```text
ai_author_forum/articles/tests/fixtures/document_import/
  minimal.docx
  formatted.docx
  embedded-images.docx
  revisions.docx
  unsafe-external-image.docx
  unsafe-embedded-object.docx
  valid.md
  front-matter.md
  unsafe-html.md
  package-mixed.zip
```

二进制夹具必须记录生成方式和 SHA-256；恶意夹具不得包含真实可执行代码，只包含足以触发结构检测的无害占位成员。

### 23.7 回归命令

```powershell
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe -m pytest ai_author_forum/articles/tests -q
.\.venv\Scripts\python.exe -m pytest ai_author_forum/journals/tests/test_admin_dashboard.py -q
.\.venv\Scripts\python.exe -m pytest ai_author_forum/site_settings/tests -q
.\.venv\Scripts\python.exe -m pytest ai_author_forum/images/tests/test_references.py -q
```

任务相关 Python 文件还必须通过 Ruff、Black 和 isort。不得对整个脏工作区执行无关格式化。

---

## 24. 验收标准

以下全部通过才算完成，不接受“基本可用”。

### 24.1 格式与入口

- [ ] 全局和本刊两个现有入口都支持 DOCX、MD、Markdown；
- [ ] 直接上传一个 DOCX 生成且只生成一篇预览文章；
- [ ] 直接上传一个 Markdown 生成且只生成一篇预览文章；
- [ ] ZIP + 清单可以批量引用多个 DOCX/Markdown；
- [ ] ZIP 可混合 DOCX、Markdown、HTML；
- [ ] `.doc`、RTF、ODT、PDF 被明确拒绝；
- [ ] 页面明确说明“一键导入不等于直接发布”。

### 24.2 元数据

- [ ] 单篇直接上传可以填写标题、slug、文章类型和作者；
- [ ] Markdown Front Matter 可填补元数据；
- [ ] DOCX 核心属性可填补候选元数据；
- [ ] 元数据优先级与本文档一致；
- [ ] 冲突时有警告；
- [ ] 中文文件名不能生成不稳定 slug；
- [ ] 全局模式直接文档必须选择默认子期刊；
- [ ] 本刊模式始终服务端锁刊。

### 24.3 内容转换

- [ ] 基础标题、段落、强调、列表、链接、代码和表格转换正确；
- [ ] DOCX 内嵌图片可预览并在执行时进入图片库；
- [ ] Markdown ZIP 相对图片可导入；
- [ ] 直接 MD 引用本地图片时给出明确提示；
- [ ] 不支持元素产生警告或明确拒绝，不静默丢失高风险内容；
- [ ] 重复首 H1 按规则处理；
- [ ] 转换后空正文被拒绝；
- [ ] 转换后 HTML 一律经过公共安全校验。

### 24.4 安全

- [ ] 外层 ZIP 路径穿越被拒绝；
- [ ] DOCX 内层路径穿越被拒绝；
- [ ] 外层和内层共享解压预算；
- [ ] ZIP 炸弹被拒绝；
- [ ] 宏、ActiveX、OLE、外部模板和外部图片被拒绝；
- [ ] XML 外部实体被拒绝；
- [ ] YAML 不安全标签和别名放大被拒绝；
- [ ] Markdown 危险 HTML 和 URL 被拒绝；
- [ ] 转换器不能访问网络和包外文件；
- [ ] 错误信息不泄漏服务器绝对路径；
- [ ] 预览不写任何文章、图片、栏目、投放或静态发布业务数据。

### 24.5 后台任务

- [ ] DOCX、Markdown 和 ZIP 预检在后台执行；
- [ ] 状态正确经历 PENDING、VALIDATING、READY；
- [ ] 状态接口可轮询；
- [ ] 预检任务不能重复启动；
- [ ] 执行任务不能重复启动；
- [ ] 队列文件哈希不匹配时拒绝；
- [ ] 超时任务可被标记失败；
- [ ] 临时文件和队列文件按策略清理。

### 24.6 业务闭环

- [ ] 所有新增文章为草稿；
- [ ] 所有更新文章重新回到草稿；
- [ ] 文档中的 approved/published/status 不生效；
- [ ] 不自动创建投放；
- [ ] 不自动创建静态发布任务；
- [ ] 未投放文章不进入静态目标；
- [ ] 只有人工审核、投放和静态发布后才生成前台固定 HTML；
- [ ] 幂等键仍是“子期刊 + slug”。

### 24.7 权限与审计

- [ ] 新格式没有新增权限旁路；
- [ ] 任务详情、状态和错误报告执行可见性检查；
- [ ] 本刊文档无法跨刊写入；
- [ ] 上传、预检、确认、执行、失败和超时均有审计；
- [ ] 禁止元数据被忽略时有审计；
- [ ] 审计包含来源格式、哈希、转换器版本和统计；
- [ ] 审计不保存全文、二进制和服务器绝对路径。

### 24.8 UI

使用 gstack `/browse` 验收：

- [ ] 桌面端上传区不拥挤；
- [ ] 375px 移动端无横向页面溢出；
- [ ] 条件字段可键盘访问；
- [ ] 全局和本刊范围提示清晰；
- [ ] VALIDATING 状态有明确进度文案；
- [ ] 转换警告可展开查看；
- [ ] 预览表格支持横向和纵向滚动；
- [ ] 错误报告链接可用；
- [ ] 清洗后正文片段不会执行脚本或加载远程图片。

---

## 25. 容量与性能验收

扩展现有 `benchmark_article_import`，至少增加：

1. 单篇 5 MB Markdown；
2. 单篇 25 MB DOCX 边界；
3. 100 个普通 DOCX 批量预检；
4. 200 个 DOCX 上限预检；
5. 200 个 Markdown 上限预检；
6. 201 个文档上限拒绝；
7. 200 个文档、500 张图片；
8. 250 MB 逻辑解压边界；
9. 转换后 10 MB HTML 边界；
10. 100% 错误文档错误报告；
11. 同一终态任务重入。

记录：

- 上传保存耗时；
- 格式检测耗时；
- 解压预检耗时；
- 文档转换耗时；
- HTML 校验耗时；
- 预览总耗时；
- 写入总耗时；
- 峰值内存；
- SQL 数量；
- 临时磁盘峰值；
- 生成图片数量；
- 错误报告大小。

生产验收目标：

- Web 上传请求不执行批量文档转换；
- 后台任务内存峰值不超过部署容器限制的 70%；
- 超限任务在写业务数据前失败；
- 200 文档任务不会阻塞其他后台导入任务；
- 任务失败后无文章半批次全局回滚，已成功行和失败行结果可追踪；
- 预发布环境保存完整 JSON 基准结果。

不在本文档中承诺固定秒数；最终阈值以生产同规格预发布环境基准为准，并写入运维文档。

---

## 26. 发布、监控与回退

### 26.1 发布顺序

1. 合并代码和依赖；
2. 构建新镜像并验证转换依赖可导入；
3. 备份数据库；
4. 执行迁移；
5. 部署 Web 和后台任务进程；
6. 运行 `manage.py check`；
7. 用最小 Markdown、最小 DOCX 做预检；
8. 验证草稿、权限和审计；
9. 验证混合 ZIP；
10. 逐步开放给内容管理员。

### 26.2 监控指标

至少监控：

- 按来源格式的任务数量；
- VALIDATING 持续时间；
- 转换失败率；
- 各错误码数量；
- 平均和 P95 文档转换时间；
- 峰值内存和临时磁盘；
- 遗留队列文件数量；
- 超时任务数量；
- 每任务图片数量；
- DOCX 高风险结构拒绝数量。

### 26.3 功能回退

需要增加功能开关：

```text
ARTICLE_DOCUMENT_IMPORT_ENABLED=true
```

回退时：

1. 设置为 `false`，立即停止新 DOCX/Markdown 上传；
2. 原有 XLSX/CSV/ZIP HTML 导入继续可用；
3. 已进入 IMPORTING 的任务按运维决策完成或标记失败；
4. 不删除已导入草稿；
5. 不回滚会破坏历史任务读取的数据迁移；
6. 保留审计、源文件和错误报告；
7. 修复后可重新开启。

---

## 27. 运维文档更新要求

更新 `docs/article-import-operations.zh-CN.md`，必须补充：

- DOCX、MD、Markdown 支持范围；
- 单篇直传步骤；
- ZIP 批量文档包结构；
- Front Matter 示例；
- DOCX 不支持元素；
- Markdown 图片规则；
- 文件和解压限制；
- 常见错误码处理；
- VALIDATING 超时处理；
- 队列和临时文件清理；
- 容量测试命令；
- 功能开关和回退步骤。

---

## 28. Definition of Done

开发完成必须同时满足：

1. 本任务书 P0 范围全部实现；
2. 第 24 节所有验收项全部通过；
3. 第 23 节自动化测试通过；
4. 无待生成迁移；
5. Django system check 为 0 问题；
6. 任务相关文件通过 Ruff、Black 和 isort；
7. 使用 gstack `/browse` 完成桌面端和移动端验收并保存截图；
8. 完成预发布容量测试并保存 JSON；
9. 更新运营、发布和回退文档；
10. 确认所有导入结果只能是草稿；
11. 确认无自动投放、无自动静态发布；
12. 代码评审确认没有网络访问、文件路径、XML、YAML、HTML 或权限旁路；
13. 未修改或覆盖工作区无关文件；
14. 项目总负责人 A 最终签字确认。

---

## 29. 最终产品口径

上线后，对运营人员的统一说明为：

> 文章一键导入支持 XLSX、CSV、ZIP、DOCX 和 Markdown。单个 DOCX/Markdown 可以直接上传；多篇文档使用 ZIP + 文章清单批量导入。系统会先把文档转换为安全 HTML，并展示元数据、图片和转换警告供人工确认。导入只创建或更新文章草稿，不代表审核通过、前台投放或静态发布。
