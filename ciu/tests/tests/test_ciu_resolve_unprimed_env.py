"""CIU-139 — `ciu resolve` / `ciu exec` seed from the selected root's record.

An unprimed shell (no CONTAINER_UID/INSTANCE_ID/REPO_ROOT/... in the process
environment) must work with `--root-folder P`, exactly as `ciu up` and
`ciu env print` do; a root with no instance record refuses and names the verb
that creates it. Only the host-fact detectors are pinned; the record is
generated and read for real.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy, workspace_env  # noqa: E402

from test_ciu_workspace_env import _hermetic_generate  # noqa: E402

SEEDED = ("INSTANCE_ID", "REPO_ROOT", "PUBLIC_FQDN", "DOCKER_GID", "CONTAINER_UID")


@pytest.fixture
def unprimed(monkeypatch, tmp_path):
    """A generated root, an unrelated cwd and a process env without CIU facts."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    _hermetic_generate(monkeypatch, root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for key in list(os.environ):
        if key in workspace_env.REQUIRED_KEYS_CORE or key in SEEDED:
            monkeypatch.delitem(os.environ, key)
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: root)
    return root


def test_resolve_seeds_identity_from_root_in_unprimed_shell(
    unprimed, monkeypatch, capsys
):
    seen = {}

    def resolve(root, **_kw):
        seen.update({k: os.environ.get(k) for k in SEEDED})
        return {"schema_version": 1, "resolved": {"identities": {}}}

    monkeypatch.setattr(deploy, "resolve_identities", resolve)
    rc = cli._resolve_identities_cli(["--root-folder", str(unprimed), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1
    env = workspace_env.parse_workspace_env(unprimed / "ciu.env")
    assert seen["INSTANCE_ID"] == env["INSTANCE_ID"]
    assert seen["REPO_ROOT"] == str(unprimed)
    assert seen["PUBLIC_FQDN"] == "example.test"


def test_exec_seeds_identity_from_root_in_unprimed_shell(unprimed, monkeypatch):
    seen = {}

    def run(root, selector, argv, **_kw):
        seen.update({k: os.environ.get(k) for k in SEEDED})
        return 0

    monkeypatch.setattr(deploy, "exec_service", run)
    rc = cli._exec_service_cli(
        ["--root-folder", str(unprimed), "tools/api", "--", "true"]
    )
    assert rc == 0
    assert seen["REPO_ROOT"] == str(unprimed)
    assert seen["INSTANCE_ID"]


@pytest.mark.parametrize("verb", ["resolve", "exec"])
def test_missing_record_refuses_naming_the_remedy(unprimed, monkeypatch, capsys, verb):
    for name in ("ciu.env", "ciu.instance.generated.toml"):
        (unprimed / name).unlink(missing_ok=True)
    monkeypatch.setattr(
        deploy, "resolve_identities", lambda *_a, **_k: pytest.fail("must refuse first")
    )
    monkeypatch.setattr(
        deploy, "exec_service", lambda *_a, **_k: pytest.fail("must refuse first")
    )
    if verb == "resolve":
        rc = cli._resolve_identities_cli(["--root-folder", str(unprimed), "--json"])
    else:
        rc = cli._exec_service_cli(
            ["--root-folder", str(unprimed), "tools/api", "--", "true"]
        )
    assert rc == 2
    assert "ciu env generate" in capsys.readouterr().err
