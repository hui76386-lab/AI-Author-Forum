# 统一登录入口与作者工作台权限隔离开发任务书

> 文档版本：v1.0
> 编制日期：2026-08-07
> 适用项目：AI Author Forum CMS
> 文档状态：可直接拆分开发和验收
> 前置基线：`08-account-and-editorial-access-development-spec.zh-CN.md`、`09-author-submission-role-development-spec.zh-CN.md`、`10-author-submission-implementation-and-operations.zh-CN.md`

## 1. 背景与当前缺陷

第 09 章已经实现作者账号、对象级投稿关系和 `/author/` 作者工作台，但当前首次临时密码修改流程存在权限域错误：

```text
/author/login/
  -> /admin/accounts/change-password/
  -> /admin/login/?next=/admin/accounts/change-password/
  -> /admin/accounts/change-password/
  -> 无限重定向
```

根因是纯作者 `is_staff=False`，而 `/admin/accounts/change-password/` 注册在 Wagtail `/admin/` 下。Wagtail 后台访问保护会拒绝纯作者，强制改密中间件又把作者送回后台改密页。密码验证本身成功，但作者无法完成首次改密，也无法进入工作台，因此当前验收必须判定失败。

本任务书解决登录体验和改密边界，不重做第 09 章已经通过验证的投稿状态机、审核、投放和静态发布链路。

## 2. 已确认的最终方案

### 2.1 统一入口，分离权限域

在现有品牌登录页提供两个明确入口：

```text
编辑与运营登录 -> Wagtail /admin/ 编辑后台
作者投稿登录   -> /author/ 作者投稿工作台
```

两个入口使用同一 `User`、同一 Django 认证后端、同一 Session 和同一密码策略；入口选择只决定登录后的工作区，不授予额外权限。

作者登录按钮可以放在现有 `/admin/login/` 页面中，也可以使用同一品牌的登录选择页，但不得把作者账号伪装成 Wagtail 后台用户。作者登录页仍保留可直接访问的 `/author/login/`，供深链接、移动端和自动化测试使用。

### 2.2 作者不进入 Wagtail 全局后台

作者登录后进入独立的 `/author/` 工作台。工作台可以复用品牌、颜色、排版和导航视觉，但不能复用 Wagtail 页面树、图片库、文档库或后台通用 API 的权限边界。

禁止通过以下方式实现作者入口：

- 把纯作者设置为 `is_staff=True` 后再隐藏菜单；
- 只依赖模板隐藏“审核、投放、发布、设置”等菜单；
- 让作者进入现有 Wagtail 文章管理页后依赖前端按钮禁用；
- 让作者通过 `/admin/` 登录，再根据用户名或 URL 猜测是否为作者；
- 为作者复制一套独立账号表、密码表或认证后端。

### 2.3 中立的首次改密入口

首次临时密码修改改为不属于 Wagtail 的中立路由：

```text
GET/POST /account/change-password/
```

该视图必须允许已认证的 active 用户访问，不要求 `is_staff`，不依赖 Wagtail admin wrapper。改密成功后按本次登录入口返回：

- 作者入口登录的用户返回 `/author/`；
- 编辑入口登录的用户返回 `/admin/`；
- 同时具备作者和编辑权限的用户按本次入口返回，不根据用户名猜测。

旧 `/admin/accounts/change-password/` 只保留兼容跳转或编辑端兼容入口；新流程、middleware、模板和测试不得再把纯作者送入该路径。

## 3. 目标路由与会话契约

### 3.1 路由

| 方法 | 路径 | 未登录 | 纯作者 | 编辑/超级管理员 |
| --- | --- | --- | --- | --- |
| `GET` | `/admin/login/` | 200，显示编辑登录和作者入口 | 200，不自动登录 | 200 |
| `GET/POST` | `/author/login/` | 200/认证后跳转 | 认证成功后进入作者流程 | 非作者或无有效投稿关系时拒绝 |
| `GET/POST` | `/account/change-password/` | 跳转到带安全 `next` 的统一登录选择入口 | 200/POST 成功 | 200/POST 成功 |
| `GET` | `/author/` | 302 到 `/author/login/` | 200 | 非作者 403 或 404 |
| `GET` | `/author/submissions/` | 302 到 `/author/login/` | 200，仅自己的有效关系 | 非作者 403 或 404 |
| `GET` | `/author/dashboard/` | 同 `/author/` | 302 到 `/author/` 或 200 别名页 | 非作者 403 或 404 |
| `GET` | `/author/articles/` | 同 `/author/` | 302 到 `/author/submissions/` 或 200 别名页 | 非作者 403 或 404 |
| `GET` | `/admin/` | 302 到 `/admin/login/` | 403，不得进入改密循环 | 按既有 Wagtail 权限 |

