import datetime
import json
import logging
import sys

from .middleware import request_id_var, session_id_var, trace_id_var, user_id_var


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str = "unknown_service", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.datetime.fromtimestamp(
            record.created, datetime.timezone.utc
            )
        timestamp = dt.isoformat(
            timespec="milliseconds"
            ).replace("+00:00", "Z")

        log_record = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service_name": self.service_name
        }

        if (req_id := request_id_var.get()) is not None:
            log_record["request_id"] = req_id
        if (trace_id := trace_id_var.get()) is not None:
            log_record["trace_id"] = trace_id
        if (sess_id := session_id_var.get()) is not None:
            log_record["session_id"] = sess_id
        if (usr_id := user_id_var.get()) is not None:
            log_record["user_id"] = usr_id

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record, ensure_ascii=False)


def setup_logging(service_name: str, log_level: str = "INFO"):
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name=service_name))

    root_logger.addHandler(handler)

    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(log_level)
    uvicorn_logger.handlers.clear()
    uvicorn_logger.propagate = True

    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.setLevel(log_level)
    uvicorn_access_logger.handlers.clear()
    uvicorn_access_logger.propagate = True
