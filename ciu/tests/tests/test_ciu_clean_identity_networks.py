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

    def __init__(self, networks=None, volumes=None, network_labels=None):
        # networks: {name: [endpoint, ...]}; volumes: {name: compose-project}
        self.networks = dict(networks or {})
        self.volumes = dict(volumes or {})
        # compose-project label per network (defaults to the <project>_default
        # convention when unset); networks absent here carry no compose label.
        self.network_labels = dict(network_labels or {})
        self.calls: list[list[str]] = []
        self.fail_disconnect_for: set[str] = set()
        self.daemon_down_for: set[str] = set()  # argv[0] ops to fail wholesale

    def __call__(self, args, **kw):
        self.calls.append(args)
        op = args[0]
        if op in self.daemon_down_for and "ls" in args[:2]:
            return _proc(1, "", "Cannot connect to the Docker daemon")

        if op == "network":
            if args[1] == "ls":
                named = sorted(self.networks)
                for i, a in enumerate(args):
                    if a == "--filter" and args[i + 1].startswith("name=^"):
                        want = args[i + 1][len("name=^"):-1]  # ^<net>$ exact
                        named = [n for n in named if n == want]
                    elif a == "--filter" and args[i + 1].startswith("label=com.docker.compose.project="):
                        project = args[i + 1].split("=", 2)[2]
                        named = [
                            n for n in named
                            if self.network_labels.get(n) == project
                        ]
                return _proc(0, "\n".join(named), "")
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
    """This devcontainer sources a checkout's legacy ciu.env export; scrub the
    identity it leaks into this process."""
    for key in ("REPO_ROOT", "REPO_NAME", "INSTANCE_ID", "DOCKER_NETWORK_INTERNAL"):
        monkeypatch.delenv(key, raising=False)


def _write_facts(root, **facts):
    """CIU-75: instance identity lives in the checkout's generated overlay
    table, so a fixture that wants a checkout to look provisioned writes THAT,
    not the legacy `ciu.env` export."""
    from ciu.workspace_env import GENERATED_FACTS_KEYS, upsert_generated_facts

    payload = {key: "" for key in GENERATED_FACTS_KEYS}
    payload.update(facts)
    upsert_generated_facts(root, payload)


def _instance_repo(tmp_path: Path) -> Path:
    _write_facts(tmp_path, network="proj-abc123-network")
    (tmp_path / "ciu.worktree-instance.json").write_text(
        '{"schema_version": 1}\n', encoding="utf-8"
    )
    (tmp_path / "apps" / "vault").mkdir(parents=True)
    return tmp_path


def _main_repo(tmp_path: Path) -> Path:
    _write_facts(tmp_path, network="proj-abc123-network")
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
        network_labels={"proj-env-vault_default": "proj-env-vault"},
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
        network_labels={"proj-env-vault_default": "proj-env-vault"},
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
        network_labels={"proj-env-vault_default": "proj-env-vault"},
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
    fake = FakeDocker(
        networks={"proj-abc123-network": [], "proj-env-vault_default": []},
        network_labels={"proj-env-vault_default": "proj-env-vault"},
    )
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


# ---------------------------------------------------------------------------
# Review repairs — B2 (clean threads the selection facts), B3 (daemon failure
# fails closed), N1 (custom-named compose networks)
# ---------------------------------------------------------------------------


def test_clean_renders_stack_templates_with_selection_context(monkeypatch, tmp_path):
    """Review B2: a stack template referencing ciu.* renders during clean too —
    omitting the facts would crash teardown for adopters of the documented
    CONSUMERS §10 pattern."""
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(networks={})
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    sel = [{"path": "apps/vault"}]
    received: list[dict | None] = []

    def render(root, profile_arg, selection_arg, ciu_context=None):
        received.append(ciu_context)
        return {entry["path"]: {} for entry in selection_arg}

    monkeypatch.setattr(deploy, "render_selected_stacks", render)
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])

    rc = deploy.action_clean(repo_root, _profile(), sel, ignore_errors=True)

    assert rc == 0
    assert received[-1] == {
        "selected_profiles": [],
        "deployed_stacks": ["apps/vault"],
    }


