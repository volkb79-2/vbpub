"""Ordinary regression coverage for the P25 real Python-project qualification
harness (``gate/python/qualify_topos.py``) and its registered-gate wiring.

`nyxloom-trove/carve-assets/P25/test_acceptance.py` is the authoritative,
carver-owned, locked acceptance suite (packet hashes, skeleton freeze, the
complete-artifact comparator's own identity-corruption rejections) but it
lives outside `tests/` and is never collected by the registered gate. This
file promotes a REAL, independent slice into the tracked tree so the
registered gate's own `pytest tests -q ...` step exercises this code too --
never mocking the harness under test, and never constructing an expected
result from the harness's own output.

Two tiers:

* **Structural / real-git tests (always real).** `materialize_scenario`
  needs only `git`/the filesystem, so these run unconditionally against the
  REAL pinned Topos commit already reachable in this exact worktree's
  history -- no container, no installed Assay, nothing mocked.
* **Full-pipeline tests (gated).** `run_scenario`/`install_locked_release`
  need `/opt/tester-venv` (the tester-unified image's own ambient
  interpreter) and a real installed `assay` on PATH -- both real ONLY inside
  the self-hosted lane's own `env_passthrough=["PATH"]` (which puts the
  just wheel-installed `run-venv/bin` first). The cockpit devcontainer has
  neither, so these tests skip there rather than approximate the tester-
  unified image with local substitutes -- consistent with how this project's
  self-hosted-lane tests never run real pytest-in-child from the cockpit
  either (see `test_distribution_gate.py`'s own stub-based tests).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT.parent
HARNESS = PROJECT_ROOT / "gate" / "python" / "qualify_topos.py"
ASSET_ROOT = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P25"
GATE_SCRIPT = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"


def _load_harness():
    spec = importlib.util.spec_from_file_location("p25_qualify_topos_ordinary", HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_status(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def _real_gate_environment() -> bool:
    """`/opt/tester-venv` exists only inside the tester-unified image."""
    return Path("/opt/tester-venv/bin/python").exists()


def _assay_version(executable: Path) -> str:
    proc = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=True, timeout=30
    )
    return proc.stdout.strip().removeprefix("assay ")


# --- structural / static checks (always real) --------------------------------


def test_harness_is_promoted_with_no_todos_and_every_owned_signature() -> None:
    assert HARNESS.is_file()
    source = HARNESS.read_text(encoding="utf-8")
    assert "NotImplementedError" not in source
    for name in (
        "verify_pinned_inputs",
        "install_locked_release",
        "materialize_scenario",
        "run_scenario",
        "normalize_artifact",
        "compare_complete_artifact",
        "qualify",
    ):
        assert f"def {name}(" in source, name


def test_harness_never_imports_assay_or_launches_topos_outer_docker() -> None:
    source = HARNESS.read_text(encoding="utf-8")
    assert "import assay" not in source
    assert "from assay" not in source
    assert "docker run" not in source
    assert "topos-v" not in source
    assert ".glob(" not in source


def test_fixture_and_release_promotions_are_byte_exact_to_the_locked_packet() -> None:
    for relative in (
        "comment-only.py",
        "coverage-witness.py",
        "probe-missing.py",
        "probe-pass.py",
        "test-comment-only.py",
        "test-probe-missing.py",
        "test-probe-pass.py",
    ):
        assert (PROJECT_ROOT / "gate/python/fixtures/P25" / relative).read_bytes() == (
            ASSET_ROOT / "fixtures" / relative
        ).read_bytes()
    for relative in ("assay-1.2.5-py3-none-any.whl", "release-manifest.json"):
        assert (PROJECT_ROOT / "gate/python/release/P25" / relative).read_bytes() == (
            ASSET_ROOT / "release" / relative
        ).read_bytes()


def test_gate_script_wires_the_harness_between_self_hosted_lane_and_witness() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]
    assert inner.index("run_self_hosted_lane") < inner.index("gate/python/qualify_topos.py")
    assert inner.index("gate/python/qualify_topos.py") < inner.index("run_independent_witness")
    assert "ASSAY_GATE_PHASE=topos-qualified" in inner
    # The gate's own marker is printed only after the harness's own success
    # marker was checked -- never unconditionally after the subprocess call.
    call_to_marker = inner[
        inner.index("gate/python/qualify_topos.py") : inner.index("ASSAY_GATE_PHASE=topos-qualified")
    ]
    assert "ASSAY_P25_TOPOS_QUALIFIED=1" in call_to_marker
    assert "$scratch/run-venv/bin/assay" in call_to_marker
    assert "--current-assay" in call_to_marker
    assert "--current-version" in call_to_marker


def test_gate_script_preserves_every_p24_marker_and_the_final_receipt() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "ASSAY_GATE_PHASE=wheel-installed",
        "ASSAY_GATE_PHASE=self-hosted-lane-passed",
        "ASSAY_GATE_PHASE=topos-qualified",
        "ASSAY_GATE_PHASE=independent-self-hosting-passed",
    ):
        assert marker in source
    assert "ASSAY_REGISTERED_GATE_COMPLETE=1" in source


def test_gate_script_has_valid_bash_syntax() -> None:
    proc = subprocess.run(["bash", "-n", str(GATE_SCRIPT)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_gate_script_passes_shellcheck_when_available() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed in this environment")
    proc = subprocess.run([shellcheck, str(GATE_SCRIPT)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- real-git behavioral tests (always real, no container needed) -----------


def test_verify_pinned_inputs_accepts_the_real_pinned_revision() -> None:
    module = _load_harness()
    module.verify_pinned_inputs(REPO_ROOT)


def test_verify_pinned_inputs_rejects_a_drifted_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_harness()
    monkeypatch.setattr(module, "INPUT_REVISION", "0" * 40)
    with pytest.raises(module.QualificationError, match="revision"):
        module.verify_pinned_inputs(REPO_ROOT)


def test_verify_pinned_inputs_rejects_a_wrong_topos_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_harness()
    monkeypatch.setattr(module, "TOPOS_TREE", "1" * 40)
    with pytest.raises(module.QualificationError, match="tree"):
        module.verify_pinned_inputs(REPO_ROOT)


def test_materialize_scenario_deletes_exactly_three_absolute_links_and_keeps_five(
    tmp_path: Path,
) -> None:
    module = _load_harness()
    repo, _witness, _log, base_oid, head_oid = module.materialize_scenario(
        source_repo=REPO_ROOT, scratch=tmp_path, spec=module.MISSING
    )
    assert len(base_oid) == 40 and len(head_oid) == 40 and base_oid != head_oid

    indexed = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True, timeout=30
    ).stdout.splitlines()
    # BASELINE_INDEX_COUNT plus this scenario's own probe/test/wrapper/toml.
    assert len(indexed) == module.BASELINE_INDEX_COUNT + 4

    for relative in module.ABSOLUTE_SYMLINKS:
        target = repo / relative
        assert not target.exists() and not target.is_symlink(), relative

    retained_symlinks = [p for p in indexed if (repo / p).is_symlink()]
    assert len(retained_symlinks) == 5

    assert (repo / "assay.toml").is_file()
    assert (repo / "topos/tests/test_assay_probe.py").read_bytes() == (
        module.FIXTURE_ROOT / "test-probe-missing.py"
    ).read_bytes()
    assert (repo / "topos/src/topos/_assay_probe.py").read_bytes() == (
        module.FIXTURE_ROOT / "probe-missing.py"
    ).read_bytes()


def test_materialize_scenario_leaves_the_real_checkout_byte_for_byte_untouched(
    tmp_path: Path,
) -> None:
    module = _load_harness()
    before = _git_status(REPO_ROOT)
    module.materialize_scenario(source_repo=REPO_ROOT, scratch=tmp_path, spec=module.COMMENT_ONLY)
    after = _git_status(REPO_ROOT)
    assert before == after


def test_materialize_scenario_baseline_matches_the_carver_witnessed_oid(tmp_path: Path) -> None:
    """Fixed identity/date over the identical exported content must reproduce
    the EXACT baseline commit the carver's own tracer witnessed
    (`probe-results.json`) -- proof this is the same construction, not merely
    *a* plausible one."""
    module = _load_harness()
    document = json.loads((ASSET_ROOT / "probe-results.json").read_text())
    expected_base = document["scenarios"][0]["base"]
    _repo, _witness, _log, base_oid, _head = module.materialize_scenario(
        source_repo=REPO_ROOT, scratch=tmp_path, spec=module.MISSING
    )
    assert base_oid == expected_base


def test_materialize_scenario_baseline_is_identical_across_different_scenarios(
    tmp_path: Path,
) -> None:
    module = _load_harness()
    _, _, _, base_a, _ = module.materialize_scenario(
        source_repo=REPO_ROOT, scratch=tmp_path / "a", spec=module.MISSING
    )
    _, _, _, base_b, _ = module.materialize_scenario(
        source_repo=REPO_ROOT, scratch=tmp_path / "b", spec=module.EXCLUDED
    )
    assert base_a == base_b


def test_lane_schema_version_for_distinguishes_the_locked_release_from_every_other_owner() -> None:
    """`_lane_schema_version_for` is the ONLY place this distinction is
    inferred from `assay_version`; every other call site names the version
    explicitly. `install_locked_release` already proves the release venv's
    own reported version is exactly `RELEASE_VERSION` before returning it, so
    this equality is not a guess about which scenario is running."""
    module = _load_harness()
    assert module._lane_schema_version_for(module.RELEASE_VERSION) == module.RELEASE_LANE_SCHEMA_VERSION
    assert module.RELEASE_LANE_SCHEMA_VERSION == 1
    for other in ("9.9.9", "1.0.1.dev307+gdd03dea6", "0.0.0"):
        assert module._lane_schema_version_for(other) == module.CURRENT_LANE_SCHEMA_VERSION
    assert module.CURRENT_LANE_SCHEMA_VERSION == 2


def test_materialize_scenario_writes_a_v1_lane_for_the_locked_release_and_v2_for_everyone_else(
    tmp_path: Path,
) -> None:
    """One lane generator serving two assay versions (the locked 1.2.5
    release understands ONLY lane schema v1 -- it predates `[isolation]`
    entirely -- while every other owner understands ONLY v2) is exactly the
    trap a lane spelled for the wrong reader falls into SILENTLY: no verdict
    artifact at all, no error. The two spellings must stay visibly distinct
    on disk, not just in the generator's own head."""
    module = _load_harness()
    release_repo, *_ = module.materialize_scenario(
        source_repo=REPO_ROOT,
        scratch=tmp_path / "release",
        spec=module.RELEASE_SMOKE,
        lane_schema_version=module.RELEASE_LANE_SCHEMA_VERSION,
    )
    release_lane = (release_repo / "assay.toml").read_text(encoding="utf-8")
    assert "schema_version = 1" in release_lane
    assert "[lanes.topos-qualification.isolation]" not in release_lane
    assert "snapshot_selection" not in release_lane

    current_repo, *_ = module.materialize_scenario(
        source_repo=REPO_ROOT, scratch=tmp_path / "current", spec=module.RELEASE_SMOKE
    )
    current_lane = (current_repo / "assay.toml").read_text(encoding="utf-8")
    assert "schema_version = 2" in current_lane
    assert "[lanes.topos-qualification.isolation]" in current_lane
    assert 'snapshot_selection = "repository"' in current_lane


