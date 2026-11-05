"""CIU-191: `ciu worktree rm` / `ciu clean` must never remove another
instance's containers, volumes or networks.

Incident: a clean, never-started worktree was removed from the MAIN checkout
(shell had sourced main's env files) and six running ``naf-dev-*`` containers of
the main project vanished. The deploy tags (`naf` / `dev`) were NOT
per-instance, so the S7.8 name prefix ``naf-dev-`` and the compose project name
selected the main checkout's objects too.

Oracles (all against a fake docker; no real daemon is ever touched):
- A worktree instance's clean removes only objects whose ownership labels name
  that instance; the other instance's containers, volumes and networks are
  untouched, even with the other instance's env exported.
- ``clean --dry-run`` (plan) names what it would remove and what it leaves, and
  changes nothing.
- ``worktree rm`` refuses a shell that exports a THIRD instance's identity and
  hands its children a scrubbed environment.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, worktree  # noqa: E402
from tests.test_ciu_worktree_lease import (  # noqa: E402,F401
    fake_generate_env,
    tmp_repo,
)

MAIN_ID = "main01"
WT_ID = "wt0002"
WT_ROOT = "/ws/.worktrees/rg"
MAIN_ROOT = "/ws"


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class SharedDaemon:
    """A docker stand-in holding main's AND the worktree's objects.

    Both use the same deploy tags (``naf``/``dev``): that is the hazard.
    """

    def __init__(self):
        # name -> (instance label, repo-root label)
        self.containers = {
            "naf-dev-app": (MAIN_ID, MAIN_ROOT),
            "naf-dev-runner": (MAIN_ID, MAIN_ROOT),
            "naf-dev-legacy": ("", ""),
            "naf-dev-wt-own": (WT_ID, WT_ROOT),
            "naf-dev-moved": (WT_ID, MAIN_ROOT),
        }
        self.volumes = {
            "naf-dev-data": (MAIN_ID, MAIN_ROOT),
            "naf-dev-wt-data": (WT_ID, WT_ROOT),
        }
        # network -> endpoints (container names)
        self.networks = {
            "naf-dev_default": ["naf-dev-app"],
            "naf-wt-network": [],
        }
        self.network_projects = {"naf-dev_default": "naf-dev", "naf-wt-network": "naf-dev"}
        # network -> (instance label, repo-root label); compose's own project
        # label is deliberately NOT ownership.
        self.network_owners = {
            "naf-dev_default": (MAIN_ID, MAIN_ROOT),
            "naf-wt-network": (WT_ID, WT_ROOT),
        }
        self.calls: list[list[str]] = []
        self.fail_inspect: set[str] = set()

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        op = args[0]
        fmt = args[args.index("--format") + 1] if "--format" in args else ""
        if op == "ps":
            rows = []
            for name, (inst, root) in sorted(self.containers.items()):
                rows.append(f"{name}\t{inst}\t{root}" if "ciu.instance" in fmt else name)
            return _proc(0, "\n".join(rows))
        if op == "rm":
            for name in args[2:]:
                self.containers.pop(name, None)
            return _proc(0)
        if op == "inspect":
            name = args[-1]
            if name in self.fail_inspect or name not in self.containers:
                return _proc(1, "", "no such container")
            inst, root = self.containers[name]
            return _proc(0, f"{inst}\t{root}\n")
        if op == "volume":
            if args[1] == "ls":
                rows = []
                for name, (inst, root) in sorted(self.volumes.items()):
                    rows.append(f"{name}\t{inst}\t{root}" if "ciu.instance" in fmt else name)
                return _proc(0, "\n".join(rows))
            if args[1] == "rm":
                for name in args[2:]:
                    self.volumes.pop(name, None)
                return _proc(0)
        if op == "network":
            sub = args[1]
            if sub == "ls":
                names = sorted(self.networks)
                for i, a in enumerate(args):
                    if a == "--filter" and args[i + 1].startswith("name=^"):
                        want = args[i + 1][len("name=^"):-1]
                        names = [n for n in names if n == want]
                    elif a == "--filter" and args[i + 1].startswith("label=com.docker.compose.project="):
                        project = args[i + 1].split("=", 2)[2]
                        names = [n for n in names if self.network_projects.get(n) == project]
                return _proc(0, "\n".join(names))
            name = args[2]
            if sub == "inspect":
                if name not in self.networks:
                    return _proc(1, "", "no such network")
                containers = {f"id-{e}": {"Name": e} for e in self.networks[name]}
                labels = {"ciu.instance": inst, "ciu.repo-root": root} if (
                    (inst := self.network_owners.get(name, ("", ""))[0]) is not None
                    and (root := self.network_owners.get(name, ("", ""))[1]) is not None
                    and inst
                ) else {}
                return _proc(0, json.dumps([{"Name": name, "Containers": containers, "Labels": labels}]))
            if sub == "disconnect":
                self.networks[name] = [e for e in self.networks[name] if e != args[3]]
                return _proc(0)
            if sub == "rm":
                self.networks.pop(name, None)
                return _proc(0)
        raise AssertionError(f"unhandled docker argv: {args}")

    def mutations(self) -> list[list[str]]:
        return [
            c for c in self.calls
            if c[0] == "rm" or c[:2] in (["volume", "rm"], ["network", "rm"], ["network", "disconnect"])
        ]


@pytest.fixture(autouse=True)
def _main_checkouts_env_exported(monkeypatch):
    """The incident's precondition: the shell carries MAIN's identity."""
    monkeypatch.setenv("INSTANCE_ID", MAIN_ID)
    monkeypatch.setenv("REPO_ROOT", MAIN_ROOT)
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", MAIN_ROOT)
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "naf-main01-network")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "naf-dev")
    monkeypatch.setenv("ENVIRONMENT_TAG", "dev")


def _checkout(tmp_path: Path, *, instance: bool, instance_id: str, root: str) -> Path:
    from ciu.workspace_env import GENERATED_FACTS_KEYS, write_generated_facts

    payload = {key: "" for key in GENERATED_FACTS_KEYS}
    payload.update(
        instance_id=instance_id, network=f"naf-{instance_id}-network",
        physical_repo_root=root, repo_root=root,
    )
    write_generated_facts(tmp_path, payload)
    if instance:
        (tmp_path / "ciu.worktree-instance.json").write_text(
            '{"schema_version": 1}\n', encoding="utf-8"
        )
    (tmp_path / "apps" / "api").mkdir(parents=True)
    return tmp_path


def _profile():
    profile = MagicMock()
    profile.config = {"deploy": {"project_name": "naf", "environment_tag": "dev"}}
    return profile


def _run(monkeypatch, repo_root: Path, daemon: SharedDaemon, **kw) -> int:
    monkeypatch.setattr(deploy.procutil, "docker", daemon)
    monkeypatch.setattr(
        deploy, "render_selected_stacks", lambda *a, **k: {"apps/api": {}}
    )
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_stack_compose_projects", lambda *a, **k: ["naf-dev"])
    monkeypatch.setattr(deploy, "_known_stack_projects", lambda *a, **k: [])
    return deploy.action_clean(
        repo_root, _profile(), [{"path": "apps/api"}], ignore_errors=True, **kw
    )


def test_worktree_clean_with_other_instance_env_leaves_that_instance_untouched(
    monkeypatch, tmp_path, capsys
):
    """THE ACCEPTANCE TEST: another instance's env is exported, its containers,
    volumes and networks share the deploy tags, and none of them is touched."""
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()

    assert _run(monkeypatch, repo, daemon) == 0

    # Main's six-stack analogue survives, byte for byte.
    assert {"naf-dev-app", "naf-dev-runner", "naf-dev-legacy"} <= set(daemon.containers)
    assert "naf-dev-data" in daemon.volumes
    assert daemon.networks["naf-dev_default"] == ["naf-dev-app"], "endpoint disconnected"
    # The worktree's own objects are gone.
    assert "naf-dev-wt-own" not in daemon.containers
    assert "naf-dev-wt-data" not in daemon.volumes
    assert "naf-wt-network" not in daemon.networks
    removed_containers = {n for c in daemon.mutations() if c[0] == "rm" for n in c[2:]}
    assert removed_containers == {"naf-dev-wt-own"}
    out = capsys.readouterr().out
    assert "naf-dev-wt-own" in out


def test_a_label_on_another_checkout_is_not_ours_even_with_our_instance_id(
    monkeypatch, tmp_path
):
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()
    _run(monkeypatch, repo, daemon)
    assert "naf-dev-moved" in daemon.containers


def test_main_checkout_still_removes_its_own_and_its_legacy_unlabelled_objects(
    monkeypatch, tmp_path
):
    """The ownership filter protects foreign objects without stranding the main
    checkout's own (including pre-label) ones."""
    repo = _checkout(tmp_path, instance=False, instance_id=MAIN_ID, root=MAIN_ROOT)
    daemon = SharedDaemon()
    daemon.networks["naf-dev_default"] = []
    _run(monkeypatch, repo, daemon)
    assert "naf-dev-app" not in daemon.containers
    assert "naf-dev-legacy" not in daemon.containers
    assert "naf-dev-data" not in daemon.volumes
    # The worktree's objects are foreign to main.
    assert "naf-dev-wt-own" in daemon.containers
    assert "naf-dev-wt-data" in daemon.volumes


