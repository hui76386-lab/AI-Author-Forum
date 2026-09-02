# Reader Interactions RI-08 部署与验收记录

## 1. 范围和版本

- 阶段：RI-08 统一前端和分享。
- 工作目录：`/opt/ai-author-forum-test-current`。
- 部署环境：`ai-author-forum-test` Docker Compose 测试运行环境。
- 功能开关：部署和验收期间保持关闭；未绕过审核、正式投放、manifest 校验或审计。

## 2. 实现摘要

- 文章静态页使用本地 bundle 统一挂载评论、系统分享、复制链接和 PDF 下载，三项受控动作共用 reader session、capability 和邮箱验证 gate。
- Web Share 在原始 click 用户手势内、任何 await/fetch 之前调用；不支持时回退 Clipboard，再回退到可聚焦并选择的只读 canonical URL。
- 验证 intent 和评论草稿只写入 15 分钟 TTL 的 sessionStorage；验证返回后只恢复草稿和焦点，绝不自动评论、分享、复制或下载。
- 分享 API 只接受 `system_share|copy_link` 和 `completed|cancelled|failed`；一分钟合并事件，仅记录最小 action/outcome，不接收接收者、账号或消息正文。
- 静态页面构建时通过进程内 ContextVar 注入冻结 job version，并在构建结束后复位；浏览器不能伪造 release。前端在页面 release 与 capability release 不一致时关闭失败。
- CSP、API/存储/Clipboard 降级、无 JavaScript、键盘焦点、44px 触控目标和 390px 移动布局均保持正文与 canonical URL 可用。

## 3. 阶段验证

- `python manage.py check`：通过。
- `python manage.py makemigrations --check --dry-run`：通过，无模型漂移。
- RI-08 真实 PostgreSQL 双库测试：`22 passed, 5 warnings, 3 subtests passed`。
- 分享/静态发布定向回归：`61 passed, 24 warnings`；另有前端/i18n 定向回归 `49 passed`。
- 全量 pytest：`906 passed, 12 skipped, 256 warnings, 410 subtests passed`。
- Playwright E2E：37 项，`35 passed, 2 skipped`；覆盖 Web Share 支持/取消/异常、Clipboard 成功/失败、验证恢复不自动执行、无 JavaScript、API 故障、键盘与移动端。
- Webpack 生产构建：通过；保留既有 `nature-home.png` 799 KiB 体积提示。
- RI-08 范围 Ruff/Black：通过；Django check 和 migration dry-run 在最终部署前再次通过。
- 全仓 Ruff 仍有两个与本阶段无关的既有问题：`journals/tests/test_admin_workspace.py` 导入顺序、`placements/publishing.py` 未使用变量。

## 4. 部署和静态发布结果

- 最终部署时间：2026-08-17 11:37 至 11:55（Asia/Shanghai）。
- 主应用镜像：`ai-author-forum-test-ri08-final:20260817T113723-0800`，digest `sha256:8c250b1a944af2687ca658c67af4d968033f691a1f4cb280b50f2b4cfe6fc2c5`。
- PDF worker 镜像：`ai-author-forum-test-ri08-pdf-final:20260817T113723-0800`，digest `sha256:8a9a64b536d8f9d2bc0ce5ee9f5dad6707f017944fad75d12511b85bd182e539`。
- release 入口执行成功：双库均无待应用迁移；collectstatic 为 `0 copied, 257 unmodified, 173 post-processed`。web、worker、三个 reader worker 与 static-frontend 已更新；web/static-frontend healthy。
- 最终静态发布 job 96：版本 `20260817T034009821410Z-job96`，scope `selective`，`3910/3910` succeeded，0 failed，5502 generated，8 redirected，8867 manifest files，225335869 bytes。
- 活动 manifest 唯一且为 job96；previous version 为 job95。manifest SHA-256：`106030e6c5fa68426424f7761cdc87fd451e9e356bfdb47088e27a1d8c1ff709`；整个 release 文件树摘要：`e51acba4a19721b8b5724656792d83cc86773a8e36e5658d2c486d8c7daeb62f`，与 `current` 完全一致。
- AuditLog 6974 记录 started，6975 记录 success/activated；中间 job95/manifest 未原地修改，修复由新 job96 发布。
- 线上样本页面 `data-release` 为 job96，与活动 manifest 一致；样本 canonical 与正文 SHA-256 在 job95/job96 间相同（正文 `b7957cd8f3d88c079864c7547f5bccad27cca4817184603d07a3eb172a1ba513`）。
- `/healthz/`、`/readyz/`、`/__static_health__/` 均为 200；页面为 200，并返回 same-origin CSP 与 `X-Static-Release: true`；Nginx 配置检查通过。
- 所有 reader 功能开关为 false；capability 为 `503 + Cache-Control: no-store` 并关闭失败，匿名 session 为 `200 + no-store`，直接 protected 路径为 404。

## 5. 备份和恢复演练

- 备份目录：`/opt/ai-author-forum-test-backups/ri08-20260817T1057-0800`，目录权限 0700；最终 job96 归档权限 0600。
- 部署前 default：35188925 bytes，SHA-256 `8ded47625b43f9bc894d2dc2c9d20ada1f4ce7b9eedc17f23e7475c3f5fbdf24`；interactions：77512 bytes，SHA-256 `1cb972ee70c5420f29fc444886469efaffc04c118611ba30e8c780e257f61350`。
- 中间部署后 default：36367907 bytes，SHA-256 `742bc3e29e3e5e407cb1c19da3a70c67f820bf403ed76c076f226429f1eee26c`；interactions：77512 bytes，SHA-256 `bfba28c25625b96b707b7bee86be5475edd71bf59591bda44504d62c92af19d8`。
- 最终 job96 default：37531553 bytes，SHA-256 `0a2f0890699660378be5e06172adb7453213e9350d5e0ac13b1e602d869465ee`；interactions：77512 bytes，SHA-256 `c4232784559b1eb38226e4848b1ad4ca96b1ad6d44e51f151c5a5de71dc6163c`。
- PostgreSQL 15 容器内 `pg_restore --list` 可读取最终两份归档；分别恢复到 `ri08_final_restore_default_20260817t1200` 和 `ri08_final_restore_interactions_20260817t1200` 成功。
- 已部署镜像连接恢复库执行 `migrate --check --database=default/interactions` 均通过；两库各 310 条迁移。恢复计数：articles 1217、placements 1499、audits 6385、active manifest 1/job96；reader identities、comments、reader action events 均为 0。
- 演练完成后仅删除上述两个明确命名的临时恢复库；数据库列表确认无 `ri08_final_restore_` 残留。

## 6. 已知限制

- 功能开关按分阶段上线要求保持关闭；RI-10 灰度前不会开放真实读者写入。
- 当前测试数据的全站构建仍被内容就绪规则拦截：首页必需投放槽为空，另有 35 个文章目标缺失。本阶段使用正式 `build_static_site --paths` copy-on-write 选择性发布 2392 个当前可构建文章路径并展开到 3910 个依赖目标；未绕过文章审核、投放、manifest 或审计。活动 release 还保留 36 个不再可构建的历史文章页，内容未改动。
- 两个 E2E 跳过项属于环境限定用例；Webpack 大资源提示及 Nginx duplicate `text/html` MIME warning 为既有告警，均未阻塞配置语法、健康或本阶段验收。
- RI-09 的容量/故障/安全演练和 RI-10 的灰度、海外验证及项目方签署尚未执行。
