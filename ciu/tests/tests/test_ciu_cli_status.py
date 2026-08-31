"""ciu-P16 / CIU-QOL-6 — `ciu status [--profile NAME] [--json]` (read-only).

Oracles under test:
- O1: every selected stack resolves via `_stack_compose_project` (the SAME
  per-entry branching `_stack_compose_projects` uses), never a bespoke
  re-derivation; a stack directory missing on disk gets a named row (never
  silently dropped from the report).
- O2: the `--json` envelope is the exact versioned shape (schema_version,
  profile, stacks[...]); container `status` is `health_pkg.classify`'s
  closed vocabulary applied to that container's own State dict; `image` is
  `Config.Image` verbatim.
- O3: `ciu status` is wired into cli.py's verb dispatch and `_USAGE`/
  `_VERB_HELP`; read-only (no compose up/down/build/exec anywhere in this
  path); a Docker daemon failure is a clean `[ERROR]` + exit 2, never a
  traceback.
- O4 is documentation-only; not exercised here.

The headline anti-pattern this file guards against (review_focus): a Docker-
daemon-unreachable failure must never render the same as "nothing running".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402
from ciu import deploy  # noqa: E402
from ciu import dev  # noqa: E402


def _profile(config=None, name=None):
    return deploy.profiles_pkg.Profile(name=name, config=config or {"deploy": {}})


def _run_json(repo_root, profile, selection, capsys):
    rc = deploy.action_status(repo_root, profile, selection, json_output=True)
    doc = json.loads(capsys.readouterr().out)
    return rc, doc


# ---------------------------------------------------------------------------
# action_status — O1: resolution, missing-stack rows never dropped
# ---------------------------------------------------------------------------


class TestActionStatusResolution:
    def test_missing_stack_dir_gets_a_named_row_not_dropped(self, tmp_path, capsys):
        selection = [{"path": "apps/missing", "name": "missing", "phase_key": "phase_1"}]
        rc, doc = _run_json(tmp_path, _profile(), selection, capsys)
        assert rc == 0
        assert doc == {
            "schema_version": 1,
            "profile": None,
            "stacks": [
                {
                    "path": "apps/missing",
                    "name": "missing",
                    "phase_key": "phase_1",
                    "compose_project": None,
                    "containers": [],
                }
            ],
        }

    def test_one_missing_stack_does_not_hide_other_stacks(self, tmp_path, monkeypatch, capsys):
        """review_focus: a stack selected but not yet deployed must not error
        out (or hide) the whole command — every OTHER stack still reports."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        selection = [
            {"path": "apps/vault", "name": "vault", "phase_key": "phase_1"},
            {"path": "apps/missing", "name": "missing", "phase_key": "phase_2"},
        ]
        monkeypatch.setattr(deploy, "_stack_compose_project", lambda *_a: "identity-vault")
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])
        rc, doc = _run_json(tmp_path, _profile(), selection, capsys)
        assert rc == 0
        assert len(doc["stacks"]) == 2
        assert doc["stacks"][0]["compose_project"] == "identity-vault"
        assert doc["stacks"][1]["compose_project"] is None

    def test_resolution_uses_the_shared_per_entry_helper_tagged(
        self, tmp_path, monkeypatch, capsys
    ):
        """Tags present -> engine.compose_project_name (S8.7 scoped name),
        same branch _stack_compose_projects already uses."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "proj", "environment_tag": "env"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])
        rc, doc = _run_json(tmp_path, profile, selection, capsys)
        assert rc == 0
        assert doc["stacks"][0]["compose_project"] == "proj-env-vault"
        assert doc["stacks"][0]["containers"] == []

    def test_resolution_uses_the_shared_per_entry_helper_untagged_identity(
        self, tmp_path, monkeypatch, capsys
    ):
        """Tags absent -> engine.identity_compose_project_name (CIU-46
        workspace identity), driven by THIS checkout's generated overlay
        facts."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        from ciu.workspace_env import GENERATED_FACTS_KEYS, upsert_generated_facts

        # CIU-75: the identity naming reads the generated overlay table.
        facts = {key: "" for key in GENERATED_FACTS_KEYS}
        facts["repo_name"] = "dstdns"
        facts["instance_id"] = "abc123"
        upsert_generated_facts(tmp_path, facts)
        profile = _profile(config={"deploy": {}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])
        rc, doc = _run_json(tmp_path, profile, selection, capsys)
        assert rc == 0
        assert doc["stacks"][0]["compose_project"] == "dstdns-abc123-vault"

    def test_missing_identity_record_refuses_rather_than_guesses(self, tmp_path):
        """A stack dir that EXISTS but whose project cannot be named (no
        generated facts, tags absent) raises -- mirrors
        _stack_compose_projects's own
        'a teardown/report that cannot be named refuses' discipline. Not
        silently reported as an empty/healthy stack."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]
        with pytest.raises(ValueError, match="declares no repo_name/instance_id"):
            deploy.action_status(tmp_path, profile, selection, json_output=True)


# ---------------------------------------------------------------------------
# action_status — O2: envelope shape, classify() vocabulary, image passthrough
# ---------------------------------------------------------------------------


class TestActionStatusEnvelope:
    def test_existing_project_zero_containers_is_a_legitimate_empty_not_an_error(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "proj", "environment_tag": "env"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])

        rc, doc = _run_json(tmp_path, profile, selection, capsys)
        assert rc == 0
        row = doc["stacks"][0]
        assert row["compose_project"] == "proj-env-vault"
        assert row["containers"] == []

    def test_populated_project_reports_mixed_container_statuses_and_images(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "proj", "environment_tag": "env"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]

        inspected = [
            {
                "Name": "/proj-env-vault-1",
                "State": {"Health": {"Status": "healthy"}},
                "Config": {"Image": "vault:1.15"},
            },
            {
                "Name": "/proj-env-vault-starting",
                "State": {"Health": {"Status": "starting"}},
                "Config": {"Image": "vault:1.15"},
            },
            {
                "Name": "/proj-env-vault-unhealthy",
                "State": {"Health": {"Status": "unhealthy"}},
                # No Config key at all -> image falls back to None.
            },
            {
                "Name": "/proj-env-vault-init",
                "State": {},  # no Health key -> no-healthcheck
                "Config": {"Image": "vault:1.15"},
            },
            {
                "Name": "/proj-env-vault-ghost",
                # No State key at all -> classify(None) -> not-found.
                "Config": {"Image": "vault:1.15"},
            },
        ]
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: inspected)

        rc, doc = _run_json(tmp_path, profile, selection, capsys)
        assert rc == 0
        assert doc["stacks"][0]["containers"] == [
            {"name": "proj-env-vault-1", "status": "healthy", "image": "vault:1.15"},
            {"name": "proj-env-vault-starting", "status": "starting", "image": "vault:1.15"},
            {"name": "proj-env-vault-unhealthy", "status": "unhealthy", "image": None},
            {"name": "proj-env-vault-init", "status": "no-healthcheck", "image": "vault:1.15"},
            {"name": "proj-env-vault-ghost", "status": "not-found", "image": "vault:1.15"},
        ]

    def test_json_document_is_the_only_thing_on_stdout(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "p", "environment_tag": "e"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])
        rc = deploy.action_status(tmp_path, profile, selection, json_output=True)
        assert rc == 0
        out = capsys.readouterr().out
        json.loads(out)  # must parse as exactly one document

    def test_profile_field_carries_the_resolved_profile_name(self, tmp_path, capsys):
        selection: list[dict] = []
        rc, doc = _run_json(tmp_path, _profile(name="core,db"), selection, capsys)
        assert rc == 0
        assert doc["profile"] == "core,db"
        assert doc["stacks"] == []

    def test_human_output_distinguishes_missing_from_not_started(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "p", "environment_tag": "e"}})
        selection = [
            {"path": "apps/vault", "name": "vault", "phase_key": "phase_1"},
            {"path": "apps/missing", "name": "missing", "phase_key": "phase_2"},
        ]
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])
        rc = deploy.action_status(tmp_path, profile, selection, json_output=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "p-e-vault" in out and "(no containers)" in out
        assert "missing" in out and "(not on disk)" in out

    def test_human_output_represents_container_name_and_status(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "p", "environment_tag": "e"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]
        monkeypatch.setattr(
            deploy.diagnose, "_inspect",
            lambda project: [{"Name": "/p-e-vault-1", "State": {"Health": {"Status": "healthy"}}}],
        )
        rc = deploy.action_status(tmp_path, profile, selection, json_output=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "p-e-vault-1=healthy" in out


# ---------------------------------------------------------------------------
# action_status — O3 / review_focus: Docker-unreachable vs "nothing running"
# ---------------------------------------------------------------------------


class TestDockerUnreachableVsEmpty:
    def test_docker_daemon_unreachable_is_not_caught_and_emptied(
        self, tmp_path, monkeypatch, capsys
    ):
        """The false-PASS attack this package exists to close: a RuntimeError
        from diagnose._inspect (Docker daemon unreachable) must propagate,
        never render as an empty/healthy-looking JSON document."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "p", "environment_tag": "e"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]

        def boom(project):
            raise RuntimeError("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")

        monkeypatch.setattr(deploy.diagnose, "_inspect", boom)

        with pytest.raises(RuntimeError, match="Docker daemon"):
            deploy.action_status(tmp_path, profile, selection, json_output=True)
        # No JSON document (or anything else) was printed on the failure path.
        assert capsys.readouterr().out == ""

    def test_zero_containers_and_daemon_unreachable_produce_different_outcomes(
        self, tmp_path, monkeypatch, capsys
    ):
        """Same stack/project, two different _inspect behaviors -> two
        DIFFERENT outcomes (a normal return vs. a raise), never collapsed."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "p", "environment_tag": "e"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]

        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])
        rc, doc = _run_json(tmp_path, profile, selection, capsys)
        assert rc == 0
        assert doc["stacks"][0]["containers"] == []

        def boom(project):
            raise RuntimeError("daemon down")

        monkeypatch.setattr(deploy.diagnose, "_inspect", boom)
        with pytest.raises(RuntimeError):
            deploy.action_status(tmp_path, profile, selection, json_output=True)

    def test_action_status_never_invokes_compose_docker(self, tmp_path, monkeypatch):
        """review_focus: no compose up/down/build/exec side effect anywhere
        in this path -- action_status never reaches procutil.docker (the
        seam every compose-mutating action goes through)."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        profile = _profile(config={"deploy": {"project_name": "p", "environment_tag": "e"}})
        selection = [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}]

        def refuse(*_a, **_kw):
            raise AssertionError("action_status must never invoke docker compose")

        monkeypatch.setattr(deploy.procutil, "docker", refuse)
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])

        rc = deploy.action_status(tmp_path, profile, selection, json_output=True)
        assert rc == 0


