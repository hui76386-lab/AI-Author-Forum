# 远程中间件接入与生产部署约定

> 适用工程：`E:\AI Author Forum\news-template`
>
> 本约定适用于生产和远程联合验收环境。真实服务器地址、用户名、密码、Redis 密钥和 TLS 证书不得写入 Git 跟踪文件，也不得出现在日志、异常信息或提交说明中。

## 1. 运行基线

生产应用默认使用远程中间件：

- PostgreSQL：由 `DATABASE_URL` 指定；
- Redis：由 `CACHE_LOCATION`、`CELERY_BROKER_URL` 和 `CELERY_RESULT_BACKEND` 指定；
- Celery：使用远程 Redis broker，并将任务结果写回远程 Redis；
- Django 缓存：必须使用 `django.core.cache.backends.redis.RedisCache`；
- Web、worker、静态前台和 Nginx 仍由本项目 Compose 管理。

生产 `docker-compose.production.yml` **不再启动或依赖本地 PostgreSQL/Redis 容器**。这样可以避免应用误连 Compose 内部服务名、误创建本地数据库，以及在服务器重建应用容器时丢失对远程中间件的明确边界。

## 2. 生产环境变量

从 `.env.production.example` 复制为服务器上的私有配置文件，或由 CI/CD secret 注入：

```text
MIDDLEWARE_MODE=remote
DATABASE_URL=postgresql://user:url-encoded-password@db.example.internal:5432/ai_author_forum
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=rediss://:url-encoded-password@redis.example.internal:6380/1
CELERY_BROKER_URL=rediss://:url-encoded-password@redis.example.internal:6380/0
CELERY_RESULT_BACKEND=rediss://:url-encoded-password@redis.example.internal:6380/0
```

约束：

1. `DATABASE_URL` 必须是 `postgres://` 或 `postgresql://`；
2. 三个 Redis 地址必须是 `redis://` 或 `rediss://`；
3. `MIDDLEWARE_MODE=remote` 时，不能使用 `localhost`、`127.0.0.1`、`::1`、`database` 或 `redis`；
4. `CACHE_BACKEND` 必须是 RedisCache，禁止生产静默回退到 LocMemCache；
5. 密码中的 `@`、`:`、`/`、`#` 等字符必须进行 URL 编码；
6. 远程 Redis 使用 TLS 时优先使用受信任 CA，不要为了绕过证书校验设置不安全的 `ssl_cert_reqs=none`；
7. 应用所在服务器与中间件在同一私有网络时，优先使用内网地址或私有 DNS，不开放数据库和 Redis 到公网。

Django production settings 在启动阶段会进行上述校验。缺变量、协议错误、缓存后端错误或误指向本地服务时，进程会直接失败，并且错误信息不会打印任何 URL、用户名或密码。

## 3. 部署命令

在服务器上执行，使用实际的私有环境文件：

```bash
docker compose --env-file .env.production \\
  -f docker-compose.production.yml up -d --build

docker compose --env-file .env.production \\
  -f docker-compose.production.yml exec web \\
  python manage.py check --deploy

docker compose --env-file .env.production \\
  -f docker-compose.production.yml exec web \\
  python manage.py build_static_site
```

迁移、静态资源收集和 Web 容器启动由应用启动命令完成；正式发布前仍应先进行备份、迁移复核和静态发布预演。发布不能直接对真实生产数据进行未授权的测试写入。

## 4. 本地/隔离验收模式

本地中间件只用于开发或独立验收，不是生产默认值。使用单独的环境文件和 Compose overlay：

```bash
cp .env.production.local.example .env.production.local
# 修改 SECRET_KEY、POSTGRES_PASSWORD 等本地值

docker compose --env-file .env.production.local \\
  -f docker-compose.production.yml \\
  -f docker-compose.local-middleware.yml up -d --build
```

此模式必须显式设置 `MIDDLEWARE_MODE=local`。不得把 local 环境文件复制到生产，也不得让本地 `postgres-data` 或 `redis-data` 卷替代远程生产数据。

## 5. 健康检查和验收

远程中间件联合验收至少包括：

```bash
# 应用容器内
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py check --deploy

# 容器/服务器侧
curl -fsS https://example.com/healthz/
curl -fsS https://example.com/readyz/
celery -A ai_author_forum inspect ping
```

`/healthz/` 必须报告数据库可用；`/readyz/` 在启用 `STATIC_PUBLISH_HEALTHCHECK_BROKER=true` 时还必须报告 broker 可用。静态发布验收仍需检查逐页面结果、manifest、失败记录、重试、回滚、审计日志和引用素材完整性。

## 6. 安全边界

- 不要读取、覆盖或复用其他项目的生产数据库和 Redis；远程联合验收必须使用独立数据库、独立 Redis DB/前缀或专用实例。
- 不要在 Compose、Django settings、README、测试快照或日志中写入真实密钥。
- 不要在异常中拼接连接 URL；本项目的生产配置校验只报告变量名和错误类型。
- 远程中间件连接失败时，优先检查网络 ACL、DNS、端口、TLS CA、数据库授权和 Redis ACL，不要把配置改回本地默认值来“绕过”故障。

## 7. 文件边界

- `docker-compose.production.yml`：生产远程中间件应用栈；
- `docker-compose.local-middleware.yml`：可选的本地 PostgreSQL/Redis overlay；
- `.env.production.example`：远程生产变量模板，不含真实密钥；
- `.env.production.local.example`：隔离本地验收模板；
- `ai_author_forum/settings/middleware.py`：生产中间件配置校验；
- `tests/test_production_middleware_config.py`：校验缺失、协议、本地误连和合法配置。
