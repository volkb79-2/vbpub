from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


CLI_PATH = Path(__file__).resolve().parents[1] / "truenas-api.py"
SPEC = importlib.util.spec_from_file_location("truenas_api_cli", CLI_PATH)
assert SPEC is not None and SPEC.loader is not None
truenas_api_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(truenas_api_cli)


class JsonOutputTests(unittest.TestCase):
    def test_emit_converts_nested_api_datetimes_to_iso_strings(self) -> None:
        class Output:
            def __init__(self) -> None:
                self.value = None

            def primary_json(self, value) -> None:
                self.value = value

        output = Output()
        runtime = SimpleNamespace(json_mode=True, output=output)
        response = {
            "buildtime": datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc),
            "nested": [{"started_at": datetime(2026, 10, 15, 6, 1, tzinfo=timezone.utc)}],
        }

        truenas_api_cli._emit(response, runtime)

        self.assertEqual(
            output.value,
            {
                "buildtime": "2026-10-15T06:00:00+00:00",
                "nested": [{"started_at": "2026-10-15T06:01:00+00:00"}],
            },
        )
