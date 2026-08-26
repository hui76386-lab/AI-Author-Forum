# Reader Interactions RI-05 部署与验收记录

## 1. 范围

- 任务卡：`RI-05 评论、回复、举报和公开快照`。
- 依赖：RI-04 已完成；本次未修改文章审核、投放、approved revision 或公共 manifest 激活链路。
- 变更版本：2026-08-17 05:55 +0800；部署前完整测试通过后生成候选镜像。
- 数据库迁移：无。`makemigrations --check --dry-run` 为 `No changes detected`。

## 2. 实现

- 评论服务：纯文本 NFC/换行/控制字符/字符数/UTF-8 字节数校验，重复正文阻断；普通评论 `published`，链接洪泛和风险适配器失败/超时进入 `pending`。
- 写入事务：活动 capability、Redis deny/version、读者状态、expected policy version、父评论同文同文章、一层回复、幂等记录、不可变 moderation event 和 data-plane outbox 在 interactions 库内受控提交。
- 状态操作：作者撤回 `published/hidden` 评论并保留占位；重复撤回返回同一 withdrawn 终态；举报按 reason 枚举和 `(comment, reporter, open)` 约束幂等。
- 读取：稳定 `created_at ASC, public_id ASC` 游标，根评论携带一层回复，匿名列表使用 ETag/Redis 短缓存和 single-flight；缓存故障只回源数据库，不阻断文章正文。
- 限流：评论 `1/10s、20/hour、100/day`，举报 `20/day`，不同窗口一次 Lua 检查；Redis 故障对敏感写入 fail closed。
- 快照：评论变化投递 `reader_comments`，版本化 immutable JSON 使用临时文件、fsync、原子 rename；内容相同重建幂等，根评论和回复并发重建只保留一个版本。
- API/UI：新增评论、回复、撤回、举报路由；CSRF/session/ETag/422/409/429 错误边界；文章底部无 PII 挂载点和懒加载 bundle，使用 `textContent`，不把 URL 自动链接。
- 内部接口：service token 保护 `POST /reader-api/v1/internal/v1/comment-snapshots/rebuild/`，仅投递 `reader_comments` 队列。

## 3. 验证

### 本地与阶段测试

```text
python manage.py check                                  OK
python manage.py makemigrations --check --dry-run       OK (No changes detected)
ruff check ai_author_forum/reader_interactions          OK
black --check ai_author_forum/reader_interactions       OK
pytest RI-05 focused                                    17 passed
pytest full                                              876 passed, 10 skipped, 408 subtests
npm run build:prod                                      OK (existing 799 KiB asset warning)
npm run test:e2e                                        31 passed, 2 skipped / 33
```

SQLite 跳过项仅为仓库既有 PostgreSQL 行锁测试及 RI-05 三项并发测试；未将 SQLite 结果作为并发验收结论。

### PostgreSQL/Redis 验收

- 在真实 PostgreSQL 双库临时库执行 `test_comment_concurrency.py`：`3 passed`，覆盖同幂等键单写、政策关闭锁顺序、快照并发单版本。
- 在真实 Redis db15 验证多窗口 Lua：首次允许、第二次拒绝且 `retry_after > 0`；评论 cache round-trip、invalidate、rebuild lock 均通过；验收键已删除。
- 风控异常和可配置超时均返回 pending；capability/rate Redis 故障敏感写入 fail closed。

## 4. 发布护栏

- `release` job 只执行迁移/collectstatic；本阶段无迁移，仍执行一次性 release 命令并保留旧公共 current。
- 未调用 `build_static_site` 生成新的公共 release；既有 job93 内容 readiness 失败及 `20260816T171349272216Z-job92` current 不被修改。
- Compose 使用项目名 `ai-author-forum-test`；`reader-comments-worker` 只监听 `reader_comments`，快照卷对 Web 为只读、评论 worker 为读写，static/email worker 不挂载。
- 候选镜像、release job、容器 digest、健康检查和备份恢复结果在本文件部署段补记。

### 部署结果（2026-08-17 05:58-06:03 +0800）

- 镜像：`ai-author-forum-test-ri05:20260817T0555-0800`，image manifest digest `sha256:80804eae993f9f5852871c249505af2da75d94c9f658893d0ae16e10f912fd7e`；web、static-frontend、static/reader-email/reader-comments worker 均运行该 digest。
- 一次性 release job：迁移 `No migrations to apply`；collectstatic `31 static files copied, 226 unmodified, 173 post-processed`。
- 容器：`ai-author-forum-test-web-1`、`worker-1`、`reader-email-worker-1`、`reader-comments-worker-1`、`static-frontend-1` 均 running；评论 worker 日志只注册并监听 `reader_comments`。
- HTTP/Nginx：`/livez/`、`/healthz/`、`/readyz/`、`/__static_health__/` 均 HTTP 200；匿名 `/reader-api/v1/session/` HTTP 200、`Cache-Control: no-store`、设置 Secure CSRF cookie；静态资源 `js/919.js` HTTP 200（8169 bytes）；普通首页响应包含 `X-Static-Release: true`、`X-Static-Served-By: nginx`。`nginx -t` 成功，仅有既有 duplicate MIME warning。
- 公共静态构建：`build_static_site --actor test-admin` 创建 job94 `20260816T220150334932Z-job94` 后因既有 content readiness blocker 失败；活动 manifest 仍为 `20260816T171349272216Z-job92`，`failed=0`，current 未切换。
- 部署前备份：`default-predeploy.dump` SHA256 `9af2ca2b536bceff2cdcb986e89d2486af3946fb91d969e119be6e01852801e6`（35187129 bytes）；`interactions-predeploy.dump` SHA256 `fead7a93fb7228d3b31f868d6e23f81dc3783c3e1a6696e0995dafa028962c21`（77438 bytes）。
- 部署后备份：`default-postdeploy.dump` SHA256 `e2999573e679e6e2a76105eab7dcbe7f9195a6f9dcbc2f68ef62aff1898eba8c`（35187531 bytes）；`interactions-postdeploy.dump` SHA256 `beec90a4d5e497ef6f57bc309225b88bbbee333c375d4df80307220bbe2dba93`（77438 bytes）。
- 恢复演练：恢复到 `ri05_restore_default_20260817_0602` / `ri05_restore_interactions_20260817_0602`，双库 `migrate --check` 通过；default 计数 `articles=1217, placements=1499`，interactions `reader_identity=0, capability_projection=0, comment_snapshot=0`；临时库已删除。

## 5. 已知限制

- RI-06 审核后台、RI-07 PDF、RI-08 分享统一前端尚未开放；相关开关继续按任务卡保持关闭或由环境显式开启。
- 风险适配器接口已提供严格超时和 fail-pending 行为，但首期仍使用内置确定性规则，不接入外部风控供应商。
- 评论快照当前使用共享卷；迁移对象存储/CDN 属后续容量任务，公开正文仍由已激活静态 manifest 直出。
