# 05 静态发布开发与运维

## 1. 运行边界

生产前台必须由 Nginx/CDN/静态服务器直接服务已激活目录，不能依赖 Django 运行时读取文章数据库。

推荐目录：

```text
published/
  releases/<release-id>/     # 每次不可变发布版本
  current/                   # 当前激活版本，可由目录链接或切换机制实现
output/                      # 构建中间产物，不直接对外服务
```

后台、预览、健康检查和管理 API 可以访问 Django；普通文章详情、首页、A-Z、栏目页和子期刊首页应访问 `published/current`。

## 2. 发布对象

| 对象 | 作用 |
| --- | --- |
| `StaticPublishJob` | 一次构建/发布/重试/回滚任务的生命周期 |
| `StaticPublishPageResult` | 逐页面成功、失败、耗时和错误 |
| `StaticManifest` | release 的文件清单、校验摘要、页面数量和输入版本 |
| `AuditLog` | 操作人、动作、目标、结果、request id、release id |

`StaticManifest` 激活后不能原地修改。修复必须生成新 release，回滚则激活旧的已验证 manifest。

## 3. 构建流程

```text
读取已审核文章/已生效投放/导航/站点设置
  -> 创建 release snapshot
  -> 渲染 HTML
  -> 复制/生成静态资源
  -> 写逐页面结果
  -> 生成 manifest
  -> 校验页面与资源
  -> 原子激活 current
  -> 回写业务状态和审计日志
```

构建输入必须冻结，避免构建中有人修改文章或投放导致同一 release 内容不一致。每个页面结果至少包括 URL/path、对象类型、对象 id、状态、错误摘要和耗时。

只有账号状态正常且属于业务组“超级管理员”的实名账号可以创建构建、激活、失败重试或回滚。主编辑、常务副编辑、副编辑、技术组成员、旧发布 Group、Django 直接 permission 和单独的 `is_superuser=True` 都不能执行这些动作。后台视图、Celery worker 和 management command 必须调用同一权限 service。

## 4. 激活策略

- 新 release 写入独立目录；
- 所有必需页面和关键资源通过校验后才允许激活；
- current 切换必须是原子动作；
- 激活失败保留旧 current；
- 不允许先删除旧 current 再写新版本；
- 激活、失败、重试、回滚均写 `AuditLog`。

## 5. 失败与重试

失败记录必须区分：

- 输入数据错误；
- 模板渲染错误；
- 图片/资源缺失；
- 输出目录权限错误；
- manifest 校验错误；
- current 切换错误；
- 外部存储或网络错误。

重试应支持页面级重试和任务级重试，但必须使用新的 attempt id 并关联原始 job。重复重试不得重复产生有效投放或重复审计业务事件。

自动发布任务的 `coalesce_key` 必须非空并表达实际合并范围。投放批次使用 `placement-batch:<batch_uuid>`，延迟合并的投放变更使用操作者作用域键；不得让多个待处理自动任务依赖模型默认空字符串。运维检查应同时关注长时间停留在 `pending` 或 `running` 的任务以及 broker 中缺失的任务消息。

发布任务显示 `0/0` 且状态失败时，通常表示构建前内容检查已经阻断任务，尚未进入逐页生成阶段，不表示“没有页面所以发布成功”。任务详情必须优先展示结构化 blocker、对应路径和修复方向；修复后创建新任务或重试，旧 current 保持不变。

### 5.1 本刊发布范围

本刊发布用于新刊上线和日常单刊更新。目标解析由静态 target provider 统一负责，至少包括：

- 子期刊总目录 `/journals/` 及所有启用语言版本；
- 目标子期刊主页、导航、栏目、期次和静态信息页；
- 主属或相关关系指向目标子期刊且满足审核、投放条件的文章详情页。

本刊发布依赖现有活动 manifest 作为增量基线；没有活动 manifest 时必须先完成一次全站发布。构建前主编辑、导航、期次、栏目最低内容和投放检查应限制在目标子期刊，不得因其他无关子期刊未就绪而阻断。本刊任务只有在新 manifest 完整校验并切换为 current 后才算完成，后台应同时提供期刊工作台、前台主页和总目录验证入口。

投放自动发布根据活动 manifest 决定范围：目标子期刊已上线时使用受影响路径增量发布；目标子期刊尚未上线时提升为本刊完整发布，并执行该刊内容就绪检查。非全站任务的分类投放一致性校验只覆盖本次任务依赖的子期刊；全站任务仍执行全局一致性校验。

## 6. 回滚

回滚前检查：

- 目标 manifest 存在且完整；
- 目标版本不是失败或构建中的版本；
- 目标版本的资源路径仍可访问；
- 当前数据库状态与目标版本差异已展示给操作者；
- 操作者是有效超级管理员，并为回滚提供明确原因。

回滚后检查：

- current 指向目标 release；
- 前台首页、文章详情、栏目页和子期刊页返回 200；
- manifest 内容与当前目录一致；
- 文章/投放发布状态没有被静默误写；
- 审计日志记录目标 release、操作者和原因。

## 7. 与文章和投放的同步

`after_publish_page` 和 `workflow_approved` 当前都可能触发栏目投放同步。开发时不得在两个 hook 中分别实现业务逻辑。应收敛为：

