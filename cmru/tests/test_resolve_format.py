"""Tests for format_result (resolve.py) — pure logic, no network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json
import unittest
from cmru.resolve import format_result

_RESULT = {
    "version": "0.2.0",
    "tag": "tls-edge-v0.2.0",
    "asset": "tls-edge-v0.2.0.tar.xz",
    "sha256": "abc123def456",
    "url": "https://github.com/example/repo/releases/download/tls-edge-v0.2.0/tls-edge-v0.2.0.tar.xz",
}


class TestFormatResultJson(unittest.TestCase):
    def test_valid_json(self):
        out = format_result(_RESULT, "json")
        parsed = json.loads(out)
        self.assertEqual(parsed["version"], "0.2.0")
        self.assertEqual(parsed["tag"], "tls-edge-v0.2.0")
        self.assertEqual(parsed["sha256"], "abc123def456")

    def test_all_fields_present(self):
        out = format_result(_RESULT, "json")
        parsed = json.loads(out)
        for key in ("version", "tag", "asset", "sha256", "url"):
            self.assertIn(key, parsed)


class TestFormatResultUrl(unittest.TestCase):
    def test_returns_url(self):
        out = format_result(_RESULT, "url")
        self.assertEqual(out, _RESULT["url"])

    def test_missing_url_returns_empty(self):
        out = format_result({}, "url")
        self.assertEqual(out, "")


class TestFormatResultEnv(unittest.TestCase):
    def _lines(self, result):
        return format_result(result, "env").splitlines()

    def test_tls_edge_prefix(self):
        lines = self._lines(_RESULT)
        self.assertIn("TLS_EDGE_VERSION=0.2.0", lines)
        self.assertIn("TLS_EDGE_TAG=tls-edge-v0.2.0", lines)
        self.assertIn(f"TLS_EDGE_URL={_RESULT['url']}", lines)
        self.assertIn("TLS_EDGE_SHA256=abc123def456", lines)

    def test_cmru_prefix(self):
        # Hyphens in project name → underscores; multi-hyphen name handled
        result = {**_RESULT, "tag": "cmru-v0.2.0", "version": "0.2.0"}
        lines = self._lines(result)
        self.assertIn("CMRU_VERSION=0.2.0", lines)
        self.assertIn("CMRU_TAG=cmru-v0.2.0", lines)

    def test_ciu_prefix(self):
        result = {**_RESULT, "tag": "ciu-v2.1.0", "version": "2.1.0"}
        lines = self._lines(result)
        self.assertIn("CIU_VERSION=2.1.0", lines)
        self.assertIn("CIU_TAG=ciu-v2.1.0", lines)

    def test_pwmcp_counter_prefix(self):
        result = {**_RESULT, "tag": "pwmcp-v1.61.0-r10", "version": "1.61.0-r10"}
        lines = self._lines(result)
        self.assertIn("PWMCP_VERSION=1.61.0-r10", lines)
        self.assertIn("PWMCP_TAG=pwmcp-v1.61.0-r10", lines)

    def test_sha256_omitted_when_none(self):
        result = {**_RESULT, "sha256": None}
        out = format_result(result, "env")
        self.assertNotIn("SHA256", out)

    def test_sha256_omitted_when_missing(self):
        result = {k: v for k, v in _RESULT.items() if k != "sha256"}
        out = format_result(result, "env")
        self.assertNotIn("SHA256", out)


if __name__ == "__main__":
    unittest.main()
