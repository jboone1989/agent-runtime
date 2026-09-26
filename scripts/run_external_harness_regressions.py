#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

TESTS = [
    "tests/test_external_harness_experience.py",
    "tests/test_life_loop.py",
    "tests/test_v030_learning.py",
    "tests/test_autobiographical_memory.py",
    "tests/test_evolution_experience.py",
    "tests/test_evolution_evidence.py",
    "tests/test_evolution_runtime.py",
]

if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", *TESTS]))
