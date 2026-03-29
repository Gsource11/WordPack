from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".wordpack"
MAX_LOG_BYTES = 10 * 1024 * 1024

_lock = threading.Lock()
_configured = False
NOISY_LOGGERS: dict[str, int] = {
    "argostranslate": logging.WARNING,
    "argostranslate.utils": logging.WARNING,
}


class DailySizeFileHandler(logging.Handler):
    def __init__(self, log_dir: Path, max_bytes: int = MAX_LOG_BYTES, encoding: str = "utf-8") -> None:
        super().__init__()
        self.log_dir = log_dir
        self.max_bytes = max_bytes
        self.encoding = encoding
        self._io_lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return


        line = f"{message}\n"
        try:
            with self._io_lock:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                target = self._resolve_target_path(datetime.now().strftime("%Y-%m-%d"))
                with target.open("a", encoding=self.encoding, newline="\n") as fp:
                    fp.write(line)
        except Exception:
            return

    def _resolve_target_path(self, date_str: str) -> Path:
        base = self.log_dir / f"{date_str}.log"
        if (not base.exists()) or base.stat().st_size < self.max_bytes:
            return base

        idx = 1
        while True:
            candidate = self.log_dir / f"{date_str}_{idx}.log"
            if (not candidate.exists()) or candidate.stat().st_size < self.max_bytes:
                return candidate
            idx += 1


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _configured

    with _lock:
        if _configured:
            return logging.getLogger("wordpack")

        root = logging.getLogger()
        root.setLevel(level)

        has_handler = any(getattr(h, "_wordpack_handler", False) for h in root.handlers)
        if not has_handler:
            handler = DailySizeFileHandler(LOG_DIR)
            handler._wordpack_handler = True  # type: ignore[attr-defined]
            handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s | %(levelname)s | %(process)d:%(threadName)s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root.addHandler(handler)

        logging.captureWarnings(True)
        for logger_name, logger_level in NOISY_LOGGERS.items():
            logging.getLogger(logger_name).setLevel(logger_level)
        _configured = True

    logger = logging.getLogger("wordpack")
    logger.info("Logging initialized. dir=%s noisy_loggers=%s", LOG_DIR, ",".join(sorted(NOISY_LOGGERS)))
    return logger


def install_global_exception_hooks(logger: logging.Logger | None = None) -> None:
    lg = logger or logging.getLogger("wordpack")

    def _sys_hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        lg.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = _sys_hook

    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            thread_name = args.thread.name if args.thread else "unknown"
            lg.critical(
                "Uncaught thread exception: %s",
                thread_name,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = _thread_hook  # type: ignore[assignment]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

