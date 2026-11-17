"""Structured JSON logging.

One JSON object per line with a fixed envelope and the correlation id that follows a
request from the receiver through SQS into the processor. The functions here accept
only keyword fields the caller names, so nothing is logged by accident: no request
bodies, no headers, no secrets.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

_SERVICE = os.environ.get("SERVICE_NAME", "webhook-ingestion")
_FUNCTION = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "local")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_THRESHOLD = _LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").upper(), 20)


def _emit(level: str, message: str, fields: dict[str, Any]) -> None:
    if _LEVELS[level] < _THRESHOLD:
        return
    record: dict[str, Any] = {
        "ts": round(time.time(), 3),
        "level": level,
        "service": _SERVICE,
        "function": _FUNCTION,
        "message": message,
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")


def debug(message: str, **fields: Any) -> None:
    _emit("DEBUG", message, fields)


def info(message: str, **fields: Any) -> None:
    _emit("INFO", message, fields)


def warning(message: str, **fields: Any) -> None:
    _emit("WARNING", message, fields)


def error(message: str, **fields: Any) -> None:
    _emit("ERROR", message, fields)


def exception(message: str, exc: BaseException, **fields: Any) -> None:
    # The exception type and message only. A traceback can carry local values, and
    # the processor's locals are vendor payloads.
    _emit("ERROR", message, {**fields, "error_type": type(exc).__name__, "error": str(exc)[:500]})
