# 08 账号、子期刊编辑角色与审核开发任务书

> 文档版本：v2.2  
> 编制日期：2026-08-04  
> 适用项目：AI Author Forum CMS  
> 技术基线：Django 6.0、Wagtail 7.4、PostgreSQL、Redis/Celery、静态发布  
> 文档状态：已确认的简化开发基线  
> 实施环境：当前测试/开发服务器；不包含正式服务器发布授权

## 1. 文档目的

本任务书将账号和编辑权限收敛为一套容易理解、可以直接开发的模型：

- 平台只保留一种全局维护角色：超级管理员；
- 每个子期刊配置一名主编辑、一名常务副编辑和多名副编辑；
- 副编辑和常务副编辑负责初审及日常维护；
- 主编辑负责子期刊整体管理和文章终审；
- 不建设复杂的外部审稿人、推荐编辑、决策编辑、诚信专员、独立发布管理员等角色体系；
- 子期刊编辑团队的姓名、单位和展示顺序可以独立维护；
- 每篇文章的作者声明可以独立编辑，并展示在文章底部；
- 已有导入、正式文章、审核、投放、不可变 manifest 和原子发布链路继续保持。

本文是下一阶段账号和角色开发的唯一任务基线。实现人员不得再自行增加新的系统角色或审核层级。

## 2. 已确认的业务规则

1. 平台级角色只有 `super_admin`，中文名称为“超级管理员”。
2. 超级管理员可以维护后台、全部子期刊、全部账号、角色分配、发布和回滚。
3. “项目总负责人”不再作为系统账号角色；项目治理身份不进入后台权限模型。
4. 子期刊角色只有 `chief_editor`、`executive_editor` 和 `associate_editor`。
5. 每个启用的子期刊必须有且只能有一名在任主编辑。
6. 每个子期刊默认有一名在任常务副编辑；确实空缺时必须在后台明确显示“未设置”。
7. 每个子期刊可以有多名副编辑。
8. 副编辑和常务副编辑可以对本刊文章初审；主编辑可以初审，也负责终审。
9. 初审通过不等于正式审核通过；只有主编辑终审通过后，文章才允许进入正式投放和静态发布链路。
10. 不同副编辑可以承担不同的期刊维护职责，职责通过固定复选项分配，不新增更多角色名称。
11. 超级管理员负责创建、停用、重置和分配所有账号；主编辑不能创建平台账号。
12. 主编辑可以调整本刊副编辑的日常职责、公开资料和排序，但不能授予超级管理员或更换本刊主编辑。
13. 平台不建设公共注册入口，也不为文章作者自动创建登录账号。
14. 本阶段不建设外部同行审稿人账号和外部评审门户。
15. 平台只保留一种全局维护角色，但实际登录账号必须实名，禁止多人共用一个超级管理员账号。
16. 编辑团队公开资料与登录账号资料分开保存，避免修改公开单位时影响登录身份和历史审计。
17. 文章作者声明是文章自己的内容，不从编辑账号、编辑团队或作者字符串自动生成。
18. 审核记录绑定固定 revision；终审后的正文或作者声明被修改时，必须重新提交初审和终审。
19. 正式前台仍只读取已激活的静态 release，不能因为权限简化而绕过投放、manifest 或原子激活。
20. 所有账号、角色、初审、终审、投放、发布和回滚动作必须写入 `AuditLog`。
21. 文章作者声明复用现有 `ArticlePage.responsibility_statement`，修改后台和前台名称，不新增 `author_declaration` 重复字段；现有 `ai_contribution_statement` 继续单独保留。
22. 现有单级 `ArticleReviewTask` 必须改为“初审任务 -> 终审任务”两级 Workflow；任何 Wagtail signal 都不能绕过终审服务直接写 `approved`。
23. 超级管理员的全局维护旁路不适用于 `can_final_review()`；旧“项目总负责人”Group、直接审核权限和旧全局审核旁路必须在迁移中撤销。
24. 子期刊编辑通过技术组“子期刊编辑基础访问”获得 Wagtail 登录和最小模型入口；直接创建账号或新增任命时，任命事务必须创建或补齐该组的后台、子期刊、文章审核和投放只读入口权限，不能依赖运维人员预先运行角色初始化命令。该技术组不是业务角色，也不提供全站数据权限，投放写权限和数据范围仍由有效任命 service 判断。
25. 主编辑和常务副编辑更换必须使用原子交接服务；不能让启用中的子期刊在交接过程中出现两名或零名主编辑。
26. 不能暂停、停用或撤销平台最后一个有效超级管理员；高风险账号操作必须防止操作者把平台锁死。
27. 初审认领、初审、终审和角色交接必须使用数据库行锁、expected state/revision 和幂等 request id，阻止并发重复操作。
28. 只有主编辑可以首次创建本刊正式投放；投放成功后必须自动创建并执行仅包含该投放影响路径的选择性静态发布任务，不再等待超级管理员批准。
29. 子期刊编辑不得修改期刊 slug、启停状态、静态输出路径、主站导航和平台配置，不得硬删除已审核或已发布业务记录。
30. 本阶段编辑团队中展示的主编、常务副编辑和副编辑都必须关联实名后台账号；纯展示、不登录的编委名单不在本阶段范围。
31. 账号任命、审核、投放和发布等高风险动作必须在同一事务内写审计；审计写入失败时业务动作回滚。
32. 常务副编辑和副编辑不能首次投放文章；具有文章维护职责的副编辑可以维护有历史静态发布时间的本刊文章，修改后必须重新完成初审和主编辑终审，之后可以对既有投放重新发布。
33. 主编辑和副编辑的期刊级自动发布只允许选择性更新本刊文章、本刊页面及其搜索索引依赖；不能取得全站构建、失败重试或回滚权限，Celery 工作进程必须再次核对任命、投放记录和精确路径。
34. 选择性发布必须承接当前活动 manifest 中未修改的历史文章；承接不产生新的审核结论，也不能让新文章绕过同 revision 的主编辑终审。

## 3. 角色职责与边界

### 3.1 角色清单

| 角色 | 编码 | 数据范围 | 主要职责 | 明确边界 |
| --- | --- | --- | --- | --- |
| 超级管理员 | `super_admin` | 全平台 | 后台维护、账号管理、子期刊管理、角色分配、故障处理、静态发布和回滚 | 日常终审应由在任主编辑完成；主编辑缺位时先调整主编辑任命 |
| 主编辑 | `chief_editor` | 被分配的子期刊 | 负责本刊全部内容和编辑团队、分配工作、初审、终审、首次投放和期刊级自动发布 | 不能管理其他子期刊、超级管理员账号和平台级配置 |
| 常务副编辑 | `executive_editor` | 被分配的子期刊 | 协助主编辑、统筹日常维护、分派初审、执行初审、维护和重新发布已有文章 | 不能终审、不能首次投放、不能更换主编辑、不能管理平台账号 |
| 副编辑 | `associate_editor` | 被分配的子期刊 | 文章初审、日常内容维护，并按分配职责维护和重新发布已有文章 | 不能终审、不能首次投放、不能查看其他子期刊、不能修改角色 |

