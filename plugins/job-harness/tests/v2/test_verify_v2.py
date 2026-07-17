from __future__ import annotations

import runpy
import unittest
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[4]
_VERIFY = runpy.run_path(str(_REPO_ROOT / "scripts" / "verify_v2.py"))


def _call(name: str, *args: Any, **kwargs: Any) -> Any:
    value = _VERIFY[name]
    if not callable(value):
        raise AssertionError(f"{name} is not callable")
    return value(*args, **kwargs)


class VerifyV2Test(unittest.TestCase):
    def test_partial_source_is_not_accepted_as_healthy(self) -> None:
        source_plans = [{"source_id": "hh_ru", "status": "partial"}]

        self.assertFalse(
            _call(
                "_validate_healthy_live_source_plan",
                source_plans,
                source_id="hh_ru",
            )
        )
