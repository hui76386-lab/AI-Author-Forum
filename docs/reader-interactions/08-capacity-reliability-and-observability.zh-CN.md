# 08 容量、可靠性与可观测性

## 1. 容量原则

当前没有访问量、评论率、PDF 大小或地区分布数据，因此不能给出可信的“支持 X QPS”承诺。工程目标是先消除架构单点，再通过可重复压测测出每个版本的饱和点，并把生产稳定负载控制在已测容量的安全区间。

核心隔离目标：

- 文章静态阅读不消耗 Django/数据库连接；
- 评论读取尽量命中 CDN/Redis，写入走独立连接池；
- PDF 字节由对象存储/Nginx 发送，不占 Web worker；
- 邮件、风控、PDF、静态发布队列互不阻塞；
- 互动流量过载时不影响 Wagtail 审核和 current 静态站点。

## 2. 首个 P0：静态直出

当前 Nginx 把 `/` 代理到 Python `static-frontend`，后者每次读取 manifest 并 `read_bytes()`。高流量上线前必须改为：

```text
CDN cache -> Nginx try_files /srv/published/current -> sendfile
                                    |
                                    +-> 小型 redirect/miss fallback
```

激活过程仍使用原子目录切换。Nginx 挂载 read-only `published-data`，HTML 使用短 TTL + stale 策略，带 hash 的 CSS/JS/图片使用长期 immutable 缓存。CDN purge/版本切换必须与 manifest 激活绑定，不能先清旧 current。

## 3. 容量模型

每次压测前用以下输入生成 profile：

```text
page_requests = active_users * pages_per_user / session_duration
origin_static_rps = page_rps * (1 - CDN_hit_ratio)
comment_read_rps = article_views * comment_open_rate * (1 - snapshot_cache_hit)
comment_write_rps = verified_readers * comment_rate
verification_jobs = gated_action_users * verification_required_ratio
pdf_egress = successful_grants * average_pdf_size
```

至少覆盖正常、活动峰值、缓存冷启动、单篇热点、邮件供应商变慢、Redis 故障和 PDF 批量构建。记录硬件/容器 limit、数据规模、软件 digest、命中率、错误率、p50/p95/p99、DB连接、锁等待、队列 lag 和成本。

发布容量门禁：持续目标负载不超过本版本首次错误/延迟失控饱和点的 50%，短时峰值不超过 70%；数值可经真实生产证据调整，但必须保留安全余量。

## 4. 分级演进

| 阶段 | 架构 | 触发依据 |
| --- | --- | --- |
| Launch | 海外 CDN/WAF；Nginx 静态直出；2+ reader-api；PostgreSQL interaction logical DB；Redis；队列隔离；私有对象存储 | 上线必备，不等待流量 |
| Growth | PgBouncer；interaction DB 独立实例/集群；Redis HA；评论 JSON 快照进对象存储/CDN；读副本用于审核/分析；worker autoscale | 连接/CPU/IO/队列或源站命中率持续越过预算 |
| High traffic | 多可用区；按 journal/article 热点拆分；评论表分区；独立 durable broker；CDN stale；热点 key 保护 | 单集群压测余量不足或故障域不可接受 |
| Multi-region | 全局边缘；区域只读快照；写入主区域或明确的分片路由；跨区灾备演练 | 业务确认跨区 RTO/RPO 与成本，不能只因“可能很大”提前引入 |

不要按日历机械升级。每次扩展由指标、压测、故障风险和成本共同触发。

## 5. PostgreSQL 与 Redis

### 5.1 PostgreSQL

- reader-api 使用独立数据库用户、连接上限和 statement timeout；
- PgBouncer transaction pooling 前验证 Django session-level 特性；
- 列表只走覆盖索引和 cursor；禁止无界 offset、`COUNT(*)` 热路径和 N+1；
- 高量事件按时间范围分区并按分区清理；评论达到实际大表门槛后再分区；
- 审核查询和分析任务不能占满写主库；
- 备份、PITR、恢复演练和 schema 迁移时间纳入容量评审。