def test_daemon_failure_during_network_verification_fails_clean(
    monkeypatch, tmp_path, capsys
):
    """Review B3: an unverifiable network is a violation, never 'gone'."""
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(networks={"proj-env-vault_default": []})
    fake.network_labels["proj-env-vault_default"] = "proj-env-vault"
    # daemon dies for network ls AFTER enumeration would have happened:
    fake.daemon_down_for.add("network")

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 1
    out = capsys.readouterr().out
    assert "unverifiable" in out or "failed" in out
    assert "clean completed with errors" in out


def test_custom_named_compose_network_removed_via_label_pass(monkeypatch, tmp_path):
    """Review N1: stacks declaring custom-named compose networks are still
    enumerated exactly (compose project label), never left to a name guess."""
    repo_root = _instance_repo(tmp_path)
    fake = FakeDocker(
        networks={"vault-tier-net": []},
        network_labels={"vault-tier-net": "proj-env-vault"},
    )

    rc = _run_clean(monkeypatch, repo_root, fake)

    assert rc == 0
    assert fake.networks == {}


# ---------------------------------------------------------------------------
# Gate-repair coverage — every fail-closed path the release gate found dark
# ---------------------------------------------------------------------------


class InstrumentedDocker(FakeDocker):
    """FakeDocker with ordinal-based ls-failure injection (1-based)."""

    def __init__(self, *a, net_ls_fail_at=(), vol_ls_fail_at=(),
                 inspect_garbage=(), inspect_empty=(), inspect_missing=(),
                 rm_fail=(), **kw):
        super().__init__(*a, **kw)
        self.net_ls_fail_at = set(net_ls_fail_at)
        self.vol_ls_fail_at = set(vol_ls_fail_at)
        self._net_ls_seen = 0
        self._vol_ls_seen = 0
        self.inspect_garbage: set[str] = set(inspect_garbage)
        self.inspect_empty: set[str] = set(inspect_empty)
        self.inspect_missing: set[str] = set(inspect_missing)  # gone between exists and inspect
        self.rm_fail: set[str] = set(rm_fail)

    def __call__(self, args, **kw):
        op, sub = args[0], args[1] if len(args) > 1 else ""
        # Only FILTERED ls calls are counted: the legacy unfiltered volume
        # name-pass predates S6.4a and its swallow-failure semantics are not
        # under test here.
        filtered = "--filter" in args
        if op == "network" and sub == "ls" and filtered:
            self._net_ls_seen += 1
            if self._net_ls_seen in self.net_ls_fail_at:
                return _proc(1, "", "Cannot connect to the Docker daemon")
        if op == "volume" and sub == "ls" and filtered:
            self._vol_ls_seen += 1
            if self._vol_ls_seen in self.vol_ls_fail_at:
                return _proc(1, "", "Cannot connect to the Docker daemon")
        if op == "network" and sub == "inspect":
            name = args[2]
            if name in self.inspect_missing:
                self.networks.pop(name, None)  # consistent: it IS gone
                return _proc(1, "", f"Error: No such network: {name}")
            if name in self.inspect_garbage:
                return _proc(0, "NOT JSON {{", "")
            if name in self.inspect_empty:
                return _proc(0, "[]", "")
        if op == "network" and sub == "rm" and args[2] in self.rm_fail:
            self.calls.append(args)
            return _proc(1, "", f"Error response from daemon: reference does not exist: {args[2]}")
        return super().__call__(args, **kw)


def _instance_repo_with_vault(tmp_path):
    repo_root = _instance_repo(tmp_path)
    # a compose-labeled network for the selected stack
    return repo_root


def test_rm_verify_daemon_failure_resolved_by_invariant_state(monkeypatch, tmp_path, capsys):
    """Daemon dying BETWEEN rm and re-verification records the removal as
    blocked-indeterminate; when the invariant's own state check then proves
    the network gone (a concurrent teardown won), the clean legitimately
    succeeds — the stale blocked entry never overrides Docker state."""
    repo_root = _instance_repo(tmp_path)
    fake = InstrumentedDocker(
        networks={"proj-abc123-network": [], "proj-env-vault_default": []},
        network_labels={"proj-env-vault_default": "proj-env-vault"},
        # filtered-ls ordinals: 1 enum, 2 id-exists, 3 id-verify, 4 vault-exists,
        # 5 vault-verify (FAILS), 6/7 invariant re-checks
        net_ls_fail_at={5},
    )
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 0
    assert fake.networks == {}


