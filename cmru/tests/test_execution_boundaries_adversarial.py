"""Behavioural tests for execution-facing CMRU boundaries.

All network, Docker, systemd and subprocess interactions are replaced at the
external seam.  Assertions check argv, refusal, serialized output and state.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class TestGithubReleaseHost:
    def _host(self):
        from cmru.hosts.github import GitHubReleaseHost
        return GitHubReleaseHost("o", "r", "t", api_base="https://api.example")

    def test_create_upload_list_and_download_use_release_contracts(self, tmp_path):
        h = self._host()
        asset = tmp_path / "a.whl"; asset.write_bytes(b"x")
        h._gh = SimpleNamespace(
            create_release=lambda *a, **kw: {"id": 7},
            _repo_url=lambda path: "https://api.example" + path,
            _request=lambda *a, **kw: (200, json.dumps({"upload_url": "upload", "tag_name": "v1"})),
            upload_asset=lambda *a: None,
            asset_download_url=lambda tag, name: f"https://download/{tag}/{name}",
            list_releases=lambda: [
                {"tag_name": "p-v1.0.0", "id": 1, "assets": [{"name": "x", "browser_download_url": "u"}]},
                {"tag_name": "p-v2.0.0", "draft": True, "assets": []},
                {"tag_name": "other-v9.0.0", "assets": []},
            ],
        )
        assert h.create_release("v1", "name", "body") == "7"
        assert h.upload_asset("7", asset) == "https://download/v1/a.whl"
        listed = h.list_releases("p-")
        assert listed == [{"tag": "p-v1.0.0", "id": "1", "assets": [{"name": "x", "url": "u"}]}]
        assert h.download_url("v1", "a.whl").endswith("/v1/a.whl")

    def test_upload_http_failure_and_latest_asset_sha_are_observable(self, monkeypatch, tmp_path):
        h = self._host(); asset = tmp_path / "a"; asset.write_bytes(b"x")
        h._gh = SimpleNamespace(
            _repo_url=lambda path: "https://api.example" + path,
            _request=lambda *a, **kw: (500, "bad"),
            _fail=lambda *a: (_ for _ in ()).throw(RuntimeError("release failure")),
        )
        with pytest.raises(RuntimeError, match="release failure"):
            h.upload_asset("7", asset)
        h._gh = SimpleNamespace(list_releases=lambda: [
            {"tag_name": "p-v1.2.0", "assets": [
                {"name": "bundle", "browser_download_url": "u"},
                {"name": "bundle.sha256", "browser_download_url": "sha"},
                {"name": "latest.json", "browser_download_url": "latest"},
            ]},
            {"tag_name": "p-v1.1.0", "assets": [{"name": "bundle", "browser_download_url": "old"}]},
        ])
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b"abc123  bundle\n"
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
        assert h.resolve_latest("p-")["sha256"] == "abc123"

    def test_latest_returns_none_for_no_release_or_no_primary_asset(self):
        h = self._host()
        h._gh = SimpleNamespace(list_releases=lambda: [])
        assert h.resolve_latest("p-") is None
        h._gh = SimpleNamespace(list_releases=lambda: [{"tag_name": "p-v1", "assets": [{"name": "x.sha256"}]}])
        assert h.resolve_latest("p-") is None

    def test_env_factory_reads_credentials(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USERNAME", "owner")
        monkeypatch.setenv("GITHUB_REPO", "repo")
        monkeypatch.setenv("GITHUB_PUSH_PAT", "secret")
        from cmru.hosts.github import github_host_from_env
        h = github_host_from_env()
        assert (h._gh.owner, h._gh.repo, h._gh.token) == ("owner", "repo", "secret")


class TestGhcrBoundaryPaths:
    def test_request_headers_and_visibility_update_payload(self):
        from cmru.ghcr import GitHubPackages
        g = GitHubPackages("o", "r", "secret", "org", api_base="https://api")
        seen = {}
        class Resp:
            status = 200
            def read(self): return b'{"visibility":"public"}'
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def opener(req):
            seen.update(method=req.method, auth=req.headers.get("Authorization"), data=req.data)
            return Resp()
        with mock.patch("cmru.ghcr.urlopen", opener):
            assert g.set_package_visibility("pkg", "public")["visibility"] == "public"
        assert seen["method"] == "PATCH" and seen["auth"] == "Bearer secret"
        assert json.loads(seen["data"]) == {"visibility": "public"}

    @pytest.mark.parametrize("owner_type", ["bogus", ""])
    def test_unknown_owner_type_fails_before_network(self, owner_type):
        from cmru.ghcr import GitHubPackages
        with pytest.raises(SystemExit):
            GitHubPackages("o", "r", "t", owner_type).package_visibility("p")

    def test_mirror_retries_missing_package_then_updates_and_validates_response(self, monkeypatch):
        from cmru.ghcr import GitHubPackages
        g = GitHubPackages("o", "r", "t", "user")
        seq = iter([None, "private"])
        monkeypatch.setattr(g, "repo_visibility", lambda: "public")
        monkeypatch.setattr(g, "package_visibility", lambda p: next(seq))
        monkeypatch.setattr(g, "set_package_visibility", lambda p, v: {"visibility": v})
        monkeypatch.setattr("cmru.ghcr.time.sleep", lambda _: None)
        assert g.mirror_package_visibility("p", retries=2, delay=0) == "public"

    def test_mirror_exhaustion_and_wrong_update_are_refused(self, monkeypatch):
        from cmru.ghcr import GitHubPackages
        g = GitHubPackages("o", "r", "t", "user")
        monkeypatch.setattr(g, "repo_visibility", lambda: "public")
        monkeypatch.setattr(g, "package_visibility", lambda p: None)
        monkeypatch.setattr("cmru.ghcr.time.sleep", lambda _: None)
        with pytest.raises(SystemExit):
            g.mirror_package_visibility("p", retries=2, delay=0)
        monkeypatch.setattr(g, "package_visibility", lambda p: "private")
        monkeypatch.setattr(g, "set_package_visibility", lambda p, v: {"visibility": "private"})
        with pytest.raises(SystemExit):
            g.mirror_package_visibility("p", retries=1)


class TestOutputContract:
    def test_severity_stream_partial_prefix_and_flush(self):
        from cmru.output import SeverityStream
        raw = io.StringIO(); stream = SeverityStream(raw, time_short=False, colour=False)
        assert stream.write("[ER") == 3
        assert raw.getvalue() == ""
        stream.write("ROR] bad\n")
        stream.write("ordinary")
        stream.flush()
        assert raw.getvalue() == "[ERROR] bad\nordinary"

    def test_consume_flags_stops_at_separator_and_propagates_env(self, monkeypatch):
        import cmru.output as output
        monkeypatch.delenv("CMRU_LOG_PREFIX_TIME_SHORT", raising=False)
        monkeypatch.setattr(output, "configure", lambda value: setattr(output, "_seen", value))
        assert output.consume_cli_flags(["--log-prefix-time-short", "run", "--", "--log-prefix-time-short"]) == ["run", "--", "--log-prefix-time-short"]
        assert os.environ["CMRU_LOG_PREFIX_TIME_SHORT"] == "1" and output._seen

    def test_colour_is_disabled_for_dumb_or_no_color(self, monkeypatch):
        from cmru.output import _colour_enabled
        stream = SimpleNamespace(isatty=lambda: True)
        monkeypatch.setenv("TERM", "dumb")
        assert not _colour_enabled(stream)
        monkeypatch.setenv("TERM", "xterm"); monkeypatch.setenv("NO_COLOR", "1")
        assert not _colour_enabled(stream)


class TestTesterGateContracts:
    def test_worktree_context_rejects_escape_and_builds_safe_command(self, tmp_path, monkeypatch):
        import cmru.tester_gate as gate
        with pytest.raises(ValueError, match="relative"):
            gate._resolve_worktree_context(tmp_path, "../outside")
        monkeypatch.setattr(gate, "_physical_path", lambda p: Path("/host/repo"))
        monkeypatch.setattr(gate, "_git_common_dir", lambda p: None)
        argv = gate.build_docker_command(tmp_path, "cmru", ["pytest", "-q"], image="tester", cgroup_parent="dev.slice", memory="1g", memory_swap="2g", cpus="1")
        assert "--cgroup-parent=dev.slice" in argv and "/host/repo" in " ".join(argv)
        with pytest.raises(ValueError, match="command"):
            gate.build_docker_command(tmp_path, ".", [], image="tester", memory="1g", memory_swap="2g", cpus="1")

    @pytest.mark.parametrize("fn,env,label", [
        ("resolve_cgroup_parent", "CMRU_TESTER_CGROUP_PARENT", "cgroup_parent"),
        ("resolve_memory", "CMRU_TESTER_MEMORY", "memory"),
        ("resolve_memory_swap", "CMRU_TESTER_MEMORY_SWAP", "memory-swap"),
        ("resolve_cpus", "CMRU_TESTER_CPUS", "CPU"),
        ("resolve_cgroup_probe_image", "CMRU_TESTER_CGROUP_PROBE_IMAGE", "probe"),
        ("resolve_dind_image", "CMRU_TESTER_DIND_IMAGE", "nested Docker"),
    ])
    def test_required_resource_resolution_fails_loudly_and_prefers_explicit(self, monkeypatch, fn, env, label):
        import cmru.tester_gate as gate
        monkeypatch.delenv(env, raising=False)
        if fn == "resolve_cgroup_parent":
            # Declared-only (CIU-46 wave): unset resolves to None — the
            # unscoped launch is announced in main(), not a refusal here.
            monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
            assert gate.resolve_cgroup_parent(None) is None
            monkeypatch.setenv(env, "from-env")
            assert gate.resolve_cgroup_parent(None) == "from-env"
            assert gate.resolve_cgroup_parent("explicit") == "explicit"
            return
        with pytest.raises(SystemExit, match=label):
            getattr(gate, fn)(None)
        monkeypatch.setenv(env, "from-env")
        assert getattr(gate, fn)("explicit") == "explicit"

    def test_slice_probe_distinguishes_loaded_transient_and_no_docker(self, monkeypatch):
        import cmru.tester_gate as gate
        monkeypatch.setattr(gate.shutil, "which", lambda name: None)
        assert gate.check_slice_unit("dev.slice", "probe")[0] is None
        monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="LoadState=loaded\nFragmentPath=\n", stderr=""))
        assert gate.check_slice_unit("typo.slice", "probe")[0] is False


class TestCliExecutionContracts:
    def _args(self, **overrides):
        values = dict(plan=None, landscape=None, to_tag=None, generation=None,
                      scope="user", node_id=None, token=None, minisign_pubkey=None,
                      release_root=None, consul_addr=None, dry_run=False,
                      log_level="INFO")
        values.update(overrides)
        return SimpleNamespace(**values)

    def _plan_file(self, tmp_path):
        p = tmp_path / "plan.json"
        p.write_text("""[plan]