```text
hook -> idempotent sync service -> one audit event -> one revision/status update
```

同步 service 必须具备幂等键，推荐使用：

```text
(article_id, approved_revision_id, placement_scope, operation)
```

重复触发只返回已有结果，不重复创建投放、不重复生成 request id，不把成功状态覆盖为 pending。

## 8. 运维命令和环境

本地开发：

```powershell
cd "E:\AI Author Forum\news-template"
.\.venv\Scripts\python.exe scripts\start_dev.py
```

常用检查：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py build_static_site
npm run build:prod
npm run test:e2e
```

`build_static_site` 使用 `--actor <username-or-email>` 标识承担责任的有效超级管理员；只有系统中恰好一名有效超级管理员时才可省略。重试使用 `--retry-job <job-id>`，回滚使用 `--rollback <release-id> --rollback-reason <至少5个字符的原因>`。命令行不因脱离 HTTP 请求而绕过业务角色和审计。

上线前必须补充真实 Docker/PostgreSQL/Redis/Celery/Nginx 联合验收。仅通过 Django `check` 或本地 SQLite 测试，不能证明生产静态切流和回滚已经可用。

## 9. 生产安全检查

`manage.py check --deploy` 当前仍提示 HSTS、SSL redirect、SECRET_KEY、Secure Cookie 和 DEBUG 配置风险。生产环境必须通过环境变量和部署配置关闭 DEBUG、使用强随机 SECRET_KEY、启用 HTTPS、设置安全 Cookie、配置 HSTS，并在真实域名和反向代理环境下验证。

## 10. 2026-07-23 发布完整性实现

### 10.1 冻结构建输入

构建器先在事务中发现并渲染所有目标，形成包含目标、HTML bytes 或异常、耗时的不可变快照；`manifest.json` 顶层 `input_snapshot_at`（并同步进入数据库 manifest metadata）记录快照时点。PostgreSQL 根事务使用 `REPEATABLE READ, READ WRITE`，渲染完成后才写 staging，避免长时间文件 I/O 扩大数据库一致性事务。SQLite 测试可验证流程，但不能替代 PostgreSQL 行锁和隔离级别验收。

### 10.2 release 与 manifest 完整性

激活和回滚前均执行全量校验：

- `manifest.json` 必须存在且为合法 JSON object；
- inventory 与磁盘文件集合完全一致，无重复路径，manifest 不得把自身列入哈希清单；
- 所有路径必须位于 release 内，不得包含路径穿越或符号链接；
- 每个文件的 size 和 SHA-256 必须匹配；
- 回滚时磁盘 manifest 的 version、previous_version、files、metadata 必须与数据库 `StaticManifest` 一致。

`StaticManifest` 的 version、job、previous_version、files、metadata、created_at 为不可变字段；模型保存和 QuerySet `update()` 都禁止修改，记录也禁止删除，只允许切换 `is_active`。

### 10.3 审计、激活与补偿

- 审计写入失败会抛出 `AuditWriteError`，高风险动作不能在缺少审计时宣称成功。
- 发布/回滚成功审计与数据库 manifest 激活位于同一事务；任一步失败都会恢复旧 filesystem current、回滚数据库状态并把 job 标记为 failed。
- retry 只写 RETRY 语义审计，不再重复制造 PUBLISH 事件。
- Playwright 默认不复用占用端口的未知服务；只有显式设置 `PLAYWRIGHT_REUSE_EXISTING_SERVER=1` 才允许复用。可用 `STATIC_E2E_PORT` 选择隔离端口，并可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指定受控 Chromium。

## 11. 作者投稿发布边界

作者工作台产生的 draft、revision 和审核提交不是静态发布输入。只有文章完成初审、终审并由有权编辑创建有效正式投放后，才能进入冻结快照。构建前检查不得因文章具有 `ArticleAuthorship` 而放宽审核、Wagtail live、投放、生效时间或资源完整性条件；作者账号不得成为 `build_static_site` actor。回滚只切换到已验证 manifest，不修改作者关系、投稿 revision 或审核历史。

## 12. 静态资源并发与英文输出约束

### 12.1 `collectstatic` 与发布复制

发布器复制 `STATIC_ROOT` 到 release staging 时，`collectstatic --clear` 可能短暂删除并重新写入带哈希的 Wagtail 资源。发布器对这类 `FileNotFoundError`/`shutil.Error` 进行有限、递增间隔重试；重试耗尽后任务失败并提示静态资源在复制期间发生变化，不激活不完整 release。运维侧仍应避免把 `collectstatic` 与正式静态构建安排在同一时间窗，失败后先确认静态资源目录稳定，再使用新的 attempt 重试。

### 12.2 英文页面的数据边界

英文导航标签必须按 `NavigationGroup.code`、`NavigationItem.managed_code` 等稳定代码从受审阅词典解析；期刊和编辑身份优先使用独立英文字段。后台兼容层只翻译已知的旧中文界面标签，未知文本必须原样保留，因为它可能是文章标题、姓名、错误详情或其他需要操作者区分的业务数据。禁止对整页 HTML 使用“未知中文统一替换为占位符”的策略，也禁止让后台清洗器处理 `/en/` 前台正文。
