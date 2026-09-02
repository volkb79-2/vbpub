"""Unit tests for the WI-5 real-consumer qualification harness
(``gate/python/qualify_cmru_b006a.py``, B006(a) §6/oracle O6).

**Design.** The real harness drives four genuinely environment-bound
operations that this test suite cannot and must not reproduce at unit-test
speed: the tester-unified M20 full-suite preflight, the repaired-node-alone
run (both need the real ``/opt/tester-venv`` interpreter and CMRU's real
dependency closure), the installed-wheel ``assay run`` invocation (needs a
real venv with the candidate wheel plus its mutation/canary machinery), and
the direct ``assay.isolation`` snapshot probe (same). Those four functions --
:func:`run_m20_preflight`, :func:`run_repaired_node`,
:func:`invoke_qualification_lane`, :func:`probe_snapshot_omissions` -- are
the module's own named "subprocess boundary" seams and are the ONLY things
this suite stubs.

Everything else -- input-drift refusal, the full-repository export, the
disposable repository's git history (seed / repair / controlled head), every
diff-shape check, and both pure verification functions
(:func:`check_qualification_artifact`, :func:`check_snapshot_probe`) -- runs
for REAL against a tiny synthetic monorepo this suite builds once per test
with real ``git``, so the harness's own control flow is proven against real
git plumbing rather than a second layer of mocks.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "gate" / "python" / "qualify_cmru_b006a.py"
_SPEC = importlib.util.spec_from_file_location("qualify_cmru_b006a", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
q = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = q
_SPEC.loader.exec_module(q)


#: The EXACT real ``cmru/tests/test_release_final_contracts.py`` bytes at
#: INPUT_REVISION (A-232: measured, not paraphrased) -- reused verbatim so
#: the fixture's sha256 matches the module's frozen
#: ``ORIGINAL_TEST_FILE_SHA256`` without touching that production constant.
_REAL_RELEASE_TEST_CONTENT = (
    '"""Exact witnesses for release REST request and pagination contracts."""\n'
    "from __future__ import annotations\n"
    "\n"
    "import json\n"
    "\n"
    "import pytest\n"
    "\n"
    "from cmru.release import GitHubReleases\n"
    "\n"
    "\n"
    "def test_release_create_includes_explicit_target_commitish_in_json_payload():\n"
    '    client = GitHubReleases("owner", "repo", "token")\n'
    "    seen = {}\n"
    "    def request(method, url, data=None, content_type=None):\n"
    "        seen.update(method=method, url=url, data=data, content_type=content_type)\n"
    '        return 201, \'{"id":7}\'\n'
    "    client._request = request\n"
    '    assert client.create_release("demo-v1", "Demo 1", "notes", "abc123") == {"id": 7}\n'
    '    payload = json.loads(seen["data"])\n'
    '    assert seen["method"] == "POST" and payload["target_commitish"] == "abc123"\n'
    "\n"
    "\n"
    "def test_release_list_paginates_until_short_page():\n"
    '    client = GitHubReleases("owner", "repo", "token")\n'
    "    pages = iter([\n"
    '        (200, json.dumps([{"id": 1}, {"id": 2}])),\n'
    '        (200, json.dumps([{"id": 3}])),\n'
    "    ])\n"
    "    client._request = lambda *args, **kwargs: next(pages)\n"
    '    assert client.list_releases(per_page=2) == [{"id": 1}, {"id": 2}, {"id": 3}]\n'
    "\n"
    "\n"
    "def test_release_publish_rejects_response_without_upload_coordinate():\n"
    '    client = GitHubReleases("owner", "repo", "token")\n'
    '    client.get_release_by_tag = lambda tag: {"id": 7}\n'
    "    with pytest.raises(SystemExit) as error:\n"
    '        client.publish("demo-v1", "title", "notes", [])\n'
    "    assert error.value.code == 1\n"
)

assert hashlib.sha256(_REAL_RELEASE_TEST_CONTENT.encode("utf-8")).hexdigest() == q.ORIGINAL_TEST_FILE_SHA256


def _build_source_repo(tmp_path: Path) -> Path:
    """A tiny synthetic monorepo shaped exactly like the real one at the
    paths this harness reads: the real release-test file (so the frozen
    sha256 matches), the two CMRU root dependencies, and the three real
    unsafe Topos symlinks -- kept tracked, never deleted."""
    repo = tmp_path / "source"
    repo.mkdir()
    q._run(["git", "init", "-q", "-b", "main"], cwd=repo)
    (repo / ".gitignore").write_text(
        "__pycache__/\n.coverage\n.coverage.*\n.pytest_cache/\n.assay/\ncoverage.json\n",
        encoding="utf-8",
    )
    (repo / "cmru" / "src" / "cmru").mkdir(parents=True)
    (repo / "cmru" / "src" / "cmru" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "cmru" / "tests").mkdir(parents=True)
    (repo / "cmru" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "cmru" / "tests" / "test_release_final_contracts.py").write_text(
        _REAL_RELEASE_TEST_CONTENT, encoding="utf-8"
    )
    (repo / "cmru.project.sample.toml").write_text("# sample\n", encoding="utf-8")
    (repo / "cmru.release.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    for relative in q.UNSAFE_SYMLINK_OMISSIONS:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink("/etc/passwd", target)
    (repo / "topos" / "src" / "topos").mkdir(parents=True)
    (repo / "topos" / "src" / "topos" / "__init__.py").write_text("", encoding="utf-8")
    q._run(["git", "add", "-A", "."], cwd=repo)
    q._run(["git", "commit", "-q", "-m", "fixture input"], cwd=repo, env=q._fixed_identity())
    return repo


def _pin_frozen_inputs(monkeypatch: pytest.MonkeyPatch, source_repo: Path) -> str:
    """Monkeypatch the module's frozen-input identity constants to match the
    tiny fixture repo, and return its HEAD oid."""
    head = q._git(source_repo, "rev-parse", "HEAD")
    cmru_tree = q._git(source_repo, "rev-parse", "HEAD:cmru")
    topos_tree = q._git(source_repo, "rev-parse", "HEAD:topos")
    tracked_count = len(q._git(source_repo, "ls-files").splitlines())
    monkeypatch.setattr(q, "INPUT_REVISION", head)
    monkeypatch.setattr(q, "CMRU_TREE", cmru_tree)
    monkeypatch.setattr(q, "TOPOS_TREE", topos_tree)
    monkeypatch.setattr(q, "TRACKED_COUNT", tracked_count)
    return head


# --- verify_pinned_inputs ----------------------------------------------------


def test_verify_pinned_inputs_accepts_the_exact_frozen_commit(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    q.verify_pinned_inputs(source_repo)  # must not raise


def test_verify_pinned_inputs_refuses_a_wrong_input_revision_git_cannot_resolve(tmp_path, monkeypatch):
    """Named differential: 'no marker on wrong input OID' (WI-5's own unit
    test contract). ``INPUT_REVISION`` names a commit the fixture repo does
    not contain at all, so git itself refuses to resolve it."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    monkeypatch.setattr(q, "INPUT_REVISION", "a" * 40)
    with pytest.raises(q.QualificationError, match="unknown revision"):
        q.verify_pinned_inputs(source_repo)