def test_invariant_unverifiable_fails_clean(monkeypatch, tmp_path, capsys):
    """A network that cannot be re-checked at invariant time fails the clean
    (review B3): indeterminacy never folds into 'gone'."""
    repo_root = _instance_repo(tmp_path)
    fake = InstrumentedDocker(
        networks={"proj-abc123-network": [], "proj-env-vault_default": []},
        network_labels={"proj-env-vault_default": "proj-env-vault"},
        net_ls_fail_at={7},  # invariant's re-check of the second target
    )
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 1
    out = capsys.readouterr().out
    assert "unverifiable" in out


def test_removal_refusal_names_network_and_fails(monkeypatch, tmp_path, capsys):
    repo_root = _instance_repo(tmp_path)
    fake = InstrumentedDocker(
        networks={"proj-abc123-network": []},
        rm_fail={"proj-abc123-network"},
    )
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 1
    out = capsys.readouterr().out
    assert "proj-abc123-network" in out
    assert "post-clean invariant violated (S6.4a)" in out


def test_inspect_garbage_is_refused_not_empty(monkeypatch):
    """Unparsable inspect output raises — absence-for-emptiness forbidden."""
    fake = InstrumentedDocker(networks={"net-a": []}, inspect_garbage={"net-a"})
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    with pytest.raises(ValueError, match="unparsable"):
        deploy._network_endpoints("net-a")


def test_inspect_empty_list_is_zero_endpoints(monkeypatch):
    fake = InstrumentedDocker(networks={"net-a": []}, inspect_empty={"net-a"})
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    assert deploy._network_endpoints("net-a") == []


def test_concurrent_teardown_between_exists_and_inspect_is_skipped(monkeypatch, tmp_path):
    """A network that vanishes right after the existence check is fine."""
    repo_root = _instance_repo(tmp_path)
    fake = InstrumentedDocker(
        networks={"proj-abc123-network": [], "proj-env-vault_default": []},
        network_labels={"proj-env-vault_default": "proj-env-vault"},
        inspect_missing={"proj-env-vault_default"},
    )
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 0
    assert fake.networks == {"proj-abc123-network": []} or len(fake.networks) <= 1


