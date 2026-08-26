# Reader Interactions RI-09 工程验收与部署记录

## 1. 结果

结果：`blocked`。RI-09 的代码、自动测试、测试环境部署、容量、安全、故障和恢复演练已完成；阶段完成标准中的生产 SLO/RPO/RTO/预算批准和主要海外区域 synthetic 尚无项目方输入。严格按实施规范第 23 节停止在 RI-09，不进入 RI-10。

目标环境：`ai-author-forum-test`，本机入口 `127.0.0.1:18080`。Git HEAD 为 `b6555c3ef5804c10458720889a62431bf0503f43`；工作区包含 RI-00 至 RI-09 未提交变更，因此运行镜像 digest 是本次部署的权威代码标识。

## 2. 实现

- WAF：reader API `20 r/s`、验证 `6 r/m`，分别带 40/3 burst；每 IP 连接限制、64 KiB body、2 秒连接和 15 秒读写超时；敏感下载/metrics 不写 access log；reader/protected 编码路径穿越在 URI 规范化前返回 400；
- 应用限流：评论 10 秒最小间隔、20/小时、100/日、单篇 10/小时、IP 60/小时；举报 20/日、IP 100/日；下载 IP 20/小时。维度只持久化 HMAC fingerprint；Redis/Lua 不可用时敏感写 fail closed；
- 观测：JSON 日志含 service/environment/release/request/event/route/status/duration/error category；脱敏邮箱、Bearer、Cookie、session、下载 token 和签名 URL。指标使用稳定 route 和有限状态 label，覆盖 HTTP、DB/Redis、连接上限、队列、评论/审核/快照、PDF 和 release 一致性；
- 部署：2 个 web 副本，每个 2 Gunicorn workers、1 CPU、1 GiB、256 pids；邮件、评论、PDF、静态发布队列隔离；PDF worker 1 CPU/1 GiB/256 pids；
- 运维：新增 synthetic、容量阶梯、Prometheus 告警和 `reader-interactions-ri09-runbook.zh-CN.md`。值班可用 fail-closed 开关停全部互动、邮件、评论、PDF grant、分享，并按不可变 public/protected pair 回滚。

## 3. 验证

- `.venv/bin/python manage.py check`：0 issues；
- `.venv/bin/python manage.py makemigrations --check --dry-run`：No changes detected；
- `.venv/bin/python -m pytest -q`：919 passed、12 skipped、259 warnings、410 subtests，429.51 秒；skipped 为 SQLite 下 PostgreSQL 锁验收和专用 PDF worker 场景，对应真实 PostgreSQL/PDF 专项已执行；
- reader/access/WAF 扩大回归：130 passed、9 skipped、21 warnings、8 subtests；最终修复专项分别为 17、28、25 passed；
- 真实 PostgreSQL 并发最终回归：17 passed、3 subtests；真实 Redis Lua/锁：原子限流成功，32 contenders 的 single-flight winners=1；
- `npm run build`：Webpack 成功；`npx playwright test`：35 passed、2 skipped；
- Compose 四层配置 `config --quiet`：通过；`nginx:1.27-alpine nginx -t`：通过，仅有既存 duplicate `text/html` MIME warning；
- synthetic：文章/全文/canonical/Nginx 直出、job96 release、匿名 session、关闭态 capability、零发送验证页、protected 直访拒绝全部为 true；metrics 无 token为 403，带 service token 为 200 且无高基数/PII。

## 4. 容量证据

测试容器可见 8 CPU，job96 manifest 为 8867 files。静态阈值为 p95 100ms，API/混合阈值为 p95 250ms，错误率阈值 1%。下表的 50%/70% 是测试环境候选安全值，不是生产承诺。

| Profile | 首次饱和 | 饱和 RPS | p95 / 错误率 | 50% 持续 RPS | 70% 峰值 RPS |
| --- | ---: | ---: | ---: | ---: | ---: |
| 单篇热点静态 | concurrency 128 | 1053.073 | 185.701ms / 0% | 526.537 | 737.151 |
| 1000 路径冷读 | concurrency 128 | 1109.811 | 176.352ms / 0% | 554.905 | 776.868 |
| 2 副本 reader-api session | concurrency 128 | 325.184 | 402.860ms / 0% | 162.592 | 227.629 |
| Nginx/WAF 混合 | concurrency 16 | 992.618 | 52.684ms / 10.9375% | 496.309 | 694.833 |

