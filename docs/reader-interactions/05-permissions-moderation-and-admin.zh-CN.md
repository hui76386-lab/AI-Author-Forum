# 05 权限、审核与后台

## 1. 授权来源

后台互动权限只接受现有有效 `JournalEditorAssignment`：

- `chief_editor`：主编辑；
- `executive_editor`：常务副编辑；
- `associate_editor`：副编辑。

三种角色都可在其有效任命的期刊内管理文章 PDF 下载政策、评论区状态和评论审核。这是本功能的明确业务规则，不要求副编辑额外具备现有 `article_maintenance` responsibility。旧 Group、菜单可见性、客户端角色参数或文章 `related_journals` 不能提供授权。

`ArticlePage.primary_journal` 决定对象范围。任命失效、账号停用、期刊停用或任期结束后，权限实时失效。

## 2. 权限矩阵

| 动作 | 匿名 | 已验证读者 | 三类本刊编辑 | 超级管理员 |
| --- | ---: | ---: | ---: | ---: |
| 阅读文章/公开评论 | 是 | 是 | 是 | 是 |
| 创建评论/回复 | 否 | 政策允许时 | 以读者身份另行验证 | 以读者身份另行验证 |
| 撤回评论 | 否 | 仅本人 | 否；应使用隐藏 | 否；应使用隐藏 |
| 举报评论 | 否 | 非本人/按规则 | 可作为读者举报 | 可作为读者举报 |
| 通过待审评论 | 否 | 否 | 本刊 | 全站应急 |
| 隐藏/恢复/拒绝/标记垃圾 | 否 | 否 | 本刊 | 全站应急 |
| 开关单篇评论区 | 否 | 否 | 本刊 | 全站应急 |
| 修改单篇 PDF 下载政策 | 否 | 否 | 本刊 | 全站应急 |
| 修改期刊默认政策 | 否 | 否 | 本刊 | 全站应急 |
| 查看读者邮箱 | 否 | 仅自己的受保护账户页 | 默认否 | 受审计的专用权限 |
| 导出互动审计 | 否 | 否 | 本刊脱敏范围 | 全站受审计 |

超级管理员执行政策或审核动作必须填写原因。该能力不改变“超级管理员不能绕过主编辑终审”的既有规则。

## 3. Wagtail 后台信息架构

```text
读者互动
  待处理评论
  已举报评论
  全部评论
  互动政策
  PDF 产物
  互动审计

文章编辑页
  读者互动面板
    评论区：继承 / 开放 / 只读 / 隐藏
    PDF 下载：继承 / 启用 / 禁用
    当前有效政策（只读）
    活动 release / PDF checksum（只读）
```

列表必须按操作者有效期刊范围过滤，详情页和 POST service 再做同一对象级校验。不能只通过隐藏其他期刊记录保证安全。

## 4. 审核工作流

### 4.1 自动决策

评论提交先执行同步硬规则：身份、评论区、长度、控制字符、重复、频率和封禁。随后调用可替换 risk adapter：

- 低风险：直接 `published`；
- 中高风险：`pending`，后台排队；
- 明确垃圾/攻击：`spam` 或直接拒绝，按规则保留事件；
- 风控超时：默认进入 `pending`，不能因供应商不可用而无条件公开高风险内容。

外部反垃圾服务只能接收经隐私评审允许的数据，不默认发送完整邮箱或原始 IP。模型/供应商结论是辅助信号，人工编辑拥有最终内容治理决定。

### 4.2 人工动作

每次审核必须提交：comment id、expected version、action、reason code、可选 note 和 idempotency key。批量操作也逐条生成 moderation event，并返回部分失败明细；不能因一条版本冲突重复修改其他记录。

隐藏用于立即停止公开展示；拒绝用于待审内容不予发布；垃圾用于反滥用训练/规则；撤回只属于评论作者。编辑不能伪造成作者撤回。

### 4.3 举报处理

举报列表按风险、唯一举报人数、最近举报和评论曝光排序。阈值可以把评论重新送入 `pending review` 队列，但不默认自动删除。处理结果记录 `no_action`、`hidden`、`spam` 或 `reader_suspended`，举报人首期不接收处理结果邮件。

## 5. 政策修改流程