def test_write_lane_refuses_an_unknown_lane_schema_version(tmp_path: Path) -> None:
    module = _load_harness()
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(module.QualificationError, match="lane_schema_version"):
        module._write_lane(
            repo,
            base="1" * 40,
            witness=tmp_path / "witness.json",
            pytest_log=tmp_path / "pytest.log",
            argv_tail=("topos/tests",),
            allow_excluded=True,
            lane_schema_version=3,
        )
    assert not (repo / "assay.toml").exists()


#: (P33/A-226) P25's own locked `expected/` templates stay frozen at v4 as
#: historical evidence (A-222); the harness they feed now compares against
#: P33's carver-supplied v5 siblings, so this consumer follows it. Reading
#: the v4 original here would test the harness against a contract the
#: product no longer emits.
P33_EXPECTED_ROOT = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P33" / "expected"


def _pass_template_actual() -> tuple[dict, dict[str, object]]:
    template = json.loads(
        (P33_EXPECTED_ROOT / "p25-pass-v5-template.json").read_text()
    )
    actual = json.loads(json.dumps(template))
    substitutions: dict[str, object] = {
        "@ASSAY_VERSION@": "9.9.9",
        "@HEAD_OID@": "1" * 40,
        "@BASE_OID@": "2" * 40,
        "@STARTED@": "2026-08-10T00:00:00+00:00",
        "@ENDED@": "2026-08-10T00:01:00+00:00",
        "@WITNESS_PATH@": "/tmp/p25-ordinary/witness.json",
        "@PYTEST_LOG@": "/tmp/p25-ordinary/pytest.log",
    }

    def replace(value):
        if isinstance(value, str) and value in substitutions:
            return substitutions[value]
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return replace(actual), substitutions


