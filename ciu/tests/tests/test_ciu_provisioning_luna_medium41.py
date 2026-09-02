"""Luna-medium41 provisioning probes interpret normal Docker results."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402


def test_probe_ref_pg_accepts_successful_docker_query(monkeypatch, tmp_path):
    from ciu import procutil

    seen = []

    def docker(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=" 1\n", stderr="")

    monkeypatch.setattr(procutil, "docker", docker)

    result = provisioning.probe_ref(
        "pg:db/app", {}, tmp_path,
        stacks={"postgres": {"provides": ["pg:db/app"]}},
    )

    assert result.satisfied is True
    assert seen[0][0][:3] == ["exec", "postgres", "psql"]


def test_probe_ref_minio_accepts_successful_docker_result(monkeypatch, tmp_path):
    from ciu import procutil

    seen = []

    def docker(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="user info\n", stderr="")

    monkeypatch.setattr(procutil, "docker", docker)

    result = provisioning.probe_ref(
        "minio:user/worker", {}, tmp_path,
        stacks={"minio": {"provides": ["minio:user/worker"]}},
    )

    assert result.satisfied is True
    assert seen[0][0] == [
        "exec", "minio", "mc", "admin", "user", "info", "local", "worker"
    ]


def test_probe_ref_stack_accepts_healthy_docker_inspect_result(monkeypatch, tmp_path):
    from ciu import procutil

    seen = []

    def docker(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Health": {"Status": "healthy"}}),
        )

    monkeypatch.setattr(procutil, "docker", docker)

    result = provisioning.probe_ref("stack:db:healthy", {}, tmp_path)

    assert result.satisfied is True
    assert seen[0][0] == ["inspect", "--format", "{{json .State}}", "db"]
