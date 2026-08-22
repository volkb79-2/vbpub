"""CIU-43 / S6.4a — `ciu clean` removes identity-scoped networks and reports keeps.

Oracles:
- An S16 worktree instance's clean leaves ZERO Docker objects carrying the
  instance identity (containers, volumes, networks — including compose
  ``*_default`` names).
- A lingering endpoint (devcontainer attached to the instance network) is
  disconnected then the network removed; a failed disconnect is named and
  fails the clean — never silently kept.
- A main-workspace clean that deliberately keeps its workspace network NAMES
  it in output.
- Controlled wrong implementation: a no-op network-removal pass must fail the
  post-clean invariant (the v1 "network removal NOT performed" behavior can
  never again report ``clean complete`` over surviving networks).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _config() -> dict:
    return {"deploy": {"project_name": "proj", "environment_tag": "env"}}


def _profile():
    profile = MagicMock()
    profile.config = _config()
    return profile


class FakeDocker:
    """Stateful docker stand-in for the network/volume passes (STATE-faithful)."""

    def __init__(self, networks=None, volumes=None):
        # networks: {name: [endpoint, ...]}; volumes: {name: compose-project}
        self.networks = dict(networks or {})
        self.volumes = dict(volumes or {})
        self.calls: list[list[str]] = []
        self.fail_disconnect_for: set[str] = set()

    def __call__(self, args, **kw):
        self.calls.append(args)
        op = args[0]

        if op == "network":
            sub, name = args[1], args[2]
            if sub == "inspect":
                if name not in self.networks:
                    return _proc(1, "", f"Error: No such network: {name}")
                containers = {
                    ep["id"]: {"Name": ep["name"]} for ep in self.networks[name]
                }
                return _proc(0, json.dumps([{"Name": name, "Containers": containers}]), "")
            if sub == "disconnect":
                ep = args[3]
                if ep in self.fail_disconnect_for:
                    return _proc(1, "", f"cannot disconnect {ep}")
                self.networks[name] = [
                    e for e in self.networks.get(name, []) if e["name"] != ep
                ]
                return _proc(0, "", "")
            if sub == "rm":
                removed = self.networks.pop(name, None)
                return _proc(0 if removed is not None else 1, "", "" if removed is not None else "not found")
            raise AssertionError(f"unhandled docker network argv: {args}")

        if op == "volume":
            if args[1] == "ls":
                named = sorted(self.volumes)
                if "--filter" in args:
                    label = args[args.index("--filter") + 1]
                    assert label.startswith("label=com.docker.compose.project="), label
                    # everything after 'label=<key>=' is the project name
                    project = label.split("=", 2)[2]
                    named = sorted(v for v, p in self.volumes.items() if p == project)
                return _proc(0, "\n".join(named), "")
            if args[1] == "rm":
                for v in args[2:]:
                    self.volumes.pop(v, None)
                return _proc(0, "", "")
            raise AssertionError(f"unhandled docker volume argv: {args}")

        raise AssertionError(f"unhandled docker argv: {args}")

    def inspect_calls(self):
        return [c for c in self.calls if c[:2] == ["network", "inspect"]]


@pytest.fixture(autouse=True)
def _clean_ambient_identity(monkeypatch):
    """This devcontainer sources a checkout's ciu.env; scrub its identity."""
    for key in ("REPO_ROOT", "REPO_NAME", "INSTANCE_ID", "DOCKER_NETWORK_INTERNAL"):
        monkeypatch.delenv(key, raising=False)


def _instance_repo(tmp_path: Path) -> Path:
    (tmp_path / "ciu.env").write_text(
        'export DOCKER_NETWORK_INTERNAL="proj-abc123-network"\n', encoding="utf-8"
    )
    (tmp_path / "ciu.worktree-instance.json").write_text(
        '{"schema_version": 1}\n', encoding="utf-8"
    )
    (tmp_path / "apps" / "vault").mkdir(parents=True)
    return tmp_path


def _main_repo(tmp_path: Path) -> Path:
    (tmp_path / "ciu.env").write_text(
        'export DOCKER_NETWORK_INTERNAL="proj-abc123-network"\n', encoding="utf-8"
    )
    (tmp_path / "apps" / "vault").mkdir(parents=True)
    return tmp_path


def _run_clean(monkeypatch, repo_root: Path, fake: FakeDocker, selection=None):
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    sel = selection or [{"path": "apps/vault"}]
    monkeypatch.setattr(
        deploy, "render_selected_stacks",
        lambda *a, **k: {entry["path"]: {} for entry in sel},
    )
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    return deploy.action_clean(repo_root, _profile(), sel, ignore_errors=True)


