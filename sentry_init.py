"""
Guards sentry_sdk.init() against being called more than once per process.

app.py is a Streamlit script: Streamlit re-executes it top-to-bottom on
every user interaction, across concurrently-running session threads in
the same worker process. Calling sentry_sdk.init() unconditionally at
module scope means it fires on every single rerun, for every session,
concurrently. With auto-enabling integrations on, that repeated/racy
init can trip a Python import race in sentry_sdk's own optional
integration probing (observed: ImportError: cannot import name
'LangchainIntegration' from sentry_sdk.integrations.langchain — VOICOVA
does not use langchain; this was sentry_sdk auto-probing for it).

init_sentry() makes the real init idempotent (module-level state here
persists across Streamlit reruns, unlike app.py's own top-level state)
and disables auto-enabling integrations so this class of probe-related
crash can't recur with some other package either.
"""

import os
import threading

import sentry_sdk

_lock = threading.Lock()
_initialized = False


def init_sentry():
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        sentry_sdk.init(
            dsn=os.environ.get("SENTRY_DSN"),
            traces_sample_rate=0.1,
            # VOICOVA doesn't use langchain/openai/etc SDKs directly enough
            # to need their auto-instrumentation, and sentry_sdk's own
            # probing for them has a known import-race bug under
            # concurrent/repeated init (see module docstring). Safer off.
            auto_enabling_integrations=False,
        )
        _initialized = True