# ---------------------------------------------------------------------------
# cli._status — argument parsing, chain reuse, clean-error mapping
# ---------------------------------------------------------------------------


class TestCliStatusDispatch:
    def _patch_repo_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)

    def test_no_profile_flag_resolves_with_names_none(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        seen = {}
        monkeypatch.setattr(
            deploy, "resolve_profiles",
            lambda cfg, names: seen.setdefault("names", names) or deploy.profiles_pkg.Profile(),
        )
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])
        monkeypatch.setattr(deploy, "action_status", lambda *a, **k: 0)

        assert cli._status([]) == 0
        assert seen["names"] is None

    def test_repeatable_profile_flags_expand(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        seen = {}
        monkeypatch.setattr(
            deploy, "resolve_profiles",
            lambda cfg, names: seen.setdefault("names", names) or deploy.profiles_pkg.Profile(),
        )
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])
        monkeypatch.setattr(deploy, "action_status", lambda *a, **k: 0)

        assert cli._status(["--profile", "core", "--profile", "db"]) == 0
        assert seen["names"] == ["core", "db"]

    def test_comma_form_skips_empty_segments(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        seen = {}
        monkeypatch.setattr(
            deploy, "resolve_profiles",
            lambda cfg, names: seen.setdefault("names", names) or deploy.profiles_pkg.Profile(),
        )
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])
        monkeypatch.setattr(deploy, "action_status", lambda *a, **k: 0)

        assert cli._status(["--profile", "core,,db"]) == 0
        assert seen["names"] == ["core", "db"]

    def test_all_empty_segments_defaults_to_none(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        seen = {}
        monkeypatch.setattr(
            deploy, "resolve_profiles",
            lambda cfg, names: seen.setdefault("names", names) or deploy.profiles_pkg.Profile(),
        )
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])
        monkeypatch.setattr(deploy, "action_status", lambda *a, **k: 0)

        assert cli._status(["--profile", ""]) == 0
        assert seen["names"] is None

    def test_json_flag_reaches_action_status(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        monkeypatch.setattr(deploy, "resolve_profiles", lambda cfg, names: deploy.profiles_pkg.Profile())
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])
        seen = {}

        def fake_action_status(repo_root, profile, selection, *, json_output):
            seen["json_output"] = json_output
            return 0

        monkeypatch.setattr(deploy, "action_status", fake_action_status)
        assert cli._status(["--json"]) == 0
        assert seen["json_output"] is True

    def test_reuses_the_same_chain_ciu_up_uses(self, monkeypatch, tmp_path):
        """load_global_config -> resolve_profiles -> build_selection, in that
        order, feeding action_status -- the exact chain `ciu up --profile`
        uses (Context item 2)."""
        self._patch_repo_root(monkeypatch, tmp_path)
        calls: list[str] = []
        cfg_marker = {"marker": "global-cfg"}
        profile_marker = deploy.profiles_pkg.Profile(config={"deploy": {}})
        selection_marker = [{"path": "x", "name": "x", "phase_key": "phase_1"}]

        def fake_load_global_config(repo_root):
            calls.append("load_global_config")
            assert repo_root == tmp_path
            return cfg_marker

        def fake_resolve_profiles(global_cfg, names):
            calls.append("resolve_profiles")
            assert global_cfg is cfg_marker
            return profile_marker

        def fake_build_selection(profile):
            calls.append("build_selection")
            assert profile is profile_marker
            return selection_marker

        def fake_action_status(repo_root, profile, selection, *, json_output):
            calls.append("action_status")
            assert profile is profile_marker
            assert selection is selection_marker
            return 0

        monkeypatch.setattr(deploy, "load_global_config", fake_load_global_config)
        monkeypatch.setattr(deploy, "resolve_profiles", fake_resolve_profiles)
        monkeypatch.setattr(deploy, "build_selection", fake_build_selection)
        monkeypatch.setattr(deploy, "action_status", fake_action_status)

        assert cli._status([]) == 0
        assert calls == [
            "load_global_config", "resolve_profiles", "build_selection", "action_status",
        ]

    def test_docker_daemon_unreachable_is_a_clean_error_not_a_traceback(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        monkeypatch.setattr(deploy, "resolve_profiles", lambda cfg, names: deploy.profiles_pkg.Profile())
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])

        def boom(*_a, **_kw):
            raise RuntimeError("Cannot connect to the Docker daemon")

        monkeypatch.setattr(deploy, "action_status", boom)

        rc = cli._status([])
        assert rc == 2
        err = capsys.readouterr().err
        assert err.startswith("[ERROR] ciu status:")
        assert "Docker daemon" in err

    def test_config_load_failure_is_also_a_clean_error_exit_2(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)

        def boom(repo_root):
            raise ValueError("bad toml")

        monkeypatch.setattr(deploy, "load_global_config", boom)
        rc = cli._status([])
        assert rc == 2
        assert "bad toml" in capsys.readouterr().err

    def test_end_to_end_through_cli_status_with_real_action_status(
        self, monkeypatch, tmp_path, capsys
    ):
        """Full path: cli._status -> deploy.action_status (real) ->
        _stack_compose_project (mocked) -> diagnose._inspect (mocked)."""
        (tmp_path / "apps" / "vault").mkdir(parents=True)
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {"deploy": {}})
        monkeypatch.setattr(
            deploy, "resolve_profiles",
            lambda cfg, names: deploy.profiles_pkg.Profile(config={"deploy": {}}),
        )
        monkeypatch.setattr(
            deploy, "build_selection",
            lambda profile: [{"path": "apps/vault", "name": "vault", "phase_key": "phase_1"}],
        )
        monkeypatch.setattr(deploy, "_stack_compose_project", lambda *_a: "identity-vault")
        monkeypatch.setattr(deploy.diagnose, "_inspect", lambda project: [])

        rc = cli._status(["--json"])
        assert rc == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["schema_version"] == 1
        assert doc["stacks"][0]["compose_project"] == "identity-vault"


# ---------------------------------------------------------------------------
# O3 — CLI wiring: dispatch, usage, per-verb help
# ---------------------------------------------------------------------------


class TestCliStatusWiring:
    def test_main_dispatches_the_status_verb(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ciu", "status"])
        monkeypatch.setattr(cli, "_status", lambda rest: 7)
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 7

    def test_status_is_listed_in_top_level_usage(self):
        assert "status [--profile NAME] [--json]" in cli._USAGE

    def test_status_has_its_own_verb_scoped_help(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ciu", "status", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "ciu status" in out
        assert "--profile" in out and "--json" in out
        # The legacy ciu-deploy argparse surface must not leak through.
        assert "--deploy" not in out
