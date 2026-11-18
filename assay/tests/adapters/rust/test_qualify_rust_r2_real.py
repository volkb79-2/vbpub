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
if args == ["--version"]:
    print("cargo 1.90.0"); sys.exit(0)
if args[:2] == ["mutants", "--version"]:
    print(config.get("version", "cargo-mutants 27.1.0")); sys.exit(0)
if args[:2] == ["nextest", "--version"]:
    print("cargo-nextest 0.9.151"); sys.exit(0)
assert args[0] == "mutants", args
fixture = FIXTURES / config["fixture"]
if "--list" in args:
    (HERE / "list-argv.json").write_text(json.dumps(args))
    (HERE / "list-diff.patch").write_bytes(Path(args[args.index("--in-diff") + 1]).read_bytes())
    if config.get("list_exit"):
        print("error: boom", file=sys.stderr); sys.exit(config["list_exit"])
    listed = fixture / "mutants.out/mutants.json"
    items = json.loads(listed.read_text()) if listed.is_file() else []
    if config.get("list_count") is not None:
        items = [{{}}] * config["list_count"]
    print(config.get("list_text") or json.dumps(items)); sys.exit(0)
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


def _bin(tmp_path: Path, monkeypatch, case, *, version: str | None = None, stubs=True, **extra) -> Path:
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
    config.update(extra)
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

    assert tool.main(["--no-preflight", "--evidence-dir", str(evidence)]) == 1
    assert any("mutation counts" in d for d in _summary(evidence)["cases"][0]["deviations"])


def test_a_case_that_raises_is_a_deviation_not_a_skip(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))
    monkeypatch.setattr(tool, "INPUTS", tmp_path / "missing-inputs")
    evidence = tmp_path / "evidence"

    assert tool.main(["--case", "indiff", "--no-preflight", "--evidence-dir", str(evidence)]) == 1
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


# --- the deviation detectors, one doctored verdict document each --------------------

import copy  # noqa: E402
from types import SimpleNamespace  # noqa: E402


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """One REAL verdict document per case (fake cargo + committed fixtures)."""
    out = {}
    with pytest.MonkeyPatch.context() as patch:
        for case in tool.CASES:
            tmp = tmp_path_factory.mktemp(case.name)
            _bin(tmp, patch, case)
            evidence = tmp / "evidence"
            assert tool.main(["--case", case.name, "--evidence-dir", str(evidence)]) == 0
            entry = _summary(evidence)["cases"][0]
            document = json.loads((evidence / "verdicts" / f"{case.name}.json").read_text(encoding="utf-8"))
            pre = json.loads((evidence / "preflight.json").read_text(encoding="utf-8"))["cases"][0]
            out[case.name] = SimpleNamespace(
                case=case, doc=document, base=entry["base"], head=entry["head"], sha=pre["diff_sha256"])
    return out


def _check(real, name, mutate=None, **kwargs):
    item = real[name]
    document = copy.deepcopy(item.doc)
    if mutate:
        mutate(document)
    return tool.check_case(item.case, document, base=item.base, head=item.head, **kwargs)[0]


def _claim(document, rigor):
    return next(c for c in document["claims"] if c["rigor"] == rigor)


def _move(document, source, target):
    mutation = _claim(document, "R2")["mutation"]
    mutation[target].append(mutation[source].pop())


def _drop_changed_diff(document):
    del document["judgment"]["r2"]["changed_diff"]


def _set_diff(key, value):
    return lambda d: d["judgment"]["r2"]["changed_diff"].__setitem__(key, value)


def _payload_onto_basefail(real):
    return lambda d: _claim(d, "R2").__setitem__("mutation", copy.deepcopy(_claim(real["indiff"].doc, "R2")["mutation"]))


@pytest.mark.parametrize("name", [case.name for case in tool.CASES])
def test_the_undoctored_verdict_of_every_case_has_no_deviation(real, name):
    assert _check(real, name, diff_sha256=real[name].sha) == []


