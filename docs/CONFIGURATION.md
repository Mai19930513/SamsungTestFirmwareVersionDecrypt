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
| `firmware_settings` | 私有通知配置（telegram_group_id），不导出公开 JSON |
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

`版本号` 是全部已验证历史；`当前服务器版本号` 是当前测试列表中仍存在的已解码项，最新测试版从后者中严格筛选高于正式版基线的固件。`解密数量` 是历史总量，`当前已解密数量 / 服务器固件数量` 才是当前命中率。同代常规测试分支可根据 FOTA 正式版推断 Android 版本，依据另存 `测试版安卓版本依据`；不同代或 Z 分支显示“待确认”，没有符合条件的已解密测试固件时显示“暂无测试版”。

## 版本字段与测试版筛选

三段版本依次为 AP / CSC / CP；Wi-Fi 设备可能没有 CP。FUS 可能附第四段数据版本。AP 的末六位依次是更新类型（U/S）、Bootloader 修订、系统分支、构建年份、构建月份、月内构建序号。Bootloader 和构建序号按 36 进制读取，年份 A–Z 对应 2001–2026，月份 A–L 对应 1–12。市场前缀（如 TB/ZC）和 U/S 不能用于判断时间先后。

例如 `F9260TBSHKZH2` 的 Bootloader 是 H（17），系统分支 K，构建于 2026 年 8 月、序号 2；`F9260ZCU1ZVK3` 的 Bootloader 是 1、Z 测试分支、2022 年 11 月、序号 3。后者属于历史测试固件，不能再显示为当前大版本测试版。Z 是测试标记，不能理解为高于所有正式分支，也不能据此推算 Android 大版本。

当前测试版要求仍在测试服务器哈希列表中，机型匹配、Bootloader 不下降、构建日期不早于正式版，且比较结果严格更新；非 Z 分支也不能低于正式分支。没有可靠正式版基线时不猜测“当前新版”。全部已验证历史仍保存在 `版本号` 中，解密旧哈希不会被宣传为新增测试版本。

## Telegram 合并通知

设置 `FW_NOTIFICATIONS=true` 并填写 `TG_BOT_TOKEN`、正整数个人 `TG_USER_ID`。同一轮到达发送时间的所有机型/地区汇总为一条消息；内测固件恢复编号列表、中文地区和完整 `AP/CSC/CP` 版本号，Wi-Fi 设备保留末尾 `/`。相同完整版本去重，AP 相同但 CSC/CP 不同的版本分别保留。标题的机型数量按机型 + CSC 组合统计，例如：

```text
三星新增内测固件—共<2>台机型(1/1)
1. #X800 - Tab S8+(香港版)
X800XXSBEZK1/X800OXMBEZK1/

2. #X900 - Tab S8 Ultra(香港版)
X900XXSBEZK1/X900OXMBEZK1/
```

多个机型的 FUS 领先 FOTA 上线消息也统一汇总，标题为「三星 FUS 新版本上线—共<N>台机型(页码/总页数)」。同轮既有内测又有 FUS 更新时，个人消息在同一条内分别列出两个区块，每个 FUS 条目保留 FOTA 当前正式版、FUS 版本、脚本发现时间及可点击的下载地址/更新说明。默认不逐条展示哈希；完整历史及哈希保留在 JSON/数据库。首次初始化默认静默（`FW_NOTIFY_INITIAL=false`），不推送历史解密补录、已过期的测试版或正式版本身。

`FW_NOTIFY_BATCH_SECONDS=300`：从首条结果起至少等待 5 分钟，期间继续合并；到期后由下一次扫描统一发送，不额外启动后台计时器。`FW_NOTIFY_COOLDOWN_SECONDS=1800`：同机型/地区至少间隔 30 分钟，汇总中的每个机型/地区都计入冷却。定时任务的实际发送时刻取决于扫描频率；可设合并窗口为 0 在本轮扫描完成后发送。仅在超过 3900 个 UTF-16 单位（含标题、页码和 emoji）时分页，优先保留完整机型条目，单个条目超长再按行分段。分页和各接收人的发送进度保存在私有队列中；部分发送成功后从未完成页续发，新结果进入下一批。重试时若 FUS 上线条件已失效，只移除剩余页中对应的 FUS 内容，保留其他机型和内测结果，不重复已成功的页面。升级前已经开始发送的旧格式消息按原有分段续发。失败的消息保留重试，429 的 retry_after 会被遵守。

