"""
Root conftest.py
Two jobs:
1. Adds all package src/ paths to sys.path so pytest works without install
2. Installs missing runtime dependencies automatically on first run

This means the exact three commands work from a clean checkout:
    git clone https://github.com/jakinmade/voxa-rebuild.git
    cd voxa-rebuild
    pytest
"""
import subprocess
import sys
from pathlib import Path

_root = Path(__file__).parent

# ---------------------------------------------------------------------------
# 1. Add all package src/ paths to sys.path
# ---------------------------------------------------------------------------
_packages = [
    "packages/voxa-core/src",
    "packages/voxa-humanisation/src",
    "packages/voxa-profile/src",
    "packages/voxa-calibration/src",
    "packages/voxa-governance/src",
]
for _pkg in _packages:
    _path = str(_root / _pkg)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# ---------------------------------------------------------------------------
# 2. Install missing runtime dependencies
# ---------------------------------------------------------------------------
_RUNTIME_DEPS = [
    "pydantic",
    "structlog",
    "httpx",
    "fastapi",
    "pytest_asyncio",
]

def _missing(pkg: str) -> bool:
    import importlib
    try:
        importlib.import_module(pkg.replace("-", "_"))
        return False
    except ImportError:
        return True

_needs_install = any(_missing(p) for p in _RUNTIME_DEPS)

if _needs_install:
    print("\n[voxa] Installing runtime dependencies from requirements.txt...", flush=True)
    _req = str(_root / "requirements.txt")
    # Try standard pip first, fall back to --break-system-packages (Debian/Ubuntu)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", _req, "-q"],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", _req, "-q",
             "--break-system-packages"],
            check=True,
        )
    print("[voxa] Dependencies installed. Running tests...\n", flush=True)
