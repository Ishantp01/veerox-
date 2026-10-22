from __future__ import annotations

import logging
import sys
from pathlib import Path
from traceback import walk_tb
from typing import Any

import structlog

from apps.api.config import settings
from apps.api.core.privacy import privacy_processor


def _safe_exception(exc_info: Any) -> str:
    """Keep error class and stack locations, never SDK payloads or locals."""
    error_type, _value, tb = exc_info
    frames = [
        f"{Path(frame.f_code.co_filename).name}:{lineno} in {frame.f_code.co_name}"
        for frame, lineno in walk_tb(tb)
    ]
    return f"{error_type.__name__}: " + " -> ".join(frames[-20:])


def setup_logging() -> None:
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        privacy_processor,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(exception_formatter=_safe_exception),
    ]

    try:
        import orjson

        json_renderer = structlog.processors.JSONRenderer(
            serializer=lambda *args, **kwargs: orjson.dumps(*args, **kwargs).decode()
        )
    except ImportError:
        json_renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            privacy_processor,  # includes formatted exception text and foreign log messages
            json_renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)

    # Uvicorn's own handlers otherwise bypass our formatter (including WS URLs).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_log = logging.getLogger(name)
        server_log.handlers = []
        server_log.propagate = True
    if settings.environment.strip().lower() == "pilot":
        logging.getLogger("uvicorn.access").disabled = True
