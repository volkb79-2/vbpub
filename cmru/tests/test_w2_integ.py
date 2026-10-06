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
import shlex
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


# Every name the code under test may write into ``os.environ``.  monkeypatch records
# nothing for ``delenv(raising=False)`` on an absent name, so a name the test never
# registers is never restored; ``_clean_env`` therefore sets then deletes each one
# (the recorded prior state is "absent", and teardown removes whatever was written).
_WRITTEN_BY_THE_CODE_UNDER_TEST = ("PYTHONUNBUFFERED",)


@pytest.fixture(scope="module", autouse=True)
def _env_before_module():
    """The leak probe's baseline: names this file's tests may write, as found."""
    watched = (*OLD_ENV_NAMES, *INTERNAL_ENV_NAMES, *_WRITTEN_BY_THE_CODE_UNDER_TEST)
    return {name: os.environ.get(name) for name in watched}


def _clean_env(monkeypatch) -> None:
    for name in (
        *OLD_ENV_NAMES, *INTERNAL_ENV_NAMES, *_WRITTEN_BY_THE_CODE_UNDER_TEST,
        transaction.CHILD_ENV,
    ):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


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


# --- C5: the CMRU_INTERNAL_ namespace is reserved against project [env] ------------

_RESERVED_PROJECT_ENV_NAMES = (
    *INTERNAL_ENV_NAMES, "CMRU_INTERNAL_RELEASE_PREFLIGHT_FD", "CMRU_RELEASE_PREFLIGHT_SNAPSHOT",
    "CMRU_INTERNAL_X",  # the PREFIX is reserved, not a list
)


@pytest.mark.parametrize("name", _RESERVED_PROJECT_ENV_NAMES)
def test_project_env_cannot_declare_internal_names(name, capsys, monkeypatch):
    from cmru import config

    _clean_env(monkeypatch)  # an earlier file may have left a CMRU_INTERNAL_* name behind
    with pytest.raises(SystemExit) as refused:
        config._scalar_env({name: "/evil"}, "[env]", reject_credentials=True)
    assert refused.value.code == exit_codes.CONFIG_ERROR
    assert "reserved for CMRU internal launch state" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="reserved for CMRU internal launch state"):
        cli.apply_release_env(
            cli.GitHubConfig("owner", "repo", "", "org"), cli.ReleaseEnvConfig({name: "/evil"}, None),
        )
    assert name not in os.environ


def test_project_env_still_accepts_ordinary_cmru_names():
    from cmru import config

    assert config._scalar_env(
        {"CMRU_LOG_LEVEL": "x", "CMRU_INTERNALS": "y"}, "[env]", reject_credentials=True,
    ) == {"CMRU_LOG_LEVEL": "x", "CMRU_INTERNALS": "y"}


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
        if isinstance(item, str):  # a shell string, possibly a ``bash -c`` payload
            argvs += _cmru_from_text(item)
            continue
        if Path(item[0]).name == "cmru" and len(item) > 1:  # not a bare ``depends_on = ["cmru"]``
            argvs.append(item)
        elif len(item) >= 4 and Path(item[0]).name in {"python", "python3"} and item[1:3] == ["-m", "cmru.handlers"]:
            argvs.append(["cmru", "handler", *item[3:]])
    return argvs


_SHELL_SPLIT = re.compile(r"&&|\|\||;|\||\n")
_LAUNCH_PREFIXES = {"exec", "sudo", "command", "nice", "time", "env"}


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:  # an unbalanced quote across a segment split: fall back to words
        return text.split()


def _cmru_from_text(text: str) -> list[list[str]]:
    """Every ``cmru ...`` invocation inside a shell-ish string.

    Tokenises with ``shlex``, descends into ``bash -c`` / ``sh -c`` payloads, and
    splits each text on ``&&``, ``||``, ``;``, ``|`` and newlines.  A segment counts
    only when ``cmru`` is its command word (after ``VAR=x`` assignments and wrappers
    like ``exec``), so prose that merely mentions cmru is not a call.
    """
    found: list[list[str]] = []
    whole = _tokens(text)
    for index, token in enumerate(whole):
        if Path(token).name in {"bash", "sh"} and "-c" in whole[index + 1:]:
            payload = whole.index("-c", index + 1) + 1
            if payload < len(whole):
                found += _cmru_from_text(whole[payload])
    for segment in _SHELL_SPLIT.split(text):
        tokens = _tokens(segment)
        while tokens and (re.fullmatch(r"\w+=.*", tokens[0]) or tokens[0] in _LAUNCH_PREFIXES):
            tokens = tokens[1:]
        if len(tokens) > 1 and Path(tokens[0]).name == "cmru":
            found.append(["cmru", *tokens[1:]])
    unique: dict[tuple[str, ...], list[str]] = {}
    for argv in found:  # a payload is seen both via its ``bash -c`` and via the raw split
        unique.setdefault(tuple(token.strip("'\"") for token in argv), argv)
    return list(unique.values())


def _estate_shell_scripts() -> list[Path]:
    return sorted(
        path for path in REPO_ROOT.rglob("*.sh")
        if not _SKIP_PARTS & set(path.relative_to(REPO_ROOT).parts)
    )


