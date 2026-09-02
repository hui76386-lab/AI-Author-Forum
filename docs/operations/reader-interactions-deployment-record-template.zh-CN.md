# 读者互动部署记录模板

每次 `RI-*` 任务部署复制本模板形成一份不可变记录。记录不得包含数据库密码、
邮箱、token、Cookie、评论正文或预签名下载 URL。

## 1. 版本和范围

- 任务卡：`RI-__`
- 目标环境：
- 开始/结束时间（UTC）：
- 操作人：
- Git SHA：
- 应用 release/image digest：
- 上一个可回滚应用版本：
- 公共 manifest version：
- protected manifest version（适用时）：

## 2. 变更和开关

- 变更文件/迁移：
- `default` migration 版本：
- `interactions` migration 版本（适用时）：
- 部署前后 feature flags：
- 数据备份/恢复点：

## 3. 验证证据

| 阶段 | 命令或检查 | 结果 | 证据/备注 |
| --- | --- | --- | --- |
| 构建 |  |  |  |
| Django check |  |  |  |
| migration dry-run |  |  |  |
| 受影响测试 |  |  |  |
| 完整 pytest |  |  |  |
| E2E |  |  |  |
| release job |  |  |  |
| `/livez/` |  |  |  |
| `/healthz/` |  |  |  |
| `/readyz/` |  |  |  |
| `/__static_health__/` |  |  |  |
| manifest/release 一致性 |  |  |  |

## 4. 激活和回滚

- 新版本激活时间：
- 原子切换/审计日志标识：
- 回滚触发条件：
- 回滚命令和目标版本：
- 回滚演练结果：

## 5. 限制和签署

- 跳过项及原因：
- 已知限制/风险：
- 产品签署：
- 编辑签署：
- 安全/隐私签署：
- 运维签署：
