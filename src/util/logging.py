"""Structured logging — JSON to stdout, stdlib only.

The prototype emits one JSON object per log line so downstream tooling
(in production, a log shipper; in the prototype, ``jq``) can pivot on
fields without parsing a human-formatted string. The shape is
deliberately small: ``timestamp``, ``level``, ``service``, plus any
extra fields the caller passes through ``log_with_context``.

Why stdlib instead of structlog or loguru: the prototype's job is to
show the shape of operational observability, not to bring in a
dependency. The scale-to-production doc treats production logging
properly (correlation IDs, sampled debug, sink fan-out). Here, the
formatter is forty lines and the audience can read it on sight.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

_CONFIGURED = False
_STD_LOG_RECORD_FIELDS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message",
    }
)


class JsonFormatter(logging.Formatter):
    """Emit each LogRecord as a one-line JSON object.

    ``extra=...`` passed to a logging call lands as attributes on the
    record; we copy any attribute that is not a standard LogRecord field
    into the JSON body. That is the bridge between stdlib logging and
    the structured-context pattern ``log_with_context`` uses.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", record.name),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STD_LOG_RECORD_FIELDS or key == "service":
                continue
            payload[key] = _coerce(value)
        return json.dumps(payload, default=str)


def _coerce(value: Any) -> Any:
    """Convert non-JSON-serializable values to a JSON-safe shape.

    Pydantic models get ``model_dump``; enums get their value; everything
    else falls through to ``json.dumps`` which uses ``default=str``.
    """
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:  # noqa: BLE001 — best-effort coercion
            return str(value)
    if hasattr(value, "value") and hasattr(value, "name"):
        return value.value
    return value


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger.

    Idempotent — calling twice does not double-attach handlers. Called
    once at app startup (lifespan handler in [src/main.py]) and once
    optionally by tests that want to read the structured output.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    root.setLevel(level)
    # Remove any pre-existing handlers so re-runs (uvicorn reload) do not
    # stack formatters on top of each other.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(service_name: str) -> logging.Logger:
    """Return a logger that tags every record with ``service=<name>``.

    The service name lands as a stable column in the JSON output and is
    how an operator filters "show me everything triage did on this bill"
    without grepping module paths.
    """
    logger = logging.getLogger(service_name)
    # Set a LoggerAdapter-equivalent default via a filter that injects
    # the service tag onto every record.
    if not any(isinstance(f, _ServiceTagFilter) for f in logger.filters):
        logger.addFilter(_ServiceTagFilter(service_name))
    return logger


class _ServiceTagFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        # Set service if the call site didn't override it via extra=.
        if not hasattr(record, "service"):
            record.service = self._service_name
        return True


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    **context: Any,
) -> None:
    """Emit a log record with ``context`` as structured fields.

    Keeps callers from string-formatting structured data into the
    message body. ``message`` is the human-readable summary (e.g.,
    "started", "completed"); ``context`` is what downstream tooling
    actually pivots on (stage, bill_ref, outcome, duration_ms, ...).
    """
    logger.log(level, message, extra=context)


class StageTimer:
    """Context manager that emits stage-entry / stage-exit logs.

    Usage::

        with StageTimer(logger, stage="normalize", bill_ref=ref) as t:
            ...
            t.set(outcome="ok", flag_count=3)

    Any keys set via ``.set(...)`` are merged into the exit log's
    context. If an exception escapes the block, the exit log is
    promoted to ERROR with ``outcome="error"`` and the exception type;
    the exception then propagates.
    """

    def __init__(self, logger: logging.Logger, *, stage: str, **context: Any) -> None:
        self._logger = logger
        self._stage = stage
        self._context = {"stage": stage, **context}
        self._exit_context: dict[str, Any] = {}
        self._start = 0.0

    def set(self, **fields: Any) -> None:
        self._exit_context.update(fields)

    def __enter__(self) -> "StageTimer":
        self._start = time.perf_counter()
        log_with_context(self._logger, logging.INFO, "started", **self._context)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration_ms = round((time.perf_counter() - self._start) * 1000, 2)
        merged = {**self._context, **self._exit_context, "duration_ms": duration_ms}
        if exc_type is not None:
            merged["outcome"] = "error"
            merged["error_type"] = exc_type.__name__
            log_with_context(self._logger, logging.ERROR, "completed", **merged)
        else:
            merged.setdefault("outcome", "ok")
            log_with_context(self._logger, logging.INFO, "completed", **merged)
