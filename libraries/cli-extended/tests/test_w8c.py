"""W8c: R2 survivor tests (boundaries, plain verbs, frozen dataclasses)."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import pkgutil
import sys
import types

import pytest
from test_workflow import _project, _run

import cli_extended
import cli_extended.identity as identity_module
from cli_extended.cli import _positive_int


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(
        sys.modules, "_cli_extended_surface_factory",
        types.ModuleType("_cli_extended_surface_factory"),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / "claude"))
    monkeypatch.setattr(identity_module, "installed_version", lambda _name: "0.1.0")


# ------------------------------------------------------- positive integer type


def test_positive_int_accepts_one_and_refuses_zero():
    assert _positive_int("1") == 1
    assert _positive_int("2") == 2
    with pytest.raises(argparse.ArgumentTypeError, match="must be an integer of at least 1"):
        _positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError, match="must be an integer of at least 1"):
        _positive_int("-1")
    with pytest.raises(argparse.ArgumentTypeError, match="invalid integer 'x'"):
        _positive_int("x")


def test_max_candidates_zero_is_an_argument_error_but_one_is_parsed(
    tmp_path, monkeypatch, capsys
):
    _project(tmp_path, monkeypatch)
    code, _out, err = _run(capsys, "surface", "sync", "--max-candidates", "0")
    assert code == 2
    assert "must be an integer of at least 1" in err
    # 1 passes argument parsing (the later refusal is the candidate limit)
    code, _out, err = _run(capsys, "surface", "sync", "--max-candidates", "1")
    assert code == 2
    assert "must be an integer of at least 1" not in err


# ------------------------------------------------------------ plain verbs


@pytest.mark.parametrize("flag", [("--json",), ("--progress", "quiet")])
@pytest.mark.parametrize("verb", ["sync", "check", "template", "pack", "report"])
def test_surface_verbs_refuse_json_and_progress(tmp_path, monkeypatch, capsys, verb, flag):
    # `--progress` is given a valid mode so only the flag's existence can fail.
    # All five verbs share the `plain` registration dict in cli.py, but a
    # mutation of either value affects every verb, so each is checked: one
    # run per (verb, flag) is cheap and also guards a verb being moved off it.
    _project(tmp_path, monkeypatch)
    code, out, err = _run(capsys, "surface", verb, *flag)
    assert code == 2
    assert out == ""
    assert f"unrecognized arguments: {' '.join(flag)}" in err


# ------------------------------------------------------ frozen dataclasses

FROZEN = {
    "cli_extended.findings": ("Finding", "FindingsFile"),
    "cli_extended.doctor": ("CheckResult", "DoctorCheck"),
    "cli_extended.config": ("CliConfig", "ProjectConfig"),
    "cli_extended.parser": ("OptionSpec", "ArgumentSpec", "VerbSpec"),
    "cli_extended.identity": ("CliIdentity",),
    "cli_extended.audit": ("AuditItem",),
    "cli_extended.constraints": ("Requires", "Conflicts", "RequiresChoice", "ResolvedConstraint"),
    "cli_extended.skills": ("_Frontmatter", "_Row", "_Action"),
    "cli_extended.surface": ("_ParserActionContext",),
    "cli_extended.review": ("ReviewCase", "ReviewCatalog", "SurfaceReport"),
}
EXPECTED = [(module, name) for module, names in FROZEN.items() for name in names]
# Deliberately mutable runtime state holders (plain `@dataclass`).
MUTABLE = {("cli_extended.parser", "CliRuntime"), ("cli_extended.parser", "RegisteredCli")}


def _all_dataclasses():
    found = set()
    for info in pkgutil.iter_modules(cli_extended.__path__):
        module = importlib.import_module(f"cli_extended.{info.name}")
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and dataclasses.is_dataclass(value)
                and value.__module__ == module.__name__
            ):
                found.add((module.__name__, name))
    return found


def test_every_dataclass_in_the_package_is_listed_here():
    # A new dataclass must be added to FROZEN (and so get the immutability
    # test) or, deliberately, to MUTABLE.
    assert _all_dataclasses() == set(EXPECTED) | MUTABLE


@pytest.mark.parametrize("module_name, class_name", sorted(MUTABLE))
def test_mutable_dataclasses_are_not_frozen(module_name, class_name):
    cls = getattr(importlib.import_module(module_name), class_name)
    assert cls.__dataclass_params__.frozen is False


@pytest.mark.parametrize("module_name, class_name", EXPECTED)
def test_listed_dataclasses_are_frozen_and_reject_assignment(module_name, class_name):
    cls = getattr(importlib.import_module(module_name), class_name)
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is True
    instance = object.__new__(cls)
    for field in dataclasses.fields(cls):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field.name, object())
    with pytest.raises(dataclasses.FrozenInstanceError):
        delattr(instance, dataclasses.fields(cls)[0].name)
