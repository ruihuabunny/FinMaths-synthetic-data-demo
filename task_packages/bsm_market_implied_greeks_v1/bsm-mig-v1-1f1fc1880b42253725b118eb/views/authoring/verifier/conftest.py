from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def agent_submission() -> dict:
    declared = os.environ.get("BSM_GREEKS_SUBMISSION")
    if not declared:
        raise RuntimeError("BSM_GREEKS_SUBMISSION must identify the agent file")
    return json.loads(Path(declared).read_text(encoding="utf-8"))