def test_normalize_artifact_rejects_a_declared_effective_environment_mismatch() -> None:
    """A new combined-axis attack not in the locked suite: the locked suite's
    own identity-corruption parametrization covers `assay_version`/`commit`/
    `started`, never a declared/effective drift -- exactly the shape P21's
    real `env_passthrough`-overrides-declared-`env` defect took."""
    module = _load_harness()
    actual, values = _pass_template_actual()
    actual["env_effective"] = dict(actual["env_effective"])
    actual["env_effective"]["LANG"] = "en_US.UTF-8"
    with pytest.raises(module.QualificationError, match="environment"):
        module.normalize_artifact(
            actual,
            assay_version=values["@ASSAY_VERSION@"],
            base_oid=values["@BASE_OID@"],
            head_oid=values["@HEAD_OID@"],
            witness=Path(values["@WITNESS_PATH@"]),
            pytest_log=Path(values["@PYTEST_LOG@"]),
        )


def test_normalize_artifact_rejects_a_witness_path_that_disagrees_with_the_plan() -> None:
    module = _load_harness()
    actual, values = _pass_template_actual()
    with pytest.raises(module.QualificationError, match="witness"):
        module.normalize_artifact(
            actual,
            assay_version=values["@ASSAY_VERSION@"],
            base_oid=values["@BASE_OID@"],
            head_oid=values["@HEAD_OID@"],
            witness=Path("/tmp/some/other/witness.json"),
            pytest_log=Path(values["@PYTEST_LOG@"]),
        )


