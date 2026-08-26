# Reader Interactions RI-07 部署与验收记录

## 1. 范围和版本

- 阶段：RI-07 PDF 构建、protected manifest 和下载。
- 工作目录：`/opt/ai-author-forum-test-current`。
- 部署环境：`ai-author-forum-test` Docker Compose 测试运行环境。
- 功能开关：部署和验收期间保持关闭；未把测试数据、草稿或兼容模型送入正式静态前台。

## 2. 实现摘要

- 使用固定 Playwright/Chromium 基础镜像和独立 `reader_pdf` 队列，从冻结 approved revision、release、policy、locale 和资源输入断网渲染 PDF。
- PDF 校验文件头、EOF、页数、关键文本、嵌入字体、大小和 SHA-256；产物写入 `/data/protected-pdfs/protected/releases/...` 私有不可变目录或私有 S3 bucket。
- public manifest、全部必需 PDF、protected manifest 和 capability 投影前置校验全部通过后才联合激活；失败恢复原 `current`，回滚要求 public/protected manifest 成对完整。
- 下载授权要求已验证会话、活动 release、approved revision、有效 policy 和活动 artifact；Redis 原子限流故障关闭，grant 默认五分钟有效。
- filesystem 下载使用一次性 HMAC token 和 Nginx `X-Accel-Redirect`；S3 使用短时 presigned URL。Python 不返回 PDF bytes，Nginx internal 路径支持 HEAD/Range。

## 3. 阶段验证

- `python manage.py check`：通过。
- `python manage.py makemigrations --check --dry-run`：通过，无模型变更。
- RI-07 读者访问/互动测试：`76 passed, 9 skipped, 8 subtests passed`。
- 静态发布、安全配置和 Nginx 测试：`66 passed`。
- 全量 pytest：`898 passed, 12 skipped, 251 warnings, 410 subtests passed`。
- 真实 PostgreSQL 双库测试：`38 passed, 4 warnings, 3 subtests passed`。
- 真实 Chromium PDF：`1 passed`；测试 HTTP 端点收到 `0` 个请求，确认渲染器断网。
- 真实 Redis：限流第一请求允许、第二请求拒绝；完整 capability marker 写入/读取/清理通过。
- 真实 Nginx：直接访问 protected 路径为 404；经 internal 转发 HEAD 为 200，Range 为 206 且仅传 8 bytes。
- Webpack：通过；保留既有 `nature-home.png` 799 KiB 体积提示。
- Playwright E2E：`31 passed, 2 skipped`，共 33 项。
- 全仓 Ruff 存在两个与本阶段无关的既有问题：`journals/tests/test_admin_workspace.py` 导入顺序、`placements/publishing.py` 未使用变量；RI-07 范围 Ruff/Black 均通过。

## 4. 部署结果

- 部署时间：2026-08-17 10:08 至 10:16（Asia/Shanghai）。
- 主应用镜像：`ai-author-forum-test-ri07:20260817T100856-0800`，digest `sha256:5aae354a65e0de92bdcab013f4a8476f57d9776f45d8161d2cd9950635ea8133`。
- PDF worker 镜像：`ai-author-forum-test-ri07-pdf:20260817T100856-0800`，digest `sha256:356e84c4835c59360787a6fe1ec4f1c13531264e795482a7aea13fe5f428226a`。
- `docker compose ... --profile release run --rm --no-deps release`：通过；双库均无待应用迁移，collectstatic 为 `31 copied, 226 unmodified, 173 post-processed`。
- `docker compose ... up -d --no-build web worker reader-email-worker reader-comments-worker reader-pdf-worker static-frontend nginx`：通过。database 因 Compose 配置哈希变化自动重建容器并复用持久化卷，恢复 healthy 后应用服务才启动；Redis 未重启。
- web、worker、reader-email-worker、reader-comments-worker、static-frontend 均运行主应用 digest；reader-pdf-worker 运行 PDF digest，监听且仅消费 `reader_pdf`，并注册 `render_pdf` 任务。
- PDF worker 运行限额：1 CPU、1 GiB memory、256 pids、并发 1；published volume 只读，protected volume 可写。
- `/livez/`、`/healthz/`、`/readyz/`、`/__static_health__/` 均为 200；匿名 `/reader-api/v1/session/` 为 200 且 `Cache-Control: no-store`；`/_protected_pdf/direct.pdf` 为 404。
- `nginx -t` 通过；保留既有 duplicate `text/html` MIME warning。真实 internal/HEAD/Range 行为已在部署前集成验收通过。
- 活动 public manifest 保持 `20260816T171349272216Z-job92`，`current` 全文件摘要保持 `5921b52e...`，manifest 文件摘要保持 `4a21d4b4...`；未构建、激活或修改正式静态 release。
- 五个读者互动功能开关均为 false；protected artifact/manifest 均为 0，没有把测试产物激活到正式数据链路。

## 5. 备份和恢复演练

- 备份目录：`/opt/ai-author-forum-test-backups/ri07-20260817T1013-0800`，权限 0700。
- 部署前 default：35,188,932 bytes，SHA-256 `84912a675ae50e5742dd6021903c053367ddf9d0621d0c925c9f5778d1eb7af7`。
- 部署前 interactions：77,512 bytes，SHA-256 `d74859d7ada7199942a65115faf6b6fb6dad8053c70a496c2de4f23455fdd9ad`。
- 部署后 default：35,188,925 bytes，SHA-256 `2140804fabcd3044995a82a5ddd4c001e97159048f64e8ebafaa5038a32768a7`。
- 部署后 interactions：77,512 bytes，SHA-256 `5d614298467e884c6e08091bddc6e0d368417a0491e92247b7627c51b9d5b232`。
- PostgreSQL 15 容器内 `pg_restore --list` 可读取两份部署后归档；分别恢复到 `ri07_restore_default_20260817t1015` 和 `ri07_restore_interactions_20260817t1015` 成功。
- 已部署镜像连接两个恢复库执行 `migrate --check --database=default/interactions` 均通过；两库各有 310 条迁移记录。
- 恢复数据只读核验：articles 1217、placements 1499、audits 6381、active manifests 1、protected artifacts/manifests 0、download grants/events 0。
- 演练完成后仅删除上述两个明确命名的临时恢复库；数据库列表确认无 `ri07_restore_` 残留。

## 6. 已知限制

- 所有读者互动功能开关保持关闭；活动静态 release 在未执行经过审核的正式联合构建前保持不变。
- 当前测试环境使用 filesystem 私有存储；S3 不可变写入、元数据和 presigned 参数由单元测试覆盖，未使用真实云凭据执行集成测试。
- 分享 UI、容量/故障演练和最终灰度发布分别属于 RI-08、RI-09 和 RI-10，不在本阶段提前启用。
