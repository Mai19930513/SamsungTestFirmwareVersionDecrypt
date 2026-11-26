"""PostgreSQL connections configured only through explicit FW_PG_* settings."""
import os

from .mysql_errors import CloudDatabaseError


def postgres_error_summary(error):
    """Return a diagnostic without DSNs, server messages, passwords or row values."""
    if isinstance(error, CloudDatabaseError):
        return str(error)
    code = getattr(error, 'sqlstate', None)
    hints = {
        '28P01': '身份认证失败，核对 FW_PG_USER / FW_PG_PASSWORD。',
        '28000': '账号或连接来源未获授权。',
        '3D000': '数据库不存在，先创建大小写完全一致的 Sam_FW 数据库。',
        '42501': '账号缺少权限，需要目标 schema 的 USAGE/CREATE 和表的读写权限。',
        '42P01': '数据表不存在，请先完成 models 迁移。',
        '42703': '字段不存在，核对迁移后的表结构和字段大小写。',
        '23505': '唯一约束冲突，请核对业务键。',
        '25P02': '事务已失败，需要回滚后重试。',
        '53300': '数据库连接数已达上限。',
        '57014': '查询超时或被取消。',
    }
    if code in hints:
        return f'[postgres_{code}] ' + hints[code]
    return '[postgres] 操作失败；检查主机、端口、Sam_FW 库名、账号权限、网络和 TLS CA/主机名。'


def postgres_options(environ=None):
    """Validate explicit options; cloud TLS verifies both CA and hostname."""
    env = os.environ if environ is None else environ
    names = ('FW_PG_HOST', 'FW_PG_USER', 'FW_PG_PASSWORD')
    if any(not env.get(name) for name in names):
        raise CloudDatabaseError('[configuration] PostgreSQL 需要 FW_PG_HOST、FW_PG_USER、FW_PG_PASSWORD。')
    host = env['FW_PG_HOST'].strip()
    user = env['FW_PG_USER'].strip()
    database = env.get('FW_PG_DATABASE', 'Sam_FW').strip()
    if not host or any(token in host for token in ('://', '/', '@')) or any(c.isspace() for c in host):
        raise CloudDatabaseError('[configuration] FW_PG_HOST 必须是数据库主机名，不能填写 URI、路径或账号。')
    if not user or not database:
        raise CloudDatabaseError('[configuration] FW_PG_USER 和 FW_PG_DATABASE 不能为空。')
    try:
        port = int(env.get('FW_PG_PORT', '5432'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise CloudDatabaseError('[configuration] FW_PG_PORT 必须是 1–65535 的整数。') from None
    mode = env.get('FW_PG_SSLMODE', 'verify-full')
    if mode not in ('verify-full', 'disable'):
        raise CloudDatabaseError('[configuration] FW_PG_SSLMODE 必须为 verify-full；本地测试可用 disable。')
    if mode == 'disable' and host not in ('localhost', '127.0.0.1', '::1'):
        raise CloudDatabaseError('[configuration] 仅允许本机回环 PostgreSQL 禁用 TLS。')
    options = dict(host=host, port=port, user=user, password=env['FW_PG_PASSWORD'],
                   dbname=database, sslmode=mode, connect_timeout=10,
                   application_name='samsung-firmware',
                   options='-c search_path=public,pg_catalog -c timezone=UTC -c statement_timeout=30000 -c lock_timeout=10000')
    if env.get('FW_PG_SSL_CA', '').strip():
        options['sslrootcert'] = env['FW_PG_SSL_CA'].strip()
    return options


def postgres_connection():
    """Open a transactional dict-row connection; no .env loading or SQL on import."""
    import psycopg
    from psycopg.rows import dict_row

    options = postgres_options()
    try:
        return psycopg.connect(**options, row_factory=dict_row, autocommit=False)
    except Exception as error:
        raise CloudDatabaseError('云数据库连接失败：' + postgres_error_summary(error)) from None


def models_select(cursor):
    """Quote migrated columns with their original case (MySQL accepts name/Name)."""
    from psycopg import sql

    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='models'")
    actual = [row['column_name'] for row in cursor.fetchall()]
    projections = []
    for required in ('name', 'code', 'cc'):
        matches = [column for column in actual if column.lower() == required]
        if len(matches) != 1:
            raise CloudDatabaseError('[schema] public.models 缺少唯一的 name/code/cc 字段，请核对迁移结构。')
        projections.append(sql.SQL('{} AS {}').format(sql.Identifier(matches[0]), sql.Identifier(required)))
    return sql.SQL('SELECT {} FROM public.models').format(sql.SQL(',').join(projections))
