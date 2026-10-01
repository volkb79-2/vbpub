"""B105 whole-source coverage for config's remaining public boundaries."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from assay import config
from assay.config import (
    CanaryConfig,
    CoverageConfig,
    EvidenceConfig,
    IsolationConfig,
    Lane,
    MutationConfig,
    _load_evidence,
    _load_mutation,
    _load_project_snapshot_limits,
    _load_posix_glob_list,
    _validate_evidence_dir,
)
from assay.errors import LaneConfigError
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS
from conftest import R0_LANE


def test_config_value_projections_cover_declared_and_omitted_fields():
    native = MutationConfig(
        jobs=1,
        max_mutants=2,
        operators=("python:compare-swap",),
        budget_per_candidate="2m",
        liveness="true",
        identity_exclude=("nyxloom-trove/**",),
        shard_index=0,
        shard_count=1,
    )
    assert native.as_declared() == {
        "jobs": 1,
        "max_mutants": 2,
        "operators": ["python:compare-swap"],
        "budget_per_candidate": "2m",
        "liveness": "true",
        "identity_exclude": ["nyxloom-trove/**"],
        "shard_index": 0,
        "shard_count": 1,
    }

    ingested = MutationConfig(
        format="stryker-json",
        artifact="reports/mutations.json",
        fail_under=100.0,
        kill_signal_artifact="reports/kills.json",
    )
    assert ingested.is_ingested
    assert ingested.as_declared() == {
        "format": "stryker-json",
        "artifact": "reports/mutations.json",
        "fail_under": 100.0,
        "kill_signal_artifact": "reports/kills.json",
    }

    assert CoverageConfig("coverage-py-json", "coverage.json").as_declared() == {
        "format": "coverage-py-json",
        "artifact": "coverage.json",
    }
    assert CoverageConfig(
        "coverage-istanbul-json", "coverage.json", producer="vitest-v8"
    ).as_declared()["producer"] == "vitest-v8"
    assert CanaryConfig("import-break", target="src/app.py").as_declared() == {
        "mechanism": "import-break",
        "target": "src/app.py",
    }
    assert CanaryConfig(
        "import-break",
        targets=("src/a.py", "src/b.py"),
        aggregation="all",
        budget_per_attempt="3m",
    ).as_declared() == {
        "mechanism": "import-break",
        "targets": ["src/a.py", "src/b.py"],
        "aggregation": "all",
        "budget_per_attempt": "3m",
    }
    with pytest.raises(ValueError, match="targets list is empty"):
        CanaryConfig("import-break", targets=())


def test_lane_projection_preserves_declared_optional_values():
    lane = Lane(
        name="x",
        scope="S1",
        rigor=("R0",),
        enforcement="gate",
        argv=("/usr/bin/python", "-m", "pytest"),
        env={},
        env_passthrough=(),
        budget="1m",
        budget_seconds=60.0,
        allow_argv_append=False,
        judge=None,
        where=None,
        isolation=None,
        environment_command=("python", "--version"),
        infrastructure={"GATE_ROOT": "required-env:GATE_ROOT"},
        cwd="src",
    )
    declared = lane.as_declared()
    assert declared["environment_command"] == ["python", "--version"]
    assert declared["infrastructure"] == {"GATE_ROOT": "required-env:GATE_ROOT"}
    assert declared["cwd"] == "src"
    assert "judge" not in declared
    assert "where" not in declared


def _minimal_lane_table(**updates):
    table = tomllib.loads(R0_LANE)["lanes"]["package"]
    table.update(updates)
    return table


@pytest.mark.parametrize(
    "updates",
    [
        {"environment_command": []},
        {"infrastructure": []},
        {"infrastructure": {}},
        {"infrastructure": {str(i): "required-env:X" for i in range(65)}},
        {"infrastructure": {"": "required-env:X"}},
        {"infrastructure": {"X": ""}},
        {"infrastructure": {"X": "unknown:X"}},
        {"infrastructure": {"X": "required-env:"}},
        {"infrastructure": {"X": "derived:a b"}},
        {"infrastructure": {"X": "derived:.a"}},
        {"infrastructure": {"X": "derived:a."}},
        {"env_required": ["MISSING"]},
        {"env": {"PATH": "/bin"}},
        {
            "env": {"X": "fixed"},
            "infrastructure": {"X": "required-env:X"},
        },
        {
            "env_passthrough": ["PATH", "X"],
            "infrastructure": {"X": "required-env:X"},
        },
    ],
)
def test_lane_loader_rejects_invalid_environment_and_infrastructure_declarations(
    project, updates
):
    with pytest.raises(LaneConfigError):
        config._load_lane(
            "package",
            _minimal_lane_table(**updates),
            project.root / "assay.toml",
            project.root,
        )


def test_lane_loader_accepts_declared_infrastructure_and_environment_probe(project):
    lane = config._load_lane(
        "package",
        _minimal_lane_table(
            environment_command=["python", "--version"],
            infrastructure={
                "GATE_ROOT": "required-env:GATE_ROOT",
                "IMAGE_TAG": "derived:environment.image.tag",
            },
        ),
        project.root / "assay.toml",
        project.root,
    )
    assert dict(lane.infrastructure) == {
        "GATE_ROOT": "required-env:GATE_ROOT",
        "IMAGE_TAG": "derived:environment.image.tag",
    }
    assert lane.environment_command == ("python", "--version")


def test_find_lane_file_refuses_a_tree_without_assay_toml(tmp_path):
    with pytest.raises(LaneConfigError, match="no assay.toml found"):
        config.find_lane_file(tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"unexpected": True},
        {"limits": []},
        {"limits": {"unexpected": 1}},
        {"limits": {"max_entries": 0}},
    ],
)
def test_project_snapshot_limit_loader_refuses_malformed_policy(value):
    with pytest.raises(LaneConfigError):
        _load_project_snapshot_limits(value, Path("assay.toml"))


def test_project_snapshot_limit_loader_accepts_omission_and_declared_limits():
    defaults, empty_dirty = _load_project_snapshot_limits(None, Path("assay.toml"))
    assert defaults is DEFAULT_SNAPSHOT_LIMITS
    assert empty_dirty == ()

    limits, dirty = _load_project_snapshot_limits(
        {"dirty_ignore": ["reports/**"]}, Path("assay.toml")
    )
    assert limits is defaults
    assert dirty == ("reports/**",)

    limits, dirty = _load_project_snapshot_limits(
        {"limits": {"max_entries": 7}}, Path("assay.toml")
    )
    assert limits.max_entries == 7
    assert dirty == ()


def test_isolation_config_checks_history_links_and_omission_order():
    with pytest.raises(LaneConfigError, match="snapshot_history"):
        IsolationConfig(
            snapshot_selection="repository",
            snapshot_history="all",
            unsafe_symlink_omissions=(),
        )
    with pytest.raises(LaneConfigError, match="link_paths.*tuple"):
        IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=["cache"],
        )
    with pytest.raises(LaneConfigError, match="strictly ascending"):
        IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=("z", "a"),
        )
    with pytest.raises(LaneConfigError, match="strictly ascending"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("z", "a"),
        )
    valid = IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=("a", "z"),
        link_paths=("cache", "vendor"),
    )
    assert valid.as_declared()["link_paths"] == ["cache", "vendor"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "../evidence",
        "/absolute",
        "a//b",
        "a/",
        ".",
        "a/../b",
        "bad\x00path",
        "bad\x7fpath",
        "\ud800",
        "/".join(["x"] * (config.MAX_ATTESTATION_DIR_COMPONENTS + 1)),
        "x" * (config.MAX_ATTESTATION_DIR_BYTES + 1),
    ],
)
def test_evidence_directory_validation_refuses_noncanonical_or_unbounded_paths(value):
    with pytest.raises(LaneConfigError):
        _validate_evidence_dir(value, "test", "attestation_dir")


def test_evidence_directory_validation_accepts_bounded_posix_path():
    assert _validate_evidence_dir(".assay/evidence", "test", "adjudication_dir") == ".assay/evidence"


@pytest.mark.parametrize(
    "value",
    [
        "not-an-array",
        [],
        [None],
        [{"source": "attested"}],
        [{"source": "attested", "key": "k", "extra": True}],
        [{"source": "unknown", "key": "k"}],
        [{"source": "attested", "key": "bad key"}],
        [{"source": "adjudicated", "key": "not-registered"}],
        [{"source": "attested", "key": "k"}, {"source": "attested", "key": "k"}],
    ],
)
def test_evidence_loader_refuses_each_closed_grammar_boundary(value):
    with pytest.raises(LaneConfigError):
        _load_evidence(value, "test lane")


def test_evidence_loader_accepts_registered_sources_and_preserves_order():
    adjudicator = sorted(config.ADJUDICATED_EVIDENCE_KEYS)[0]
    result = _load_evidence(
        [
            {"source": "adjudicated", "key": adjudicator},
            {"source": "attested", "key": "manual-review"},
        ],
        "test lane",
    )
    assert result == (
        EvidenceConfig("adjudicated", adjudicator),
        EvidenceConfig("attested", "manual-review"),
    )


def _valid_native_mutation(**overrides):
    value = {
        "jobs": 1,
        "max_mutants": 10,
        "operators": ["python:compare-swap"],
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("overrides", "language", "argv"),
    [
        ({"jobs": True}, "python", ("pytest",)),
        ({"jobs": 0}, "python", ("pytest",)),
        ({"max_mutants": True}, "python", ("pytest",)),
        ({"max_mutants": 0}, "python", ("pytest",)),
        ({"budget_per_candidate": 1}, "python", ("pytest",)),
        ({"budget_per_candidate": "soon"}, "python", ("pytest",)),
        ({"liveness": "true"}, "python", ("pytest",)),
        ({"liveness": True}, "python", ("python", "-m", "unittest")),
        ({"shard_index": 0}, "python", ("pytest",)),
        ({"shard_count": 2}, "python", ("pytest",)),
        ({"shard_index": False, "shard_count": 2}, "python", ("pytest",)),
        ({"shard_index": 0, "shard_count": 0}, "python", ("pytest",)),
        ({"shard_index": 2, "shard_count": 2}, "python", ("pytest",)),
    ],
)
def test_native_mutation_loader_rejects_invalid_limits_and_incomplete_execution_policy(
    tmp_path, overrides, language, argv
):
    with pytest.raises(LaneConfigError):
        _load_mutation(
            _valid_native_mutation(**overrides),
            "test lane",
            language,
            tmp_path,
            argv,
        )


def test_native_mutation_loader_accepts_declared_optional_execution_policy(tmp_path):
    result = _load_mutation(
        _valid_native_mutation(
            budget_per_candidate="auto",
            liveness=True,
            identity_exclude=["nyxloom-trove/**"],
            shard_index=0,
            shard_count=1,
        ),
        "test lane",
        "python",
        tmp_path,
        ("python", "-m", "pytest"),
    )
    assert result.liveness == "true"
    assert result.identity_exclude == ("nyxloom-trove/**",)
    assert result.shard_index == 0 and result.shard_count == 1


def test_native_mutation_loader_names_withdrawn_operator(tmp_path):
    withdrawn = sorted(config.WITHDRAWN_MUTATION_OPERATORS)[0]
    with pytest.raises(LaneConfigError, match="withdrawn"):
        _load_mutation(
            _valid_native_mutation(operators=[withdrawn]),
            "test lane",
            "python",
            tmp_path,
        )


def test_ingested_sql_kill_signal_is_validated_before_sql_ingestion_refusal(tmp_path):
    report_format = sorted(config.MUTATION_FORMAT_REGISTRY)[0]
    with pytest.raises(LaneConfigError, match="cannot ingest"):
        config._load_ingested_mutation(
            {
                "format": report_format,
                "artifact": "report.json",
                "fail_under": 100.0,
                "kill_signal_artifact": "kills.json",
            },
            "test lane",
            "sql",
            tmp_path,
        )


def test_canary_loader_rejects_non_string_per_attempt_budget(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    target = source / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="budget_per_attempt"):
        config._load_canary(
            {
                "mechanism": "import-break",
                "target": "src/app.py",
                "budget_per_attempt": False,
            },
            "test lane",
            tmp_path,
            (source,),
        )


def test_canary_target_boundary_refuses_backslash_and_normalized_escape(tmp_path):
    project_root = tmp_path / "project"
    src = project_root / "src"
    src.mkdir(parents=True)
    backslash_file = src / "odd\\name.py"
    backslash_file.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="backslash"):
        config._load_canary_target(
            "src/odd\\name.py",
            where="test",
            field="judge.canary.target",
            project_root=project_root,
            source_root_paths=(src,),
        )

    outside = tmp_path / "outside.py"
    outside.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="does not normalize"):
        config._load_canary_target(
            "../outside.py",
            where="test",
            field="judge.canary.target",
            project_root=project_root,
            source_root_paths=(Path("/"),),
        )


def test_posix_glob_loader_returns_canonical_nonempty_values():
    assert _load_posix_glob_list(["reports/*.json"], "test", "paths") == (
        "reports/*.json",
    )