后台角色名称与前台展示名称允许如下映射，但权限编码保持不变：

| 系统角色 | 默认前台名称 | 可选前台名称 |
| --- | --- | --- |
| `chief_editor` | 主编 | 主编辑 |
| `executive_editor` | 执行主编 | 常务副编辑 |
| `associate_editor` | 副主编 | 副编辑 |

### 3.2 副编辑职责

所有在任副编辑均具有本刊文章初审能力。日常维护职责使用以下固定复选项：

| 职责编码 | 后台名称 | 允许操作 |
| --- | --- | --- |
| `article_maintenance` | 文章维护 | 编辑本刊文章草稿、作者声明、图片和元数据，提交初审 |
| `journal_profile` | 期刊资料 | 维护本刊名称、简介、封面、联系方式和 SEO |
| `column_navigation` | 栏目与导航 | 维护本刊栏目资料和允许范围内的子期刊导航 |
| `issue_management` | 期次管理 | 维护期次、目录和文章编排，不执行终审 |
| `media_assets` | 图片与素材 | 维护本刊图片、文档和展示素材 |

常务副编辑默认拥有上述全部本刊职责。主编辑默认拥有全部本刊职责。普通副编辑至少选择一项职责，且可以同时选择多项。

职责只决定日常维护入口，不改变审核级别。给副编辑勾选全部职责后，该用户仍然不能执行终审。

### 3.3 权限矩阵

符号：`G` 全平台；`J` 被分配子期刊；`D` 取决于职责复选项；`-` 禁止。

| 操作 | 超级管理员 | 主编辑 | 常务副编辑 | 副编辑 |
| --- | ---: | ---: | ---: | ---: |
| 创建、停用、重置账号 | G | - | - | - |
| 分配超级管理员 | G | - | - | - |
| 分配主编辑和常务副编辑 | G | - | - | - |
| 调整本刊副编辑职责和公开排序 | G | J | J | - |
| 查看子期刊全部文章 | G | J | J | J |
| 编辑文章草稿和作者声明 | G | J | J | D |
| 文章初审 | G | J | J | J |
| 分派本刊初审 | G | J | J | - |
| 文章终审 | -；必须先调整主编辑任命 | J | - | - |
| 维护子期刊资料 | G | J | J | D |
| 维护本刊栏目、导航和期次草稿 | G | J | J | D |
| 发布期次、设为当前期次 | G | J | J | - |
| 回滚或归档本刊期次 | G | J | - | - |
| 修改期刊 slug、启停状态、静态路径 | G | - | - | - |
| 修改主站导航、全局栏目和平台配置 | G | - | - | - |
| 维护编辑团队公开资料 | G | J | J | 本人公开资料 |
| 首次创建本刊范围正式投放 | G | J | - | - |
| 维护并重新发布本刊已有文章 | G | J | J | D |
| 创建主站、全局或跨刊投放 | G | - | - | - |
| 硬删除已审核/已发布业务记录 | - | - | - | - |
| 本刊投放触发选择性构建和原子激活 | G | J | 仅已有文章 | D，仅已有文章 |
| 全站构建、失败重试和回滚 | G | - | - | - |
| 查看审计日志 | G | 本刊 | 本刊本人动作 | 本人动作 |

### 3.4 敏感字段、归档与删除边界

子期刊角色只能维护本刊的业务内容，不因拥有 `journal_profile`、`column_navigation` 或 `issue_management` 而获得平台结构权限：

- 期刊 `slug`、状态、`static_site_path`、静态输出根目录和目标文章数量只允许超级管理员修改；
- 主站导航、默认子期刊模板、全局栏目、全局版位和站点配置只允许超级管理员修改；
- 副编辑可以编辑期次草稿和文章目录；主编辑、常务副编辑可以发布期次及设为当前期次；只有主编辑和超级管理员可以回滚或归档本刊期次；
- 文章、期次、栏目、任命、投放和审核记录已进入审核或发布链路后禁止硬删除，使用退回、停用、归档、撤销或新记录纠正；
- 超级管理员也不能硬删除已激活 manifest、审核记录或审计日志；
- 图片或文档仍被文章、期次或页面引用时不得删除。

## 4. 新账号创建与管理

### 4.1 创建入口

后台增加：

```text
系统
  账号管理
    所有账号
    新建账号
    已停用账号
```

固定路由：

```text
GET  /admin/accounts/
GET  /admin/accounts/new/
POST /admin/accounts/new/
GET  /admin/accounts/<id>/
POST /admin/accounts/<id>/suspend/
POST /admin/accounts/<id>/activate/
POST /admin/accounts/<id>/reset-password/
```

只有超级管理员可以访问这些入口。菜单隐藏、视图、表单和 service 必须分别检查权限。

子期刊“编辑团队”页面同时提供“创建本刊角色账号”快捷入口，仅向超级管理员显示。该入口固定当前子期刊，可预选主编辑、常务副编辑或副编辑；创建时三类角色均使用五项日常维护职责的只读预设，审核级别仍由角色编码决定。账号与当前期刊任命必须在同一事务中创建，任命失败时不得留下孤立账号。已有账号仍通过同页的“任命已有账号”入口处理。

### 4.2 新建账号表单

第一部分“账号资料”：

| 字段 | 是否必填 | 说明 |
| --- | ---: | --- |
| 用户名 | 是 | 全平台唯一，创建后普通用户不能修改 |
| 邮箱 | 是 | 大小写不敏感唯一 |
| 姓名 | 是 | 后台显示和审计使用 |
| 单位 | 否 | 默认带入编辑团队公开资料，可单独修改 |
| 职务/职称 | 否 | 账号资料，不决定系统权限 |
| 临时密码 | 是 | 使用 Django 密码校验器；不得写入日志或审计 |
| 账号状态 | 是 | 新账号默认 `active` |

第二部分“角色分配”：

- 选择“超级管理员”时，不再选择子期刊；
- 选择“主编辑”“常务副编辑”或“副编辑”时，必须选择一个或多个子期刊；
- 选择副编辑时，必须为每个子期刊至少选择一项维护职责；
- 选择主编辑或常务副编辑时，表单必须检查该子期刊是否已有在任人员；
- 可以为同一账号增加多条子期刊任命，例如在 A 期刊任主编辑、在 B 期刊任副编辑；
- 表单提交前展示最终的“账号 + 子期刊 + 角色 + 职责”摘要。