`/author/dashboard/` 和 `/author/articles/` 如果保留兼容别名，必须只做明确的内部 302，不得复制业务逻辑，也不得改变对象级查询。

### 3.2 安全的 `next` 和登录范围

- `next` 只能是本站内部、与当前入口匹配的路径；禁止外部 URL、协议相对 URL 和跨工作区跳转。
- `/author/login/?next=/admin/...` 必须丢弃或改为 `/author/`，不能借作者入口进入后台。
- `/admin/login/?next=/author/...` 对编辑入口不授予作者权限；登录后按编辑工作区处理。
- 认证成功后在 Session 中记录短生命周期的 `login_scope=author|admin` 或等价不可伪造范围标记；该标记只决定改密完成后的返回地址，不参与授权判断。
- 登出时清理 `login_scope`、改密返回地址和任何临时登录状态。

## 4. 登录页面任务

### 4.1 编辑登录页

修改 `templates/wagtailadmin/login.html` 或等价的 Wagtail 登录模板：

- 保留现有编辑/运营账号登录表单和行为；
- 增加清晰的次级按钮“作者投稿登录”，链接到 `/author/login/`；
- 中文和英文界面均有对应文本；
- 不在编辑表单中增加作者角色选择字段，也不根据输入用户名自动切换入口；
- 按钮必须可键盘操作、可读屏识别，不能只依赖颜色或图标；
- 作者入口链接不能带未经校验的外部 `next`。

### 4.2 作者登录页

保留 `/author/login/`，并补充：

- 返回“编辑与运营登录”的链接；
- 明确说明该入口用于投稿工作台，不是 CMS 编辑后台；
- 错误时不泄露账号是否存在、是否有文章或期刊信息；
- 认证成功后先处理首次改密，再进入作者工作台；
- 登录成功、失败、无有效投稿关系和速率限制均按现有审计规范处理。

### 4.3 账号状态检查

登录与工作台访问必须同时检查：

1. `is_active=True`；
2. `account_status=active`；
3. `is_author=True`；
4. 至少存在一条未撤销、文章对象有效且用户仍具备有效作者身份的 `ArticleAuthorship`。

没有有效投稿关系的作者账号可以保留登录账号，但不能进入工作台，应返回不泄露对象信息的明确提示或 403。

## 5. 中立首次改密任务

### 5.1 视图与模板

新增或迁移以下代码边界：

- `ai_author_forum/urls.py` 注册 `/account/change-password/`；
- `users/views.py` 将首次改密视图从 Wagtail 专属模板中解耦；
- 新模板不得继承 `wagtailadmin/base.html`，使用公共或作者安全模板；
- CSS、JS、翻译和静态资源必须能在非 `/admin/` 路径下加载；
- `docker/nginx.conf` 将 `/account/` 转发到 Django；不得让静态前台或 Nginx 直接返回伪 404；
- 旧 `/admin/accounts/change-password/` 只作为兼容跳转，不再是纯作者的可达目标。

### 5.2 改密行为

- 只允许已认证用户提交；
- 必须验证旧密码、新密码两次输入和 Django 密码策略；
- 成功后设置 `must_change_password=False`，调用 `update_session_auth_hash` 保持当前 Session；
- 成功后按 `login_scope` 返回作者工作台或编辑后台，并清理一次性范围标记；
- 失败时保留表单和字段级错误，不改变密码；
- 受现有凭据限流保护；
- 成功和失败均写 `AuditLog`，不得写入旧密码、新密码、密码哈希、Session 内容或 token；
- 重置密码服务撤销旧会话后，用户必须能用新临时密码完成作者入口登录和首次改密。

### 5.3 中间件顺序

`RequiredPasswordChangeMiddleware` 必须遵守以下顺序：

