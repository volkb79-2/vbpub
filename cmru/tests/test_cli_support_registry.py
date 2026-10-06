"""W2-PKG0: the shared registry factory and the library-selector target adapter."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest
from cli_extended import SelectorList, VerbSpec
from cli_extended.identity import VersionLookupError

from cmru import cli, cli_support, output
from cmru.cli_support import (
    TargetSelectionError,
    cmru_registry,
    select_target_names,
    target_argument,
)


def _noop(args, runtime):
    return 0


def _parent_and_delegate():
    child = cmru_registry("cmru child", "A delegate.")
    child.register(VerbSpec("ping", description="Ping.", handler=_noop))
    parent = cmru_registry("cmru", "The parent.")
    parent.register(VerbSpec("child", description="Delegate.", delegate=child.build()))
    return parent, child


# --- factory -----------------------------------------------------------------


def test_registry_identity_comes_from_installed_metadata_only(monkeypatch):
    seen = []

    def fake(distribution):
        seen.append(distribution)
        return "9.8.7"

    monkeypatch.setattr("cli_extended.identity.installed_version", fake)
    registry = cmru_registry("cmru", "d")
    assert seen == ["cmru"]
    assert registry.identity.version == "9.8.7"
    assert registry.identity.name == "CMRU"
    assert registry.identity.command_name == "cmru"


def test_registry_has_no_literal_version_fallback(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    def missing(distribution):
        raise PackageNotFoundError(distribution)

    monkeypatch.setattr("cli_extended.identity.installed_version", missing)
    with pytest.raises(VersionLookupError):
        cmru_registry("cmru", "d")


def test_registry_uses_the_single_policy_constant(monkeypatch):
    assert cli_support.UNEXPECTED_EXCEPTIONS_POLICY == "raise"
    assert cmru_registry("cmru", "d").unexpected_exceptions == "raise"
    monkeypatch.setattr(cli_support, "UNEXPECTED_EXCEPTIONS_POLICY", "report")
    parent, child = _parent_and_delegate()
    assert parent.unexpected_exceptions == child.unexpected_exceptions == "report"
    parent.build()  # the library refuses a parent/delegate policy mismatch


def test_registry_refuses_caller_supplied_policy_and_identity():
    with pytest.raises(TypeError):
        cmru_registry("cmru", "d", unexpected_exceptions="report")
    with pytest.raises(TypeError):
        cmru_registry("cmru", "d", identity=object())


def test_registry_default_logger_and_passthrough_keywords():
    registry = cmru_registry("cmru x", "d", getting_started=("cmru x",), single_command=True)
    assert registry.logging_logger == "cmru"
    assert registry.getting_started == ("cmru x",)
    assert registry.single_command is True
    assert cmru_registry("cmru", "d", logging_logger="other").logging_logger == "other"


def test_registry_declares_the_time_prefix_once_and_extends_global_options():
    from cli_extended import OptionSpec

    extra = OptionSpec(("--extra",), "extra", parser_kwargs={"action": "store_true", "default": False})
    registry = cmru_registry("cmru", "d", global_options=(extra,))
    flags = [spec.flags if hasattr(spec, "flags") else spec.names for spec in registry.global_options]
    assert sum(1 for names in flags if "--log-prefix-time-short" in names) == 1
    assert any("--extra" in names for names in flags)


def test_delegate_built_with_the_factory_inherits_the_global_option(monkeypatch):
    # setenv (not delenv): it records the original state so the "1" the code under
    # test writes is undone at teardown instead of leaking into later tests.
    monkeypatch.setenv(output._TIME_ENV, "0")
    monkeypatch.setattr(output, "configure", lambda value: None)
    parent, _child = _parent_and_delegate()
    built = parent.build()
    assert built.parser.parse_args(["child"]).log_prefix_time_short is False
    delegated = built.delegates["child"]
    args = delegated.parser.parse_args(["ping", "--log-prefix-time-short"])
    assert args.log_prefix_time_short is True
    assert os.environ[output._TIME_ENV] == "1"


def test_module_entry_and_root_builders_agree_on_the_policy(monkeypatch):
    # The root goes through the factory, so flipping the one constant moves it
    # (non-vacuous: a root built with its own literal would stay "raise").
    assert cli._build_cli().unexpected_exceptions == "raise"
    monkeypatch.setattr(cli_support, "UNEXPECTED_EXCEPTIONS_POLICY", "report")
    flipped = cli._build_cli  # delegates still build their own registries
    try:
        built = flipped()
    except ValueError as mismatch:
        # Until every delegate module uses the factory the library refuses the
        # parent/delegate mismatch: that refusal IS the proof the root follows
        # the constant while an un-migrated delegate does not.
        assert "differs from" in str(mismatch)
    else:
        assert built.unexpected_exceptions == "report"


# --- selector ----------------------------------------------------------------

PROJECTS = {"zeta": 1, "alpha": 2, "mid": 3}
ORDER = ["zeta", "alpha", "mid"]


def _argument_parser():
    spec = target_argument()
    parser = argparse.ArgumentParser(prog="t")
    parser.add_argument(spec.name, **dict(spec.parser_kwargs))
    return spec, parser


def test_target_argument_shape_and_parse_results():
    spec, parser = _argument_parser()
    assert spec.metavar == "[all|PROJECT[,PROJECT...]]"
    assert isinstance(spec.parser_kwargs["type"], SelectorList)
    assert parser.parse_args([]).target is None
    assert parser.parse_args(["all"]).target is SelectorList.ALL
    assert parser.parse_args(["mid,zeta"]).target == ("mid", "zeta")


def _legacy(raw, *, projects=PROJECTS, order=ORDER, context=None, monkeypatch=None):
    """cli._select_projects (explicit target: no discovery) for the failure paths."""
    if raw is None:
        from types import SimpleNamespace

        monkeypatch.setattr(
            cli, "resolve_invocation_context",
            lambda path=None: SimpleNamespace(project_name=context),
        )
    return cli._select_projects(Path("cmru.toml"), raw, projects, order)


# Literal expectations, derived from the pre-change behaviour
# (``git show 839e8841c:cmru/src/cmru/cli_support.py``): matching is
# case-sensitive, ``all`` (also padded) means the whole declared order, and any
# named subset comes back in DECLARED order, never the order given.
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("all", ["zeta", "alpha", "mid"]),
        (" all ", ["zeta", "alpha", "mid"]),
        ("mid", ["mid"]),
        ("mid,zeta", ["zeta", "mid"]),
        ("zeta,mid,alpha", ["zeta", "alpha", "mid"]),
        ("alpha, mid", ["alpha", "mid"]),
        ("mid,alpha,zeta", ["zeta", "alpha", "mid"]),
    ],
)
def test_adapter_selection_matches_the_recorded_legacy_results(raw, expected):
    assert select_target_names(SelectorList()(raw), PROJECTS, ORDER) == expected
    assert select_target_names(raw, PROJECTS, ORDER) == expected  # legacy string still works
    assert _legacy(raw) == expected


@pytest.mark.parametrize("raw", ["All", "ALL"])
def test_all_is_case_sensitive_so_other_spellings_are_unknown_projects(raw):
    # Legacy: "All"/"ALL" are project names, hence "unknown project(s)".
    with pytest.raises(TargetSelectionError, match=f"unknown project.*{raw}"):
        select_target_names(SelectorList()(raw), PROJECTS, ORDER)
    with pytest.raises(TargetSelectionError, match=f"unknown project.*{raw}"):
        select_target_names(raw, PROJECTS, ORDER)


def test_all_with_a_registry_project_absent_from_the_order_returns_only_ordered_ones():
    projects = {"alpha": 1, "extra": 2}
    # Legacy: ``all`` is the project_order filtered by the registry; "extra" is
    # loaded but not orchestrated, so it is never selected by ``all``.
    assert select_target_names(SelectorList.ALL, projects, ["alpha"]) == ["alpha"]
    # Legacy quirk kept: naming it selects nothing, because results are filtered
    # through the declared order.
    assert select_target_names(("extra",), projects, ["alpha"]) == []


def test_target_argument_accepts_a_non_default_name_and_description():
    spec = target_argument("pick projects", name="projects")
    parser = argparse.ArgumentParser(prog="t")
    parser.add_argument(spec.name, **dict(spec.parser_kwargs))
    assert spec.name == "projects" and spec.description == "pick projects"
    assert parser.parse_args(["mid,zeta"]).projects == ("mid", "zeta")
    assert parser.parse_args([]).projects is None


def test_adapter_all_keeps_declared_order_not_given_order():
    assert select_target_names(SelectorList.ALL, PROJECTS, ORDER) == ["zeta", "alpha", "mid"]
    assert select_target_names(("mid", "zeta"), PROJECTS, ORDER) == ["zeta", "mid"]


def test_adapter_omitted_target_uses_context_or_the_whole_estate(monkeypatch):
    assert select_target_names(None, PROJECTS, ORDER) == ORDER
    assert select_target_names(None, PROJECTS, ORDER, context_project="alpha") == ["alpha"]
    assert _legacy(None, context="alpha", monkeypatch=monkeypatch) == ["alpha"]
    assert _legacy(None, context=None, monkeypatch=monkeypatch) == ORDER


def test_legacy_string_parser_keeps_its_contract():
    assert cli_support.parse_target_names(None) is None
    assert cli_support.parse_target_names(" a , b ") == ["a", "b"]
    assert cli_support.parse_target_names("all") == ["all"]


def test_adapter_explicit_all_beats_the_context_project():
    # ALL inside a project must still mean every project, unlike an omitted target.
    assert select_target_names(
        SelectorList.ALL, PROJECTS, ORDER, context_project="alpha"
    ) == ORDER
    assert select_target_names("all", PROJECTS, ORDER, context_project="alpha") == ORDER


def test_adapter_all_ignores_orchestration_names_not_in_the_registry():
    assert select_target_names(SelectorList.ALL, {"alpha": 1}, ["ghost", "alpha"]) == ["alpha"]


@pytest.mark.parametrize("raw", ["ghost", "mid,ghost"])
def test_adapter_rejects_unknown_names_like_the_legacy_path(raw, capsys):
    with pytest.raises(TargetSelectionError, match="unknown project"):
        select_target_names(SelectorList()(raw), PROJECTS, ORDER)
    with pytest.raises(SystemExit):  # the oracle reports and exits with a config error
        _legacy(raw)
    assert "unknown project(s): ghost" in capsys.readouterr().err


@pytest.mark.parametrize(
    "raw",
    [("mid", "mid"), ("",), ("all", "mid"), ("mid", "")],
)
def test_adapter_still_refuses_malformed_programmatic_tuples(raw):
    with pytest.raises(TargetSelectionError):
        select_target_names(raw, PROJECTS, ORDER)


def test_adapter_empty_tuple_is_not_an_everything_selector():
    # An empty explicit selection selects nothing rather than silently widening.
    assert select_target_names((), PROJECTS, ORDER) == []


@pytest.mark.parametrize("raw", ["mid,mid", "a,", "all,mid"])
def test_library_parse_refuses_what_the_legacy_parser_refused(raw):
    # Structural errors now surface at argparse time (usage exit 2).
    with pytest.raises(argparse.ArgumentTypeError):
        SelectorList()(raw)
    with pytest.raises(SystemExit):
        _legacy(raw)
