"""结构化日志：JSON 输出 + request_id + 手机号脱敏。"""

import logging
import re
import sys
from contextvars import ContextVar
from uuid import uuid4

import structlog

_request_id: ContextVar[str] = ContextVar("request_id", default="")

_PHONE_RE = re.compile(r"(1[3-9]\d)(\d{4})(\d{4})")


def set_request_id(value: str = "") -> str:
    rid = value or uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


def mask_phone(value: str) -> str:
    return _PHONE_RE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", value)


def _add_request_id(_logger, _method, event_dict):
    rid = get_request_id()
    if rid:
        event_dict["request_id"] = rid
    return event_dict


def _mask_processor(_logger, _method, body):
    if isinstance(body, str):
        return mask_phone(body)
    return body


def configure_logging(level: str = "INFO") -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_request_id,
            _mask_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app"):
    return structlog.get_logger(name)
