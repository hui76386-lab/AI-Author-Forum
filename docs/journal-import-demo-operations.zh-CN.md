# 子期刊批量导入与静态文章发布验收说明

本文档对应 B 模块交付范围：120 个子期站点批量导入、AI Article 静态 HTML 文章导入、素材匹配、A-Z/子期刊静态上下文、审核后投放到主站和子期站点，再进入统一静态发布。

## 技术栈与边界

- 底座：Django + Wagtail News Template 二次开发。
- 后台入口：Wagtail admin 下的 `journals`、`articles`、`placements`、`static_publish`。
- 表格处理：`openpyxl` 读取 `journals.xlsx` 和 `articles.xlsx`。
- 导入包：ZIP 包，工作簿放根目录，图片素材放 `media/`。
- 图片处理：Wagtail 自定义图片模型 `images.CustomImage`，通过文件名或相对路径匹配。
- 前端发布：`static_publish.StaticPublisher` 统一生成不可变 release，并原子切换到 `STATIC_PUBLISH_ROOT/current`。
- 文章口径：AI Article 文章以 HTML 静态页面形式固定生成；线上访问固定 HTML 文件，不走后台文章详情动态查询。
- 搜索口径：Search 是静态推荐页入口，本模块不建设真实搜索系统。

`journals.StaticArticle` 保留为兼容导入源和旧流程桥接模型；正式展示、审核、投放和静态发布以 `articles.ArticlePage` 为准。导入时会自动同步 canonical `ArticlePage`，不要再新增独立文章发布链路。

## 一键导入验收命令

默认生成 120 个子期站点、每个子期 100 篇 AI Article，总计 12,000 篇文章，并走正常导入链路：

```console
python manage.py seed_journal_demo_data
```

需要验证 15,000 篇文章通道时，使用 120 个子期、每个子期 125 篇：

```console
python manage.py seed_journal_demo_data --journals 120 --articles-per-journal 125
```

同时触发统一静态发布：

```console
python manage.py seed_journal_demo_data --publish-static-site --operator-id 1
```

只做预检查，不写入业务数据：

```console
python manage.py seed_journal_demo_data --dry-run
```

保存生成的导入包，便于人工复核 Excel：

```console
python manage.py seed_journal_demo_data --package-out tmp/journal-demo-package.zip
```

命令是幂等的：同一个 `--prefix` 重复执行会按子期刊 slug 和文章 slug 更新已有记录，不会重复创建站点或文章。

## 业务流程

1. 运营准备 ZIP 包或使用 `seed_journal_demo_data` 生成验收包。
2. `journals.xlsx` 创建或更新 1 主站下的子期刊资料，支持 120 个正式子期，模型层预留 200 个规模。
3. `articles.xlsx` 导入 AI Article 静态 HTML 片段或 HTML 文件路径。
4. 导入服务逐行校验，空行跳过，错误行写入错误报告，有效行继续成功入库。
5. `StaticArticle` 同步为 canonical `ArticlePage`，文章审核状态映射到正式文章模型。
6. `main_site_slot` 决定文章是否进入主站位置，`journal_slot` 决定文章是否进入对应子期站点位置。
7. 审核通过并有有效投放的文章进入静态发布目标。
8. `StaticPublisher` 生成首页、A-Z、子期刊首页、栏目页、文章详情页和静态 Search 页。
9. 生成成功后切换 `current`，失败时保留上一版，可重试或回滚。

## 验收重点

- 120 个子期站点可以一次性生成、重复执行可更新。
- 每个子期 100 篇文章时总计 12,000 篇；压测 15,000 篇使用 `--articles-per-journal 125`。
- 文章位置由管理端 Excel 字段或后续投放后台控制，主站位置和子期站点位置分开。
- 缺失素材只影响引用该素材的行，不回滚其他有效文章。
- 重复 slug 走更新逻辑，不制造重复 `Journal`、`StaticArticle` 或 `ArticlePage`。
- ZIP 路径穿越会被拒绝。
- 导入发布必须进入统一 `StaticPublishJob`，不能绕过静态发布中心。

## 回归命令

```console
python manage.py makemigrations --check --dry-run
python manage.py check
python -m pytest ai_author_forum/journals/tests -q
ruff check ai_author_forum/journals
black --check ai_author_forum/journals
isort --check-only ai_author_forum/journals
```
