"""B054 (DA-D3 → A-410) — a self-contradictory istanbul ``branchMap`` is a
defect of ONE FILE, not of the verdict.

The measured mechanism, from dstdns's first `javascript` lane adoption:
`@vitest/coverage-istanbul` statically instruments every file its
`coverage.include` glob matches, including files no test imports. For an
ordinary braceless single-statement `if` in such a file it emitted a
`branchMap` arc on a line that appears in NEITHER the record's `executed` nor
its `missing` bucket. Through assay 4.1.0 the parser refused
`ERROR`/`UNREADABLE_ARTIFACT` for the WHOLE artifact — so one never-executed
file with zero relation to the judged diff took down every other file's
correct, correctly-produced coverage data. That is the exact opposite of what
`changed_lines` mode promises a consumer adopting coverage incrementally.

DA-D3's disposition, per file, on the A-405 principle:

* a defective file with **no line in the judged set** is skipped and NAMED on
  the diagnostics stream — never silently;
* a defective file **inside the judged set** refuses
  `ERROR`/`UNREADABLE_ARTIFACT` naming the file and the arc line.

**On the artifact used here (A-334).** These documents are assembled by hand
from a real record's shape rather than produced by Vitest — assay's suite has
no Node toolchain (DESIGN-GUIDE §10), exactly as it has no Go one. That is
legitimate because the claim under test is **assay's own disposition rule**,
not Vitest's behaviour: A-334 forbids a test double standing in as evidence
about an external system, and the committed real artifacts under
`tests/fixtures/coverage/` are what witness what Vitest actually writes. The
contradiction is reproduced exactly as B054 recorded it — a branch arc on a
line the same record's `statementMap`/`s` does not classify at all.
"""

from __future__ import annotations

import io
import json

import pytest
from conftest import GitRepo

from assay.cli import main
from assay.coverage_parsers import coverage_istanbul_json


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


#: The judged file: `src/app.ts`, which the diff touches.
JUDGED = "$PWD/src/app.ts"
#: The bystander: `src/never_imported.ts`, matched by the coverage glob and
#: imported by no test. B054's own witness file, in miniature.
BYSTANDER = "$PWD/src/never_imported.ts"


def _clean_record(key: str) -> dict:
    """One statement on line 1 that ran, one two-armed `if` on line 1 whose
    first arm was taken. The shape every committed real istanbul fixture
    has."""
    return {
        "path": key,
        "statementMap": {
            "0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}}
        },
        "s": {"0": 1},
        "fnMap": {},
        "f": {},
        "branchMap": {
            "0": {
                "type": "if",
                "loc": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}},
                "locations": [
                    {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}},
                    {"start": {}, "end": {}},
                ],
            }
        },
        "b": {"0": [1, 0]},
    }


def _contradictory_record(key: str, *, arc_line: int) -> dict:
    """The same record with ONE thing changed: the `if` sits on *arc_line*,
    which no `statementMap` entry covers.

    This is B054's measured shape — the record classifies line 1 as code and
    nothing else, then names a branch on a line it never classified.
    """
    record = _clean_record(key)
    record["branchMap"]["0"] = {
        "type": "if",
        "line": arc_line,
        "locations": [
            {"start": {"line": arc_line}},
            {"start": {"line": arc_line}},
        ],
    }
    return record


def _covering_statements(key: str, count: int) -> dict:
    """A record whose single statement spans lines 1..4 with *count* hits —
    used for the JUDGED file, whose changed lines are 2-4."""
    record = _clean_record(key)
    record["statementMap"] = {
        "0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 9}},
        "1": {"start": {"line": 2, "column": 0}, "end": {"line": 4, "column": None}},
    }
    record["s"] = {"0": 1, "1": count}
    return record


def _document(records: dict) -> str:
    return json.dumps(records)


def _heredoc(document: str) -> str:
    """UNQUOTED heredoc, so the shell expands `$PWD` inside the document: a
    real coverage tool writes the SNAPSHOT's absolute paths, never the
    caller's. JSON carries no backticks and no other `$`-prefixed text."""
    return f"cat > coverage-final.json <<EOF\n{document}\nEOF"


def _lane(*, base: str, write_artifact: str, source_roots: str = '["src"]') -> str:
    return f"""\
schema_version = 2

[lanes.ui]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(write_artifact)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.ui.isolation]
snapshot_selection = "repository"

[lanes.ui.judge]
language = "javascript"
source_roots = {source_roots}
fail_under = 100.0
allow_excluded = false
coverage = {{ format = "coverage-istanbul-json", artifact = "coverage-final.json", producer = "istanbul" }}
base = "{base}"
"""


def _seed(git_repo: GitRepo) -> str:
    """Two commits. The second adds lines 2-4 to `src/app.ts` ONLY —
    `src/never_imported.ts` is committed in the BASE and never touched
    again, so it has no line in the judged diff.
    """
    (git_repo.path / "src").mkdir()
    git_repo.write(".gitignore", "coverage-final.json\n")
    git_repo.write("src/app.ts", "export const one = 1\n")
    git_repo.write(
        "src/never_imported.ts",
        "export const untouched = 1\n",
    )
    base_rev = git_repo.commit_all("add app.ts and never_imported.ts")
    git_repo.write(
        "src/app.ts",
        "export const one = 1\n"
        "export function two(value: number): number {\n"
        "  return value * 2\n"
        "}\n",
    )
    git_repo.commit_all("add two()")
    return base_rev


# --- the parser: isolation, not verdict-wide refusal -------------------------


