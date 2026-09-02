# 04 API 与前端集成

> 实施状态（2026-08-17）：RI-08 已完成统一 session gate、分享/复制、PDF 下载入口、验证意图恢复、本地动态 bundle、CSP 和响应式/无 JavaScript 降级；分享事件与前端验收记录见 `docs/operations/reader-interactions-ri08-20260817T1121+0800.zh-CN.md`。

## 1. API 约定

- 同源前缀：`/reader-api/v1/`；
- JSON 使用 UTF-8，时间为 RFC 3339 UTC；
- 写请求必须带 CSRF token、有效 reader session（验证申请/消费除外）和 `Idempotency-Key`；
- 响应包含 `X-Request-ID`，资源返回整数 `version`；
- 列表使用 opaque cursor，不使用 page number/offset；
- API 最少兼容当前和上一活动静态 release 所需契约；
- 不把邮箱、token 或签名 URL写入访问日志。

成功包络：

```json
{
  "data": {},
  "request_id": "01J..."
}
```

错误包络：

```json
{
  "error": {
    "code": "comments_closed",
    "message": "Comments are closed for this article.",
    "field_errors": {}
  },
  "request_id": "01J..."
}
```

## 2. 身份与会话接口

| 方法与路径 | 说明 | 关键行为 |
| --- | --- | --- |
| `GET /session` | 当前 reader session | 匿名返回 `authenticated=false`，不返回 401 |
| `POST /email-verifications` | 申请魔法链接 | 始终通用 `202`，防邮箱枚举 |
| `GET /email-verifications/{challenge}` | 验证链接展示 | 只校验可展示状态，不消费 token |
| `POST /email-verifications/{challenge}/consume` | 明确确认 | 单次消费，创建/更新身份和 session |
| `PATCH /session/profile` | 设置公开昵称 | 昵称必填，expected version |
| `POST /session/logout` | 撤销当前会话 | 幂等，清 Cookie |

申请示例：

```json
{
  "email": "reader@example.org",
  "return_to": "/articles/example/",
  "intent": "download"
}
```

`return_to` 只允许本站相对路径并在服务端 allowlist 校验，禁止开放重定向。验证确认页设置 `Referrer-Policy: no-referrer`，消费后用 `history.replaceState` 清除 URL token。

## 3. 能力接口

`GET /articles/{article_public_id}/capabilities` 返回活动 release、评论有效模式、PDF 是否已就绪及读者是否需要验证。此接口可短缓存，但 `download enabled=false` 和评论关闭必须通过 revocation version 立即覆盖缓存。

示例：

```json
{
  "data": {
    "article_id": "56b0...",
    "release": "20260814T120000Z-abcd1234",
    "comments": {"mode": "open", "can_create": true},
    "download": {"format": "pdf", "available": true, "can_request": true},
    "share": {"can_use_ui": true}
  },
  "request_id": "01J..."
}
```

匿名响应中的 `can_create`、`can_request` 和 `can_use_ui` 为 false，同时返回 `verification_required=true`。

## 4. 评论接口

RI-05 已实现本节评论接口与静态文章挂载点；实现/验收记录见 `docs/operations/reader-interactions-ri05-20260817T0555+0800.zh-CN.md`。

| 方法与路径 | 说明 |
| --- | --- |
| `GET /articles/{id}/comments?cursor=&limit=` | 已公开评论树的稳定游标分页 |
| `POST /articles/{id}/comments` | 创建根评论 |
| `POST /articles/{id}/comments/{comment_id}/replies` | 创建一层回复 |
| `POST /articles/{id}/comments/{comment_id}/withdrawal` | 作者撤回本人评论 |
| `POST /articles/{id}/comments/{comment_id}/reports` | 举报评论 |

创建请求：

```json
{
  "body": "A concise response to the article.",
  "expected_policy_version": 7
}
```

创建响应必须区分：

- `201 + state=published`：已公开；
- `202 + state=pending`：只在本人界面显示等待审核；
- `409 comments_closed`：提交期间政策已关闭；
- `409 stale_policy`：客户端能力版本过期，应刷新；
- `422 invalid_comment`：长度、字符或结构不合法；
- `429 rate_limited`：带 `Retry-After`。

撤回请求包含 `expected_version`，重复撤回返回同一终态。举报请求包含枚举 `reason`：`spam`、`harassment`、`hate`、`privacy`、`misinformation`、`other`，并允许受限长度的说明。

