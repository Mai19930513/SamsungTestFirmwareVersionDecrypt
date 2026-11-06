"""Safe MySQL diagnostics: classify errors without logging connection secrets."""
import socket
import ssl


class CloudDatabaseError(RuntimeError):
    """A controlled message that can safely appear in Actions logs."""


def mysql_error_summary(error):
    if isinstance(error, CloudDatabaseError):
        return str(error)
    queue = [error]
    chain = []
    seen = set()
    while queue:
        current = queue.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        queue.extend((getattr(current, 'original_exception', None), current.__cause__, current.__context__))
    for current in chain:
        if isinstance(current, ssl.SSLCertVerificationError):
            if getattr(current, 'verify_code', None) == 62 or 'hostname mismatch' in str(current).lower():
                return '[tls_hostname] TLS 证书与主机名不匹配；FW_MYSQL_HOST 必须是 Aiven 提供的 Host，不要使用 IP 或其他别名。'
            return '[tls_certificate] TLS 证书验证失败；从该 Aiven 服务下载 CA 证书，将完整 PEM 内容设置为 GitHub Secret FW_MYSQL_CA_PEM。本地使用 FW_MYSQL_SSL_CA 指向证书文件。'
    for current in chain:
        if isinstance(current, socket.gaierror):
            return '[dns] 无法解析 MySQL 主机；核对 FW_MYSQL_HOST，只填主机名，不包含 mysql://、端口或路径。'
        if isinstance(current, (TimeoutError, socket.timeout)):
            return '[timeout] MySQL 连接超时；核对 Aiven 服务状态、FW_MYSQL_PORT 和允许访问的 IP 范围。'
        if isinstance(current, ConnectionRefusedError):
            return '[connection_refused] MySQL 连接被拒绝；核对 Aiven 服务状态及实际端口，端口不能沿用其他服务的值。'
        if isinstance(current, ssl.SSLError):
            return '[tls_handshake] MySQL TLS 握手失败；核对服务端 TLS 配置和 CA 证书。'
    hints = {
        1044: '账号没有访问指定数据库的权限；核对 FW_MYSQL_DATABASE 和数据库授权。',
        1045: 'MySQL 拒绝登录；核对 FW_MYSQL_USER、FW_MYSQL_PASSWORD 和允许连接的来源。',
        1049: '指定数据库不存在；先在 Aiven 创建数据库，FW_MYSQL_DATABASE 必须与其名称及大小写一致。',
        1064: '建表或查询 SQL 语法不兼容；请提供此错误码及失败阶段以检查服务端兼容性。',
        1142: '账号缺少表操作权限；需要 CREATE、SELECT、INSERT、UPDATE、DELETE。',
        1143: '账号缺少列操作权限；检查指定数据库的授权。',
        1226: 'MySQL 账号或服务达到资源配额；检查 Aiven 服务和连接数限制。',
        1040: 'MySQL 连接数已达上限；检查 Aiven 当前连接数。',
        2003: '无法连接 MySQL；核对 Host、实际端口、服务状态及访问限制。',
        2006: 'MySQL 连接已断开；检查服务状态、连接超时和网络。',
        2013: 'MySQL 查询期间连接中断；检查服务状态和网络。',
        2026: 'MySQL TLS 连接失败；核对 CA 证书和服务器 TLS 支持。',
        3159: '服务器要求安全连接；本程序要求 TLS，请核对客户端依赖和服务配置。',
    }
    for current in chain:
        code = current.args[0] if current.args and type(current.args[0]) is int else None
        if code in hints:
            return f'[mysql_{code}] {hints[code]}'
        if code is not None and 1000 <= code <= 9999:
            return f'[mysql_{code}] MySQL 操作失败；请根据错误码检查服务配置和权限。'
    return '[unknown] 云数据库操作失败；运行 python -m samsung_firmware.mysql_check 检查连接，或提供失败阶段进一步排查。'
