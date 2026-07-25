"""Exact residual coverage for Docker resource-update operations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.actions.catalog import DOCKER_EXECUTABLE
from topos.actions.update_ops import (
    UpdatePlan,
    _default_current_memory_reader,
    build_update_argv,
    build_update_preview,
    render_update_preview,
    validate_cpus,
    validate_memory,
)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_cpu_value_must_be_finite_exactly(value: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_cpus(value, max_cpus=64)
    assert str(exc_info.value) == f"cpus must be a finite number: {value!r}"


def test_cpu_value_must_not_exceed_explicit_limit() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_cpus("2.5", max_cpus=2)
    assert str(exc_info.value) == "cpus value 2.5 exceeds host CPU count 2"


@pytest.mark.parametrize("value", [None, ""])
def test_memory_value_must_be_a_nonempty_string(value: object) -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_memory(value)  # type: ignore[arg-type]
    assert str(exc_info.value) == "memory must be a non-empty string"


def test_memory_value_must_not_exceed_explicit_limit() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_memory("2K", max_bytes=1024)
    assert str(exc_info.value) == "memory value 2048 exceeds maximum 1024"


def _patch_memory_read(
    monkeypatch,
    result: str | BaseException,
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def read_text(path: Path, *, encoding: str) -> str:
        calls.append((str(path), encoding))
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(Path, "read_text", read_text)
    return calls


def test_default_reader_resolves_name_once_and_reads_exact_cgroup_path(
    monkeypatch,
) -> None:
    from topos import config as config_module
    from topos.collect import collector as collector_module
    from topos.collect import dockerjoin

    configured = object()
    entity = object()
    calls: dict[str, list[object]] = {
        "load": [],
        "collector_init": [],
        "collect": [],
        "resolve": [],
    }

    def load(path: object) -> object:
        calls["load"].append(path)
        return configured

    class Collector:
        def __init__(self, *, config: object) -> None:
            calls["collector_init"].append(config)

        def collect_once(self) -> object:
            calls["collect"].append("once")
            return SimpleNamespace(
                entities={"candidate": SimpleNamespace(entity=entity)}
            )

    def resolve(target: str, entities: dict[str, object]) -> str:
        calls["resolve"].append((target, entities))
        return "system.slice/docker-worker.scope"

    monkeypatch.setattr(config_module, "load", load)
    monkeypatch.setattr(collector_module, "Collector", Collector)
    monkeypatch.setattr(dockerjoin, "resolve_container_key", resolve)
    read_calls = _patch_memory_read(monkeypatch, " 4096\n")

    assert _default_current_memory_reader("worker") == 4096
    assert calls == {
        "load": [None],
        "collector_init": [configured],
        "collect": ["once"],
        "resolve": [("worker", {"candidate": entity})],
    }
    assert read_calls == [
        (
            "/sys/fs/cgroup/system.slice/docker-worker.scope/memory.current",
            "utf-8",
        )
    ]


@pytest.mark.parametrize(
    "result",
    [
        OSError("cgroup unavailable"),
        "not-an-integer",
    ],
)
def test_default_reader_direct_key_failures_return_none_exactly(
    monkeypatch,
    result: str | BaseException,
) -> None:
    read_calls = _patch_memory_read(monkeypatch, result)

    assert _default_current_memory_reader("system.slice/worker.scope") is None
    assert read_calls == [
        (
            "/sys/fs/cgroup/system.slice/worker.scope/memory.current",
            "utf-8",
        )
    ]


def test_default_reader_ordinary_resolution_failure_returns_none(
    monkeypatch,
) -> None:
    from topos import config as config_module

    calls: list[object] = []

    def load(path: object) -> object:
        calls.append(path)
        raise RuntimeError("configuration unavailable")

    monkeypatch.setattr(config_module, "load", load)

    assert _default_current_memory_reader("worker") is None
    assert calls == [None]


def test_default_reader_does_not_swallow_keyboard_interrupt(monkeypatch) -> None:
    from topos import config as config_module

    calls: list[object] = []

    def load(path: object) -> object:
        calls.append(path)
        raise KeyboardInterrupt

    monkeypatch.setattr(config_module, "load", load)

    with pytest.raises(KeyboardInterrupt):
        _default_current_memory_reader("worker")
    assert calls == [None]


@pytest.mark.parametrize("target", [None, ""])
def test_update_argv_requires_nonempty_string_target(target: object) -> None:
    with pytest.raises(ValueError) as exc_info:
        build_update_argv(target, memory=1024)  # type: ignore[arg-type]
    assert str(exc_info.value) == "target must be a non-empty string"


def test_update_argv_requires_at_least_one_resource() -> None:
    with pytest.raises(ValueError) as exc_info:
        build_update_argv("worker")
    assert str(exc_info.value) == (
        "at least one of --memory or --cpus is required"
    )


@pytest.mark.parametrize("memory", ["1024", 0, True])
def test_update_argv_rejects_invalid_typed_memory(memory: object) -> None:
    with pytest.raises(ValueError) as exc_info:
        build_update_argv("worker", memory=memory)  # type: ignore[arg-type]
    assert str(exc_info.value) == "memory must be a positive integer"


@pytest.mark.parametrize("cpus", ["2", 0, True])
def test_update_argv_rejects_invalid_typed_cpus(cpus: object) -> None:
    with pytest.raises(ValueError) as exc_info:
        build_update_argv("worker", cpus=cpus)  # type: ignore[arg-type]
    assert str(exc_info.value) == "cpus must be a positive number"


def test_preview_unreadable_usage_has_exact_fail_closed_error() -> None:
    calls: list[str] = []

    def reader(target: str) -> int:
        calls.append(target)
        raise RuntimeError("read failed")

    with pytest.raises(ValueError) as exc_info:
        build_update_preview(
            "worker",
            memory="1024",
            current_memory_reader=reader,
        )
    assert str(exc_info.value) == (
        "current memory usage of 'worker' could not be established, so a "
        "limit of 1024 bytes cannot be shown to be safe; pass --below-current "
        "to apply it anyway (this may OOM the container)"
    )
    assert calls == ["worker"]


def test_preview_does_not_swallow_keyboard_interrupt() -> None:
    calls: list[str] = []

    def reader(target: str) -> int:
        calls.append(target)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        build_update_preview(
            "worker",
            memory="1024",
            current_memory_reader=reader,
        )
    assert calls == ["worker"]


def test_render_cpus_only_plan_is_complete() -> None:
    plan = UpdatePlan(
        kind="docker-update",
        target="worker",
        memory=None,
        cpus=2.0,
        argv=(DOCKER_EXECUTABLE, "update", "--cpus", "2.0", "worker"),
    )
    assert render_update_preview(plan) == "\n".join(
        [
            "Action: docker-update",
            "Target: worker",
            "CPUs: 2.0",
            "Below-current override: no",
            f"Command argv: {list(plan.argv)}",
            "Description: Update Docker container resource limits.",
            "Mode: preview only; no command was executed",
        ]
    )


def test_render_memory_plan_without_current_usage_is_complete() -> None:
    plan = UpdatePlan(
        kind="docker-update",
        target="worker",
        memory=1024,
        cpus=None,
        argv=(DOCKER_EXECUTABLE, "update", "--memory", "1024", "worker"),
        current_memory=None,
        below_current=True,
    )
    assert render_update_preview(plan) == "\n".join(
        [
            "Action: docker-update",
            "Target: worker",
            "Memory: 1024 bytes",
            "Below-current override: yes",
            f"Command argv: {list(plan.argv)}",
            "Description: Update Docker container resource limits.",
            "Mode: preview only; no command was executed",
        ]
    )
