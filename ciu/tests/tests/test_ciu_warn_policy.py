"""Tests for src/ciu/warn_policy.py — CIU's ``ciu.exit_on`` severity policy.

Normative contract: docs/SPEC.md §S10.6. This exercises the closed-vocabulary
``ciu.exit_on`` model (``WARN``/``ERROR``/``NEVER``, default ``ERROR``) that
replaced the old boolean ``CIU_WARNINGS_AS_ERRORS`` env var — see
warn_policy.py's module docstring for the full resolution-order contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import warn_policy  # noqa: E402


# ---------------------------------------------------------------------------
# _resolve_exit_on — precedence: config > env > default
# ---------------------------------------------------------------------------


class TestResolveExitOn:
    def test_default_when_neither_config_nor_env_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(warn_policy.EXIT_ON_ENV_VAR, raising=False)
        assert warn_policy._resolve_exit_on(None) == warn_policy.DEFAULT_EXIT_ON == "ERROR"

    def test_env_wins_when_no_config_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "WARN")
        assert warn_policy._resolve_exit_on(None) == "WARN"

    def test_env_wins_when_config_has_no_ciu_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "NEVER")
        assert warn_policy._resolve_exit_on({"deploy": {}}) == "NEVER"

    def test_env_wins_when_ciu_table_has_no_exit_on_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "NEVER")
        assert warn_policy._resolve_exit_on({"ciu": {"other_key": 1}}) == "NEVER"

    def test_env_wins_when_ciu_value_is_not_a_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "NEVER")
        assert warn_policy._resolve_exit_on({"ciu": "not-a-table"}) == "NEVER"

    def test_config_wins_over_env_even_when_env_set_differently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "WARN")
        assert warn_policy._resolve_exit_on({"ciu": {"exit_on": "NEVER"}}) == "NEVER"

    def test_env_value_is_stripped_and_uppercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "  warn  ")
        assert warn_policy._resolve_exit_on(None) == "WARN"


# ---------------------------------------------------------------------------
# _validate_exit_on — rejects invalid values from either source
# ---------------------------------------------------------------------------


class TestValidateExitOn:
    def test_valid_values_pass_through_normalized(self) -> None:
        for value in warn_policy.EXIT_ON_VALUES:
            assert warn_policy._validate_exit_on(value.lower(), source="test") == value

    def test_rejects_invalid_config_value_naming_source_and_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(warn_policy.EXIT_ON_ENV_VAR, raising=False)
        with pytest.raises(ValueError) as exc_info:
            warn_policy._resolve_exit_on({"ciu": {"exit_on": "BOGUS"}})
        message = str(exc_info.value)
        assert "[S10.7]" in message
        assert "ciu.exit_on" in message
        for value in warn_policy.EXIT_ON_VALUES:
            assert value in message
        assert "BOGUS" in message

    def test_rejects_invalid_env_value_naming_source_and_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(warn_policy.EXIT_ON_ENV_VAR, "BOGUS")
        with pytest.raises(ValueError) as exc_info:
            warn_policy._resolve_exit_on(None)
        message = str(exc_info.value)
        assert "[S10.7]" in message
        assert f"${warn_policy.EXIT_ON_ENV_VAR}" in message
        for value in warn_policy.EXIT_ON_VALUES:
            assert value in message
        assert "BOGUS" in message


# ---------------------------------------------------------------------------
# should_exit_on — truth table: 3 thresholds x 2 severities
# ---------------------------------------------------------------------------


class TestShouldExitOn:
    @pytest.mark.parametrize(
        "threshold,severity,expected",
        [
            ("NEVER", "WARN", False),
            ("NEVER", "ERROR", False),
            ("WARN", "WARN", True),
            ("WARN", "ERROR", True),
            ("ERROR", "WARN", False),
            ("ERROR", "ERROR", True),
        ],
    )
    def test_truth_table(self, threshold: str, severity: str, expected: bool) -> None:
        config = {"ciu": {"exit_on": threshold}}
        assert warn_policy.should_exit_on(severity, config=config) is expected

    def test_default_threshold_is_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(warn_policy.EXIT_ON_ENV_VAR, raising=False)
        assert warn_policy.should_exit_on("WARN", config=None) is False
        assert warn_policy.should_exit_on("ERROR", config=None) is True


# ---------------------------------------------------------------------------
# warn_or_raise — prints "[<SEVERITY>] <message>", raises exactly when
# should_exit_on says so
# ---------------------------------------------------------------------------


class TestWarnOrRaise:
    def test_prints_and_does_not_raise_by_default_for_warn_severity(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv(warn_policy.EXIT_ON_ENV_VAR, raising=False)
        warn_policy.warn_or_raise("a test message")  # default exit_on=ERROR; WARN doesn't exit
        out = capsys.readouterr().out
        assert "[WARN] a test message" in out

    def test_prints_and_raises_when_exit_on_warn(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        with pytest.raises(ValueError, match="^a test message$"):
            warn_policy.warn_or_raise("a test message", config={"ciu": {"exit_on": "WARN"}})
        out = capsys.readouterr().out
        assert "[WARN] a test message" in out

    def test_prints_and_raises_for_error_severity_by_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv(warn_policy.EXIT_ON_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="^an error message$"):
            warn_policy.warn_or_raise("an error message", severity="ERROR")
        out = capsys.readouterr().out
        assert "[ERROR] an error message" in out

    def test_does_not_raise_when_exit_on_never(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        warn_policy.warn_or_raise(
            "a test message", severity="ERROR", config={"ciu": {"exit_on": "NEVER"}}
        )
        out = capsys.readouterr().out
        assert "[ERROR] a test message" in out

    def test_raised_exception_text_matches_message_exactly_no_tag(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        message = "[S15.16] some formatted message with detail"
        with pytest.raises(ValueError) as exc_info:
            warn_policy.warn_or_raise(message, config={"ciu": {"exit_on": "WARN"}})
        assert str(exc_info.value) == message
        assert f"[WARN] {message}" in capsys.readouterr().out