WAF 混合项的饱和由 429 产生，表示单来源防滥用边界，不可作为多地区总流量预算。正式容量必须用项目方指定的主要海外区域、CDN 命中率、生产数据规模和成本重测。

## 5. 安全、故障与恢复

- 邮件轰炸：10 次突发结果为 `403 403 403 403 429 429 429 429 429 429`；评论无 CSRF 为 403；日志无 canary 邮箱、脚本正文、Bearer/session/签名 URL；
- 路径穿越：修复前发现 protected 编码 `..` 被规范化到公开 manifest；修复后 protected/download 编码穿越均为 400，protected 正常直访仍为 404；
- PostgreSQL pause：静态文章 200、`X-Static-Served-By=nginx` 且 SHA 不变，readyz 为 500，unpause 后为 200；
- Redis pause：初测发现 readiness 超过 20 秒悬挂；增加 Kombu socket connect/read timeout 后复测，静态 SHA 不变，readyz 6 秒内返回 503，恢复后为 200；
- 私有存储：`/proc` 不可写目标立即 `FileNotFoundError`，没有回退到 Web 传输；慢 SMTP 在 516ms `SMTPServerDisconnected`，任务失败重试/脱敏另有自动测试；
- 备份目录 `/opt/ai-author-forum-test-backups/ri09-20260817T124100-0800`，文件 mode 0600。default dump 37,531,554 bytes，SHA-256 `471f42780b2ba221264cdc12113962b486c0cf48a6f7c7c790a8e54e74d6c98a`；interactions dump 77,512 bytes，SHA-256 `1ed2dcf85a001a810795ae3c14b2c831cbd4433329b76dade606c84677f45762`；
- 两份 dump 均通过 PostgreSQL 17 `pg_restore --list`，并恢复到隔离临时库。default 源/恢复均为 `310 migrations | 1217 articles | 1499 placements | 6385 audits | 1 active manifest`；interactions 均为 `310 | 0 identities | 0 comments | 0 actions`；临时库已删除且零残留。

## 6. 部署与回滚

- 最终应用版本：`ri09-20260817T125905-0800`；主镜像 `sha256:79bc98379f666888e7609409a778e19f824c93f135f2d830da0b3f5908c60c36`；PDF 镜像 `sha256:4a1f5c115e00a8fe4bfb16fb439d5cfd73ce4c767338b4f14d704c2fd1892726`；
- release job：default/interactions 均无待迁移，collectstatic 0 copied、257 unmodified、173 post-processed；2 个 web 和 static frontend 均 healthy；
- RI-09 没有修改正式静态页面输入，未创建或激活新静态 release。活动 public manifest 保持 `20260817T034009821410Z-job96`、8867 files、manifest SHA-256 `106030e6c5fa68426424f7761cdc87fd451e9e356bfdb47088e27a1d8c1ff709`，因此没有绕过 canonical/审核/投放/manifest 审计链；
- 回滚点：主镜像 `ai-author-forum-test-ri08-complete:20260817T115902-0800`（`sha256:9ef2921e...`）、PDF 镜像 `ai-author-forum-test-ri08-pdf-complete:20260817T115902-0800`（`sha256:4a78eebf...`）、上述双库备份和未变更的 job96 current。

## 7. 阻塞与生产门禁

当前测试环境五个 reader flag 全为 false；互动数据库与 default 使用同一用户 `ai_author_forum`，角色 `rolconnlimit=-1`。生产中间件会在总开关启用时拒绝共享用户或无正连接上限，因此本环境没有冒充 Launch 就绪。

项目方必须提供并批准：正式域名和主要海外区域、事务邮件供应商/发件域、私有对象存储/CDN、隐私主体/政策 URL、客服/滥用邮箱、最终保留周期、SLO/RPO/RTO、持续/峰值与成本预算、值班 owner 和应急联系人。随后还需接入并验证 CDN/邮件/对象存储/成本 exporter，执行海外 synthetic、真实 provider 联合故障和签字。以上完成前 RI-09 不标记 complete，RI-10 不启动。