GET 评论使用 `ETag`/`If-None-Match`。根评论按产品确定的稳定规则排序，首期默认 `created_at ASC, public_id ASC`；回复只随所属根评论返回。任何排序调整都要版本化，避免游标重复或丢失。

## 5. 下载与分享接口

RI-07 已完成下载授权和私有传输；RI-08 已完成分享事件、统一前端入口和验证恢复。

| 方法与路径 | 说明 |
| --- | --- |
| `POST /articles/{id}/download-grants` | 校验身份、政策和活动产物，签发短时下载 |
| `POST /articles/{id}/share-events` | 在系统分享/复制完成后记录最小事件 |

下载响应对对象存储返回 5 分钟 presigned URL；本地/共享存储返回一次性站内 URL，由 Nginx `internal`/`X-Accel-Redirect` 传输。响应设置 `Cache-Control: no-store`，不得把签名 URL放进 analytics。

分享事件只接受 `system_share` 或 `copy_link` 及 `outcome=completed|cancelled|failed`，不接收接收者、目标账号或用户输入消息。浏览器系统分享必须在用户手势内调用；前端不能为了先等待埋点请求而丢失 transient activation，事件在分享完成后异步发送。

## 6. 后台内部接口

控制面到数据面的内部接口不暴露公网：

- `PUT /internal/v1/article-capabilities/{id}`：按 projection version 幂等应用；
- `POST /internal/v1/moderation-commands`：隐藏、恢复、通过、拒绝、标记垃圾；
- `GET /internal/v1/moderation-commands/{command_id}`：审计对账；
- `POST /internal/v1/comment-snapshots/rebuild`：受控重建；
- `GET /internal/v1/readyz`：依赖就绪。

使用 mTLS 或私网加短期服务凭据，凭据有明确 audience 和最小权限。内部接口同样校验 journal/article scope、幂等键和 expected version，不能信任前端传来的角色名。

## 7. 静态文章挂载

静态模板在文章底部输出无 PII 的挂载点：

```html
<section
  id="reader-interactions"
  data-article-id="56b0d9b2-..."
  data-release="20260814T120000Z-abcd1234"
  data-locale="en"
  aria-labelledby="reader-interactions-heading"
>
  <h2 id="reader-interactions-heading">Comments</h2>
  <div data-reader-interactions-root></div>
</section>
```

页面从项目自身静态资源清单加载一个小型 bootstrap。组件在接近视口时动态加载评论 bundle；下载和分享按钮可以先渲染占位，但在 capability 返回前不得显示为可操作。JavaScript 失败时文章正文和 canonical URL 完整可用。

## 8. 前端行为

### 8.1 验证恢复

点击受控动作时保存安全的 `intent` 和相对 `return_to`，验证后恢复到文章底部。原评论草稿只保存在 sessionStorage，设置短 TTL，验证完成后由用户再次确认提交；不得在 GET 回调中自动发布评论或自动下载。

当前实现使用 15 分钟 TTL；分享和复制同样只恢复焦点并要求读者再次点击，不会在验证回调中自动打开系统面板或写入剪贴板。sessionStorage 不可用时安全降级为不保留 intent/草稿。

### 8.2 评论渲染

只用 `textContent` 或框架等价安全 API渲染正文并保留换行；不使用 `innerHTML`。URL 字符串不自动变链接。撤回/隐藏节点显示状态占位，不把原文留在 DOM、data attribute 或客户端状态中。

### 8.3 可访问性与响应式

- 表单标签、错误摘要、字符计数和审核状态可被屏幕阅读器读取；
- 新评论成功或进入待审使用 `aria-live=polite`；
- 回复表单打开后焦点移动到编辑框，关闭后回到触发按钮；
- 触控目标至少 44x44 CSS px，移动端不出现水平滚动；
- 系统分享、复制、举报、撤回均有明确成功/失败状态，不能只靠颜色。

## 9. 核心错误码

`authentication_required`、`email_verification_required`、`session_expired`、`reader_suspended`、`article_not_active`、`comments_closed`、`comments_hidden`、`download_disabled`、`artifact_not_ready`、`stale_policy`、`stale_version`、`invalid_comment`、`reply_depth_exceeded`、`not_comment_owner`、`already_reported`、`rate_limited`、`risk_challenge_required`、`service_degraded`。

错误文案可本地化，客户端只根据稳定 code 分支，不能解析英文 message。
