# PostgreSQL 与 Sam_FW

程序支持 SQLite、MySQL、PostgreSQL。此改造不会自动切换原配置，MySQL 保持兼容。

第一阶段迁移范围为 **MySQL `defaultdb.models` → PostgreSQL 数据库 `Sam_FW`、schema `public`、表 `models`**。随后已补迁 **MySQL `Sam_FW` 的全部六张 `firmware_*` 表 → PostgreSQL `Sam_FW.public`**。`Sam_FW` 大小写必须完全一致。2026-11-26 生产目标已包含七张表、44,291 行；下文首次迁移命令不可直接对当前非空目标重跑。

## 先迁移机型表

VPS 的工具位于相邻的 `~/docker/database-migration/`，`config.json` 已添加 `samsung-models` job。连接参数由你手动填写；工具不加载本项目 `.env`。

1. 在 Aiven PostgreSQL 上创建空数据库 `Sam_FW`，确保账号可以创建 public schema 下的表、序列、索引，并读写数据。SQL 建库时必须用 `CREATE DATABASE "Sam_FW"`，避免自动折叠为小写。
2. 填写 `~/docker/database-migration/config.json` 中两端参数与 CA 文件路径。
3. 确认停止 models 的所有写入端；包括 GitHub Actions、手动机型管理、宿主机 cron 等。旧程序通常只读取 models，但迁移期间不能让其他管理脚本修改该表。
4. 执行：

```bash
cd ~/docker/database-migration
bash run.sh plan --job samsung-models --report reports/samsung-models-plan.json
bash run.sh migrate --job samsung-models --writers-stopped --archive archives/samsung-models-20261126
bash run.sh verify --job samsung-models --archive archives/samsung-models-20261126
```

新建的迁移目录必须不存在，目标 public schema 必须为空。迁移全部 models 字段和行，保留主键、索引、自增水位，保存原始行档案，并比对行数/内容指纹。所选表的触发器、越界外键和未支持结构会阻断；不选中的 `system_setting`、`update_minor` 等对象只记录为明确排除，留在源库。**迁移及 verify 完成前，不要启动应用初始化 firmware_* 表，否则空目标保护会拒绝迁移。**

迁移脚本当前不会创建 database，也不会删除源表。复核要在运行应用之前完成；应用随后创建 firmware_* 表，结构已合法变化，原迁移清单的全 schema 结构指纹不再匹配属于预期。

## 再配置应用

用环境变量或由你自行编辑的 `.env` 配置下列字段：

| 参数 | 设置 |
| --- | --- |
| `FW_DB_BACKEND` | `postgres` |
| `FW_MODEL_SOURCE` | `postgres`，读取 public.models |
| `FW_STORAGE` | `cloud`，或 `both` 保留 SQLite 副本 |
| `FW_PG_HOST` | Aiven PostgreSQL 主机名 |
| `FW_PG_PORT` | Aiven PostgreSQL 实际端口 |
| `FW_PG_USER` / `FW_PG_PASSWORD` | PostgreSQL 账号与密码 |
| `FW_PG_DATABASE` | `Sam_FW` |
| `FW_PG_SSL_CA` | PostgreSQL 服务 CA 文件路径 |
| `FW_PG_SSLMODE` | `verify-full`（默认） |

不要沿用 MySQL 端口、CA 或数据库名。PostgreSQL 参数不会读取旧 `HOST/DBUSER/PASSWORD/DATABASE` 回退值。`FW_MODEL_SOURCE` 与写入位置独立，两个 postgres 开关都需要设置。

应用新增的 PostgreSQL 路径包含六张 firmware_* 表、参数化查询、`ON CONFLICT` upsert、事务回滚、快照恢复、缓存和补传队列；DOUBLE 时间字段改为 DOUBLE PRECISION。MySQL 与 PostgreSQL 待补传文件按后端和目标库隔离，不会误把原库待补传文件发送到新库。旧 MySQL 待补传队列要保留并单独处理。

MySQL 中六张 firmware_* 表已补迁，包括设备快照、版本、哈希、设置、搜索进度和缓存。文件中的待补传队列属于数据库之外的状态，未随数据库迁移移动；旧队列仍需按后端隔离规则处理。程序可以按原流程导入已有公开 JSON / 本地 SQLite，但它们不能替代全部私有状态。

只读诊断（**只读取进程环境，不自动加载 `.env`**）：

```bash
cd ~/docker/SamsungTestFirmwareVersionDecrypt
.venv/bin/python -m samsung_firmware.postgres_check
```

诊断检查连接、models 的 name/code/cc 三个业务字段和行数，不创建表、不发起三星请求、不发送通知。普通 CLI 保留原有 `.env` 加载行为，本轮改造和测试没有调用它读取任何私密配置。

GitHub Actions 的修改只在本地仓库中；推送前先停用旧定时任务，配置新的 FW_PG_* Secrets/Variables，再按 [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md) 切换。本轮没有推送代码、修改远端 Secrets 或触发 Actions。

## 已完成的验证

2026-11-26：93 个离线单元/回归测试通过。独立 MySQL 8.4 → PostgreSQL 16 的集成测试把 models 从一个含其他表、视图和例程的源库迁至大小写保留的 Sam_FW_test_* 数据库，并验证其他对象未复制、缺表/越界外键/所选表触发器会被拒绝。

迁移后使用真实 PostgreSQL 驱动运行本项目：读取中文/emoji 机型和多个地区、创建 firmware_* 表、重复 upsert、事务冲突回滚后的继续写入、快照/版本/哈希/缓存、过期清理、写入失败后保留队列并重试。测试没有联网扫描固件或发送通知。

2026-11-26 生产进度：`defaultdb.models` 的 93 行首先迁入 PostgreSQL `Sam_FW.public.models`；14:26 核对发现另六张 firmware_* 表遗漏；14:34 已完成这六张表的补迁，共 44,198 行。目标七张表共 44,291 行，内容与迁移档案一致，原 models 保留，补迁独立复核通过。补迁流程、档案与复核命令见 [Samsung 迁移记录](../../database-migration/SAMSUNG.md)。应用配置、GitHub Actions 和生产部署尚未切换。
