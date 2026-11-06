"""CIU-141 clean must not tear down another checkout's detached one-shot."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ciu import engine


def _run_document(container_id: str, project_root: Path, *, stack="tools/runner"):
    return {
        "Id": container_id,
        "Name": f"/one-shot-{container_id[:8]}",
        "Config": {"Labels": {
            "com.docker.compose.project": "demo-dev-runner",
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.project.working_dir": str(project_root),
            "ciu.run": "1",
            "ciu.run.stack": stack,
        }},
    }


def test_clean_refuses_foreign_one_shot_before_compose_down(tmp_path, monkeypatch):
    root = tmp_path / "current"
    foreign_root = tmp_path / "foreign"
    stack = root / "tools" / "runner"
    stack.mkdir(parents=True)
    foreign_root.mkdir()
    container_id = "c" * 64
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(argv, 0, container_id + "\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([_run_document(container_id, foreign_root)]), "",
            )
        raise AssertionError(f"clean must stop before destructive Docker work: {argv!r}")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)

    with pytest.raises(RuntimeError, match="refusing clean.*one-shot"):
        engine.reset_service(
            {"deploy": {"project_name": "demo", "environment_tag": "dev",
                         "labels": {"prefix": "demo"}}},
            stack, assume_yes=True, repo_root=root,
        )

    assert not any("compose" in argv and "down" in argv for argv in calls)
    assert any(argv[:2] == ["docker", "inspect"] and argv[-1] == container_id for argv in calls)


def test_clean_accepts_one_shot_from_the_same_primary_checkout(tmp_path, monkeypatch):
    root = tmp_path / "current"
    stack = root / "tools" / "runner"
    stack.mkdir(parents=True)
    container_id = "d" * 64
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(argv, 0, container_id + "\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([_run_document(container_id, root)]), "",
            )
        if argv[:3] == ["docker", "compose", "-p"] and "down" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)

    engine.reset_service(
        {"deploy": {"project_name": "demo", "environment_tag": "dev",
                     "labels": {"prefix": "demo"}}},
        stack, assume_yes=True, repo_root=root,
    )

    assert any("compose" in argv and "down" in argv for argv in calls)


def test_reset_uses_stack_scoped_absolute_compose_paths(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    stack = root / "tools" / "runner"
    overlay_dir = stack / ".ciu"
    overlay_dir.mkdir(parents=True)
    (stack / "ciu.compose.yml").write_text("services: {}\n", encoding="utf-8")
    (overlay_dir / "ciu.compose.overlay.yml").write_text("services: {}\n", encoding="utf-8")
    (overlay_dir / "ciu.compose.ownership.yml").write_text("services: {}\n", encoding="utf-8")
    calls = []

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)

    engine.reset_service(
        {"deploy": {"project_name": "demo", "environment_tag": "dev",
                     "labels": {"prefix": "demo"}}},
        stack,
        assume_yes=True,
        repo_root=root,
    )

    down = next(argv for argv in calls if "compose" in argv and "down" in argv)
    file_values = [down[index + 1] for index, value in enumerate(down[:-1]) if value == "-f"]
    assert file_values == [
        str(stack / "ciu.compose.yml"),
        str(overlay_dir / "ciu.compose.overlay.yml"),
        str(overlay_dir / "ciu.compose.ownership.yml"),
    ]
