# 简化账号与子期刊角色迁移和回滚手册

本文只执行 08 号任务书定义的目标模型：平台级 `super_admin`，以及按子期刊任命的 `chief_editor`、`executive_editor`、`associate_editor`。不得根据旧 Group 推断期刊范围，也不得把旧“项目总负责人”或“发布管理员”自动升级为超级管理员。

## 1. 输入和前置条件

- 使用已验证且与目标环境一致的代码版本；先完成数据库备份和应用版本回滚准备。
- 映射文件不得进入 Git，也不得包含密码、token、数据库连接串或其他密钥。
- 以 [`simple-role-migration.example.json`](./simple-role-migration.example.json) 为结构填写人工确认映射。
- `super_admins` 明确列出要授予平台维护权限的实名账号。
- `assignments` 的每一行明确写出账号、期刊 slug、角色、职责和公开资料；副编辑至少一项固定职责。
- `deactivate_users` 只列出负责人已确认停用的账号。
- 每个启用子期刊必须解析为恰好一名有效主编辑；同一账号不能同时是超级管理员和子期刊编辑。

## 2. 只读盘点和 dry-run

先生成不带映射的旧权限盘点。报告包含账号、旧 Group 成员、模型权限、页面权限、资源库权限、直接审核权限、待审文章和无有效终审来源的旧 approved 文章：

```bash
python manage.py plan_simple_role_migration \
  --output /受控路径/simple-role-preflight.json
```

负责人填写映射后执行带映射校验：

```bash
python manage.py plan_simple_role_migration \
  --mapping /受控路径/simple-role-mapping.json \
  --output /受控路径/simple-role-reviewed.json

python manage.py apply_simple_role_migration \
  /受控路径/simple-role-mapping.json \
  --actor '<有效超级管理员用户名或邮箱>'
```

第二条命令不带 `--apply`，只输出 dry-run，不写数据库。以下任一情况均阻断应用：未知账号或期刊、非法角色或职责、重复任命、超级管理员与期刊编辑身份重叠、副编辑无职责、停用与授权冲突、任一启用期刊不能解析为恰好一名主编辑。

人工复核至少确认：

- `mapping_validation.errors` 为空；
- `chief_validation` 中每个启用期刊均为 `valid=true`；
- `legacy_project_lead_members` 和 `direct_review_permission_users` 已逐一决定去向；
- `legacy_approved_without_final_source` 已进入人工复核，不被自动视为新两级审核通过；
- 没有任何账号因旧 Group 被自动授予全部子期刊。

## 3. 应用和复核

只有完成上述复核后才执行：

```bash
python manage.py apply_simple_role_migration \
  /受控路径/simple-role-mapping.json \
  --actor '<有效超级管理员用户名或邮箱>' \
  --apply
```

应用命令在单一数据库事务中完成超级管理员授权、期刊任命、确认停用、旧 Group/直接审核权限清理、两级 Workflow 接入和迁移审计。失败时整笔事务回滚。命令可以使用同一映射重复执行，不应重复创建有效任命。

应用后再次生成报告并执行检查：

```bash
python manage.py plan_simple_role_migration \
  --mapping /受控路径/simple-role-mapping.json \
  --output /受控路径/simple-role-post-apply.json
python manage.py check
python manage.py makemigrations --check --dry-run
```

随后按 08 号任务书验证四种角色的菜单、跨刊列表、认领、初审、终审、投放、账号管理、静态发布和审计边界。旧 Group 可以保留一个稳定发布周期，但权限必须为空且不能成为业务授权来源。

## 4. 回滚

应用命令执行中失败不需要数据回滚，因为数据库事务不会留下部分任命。应用成功后的回滚不得删除新 `JournalEditorAssignment`、`ArticleReviewRecord` 或 `AuditLog`，不得把无终审来源的旧 approved 文章伪造成已终审。

若新权限版本需要回退：

1. 停止继续修改账号、任命和审核状态，保留错误、审计和迁移报告；
2. 保留 `simple-role-preflight.json`、人工映射、应用后报告、来源版本和数据库备份标识；
3. 回退到已验证的上一应用 release，不直接编辑正式运行目录；
4. 若上一 release 依赖旧 Group，按 preflight 报告中逐项记录的 `permissions`、`page_permissions` 和 `collection_permissions` 进行独立评审后恢复旧授权；禁止恢复报告之外的权限或把旧角色扩展到全部期刊；
5. 新任命、两级审核记录和审计记录继续保留为历史数据；旧版本只能忽略这些记录，不能删除或改写；
6. 静态前台只通过完整、已验证的旧 manifest 原子回滚，不从数据库草稿重建旧页面；
7. 回滚完成后验证后台登录、旧权限最小可用范围、首页、文章、栏目、子期刊、静态资源、健康检查和审计记录。

恢复旧 Group 授权属于临时应急动作，必须由正式发布负责人基于迁移前报告逐项审批和审计；项目不提供自动放大旧权限的回滚命令。

## 5. 环境边界

本手册中的命令先在测试环境执行并形成版本、报告和验收记录。未经明确正式发布指令，不得对 `64.90.31.87` 执行 SSH、上传、迁移、服务重启或任何生产变更；不得把测试数据库、媒体、`.env`、日志或本地构建目录作为发布产物。

## 6. 测试环境验收记录（2026-08-05）

本记录只对应当前测试服务器和 Compose 项目 `ai-author-forum-test`，不代表正式环境已经发布。

