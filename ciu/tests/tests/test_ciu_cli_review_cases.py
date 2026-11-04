"""Behavioural probes behind the reviewed CLI catalog ``docs/cli-review.toml`` (AC-17, AC-21, AC-22).

Every active catalog row names one parameter of ``test_reviewed_case`` in its ``test_ids``;
``@pytest.mark.cli_case`` links the two and the cli-extended pytest plugin (enabled in
``tests/conftest.py``) fails collection when a row has no collected test. The test runs the
row's ``invocation`` through the REAL ``ciu.cli.main`` in the hermetic sandbox of
``cli_review_harness`` and asserts the recorded exit status and stderr/stdout fragments, so a
row can never claim a grammar the CLI does not have. The catalog is a characterization of the
GRAMMAR (see ``tools/gen-cli-review-catalog.py``); behaviour behind a verb is pinned elsewhere.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_review_harness as harness  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "docs" / "cli-review.toml"


def _load_cases() -> list:
    raw = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
    return [
        pytest.param(case, id=harness.short_id(case["id"]), marks=pytest.mark.cli_case(case["id"]))
        for case in raw["cases"]
        if case.get("state") == "active"
    ]


@pytest.mark.parametrize("case", _load_cases())
def test_reviewed_case(case):
    argv = list(case["invocation"])
    status, out, err, recorded = harness.run(argv)
    assert status == case["expected_exit_status"], (
        f"{argv!r} exited {status}\nstdout={out!r}\nstderr={err!r}"
    )
    assert case["expected_stdout_contains"] in out, (out, err)
    assert case["expected_stderr_contains"] in err, (out, err)
    assert "Traceback" not in err
    if case["decision"] == "refuse" and argv[:1] not in (["doctor"], ["skills"]):
        # A refusal by the shared parser never reaches the implementation layer.
        assert recorded == [] or case["expected_exit_status"] == 0


def test_every_catalog_case_is_active_and_unique():
    """No pending or retired row may hide in the committed catalog."""
    raw = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
    ids = [case["id"] for case in raw["cases"]]
    assert len(ids) == len(set(ids))
    assert {case.get("state") for case in raw["cases"]} == {"active"}
    short = [harness.short_id(case_id) for case_id in ids]
    assert len(short) == len(set(short)), "ambiguous short ids"
    for case in raw["cases"]:
        assert case["test_ids"] == [
            f"tests/tests/test_ciu_cli_review_cases.py::test_reviewed_case[{harness.short_id(case['id'])}]"
        ]


def test_real_executable_obeys_help_version_and_parse_contract(tmp_path):
    """The library's black-box probe against a launcher with the console script's body (AC-21)."""
    import importlib.metadata

    from cli_extended import assert_cli_contract, make_invoker

    from ciu.cli_registry import build_cli

    src = ROOT / "src"
    worktree_src = ROOT.parent / "libraries" / "worktree" / "src"
    dist_dir = Path(importlib.metadata.distribution("ciu")._path).parent  # type: ignore[attr-defined]
    entry = tmp_path / "ciu_entry.py"
    entry.write_text("import sys\nfrom ciu.cli import main\nmain()\n")
    cli = build_cli()
    # `python=sys.executable` is deliberate: the library would otherwise prepend its OWN
    # install directory, whose installed ciu (if any) could shadow `src`.
    invoke = make_invoker(
        entry, home=tmp_path.resolve(), cwd=tmp_path, python=sys.executable,
        pythonpath=[src, worktree_src, dist_dir],
    )
    assert_cli_contract(
        invoke, cli.identity, ("status", "show", "doctor", "skills"),
        invalid_invocations={"unknown option": ["status", "--nonsuch"]},
    )
