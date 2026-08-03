# 静态发布生产运维手册

## 1. 架构边界

- Nginx 将普通前台请求代理到 `static-frontend`；该服务只读取 `STATIC_PUBLISH_ROOT/current` 中的固定 HTML 和 `manifest.json`，不导入 Django、不连接数据库。
- `static-frontend` 根据活动 manifest 对旧栏目路径返回真实 HTTP 301；未发布或不存在的前台路径返回 503，绝不回退到 Django 动态渲染。
- Django Gunicorn 只负责 Wagtail 后台、CMS 健康检查和必要管理接口。
- Celery worker 执行全量发布、失败重试和回滚。
- Redis 提供 Celery broker、结果存储和共享缓存。
- PostgreSQL 保存 CMS 内容、发布任务、逐页结果、manifest 记录和审计日志。

## 2. 首次部署

```powershell
Copy-Item .env.production.example .env.production
```

修改 `.env.production` 中的密钥、域名和数据库密码，然后启动：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
```

首次没有活动静态版本时，`/readyz/` 返回 503，前台也返回 503；后台仍可通过
`/admin/` 访问。执行首次构建：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site
```

## 3. 健康检查

- `/healthz/`：数据库和 Django 进程存活检查。
- `/readyz/`：数据库、活动 release、磁盘 manifest 和 Redis broker 就绪检查。
- `/__static_health__/`：`static-frontend` 进程检查；响应中的 `release_available` 表示 `current/manifest.json` 是否存在。该端点不访问数据库。
- 命令行检查：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py check_static_publish_health
```

只有 `/readyz/` 返回 200 后才能切入生产流量。

## 4. 发布、重试和回滚

发布管理员可在 Wagtail 的“Static publishing”菜单发起全量或选择性发布。后台操作会进入 Celery 队列，不阻塞 Web 请求。

命令行全量发布：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site
```

指定页面：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site --path / --path /journals/example/
```

重试失败任务和回滚：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site --retry-job 42
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site --rollback VERSION --rollback-reason "回滚到已验证的稳定版本"
```

构建失败不会替换 `current`。回滚只允许切换到数据库中仍存在 manifest 记录的 release。

## 5. 监控和告警

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml logs -f worker
docker compose --env-file .env.production -f docker-compose.production.yml exec worker celery -A ai_author_forum inspect ping
docker compose --env-file .env.production -f docker-compose.production.yml exec worker celery -A ai_author_forum inspect active
docker compose --env-file .env.production -f docker-compose.production.yml exec worker celery -A ai_author_forum inspect reserved
```

建议告警条件：

- `/healthz/` 连续 3 次失败。
- `/readyz/` 连续 2 次失败。
- 发布任务运行超过 `CELERY_TASK_TIME_LIMIT`。
- 发布任务状态为 `failed` 或 `partial`。
- Redis、PostgreSQL 或 Celery worker 不可用。
- `published` 卷剩余容量低于 20%。

## 6. 备份和灾备

发布前至少备份 PostgreSQL 和三个持久卷：`media-data`、`published-data`、`postgres-data`。

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec -T database pg_dump -U ai_author_forum ai_author_forum > ai-author-forum.sql
```

灾备恢复顺序：

1. 停止 Nginx，避免恢复过程中接入流量。
2. 恢复 PostgreSQL 和 `media-data`、`published-data`。
3. 启动数据库、Redis、Web 和 worker。
4. 执行 `check_static_publish_health`。
5. 如果磁盘 `current/manifest.json` 与数据库活动 manifest 不一致，回滚到最近的完整版本。
6. `/readyz/` 返回 200 后再启动 Nginx。

## 7. 权限验收

- `publisher`：可查看发布中心，可发起发布、失败重试和回滚。
- `readonly`：可查看发布记录和质量报告，但看不到发布、重试、回滚控件，直接 POST 也会被拒绝。
- 本地账号密码只保存在外部测试账号文档中，禁止提交到 Git。

## 8. 断开数据库的静态前台验收

本地验收会先创建两个 release、执行一次回滚，然后关闭 Django 数据库连接并把 `.e2e/db.sqlite3` 移动为 `.e2e/db.sqlite3.offline`。浏览器测试期间原数据库路径必须不存在。

```powershell
.\.venv\Scripts\python.exe scripts\prepare_static_e2e.py
npm run test:e2e:only
```

验收必须同时确认：

- 主站、A-Z、子期刊、动态栏目、文章、固定频道和 Search 推荐页均由固定 HTML 返回 200；
- 旧栏目 URL 在禁止自动跟随跳转时返回真实 HTTP 301 和正确的 `Location`；
- 浏览器跟随跳转后到达新栏目固定 HTML；
- `.e2e/db.sqlite3` 不存在，而 `.e2e/db.sqlite3.offline` 存在；
- 回滚后的页面内容和 manifest 版本一致。

生产环境还必须执行容器级联调；以下命令不可由本地 WSGI/SQLite 测试替代：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
docker compose --env-file .env.production -f docker-compose.production.yml exec static-frontend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/__static_health__/').read().decode())"
```

如果执行环境没有 Docker/PostgreSQL，必须把容器联调和 PostgreSQL 并发行锁测试记录为“环境阻塞”，不得以 SQLite 或单进程测试冒充通过。

## 投放变更自动增量发布

起用的人工投放、批量子期刊投放、排序和停用会在事务提交后自动写入一个“指定路径”静态发布任务。文章详情、首页、子期刊、栏目或搜索页会按投放目标自动计算。栏目变更还会重建受影响的聚合父栏目和分页，并清理因分页缩短而不再需要的旧静态文件。

- 默认合并窗口为 60 秒；窗口内的多次变更合并为一个任务，以最后一次变更为准重新计时。
- Celery 会在窗口结束后执行任务；旧的延迟消息会自动让位，因此同一批次只会构建一次。
- 每次入队或合并都会留下审计日志；最终构建继续使用既有 manifest、逐页结果、失败重试和原子切换。
- 发布中心会将这类任务标明为“投放自动合并”。

可通过环境变量调整：

```env
# 维护窗口需要改为人工发布时设为 false
STATIC_PUBLISH_AUTO_ON_PLACEMENT_CHANGE=true
# 多次投放合并等待时间（秒）
STATIC_PUBLISH_AUTO_DEBOUNCE_SECONDS=60
```
