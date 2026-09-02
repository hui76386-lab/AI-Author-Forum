# 09 测试、部署与回滚

## 1. 测试策略

测试必须同时证明功能正确、权限隔离、发布完整性和故障下文章仍可读。只测试评论模型或单个 happy path 不足以发布。

### 1.1 单元测试

- 邮箱规范化/HMAC、challenge 过期/单次消费、session 撤销；
- effective policy 合并、版本冲突和禁用优先；
- 评论正文规范化、层级、状态迁移、撤回占位；
- 举报幂等和审核事件不可变；
- 三种角色与期刊对象范围、失效任命；
- presigned/internal grant 条件和安全文件名；
- outbox 消费幂等、投影版本拒绝倒退；
- database router 和跨库无 FK 约束。

### 1.2 集成测试

- PostgreSQL 行锁、唯一约束、并发评论/撤回/审核；
- Redis 原子限流和故障 fail-closed；
- Celery 重试、重复投递、队列隔离和 dead letter；
- 邮件 adapter 用 Mailpit/fake provider 验证中立响应和 token 不落日志；
- Wagtail policy service 同事务写 `AuditLog` 与 outbox；
- moderation command 跨库成功、超时、重复和对账；
- PDF renderer 断网、字体、资源失败、checksum 和 manifest 联合激活；
- S3 compatible presigned URL或 Nginx `X-Accel-Redirect` 的过期、Range 和越权。

SQLite 不作为并发、行锁、分区、router 或生产迁移的验收数据库。

### 1.3 E2E

Playwright 覆盖桌面和移动：

1. 匿名读文章，评论 API故障时正文仍可用；
2. 评论/下载/分享触发邮箱验证并安全返回；
3. 普通评论立即发布，高风险评论显示待审；
4. 回复、举报、本人撤回、非本人拒绝；
5. 评论区 open/read-only/hidden 三种状态；
6. 三类编辑操作本刊政策，跨刊 403；
7. PDF 下载、过期链接、禁用和 release 回滚；
8. Web Share 支持/不支持/取消/Clipboard 失败；
9. 键盘、焦点、屏幕阅读器名称、长昵称/长单词和小屏布局；
10. CSP 下无第三方脚本、无 mixed content、无 token Referer。

### 1.4 安全与负载

- CSRF、CORS、开放重定向、IDOR、XSS、路径穿越、host header、session fixation；
- 邮箱枚举时间差、邮件轰炸、验证码重放、链接扫描器 GET；
- 并发限流、缓存击穿、单文章热点、慢请求和大 body；
- comment read/write、verification、download grant 和 CDN cold-cache 独立/混合 profile；
- renderer SSRF、压缩炸弹/超大资源、进程超时和资源上限；
- 备份恢复、数据库故障、Redis 故障、队列积压和对象存储故障演练。

负载报告必须记录版本、环境、数据量、命中率、资源 limit、结果和饱和点，不只给平均响应时间。

## 2. 迁移方案

### 2.1 `ArticlePage.public_id`

采用 expand/contract：

1. 新增 nullable 字段和非唯一索引；
2. 分批、幂等回填 UUID，记录进度；
3. 检查 null/重复为零；
4. 建唯一约束（生产按数据库能力减少锁）；
5. 改为非空，先双读兼容旧静态 release；
6. 所有活动文章新 release 含 `data-article-id` 后再启用互动。

不得改变文章 review/publication status、approved revision、placement 或现有 manifest。

实现迁移固定为 `0016` nullable expand、`0017` 分批幂等回填、`0018` 零空值/
零重复校验并建立唯一约束、`0019` non-null contract。正式环境先应用至
`0017`，部署会为所有新文章写 UUID 的应用版本，再应用 `0018`/`0019`；两个
阶段都必须比较审核、投放和活动 manifest 的部署前后摘要。旧静态 release
没有 `data-article-id` 时仍可阅读，但所有读者互动开关继续关闭。

### 2.2 双数据库

先创建 `interactions` 数据库用户/库、迁移 job 和只用于该 alias 的 router。CI 分别执行：

```bash
python manage.py migrate --database=default --noinput
python manage.py migrate --database=interactions --noinput
python manage.py migrate --database=default --check
python manage.py migrate --database=interactions --check
python manage.py makemigrations --check --dry-run
```

正式迁移使用一次性 release job，固定先 `default` 后 `interactions`，任一步失败即
停止服务更新；Web/worker 副本启动不得迁移。`INTERACTIONS_DATABASE_URL` 在生产
必填且必须指向独立 PostgreSQL 数据库；开发默认使用独立
`interactions.sqlite3`。`ReaderInteractionsRouter` 只允许
`reader_interactions` 模型在该 alias 建表，控制面模型不能进入该库。

部署前分别生成可恢复备份，不得把两个数据库混为一个恢复点：

```bash
pg_dump --format=custom --file=backup-default.dump "$DATABASE_URL"
pg_dump --format=custom --file=backup-interactions.dump "$INTERACTIONS_DATABASE_URL"
pg_restore --list backup-default.dump >/dev/null
pg_restore --list backup-interactions.dump >/dev/null
```