第三部分“公开资料”：

- 公开姓名；
- 公开单位；
- 前台角色名称；
- 展示顺序；
- 是否在子期刊编辑团队中公开。

提交后一次性创建账号和角色任命，并写一条账号审计和每条角色任命审计。任一关键写入失败时整笔事务回滚。

账号与后台基础权限规则：

- 超级管理员账号加入业务组“超级管理员”；
- 主编辑、常务副编辑和副编辑账号设置 `is_staff=True`，并加入技术组“子期刊编辑基础访问”；
- 技术组只授予 `wagtailadmin.access_admin`、查看必要菜单和最小模型入口，不授予任何期刊的全局增删改查权限；
- 子期刊对象范围始终由 `JournalEditorAssignment` 和统一权限 service 决定；
- 账号失去全部有效子期刊任命后，从技术组移除并撤销后台会话；
- 禁止业务代码通过直接修改 Group 成员关系授予主编辑、常务副编辑或副编辑角色。

### 4.3 首次登录和密码

1. 超级管理员创建账号时设置临时密码。
2. 新账号设置 `must_change_password=True`。
3. 用户第一次登录后只能进入修改密码页面。
4. 密码修改成功后才能进入业务后台。
5. 超级管理员重置密码后，再次设置 `must_change_password=True` 并撤销旧会话。
6. 密码、密码摘要和重置内容不得进入 `AuditLog`。
7. 强制改密期间仅允许使用 `GET`/`HEAD` 读取 Wagtail 改密页必需的 `/admin/jsi18n/` 和 `/admin/sprite/`；后台业务页面及这些资源端点的写请求仍必须重定向到改密页，避免资源重定向循环且不扩大业务访问权限。

本阶段不建设邀请邮件、MFA、恢复码和复杂的临时提权流程。后续如有安全合规要求，再作为独立任务增加，不改变本任务书的角色结构。

登录、首次改密和重置密码接口必须按账号和 IP 做速率限制。连续失败进入短时冷却并写安全审计，不允许无限尝试临时密码。

### 4.4 账号状态

| 状态 | 编码 | 可登录 | 说明 |
| --- | --- | ---: | --- |
| 正常 | `active` | 是 | 按角色和子期刊范围访问 |
| 已暂停 | `suspended` | 否 | 临时停权，可由超级管理员恢复 |
| 已停用 | `deactivated` | 否 | 离职或长期不用，默认不恢复 |

状态变化必须同步 Django `is_active`、撤销现有会话并写审计。不得删除已有账号、审核记录或历史角色任命。

超级管理员保护规则：

- 系统始终至少保留一个 `active` 超级管理员；
- 暂停、停用、撤销超级管理员或移除其 Group 前，在事务内锁定并统计其他有效超级管理员；
- 操作者不能暂停或停用自己，除非已有另一名有效超级管理员且在确认页再次输入本人密码；
- 最后一个有效超级管理员的暂停、停用和角色撤销必须拒绝；
- 创建、重置或变更超级管理员账号必须使用独立确认页并写高风险审计。

### 4.5 首个超级管理员

仅当系统不存在可用超级管理员时，允许在已确认环境运行：

```text
python manage.py createsuperuser
```

该命令只用于系统初始化或管理员全部失效后的恢复。后续账号统一从 `/admin/accounts/new/` 创建。

初始化命令创建的 Django superuser 必须在初始化 service 中同步加入“超级管理员”Group。正常权限判断以有效超级管理员 Group 为业务来源；`is_superuser=True` 只保留为技术恢复能力，不能绕过主编辑终审规则。

## 5. 数据模型

### 5.1 `users.User`

在现有 `AbstractUser` 基础上增加：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `display_name` | `CharField(120)` | 必填，后台和审计显示名 |
| `institution` | `CharField(255)` | 单位，可空 |
| `job_title` | `CharField(120)` | 职务/职称，可空 |
| `account_status` | `CharField` | `active/suspended/deactivated` |
| `must_change_password` | `BooleanField` | 新建和重置密码后为真 |
| `created_by` | FK `User` | 创建人，可空以兼容初始化账号 |
| `suspended_at` | `DateTimeField` | 可空 |
| `deactivated_at` | `DateTimeField` | 可空 |
| `status_reason` | `TextField` | 暂停或停用时必填 |

约束：`Lower(email)` 唯一；普通账号邮箱不能为空；`active` 与 `is_active=True` 保持一致；超级管理员账号必须 `is_staff=True`；子期刊编辑账号必须 `is_staff=True` 但不得自动获得全站业务模型权限。

### 5.2 `JournalEditorAssignment`

所属应用：`journals`。

| 字段 | 类型/说明 |
| --- | --- |
| `user` | 编辑账号 |
| `journal` | 子期刊 |
| `role` | `chief_editor/executive_editor/associate_editor` |
| `responsibilities` | `JSONField(default=list)`，只允许第 3.2 节固定编码 |
| `public_name` | 前台姓名，可与账号姓名不同 |
| `public_affiliation` | 前台单位，可独立维护 |
| `public_role_label` | 主编、执行主编/常务副编辑、副主编/副编辑 |
| `display_order` | 同角色内排序 |
| `show_publicly` | 是否前台展示 |
| `starts_at`、`ends_at` | 任期，可空 |
| `is_active` | 当前任命是否有效 |
| `created_by`、`created_at`、`updated_at` | 创建人和时间 |
| `ended_by`、`ended_at`、`end_reason` | 任命结束信息 |
| `replaced_by_assignment` | 被交接时指向新任命，可空 |

数据库和服务约束：

- 同一子期刊最多一条有效 `chief_editor`；
- 同一子期刊最多一条有效 `executive_editor`；
- 同一账号、子期刊、角色不能重复有效；
- 同一用户、期刊和角色的有效任命时间不能重叠；
- 副编辑必须至少有一项职责；
- 角色到期、账号暂停或子期刊停用后任命立即失效；
- 子期刊启用或进入正式发布前，必须存在一名有效主编辑；
- 角色变更保留旧记录，不覆盖历史任命。

超级管理员继续使用 Django Group `超级管理员`。子期刊编辑角色不得使用全局 Group，必须从 `JournalEditorAssignment` 按当前文章所属期刊判断。

必须实现以下任命 service，禁止后台表单直接保存角色交接：

```python
appoint_journal_editor(*, actor, user, journal, role, responsibilities, public_profile)
replace_chief_editor(*, actor, journal, new_user, reason)
replace_executive_editor(*, actor, journal, new_user, reason)
end_journal_editor_assignment(*, actor, assignment, reason)
```

