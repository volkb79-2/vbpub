"""W2-INTEG: cross-package seams of the Wave 2 CLI redesign.

Covers the section D environment renames (and the child-only reader), the single
``REFUSED`` exit-code name, the ``cmru[interactive]`` hint, and the estate-wide
guard that every project contract's ``cmru`` argv still parses against the
registry (a removed flag in one project's ``cmru.toml`` breaks that project's
release at runtime, not at review time).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, cli_support, exit_codes, output, scaffold, transaction

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent

OLD_ENV_NAMES = (
    "CMRU_BIN", "CMRU_RUN_LOG", "CMRU_SHOW_RUN_DETAILS", "CMRU_LOG_APPEND",
    "CMRU_LOG_PREFIX_TIME_SHORT",
)
INTERNAL_ENV_NAMES = tuple(
    name.replace("CMRU_", "CMRU_INTERNAL_", 1) for name in OLD_ENV_NAMES
)


def _clean_env(monkeypatch) -> None:
    for name in (*OLD_ENV_NAMES, *INTERNAL_ENV_NAMES, transaction.CHILD_ENV):
        monkeypatch.delenv(name, raising=False)


# --- D: internal environment names -------------------------------------------


def test_the_internal_names_are_exactly_the_renamed_old_names():
    assert transaction.INTERNAL_BIN_ENV == "CMRU_INTERNAL_BIN"
    assert output._TIME_ENV == "CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT"
    assert INTERNAL_ENV_NAMES == (
        "CMRU_INTERNAL_BIN", "CMRU_INTERNAL_RUN_LOG", "CMRU_INTERNAL_SHOW_RUN_DETAILS",
        "CMRU_INTERNAL_LOG_APPEND", "CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT",
    )


def test_writers_set_only_the_internal_names(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(tmp_path / "release.log"))
    monkeypatch.setattr(output, "configure", lambda _time_short: None)

    cli._apply_output_options(SimpleNamespace(show_run_details=True, log_append=True))
    cli._prepare_native_release_log(tmp_path, append=True)
    cli._prepare_native_release_log(tmp_path, append=False)
    output.enable_short_time_prefix()

    assert os.environ["CMRU_INTERNAL_SHOW_RUN_DETAILS"] == "1"
    assert os.environ["CMRU_INTERNAL_RUN_LOG"] == str((tmp_path / "release.log").resolve())
    assert os.environ["CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT"] == "1"
    assert "CMRU_INTERNAL_LOG_APPEND" not in os.environ  # the last call was a fresh log
    for old in OLD_ENV_NAMES:
        assert old not in os.environ, f"{old} is still written"

    cli._prepare_native_release_log(tmp_path, append=True)
    assert os.environ["CMRU_INTERNAL_LOG_APPEND"] == "1"
    assert "CMRU_LOG_APPEND" not in os.environ


def test_the_launcher_variable_is_ignored_outside_a_transaction_child(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_INTERNAL_BIN", "/evil/cmru")
    # No CMRU_RELEASE_TRANSACTION_CHILD: the real predicate says "not a child".
    assert transaction.is_transaction_child(tmp_path) is False
    assert transaction.internal_launcher(tmp_path) is None


def test_the_launcher_variable_is_honoured_inside_a_verified_child(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_INTERNAL_BIN", "  /bound/cmru  ")
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _root: True)
    assert transaction.internal_launcher(tmp_path) == "/bound/cmru"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_unset_or_blank_launcher_variable_is_none(monkeypatch, tmp_path, value):
    _clean_env(monkeypatch)
    if value is not None:
        monkeypatch.setenv("CMRU_INTERNAL_BIN", value)
    monkeypatch.setattr(
        transaction, "is_transaction_child",
        lambda _root: pytest.fail("a blank launcher must not even ask the predicate"),
    )
    assert transaction.internal_launcher(tmp_path) is None


def test_an_inconsistent_child_context_fails_closed(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_INTERNAL_BIN", "/evil/cmru")
    monkeypatch.setenv(transaction.CHILD_ENV, "1")  # child marker without its context
    with pytest.raises(RuntimeError, match="incomplete"):
        transaction.is_transaction_child(tmp_path)
    assert transaction.internal_launcher(tmp_path) is None


def _workspace(tmp_path):
    return SimpleNamespace(
        path=tmp_path, branch="cmru-release-test", base="a" * 40,
        workspace_id=None, repo_root=tmp_path,
    )


def _capture_run(monkeypatch, observed):
    monkeypatch.setattr(
        transaction.subprocess, "run",
        lambda argv, *, cwd, env: observed.update(argv=argv) or SimpleNamespace(returncode=0),
    )


def test_run_child_ignores_the_variable_and_the_old_name_outside_a_child(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_INTERNAL_BIN", "/evil/cmru")
    monkeypatch.setenv("CMRU_BIN", "/old/evil/cmru")
    monkeypatch.setattr(transaction.shutil, "which", lambda _name: "/found/cmru")
    observed: dict = {}
    _capture_run(monkeypatch, observed)

    assert transaction.run_child(_workspace(tmp_path), ["demo"]) == 0
    assert observed["argv"][0] == "/found/cmru"


def test_run_child_ignores_the_old_name_even_inside_a_child(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_BIN", "/old/evil/cmru")
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _root: True)
    monkeypatch.setattr(transaction.shutil, "which", lambda _name: "/found/cmru")
    observed: dict = {}
    _capture_run(monkeypatch, observed)

    assert transaction.run_child(_workspace(tmp_path), ["demo"]) == 0
    assert observed["argv"][0] == "/found/cmru"


def test_the_family_dispatcher_ignores_the_variable_outside_a_child(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.setenv("CMRU_INTERNAL_BIN", "/evil/cmru")
    monkeypatch.setattr(shutil, "which", lambda _name: "/found/cmru")
    left, right = SimpleNamespace(name="left"), SimpleNamespace(name="right")
    monkeypatch.setattr(
        transaction, "project_git_family_groups",
        lambda *_args: {tmp_path / "left": [left], tmp_path / "right": [right]},
    )
    seen = []
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kwargs: seen.append(argv) or SimpleNamespace(returncode=0),
    )
    assert cli._dispatch_independent_git_families(
        "build", [], tmp_path / "cmru.toml", tmp_path, {"left": left, "right": right},
        ["left", "right"], original_target=None, forward_from=None,
    ) == 0
    assert {argv[0] for argv in seen} == {"/found/cmru"}


# --- E: one exit-code name ---------------------------------------------------


def test_refused_is_the_only_exit_code_name_for_four():
    assert exit_codes.REFUSED == 4
    assert not hasattr(exit_codes, "POLICY_REFUSED")
    names = {n: v for n, v in vars(exit_codes).items() if n.isupper()}
    assert sorted(names.values()) == [0, 1, 2, 3, 4]
    for source in (PROJECT_DIR / "src" / "cmru").glob("*.py"):
        assert "POLICY_REFUSED" not in source.read_text(encoding="utf-8"), source.name


# --- D-A: the init wizard's optional dependency -------------------------------


def test_pyproject_declares_the_interactive_extra_from_the_released_cli_extended():
    declared = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared["project"]["optional-dependencies"]["interactive"] == [
        "cli-extended[interactive]>=0.2.0",
    ]
    assert cli_support.INTERACTIVE_EXTRA == "cmru[interactive]"


@pytest.mark.parametrize("entry", ["root", "module"])
def test_a_missing_prompt_driver_hint_names_the_cmru_extra(monkeypatch, tmp_path, capsys, entry):
    import cli_extended.prompts as prompts

    monkeypatch.setattr(prompts, "_is_tty", lambda _stream: True)
    monkeypatch.setitem(sys.modules, "questionary", None)  # import raises ImportError
    argv = ["--root", str(tmp_path), "--layout", "single"]

    result = cli.main(["init", *argv]) if entry == "root" else scaffold.init_main(argv)

    captured = capsys.readouterr()
    assert result == 2
    assert "pip install 'cmru[interactive]'" in captured.err + captured.out
    assert "cli-extended[interactive]" not in captured.err + captured.out
    assert not list(tmp_path.iterdir())  # nothing written


# --- A6: every estate contract's cmru argv parses against the registry --------

_SKIP_PARTS = {".worktrees", ".git", "node_modules", "artifacts", ".venv", "__pycache__"}
_REMOVED_SPELLINGS = re.compile(
    r"\bcmru\s+run-step\b|\brun-step\b"
    r"|--forward-cgroup-parent-var|--forward-cgroup-parent-gates-var"
    r"|--discard-build-worktree|--discard-(?:logs|artifacts|evidence)-on-release"
    r"|\bcmru\s+run\s+--(?:run-tests|build|push|validate)\b"
)


def _estate_contracts() -> list[Path]:
    found = []
    for path in sorted(REPO_ROOT.rglob("*.toml")):
        relative = path.relative_to(REPO_ROOT)
        if _SKIP_PARTS & set(relative.parts):
            continue
        if path.name.startswith(("cmru", "run-gate", "assay")) or "templates" in relative.parts:
            found.append(path)
    return found


def _load(path: Path) -> dict:
    """Parse a contract; ``cmru init`` templates get their ``@@FIELD@@`` filled in."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\[@@\w+@@\]", "[]", text)
    text = re.sub(r"^@@\w+@@$", "", text, flags=re.MULTILINE)
    return tomllib.loads(re.sub(r"@@\w+@@", "x", text))


