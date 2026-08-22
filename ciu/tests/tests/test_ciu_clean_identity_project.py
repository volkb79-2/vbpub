"""CIU-46 / S6.4a — clean enumerates a config-less shipped stack's IDENTITY project.

A shipped stack deployed without ``deploy.project_name``/``environment_tag``
runs under the WORKSPACE-IDENTITY compose project (``REPO_NAME``-
``INSTANCE_ID``-``<stack>``, derived from THIS checkout's ciu.env). The
withdrawn basename "legacy" fallback ran under docker's directory-derived
name: identical for every checkout of the repo (cross-instance collisions)
and unenumerable by clean, whose S6.4a passes then skipped the stack while
printing ``clean complete``.

Oracles:
- A tagless shipped stack's clean removes its containers, ``*_default``
  network, and label-prefixed volumes under the identity project; the
  enumeration filters carry that name (the controlled wrong implementation —
  returning [] — never issues these filters).
- A missing or key-less ciu.env REFUSES the enumeration — a teardown that
  cannot be named never silently skips.
- Tagged selections keep today's exact behavior (S8.7 scoped names).
- Container enumeration failure is indeterminate → fails the clean (B3).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402

REPO_NAME = "dstdns"
INSTANCE_ID = "abc123"
IDENTITY_PREFIX = f"{REPO_NAME}-{INSTANCE_ID}"


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class FakeDocker:
    """Stateful docker stand-in covering the ps/network/volume passes."""

    def __init__(self, containers=None, networks=None, volumes=None):
        # {name: compose-project} for each
        self.containers = dict(containers or {})
        self.networks = dict(networks or {})  # {name: [endpoints]}
        self.volumes = dict(volumes or {})
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        op = args[0]

        if op == "ps":
            assert args[1] == "-a"
            named = sorted(self.containers)
            for i, a in enumerate(args):
                if a == "--filter" and args[i + 1].startswith(
                    "label=com.docker.compose.project="
                ):
                    project = args[i + 1].split("=", 2)[2]
                    named = [
                        n for n, p in self.containers.items() if p == project
                    ]
            return _proc(0, "\n".join(sorted(named)), "")

        if op == "network":
            if args[1] == "ls":
                named = sorted(self.networks)
                for i, a in enumerate(args):
                    if a == "--filter" and args[i + 1].startswith("name=^"):
                        want = args[i + 1][len("name=^"):-1]
                        named = [n for n in named if n == want]
                    elif a == "--filter" and args[i + 1].startswith(
                        "label=com.docker.compose.project="
                    ):
                        project = args[i + 1].split("=", 2)[2]
                        named = [n for n in named if n.endswith(f"{project}_default")]
                return _proc(0, "\n".join(named), "")
            sub, name = args[1], args[2]
            if sub == "inspect":
                if name not in self.networks:
                    return _proc(1, "", f"No such network: {name}")
                containers = {
                    ep["id"]: {"Name": ep["name"]}
                    for ep in self.networks[name]
                }
                return _proc(0, json.dumps([{"Name": name, "Containers": containers}]), "")
            if sub == "disconnect":
                return _proc(0, "", "")
            if sub == "rm":
                removed = self.networks.pop(name, None)
                return _proc(0 if removed is not None else 1, "", "")
            raise AssertionError(f"unhandled docker network argv: {args}")

        if op == "volume":
            if args[1] == "ls":
                named = sorted(self.volumes)
                if "--filter" in args:
                    label = args[args.index("--filter") + 1]
                    project = label.split("=", 2)[2]
                    named = sorted(v for v, p in self.volumes.items() if p == project)
                return _proc(0, "\n".join(named), "")
            if args[1] == "rm":
                for v in args[2:]:
                    self.volumes.pop(v, None)
                return _proc(0, "", "")
            raise AssertionError(f"unhandled docker volume argv: {args}")

        if op == "rm":  # docker rm -f c1 c2 ...
            for c in args[2:]:
                self.containers.pop(c, None)
            return _proc(0, "", "")

        raise AssertionError(f"unhandled docker argv: {args}")

    def label_filters(self, op, sub=None):
        """Every compose-project label filter issued for a top-level op."""
        out = []
        for c in self.calls:
            if c[0] != op or (sub is not None and (len(c) < 2 or c[1] != sub)):
                continue
            for i, a in enumerate(c):
                if a == "--filter" and a != c[-1] and c[i + 1].startswith(
                    "label=com.docker.compose.project="
                ):
                    out.append(c[i + 1].split("=", 2)[2])
        return out


@pytest.fixture(autouse=True)
def _clean_ambient_identity(monkeypatch):
    for key in ("REPO_ROOT", "REPO_NAME", "INSTANCE_ID", "DOCKER_NETWORK_INTERNAL"):
        monkeypatch.delenv(key, raising=False)


def _identity_repo(tmp_path: Path, *, with_env: bool = True, with_keys: bool = True) -> Path:
    """A checkout whose global config carries NO deploy tags."""
    (tmp_path / "apps" / "vault").mkdir(parents=True)
    (tmp_path / "apps" / "ghost").mkdir(parents=True)
    if with_env:
        keys = (
            f'export REPO_NAME="{REPO_NAME}"\n' if with_keys else ""
        ) + f'export INSTANCE_ID="{INSTANCE_ID}"\n'
        (tmp_path / "ciu.env").write_text(keys, encoding="utf-8")
    return tmp_path


def _untagged_profile():
    profile = MagicMock()
    profile.config = {"deploy": {}}
    return profile


def _run_clean(monkeypatch, repo_root: Path, fake: FakeDocker, selection=None):
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    sel = selection or [{"path": "apps/vault"}]
    monkeypatch.setattr(
        deploy, "render_selected_stacks",
        lambda *a, **k: {entry["path"]: {} for entry in sel},
    )
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    return deploy.action_clean(repo_root, _untagged_profile(), sel, ignore_errors=True)


def test_identity_project_name_shape(tmp_path):
    repo = _identity_repo(tmp_path)
    assert engine.identity_compose_project_name(repo, repo / "apps" / "vault") == (
        f"{IDENTITY_PREFIX}-vault"
    )


def test_identity_project_name_refuses_non_round_tripping_basename(tmp_path):
    """Review fix: sibling stacks 'Vault' and 'vault' would normalize onto the
    SAME compose project within one workspace — the second up silently
    adopting the first one's containers. A directory name that does not
    round-trip normalization refuses instead of colliding."""
    repo = _identity_repo(tmp_path)
    weird = repo / "apps" / "Vendor Stack"
    weird.mkdir()
    with pytest.raises(ValueError, match="already normalized"):
        engine.identity_compose_project_name(repo, weird)


def test_identity_project_name_refuses_without_ciu_env(tmp_path):
    repo = _identity_repo(tmp_path, with_env=False)
    with pytest.raises(ValueError, match="no ciu.env"):
        engine.identity_compose_project_name(repo, repo / "apps" / "vault")


def test_identity_project_name_refuses_without_identity_keys(tmp_path):
    repo = _identity_repo(tmp_path, with_keys=False)
    with pytest.raises(ValueError, match="lacks REPO_NAME/INSTANCE_ID"):
        engine.identity_compose_project_name(repo, repo / "apps" / "vault")


def test_identity_project_name_refuses_invalid_result(tmp_path):
    """A stack basename that strips to nothing leaves a trailing-dash name
    starting fine but... a basename that is ONLY invalid characters would
    still start with the identity prefix — so force the pathological case
    where the identity itself is hostile."""
    repo = tmp_path
    (repo / "ciu.env").write_text(
        'export REPO_NAME="!!!"\nexport INSTANCE_ID="!!!"\n', encoding="utf-8"
    )
    (repo / "apps" / "vault").mkdir(parents=True)
    with pytest.raises(ValueError, match="normalizes to"):
        engine.identity_compose_project_name(repo, repo / "apps" / "vault")


def test_untagged_selection_enumerates_identity_projects(tmp_path):
    repo = _identity_repo(tmp_path)
    sel = [{"path": "apps/vault"}, {"path": "apps/ghost"}, {"path": "apps/missing"}]
    assert deploy._stack_compose_projects(repo, {"deploy": {}}, sel) == [
        f"{IDENTITY_PREFIX}-vault",
        f"{IDENTITY_PREFIX}-ghost",
    ]


def test_untagged_enumeration_refuses_without_identity_record(tmp_path):
    """No ciu.env → loud refusal (exit 2 via the CLI's ValueError mapping),
    never a silent empty enumeration over a printed clean complete."""
    repo = _identity_repo(tmp_path, with_env=False)
    with pytest.raises(ValueError, match="no ciu.env"):
        deploy._stack_compose_projects(repo, {"deploy": {}}, [{"path": "apps/vault"}])


def test_tagged_selection_keeps_scoped_names(tmp_path):
    """Tags present → today's exact S8.7 behavior, unchanged."""
    repo = _identity_repo(tmp_path)
    cfg = {"deploy": {"project_name": "proj", "environment_tag": "env"}}
    assert deploy._stack_compose_projects(repo, cfg, [{"path": "apps/vault"}]) == [
        "proj-env-vault"
    ]


def test_untagged_clean_removes_identity_stack_objects(monkeypatch, tmp_path, capsys):
    """Oracle: a tagless shipped stack's clean leaves NOTHING behind — and the
    passes provably enumerated the identity project (the controlled-wrong []
    return never issues these filters)."""
    repo = _identity_repo(tmp_path)
    project = f"{IDENTITY_PREFIX}-vault"
    fake = FakeDocker(
        containers={"vault-1": project, "vault-init": project},
        networks={f"{project}_default": []},
        volumes={"vault-data": project},
    )

    rc = _run_clean(monkeypatch, repo, fake)

    assert rc == 0
    assert fake.containers == {}, f"containers survived: {list(fake.containers)}"
    assert fake.networks == {}, f"networks survived: {list(fake.networks)}"
    assert fake.volumes == {}, f"volumes survived: {list(fake.volumes)}"
    # Every enumeration pass asked for the identity project by name — the
    # pre-CIU-46 [] return would have issued none of these.
    assert project in fake.label_filters("ps")
    assert project in fake.label_filters("volume", sub="ls")
    assert project in fake.label_filters("network", sub="ls")
    out = capsys.readouterr().out
    assert "clean complete" in out
    assert "post-clean invariant" not in out


def test_untagged_container_enumeration_failure_fails_clean(monkeypatch, tmp_path, capsys):
    """A daemon failure during identity container enumeration is
    indeterminate — it fails the clean instead of reading as 'nothing to
    remove' (review B3)."""
    repo = _identity_repo(tmp_path)
    project = f"{IDENTITY_PREFIX}-vault"

    class FailingPsDocker(FakeDocker):
        def __call__(self, args, **kw):
            if args[:1] == ["ps"]:
                self.calls.append(args)
                return _proc(1, "", "Cannot connect to the Docker daemon")
            return super().__call__(args, **kw)

    fake = FailingPsDocker(containers={"vault-1": project})

    rc = _run_clean(monkeypatch, repo, fake)

    assert rc == 1
    out = capsys.readouterr().out
    assert "container enumeration failed (S6.4a)" in out
    assert "clean completed with errors" in out


def test_run_shipped_refuses_when_ciu_env_cannot_name_project(tmp_path, monkeypatch):
    """S8.5/CIU-46 cutover: a checkout with NO ciu.env cannot name a
    config-less shipped deployment — refuse loudly, never start an
    unenumerable project."""
    stack = tmp_path / "vendor" / "vault"
    stack.mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n")

    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "net")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {"ciu": {}})
    monkeypatch.setattr(engine, "configure_logging", lambda *args: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **kwargs: path)
    monkeypatch.setattr(engine, "compose_project_name", lambda *args: (_ for _ in ()).throw(ValueError("no deploy")))

    with pytest.raises(ValueError, match="no ciu.env"):
        engine.run_shipped(stack, define_root=tmp_path)


def test_untagged_selection_dedupes_same_basename_stacks(tmp_path):
    """Two selected stacks with the SAME directory basename (different parents)
    share one legacy identity project name — deduped, once."""
    repo = _identity_repo(tmp_path)
    (repo / "apps" / "a" / "vault").mkdir(parents=True)
    (repo / "apps" / "b" / "vault").mkdir(parents=True)
    sel = [{"path": "apps/a/vault"}, {"path": "apps/b/vault"}]
    assert deploy._stack_compose_projects(repo, {"deploy": {}}, sel) == [
        f"{IDENTITY_PREFIX}-vault"
    ]
