# 07 安全、隐私与反滥用

## 1. 威胁模型

重点威胁包括：邮箱枚举和轰炸、魔法链接窃取/预取、session 劫持、CSRF、存储型 XSS、垃圾评论、自动化举报、下载链接批量抓取、越权管理其他期刊、对象 key 猜测、跨库投影过期、邮件/风控供应商泄露和高流量资源耗尽。

安全控制必须在 API/service/数据库约束中实施，不能依赖按钮隐藏、前端校验或邮件文案。

## 2. 邮箱验证

- 申请接口无论邮箱是否存在都返回相同 `202` 和近似时间；
- token 至少 256 bit 随机，库内只存带服务端 pepper 的 hash；
- 默认 15 分钟、单次使用、同用途新申请废弃旧 challenge；
- GET 链接不产生业务副作用，用户明确 POST 后消费；
- token 不写日志、analytics、Referer、错误追踪或客服工单；
- return URL只允许本站相对路径；
- 按 IP、邮箱 HMAC、设备风险、ASN/国家异常和全局速率分层限流；
- 邮件模板明确请求时间、用途、过期时间和“不是本人可忽略”。

邮件发送异步化，但 challenge 创建不以队列发送成功作为已送达。监控 provider accepted、delivered、bounced 和 complaint；前端只展示中立状态，避免枚举。

## 3. 会话与 CSRF

reader session Cookie：`HttpOnly; Secure; SameSite=Lax; Path=/reader-api/`，生产域不得设置过宽的 parent-domain cookie。登录/验证后轮换 secret，注销、封禁和风险事件可服务端撤销。

所有同源写请求使用 Django CSRF cookie/header 或等价双提交机制，并校验 `Origin`/`Host`。实现遵循 [Django CSRF 官方文档](https://docs.djangoproject.com/en/6.0/ref/csrf/)。CORS 默认关闭跨域凭据；未来多域名必须逐域 allowlist，不使用 `*`。

响应包含严格 CSP、`X-Content-Type-Options: nosniff`、`Referrer-Policy` 和合适的 frame policy。评论 bundle 自托管并进入静态 manifest，不从未知 CDN 执行脚本。

## 4. 评论内容安全

- 服务端按 Unicode code point/标准库规则校验长度，并限制总字节数；
- 拒绝 NUL、不允许的控制字符、异常超长单词/行和危险双向控制字符；
- 保存规范化纯文本，公开输出做上下文转义；
- 前端只使用 `textContent`，不解析 HTML/Markdown，不自动链接 URL；
- 昵称采用单独长度和字符策略，防冒充官方标识；
- 反垃圾标签、原始供应商响应和审核 note 不进入公共 API；
- 评论 API限制 JSON body 大小，Nginx 和 Django 双层执行。

XSS 测试必须覆盖标签、实体、SVG、`javascript:`、模板表达式、Unicode 双向字符和截断边界。

## 5. 原子限流与反滥用

当前项目已有的 cache get/set 型计数不适合并发限流。读者接口使用 Redis Lua、可靠原子命令或成熟限流组件，实现滑动窗口/token bucket，并在 Redis 不可用时按动作分级 fail closed。

建议初始维度，不把数值视为永久产品规则：

| 动作 | 维度 | 初始策略 |
| --- | --- | --- |
| 申请验证 | IP + email HMAC + 全局 | 短窗突发 + 日上限 + 冷却 |
| 消费验证 | challenge + IP | 尝试上限，成功即失效 |
| 发评论/回复 | reader + IP + article | 秒级间隔、分钟/日限额、重复检测 |
| 举报 | reader + IP + comment | 单人单评论一次，日上限 |
| 下载授权 | reader + IP + article | 突发与日配额，异常并发阻断 |
| 分享事件 | reader + article | 采样/合并，永不影响实际分享体验 |

确切阈值通过压测和上线数据调整。限流响应使用稳定 `429`、`Retry-After` 和 request id，不公开完整风控规则。

WAF 负责明显 bot、恶意 ASN、协议异常和大流量攻击；业务 API仍自行校验。代理链只信任明确的 ingress，不能直接接受客户端伪造 `X-Forwarded-For`。

## 6. 邮件与海外交付

生产使用目标用户地区覆盖良好的事务邮件供应商，接口封装为 provider adapter，至少有主/备或可快速切换方案。发件域配置 SPF、DKIM、DMARC，处理 bounce/complaint 并抑制无效地址。开发团队在国内时使用 Mailpit/console backend 和测试域，不把生产 API key 分发到本地。

供应商选择前完成：数据处理条款、数据驻留、跨境传输、日志保留、子处理者、退订不适用于纯事务邮件的确认，以及国内开发人员访问生产 PII 的审批。本文不替代法律意见。

## 7. 隐私设计

### 7.1 最小化

- 公开：昵称、评论正文、发布时间、状态占位；
- 私密：邮箱、reader id 关联、举报人、风控和必要安全数据；
- 不收集：通讯录、分享接收者、第三方账号、营销偏好、精确位置；
- IP 仅用于安全，在隔离存储中短期保留或使用带轮换密钥的前缀 HMAC；
- email lookup 使用 HMAC，邮箱正文用 KMS/信封加密，密钥可轮换。

### 7.2 建议保留基线

| 数据 | 建议默认 | 说明 |
| --- | --- | --- |
| 未消费 challenge | 过期后 24 小时清理 | 保留最少反滥用窗口 |
| session | 到期/撤销后 30 天清理安全元数据 | secret hash 可更早删除 |
| 原始邮件投递事件 | 30 至 90 天 | provider 侧也要配置 |
| 原始 IP/安全明细 | 最长 30 天 | 仅受限安全存储 |
| 分享/下载行为事件 | 90 天后聚合或删除 | 不建长期读者画像 |
| 评论/回复 | 依内容治理政策 | 删除请求可匿名化身份并保留公共讨论连续性 |
| 举报和审核事件 | 按争议/审计政策 | 后台受限，不公开 |
| 平台 AuditLog | 依项目正式审计政策 | 不写明文邮箱/token |

上线前由数据控制者、隐私负责人和目标地区法律要求确认最终周期，并写入公开隐私声明。

### 7.3 数据主体操作

提供验证邮箱后的导出、会话撤销、身份删除/匿名化和纠错流程。删除身份时，评论可变为匿名作者但保持撤回/审核证据；具体保留依据必须在隐私声明说明。所有导出文件短时私有下载，不通过普通邮件附件发送。

## 8. 密钥和供应链

- 邮件、KMS、对象存储和内部 API凭据只从 secret manager/环境注入，不进仓库、镜像或 manifest；
- 密钥有 owner、用途、轮换和撤销手册；
- 容器使用固定 digest、最小权限用户、只读根文件系统（可行时）和独立临时目录；
- Chromium renderer 禁止访问云 metadata 和公网，设置 CPU、内存、进程数和执行超时；
- 依赖锁定、SCA、镜像扫描和 SBOM 进入发布门禁。

## 9. 安全事件处置

预置开关：全局停评论写入、全局停验证邮件、全局停 PDF grant、按 reader/session/email HMAC 封禁、按 article 隐藏评论。开关默认 fail closed，变更有审计和自动过期/人工解除策略。

告警后保留 request/event id、最小必要安全证据和时间线。不要在应急群或工单粘贴明文 token、完整邮箱列表或 presigned URL。
