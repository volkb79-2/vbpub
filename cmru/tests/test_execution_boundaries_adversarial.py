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

    def test_registered_timestamp_option_reaches_delegated_grammar(self, monkeypatch):
        from cmru import cli, output

        monkeypatch.delenv(output._TIME_ENV, raising=False)
        monkeypatch.setattr(output, "configure", lambda value: setattr(output, "_seen", value))
        delegated = cli._build_cli().delegates["versions"]
        args = delegated.parser.parse_args(["check", "--log-prefix-time-short"])

        assert args.log_prefix_time_short is True
        assert os.environ[output._TIME_ENV] == "1" and output._seen

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
        argv = gate.build_docker_command(tmp_path, "cmru", ["pytest", "-q"], image="tester", cgroup_parent="dev.slice", memory="1g", memory_swap="2g", cpus="1", pids_limit="64")
        assert "--cgroup-parent=dev.slice" in argv and "/host/repo" in " ".join(argv)
        with pytest.raises(ValueError, match="command"):
            gate.build_docker_command(tmp_path, ".", [], image="tester", memory="1g", memory_swap="2g", cpus="1", pids_limit="64", cgroup_parent="dev-gates.slice")

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
            # Gate placement is mandatory: an absent declared tier must refuse
            # before any Docker command can be assembled.
            with pytest.raises(SystemExit, match="cgroup_parent"):
                gate.resolve_cgroup_parent(None)
            monkeypatch.setenv(env, "from-env")
            assert gate.resolve_cgroup_parent(None) == "from-env"
            assert gate.resolve_cgroup_parent("explicit") == "explicit"
            return
        with pytest.raises(SystemExit, match=label):
            getattr(gate, fn)(None)
        if fn == "resolve_cpus":
            monkeypatch.setenv(env, "1.5")
            assert gate.resolve_cpus(None) == "1.5"
            assert gate.resolve_cpus("0.75") == "0.75"
        else:
            # Privileged images must be digest-pinned (BG-06), so their
            # "from-env"/"explicit" stand-ins are digests.
            pinned = fn in ("resolve_cgroup_probe_image", "resolve_dind_image")
            from_env = "img@sha256:" + "1" * 64 if pinned else "from-env"
            explicit = "img@sha256:" + "2" * 64 if pinned else "explicit"
            monkeypatch.setenv(env, from_env)
            assert getattr(gate, fn)(None) == from_env
            assert getattr(gate, fn)(explicit) == explicit

    def test_slice_probe_distinguishes_loaded_transient_and_no_docker(self, monkeypatch):
        import cmru.tester_gate as gate
        monkeypatch.setattr(gate.shutil, "which", lambda name: None)
        assert gate.check_slice_unit("dev.slice", "probe", "dev-gates.slice")[0] is None
        monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="LoadState=loaded\nFragmentPath=\n", stderr=""))
        assert gate.check_slice_unit("typo.slice", "probe", "dev-gates.slice")[0] is False