恢复演练只能写入两个新建的隔离数据库，先恢复 `default`，再恢复
`interactions`，最后分别运行 `migrate --check` 和表边界检查；不得覆盖当前生产库：

```bash
pg_restore --clean --if-exists --no-owner --dbname="$DEFAULT_RESTORE_URL" backup-default.dump
pg_restore --clean --if-exists --no-owner --dbname="$INTERACTIONS_RESTORE_URL" backup-interactions.dump
DATABASE_URL="$DEFAULT_RESTORE_URL" INTERACTIONS_DATABASE_URL="$INTERACTIONS_RESTORE_URL" \
  python manage.py migrate --database=default --check
DATABASE_URL="$DEFAULT_RESTORE_URL" INTERACTIONS_DATABASE_URL="$INTERACTIONS_RESTORE_URL" \
  python manage.py migrate --database=interactions --check
```

## 3. 功能开关

至少提供服务端开关：

```text
READER_INTERACTIONS_ENABLED
READER_EMAIL_VERIFICATION_ENABLED
READER_COMMENTS_WRITE_ENABLED
READER_PDF_GRANTS_ENABLED
READER_SHARE_UI_ENABLED
READER_SNAPSHOT_READ_FALLBACK
```

开关默认关闭或 fail closed，通过配置和审计启用。前端 bundle 存在不等于功能开启；capability API 是最终运行时判断。应急开关不得改变 canonical 文章内容或 current manifest。

## 4. 部署产物

- 固定 digest 的 `web`、`reader-api`、各 worker 和 `pdf-renderer` 镜像；
- Webpack hashed assets 与静态资源 manifest；
- `default` 和 `interactions` 迁移计划及 dry-run 结果；
- 公共 static manifest 与配对 protected manifest；
- 配置 schema、secret 引用、SBOM、测试/压测报告；
- release id、Git SHA、镜像 digest 和数据库 migration version 记录。

## 5. 部署顺序

```text
构建并扫描镜像
  -> 备份/恢复点与容量检查
  -> expand migrations（default + interactions）
  -> collectstatic / 前端构建
  -> 部署 reader-api 和分队列 worker（功能开关关闭）
  -> /livez /readyz 与内部连通检查
  -> 构建新的公共 release + PDF + protected manifest
  -> 完整性校验
  -> 原子激活 current 与配对 protected manifest
  -> 小范围开启邮箱/评论/下载/分享
  -> 海外 synthetic、桌面/移动、日志和指标验证
  -> 扩大灰度
```

任何构建、迁移、产物校验或健康检查失败都不得激活新 release。激活、失败、重试和回滚继续写现有 `AuditLog`。

## 6. 兼容与灰度

- reader-api 至少兼容 N 和 N-1 静态前端契约；
- 数据库先 expand，旧代码可运行，再部署新代码；contract migration 延后至少一个完整回滚窗口；
- 首期按内部账号/期刊 allowlist 灰度，再按流量比例扩大；
- 邮件先用受控域和 sandbox，确认 SPF/DKIM/DMARC 与 bounce 处理后开放；
- PDF 先对少量文章构建，验证字体、大小和出网；
- 评论先只读快照，再开启写入和默认发布。

## 7. 健康检查

部署后必须验证：

- Django `/healthz/`、`/readyz/`；
- 静态服务 `/__static_health__/`；
- reader-api `/livez`、`/readyz`；
- 当前公共 manifest、protected manifest 和 capability projection release 一致；
- Nginx 普通文章由 `try_files/sendfile` 而非 Python 返回；
- 邮件 provider sandbox、评论创建/撤回、PDF grant 和对象 Range；
- CDN 主要海外 PoP 返回正确 release header/ETag；
- 日志中无 email/token/session/presigned URL。

## 8. 回滚

### 8.1 应用回滚

先关闭写入/授权开关，再将 reader-api/worker 回滚到兼容镜像。只回滚代码，不执行破坏性数据库 downgrade；expand schema 保留到新旧版本都退出回滚窗口。

### 8.2 静态与 PDF 回滚

只切换到已验证旧公共 manifest，并同时切换其配对 protected manifest。不能把新 PDF 挂到旧文章 release，也不能直接覆盖 `current`。投影消费者将 active release 回退，未匹配投影期间 PDF fail closed。

### 8.3 数据面故障

评论事件不因静态回滚删除。若新前端存在问题，关闭评论写入并保留公开快照；修复后产生新 release。错误审核使用新的补偿 moderation event，不更新/删除历史事件。

### 8.4 灾难恢复

分别定义 `default`、`interactions`、Redis session、对象存储和 manifest 的 RPO/RTO。恢复顺序以公共静态阅读优先，然后 canonical control plane、互动读、互动写、邮件和 PDF。每季度至少演练一次从备份恢复并验证 public/protected manifest checksum。

## 9. 每次变更的最低命令

按项目 `AGENTS.md`，实现阶段至少执行：

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
pytest <affected tests>
npm run build
npx playwright test <affected e2e>
```

发布前执行完整测试、生产配置检查、静态/受保护 manifest 校验和真实 PostgreSQL/Redis/Celery/Nginx 联合验收。命令、结果、版本和限制写入发布记录；任何未执行项必须明确说明。
