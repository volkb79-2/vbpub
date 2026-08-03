"""Tests for src/ciu/warn_policy.py — CIU v2 global warnings-as-errors policy.

Normative contract: docs/SPEC.md §S10.6.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import warn_policy  # noqa: E402


class TestWarningsAsErrorsEnabled:
    def test_default_unset_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, raising=False)
        assert warn_policy.warnings_as_errors_enabled() is True

    def test_explicit_1_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, "1")
        assert warn_policy.warnings_as_errors_enabled() is True

    def test_explicit_0_is_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, "0")
        assert warn_policy.warnings_as_errors_enabled() is False

    def test_any_other_value_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-safe default: only the literal "0" opts out; a typo like
        "flase" or "no" still means "yes, fail on warnings"."""
        monkeypatch.setenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, "no")
        assert warn_policy.warnings_as_errors_enabled() is True

    def test_reads_fresh_every_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, raising=False)
        assert warn_policy.warnings_as_errors_enabled() is True
        monkeypatch.setenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, "0")
        assert warn_policy.warnings_as_errors_enabled() is False


class TestWarnOrRaise:
    def test_default_prints_and_raises(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.delenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="a test message"):
            warn_policy.warn_or_raise("a test message")
        out = capsys.readouterr().out
        assert "[WARN] a test message" in out

    def test_opted_out_prints_but_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, "0")
        warn_policy.warn_or_raise("a test message")  # must not raise
        out = capsys.readouterr().out
        assert "[WARN] a test message" in out

    def test_raised_exception_text_matches_printed_message_exactly(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv(warn_policy.WARNINGS_AS_ERRORS_ENV_VAR, raising=False)
        message = "[S15.16] some formatted message with detail"
        with pytest.raises(ValueError) as exc_info:
            warn_policy.warn_or_raise(message)
        assert str(exc_info.value) == message
        assert f"[WARN] {message}" in capsys.readouterr().out