主编辑和常务副编辑交接时锁定 `Journal` 及对应有效任命，在同一事务中创建新任命、结束旧任命、写两条任命审计并更新技术组投影。任一环节或审计失败时全部回滚。启用中的子期刊不允许直接结束最后一名主编辑；应调用 `replace_chief_editor()`，或先由超级管理员暂停子期刊。

### 5.3 文章作者声明和初审分配字段

正式模型 `articles.ArticlePage` 已有 `responsibility_statement`。本阶段复用该字段并将后台、预览和前台文案统一为“作者声明”，不新增语义重复的 `author_declaration`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `responsibility_statement` | 现有 `TextField(blank=True)` | 复用并显示为“作者声明”，文章底部独立显示 |
| `ai_contribution_statement` | 现有 `TextField(blank=True)` | 继续作为“AI 参与说明”，不得与作者声明合并 |
| `assigned_initial_editor` | FK `User` | 可空，指定初审副编辑 |
| `assigned_by`、`assigned_at` | FK/时间 | 初审认领或改派操作者和时间 |
| `assignment_request_id` | `UUIDField` | 认领/改派幂等请求标识 |

作者声明使用纯文本和换行展示，不接受 HTML，因此不需要在该字段中开放富文本能力。允许写作者贡献、利益冲突、基金、数据可用性或其他声明；不从账号资料自动生成。

作者声明参与 revision 和审核摘要。终审后修改作者声明与修改正文处理一致：生成新 revision、撤销旧审核投影、重新进入初审。

文章导入、版本差异、预览、静态 context 和模板必须继续读取 `responsibility_statement`。历史数据无需字段复制，只做显示名称和模板位置调整。

### 5.4 审核状态和审核记录

`ArticlePage.review_status` 必须增加 `pending_final`（待终审）状态；现有 `draft/submitted/approved/rejected/published` 保持兼容，但新审核 service 不得把 `submitted` 直接改成 `approved`。`approved_version` 只能由终审 service 写入，`pending_final` 必须存在同 revision 的初审通过记录。

扩展现有 `ArticleReviewRecord`，不另建复杂同行评审模型：

| 字段 | 说明 |
| --- | --- |
| `stage` | `initial/final` |
| `action` | `submit/initial_approve/initial_return/initial_reject/final_approve/final_return/final_reject/reopen` |
| `revision` | 本次审核固定 revision |
| `reviewer` | 实际操作账号 |
| `journal_editor_assignment` | 本次审核使用的有效子期刊任命；超级管理员执行初审时可空 |
| `reviewer_role` | 操作时角色快照：`super_admin/chief_editor/executive_editor/associate_editor` |
| `request_id` | 幂等请求标识，同一审核动作唯一 |
| `comment` | 审核意见；退回和拒绝时必填 |
| `created_at` | 审核时间 |

审核记录创建后不得修改或删除。重新审核产生新记录。`journal_editor_assignment` 使用 `PROTECT`；任命结束不影响历史审核记录。数据库对 `request_id` 建唯一约束，重复请求返回第一次结果，不重复改变状态或写审计。迁移旧记录时：旧 `submitted` 映射为 `submit`；旧 `approved` 只有在存在终审来源和固定 revision 时映射为 `final_approve`，否则进入待人工复核报告，不得默认视为终审。

## 6. 文章审核流程

### 6.1 状态机

```text
draft
  -> submitted
  -> pending_final        # 副编辑/常务副编辑/主编辑初审通过
  -> approved             # 主编辑终审通过

submitted -> draft        # 初审退回修改
submitted -> rejected     # 初审明确拒绝
pending_final -> draft    # 主编辑退回修改
pending_final -> rejected # 主编辑终审拒绝
approved -> draft         # 已通过内容或作者声明发生修改
rejected -> draft         # 主编辑或超级管理员填写原因后重新开启并生成新 revision
```

`rejected` 对当前 revision 是终止状态，不能直接重新提交。重新开启必须调用独立 service，填写原因、生成新 revision、清空 `assigned_initial_editor` 并写审计。

### 6.2 Wagtail Workflow 接入

现有单级 `ArticleReviewTask` 替换为同一个 Workflow 中顺序执行的两个自定义任务：

```text
ArticleInitialReviewTask
  -> ArticleFinalReviewTask
  -> Workflow approved
```

实现要求：

- `ArticleInitialReviewTask` 使用 `can_initial_review()`；通过时调用 `initial_review_article()`，文章只进入 `pending_final`；
- `ArticleFinalReviewTask` 使用 `can_final_review()`；只有本刊有效主编辑可以执行，通过时调用 `final_review_article()`；
- 现有 `workflow_approved`、`workflow_rejected` signal 只能调用审核 service 或校验最终投影，不能直接 `update(review_status="approved")`；
- `workflow_approved` 触发时如果不存在同 revision 的有效终审通过记录，必须拒绝写入 `approved_version` 并记录失败审计；
- 删除 `ArticleReviewTask._user_can_review()` 中 `is_superuser`、全局 `review_article` 和旧审核组可以直接完成最终审核的旁路；
- `articles.review_article` 仅保留一个迁移周期作为旧入口兼容权限，不再作为 `can_final_review()` 的判断依据；
- Wagtail 任务状态、`ArticlePage.review_status` 和 `ArticleReviewRecord` 必须在同一 service 中保持一致。

### 6.3 初审

- 可执行人：本刊主编辑、常务副编辑、副编辑；
- 超级管理员可以执行应急初审，但必须填写原因；该能力不延伸到终审；
- 未分配文章可以由符合条件的副编辑点击“认领初审”，第一名成功认领者写入 `assigned_initial_editor`；
- 已分配文章只有被分配副编辑、本刊常务副编辑和本刊主编辑可以执行初审；其他同刊副编辑可查看状态，但不能提交审核动作；
- 主编辑和常务副编辑可以改派初审；改派必须填写原因并写审计；
- 初审动作：通过、退回修改、拒绝；
- 通过后状态进入 `pending_final`；
- 退回或拒绝必须填写意见；
- 初审只能针对当前最新 revision，过期页面提交必须失败；
- 账号或子期刊任命失效时，尚未完成的 `assigned_initial_editor` 自动清空并回到未分配队列。

初审认领和动作必须锁定 `ArticlePage`，同时校验 expected state、expected revision 和 request id。并发请求中只能一个成功；状态已变化的请求返回 409，不覆盖第一名编辑的记录。

### 6.4 终审

