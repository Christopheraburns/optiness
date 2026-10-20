"""Consistent logging for every service. Workbench captures stdout/stderr of
an application, so plain line-oriented logs are the right format in both
targets."""
from __future__ import annotations

import logging
import os
import sys


def setup_logging(name: str) -> logging.Logger:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    return logging.getLogger(name)
