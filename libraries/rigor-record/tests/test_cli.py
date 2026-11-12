import hashlib
import io
import json
import re
import shutil
import subprocess

import pytest
from cli_extended import CliIdentity

from conftest import ASSEMBLE, COMMIT, GOLDEN, TREE, project_git
from rigor_record import canonical_json, cli as cli_module
from rigor_record.cli import _build_cli, _now, main
from rigor_record.render import RenderError

IDENTITY = CliIdentity(name="RIGOR-RECORD", version="0.0.0", long_name="test", command="rigor-record")
PROJECT = GOLDEN / "project"


def run(*argv, monkeypatch=None):
    out, err = io.StringIO(), io.StringIO()
    code = _build_cli(IDENTITY).run(argv=list(argv), stdout=out, stderr=err, stdin=io.StringIO())
    return code, out.getvalue(), err.getvalue()


def git(repo, *args):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": str(repo), "PATH": __import__("os").environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env).stdout.strip()


@pytest.fixture
def inputs(tmp_path):
    """A real repository holding the project and the golden assemble inputs rewritten to its commit and tree."""
    repo = tmp_path / "repo"
    (repo / "cgprofile").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "cgprofile" / "x.txt").write_text("x")
    # node_modules is a second declared input of the golden assay fragment and, as in production (an assay
    # link_path), it is NOT tracked by git: it must be recorded as unresolved, never silently dropped.
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "y.txt").write_text("y")
    git(repo, "add", "cgprofile")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "one")
    git(repo, "-c", "tag.gpgsign=false", "tag", "-a", "-m", "release", "cgprofile-v2.0.0")
    commit, tree = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD:cgprofile")
    work = tmp_path / "work"
    shutil.copytree(ASSEMBLE, work)
    for path in work.rglob("*"):
        if path.is_file() and path.name not in ("record.json", "release-body.md"):
            path.write_text(path.read_text().replace(COMMIT, commit).replace(TREE, tree))
    (work / "out").mkdir()
    return {"repo": repo, "work": work, "commit": commit, "tree": tree}


def assemble_args(ctx, **replace):
    work = ctx["work"]
    args = {
        "--project": "cgprofile", "--tag": "cgprofile-v2.0.0", "--commit": ctx["commit"],
        "--project-dir": "cgprofile", "--repo": str(ctx["repo"]), "--fragments": str(work / "fragments"),
        "--review": str(work / "review.md"), "--artifacts": str(work / "build.json"),
        "--waivers": str(work / "waivers.toml"), "--published-at": "2026-11-04T18:02:11Z", "--out": str(work / "out"),
    }
    args.update(replace)
    return [item for pair in args.items() if pair[1] is not None for item in pair]


# -- assemble ---------------------------------------------------------------------------------------------------
def test_assemble_end_to_end_against_a_real_repository(inputs):
    code, out, err = run("assemble", *assemble_args(inputs))
    assert code == 0, err
    assert out.startswith("assembled cgprofile-v2.0.0: record.json (")
    record = json.loads((inputs["work"] / "out" / "record.json").read_text())
    assert record["commit"] == inputs["commit"] and record["results"][0]["targets"] == {"cgprofile": inputs["tree"]}
    body = (inputs["work"] / "out" / "release-body.md").read_text()
    assert body.startswith("| Level | Result |")


def test_assemble_records_the_declared_r2_lanes(inputs):
    code, _out, err = run("assemble", *assemble_args(inputs), "--r2-lane", "z-r2", "--r2-lane", "a-r2")
    assert code == 0, err
    record = json.loads((inputs["work"] / "out" / "record.json").read_text())
    assert record["inventory"]["r2_lanes"] == ["a-r2", "z-r2"]


def test_assemble_matches_the_golden_when_commit_and_tree_are_mapped_back(inputs):
    run("assemble", *assemble_args(inputs))
    text = (inputs["work"] / "out" / "record.json").read_text()
    # the golden record, with the one input git cannot resolve (untracked node_modules) turned into an unresolved input
    golden = json.loads((ASSEMBLE / "record.json").read_text())
    lane = next(r for r in golden["results"] if r["lane"] == "assay-r1")
    del lane["targets"]["node_modules"]
    lane["unresolved_inputs"] = ["node_modules"]
    # the review digest covers the rewritten review text, so it is the one value that legitimately differs
    rewritten = hashlib.sha256((inputs["work"] / "review.md").read_bytes()).hexdigest()
    original = hashlib.sha256((ASSEMBLE / "review.md").read_bytes()).hexdigest()
    assert text.replace(inputs["commit"], COMMIT).replace(inputs["tree"], TREE).replace(rewritten, original) == canonical_json(golden)


def test_assemble_without_published_at_uses_the_clock(inputs, monkeypatch):
    monkeypatch.setattr(cli_module, "_now", lambda: "2030-01-02T03:04:05Z")
    assert run("assemble", *assemble_args(inputs, **{"--published-at": None}))[0] == 0
    assert json.loads((inputs["work"] / "out" / "record.json").read_text())["labels"][0]["published_at"] == "2030-01-02T03:04:05Z"