DOCTORED = [
    ("verify-loop", "indiff", lambda d: d.update(schema_version=1), "assay verify refused the verdict"),
    ("r0-status", "indiff", lambda d: _claim(d, "R0").update(status="FAIL"), "R0 was"),
    ("r0-status-basefail", "basefail-test", lambda d: _claim(d, "R0").update(status="PASS"), "R0 was"),
    ("r2-status", "indiff", lambda d: _claim(d, "R2").update(status="PASS"), "R2 was"),
    ("r2-reason", "indiff", lambda d: _claim(d, "R2").update(reason_code="COMMAND_FAILED"), "R2 was"),
    ("r2-absent", "indiff", lambda d: d["claims"].remove(_claim(d, "R2")), "R2 was"),
    ("outcome", "indiff", lambda d: d.update(outcome="PASS"), "verdict outcome was"),
    ("outcome-zero-mutants", "zero-mutants", lambda d: d.update(outcome="PASS"), "verdict outcome was"),
    ("outcome-crashed", "crashed-f4", lambda d: d.update(outcome="FAIL"), "verdict outcome was"),
    ("outcome-basefail", "basefail-build", lambda d: d.update(outcome="ERROR"), "verdict outcome was"),
    ("exit-code", "indiff", lambda d: d.update(exit_code=0), "exit_code was"),
    ("exit-code-zero-mutants", "zero-mutants", lambda d: d.update(exit_code=0), "exit_code was"),
    ("exit-code-crashed", "crashed-f4", lambda d: d.update(exit_code=1), "exit_code was"),
    ("exit-code-basefail", "basefail-test", lambda d: d.update(exit_code=2), "exit_code was"),
    ("report-absent-unexpected", "indiff", lambda d: d["judgment"]["r2"].update(report_absent=True), "report_absent"),
    ("report-absent-missing", "zero-mutants", lambda d: d["judgment"]["r2"].update(report_absent=False), "report_absent"),
    ("changed-diff-missing", "indiff", _drop_changed_diff, "changed_diff was"),
    ("changed-diff-base", "indiff", _set_diff("base_rev", "0" * 40), "changed_diff was"),
    ("changed-diff-head", "indiff", _set_diff("head_rev", "0" * 40), "changed_diff was"),
    ("changed-diff-relative", "subdir", _set_diff("relative_to", "."), "changed_diff was"),
    ("counts", "indiff", lambda d: _claim(d, "R2")["mutation"]["survived"].pop(), "mutation counts"),
    ("counts-paths", "paths", lambda d: _move(d, "killed", "crashed"), "mutation counts"),
    ("no-payload", "indiff", lambda d: _claim(d, "R2").pop("mutation"), "no mutation payload"),
    ("basefail-payload", "basefail-test", "BASEFAIL", "must carry no mutation payload"),
    ("tolerance-crashed", "full", lambda d: _move(d, "budget_exceeded", "crashed"), "timeout tolerance"),
    ("tolerance-killed-lower", "full", lambda d: (_move(d, "killed", "survived"),), "timeout tolerance"),
    ("tolerance-survived-lower", "full", lambda d: _move(d, "survived", "killed"), "timeout tolerance"),
    ("tolerance-sum", "full", lambda d: _claim(d, "R2")["mutation"]["killed"].append("x"), "timeout tolerance"),
]


@pytest.mark.parametrize("label,name,mutate,expected", DOCTORED, ids=[row[0] for row in DOCTORED])
def test_each_deviation_detector_reports_its_doctored_document(real, label, name, mutate, expected):
    if mutate == "BASEFAIL":
        mutate = _payload_onto_basefail(real)

    problems = _check(real, name, mutate, diff_sha256=real[name].sha)

    assert any(expected in problem for problem in problems), problems


def test_a_diff_that_is_not_the_preflight_diff_is_a_deviation(real):
    assert any("changed_diff was" in p for p in _check(real, "indiff", diff_sha256="0" * 64))


def test_the_timeout_mutant_that_moved_is_tolerated_with_the_failing_verdict(real):
    def moved(document):
        _move(document, "budget_exceeded", "killed")
        _claim(document, "R2").update(status="FAIL", reason_code="MUTANTS_SURVIVED")
        document.update(outcome="FAIL", exit_code=1)

    problems = [p for p in _check(real, "full", moved) if "assay verify" not in p]

    assert problems == []
    # ...but the nominal BUDGET_EXCEEDED claim no longer fits a run with no timeout left.
    kept = lambda d: _move(d, "budget_exceeded", "killed")  # noqa: E731
    assert any("R2 was" in p for p in _check(real, "full", kept))