1. 页面读取当前 desired/effective policy 与 version；
2. 编辑选择新值并填写原因；
3. service 行锁 canonical policy，校验任命和 expected version；
4. 同事务写政策、`AuditLog(CONFIGURE)` 和 control-plane outbox；
5. 投影消费者写入互动 DB 与 Redis revocation；
6. 后台显示 `applying`，投影确认后显示 `effective`；
7. 需要改变静态按钮或 PDF 时，创建新的静态构建任务，不能直接改 `current`。

下载从 disabled 改 enabled 只有在 PDF 随新 manifest 激活后才有效。enabled 改 disabled 时运行时 API先拒绝新授权，静态页面按钮稍后随新 release 移除。评论 open 改 read-only/hidden 同样先限制写路径。

## 6. 跨库审核与审计

评论存于 `interactions`，平台 `AuditLog` 存于 `default`，不能声称存在跨库原子事务。后台审核采用可对账 command：

1. `default` 事务创建唯一 `ModerationCommand` 和 `AuditLog(status=started)`；
2. 内部 API按 command id 在互动库事务中写状态和不可变 `CommentModerationEvent`；
3. 回执后在 `default` 追加 `AuditLog(status=success|failure)`，不更新旧审计行；
4. 超时返回“处理中”，对账任务持续查询，不能把未知状态显示成成功；
5. 审计镜像失败不恢复已经隐藏的有害评论；以互动库不可变事件为即时事实，并由告警和 reconciliation 补齐平台日志。

这是安全治理优先的显式一致性选择。下载/评论政策属于 `default`，可以继续在同库事务中满足政策与 `AuditLog` 同成败。

## 7. 审计字段

至少记录：actor id/type、journal id、article public id、comment id、action、from/to state、reason code、expected/actual version、command/event/request id、release version、结果、错误类别和时间。

不得记录：明文邮箱、魔法链接 token、session secret、完整 presigned URL、第三方邮件凭据或不必要的原始 IP。后台显示读者时使用 public id、昵称和脱敏邮箱；查看完整邮箱需要独立高权限动作和审计。

## 8. 竞争与异常

- 两名编辑同时审核同一评论：只有 expected version 匹配者成功，另一方收到冲突并刷新；
- 作者撤回与编辑隐藏并发：锁行后按先提交事件生效，终态不丢失任何事件；
- 政策关闭与评论提交并发：评论事务重新读取 capability/revocation version，关闭后提交返回 409；
- 任命在页面打开后失效：POST 时重新鉴权，不能依赖页面加载时权限；
- 内部服务超时：command 状态为 unknown/processing，禁止盲目重试生成新 command id。

## 9. 管理端验收

- 三类角色均能操作本刊，且完全看不到/访问不到其他期刊对象；
- 旧 Group 或只有 Django permission 的账号不能旁路 service；
- 所有高风险动作有确认、原因、幂等和审计；
- 批量审核对单条冲突给出可恢复结果；
- 关闭评论后无需等待静态构建即可阻止新写入；
- 下载禁用后不再签发链接，并显示旧授权最长残余窗口；
- 评论正文、邮箱和 IP 在列表导出中按权限脱敏。
# RI-04 实现状态

RI-04 已将互动政策的控制面权限落地：政策变更只接受活动期刊的 `chief_editor`、`executive_editor`、`associate_editor` 有效任命；任命、用户或期刊失效会在下一次 POST 即时拒绝。旧 Group、Django permission、`is_superuser` 和客户端传入角色不会改变对象范围。超级管理员的全站应急变更必须填写原因并写入不可变 `AuditLog(CONFIGURE)`。

政策写入先写 Redis desired/deny 版本，再在 default 事务内写政策、ControlPlaneOutbox 和审计；interactions projection 只允许单调递增，版本不匹配时 capability API 隐藏评论并关闭下载。后台同时展示 desired、applying、effective 状态。具体部署证据见 `docs/operations/reader-interactions-ri04-20260817T0438+0800.zh-CN.md`。

# RI-06 实现状态

RI-06 已实现期刊范围审核后台、五类人工动作、幂等/expected version、批量部分失败和跨库 `ModerationCommand`。平台审计先记录 started，只有 interactions 不可变 moderation event 得到确认或 reconciliation 查到同 command id 后才追加 success；超时/异常保持 unknown/failure。审核审计已按本文件字段脱敏，具体测试、迁移、镜像、备份和恢复证据见 `docs/operations/reader-interactions-ri06-20260817T084217+0800.zh-CN.md`。
