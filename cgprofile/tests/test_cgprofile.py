"""cgprofile, the end-user client: run, mark, report, analyze and skills (CGPROFILECTL-SPEC.md section 2.4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types

import pytest

import _fakectl
from cgprofile.cli import cgprofile as client
from cgprofile.ctlwire import WireFailure

from test_cgprofilectl import SESSION_DONE, SUMMARY, _clean_env, daemon, handlers, jdoc, sockdir  # noqa: F401


@pytest.fixture()
def cli(capsys):
    def run(*argv):
        code = client.build_cli().run(argv=list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return run


def test_identity_and_main(capsys):
    assert client.identity().command == "cgprofile"
    assert client.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("cgprofile ")


# ── report ───────────────────────────────────────────────────────────────────

class FakeStageModules:
    """Stand-ins for the report stack (pandas is absent in some environments)."""

    def __init__(self, monkeypatch, proposals=()):
        import cgprofile
        self.analysis_for = []
        analyze = types.ModuleType("cgprofile.analyze")
        analysis = types.SimpleNamespace(proposals=list(proposals))
        analyze.build = lambda run: (self.analysis_for.append(run.path) or analysis)
        html = types.ModuleType("cgprofile.report_html")
        md = types.ModuleType("cgprofile.report_md")

        def write(path):
            with open(path, "w") as handle:
                handle.write("x")
            return path

        html.render = lambda analysis, path: write(path)
        md.render = lambda analysis, out_dir: write(os.path.join(out_dir, "report.md"))
        for name, module in (("analyze", analyze), ("report_html", html), ("report_md", md)):
            monkeypatch.setitem(sys.modules, f"cgprofile.{name}", module)
            monkeypatch.setattr(cgprofile, name, module, raising=False)


def test_report_renders_an_exported_session(cli, daemon, monkeypatch, tmp_path):
    fake = FakeStageModules(monkeypatch)
    d = daemon()
    out_dir = tmp_path / "out"
    code, out, err = cli("report", SESSION_DONE, "--out", str(out_dir), "--json", "--socket", d.path)
    doc = jdoc(out)
    assert code == 0 and sorted(f["path"] for f in doc["files"]) == ["report.html", "report.md"]
    assert fake.analysis_for and os.path.basename(fake.analysis_for[0]) == SESSION_DONE
    assert (out_dir / "report.md").exists()
    code, out, err = cli("report", SESSION_DONE, "--out", str(out_dir), "--md-only", "--socket", d.path)
    assert out.strip().endswith("report.md") and "report.html" not in out
    code, out, err = cli("report", SESSION_DONE, "--out", str(out_dir), "--html-only", "--socket", d.path)
    assert out.strip().endswith("report.html")


def test_report_default_out_dir(cli, daemon, monkeypatch, tmp_path):
    FakeStageModules(monkeypatch)
    monkeypatch.chdir(tmp_path)
    d = daemon()
    code, out, err = cli("report", SESSION_DONE, "--md-only", "--socket", d.path)
    assert code == 0 and (tmp_path / f"cgprofile-report-{SESSION_DONE}" / "report.md").exists()


def test_report_export_only_writes_raw_parts_and_needs_no_report_stack(cli, daemon, tmp_path):
    d = daemon()
    target = tmp_path / "raw"
    code, out, err = cli("report", SESSION_DONE, "--export-only", str(target), "--json", "--socket", d.path)
    doc = jdoc(out)
    assert code == 0 and doc["out_dir"] == str(target)
    assert sorted(f["path"] for f in doc["files"]) == ["host.jsonl", "summary.json"]
    assert (target / "summary.json").read_bytes() == json.dumps(SUMMARY).encode()


def test_report_run_dir_needs_no_daemon(cli, monkeypatch, tmp_path):
    FakeStageModules(monkeypatch)
    run = tmp_path / "run-1"
    run.mkdir()
    code, out, err = cli("report", "--run-dir", str(run), "--json")
    doc = jdoc(out)
    assert code == 0 and doc["session"] == "run-1" and doc["out_dir"] == str(run.resolve())
    code, out, err = cli("report", "--run-dir", str(run), "--out", str(tmp_path / "o"), "--md-only")
    assert code == 0 and (tmp_path / "o" / "report.md").exists()


@pytest.mark.parametrize("extra", [["s-1"], ["--export-only", "x"]])
def test_report_run_dir_conflicts(cli, tmp_path, extra):
    code, out, err = cli("report", "--run-dir", str(tmp_path), *extra)
    assert code == 2 and "takes no session id" in err


def test_report_input_errors(cli, tmp_path, daemon):
    assert "does not exist" in cli("report", "--run-dir", str(tmp_path / "none"))[2]
    d = daemon()
    code, out, err = cli("report", "--socket", d.path)
    assert code == 2 and "needs a session id" in err


def test_render_without_the_report_extra_names_the_install(monkeypatch, tmp_path):
    import cgprofile
    monkeypatch.setitem(sys.modules, "cgprofile.analyze", None)
    monkeypatch.delattr(cgprofile, "analyze", raising=False)
    with pytest.raises(WireFailure) as caught:
        client._render(str(tmp_path / "run"), str(tmp_path / "o"), True, True)
    assert caught.value.exit_code == 1 and "pip install 'cgprofile[report]'" in caught.value.message


# ── analyze ──────────────────────────────────────────────────────────────────

def _proposal(**over):
    body = {"id": "dev-slice-uncapped", "severity": "serious", "title": "dev.slice has no cap",
            "confidence": "inferred"}
    body.update(over)
    return types.SimpleNamespace(to_dict=lambda: dict(body))


def test_analyze_prints_the_proposals_as_text_and_json(cli, monkeypatch, tmp_path):
    FakeStageModules(monkeypatch, proposals=[_proposal()])
    run = tmp_path / "run-1"
    run.mkdir()
    code, out, err = cli("analyze", str(run))
    assert code == 0 and "serious" in out and "dev-slice-uncapped" in out and "(inferred)" in out
    code, out, err = cli("analyze", str(run), "--json")
    doc = jdoc(out)
    assert code == 0 and doc["run_dir"] == str(run.resolve()) and doc["proposals"][0]["id"] == "dev-slice-uncapped"


def test_analyze_real_run_directory_prints_real_proposals_as_text_and_json(cli, run_dir_with_data):
    """Exercise the CLI, RunDir, analyzer and Proposal model against on-disk run data."""
    manifest_path = run_dir_with_data / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    subject = manifest["targets"][0]["cgroup"]
    manifest["limits"] = {
        subject: {
            "resolved": {
                "memory_high": 1_000_000,
                "strict_min": 900_000,
                "strict_low": 0,
                "recursive_min": 900_000,
                "recursive_low": 0,
                "protection_mode": "strict",
                "warnings": [],
            },
            "described": [],
            "fingerprint": "fixture",
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code, out, err = cli("analyze", str(run_dir_with_data), "--json")
    document = jdoc(out)
    assert code == 0, err
    assert document["run_dir"] == str(run_dir_with_data.resolve())
    assert len(document["proposals"]) == 1
    proposal = document["proposals"][0]
    assert proposal["id"] == f"pinned:{subject}"
    assert proposal["severity"] == "warn" and proposal["confidence"] == "inferred"
    assert proposal["evidence"] and proposal["change"]

    code, out, err = cli("analyze", str(run_dir_with_data))
    assert code == 0, err
    assert proposal["id"] in out and proposal["title"] in out and "(inferred)" in out


def test_analyze_with_no_proposals_says_so(cli, monkeypatch, tmp_path):
    FakeStageModules(monkeypatch)
    code, out, err = cli("analyze", str(tmp_path))
    assert code == 0 and out.strip() == "no proposals"


def test_analyze_input_errors(cli, tmp_path, monkeypatch):
    code, out, err = cli("analyze", str(tmp_path / "none"))
    assert code == 2 and "does not exist" in err
    import cgprofile
    monkeypatch.setitem(sys.modules, "cgprofile.analyze", None)
    monkeypatch.delattr(cgprofile, "analyze", raising=False)
    code, out, err = cli("analyze", str(tmp_path))
    assert code == 1 and "pip install 'cgprofile[report]'" in err


# ── run / mark ───────────────────────────────────────────────────────────────

def test_run_refuses_without_cgroupfs_with_the_spec_line(cli, monkeypatch):
    from cgprofile import access
    monkeypatch.setattr(access, "have_host_cgroup_view", lambda: False)
    code, out, err = cli("run", "--", "true")
    assert code == 2 and client.DIRECT_MODE_REFUSAL in err


def test_run_needs_a_command(cli):
    assert "nothing to run" in cli("run")[2]
    assert "nothing to run" in cli("run", "--")[2]


def test_run_delegates_to_direct_mode(cli, monkeypatch, tmp_path):
    from cgprofile import access, direct
    monkeypatch.setattr(access, "have_host_cgroup_view", lambda: True)
    seen = {}

    def fake_run(command, **kw):
        seen["command"] = command
        seen.update(kw)
        return direct.Outcome("/runs/r1", 7, 1.5)

    monkeypatch.setattr(direct, "run_command", fake_run)
    code, out, err = cli("run", "--out-dir", str(tmp_path), "--phase", "build", "--json", "--", "make", "-j2")
    doc = jdoc(out)
    assert code == 0 and doc["exit_status"] == 7 and doc["run_dir"] == "/runs/r1"
    assert seen["command"] == ["make", "-j2"] and seen["phase"] == "build"
    assert seen["client_dir"] == os.path.dirname(os.path.realpath(sys.argv[0]))
    code, out, err = cli("run", "--propagate-exit", "make")
    assert code == 7 and "command exited 7" in out
    assert seen["command"] == ["make"]


def test_mark_appends_to_the_run_directory(cli, monkeypatch, tmp_path):
    monkeypatch.setenv("CGPROFILE_RUN_DIR", str(tmp_path))
    code, out, err = cli("mark", "restoring", "--meta", '{"a": 1}', "--json")
    assert code == 0 and jdoc(out)["name"] == "restoring" and jdoc(out)["kind"] == "phase"
    code, out, err = cli("mark", "ready", "--kind", "event")
    assert "mark event:ready" in out


def test_mark_failures(cli, monkeypatch, tmp_path):
    code, out, err = cli("mark", "x")
    assert code == 2 and "CGPROFILE_RUN_DIR is not set" in err and "`cgprofile run`" in err
    monkeypatch.setenv("CGPROFILE_RUN_DIR", str(tmp_path / "gone"))
    assert "does not exist" in cli("mark", "x")[2]
    monkeypatch.setenv("CGPROFILE_RUN_DIR", str(tmp_path))
    assert "valid JSON" in cli("mark", "x", "--meta", "{bad")[2]
    assert "JSON object" in cli("mark", "x", "--meta", "[1]")[2]


def test_mark_imports_nothing_outside_the_stdlib_and_cli_extended(tmp_path):
    """The mark path must stay cheap and dependency-free (spec section 2.1)."""
    script = (
        "import json, os, sys\n"
        "sys.path[:0] = json.loads(sys.argv[2])\n"
        "from cgprofile.cli import cgprofile\n"
        "os.environ['CGPROFILE_RUN_DIR'] = sys.argv[1]\n"
        "code = cgprofile.main(['mark', 'm1'])\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('pandas', 'numpy', 'scipy', 'matplotlib', 'plotly', 'ruptures') or m in "
        "('cgprofile.analyze', 'cgprofile.report_html', 'cgprofile.report_md', 'cgprofile.serve'))\n"
        "print(json.dumps({'code': code, 'bad': bad}))\n"
    )
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    env = dict(os.environ)
    result = subprocess.run([sys.executable, "-I", "-c", script, str(run_dir), json.dumps(sys.path)],
                            capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"code": 0, "bad": []}


# ── skills ───────────────────────────────────────────────────────────────────

def test_skills_list_names_the_packaged_skill(cli):
    code, out, err = cli("skills", "list")
    assert code == 0 and "cgprofile" in out


def test_the_daemon_verbs_are_not_on_the_end_user_client(cli):
    for verb in ("status", "sessions", "start", "stop", "watch", "prune", "config", "doctor", "host", "show"):
        code, out, err = cli(verb)
        assert code == 2, verb