def test_every_case_pins_the_expected_outcome_and_exit_code(real):
    pinned = {
        "full": ("BUDGET_EXCEEDED", 4), "indiff": ("FAIL", 1), "subdir": ("FAIL", 1),
        "zero-mutants": ("INCONCLUSIVE", 5), "basefail-test": ("FAIL", 1), "basefail-build": ("FAIL", 1),
        "crashed-f4": ("ERROR", 2), "w-true": ("PASS", 0), "ws-sub": ("PASS", 0),
        "methods": ("PASS", 0), "paths": ("FAIL", 1), "unicode": ("PASS", 0),
    }
    assert set(pinned) == {case.name for case in tool.CASES}
    for name, (outcome, code) in pinned.items():
        assert (real[name].case.outcome, tool.EXIT_CODES[outcome]) == (outcome, code), name
        assert (real[name].doc["outcome"], real[name].doc["exit_code"]) == (outcome, code), name


def test_the_failed_baseline_fixtures_hold_no_mutant_outcomes_but_list_their_mutants():
    for name in ("basefail-test", "basefail-build"):
        assert tool.fixture_counts(name) == dict.fromkeys(("killed", "survived", "crashed", "budget_exceeded", "unviable"), 0)
        assert tool.expected_listing(_by_name(name)) == 25
    assert tool.expected_listing(_by_name("zero-mutants")) == 0


def test_the_timeout_tolerance_rejects_each_single_bound():
    full, want = _by_name("full"), tool.fixture_counts("full")
    assert tool._compare_counts(full, {"killed": 15, "survived": 8, "crashed": 0, "budget_exceeded": 1}, want)
    assert tool._compare_counts(full, {"killed": 17, "survived": 6, "crashed": 0, "budget_exceeded": 1}, want)
    assert tool._compare_counts(full, {"killed": 16, "survived": 7, "crashed": 1, "budget_exceeded": 0}, want)
    assert tool._compare_counts(full, {"killed": 17, "survived": 7, "crashed": 0, "budget_exceeded": 1}, want)
    assert tool._compare_counts(full, {"killed": 16, "survived": 7, "crashed": 0, "budget_exceeded": 1}, want) == []


# --- preflight ------------------------------------------------------------------------


def test_preflight_lists_every_case_and_runs_no_mutation(tmp_path: Path, monkeypatch, capsys):
    binary = _bin(tmp_path, monkeypatch, _by_name("paths"))
    evidence = tmp_path / "evidence"

    code = tool.main(["--preflight", "--case", "paths", "--evidence-dir", str(evidence)])

    assert code == 0, capsys.readouterr().err
    report = json.loads((evidence / "preflight.json").read_text(encoding="utf-8"))
    entry = report["cases"][0]
    assert entry["mutants_listed"] == entry["mutants_expected"] == 10 and entry["tab_suffixed_header"] is True
    assert entry["diff_shape"] == "new-file" and entry["deviations"] == []
    listed = json.loads((binary / "list-argv.json").read_text(encoding="utf-8"))
    assert listed[:3] == ["mutants", "--list", "--json"] and "--in-diff" in listed
    assert not (binary / "argv.json").exists() and not (evidence / "summary.json").exists()
    assert b"--- /dev/null" in (binary / "list-diff.patch").read_bytes()


def test_preflight_covers_the_modify_and_subdir_shapes(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("subdir"))
    evidence = tmp_path / "evidence"

    assert tool.main(["--preflight", "--case", "subdir", "--evidence-dir", str(evidence)]) == 0

    entry = json.loads((evidence / "preflight.json").read_text(encoding="utf-8"))["cases"][0]
    assert entry["diff_shape"] == "modify" and entry["mutants_listed"] == 9


@pytest.mark.parametrize("config,message", [
    ({"list_count": 3}, "printed 3 mutants"),
    ({"list_exit": 2}, "exited 2"),
    ({"list_text": "not json"}, "printed no JSON"),
    ({"list_text": '{"a": 1}'}, "expected a list"),
])
def test_preflight_deviations_exit_one_and_abort_the_main_run_with_two(tmp_path: Path, monkeypatch, capsys, config, message):
    _bin(tmp_path, monkeypatch, _by_name("indiff"), **config)
    standalone = tmp_path / "standalone"
    aborted = tmp_path / "aborted"

    assert tool.main(["--preflight", "--case", "indiff", "--evidence-dir", str(standalone)]) == 1
    assert message in capsys.readouterr().err
    assert tool.main(["--case", "indiff", "--evidence-dir", str(aborted)]) == 2
    assert not (aborted / "summary.json").exists()


