"""
Centralized logger. Every module imports `from utils.logger import log`.
Logs to both console and rotating daily files. Trade events go to a separate file.
"""
import sys
from pathlib import Path
from loguru import logger
from utils.config_loader import config


def setup_logger():
    """Configure loguru with file rotation and multiple sinks."""
    log_dir = Path(__file__).parent.parent / config.get("logging", "log_dir", default="logs")
    log_dir.mkdir(exist_ok=True)

    level = config.get("logging", "level", default="INFO")
    rotation = config.get("logging", "rotation", default="00:00")
    retention_days = config.get("logging", "retention_days", default=30)

    # Remove default handler
    logger.remove()

    # Console handler — pretty colored output
    logger.add(
        sys.stdout,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
               "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # General log file — everything
    logger.add(
        log_dir / "bot_{time:YYYY-MM-DD}.log",
        level=level,
        rotation=rotation,
        retention=f"{retention_days} days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {module}:{function}:{line} - {message}",
        enqueue=True,
    )

    # Trade-only log — critical events for audit
    logger.add(
        log_dir / "trades_{time:YYYY-MM-DD}.log",
        level="INFO",
        rotation=rotation,
        retention=f"{retention_days} days",
        filter=lambda record: record["extra"].get("trade", False),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        enqueue=True,
    )

    # Error-only log — for quick incident review
    logger.add(
        log_dir / "errors_{time:YYYY-MM-DD}.log",
        level="ERROR",
        rotation=rotation,
        retention=f"{retention_days} days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {module}:{function}:{line} - {message}\n{exception}",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    return logger


def log_trade(msg: str, **kwargs):
    """Log a trade event to the dedicated trade log."""
    extra_str = " | ".join(f"{k}={v}" for k, v in kwargs.items())
    full_msg = f"{msg} | {extra_str}" if extra_str else msg
    logger.bind(trade=True).info(full_msg)


# Initialize on import
log = setup_logger()
