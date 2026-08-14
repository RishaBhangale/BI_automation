# utils/logger.py

import logging
import os


# Hardcoded here to break the circular import:
# config.settings → encryption_utils → logger → config.settings
_LOG_DIR = "logs"


class SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that safely ignores writes to closed streams during process exit or pytest teardown."""
    def emit(self, record):
        try:
            if self.stream and not getattr(self.stream, "closed", False):
                super().emit(record)
        except (ValueError, OSError):
            pass


def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger that logs to both console and logs/framework.log.
    Avoids duplicate handlers and disables propagation to root.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    os.makedirs(_LOG_DIR, exist_ok=True)
    log_path = os.path.join(_LOG_DIR, "framework.log")

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = SafeStreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger