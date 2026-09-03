"""Tests for batch_report.py's pure formatting helper."""
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)
import batch_report as br


class TestFmtBytes:
    def test_bytes(self):
        assert br.fmt_bytes(512) == '512 B'

    def test_kib(self):
        assert br.fmt_bytes(1024) == '1.0 KiB'

    def test_mib(self):
        assert br.fmt_bytes(1024 ** 2) == '1.0 MiB'

    def test_gib(self):
        assert br.fmt_bytes(1024 ** 3) == '1.0 GiB'

    def test_tib(self):
        assert br.fmt_bytes(1024 ** 4) == '1.0 TiB'

    def test_pib_fallback(self):
        # Regression test: fmt_bytes used to fall off the end of its loop
        # and implicitly return None for anything >= 1024 TiB.
        assert br.fmt_bytes(1024 ** 5) == '1.0 PiB'
