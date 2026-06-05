"""
Root conftest.py — adds all Voxa packages to sys.path automatically.

This means `pytest` works immediately after cloning, with no install step.
The packages are loaded directly from the source tree.

For production use, install properly:
    pip install -r requirements.txt
"""
import sys
from pathlib import Path

_root = Path(__file__).parent
_packages = [
    "packages/voxa-core/src",
    "packages/voxa-humanisation/src",
    "packages/voxa-profile/src",
    "packages/voxa-rendering/src",
    "packages/voxa-calibration/src",
    "packages/voxa-governance/src",
    "packages/voxa-api/src",
]

for _pkg in _packages:
    _path = str(_root / _pkg)
    if _path not in sys.path:
        sys.path.insert(0, _path)
