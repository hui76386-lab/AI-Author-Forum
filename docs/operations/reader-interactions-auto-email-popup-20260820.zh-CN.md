# 邮箱点击自动验证与悬浮窗部署记录

## 1. 结果

- 部署目标：`ai-author-forum-test`，`https://test-author.huixixi.top`
- 应用镜像：web `sha256:39995df5a13b2107806c2a94aa80fcf5fde61d4c2ff62ca43650c83f3a949d03`；static frontend `sha256:9d248f58a3889c6f03a1ebf6bbbde480c62999f5c51a7328706092104d9b37b4`；reader email worker `sha256:f7476eee106085ccaaba9bc9358977d0622aa3490452a81c9c8a924b220bc712`
- 活动静态 release：`20260820T043849638005Z-job104`
- 上一活动版本：`20260820T040801172885Z-job103`
- job104：2392 个文章入口展开为 3910 个目标，3910 成功、0 失败；manifest 摘要为 8880 文件、228380485 bytes、3910 pages、0 failed。
- manifest SHA-256：`1c1ffa0d18c20d0f6f77a502ebada61bc91a26fdb71f82bf46e4c07f55cf3acf`

## 2. 交互验收

- 文章页邮箱验证表单为 `role=dialog` 的固定悬浮窗，桌面/移动宽度均受视口约束，提供关闭按钮。
- 邮件验证页从 URL fragment 自动读取一次性令牌并立即 POST consume；不显示昵称或匹配码输入框。
- 线上真实文章页面检查到“邮箱验证”标题、悬浮窗 `position: fixed` 和关闭按钮；页面 HTML 不含“电脑配对码”。
- 浏览器线上验收完成一次评论验证触发：表单显示为固定对话框，邮箱字段提交前无需输入匹配码。

## 3. 验证命令

| 检查 | 结果 |
| --- | --- |
| `python manage.py check` | 通过 |
| `python manage.py makemigrations --check --dry-run` | 通过 |
| 后端受影响测试 | 38 项通过，4 项按环境跳过 |
| Django 完整测试 | 838 项通过，14 项跳过，退出码 0 |
| `npm run build:prod` | 通过 |
| Playwright 全量 | 35 项通过，2 项跳过 |
| PostgreSQL 并发测试 | 2 项通过；临时 `CREATEDB` 权限已撤销 |
| `check_static_publish_health --skip-broker` | 通过 |
| `/livez/`、`/healthz/`、`/readyz/`、`/__static_health__/` | 全部 HTTP 200 |

首次全量 `build_static_site --actor test-admin` 因既有首页投放槽和历史文章内容就绪缺口失败，未切换 current；随后按运行手册选择性发布正式文章入口，job104 完整成功并原子激活。

## 4. 备份与限制

- 发布前双库备份目录：`/opt/ai-author-forum-test-backups/auto-email-popup-20260820T1059+0800/`
- `default.dump` SHA-256：`bb2a87189691bbb012770e4328970966a09e7ac77eaeadeb5a8c59d06eb6d1f5`
- `interactions.dump` SHA-256：`d0f5ae066fa02a83a199e1b279f9ac578d1c283f07e8849f8e5198e7e1`
- 尚未通过项目方指定的真实收件箱完成 SMTP 到达和人工点击；自动链接消费由后端测试、双浏览器 E2E 和线上验证页脚本验收覆盖。实际邮箱供应商到达仍需使用专用测试邮箱进行人工验收。
