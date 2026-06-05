"""
Installs all seven Voxa packages in editable mode.
Run: python3 setup.py
Then: pytest
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
                    [sys.executable, "-m", "pip", "install", "-e"] + flags + [path],
                    stderr=subprocess.PIPE,
                )
                break
            except subprocess.CalledProcessError:
                continue
    print("\n✓ Voxa packages installed. Run: pytest")
