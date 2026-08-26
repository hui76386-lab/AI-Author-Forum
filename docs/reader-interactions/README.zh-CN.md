# 读者互动与受控 PDF 开发文档总览

> 文档版本：v1.0  
> 基线日期：2026-08-14  
> 文档性质：已确认需求的开发契约；除明确标注“现有能力”外，均为待实施设计

> 直接交给 AI 开发时，使用 [AI 可执行开发任务书](./AI-IMPLEMENTATION-SPEC.zh-CN.md)。该文件自包含产品规则、架构、模型、API、任务卡、测试、部署和验收条件。

## 1. 已确认结论

本功能采用项目内自建方案，不把 Twikoo、Artalk、Waline 或 Coral 作为核心评论后端。读者通过邮箱魔法链接完成验证后，才能评论、调用系统分享面板、复制分享链接和申请 PDF 下载。公开阅读文章本身不要求登录。

第一阶段范围如下：

- PDF 是唯一下载格式；
- 分享只调用浏览器系统分享面板和复制链接，不替读者向第三方发送消息；
- 评论区位于文章正文底部，默认发布、事后审核，命中高风险规则时先进入待审；
- 评论支持回复、举报、撤回本人评论、管理员隐藏和关闭单篇文章评论区；
- 评论只允许纯文本和换行，不支持 Markdown、HTML、图片、附件或可点击链接；
- 主编辑、常务副编辑、副编辑均可在其有效任命的期刊范围内管理文章 PDF 下载权限；
- 暂不做订阅、营销触达或第三方社交平台代发；
- 当前没有流量基线，容量采用分级扩展和压测门禁，不承诺未经实测的固定 QPS。

## 2. 不可破坏边界

本功能必须服从项目既有正式数据链路：

```text
导入暂存 -> canonical ArticlePage 草稿/revision -> 审核 -> 正式投放
  -> 冻结构建快照 -> 不可变 manifest -> current 原子激活
```

读者互动不能让草稿、未审核版本或未投放文章进入前台。文章静态页仍由 CDN/Nginx 直接提供；评论、邮箱验证和下载授权是独立动态数据面，动态服务故障不得阻断文章阅读。

## 3. 文档导航

| 文档 | 用途 |
| --- | --- |
| [AI 可执行开发任务书](./AI-IMPLEMENTATION-SPEC.zh-CN.md) | 单文件权威实施规范；按 `RI-00` 至 `RI-10` 逐卡交给 AI 开发 |
| [00 技术选型与 ADR](./00-technology-decision.zh-CN.md) | 比较自建与第三方评论系统，记录最终选择 |
| [01 产品需求与验收边界](./01-product-requirements.zh-CN.md) | 固化用户故事、业务规则、范围和非目标 |
| [02 系统架构](./02-system-architecture.zh-CN.md) | 定义控制面、数据面、静态发布、存储和扩展方式 |
| [03 领域模型与状态机](./03-domain-model-and-state-machines.zh-CN.md) | 定义身份、评论、举报、策略、PDF 产物和状态迁移 |
| [04 API 与前端集成](./04-api-and-frontend-integration.zh-CN.md) | 定义接口、幂等、错误码和静态文章挂载方式 |
| [05 权限、审核与后台](./05-permissions-moderation-and-admin.zh-CN.md) | 定义三类编辑角色、审核动作、对象范围和审计 |
| [06 PDF 下载与分享](./06-pdf-download-and-sharing.zh-CN.md) | 定义 PDF 构建、私有存储、授权和分享行为 |
| [07 安全、隐私与反滥用](./07-security-privacy-and-abuse.zh-CN.md) | 定义邮箱验证、CSRF、限流、隐私和内容安全 |
| [08 容量、可靠性与可观测性](./08-capacity-reliability-and-observability.zh-CN.md) | 定义高流量架构、降级、SLO 和监控 |
| [09 测试、部署与回滚](./09-testing-deployment-and-rollback.zh-CN.md) | 定义测试矩阵、灰度、部署顺序和故障回滚 |
| [10 实施计划与验收](./10-implementation-plan-and-acceptance.zh-CN.md) | 给出可执行阶段、任务依赖和 DoD |
| [11 跨设备邮箱验证与评论发布方案](./11-cross-device-email-verification-plan.zh-CN.md) | 解决电脑发起、手机验证后电脑无法继续评论的问题 |

## 4. 术语

- `ReaderIdentity`：以已验证邮箱为主键语义的读者身份，不等同于后台 `User`。
- `reader session`：邮箱验证成功后签发的读者会话，只用于读者互动接口。
- `article_public_id`：文章不可变 UUID；评论和下载策略不能以可变 slug 作为业务主键。
- `control plane`：Wagtail 后台、权限、政策配置、审核和审计。
- `interaction data plane`：读者身份、评论、举报、事件、运行时能力投影和下载授权。
- `protected manifest`：与静态 release 绑定、记录私有 PDF 产物和校验和的不可变清单。
- `default publish / risk pre-moderation`：普通评论立即公开，高风险评论先待审。

## 5. 文档维护规则

实现期间如修改字段、状态、权限、接口、发布或回滚契约，必须同步修改本目录和对应测试。设计与代码不一致时应显式记录差异，不能把规划状态写成已上线状态。
