"""Shared logging setup for pipeline scripts."""

import logging
import sys
from pathlib import Path


def setup_logging(log_dir: Path, name: str) -> logging.Logger:
    # Windows consoles default to cp1252; never crash on ✎ ◆ ▤ or titles
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-8s  %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
