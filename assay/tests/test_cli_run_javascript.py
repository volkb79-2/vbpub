"""B036 — a JavaScript lane through the REAL CLI, end to end: a real
two-commit git diff, a real ``coverage-final.json`` the lane's OWN command
writes, and the real ``_built_in_registry`` resolving ``javascript`` at R1.

The artifact each lane writes is a genuine istanbul document in the real key
shape (absolute paths), assembled at run time from the temp repository's own
location — the same reconciliation
``test_evaluate_javascript_end_to_end.py`` proves against the committed real
fixture, exercised here through the installed CLI instead of the pure
function.

The lane's command is ``/bin/sh``, not a real Vitest run: assay's suite has no
Node toolchain, exactly as it has no Go one (DESIGN-GUIDE §10), and what this
module tests is assay's own wiring — that a JavaScript lane resolves, judges,
passes and fails, and refuses — never Vitest's behaviour, which the committed
fixtures already witness.

Negative: an unregistered/unregistrable declaration reaching evaluation, or a
malformed artifact producing a PASS instead of a typed refusal, is the
silent-green class this whole project exists to remove.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from assay.cli import main
from conftest import GitRepo


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


#: The shell expands this to the ABSOLUTE path of the file inside whatever
#: working directory the lane's command actually ran in. That is the whole
#: point: a real coverage tool runs INSIDE assay's relocated snapshot and
#: writes the snapshot's own absolute paths, never the caller's repository
#: path -- so a fixture hardcoding the latter would be testing a shape
#: production never produces. Substituted by the unquoted heredoc in
#: :func:`heredoc`.
KEY_EXPRESSION = "$PWD/src/app.ts"


def istanbul_document(statements: list[tuple[int, int, int]]) -> str:
    """A real-shaped istanbul record for ``src/app.ts``, keyed by the
    ABSOLUTE path the tool would have written (see :data:`KEY_EXPRESSION`),
    from *statements* given as ``(start_line, end_line, count)``."""
    key = KEY_EXPRESSION
    return json.dumps(
        {
            key: {
                "path": key,
                "statementMap": {
                    str(index): {
                        "start": {"line": start, "column": 0},
                        "end": {"line": end, "column": None},
                    }
                    for index, (start, end, _count) in enumerate(statements)
                },
                "fnMap": {},
                "branchMap": {},
                "s": {
                    str(index): count
                    for index, (_start, _end, count) in enumerate(statements)
                },
                "f": {},
                "b": {},
            }
        }
    )


def lane_toml(*, rigor: str, language: str, base: str, write_artifact: str) -> str:
    return f"""\
schema_version = 2

[lanes.ui]
scope = "S1"
rigor = {rigor}
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(write_artifact)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.ui.isolation]
snapshot_selection = "repository"

