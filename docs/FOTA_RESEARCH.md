# FOTA / FUS 实现依据

调研和真实接口验证日期：2026-11-05。以下仅依赖公开项目源码和三星接口返回，不把搜索摘要当成协议保证。

数据库隐私另外核对了本地安装的 PyMySQL 1.2.3 官方包源码 `pymysql/connections.py`，并模拟握手验证：显式传入 SSLContext 时强制 TLS，服务器不支持 TLS 则在认证前拒绝连接。本项目最低要求已验证的 PyMySQL 1.2.3，并在建立云连接前拒绝旧版本，避免旧库降级为未加密连接。此次官方在线变更记录请求超时，未据此推断该行为最早出现的版本。

## 版本列表与下载接口

- 三星 FOTA：`https://fota-cloud-dn.ospserver.net/firmware/{CSC}/{MODEL}/version.xml` 返回正式版、Android 版本以及升级来源版本；`version.test.xml` 返回测试版本哈希。公开测试列表没有固件下载地址，不能由一个 MD5 拼出下载链接。
- [samloader](https://github.com/samloader/samloader/tree/0e53d8032699a4039ea6f5310ebec05f8f417f07) 的 `request.py`、`fusclient.py`、`main.py` 展示了 FUS GenerateNonce → BinaryInform → BinaryInit → BinaryForMass 流程以及 `BINARY_NAME`、`MODEL_PATH`、`BINARY_BYTE_SIZE` 字段。定时监控只查询元数据；独立的 `samsung_firmware.download` 命令现已实现按需下载和解密，见 [使用说明](DOWNLOAD.md)。
- [Bifrost / SamloaderKotlin](https://github.com/zacharee/SamloaderKotlin/tree/ed73a034d3b27d84c68b2c21622a9de6618455de) 的 `FusClient.kt`、`FusClientLegacy.kt`、`Request.kt`、`CryptUtils.kt` 展示当前 Smart 接口与基于 `auth_param.dat` 的认证。旧固定 AES 密钥不能作为当前默认方案。
- 当前默认使用 `NF_SmartDownloadGenerateNonce.do` 和 `NF_SmartDownloadBinaryInform.do`。真实响应成功码为 `S00`；兼容 legacy 的 `200`。只保留版本、文件名、大小、服务器路径组装的 URL，不保留响应中的其他字段。

FUS 下载地址不是免认证公开直链。JSON 中 `requires_auth=true`、`direct_download=false`，使用者仍需重新建立 FUS 会话、初始化下载，并处理 `.enc4` 加密文件。客户端 nonce、signature、Cookie、解密密钥不随 JSON 导出。测试版固件通常不通过公开 FOTA 列表提供下载，输出 `not_provided_by_public_fota`，不把正式版链接标成测试版链接。

## 认证参数

认证参数来自上述 Bifrost 固定提交中的 `common/src/commonMain/composeResources/files/auth_param.dat`。运行时下载数据文件，不执行远程代码；下载失败会尝试相同 Git 对象的 GitHub API。默认校验：

```text
size: 820792
Git blob: ce2d4b3713a24c5e795144433837d65e3b94855e
SHA-256: 6f969176bbe3eca193b51509313ca45d32366cf1825a907f04fde5b64d3f35b4
```

参数放在被 Git 忽略的 `.cache/firmware/auth_param.dat`；可通过 `FW_FUS_AUTH_FILE` 指定本地文件，升级文件时同时设置 `FW_FUS_AUTH_SHA256`。首次下载失败会短暂复用失败状态，避免每个机型都重复下载。参数来自第三方协议研究，不是三星的稳定公开 API 承诺；接口变动会以错误状态保存。

## MD5 / HMAC

项目已有 `tmp/web-research/oneui9-fota-hmac-findings.md` 和保存的 CheckFirm `Tools.kt` 调研材料，记载了 [CheckFirm PR 8](https://github.com/Bluesion/CheckFirm/pull/8) 的 32 位 MD5 / 64 位 HMAC-SHA256 分流。本次重新请求该链接返回 404，因此没有把它描述为当前仍可访问的项目状态。实现依据是已保存的源码、独立计算和三星真实列表的共同标记。

使用候选字符串 `AP/CSC/CP` 的 ASCII 字节。HMAC 的公开协议参数默认为 `fcjimts25@%`，可配置。以下复算结果用于识别公共标记：

```text
HMAC-SHA256("fcjimts25@%", "all")
= d77ea9576c3dce257b4b6ed7d5c9f1f6a495022e592d5b5b35a7450dfa052a13
```

该标记不算作某个固件版本，不参与命中率分母。32/64 位以外的格式视为未知格式并保留旧数据，不擅自猜测算法。知道算法不代表任意哈希均可反解，候选范围和 CPU 预算仍会限制命中率。

## 已完成的在线验证

2026-11-05 查询 `SM-S9380/CHC`：FOTA 正式版为 `S9380ZCUECZJ1/S9380CHCECZJ1/S9380ZCUECZJ1`，FUS 返回对应四段版本、文件 `SM-S9380_3_20261013120710_rw9xwy5gj3_fac.zip.enc4` 和 `20252146016` 字节大小。查询没有发送设备 IMEI 或序列号。此记录是该日的验证样本，最新结果以实际运行后的 JSON 为准。