def test_now_has_the_record_timestamp_shape():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", _now())


def test_assemble_without_waivers(inputs):
    assert run("assemble", *assemble_args(inputs, **{"--waivers": None}))[0] == 0


def test_assemble_refusal_names_the_causes(inputs):
    (inputs["work"] / "review.md").write_text("no marker\n")
    code, _out, err = run("assemble", *assemble_args(inputs))
    assert code == 1 and "assemble refused:" in err and "found 0" in err
    assert list((inputs["work"] / "out").iterdir()) == []


def test_assemble_refuses_a_tree_mismatch_with_a_real_repository(inputs):
    (inputs["repo"] / "cgprofile" / "x.txt").write_text("changed")
    git(inputs["repo"], "add", "-A")
    git(inputs["repo"], "-c", "commit.gpgsign=false", "commit", "-q", "-m", "two")
    git(inputs["repo"], "-c", "tag.gpgsign=false", "tag", "-a", "-m", "release", "cgprofile-v2.1.0")
    head = git(inputs["repo"], "rev-parse", "HEAD")
    for name in ("review.md", "build.json", "fragments/composite.json"):  # bind them to the new tag so only the tree can be wrong
        path = inputs["work"] / name
        path.write_text(path.read_text().replace(inputs["commit"], head))
    code, _out, err = run("assemble", *assemble_args(inputs, **{"--tag": "cgprofile-v2.1.0", "--commit": head}))
    assert code == 1 and "judged on project tree" in err


def test_assemble_out_must_be_an_empty_directory(inputs, tmp_path):
    (inputs["work"] / "out" / "stale").write_text("x")
    assert "must be an existing empty directory" in run("assemble", *assemble_args(inputs))[2]
    assert "must be an existing empty directory" in run("assemble", *assemble_args(inputs, **{"--out": str(tmp_path / "nope")}))[2]


def broken(inputs, name, text):
    (inputs["work"] / name).write_bytes(text)


def test_assemble_input_errors(inputs):
    work = inputs["work"]
    assert "cannot read review" in run("assemble", *assemble_args(inputs, **{"--review": str(work / "none")}))[2]
    assert "does not exist" in run("assemble", *assemble_args(inputs, **{"--fragments": str(work / "none")}))[2]


@pytest.mark.parametrize("name, data, expected", [
    ("build.json", b"{", "artifact inventory"),
    ("waivers.toml", b"\xff", "waivers"),
    ("waivers.toml", b"= x", "waivers"),
])
def test_assemble_unparseable_inputs(inputs, name, data, expected):
    broken(inputs, name, data)
    assert expected in run("assemble", *assemble_args(inputs))[2]


def test_assemble_fragment_dir_with_a_stray_entry(inputs):
    (inputs["work"] / "fragments" / "notes.txt").write_text("x")
    assert "not a regular .json fragment" in run("assemble", *assemble_args(inputs))[2]
    (inputs["work"] / "fragments" / "notes.txt").unlink()
    (inputs["work"] / "fragments" / "link.json").symlink_to(inputs["work"] / "fragments" / "assay-r1.json")
    assert "not a regular .json fragment" in run("assemble", *assemble_args(inputs))[2]


def test_assemble_write_failure(inputs, monkeypatch):
    def deny(self, data):
        raise PermissionError("no")

    monkeypatch.setattr(cli_module.Path, "write_bytes", deny)
    assert "cannot write" in run("assemble", *assemble_args(inputs))[2]


# -- privacy config ---------------------------------------------------------------------------------------------
def test_privacy_config_option_and_environment(inputs, tmp_path, monkeypatch):
    config = tmp_path / "privacy.toml"
    config.write_text('hosts = ["cgprofile"]\n')
    code, _out, err = run("assemble", *assemble_args(inputs, **{"--privacy-config": str(config)}))
    assert code == 1 and "privacy rule host" in err
    monkeypatch.setenv("RIGOR_RECORD_PRIVACY_CONFIG", str(config))
    assert run("assemble", *assemble_args(inputs))[0] == 1
    config.write_text("= x")
    assert "privacy config is not valid TOML" in run("assemble", *assemble_args(inputs))[2]
    monkeypatch.setenv("RIGOR_RECORD_PRIVACY_CONFIG", str(tmp_path / "missing.toml"))
    assert "cannot read privacy config" in run("assemble", *assemble_args(inputs))[2]


# -- render -----------------------------------------------------------------------------------------------------
def test_render_record_and_body_to_stdout_and_file(tmp_path):
    record = str(GOLDEN / "records" / "r2-pass.json")
    code, out, _err = run("render", "--kind", "record", "--record", record)
    assert code == 0 and out == (GOLDEN / "records" / "r2-pass.json").read_text()
    target = tmp_path / "body.md"
    assert run("render", "--kind", "body", "--record", record, "--project-dir", "cgprofile", "--out", str(target))[0] == 0
    assert target.read_text().endswith("Record: cgprofile/RIGOR.md on main\n")


