from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))


@pytest.fixture
def package_root() -> Path:
    return PACKAGE_ROOT


@pytest.fixture
def agent_submission() -> dict:
    declared = os.environ.get("BSM_GREEKS_SUBMISSION")
    if not declared:
        raise RuntimeError("BSM_GREEKS_SUBMISSION must identify the agent file")
    return json.loads(Path(declared).read_text(encoding="utf-8"))
