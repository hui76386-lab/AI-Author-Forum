# 10 实施计划与验收

## 1. 实施原则

按依赖顺序逐阶段交付，每阶段都可关闭、可观测、可回滚。不能先嵌入第三方评论框再补统一身份，也不能在 PDF 私有存储完成前把文件临时放到公开 `/media/`。

## 2. Phase 0：容量与发布基础

### 工作项

- 为 `ArticlePage` 增加并安全回填不可变 `public_id`；
- Nginx 改为直接 `try_files/sendfile` 服务活动静态 release；
- 将迁移从 Web 多副本启动命令收敛为一次性 release job；
- 建立 `reader-api`、独立数据库 alias、router、outbox 和分队列 worker 骨架；
- 建立 `/livez`、`/readyz`、结构化日志、指标和 trace 脱敏；
- 配置私有 PDF 存储、secret 管理和测试邮件 provider；
- 固定公共/受保护 manifest 配对契约和 feature flags。

### 完成标准

- 全部活动文章有唯一 UUID，slug 变更测试不影响关联；
- 互动服务关闭/故障时文章仍由 Nginx/CDN 可读；
- 两库迁移、备份恢复、路由隔离和 outbox 幂等测试通过；
- 未经授权无法访问私有对象或 internal Nginx location；
- 静态页面、CMS 和 worker 的资源池/队列互相隔离。

## 3. Phase 1：邮箱身份与能力门

### 工作项

- `ReaderIdentity`、challenge、session、加密/HMAC 和清理任务；
- 魔法链接申请、GET 确认页、POST 消费、昵称和注销；
- 中立响应、原子限流、CSRF/CSP、return URL allowlist；
- capability projection 与 control-plane outbox；
- 邮件 adapter、Mailpit、海外生产 provider 配置和投递指标；
- 文章底部组件 bootstrap、验证意图恢复和无 JS 降级。

### 完成标准

- 邮箱不可枚举，token 单次/过期/重放/扫描器场景通过；
- session 可撤销、不在 localStorage、Cookie 属性正确；
- 文章非活动或投影不一致时所有受控动作 fail closed；
- email/token/session 不出现在日志、trace、analytics；
- 主要海外区域邮件 synthetic 和延迟记录可用。

## 4. Phase 2：评论与审核

### 工作项

- Comment、report、moderation event、interaction outbox 和索引；
- 默认发布/风险前审、纯文本校验、两层回复、撤回占位；
- 评论列表 cursor/ETag、热点缓存和版本化 JSON 快照；
- Wagtail 审核列表、举报列表、批量操作和对象级 RBAC；
- 文章/期刊 comments policy、运行时 revocation 与静态挂载；
- 跨库 moderation command、AuditLog 镜像和 reconciliation；
- 安全、并发、移动/桌面 E2E 和负载测试。

### 完成标准

- 普通评论立即公开，高风险评论不对其他读者可见；
- 回复、举报、本人撤回、编辑隐藏/恢复和单篇关闭符合状态机；
- 三类编辑只治理本刊；旧 Group 和客户端参数无法越权；
- 单篇热点和缓存冷启动压测达到已批准安全余量；
- interaction DB/Redis/风控故障时按降级矩阵工作，文章不受影响。

## 5. Phase 3：PDF 与分享

### 工作项

- journal/article policy 与三类编辑管理界面；
- 固定 Chromium renderer、print template、资源封闭和产物校验；
- `ProtectedArtifact`/`ProtectedManifest` 与公共 release 联合激活；
- S3 presigned URL或 Nginx internal grant；
- system share、copy fallback 和最小化事件；
- PDF/分享的权限、安全、兼容、出网和回滚测试。

### 完成标准

- 只有已验证读者可通过站内流程获得当前活动 PDF；
- enabled/disabled/inherit 与三种编辑角色、跨刊拒绝正确；
- PDF 永不进入公共 media，Web worker 不传输文件 bytes；
- build 失败保留旧 current，回滚公共/受保护 manifest 一致；
- 分享不代发、不收接收者，浏览器不支持时可复制 URL；
- 下载和分享统计不夸大“grant”为“完成下载”。

## 6. Phase 4：上线与规模化

### 工作项

- 生产 CDN/WAF、邮件域、隐私声明、保留和主体请求流程；
- 正常/峰值/冷缓存/热点/故障混合压测与成本模型；
- allowlist 灰度、期刊灰度、错误预算和告警/runbook；
- 备份恢复、manifest 回滚、依赖故障和安全事件演练；
- 根据实际瓶颈启用 PgBouncer、Redis HA、评论 CDN 快照、读副本或独立 DB 集群。

### 完成标准

- 已批准 SLO、容量余量、RPO/RTO 和预算告警；
- 海外 synthetic、桌面/移动 E2E、完整测试集和安全评审通过；
- 值班人员能按 runbook 停写、停邮件、停下载、回滚并恢复；
- 发布记录包含 Git SHA、镜像 digest、migration、manifest、压测和已知限制。

## 7. 建议代码边界

```text
ai_author_forum/
  reader_access/             # default DB：policy、artifact、protected manifest、outbox
  reader_interactions/       # interactions DB：identity、comment、report、session、API
  reader_interactions_api/   # URL/schema/serializers（也可作为上一个 app 的 api 包）
  static_publish/            # 扩展冻结输入和联合 manifest，不改核心激活原则

frontend/
  reader-interactions/       # bootstrap、session gate、comments、share/download controls

templates/
  articles/article_page.html # 只加入 mount/data attributes，不实时查询评论
  reader_interactions/       # 邮箱确认和 PDF print template
```

是否把 API 作为独立 Django app 由实现阶段按现有工程风格决定，但 domain/service/API 依赖方向必须保持：API -> service -> model/outbox，模板和 hook 不直接写模型状态。

## 8. PR 切分建议

1. UUID 迁移 + 静态直出基线；
2. interaction DB/router/outbox/observability 骨架；
3. 邮箱验证和 session；
4. capability policy/projection；
5. 评论模型/API/快照；
6. moderation 后台/RBAC/audit；
7. PDF renderer/protected manifest/private storage；
8. 分享和统一前端；
9. 故障/负载/安全与运维收口。

每个 PR 都包含迁移（如有）、相关测试、文档、开关和部署验证。不要把全部功能作为一个不可回滚的大迁移发布。

## 9. 全功能 Definition of Done

- 本目录所有已确认需求均有代码、自动测试和操作手册对应；
- canonical 审核、投放、冻结构建、manifest 激活和回滚链路未被绕过；
- 三类编辑的下载/评论权限和跨刊隔离通过服务端测试；
- 静态文章在互动全故障下可用，动态能力按 fail-closed 降级；
- PII、token、session、presigned URL完成日志/trace/导出检查；
- 正式 PostgreSQL/Redis/Celery/Nginx/对象存储/邮件联合验收通过；
- 完整 pytest、前端 build、Playwright、负载、安全、恢复和回滚演练通过；
- 公共与 protected manifest、release、镜像和迁移可追溯；
- 产品、编辑代表、安全/隐私、运维共同签署上线验收。

## 10. 上线前仍需项目方提供

不再需要补充功能方向；实施可以按本文件启动。生产开放前还需提供部署参数：正式域名与主要用户地区、邮件发件域/供应商、对象存储/CDN、隐私主体和政策 URL、客服/滥用邮箱、保留周期、SLO/RPO/RTO、预算和应急联系人。这些参数不得由开发者猜测。