def test_instance_without_a_recorded_id_removes_nothing_by_prefix(monkeypatch, tmp_path):
    repo = _checkout(tmp_path, instance=True, instance_id="", root=WT_ROOT)
    daemon = SharedDaemon()
    _run(monkeypatch, repo, daemon)
    assert set(daemon.containers) == {
        "naf-dev-app", "naf-dev-runner", "naf-dev-legacy", "naf-dev-wt-own", "naf-dev-moved",
    }
    assert set(daemon.volumes) == {"naf-dev-data", "naf-dev-wt-data"}


def test_endpoint_that_cannot_be_inspected_keeps_the_network(monkeypatch, tmp_path):
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()
    daemon.networks["naf-wt-network"] = ["naf-dev-app"]
    daemon.fail_inspect = {"naf-dev-app"}
    _run(monkeypatch, repo, daemon)
    assert daemon.networks["naf-wt-network"] == ["naf-dev-app"]
    assert not [c for c in daemon.calls if c[:2] == ["network", "disconnect"]]


def test_network_endpoint_listing_failure_keeps_the_network(monkeypatch):
    def broken(_net):
        raise ValueError("unparsable")

    monkeypatch.setattr(deploy, "_network_endpoints", broken)
    monkeypatch.setattr(deploy, "_network_foreign_reason", lambda *_a: None)
    owner = deploy._CleanOwner(WT_ID, WT_ROOT, True)
    removable, kept = deploy._split_foreign_endpoint_networks(["n"], owner)
    assert removable == []
    assert kept[0][0] == "n" and "unparsable" in kept[0][1]