- 正常情况下只有本刊有效主编辑可以终审；
- 终审动作：通过、退回修改、拒绝；
- 终审通过后记录准确的 approved revision；
- 常务副编辑和副编辑即使拥有全部维护职责，也不能看到或调用终审动作；
- 超级管理员不能用全局权限替代日常学术终审。主编辑缺位时，先由超级管理员调整主编辑任命，再继续终审；
- 终审通过前，必须确认子期刊、作者声明、文章资源和初审记录完整。

终审 service 必须锁定文章并验证：状态为 `pending_final`、revision 与最新初审通过记录一致、操作者是当前有效主编辑、任命所属期刊与文章 `primary_journal` 一致。服务在同一事务内创建终审记录、更新 `approved_version`、同步发布投影并写审计；任一步失败全部回滚。

### 6.5 投放与发布

- 只有 `approved` 且 revision 与终审记录一致的文章可以创建正式投放；
- 只有主编辑可以为本刊已终审文章首次创建本刊首页、本刊栏目/分类和本刊文章位投放；
- 主编辑完成投放后，系统自动创建期刊范围的选择性静态任务并进入 Celery 队列，不再生成“等待发布管理员批准”的任务；
- 常务副编辑和具有 `article_maintenance` 职责的副编辑只能维护有 `last_static_published_at` 的本刊文章和既有投放，不能创建或复制为新的正式投放；
- 副编辑修改已发布文章后，文章返回草稿；只有新 revision 再次完成初审和主编辑终审后，副编辑才能执行“重新发布”；
- 主站、全局栏目、搜索页、其他期刊、跨刊推荐位和不属于本刊的 slot 只允许超级管理员；文章出现在 `related_journals` 中不自动授予目标期刊编辑跨刊投放权限；
- 搜索页只允许作为已授权期刊投放任务的固定依赖自动刷新，子期刊编辑不能直接发布或编排搜索页；
- 只有超级管理员可以主动执行全站静态构建、失败重试和回滚；
- Celery 执行期刊级任务前必须再次验证：任务为自动选择性任务、发起人任命仍有效、投放记录属于本刊、请求路径与投放影响路径完全一致；
- 当前活动 manifest 中已发布、approved revision 未改变的历史文章可以原样承接到新 release；新文章仍必须存在同 revision 主编辑终审记录；
- 构建仍使用冻结输入快照、不可变 manifest、SHA-256 校验和 current 原子切换；
- 构建失败时保留旧 current，不允许把部分成功页面激活为新版本。

## 7. 编辑团队与文章底部展示

### 7.1 子期刊编辑团队

后台提供“子期刊 -> 编辑团队”页面，以结构化行维护：角色、姓名、单位、前台名称、排序和是否展示。

`Journal` 增加两个展示配置：

| 字段 | 类型 | 默认值 |
| --- | --- | --- |
| `show_editorial_team_on_article_pages` | `BooleanField` | `True` |
| `editorial_team_heading` | `CharField(80)` | `编辑团队` |

前台按以下方式分组，达到示例图片中的效果：

```text
主编：姓名  单位
执行主编：姓名  单位
副主编：姓名  单位
          姓名  单位
          姓名  单位
```

规则：

- 主编、执行主编/常务副编辑和副主编/副编辑分组展示；
- 同一组按 `display_order` 排序；
- 姓名、单位分别存储，不要求管理员手写带空格的整段文本；
- 编辑团队固定显示在子期刊介绍页；`show_editorial_team_on_article_pages=True` 时同时显示在本刊文章底部；
- 只展示账号正常、任命在有效期内、`is_active=True` 且 `show_publicly=True` 的成员；
- 本阶段每条公开成员都必须关联实名后台账号，不支持不登录的纯展示编委；
- 历史文章无需冻结旧编辑团队名单；如将来要求保留发表时名单，再增加发布快照，不在本阶段实现。

### 7.2 文章作者声明

文章编辑页将现有 `responsibility_statement` 以独立的“作者声明”区域展示，前台显示在文章正文之后、编辑团队和相关推荐之前。该字段：

- 由有文章维护职责的副编辑、常务副编辑或主编辑编辑；
- 可以逐篇留空或填写；
- 不与编辑团队名单合并；
- 使用纯文本和换行，不接受 HTML、脚本或 iframe；
- 进入静态页面和 manifest 文件清单；
- 终审后修改必须重新审核。

## 8. 权限实现规范

新增统一入口：

```text
ai_author_forum/site_settings/access_control.py
```

必须提供：

```python
is_super_admin(user)
get_journal_editor_assignment(user, journal)
can_manage_accounts(user)
can_manage_journal(user, journal, responsibility=None)
can_initial_review(user, article)
can_final_review(user, article)
can_manage_article(user, article)
can_manage_journal_field(user, journal, field_name)
can_manage_placement_target(user, article, target_type, target)
can_publish_issue(user, issue)
filter_accessible_journals(user, queryset)
filter_accessible_articles(user, queryset)
```

判定顺序：

1. 用户已认证且账号状态为 `active`；
2. 超级管理员可进入平台维护功能，但 `can_final_review()` 不使用该旁路；
3. 子期刊操作必须存在有效 `JournalEditorAssignment`；
4. 维护操作检查对应职责；
5. 初审检查本刊编辑角色和当前状态；
6. 终审只接受本刊有效主编辑；
7. 审核检查当前 revision；
8. 检查投放目标、敏感字段、期次动作和删除/归档边界；
9. 在业务事务内写审计，审计失败时回滚业务动作。

列表必须在 QuerySet 层过滤。隐藏菜单或按钮不能代替后端检查；直接 URL、构造 POST、Celery task 和 management command 必须使用相同 service guard。

旧 `is_global_admin()` 只能作为平台维护判断的兼容入口，并移除“项目总负责人”Group。文章终审必须直接调用 `can_final_review()`，不得先调用 `is_global_admin()` 放行。超级管理员即使拥有 Django `is_superuser=True` 或全部 model permission，也不能绕过终审 service。

对无权知道对象存在的跨刊请求返回 404；对象可见但动作不允许时返回 403；expected state 或 revision 已变化的并发请求返回 409。异步任务实际执行时重新检查账号状态、角色任命、期刊和目标范围，不能只相信入队时权限。

## 9. 后台页面

```text
系统
  账号管理                 # 仅超级管理员
  角色分配                 # 仅超级管理员
  审计日志                 # 超级管理员全局，编辑按范围只读

子期刊
  我的子期刊
  子期刊资料
  编辑团队
  栏目与导航
  期次管理

文章
  所有可访问文章
  我的初审待办
  本刊待初审
  本刊待终审               # 主编辑可见
  已通过/已退回

发布
  投放管理                 # 主编辑、常务副编辑、本刊范围
  静态发布中心             # 仅超级管理员
```

页面要求：

