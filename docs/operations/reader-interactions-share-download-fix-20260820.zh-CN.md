# 分享、复制链接与 PDF 下载修复记录

## 1. 结果

- 部署目标：`ai-author-forum-test`，`https://test-author.huixixi.top`
- 活动静态 release：`20260820T110301037817Z-job111`
- 受保护 manifest：`activated`，SHA-256：`0f2142892ace3ea70d84c97d3199f6678092b14f9ad322917ba33121e520967f`
- 已激活 PDF 产物：`1213`
- 已启用下载能力投影：`1213`
- 旧 release 在新 release 完整校验前保持活动；job106-job110 的失败记录保留在审计链路中，未被激活。

## 2. 根因

1. 测试环境关闭了 `READER_SHARE_UI_ENABLED` 和 `READER_PDF_GRANTS_ENABLED`，前端因此将分享、复制链接和下载按钮保持为禁用状态。
2. 数据库最初没有可用的受保护 PDF 产物和 manifest。
3. 历史文章中存在没有当前已审核版本的记录，PDF 构建任务会错误地把它们作为必需目标。
4. PDF worker 缺少 CJK 字体，且 PDF 文本校验中的 UUID 在字体连字处理下可能被提取为不可匹配文本。
5. 静态发布 worker 运行在 web 容器内，而 protected-pdf 挂载为只读，manifest 无法写入。

## 3. 修复

- `ai_author_forum/reader_access/pdfs.py` 只为具有当前已审核版本的文章生成 PDF，并使用 UUID 和 release 作为稳定的机器校验锚点。
- `ai_author_forum/reader_access/tests/test_pdfs.py` 增加无已审核版本文章的排除测试，并覆盖 UUID 连字场景。
- `docker/pdf-worker/Dockerfile` 增加 `fonts-noto-cjk`。
- `templates/reader_interactions/pdf/article.html` 使用 CJK 字体，traceability 文本关闭连字。
- `docker-compose.production.yml` 为 protected-pdf 写入链路启用读写挂载，nginx 保持只读；PDF worker 增加 `init: true`。
- `/opt/ai-author-forum-test-shared/.env.test` 启用：
  - `READER_SHARE_UI_ENABLED=true`
  - `READER_PDF_GRANTS_ENABLED=true`
  - `READER_PDF_BUILD_WAIT_SECONDS=1800`

## 4. 备份

- 目录：`/opt/ai-author-forum-test-backups/share-download-fix-20260820T1420+0800/`
- `default.dump` SHA-256：`8dcc11d52c57a4d25d9c61ff35a9924269897f198d4c27cedd8663ea1cc4c0cd`
- `interactions.dump` SHA-256：`f5a132a9ab9f4c686e3a9e0d3c55fcb9238d796d94e8550ebfb315bff5f5fe5c`

## 5. 验证

| 检查 | 结果 |
| --- | --- |
| `python manage.py check` | 通过 |
| `python manage.py makemigrations --check --dry-run` | 通过，无模型变更 |
| 分享、PDF、下载相关测试 | 17 项通过，1 项按环境跳过 |
| `/healthz/` | HTTP 200，数据库、投影、私有存储检查通过 |
| `/readyz/` | HTTP 200，活动 release 已就绪 |
| `/__static_health__/` | HTTP 200，release 可用 |
| 测试数据库角色权限 | `ai_author_forum_interactions.rolcreatedb=false` |

匿名用户会看到可用的分享、复制链接和下载入口；执行受保护动作时仍需完成邮箱验证。验证后的会话按现有全局 reader session 规则可在其他文章复用。

## 6. 2026-08-24 静态文章交互恢复

- 根因：当前静态 release 中的历史文章页未带读者互动标记；同时投放查询把“已发布但后来产生新草稿”的文章错误排除，导致新 release 无法完整刷新这些页面。
- 修复：`ArticlePlacementQuerySet` 在存在活动 manifest 时按已发布版本保留文章，草稿与当前审核版本不一致不再错误隐藏正式页面；新增回归测试覆盖该状态组合。
- 本次发布：`20260824T070520151153Z-job118`，`StaticManifest` 与 `ProtectedManifest` 均已激活，1,213 个 PDF 产物全部 `activated`，发布任务 0 失败。

## 7. 2026-08-24 页面 release 一致性修复

用户抽查的两个正式文章页面仍由 HTML 中的 `job111` 标识加载，虽然能力接口和活动 manifest 已为 `job118`。读者前端检测到 `data-release` 与 `active_release` 不一致后会按 fail-closed 策略禁用评论、分享、复制和下载，因此接口正常也无法操作。

静态发布器现在同时检查交互挂载标记和页面内嵌的 `data-release`。选择性发布会重新渲染携带旧 release 标识的文章页，生成新的不可变 release 后再原子切换 `current`。新增回归测试覆盖“交互标记完整但 release 过期”的页面。
- 验证：`/healthz/`、`/readyz/`、`/__static_health__/` 均 HTTP 200；文章页包含 `data-reader-interactions`，评论、复制链接和下载入口可加载，受保护操作按预期要求邮箱验证。