def _walk(node):
    """Yield every string and every list of strings inside a parsed TOML value."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        if node and all(isinstance(item, str) for item in node):
            yield node
        for item in node:
            yield from _walk(item)
    elif isinstance(node, str):
        yield node


def _cmru_argvs(parsed) -> list[list[str]]:
    argvs = []
    for item in _walk(parsed):
        if not isinstance(item, list):
            continue
        if Path(item[0]).name == "cmru" and len(item) > 1:  # not a bare ``depends_on = ["cmru"]``
            argvs.append(item)
        elif len(item) >= 4 and Path(item[0]).name in {"python", "python3"} and item[1:3] == ["-m", "cmru.handlers"]:
            argvs.append(["cmru", "handler", *item[3:]])
    return argvs


def _registry_problem(argv: list[str], built) -> str | None:
    """Return why ``argv`` is not a valid cmru invocation, or None."""
    rest = [item for item in argv[1:]]
    while rest and rest[0].startswith("-"):
        rest = rest[1:]  # a global flag before the verb
    if not rest:
        return "no verb"
    verb, arguments = rest[0], rest[1:]
    parser = (
        built.delegates[verb].parser if verb in built.delegates
        else built.command_parsers.get(verb)
    )
    if parser is None:
        return f"unknown verb {verb!r}"
    try:
        parser.parse_args(arguments)
    except Exception as exc:  # argparse/UsageError: the registry refuses this argv
        return str(exc)
    return None


@pytest.fixture(scope="module")
def registry():
    return cli._build_cli()


def test_the_validator_rejects_each_removed_spelling(registry):
    handler = ["cmru", "handler", "oci-image-build", "--cwd", ".", "--bake-file", "f"]
    assert _registry_problem([*handler, "--bake-target", "t"], registry) is None
    assert _registry_problem([*handler, "--target", "t"], registry) is not None
    assert _registry_problem(["cmru", "run-step", "--step", "x"], registry) is not None
    assert _registry_problem(
        ["cmru", "tester-gate", "--cwd", ".", "--forward-cgroup-parent-var", "X", "--", "true"],
        registry,
    ) is not None
    assert _registry_problem(
        ["cmru", "tester-gate", "--cwd", ".", "--forward-gates-slice", "X", "--", "true"],
        registry,
    ) is None
    assert _registry_problem(["cmru", "cleanup", "--discard-build-worktree", "/p"], registry)
    assert _registry_problem(["cmru", "run", "--run-tests"], registry) is not None


def test_the_estate_scan_actually_finds_the_project_contracts():
    contracts = _estate_contracts()
    names = {path.relative_to(REPO_ROOT).as_posix() for path in contracts}
    assert {"cmru/cmru.toml", "ciu/cmru.toml", "cmru.orchestration.toml"} <= names
    argvs = [argv for path in contracts for argv in _cmru_argvs(_load(path))]
    assert len(argvs) >= 10
    assert any(argv[1:2] == ["handler"] for argv in argvs)
    assert any(argv[1:2] == ["tester-gate"] for argv in argvs)


def test_every_estate_cmru_argv_parses_against_the_registry(registry):
    problems = []
    for path in _estate_contracts():
        for argv in _cmru_argvs(_load(path)):
            problem = _registry_problem(argv, registry)
            if problem:
                problems.append(f"{path.relative_to(REPO_ROOT)}: {' '.join(argv)[:120]} -> {problem}")
    assert not problems, "\n".join(problems)


def test_no_estate_contract_uses_a_removed_cmru_spelling():
    hits = []
    for path in _estate_contracts():
        for item in _walk(_load(path)):
            text = " ".join(item) if isinstance(item, list) else item
            if _REMOVED_SPELLINGS.search(text):
                hits.append(f"{path.relative_to(REPO_ROOT)}: {text[:120]}")
    assert not hits, "\n".join(hits)