def test_the_parser_isolates_the_defect_and_keeps_every_other_file(
):
    """The unit-level statement of the fix: a two-file document in which ONE
    record contradicts itself parses, and the clean record is untouched.

    Through 4.1.0 this raised and there was no profile at all.
    """
    document = _document(
        {
            "/p/src/clean.ts": _clean_record("/p/src/clean.ts"),
            "/p/src/broken.ts": _contradictory_record(
                "/p/src/broken.ts", arc_line=215
            ),
        }
    )

    profile = coverage_istanbul_json.parse(document, producer="istanbul")

    clean = profile.files["/p/src/clean.ts"]
    broken = profile.files["/p/src/broken.ts"]
    assert clean.contradictory_branch_lines is None
    assert clean.branches.by_line == {1: (1, 2)}
    assert broken.contradictory_branch_lines == frozenset({215})
    assert 215 not in broken.branches.by_line
    # The line classification the record DID get right survives.
    assert broken.executed == frozenset({1})


def test_a_clean_document_records_no_contradiction_at_all():
    """The control. `None`, not an empty frozenset: "the parser looked and
    found nothing" must be distinguishable from "there is a defect with no
    lines", which is not a state that exists."""
    document = _document({"/p/src/clean.ts": _clean_record("/p/src/clean.ts")})

    profile = coverage_istanbul_json.parse(document, producer="istanbul")

    assert profile.files["/p/src/clean.ts"].contradictory_branch_lines is None


@pytest.mark.parametrize(
    "name",
    [
        "coverage-istanbul-json.vitest-istanbul.json",
        "coverage-istanbul-json.vite-plugin-istanbul.json",
    ],
)
def test_the_committed_real_artifacts_carry_no_contradiction(name: str):
    """A-334's other direction: the isolation must not be quietly rewriting
    what a CORRECT artifact means. Both committed real arc-bearing documents
    — produced outside this repository by real tools — record no
    contradiction on any file, so nothing about their parse changed."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parent / "fixtures" / "coverage" / name
    ).read_text(encoding="utf-8")

    profile = coverage_istanbul_json.parse(text, producer="istanbul")

    assert profile.files
    assert all(
        file_cov.contradictory_branch_lines is None
        for file_cov in profile.files.values()
    )


# --- the lane: the two-file disposition, end to end through the CLI ----------


def test_a_defective_file_outside_the_judged_set_is_skipped_and_named(
    git_repo: GitRepo,
):
    """DA-D3's first half, and B054's own oracle: the lane PASSES on the
    strength of the changed-lines diff actually being fully covered, and the
    defective bystander is NAMED on the diagnostics stream.

    Through 4.1.0 this exact document produced a verdict-wide
    `ERROR`/`UNREADABLE_ARTIFACT` — the adoption blocker the entry filed.
    """
    base_rev = _seed(git_repo)
    document = _document(
        {
            JUDGED: _covering_statements(JUDGED, 3),
            BYSTANDER: _contradictory_record(BYSTANDER, arc_line=215),
        }
    )
    path = git_repo.write(
        "assay.toml", _lane(base=base_rev, write_artifact=_heredoc(document))
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    verdict = json.loads(out)
    assert verdict["outcome"] == "PASS"
    assert verdict["claims"][1]["coverage"]["pct"] == 100.0
    # NAMED, never silent: the file, and the line the dropped arc sat on.
    named = [
        line for line in err.splitlines() if "never_imported.ts" in line
    ]
    assert len(named) == 1, err
    assert "215" in named[0], named
    assert "contradicts itself" in named[0], named


def test_a_defective_file_inside_the_judged_set_refuses_and_names_the_arc_line(
    git_repo: GitRepo,
):
    """DA-D3's second half. Same lane, same defect — moved onto the file the
    diff touches. There is no honest branch number for it, so the lane
    refuses `ERROR`/`UNREADABLE_ARTIFACT` naming the file and the line."""
    base_rev = _seed(git_repo)
    judged = _covering_statements(JUDGED, 3)
    judged["branchMap"]["0"] = {
        "type": "if",
        "line": 215,
        "locations": [{"start": {"line": 215}}, {"start": {"line": 215}}],
    }
    document = _document(
        {JUDGED: judged, BYSTANDER: _clean_record(BYSTANDER)}
    )
    path = git_repo.write(
        "assay.toml", _lane(base=base_rev, write_artifact=_heredoc(document))
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    verdict = json.loads(out)
    r1 = [claim for claim in verdict["claims"] if claim["rigor"] == "R1"][0]
    assert r1["status"] == "ERROR"
    assert r1["reason_code"] == "UNREADABLE_ARTIFACT"
    assert code == verdict["exit_code"] != 0
    # B053/A-409's emitter carries the refusal's own sentence, which names
    # the file and the arc line -- so the operator is not left guessing which
    # of N files broke it (B054's "must at minimum name WHICH file").
    refusals = [
        line
        for line in err.splitlines()
        if line.startswith("assay: ERROR/UNREADABLE_ARTIFACT: ")
    ]
    assert len(refusals) == 1, err
    assert "app.ts" in refusals[0], refusals
    assert "215" in refusals[0], refusals


def test_a_lane_that_judges_nothing_defective_is_unaffected(git_repo: GitRepo):
    """The control the whole disposition rests on: identical lane, identical
    diff, a document with NO contradiction anywhere. PASS, and nothing on the
    diagnostics stream about a contradictory record — so the PASS above is
    caused by the disposition rule and not by the lane being lenient."""
    base_rev = _seed(git_repo)
    document = _document(
        {
            JUDGED: _covering_statements(JUDGED, 3),
            BYSTANDER: _clean_record(BYSTANDER),
        }
    )
    path = git_repo.write(
        "assay.toml", _lane(base=base_rev, write_artifact=_heredoc(document))
    )
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "ui", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    assert json.loads(out)["outcome"] == "PASS"
    assert "contradicts itself" not in err
