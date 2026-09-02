# RI-03 邮箱验证和 reader session 部署记录

## 版本和范围

- 任务卡：`RI-03`
- 目标环境：`ai-author-forum-test`
- 时间：2026-08-17 02:48-03:42（Asia/Shanghai）
- 部署标识：`ri03-20260817T0326+0800`
- 上一个公共 manifest：`20260816T171349272216Z-job92`（本次保持不变）

## 实现和迁移

- 新增版本化邮箱加密、独立 lookup HMAC、pepper token HMAC、密钥轮换和生产配置校验；邮箱验证使用 fragment token，服务端 GET 只确认 challenge，POST 才消费。
- 新增原子 Redis 多维固定窗口限流、challenge/session 生命周期、单次消费和并发保护、幂等 profile/logout、CSRF 和 same-origin JSON API。
- 新增 Django 邮件适配器、文本/HTML 模板、`reader_email` 专用 Celery worker、加密 outbox payload 和 retention cleanup；worker 只监听 `reader_email`。
- 新增 RI-03 migration `reader_interactions.0002_one_issued_challenge_per_purpose`，数据库边界仍由 router 保持独立。
- flags 仍全部关闭：`READER_INTERACTIONS_ENABLED`、`READER_EMAIL_VERIFICATION_ENABLED`、`READER_COMMENTS_WRITE_ENABLED`、`READER_PDF_GRANTS_ENABLED`、`READER_SHARE_UI_ENABLED`、`READER_SNAPSHOT_READ_FALLBACK`。

## 验证证据

| 阶段 | 命令或检查 | 结果 |
| --- | --- | --- |
| 静态检查 | `python manage.py check`；`python manage.py makemigrations --check --dry-run` | 通过；无待生成迁移 |
| 代码质量 | Black、isort、Ruff（受影响文件） | 通过 |
| 阶段测试 | RI-03 定向测试 + 真实 PostgreSQL 并发/Redis/Celery/SMTP | 通过；PostgreSQL 并发验证和并发消费均满足单赢家 |
| 完整 pytest | `.venv/bin/pytest -q` | 847 passed，5 skipped，245 warnings，406 subtests |
| E2E | `npm run test:e2e` | 30 passed，2 skipped |
| 前端构建 | `npm run build:prod` | 成功；保留既有 799 KiB 资源告警 |
| 真实邮件链路 | Mailpit v1.30.0 + 隔离 `reader_email` worker | 1 封邮件送达；fragment token、canonical verify path 正确；无 query token/明文邮箱入库 |
| 故障/安全验收 | 缺 secret 启动失败、Redis/SMTP 故障、CSRF、开放重定向、扫描器 GET、过期/重放、session 轮换/注销、密钥轮换 | 通过 |
| 发布 job | default -> interactions release job | 成功；`0002` 约束已应用，collectstatic 249 copied / 7 unmodified / 172 post-processed |
| 公共静态构建 | `build_static_site`（`test-admin`） | 按内容就绪门禁失败并写入 AuditLog；旧 current 保持 `job92`，未激活不完整 release |
| 健康 | HTTPS forwarded `/livez/`、`/healthz/`、`/readyz/`；Nginx `/__static_health__/` | 全部 200；reader API 匿名态 200、`Cache-Control: no-store`、带 request ID |
| Nginx/日志 | `location ^~ /reader-api/`、`X-Forwarded-For $remote_addr`、`reader_api` 日志格式 | 通过；日志不含 query/referer/user-agent |
| 数据边界 | default 6 张 `reader_access_*`、0 张 `reader_interactions_*`；interactions 0/12（正式库无互动数据） | 通过；文章 1217、投放 1499 |
| 备份/恢复 | post-deploy 两份 custom dump 恢复隔离库、双 `migrate --check`、业务计数、表边界和部分唯一索引校验 | 通过；临时库已删除 |

## 运行产物

- release image：`sha256:a4d4cd26b1a4173d7bd472516cc05cbf03775b187a25f78eba66e5e123fdda4a`
- web：`sha256:96ceb33e93ac0e4d7ac4fcaeb79fa0f397cfd9857f5d30cef24325a7f707453e`
- worker：`sha256:6b6777554af3dbf7c367045132fd912c9c7aec92dd77f708f2b38e9da144ad42`
- reader-email-worker：`sha256:8fb0711d0604bfc82b890a5db3463e7e948f401611c263b678e89489cec1077c`
- static-frontend：`sha256:5ef23bc9028bf15404f10528abf022127b5d2f103fb5a74fc3beb7c0acb2fc85`
- 活动公共 manifest：`20260816T171349272216Z-job92`，`failed=0`，8856 files；本次没有生成或激活新公共内容 release。
- pre-deploy dumps：`/opt/ai-author-forum-test-backups/ri03-20260817T0326+0800/default-predeploy.dump`（SHA256 `45d3674b4920b3010cac977384797d14c944266d25d6144d178a85e587889a73`）、`interactions-predeploy.dump`（SHA256 `6791dce78fcc8214bb4555627582ac4b1cb73ffd9d679a1e51768404e688bc55`）。
- post-deploy dumps：`default-postdeploy.dump`（SHA256 `71260e267f868b3278b95e95a8b5a83da3b06d743e2fbe920c72cf15bb04ac5a`）、`interactions-postdeploy.dump`（SHA256 `9a14104b70c79a3ce01ccbe268d56e1eeb0679865d3f2451813afc539c665f29`）。

## 限制和后续

本次 `build_static_site` 发现已有 CMS 内容就绪缺口（首页 `home_hero`/`home_visual_stories` 以及若干文章目标），已生成 job 93 failure/start 审计记录。按照发布护栏没有绕过审核、正式投放或 manifest 校验，旧的已验证 `job92` 继续服务；这不阻塞 RI-03 的隔离 reader API、邮件和 session 验收。六个读者互动开关必须继续保持关闭，直到 RI-04 及后续阶段完成并经过灰度验收。