def test_without_an_owner_every_network_stays_removable():
    assert deploy._split_foreign_endpoint_networks(["a", "b"], None) == (["a", "b"], [])


def test_dry_run_prints_the_plan_and_changes_nothing(monkeypatch, tmp_path, capsys):
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()
    assert _run(monkeypatch, repo, daemon, plan_only=True) == 0
    out = capsys.readouterr().out
    assert "Clean plan for" in out
    assert f"instance {WT_ID}" in out
    assert "containers to remove (1): naf-dev-wt-own" in out
    assert "volumes to remove (1): naf-dev-wt-data" in out
    assert "naf-wt-network" in out
    assert "NOT touching naf-dev-app: owned by another instance (main01)" in out
    assert "NOT touching naf-dev-legacy: unlabelled" in out
    assert "NOT touching naf-dev-moved: owned by another checkout" in out
    assert daemon.mutations() == []
    assert set(daemon.containers) >= {"naf-dev-app", "naf-dev-wt-own"}


def test_plan_names_networks_kept_for_foreign_endpoints_and_unowned_identity(
    monkeypatch, tmp_path, capsys
):
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()
    daemon.network_owners["naf-dev_default"] = (WT_ID, WT_ROOT)  # ours, foreign endpoint
    _run(monkeypatch, repo, daemon, plan_only=True)
    out = capsys.readouterr().out
    assert "NOT touching network naf-dev_default: still has foreign endpoint(s): naf-dev-app" in out


