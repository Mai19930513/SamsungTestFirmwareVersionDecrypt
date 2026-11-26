"""Explicit environment only. Read-only connection/models check; no .env or scanning."""
from .postgres import postgres_connection, postgres_error_summary, models_select


def main():
    connection = None
    try:
        connection = postgres_connection()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            from psycopg import sql
            cursor.execute(models_select(cursor) + sql.SQL(' LIMIT 0'))
            cursor.execute('SELECT count(*) AS count FROM public.models')
            count = cursor.fetchone()['count']
        print(f'PostgreSQL 连接与 models 字段检查通过，共 {count} 行；未写入数据。')
        return 0
    except Exception as error:
        print('PostgreSQL 诊断失败：' + postgres_error_summary(error))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
