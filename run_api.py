"""
run_api.py — container entrypoint for the Chrome-First API service.

Deliberately NOT a shell one-liner in Dockerfile.api's CMD (e.g.
`sh -c "uvicorn ... --port $PORT"`). Three consecutive real Railway
deployments (6 Sept 2026) all failed with uvicorn receiving the
literal four-character string "$PORT" as its --port value — meaning
whatever actually invoked the container's CMD did not go through a
shell that would expand it, despite the CMD being written as an
explicit `sh -c "..."` array element. The exact mechanism was never
conclusively identified (no local Docker available to reproduce it
directly), so rather than keep guessing at shell-invocation
specifics, this sidesteps the entire question: os.environ works
identically no matter how the Python process itself was started,
shell or no shell, so there is nothing here left to get wrong the
same way.
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port)
