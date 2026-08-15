"""
logging_config.py — structured logging setup for VOICOVA.

JSON lines on stdout, nothing else. Railway captures stdout/stderr
natively and surfaces it in the deploy logs UI, so this needs no
external service, no API key, no new failure mode. structlog was
already a pinned dependency (pyproject.toml) before this file existed.

get_logger() is the only thing other modules should import from here.
Configuration runs once, guarded by _configured, so importing this
module from multiple places (app.py, a future API entry point) never
double-configures or duplicates output.

If a hosted logging service is ever wanted later (Better Stack, Datadog,
whatever), JSON lines is already the right shape to ship as-is — this
file is the one place that would change, nothing that calls
get_logger() would need to.
"""

import logging
import sys

import structlog

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "voicova") -> structlog.BoundLogger:
    """Import this, call once per module: log = get_logger(__name__)."""
    configure_logging()
    return structlog.get_logger(name)