def test_expected_comparator_pins_the_locked_hand_manifest_numbers() -> None:
    """The copied Topos evaluator's own numbers are frozen per scenario, so
    `passed` alone can never stand in for agreement."""
    module = _load_harness()
    pass_numbers = {
        "passed": True,
        "changed_executable": 5,
        "covered": 5,
        "uncovered": {},
        "files_missing_coverage": [],
    }
    assert module._expected_comparator(module.PRIMARY) == pass_numbers
    assert module._expected_comparator(module.RELEASE_SMOKE) == pass_numbers
    assert module._expected_comparator(module.MISSING) == {
        "passed": False,
        "changed_executable": 5,
        "covered": 4,
        "uncovered": {"topos/src/topos/_assay_probe.py": [7]},
        "files_missing_coverage": [],
    }
    assert module._expected_comparator(module.COMMENT_ONLY) == {
        "passed": True,
        "changed_executable": 0,
        "covered": 0,
        "uncovered": {},
        "files_missing_coverage": [],
    }
    # The frozen exclusion-capability asymmetry stays recorded, not compared.
    assert module._expected_comparator(module.EXCLUDED) is None


def test_expected_comparator_agrees_with_the_carver_witnessed_topos_results() -> None:
    """Independent cross-check: the numbers derived from the hand manifest
    must equal the ones the carver's own tracer witnessed from the real
    unmodified Topos evaluator inside tester-unified."""
    module = _load_harness()
    document = json.loads((ASSET_ROOT / "probe-results.json").read_text())
    witnessed = {item["scenario"]: item["comparator"] for item in document["scenarios"]}
    for spec, key in (
        (module.PRIMARY, "pass"),
        (module.MISSING, "missing"),
        (module.COMMENT_ONLY, "comment-only"),
    ):
        expected = module._expected_comparator(spec)
        assert {field: witnessed[key][field] for field in expected} == expected, key


