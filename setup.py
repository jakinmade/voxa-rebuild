"""
Installs all Voxa packages in editable mode.

Usage:
    pip install -r requirements.txt     # Recommended — works in all environments
    python3 setup.py                    # Alternative
    make install                        # Via Makefile

After install: pytest
"""
import subprocess
import sys
from pathlib import Path

PACKAGES = [
    "packages/voxa-core",
    "packages/voxa-humanisation",
    "packages/voxa-profile",
    "packages/voxa-rendering",
    "packages/voxa-calibration",
    "packages/voxa-governance",
    "packages/voxa-api",
]

if __name__ == "__main__":
    root = Path(__file__).parent
    for pkg in PACKAGES:
        path = str(root / pkg)
        for flags in [[], ["--break-system-packages"]]:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install"] + flags + [path],
                    stderr=subprocess.PIPE,
                )
                break
            except subprocess.CalledProcessError:
                continue
    print("\n✓ Voxa packages installed. Run: pytest")
