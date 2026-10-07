"""Behavioural probes behind the reviewed CLI catalog ``docs/cli-review.toml`` (W2-PKG5, CLI-T3).

Every active catalog row names one parameter of ``test_reviewed_case`` in its ``test_ids``;
``@pytest.mark.cli_case`` links the two and the cli-extended pytest plugin (enabled in
``tests/conftest.py``) fails collection when a row has no collected test. The test reads the row's
``invocation`` and runs it through the REAL ``cmru.cli.main`` in a sandbox, then asserts the exit
status and the recorded stdout/stderr fragments, so a catalog row can never claim a behaviour the
CLI does not have.

Sandbox: an empty non-repository cwd, a tmp ``HOME``, a ``PATH`` holding only ``git`` and stub
``docker``/``gh``/``curl`` (they log their argv and exit 97), no credentials, ``CMRU_*`` and
registry variables removed, the GitHub HTTP layer replaced by a refusing fake. The sandbox has no
cmru configuration, so a verb that accepts its arguments stops at configuration discovery; deeper
semantics live in the verb's own test modules.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import sys
import tomllib
import urllib.error
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[1] / "docs" / "cli-review.toml"
_PREFIX = "case:route:entrypoint:cmru/"
_KINDS = (
    "argument-shape", "argument-choice", "option-spelling", "option-choice",
    "exclusive-member", "exclusive-conflict", "constraint-requires", "constraint-conflict",
    "minimum",
)
# Verbs whose recorded behaviour legitimately runs the (stub) docker CLI.
_DOCKER_ROUTES = ("doctor", "handler oci-image-build")


def _short_id(case_id: str) -> str:
    """The pytest parameter id the catalog's ``test_ids`` use (the id minus its repeats)."""
    rest = case_id.removeprefix(_PREFIX)
    kind = next((k for k in _KINDS if f"/{k}" in rest), None)
    if kind is None:
        return rest
    route = rest.split(f"/{kind}", 1)[0]
    for noun in ("option", "argument"):
        rest = rest.replace(f"{noun}:route:entrypoint:cmru/{route}/", "")
    return rest


def _load_cases() -> list:
    raw = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
    return [
        pytest.param(
            case, id=_short_id(case["id"]), marks=pytest.mark.cli_case(case["id"]),
        )
        for case in raw["cases"]
        if case.get("state") == "active"
    ]


