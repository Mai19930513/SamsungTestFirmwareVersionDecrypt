"""Keep both database drivers' diagnostics free of credentials and business rows."""
from .mysql_errors import CloudDatabaseError, mysql_error_summary
from .postgres import postgres_error_summary


def database_error_summary(error, backend):
    if isinstance(error, CloudDatabaseError):
        return str(error)
    return postgres_error_summary(error) if backend == 'postgres' else mysql_error_summary(error)