[lanes.ui.judge]
language = "{language}"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = {{ format = "coverage-istanbul-json", artifact = "coverage-final.json" }}
base = "{base}"
"""


def seed_repo(git_repo: GitRepo) -> str:
    """Two commits: the second adds three lines to ``src/app.ts``. Returns
    the base revision."""
    (git_repo.path / "src").mkdir()
    # The lane's own command writes the artifact for real, so it must be
    # gitignored or the post-command dirt check refuses the run.
    git_repo.write(".gitignore", "coverage-final.json\n")
    git_repo.write("src/app.ts", "export const one = 1\n")
    base_rev = git_repo.commit_all("add app.ts")
    git_repo.write(
        "src/app.ts",
        "export const one = 1\n"
        "export function two(value: number): number {\n"
        "  return value * 2\n"
        "}\n",
    )
    git_repo.commit_all("add two()")
    return base_rev


def heredoc(document: str) -> str:
    """An UNQUOTED heredoc, so the shell expands ``$PWD`` inside the document
    (:data:`KEY_EXPRESSION`). JSON carries no backticks and no other
    ``$``-prefixed text here, so nothing else can be expanded by accident."""
    return f"cat > coverage-final.json <<EOF\n{document}\nEOF"


def test_a_javascript_r1_lane_passes_end_to_end(git_repo: GitRepo):
    """Lines 2-4 are the diff; the artifact reports all three executed. The
    key is ABSOLUTE, so this also proves the CLI reconciles the real key
    shape — without that the file reads as unmeasured and this FAILs."""
    base_rev = seed_repo(git_repo)
    document = istanbul_document([(1, 1, 1), (2, 4, 3)])
    path = git_repo.write(
        "assay.toml",
        lane_toml(
            rigor='["R0", "R1"]',
            language="javascript",
            base=base_rev,
            write_artifact=heredoc(document),
        ),
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    verdict = json.loads(out)
    assert verdict["outcome"] == "PASS"
    assert [claim["rigor"] for claim in verdict["claims"]] == ["R0", "R1"]
    coverage = verdict["claims"][1]["coverage"]
    assert coverage["pct"] == 100.0
    assert coverage["covered"] == 3
    assert coverage["executable"] == 3
    # A-343/A-344 on the wire: both capabilities are honestly unavailable for
    # this format, never "reported" with a fabricated zero.
    assert coverage["exclusion_capability"] == "unavailable"
    assert coverage["branch_capability"] == "unavailable"
    assert verdict["judgment"]["r1"]["coverage_format"] == "coverage-istanbul-json"


def test_a_javascript_r1_lane_fails_and_names_the_uncovered_lines(git_repo: GitRepo):
    """The paired failure: the same lane, the same diff, an artifact whose
    multi-line statement covering lines 2-4 was never executed."""
    base_rev = seed_repo(git_repo)
    document = istanbul_document([(1, 1, 1), (2, 4, 0)])
    path = git_repo.write(
        "assay.toml",
        lane_toml(
            rigor='["R0", "R1"]',
            language="javascript",
            base=base_rev,
            write_artifact=heredoc(document),
        ),
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    verdict = json.loads(out)
    assert verdict["outcome"] == "FAIL"
    r1 = verdict["claims"][1]
    assert r1["reason_code"] == "UNCOVERED_LINES"
    assert r1["coverage"]["pct"] == 0.0
    # Every line of the multi-line statement's own extent is named, not just
    # the line it starts on -- the parser's extent expansion reaching the
    # wire, on a real diff, through the real CLI.
    assert r1["coverage"]["missing_lines"] == {"src/app.ts": [2, 3, 4]}
    assert r1["coverage"]["files_missing_coverage"] == []


# --- refusals ---------------------------------------------------------------


def test_a_javascript_lane_declaring_r2_is_refused_before_anything_runs(
    git_repo: GitRepo, tmp_path: Path
):
    """B037's boundary, enforced: this build wires ``javascript`` at R1 only,
    so R2 is ``ERROR``/``BAD_LANE_CONFIG`` at ``get_adapter``'s choke point.
    The marker file proves the lane's command never executed."""
    base_rev = seed_repo(git_repo)
    marker = tmp_path / "the-command-ran"
    lane = f"""\
schema_version = 2

[lanes.ui]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "touch {marker}"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.ui.isolation]
snapshot_selection = "repository"

[lanes.ui.judge]
language = "javascript"
source_roots = ["src"]
base = "{base_rev}"

[lanes.ui.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:compare-swap"]
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    assert not marker.exists(), "the lane's command must never have run"
    assert "javascript" in (out + err)


def test_an_unrecognised_language_is_still_refused(git_repo: GitRepo, tmp_path: Path):
    """``"typescript"`` is the name a consumer plausibly reaches for and is
    deliberately NOT registered (A-340: one adapter, named ``javascript``,
    covers ``.ts``/``.tsx``). It is refused exactly as any unknown language
    is — this is the O2 negative, unchanged by B036 having added a language."""
    base_rev = seed_repo(git_repo)
    marker = tmp_path / "the-command-ran"
    path = git_repo.write(
        "assay.toml",
        lane_toml(
            rigor='["R0", "R1"]',
            language="typescript",
            base=base_rev,
            write_artifact=f"touch {marker}",
        ),
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    assert not marker.exists(), "the lane's command must never have run"
    assert "typescript" in (out + err)


@pytest.mark.parametrize(
    "artifact_body",
    [
        # Truncated mid-record: what a killed test runner leaves behind.
        '{"$PWD/src/app.ts": {"statementMap": {"0": {"start": {"line": 1',
        # Well-formed JSON, malformed record: a statement with no count.
        '{"$PWD/src/app.ts": {"statementMap": {"0": {"start": {"line": 1,'
        ' "column": 0}, "end": {"line": 1, "column": 9}}}, "s": {}}}',
        # Well-formed JSON, nonsense line numbers.
        '{"$PWD/src/app.ts": {"statementMap": {"0": {"start": {"line": 0,'
        ' "column": 0}, "end": {"line": 0, "column": 9}}}, "s": {"0": 1}}}',
    ],
)
def test_a_malformed_coverage_final_json_is_an_error_not_a_pass(
    git_repo: GitRepo, artifact_body: str
):
    """A broken artifact is ``ERROR``/``UNREADABLE_ARTIFACT`` through the real
    CLI — never a 0/0 PASS, and never a NO_MEASUREMENT (which would say the
    lane produced nothing, when it produced something unreadable)."""
    base_rev = seed_repo(git_repo)
    path = git_repo.write(
        "assay.toml",
        lane_toml(
            rigor='["R0", "R1"]',
            language="javascript",
            base=base_rev,
            write_artifact=heredoc(artifact_body),
        ),
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    verdict = json.loads(out)
    assert verdict["outcome"] == "ERROR"
    assert verdict["claims"][1]["reason_code"] == "UNREADABLE_ARTIFACT"


def test_a_lane_whose_command_writes_no_artifact_is_no_measurement(git_repo: GitRepo):
    """The must-distinguish control for the refusals above: "nothing was
    produced" is ``NO_MEASUREMENT``/``EMPTY_COVERAGE``, a different fact from
    "something unreadable was produced"."""
    base_rev = seed_repo(git_repo)
    path = git_repo.write(
        "assay.toml",
        lane_toml(
            rigor='["R0", "R1"]',
            language="javascript",
            base=base_rev,
            write_artifact="true",
        ),
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code != 0
    verdict = json.loads(out)
    assert verdict["claims"][1]["reason_code"] == "EMPTY_COVERAGE"
