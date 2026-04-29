from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(
    *,
    base_dir: Path | None = None,
    level: int = logging.INFO,
    console: bool = True,
) -> None:
    """Set up rotating file + optional console logging.

    File goes to ``logs/watchtower.log`` under *base_dir*, rotating at 5 MB
    with 3 backups kept.
    """
    if base_dir is None:
        from config_loader import BASE_DIR as _fallback

        base_dir = _fallback

    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — rotating
    fh = RotatingFileHandler(
        log_dir / "watchtower.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler
    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # Quiet noisy third-party loggers
    for noisy in ("akshare", "urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
