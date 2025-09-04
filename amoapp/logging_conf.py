# -*- coding: utf-8 -*-
import logging
from typing import Optional

from .constants import DEFAULT_LOG_LEVEL, LOG_FORMAT


def setup_logging(level_str: Optional[str] = None) -> None:
    level = (level_str or DEFAULT_LOG_LEVEL).upper()
    logging.basicConfig(level=level, format=LOG_FORMAT)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
