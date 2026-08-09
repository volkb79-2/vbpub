"""Locked P21 acceptance: v4 parity, bounded sites, output, and HEAD identity."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from assay import git as assay_git
from assay import mutation, runner, verify
from assay.cli import main
from assay.config import LaneConfigError, load_lane_file
from assay.errors import AssayError, Outcome, ReasonCode
from assay.output import reserve_verdict_output
from assay.verdict import (
    VERDICT_SCHEMA_VERSION,
    CanaryResult,
    Claim,
    Coverage,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    MutantOutcome,
    Mutation,
    Verdict,
    load_schema,
)

HERE = Path(__file__).resolve().parent
EXPECTED = HERE / "expected"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_pointer(document: dict, pointer: str, value: object) -> None:
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
    target: object = document
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]  # type: ignore[index]
    if isinstance(target, list):
        target[int(parts[-1])] = value
    else:
        target[parts[-1]] = value  # type: ignore[index]


def _invalid_documents() -> list[tuple[dict, dict]]:
    result = []
    for case in json.loads((HERE / "invalid-cases.json").read_text(encoding="utf-8")):
        document = copy.deepcopy(_load(EXPECTED / case["base"]))
        for pointer, value in case["replace"].items():
            _replace_pointer(document, pointer, value)
        result.append((case, document))
    return result


def _combined_model() -> Verdict:
    killed = MutantOutcome(
        path="src/p.py",
        lineno=7,
        start_byte=83,
        end_byte=84,
        replacement_sha256="b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866",
        operator="compare-swap",
        description="Lt->LtE",
    )
    claims = (
        Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True),
        Claim(
            rigor="R1",
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=True,
            coverage=Coverage(
                covered=1,
                changed_executable=1,
                pct=100.0,
                considered=1,
                exclusion_capability="reported",
                missing_lines={},
                files_missing_coverage=(),
                unclassified_lines={},
                files_with_unclassified_lines=(),
                excluded_lines={},
                files_with_excluded_lines=(),
            ),
        ),
        Claim(
            rigor="R2",
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=True,
            mutation=Mutation(
                candidate_count=1,
                total=1,
                killed=(killed,),
                survived=(),
                crashed=(),
                budget_exceeded=(),
            ),
        ),
        Claim(
            rigor="R3",
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=True,
            canary=CanaryResult(
                mechanism="uncovered-line",
                target="src/p.py",
                description="appended one never-called function",
                control_outcome=Outcome.PASS,
                transformed_outcome=Outcome.FAIL,
                expected_reason_code=ReasonCode.UNCOVERED_LINES,
                observed_reason_code=ReasonCode.UNCOVERED_LINES,
            ),
        ),
    )
    return Verdict(
        lane="package",
        commit="4" * 40,
        outcome=Outcome.PASS,
        started="2026-08-09T12:00:00+01:00",
        ended="2026-08-09T11:01:00+00:00",
        assay_version="0.2.0",
        declared_rigor=("R0", "R1", "R2", "R3"),
        declared_evidence=(),
        argv_declared=("python", "-m", "pytest", "tests", "-q"),
        argv_appended=(),
        argv_effective=("python", "-m", "pytest", "tests", "-q"),
        env_declared={},
        env_effective={"PATH": "/tool/bin"},
        scope="S1",
        enforcement="gate",
        judgment=Judgment(
            r1=JudgmentR1(
                language="python",
                source_roots=("src",),
                coverage_format="coverage-py-json",
                coverage_artifact="build/coverage.json",
                fail_under=100.0,
                allow_excluded=False,
                base="1" * 40,
            ),
            r2=JudgmentR2(
                jobs=2,
                max_mutants=50,
                operators=("compare-swap",),
            ),
            r3=JudgmentR3(mechanism="uncovered-line", target="src/p.py"),
        ),
        claims=claims,
        evidence=(),
    )


def test_complete_combined_v4_is_constructible_and_exact() -> None:
    assert VERDICT_SCHEMA_VERSION == 4
    assert _combined_model().to_dict() == _load(EXPECTED / "combined-pass-v4.json")


@pytest.mark.parametrize(
    "name",
    [
        "combined-pass-v4.json",
        "r1-unavailable-v4.json",
        "r2-limit-v4.json",
        "r2-unsupported-v4.json",
    ],
)
def test_complete_v4_examples_pass_schema_and_independent_raw_verifier(name: str) -> None:
    assert VERDICT_SCHEMA_VERSION == 4
    document = _load(EXPECTED / name)
    assert list(Draft202012Validator(load_schema()).iter_errors(document)) == []
    assert verify.verify_document(document) == []


@pytest.mark.parametrize(
    "case,document",
    _invalid_documents(),
    ids=lambda item: item.get("id", "document") if isinstance(item, dict) else None,
)
def test_invalid_examples_fail_every_layer_that_can_express_the_rule(
    case: dict, document: dict
) -> None:
    assert VERDICT_SCHEMA_VERSION == 4
    layers = set(case["reject_layers"])

    schema_failures = list(Draft202012Validator(load_schema()).iter_errors(document))
    assert bool(schema_failures) is ("schema" in layers)

    if "model" in layers:
        with pytest.raises((TypeError, ValueError, KeyError, AttributeError)):
            verify._reconstruct_verdict(document)
    else:
        verify._reconstruct_verdict(document)

    raw_failures = verify.verify_document(document)
    assert bool(raw_failures) is ("raw" in layers)
    if case["id"] == "verdict-old-version":
        assert len(raw_failures) == 1
        assert "schema_version 3" in raw_failures[0]
    else:
        assert all("is not this verifier's version" not in item for item in raw_failures)


def test_python_sites_match_handwritten_utf8_byte_manifest_and_bound() -> None:
    manifest = json.loads((HERE / "python-site-manifest.json").read_text(encoding="utf-8"))
    from assay.adapters.python import PythonAdapter

    for case in manifest["cases"]:
        sites = PythonAdapter().generate_mutation_sites(
            manifest["text"],
            set(manifest["lines"]),
            operators=tuple(manifest["operators"]),
            limit=case["limit"],
        )
        rendered = [
            {
                "start_byte": site.start_byte,
                "end_byte": site.end_byte,
                "replacement": site.replacement.decode("utf-8"),
                "replacement_sha256": hashlib.sha256(site.replacement).hexdigest(),
                "lineno": site.lineno,
                "operator": site.operator,
                "description": site.description,
            }
            for site in sites
        ]
        assert rendered == case["sites"]

    with pytest.raises(mutation.MutationDiscoveryError):
        PythonAdapter().generate_mutation_sites(
            "def broken(",
            {1},
            operators=("compare-swap",),
            limit=1,
        )


def test_go_unsupported_is_distinct_from_valid_empty_discovery(tmp_path: Path) -> None:
    from assay.adapters.go import GoAdapter
    from assay.mutation import MutationTarget

    baseline = SimpleNamespace(outcome=Outcome.PASS, reason_code=None)

    def forbidden_executor(_jobs: int):
        raise AssertionError("unsupported discovery must submit no mutant command")

    result = mutation.run_mutation(
        SimpleNamespace(),
        baseline=baseline,
        project_root=tmp_path,
        repo_top=tmp_path,
        scratch_root=tmp_path / "scratch",
        targets=(
            MutationTarget(
                path="p.go",
                text="package p\nfunc F() bool { return true }\n",
                lines=frozenset({2}),
            ),
        ),
        adapter=GoAdapter(),
        jobs=2,
        max_mutants=2,
        operators=("bool-const-flip",),
        executor_factory=forbidden_executor,
    )
    assert result == "UNSUPPORTED"
    claim = mutation.build_mutation_claim(baseline, result)
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.MUTATION_UNSUPPORTED
    assert claim.mutation is None

    class EmptyAdapter:
        def generate_mutation_sites(self, text, lines, *, operators, limit):
            return ()

    empty = mutation.run_mutation(
        SimpleNamespace(),
        baseline=baseline,
        project_root=tmp_path,
        repo_top=tmp_path,
        scratch_root=tmp_path / "scratch-empty",
        targets=(
            MutationTarget(path="p.py", text="x = 1\n", lines=frozenset({1})),
        ),
        adapter=EmptyAdapter(),
        jobs=2,
        max_mutants=2,
        operators=("compare-swap",),
        executor_factory=forbidden_executor,
    )
    assert empty == mutation.Mutation(
        candidate_count=0,
        total=0,
        killed=(),
        survived=(),
        crashed=(),
        budget_exceeded=(),
    )
    empty_claim = mutation.build_mutation_claim(baseline, empty)
    assert empty_claim.status is Outcome.INCONCLUSIVE
    assert empty_claim.reason_code is ReasonCode.NO_MUTANTS
    assert empty_claim.mutation is empty

    class MixedAdapter:
        def __init__(self):
            self.calls = 0

        def generate_mutation_sites(self, text, lines, *, operators, limit):
            self.calls += 1
            return () if self.calls == 1 else "UNSUPPORTED"

    with pytest.raises(mutation.MutationDiscoveryError):
        mutation.collect_mutation_sites(
            (
                MutationTarget(path="a.py", text="x = 1\n", lines=frozenset({1})),
                MutationTarget(path="b.py", text="y = 2\n", lines=frozenset({1})),
            ),
            adapter=MixedAdapter(),
            operators=("compare-swap",),
            limit=3,
        )
    assert (
        mutation.collect_mutation_sites(
            (),
            adapter=MixedAdapter(),
            operators=("compare-swap",),
            limit=3,
        )
        == ()
    )


def test_max_plus_one_is_a_zero_submission_terminal(tmp_path: Path) -> None:
    from assay.mutation import MutationSite, MutationTarget

    class ThreeSiteAdapter:
        def generate_mutation_sites(self, text, lines, *, operators, limit):
            assert operators == ("compare-swap",)
            assert limit == 3
            return tuple(
                MutationSite(
                    start_byte=start,
                    end_byte=start + 1,
                    replacement=b"z",
                    lineno=1,
                    operator="compare-swap",
                    description=f"site-{start}",
                )
                for start in (0, 2, 4)
            )

    def forbidden_executor(_jobs: int):
        raise AssertionError("no executor or mutant command is legal after max+1")

    result = mutation.run_mutation(
        SimpleNamespace(),
        baseline=SimpleNamespace(outcome=Outcome.PASS, reason_code=None),
        project_root=tmp_path,
        repo_top=tmp_path,
        scratch_root=tmp_path / "scratch",
        targets=(MutationTarget(path="p.py", text="a b c", lines=frozenset({1})),),
        adapter=ThreeSiteAdapter(),
        jobs=2,
        max_mutants=2,
        operators=("compare-swap",),
        executor_factory=forbidden_executor,
    )
    assert result == Mutation(
        candidate_count=3,
        total=0,
        killed=(),
        survived=(),
        crashed=(),
        budget_exceeded=(),
    )
    assert mutation.judge_mutation(SimpleNamespace(), result) == (
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.MUTANT_LIMIT_EXCEEDED,
    )


def _r2_config(root: Path, mutation_table: str) -> Path:
    (root / "src").mkdir(exist_ok=True)
    path = root / "assay.toml"
    path.write_text(
        """schema_version = 1
