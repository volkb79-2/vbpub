"""ciu-P17 / CIU-QOL-7 — unified `ciu bake` (`--profile NAME` selection model).

Oracles under test:
- O1 is dead-code removal in `deploy.py`; not exercised here (see the LOG for
  the grep evidence and the reason it is NOT shipped in this package).
- O2: `ciu bake --profile NAME` resolves targets via the SAME chain `ciu up
  --profile` uses (`load_global_config` -> `resolve_profiles` ->
  `build_selection`, then `deploy.collect_bake_targets_from_selection`), fed
  into the SAME `docker buildx bake ... --load` invocation the no-profile
  path already builds. `ciu bake [targets ...]` with NO `--profile` is
  byte-identical to the pre-existing v1 behaviour.
- O3: `--profile` and explicit positional targets are mutually exclusive,
  prefix-aware (catches `--profile=NAME`, not just `--profile NAME`);
  `--no-cache` combines with either mode unchanged.
- O4 is documentation-only; not exercised here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import ciu.engine as _engine  # noqa: E402
from ciu import cli  # noqa: E402
from ciu import deploy  # noqa: E402
from ciu import dev  # noqa: E402

_REVISION_ARGS = ["--set", "*.labels.org.opencontainers.image.revision=abc12345"]


@pytest.fixture(autouse=True)
def _pin_git_hash(monkeypatch):
    """Every buildx argv includes a provenance stamp (engine.bake_revision_args);
    pin it so assertions on the exact argv are deterministic."""
    monkeypatch.setattr(_engine, "get_git_hash", lambda: "abc12345")


class TestCliBakeNoProfileRegressionBar:
    """`ciu bake` with NO `--profile` must be byte-identical to the
    pre-existing v1 behaviour -- this is an ADDITIVE flag, not a
    replacement (O2's regression bar)."""

    def test_no_targets_no_flags_bakes_all(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 0)

        assert cli._bake([]) == 0
        assert seen == [["docker", "buildx", "bake", "all", "--load", *_REVISION_ARGS]]

    def test_explicit_targets_pass_through_unchanged_and_unsorted(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 0)

        assert cli._bake(["zeta", "alpha"]) == 0
        assert seen == [["docker", "buildx", "bake", "zeta", "alpha", "--load", *_REVISION_ARGS]]

    def test_no_cache_combines_with_no_profile_mode(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 3)

        assert cli._bake(["api", "--no-cache"]) == 3
        assert seen == [["docker", "buildx", "bake", "api", "--load", *_REVISION_ARGS, "--no-cache"]]

    def test_dispatches_through_main_with_the_same_argv_shape(self, monkeypatch):
        """End-to-end wiring proof: `ciu bake` (no --profile) via the real
        `main()` verb dispatch produces the exact same argv/exit-code contract
        as before this package (mirrors the pre-existing
        test_ciu_cli_parser.py::test_bake_constructs_default_and_no_cache_argv)."""
        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 3)
        monkeypatch.setattr(sys, "argv", ["ciu", "bake", "--no-cache"])

        with pytest.raises(SystemExit) as raised:
            cli.main()

        assert raised.value.code == 3
        assert seen == [["docker", "buildx", "bake", "all", "--load", *_REVISION_ARGS, "--no-cache"]]


class TestCliBakeProfileFlagConflict:
    """O3: --profile and explicit positional targets are mutually exclusive,
    prefix-aware (the `up --layout` B2 precedent's fix -- also catches the
    `--profile=NAME` equals form, not just the space form)."""

    def test_space_form_conflicts_with_a_positional_target(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli.subprocess, "call",
            lambda argv: (_ for _ in ()).throw(AssertionError("must not bake when rejected")),
        )

        assert cli._bake(["--profile", "core", "extra-target"]) == 2
        err = capsys.readouterr().err
        assert "ciu bake:" in err
        assert "mutually exclusive" in err

    def test_equals_form_also_conflicts_with_a_positional_target(self, monkeypatch, capsys):
        """The exact bug class fixed once already in the `up --layout`
        precedent (checkpoint C): a bare `"--profile" in rest` membership
        check would MISS `--profile=core` and silently treat it as a raw
        buildx target instead of rejecting it."""
        monkeypatch.setattr(
            cli.subprocess, "call",
            lambda argv: (_ for _ in ()).throw(AssertionError("must not bake when rejected")),
        )

        assert cli._bake(["--profile=core", "extra-target"]) == 2
        err = capsys.readouterr().err
        assert "ciu bake:" in err
        assert "mutually exclusive" in err

    def test_positional_target_before_the_profile_flag_still_conflicts(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli.subprocess, "call",
            lambda argv: (_ for _ in ()).throw(AssertionError("must not bake when rejected")),
        )

        assert cli._bake(["extra-target", "--profile=core"]) == 2
        assert "mutually exclusive" in capsys.readouterr().err