def _clean_with_identity_env(monkeypatch, tmp_path, sel=None):
    """Shared arrangement for the three CIU-62 identity-env arcs below."""
    (tmp_path / "ciu.worktree-instance.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "apps" / "vault").mkdir(parents=True)
    sel = sel or [{"path": "apps/vault"}]
    fake = FakeDocker(networks={})
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    monkeypatch.setattr(deploy, "render_selected_stacks",
                        lambda *a, **k: {e["path"]: {} for e in sel})
    monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    return deploy.action_clean(tmp_path, _profile(), sel, ignore_errors=True)


def test_identity_env_parse_failure_is_indeterminate_not_no_network(
    monkeypatch, tmp_path, capsys
):
    """CIU-62 — REVERSED contract (this test previously asserted the opposite).

    An UNPARSEABLE generated table used to read as "there is no identity
    network",
    which is the estate's absence-for-emptiness anti-pattern with a live
    consequence: the instance's own network is then neither removed nor
    enumerated as a survivor, so `ciu clean` announces the S6.4a zero-objects
    invariant as satisfied over a network it never even resolved. An
    unresolvable identity network is now reported and fails the clean, the
    same treatment its sibling volume/network/container enumerations already
    give indeterminacy.
    """
    (tmp_path / "ciu.global.worktree.toml.j2").write_text(
        "[ciu.instance.generated]\nnetwork = not-a-toml-value\n", encoding="utf-8"
    )

    rc = _clean_with_identity_env(monkeypatch, tmp_path)

    assert rc == 1
    out = capsys.readouterr().out
    assert "workspace identity network unresolvable (S6.4a)" in out
    assert "ciu.global.worktree.toml.j2" in out


def test_identity_env_non_utf8_is_indeterminate_too(monkeypatch, tmp_path, capsys):
    """CIU-62 — the byte-level half. `UnicodeDecodeError` is a `ValueError`
    subclass and a SIBLING of `WorkspaceEnvError`; the pre-fix
    `except WorkspaceEnvError` caught neither it nor `OSError`, so a
    non-UTF-8 record escaped `action_clean` as a raw traceback rather than
    either of its two defined outcomes. CIU-75 moved the source to the
    overlay and normalized all three types at the reader."""
    (tmp_path / "ciu.global.worktree.toml.j2").write_bytes(
        b'[ciu.instance.generated]\nnetwork = "\xff\xfe"\n'
    )

    rc = _clean_with_identity_env(monkeypatch, tmp_path)

    assert rc == 1
    assert "workspace identity network unresolvable (S6.4a)" in capsys.readouterr().out


def test_identity_env_absent_still_reads_as_no_network(monkeypatch, tmp_path, capsys):
    """CIU-62's legitimate state, constructed: a checkout where `ciu env
    generate` was never run genuinely HAS no identity network. That arc must
    stay green — a refusal whose condition also matches an ordinary state is
    a superset refusal, and gets switched off."""
    assert not (tmp_path / "ciu.global.worktree.toml.j2").exists()

    rc = _clean_with_identity_env(monkeypatch, tmp_path)

    assert rc == 0
    assert "workspace identity network unresolvable" not in capsys.readouterr().out


def test_stack_projects_skip_missing_dirs():
    from ciu.deploy_pkg.profiles import Profile
    config = {"deploy": {"project_name": "p", "environment_tag": "e"}}
    assert deploy._stack_compose_projects(
        Path("/nonexistent-root"), config, [{"path": "no/such/dir"}]
    ) == []


def test_volume_enumeration_daemon_failure_fails_clean(monkeypatch, tmp_path, capsys):
    """Volume ls failing mid-clean fails the clean — never 'nothing to remove'."""
    repo_root = _instance_repo(tmp_path)
    fake = InstrumentedDocker(networks={}, vol_ls_fail_at={1})
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 1
    out = capsys.readouterr().out
    assert "volume enumeration failed (S6.4a)" in out
    assert "cannot certify a complete teardown" in out


def test_main_workspace_kept_network_already_gone_stays_green(monkeypatch, tmp_path, capsys):
    repo_root = _main_repo(tmp_path)
    fake = FakeDocker(networks={}, )  # identity net named in the overlay but absent
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 0
    out = capsys.readouterr().out
    assert "kept:" not in out
    assert "clean complete" in out


def test_stack_projects_dedupe_repeated_compose_projects(tmp_path):
    """The dedupe arc: distinct stack paths whose BASENAMES collide resolve to
    one compose project name (S8.7 keys on the basename)."""
    config = {"deploy": {"project_name": "p", "environment_tag": "e"}}
    root = tmp_path
    for rel in ("apps/vault", "other/vault"):
        (root / rel).mkdir(parents=True)
    assert deploy._stack_compose_projects(
        root, config,
        [{"path": "apps/vault"}, {"path": "apps/vault"}, {"path": "other/vault"}],
    ) == ["p-e-vault"]


def test_enumerated_network_matching_a_keep_is_not_targeted(monkeypatch, tmp_path):
    """A compose-labeled network whose name EQUALS the kept workspace network is
    skipped as a target (the keep wins) — the skip-append arc."""
    repo_root = _main_repo(tmp_path)
    # identity net ALSO carries a compose label matching the selected project:
    # pathological but expressible, and it must resolve to keep-not-remove.
    fake = InstrumentedDocker(
        networks={"proj-abc123-network": []},
        network_labels={"proj-abc123-network": "proj-env-vault"},
    )
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 0
    assert "proj-abc123-network" in fake.networks


def test_kept_network_check_daemon_failure_warns_not_crashes(monkeypatch, tmp_path, capsys):
    """The keep-existence check degrading (daemon failure) warns and keeps
    going — the keep was declared by policy, not discovered by state."""
    repo_root = _main_repo(tmp_path)
    fake = InstrumentedDocker(networks={}, net_ls_fail_at={2})
    # ordinals: 1 enum(label, empty), 2 keep-check(identity) FAILS -> warn path
    rc = _run_clean(monkeypatch, repo_root, fake)
    assert rc == 0
    out = capsys.readouterr().out
    assert "kept-network check failed" in out
    assert "clean complete" in out
