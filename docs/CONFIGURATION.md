# 配置与运行

GitHub 定时部署请先阅读 [Actions 配置说明](GITHUB_ACTIONS.md)：runner 使用仓库 Secrets/Variables，并通过加密 artifact 保留本地数据库和队列。

Python 3.10 及以上，Windows 使用 Git Bash。原入口文件名保持不变，命令从项目目录执行。

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python 三星测试版固件版本号解密.py
```

Linux 激活命令为 `source .venv/bin/activate`。已有 `.env` 不需要覆盖；按 `.env.example` 追加所需配置。系统环境变量优先于 `.env`。相对配置路径均相对于项目根目录，`--output-dir` 相对于当前工作目录。

## 自定义位置

| 位置 / 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `models.txt` | 现有文件 | 每行 `名称,型号,CSC1\|CSC2`，支持空行和 `#` 注释 |
| `FW_MODEL_SOURCE` | `auto` | 合并 models.txt 与历史 JSON；`file` 仅处理文件中的设备；`mysql` 读取旧 models 表 |
| `FW_IO_WORKERS` | `8` | FOTA/文档网络并发，最大 32；FUS 并发最多 4 |
| `FW_CPU_WORKERS` | CPU 数与 4 的较小值 | 搜索进程数，工作进程不写文件或数据库，主进程收到每个设备结果后立即提交断点 |
| `FW_CONNECT_TIMEOUT` / `FW_READ_TIMEOUT` | `5` / `20` 秒 | 连接和读超时 |
| `FW_RETRIES` | `2` | GET 连接错误、429 和部分 5xx 的有限重试 |
| `FW_CACHE_SECONDS` | `300` | HTTP 缓存，过期使用 ETag/Last-Modified 条件请求 |
| `FW_SEARCH_CACHE_HOURS` | `24` | 完整搜索结果缓存；到期重搜。未完成的断点不受此 TTL 限制 |
| `FW_DOWNLOAD_CACHE_HOURS` | `6` | 同一正式版的成功 FUS 查询缓存，版本变化立即重查 |
| `FW_MAX_CANDIDATES` / `FW_MAX_SECONDS` | `5000000` / `120` | 每个机型/地区的穷举上限，达到任一项则标记 `bounded` 并保存断点，下轮从未检查的候选继续 |
| `FW_HISTORY_YEARS` | `4` | 搜索近四年和当前年，旧版本通过历史缓存保留 |
| `FW_MODEM_LIMIT` | `12` | 参与组合的历史 CP 数量 |
| `FW_YEAR_CODES` | 空 | 覆盖年份字符集合，如 `XYZ` |
| `FW_BOOTLOADER_CODES` | 空 | 覆盖 BL 字符集合，如 `6789AB` |
| `FW_UPDATE_CODES` | 空 | 覆盖主要更新字符集合，如 `BCZ` |
| `FW_MONTH_CODES` | `ABCDEFGHIJKL` | 搜索月份，如仅 10–12 月设 `JKL` |
| `FW_SERIAL_CODES` | `123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ` | 每月构建序号，需要 0 时自行加入 |
| `FW_HMAC_KEY` | 公开协议参数 | HMAC-SHA256 匹配参数 |
| `FW_OUTPUT_DIR` / `FW_DATA_DIR` / `FW_CACHE_DIR` | `.` / `data` / `.cache/firmware` | 输出、SQLite、缓存目录 |
| `FW_DOWNLOADS` | `true` | 查询最新正式版下载信息 |
| `FW_FUS_PROTOCOL` | `smart` | 当前 Smart 接口；`legacy` 为老接口配合当前认证 |
| `FW_FUS_AUTH_FILE` / `FW_FUS_AUTH_SHA256` | 空 | 可选本地认证参数及 SHA-256，来源见调研文档 |
| `FW_TIMELINE` | `true` | 更新日志查询，三天内不重复，失败保留旧内容 |
| `FW_NOTIFICATIONS` | `false` | 开启后才发送 Telegram 消息 |