class TestCliBakeProfileResolution:
    """O2: --profile resolves targets via the SAME chain `ciu up --profile`
    uses, feeding the SAME buildx invocation the no-profile path builds."""

    @staticmethod
    def _patch_repo_root(monkeypatch, tmp_path):
        monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)

    def test_resolves_via_the_same_chain_ciu_up_uses_in_order(self, monkeypatch, tmp_path):
        """load_global_config -> resolve_profiles -> build_selection, in
        that order, then the REAL collect_bake_targets_from_selection over
        whatever build_selection returns -- proving `ciu bake --profile X`
        would build exactly the images `ciu up --profile X` deploys."""
        self._patch_repo_root(monkeypatch, tmp_path)
        calls: list[str] = []
        cfg_marker = {"marker": "global-cfg"}
        profile_marker = deploy.profiles_pkg.Profile(config={"deploy": {}})
        # Mirrors the deleted internal action_build's own fixture shape
        # (applications/tools targets kept, other top-level dirs dropped).
        selection_marker = [
            {"path": "applications/api"},
            {"path": "tools/admin"},
            {"path": "infrastructure/network"},
        ]

        def fake_load_global_config(repo_root):
            calls.append("load_global_config")
            assert repo_root == tmp_path
            return cfg_marker

        def fake_resolve_profiles(global_cfg, names):
            calls.append("resolve_profiles")
            assert global_cfg is cfg_marker
            assert names == ["core"]
            return profile_marker

        def fake_build_selection(profile):
            calls.append("build_selection")
            assert profile is profile_marker
            return selection_marker

        monkeypatch.setattr(deploy, "load_global_config", fake_load_global_config)
        monkeypatch.setattr(deploy, "resolve_profiles", fake_resolve_profiles)
        monkeypatch.setattr(deploy, "build_selection", fake_build_selection)
        # collect_bake_targets_from_selection is deliberately left REAL.

        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 0)

        assert cli._bake(["--profile", "core"]) == 0
        assert calls == ["load_global_config", "resolve_profiles", "build_selection"]
        assert seen == [["docker", "buildx", "bake", "admin", "api", "--load", *_REVISION_ARGS]]

    def test_empty_buildable_selection_defaults_to_all(self, monkeypatch, tmp_path):
        """A profile with no applications/tools targets still performs the
        documented all-target bake (the same fallback the no-profile path
        and the deleted action_build both used)."""
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        monkeypatch.setattr(deploy, "resolve_profiles", lambda cfg, names: deploy.profiles_pkg.Profile())
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])

        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 0)

        assert cli._bake(["--profile", "core"]) == 0
        assert seen == [["docker", "buildx", "bake", "all", "--load", *_REVISION_ARGS]]

    def test_no_cache_combines_with_profile_mode(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        monkeypatch.setattr(deploy, "resolve_profiles", lambda cfg, names: deploy.profiles_pkg.Profile())
        monkeypatch.setattr(
            deploy, "build_selection", lambda profile: [{"path": "applications/api"}],
        )

        seen = []
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 0)

        assert cli._bake(["--profile", "core", "--no-cache"]) == 0
        assert seen == [
            ["docker", "buildx", "bake", "api", "--load", *_REVISION_ARGS, "--no-cache"]
        ]

    def test_repeatable_profile_flags_expand(self, monkeypatch, tmp_path):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})
        seen = {}
        monkeypatch.setattr(
            deploy, "resolve_profiles",
            lambda cfg, names: seen.setdefault("names", names) or deploy.profiles_pkg.Profile(),
        )
        monkeypatch.setattr(deploy, "build_selection", lambda profile: [])
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: 0)

        assert cli._bake(["--profile", "core", "--profile", "db"]) == 0
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
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: 0)

        assert cli._bake(["--profile", "core,,db"]) == 0
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
        monkeypatch.setattr(cli.subprocess, "call", lambda argv: 0)

        assert cli._bake(["--profile", ""]) == 0
        assert seen["names"] is None

    def test_config_load_failure_is_a_clean_error_not_a_traceback(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)

        def boom(repo_root):
            raise ValueError("bad toml")

        monkeypatch.setattr(deploy, "load_global_config", boom)
        monkeypatch.setattr(
            cli.subprocess, "call",
            lambda argv: (_ for _ in ()).throw(AssertionError("must not bake after a config error")),
        )

        rc = cli._bake(["--profile", "core"])
        assert rc == 2
        err = capsys.readouterr().err
        assert err.startswith("[ERROR] ciu bake:")
        assert "bad toml" in err

    def test_profile_resolution_runtime_error_is_also_a_clean_error(
        self, monkeypatch, tmp_path, capsys
    ):
        self._patch_repo_root(monkeypatch, tmp_path)
        monkeypatch.setattr(deploy, "load_global_config", lambda repo_root: {})

        def boom(cfg, names):
            raise RuntimeError("no such profile")

        monkeypatch.setattr(deploy, "resolve_profiles", boom)
        monkeypatch.setattr(
            cli.subprocess, "call",
            lambda argv: (_ for _ in ()).throw(AssertionError("must not bake after a resolution error")),
        )

        rc = cli._bake(["--profile", "core"])
        assert rc == 2
        assert "no such profile" in capsys.readouterr().err
