import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger
from pythonjsonlogger.json import JsonFormatter

from config import Settings

STRUCTURED_LOG_FIELDS = (
    "user_id",
    "action",
    "file_id",
    "disk_id",
    "result",
    "error_code",
)

_json_formatter = JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(message)s %(user_id)s %(action)s "
    "%(file_id)s %(disk_id)s %(result)s %(error_code)s",
    rename_fields={"levelname": "level", "asctime": "timestamp"},
    timestamp=True,
)


def _serialize_log_record(record: dict[str, Any]) -> str:
    extra = record["extra"]
    log_record = logging.LogRecord(
        name=record["name"],
        level=record["level"].no,
        pathname=record["file"].path,
        lineno=record["line"],
        msg=record["message"],
        args=(),
        exc_info=record["exception"],
    )
    for field in STRUCTURED_LOG_FIELDS:
        value = extra.get(field)
        setattr(log_record, field, value if value is not None else None)
    return _json_formatter.format(log_record)


def _loguru_sink(message: Any) -> None:
    sys.stderr.write(_serialize_log_record(message.record) + "\n")
    sys.stderr.flush()


def setup_logging(settings: Settings) -> None:
    logger.remove()
    logger.configure(
        extra={field: None for field in STRUCTURED_LOG_FIELDS},
    )
    logger.add(
        _loguru_sink,
        level=settings.logging.level,
        format="{message}",
        backtrace=False,
        diagnose=False,
    )
    if settings.logging.file_enabled:
        log_path = Path(settings.logging.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def file_json_sink(message: Any) -> None:
            with log_path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(_serialize_log_record(message.record) + "\n")

        logger.add(
            file_json_sink,
            level=settings.logging.level,
            format="{message}",
            backtrace=False,
            diagnose=False,
            enqueue=True,
        )
        logger.info("File logging enabled at {}", settings.logging.file_path)

    logger.info("Structured JSON logging initialized at level {}", settings.logging.level)
