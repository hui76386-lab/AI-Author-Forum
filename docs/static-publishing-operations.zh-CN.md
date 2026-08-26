# 静态发布生产运维手册

## 1. 架构边界

- Nginx 对含 `.nginx-direct-ready` 的活动 release 使用 `try_files`/`sendfile` 直接读取 `STATIC_PUBLISH_ROOT/current`；首页、文章和普通聚合页不进入 Python。
- 旧 release 没有直出标记时整站回退 `static-frontend`，因此应用回滚不要求原地修改旧 manifest。新 release 的 redirect 哨兵也回退该服务，由活动 manifest 返回真实 HTTP 301；未发布或不存在的路径返回 503，绝不回退到 Django 动态渲染。
- Django Gunicorn 只负责 Wagtail 后台、CMS 健康检查和必要管理接口。
- `release` job 每个应用版本只执行一次迁移和 `collectstatic`；Web/worker 副本启动不修改 schema 或静态目录。
- Celery worker 只消费 `static_publish` 队列，执行全量发布、失败重试和回滚。
- Redis 提供 Celery broker、结果存储和共享缓存。
- PostgreSQL 保存 CMS 内容、发布任务、逐页结果、manifest 记录和审计日志。

## 2. 首次部署

```powershell
Copy-Item .env.production.example .env.production
```

修改 `.env.production` 中的密钥、域名和数据库密码。先构建镜像，执行一次
release job，成功后再更新长期运行服务：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml build
docker compose --env-file .env.production -f docker-compose.production.yml --profile release run --rm release
docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-build
```

`release` 任一步失败都必须停止部署；不得启动新 Web/worker，也不得激活新
静态 release。扩容 Web/worker 时直接启动副本，不重复运行 release job。

首次没有活动静态版本时，`/readyz/` 返回 503，前台也返回 503；后台仍可通过
`/admin/` 访问。执行首次构建：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site
```

## 3. 健康检查

- `/livez/`：只检查 Django 进程，不访问数据库、Redis 或活动 release；容器存活探针使用此端点。
- `/healthz/`：检查数据库、输出目录和 Django 请求链路，保留现有兼容语义。
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
每个新 release 的 `.nginx-direct-ready` 和 `.nginx-redirects/<output_path>.redirect` 都属于 manifest
哈希 inventory，不能在激活后修改；前两者的 URL 必须返回 404。普通页面响应应含
`X-Static-Served-By: nginx`，redirect 回退响应为
`X-Static-Served-By: static-frontend`。

选择性发布从旧 `current` 复制未选中的页面。发布快照会检查 manifest 中的文章详情页是否
包含互动容器、评论、分享、复制链接和 PDF 下载的完整挂载标记；发现旧模板页面时，会从当前
provider 重新加入该文章目标并在新 release 中渲染。这样评论、复制/分享和 PDF 下载的前端入口
不会因历史页面被沿用而只在部分文章出现。旧页面无法由当前审核/投放数据重新解析时仍保持原
release，不能绕过审核链路强行生成；此类页面应先恢复正式文章目标后再发布。

## 5. 监控和告警

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml logs -f worker
docker compose --env-file .env.production -f docker-compose.production.yml exec worker celery -A ai_author_forum inspect ping
docker compose --env-file .env.production -f docker-compose.production.yml exec worker celery -A ai_author_forum inspect active
docker compose --env-file .env.production -f docker-compose.production.yml exec worker celery -A ai_author_forum inspect reserved
```

建议告警条件：

- `/livez/` 连续 3 次失败。
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
- 新 release 的首页和文章响应含 `X-Static-Served-By: nginx`，旧 release 回滚后仍可由 `static-frontend` 完整读取；
- `/.nginx-direct-ready`、`/.nginx-redirects/` 和路径穿越请求不能读取发布目录外内容；
- 浏览器跟随跳转后到达新栏目固定 HTML；
- `.e2e/db.sqlite3` 不存在，而 `.e2e/db.sqlite3.offline` 存在；
- 回滚后的页面内容和 manifest 版本一致。

生产环境还必须执行容器级联调；以下命令不可由本地 WSGI/SQLite 测试替代：

```powershell
docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production -f docker-compose.production.yml build
docker compose --env-file .env.production -f docker-compose.production.yml --profile release run --rm release
docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-build
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
