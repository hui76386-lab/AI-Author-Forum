# RI-02 双数据库与领域骨架部署记录

## 版本和范围

- 任务卡：`RI-02`
- 目标环境：`ai-author-forum-test`
- 时间：2026-08-17 02:04-02:18（Asia/Shanghai）
- Git 基线：`b6555c3ef5804c10458720889a62431bf0503f43`
- 部署标识：`ri02-20260817T021439+0800`
- 上一个公共 manifest：`20260816T171349272216Z-job92`（本次保持不变）

## 变更和迁移

- 新增 `ai_author_forum/reader_access/` 与 `ai_author_forum/reader_interactions/` 模型、迁移、router、manager、admin 和阶段测试；新增双库配置、Compose release job、CI 迁移检查及运维文档。
- `default`：`reader_access.0001_initial`
- `interactions`：`reader_interactions.0001_initial`
- 部署前 flags：六个 `READER_*` 均为 `false`；部署后仍全部为 `false`。
- 部署前 custom dump：`default d23e83b9d13bd73096e37e0fdbd9cb521f31602b9497ce3fdb1bdfc3889080f5`，`interactions 4fd5d4613ec00b24503899b5b34bfa5c6db25335939297f684648e6181905b7f`。
- 部署后 custom dump：`default 0dbad4a9d64283b38c6755a7fc4e2711b10e98b46219c39287df8e5d3e06cfc5`，`interactions 345d019fb9390aa973b9e582cda30eae72ce82655fab5619b6c7b65f163ac19c`。

## 验证证据

| 阶段 | 命令或检查 | 结果 |
| --- | --- | --- |
| 构建 | `docker compose ... --profile release build web worker static-frontend release` | 成功；生产 Webpack 成功，保留既有 799 KiB 资源告警 |
| Django check | `python manage.py check` | 通过 |
| migration dry-run | `python manage.py makemigrations --check --dry-run`；双 alias `migrate --check` | 通过 |
| 阶段测试 | `reader_access`/`reader_interactions` 真实 PostgreSQL | 11 passed |
| 完整 pytest | `.venv/bin/pytest -q` | 808 passed，3 skipped，396 subtests |
| E2E | `npm run test:e2e` | 30 passed，2 skipped |
| release job | `--profile release run --rm release` | 先 default、后 interactions、再 collectstatic，成功 |
| 健康 | 内部 HTTPS forwarded `/livez/`、`/healthz/`、`/readyz/`；Nginx `/__static_health__/` | 全部 200 |
| 表边界 | default 6 张 `reader_access_*`、0 张 `reader_interactions_*`；interactions 0/12 | 通过；外键全部留在 interactions |
| 恢复演练 | 两份 dump 分别恢复到隔离库，双 `migrate --check`、业务计数、表边界检查 | 通过；临时库已删除 |

## 运行产物

- web：`sha256:ddcb1178eb1fad71eb742b66e6d9e4b8070ba0b3bfe22325a649611bf2717cd4`
- worker：`sha256:fd1e8888d20d596b4d104844c49bc65a070220e9c6542b555af2834ed2798da3`
- static-frontend：`sha256:fbf4fab9e21cf3d9af5fed5ba6a639b9da3e8ecbbe1211b8367e13e75fd7e150`
- 活动公共 manifest：`20260816T171349272216Z-job92`，未生成新静态内容 release。

## 限制

RI-02 只交付模型/边界骨架。邮箱验证、session 业务、评论 API、政策后台、PDF、分享、限流和 reconciliation 尚未上线；六个开关必须保持关闭。