1. 对未认证用户不执行首次改密跳转；
2. `/account/change-password/` 是所有已认证角色的允许路径；
3. `must_change_password=True` 的作者请求只跳转到 `/account/change-password/`，不能跳到 `/admin/`；
4. 纯作者访问 `/admin/` 仍返回 403，但该 403 不能被改密逻辑覆盖成重定向；
5. Wagtail 资源允许列表不得把完整后台页面开放给纯作者；
6. 对任意首次改密场景，连续跟随重定向不得超过 3 跳且不得出现重复 URL 循环。

纯作者触发上述 403 时必须渲染受控的作者工作台提示页，提供返回 `/author/` 和安全退出操作；不得返回裸文本错误页，也不得展示任何编辑后台导航或对象信息。

## 6. 作者工作台任务

### 6.1 工作台外壳

作者工作台可以采用与截图中后台一致的品牌语言，但导航只保留：

- 我的投稿；
- 新建投稿；
- 开放投稿期刊/投稿须知；
- 账号和修改密码；
- 退出登录。

不得显示或仅靠 CSS 隐藏以下入口：审核、投放、静态发布、期刊运营、账号管理、站点设置、媒体库、文档库、审计日志和全局搜索。

### 6.2 功能范围

作者工作台必须继续使用第 09 章既有 service 和对象级查询，覆盖：

- 投稿列表和状态；
- 新建、保存草稿、改刊、提交审核；
- 公开退回意见和重新提交；
- revision 历史、锁定状态和并发冲突；
- 自己文章的作者关系和公开作者声明；
- 受限的封面/附件上传；
- 桌面端和 320px 以上移动端布局。

作者工作台不新增审核、投放、manifest、发布或期刊管理 service。

### 6.3 双角色账号

同一个用户可以同时具有作者关系和期刊编辑任命：

- 从“作者投稿登录”进入作者工作台；
- 从“编辑与运营登录”进入 Wagtail 编辑后台；
- 两个入口共享认证账号，但每个请求仍执行对应权限检查；
- 改密完成后的返回位置遵循本次入口；
- 作者工作台不能显示编辑菜单，编辑后台不能因为存在作者关系而显示作者私有投稿入口，除非明确提供无权限旁路的链接。

## 7. 权限和安全验收矩阵

| 场景 | 期望结果 |
| --- | --- |
| 未登录访问 `/author/` | 302 到 `/author/login/`，保留安全内部 `next` |
| 纯作者登录且无需改密 | 302/200 进入 `/author/` |
| 纯作者首次登录 | 302 到 `/account/change-password/`，该页 200，不出现 `/admin/login/` |
| 纯作者完成改密 | 302 到 `/author/`，随后 200 |
| 纯作者访问 `/admin/` | 403 或受控拒绝，不得跳转循环 |
| 纯作者访问 `/admin/accounts/` | 403，不得泄露账号列表 |
| 纯作者访问其他文章 | 404 或 403，不泄露标题、期刊、作者和状态 |
| 编辑账号从 `/admin/login/` 登录 | 保持现有后台流程，不重定向到作者工作台 |
| 无有效投稿关系的 `is_author=True` 用户 | 作者入口拒绝或工作台 403，不显示文章信息 |
| 已暂停/停用作者 | 不能登录，既有作者 Session 失效 |
| 作者提交外部 `next` | 丢弃并返回本入口默认页 |
| 失败密码连续尝试 | 触发既有 429 限流和审计，不锁死其他账号 |

权限判断必须落在 view、service 和关键查询层；菜单、按钮和 CSS 只负责展示，不能作为安全边界。

## 8. 开发任务拆分

### T-01 重现并锁定重定向缺陷

目标：先用真实登录流程复现纯作者首次改密循环，建立回归测试基线。

涉及：`users` 测试、`articles` 作者测试、Playwright 作者 E2E。

完成标准：使用 `must_change_password=True` 的纯作者真实 POST 登录后，测试能捕获当前失败链路；修复后同一测试必须完成改密并进入工作台。

### T-02 增加中立改密路由和视图

目标：实现 `/account/change-password/`，解除对 Wagtail admin wrapper 的依赖。

涉及：`ai_author_forum/urls.py`、`ai_author_forum/users/views.py`、`templates/account/` 或等价安全模板、表单和静态资源。

完成标准：作者、编辑、双角色用户都能在该路径完成首次改密；改密成功后按登录范围返回；审计不含任何凭据。

### T-03 修正强制改密中间件