def _shell_text(path: Path) -> str:
    """The script without comment lines and with ``\\`` continuations joined."""
    lines = [
        line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not line.lstrip().startswith("#")
    ]
    return re.sub(r"\\\n", " ", "\n".join(lines))


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


def test_the_text_scanner_finds_string_and_bash_c_invocations(registry):
    def problems(text):
        return [_registry_problem(argv, registry) for argv in _cmru_from_text(text)]

    assert problems("cmru publish ciu") and problems("cmru publish ciu")[0]  # needs a source
    assert problems("cmru publish ciu --from-checkout") == [None]
    assert problems("cd /x && exec cmru release ciu --set-version 1.0.0 | tee log") == [None]
    assert problems("FOO=1 cmru cleanup; cmru status") != [None]  # bare cleanup is refused
    assert len(problems("bash -c 'cd x && cmru publish ciu'")) == 1  # the payload, once
    assert problems("sh -c \"cmru run --run-tests\"")[0]
    assert problems("python -m cmru.handlers wheel-build") == []  # not the cmru command word
    assert problems("run the cmru release step, then cmru is done") == []  # prose is not a call
    assert problems("echo 'unbalanced") == []


def test_the_shell_script_reader_drops_comments_and_joins_continuations(tmp_path):
    script = tmp_path / "release.sh"
    script.write_text(
        "#!/bin/sh\n# cmru publish ciu\ncmru build ciu \\\n  --config x\ncmru publish ciu\n",
        encoding="utf-8",
    )
    assert _cmru_from_text(_shell_text(script)) == [
        ["cmru", "build", "ciu", "--config", "x"], ["cmru", "publish", "ciu"],
    ]


def _on_disk_project_contracts() -> set[str]:
    """Independent of ``_SKIP_PARTS``: every project contract the checkout really has."""
    skipped = {".worktrees", ".git"}
    return {
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in ("*/cmru.toml", "*/*/cmru.toml")
        for path in REPO_ROOT.glob(pattern)
        if not skipped & set(path.relative_to(REPO_ROOT).parts)
    }


def test_the_estate_scan_actually_finds_the_project_contracts():
    contracts = _estate_contracts()
    names = {path.relative_to(REPO_ROOT).as_posix() for path in contracts}
    # The canary lane runs in a sparse snapshot (no sibling projects), so only
    # cmru's own contract and its `init` templates are guaranteed to exist ...
    assert {"cmru/cmru.toml", "cmru/src/cmru/templates/project-wheel.toml"} <= names
    argvs = [argv for path in contracts for argv in _cmru_argvs(_load(path))]
    assert len(argvs) >= 4
    assert any(argv[1:2] == ["handler"] for argv in argvs)
    assert any(argv[1:2] == ["tester-gate"] for argv in argvs)  # the template carries one
    # ... but wherever a sibling project exists, the guard REQUIRES its contract.
    on_disk = _on_disk_project_contracts()
    assert on_disk <= names, f"estate guard skips real project contracts: {sorted(on_disk - names)}"
    siblings = [
        sibling for sibling in ("ciu", "nyxloom", "assay", "topos", "pwmcp", "tls-edge", "run-gate-project")
        if (REPO_ROOT / sibling / "cmru.toml").is_file()  # the sparse canary has a bare run-gate-project/
    ]
    for sibling in siblings:
        assert f"{sibling}/cmru.toml" in names, f"{sibling}/ exists but its cmru.toml is not scanned"
    if siblings:  # a full checkout: a minimum count of distinct project contracts
        assert len(on_disk) >= 8, sorted(on_disk)


def test_the_estate_scan_covers_the_shell_scripts_that_call_cmru():
    scripts = {path.relative_to(REPO_ROOT).as_posix() for path in _estate_shell_scripts()}
    callers = {
        "tls-edge/scripts/release.sh", "game_stuff/empyrion/run-full-workflow.sh",
    }
    for caller in callers:
        if (REPO_ROOT / caller).is_file():
            assert caller in scripts, f"{caller} is not scanned"
            assert _cmru_from_text(_shell_text(REPO_ROOT / caller)), f"{caller} has no cmru call"


def test_every_estate_cmru_argv_parses_against_the_registry(registry):
    problems = []
    for path in _estate_contracts():
        for argv in _cmru_argvs(_load(path)):
            problem = _registry_problem(argv, registry)
            if problem:
                problems.append(f"{path.relative_to(REPO_ROOT)}: {' '.join(argv)[:120]} -> {problem}")
    for path in _estate_shell_scripts():
        for argv in _cmru_from_text(_shell_text(path)):
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
    for path in _estate_shell_scripts():
        for line in _shell_text(path).splitlines():
            if _REMOVED_SPELLINGS.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


# --- C4: this file leaves the process environment as it found it -----------------
# Keep LAST in the file: pytest runs a file's tests in order, so by now every
# test above has run and torn down.


def test_zz_no_internal_name_or_pythonunbuffered_leaks_out_of_this_file(_env_before_module):
    leaked = {
        name: os.environ.get(name) for name, before in _env_before_module.items()
        if os.environ.get(name) != before
    }
    assert not leaked, f"the tests above leaked into the process environment: {leaked}"
    assert not [name for name in os.environ if name.startswith("CMRU_INTERNAL_")
                and name not in _env_before_module]
