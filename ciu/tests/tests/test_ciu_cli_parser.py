"""
CIU CLI argument parser tests.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu.engine import parse_arguments, _build_secrets_subparser  # noqa: E402
from ciu import cli, dev, worktree  # noqa: E402
from ciu.cli_registry import build_cli  # noqa: E402


@pytest.fixture(autouse=True)
def _keep_bake_parser_tests_on_the_primary_contract(monkeypatch):
    monkeypatch.setattr(
        dev, "resolve_repo_root",
        lambda *_args, **_kwargs: Path(__file__).resolve().parents[2],
    )
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix", lambda *_a, **_kw: None
    )


def test_all_ciu_parser_diagnostics_start_with_the_dynamic_headline(monkeypatch, capsys):
    import ciu.cli_utils as cli_utils

    monkeypatch.setattr(cli_utils, "get_cli_version", lambda: "9.8.7-test")

    with pytest.raises(SystemExit):
        parse_arguments(["--not-a-ciu-option"])
    assert capsys.readouterr().err.splitlines()[0] == (
        "CIU 9.8.7-test — Container Infrastructure Utility"
    )

    with pytest.raises(SystemExit):
        parse_arguments(["--help"])
    assert capsys.readouterr().out.splitlines()[0] == (
        "CIU 9.8.7-test — Container Infrastructure Utility"
    )


class TestParseArgumentsDefaults:
    def test_default_values(self):
        args = parse_arguments([])

        assert args.dir == Path.cwd()
        assert args.file == "ciu.compose.yml.j2"
        assert args.dry_run is False
        assert args.render_toml is False
        assert args.print_context is False
        assert args.skip_hostdir_check is False
        assert args.skip_hooks is False
        assert args.skip_secrets is False
        assert args.generate_env is False
        assert args.yes is False
        assert args.reset is False
        assert args.define_root is None
        assert args.auto_connect_network is None


class TestParseArgumentsFlags:
    def test_dir_and_file_flags(self):
        args = parse_arguments(["-d", "/tmp/service", "-f", "custom.yml.j2"])

        assert args.dir == Path("/tmp/service")
        assert args.file == "custom.yml.j2"

    def test_root_folder_flag(self):
        args = parse_arguments(["--root-folder", "/tmp/repo"])

        assert args.define_root == Path("/tmp/repo")

    def test_root_folder_alias(self):
        args = parse_arguments(["--root-folder", "/tmp/repo"])

        assert args.define_root == Path("/tmp/repo")

    def test_boolean_flags(self):
        args = parse_arguments([
            "--dry-run",
            "--print-context",
            "--render-toml",
            "--skip-hostdir-check",
            "--skip-hooks",
            "--skip-secrets",
            "--generate-env",
            "--reset",
            "-y",
        ])

        assert args.dry_run is True
        assert args.print_context is True
        assert args.render_toml is True
        assert args.skip_hostdir_check is True
        assert args.skip_hooks is True
        assert args.skip_secrets is True
        assert args.generate_env is True
        assert args.reset is True
        assert args.yes is True
        assert args.auto_connect_network is None

    def test_auto_connect_flags(self):
        args = parse_arguments(["--auto-connect-network"])
        assert args.auto_connect_network is True

        args = parse_arguments(["--no-auto-connect-network"])
        assert args.auto_connect_network is False

    def test_secrets_flag_defaults_false(self):
        args = parse_arguments([])
        assert args.secrets is False

    def test_reset_with_secrets_flag(self):
        args = parse_arguments(["--reset", "--secrets", "-y"])
        assert args.reset is True
        assert args.secrets is True
        assert args.yes is True

    def test_version_flag_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_arguments(["--version"])

        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "ciu " in captured.out


class TestParseArgumentsEdgeCases:
    def test_relative_directory(self):
        args = parse_arguments(["-d", "./services/postgres"])

        assert args.dir == Path("./services/postgres")

    def test_duplicate_flag_last_wins(self):
        args = parse_arguments(["-d", "/tmp", "-d", "/opt"])

        assert args.dir == Path("/opt")


class TestParseArgumentsHelp:
    def test_has_docstring(self):
        assert parse_arguments.__doc__ is not None
        assert "arguments" in parse_arguments.__doc__.lower()


class TestSecretsSubcommand:
    def test_secrets_list_parses(self):
        sub = _build_secrets_subparser().parse_args(["list", "-d", "/tmp/stack"])
        assert sub.action == "list"
        assert sub.dir == Path("/tmp/stack")
        assert sub.yes is False
        assert sub.name is None

    def test_secrets_reset_with_name_and_yes(self):
        sub = _build_secrets_subparser().parse_args(
            ["reset", "--name", "redis_password", "-y"]
        )
        assert sub.action == "reset"
        assert sub.name == "redis_password"
        assert sub.yes is True

    def test_secrets_invalid_action_rejected(self):
        with pytest.raises(SystemExit):
            _build_secrets_subparser().parse_args(["delete"])

    def test_secrets_define_root_alias(self):
        sub = _build_secrets_subparser().parse_args(["list", "--root-folder", "/r"])
        assert sub.define_root == Path("/r")


class TestPerVerbHelp:
    """CIU-7 / S10.1 — `ciu <verb> --help` is verb-scoped, not the legacy surface."""

    def _help_out(self, capsys, monkeypatch, argv: list[str]) -> str:
        monkeypatch.setattr(sys, "argv", ["ciu", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        return capsys.readouterr().out

    def test_clean_help_shows_clean_options_not_legacy(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["clean", "--help"])
        assert "ciu clean" in out
        assert "--ignore-errors" in out and "--yes" in out
        # The legacy ciu-deploy argparse surface must NOT leak through.
        assert "--deploy" not in out
        assert "--stop" not in out

    def test_short_help_flag_is_rejected(self, capsys, monkeypatch):
        """The registry has long options only: `-h` is not a spelling of `--help`."""
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "-h"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        assert "unrecognized" in capsys.readouterr().err.lower()

    def test_up_help_lists_the_shared_actions(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["up", "--help"])
        assert "ciu up" in out and "--dir" in out
        # `--stop` is a legacy ciu-deploy action flag that must not leak through;
        # CIU-68: `--deploy`/`--healthcheck` are discoverable ON PURPOSE.
        assert "--stop" not in out
        assert "--deploy" in out and "--healthcheck" in out

    def test_every_verb_has_a_help_entry(self):
        # Every public verb is registered, so help is generated for each of them.
        expected = {
            "env", "render", "show", "up", "down", "clean",
            "status", "diagnose", "bake", "dev", "secrets", "governance",
            "check", "graph", "ssh", "migrate", "schema", "doctor", "skills",
        }
        registered = {verb.name for verb in build_cli().registered_verbs}
        assert expected <= registered
        # The renamed-away spellings are gone, not aliased.
        assert not ({"health", "profiles", "layouts", "ksm", "iops-baseline",
                     "capabilities", "migration-check", "host-secrets"} & registered)

    @pytest.mark.parametrize(
        "verb, expected_text",
        [
            ("diagnose", "--json"),
            ("check", "--live"),
            ("graph", "--format"),
            ("ssh", "--admin"),
        ],
    )
    def test_every_dispatchable_help_screen_is_specific(
        self, capsys, monkeypatch, verb: str, expected_text: str
    ):
        out = self._help_out(capsys, monkeypatch, [verb, "--help"])
        assert f"ciu {verb}" in out
        assert expected_text in out
        assert out.splitlines()[0].startswith("CIU ")
        assert "Container Infrastructure Utility" in out.splitlines()[0]

    def test_env_generate_help_is_its_own_screen(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["env", "generate", "--help"])
        assert "ciu env generate" in out and "--root-folder" in out
        # a bare group prints the group's verbs
        out = self._help_out(capsys, monkeypatch, ["env", "--help"])
        assert "generate" in out and "print" in out

    def test_status_help_lists_the_absorbed_health_modes(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["status", "--help"])
        assert "--gate" in out and "--preflight" in out and "--strict" in out

    def test_iops_baseline_help_shows_own_options(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["governance", "iops-baseline", "--help"])
        assert "ciu governance iops-baseline" in out
        assert "--path" in out and "--runtime" in out and "--force" in out
        assert "saturating" in " ".join(out.lower().split())
        assert "--deploy" not in out

    @pytest.mark.parametrize("argv", [["--help"], ["worktree", "--help"]])
    def test_worktree_help_lists_the_managed_lifecycle(self, capsys, monkeypatch, argv):
        """Both public help surfaces make the managed workflow discoverable."""
        out = self._help_out(capsys, monkeypatch, argv)
        assert "worktree" in out
        if argv == ["worktree", "--help"]:
            for verb in ("create", "ensure", "adopt", "up", "exec", "rm", "inspect", "lease",
                         "refork", "branches", "reap", "list"):
                assert verb in out, verb

    def test_worktree_create_help_teaches_exact_base_and_no_deploy(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["worktree", "create", "--help"])
        text = " ".join(out.split())
        for guidance in (
            "LOGICAL", "--base REF", "--profile P1,P2", "exact commit SHA", "does NOT deploy",
            'ciu worktree create pkg-under-test --base "$(git rev-parse HEAD)" --json',
        ):
            assert guidance in text, guidance

    def test_worktree_rm_help_teaches_branch_safety(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["worktree", "rm", "--help"])
        text = " ".join(out.split())
        for guidance in (
            "--keep-branch | --delete-branch", "--discard-protected-state BRANCH",
            "keeps an unmerged branch tip by default",
            "ciu worktree rm pkg-under-test --yes",
        ):
            assert guidance in text, guidance


class TestIopsBaselineVerb:
    """S15.9 — `ciu iops-baseline` dispatch and argument validation."""

    def _run(self, monkeypatch, argv: list[str]) -> int:
        monkeypatch.setattr(sys, "argv", ["ciu", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        return exc.value.code

    def test_dispatch_forwards_path_runtime_force(self, monkeypatch):
        calls = {}

        def fake_run(output_path, *, runtime_s, force):
            calls["args"] = (output_path, runtime_s, force)
            return 0

        import ciu.governance as gov
        monkeypatch.setattr(gov, "run_iops_baseline", fake_run)
        code = self._run(
            monkeypatch,
            ["governance", "iops-baseline", "--path", "/tmp/x.env", "--runtime", "5", "--force"],
        )
        assert code == 0
        assert calls["args"] == (Path("/tmp/x.env"), 5, True)

    def test_defaults(self, monkeypatch):
        calls = {}

        def fake_run(output_path, *, runtime_s, force):
            calls["args"] = (output_path, runtime_s, force)
            return 0

        import ciu.governance as gov
        monkeypatch.setattr(gov, "run_iops_baseline", fake_run)
        code = self._run(monkeypatch, ["governance", "iops-baseline"])
        assert code == 0
        assert calls["args"] == (None, 10, False)

    def test_nonpositive_runtime_exits_2(self, monkeypatch, capsys):
        code = self._run(monkeypatch, ["governance", "iops-baseline", "--runtime", "0"])
        assert code == 2
        assert capsys.readouterr().err.splitlines()[0].startswith("CIU ")

    def test_init_invalid_project_name_uses_headline(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ciu", "init", "--project-name", "BAD"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert capsys.readouterr().err.splitlines()[0].startswith("CIU ")


class TestLocalCliDispatch:
    """The public CLI's local, Docker-free dispatch boundary.

    These deliberately replace only the immediate command handlers.  They test
    the argv and exit-code contract of ``ciu`` without accidentally exercising
    a Docker daemon, an SSH transport, or a real workspace configuration.
    """

    @staticmethod
    def _run(monkeypatch, argv: list[str]) -> int:
        monkeypatch.setattr(sys, "argv", ["ciu", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        return exc.value.code

    def test_bare_env_is_a_group_not_the_removed_ciu_env_dump(self, monkeypatch, tmp_path, capsys):
        """C11/F8: the deprecated `ciu env` dump is gone; `env` is the generate|print group."""
        (tmp_path / "ciu.env").write_text("REPO_ROOT=/repo\n")
        monkeypatch.chdir(tmp_path)

        assert self._run(monkeypatch, ["env"]) == 0
        captured = capsys.readouterr()
        assert "REPO_ROOT" not in captured.out
        assert "generate" in captured.out and "print" in captured.out

    def test_env_generate_forwards_explicit_root_and_cwd(self, monkeypatch, tmp_path):
        import ciu.deploy as deploy

        seen = {}

        def fake_generate(define_root, cwd):
            seen["args"] = (define_root, cwd)
            return 7

        monkeypatch.setattr(deploy, "action_generate_env", fake_generate)
        monkeypatch.chdir(tmp_path)

        assert self._run(monkeypatch, ["env", "generate", "--root-folder", "/repo"]) == 7
        assert seen["args"] == (Path("/repo"), tmp_path)

    def test_iops_runtime_rejects_negative_values(self, monkeypatch, capsys):
        assert self._run(monkeypatch, ["governance", "iops-baseline", "--runtime", "-1"]) == 2
        assert "positive integer" in capsys.readouterr().err

    def test_diagnose_validates_log_range_before_handler(self, monkeypatch, capsys):
        import ciu.diagnose as diagnose

        monkeypatch.setattr(diagnose, "run", lambda **_: pytest.fail("handler must not run"))

        assert self._run(monkeypatch, ["diagnose", "--logs", "10001"]) == 2
        assert capsys.readouterr().err.splitlines()[0].startswith("CIU ")

    def test_diagnose_forwards_all_public_options(self, monkeypatch):
        import ciu.diagnose as diagnose

        seen = {}

        def fake_run(**kwargs):
            seen.update(kwargs)
            return 4

        monkeypatch.setattr(diagnose, "run", fake_run)

        assert self._run(monkeypatch, ["diagnose", "--project", "demo", "--logs", "0", "--json"]) == 4
        assert seen == {"project": "demo", "log_lines": 0, "json_output": True}

    def test_bake_constructs_default_and_no_cache_argv(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 3)
        # Pin the revision so the provenance label is deterministic here.
        import ciu.engine as _engine
        monkeypatch.setattr(_engine, "get_git_hash", lambda: "abc12345")

        assert self._run(monkeypatch, ["bake", "--no-cache"]) == 3
        assert seen == [["docker", "buildx", "bake", "all", "--load",
                         "--set", "*.labels.org.opencontainers.image.revision=abc12345", "--no-cache"]]

    @pytest.mark.parametrize(
        ("verb", "rest", "expected"),
        [
            ("render", ["--profile", "prod"], ["--render-toml", "--profile", "prod"]),
            ("show", ["profiles"], ["--list-profiles"]),
            ("clean", ["--yes"], ["--clean", "--yes"]),
            ("check", ["--live"], ["--check", "--live"]),
            ("graph", ["--json"], ["--graph", "--format", "json"]),
            ("graph", ["--format", "dot"], ["--graph", "--format", "dot"]),
            ("status", ["--gate"], ["--healthcheck"]),
            ("status", ["--preflight", "--strict"], ["--preflight", "--strict"]),
        ],
    )
    def test_local_verbs_map_to_the_documented_handler_argv(
        self, monkeypatch, verb, rest, expected
    ):
        import ciu.deploy as deploy

        seen = []
        monkeypatch.setattr(deploy, "main", lambda argv: seen.append(argv) or 6)

        assert self._run(monkeypatch, [verb, *rest]) == 6
        assert seen == [expected]

    def test_dash_d_is_not_a_verb_and_is_not_aliased(self, monkeypatch, capsys):
        """The flat `ciu -d DIR` form is not a public surface and has no alias (F5)."""
        assert self._run(monkeypatch, ["-d", "services/demo"]) == 2
        err = capsys.readouterr().err.lower()
        assert "unrecognized" in err or "invalid choice" in err

    def test_abbreviated_spellings_are_usage_errors(self, monkeypatch, capsys):
        """F5: option prefixes are not accepted; nothing reaches the implementation layer."""
        import ciu.deploy as deploy

        monkeypatch.setattr(deploy, "main", lambda argv: pytest.fail("must not dispatch"))
        assert self._run(monkeypatch, ["up", "--prof", "core"]) == 2
        assert "--prof" in capsys.readouterr().err
