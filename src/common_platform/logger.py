import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from common_platform.correlation import get_correlation_id

SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "token",
    "secret",
    "password",
    "body",
    "payload",
}


def _sanitize_data(data: dict[str, Any]) -> dict[str, Any]:
    """機密情報や巨大な本文データがログに混入するのを防ぐ。"""
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_KEYS:
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_data(v)
        else:
            sanitized[k] = v
    return sanitized


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # recordに保存されたcorrelation_idを優先し、なければ現在のコンテキストから取得
        cid = getattr(record, "correlation_id", None) or get_correlation_id()

        log_record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "correlation_id": cid,
        }

        extra_fields = getattr(record, "extra_fields", {})
        if isinstance(extra_fields, dict):
            log_record.update(_sanitize_data(extra_fields))

        if record.exc_info and record.exc_info[0] is not None:
            log_record["error_type"] = record.exc_info[0].__name__

        return json.dumps(log_record, ensure_ascii=False)


def get_logger(name: str = "common_platform", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level.upper())

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)

    return logger


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """構造化イベントログを出力するヘルパー関数。"""
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "correlation_id": get_correlation_id(),
            "extra_fields": fields,
        },
    )