年份 `A..Z` 已知对应 2001–2026，不会擅自把 `Z` 回绕为 `A`。跨越 2026 后，应按服务器实际规则调整年份字符与 `decode.py` 的日期解释。不能保证覆盖特殊 CSC 后缀及所有 CP 组合，未命中项完整保存在 `未解密哈希`，不会伪造结果。

`BOOTSTRAP` 位于 `samsung_firmware/decode.py`，用于正式版和历史记录都不存在时推导前缀；常见 CHC、CHN、TGY、KOO、EUX、INS、XAA、ATT 已提供。有正式版时优先从其 AP/CSC/CP 推导。Wi-Fi 固件保留末尾 `/`。

## 常用命令

```bash
# 导入现有 JSON，不请求 FOTA/FUS，不读取 MySQL models 表
python 三星测试版固件版本号解密.py --migrate-only

# 抓取元数据、匹配已知版本、获取下载地址，跳过穷举
python 三星测试版固件版本号解密.py --metadata-only --no-timeline

# 单设备完整搜索，保留其他设备的历史数据
python 三星测试版固件版本号解密.py --model SM-S9380 --region CHC

# 跳过搜索/下载缓存，并向服务器重新验证 HTTP 内容
python 三星测试版固件版本号解密.py --model SM-S9380 --region CHC --force

FW_MAX_SECONDS=30 FW_CPU_WORKERS=2 python 三星测试版固件版本号解密.py --no-downloads --no-timeline
```

退出码：`0` 完成，`1` 配置/数据库/文件等致命错误，`2` 部分 FOTA 失败但已保存其他结果，`130` 手动中断。FUS 不可用通过字段表达。`--metadata-only` 可能发现当前正式版本身匹配测试列表的哈希，因此不是只读模式。`--force` 下的 HTTP 304 仍是服务器重新验证后的缓存。

## 数据库

可以手动选择保存位置，无需修改代码：

```bash
python 三星测试版固件版本号解密.py --storage local
python 三星测试版固件版本号解密.py --storage cloud
python 三星测试版固件版本号解密.py --storage both
```

`local` 使用本地 SQLite，`cloud` 使用云 MySQL，`both` 同时写入。长期默认在 `.env` 设置 `FW_STORAGE=local/cloud/both`。优先级为 `--storage` > `FW_STORAGE` > 旧 `FW_DB_BACKEND`，未配置时默认 local。机型来源 `FW_MODEL_SOURCE` 是独立选项，与数据保存位置无关。GitHub Actions 手动运行也提供 storage 下拉菜单，定时任务读取仓库变量 `FW_STORAGE`。

双库模式先提交本地，再提交云端；两台数据库没有分布式事务。云端中断返回退出码 `3`，本地数据和 `data/cloud-pending-*.private.json` 待同步队列保留，下次选择 cloud/both 自动补传。云端单独模式连接失败时退出，不伪装成功；写入失败则保留私有待同步队列。队列按云端主机/端口/库名的哈希隔离，里面不含连接密码。切换保存位置后会同步所有现有历史记录，不只同步当前筛选的机型。

默认 SQLite 文件为 `data/firmware.sqlite3`。WAL、30 秒忙等待、参数化 SQL、批量 upsert 和事务保证重复运行不重复插入。主进程统一写入，数据库保存完成后再原子替换 JSON。数据库快照较新时，下次运行优先恢复它。

| 表 | 内容 |
| --- | --- |
| `firmware_devices` | 每个机型/地区的最新记录，含下载信息与抓取状态 |
| `firmware_versions` | 已验证哈希、算法、AP/CSC/CP、BL、构建年月及序号、首次/最近观察时间、存在状态 |
| `firmware_hashes` | 服务器哈希、算法、观察时间和移除状态，不含公共 all 标记 |
| `firmware_search_progress` | 每个机型/地区的候选组、CP 位置、累计候选、固定搜索输入及已发现映射 |
| `firmware_search_cache` | 搜索输入指纹与完成结果，30 天后清理 |

