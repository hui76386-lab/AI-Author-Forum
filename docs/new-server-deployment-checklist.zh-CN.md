# AI Author Forum 新服务器部署清单

> 适用范围：将当前项目部署到一台新的生产、预发布或独立测试服务器。
> 正式环境、预发布环境和测试环境必须使用独立的数据库、Redis、持久卷和密钥。

## 1. 服务器资源

- [ ] Linux x86_64，推荐 Ubuntu 22.04 LTS 或更新的 LTS。
- [ ] 至少 4 vCPU。
- [ ] 至少 8 GiB 内存。
- [ ] 至少 100 GiB 磁盘。
- [ ] 磁盘余量大于数据库、媒体、发布文件和镜像总量的 3 倍。
- [ ] 系统时区设置为 `Asia/Shanghai`。
- [ ] NTP 时间同步正常。
- [ ] Docker Engine 已安装。
- [ ] Docker Compose plugin 已安装。
- [ ] 宿主机 Nginx 或云负载均衡已准备。

## 2. 网络与域名

- [ ] DNS 已指向新服务器。
- [ ] TLS 证书已单独签发。
- [ ] 公网仅开放 SSH、HTTP 和 HTTPS 所需端口。
- [ ] PostgreSQL `5432` 不向公网开放。
- [ ] Redis `6379` 不向公网开放。
- [ ] 应用入口端口只绑定到回环地址，例如 `127.0.0.1:8080`。
- [ ] 反向代理传递 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto=https`。
- [ ] 上传限制至少配置为 `client_max_body_size 256m`。
- [ ] SSH 优先使用密钥登录并限制来源地址。

## 3. 中间件

- [ ] 准备独立 PostgreSQL 15 数据库。
- [ ] 为应用创建独立数据库和账号。
- [ ] 数据库账号具有迁移所需的 DDL 权限。
- [ ] 准备 Redis 7.2。
- [ ] Redis 缓存使用独立 DB，例如 `/1`。
- [ ] Celery broker 和结果存储使用独立 DB，例如 `/0`。
- [ ] PostgreSQL 和 Redis 优先使用私网地址或私有 DNS。
- [ ] Redis 跨网络连接使用 `rediss://` 和可信 CA。
- [ ] 不与旧服务器或其他项目共用数据库、Redis 队列。

## 4. 环境变量

- [ ] `SECRET_KEY`：在新服务器生成独立的长随机值。
- [ ] `MIDDLEWARE_MODE=remote`：正式环境必须使用远程中间件模式。
- [ ] `DATABASE_URL`：PostgreSQL 连接 URL。
- [ ] `CACHE_BACKEND=django.core.cache.backends.redis.RedisCache`。
- [ ] `CACHE_LOCATION`：Redis 缓存 URL。
- [ ] `CELERY_BROKER_URL`：Celery broker URL。
- [ ] `CELERY_RESULT_BACKEND`：Celery 结果存储 URL。
- [ ] `ALLOWED_HOSTS`：逗号分隔的允许域名。
- [ ] `CSRF_TRUSTED_ORIGINS`：包含协议的可信来源。
- [ ] `WAGTAILADMIN_BASE_URL`：后台站点根地址。
- [ ] `SECURE_SSL_REDIRECT=true`。
- [ ] `SESSION_COOKIE_SECURE=true`。
- [ ] `CSRF_COOKIE_SECURE=true`。
- [ ] `SECURE_HSTS_SECONDS` 已按域名策略配置。
- [ ] `SECURE_HSTS_INCLUDE_SUBDOMAINS` 已按域名策略配置。
- [ ] `SECURE_HSTS_PRELOAD` 已按域名策略配置。
- [ ] `STATIC_PUBLISH_HEALTHCHECK_BROKER=true`。
- [ ] `HTTP_PORT=127.0.0.1:8080`，或使用指定的内部监听端口。
- [ ] 环境文件权限设置为 `0600`。
- [ ] 连接 URL 中的密码已经进行 URL 编码。
- [ ] 环境文件和真实密钥未提交到 Git。

生产配置示例：

```dotenv
SECRET_KEY=<新生成的长随机值>
MIDDLEWARE_MODE=remote
DATABASE_URL=postgresql://user:<URL编码密码>@db.internal:5432/ai_author_forum
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=rediss://:<URL编码密码>@redis.internal:6380/1
CELERY_BROKER_URL=rediss://:<URL编码密码>@redis.internal:6380/0
CELERY_RESULT_BACKEND=rediss://:<URL编码密码>@redis.internal:6380/0

ALLOWED_HOSTS=example.com
CSRF_TRUSTED_ORIGINS=https://example.com
WAGTAILADMIN_BASE_URL=https://example.com

SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=false
SECURE_HSTS_PRELOAD=false

STATIC_PUBLISH_HEALTHCHECK_BROKER=true
HTTP_PORT=127.0.0.1:8080
```

注意：`docker compose --env-file` 主要用于 Compose 变量插值。只有在 Compose
`environment` 或 `env_file` 中声明的变量才会进入容器。新增 `SEO_NOINDEX`、
`TIME_ZONE`、`CELERY_TASK_*`、`STATIC_PUBLISH_*`、`ARTICLE_IMPORT_*`、
`PLACEMENTS_*` 或对象存储变量时，应同时核对 Compose 配置。

