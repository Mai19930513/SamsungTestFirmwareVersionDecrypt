import argparse
import multiprocessing
from dataclasses import replace
from pathlib import Path
from contextlib import ExitStack

from dotenv import load_dotenv

from .config import Settings
from .files import run_lock
from .pipeline import execute_run


def main(argv=None):
    parser = argparse.ArgumentParser(description="三星 FOTA 固件查询、哈希匹配、数据库和下载信息导出")
    parser.add_argument("--model", action="append", help="只处理指定机型，可重复")
    parser.add_argument("--region", action="append", help="只处理指定 CSC，可重复")
    parser.add_argument("--metadata-only", action="store_true", help="抓取并查询下载信息，跳过穷举")
    parser.add_argument("--sync-notification-config", action="store_true", help="仅保存 Telegram 群组配置，不扫描固件或发送消息")
    parser.add_argument("--migrate-only", action="store_true", help="仅将现有 JSON 导入数据库，不联网")
    parser.add_argument("--force", action="store_true", help="重新验证 HTTP 缓存、跳过搜索/下载缓存并从头搜索")
    parser.add_argument("--output-dir", help="JSON 和日志输出目录")
    parser.add_argument("--no-downloads", action="store_true", help="关闭 FUS 下载信息查询")
    parser.add_argument("--no-timeline", action="store_true", help="关闭更新日志查询")
    parser.add_argument("--storage", choices=("local", "cloud", "both"), help="保存到本地 SQLite、云 MySQL 或同时保存")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    multiprocessing.freeze_support()
    try:
        settings = Settings.from_env(root, args.output_dir)
        settings = replace(settings, downloads=settings.downloads and not args.no_downloads,
                           timeline=settings.timeline and not args.no_timeline,
                           storage_target=args.storage or settings.storage_target)
        with ExitStack() as stack:
            for directory in sorted({settings.output_dir, settings.data_dir}):
                stack.enter_context(run_lock(directory))
            return execute_run(settings, args)
    except KeyboardInterrupt:
        print("已中断；已完成的数据库事务和旧 JSON 保留。")
        return 130
    except Exception as error:
        safe = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(f"运行失败: {safe}")
        return 1
