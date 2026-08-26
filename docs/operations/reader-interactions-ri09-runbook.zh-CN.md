# Reader Interactions RI-09 值班、降级与恢复手册

## 1. 适用范围

本手册用于 reader-api、互动数据库、Redis、邮件、评论快照、PDF、对象存储和静态 release。普通文章由 Nginx `current` 静态目录提供；任何互动处置不得修改 canonical 文章、绕过审核/投放或原地修改 manifest。

处置前记录时间、告警、环境、应用镜像、活动 public/protected manifest、最近变更和操作者。工单不得粘贴邮箱、token、Cookie、评论正文或 presigned URL。

## 2. 首轮确认

```bash
docker compose -p ai-author-forum-test --env-file /path/to/environment \
  -f docker-compose.production.yml -f docker-compose.local-middleware.yml \
  -f docker-compose.preproduction.yml -f docker-compose.test.yml ps
curl -fsS -H 'X-Forwarded-Proto: https' https://SITE/healthz/
curl -fsS -H 'X-Forwarded-Proto: https' https://SITE/readyz/
curl -fsS https://SITE/__static_health__/
curl -fsS -H "Authorization: Bearer $READER_INTERNAL_SERVICE_TOKEN" \
  https://SITE/reader-api/internal/v1/metrics/
```

确认活动 manifest 的数据库记录、磁盘 `current/manifest.json` 和页面 `data-release` 相同。`livez` 只代表进程存活，不能替代 `readyz`。

## 3. 应急开关

在环境的受控配置源中修改，记录变更审批和审计，然后只更新应用服务：

```text
停全部互动：READER_INTERACTIONS_ENABLED=false
停新验证邮件：READER_EMAIL_VERIFICATION_ENABLED=false
停评论/回复/举报：READER_COMMENTS_WRITE_ENABLED=false
停新 PDF grant：READER_PDF_GRANTS_ENABLED=false
停站内分享入口：READER_SHARE_UI_ENABLED=false
```

```bash
docker compose -p PROJECT --env-file /path/to/environment \
  -f docker-compose.production.yml -f docker-compose.local-middleware.yml \
  -f docker-compose.preproduction.yml -f docker-compose.test.yml \
  up -d --no-build --scale web=2 web reader-email-worker \
  reader-comments-worker reader-pdf-worker nginx
```

关闭开关不得删除评论、session、artifact 或事件。恢复前先确认依赖健康、积压可控和投影 release 一致，再按邮箱、只读评论、评论写、PDF、分享的顺序逐项开启。

## 4. Reader API

当 5xx 或 p95/p99 超预算时，先停止写入和 grant，保留静态文章。检查两 web 实例、数据库连接、Redis、队列和最近发布。不得通过取消限流或让敏感写在 Redis 故障时放行来恢复表面可用性。

若回滚应用，先关闭开关，再把 Compose service 标签恢复到已验证 digest；运行 release job 只做向前兼容迁移检查和 collectstatic，不执行破坏性 downgrade。健康检查通过后按最小 allowlist 重开。

## 5. 依赖故障

### 互动数据库

保持静态文章和已生成评论快照可读，reader 写入、验证消费和下载拒绝。检查连接上限、锁、statement timeout、磁盘和最近迁移。恢复后先执行双库 `migrate --check` 与投影对账，再恢复写入。

### Redis

保持文章和快照读取；验证消费、评论/举报和下载 grant 必须 fail closed。检查内存、eviction、延迟、持久化和网络。不得临时退回进程内计数器。

### 邮件

设置 `READER_EMAIL_VERIFICATION_ENABLED=false` 可停止新邮件；已有 reader session 继续使用。检查 `reader_email` oldest age、retry 和 provider accepted/bounce/complaint；切备用供应商前核对数据处理与发件域配置，避免重复发送。

## 6. 队列积压