def test_verify_pinned_inputs_refuses_a_resolvable_oid_with_the_wrong_spelling(tmp_path, monkeypatch):
    """The internal exact-spelling comparison itself: an ABBREVIATED oid that
    git happily resolves, but to a full 40-hex string that disagrees with the
    frozen literal -- proving assay demands the exact spelling, not merely a
    reachable one."""
    source_repo = _build_source_repo(tmp_path)
    full = _pin_frozen_inputs(monkeypatch, source_repo)
    monkeypatch.setattr(q, "INPUT_REVISION", full[:12])
    with pytest.raises(q.QualificationError, match="INPUT_REVISION"):
        q.verify_pinned_inputs(source_repo)


def test_verify_pinned_inputs_refuses_a_wrong_cmru_tree(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    monkeypatch.setattr(q, "CMRU_TREE", "b" * 40)
    with pytest.raises(q.QualificationError, match="CMRU_TREE"):
        q.verify_pinned_inputs(source_repo)


def test_verify_pinned_inputs_refuses_a_wrong_topos_tree(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    monkeypatch.setattr(q, "TOPOS_TREE", "c" * 40)
    with pytest.raises(q.QualificationError, match="TOPOS_TREE"):
        q.verify_pinned_inputs(source_repo)


def test_verify_pinned_inputs_refuses_a_drifted_release_test_file(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    monkeypatch.setattr(q, "ORIGINAL_TEST_FILE_SHA256", "0" * 64)
    with pytest.raises(q.QualificationError, match="drifted"):
        q.verify_pinned_inputs(source_repo)


# --- _extract_full_repository / seed_disposable_repository -------------------


def test_extract_full_repository_refuses_when_git_archive_fails(tmp_path):
    with pytest.raises(q.QualificationError, match="full-repository export failed"):
        q._extract_full_repository(tmp_path / "not-a-repo", tmp_path / "dest")


def test_seed_disposable_repository_materialises_the_whole_tree_symlinks_kept(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    repository, input_oid = q.seed_disposable_repository(source_repo, scratch)
    assert len(input_oid) == 40
    for relative in q.UNSAFE_SYMLINK_OMISSIONS:
        path = repository / relative
        assert path.is_symlink()
        assert os.readlink(path) == "/etc/passwd"
    assert (repository / "cmru.project.sample.toml").is_file()
    assert (repository / "cmru.release.sh").is_file()
    assert (repository / "topos" / "src" / "topos" / "__init__.py").is_file()
    assert q._git(repository, "status", "--porcelain=v1") == ""


def test_seed_disposable_repository_refuses_a_tracked_count_mismatch(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    monkeypatch.setattr(q, "TRACKED_COUNT", 999999)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(q.QualificationError, match="force-indexed"):
        q.seed_disposable_repository(source_repo, scratch)


def test_seed_disposable_repository_refuses_a_missing_unsafe_symlink(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    # Replace one declared symlink with an ordinary file BEFORE freezing the
    # input identity, so the frozen tree hashes stay self-consistent.
    victim = source_repo / q.UNSAFE_SYMLINK_OMISSIONS[0]
    victim.unlink()
    victim.write_text("not a symlink\n", encoding="utf-8")
    q._run(["git", "add", "-A", "."], cwd=source_repo)
    q._run(
        ["git", "commit", "-q", "-m", "corrupt one omission"],
        cwd=source_repo,
        env=q._fixed_identity(),
    )
    _pin_frozen_inputs(monkeypatch, source_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(q.QualificationError, match="must stay tracked"):
        q.seed_disposable_repository(source_repo, scratch)


def test_seed_disposable_repository_refuses_a_retargeted_unsafe_symlink(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    victim = source_repo / q.UNSAFE_SYMLINK_OMISSIONS[0]
    victim.unlink()
    os.symlink("/etc/shadow", victim)
    q._run(["git", "add", "-A", "."], cwd=source_repo)
    q._run(
        ["git", "commit", "-q", "-m", "retarget one omission"],
        cwd=source_repo,
        env=q._fixed_identity(),
    )
    _pin_frozen_inputs(monkeypatch, source_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(q.QualificationError, match="drifted"):
        q.seed_disposable_repository(source_repo, scratch)


# --- apply_qualification_baseline_repair --------------------------------


def _seeded_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    repository, input_oid = q.seed_disposable_repository(source_repo, scratch)
    return source_repo, repository, input_oid


def test_apply_qualification_baseline_repair_writes_frozen_blob_plus_insertion(tmp_path, monkeypatch):
    _source, repository, input_oid = _seeded_repository(tmp_path, monkeypatch)
    baseline_oid = q.apply_qualification_baseline_repair(repository)
    assert len(baseline_oid) == 40 and baseline_oid != input_oid
    patched = (repository / q._RELEASE_TEST_PATH).read_bytes()
    expected = _REAL_RELEASE_TEST_CONTENT.encode("utf-8").replace(
        q._ANCHOR_LINE.encode("utf-8"),
        q._ANCHOR_LINE.encode("utf-8") + q._REPAIR_LINE.encode("utf-8"),
        1,
    )
    assert patched == expected
    assert q._diff_names(repository, input_oid, baseline_oid) == [q._RELEASE_TEST_PATH]


def test_apply_qualification_baseline_repair_refuses_drifted_seed_bytes(tmp_path, monkeypatch):
    _source, repository, _input_oid = _seeded_repository(tmp_path, monkeypatch)
    (repository / q._RELEASE_TEST_PATH).write_text("drifted content\n", encoding="utf-8")
    with pytest.raises(q.QualificationError, match="drifted from the frozen"):
        q.apply_qualification_baseline_repair(repository)


def test_apply_qualification_baseline_repair_refuses_a_duplicated_anchor(tmp_path, monkeypatch):
    _source, repository, _input_oid = _seeded_repository(tmp_path, monkeypatch)
    duplicated = _REAL_RELEASE_TEST_CONTENT + q._ANCHOR_LINE
    monkeypatch.setattr(
        q, "ORIGINAL_TEST_FILE_SHA256", hashlib.sha256(duplicated.encode("utf-8")).hexdigest()
    )
    (repository / q._RELEASE_TEST_PATH).write_text(duplicated, encoding="utf-8")
    with pytest.raises(q.QualificationError, match="exactly one occurrence"):
        q.apply_qualification_baseline_repair(repository)


# --- run_m20_preflight / run_repaired_node (real subprocess, synthetic tests) -


def _write_pytest_module(repository: Path, *, passing: bool) -> None:
    (repository / "cmru" / "tests").mkdir(parents=True, exist_ok=True)
    (repository / "cmru" / "tests" / "__init__.py").write_text("", encoding="utf-8")
    body = "def test_x():\n    assert " + ("True" if passing else "False") + "\n"
    (repository / "cmru" / "tests" / "test_x.py").write_text(body, encoding="utf-8")


def test_run_m20_preflight_reports_success_for_real(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_TESTER_VENV_PYTHON", sys.executable)
    repository = tmp_path / "repo"
    _write_pytest_module(repository, passing=True)
    proc = q.run_m20_preflight(repository)
    assert proc.returncode == 0
    assert "1 passed" in proc.stdout


def test_run_m20_preflight_reports_failure_for_real(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_TESTER_VENV_PYTHON", sys.executable)
    repository = tmp_path / "repo"
    _write_pytest_module(repository, passing=False)
    proc = q.run_m20_preflight(repository)
    assert proc.returncode != 0
    assert "1 failed" in proc.stdout


def test_run_repaired_node_runs_the_named_node_for_real(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_TESTER_VENV_PYTHON", sys.executable)
    monkeypatch.setattr(q, "_RELEASE_TEST_NODE", "tests/test_x.py::test_x")
    repository = tmp_path / "repo"
    _write_pytest_module(repository, passing=True)
    proc = q.run_repaired_node(repository)
    assert proc.returncode == 0


def test_failing_node_lines_extracts_failed_and_error_prefixed_lines():
    stdout = "FAILED tests/test_x.py::test_x - assert False\nsome other line\n"
    stderr = "ERROR tests/test_y.py::test_y - boom\nnoise\n"
    lines = q._failing_node_lines(stdout, stderr)
    assert lines == [
        "FAILED tests/test_x.py::test_x - assert False",
        "ERROR tests/test_y.py::test_y - boom",
    ]


# --- build_controlled_head ------------------------------------------------


def test_build_controlled_head_touches_exactly_the_declared_three_files(tmp_path, monkeypatch):
    _source, repository, _input_oid = _seeded_repository(tmp_path, monkeypatch)
    baseline_oid = q.apply_qualification_baseline_repair(repository)
    head_oid = q.build_controlled_head(repository, baseline_oid)
    assert q._diff_names(repository, baseline_oid, head_oid, pathspec="cmru/src") == [
        q._PROBE_MODULE_PATH
    ]
    assert sorted(q._diff_names(repository, baseline_oid, head_oid)) == sorted(
        [q._PROBE_MODULE_PATH, q._PROBE_TEST_PATH, q._LANE_PATH]
    )
    lane_text = (repository / q._LANE_PATH).read_text(encoding="utf-8")
    assert f'base = "{baseline_oid}"' in lane_text
    assert q._git(repository, "status", "--porcelain=v1") == ""


# --- check_qualification_artifact / check_snapshot_probe (pure functions) ----


def _pass_claim(rigor: str, **payload) -> dict:
    return {"rigor": rigor, "status": "PASS", **payload}


def _good_artifact() -> dict:
    return {
        "outcome": "PASS",
        "snapshot_policy": {
            "selection": "repository-minus-unsafe-symlinks",
            "unsafe_symlink_omissions": list(q.UNSAFE_SYMLINK_OMISSIONS),
        },
        "claims": [
            _pass_claim("R0"),
            _pass_claim("R1", coverage={"executable": 2}),
            _pass_claim(
                "R2",
                mutation={
                    "candidate_count": 1,
                    "total": 1,
                    "killed": [
                        {
                            "path": "src/cmru/_b006a_probe.py",
                            "lineno": 2,
                            "operator": "python:compare-swap",
                            "description": "Eq->NotEq",
                        }
                    ],
                    "survived": [],
                    "crashed": [],
                    "budget_exceeded": [],
                    "equivalent": [],
                },
            ),
            _pass_claim(
                "R3",
                # (verdict v10 / B007 / A-432) `{mechanism, attempts[]}`, one
                # entry per declared target; this lane declares exactly one.
                canary={
                    "mechanism": "uncovered-line",
                    "attempts": [
                        {
                            "target": "src/cmru/_b006a_probe.py",
                            "disposition": "attempted",
                            "control_outcome": "PASS",
                            "transformed_outcome": "FAIL",
                            "expected_reason_code": "UNCOVERED_LINES",
                            "observed_reason_code": "UNCOVERED_LINES",
                        }
                    ],
                },
            ),
        ],
        # The judgment's declared target list is half of the pairing
        # `check_qualification_artifact` now checks against the attempt's own
        # target; `aggregation` is absent because one probe declares none.
        "judgment": {
            "r3": {
                "mechanism": "uncovered-line",
                "targets": ["src/cmru/_b006a_probe.py"],
            }
        },
    }


def test_check_qualification_artifact_accepts_the_true_shape_and_returns_identity():
    identity = q.check_qualification_artifact(_good_artifact())
    assert identity["operator"] == "python:compare-swap"


def test_check_qualification_artifact_refuses_non_pass_outcome():
    artifact = _good_artifact()
    artifact["outcome"] = "FAIL"
    with pytest.raises(q.QualificationError, match="expected the qualification lane to PASS"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_failed_claim():
    artifact = _good_artifact()
    artifact["claims"][1]["status"] = "FAIL"
    with pytest.raises(q.QualificationError, match=r"R1 claim did not PASS"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_missing_claim():
    artifact = _good_artifact()
    artifact["claims"] = [c for c in artifact["claims"] if c["rigor"] != "R3"]
    with pytest.raises(q.QualificationError, match="no R3 claim"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_zero_executable_lines():
    artifact = _good_artifact()
    artifact["claims"][1]["coverage"]["executable"] = 0
    with pytest.raises(q.QualificationError, match="R1 claim reports executable"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_wrong_candidate_count():
    artifact = _good_artifact()
    artifact["claims"][2]["mutation"]["candidate_count"] = 2
    with pytest.raises(q.QualificationError, match="candidate_count/total"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_zero_killed_identities():
    artifact = _good_artifact()
    artifact["claims"][2]["mutation"]["killed"] = []
    with pytest.raises(q.QualificationError, match="exactly one killed"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_surviving_mutant():
    """Named differential: 'no marker on ... surviving mutant'."""
    artifact = _good_artifact()
    artifact["claims"][2]["mutation"]["survived"] = [{"path": "x", "lineno": 1}]
    with pytest.raises(q.QualificationError, match=r"'survived' bucket is not empty"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_control_not_pass():
    artifact = _good_artifact()
    artifact["claims"][3]["canary"]["attempts"][0]["control_outcome"] = "FAIL"
    with pytest.raises(q.QualificationError, match="R3 control did not PASS"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_transformed_not_fail():
    artifact = _good_artifact()
    artifact["claims"][3]["canary"]["attempts"][0]["transformed_outcome"] = "PASS"
    with pytest.raises(q.QualificationError, match="R3 transformed did not FAIL"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_wrong_canary_reason():
    """Named differential: 'no marker on ... wrong canary reason'."""
    artifact = _good_artifact()
    artifact["claims"][3]["canary"]["attempts"][0]["observed_reason_code"] = "COMMAND_FAILED"
    with pytest.raises(q.QualificationError, match="R3 reason code mismatch"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_second_canary_attempt():
    """(verdict v10 / B007) The lane this harness drives declares ONE canary
    target. A second attempt means the lane stopped being that lane, so the
    length is asserted rather than indexed past -- reading ``attempts[0]``
    and ignoring the rest would let the harness keep passing while the thing
    it qualifies changed underneath it."""
    artifact = _good_artifact()
    canary = artifact["claims"][3]["canary"]
    canary["attempts"] = list(canary["attempts"]) + [
        dict(canary["attempts"][0], target="src/cmru/_b006a_probe_two.py")
    ]
    with pytest.raises(q.QualificationError, match="single-attempt payload"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_target_the_judgment_never_declared():
    """(verdict v10 / A-432) The attempt's target and ``judgment.r3.targets``
    are one pairing, and it is checked here on a REAL artifact rather than
    trusted from ``verify.py``'s own re-derivation."""
    artifact = _good_artifact()
    artifact["judgment"]["r3"]["targets"] = ["src/cmru/somewhere_else.py"]
    with pytest.raises(q.QualificationError, match="does not declare exactly the one target"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_an_aggregation_over_one_probe():
    """(verdict v10 / A-432) With one declared probe ``any`` and ``all``
    denote the same function, so recording either would record a policy the
    lane never stated."""
    artifact = _good_artifact()
    artifact["judgment"]["r3"]["aggregation"] = "any"
    with pytest.raises(q.QualificationError, match="with no aggregation"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_not_attempted_disposition():
    """(verdict v10 / B007) A probe that was never run is not a control that
    passed; the disposition fork is checked before any run field is read."""
    artifact = _good_artifact()
    attempt = artifact["claims"][3]["canary"]["attempts"][0]
    attempt["disposition"] = "not_attempted"
    with pytest.raises(q.QualificationError, match="was not attempted"):
        q.check_qualification_artifact(artifact)


def test_check_qualification_artifact_refuses_a_missing_omission():
    """Named differential: 'no marker on ... missing omission'."""
    artifact = _good_artifact()
    artifact["snapshot_policy"]["unsafe_symlink_omissions"] = list(q.UNSAFE_SYMLINK_OMISSIONS[:2])
    with pytest.raises(q.QualificationError, match="snapshot_policy does not match"):
        q.check_qualification_artifact(artifact)


def _good_probe() -> dict:
    return {
        "omitted_absent": [True, True, True],
        "cmru_root_present": True,
        "topos_ordinary_present": True,
        "status_clean": True,
    }


def test_check_snapshot_probe_accepts_the_true_shape():
    q.check_snapshot_probe(_good_probe())  # must not raise


def test_check_snapshot_probe_refuses_when_an_omitted_leaf_is_present():
    probe = _good_probe()
    probe["omitted_absent"] = [True, True, False]
    with pytest.raises(q.QualificationError, match="did not observe all three leaves absent"):
        q.check_snapshot_probe(probe)


def test_check_snapshot_probe_refuses_missing_root_dependencies():
    probe = _good_probe()
    probe["cmru_root_present"] = False
    with pytest.raises(q.QualificationError, match="CMRU root dependencies"):
        q.check_snapshot_probe(probe)


def test_check_snapshot_probe_refuses_missing_ordinary_topos_file():
    probe = _good_probe()
    probe["topos_ordinary_present"] = False
    with pytest.raises(q.QualificationError, match="ordinary Topos file"):
        q.check_snapshot_probe(probe)


def test_check_snapshot_probe_refuses_a_dirty_snapshot():
    probe = _good_probe()
    probe["status_clean"] = False
    with pytest.raises(q.QualificationError, match="was not clean"):
        q.check_snapshot_probe(probe)


# --- qualify(): end-to-end orchestration, real git + four stubbed boundaries -


def _install_happy_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The four real-environment boundaries, each stubbed to the outcome a
    genuinely successful tester-unified/installed-wheel run would produce.
    Real git (seeding, repair, controlled-head, diffs) still runs for real."""

    def fake_m20(repository: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["fake-m20"], returncode=0, stdout="", stderr="")

    def fake_repaired(repository: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["fake-repaired"], returncode=0, stdout="", stderr="")

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = q._git(repository, "rev-parse", "HEAD")
        artifact["assay_version"] = "9.9.9"
        artifact["exit_code"] = 0
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=0, stdout="", stderr="")
        return proc, artifact

    def fake_probe(assay_executable, repository, commit, scratch):
        return _good_probe()

    monkeypatch.setattr(q, "run_m20_preflight", fake_m20)
    monkeypatch.setattr(q, "run_repaired_node", fake_repaired)
    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    monkeypatch.setattr(q, "probe_snapshot_omissions", fake_probe)


def test_qualify_succeeds_when_every_boundary_agrees(tmp_path, monkeypatch):
    """The must-succeed control: without it, a qualify() that refused
    everything would score perfectly on every negative test below."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    scratch = tmp_path / "scratch"
    before = q._consumer_status(source_repo)

    result = q.qualify(
        source_repo=source_repo,
        scratch=scratch,
        current_assay=Path("/nonexistent/bin/assay"),
        current_version="9.9.9",
    )

    assert result["killed_identity"]["operator"] == "python:compare-swap"
    assert q._consumer_status(source_repo) == before == ""


def test_qualify_raises_on_failed_m20_preflight(tmp_path, monkeypatch):
    """Named differential: 'no marker on ... failed M20 preflight'."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        q,
        "run_m20_preflight",
        lambda repository: subprocess.CompletedProcess(
            args=["fake-m20"], returncode=1, stdout="FAILED tests/x.py::y\n", stderr=""
        ),
    )

    def _unreachable_repaired_node(repository: Path) -> subprocess.CompletedProcess[str]:
        calls.append("repaired")
        return subprocess.CompletedProcess(args=["unreachable"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(q, "run_repaired_node", _unreachable_repaired_node)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="M20 preflight failed"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )
    assert calls == []


def test_qualify_raises_on_a_failed_repaired_node(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    monkeypatch.setattr(
        q,
        "run_repaired_node",
        lambda repository: subprocess.CompletedProcess(
            args=["fake-repaired"], returncode=1, stdout="", stderr=""
        ),
    )
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="did not PASS in isolation"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_a_surviving_mutant(tmp_path, monkeypatch):
    """Named differential: 'no marker on ... surviving mutant'."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = q._git(repository, "rev-parse", "HEAD")
        artifact["assay_version"] = "9.9.9"
        artifact["exit_code"] = 0
        artifact["claims"][2]["mutation"]["survived"] = [{"path": "x", "lineno": 1}]
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=0, stdout="", stderr="")
        return proc, artifact

    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match=r"'survived' bucket is not empty"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_wrong_canary_reason(tmp_path, monkeypatch):
    """Named differential: 'no marker on ... wrong canary reason'."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = q._git(repository, "rev-parse", "HEAD")
        artifact["assay_version"] = "9.9.9"
        artifact["exit_code"] = 0
        artifact["claims"][3]["canary"]["attempts"][0]["observed_reason_code"] = "COMMAND_FAILED"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=0, stdout="", stderr="")
        return proc, artifact

    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="R3 reason code mismatch"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_a_missing_omission(tmp_path, monkeypatch):
    """Named differential: 'no marker on ... missing omission'."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = q._git(repository, "rev-parse", "HEAD")
        artifact["assay_version"] = "9.9.9"
        artifact["exit_code"] = 0
        artifact["snapshot_policy"]["unsafe_symlink_omissions"] = list(q.UNSAFE_SYMLINK_OMISSIONS[:2])
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=0, stdout="", stderr="")
        return proc, artifact

    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="snapshot_policy does not match"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_a_dirty_probe_leaf(tmp_path, monkeypatch):
    """The probe-level twin of the missing-omission differential: the CLI
    run's OWN artifact matches, but the independent low-level probe observes
    one declared leaf still present."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    monkeypatch.setattr(
        q,
        "probe_snapshot_omissions",
        lambda assay_executable, repository, commit, scratch: {
            **_good_probe(),
            "omitted_absent": [True, False, True],
        },
    )
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="did not observe all three leaves absent"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_wrong_artifact_commit(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = "f" * 40
        artifact["assay_version"] = "9.9.9"
        artifact["exit_code"] = 0
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=0, stdout="", stderr="")
        return proc, artifact

    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="not the disposable controlled head"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_wrong_artifact_version(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = q._git(repository, "rev-parse", "HEAD")
        artifact["assay_version"] = "0.0.1"
        artifact["exit_code"] = 0
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=0, stdout="", stderr="")
        return proc, artifact

    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="does not match the installed owner"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_when_process_exit_disagrees_with_artifact(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_invoke(assay_executable, repository, artifact_path):
        artifact = _good_artifact()
        artifact["commit"] = q._git(repository, "rev-parse", "HEAD")
        artifact["assay_version"] = "9.9.9"
        artifact["exit_code"] = 0
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        proc = subprocess.CompletedProcess(args=["fake-assay"], returncode=1, stdout="", stderr="")
        return proc, artifact

    monkeypatch.setattr(q, "invoke_qualification_lane", fake_invoke)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="disagrees with the artifact"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_when_the_disposable_repository_is_left_dirty(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_probe(assay_executable, repository, commit, scratch):
        (repository / "STRAY-UNTRACKED-FILE").write_text("dirt\n", encoding="utf-8")
        return _good_probe()

    monkeypatch.setattr(q, "probe_snapshot_omissions", fake_probe)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="left dirty"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_source_checkout_dirt(tmp_path, monkeypatch):
    """Named differential: 'no marker on ... source-checkout dirt'. Simulates
    an accidental write to the REAL checkout during a boundary call."""
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)

    def fake_probe(assay_executable, repository, commit, scratch):
        (source_repo / "cmru" / "STRAY-REAL-CHECKOUT-WRITE").write_text("oops\n", encoding="utf-8")
        return _good_probe()

    monkeypatch.setattr(q, "probe_snapshot_omissions", fake_probe)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="real cmru/topos checkout changed"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )
    (source_repo / "cmru" / "STRAY-REAL-CHECKOUT-WRITE").unlink()


def test_qualify_raises_on_repair_diff_shape_mismatch(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    real_repair = q.apply_qualification_baseline_repair

    def extra_file_repair(repository: Path) -> str:
        (repository / "cmru" / "STRAY.txt").write_text("stray\n", encoding="utf-8")
        q._run(["git", "add", "cmru/STRAY.txt"], cwd=repository)
        oid = real_repair(repository)
        return oid

    monkeypatch.setattr(q, "apply_qualification_baseline_repair", extra_file_repair)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="touched more than the one test file"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_controlled_head_src_diff_shape_mismatch(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    real_build = q.build_controlled_head

    def extra_src_build(repository: Path, baseline_oid: str) -> str:
        extra = repository / "cmru" / "src" / "cmru" / "_extra.py"
        extra.write_text("EXTRA = 1\n", encoding="utf-8")
        q._run(["git", "add", "cmru/src/cmru/_extra.py"], cwd=repository)
        return real_build(repository, baseline_oid)

    monkeypatch.setattr(q, "build_controlled_head", extra_src_build)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="cmru/src diff is not exactly the probe module"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


def test_qualify_raises_on_controlled_head_full_diff_shape_mismatch(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    real_build = q.build_controlled_head

    def extra_root_build(repository: Path, baseline_oid: str) -> str:
        real_build(repository, baseline_oid)
        extra = repository / "cmru" / "EXTRA-ROOT-FILE.txt"
        extra.write_text("extra\n", encoding="utf-8")
        q._run(["git", "add", "cmru/EXTRA-ROOT-FILE.txt"], cwd=repository)
        q._run(
            ["git", "commit", "-q", "-m", "extra untracked-turned-committed noise"],
            cwd=repository,
            env=q._fixed_identity(),
        )
        return q._git(repository, "rev-parse", "HEAD")

    monkeypatch.setattr(q, "build_controlled_head", extra_root_build)
    scratch = tmp_path / "scratch"
    with pytest.raises(q.QualificationError, match="changed more than declared"):
        q.qualify(
            source_repo=source_repo,
            scratch=scratch,
            current_assay=Path("/nonexistent/bin/assay"),
            current_version="9.9.9",
        )


# --- main() ------------------------------------------------------------------


def test_main_prints_the_success_marker_and_returns_zero(tmp_path, monkeypatch, capsys):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    _install_happy_boundaries(monkeypatch)
    scratch = tmp_path / "scratch"
    exit_code = q.main(
        [
            "--source-repo",
            str(source_repo),
            "--scratch",
            str(scratch),
            "--current-assay",
            "/nonexistent/bin/assay",
            "--current-version",
            "9.9.9",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == "ASSAY_B006A_CMRU_QUALIFIED=1\n"
    assert "r2_killed_identity=" in captured.err
    assert "r3_canary=" in captured.err
    assert "snapshot_policy=" in captured.err


def test_print_receipt_writes_a_complete_human_readable_summary():
    artifact = _good_artifact()
    artifact["exit_code"] = 0
    result = {
        "input_oid": "1" * 40,
        "qualification_baseline_oid": "2" * 40,
        "head_oid": "3" * 40,
        "artifact": artifact,
        "killed_identity": artifact["claims"][2]["mutation"]["killed"][0],
        "probe": _good_probe(),
    }
    stream = io.StringIO()
    q.print_receipt(result, stream=stream)
    text = stream.getvalue()
    assert "input_oid=" + "1" * 40 in text
    assert "claim[R3]=status=PASS" in text
    assert "r2_killed_identity=" in text
    assert "r3_canary=" in text
    assert "omission_probe=" in text


def test_main_refuses_a_pre_existing_scratch_directory(tmp_path, monkeypatch):
    source_repo = _build_source_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, source_repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(SystemExit):
        q.main(
            [
                "--source-repo",
                str(source_repo),
                "--scratch",
                str(scratch),
                "--current-assay",
                "/nonexistent/bin/assay",
                "--current-version",
                "9.9.9",
            ]
        )


# --- invoke_qualification_lane / probe_snapshot_omissions (real, no stub) ---
#
# These two functions are the genuinely tester-unified/installed-wheel-bound
# operations that `qualify()`'s own tests above stub out. Here they run for
# REAL, but against a fake standalone "assay" executable (a trivial script
# mimicking the CLI's I/O contract) and against the REAL, importable
# `assay.isolation`/`assay.config` modules from this dev checkout (via a
# tiny python shim that inserts `src/` onto `sys.path` before executing the
# generated probe script) -- never a real installed wheel, but never a
# monkeypatched replacement of the function under test either.

_ASSAY_SRC_ROOT = str(Path(__file__).resolve().parents[1] / "src")


def test_invoke_qualification_lane_runs_and_parses_the_artifact(tmp_path):
    fake_assay = tmp_path / "fake-assay"
    fake_assay.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "artifact_path = sys.argv[sys.argv.index('--verdict-json') + 1]\n"
        "with open(artifact_path, 'w', encoding='utf-8') as fh:\n"
        "    json.dump({'outcome': 'PASS', 'exit_code': 0}, fh)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    fake_assay.chmod(0o755)
    repository = tmp_path / "repo"
    (repository / "cmru").mkdir(parents=True)
    (repository / q._LANE_PATH).write_text("dummy\n", encoding="utf-8")
    artifact_path = tmp_path / "verdict.json"
    proc, artifact = q.invoke_qualification_lane(fake_assay, repository, artifact_path)
    assert proc.returncode == 0
    assert artifact == {"outcome": "PASS", "exit_code": 0}


def _write_python_shim(path: Path) -> None:
    """A stand-in for a venv's ``bin/python`` that makes the REAL, importable
    ``assay`` package (from this dev checkout's ``src/``) visible to a
    ``-c`` script, exactly the way a real installed wheel's own interpreter
    would see its own installed package -- without needing one built."""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.path.insert(0, {_ASSAY_SRC_ROOT!r})\n"
        "assert sys.argv[1] == '-c'\n"
        "exec(compile(sys.argv[2], '<probe>', 'exec'))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_probe_snapshot_omissions_runs_the_real_isolation_module(tmp_path):
    repository = _build_source_repo(tmp_path)
    commit = q._git(repository, "rev-parse", "HEAD")
    scratch = tmp_path / "probe-scratch"
    scratch.mkdir()
    venv = tmp_path / "venv"
    venv.mkdir()
    _write_python_shim(venv / "python")
    result = q.probe_snapshot_omissions(venv / "assay", repository, commit, scratch)
    assert result == {
        "omitted_absent": [True, True, True],
        "cmru_root_present": True,
        "topos_ordinary_present": True,
        "status_clean": True,
    }
    # the source repo itself must be untouched by the probe
    assert q._consumer_status(repository) == ""


# --- module entry point -------------------------------------------------


def test_module_dispatches_to_main_when_run_as_a_script(monkeypatch):
    """Runs the module's own ``if __name__ == "__main__":`` line IN-PROCESS
    (via :mod:`runpy`, not a subprocess) so coverage measurement sees it --
    a spawned subprocess would execute a second, un-instrumented interpreter."""
    monkeypatch.setattr(sys, "argv", ["qualify_cmru_b006a.py"])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_MODULE_PATH), run_name="__main__")
    assert excinfo.value.code == 2  # argparse: missing required arguments


def test_run_raises_on_a_command_failure_when_check_is_requested():
    with pytest.raises(q.QualificationError, match="command failed"):
        q._run(["false"], check=True)


def test_run_does_not_raise_on_failure_when_check_is_not_requested():
    proc = q._run(["false"], check=False)
    assert proc.returncode != 0
