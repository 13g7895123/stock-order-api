"""Loguru 日誌設定。

Sink 列表（見 plan.md §5 / plan-account.md §10）：
  1. 終端（INFO 彩色）
  2. logs/app.log          人類可讀，每日輪替 30 天
  3. logs/app.jsonl        JSON 結構化日誌
  4. logs/audit.log        稽核事件（有 extra["audit"] == True）
  5. logs/error.log        ERROR 獨立檔
  6. （選用）Qt signal sink by register_qt_sink()
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

_configured = False


def setup_logging(log_dir: Path | str = "logs", level: str = "INFO") -> None:
    """初始化全域 logger。多次呼叫會先移除現有 sink 再重設。"""
    global _configured
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # 1) 終端
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level:<7}</level> "
            "<cyan>{extra[event]!s:<20}</cyan> "
            "{message}"
        ),
        filter=_inject_default_extra,
    )

    # 2) 人類可讀主日誌
    logger.add(
        log_dir / "app.log",
        rotation="00:00",
        retention="30 days",
        compression="zip",
        level="DEBUG",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{name}:{function}:{line} | event={extra[event]} | {message}"
        ),
        filter=_inject_default_extra,
    )

    # 3) JSON 結構化
    logger.add(
        log_dir / "app.jsonl",
        rotation="100 MB",
        retention="90 days",
        level="DEBUG",
        enqueue=True,
        serialize=True,
    )

    # 4) 稽核日誌（extra.audit=True）
    logger.add(
        log_dir / "audit.log",
        rotation="1 day",
        retention="2555 days",
        compression="zip",
        level="INFO",
        enqueue=True,
        filter=lambda r: r["extra"].get("audit") is True,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {extra[event]} | {message}",
    )

    # 5) 錯誤獨立檔
    logger.add(
        log_dir / "error.log",
        rotation="10 MB",
        retention="90 days",
        level="ERROR",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    _configured = True
    logger.bind(event="LOG_INIT").info(f"logging initialized (dir={log_dir}, level={level})")


def _inject_default_extra(record: Any) -> bool:
    """確保 extra['event'] 一定存在，避免 format 失敗。"""
    record["extra"].setdefault("event", "-")
    return True


def register_qt_sink(emit: Callable[[str], None], level: str = "INFO") -> int:
    """將 log 透過 callback 推送到 Qt GUI。

    Parameters
    ----------
    emit: 單參數 callback，收到已格式化的日誌字串。
    level: 過濾等級。

    Returns
    -------
    sink handler id，可用 `logger.remove(id)` 解除。
    """
    return logger.add(
        lambda msg: emit(msg),
        level=level,
        format="{time:HH:mm:ss} | {level:<7} | {extra[event]} | {message}",
        filter=_inject_default_extra,
        enqueue=False,
    )


__all__ = ["setup_logging", "register_qt_sink", "logger"]
