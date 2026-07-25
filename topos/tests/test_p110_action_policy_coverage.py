"""Exact residual coverage for the action catalog and memory governance."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from topos.actions import catalog, governance
from topos.actions.catalog import (
    ACTION_CATALOG,
    SYSTEMCTL_EXECUTABLE,
    ActionKind,
    validate_target,
)
from topos.actions.governance import (
    SetPropertyPlan,
    _systemctl_show_reader,
    build_set_property_argv,
    build_set_property_preview,
    validate_memory_high_value,
)


def test_catalog_set_property_rejects_an_empty_target_exactly() -> None:
    builder = ACTION_CATALOG[ActionKind.SYSTEMD_SET_PROPERTY].builder
    with pytest.raises(ValueError) as exc_info:
        builder("")
    assert str(exc_info.value) == (
        "systemd-set-property target must be a bare unit name"
    )


@pytest.mark.parametrize("target", ["demo.slice=memory.high", "demo slice"])
def test_catalog_set_property_rejects_composite_targets_exactly(
    target: str,
) -> None:
    builder = ACTION_CATALOG[ActionKind.SYSTEMD_SET_PROPERTY].builder
    with pytest.raises(ValueError) as exc_info:
        builder(target)
    assert str(exc_info.value) == (
        "systemd-set-property composite target format is removed; "
        "use --property and --value instead"
    )


def test_validate_target_defensively_rejects_an_unhandled_allowlisted_kind(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        catalog,
        "EXECUTION_ALLOWLIST",
        frozenset({ActionKind.DOCKER_KILL}),
    )
    with pytest.raises(ValueError) as exc_info:
        validate_target(ActionKind.DOCKER_KILL, "worker")
    assert str(exc_info.value) == "execution not allowed for kind 'docker-kill'"


@pytest.mark.parametrize(
    ("kind", "target", "message"),
    [
        (
            ActionKind.SYSTEMD_SET_PROPERTY,
            "bad unit.service",
            "systemd set-property target must not contain whitespace: "
            "'bad unit.service'",
        ),
        (
            ActionKind.SYSTEMD_SET_PROPERTY,
            "unit.txt",
            "invalid systemd unit name for set-property: 'unit.txt'",
        ),
        (
            ActionKind.DOCKER_KILL,
            "bad name",
            "target must not contain whitespace: 'bad name'",
        ),
        (
            ActionKind.DOCKER_UPDATE,
            "bad?",
            "invalid Docker container identifier: 'bad?'",
        ),
        (
            ActionKind.SYSTEMD_KILL,
            "bad unit.service",
            "target must not contain whitespace: 'bad unit.service'",
        ),
        (
            ActionKind.SYSTEMD_KILL,
            "bad..service",
            "invalid systemd unit name: 'bad..service'",
        ),
    ],
)
def test_specialized_target_validation_errors_are_exact(
    kind: ActionKind,
    target: str,
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_target(kind, target)
    assert str(exc_info.value) == message


def test_digit_limit_conversion_failure_has_exact_error_and_cause() -> None:
    previous_limit = sys.get_int_max_str_digits()
    value = "9" * 641
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(ValueError) as exc_info:
            validate_memory_high_value(value)
    finally:
        sys.set_int_max_str_digits(previous_limit)

    assert str(exc_info.value) == (
        f"memory.high value is not a valid integer: {value!r}"
    )
    assert type(exc_info.value.__cause__) is ValueError


def _expected_show_call(unit: str) -> tuple[list[str], dict[str, object]]:
    return (
        [
            SYSTEMCTL_EXECUTABLE,
            "show",
            "--property",
            "MemoryHigh",
            "--value",
            unit,
        ],
        {
            "capture_output": True,
            "text": True,
            "timeout": 10.0,
            "shell": False,
        },
    )


@pytest.mark.parametrize(
    "error",
    [
        OSError("systemctl unavailable"),
        subprocess.TimeoutExpired(cmd=["systemctl"], timeout=10.0),
    ],
)
def test_systemctl_reader_transport_failures_return_none_exactly(
    monkeypatch,
    error: BaseException,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append((argv, kwargs))
        raise error

    monkeypatch.setattr(subprocess, "run", run)

    assert _systemctl_show_reader("demo.slice") is None
    assert calls == [_expected_show_call("demo.slice")]


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (7, "ignored\n", None),
        (0, "", None),
        (0, "(null)\n", None),
        (0, "infinity\n", None),
        (0, " 1048576 \n", "1048576"),
    ],
)
def test_systemctl_reader_result_shapes_are_exact(
    monkeypatch,
    returncode: int,
    stdout: str,
    expected: str | None,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **kwargs: object) -> object:
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", run)

    assert _systemctl_show_reader("demo.slice") == expected
    assert calls == [_expected_show_call("demo.slice")]


def test_set_property_argv_rejects_empty_unit_exactly() -> None:
    with pytest.raises(ValueError) as exc_info:
        build_set_property_argv("", "memory.high", "1024")
    assert str(exc_info.value) == "unit must be a non-empty string"


def test_explicit_preview_persistence_produces_complete_plan() -> None:
    calls: list[str] = []

    def reader(unit: str) -> str:
        calls.append(unit)
        return "512"

    assert build_set_property_preview(
        "demo.slice",
        "memory.high",
        "1024",
        persistence="PERSISTENT",
        current_value_reader=reader,
    ) == SetPropertyPlan(
        kind="systemd-set-property",
        unit="demo.slice",
        property_name="memory.high",
        property_value="1024",
        argv=(
            SYSTEMCTL_EXECUTABLE,
            "set-property",
            "demo.slice",
            "memory.high=1024",
        ),
        current_value="512",
        persistence="persistent",
    )
    assert calls == ["demo.slice"]


def test_preview_ordinary_reader_failure_produces_complete_fallback_plan() -> None:
    calls: list[str] = []

    def reader(unit: str) -> str:
        calls.append(unit)
        raise RuntimeError("reader failed")

    assert build_set_property_preview(
        "demo.scope",
        "memory.high",
        "max",
        current_value_reader=reader,
    ) == SetPropertyPlan(
        kind="systemd-set-property",
        unit="demo.scope",
        property_name="memory.high",
        property_value="max",
        argv=(
            SYSTEMCTL_EXECUTABLE,
            "set-property",
            "--runtime",
            "demo.scope",
            "memory.high=max",
        ),
        current_value=None,
        persistence="runtime",
    )
    assert calls == ["demo.scope"]


def test_preview_does_not_swallow_keyboard_interrupt() -> None:
    calls: list[str] = []

    def reader(unit: str) -> str:
        calls.append(unit)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        build_set_property_preview(
            "demo.slice",
            "memory.high",
            "max",
            current_value_reader=reader,
        )
    assert calls == ["demo.slice"]
