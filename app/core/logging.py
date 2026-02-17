from __future__ import annotations
import logging
import json
import os
import sys
from datetime import datetime, timezone

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    # remove default handlers
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