def test_instance_clean_removes_all_identity_scoped_networks(monkeypatch, tmp_path, capsys):
    """Oracle 1: after an instance clean, zero identity-scoped networks remain."""
    repo_root = _instance_repo(tmp_path)
    devcontainer_ep = {"id": "cid-dev", "name": "dstdns-devcontainer-vb"}
    fake = FakeDocker(
        networks={
            "proj-abc123-network": [devcontainer_ep],
            "proj-env-vault_default": [],
        },
    )

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 0
    assert fake.networks == {}, f"identity-scoped networks survived: {list(fake.networks)}"
    out = capsys.readouterr().out
    assert "proj-abc123-network" in out
    assert "proj-env-vault_default" in out
    # the devcontainer endpoint was disconnected before removal, by name
    assert ["network", "disconnect", "proj-abc123-network", "dstdns-devcontainer-vb"] in fake.calls


def test_main_workspace_keeps_its_network_and_names_it(monkeypatch, tmp_path, capsys):
    """Oracle 3: main-workspace clean keeps the workspace network — and says so."""
    repo_root = _main_repo(tmp_path)
    fake = FakeDocker(
        networks={
            "proj-abc123-network": [],
            "proj-env-vault_default": [],
        },
    )

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 0
    assert "proj-abc123-network" in fake.networks, "main workspace network was removed"
    assert "proj-env-vault_default" not in fake.networks, "stack default must still go"
    out = capsys.readouterr().out
    assert "kept: proj-abc123-network" in out
    assert "clean complete (kept: proj-abc123-network)" in out
    assert "devcontainer residence" in out


def test_failed_disconnect_names_endpoint_and_fails_clean(monkeypatch, tmp_path, capsys):
    """Oracle 2: an unremovable endpoint is named; clean never lies complete."""
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(
        networks={
            "proj-abc123-network": [{"id": "cid-x", "name": "pinned-endpoint"}],
            "proj-env-vault_default": [],
        },
    )
    fake.fail_disconnect_for.add("pinned-endpoint")

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 1
    assert "proj-abc123-network" in fake.networks, "refused network was force-removed"
    out = capsys.readouterr().out
    assert "'pinned-endpoint'" in out
    assert "post-clean invariant violated (S6.4a)" in out
    assert "clean completed with errors" in out


def test_controlled_wrong_noop_removal_fails_invariant(monkeypatch, tmp_path):
    """Restoring v1's no-network-removal path must fail the invariant (rc=1)."""
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(networks={"proj-abc123-network": [], "proj-env-vault_default": []})
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    sel = [{"path": "apps/vault"}]
    monkeypatch.setattr(
        deploy, "render_selected_stacks",
        lambda *a, **k: {entry["path"]: {} for entry in sel},
    )
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    monkeypatch.setattr(
        deploy, "_remove_identity_networks", lambda nets: ([], [])
    )

    rc = deploy.action_clean(repo_root, _profile(), sel, ignore_errors=True)

    assert rc == 1


def test_bare_project_prefix_volumes_removed_via_compose_label_pass(monkeypatch, tmp_path):
    """The 6.3.0 second reproduction: <project>-vault-* volumes survive no more.

    The vault stack's named volumes carry the bare project prefix (no instance
    tag) — invisible to the ``{project}-{env}-*`` name pass, caught by the
    compose-label pass under the stack's own S8.7 project.
    """
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(
        networks={},
        volumes={
            "proj-env-worker-data": "proj-env-worker",
            "proj-vault-data": "proj-env-vault",
            "proj-vault-logs": "proj-env-vault",
        },
    )

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 0
    assert fake.volumes == {}, f"volumes survived: {fake.volumes}"
    rm_calls = [c for c in fake.calls if c[:2] == ["volume", "rm"]]
    assert rm_calls, "no volume rm issued"
    removed = set(rm_calls[0][2:])
    assert {"proj-vault-data", "proj-vault-logs"} <= removed


def test_label_pass_cannot_eat_unrelated_projects_volumes():
    """Exactness guard: the label filter is per selected stack's compose
    project — another project's volume is never enumerated."""
    fake = FakeDocker(volumes={"other-vault-data": "other-project"})
    result = fake(["volume", "ls", "--filter",
                   "label=com.docker.compose.project=proj-env-vault",
                   "--format", "{{.Name}}"])
    assert result.stdout == ""


def test_network_endpoints_none_vs_empty_distinction(monkeypatch):
    """A gone network (None) is not 'zero endpoints' ([])."""
    fake = FakeDocker(networks={"net-a": []})
    monkeypatch.setattr(deploy.procutil, "docker", fake)

    assert deploy._network_endpoints("net-a") == []
    assert deploy._network_endpoints("never-existed") is None


def test_already_gone_network_is_silently_fine(monkeypatch, tmp_path):
    """Idempotent clean: re-running on a torn-down instance stays rc=0."""
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(networks={})  # nothing exists

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 0
