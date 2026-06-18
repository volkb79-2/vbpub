"""
CIU CLI argument parser tests.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu.engine import parse_arguments, _build_secrets_subparser  # noqa: E402
from ciu import cli  # noqa: E402


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

    def test_define_root_flag(self):
        args = parse_arguments(["--define-root", "/tmp/repo"])

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
        assert "--ignore-errors" in out and "-y" in out
        # The legacy ciu-deploy argparse surface must NOT leak through.
        assert "--deploy" not in out
        assert "--stop" not in out

    def test_short_flag_also_works(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["up", "-h"])
        assert "ciu up" in out and "--dir" in out and "--deploy" not in out

    def test_every_verb_has_a_help_entry(self):
        # Every dispatchable verb (except the bare top-level) is covered.
        expected = {
            "env", "render", "profiles", "up", "down", "clean",
            "health", "bake", "dev", "secrets",
        }
        assert expected <= set(cli._VERB_HELP)

    def test_env_generate_help_is_not_intercepted(self):
        # `env generate --help` must fall through to its own argparse help.
        assert cli._wants_verb_help("env", ["generate", "--help"]) is False
        # but `env --help` IS the verb's help
        assert cli._wants_verb_help("env", ["--help"]) is True

    def test_health_help_lists_preflight(self, capsys, monkeypatch):
        out = self._help_out(capsys, monkeypatch, ["health", "--help"])
        assert "--preflight" in out and "--strict" in out
