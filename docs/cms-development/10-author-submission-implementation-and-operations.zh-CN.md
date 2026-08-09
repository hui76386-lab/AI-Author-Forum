# 作者投稿角色实现与运维说明

本文说明 `09-author-submission-role-development-spec.zh-CN.md` 在当前项目中的实现入口、启用条件、迁移边界和验收方式。第 09 章仍是功能和验收标准；本文不放宽其中任何权限、安全或发布要求。

## 1. 实现边界

作者入口使用独立的 `/author/` 工作台，不授予 Wagtail 页面树访问权。对象级授权只来自有效 `ArticleAuthorship`，公开 `ArticleContributor`、文章 owner 字段、姓名或邮箱都不能用于推断登录权限。

正式链路保持不变：

```text
作者工作台 -> canonical ArticlePage 草稿/revision -> 初审 -> 终审
  -> 正式投放 -> 冻结构建快照 -> 不可变 manifest -> current 原子激活
```

作者创建、保存、改刊和提交必须分别调用 `create_author_submission()`、`save_author_submission()`、`change_author_submission_journal()` 和 `submit_author_submission()`。作者入口不得直接修改审核状态，不得创建投放，也不得触发静态发布。

## 2. 数据与权限入口

- 作者账号：`users.User.is_author`，账号同时必须 active，且至少拥有一条归属于 active 期刊的有效投稿关系。
- 期刊开放状态：`Journal.accepts_author_submissions`、关闭时间、期刊状态和有效主编辑共同决定；迁移默认值为关闭。
- 投稿授权：`ArticleAuthorship` 保存 owner/co-author、编辑权、通讯作者、接受和撤销时间；有效 owner 和有效通讯作者均有数据库唯一约束。
- 写操作幂等：`AuthorSubmissionOperation.request_id` 全局唯一，记录动作、操作者、文章、关系、revision 和结果。
- 审核快照：提交记录固定 revision、内容 SHA-256、投稿 owner、主投期刊和关系更新时间。
- 作者资源：封面限制为 JPEG/PNG/WebP 和 8 MiB，校验声明 MIME、解码格式、文件大小与 SHA-256，并保存扫描状态及文章专属 collection。

作者账号即使同时兼任编辑，两套权限也分别按作者关系和期刊任命计算。作者身份不会扩展编辑范围，编辑身份也不会让作者工作台看到没有关系的文章。

## 3. 状态、并发与错误契约

- `draft` 可由有效且可编辑的作者关系保存和提交。
- `submitted`、`pending_final`、`approved`、`placed`、`built`、`published` 均锁定作者编辑。
- 编辑退回后文章回到 `draft`，作者可生成新 revision 并重新提交；首次提交标记永久保留，作者不能自行改刊。
- 首次提交前只有有效 owner 可确认改刊；首次提交后的改投只能由原期刊主编辑或超级管理员通过受控入口执行。
- 所有保存、提交和转投使用 expected revision/state、数据库行锁和 request id。表单校验失败返回 400，权限或关闭期刊返回 403，对象越权返回 404，revision 或状态冲突返回 409，速率限制返回 429。

成功写入、表单失败、对象拒绝、并发冲突、状态转换、关系授予/撤销、审核意见查看和速率限制均写入 `AuditLog`。审计 metadata 只记录动作来源、对象 id、状态、错误类型和无敏感内容的字段名，不保存密码、上传内容、内部审核意见或作者可见意见正文。

## 4. 编辑端接口

本刊主编辑和超级管理员可以使用文章管理端的投稿关系和受控转投入口。共同作者默认只读，只有已接受邀请的共同作者才能获得编辑权。关系撤销使用时间戳，不删除历史关系、revision、审核记录或公开作者资料，并立即撤销该账号现有会话。

退回或拒绝时，编辑端必须分别填写内部讨论和作者可见理由。作者工作台只读取 `author_visible_comment`，不得展示 reviewer 身份、内部 comment、编辑负载或改派信息。

## 5. 迁移与启用

迁移文件：

- `users/0004_user_is_author.py`
- `journals/0016_journal_accepts_author_submissions_and_more.py`
- `articles/0014_articlepage_first_submitted_at_and_more.py`

迁移只增加结构，不自动把既有用户设为作者，不自动开放任何期刊，也不根据作者姓名猜测账号。上线前先运行只读报告：

```bash
python manage.py report_author_authorship_migration --output-format json
```

报告只把明确的 Wagtail owner user id 列为候选，并列出无明确 owner、无效账号、重复通讯作者等问题；命令不写数据库。人工确认后，关系只能由主编辑或超级管理员通过受控入口逐条授予。

回滚代码或停用功能时，先关闭期刊投稿标志并撤销作者会话；不得删除关系、审核快照、幂等记录或审计记录，不得修改已激活 manifest。数据库迁移回退会丢失新增历史结构，因此已产生作者投稿数据后不作为常规回滚手段。

## 6. 验收

最低自动检查：

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest ai_author_forum/articles ai_author_forum/users ai_author_forum/site_settings
ruff check ai_author_forum/articles ai_author_forum/journals ai_author_forum/site_settings ai_author_forum/users
black --check ai_author_forum/articles ai_author_forum/journals ai_author_forum/site_settings ai_author_forum/users
isort --check-only ai_author_forum/articles ai_author_forum/journals ai_author_forum/site_settings ai_author_forum/users
```

Compose 验收必须使用根目录 `AGENTS.md` 指定的测试项目名、环境文件和两份 Compose 配置。浏览器至少覆盖作者登录、桌面和移动工作台、创建/保存/改刊/提交、公开退回意见、重新提交、锁定提示、他人对象拒绝、编辑审核、关系管理、受控转投和投放入口。

完整发布链路验收使用测试数据完成初审、终审和正式投放后再运行 `build_static_site`，校验文章页面、manifest inventory/SHA-256 和 current。不得把作者草稿或测试数据直接写入静态目录，也不得把测试数据库、媒体或环境文件带入发布产物。