def _stub(path: Path, log: Path) -> None:
    path.write_text(f'#!/bin/sh\necho "$0 $*" >> "{log}"\nexit 97\n')
    path.chmod(0o755)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A hermetic place to run one CLI invocation; returns ``(workdir, externals_log)``."""
    from cmru import output

    home = tmp_path / "home"
    work = tmp_path / "work"
    bindir = tmp_path / "bin"
    for directory in (home, work, bindir):
        directory.mkdir()
    log = tmp_path / "externals.log"
    for name in ("docker", "gh", "curl"):
        _stub(bindir / name, log)
    git = shutil.which("git")
    assert git, "git is required for the sandbox"
    os.symlink(git, bindir / "git")
    for key in list(os.environ):
        if key.startswith(("CMRU_", "GITHUB_", "GIT_", "XDG_")) or key in {
            "SOURCE_DATE_EPOCH", "REGISTRY", "GH_TOKEN",
        }:
            monkeypatch.delenv(key)
    monkeypatch.delenv(output._TIME_ENV, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.chdir(work)

    def refuse_http(*_args, **_kwargs):
        raise urllib.error.URLError("network blocked by the review-case sandbox")

    monkeypatch.setattr("cmru.release.urlopen", refuse_http)

    def refuse_connect(self, *_args, **_kwargs):
        raise OSError("socket use blocked by the review-case sandbox")

    monkeypatch.setattr(socket.socket, "connect", refuse_connect)

    def refuse_dns(*_args, **_kwargs):
        raise socket.gaierror("name resolution blocked by the review-case sandbox")

    monkeypatch.setattr(socket, "getaddrinfo", refuse_dns)
    yield work, log
    # `--log-prefix-time-short` sets a process-wide env flag and wraps the std streams.
    os.environ.pop(output._TIME_ENV, None)
    output.configure(False)


def _run(argv: list[str], capsys) -> int:
    from cmru.cli import main

    try:
        status = main(list(argv))
    except SystemExit as exc:
        status = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return int(status or 0)


def test_the_sandbox_blocks_dns_and_resolve_repo_does_no_lookup(sandbox, capsys, monkeypatch):
    """``resolve --repo ... --prefix ...`` imports its own ``urlopen`` (not the patched one) and
    used to perform a real DNS lookup for github.com; the sandbox now blocks name resolution."""
    with pytest.raises(socket.gaierror, match="blocked by the review-case sandbox"):
        socket.getaddrinfo("github.com", 443)
    answered: list[object] = []
    blocked = socket.getaddrinfo

    def spy(*args, **kwargs):
        result = blocked(*args, **kwargs)  # the sandbox stub: always raises
        answered.append(args[:1])  # only reached if a real lookup answered
        return result

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    status = _run(["resolve", "--repo", "owner/repo", "--prefix", "demo-v"], capsys)
    capsys.readouterr()
    assert status != 0  # it could not fetch: nothing was resolved
    assert answered == []  # no lookup was ever answered by a real resolver


@pytest.mark.parametrize("case", _load_cases())
def test_reviewed_case(case, sandbox, capsys):
    work, log = sandbox
    argv = list(case["invocation"])
    status = _run(argv, capsys)
    captured = capsys.readouterr()
    assert status == case["expected_exit_status"], (
        f"{argv!r} exited {status}\nstdout={captured.out!r}\nstderr={captured.err!r}"
    )
    assert case["expected_stdout_contains"] in captured.out, (captured.out, captured.err)
    assert case["expected_stderr_contains"] in captured.err, (captured.out, captured.err)
    assert "Traceback" not in captured.err
    externals = log.read_text().splitlines() if log.exists() else []
    allowed_docker = any(" ".join(argv).startswith(route) for route in _DOCKER_ROUTES)
    for line in externals:
        command = Path(line.split()[0]).name
        assert command == "docker" and allowed_docker, (
            f"unexpected external command {line!r} for {argv!r}"
        )
    # No row may leave files behind in the sandbox except the explicit write destinations.
    leftovers = sorted(p.name for p in work.iterdir())
    assert leftovers in ([], ["dest"]), leftovers


def test_every_catalog_case_is_active_and_unique():
    """No pending or retired row may hide in the committed catalog."""
    raw = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
    ids = [case["id"] for case in raw["cases"]]
    assert len(ids) == len(set(ids))
    assert {case.get("state") for case in raw["cases"]} == {"active"}
    short = [_short_id(case_id) for case_id in ids]
    assert len(short) == len(set(short)), "ambiguous short ids"
    for case in raw["cases"]:
        assert case["test_ids"] == [
            f"tests/test_cli_review_cases.py::test_reviewed_case[{_short_id(case['id'])}]"
        ]


def test_real_executable_obeys_help_version_and_parse_contract(tmp_path):
    """The library's black-box probe against ``python -m cmru.cli`` (CLI-T3)."""
    from cli_extended import assert_cli_contract, make_invoker
    from cmru.cli import _build_cli

    src = Path(__file__).resolve().parents[1] / "src"
    worktree_src = Path(__file__).resolve().parents[2] / "libraries" / "worktree" / "src"
    cli = _build_cli()
    # `python -m cmru.cli` is deliberately unsupported (S-CLI.9), so the probe runs a one-line
    # launcher with the same body as the `cmru` console script.
    entry = tmp_path / "cmru_entry.py"
    entry.write_text("import sys\nfrom cmru.cli import main\nsys.exit(main())\n")
    # `python=sys.executable` is deliberate: without it the library prepends ITS OWN install
    # directory to PYTHONPATH, which in the gate image holds an installed (older) cmru that
    # would shadow `src` and make the probe test the wrong code.
    invoke = make_invoker(
        entry, home=tmp_path.resolve(), cwd=tmp_path, python=sys.executable,
        pythonpath=[src, worktree_src],
    )
    assert_cli_contract(
        invoke, cli.identity, ("status", "release", "doctor", "skills"),
        invalid_invocations={"unknown option": ["status", "--nonsuch"]},
    )
    assert sys.executable  # the probe ran the same interpreter as the suite
