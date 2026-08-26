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

BUG FIXED (26 Aug 2026) — every log.info()/log.error() call in this
codebase (profile_restored, profile_save_failed, etc.) was silently
never reaching Railway's logs. Root cause: PYTHONUNBUFFERED is not
set anywhere (not in railway.json, not in the Procfile), so this
container's stdout is block-buffered. structlog.PrintLoggerFactory
writes via a bare print() with no flush, so every structured log line
sat in the buffer indefinitely. The only reason the ad-hoc
print(..., flush=True) DIAG lines elsewhere in this codebase were
ever visible is that they force a flush explicitly, one call at a
time. Routing through the standard `logging` module instead (as
below) fixes this at the root: logging.StreamHandler.emit() calls
self.flush() after every single record, unconditionally, regardless
of whether PYTHONUNBUFFERED is set. Belt-and-braces: also set
PYTHONUNBUFFERED=1 as a Railway service variable so this class of bug
can't recur even for code that logs via bare print() elsewhere.

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
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        # stdlib.LoggerFactory routes every log.info()/log.error() call
        # through Python's own `logging` module (configured above with
        # a StreamHandler on stdout). StreamHandler.emit() flushes
        # after every record unconditionally — that's the fix. The
        # previous PrintLoggerFactory used a bare, unflushed print(),
        # which is why these logs were silently disappearing into the
        # container's stdout buffer (see module docstring).
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Render the JSON on the way out, via the stdlib handler this time
    # (not structlog's own renderer), since JSONRenderer moved into
    # the formatter chain above.
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    _configured = True


def get_logger(name: str = "voicova") -> structlog.BoundLogger:
    """Import this, call once per module: log = get_logger(__name__)."""
    configure_logging()
    return structlog.get_logger(name)
