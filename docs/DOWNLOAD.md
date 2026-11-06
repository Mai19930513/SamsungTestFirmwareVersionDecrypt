# 固件下载、续传与解密

Python 3.10+，先按根目录 `requirements.txt` 安装依赖。本功能是独立本地命令；定时监控仍只查询元数据，不在 GitHub runner 下载完整固件。静态网页的下载地址也仍需要 FUS 会话认证。

## 小流量验证

```bash
python -m samsung_firmware.download probe --model SM-F9760 --region CHC
```

不指定版本时先查询 FOTA 正式版，再请求 FUS。`probe` 只读取最多 1 MiB 的文件头和额外最多 64 KiB 的非零偏移样本，验证下载认证、Range 续传和 AES 解密后的 ZIP 文件头。它不会下载完整固件，也不会声称已校验整包；JSON 报告会标记 `full_archive_verified=false`。

如当前 Python 网络环境不能连接三星，而 Git Bash 的 curl 可以连接，可在命令末尾加 `--transport curl`。程序从 PATH 解析 curl 的绝对路径，不调用 PowerShell/CMD，不关闭 HTTPS 证书验证，不把认证参数放到命令行或日志中。该模式需要已安装 curl。

## 完整下载并自动解密

```bash
python -m samsung_firmware.download download --model SM-F9760 --region CHC --output-dir downloads
```

需要固定版本时加上三段或四段版本：

```bash
python -m samsung_firmware.download download \
  --model SM-F9760 --region CHC \
  --version F9760ZCU3AZJ8/F9760CHC3AZJ8/F9760ZCU3AZI1/F9760ZCU3AZJ8 \
  --output-dir downloads
```

只接受三星接口确实提供的文件。明确指定版本而服务器返回不同版本时拒绝继续，防止下载错包。未指定版本则使用本次服务器返回版本。

下载时写入 `.enc4.part`（或 `.enc2.part`），同目录保存不含密钥的 `.download.json` 清单。断网后重跑相同命令，校验文件身份后从已有字节继续。请求偏移、Content-Range、总长度或清单不匹配时保留原文件并报错，不向旧文件拼接另一份固件。Ctrl+C 保留下载进度；解密中断会清理本次不完整 ZIP，解密需从头重跑。

下载完成校验总大小、服务器提供的 CRC32，以及可用的整包 Content-MD5，并计算 SHA-256。解密使用流式 AES-128-ECB，不把整个固件载入内存；保留三星块填充，检查 ZIP 结构及每个成员 CRC，全部通过后才发布最终 `.zip`。加密文件会保留。没有服务器校验值时，SHA-256 仅用于记录本地文件身份，不能代替官方签名认证。

20 GB 左右固件需要同时容纳加密文件和 ZIP，建议预留至少 45 GB；程序检查剩余空间。解压需要额外空间。文件已有时不覆盖，清单不匹配时请换输出目录。

## 仅下载和离线解密

```bash
# 仅下载加密文件，并显式保留离线解密参数
python -m samsung_firmware.download download \
  --model SM-F9760 --region CHC --no-decrypt --save-key --output-dir downloads

# 用上一步产生的私有密钥文件离线解密
python -m samsung_firmware.download decrypt \
  --input downloads/文件名.zip.enc4 \
  --key-file downloads/文件名.zip.enc4.key.private.json \
  --output downloads/固件.zip
```

默认密钥只驻留内存。`--save-key` 是明确选择把密钥保存到本机的开关；不要把 `.key.private.json` 发送给别人、放入公开目录或上传 GitHub。

已有加密文件但没有密钥文件，也可指定它对应的准确版本，重新向 FUS 获取参数：

```bash
python -m samsung_firmware.download decrypt \
  --input downloads/文件名.zip.enc4 --output downloads/固件.zip \
  --model SM-F9760 --region CHC \
  --version AP版本/CSC版本/CP版本/第四段版本
```

本地 `.enc2` 使用地区、型号和服务器版本派生密钥；`.enc4` 使用 FUS 返回的 `LOGIC_VALUE_FACTORY/HOME` 与版本派生。三星以后若不再提供该历史包的解密参数，在线重新获取可能失败，已保存的私有密钥仍可离线使用。

`downloads/`、`.enc2`、`.enc4`、`.part`、私有密钥和下载报告均已忽略。工作流不会上传它们，查询 JSON 也不包含密钥、Cookie 或签名。

## 开源依据与验证范围

参考 Bifrost 主分支于 2026-11-07 查询到的提交 `ed73a034d3b27d84c68b2c21622a9de6618455de`（提交日期 2026-09-26），核对 `FusClient.kt`、`Request.kt`、`CryptUtils.kt`、`IFusClient.kt` 与 `Downloader.kt`。许可见 [第三方声明](../THIRD_PARTY_NOTICES.md)。

真实 `SM-F9760/CHC` 验证通过：FUS 查询、解密参数获取、下载初始化、1 MiB HTTPS 下载、64 KiB 非零偏移重读一致性、AES 解密得到 ZIP 文件头。下载 URL 的 `file` 参数须保留 `/`，编码成 `%2F` 在本次三星接口返回 404，已修正导出的 URL。

按用户要求未下载整份约 19.78 GB 固件。完整下载、断线续传、重启续传、错误 Range/清单拒绝、CRC 失败、错误密钥拒绝和完整 ZIP 解密校验使用本地小型固件样本测试。真实整包下载耗时、整包 CRC 和所有固件分区内容没有在本次验证中测试。
