"""Unit tests for the P34/W9 real-PostgreSQL qualification harness
(``gate/python/qualify_dstdns_sql.py``, carve §6/W9, §7 O3/O4/O5).

**Design (mirrors ``test_gate_qualify_cmru_b006a.py`` and
``test_python_qualification.py``).** The registered gate runs this project's
own test suite WITHOUT a docker socket (this file's own module docstring
constraint), so nothing here may unconditionally require one. Three tiers:

* **Structural / pure-logic tests (always real, no docker, no external
  checkout).** Pin verification, corpus listing/export, real mutation-site
  discovery against the byte-exact committed fixture corpus
  (``gate/python/fixtures/dstdns-sql/corpus/`` -- three real dstdns files at
  the pinned blobs, A-286), the catalog-scenario lookup/apply logic, the
  witness normalize/compare comparator, the wrapper-script and CLI-argv
  string construction, and ``main()``'s own argument handling (with the
  three heavy orchestrators stubbed, exactly ``test_gate_qualify_cmru_
  b006a.py``'s own four-stubbed-boundary shape).
* **Docker-gated, dstdns-independent (O5 + container mechanics).** A real
  ``postgres:18-alpine`` container, ``--network none``, removed even on
  failure. O5's own carve command names this file with ``-k restrict_key``,
  so both the must-succeed control (WITH a pinned key) and the probe
  (WITHOUT one) live here, real. Skips with a stated reason when no docker
  socket is reachable.
* **Docker + real pinned dstdns checkout (the three orchestrators' own full
  real-environment proof).** ``run_o3_span_fidelity``, ``run_o4_residue_
  probe`` and ``capture_witness`` each drive several real container round
  trips against the actual pinned commit; promoting them here mirrors
  ``test_python_qualification.py``'s own "full-pipeline tests: real only
  inside X environment" tier -- real ONLY when both a docker socket AND the
  pinned dstdns checkout are reachable, skipped elsewhere with a stated
  reason. The qualification itself remains OPERATOR-run via the CLI (the
  carve's own O3/O4 commands are bare script invocations, never pytest);
  these tests are this suite's own regression coverage of that same code,
  not a replacement for the operator's own run.
"""

from __future__ import annotations

import importlib.util
import io
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT

