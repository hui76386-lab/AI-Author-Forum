# 文章导入测试文件（每种格式 10 篇）

生成日期：2026-07-30
默认测试子期刊：`foundation-model-systems`

## 文件说明

| 格式 | 文件 | 文章数量 | 使用方式 |
|---|---|---:|---|
| XLSX | `01-xlsx/articles-10.xlsx` | 10 | 全局导入，文件内已填写 `journal_slug` |
| CSV | `02-csv/articles-10.csv` | 10 | 全局导入，编码为 UTF-8-SIG |
| ZIP | `03-zip/articles-10-mixed-documents.zip` | 10 | 5 篇 DOCX + 5 篇 Markdown，由 `articles.xlsx` 引用 |
| DOCX | `04-docx/*.docx` | 10 个单篇文件 | 每个文件单独上传；选择默认子期刊和 `AI Article` 类型 |
| Markdown | `05-markdown/*.md` | 10 个单篇文件 | 每个文件单独上传；选择默认子期刊，元数据在 Front Matter 中 |

每篇测试文章正文都包含 **10 个章节**，因此也可以验证长正文、标题层级和转换结果。

## 后台测试步骤

1. 进入“文章管理 → 一键导入文章”。
2. XLSX、CSV、ZIP 可直接上传并预检。
3. 单篇 DOCX：选择默认子期刊 `foundation-model-systems`，文章类型选择 `AI Article`，再逐个上传。
4. 单篇 Markdown：选择默认子期刊 `foundation-model-systems`，再逐个上传。
5. 核对预检结果后人工确认。
6. 确认文章仅进入草稿，未自动审核、投放或静态发布。

## 幂等与隔离

- 各格式使用不同 slug，不会互相覆盖。
- 重复导入同一文件应命中同一子期刊 + slug，适合测试更新/幂等逻辑。
- 文件仅用于测试，请勿在生产站点直接确认导入。