- 超级管理员账号页同时展示账号状态和全部子期刊任命；
- 主编辑首页展示本刊待初审、待终审、编辑负载和未设置职责的副编辑；
- 常务副编辑首页展示本刊待初审、退修和日常维护待办；
- 副编辑首页只展示本刊内容和被分配职责入口；
- 终审按钮只在主编辑页面显示，后端仍需再次校验；
- 编辑团队页可以调整公开姓名、单位、前台名称和排序；
- 作者声明字段在文章编辑页独立成组，不混在作者列表中。

## 10. 代码边界

建议新增或调整：

```text
ai_author_forum/users/
  models.py                 # User 账号字段
  services.py               # 创建、暂停、恢复、重置密码、最后管理员保护
  forms.py                  # 新账号和账号状态表单
  views.py / urls.py        # /admin/accounts/*
  wagtail_hooks.py          # 账号管理菜单
  tests/

ai_author_forum/journals/
  models.py                 # JournalEditorAssignment
  editor_services.py        # 任命、原子交接、职责、排序、到期
  editor_views.py           # 编辑团队后台
  tests/

ai_author_forum/articles/
  models.py                 # 复用 responsibility_statement、初审分配、审核阶段字段
  review_services.py        # 认领、改派、初审、终审、并发和 revision 校验
  wagtail_hooks.py          # 两级 Workflow 及 signal 收口
  admin_views.py            # 待初审、待终审和审核详情
  tests/

ai_author_forum/site_settings/
  access_control.py         # 全局和子期刊统一权限
  management/commands/
    seed_roles.py           # 只保留新角色需要的权限预设
    plan_simple_role_migration.py
    apply_simple_role_migration.py

ai_author_forum/static_publish/
  frontend.py               # 编辑团队和作者声明静态渲染
```

模板、Wagtail hook 和 signal 不得直接写账号任命或审核状态，必须调用 service。账号高风险动作、角色交接、审核、投放和发布 service 使用 `transaction.atomic()`，在同一事务中写 `AuditLog`；审计失败必须抛出异常并回滚，不得先提交业务再补写审计。

## 11. 迁移方案

### 11.1 旧角色处理

| 旧角色 | 新处理方式 |
| --- | --- |
| 项目总负责人 | 不再保留该角色；列入迁移报告，由负责人确认是否改为超级管理员 |
| 超级管理员 | 经确认后映射为新 `super_admin` |
| 内容管理员 | 按实际负责子期刊映射为常务副编辑或副编辑，并分配职责 |
| 审核人员 | 按实际负责子期刊映射为主编辑、常务副编辑或副编辑 |
| 站点运营 | 按实际负责子期刊映射为副编辑，并分配期刊维护职责 |
| 发布管理员 | 不自动升级超级管理员；由负责人确认是否仍需要平台维护权限 |
| 只读人员 | 本阶段无独立角色；确认后停用或改为具体子期刊副编辑 |

禁止根据旧 Group 自动给用户分配全部子期刊。所有子期刊任命必须在迁移表中明确写出期刊、角色和职责。

### 11.2 迁移步骤

1. 增加账号字段、`JournalEditorAssignment`、期刊编辑团队展示配置和初审分配字段；
2. 回填现有账号状态和显示名；
3. 生成现有用户、Group、直接权限和最后登录报告；
4. 由负责人填写用户到新角色及子期刊的映射表；
5. dry-run 检查每个启用子期刊是否恰好一名主编辑；
6. 应用角色任命并写审计；
7. 影子比较旧权限和新权限；
8. 测试通过后启用简化角色策略；
9. 将旧 `project_lead`、旧“项目总负责人”旁路和旧直接 `articles.review_article` 权限列入差异报告并撤销；
10. 将旧单级 Workflow 迁移为初审/终审两级任务，核对每篇待审文章状态；
11. 保留旧 Group 一个稳定发布周期，但不再作为业务授权来源；
12. 确认无回退需求后再移除旧菜单和旧授权代码。

建议使用开关：

```text
SIMPLE_JOURNAL_RBAC_ENABLED=false
SIMPLE_JOURNAL_RBAC_SHADOW_MODE=true
```

## 12. 开发任务

### TASK-ACC-01：账号字段与账号管理

依赖：无。  
修改：`users/models.py`、迁移、forms、services、views、Wagtail 菜单。  
交付：账号列表、新建账号、暂停、恢复、停用、重置密码、首次强制改密、最后超级管理员保护和“子期刊编辑基础访问”技术组投影。  
验收：只有超级管理员能管理账号；邮箱唯一；临时密码不入日志；状态变化撤销会话；编辑账号没有全站业务模型权限；不能停用最后一个有效超级管理员。

### TASK-ROLE-01：简化角色和子期刊任命模型

依赖：TASK-ACC-01。  
修改：`JournalEditorAssignment`、迁移、`seed_roles.py`、任命 service。  
交付：超级管理员、主编辑、常务副编辑、副编辑四类角色、五项副编辑职责、“子期刊编辑基础访问”技术组和原子任命/交接 service。  
验收：每刊最多一名有效主编辑和常务副编辑；副编辑至少一项职责；跨刊权限无效；交接不出现零名或两名有效主编辑；最后一个超级管理员不可被停用。

### TASK-ROLE-02：编辑团队后台与公开资料

依赖：TASK-ROLE-01。  
修改：编辑团队页面、表单、排序 service 和前台 context。  
交付：姓名、单位、前台角色名称、排序、公开开关和按角色分组展示。  
验收：主编辑/执行主编/副主编展示顺序稳定；公开资料修改不改变账号身份和历史审计。

### TASK-PERM-01：统一权限检查

依赖：TASK-ROLE-01。  
修改：`site_settings/access_control.py` 及账号、期刊、文章、投放、发布入口。  
交付：菜单、QuerySet、详情、POST、Celery 和命令行统一权限；技术组基础访问；敏感字段、期次、删除和投放目标限制。  
验收：A 刊编辑无法看到或操作 B 刊；副编辑构造终审请求返回 403；超级管理员可维护全部模块但不能绕过终审 service；跨刊投放返回 403/404。

### TASK-REV-01：初审与终审

依赖：TASK-PERM-01。  
修改：两级 Wagtail Workflow、审核状态、`ArticleReviewRecord`、审核 service、列表和详情页。  
交付：认领/改派、初审通过/退回/拒绝、终审通过/退回/拒绝、`pending_final` 状态、固定 revision、并发锁和幂等 request id。  
验收：副编辑只能初审；主编辑终审；Wagtail 一次 approve 不得直接终审；过期 revision 和重复 request 不能重复改变状态；无终审来源的旧 approved 记录进入人工复核；审核记录不可修改删除。