def test_plan_without_an_owner_reports_that_and_reports_enumeration_failures(
    monkeypatch, tmp_path, capsys
):
    # no facts, not an instance: no ownership filter (legacy behaviour)
    (tmp_path / "apps" / "api").mkdir(parents=True)
    daemon = SharedDaemon()
    daemon.networks = {}
    assert _run(monkeypatch, tmp_path, daemon, plan_only=True) == 0
    assert "no ownership filter" in capsys.readouterr().out

    def failing(args, **kw):
        return _proc(1, "", "Cannot connect to the Docker daemon")

    monkeypatch.setattr(deploy.procutil, "docker", failing)
    repo = _checkout(tmp_path / "x", instance=True, instance_id=WT_ID, root=WT_ROOT) \
        if (tmp_path / "x").mkdir() is None else None
    rc = deploy.action_clean(
        repo, _profile(), [{"path": "apps/api"}], ignore_errors=True, plan_only=True,
    )
    assert rc == 1
    out = capsys.readouterr()
    assert "plan: container enumeration failed" in out.err + out.out
    assert "plan: volume enumeration failed" in out.err + out.out


def test_untagged_plan_uses_the_compose_label_container_listing(monkeypatch, tmp_path, capsys):
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    profile = MagicMock()
    profile.config = {"deploy": {}}
    daemon = SharedDaemon()
    monkeypatch.setattr(deploy.procutil, "docker", daemon)
    monkeypatch.setattr(deploy, "_stack_compose_projects", lambda *a, **k: ["naf-wt0002"])
    monkeypatch.setattr(deploy, "_stack_project_containers", lambda projects: ["naf-wt0002-api-1"])
    monkeypatch.setattr(deploy, "_known_stack_projects", lambda *a, **k: [])
    assert deploy.action_clean(
        repo, profile, [{"path": "apps/api"}], ignore_errors=True, plan_only=True
    ) == 0
    assert "containers to remove (1): naf-wt0002-api-1" in capsys.readouterr().out


def test_owner_listing_failure_fails_closed_for_the_real_clean(monkeypatch, tmp_path, capsys):
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()

    def failing_ps(args, **kw):
        if args[0] == "ps":
            return _proc(1, "", "Cannot connect to the Docker daemon")
        return daemon(args, **kw)

    monkeypatch.setattr(deploy.procutil, "docker", failing_ps)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {"apps/api": {}})
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_stack_compose_projects", lambda *a, **k: ["naf-dev"])
    rc = deploy.action_clean(repo, _profile(), [{"path": "apps/api"}], ignore_errors=True)
    assert rc == 1
    assert "container enumeration failed" in capsys.readouterr().out
    assert "naf-dev-app" in daemon.containers


def test_unowned_matching_containers_listing_without_a_skip_sink(monkeypatch):
    daemon = SharedDaemon()
    monkeypatch.setattr(deploy.procutil, "docker", daemon)
    owner = deploy._CleanOwner(WT_ID, WT_ROOT, True)
    cfg = {"deploy": {"project_name": "naf", "environment_tag": "dev"}}
    assert deploy._matching_containers(cfg, all_states=True, owner=owner) == ["naf-dev-wt-own"]
    assert deploy._list_project_volumes(cfg, owner=owner) == ["naf-dev-wt-data"]
    assert deploy._list_project_volumes({"deploy": {}}, owner=owner) == []
    assert deploy._list_stack_project_volumes(["naf-dev"], owner=owner) == ["naf-dev-wt-data"]


def test_owner_listing_ignores_blank_lines_and_other_prefixes(monkeypatch):
    def docker(args, **kw):
        return _proc(0, f"\n{'other-x'}\t{WT_ID}\t\nnaf-dev-a\t{WT_ID}\t<no value>\n")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    owner = deploy._CleanOwner(WT_ID, WT_ROOT, True)
    cfg = {"deploy": {"project_name": "naf", "environment_tag": "dev"}}
    assert deploy._matching_containers(cfg, owner=owner) == ["naf-dev-a"]


def test_owner_volume_listing_failure_raises(monkeypatch):
    monkeypatch.setattr(deploy.procutil, "docker", lambda *a, **k: _proc(1, "", "down"))
    owner = deploy._CleanOwner(WT_ID, WT_ROOT, True)
    cfg = {"deploy": {"project_name": "naf", "environment_tag": "dev"}}
    with pytest.raises(ValueError, match="docker volume ls failed: down"):
        deploy._list_project_volumes(cfg, owner=owner)