def test_no_preflight_skips_the_listing_checks(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"), list_count=3)
    evidence = tmp_path / "evidence"

    assert tool.main(["--case", "indiff", "--no-preflight", "--evidence-dir", str(evidence)]) == 0
    assert not (evidence / "preflight.json").exists()


def test_the_main_run_compares_the_verdict_diff_with_the_preflight_diff(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))
    monkeypatch.setattr(tool, "changed_diff", lambda *a, **k: b"not the diff assay produced\n")
    evidence = tmp_path / "evidence"

    assert tool.main(["--case", "indiff", "--evidence-dir", str(evidence)]) == 1
    assert any("changed_diff was" in d for d in _summary(evidence)["cases"][0]["deviations"])


def test_an_old_python_is_a_missing_prerequisite(tmp_path: Path, monkeypatch, capsys):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))
    monkeypatch.setattr(tool, "MIN_PYTHON", (99, 0))

    assert tool.main(["--preflight", "--case", "indiff", "--evidence-dir", str(tmp_path / "e")]) == 2
    assert "older than 99.0" in capsys.readouterr().err


def test_an_assay_import_failure_is_a_preflight_deviation(tmp_path: Path, monkeypatch, capsys):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))

    def broken():
        raise ImportError("No module named 'assay.runner'")

    monkeypatch.setattr(tool, "_assay", broken)

    assert tool.main(["--preflight", "--case", "indiff", "--evidence-dir", str(tmp_path / "e")]) == 1
    assert "cannot import assay" in capsys.readouterr().err


def test_the_preflight_probes_cargo_rustc_git_and_python_versions(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))

    problems, versions = tool.probe_tools()

    assert problems == [] and any(v.startswith("cargo 1.") for v in versions)
    assert any(v.startswith("git version") for v in versions) and versions[-1].startswith("python 3.")


# --- evidence of a deviating case ---------------------------------------------------


def test_a_deviating_case_keeps_the_real_assay_out_and_the_lane_file(tmp_path: Path, monkeypatch):
    case = _by_name("indiff")
    _bin(tmp_path, monkeypatch, case)
    wrong = tool.Case(**{**case.__dict__, "r2": ("PASS", None), "outcome": "PASS"})
    monkeypatch.setattr(tool, "CASES", (wrong,))
    evidence = tmp_path / "evidence"

    assert tool.main(["--evidence-dir", str(evidence)]) == 1

    kept = evidence / "deviations" / "indiff"
    assert (kept / "assay.toml").is_file()
    assert list(kept.rglob("outcomes.json")), sorted(p.as_posix() for p in kept.rglob("*"))
    assert list(kept.rglob("in-diff.patch"))
    assert _summary(evidence)["cases"][0]["evidence"]["copied"]


def test_a_clean_case_keeps_no_deviation_evidence(tmp_path: Path, monkeypatch):
    _bin(tmp_path, monkeypatch, _by_name("indiff"))
    evidence = tmp_path / "evidence"

    assert tool.main(["--case", "indiff", "--evidence-dir", str(evidence)]) == 0
    assert not (evidence / "deviations").exists()


def test_the_evidence_copy_is_bounded(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    (source / "log").mkdir(parents=True)
    (source / "big.log").write_bytes(b"x" * (tool.EVIDENCE_FILE_LIMIT + 1))
    (source / "log" / "small.log").write_text("ok", encoding="utf-8")
    (source / "link").symlink_to(source / "log" / "small.log")
    budget = [tool.EVIDENCE_TOTAL_LIMIT]

    skipped = tool._copy_bounded(source, tmp_path / "dest", budget)

    assert skipped == 1
    assert (tmp_path / "dest/log/small.log").is_file() and not (tmp_path / "dest/big.log").exists()
    assert not (tmp_path / "dest/link").exists()
    budget = [1]
    assert tool._copy_bounded(source, tmp_path / "dest2", budget) == 2 and not (tmp_path / "dest2/log/small.log").exists()
