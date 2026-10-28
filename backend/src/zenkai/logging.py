import json
import logging
from datetime import datetime, timezone


def configure(service, directory):
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(service)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(directory / f"{service}.log")
        logger.addHandler(handler)
        logger.addHandler(logging.StreamHandler())
    return logger


def event(logger, level, message, **fields):
    logger.log(getattr(logging, level.upper()), json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(), "level": level,
        "service": logger.name, "message": message, **fields,
    }))