def test_clean_owner_is_none_without_any_identity(tmp_path):
    assert deploy._clean_owner(tmp_path) is None


def test_clean_owner_with_unreadable_facts_in_an_instance(tmp_path):
    (tmp_path / "ciu.worktree-instance.json").write_text("{}", encoding="utf-8")
    owner = deploy._clean_owner(tmp_path)
    assert owner == deploy._CleanOwner(None, None, True, False)  # never generated


# ---------------------------------------------------------------------------
# worktree layer
# ---------------------------------------------------------------------------


def test_clean_in_runs_the_plan_first_and_both_children_get_a_scrubbed_env(
    tmp_path, monkeypatch
):
    from ciu import workspace_env

    monkeypatch.setattr(workspace_env, "outdated_generated_identity", lambda _w: None)
    monkeypatch.setattr(
        workspace_env, "read_instance_identity_env",
        lambda _w: {"INSTANCE_ID": WT_ID, "REPO_ROOT": WT_ROOT},
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(worktree.subprocess, "run", run)
    assert worktree._clean_in(tmp_path, yes=True) == 0
    assert [c[0][-1] for c in calls] == ["--dry-run", "--yes"]
    for _argv, env in calls:
        assert env["INSTANCE_ID"] == WT_ID and env["REPO_ROOT"] == WT_ROOT
        for key in ("PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                    "COMPOSE_PROJECT_NAME", "ENVIRONMENT_TAG"):
            assert key not in env, f"{key} from the shell leaked into the child"


def test_clean_in_plan_only_runs_just_the_dry_run_child(tmp_path, monkeypatch):
    from ciu import workspace_env

    monkeypatch.setattr(workspace_env, "outdated_generated_identity", lambda _w: None)
    monkeypatch.setattr(
        workspace_env, "read_instance_identity_env", lambda _w: {"INSTANCE_ID": WT_ID},
    )
    calls = []
    monkeypatch.setattr(
        worktree.subprocess, "run",
        lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert worktree._clean_in(tmp_path, yes=False, plan_only=True) == 0
    assert [c[-2:] for c in calls] == [["clean", "--dry-run"]]


def test_clean_in_plan_only_skips_a_retired_identity(tmp_path, monkeypatch):
    from ciu import workspace_env

    monkeypatch.setattr(
        workspace_env, "outdated_generated_identity", lambda _w: {"instance_id": "old123"},
    )
    monkeypatch.setattr(
        worktree.subprocess, "run", lambda *a, **k: pytest.fail("no child for a plan"),
    )
    assert worktree._clean_in(tmp_path, yes=True, plan_only=True) == 0


def _fake_identity(monkeypatch, *, target, primary):
    from ciu import workspace_env

    def read(root):
        if str(root).endswith("PRIMARY"):
            if primary is None:
                raise workspace_env.WorkspaceEnvError("unreadable")
            return primary
        if target is None:
            raise workspace_env.WorkspaceEnvError("unreadable")
        return target

    monkeypatch.setattr(workspace_env, "read_instance_identity_env", read)
    monkeypatch.setattr(
        worktree, "_primary_ciu_root_for_record", lambda *_a: Path("/x/PRIMARY"),
    )


def _record():
    return type("R", (), {"logical_name": "rg"})()


def test_ambient_identity_of_the_primary_or_the_target_is_accepted(monkeypatch):
    _fake_identity(
        monkeypatch,
        target={"INSTANCE_ID": WT_ID, "REPO_ROOT": WT_ROOT},
        primary={"INSTANCE_ID": MAIN_ID, "REPO_ROOT": MAIN_ROOT,
                 "PHYSICAL_REPO_ROOT": MAIN_ROOT,
                 "DOCKER_NETWORK_INTERNAL": "naf-main01-network"},
    )
    assert worktree._foreign_ambient_identity(Path("/r"), _record(), Path("/t")) is None


def test_ambient_identity_of_a_third_instance_is_refused_with_the_remedy(monkeypatch):
    _fake_identity(
        monkeypatch,
        target={"INSTANCE_ID": WT_ID},
        primary={"INSTANCE_ID": MAIN_ID, "REPO_ROOT": MAIN_ROOT,
                 "PHYSICAL_REPO_ROOT": MAIN_ROOT,
                 "DOCKER_NETWORK_INTERNAL": "naf-main01-network"},
    )
    monkeypatch.setenv("INSTANCE_ID", "third9")
    message = worktree._foreign_ambient_identity(Path("/r"), _record(), Path("/t"))
    assert "INSTANCE_ID='third9'" in message
    assert f"INSTANCE_ID={WT_ID!r}" in message
    assert "-u INSTANCE_ID" in message and "ciu worktree rm rg" in message


def test_unreadable_target_or_primary_identity_is_left_to_the_cleanup(monkeypatch):
    for key in ("REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL"):
        monkeypatch.delenv(key)
    _fake_identity(monkeypatch, target=None, primary={})
    monkeypatch.setenv("INSTANCE_ID", "third9")
    assert worktree._foreign_ambient_identity(Path("/r"), _record(), Path("/t")) is None
    _fake_identity(monkeypatch, target={"INSTANCE_ID": "third9"}, primary=None)
    assert worktree._foreign_ambient_identity(Path("/r"), _record(), Path("/t")) is None
    monkeypatch.setattr(
        worktree, "_primary_ciu_root_for_record",
        lambda *_a: (_ for _ in ()).throw(worktree.WorktreeError("no primary")),
    )
    assert worktree._foreign_ambient_identity(Path("/r"), _record(), Path("/t")) is None


def test_rm_refuses_a_third_instance_shell_before_cleaning_anything(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "one", base="main")
    monkeypatch.setattr(
        worktree, "_foreign_ambient_identity", lambda *_a: "[CIU-191] shell names third9",
    )
    monkeypatch.setattr(
        worktree, "_clean_in", lambda *a, **k: pytest.fail("must not clean"),
    )
    with pytest.raises(worktree.WorktreeRemovalError, match="third9") as err:
        worktree.remove(tmp_repo, "one")
    assert err.value.phase_report["phases"]["resource_cleanup"] == "refused-ambient-identity"
    assert record.git_worktree_path.exists()


def test_rm_dry_run_shows_the_cleanup_plan_without_cleaning(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "one", base="main")
    seen = []
    monkeypatch.setattr(
        worktree, "_clean_in",
        lambda root, **kw: seen.append((root.resolve(), kw)) or 0,
    )
    doc = worktree.remove_document(tmp_repo, "one", dry_run=True)
    assert doc["status"] == "dry-run"
    assert seen and seen[0][0] == record.ciu_root.resolve()
    assert seen[0][1]["plan_only"] is True
    assert record.git_worktree_path.exists()


def test_rm_dry_run_reports_an_unavailable_plan(tmp_repo, fake_generate_env, monkeypatch):
    worktree.create(tmp_repo, "one", base="main")

    def broken(*_a, **_k):
        raise worktree.WorktreeError("no identity")

    monkeypatch.setattr(worktree, "_clean_in", broken)
    doc = worktree.remove_document(tmp_repo, "one", dry_run=True)
    assert "cleanup plan unavailable: no identity" in doc["blockers"]


def test_rm_dry_run_without_a_ciu_root_marker_has_no_plan_to_show(
    tmp_repo, fake_generate_env, monkeypatch
):
    worktree.create(tmp_repo, "one", base="main")
    monkeypatch.setattr(worktree, "_ciu_root_marker_present", lambda _root: False)
    monkeypatch.setattr(
        worktree, "_clean_in", lambda *a, **k: pytest.fail("no root, no plan"),
    )
    doc = worktree.remove_document(tmp_repo, "one", dry_run=True)
    assert doc["status"] == "dry-run"
    assert not any("cleanup plan" in b for b in doc["blockers"])


def test_rm_dry_run_json_keeps_the_plan_off_stdout(tmp_repo, fake_generate_env, monkeypatch):
    worktree.create(tmp_repo, "one", base="main")
    seen = []
    monkeypatch.setattr(
        worktree, "_clean_in", lambda root, **kw: seen.append(kw) or 0,
    )
    worktree.remove_document(tmp_repo, "one", dry_run=True, json_output=True)
    assert seen == [{"yes": False, "plan_only": True, "json_output": True}]


def test_network_that_vanishes_between_exists_and_inspect_is_skipped(monkeypatch):
    """A concurrent teardown won: not an error, nothing disconnected or removed."""
    calls = []
    monkeypatch.setattr(deploy, "_network_exists", lambda _n: True)
    monkeypatch.setattr(deploy, "_network_endpoints", lambda _n: None)
    monkeypatch.setattr(deploy.procutil, "docker", lambda *a, **k: calls.append(a) or _proc())
    assert deploy._remove_identity_networks(["gone"]) == ([], [])
    assert calls == []


def test_unowned_stack_volume_listing_failure_raises(monkeypatch):
    monkeypatch.setattr(deploy.procutil, "docker", lambda *a, **k: _proc(1, "", "down"))
    with pytest.raises(ValueError, match="docker volume ls failed for compose project 'p'"):
        deploy._list_stack_project_volumes(["p"])


def test_stopped_foreign_network_with_the_same_compose_project_survives(
    monkeypatch, tmp_path, capsys
):
    """F1: main's stack is stopped, so its network lists no running endpoint;
    the compose project label is shared and is NOT ownership. Labels decide."""
    repo = _checkout(tmp_path, instance=True, instance_id=WT_ID, root=WT_ROOT)
    daemon = SharedDaemon()
    daemon.networks["naf-dev_default"] = []
    assert _run(monkeypatch, repo, daemon) == 0
    assert "naf-dev_default" in daemon.networks
    assert "naf-wt-network" not in daemon.networks


def test_unlabelled_network_is_kept_in_a_worktree_but_removed_by_main(
    monkeypatch, tmp_path
):
    repo = _checkout(tmp_path / "wt", instance=True, instance_id=WT_ID, root=WT_ROOT) \
        if (tmp_path / "wt").mkdir() is None else None
    daemon = SharedDaemon()
    daemon.networks["naf-dev_default"] = []
    del daemon.network_owners["naf-dev_default"]
    _run(monkeypatch, repo, daemon)
    assert "naf-dev_default" in daemon.networks


def test_uninspectable_network_is_kept(monkeypatch):
    owner = deploy._CleanOwner(WT_ID, WT_ROOT, True)
    monkeypatch.setattr(deploy.procutil, "docker", lambda *a, **k: _proc(1, "", "x"))
    assert deploy._network_foreign_reason("n", owner) == "could not be inspected"
    monkeypatch.setattr(deploy.procutil, "docker", lambda *a, **k: _proc(0, "not json"))
    assert deploy._network_foreign_reason("n", owner) == "could not be inspected"
    monkeypatch.setattr(deploy.procutil, "docker", lambda *a, **k: _proc(0, "[]"))
    assert "unlabelled" in deploy._network_foreign_reason("n", owner)


def test_worktree_whose_facts_record_no_instance_id_refuses(monkeypatch, tmp_path, capsys):
    repo = _checkout(tmp_path, instance=True, instance_id="", root=WT_ROOT)
    daemon = SharedDaemon()
    assert _run(monkeypatch, repo, daemon) == 1
    assert "records no instance id" in capsys.readouterr().out
    assert daemon.mutations() == []


def test_linked_git_worktree_takes_the_strict_rule_without_a_record(tmp_path):
    """F3: a sibling CIU root of a linked worktree may carry no record file."""
    import subprocess as sp

    main = tmp_path / "main"
    main.mkdir()
    for cmd in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t",
                "commit", "-q", "--allow-empty", "-m", "i"]):
        sp.run(["git", "-C", str(main), *cmd], check=True, capture_output=True)
    linked = tmp_path / "linked"
    sp.run(["git", "-C", str(main), "worktree", "add", "-q", "-b", "b", str(linked)],
           check=True, capture_output=True)
    (linked / "sibling").mkdir()
    assert deploy._is_worktree_instance(linked / "sibling") is True
    assert deploy._is_worktree_instance(main) is False
    assert deploy._is_worktree_instance(tmp_path / "nowhere-not-git") is False


def test_missing_git_binary_is_not_a_linked_worktree(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr("subprocess.run", boom)
    assert deploy._is_linked_git_worktree(tmp_path) is False