def test_render_usage_errors(tmp_path):
    record = str(GOLDEN / "records" / "r2-pass.json")
    assert run("render", "--kind", "record")[0] == 2
    assert run("render", "--kind", "body", "--record", record)[0] == 2
    assert run("render", "--kind", "views")[0] == 2
    assert run("render", "--kind", "views", "--project-dir", str(tmp_path), "--out", "x")[0] == 2
    assert run("render", "--kind", "views", "--project-dir", str(tmp_path), "--record", record)[0] == 2


def test_render_refuses_an_invalid_record(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    code, _out, err = run("render", "--kind", "record", "--record", bad.as_posix())
    assert code == 1 and "render refused" in err
    bad.write_text("{")
    assert "is not valid JSON" in run("render", "--kind", "record", "--record", bad.as_posix())[2]


@pytest.fixture
def golden_git(monkeypatch):
    """The CLI reads object ids through RepoGit: serve the ones the golden project was rendered against."""
    monkeypatch.setattr(cli_module, "RepoGit", lambda _path: project_git())


def test_render_views_writes_byte_identical_files(tmp_path, golden_git):
    target = tmp_path / "proj"
    shutil.copytree(PROJECT, target)
    (target / "RIGOR.md").unlink()
    (target / "rigor" / "index.json").unlink()
    code, out, err = run("render", "--kind", "views", "--project-dir", str(target))
    assert code == 0, err
    assert out.strip() == "rendered RIGOR.md, rigor/index.json"
    assert (target / "RIGOR.md").read_text() == (PROJECT / "RIGOR.md").read_text()
    assert (target / "rigor" / "index.json").read_text() == (PROJECT / "rigor" / "index.json").read_text()


def test_render_views_refusals(tmp_path, monkeypatch, golden_git):
    assert "holds no records" in run("render", "--kind", "views", "--project-dir", str(tmp_path))[2]
    target = tmp_path / "proj"
    shutil.copytree(PROJECT, target)
    (target / "rigor" / "commits" / "junk.json").write_text("{}")
    assert "render refused" in run("render", "--kind", "views", "--project-dir", str(target))[2]
    (target / "rigor" / "commits" / "junk.json").unlink()

    def boom(*_a, **_k):
        raise RenderError(["too big"])

    monkeypatch.setattr(cli_module, "render_views", boom)
    assert "too big" in run("render", "--kind", "views", "--project-dir", str(target))[2]


# -- check ------------------------------------------------------------------------------------------------------
def test_check_ok_and_failures(tmp_path, golden_git):
    target = tmp_path / "proj"
    shutil.copytree(PROJECT, target)
    code, out, _err = run("check", "--project-dir", str(target))
    assert code == 0 and out.strip() == "ok"
    (target / "RIGOR.md").write_text("edited\n")
    code, _out, err = run("check", "--project-dir", str(target))
    assert code == 1 and "check failed:" in err and "RIGOR.md: differs from its regeneration" in err


def test_check_waivers_preflight(tmp_path):
    target = tmp_path / "proj"
    shutil.copytree(PROJECT, target)
    assert run("check", "--project-dir", str(target), "--waivers", "--release-version", "2.0.0")[0] == 0
    code, _out, err = run("check", "--project-dir", str(target), "--waivers", "--release-version", "3.0.0")
    assert code == 1 and "expired" in err
    assert run("check", "--project-dir", str(target), "--waivers")[0] == 2
    assert run("check", "--project-dir", str(target), "--release-version", "1.0.0")[0] == 2


# -- registry and entry point -----------------------------------------------------------------------------------
def test_help_lists_the_three_verbs():
    code, out, _err = run("--help")
    assert code == 0 and all(verb in out for verb in ("assemble", "render", "check"))


def test_registry_resolves_its_identity_from_the_distribution(monkeypatch):
    seen = {}

    class Fake:
        @staticmethod
        def resolve(**kwargs):
            seen.update(kwargs)
            return IDENTITY

    monkeypatch.setattr(cli_module, "CliIdentity", Fake)
    _build_cli()
    assert seen["distribution"] == "rigor-record" and seen["command"] == "rigor-record"


def test_main_runs_the_registry(monkeypatch):
    calls = []

    class Fake:
        def run(self, **kwargs):
            calls.append(kwargs)
            return 7

    monkeypatch.setattr(cli_module, "_build_cli", lambda: Fake())
    assert main(["check"], stdout=None) == 7
    assert calls == [{"argv": ["check"], "stdout": None}]


def test_main_reports_a_missing_distribution(monkeypatch, capsys):
    from cli_extended.identity import VersionLookupError

    def missing():
        raise VersionLookupError("nope")

    monkeypatch.setattr(cli_module, "_build_cli", missing)
    assert main([]) == 3
    assert "not installed as a distribution" in capsys.readouterr().err
