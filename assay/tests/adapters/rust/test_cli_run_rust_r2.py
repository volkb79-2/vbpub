"""B245 U8a -- a Rust R2 lane that INGESTS cargo-mutants' ``outcomes.json`` is
accepted by the CLI entry path (``assay run`` through ``cli.main`` and the real
``_built_in_registry``), runs end to end and produces a verdict ``assay verify``
accepts; a NATIVE Rust R2 lane (no ingested mutation format) and Rust R3 are
still refused before the lane's command can run.

The lane's command is a real ``/bin/sh`` that copies a staged real
cargo-mutants ``mutants.out`` into ``<out>`` and stamps it with the current
time, so the report lands inside the command's own wall-clock window exactly as
a freshly written one would.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

from conftest import GitRepo, TESTS_ROOT

from assay.cli import main

CASES = TESTS_ROOT / "fixtures" / "rust" / "cargo-mutants"

_RETIME = (
    "import datetime, json; p = 'out/mutants.out/outcomes.json'; "
    "d = json.load(open(p)); "
    "n = datetime.datetime.now(datetime.timezone.utc)"
    ".strftime('%Y-%m-%dT%H:%M:%S.%f') + '000Z'; "
    "d['start_time'] = d['end_time'] = n; json.dump(d, open(p, 'w'))"
)


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _seed(git_repo: GitRepo, case: str) -> str:
    """Commit the case's subject on top of a ``.gitignore`` commit; returns the
    base revision, so every line of the subject is an added line."""
    git_repo.write(".gitignore", "out\n")
    base = git_repo.commit_all("gitignore the output directory")
    subject = CASES / case / "subject"
    for source in sorted(p for p in subject.rglob("*") if p.is_file()):
        target = git_repo.path / source.relative_to(subject)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    git_repo.commit_all("add the subject")
    return base


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _ingested_lane(git_repo: GitRepo, case: str, tmp_path: Path, base: str) -> Path:
    staged = tmp_path / "staged" / "mutants.out"
    shutil.copytree(CASES / case / "mutants.out", staged)
    exit_code = (CASES / case / "exit-code.txt").read_text(encoding="utf-8").strip()
    script = (
        f"mkdir -p out && cp -R {staged} out/mutants.out && "
        f"{sys.executable} -c {_shell_quote(_RETIME)}; exit {exit_code}"
    )
    lane = f"""\
schema_version = 2

[lanes.mutants]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {_toml_string(script)}, "sh", "--in-diff", "{{assay.changed_diff}}", "--output", "out"]
env = {{}}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.mutants.isolation]
snapshot_selection = "repository"

[lanes.mutants.judge]
language = "rust"
mode = "changed_lines"
source_roots = ["src"]
base = "{base}"

[lanes.mutants.judge.mutation]
format = "cargo-mutants-outcomes"
artifact = "out"
fail_under = 100.0

[lanes.mutants.result_report]
format = "cargo-mutants-outcomes"
path = "out"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")
    return path


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def test_an_ingested_cargo_mutants_lane_runs_end_to_end_and_verifies(
    git_repo: GitRepo, tmp_path: Path
):
    base = _seed(git_repo, "methods")
    path = _ingested_lane(git_repo, "methods", tmp_path, base)
    verdict_path = tmp_path / "verdict.json"

    code, out, err = run(
        ["run", "mutants", "--file", str(path), "--verdict-json", str(verdict_path)]
    )

    assert code == 0, f"{out}\n{err}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    claims = {claim["rigor"]: claim for claim in document["claims"]}
    assert claims["R0"]["status"] == "PASS"
    assert claims["R2"]["status"] == "PASS"
    verify_code, verify_out, verify_err = run(["verify", str(verdict_path)])
    assert verify_code == 0, f"{verify_out}\n{verify_err}"


def _native_lane(git_repo: GitRepo, rigor: str, extra: str, marker: Path) -> Path:
    git_repo.write("src/lib.rs", "pub fn one() -> u32 {\n    1\n}\n")
    lane = f"""\
schema_version = 2

[lanes.native]
scope = "S1"
rigor = ["R0", "{rigor}"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "touch {marker}"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.native.isolation]
snapshot_selection = "repository"

[lanes.native.judge]
language = "rust"
source_roots = ["src"]
base = "HEAD"
{extra}"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")
    return path


def test_a_native_rust_r2_lane_is_refused_before_anything_runs(
    git_repo: GitRepo, tmp_path: Path
):
    """Registering Rust at R2 admits only the ingested path: a native lane must
    declare operators and no ``rust`` operator exists, so the config layer
    refuses it at load time (``BAD_LANE_CONFIG`` naming the language), exactly
    as for native ``javascript``. The marker proves the command never ran."""
    marker = tmp_path / "the-command-ran"
    path = _native_lane(
        git_repo,
        "R2",
        '\n[lanes.native.judge.mutation]\njobs = 1\nmax_mutants = 10\n'
        'operators = ["python:compare-swap"]\n',
        marker,
    )

    code, out, err = run(["run", "native", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    assert not marker.exists(), "the lane's command must never have run"
    assert "rust" in (out + err)
    assert "another language" in (out + err)


def test_a_native_rust_r2_lane_with_a_rust_prefixed_operator_is_refused(
    git_repo: GitRepo, tmp_path: Path
):
    """Same invariant through the other spelling: no ``rust`` catalogue exists,
    so a ``rust:``-prefixed operator is refused as unknown and the command
    never runs (a future ``rust`` catalogue must update this test on purpose)."""
    marker = tmp_path / "the-command-ran"
    path = _native_lane(
        git_repo,
        "R2",
        '\n[lanes.native.judge.mutation]\njobs = 1\nmax_mutants = 10\n'
        'operators = ["rust:compare-swap"]\n',
        marker,
    )

    code, out, err = run(["run", "native", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    assert not marker.exists(), "the lane's command must never have run"
    assert "rust" in (out + err)


def test_a_rust_r3_lane_is_still_refused_before_anything_runs(
    git_repo: GitRepo, tmp_path: Path
):
    marker = tmp_path / "the-command-ran"
    path = _native_lane(
        git_repo,
        "R3",
        '\n[lanes.native.judge.canary]\nmechanism = "import-break"\n'
        'target = "src/lib.rs"\n',
        marker,
    )

    code, out, err = run(["run", "native", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    assert not marker.exists(), "the lane's command must never have run"
    assert "rust" in (out + err)
    assert "R3" in (out + err)