[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/true"]
env = {}
env_passthrough = []
budget = "1m"
allow_argv_append = false
[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "HEAD^"
"""
        + mutation_table,
        encoding="utf-8",
    )
    return path


def test_max_mutants_is_required_and_bounded_at_config_load(tmp_path: Path) -> None:
    missing = _r2_config(
        tmp_path,
        '[lanes.package.judge.mutation]\njobs = 1\noperators = ["compare-swap"]\n',
    )
    with pytest.raises(LaneConfigError, match="max_mutants"):
        load_lane_file(missing)

    for value in (0, 10_001, True):
        table = (
            "[lanes.package.judge.mutation]\n"
            f"jobs = 1\nmax_mutants = {str(value).lower()}\n"
            'operators = ["compare-swap"]\n'
        )
        with pytest.raises(LaneConfigError, match="max_mutants"):
            load_lane_file(_r2_config(tmp_path, table))


def _run(command: list[str], cwd: Path) -> str:
    return subprocess.run(command, cwd=cwd, check=True, stdout=subprocess.PIPE).stdout.decode().strip()


def _seed_r0_repo(root: Path, shell_command: str) -> tuple[Path, str]:
    root.mkdir()
    _run(["git", "init", "-q"], root)
    _run(["git", "config", "user.name", "fixture"], root)
    _run(["git", "config", "user.email", "fixture@example.invalid"], root)
    (root / "state.txt").write_text("before\n", encoding="utf-8")
    lane = root / "assay.toml"
    lane.write_text(
        "schema_version = 1\n"
        "[lanes.package]\n"
        'scope = "S1"\nrigor = ["R0"]\nenforcement = "gate"\n'
        f"argv = {json.dumps(['/bin/sh', '-c', shell_command])}\n"
        "env = {}\nenv_passthrough = [\"PATH\"]\nbudget = \"1m\"\n"
        "allow_argv_append = false\n",
        encoding="utf-8",
    )
    _run(["git", "add", "state.txt", "assay.toml"], root)
    _run(["git", "commit", "-q", "-m", "seed"], root)
    return lane, _run(["git", "rev-parse", "HEAD"], root)


def _clock():
    moments = iter(
        datetime(2026, 8, 9, 11, 0, second, tzinfo=timezone.utc)
        for second in range(10)
    )
    return lambda: next(moments)


def test_clean_head_move_is_head_changed_and_dirt_still_wins(tmp_path: Path) -> None:
    commit_command = (
        "printf after > state.txt && git add state.txt && "
        "git -c user.name=cmd -c user.email=cmd@example.invalid commit -q -m move"
    )
    for suffix, command, expected in (
        ("clean", commit_command, ReasonCode.HEAD_CHANGED),
        ("dirty", commit_command + " && printf dirt > leftover.txt", ReasonCode.DIRTY_TREE),
    ):
        repo = tmp_path / suffix
        lane_path, head = _seed_r0_repo(repo, command)
        lane = load_lane_file(lane_path).lane("package")
        verdict = runner.run_lane(
            lane,
            commit=head,
            repo=repo,
            project_root=repo,
            adapter=None,
            assay_version="0.2.0",
            passthrough_source={"PATH": os.environ["PATH"]},
            clock=_clock(),
        )
        assert assay_git.head_rev(repo) != head
        assert verdict.outcome is Outcome.NO_MEASUREMENT
        assert verdict.reason_code is expected


def test_cli_refuses_bad_verdict_parent_before_lane_command(tmp_path: Path) -> None:
    marker = tmp_path / "RAN"
    repo = tmp_path / "repo"
    lane_path, _head = _seed_r0_repo(repo, f"printf ran > {marker}")
    out, err = io.StringIO(), io.StringIO()
    code = main(
        [
            "run",
            "package",
            "--file",
            str(lane_path),
            "--verdict-json",
            str(tmp_path / "missing" / "verdict.json"),
        ],
        stdout=out,
        stderr=err,
    )
    assert code == Outcome.ERROR.exit_code
    assert "ERROR/OUTPUT_WRITE_FAILED" in err.getvalue()
    assert not marker.exists()


def test_reserved_destination_never_overwrites_an_object_that_appeared(tmp_path: Path) -> None:
    target = tmp_path / "verdict.json"
    destination = reserve_verdict_output(str(target), stdout=io.StringIO())
    target.write_text("intruder\n", encoding="utf-8")
    with pytest.raises(AssayError) as caught:
        destination.emit("new verdict\n")
    assert caught.value.reason_code is ReasonCode.OUTPUT_WRITE_FAILED
    assert target.read_text(encoding="utf-8") == "intruder\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["verdict.json"]