目标：统一所有角色的改密跳转，保证 `/account/change-password/` 是允许路径。

涉及：`ai_author_forum/users/middleware.py`、认证范围辅助函数和相关测试。

完成标准：首次改密路径不再出现 `/admin/accounts/change-password/`；纯作者访问 `/admin/` 仍然拒绝且不循环；静态资源和登出路径不被误拦截。

### T-04 统一品牌登录入口

目标：在 `/admin/login/` 增加作者入口，同时保留 `/author/login/` 深链接。

涉及：`templates/wagtailadmin/login.html`、`templates/author/login.html`、中英文文案、CSRF、内部 `next` 校验。

完成标准：编辑登录行为无回归；作者按钮在桌面和移动端可见、可键盘操作、可读屏识别；入口切换不改变账号角色或权限。

### T-05 建立登录范围和双角色返回逻辑

目标：记录本次进入的是作者工作台还是编辑后台，避免双角色改密后返回错误工作区。

涉及：认证 view、session helper、登出逻辑和安全重定向测试。

完成标准：同一双角色账号分别从两个入口登录，首次改密后分别返回 `/author/` 和 `/admin/`；篡改 Session 范围不能扩大权限。

### T-06 重整作者工作台外壳

目标：让作者体验具有后台工作台的稳定外观，但只显示投稿相关功能。

涉及：`templates/author/`、`author-workbench.css/js`、导航和响应式布局。

完成标准：作者看到投稿管理、新建投稿、开放期刊、账号改密和退出；不出现审核、投放、发布、设置、媒体库和账号管理入口；320px、768px 和桌面端无横向溢出或控件重叠。

### T-07 加固后台边界和兼容别名

目标：保证作者无法通过旧链接、直接 URL、批量接口或后台别名越权。

涉及：`wagtail_hooks.py`、作者 view、Nginx `/account/` 路由、兼容 redirect。

完成标准：`/admin/`、`/admin/accounts/`、审核、投放、静态发布、媒体和文档入口对纯作者均受控拒绝；`/author/dashboard/`、`/author/articles/` 如保留，只能安全跳转到作者工作台。

### T-08 完整回归与测试环境验收

目标：覆盖真实登录、改密、工作台、编辑回归和发布前检查。

涉及：单元/集成测试、Playwright、Compose、文档和交付报告。

完成标准：通过第 10 节的全部阻断项，并记录测试版本、命令、结果、浏览器地址和未执行项目。

## 9. 测试要求

### 9.1 单元与集成测试

至少增加以下测试：

- `/admin/login/` 返回作者入口链接，中文和英文文案正确；
- 作者登录成功设置作者范围，编辑登录成功保持编辑范围；
- 纯作者首次登录跳转 `/account/change-password/`，页面返回 200；
- 纯作者完成改密后返回 `/author/`；
- 编辑首次改密后返回 `/admin/`；
- 双角色账号从两个入口分别返回对应工作区；
- `next` 外部 URL、跨工作区路径和协议相对 URL 被拒绝；
- 纯作者访问 `/admin/`、`/admin/accounts/`、审核、投放和发布入口均 403/404；
- 首次改密请求最多跟随 3 跳，不出现重复 URL；
- 改密成功保留 Session，旧会话撤销，审计不包含密码或哈希；
- 作者、编辑、停用账号和无投稿关系账号的错误状态符合矩阵；
- 现有投稿、审核、投放和静态发布测试全量通过。

### 9.2 浏览器 E2E

必须使用真实 HTTP 登录，不得只使用 `force_login`：

1. 访问测试域名并通过测试 Basic Auth；
2. 从编辑登录页点击“作者投稿登录”；
3. 输入纯作者账号和临时密码；
4. 完成首次密码修改；
5. 进入作者投稿列表；
6. 创建、保存、提交一篇草稿并验证锁定提示；
7. 直接访问 `/admin/` 和 `/admin/accounts/`，确认 403 且无循环；
8. 在桌面和移动端检查导航、表单、错误提示和横向溢出；
9. 用编辑账号验证既有 `/admin/` 登录和后台文章管理不回归；
10. 用双角色账号分别从两个入口验证返回工作区。

E2E 必须设置最大重定向跳数并记录最终 URL；发现 `/admin/login/?next=/admin/accounts/change-password/` 时立即失败。

## 10. 验收标准

