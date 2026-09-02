# RI-04 政策、能力投影与后台权限部署记录

## 版本和范围

- 任务卡：`RI-04`
- 目标环境：`ai-author-forum-test`
- 时间：2026-08-17 03:42-04:48（Asia/Shanghai）
- 部署标识：`ri04-20260817T0438+0800`
- 活动公共 manifest：`20260816T171349272216Z-job92`（本次保持不变）

## 实现

- `JournalInteractionPolicy` 和 `ArticleInteractionPolicy` 通过 default 库控制面服务计算 effective policy；文章不在活动 manifest、revision 不匹配或没有 activated protected artifact 时，能力 fail closed。
- 三类有效本刊任命（`chief_editor`、`executive_editor`、`associate_editor`）统一经过对象级权限服务；跨刊、失效任命、旧 Group、Django permission、`is_superuser` 和客户端角色参数不能旁路。超级管理员只能以必填原因执行应急变更。
- 政策写入在同一 default 事务内完成版本递增、Redis desired/deny marker、`ControlPlaneOutbox` 和成功 `AuditLog(CONFIGURE)`；Redis marker 先于异步投影，版本不匹配时 API 隐藏评论并关闭下载。
- `reader_access` worker 以至少一次投递把 projection 写入 interactions；投影版本只增不减，重复事件幂等，旧 marker 不能清除新 marker。新增 `/reader-api/v1/articles/{public_id}/capabilities/` 和 service-token 保护的内部投影接口；Wagtail 后台显示 desired/applying/effective 三态。
- 新增生产 Compose `reader-comments-worker`，只监听 `reader_comments`；既有静态发布和邮件 worker 的队列边界未改变。

## 验证证据

| 阶段 | 命令或检查 | 结果 |
| --- | --- | --- |
| 静态检查 | `python manage.py check`；`python manage.py makemigrations --check --dry-run` | 通过；RI-04 无 schema migration |
| 代码质量 | Ruff、Black（受影响文件） | 通过 |
| 阶段测试 | `reader_access`/`reader_interactions` + policy/admin/API 测试 | 67 passed，4 SQLite PostgreSQL 专用跳过 |
| PostgreSQL 并发 | 隔离双 PostgreSQL：expected version policy write、projection race | 2 passed；单赢家、最高版本保留 |
| Redis | 真实 Redis Lua desired marker | 旧 marker 清理返回 0，新 marker 清理返回 1；无越权清理 |
| 完整 pytest | `.venv/bin/pytest -q` | 861 passed，7 skipped，248 warnings，408 subtests |
| E2E | `npm run test:e2e` | 30 passed，2 skipped |
| 前端构建 | `npm run build:prod` | 成功；既有 799 KiB 资源告警保留 |
| release job | candidate image 中 createcachetable、default/interactions migrate、collectstatic | 成功；30 copied / 226 unmodified / 172 post-processed |
| 服务更新 | web、worker、reader-email-worker、reader-comments-worker、static-frontend | 全部 candidate digest `sha256:c813ecb0397e4ecdee92e03ab3eb1e499193f57f02f11ef1761f4fef95751465` |
| 健康 | HTTPS forwarded `/livez/`、`/healthz/`、`/readyz/`；Nginx `/__static_health__/` | 全部 200；Reader session 200、`no-store`、request ID |
| 队列边界 | 容器 command、reader-comments worker startup | static_publish / reader_email / reader_comments 分离；worker 仅消费 `reader_comments` |
| 正式数据 | 文章/投放计数、manifest、互动库安全表 | 1217/1499；manifest job92；reader/challenge/session/outbox/projection 全部 0 |
| 备份/恢复 | post-deploy 两份 custom dump 恢复隔离库、双 `migrate --check`、业务计数和 6/12 表边界 | 通过；临时库已删除 |

## 运行产物和备份

- candidate release image：`sha256:c813ecb0397e4ecdee92e03ab3eb1e499193f57f02f11ef1761f4fef95751465`
- post-deploy dumps：`/opt/ai-author-forum-test-backups/ri04-20260817T0438+0800/default-postdeploy.dump`（SHA256 `babd9e9156f8310425224ab8ad3616abd1d0704947257cafffa740cf1bc8b103`）、`interactions-postdeploy.dump`（SHA256 `a62d6a8885b158de91cf8d844aa0fc152e46c44a0d492d8615b25ce22d033af0`）。
- default 仍只包含 `reader_access_*` 控制面，interactions 仍只包含 12 张 `reader_interactions_*` 数据面表；无跨库外键或正式读者数据。

## 限制

公共静态内容仍使用已验证 job92。RI-03 的 `build_static_site` job93 因现有首页/文章内容就绪缺口失败并写入审计；本阶段没有绕过审核、正式投放或 manifest 校验。互动开关仍全部关闭，RI-04 只部署控制面和 fail-closed 能力基础，待 RI-05 及后续卡完成后再灰度开启。