四个队列必须隔离。先确认 oldest age 和失败类别，再按同一镜像扩对应 worker；不得让 PDF 或邮件任务进入 static_publish/reader_comments 队列。缩容前确认任务 ack 和幂等状态，禁止直接清空 Redis list。无法处理的任务保留事件/command id 并走受控重试或对账。

## 7. 审核积压与评论快照

待审积压由本刊三类有效编辑处理，不能授予旧 Group 或全局 permission 旁路。快照延迟时保留写响应的最终一致性说明；运行内部 snapshot rebuild 必须使用服务凭据和明确 article UUID，不能触发 `build_static_site`。

## 8. PDF 与对象存储

renderer 故障时旧活动 PDF 可用，新 release 不激活。对象存储故障时关闭 grant，不让 Web/Python 代理文件 bytes。恢复时校验 object key、大小、SHA-256、approved revision、public/protected release 后再启用。

直接 `/_protected_pdf/` 必须保持 404；授权传输由 Nginx internal location 执行，HEAD/Range 不经过 Python 发送文件。

## 9. 静态与 protected 成对回滚

只允许回滚到数据库中已有、文件完整且 checksum 通过的不可变版本。先关闭评论写入和 PDF grant，然后执行：

```bash
python manage.py build_static_site \
  --rollback VERIFIED_VERSION \
  --rollback-reason 'incident rollback reason' \
  --actor OPERATOR_USERNAME
```

回滚服务会校验 public/protected pair、原子切换 `current` 并写 AuditLog。禁止删除旧 `current`、覆盖 manifest 或把数据库文章静默回滚。完成后核对页面 release、capability projection、protected manifest 和四个健康端点。

## 10. 备份恢复

default 与 interactions 使用独立 custom-format dump 和独立恢复目标。恢复演练不得覆盖运行库。顺序：静态阅读、default canonical 控制面、互动读、互动写、邮件、PDF。

```bash
pg_restore --list backup-default.dump >/dev/null
pg_restore --list backup-interactions.dump >/dev/null
pg_restore --no-owner --no-privileges --dbname="$DEFAULT_RESTORE_URL" backup-default.dump
pg_restore --no-owner --no-privileges --dbname="$INTERACTIONS_RESTORE_URL" backup-interactions.dump
DATABASE_URL="$DEFAULT_RESTORE_URL" python manage.py migrate --database=default --check
INTERACTIONS_DATABASE_URL="$INTERACTIONS_RESTORE_URL" \
  python manage.py migrate --database=interactions --check
```

核对文章/投放/AuditLog、活动 manifest、reader/comment/event 数量和 checksum 后才可提出恢复审批。临时库只能按已解析的明确名称删除。

## 11. 安全事件

邮件轰炸、IDOR、XSS、CSRF、路径穿越或凭据疑似泄露时，先关闭相应写开关并保留 request/event/command id 与最小证据。轮换 secret 时按双 key 解密窗口执行；撤销 session 和 token，不在应急群传播原值。WAF 封禁不能替代 API 服务端对象权限和幂等检查。

## 12. 恢复完成标准

- `/livez/`、`/healthz/`、`/readyz/`、`/__static_health__/` 符合语义；
- 静态文章由 Nginx 直出，互动故障不影响正文/canonical；
- public/protected manifest 与 capability release 一致；
- 数据库迁移、Redis 原子限流、队列 oldest age 和错误率恢复；
- 日志抽检不含 PII/bearer；
- 开关、回滚、失败和重试均有审计；
- 事件时间线、根因、恢复点和后续行动已记录。

## 13. 生产审批参数

下列值必须由项目方在 RI-10 前签署，不能由开发或压测结果自动批准：正式域名、主要海外区域、静态/API SLO、default/interactions/对象存储 RPO/RTO、持续与峰值流量预算、成本预算、保留周期、值班 owner 和应急联系人。RI-09 报告提供实测候选值和 50%/70%容量，签署后的数值应进入正式监控配置。