### A. 功能阻断项

- [ ] 作者入口可从现有品牌登录页发现，也可直接访问；
- [ ] 纯作者首次登录可以完成密码修改并进入 `/author/`；
- [ ] 作者可以完成新建、保存、提交和查看自己的投稿；
- [ ] 审核中和已发布内容对作者正确锁定；
- [ ] 编辑和超级管理员原有登录、审核、投放、发布流程无回归；
- [ ] 双角色账号根据入口返回正确工作区。

### B. 安全阻断项

- [ ] 作者没有 `is_staff`、Wagtail 页面树、媒体库、文档库或全局 API 权限；
- [ ] 仅隐藏菜单不能通过验收，所有直接 URL 和 service 调用都必须拒绝；
- [ ] 对象级文章隔离、期刊开放校验、CSRF、限流和审计均通过；
- [ ] 不存在首次改密重定向循环，任何失败不超过 3 跳；
- [ ] 测试日志、审计日志、截图和发布归档不含密码、哈希、Session 或 token。

### C. 体验阻断项

- [ ] 登录页的两个入口语义清晰，不使用用户名猜测或隐式角色切换；
- [ ] 作者工作台视觉与现有后台品牌一致，但导航只显示投稿相关功能；
- [ ] 中文/英文、桌面、移动端均无文字重叠、控件溢出或不可操作按钮；
- [ ] 首次改密页面可在作者和编辑两个权限域正常加载静态资源。

### D. 发布前阻断项

- [ ] `python manage.py check` 通过；
- [ ] `python manage.py makemigrations --check --dry-run` 通过；
- [ ] 受影响测试和完整 pytest 通过；
- [ ] `ruff`、`black --check`、`isort --check-only`、前端生产构建通过；
- [ ] 测试 Compose 的 `web worker static-frontend nginx` 已用当前源码重建并健康；
- [ ] `/healthz/`、作者登录、作者工作台、后台登录和本次变更页面通过浏览器验收；
- [ ] 如修改 Django 静态资源，完成 `collectstatic --noinput`；
- [ ] 不需要重新构建静态文章站点时，必须在报告中明确说明静态发布目录未被改变；
- [ ] 未执行正式服务器上传、SSH、迁移或重启。

## 11. 推荐验收命令

开发服务器必须使用根目录 `AGENTS.md` 指定的 Compose 项目、环境文件和配置文件：

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
compose_test exec web python manage.py check
compose_test exec web python manage.py makemigrations --check --dry-run
compose_test exec web python manage.py collectstatic --noinput
compose_test ps
```

受影响测试至少包含：

```bash
compose_test exec web python -m pytest \
  ai_author_forum/users/tests \
  ai_author_forum/articles/tests/test_author_submission_role.py
npm run build:prod
npm run test:e2e
```

完整发布前仍须按照第 10 章既有流程完成审核、正式投放、manifest 校验和静态链路验收；作者草稿不能直接写入正式静态目录。

## 12. 兼容、回滚与交付

本任务默认不新增数据库模型，不应为了修复登录循环创建第二套用户或密码字段。若实现确实需要新增 Session 审计字段或登录范围模型，必须同步提交迁移、回滚说明和数据安全评审。

回滚时：

1. 保留 `/account/change-password/` 和纯作者改密可达性；不能回滚到已知的 `/admin/accounts/change-password/` 循环；
2. 可暂时隐藏登录页作者按钮，但不能删除作者入口、作者关系、审计和已有投稿数据；
3. 禁止把作者设置为 `is_staff=True` 作为应急修复；
4. 记录当前活动版本、测试结果和可回滚 manifest；
5. 代码修复必须生成新 release，不能原地修改已激活静态 manifest。

交付报告必须包含：变更文件、测试 Compose 版本、测试命令及结果、重定向链路结果、桌面/移动浏览器地址、静态资源检查、未执行项目和发布归档 SHA-256。

## 13. 完成定义

本任务只有在以下流程完整通过后才算完成：

```text
品牌登录页点击作者入口
  -> 作者账号真实登录
  -> 中立首次改密页完成修改
  -> 作者投稿工作台
  -> 创建/保存/提交投稿
  -> 直接访问 /admin/ 被拒绝且无重定向循环
```

“账号密码正确但无法进入作者工作台”只能算认证部分通过，不能作为本任务完成。
