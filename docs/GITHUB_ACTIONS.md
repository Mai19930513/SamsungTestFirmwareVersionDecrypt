# GitHub 定时运行

工作流为 `.github/workflows/python-app.yml`，只支持分支运行，不在 PR 上运行。定时任务读取默认分支。默认按北京时间每天 07:00、09:00、11:00、13:00、15:00、17:00、19:00 运行；对应 UTC cron 为 `0 1-11/2,23 * * *`。GitHub 调度可能延迟，不能保证精确到达分钟。

## 上传后首次配置

1. 上传代码、`models.txt` 和公开 JSON，确认仓库启用了 Actions。不要上传 `.env`、`data/`、`.cache/`、设备标识或任何密钥。
2. 在仓库 Settings → Secrets and variables → Actions 配置下面的 Secrets 和 Variables。代码不会从你电脑的 `.env` 自动获取这些值。
3. 允许工作流写入仓库内容。工作流声明 `contents: write`；如果组织策略或分支保护禁止机器人提交，需要按仓库规则配置允许写入的分支。
4. 在 Actions → Samsung firmware monitor → Run workflow 手动运行一次，再启用日常调度。`storage` 和 `scan_mode` 选择 `configured` 时使用仓库 Variables。

## Secrets

| Secret | 用途 |
| --- | --- |
| `FW_STATE_KEY` | 默认必填，64 个十六进制字符，用于 AES-256-GCM 加密运行状态 |
| `TG_BOT_TOKEN` | Telegram 机器人 Token |
| `TG_USER_ID` | 个人数字 ID，正式版上线和回滚只发这里；需先向机器人发起对话 |
| `TG_CHAT_ID` | 可选群组 ID；仅数据库缺失时读入并保存，群组发送当前暂停 |
| `FW_MYSQL_HOST` | 云数据库主机，兼容旧 Secret `HOST` |
| `FW_MYSQL_USER` | 云数据库用户，兼容 `DBUSER` |
| `FW_MYSQL_PASSWORD` | 云数据库密码，兼容 `PASSWORD` |
| `FW_MYSQL_DATABASE` | 云数据库名称，兼容 `DATABASE` |
| `FW_MYSQL_CA_PEM` | 可选私有 CA 的 PEM 内容，只写入 runner 临时目录 |

在自己的终端生成状态密钥，再将结果复制到 `FW_STATE_KEY` Secret：

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

妥善保管此密钥，不要写入代码或公开日志。更换密钥后，旧状态无法直接解密；恢复会明确失败，不会静默清空数据库。需要重新开始时，先备份旧密钥和状态，再手动删除该分支对应的旧 `firmware-state-v1-*` artifacts。其他分支和仓库的状态不能混用。

`GITHUB_TOKEN` 由 Actions 自动提供，不需要创建 PAT；`actions: read` 用于读取上一轮状态 artifact。状态加密密钥仅传给恢复/打包步骤，不传给 FOTA/FUS 查询步骤。

## Variables

| Variable | 默认值 | 用途 |
| --- | --- | --- |
| `FW_STORAGE` | `local` | `local`、`cloud`、`both` |
| `FW_SCAN_MODE` | `full` | `full` 完整解码；`metadata` 仅查询版本、复用已知映射和获取下载信息 |
| `FW_PERSIST_STATE` | `true` | 是否跨次保存加密状态；关闭后本地数据库和待发送/补传队列随 runner 丢失 |
| `FW_STATE_RETENTION_DAYS` | `30` | 加密 artifact 保留天数，公共仓库可设 1–90，受仓库策略限制 |
| `FW_NOTIFICATIONS` | 自动 | 同时有 Token 和个人 ID 时开启；设 `false` 可明确关闭 |
| `FW_NOTIFY_BATCH_SECONDS` | `300` | 合并窗口秒数，到期由下一轮扫描发送 |
| `FW_NOTIFY_COOLDOWN_SECONDS` | `1800` | 同机型/地区最小发送间隔秒数 |
| `FW_NOTIFY_INITIAL` | `false` | 是否通知首次发现的现有正式版 |
| `FW_NOTIFY_DOWNLOAD_LINK` | `true` | 正式版消息是否附加同版本的 FUS 地址 |
| `FW_DOWNLOAD_CACHE_HOURS` | `0` | 第一阶段每次检查 FUS，避免错过先于手机 OTA 的服务器上架 |
| `FW_IO_WORKERS` / `FW_CPU_WORKERS` | `8` / `2` | 网络并发与解码进程数 |
| `FW_MAX_SECONDS` / `FW_MAX_CANDIDATES` | `30` / `5000000` | 每个型号/地区的搜索预算 |
| `FW_SEARCH_CACHE_HOURS` | `24` | 已完成搜索缓存时长；设 `0` 仍保留未完成断点 |
| `FW_MODEM_LIMIT` | `12` | 参与候选组合的历史 CP 数量 |
| `FW_READ_TIMEOUT` | `60` | 网络读取超时，秒 |
| `FW_MODEL_SOURCE` / `FW_MODELS_FILE` | `auto` / `models.txt` | 机型来源，设 `file` 可只监控文件内设备 |
| `FW_MYSQL_PORT` | `3306` | 旧数据库如使用 21777，必须明确填写 |
| `FW_DOWNLOADS` / `FW_TIMELINE` | `true` / `true` | 下载元数据及更新日志查询开关 |
| `FW_HISTORY_YEARS` | `4` | 搜索年份范围 |
| `FW_YEAR_CODES` / `FW_BOOTLOADER_CODES` / `FW_UPDATE_CODES` | 空 | 可选覆盖候选字符集合 |
| `FW_MONTH_CODES` / `FW_SERIAL_CODES` | 全部默认字符 | 可缩小月份和构建号范围 |

