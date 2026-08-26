# Reader Interactions RI-06 部署与验收记录

## 1. 范围

- 任务卡：`RI-06 审核后台与跨库审计`；依赖 RI-05 已完成。
- 部署标识：`ri06-20260817T083720-0800`；目标：`ai-author-forum-test`。
- 数据库迁移：`reader_access.0002`、`reader_access.0003`、`site_settings.0015`；只扩展控制面 command/audit schema，无数据回填和 contract 删除。

## 2. 实现

- Wagtail 新增待审、举报、全部评论工作区；queryset、详情 POST 和 service 都按有效 `JournalEditorAssignment` 重复校验，三类编辑只治理本刊，旧 Group、Django permission 和 `is_superuser` 不能旁路。
- 审核支持 `approve/reject/hide/restore/spam`、expected version、reason/note、幂等键和最多 100 条批量部分失败；编辑不能执行作者撤回。
- default 库先创建唯一 `ModerationCommand` 和不可变 `AuditLog(started)`；interactions 事务锁 comment，写状态、不可变 `CommentModerationEvent` 和 outbox；确认后追加 success/failure 审计。
- 超时/异常保持 `unknown` 且只写 failure 审计；reconciliation 只有查到同 command id 的不可变事件才补记 applied/success，未知结果不显示成功。
- 举报评论通过、恢复时 open report 变 dismissed；隐藏、拒绝、垃圾时变 resolved。公开状态变化刷新 Redis cache 和 immutable snapshot。
- 审计元数据包含 actor、journal/article/comment、action、expected/actual version、from/to state、reason、command/event/request、release 和错误类别；不记录正文、邮箱、token、session、URL 或原始 IP。
- service-token 内部接口新增 moderation command create/status；Celery apply/reconcile 任务固定到 `reader_comments`。

## 3. 验证

```text
python manage.py check                                  OK
python manage.py makemigrations --check --dry-run       OK (No changes detected)
ruff check / black --check                              OK
pytest reader_access + reader_interactions              67 passed, 8 skipped
pytest RI-06 focused (SQLite)                            4 passed
pytest full                                              880 passed, 11 skipped, 409 subtests
pytest RI-06 real PostgreSQL dual DB                     5 passed
npm run build:prod                                      OK (existing 799 KiB asset warning)
npm run test:e2e                                        31 passed, 2 skipped / 33
```

真实 PostgreSQL 测试覆盖两个同 expected version 命令并发竞争，仅一个 applied、另一个 stale/failed；同时覆盖幂等冲突、unknown 不成功、事件对账和本刊权限。候选生产镜像不包含 pytest，首次容器测试在执行前退出且 trap 已清理临时库；随后由锁定 `.venv` 连接同一 PostgreSQL 容器隔离双库完成验收。

## 4. 部署

- 最终镜像：`ai-author-forum-test-ri06:20260817T083720-0800`，manifest digest `sha256:10440ea83f6359715e58df79caab8cdaaf6d486933414bc7b19e3e3facb7a1fb`；web、static frontend、static/email/comment worker 均运行该 digest。
- 一次性 release job 首次应用三项迁移并 collectstatic `31 copied, 226 unmodified, 173 post-processed`；最终镜像复跑为 `No migrations to apply`、`0 copied, 257 unmodified, 173 post-processed`。
- `/livez/`、`/healthz/`、`/readyz/`、`/__static_health__/` 均 HTTP 200；session GET 为 200、`Cache-Control: no-store`；`nginx -t` 成功，仅有既有 duplicate MIME warning。
- `reader-comments-worker` 只监听 `reader_comments`，已注册 apply/reconcile moderation task。活动公共 manifest 保持 `20260816T171349272216Z-job92`，`is_active=true`、`failed=0`，未重建或绕过内容 readiness。

## 5. 备份与恢复

- 目录：`/opt/ai-author-forum-test-backups/ri06-20260817T084217+0800/`。
- predeploy default：35187531 bytes，SHA256 `1d7055dfd2bd3d0945fc21d2967630772dcd40560ad485f00997243fc5eb8788`。
- predeploy interactions：77438 bytes，SHA256 `29859f2b1eb78fbd78693b84149680dfc95a6f87c43b338cccb19b513e915ed0`。
- postdeploy default：35188919 bytes，SHA256 `bd99b53935ab8aa8473c1d26b2fe06ec5063ffdbe927e79af9602a14f7675bc6`。
- postdeploy interactions：77512 bytes，SHA256 `114901461e805cc3a30112b5a18486a653e9e94ef2ce55cde3b32a49fd98d050`。
- 隔离恢复到 `ri06_restore_default_20260817_0848` / `ri06_restore_interactions_20260817_0848`；双库 `migrate --check` 通过，default 计数 `articles=1217, placements=1499, moderation_commands=0, audit_logs=6381`，interactions `comments/events/reports=0/0/0`，六个 command 新字段齐全；临时库已删除。

## 6. 已知限制

- 所有读者互动 feature flag 仍由环境显式控制；本阶段没有开放 RI-07 PDF 或 RI-08 分享。
- 公共 static current 继续使用 job92；既有全量内容 readiness 缺口必须通过 canonical 审核/投放修复，不能由互动功能绕过。
- 正式生产域、隐私周期、SLO/RPO/RTO、值班和外部供应商参数仍由 RI-10 项目方签署，不在测试环境猜测。
