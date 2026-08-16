"""Logging setup."""
import io
import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logger(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    log_file = log_dir / f'run-{today}.log'

    fmt = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'

    # Windows 콘솔이 CP949일 때 한글·특수문자 깨짐 방지
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    stream = sys.stdout
    if getattr(stream, 'encoding', 'utf-8').lower().replace('-', '') not in ('utf8', 'utf-8'):
        stream = io.TextIOWrapper(
            getattr(stream, 'buffer', stream),
            encoding='utf-8',
            errors='replace',
            line_buffering=True,
        )

    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(stream),
        ],
    )