首次导入的观察时间表示导入时间，不等于三星上传时间；`最新测试版上传时间` 也是本程序观察到新版本的时间。离线导入时 `active` 可为 NULL，只有成功抓取测试列表才更新存在状态。失败不会把所有历史版本标为移除。达到预算的搜索标记为 `bounded`，每个设备完成后立即将断点独立提交到 SQLite。后续设备失败或导出失败，不会撤销已提交的搜索进度。正在计算但还没返回的设备可能重做本轮预算。

MySQL 使用 `FW_DB_BACKEND=mysql`，配置 `FW_MYSQL_HOST`、`FW_MYSQL_PORT`、`FW_MYSQL_USER`、`FW_MYSQL_PASSWORD`、`FW_MYSQL_DATABASE`，支持旧 `HOST/DBUSER/PASSWORD/DATABASE` 回退。旧端口固定为 21777，新默认 3306，旧部署需显式设置 `FW_MYSQL_PORT=21777`。账号需要创建上述表和增删改查权限，不修改旧 `models` 表。TLS 验证主机名和系统 CA，私有 CA 使用 `FW_MYSQL_SSL_CA`，不能静默关闭验证。

## 下载 JSON 字段

完整 JSON 和精简 JSON 中，每个 `[型号][CSC]` 都包含 `最新固件下载链接`（字符串或 null）和 `最新固件下载` 对象：

```json
{
  "status": "available",
  "source": "samsung_fus",
  "protocol": "smart",
  "version": "服务器返回的四段版本",
  "requested_version": "FOTA 查询得到的版本",
  "url": "服务器路径组装的 FUS 下载地址",
  "filename": "服务器返回的文件名.zip.enc4",
  "size_bytes": 20252146016,
  "encrypted": true,
  "requires_auth": true,
  "direct_download": false,
  "checked_at": "2026-11-05 21:27:01",
  "error": null
}
```

状态包括 `available`、`unavailable`、`error`、`stale`、`disabled`、`not_checked`。同版本查询网络失败保留旧地址并标记 `stale`，版本变化不拿旧链接冒充新版本。FOTA 失败也不会把旧数据称为刚确认的最新版。链接需要三星认证；页面提供复制按钮和状态，不声称点击即可下载。测试版下载固定标记 `not_provided_by_public_fota`，不会误用正式版链接。

`版本号` 是全部已验证历史；`当前服务器版本号` 是当前测试列表中仍存在的已解码项，最新测试版从后者选择。`解密数量` 是历史总量，`当前已解密数量 / 服务器固件数量` 才是当前命中率。测试版 Android 版本无法从哈希可靠确认，保留为“未知”。

## Telegram 正式版上线通知

设置 `FW_NOTIFICATIONS=true` 并填写 `TG_BOT_TOKEN`、`TG_USER_ID`。正式版上线及回滚通知固定只发个人 `TG_USER_ID`，不发 `TG_CHAT_ID` 群组；个人 ID 必须为正整数，缺失时不会回退发群。旧待发送队列中的正式版群组目标也会在发送前移除。测试版通知仍可同时发个人和可选的 `TG_CHAT_ID` 群组。

FOTA 或 FUS 正式版变化会产生独立通知，即使没有任何新增测试版也会发送。优先使用 FUS 返回的服务器版本，可能早于手机 OTA；通知明确区分服务器上线与手机推送，回退版本另标记为回滚。希望每次轮询都检查 FUS 是否提前上架时，设置 `FW_DOWNLOAD_CACHE_HOURS=0`；轮询间隔由定时任务控制。

默认 `FW_NOTIFY_DOWNLOAD_LINK=true`，取得同版本有效下载信息时附地址和“需要 FUS 认证”说明；失败时说明本次未取得链接，不附旧版本链接。`FW_NOTIFY_INITIAL=false` 避免首次从空数据库扫描时批量发送所有现有正式版；设 true 可通知首次发现。新测试版仍按原功能通知。

`data/telegram-outbox.private.json` 持久化待发送事件并记录每个收件目标的进度。失败下次重试，已完成事件保留 30 天用于去重；目标标识仅存哈希，不存 Token 或 chat ID。普通重复扫描不重发正式版。网络超时发生在 Telegram 已收取但客户端未确认时，重试仍可能产生重复，这是外部接口没有幂等键的限制。