本地命令行仍使用 `.env`；上表是 GitHub runner 的默认值，与本地默认关闭通知、120 秒搜索预算不同。没有映射进工作流 `env` 的新变量不会自动传给 Python，增加自定义参数时需要一并修改该段。

## 每次运行的流程

1. 安装依赖，按仓库及分支恢复最近一份加密状态。
2. `full` 模式先运行快速元数据查询，将正式版变更加入合并队列，再运行限时解码和更新日志。第二阶段临时复用 24 小时内的成功 FUS 查询，减少同一轮重复请求；版本变化仍会重新查询。`metadata` 模式只运行一次元数据查询。
3. `local` 保存 SQLite；`cloud` 保存 MySQL；`both` 先写 SQLite 再写云端。云失败时，待补传队列随加密状态一起保留。
4. 使用 SQLite backup API 生成独立快照，将数据库、Telegram 队列、云同步队列压缩后加密，上传独立 artifact。不会打包整个工作目录、`.env`、设备标识、FUS 会话或数据库连接密码。
5. 只提交七个公开 JSON、Markdown、日志文件。部分 FOTA/云同步失败仍先保存可用结果，再将任务标记为失败，方便发现问题。

下载功能仍然只保存 FUS 文件地址与元数据，不在 GitHub runner 上下载数十 GB 的固件。地址需要 FUS 认证，也不会把 Cookie、签名或解密密钥写进公开 JSON。

## 状态保留与限制

GitHub 的 `local` 指本轮 runner 的 SQLite。跨次持久化由加密 artifact 提供；云模式也需要状态 artifact 保留通知重试和补传记录。artifact 会占用 Actions 存储额度，按保留期限自动过期。停跑超过保留时间、手动删除 artifact，或任务被强制终止而未完成上传时，最近的本地队列可能无法恢复。公开 JSON 可恢复已解码版本和版本基线，但不能替代通知进度与待补传记录；长期可靠存储应同时配置自己的云数据库。

上传、恢复或解密失败会让工作流失败；恢复失败后不会继续扫描并覆盖旧状态。程序正常返回错误时仍尝试保存状态，取消或超时不保证完成保存。发布推送失败时，已经上传的状态可在下一次运行恢复。

切换为 `cloud` 时，如恢复到上一轮的 SQLite，会只读合并其中的历史映射与较新快照，随后向云数据库写入固件数据，本地 SQLite 额外保存解码断点，避免上一次公开文件推送失败造成数据遗漏。

同一分支不会并发运行，也不会因新任务中断正在运行的一轮；GitHub 可能替换较早的等待任务。工作流固定引用已核验的 Action 提交：checkout v7.0.1、setup-python v7.1.0、upload-artifact v7.0.2；核验日期 2026-11-06。

## 最少需要填写哪些配置

仅监控正式版、解码测试版并保存进度：创建 **Secret `FW_STATE_KEY`** 即可，其余默认使用本地 SQLite 和加密 artifact。填写位置：仓库 Settings → Secrets and variables → Actions → Secrets。状态密钥生成命令见上文。

需要个人 Telegram 通知时，再创建 **Secrets `TG_BOT_TOKEN`、`TG_USER_ID`**；`TG_CHAT_ID` 会在数据库缺失时迁移入库；群组通知当前暂停。创建 Secret 后默认自动启用通知；可设 Variable `FW_NOTIFICATIONS=false` 关闭。

需要云数据库时，再创建 **Secrets `FW_MYSQL_HOST`、`FW_MYSQL_USER`、`FW_MYSQL_PASSWORD`、`FW_MYSQL_DATABASE`**，并在 **Variables** 设置 `FW_STORAGE=both` 或 `cloud`、正确的 `FW_MYSQL_PORT`。私有证书才需要 `FW_MYSQL_CA_PEM`。Secret 与 Variable 是两个标签页；普通执行参数放 Variables。

`FW_PERSIST_STATE=true`、`FW_SCAN_MODE=full`、`FW_CPU_WORKERS=2`、`FW_MAX_SECONDS=30`、`FW_MAX_CANDIDATES=5000000` 均已有默认值，不必逐个创建。`GITHUB_TOKEN` 自动提供，FOTA/FUS 公共查询无需三星账户 Secret。

断点随数据库加密备份，不增加 Secret。服务器测试哈希、正式版或搜索范围改变时自动重搜；不变时从未完成的位置继续，避免每轮重复扫描相同候选。已完成结果的缓存默认 24 小时，到期重新搜索；读取缓存不会续期。`metadata` 阶段保留原断点，`full` 阶段每完成一个设备即提交进度。详见 [断点续搜规则](CONFIGURATION.md#断点续搜与后续新增版本)。

代码变更 push 触发的监控工作流只执行 `--sync-notification-config`，从数据库优先读取群组 ID，缺失时读取 Secret 入库，随后加密备份；不会扫描固件或发送 Telegram。定时/手动执行仍运行扫描。`FW_NOTIFY_GROUPS` 当前在 workflow 固定为 `false`，恢复的旧队列也不能发群。通知格式与版本比较规则见 [配置说明](CONFIGURATION.md)。
