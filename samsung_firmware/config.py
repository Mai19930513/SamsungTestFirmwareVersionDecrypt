import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name, default=False):
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


def env_int(name, default, minimum=0, maximum=100000000):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    root: Path
    output_dir: Path
    data_dir: Path
    cache_dir: Path
    models_file: Path
    model_source: str
    db_backend: str
    storage_target: str
    io_workers: int
    cpu_workers: int
    connect_timeout: int
    read_timeout: int
    retries: int
    cache_seconds: int
    search_cache_hours: int
    download_cache_hours: int
    max_candidates: int
    max_seconds: int
    history_years: int
    modem_limit: int
    year_codes: str
    bootloader_codes: str
    update_codes: str
    month_codes: str
    serial_codes: str
    hmac_key: str
    downloads: bool
    timeline: bool
    notifications: bool

    @classmethod
    def from_env(cls, root, output_dir=None):
        root = Path(root).resolve()

        def path(name, default):
            value = Path(os.getenv(name, default)).expanduser()
            return value.resolve() if value.is_absolute() else (root / value).resolve()

        def codes(name, default, alphabet):
            value = os.getenv(name, default).upper()
            if default and not value:
                raise ValueError(f"{name} must not be empty")
            if any(char not in alphabet for char in value):
                raise ValueError(f"{name} contains unsupported characters")
            return "".join(dict.fromkeys(value))

        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        model_source = os.getenv("FW_MODEL_SOURCE", "auto")
        db_backend = os.getenv("FW_DB_BACKEND", "sqlite")
        storage_target = os.getenv("FW_STORAGE", "cloud" if db_backend == "mysql" else "local")
        if model_source not in {"auto", "file", "mysql"}:
            raise ValueError("FW_MODEL_SOURCE must be auto, file or mysql")
        if db_backend not in {"sqlite", "mysql"}:
            raise ValueError("FW_DB_BACKEND must be sqlite or mysql")
        if storage_target not in {"local", "cloud", "both"}:
            raise ValueError("FW_STORAGE must be local, cloud or both")
        return cls(
            root=root,
            output_dir=Path(output_dir).resolve() if output_dir else path("FW_OUTPUT_DIR", "."),
            data_dir=path("FW_DATA_DIR", "data"),
            cache_dir=path("FW_CACHE_DIR", ".cache/firmware"),
            models_file=path("FW_MODELS_FILE", "models.txt"),
            model_source=model_source,
            db_backend=db_backend,
            storage_target=storage_target,
            io_workers=env_int("FW_IO_WORKERS", 8, 1, 32),
            cpu_workers=env_int("FW_CPU_WORKERS", min(4, os.cpu_count() or 1), 1, 32),
            connect_timeout=env_int("FW_CONNECT_TIMEOUT", 5, 1, 120),
            read_timeout=env_int("FW_READ_TIMEOUT", 20, 1, 300),
            retries=env_int("FW_RETRIES", 2, 0, 5),
            cache_seconds=env_int("FW_CACHE_SECONDS", 300, 0, 86400),
            search_cache_hours=env_int("FW_SEARCH_CACHE_HOURS", 24, 0, 720),
            download_cache_hours=env_int("FW_DOWNLOAD_CACHE_HOURS", 6, 0, 720),
            max_candidates=env_int("FW_MAX_CANDIDATES", 5000000, 1),
            max_seconds=env_int("FW_MAX_SECONDS", 120, 1, 3600),
            history_years=env_int("FW_HISTORY_YEARS", 4, 0, 25),
            modem_limit=env_int("FW_MODEM_LIMIT", 12, 0, 100),
            year_codes=codes("FW_YEAR_CODES", "", alphabet[10:]),
            bootloader_codes=codes("FW_BOOTLOADER_CODES", "", alphabet),
            update_codes=codes("FW_UPDATE_CODES", "", alphabet[10:]),
            month_codes=codes("FW_MONTH_CODES", "ABCDEFGHIJKL", "ABCDEFGHIJKL"),
            serial_codes=codes("FW_SERIAL_CODES", "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", alphabet),
            hmac_key=os.getenv("FW_HMAC_KEY", "fcjimts25@%"),
            downloads=env_bool("FW_DOWNLOADS", True),
            timeline=env_bool("FW_TIMELINE", True),
            notifications=env_bool("FW_NOTIFICATIONS", False),
        )

    @property
    def timeout(self):
        return self.connect_timeout, self.read_timeout