### TASK-CONTENT-01：文章作者声明

依赖：TASK-REV-01。  
修改：现有 `responsibility_statement`、编辑表单、差异显示、预览和静态模板。  
交付：将责任声明统一显示为作者声明，保留 `ai_contribution_statement`，并在文章底部展示。  
验收：不新增重复字段；历史数据继续显示；字段可单独编辑；HTML/脚本被阻断；终审后修改会重新进入审核。

### TASK-PUB-01：投放和静态发布权限接入

依赖：TASK-PERM-01、TASK-REV-01。  
修改：投放、静态构建、激活、重试和回滚 service。  
交付：主编辑首次投放并自动选择性发布；副编辑维护和重新发布已有文章；超级管理员保留全局发布权限；跨刊目标拦截。  
验收：未终审或 revision 不一致的文章不能投放或重新发布；副编辑不能首次投放；主编辑投放任务自动入队；工作进程拒绝越权或路径篡改任务；本刊编辑不能主动操作主站、搜索页或其他期刊；只有超级管理员能全站发布、重试和回滚；manifest 规则不变。

### TASK-UI-01：角色化后台菜单和工作台

依赖：TASK-ROLE-02、TASK-PERM-01、TASK-REV-01。  
修改：菜单、Dashboard、模板和中英文文案。  
交付：超级管理员、主编辑、常务副编辑和副编辑四种后台视图。  
验收：每个角色只看到需要的入口；主编辑有待终审列表；移动端和桌面端无重叠或截断。

### TASK-MIG-01：旧账号与角色迁移

依赖：TASK-ROLE-01、TASK-PERM-01。  
修改：规划/应用命令、迁移模板和审计。  
交付：dry-run 报告、人工映射、旧 Group/直接权限清理、单级 Workflow 到两级 Workflow 的迁移报告、幂等应用和回滚说明。  
验收：不自动授予全刊权限；每个启用期刊主编辑校验通过；旧“项目总负责人”旁路和无终审来源的旧 approved 记录被列出；重复执行不重复创建任命。

### TASK-TST-01：权限和流程测试

依赖：全部功能任务。  
修改：users、journals、articles、placements、static_publish 测试和 E2E。  
交付：第 13 节全部场景的自动化测试。  
验收：完整测试集和浏览器验收通过。

### TASK-DOC-01：文档和运维手册

依赖：全部功能任务。  
修改：本目录 01～08、README、账号和角色迁移手册。  
交付：最终角色、字段、菜单、迁移、验收和回滚说明。  
验收：文档中的编码、权限、路由和实际代码一致。

### 12.1 实现入口索引

- 账号列表、新建和首次改密：`/admin/accounts/`、`/admin/accounts/new/`、`/admin/accounts/change-password/`；账号详情下提供暂停、恢复、停用和重置密码确认动作。
- 编辑团队：`/admin/journals/editorial-team/` 和 `/admin/journals/<journal_id>/editorial-team/`。
- 文章列表、待初审、待终审和审核详情：`/admin/articles/`、`/admin/articles/pending/`、`/admin/articles/final/`、`/admin/articles/<page_id>/review/`。
- 统一对象权限：`site_settings/access_control.py`；账号动作：`users/services.py`；任命/交接：`journals/editor_services.py`；两级审核：`articles/review_services.py`。
- 角色初始化：`python manage.py seed_roles`；迁移盘点：`plan_simple_role_migration`；幂等应用：`apply_simple_role_migration`。
- 迁移、复核和回滚步骤见 [简化账号与子期刊角色迁移和回滚手册](../operations/simple-role-migration-and-rollback.zh-CN.md)。

## 13. 自动化验收场景

### 13.1 账号

- [ ] 超级管理员可以创建普通编辑账号并一次性分配多个子期刊角色；
- [ ] 非超级管理员看不到账号管理，直接访问返回 403；
- [ ] 相同邮箱不同大小写不能重复创建；
- [ ] 新账号首次登录必须修改临时密码；
- [ ] 暂停、停用和重置密码后旧会话立即失效；
- [ ] 密码和密码摘要不进入审计日志；
- [ ] 多个维护人员使用实名账号，不共享同一超级管理员登录名。
- [ ] 子期刊编辑登录账号加入基础访问技术组，但没有全站文章和期刊模型权限；
- [ ] 旧“项目总负责人”Group 和直接审核权限迁移后不再产生平台旁路；
- [ ] 最后一个有效超级管理员不能被停用、暂停或撤销角色；
- [ ] 停用、重置密码、角色撤销和账号状态变更均撤销旧会话并写审计。

### 13.2 子期刊角色

- [ ] 每个启用子期刊恰好一名有效主编辑；
- [ ] 同一子期刊不能存在两名有效常务副编辑；
- [ ] 一个子期刊可以配置多名副编辑；
- [ ] 副编辑未选择职责时不能保存；
- [ ] A 刊主编辑、副编辑不能查看或操作 B 刊后台对象；
- [ ] 主编辑可以调整本刊副编辑职责和公开排序，但不能创建账号或授予超级管理员；
- [ ] 角色到期、停用或账号暂停后权限立即失效；
- [ ] 主编辑交接在同一事务中结束旧任命、创建新任命和写审计；失败时不留下半套任命；
- [ ] 启用中的期刊不能没有主编辑；不能同时存在两名有效主编辑；
- [ ] 期刊 slug、状态、静态路径和主站导航不能由子期刊编辑修改；
- [ ] 已审核、已发布文章和审核记录不能硬删除。

### 13.3 初审和终审

- [ ] 副编辑、常务副编辑和主编辑可以执行本刊初审；
- [ ] 未分配文章只有第一个成功认领的编辑获得初审写权限；
- [ ] 主编辑或常务副编辑改派初审必须填写原因并写审计；
- [ ] 副编辑和常务副编辑不能执行终审；
- [ ] 超级管理员拥有平台维护权限但调用终审接口仍返回 403；
- [ ] 主编辑只能终审本人负责子期刊的文章；
- [ ] 初审通过后文章进入待终审，不进入正式发布；
- [ ] `pending_final` 必须存在同 revision 初审通过记录，不能由普通保存直接写入；
- [ ] 退回和拒绝时必须填写意见；
- [ ] 过期 revision 的初审和终审均失败；
- [ ] 审核记录不能更新或删除；
- [ ] 已终审文章修改正文或作者声明后重新进入草稿和审核；
- [ ] Wagtail 初审任务通过不会触发 `approved`；只有终审任务通过才能写入 `approved_version`；
- [ ] 两个并发审核请求只有一个成功，另一个返回 409 且不新增重复审核记录；
- [ ] 重复 request id 返回第一次结果，不重复写状态和审计；
- [ ] `workflow_approved` 没有同 revision 终审记录时失败并写失败审计。