群组当前暂停：本地默认 `FW_NOTIFY_GROUPS=false`，workflow 明确固定为 false；包括恢复的待发送群组目标也不会发送。以后恢复需显式修改开关。FUS 上线仍仅发送个人，群组摘要只含测试版。旧逐哈希格式的队列会标记为停用，避免修复上线后继续刷屏，记录保留 30 天。

群组 ID 获取顺序：配置的云数据库 → 本地 SQLite → 环境中的 `TG_CHAT_ID`（GitHub Secrets 注入）。数据库已有有效值时不会被 Secret 覆盖；缺失时从 Secret 写入 `firmware_settings.telegram_group_id`，并同步本地备份。保存 ID 不等于允许群组发送，通知关闭时也会迁移。ID 不写公开 JSON、代码或日志；Bot Token 仍来自环境，不保存到数据库。云库暂不可用且本地存在配置时沿用本地配置，下次继续同步。

```bash
# 仅迁移通知配置，不扫描固件，不调用 Telegram
python 三星测试版固件版本号解密.py --sync-notification-config
```

相关代码推送到 master 后，workflow 自动执行上述配置迁移并加密保存状态；push 触发不会扫描或发送。定时/手动任务继续按原流程扫描。SQLite 随既有 `FW_STATE_KEY` 加密 artifact 保存，云模式同时存入配置的 MySQL。

FUS 上线通知以本次成功查询的 `https://fota-cloud-dn.ospserver.net/firmware/{CSC}/{MODEL}/version.xml` 中 `<latest>` 为基线，即记录中的 `最新正式版`。只有 `最新固件下载.status=available`，且 FUS 返回的 `version` 严格高于这个 FOTA 正式版时，才产生上线事件。相同、落后、仅 FOTA 更新或回滚、本地旧快照落后于服务器，都不会触发上线通知；缺少有效 FOTA 基线或任一查询失败时也不产生。比较要求机型一致、Bootloader 和构建日期不倒退、非 Z 分支不下降，并且版本顺序严格增加；只补全 CP、增加第四段、改变 U/S 或无法确定先后的组件差异不算更高版本。

消息明确标注「FOTA 当前正式版」「FUS 版本」和「脚本发现时间」，发现时间不是三星上传或 OTA 推送时间。原先消息的「原版本」取自上次保存的 `最新服务器正式版`（缺失则取 `最新正式版`），并不是本次 FOTA 基线，因此会将已经公开的更新误报为上线。`最新服务器正式版` 继续记录服务器观察结果，不单独决定通知；`已记录FUS领先版本` 保存最近已处理的领先版本，用于重复扫描、查询失败后恢复和 FOTA 追平后的去重，首次静默初始化也会记录。即使 `FW_NOTIFY_INITIAL=true`，也必须先满足 FUS 高于 FOTA 的条件。

发送及重试前会用本轮结果再次核对待发送队列：FOTA 已追平或超过、FUS 已换成另一版本时去掉过期上线内容，保留同批其他有效更新；本轮缺少可用查询结果时延后发送包含该 FUS 条目的页面，避免恢复旧队列后误发。FUS 对比展示 AP 段，内测列表每行展示一个完整 `AP/CSC/CP` 版本，不添加底部说明；完整版本仍保存在 JSON/数据库，FUS 原始四段保留在下载元数据。服务器上线不代表设备已收到 OTA。「下载地址」「更新说明」使用可点击文字链接，不展示长 URL；下载地址默认附带（`FW_NOTIFY_DOWNLOAD_LINK=true`），需要 FUS 会话认证。

`data/telegram-outbox.private.json` 保存合并事件、目标哈希、发送进度和间隔；已完成事件保留 30 天。Telegram 已收到但客户端超时的极端情况仍可能在重试时重复，因为发送接口没有幂等键。开发回归测试全部模拟请求，不发送真实消息。

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