通知内容仅包含公开固件信息。开发验证默认禁用实际发送，使用本地模拟请求检查消息与重试。

## 隐私与发布细则

Smart 查询默认仅发送机型、CSC 和固件版本，不发送 IMEI 或设备序列号。某设备必须通过 legacy 查询时，可将自己的标识放在 `data/device-identities.private.json`，格式为 `{ "SM-型号/CSC": "自己的标识" }`，设置 `FW_FUS_PROTOCOL=legacy` 和 `FW_DEVICE_IDENTITIES_FILE=data/device-identities.private.json`。该值只发给三星 FUS，不写数据库、缓存、JSON、日志或 Telegram，不生成或枚举设备标识。

`.env`、`.env.*`（示例除外）、数据库、WAL、缓存、私有 JSON 和调研临时文件均被 Git 忽略。工作流仅暂存七个公开输出文件。GitHub runner 会将白名单内的 SQLite 和通知/同步队列加密后上传独立 artifact，依靠 `FW_STATE_KEY` 恢复；不会提交数据库原文件。长期持久化也可配置自己的 MySQL。`.gitignore` 不能撤回已被 Git 跟踪的秘密。

数据库和缓存没有额外的静态加密。POSIX 尝试设置私有文件权限，Windows 的 `chmod` 不能替代 NTFS ACL；磁盘加密和账号权限由部署环境管理。MySQL 使用 TLS。错误信息不输出完整 HTTP 响应、数据库 DSN、代理凭据或认证头。

`run.sh` 是本地被忽略文件，现只运行并生成数据，不自动拉取、提交或推送代码。可运行 `bash run.sh --metadata-only --no-timeline`。需要发布时审核公开输出后自行执行 Git 操作。

## 断点续搜与后续新增版本

每轮先查询 FOTA 测试哈希列表，再决定是否复用断点。输入指纹包含机型、地区、正式版、完整测试哈希集合、搜索字符范围、历史 CP 限制和 HMAC 参数；公共 `all` 标记不参与搜索。服务器新增、删除或恢复一个哈希，正式版变化，或者搜索范围变化，都会使旧断点失效，从头搜索。这样即使某个候选在上一轮已经检查过，后来服务器才发布它的哈希，也仍会被重新覆盖。

输入未变化时继续上次未完成的位置，直接定位候选组和组内 CP，不重新生成整个前缀。搜索计划固定本轮起点使用的历史版本及 CP 顺序；搜索中发现新映射不会反过来打乱后续顺序。外部导入新的历史版本会开启新计划。修改每轮时间或候选数量预算不会丢弃断点；`--force` 主动从头搜索。`--metadata-only` 不覆盖已有搜索断点。

已完成搜索默认缓存 24 小时（`FW_SEARCH_CACHE_HOURS`），命中缓存不会延长过期时间；设为 `0` 可关闭已完成结果缓存，同时保留未完成搜索的续搜能力。未命中并不等于服务器没有该版本，只表示当前候选规则没有匹配成功；特殊前缀、CSC/CP 组合仍可能需要扩展规则。

所有保存模式都在 `data/firmware.sqlite3` 中保留断点；`cloud` 模式的固件快照仍写入云端，本地 SQLite 只额外保存搜索状态。GitHub Actions 沿用 `FW_STATE_KEY` 加密备份这份数据库，不需要新增 Secret。只有成功上传 artifact，下一台 runner 才能恢复本轮进度；强制取消或 runner 超时可能丢失尚未上传的进度。

`解密统计` 新增 `resumed`（是否复用计划）、`candidates_total`（当前计划累计检查数）、`candidates_per_second`（本轮速度）；`candidates` 仍表示本轮新检查数。`cached` 表示复用已完成结果，`bounded` 表示已保存断点，`exhausted` 表示已覆盖当前候选范围。工作流摘要展示这些统计。

## 本地固件文件下载

固件监控中的 `FW_DOWNLOADS` 仍指下载元数据查询。实际下载、续传和解密请使用独立命令 `python -m samsung_firmware.download`，见 [下载与解密说明](DOWNLOAD.md)，不需要云数据库或 Telegram 配置。