def test_a_topos_pass_alone_cannot_witness_agreement(tmp_path: Path) -> None:
    """Topos defines `pct = 100.0` whenever `changed_executable == 0`, so a run
    that measured NOTHING returns `passed=True` exactly like a truthful 5/5
    run. Real unmodified evaluator, real pinned tree, no Assay involved."""
    module = _load_harness()
    repo, _witness, _log, _base, head = module.materialize_scenario(
        source_repo=REPO_ROOT, scratch=tmp_path, spec=module.MISSING
    )
    empty_profile = tmp_path / "empty-witness.json"
    empty_profile.write_text(json.dumps({"files": {}}), encoding="utf-8")

    # base == HEAD, so the diff this evaluator sees is empty.
    vacuous = module._topos_comparator(repo, head, empty_profile)
    assert vacuous["passed"] is True
    assert vacuous["changed_executable"] == 0

    expected = module._expected_comparator(module.RELEASE_SMOKE)
    assert {field: vacuous[field] for field in expected} != expected


# --- full-pipeline tests: real only inside the self-hosted lane --------------


@pytest.fixture(scope="module")
def installed_assay() -> Path:
    if not _real_gate_environment():
        pytest.skip("requires the tester-unified image's own /opt/tester-venv")
    found = shutil.which("assay")
    if found is None:
        pytest.skip(
            "no installed `assay` on PATH -- only real inside the self-hosted "
            "lane's own env_passthrough=['PATH']"
        )
    return Path(found)


def test_run_scenario_reproduces_the_locked_missing_line_terminal(
    installed_assay: Path, tmp_path: Path
) -> None:
    module = _load_harness()
    version = _assay_version(installed_assay)
    result = module.run_scenario(
        source_repo=REPO_ROOT,
        scratch=tmp_path,
        assay_executable=installed_assay,
        assay_version=version,
        spec=module.MISSING,
    )
    assert result.artifact["outcome"] == "FAIL"
    assert result.artifact["reason_code"] == "UNCOVERED_LINES"
    assert result.comparator is not None
    assert result.comparator["passed"] is False
    assert result.comparator["uncovered"] == {"topos/src/topos/_assay_probe.py": [7]}
    assert result.consumer_clean is True


def test_run_scenario_records_the_exclusion_capability_asymmetry(
    installed_assay: Path, tmp_path: Path
) -> None:
    module = _load_harness()
    version = _assay_version(installed_assay)
    result = module.run_scenario(
        source_repo=REPO_ROOT,
        scratch=tmp_path,
        assay_executable=installed_assay,
        assay_version=version,
        spec=module.EXCLUDED,
    )
    assert result.artifact["outcome"] == "FAIL"
    assert result.artifact["reason_code"] == "EXCLUDED_LINES"
    assert result.comparator is not None
    assert result.comparator["passed"] is True  # Topos cannot see the exclusion