## 5. 应用服务与持久卷

- [ ] `web`：Django、Wagtail 和 Gunicorn。
- [ ] `worker`：Celery worker。
- [ ] `static-frontend`：只读静态发布服务。
- [ ] `nginx`：容器入口和静态资源服务。
- [ ] `static-data` 持久卷已创建并纳入备份策略。
- [ ] `media-data` 持久卷已创建并纳入备份策略。
- [ ] `published-data` 持久卷已创建并纳入备份策略。
- [ ] 使用本地中间件时，`postgres-data` 和 `redis-data` 使用独立命名卷。
- [ ] 所有长期运行容器配置了重启策略。

## 6. 迁移材料

- [ ] 确认待部署 Git commit 和 `.release-version`。
- [ ] 打包应用代码、Compose、Dockerfile 和 Nginx 配置。
- [ ] 使用 `pg_dump -Fc` 导出 PostgreSQL 一致性逻辑备份。
- [ ] 备份 `media-data`。
- [ ] 备份 `published-data`。
- [ ] 保存活动 `manifest.json`。
- [ ] 保存文件数量、数据量和 SHA-256 校验清单。
- [ ] 记录旧服务器的活动 release 版本。
- [ ] 不迁移 Redis 数据、缓存和 Celery 队列。
- [ ] 不复制旧服务器环境文件、明文密钥或临时文件。
- [ ] 不直接复制 PostgreSQL 或 Redis 的原始数据目录。

## 7. 部署前验证

- [ ] `docker compose ... config --quiet` 通过。
- [ ] `python manage.py check` 通过。
- [ ] `python manage.py check --deploy` 通过。
- [ ] `python manage.py makemigrations --check --dry-run` 通过。
- [ ] 受影响的单元和集成测试通过。
- [ ] 完整测试集通过。
- [ ] E2E 测试通过。
- [ ] 旧环境数据库、媒体和发布目录已经备份。
- [ ] 数据库迁移状态正常，不存在失败或部分应用的迁移。

## 8. 部署顺序

- [ ] 构建应用镜像，构建失败时停止部署。
- [ ] 恢复 PostgreSQL 备份。
- [ ] 恢复并校验 `media-data`。
- [ ] 恢复并校验 `published-data`。
- [ ] 执行数据库迁移。
- [ ] 执行 `collectstatic`。
- [ ] 启动并验证 `web`。
- [ ] 启动并验证 `worker`。
- [ ] 启动并验证 `static-frontend`。
- [ ] 启动容器 Nginx。
- [ ] 通过 `build_static_site` 生成新的不可变 release 和 manifest。
- [ ] 完整性校验通过后原子激活 `current`。
- [ ] 所有健康检查通过后接入正式流量。

正式数据链路必须保持为：

```text
导入暂存 -> canonical ArticlePage 草稿/revision -> 审核 -> 正式投放
  -> 冻结构建快照 -> 不可变 manifest -> current 原子激活
```

不得绕过审核、正式投放、manifest 校验或审计日志，不得直接覆盖
`published/current`。

## 9. 上线验收

- [ ] `/healthz/` 返回 HTTP 200。
- [ ] `/readyz/` 返回 HTTP 200。
- [ ] `/__static_health__/` 返回正常，且活动 release 可用。
- [ ] `celery -A ai_author_forum inspect ping` 成功。
- [ ] 后台 `/admin/` 可以登录并完成关键操作。
- [ ] 首页、栏目、子期刊和文章页面可访问。
- [ ] 桌面端和移动端页面正常。
- [ ] CSS、JavaScript、图片和媒体资源正常。
- [ ] 当前磁盘 manifest 与数据库活动 manifest 一致。
- [ ] 普通前台请求不依赖 Django 实时查询文章数据库。
- [ ] 发布、失败、重试和回滚均写入 `AuditLog`。
- [ ] 旧服务器在观察期内继续保留，不立即销毁。

## 10. 回滚与交付记录

- [ ] 保留上一个经过验证的 release 和 manifest。
- [ ] 回滚仅切换到数据库中有记录且完整的旧 release。
- [ ] 回滚后重新验证三个健康检查和活动 manifest。
- [ ] 记录部署时间、Git commit、release 版本和 manifest 标识。
- [ ] 记录所有验证命令及结果。
- [ ] 记录部署失败、重试、回滚和已知限制。
- [ ] 保存数据库、媒体和发布目录的恢复演练记录。

## 11. 项目参考文件

- `docker-compose.production.yml`：正式应用栈。
- `docker-compose.local-middleware.yml`：独立验收中间件。
- `docker-compose.preproduction.yml`：预发布容量限制和版本覆盖。
- `docker-compose.test.yml`：测试环境隔离配置。
- `.env.production.example`：正式环境变量模板。
- `docs/remote-middleware.zh-CN.md`：远程数据库和 Redis 接入说明。
- `docs/static-publishing-operations.zh-CN.md`：静态发布、健康检查和回滚。
- `docs/test-server-migration-38.92.8.133.zh-CN.md`：服务器迁移执行方案。
