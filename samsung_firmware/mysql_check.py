"""Read-only MySQL/TLS connection check; never creates tables or scans firmware."""
import os
from pathlib import Path

from dotenv import load_dotenv

from .mysql_errors import mysql_error_summary
from .storage import mysql_connection


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    print('MySQL 只读诊断：使用自定义 CA。' if os.getenv('FW_MYSQL_SSL_CA', '').strip()
          else 'MySQL 只读诊断：使用系统 CA；Aiven 私有 CA 需配置 FW_MYSQL_CA_PEM（GitHub）或 FW_MYSQL_SSL_CA（本地）。', flush=True)
    connection = None
    try:
        connection = mysql_connection()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1 AS connection_ok')
            cursor.fetchone()
            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            row = cursor.fetchone()
            if not row or not row.get('Value'):
                raise RuntimeError('Missing TLS session')
        print('MySQL 连接、身份认证、数据库选择和 TLS 加密均通过；未创建表、未写入数据。', flush=True)
        return 0
    except Exception as error:
        print('MySQL 诊断失败：' + mysql_error_summary(error), flush=True)
        return 1
    finally:
        if connection:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