PostgreSQL 官方说明分区适合非常大的表并会影响规划，不能把分区当成默认优化，见 [表分区文档](https://www.postgresql.org/docs/current/ddl-partitioning.html)。

### 5.2 Redis

Redis 只保存可重建或有 PostgreSQL 事实源的状态；reader session 在 PostgreSQL 有权威记录，Redis 只做热缓存和撤销加速。限流用原子脚本；热点评论 key 加随机 TTL；避免单个巨大 JSON；设置内存上限、淘汰策略和 key namespace。Redis 故障时可以回源校验已有 session，但因原子限流不可用，写评论、验证消费、下载 grant 默认拒绝；公开评论可回退 CDN 快照，文章阅读不受影响。

## 6. 热点与缓存

- capability：短 TTL + versioned key + revocation deny key；
- 评论 API：ETag、Redis 和 single-flight，防同一热点缓存击穿；
- 评论公开快照：`article_id/snapshot_version.json` 不可变，active pointer 短缓存；
- 邮箱申请：不缓存带 PII 响应，只缓存限流/挑战状态；
- PDF：同一 release 同一文件边缘缓存必须保持私有授权语义，不能使用公共 cache key；
- 404/空评论：短时 negative cache，政策更新主动失效。

## 7. 降级矩阵

| 故障 | 用户行为 | 系统动作 |
| --- | --- | --- |
| reader-api 不可用 | 文章可读；互动显示暂不可用 | CDN/Nginx 继续静态服务，不重试风暴 |
| interaction DB 不可用 | 评论快照可读；写/登录/下载拒绝 | 503 + Retry-After，保护连接池 |
| Redis 不可用 | 文章/快照可读；敏感写 fail closed | 告警，禁止退化为无原子限流 |
| 邮件供应商异常 | 现有 session 可用；新验证显示延迟 | 队列重试/切备用，抑制重复邮件 |
| 风控超时 | 评论进入 pending | 不把未知风险直接公开 |
| PDF renderer 故障 | 当前已激活 PDF可下载；新 release 不激活 | 队列隔离，保留旧 current |
| 对象存储异常 | 下载暂不可用 | 不经 Web worker代理全文件兜底 |
| 评论快照滞后 | 提交者看到写响应，其他人稍后可见 | 展示可接受最终一致性，监控 lag |

## 8. SLO 候选

在有基线数据前，下列仅作为上线评审候选，不是当前承诺：

| 服务 | 候选 SLO |
| --- | --- |
| CDN 静态文章可用性 | 99.99% 月度 |
| reader-api 核心能力可用性 | 99.9% 月度 |
| 评论公开快照新鲜度 | 99% 在状态变化后 30 秒内 |
| 魔法链接任务入队 | 99% 在 2 秒内；最终送达由供应商指标单列 |
| 下载授权 | 99% 可用请求在 2 秒内返回 grant |

延迟目标需按主要海外区域分别测量，不能只用中国开发机或服务端 localhost 数据。

## 9. 可观测性

### 9.1 指标

- CDN hit ratio、origin RPS/带宽、静态 4xx/5xx；
- reader-api RPS、p50/p95/p99、错误码、in-flight、worker saturation；
- PostgreSQL 连接、事务、锁等待、慢查询、复制延迟、表/索引大小；
- Redis 命中、内存、eviction、command latency、限流命中；
- 各队列 depth、oldest age、retry、dead letter；
- verification requested/accepted/delivered/bounced/complaint，均不带邮箱 label；
- comment published/pending/hidden/report、审核 backlog 和 snapshot lag；
- PDF render duration/failure/size、对象存储错误和 grant 结果；
- 每千次验证、评论、下载及每 GB 出网成本。

禁止把 article id、reader id、email、IP 等高基数字段直接作为 Prometheus label。

### 9.2 日志与追踪

结构化日志包含 timestamp、service、environment、release、request/event/command id、route template、status、duration 和安全脱敏错误类别。OpenTelemetry trace 跨 API、outbox、Celery 和 provider adapter 传播，但采样器和 exporter 必须过滤 token、Cookie、email、comment body、presigned URL。

### 9.3 探针

- `livez`：只证明进程/event loop 可响应，不访问数据库/Redis；
- `readyz`：检查必要依赖和迁移版本，失败时停止接收新流量；
- `startupz`：renderer/font/browser 等慢启动校验（平台支持时）；
- synthetic：从主要海外区域定时完成文章读取、评论快照和不实际发送邮件的安全验证流程。

## 10. 告警与值班

按用户影响告警，不按单个偶发异常告警。至少配置：静态源站 5xx、reader-api error budget burn、数据库连接耗尽、Redis 不可用、队列 oldest age、邮件 bounce/complaint 激增、待审积压、PDF 构建失败、manifest/projection 不一致、对象存储授权失败和成本异常。每个告警链接到 runbook、owner、最近变更和回滚入口。

## 11. RI-09 工程落地

- 内部 metrics 只接受 `Authorization: Bearer` service token；query token 被拒绝，Nginx 对该路径关闭 access log；
- `reader_synthetic_check` 与 `reader_capacity_probe` 可通过 `--base-url` 指向内部 origin，并用 `--host-header` 保持正式 Host/`ALLOWED_HOSTS` 约束；不得把内部服务名加入生产公开 Host 作为替代；
- readiness 的 Redis broker 探测同时限制 connect、socket connect 和 socket read timeout；依赖故障必须在摘流预算内返回非 200；
- Nginx 在规范化前检查 reader/protected 原始路径，拒绝编码 dot segment、slash 和 backslash；应用对象权限与文件 key 校验仍是第二道边界；
- 测试环境容量、故障和恢复证据见 `docs/operations/reader-interactions-ri09-20260817T1309+0800.zh-CN.md`。正式海外 synthetic、provider/exporter 与已批准 SLO/RPO/RTO/预算属于 RI-10 硬门禁。
