# 00 技术选型与 ADR

## 1. 决策

采用项目内自建的 Django/Wagtail 读者互动模块，复用现有 PostgreSQL、Redis、Celery、Webpack、Nginx/CDN 和 `JournalEditorAssignment`。模块按“同仓库、清晰边界、可独立扩容”建设：控制面集成 Wagtail，动态 API 和 worker 可独立部署。

不采用 Twikoo、Artalk、Waline 或 Coral 作为核心后端。它们保留为未来独立站点或迁移场景的参考，不在第一阶段同时运行双写。

## 2. 选择标准

本项目的关键需求不是单独增加一个评论框，而是建立统一读者资格：同一个已验证邮箱要同时控制评论、分享入口和 PDF 下载，并受文章发布状态、期刊角色、单篇政策、审计和静态 manifest 约束。因此选型权重为：

1. 与 canonical `ArticlePage`、不可变 release 和回滚契约的一致性；
2. 一个读者身份覆盖三个能力，不建立第二套账号；
3. 期刊级对象权限和现有三类编辑任命；
4. 海外访问、邮件送达、隐私和反滥用；
5. 高流量下独立扩容、缓存、分区和降级；
6. 总拥有成本、运维复杂度和供应商锁定。

## 3. 方案比较

| 方案 | 部署/数据 | 身份与审核 | 扩展与运维 | 项目结论 |
| --- | --- | --- | --- | --- |
| Twikoo | 私有 Docker；默认 LokiJS，可接 MongoDB | 昵称/邮箱/网址字段可配置，有管理面板、反垃圾和审核 | 上手最快，但默认本地库更适合轻量站点；需维护第二套数据和管理员 | 适合个人站/轻量评论，不作为本项目核心 |
| Artalk | Go 服务/Docker；SQLite、MySQL、PostgreSQL、SQL Server；Redis/Memcache 可选 | 管理员多站点、邮件、社交登录、验证码和评论审核 | 性能与功能较完整，但仍要同步第二套身份、站点范围和权限 | 独立评论服务的最佳备选，不选作核心 |
| Waline | Node 服务；支持 PostgreSQL/MySQL/SQLite/MongoDB 等适配 | 可强制登录，支持评论全量审核、反垃圾和 IP 频率限制 | 部署灵活，但注册/登录和 Wagtail 读者资格重复 | 不选 |
| Coral | Node + MongoDB + Redis，官方提供 Docker | 面向媒体的账号、SSO、复杂审核和管理后台 | 规模化能力强，但引入 MongoDB/Node 运维面，首期明显过重 | 超大型媒体联盟场景再评估 |
| 项目内自建 | Django/Wagtail + PostgreSQL + Redis + Celery，与当前仓库一致 | 一个邮箱资格覆盖评论、分享、PDF；直接复用期刊 RBAC 和审计 | 首期开发量较大，但 API/DB/worker 可独立扩容，数据边界完全可控 | 选择 |

许可、版本支持和数据导出能力必须在真正引入第三方时按拟锁定版本重新做法务和迁移核验；本次没有把“当前官网功能”当成未来永久兼容承诺。

官方资料核验入口：

- [Twikoo 常见问题](https://twikoo.js.org/faq.html)
- [Artalk 后端配置](https://artalk.js.org/en/guide/backend/config.html)
- [Waline 服务端环境变量](https://waline.js.org/en/reference/server/env.html)
- [Coral 安装与依赖](https://docs.coralproject.net/)

## 4. 目标技术栈

| 层 | 选择 | 说明 |
| --- | --- | --- |
| CMS 控制面 | Django 6 + Wagtail 7 | 延续当前项目，承载政策配置、期刊范围权限和审核后台 |
| 动态 API | Django ASGI/WSGI 无状态服务 | 首期复用项目语言和安全组件；以独立 `reader-api` 进程部署 |
| 前端 | 当前 Webpack/SASS/JavaScript | 评论组件懒加载并进入静态资源 manifest，不从第三方 CDN 载入脚本 |
| 业务数据 | PostgreSQL | 正式关系数据、约束、幂等键、审核历史；高量事件按时间分区 |
| 短期状态 | Redis | 会话热缓存/撤销、单次 nonce、原子限流、能力撤销和热点评论；会话事实仍落 PostgreSQL |
| 异步任务 | Celery | 邮件、风控、评论快照、PDF、通知；队列按工作负载隔离 |
| PDF | 固定版本 Chromium/Playwright | 用专用 `pdf-renderer` 镜像从冻结打印模板生成 PDF |
| 私有文件 | 私有 S3 兼容对象存储或 Nginx internal | 短时授权下载；不得放入公开 `/media/` |
| 边缘 | 海外 CDN + WAF + Nginx | 静态文章和评论快照边缘缓存，API 做限流和防护 |
| 可观测性 | OpenTelemetry + 指标/日志/追踪后端 | API、队列、邮件、PDF、缓存、数据库和 CDN 统一关联 request id |

## 5. 部署形态决策

首期保持模块化单体代码库，避免过早拆成多个独立仓库；运行时至少分开：

- `web`：现有 Wagtail 控制面；
- `reader-api`：无状态读者接口，独立 worker 数和连接池；
- `worker-email`、`worker-comments`、`worker-pdf`、`worker-static-publish`：独立队列和资源限制；
- `pdf-renderer`：包含固定 Chromium 的隔离镜像；
- `nginx`：直接服务静态 release，并反向代理 `/reader-api/`。

动态互动数据从第一天只通过稳定的 `article_public_id` 与 CMS 对接。这样可以先运行在同一 PostgreSQL 集群的独立逻辑库，流量增长后迁移到独立集群而不改公开 API。

## 6. 被否决的实现

- 不把评论写进静态 HTML release；否则每条评论都会触发文章 release，破坏不可变发布和成本边界。
- 不用邮箱地址直接作为公开昵称；邮箱始终是私密数据。
- 不使用长期 JWT 作为普通读者会话；短期 opaque session 更易撤销。
- 不把 PDF 放入 `/media/` 或公共 CDN 源站。
- 不让静态文章请求实时查询 Django 数据库。
- 不在浏览器中保存第三方评论系统管理员密钥。

## 7. 重新评估条件

当出现跨多个独立媒体域统一评论、专职审核团队需要复杂工作流、日评论量持续达到现架构压测上限，或自建维护成本连续两个季度高于成熟方案的总拥有成本时，可重新比较 Coral 或托管评论平台。重新选型必须先验证统一身份、数据导出、期刊 RBAC、审计和回滚接口，不能只比较评论框功能。