id = "p"
landscape = "prod"
release_tag = "v1"
manifest_url = "u"
manifest_sha256 = "s"

[[plan.waves]]
phase = 1
name = "canary"
type = "canary"
nodes = ["n"]
profiles = []
""")
        return p

    def test_controller_commands_dispatch_success_and_failure(self, tmp_path, monkeypatch, capsys):
        from cmru.controller import cli
        plan = self._plan_file(tmp_path)
        events = []
        engine = SimpleNamespace(
            publish=lambda p: events.append("publish"),
            approve=lambda p: events.append(("approve", p)),
            hold=lambda p: events.append(("hold", p)),
            rollback=lambda p, **kw: events.append(("rollback", kw)),
            status=lambda p: {"ok": True},
        )
        monkeypatch.setattr(cli, "_build_engine", lambda *a: engine)
        assert cli.cmd_publish(self._args(plan=str(plan))) == 0
        assert cli.cmd_approve(self._args(plan="p")) == 0
        assert cli.cmd_hold(self._args(plan="p")) == 0
        assert cli.cmd_rollback(self._args(plan=str(plan), to_tag="old", generation=8)) == 0
        assert cli.cmd_status(self._args(plan=str(plan))) == 0
        assert events[:3] == ["publish", ("approve", "p"), ("hold", "p")]
        assert '"ok": true' in capsys.readouterr().out

        broken = SimpleNamespace(publish=lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(cli, "_build_engine", lambda *a: broken)
        assert cli.cmd_publish(self._args(plan=str(plan))) == 1
        assert "Publish failed" in capsys.readouterr().err

    def test_controller_status_catalog_and_malformed_plan_refuse(self, monkeypatch, capsys):
        from cmru.controller import cli
        assert cli.cmd_status(self._args()) == 2
        class Backend:
            def _get(self, path): return 200, b"not-json", {}
        monkeypatch.setattr(cli, "_build_backend", lambda args: Backend())
        assert cli.cmd_status(self._args(landscape="prod")) == 0
        assert "Could not parse" in capsys.readouterr().out

    def test_agent_cli_backend_env_and_command_outcomes(self, tmp_path, monkeypatch, capsys):
        import cmru.agent.cli as cli
        monkeypatch.setenv("CONSUL_HTTP_ADDR", "http://env")
        monkeypatch.setenv("CONSUL_HTTP_TOKEN", "env-token")
        backend = cli._build_backend(self._args())
        assert backend._addr == "http://env" and backend._token == "env-token"
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        args = self._args(node_id="n", landscape="l", token="arg-token", minisign_pubkey="pub")
        identity = SimpleNamespace(node_id="n", landscape="l", token_path=None, public_key="pub")
        fake_backend = SimpleNamespace(_token=None, enroll=lambda seed: identity)
        monkeypatch.setattr(cli, "_build_backend", lambda args: fake_backend)
        assert cli.cmd_enroll(args) == 0
        monkeypatch.setattr(cli, "_load_identity", lambda scope: ("n", {"landscape": "l", "public_key": "p"}))
        rec = SimpleNamespace(run=lambda: None, once=lambda: False)
        monkeypatch.setattr("cmru.agent.reconciler.Reconciler", lambda **kw: rec)
        assert cli.cmd_run(args) == 0 and cli.cmd_once(args) == 0
        assert "no change" in capsys.readouterr().out

    def test_agent_cli_enroll_and_status_refuse_bad_inputs(self, tmp_path, monkeypatch, capsys):
        import cmru.agent.cli as cli
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        args = self._args()
        assert cli.cmd_enroll(args) == 2
        args = self._args(node_id="n")
        assert cli.cmd_enroll(args) == 2
        monkeypatch.setattr(cli, "_build_backend", lambda args: SimpleNamespace(enroll=lambda seed: (_ for _ in ()).throw(RuntimeError("down")), _token=None))
        args = self._args(node_id="n", landscape="l")
        assert cli.cmd_enroll(args) == 1
        from cmru.agent.state import write_node_id, write_current_generation, write_observed
        from cmru.agent.protocol import ObservedState
        write_node_id("n"); write_current_generation(4); write_observed(ObservedState(applied_generation=4, health="failed", error_class="bad", started_at="s", finished_at="f"))
        assert cli.cmd_status(self._args()) == 0
        assert "error_class" in capsys.readouterr().out


class TestRolloutStateTransitions:
    def _plan(self):
        from cmru.controller.planner import load_plan_json
        return load_plan_json(json.dumps({"plan": {"id": "p", "landscape": "prod",
            "release_tag": "v1", "manifest_url": "u", "manifest_sha256": "s",
            "waves": [{"phase": 1, "name": "canary", "type": "canary", "nodes": ["n"], "profiles": []},
                       {"phase": 2, "name": "prod", "type": "production", "nodes": ["m"], "profiles": []}]}}))

    def test_approval_and_hold_are_polled_before_production(self, monkeypatch):
        from cmru.controller.rollout import RolloutEngine, _plan_approval_key, _plan_hold_key
        plan = self._plan()
        class Backend:
            def __init__(self): self.approved = False; self.released = False; self.writes = []
            def _put(self, path, body, params=None): self.writes.append((path, body)); return 200, b"true"
            def _get(self, path, params=None):
                if path.endswith("/approved") and self.approved: return 200, b"approved", {}
                if path.endswith("/hold") and not self.released: return 200, b"hold", {}
                return 404, b"", {}
            def read_observed(self, node, landscape):
                from cmru.agent.protocol import ObservedState
                return ObservedState(applied_generation=201, health="healthy").to_json() if node == "m" else ObservedState(applied_generation=101, health="healthy").to_json()
        b = Backend(); calls = [0]
        def sleep(_):
            calls[0] += 1
            if calls[0] == 1: b.released = True
            else: b.approved = True
        monkeypatch.setattr("cmru.controller.rollout.time.sleep", sleep)
        RolloutEngine(b, "prod", poll_interval=1, wave_timeout=1).publish(plan)
        assert any(path.endswith("/status") and b'complete' in body for path, body in b.writes)