def test_integrity_matrix_negatives_produce_their_frozen_terminals(
    installed_assay: Path, tmp_path: Path
) -> None:
    module = _load_harness()
    version = _assay_version(installed_assay)
    # Each `_check_*` function is itself the oracle: it raises
    # QualificationError unless the real terminal/clean-topology matches the
    # frozen decision table exactly, so "did not raise" is not hollow here.
    module._check_missing_profile(REPO_ROOT, tmp_path / "missing-profile", installed_assay, version)
    module._check_dirty_consumer(REPO_ROOT, tmp_path / "dirty-consumer", installed_assay, version)
    module._check_base_is_head(REPO_ROOT, tmp_path / "base-is-head", installed_assay, version)
    module._check_command_dirt(REPO_ROOT, tmp_path / "command-dirt", installed_assay, version)
    module._check_command_head_move(
        REPO_ROOT, tmp_path / "command-head-move", installed_assay, version
    )
    module._check_wrong_source_root(
        REPO_ROOT, tmp_path / "wrong-source-root", installed_assay, version
    )


def test_universal_pass_mutation_is_rejected_by_the_whole_document_comparator(
    installed_assay: Path, tmp_path: Path
) -> None:
    module = _load_harness()
    version = _assay_version(installed_assay)
    result = module.run_scenario(
        source_repo=REPO_ROOT,
        scratch=tmp_path,
        assay_executable=installed_assay,
        assay_version=version,
        spec=module.MISSING,
    )
    module._check_universal_pass_mutation(result)


