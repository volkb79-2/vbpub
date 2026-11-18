"""B245 U7: ``tools/qualify_rust_r2_real.py`` against a FAKE ``cargo``.

The real run needs ``tester-unified-rust`` (cargo-mutants 27, cargo-nextest) and
happens on a host through the ``rust-r2-qualification`` lane. Here a small script
named ``cargo`` (plus stubs for ``cargo-mutants`` and ``cargo-nextest``) answers
the version probes and, for ``cargo mutants ... --output X``, copies the committed
REAL fixture ``mutants.out`` of the case into ``X``, retimed into the run's window
(and, for the workspace cases, with the member directories renamed the way the
tool renames them); it exits with the fixture's real exit status. The tool's own
repository building, lane writing, in-process ``assay run``, expectations,
``assay verify`` round trip, summary and exit codes are the real code.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).parents[3] / "tools" / "qualify_rust_r2_real.py"


def _load():
    spec = importlib.util.spec_from_file_location("qualify_rust_r2_real", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

FAKE = '''#!{python}
import datetime, json, re, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = Path({fixtures!r})
args = sys.argv[1:]
config = json.loads((HERE / "case.json").read_text())
if args[:2] == ["mutants", "--version"]:
    print(config.get("version", "cargo-mutants 27.1.0")); sys.exit(0)
if args[:2] == ["nextest", "--version"]:
    print("cargo-nextest 0.9.151"); sys.exit(0)
assert args[0] == "mutants", args
out = Path(args[args.index("--output") + 1])
fixture = FIXTURES / config["fixture"]
(HERE / "argv.json").write_text(json.dumps(args))
source = fixture / "mutants.out"
if source.is_dir():
    target = out / "mutants.out"
    shutil.copytree(source, target)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
    report = target / "outcomes.json"
    document = json.loads(report.read_text())
    document["start_time"] = stamp(now - datetime.timedelta(seconds=1))
    document["end_time"] = stamp(now)
    report.write_text(json.dumps(document))
    if config.get("rename"):
        for path in sorted(target.rglob("*"), key=lambda p: -len(p.parts)):
            if path.is_file():
                text = path.read_text()
                text = re.sub(r"(?<![\\w])([ab])/src/", r"crate_\\1/src/", text)
                text = re.sub(r"(?<![\\w])([ab])__src__", r"crate_\\1__src__", text)
                path.write_text(text)
                renamed = re.sub(r"^([ab])__src__", r"crate_\\1__src__", path.name)
                if renamed != path.name:
                    path.rename(path.with_name(renamed))
sys.exit(int((fixture / "exit-code.txt").read_text().strip()))
'''


def _bin(tmp_path: Path, monkeypatch, case, *, version: str | None = None, stubs=True) -> Path:
    binary = tmp_path / "bin"
    binary.mkdir()
    script = binary / "cargo"
    script.write_text(FAKE.format(python=sys.executable, fixtures=str(tool.FIXTURES)), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    if stubs:
        for name in ("cargo-mutants", "cargo-nextest", "rustc"):
            stub = binary / name
            stub.write_text(f"#!{sys.executable}\nprint('{name} 1')\n", encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    config = {"fixture": case.fixture or case.name, "rename": case.source == "ws"}
    if version:
        config["version"] = version
    (binary / "case.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("PATH", f"{binary}:/usr/bin:/bin")
    monkeypatch.setenv("CARGO_NET_OFFLINE", "true")
    return binary


def _by_name(name: str):
    return next(case for case in tool.CASES if case.name == name)


def _summary(evidence: Path) -> dict:
    return json.loads((evidence / "summary.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", [case.name for case in tool.CASES])
def test_every_case_matches_its_expectation_against_the_committed_fixture(
    tmp_path: Path, monkeypatch, capsys, name: str
):
    case = _by_name(name)
    binary = _bin(tmp_path, monkeypatch, case)
    evidence = tmp_path / "evidence"

    code = tool.main(["--case", name, "--evidence-dir", str(evidence)])

    summary = _summary(evidence)
    assert summary["cases"][0]["deviations"] == [], capsys.readouterr().out
    assert code == 0 and summary["ok"] is True
    argv = json.loads((binary / "argv.json").read_text(encoding="utf-8"))
    assert argv[:1] == ["mutants"] and "--in-diff" in argv and "--locked" not in argv
    assert "--cargo-arg=--locked" in argv and "--cargo-arg=--offline" in argv
    assert argv[argv.index("--in-diff") + 1] != "{assay.changed_diff}"  # substituted by assay
    assert (evidence / "verdicts" / f"{name}.json").is_file()
    assert summary["cases"][0]["wall_seconds"] >= 0


def test_the_summary_reports_tools_and_per_case_wall_time(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))
    evidence = tmp_path / "evidence"

    assert tool.main(["--case", "indiff", "--evidence-dir", str(evidence)]) == 0

    summary = _summary(evidence)
    assert summary["tools"][0].startswith("cargo-mutants 1") or summary["tools"][0]
    assert summary["wall_seconds_total"] == summary["cases"][0]["wall_seconds"]
    assert summary["cases"][0]["observed"]["counts"]["survived"] == 9


def test_a_deviating_case_fails_closed_with_exit_one(tmp_path: Path, monkeypatch, capsys):
    # The tool is told the indiff case is the zero-mutant one: the real verdict deviates.
    case = _by_name("indiff")
    _bin(tmp_path, monkeypatch, case)
    wrong = tool.Case(**{**case.__dict__, "r2": ("PASS", None), "outcome": "PASS"})
    monkeypatch.setattr(tool, "CASES", (wrong,))
    evidence = tmp_path / "evidence"

    code = tool.main(["--evidence-dir", str(evidence)])

    assert code == 1
    summary = _summary(evidence)
    assert summary["ok"] is False
    assert any("R2 was" in d for d in summary["cases"][0]["deviations"])
    assert "DEVIATED" in capsys.readouterr().out


def test_counts_that_differ_from_the_fixture_are_a_deviation(tmp_path: Path, monkeypatch):
    # indiff's real run produced 9 survivors; claiming the paths fixture (1 survivor) deviates on counts.
    case = _by_name("indiff")
    _bin(tmp_path, monkeypatch, case)
    wrong = tool.Case(**{**case.__dict__, "fixture": "paths"})
    monkeypatch.setattr(tool, "CASES", (wrong,))
    evidence = tmp_path / "evidence"

    assert tool.main(["--evidence-dir", str(evidence)]) == 1
    assert any("mutation counts" in d for d in _summary(evidence)["cases"][0]["deviations"])


def test_a_case_that_raises_is_a_deviation_not_a_skip(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))
    monkeypatch.setattr(tool, "INPUTS", tmp_path / "missing-inputs")
    evidence = tmp_path / "evidence"

    assert tool.main(["--case", "indiff", "--evidence-dir", str(evidence)]) == 1
    assert _summary(evidence)["cases"][0]["deviations"]


def test_a_missing_binary_exits_two_without_running_a_case(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    evidence = tmp_path / "evidence"

    assert tool.main(["--evidence-dir", str(evidence)]) == 2
    assert "cargo-mutants is not on PATH" in capsys.readouterr().err
    assert not (evidence / "summary.json").exists()


def test_an_unqualified_major_exits_two(tmp_path: Path, monkeypatch, capsys):
    _bin(tmp_path, monkeypatch, _by_name("indiff"), version="cargo-mutants 28.0.0")

    assert tool.main(["--case", "indiff", "--evidence-dir", str(tmp_path / "e")]) == 2
    assert "outside the qualified major 27." in capsys.readouterr().err


def test_an_unknown_case_exits_two(capsys):
    assert tool.main(["--case", "nonesuch"]) == 2
    assert "unknown case" in capsys.readouterr().err


def test_list_prints_every_case(capsys):
    assert tool.main(["--list"]) == 0
    assert capsys.readouterr().out.split() == [case.name for case in tool.CASES]


def test_the_lint_refuses_a_member_directory_named_a_or_b(tmp_path: Path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="matches no mutant"):
        tool.lint_workspace(tmp_path)


def test_the_lane_is_a_whole_token_placeholder_lane_with_the_measured_flags():
    case = _by_name("subdir")

    text = tool.lane_toml(case, "0" * 40)

    assert '"--in-diff", "{assay.changed_diff}"' in text
    assert '"--cargo-arg=--locked"' in text and '"--cargo-arg=--offline"' in text
    assert 'cwd = "sub/crate1"' in text and 'artifact = "sub/crate1/assay-out"' in text
    assert 'path = "assay-out"' in text and f'base = "{"0" * 40}"' in text


def test_the_fixture_counts_follow_the_provenance_table():
    assert tool.fixture_counts("full") == {
        "killed": 16, "survived": 7, "crashed": 0, "budget_exceeded": 1, "unviable": 1}
    assert tool.fixture_counts("crashed-f4")["crashed"] == 5
    assert tool.fixture_counts("crashed-f4")["killed"] == 3
    assert tool.fixture_counts("w-true")["killed"] == 8


def test_the_timeout_mutant_may_move_but_nothing_else():
    full = _by_name("full")
    want = tool.fixture_counts("full")
    moved = {"killed": 17, "survived": 7, "crashed": 0, "budget_exceeded": 0}
    other = {"killed": 17, "survived": 7, "crashed": 0, "budget_exceeded": 1}

    assert tool._compare_counts(full, moved, want) == []
    assert tool._compare_counts(full, other, want)
    assert tool._compare_counts(full, {**moved, "crashed": 1}, want)