### 13.4 编辑团队和作者声明

- [ ] 前台按主编、执行主编/常务副编辑、副主编/副编辑分组展示；
- [ ] 每位编辑的姓名、单位和顺序可以独立维护；
- [ ] 隐藏的编辑团队成员不出现在静态页面；
- [ ] 文章作者声明可以逐篇独立填写或留空；
- [ ] 作者声明显示在文章底部且不与编辑团队混合；
- [ ] 作者声明中的危险 HTML 和脚本不能进入预览或静态页面；
- [ ] 现有 `responsibility_statement` 历史数据按“作者声明”展示，未新增重复字段；
- [ ] 编辑团队只展示关联实名账号、任命有效且 `show_publicly=True` 的成员；

### 13.5 投放和发布

- [ ] 未终审文章不能创建正式投放；
- [ ] 只有主编辑可以首次创建本刊正式投放，任务自动入队且不等待发布管理员批准；
- [ ] 常务副编辑和副编辑不能首次创建或复制正式投放；
- [ ] 常务副编辑和具有文章维护职责的副编辑只能维护有历史静态发布时间的本刊文章；
- [ ] 已发布文章修改后重新进入两级审核，终审前不能重新发布；
- [ ] 主编辑和副编辑不能主动操作主站、全局栏目、搜索页、其他期刊或跨刊推荐位；
- [ ] 工作进程会重新校验任命、投放 ID 和精确路径，路径篡改任务失败；
- [ ] 只有超级管理员可以主动执行全站构建、失败重试和回滚；
- [ ] 选择性发布承接活动 manifest 中未修改的历史文章，不删除原有期刊文章；
- [ ] 新 release 构建失败时旧 current 保持不变；
- [ ] 已激活 manifest 不可原地修改；
- [ ] 发布和回滚均写入审计日志。

## 14. 测试和应用要求

开发阶段至少执行：

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest ai_author_forum/users/tests -q
python -m pytest ai_author_forum/journals/tests -q
python -m pytest ai_author_forum/articles/tests -q
python -m pytest ai_author_forum/placements/tests -q
python -m pytest ai_author_forum/static_publish/tests -q
ruff check ai_author_forum
black --check ai_author_forum
isort --check-only ai_author_forum
```

功能完成后按测试环境受控流程应用：

```bash
cd /opt/ai-author-forum-test-current

compose_test() {
  docker compose \
    --project-name ai-author-forum-test \
    --env-file /opt/ai-author-forum-test-shared/.env.test \
    -f docker-compose.production.yml \
    -f docker-compose.test.yml \
    "$@"
}

compose_test config >/dev/null
compose_test build web worker static-frontend
compose_test up -d web worker static-frontend nginx
compose_test exec web python manage.py migrate
compose_test exec web python manage.py seed_roles
compose_test exec web python manage.py check
compose_test exec web python manage.py makemigrations --check --dry-run
compose_test ps
```

涉及作者声明和编辑团队前台展示时，必须先通过正式文章审核和投放链路，再运行：

```bash
compose_test exec web python manage.py build_static_site
```

浏览器至少验收：

- `https://test-author.huixixi.top/admin/`；
- `/admin/accounts/` 和 `/admin/accounts/new/`；
- 子期刊编辑团队页面；
- 本刊待初审和待终审页面；
- `https://test-author.huixixi.top/en/journals/representation-learning/`；
- 一篇包含作者声明和编辑团队信息的静态文章；
- `https://test-author.huixixi.top/admin/placements/`；
- `/healthz/`。

## 15. 切换与回滚

1. 先部署加法迁移，不立即移除旧 Group；
2. 运行角色迁移 dry-run；
3. 确认超级管理员和每个子期刊的主编辑、常务副编辑、副编辑名单；
4. 应用任命并开启影子模式；
5. 验证四种角色的菜单、列表、初审、终审和投放边界；
6. 开启 `SIMPLE_JOURNAL_RBAC_ENABLED`；
7. 稳定一个发布周期后移除旧角色入口；
8. 回滚时关闭新权限开关，但不得删除新任命、审核和审计记录；
9. 静态前台回滚只切换到完整、已验证的旧 manifest；
10. 未经明确正式发布指令，不得操作 `64.90.31.87`。

## 16. 完成定义

- [ ] 后台系统角色只有超级管理员、主编辑、常务副编辑和副编辑；
- [ ] “项目总负责人”不再作为系统角色；
- [ ] 超级管理员可以创建和维护全部账号及子期刊任命；
- [ ] 每个启用子期刊恰好一名主编辑；
- [ ] 副编辑职责可以通过固定复选项分配；
- [ ] 副编辑初审、主编辑终审的服务端限制不可绕过；
- [ ] 编辑团队姓名、单位、角色名称和排序可以独立维护并静态展示；
- [ ] 现有 `responsibility_statement` 统一显示为作者声明并纳入 revision 审核；
- [ ] 两级 Wagtail Workflow 与 `ArticleReviewRecord`、文章状态和终审 revision 一致；
- [ ] 超级管理员不能通过全局 permission、`is_superuser` 或旧 Group 旁路完成终审；
- [ ] 编辑账号基础访问技术组与业务任命分离，QuerySet 和 service 均执行子期刊范围过滤；
- [ ] 主编辑交接、最后超级管理员保护、并发审核和重复请求规则均有自动化测试；
- [ ] 子期刊编辑的投放目标、期刊敏感字段和删除边界均有服务端校验；
- [ ] 正式投放、不可变 manifest 和 current 原子激活规则保持不变；
- [ ] 全部自动化测试、测试 Compose 和浏览器验收通过；
- [ ] 已记录迁移报告、测试结果、已知限制和回滚方案；
- [ ] 未执行任何未经授权的正式服务器操作。

## 17. 相关文档

- [01 系统架构与模块边界](./01-system-architecture.zh-CN.md)
- [02 核心业务流程](./02-core-business-workflows.zh-CN.md)
- [03 后台菜单与权限](./03-admin-menu-and-permissions.zh-CN.md)
- [04 数据模型与状态机](./04-data-models-and-state-machines.zh-CN.md)
- [05 静态发布开发与运维](./05-static-publishing-development-and-operations.zh-CN.md)
- [06 当前冲突与整改路线](./06-current-conflicts-and-remediation-roadmap.zh-CN.md)
- [07 待确认需求与验收标准](./07-open-decisions-and-acceptance-criteria.zh-CN.md)
- [简化账号与子期刊角色迁移和回滚手册](../operations/simple-role-migration-and-rollback.zh-CN.md)