def test_a_scenario_that_must_compare_with_topos_refuses_a_missing_witness(
    installed_assay: Path, tmp_path: Path
) -> None:
    """A `compare_with_topos=True` scenario may not silently degrade to one
    witness (A-208).

    `missing-line` declares `expected_exit=1`, so a guard keyed only on
    `expected_exit == 0` never fires for it. A wrapper regression that still
    runs the real pytest+coverage — leaving Assay's own in-snapshot verdict
    truthfully `FAIL/UNCOVERED_LINES` — but never exports the witness would
    then drop the independent Topos comparison entirely and still be recorded
    as qualified. The wrapper is a locked byte-exact fixture today, so this is
    defence in depth against a future change to it; the guard must still
    discriminate.
    """
    module = _load_harness()
    non_copying_wrapper = '''"""P25 regression wrapper: real coverage, no witness export."""
import os
import subprocess
import sys
from pathlib import Path

ARTIFACT = Path(".assay/topos-coverage.json")


def main() -> int:
    with Path(os.environ["ASSAY_P25_LOG"]).open("wb") as stream:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *sys.argv[1:],
                "--cov=topos/src/topos",
                "--cov-branch",
                f"--cov-report=json:{ARTIFACT}",
            ],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def materialize_without_exporting_the_witness(
        *, source_repo, scratch, spec, lane_schema_version=module.CURRENT_LANE_SCHEMA_VERSION
    ):
        return module._materialize_negative(
            source_repo,
            scratch,
            spec.name,
            probe_fixture=spec.probe_fixture,
            test_fixture=spec.test_fixture,
            argv_tail=spec.argv_tail,
            lane_schema_version=lane_schema_version,
            allow_excluded=spec.allow_excluded,
            wrapper_fixture=None,
            wrapper_text=non_copying_wrapper,
            source_target=spec.source_target,
        )

    module.materialize_scenario = materialize_without_exporting_the_witness
    assert module.MISSING.expected_exit != 0 and module.MISSING.compare_with_topos
    with pytest.raises(module.QualificationError, match="expected a coverage witness"):
        module.run_scenario(
            source_repo=REPO_ROOT,
            scratch=tmp_path,
            assay_executable=installed_assay,
            assay_version=_assay_version(installed_assay),
            spec=module.MISSING,
        )
    assert not (tmp_path / module.MISSING.name / "witness.json").exists()


def test_a_scenario_that_measured_nothing_is_refused(
    installed_assay: Path, tmp_path: Path
) -> None:
    """Combined-axis regression: release-wheel ownership x a materialization
    regression that seeds the scenario probe into the BASELINE.

    `base..HEAD` then contains no executable change, so installed Assay
    truthfully reports 0/0 PASS and the copied Topos evaluator truthfully
    reports 0/0 PASS -- they "agree" on every boolean. The release smoke is
    the ONLY proof the separately hash-installed clean 1.2.5 wheel gets and
    carries no complete-artifact template, so it must refuse a run that
    measured nothing rather than record a vacuous agreement.
    """
    module = _load_harness()
    original = module._seed_baseline

    def seeded_with_the_probe_already_present(source_repo: Path, repository: Path) -> str:
        original(source_repo, repository)
        module._copy_fixture("probe-pass.py", repository / "topos/src/topos/_assay_probe.py")
        module._run(["git", "add", "-f", "topos/src/topos/_assay_probe.py"], cwd=repository)
        module._run(
            ["git", "commit", "-q", "-m", "probe already in baseline"],
            cwd=repository,
            env=module._fixed_identity(),
        )
        return module._git(repository, "rev-parse", "HEAD")

    module._seed_baseline = seeded_with_the_probe_already_present
    with pytest.raises(module.QualificationError, match="hand manifest"):
        module.run_scenario(
            source_repo=REPO_ROOT,
            scratch=tmp_path,
            assay_executable=installed_assay,
            assay_version=_assay_version(installed_assay),
            spec=module.RELEASE_SMOKE,
        )


def test_the_wrong_source_root_decoy_is_rejected_because_of_the_root(
    installed_assay: Path, tmp_path: Path
) -> None:
    """The decoy must fail the whole-document comparison BECAUSE of the root.

    `test_integrity_matrix_negatives_produce_their_frozen_terminals` already
    covers the wrong-root direction; this is the control that makes it an
    oracle. Run the identical construction with the CORRECT source root: if
    the check still reported "decoy caught", its rejection was attributable to
    something else (a mismatched probe or argv) and it proved nothing about
    source roots at all.

    """
    module = _load_harness()
    original = module._materialize_negative

    calls: list[dict[str, object]] = []

    def with_the_correct_source_root(*args: object, **kwargs: object):
        kwargs["source_roots"] = "topos/src/topos"
        calls.append(dict(kwargs))
        return original(*args, **kwargs)

    module._materialize_negative = with_the_correct_source_root
    try:
        module._check_wrong_source_root(
            REPO_ROOT, tmp_path, installed_assay, _assay_version(installed_assay)
        )
    except module.QualificationError:
        raise AssertionError(
            "the correct-root control raised; the decoy oracle is no longer "
            "exercising a matched comparison"
        )
    assert calls and calls[-1].get("source_roots") == "topos/src/topos", (
        "the correct-root control did not run with the declared root"
    )


def test_install_locked_release_produces_a_pure_hash_bound_venv(tmp_path: Path) -> None:
    if not _real_gate_environment():
        pytest.skip("requires the tester-unified image's own /opt/tester-venv")
    module = _load_harness()
    release_assay, release_version = module.install_locked_release(
        source_repo=REPO_ROOT, scratch=tmp_path
    )
    assert release_version == module.RELEASE_VERSION == "1.2.5"
    assert release_assay.is_file()
    assert str(release_assay).startswith(str(tmp_path))


def test_release_smoke_scenario_matches_the_current_full_pass_shape(tmp_path: Path) -> None:
    if not _real_gate_environment():
        pytest.skip("requires the tester-unified image's own /opt/tester-venv")
    module = _load_harness()
    release_assay, release_version = module.install_locked_release(
        source_repo=REPO_ROOT, scratch=tmp_path / "release"
    )
    result = module.run_scenario(
        source_repo=REPO_ROOT,
        scratch=tmp_path / "smoke",
        assay_executable=release_assay,
        assay_version=release_version,
        spec=module.RELEASE_SMOKE,
    )
    assert result.artifact["outcome"] == "PASS"
    assert result.comparator is not None and result.comparator["passed"] is True