_MODULE_PATH = PROJECT_ROOT / "gate" / "python" / "qualify_dstdns_sql.py"
_SPEC = importlib.util.spec_from_file_location("qualify_dstdns_sql", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
q = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = q
_SPEC.loader.exec_module(q)

_FIXTURE_CORPUS = PROJECT_ROOT / "gate" / "python" / "fixtures" / "dstdns-sql" / "corpus"
_WITNESS_PATH = (
    PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "W3" / "expected" / "dstdns-sql-r2-v6-witness.json"
)

#: The exact real git blob SHA1s at DSTDNS_COMMIT (§9 M17) for the three
#: committed fixture files -- independent of anything this module computes,
#: so a corrupted fixture is caught before any test that trusts its bytes.
_FIXTURE_BLOB_SHAS = {
    "20-create-corpora.sql": "d4b394add21c1a6423cff3d4cfcaa45ffc2792ae",
    "03c-create-workflow-core.sql": "84b043f6454196f63a6c88b2898eb47cdce28db2",
    "21-create-workflow-corpus.sql": "e188053a9e678ca4ff474039d3685a15b31161e7",
}


def _git_blob_sha(path: Path) -> str:
    return subprocess.run(
        ["git", "hash-object", str(path)], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()


def _docker_usable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _dstdns_checkout() -> Path | None:
    """The real pinned dstdns checkout, if this host happens to carry one at
    the exact pinned commit -- never assumed, always independently
    re-verified through the module's own :func:`verify_pinned_inputs`."""
    candidate = Path("/workspaces/dstdns")
    if not candidate.is_dir():
        return None
    try:
        q.verify_pinned_inputs(candidate)
    except q.QualificationError:
        return None
    return candidate


@pytest.fixture(scope="module")
def docker() -> None:
    if not _docker_usable():
        pytest.skip(
            "no usable docker socket in this environment -- the registered "
            "gate runs this suite WITHOUT one by design; O3/O4/O5 are "
            "operator-run against a real docker daemon"
        )


@pytest.fixture(scope="module")
def dstdns_checkout() -> Path:
    repo = _dstdns_checkout()
    if repo is None:
        pytest.skip(
            "no real dstdns checkout at the pinned commit reachable at "
            "/workspaces/dstdns in this environment -- run_o3_span_fidelity/"
            "run_o4_residue_probe/capture_witness's full real-environment "
            "proof is operator-run there; skipping the regression promotion"
        )
    return repo


# =============================================================================
# Structural
# =============================================================================


def test_harness_has_every_owned_signature() -> None:
    assert _MODULE_PATH.is_file()
    source = _MODULE_PATH.read_text(encoding="utf-8")
    for name in (
        "verify_pinned_inputs",
        "list_corpus_basenames",
        "export_corpus",
        "write_corpus",
        "discover_sites",
        "find_scenario_site",
        "apply_mutation",
        "verify_dump_reproducible",
        "run_o3_span_fidelity",
        "run_o4_residue_probe",
        "capture_witness",
        "normalize_verdict",
        "compare_with_witness",
        "main",
    ):
        assert f"def {name}(" in source, name


def test_gate_script_exists_and_has_valid_posix_sh_syntax() -> None:
    assert q.GATE_SCRIPT_HOST_PATH.is_file()
    proc = subprocess.run(["sh", "-n", str(q.GATE_SCRIPT_HOST_PATH)], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_gate_script_passes_shellcheck_when_available() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed in this environment")
    proc = subprocess.run([shellcheck, str(q.GATE_SCRIPT_HOST_PATH)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_gate_script_declares_the_documented_required_env_vars() -> None:
    source = q.GATE_SCRIPT_HOST_PATH.read_text(encoding="utf-8")
    for var in (
        "SCHEMA_GATE_INIT_SCRIPTS_DIR",
        "SCHEMA_GATE_DBNAME",
        "SCHEMA_GATE_DUMP_PATH",
        "SCHEMA_GATE_KILL_SIGNAL_PATH",
        "SCHEMA_GATE_RESTRICT_KEY",
        "SCHEMA_GATE_TEST_CMD",
    ):
        assert var in source


def test_gate_script_never_invokes_dstdns_own_broken_script() -> None:
    """A-280's own refusal, mechanically enforced: the pinned, never-run
    blob's own filename must not appear on any EXECUTABLE line -- the
    script's own header comment names it once, as documentation of what it
    replaces, which is not an invocation."""
    executable_lines = [
        line
        for line in q.GATE_SCRIPT_HOST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("scripts/schema-gate.sh" in line for line in executable_lines)


def test_fixture_corpus_files_are_byte_exact_to_the_pinned_blobs() -> None:
    for basename, expected_sha in _FIXTURE_BLOB_SHAS.items():
        path = _FIXTURE_CORPUS / basename
        assert path.is_file(), basename
        assert _git_blob_sha(path) == expected_sha, basename


def test_every_operator_has_exactly_one_scenario() -> None:
    assert {s.operator for s in q.SCENARIOS} == set(q.ALL_OPERATORS)
    assert len({s.name for s in q.SCENARIOS}) == len(q.SCENARIOS) == 7


# =============================================================================
# verify_pinned_inputs / list_corpus_basenames / export_corpus / write_corpus
#   -- real git, a tiny synthetic fixture repo, no docker
# =============================================================================


def _build_synthetic_dstdns_repo(tmp_path: Path) -> Path:
    """A tiny synthetic repository shaped exactly like the paths
    :func:`verify_pinned_inputs`/:func:`export_corpus` read -- real git
    plumbing, never a mock of it."""
    repo = tmp_path / "source"
    repo.mkdir()
    q._run(["git", "init", "-q", "-b", "main"], cwd=repo)
    init_dir = repo / q.INIT_SCRIPTS_PATH
    init_dir.mkdir(parents=True)
    ordinary = {
        "03-a.sql": "CREATE TABLE a (id INT);\n",
        "10-b.sql": "CREATE TABLE b (id INT);\n",
        "20-c.sql": "CREATE TABLE c (id INT);\n",
    }
    deferred = {"95-marker.sql": "-- marker\n", "99-seed.sql": "-- seed\n"}
    excluded = {name: "-- excluded (TimescaleDB)\n" for name in q.EXCLUDED_BASENAMES}
    non_sql = {"90-grant-permissions.sh": "#!/bin/sh\n"}
    for name, content in {**ordinary, **deferred, **excluded, **non_sql}.items():
        (init_dir / name).write_text(content, encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "schema-gate.sh").write_text("#!/bin/sh\necho dummy\n", encoding="utf-8")
    (repo / "docs" / "proposals" / "cw2-p85-wave").mkdir(parents=True)
    (repo / "docs" / "proposals" / "cw2-p85-wave" / "REVIEW-CW2A.md").write_text("dummy review\n", encoding="utf-8")
    q._run(["git", "add", "-A"], cwd=repo)
    q._run(
        ["git", "commit", "-q", "-m", "synthetic fixture"],
        cwd=repo,
        env=q._env_with(
            {
                "GIT_AUTHOR_NAME": "Assay P34 W9 tests",
                "GIT_AUTHOR_EMAIL": "assay-p34-w9-tests@example.invalid",
                "GIT_COMMITTER_NAME": "Assay P34 W9 tests",
                "GIT_COMMITTER_EMAIL": "assay-p34-w9-tests@example.invalid",
            }
        ),
    )
    return repo


def _pin_frozen_inputs(monkeypatch: pytest.MonkeyPatch, repo: Path) -> str:
    head = q._git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(q, "DSTDNS_COMMIT", head)
    monkeypatch.setattr(q, "DSTDNS_TREE", q._git(repo, "rev-parse", f"{head}^{{tree}}"))
    monkeypatch.setattr(q, "INIT_SCRIPTS_TREE", q._git(repo, "rev-parse", f"{head}:{q.INIT_SCRIPTS_PATH}"))
    monkeypatch.setattr(q, "SCHEMA_GATE_BLOB", q._git(repo, "rev-parse", f"{head}:scripts/schema-gate.sh"))
    monkeypatch.setattr(
        q,
        "REVIEW_BLOB",
        q._git(repo, "rev-parse", f"{head}:docs/proposals/cw2-p85-wave/REVIEW-CW2A.md"),
    )
    return head


def test_verify_pinned_inputs_accepts_the_exact_frozen_commit(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    q.verify_pinned_inputs(repo)  # must not raise


def test_verify_pinned_inputs_accepts_the_real_pinned_dstdns_commit_shape(tmp_path) -> None:
    """The module's own UN-monkeypatched constants, against the REAL
    committed fixture directory's own git-blob identity -- proves the four
    pins in the shipped module still agree with each other's own shape
    (each is a 40-hex string) without needing the real dstdns checkout."""
    for value in (q.DSTDNS_COMMIT, q.DSTDNS_TREE, q.INIT_SCRIPTS_TREE, q.SCHEMA_GATE_BLOB, q.REVIEW_BLOB):
        assert len(value) == 40
        int(value, 16)  # must be valid hex


def test_verify_pinned_inputs_refuses_an_unresolvable_commit(tmp_path, monkeypatch) -> None:
    """A wholly nonexistent 40-hex commit: ``git rev-parse`` itself fails
    (exit 128), so ``_run``'s own ``check=True`` raises before this
    function's own comparison ever runs -- the raw git failure propagates,
    unchanged, exactly as :mod:`qualify_cmru_b006a`'s identical precedent
    test expects."""
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    monkeypatch.setattr(q, "DSTDNS_COMMIT", "a" * 40)
    with pytest.raises(q.QualificationError, match="command failed"):
        q.verify_pinned_inputs(repo)


def test_verify_pinned_inputs_refuses_a_resolvable_oid_with_the_wrong_spelling(tmp_path, monkeypatch) -> None:
    """The internal exact-spelling comparison itself: an ABBREVIATED oid
    that git happily resolves, but to a full 40-hex string that disagrees
    with the frozen literal -- this is the ONLY shape that reaches this
    function's own ``not reachable exactly`` message, since a wholly
    unresolvable revision fails inside ``_run`` first (see the test above)."""
    repo = _build_synthetic_dstdns_repo(tmp_path)
    full = _pin_frozen_inputs(monkeypatch, repo)
    monkeypatch.setattr(q, "DSTDNS_COMMIT", full[:12])
    with pytest.raises(q.QualificationError, match="not reachable exactly"):
        q.verify_pinned_inputs(repo)


def test_verify_pinned_inputs_refuses_a_wrong_dstdns_tree(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    monkeypatch.setattr(q, "DSTDNS_TREE", "b" * 40)
    with pytest.raises(q.QualificationError, match="DSTDNS_TREE"):
        q.verify_pinned_inputs(repo)


def test_verify_pinned_inputs_refuses_a_wrong_init_scripts_tree(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    monkeypatch.setattr(q, "INIT_SCRIPTS_TREE", "c" * 40)
    with pytest.raises(q.QualificationError, match="INIT_SCRIPTS_TREE"):
        q.verify_pinned_inputs(repo)


def test_verify_pinned_inputs_refuses_a_wrong_schema_gate_blob(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    monkeypatch.setattr(q, "SCHEMA_GATE_BLOB", "d" * 40)
    with pytest.raises(q.QualificationError, match="SCHEMA_GATE_BLOB"):
        q.verify_pinned_inputs(repo)


def test_verify_pinned_inputs_refuses_a_wrong_review_blob(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    monkeypatch.setattr(q, "REVIEW_BLOB", "e" * 40)
    with pytest.raises(q.QualificationError, match="REVIEW_BLOB"):
        q.verify_pinned_inputs(repo)


def test_list_corpus_basenames_excludes_deferred_excluded_and_non_sql(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    names = q.list_corpus_basenames(repo)
    assert names == ("03-a.sql", "10-b.sql", "20-c.sql")  # sorted, deferred/excluded/non-sql gone


def test_list_corpus_basenames_is_sorted_bytewise(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    names = q.list_corpus_basenames(repo)
    assert list(names) == sorted(names)


def test_export_corpus_is_byte_exact_and_matches_the_listing(tmp_path, monkeypatch) -> None:
    repo = _build_synthetic_dstdns_repo(tmp_path)
    _pin_frozen_inputs(monkeypatch, repo)
    corpus = q.export_corpus(repo)
    assert set(corpus) == set(q.list_corpus_basenames(repo))
    assert corpus["03-a.sql"] == b"CREATE TABLE a (id INT);\n"


def test_write_corpus_materialises_every_file_with_exact_bytes(tmp_path) -> None:
    destination = tmp_path / "out"
    corpus = {"a.sql": b"one", "b.sql": b"two"}
    q.write_corpus(corpus, destination)
    assert (destination / "a.sql").read_bytes() == b"one"
    assert (destination / "b.sql").read_bytes() == b"two"


# =============================================================================
# discover_sites / SCENARIOS / find_scenario_site / apply_mutation
#   -- pure, over the REAL byte-exact fixture corpus, no docker
# =============================================================================


def _real_fixture_corpus() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in _FIXTURE_CORPUS.glob("*.sql")}


@pytest.mark.parametrize("scenario", q.SCENARIOS, ids=lambda s: s.name)
def test_every_scenario_site_is_discovered_in_the_real_fixture_corpus(scenario) -> None:
    corpus = _real_fixture_corpus()
    text = corpus[scenario.file].decode("utf-8")
    sites = q.discover_sites(text)
    site = q.find_scenario_site({scenario.file: sites}, scenario)
    assert site.lineno == scenario.lineno
    assert (site.start_byte, site.end_byte) == (scenario.start_byte, scenario.end_byte)
    assert site.operator == scenario.operator


def test_find_scenario_site_raises_when_no_site_matches_the_pinned_span() -> None:
    scenario = q.SCENARIOS[0]
    with pytest.raises(q.QualificationError, match="the pinned corpus drifted"):
        q.find_scenario_site({scenario.file: ()}, scenario)


def test_find_scenario_site_raises_on_a_description_drift() -> None:
    scenario = q.SCENARIOS[0]
    drifted = q.MutationSite(
        start_byte=scenario.start_byte,
        end_byte=scenario.end_byte,
        replacement=b"NULL",
        lineno=scenario.lineno,
        operator=scenario.operator,
        description="a different description entirely",
    )
    with pytest.raises(q.QualificationError, match="description drifted"):
        q.find_scenario_site({scenario.file: (drifted,)}, scenario)


def test_apply_mutation_changes_only_the_named_file() -> None:
    corpus = {"a.sql": b"AAAA", "b.sql": b"BBBB"}
    site = q.MutationSite(start_byte=1, end_byte=2, replacement=b"X", lineno=1, operator="sql:drop-check", description="x")
    mutated = q.apply_mutation(corpus, "a.sql", site)
    assert mutated["a.sql"] == b"AXAA"
    assert mutated["b.sql"] == b"BBBB"
    assert corpus["a.sql"] == b"AAAA"  # the original is never mutated in place


def test_invalid_control_site_applies_the_naive_type_mismatched_replacement() -> None:
    corpus = _real_fixture_corpus()
    mutated = q.apply_mutation(corpus, q.INVALID_CONTROL_FILE, q.INVALID_CONTROL_SITE)
    assert b"'__assay_widened__'" in mutated[q.INVALID_CONTROL_FILE]
    # the REAL, shipped adapter's own literal-shape rule would never emit
    # this replacement for the identical span -- the paired must-succeed
    # demonstration that the adapter's own site differs from the hand-built one.
    real_sites = q.discover_sites(corpus[q.INVALID_CONTROL_FILE].decode("utf-8"))
    real_site = q.find_scenario_site(
        {q.INVALID_CONTROL_FILE: real_sites},
        next(s for s in q.SCENARIOS if s.name == "widen-check-in-result-inbox-envelope-version"),
    )
    assert real_site.replacement != q.INVALID_CONTROL_SITE.replacement
    assert real_site.start_byte == q.INVALID_CONTROL_SITE.start_byte
    assert real_site.end_byte == q.INVALID_CONTROL_SITE.end_byte


# =============================================================================
# normalize_verdict / compare_with_witness
# =============================================================================


def _minimal_verdict(**overrides) -> dict:
    document = {
        "assay_version": "9.9.9",
        "commit": "1" * 40,
        "started": "2026-08-18T00:00:00+00:00",
        "ended": "2026-08-18T00:01:00+00:00",
        "judgment": {"resolved": {"base": "2" * 40}},
        "outcome": "PASS",
    }
    document.update(overrides)
    return document


def test_normalize_verdict_replaces_the_four_placeholder_fields() -> None:
    normalized = q.normalize_verdict(_minimal_verdict(), assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)
    assert normalized["assay_version"] == "@ASSAY_VERSION@"
    assert normalized["commit"] == "@HEAD_OID@"
    assert normalized["started"] == "@STARTED@"
    assert normalized["ended"] == "@ENDED@"
    assert normalized["judgment"]["resolved"]["base"] == "@BASE_OID@"


def test_normalize_verdict_refuses_a_wrong_assay_version() -> None:
    with pytest.raises(q.QualificationError, match="assay_version"):
        q.normalize_verdict(_minimal_verdict(), assay_version="0.0.1", head_oid="1" * 40, base_oid="2" * 40)


def test_normalize_verdict_refuses_a_wrong_commit() -> None:
    with pytest.raises(q.QualificationError, match="not the disposable HEAD"):
        q.normalize_verdict(_minimal_verdict(), assay_version="9.9.9", head_oid="f" * 40, base_oid="2" * 40)


def test_normalize_verdict_refuses_a_wrong_base() -> None:
    with pytest.raises(q.QualificationError, match="resolved.base"):
        q.normalize_verdict(_minimal_verdict(), assay_version="9.9.9", head_oid="1" * 40, base_oid="f" * 40)


@pytest.mark.parametrize("field", ["started", "ended"])
def test_normalize_verdict_refuses_an_empty_timestamp(field: str) -> None:
    document = _minimal_verdict(**{field: ""})
    with pytest.raises(q.QualificationError, match="nonempty timestamp"):
        q.normalize_verdict(document, assay_version="9.9.9", head_oid="1" * 40, base_oid="2" * 40)


_PLACEHOLDER_SUBSTITUTIONS = {
    "@ASSAY_VERSION@": "9.9.9",
    "@HEAD_OID@": "1" * 40,
    "@BASE_OID@": "2" * 40,
    "@STARTED@": "2026-08-18T00:00:00+00:00",
    "@ENDED@": "2026-08-18T00:01:00+00:00",
}


def _witness_as_actual() -> dict:
    """The frozen witness with its own placeholders reversed back into
    real-looking values -- mirrors ``qualify_topos.py``'s own precedent
    test (``_pass_template_actual``) for round-tripping a locked template."""
    template = json.loads(_WITNESS_PATH.read_text(encoding="utf-8"))

    def replace(value):
        if isinstance(value, str) and value in _PLACEHOLDER_SUBSTITUTIONS:
            return _PLACEHOLDER_SUBSTITUTIONS[value]
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return replace(template)


def test_compare_with_witness_accepts_the_frozen_witness_round_tripped() -> None:
    actual = _witness_as_actual()
    q.compare_with_witness(
        actual,
        _WITNESS_PATH,
        assay_version="9.9.9",
        head_oid="1" * 40,
        base_oid="2" * 40,
    )  # must not raise


def test_compare_with_witness_refuses_a_corrupted_mutation_bucket() -> None:
    actual = _witness_as_actual()
    actual["claims"][1]["mutation"]["killed"] = []
    with pytest.raises(q.QualificationError, match="differs from the frozen witness"):
        q.compare_with_witness(
            actual,
            _WITNESS_PATH,
            assay_version="9.9.9",
            head_oid="1" * 40,
            base_oid="2" * 40,
        )


def test_witness_file_is_valid_json_with_the_v7_schema_version() -> None:
    document = json.loads(_WITNESS_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == 7
    assert document["judgment"]["resolved"]["language"] == "sql"
    assert document["outcome"] == "FAIL"
    assert document["reason_code"] == "MUTANTS_SURVIVED"


def test_require_witness_commit_matches_accepts_the_disposable_head() -> None:
    q._require_witness_commit_matches({"commit": "1" * 40}, "1" * 40)  # must not raise


def test_require_witness_commit_matches_refuses_a_wrong_commit() -> None:
    with pytest.raises(q.QualificationError, match="not the disposable HEAD"):
        q._require_witness_commit_matches({"commit": "f" * 40}, "1" * 40)


# =============================================================================
# _assay_argv / _witness_wrapper_script (pure string construction)
# =============================================================================


def test_assay_argv_bootstraps_sys_path_and_forwards_arguments() -> None:
    argv = q._assay_argv("/usr/bin/python3", "run", "lane-name")
    assert argv[0] == "/usr/bin/python3"
    assert argv[1] == "-c"
    assert str(q._SRC_ROOT) in argv
    assert argv[-2:] == ["run", "lane-name"]


def test_assay_argv_bootstrap_script_is_syntactically_valid_python() -> None:
    argv = q._assay_argv(sys.executable)
    compile(argv[2], "<bootstrap>", "exec")  # must not raise


def test_witness_wrapper_script_is_valid_posix_sh() -> None:
    script = q._witness_wrapper_script(container_name="my-container", dbname="witness", restrict_key="thekey")
    proc = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr


def test_witness_wrapper_script_interpolates_container_dbname_and_key() -> None:
    script = q._witness_wrapper_script(container_name="my-container", dbname="witness", restrict_key="thekey")
    assert "my-container" in script
    assert "SCHEMA_GATE_DBNAME=witness" in script
    assert "SCHEMA_GATE_RESTRICT_KEY=thekey" in script
    assert "sh /schema-gate.sh" in script


def test_witness_wrapper_script_carries_the_exact_assertion_sql_verbatim() -> None:
    script = q._witness_wrapper_script(container_name="c", dbname="d", restrict_key="k")
    assert q._WITNESS_TEST_ASSERTION_SQL in script


def test_witness_wrapper_script_never_reaches_dstdns_own_broken_script() -> None:
    script = q._witness_wrapper_script(container_name="c", dbname="d", restrict_key="k")
    assert "scripts/schema-gate.sh" not in script


def test_witness_test_cmd_references_the_dbname_env_var_not_a_literal() -> None:
    """The command is delivered via ``-e SCHEMA_GATE_TEST_CMD=...`` and must
    stay a single shell word once split by the OUTER wrapper -- it names
    ``$SCHEMA_GATE_DBNAME`` literally so the INNER ``schema-gate.sh`` shell
    expands it, never a baked-in database name."""
    assert '"$SCHEMA_GATE_DBNAME"' in q._WITNESS_TEST_CMD
    assert "'" not in q._WITNESS_TEST_CMD or q._WITNESS_TEST_CMD.count("'") == 0


# =============================================================================
# print_o3_receipt / print_o4_receipt
# =============================================================================


def test_print_o3_receipt_writes_every_scenario_and_the_control() -> None:
    report = {
        "dstdns_commit": "1" * 40,
        "scenarios": [
            {
                "name": "x",
                "applied": True,
                "matches_operator": True,
                "baseline_observed": "NO",
                "mutant_observed": "YES",
            }
        ],
        "invalid_control": {"gate_returncode": 3, "dump_present": False},
    }
    stream = io.StringIO()
    q.print_o3_receipt(report, stream=stream)
    text = stream.getvalue()
    assert "dstdns_commit=" in text
    assert "matches_operator=True" in text
    assert "invalid_control=" in text


def test_print_o4_receipt_writes_every_field() -> None:
    report = {"scenario": "x", "fresh_is_nullable": "YES", "residue_is_nullable": "NO"}
    stream = io.StringIO()
    q.print_o4_receipt(report, stream=stream)
    text = stream.getvalue()
    assert "scenario=x" in text
    assert "fresh_is_nullable=YES" in text


# =============================================================================
# ThrowawayPostgres -- pure construction (no docker touched by __init__)
# =============================================================================


def test_throwaway_postgres_names_are_unique_and_prefixed() -> None:
    a = q.ThrowawayPostgres()
    b = q.ThrowawayPostgres()
    assert a.name != b.name
    assert a.name.startswith("assay-p34w9-")
    assert a._started is False


def test_create_role_with_retry_succeeds_on_a_later_attempt(monkeypatch) -> None:
    container = q.ThrowawayPostgres()
    monkeypatch.setattr(q.time, "sleep", lambda _seconds: None)
    attempts_seen: list[int] = []

    def flaky_exec(argv, *, input=None, check=True, timeout=180):
        attempts_seen.append(1)
        returncode = 0 if len(attempts_seen) >= 2 else 1
        return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout="", stderr="transient")

    monkeypatch.setattr(container, "exec", flaky_exec)
    container._create_role_with_retry("controller", attempts=5)
    assert len(attempts_seen) == 2


def test_create_role_with_retry_raises_after_every_attempt_fails(monkeypatch) -> None:
    container = q.ThrowawayPostgres()
    monkeypatch.setattr(q.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        container,
        "exec",
        lambda argv, **kwargs: subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="permanent"),
    )
    with pytest.raises(q.QualificationError, match="did not succeed after 3 attempts"):
        container._create_role_with_retry("controller", attempts=3)


def test_wait_ready_raises_when_the_container_never_becomes_ready(monkeypatch) -> None:
    container = q.ThrowawayPostgres()
    monkeypatch.setattr(q.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        q,
        "_run",
        lambda argv, **kwargs: subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(q.QualificationError, match="never became ready"):
        container._wait_ready(attempts=2)


def test_wait_ready_retries_when_pg_isready_succeeds_but_the_select_probe_fails_once(monkeypatch) -> None:
    """The narrower race this method's own docstring names: the socket is
    ready (``pg_isready`` exits 0) a moment before a real query reliably
    succeeds."""
    container = q.ThrowawayPostgres()
    monkeypatch.setattr(q.time, "sleep", lambda _seconds: None)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "pg_isready" in argv:
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
        # the SELECT 1 probe: fails on the first attempt, succeeds on the second
        select_probe_count = sum(1 for call in calls if "psql" in call)
        if select_probe_count <= 1:
            return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="not ready yet")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="1\n", stderr="")

    monkeypatch.setattr(q, "_run", fake_run)
    container._wait_ready(attempts=5)  # must not raise
    assert sum(1 for call in calls if "psql" in call) == 2


def test_remove_is_a_no_op_on_a_never_started_container() -> None:
    container = q.ThrowawayPostgres()
    assert container._started is False
    container._remove()  # must not raise, must not attempt a docker call
    assert container._started is False


# =============================================================================
# The O3/O4 pure verdict helpers (no docker: constructed CompletedProcess/dict)
# =============================================================================


def _proc(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_require_gate_applied_accepts_a_clean_apply() -> None:
    q._require_gate_applied(_proc(0), True, context="x")  # must not raise


def test_require_gate_applied_refuses_a_nonzero_exit() -> None:
    with pytest.raises(q.QualificationError, match="x"):
        q._require_gate_applied(_proc(1), True, context="x")


def test_require_gate_applied_refuses_a_missing_dump_even_on_exit_zero() -> None:
    with pytest.raises(q.QualificationError, match="x"):
        q._require_gate_applied(_proc(0), False, context="x")


def test_require_scenario_baseline_matches_accepts_the_expected_value() -> None:
    scenario = q.SCENARIOS[0]
    q._require_scenario_baseline_matches(scenario, scenario.baseline_expected)  # must not raise


def test_require_scenario_baseline_matches_refuses_a_drifted_value() -> None:
    scenario = q.SCENARIOS[0]
    with pytest.raises(q.QualificationError, match="baseline catalog value drifted"):
        q._require_scenario_baseline_matches(scenario, "SOMETHING-ELSE")


def _ok_scenario_report(**overrides) -> dict:
    report = {"name": "x", "applied": True, "matches_operator": True}
    report.update(overrides)
    return report


def test_require_scenarios_ok_accepts_every_scenario_applied_and_matching() -> None:
    q._require_scenarios_ok([_ok_scenario_report(), _ok_scenario_report(name="y")])  # must not raise


def test_require_scenarios_ok_refuses_a_scenario_that_did_not_apply() -> None:
    with pytest.raises(q.QualificationError, match="did not apply cleanly"):
        q._require_scenarios_ok([_ok_scenario_report(applied=False)])


def test_require_scenarios_ok_refuses_a_scenario_whose_delta_did_not_match() -> None:
    with pytest.raises(q.QualificationError, match="catalog delta did not match"):
        q._require_scenarios_ok([_ok_scenario_report(matches_operator=False)])


def test_require_invalid_control_refused_accepts_a_genuine_refusal() -> None:
    q._require_invalid_control_refused({"gate_returncode": 3, "dump_present": False})  # must not raise


def test_require_invalid_control_refused_refuses_when_it_unexpectedly_applied() -> None:
    with pytest.raises(q.QualificationError, match="unexpectedly applied cleanly"):
        q._require_invalid_control_refused({"gate_returncode": 0, "dump_present": True})


def test_require_o4_gate_applied_accepts_a_clean_exit() -> None:
    q._require_o4_gate_applied(_proc(0), context="x")  # must not raise


def test_require_o4_gate_applied_refuses_a_nonzero_exit() -> None:
    with pytest.raises(q.QualificationError, match="x"):
        q._require_o4_gate_applied(_proc(1, stderr="boom"), context="x")


def _ok_o4_report(**overrides) -> dict:
    report = {
        "fresh_is_nullable": "YES",
        "residue_is_nullable": "NO",
        "fresh_dump_differs_from_baseline": True,
        "residue_dump_equals_baseline": True,
    }
    report.update(overrides)
    return report


def test_require_o4_invariants_accepts_the_true_shape() -> None:
    q._require_o4_invariants(_ok_o4_report())  # must not raise


def test_require_o4_invariants_refuses_fresh_not_observing_the_mutation() -> None:
    with pytest.raises(q.QualificationError, match="did not observe the mutation"):
        q._require_o4_invariants(_ok_o4_report(fresh_is_nullable="NO"))


def test_require_o4_invariants_refuses_residue_observing_the_mutation() -> None:
    with pytest.raises(q.QualificationError, match="no false-survival to convert"):
        q._require_o4_invariants(_ok_o4_report(residue_is_nullable="YES"))


def test_require_o4_invariants_refuses_a_fresh_dump_equal_to_baseline() -> None:
    with pytest.raises(q.QualificationError, match="did not differ from baseline"):
        q._require_o4_invariants(_ok_o4_report(fresh_dump_differs_from_baseline=False))


def test_require_o4_invariants_refuses_a_residue_dump_unequal_to_baseline() -> None:
    with pytest.raises(q.QualificationError, match="not byte-identical to baseline"):
        q._require_o4_invariants(_ok_o4_report(residue_dump_equals_baseline=False))


# =============================================================================
# main() -- argument handling, three orchestrators stubbed
# =============================================================================


def _stub_orchestrators(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_o3(*, source_repo, scratch):
        calls.append("o3")
        scratch.mkdir(parents=True)
        return {"dstdns_commit": "x", "scenarios": [], "invalid_control": {}}

    def fake_o4(*, source_repo, scratch):
        calls.append("o4")
        scratch.mkdir(parents=True)
        return {"scenario": "x"}

    def fake_witness(*, source_repo, scratch, python=sys.executable):
        calls.append("witness")
        scratch.mkdir(parents=True)
        return {"verdict": {"outcome": "PASS"}}

    monkeypatch.setattr(q, "run_o3_span_fidelity", fake_o3)
    monkeypatch.setattr(q, "run_o4_residue_probe", fake_o4)
    monkeypatch.setattr(q, "capture_witness", fake_witness)
    return calls


def test_main_default_mode_runs_o3_and_writes_json(tmp_path, monkeypatch, capsys) -> None:
    calls = _stub_orchestrators(monkeypatch)
    scratch = tmp_path / "scratch"
    out_json = tmp_path / "report.json"
    exit_code = q.main(
        ["--source-repo", str(tmp_path), "--scratch", str(scratch), "--json", str(out_json)]
    )
    assert exit_code == 0
    assert calls == ["o3"]
    assert json.loads(out_json.read_text())["dstdns_commit"] == "x"
    captured = capsys.readouterr()
    assert captured.out == "ASSAY_P34_W9_QUALIFIED=1\n"


def test_main_default_mode_without_json_flag_does_not_write_a_file(tmp_path, monkeypatch) -> None:
    calls = _stub_orchestrators(monkeypatch)
    scratch = tmp_path / "scratch"
    exit_code = q.main(["--source-repo", str(tmp_path), "--scratch", str(scratch)])
    assert exit_code == 0
    assert calls == ["o3"]


def test_main_residue_probe_mode_dispatches_to_o4(tmp_path, monkeypatch, capsys) -> None:
    calls = _stub_orchestrators(monkeypatch)
    scratch = tmp_path / "scratch"
    exit_code = q.main(["--source-repo", str(tmp_path), "--scratch", str(scratch), "--residue-probe"])
    assert exit_code == 0
    assert calls == ["o4"]
    assert "scenario=x" in capsys.readouterr().err


def test_main_witness_mode_dispatches_to_capture_witness_and_writes_file(tmp_path, monkeypatch) -> None:
    calls = _stub_orchestrators(monkeypatch)
    scratch = tmp_path / "scratch"
    out = tmp_path / "witness.json"
    exit_code = q.main(["--source-repo", str(tmp_path), "--scratch", str(scratch), "--witness", str(out)])
    assert exit_code == 0
    assert calls == ["witness"]
    assert json.loads(out.read_text())["outcome"] == "PASS"


def test_main_refuses_a_pre_existing_scratch_directory(tmp_path, monkeypatch) -> None:
    _stub_orchestrators(monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(SystemExit):
        q.main(["--source-repo", str(tmp_path), "--scratch", str(scratch)])


def test_main_refuses_residue_probe_and_witness_together(tmp_path, monkeypatch) -> None:
    _stub_orchestrators(monkeypatch)
    scratch = tmp_path / "scratch"
    with pytest.raises(SystemExit):
        q.main(
            [
                "--source-repo",
                str(tmp_path),
                "--scratch",
                str(scratch),
                "--residue-probe",
                "--witness",
                str(tmp_path / "w.json"),
            ]
        )


def test_module_dispatches_to_main_when_run_as_a_script(monkeypatch) -> None:
    """Runs the module's own ``if __name__ == "__main__":`` line IN-PROCESS
    via :mod:`runpy` so coverage measurement sees it."""
    monkeypatch.setattr(sys, "argv", ["qualify_dstdns_sql.py"])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_MODULE_PATH), run_name="__main__")
    assert excinfo.value.code == 2  # argparse: missing required --source-repo/--scratch


# =============================================================================
# _run
# =============================================================================


def test_run_raises_on_a_command_failure_when_check_is_requested() -> None:
    with pytest.raises(q.QualificationError, match="command failed"):
        q._run(["false"], check=True)


def test_run_does_not_raise_on_failure_when_check_is_not_requested() -> None:
    proc = q._run(["false"], check=False)
    assert proc.returncode != 0


def test_run_passes_env_through_when_given() -> None:
    proc = q._run(["sh", "-c", "echo $ASSAY_P34_W9_PROBE"], env=q._env_with({"ASSAY_P34_W9_PROBE": "hi"}))
    assert proc.stdout.strip() == "hi"


# =============================================================================
# O5 -- the pg_dump reproducibility trap (docker-gated, dstdns-independent)
#   Carve command: `pytest tests/test_gate_qualify_dstdns_sql.py -k restrict_key -q`
# =============================================================================


def test_dump_with_restrict_key_is_reproducible(docker, tmp_path) -> None:
    with q.ThrowawayPostgres() as container:
        container.exec(["psql", "-U", "postgres", "-c", "CREATE DATABASE probe;"])
        container.exec(["psql", "-U", "postgres", "-d", "probe", "-c", "CREATE TABLE t(id int);"])
        first = q.verify_dump_reproducible(container, "probe", restrict_key=q.RESTRICT_KEY)
        assert b"CREATE TABLE" in first
        second = q.verify_dump_reproducible(container, "probe", restrict_key=q.RESTRICT_KEY)
        assert first == second


def test_dump_without_restrict_key_differs_and_error_names_restrict_key(docker, tmp_path) -> None:
    with q.ThrowawayPostgres() as container:
        container.exec(["psql", "-U", "postgres", "-c", "CREATE DATABASE probe2;"])
        container.exec(["psql", "-U", "postgres", "-d", "probe2", "-c", "CREATE TABLE t(id int);"])
        with pytest.raises(q.QualificationError, match=r"\\restrict"):
            q.verify_dump_reproducible(container, "probe2", restrict_key=None)


# =============================================================================
# ThrowawayPostgres lifecycle (docker-gated, dstdns-independent)
# =============================================================================


def _container_running(name: str) -> bool:
    proc = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True, timeout=15
    )
    return name in proc.stdout.splitlines()


def test_throwaway_postgres_is_removed_on_clean_exit(docker) -> None:
    container = q.ThrowawayPostgres()
    with container:
        assert _container_running(container.name)
    assert not _container_running(container.name)


def test_throwaway_postgres_is_removed_even_when_the_body_raises(docker) -> None:
    container = q.ThrowawayPostgres()
    with pytest.raises(RuntimeError, match="boom"):
        with container:
            assert _container_running(container.name)
            raise RuntimeError("boom")
    assert not _container_running(container.name)


def test_throwaway_postgres_is_removed_when_provisioning_fails_after_docker_run(docker, monkeypatch) -> None:
    """The `__enter__` `except Exception: self._remove(); raise` path: the
    real container starts, but a LATER provisioning step (role creation)
    fails -- the container must still be torn down, never leaked."""
    container = q.ThrowawayPostgres()

    def failing_role_create(role: str) -> None:
        raise q.QualificationError("simulated provisioning failure")

    monkeypatch.setattr(container, "_create_role_with_retry", failing_role_create)
    with pytest.raises(q.QualificationError, match="simulated provisioning failure"):
        container.__enter__()
    assert not _container_running(container.name)


def test_throwaway_postgres_creates_the_three_service_roles(docker) -> None:
    with q.ThrowawayPostgres() as container:
        out = container.query_one(
            "postgres",
            "SELECT string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles "
            "WHERE rolname = ANY(ARRAY['controller','workerdb','webapp'])",
        )
        assert out == ",".join(sorted(q.SERVICE_ROLES))


def test_run_gate_script_apply_dump_test_with_a_trivial_single_file_corpus(docker, tmp_path) -> None:
    """A minimal, self-contained exercise of the REAL gate script's full
    apply-and-dump-and-test flow, independent of the real dstdns corpus --
    proves :meth:`ThrowawayPostgres.run_gate_script`/``replace_corpus``/
    ``create_database``/``query_one``/``path_exists`` for real."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "01-trivial.sql").write_text("CREATE TABLE probe_table (id INT NOT NULL);\n", encoding="utf-8")
    with q.ThrowawayPostgres() as container:
        container.replace_corpus(corpus_dir)
        container.create_database("trivial")
        result = container.run_gate_script(
            dbname="trivial",
            dump_path="/dump.sql",
            kill_signal_path="/kill.txt",
            restrict_key=q.RESTRICT_KEY,
            test_cmd="true",
        )
        assert result.returncode == 0
        assert container.path_exists("/dump.sql")
        nullable = container.query_one(
            "trivial",
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='probe_table' AND column_name='id'",
        )
        assert nullable == "NO"


def test_run_gate_script_reports_a_nonzero_exit_and_writes_the_kill_signal(docker, tmp_path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "01-trivial.sql").write_text("CREATE TABLE probe_table (id INT);\n", encoding="utf-8")
    with q.ThrowawayPostgres() as container:
        container.replace_corpus(corpus_dir)
        container.create_database("trivial2")
        result = container.run_gate_script(
            dbname="trivial2",
            dump_path="/dump.sql",
            kill_signal_path="/kill.txt",
            restrict_key=q.RESTRICT_KEY,
            test_cmd="false",
        )
        assert result.returncode != 0
        assert container.path_exists("/kill.txt")


# =============================================================================
# The three real orchestrators -- docker AND the real pinned dstdns checkout
# =============================================================================


def test_run_o3_span_fidelity_end_to_end_against_the_real_dstdns_corpus(
    docker, dstdns_checkout, tmp_path
) -> None:
    report = q.run_o3_span_fidelity(source_repo=dstdns_checkout, scratch=tmp_path / "o3")
    assert len(report["scenarios"]) == 7
    assert all(entry["applied"] and entry["matches_operator"] for entry in report["scenarios"])
    assert report["invalid_control"]["gate_returncode"] != 0
    assert report["invalid_control"]["dump_present"] is False


def test_run_o4_residue_probe_end_to_end_against_the_real_dstdns_corpus(
    docker, dstdns_checkout, tmp_path
) -> None:
    report = q.run_o4_residue_probe(source_repo=dstdns_checkout, scratch=tmp_path / "o4")
    assert report["fresh_is_nullable"] == "YES"
    assert report["residue_is_nullable"] == "NO"
    assert report["fresh_dump_differs_from_baseline"] is True
    assert report["residue_dump_equals_baseline"] is True


def test_capture_witness_end_to_end_matches_the_frozen_witness(docker, dstdns_checkout, tmp_path) -> None:
    result = q.capture_witness(source_repo=dstdns_checkout, scratch=tmp_path / "witness")
    proc = subprocess.run(
        q._assay_argv(sys.executable, "--version"), capture_output=True, text=True, check=True, timeout=30
    )
    assay_version = proc.stdout.strip().removeprefix("assay ")
    q.compare_with_witness(
        result["verdict"],
        _WITNESS_PATH,
        assay_version=assay_version,
        head_oid=result["head_oid"],
        base_oid=result["base_oid"],
    )  # must not raise: the real run matches the frozen witness exactly
