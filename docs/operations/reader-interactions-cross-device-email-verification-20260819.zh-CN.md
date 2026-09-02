# 跨设备邮箱验证实现记录

## 1. 范围与版本

- 方案：`docs/reader-interactions/11-cross-device-email-verification-plan.zh-CN.md`
- 实现范围：评论/分享/PDF 共用的邮箱验证设备配对流程；不改变评论审核和静态发布链路。
- 记录时间（UTC）：2026-08-19
- Git SHA：当前工作区未提交；以本次构建版本和容器镜像 digest 作为部署标识
- 部署目标：`ai-author-forum-test`，公网入口 `https://test-author.huixixi.top`，本机容器入口 `127.0.0.1:18080`
- 应用版本：`cross-device-email-20260819T153400+0800`
- 静态 release：`20260819T074222044795Z-job100`，由 `20260819T062313239215Z-job99` 原子替换

## 2. 变更

- 新增 `ReaderDeviceFlow` 模型、状态机、设备绑定 Cookie、配对码哈希、过期清理和审计记录。
- 邮箱验证创建接口返回 `flow_id`、一次性 `user_code`、`expires_in`、`interval`。
- 手机消费接口校验配对码并批准流程；电脑使用 `status` 轮询和 `claim` 领取自己的 `reader_session`。
- 增加取消、重发/替代、失败次数、幂等领取、跨设备独立会话和评论草稿恢复。
- 修复电脑 `claim` 后能力刷新触发评论组件重渲染、导致流程隔离草稿丢失的问题；能力刷新时显式恢复当前 flow 草稿，仍不自动提交。
- 新增迁移：`reader_interactions.0003_readerdeviceflow`、`0004_emailverificationchallenge_consumed_reader_and_more`。

## 3. 验证证据

| 阶段 | 命令或检查 | 结果 |
| --- | --- | --- |
| Django check | `./.venv/bin/python manage.py check` | 通过 |
| migration dry-run | `./.venv/bin/python manage.py makemigrations --check --dry-run` | 通过 |
| 后端跨设备测试 | `./.venv/bin/python manage.py test ai_author_forum.reader_interactions.tests.test_device_flows` | 通过（9 项；SQLite 环境跳过 2 项 PostgreSQL 行锁测试） |
| PostgreSQL 并发测试 | Compose release 镜像运行 `ReaderDeviceFlowConcurrencyTests` | 通过（2 项）；覆盖双手机确认、双电脑领取，测试库已删除且临时 `CREATEDB` 权限已撤销 |
| 过期批准状态回归 | 后端跨设备测试 | 通过；`approved` 流程过期后 status/claim 均为终态 |
| 受影响验证/邮件测试 | `./.venv/bin/python manage.py test ai_author_forum.reader_interactions.tests.test_verification_services ai_author_forum.reader_interactions.tests.test_email_tasks` | 通过 |
| 前端 i18n 检查 | `./.venv/bin/python manage.py test ai_author_forum.utils.tests.test_i18n --verbosity 1` | 通过（11 项） |
| 完整测试集 | `./.venv/bin/python manage.py test --verbosity 1` | 通过；测试发现共 838 项，进程退出码 0 |
| 前端构建 | `npm run build:prod` | 通过；Webpack 仅报告 `nature-home.png` 体积提示 |
| Playwright 全量 | `npx playwright test` | 通过（35 项，跳过 2 项） |
| 双设备 E2E | 独立桌面/移动 browser context | 通过；移动端真实加载验证脚本并消费配对码后，桌面才从 pending 进入 approved/claim；两端 session Cookie 不同，草稿恢复且只手动发布一次 |
| 双设备线上 UI 验收 | Playwright，1280x720 + 390x844 | 通过；配对码、倒计时、等待状态和无横向溢出 |
| `/healthz/`、`/readyz/`、`/__static_health__/` | `curl https://test-author.huixixi.top/{healthz,readyz,__static_health__}/` | 全部 HTTP 200 |
| 静态发布健康检查 | `docker compose ... exec web python manage.py check_static_publish_health --skip-broker` | 通过；DB、Redis、reader crypto、projection 与 active release 均正常 |
| manifest/release 一致性 | 容器内读取 `/data/published/current/manifest.json` 并计算摘要 | 通过；job100，8877 files，failed=0，SHA-256 `b9061e6055dbd99f33d3e5429a5ef3a6bba9b30f5d2f14562e188007746a44b0` |
| API 真实入口 | `POST /reader-api/v1/email-verifications/`、随机 flow status | 通过；无效邮箱保持中性 `202`，随机 flow 返回结构化 `404` |
| 公网静态资源 | 真实正式文章页与 `/static/js/489.js` | 通过；文章 `data-release` 为 job100，配对 UI 存在，最终 chunk 20511 bytes |

## 4. 实际部署结果

- 最终部署前生成双库可恢复备份：`/opt/ai-author-forum-test-backups/cross-device-email-20260819T153400+0800/`，文件 mode `0600` 且通过 `pg_restore --list`；default SHA-256 `d2943973996199d0109133c29837fb76879e96c188614287c9babaf6b00f7e46`，interactions SHA-256 `7ca6363f616ced9ca4a1154d50a6f42f9b1638228943ddcbe4e443e47b4374f2`
- Compose 四层配置 `config --quiet` 通过；新主应用、reader email/comments/PDF worker、static frontend 镜像构建成功。
- release 入口成功执行 default/interactions 迁移（均无待应用迁移）；collectstatic 为 `31 copied / 226 unmodified / 173 post-processed`。
- `build_static_site` 使用 `test-admin`，从当前正式文章目标解析 2392 个 canonical 文章路径，copy-on-write 展开为 3910 targets；job100 为 `3910/3910 succeeded`、`0 failed`，manifest `8877 files / 227185364 bytes`，并写入成功/激活审计。
- Web 两副本、static frontend、static publish、reader email、reader comments、reader PDF worker 均已更新；web/static frontend healthy。最终运行镜像 digest 记录在 `/opt/ai-author-forum-test-backups/cross-device-email-20260819T153400+0800/deployment-metadata.txt`，避免把包含本记录的镜像摘要反向写入自身构建输入。
- 回滚点已保留为 `ai-author-forum-test-rollback-main:cross-device-email-20260819t141800-0800`、`ai-author-forum-test-rollback-pdf:cross-device-email-20260819t141800-0800`、双轮双库备份和已验证 job99 release。

## 5. 已知限制

- 本地兼容路径允许旧客户端在提供昵称但不提供配对码时建立手机独立会话；该路径不会批准电脑流程。新验证页始终要求配对码。
- 已补充并通过双手机确认、双电脑领取的 PostgreSQL 并发测试；SQLite 本地运行时按环境跳过真实行锁测试。
- 已执行真实 PostgreSQL/Redis/Celery/Nginx 联合验收和线上桌面/移动 UI 验收；Playwright 线上验收拦截邮件投递，只验证页面/API 状态，不向真实邮箱发送测试邮件。
- 尚未用项目方指定的真实收件箱完成 SMTP 邮件到达、手机实际打开链接和最终评论发布；这是外部邮箱/人员验收项，不是部署阻塞。生产灰度前仍需用专用测试邮箱完成一次端到端人工验收。