- 测试镜像：`web` 为 `sha256:6bcfeab65165cdbcf262567793f9dbc63723fec2d3791defe544dd295aa6059e`，`worker` 为 `sha256:9c26afb9b2c9542c3cc9ed532c4c0a84117056924c9d3f64e2d4410a593f2669`，`static-frontend` 为 `sha256:68fa301026beccb0cd2699df9dc76a38bb2e22b417ff1288086ca8099f9cfe8b`；
- 静态验收 release：全量基线 `20260805T022040832664Z-job52` 通过正式审核、正式投放、manifest 校验和 `current` 原子激活生成；job57 出现期刊文章列表回归后，job58 已按完整 manifest 原子回滚，当前活动版本为 job52；
- `python manage.py check`、`makemigrations --check --dry-run`、迁移、`seed_roles`、`collectstatic`、Ruff、Black、isort 和 `npm run build:prod` 均通过；
- SQLite 全量测试为 `695 passed, 3 skipped, 354 subtests passed`；三个因 SQLite 不支持行锁而跳过的审核/栏目并发用例在独立 PostgreSQL 15 中为 `3 passed`；
- 固定 HTML Playwright 验收为 `29 passed`；测试 Compose Nginx 上的超级管理员、主编辑、常务副编辑、副编辑浏览器权限矩阵通过，公开期刊页和文章页的编辑团队、作者声明、英文角色名称及桌面/移动端无溢出检查通过；
- 测试环境已启用 `SIMPLE_JOURNAL_RBAC_ENABLED=True`，且 `SIMPLE_JOURNAL_RBAC_SHADOW_MODE=False`；Web、Worker、PostgreSQL、Redis、静态前台和 Nginx 均处于运行状态，Web 与静态前台健康检查通过；
- 公开验收地址 `https://test-author.huixixi.top/`、`https://test-author.huixixi.top/en/journals/representation-learning/` 和 `https://test-author.huixixi.top/admin/placements/` 已通过宿主 Nginx、TLS 和 Basic Auth 验收；共享测试密码已轮换并与 htpasswd 同步。外部健康接口、期刊页、文章页、后台登录入口和移动端截图均通过；四角色登录后的权限矩阵在同一 Compose Nginx 的本机入口通过；
- Redis/Celery 实际往返已通过：Worker `ping` 返回 `pong`，经 broker 投递的 job52 非自动发布任务返回 `SUCCESS` 和 `skipped=True`。该验收同时修复并覆盖了 PostgreSQL 对可空 `triggered_by` 外连接执行无范围 `FOR UPDATE` 的问题，任务现在只锁 `StaticPublishJob` 自身；
- 静态回滚演练已通过：job53 校验完整 manifest 后从 job52 原子切换到已验证的 job51，数据库活动 manifest、磁盘 `current`、外部健康接口和文章页一致；job54 随后恢复 job52。两次操作状态均为 `rolled_back`，各有一条成功回滚审计。模拟编辑选择性发布 job57 暴露文章列表回归后，job58 再次校验 job52 的完整 manifest 并原子恢复，回滚状态和成功审计均已落库。

负责人随后明确要求在当前测试环境为所有期刊临时生成模拟编辑。已通过账号和任命 service 在单一事务中创建 360 个唯一模拟实名账号，为 120 个启用期刊分别配置一名 `chief_editor` 和两名 `associate_editor`；副编辑均分配五项固定职责，账号均强制首次登录改密，且没有模拟账号进入“超级管理员”组。原有 `representation-learning` 主编辑通过原子交接 service 替换，原有副编辑任命按 service 结束；原有一名 `executive_editor` 不在本次“两名副编辑”指令范围内，继续保留。账号创建和任命目标分别有 360 条和 363 条权限审计；为避免中文模拟姓名在英文静态本地化中变成不可读占位文本，又通过公开资料 service 为 360 条任命生成唯一英文公开姓名和明确的 `(Simulated)` 标记，并逐条写入审计，后台随机中文姓名保持不变。

显式模拟映射包含 360 条任命和 120 个唯一期刊，`plan_simple_role_migration` 与 `apply_simple_role_migration` 不带 `--apply` 的只读 dry-run 均通过：映射错误、警告均为 0，120/120 个启用期刊的主编辑校验有效。迁移报告同时修复了空“项目总负责人” Group 被错误列为一个 `null` 成员的问题；当前真实旧项目负责人成员为 0。受控文件均为 `0600`，包括 `/opt/ai-author-forum-test-shared/simple-role-mapping-simulated-20260805.json`、`/opt/ai-author-forum-test-shared/simulated-editor-credentials-20260805.json` 和 `/opt/ai-author-forum-test-shared/simple-role-simulated-post-create-20260805.json`，不得进入 Git 或用于正式环境。

尚未执行迁移命令的 `--apply`：报告仍列出 1205 条无有效终审来源的旧 approved 文章，负责人尚未完成人工复核，系统也未将其伪造成新两级审核通过。当前 1488 条投放中只有 2 条满足新规则要求的有效终审来源；因此任何重新渲染期刊首页的发布都会把迁移前静态快照中的历史文章过滤掉。全站构建 job55 被 readiness 正确阻断；选择性 job57 虽完成文件和 manifest 校验，但造成中文 119 个期刊首页出现空列表，属于内容验收失败。发现后立即通过 job58 回滚到 `20260805T022040832664Z-job52`：中英文各 120 个期刊首页均无空列表提示，每种语言恢复 2392 个文章链接；抽查多个期刊为 200 且每刊显示 10 张文章卡，桌面和移动端无横向溢出。模拟账号与任命继续保留在后台数据库，但在历史文章复核和投放迁移完成前不得再次发布这些期刊首页。未对 `64.90.31.87` 执行上传、登录、迁移或服务重启。
