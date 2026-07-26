from __future__ import annotations

import shutil
from pathlib import Path

from conftest import fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.config import ToposConfig

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
PAKS_KEY = "soulmask.slice/soulmask-paks.slice"
FOOTGUN_KEY = "transient-footgun.slice"


def _collector(root: Path, runner_name: str, protected: tuple[str, ...] = ("soulmask-paks.slice",)) -> Collector:
    return Collector(
        cgroup_root=root,
        config=ToposConfig(interval=5.0, protected_services=protected),
        host_collector=lambda: {},
        now=lambda: 100.0,
        network_providers=(),
        systemctl_show_runner=systemctl_fixture_runner(runner_name),
    )


def test_origin_clean_runtime_dropin_band() -> None:
    frame = _collector(fixture_root() / "cgroupfs" / "gstammtisch", "gstammtisch").collect_once()
    game = frame.entities[GAME_KEY]
    assert game.governance is not None
    assert game.governance["summary"] == {
        "origin": "systemd_runtime_dropin",
        "drift": False,
        "severity": "none",
        "drifted_limits": [],
        "reasons": [],
        "unit": "docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope",
    }
    assert game.governance["limits"]["mem_high"]["origin"] == "systemd_runtime_dropin"
    assert game.governance["limits"]["mem_high"]["drift"] is False


def test_origin_flags_raw_write_drift_against_systemd_record(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    (root / GAME_KEY / "memory.high").write_text("2147483648")
    frame = _collector(root, "gstammtisch").collect_once()
    game = frame.entities[GAME_KEY]
    assert game.metrics["governance_drift"].v == 1
    assert game.governance is not None
    assert game.governance["summary"]["severity"] == "warn"
    assert game.governance["limits"]["mem_high"]["origin"] == "raw_write"
    assert game.governance["limits"]["mem_high"]["severity"] == "warn"


def test_effective_memory_min_turns_red_when_ancestor_caps_protected_entity(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    (root / "soulmask.slice" / "memory.min").write_text("0")
    frame = _collector(root, "gstammtisch").collect_once()
    paks = frame.entities[PAKS_KEY]
    assert paks.metrics["effective_memory_min"].v == 0
    assert paks.metrics["governance_drift"].v == 2
    assert paks.governance is not None
    assert paks.governance["summary"]["severity"] == "red"
    assert paks.governance["limits"]["mem_min"]["severity"] == "red"
    assert paks.governance["effective_memory_min"]["clamped_by"] == {"key": "soulmask.slice", "value": 0}


def test_transient_slice_without_unit_record_is_flagged_as_raw_write() -> None:
    frame = _collector(fixture_root() / "cgroupfs" / "transient-footgun", "footgun", protected=()).collect_once()
    footgun = frame.entities[FOOTGUN_KEY]
    assert footgun.governance is not None
    assert footgun.governance["summary"]["origin"] == "raw_write"
    assert footgun.governance["limits"]["mem_min"]["origin"] == "raw_write"
    assert footgun.governance["limits"]["mem_min"]["severity"] == "warn"


# --- P133 governance origin policy coverage tests ---


def test_unavailable_live_with_systemd_record_shows_systemd_origin() -> None:
    """Unavailable live limits with successful systemd record shows systemd origin."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        if unit == "test.slice":
            return ShowResult(
                stdout="MemoryMin=1048576\nFragmentPath=/etc/systemd/system/test.slice\n",
                returncode=0,
            )
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_min": MetricValue(None, "unavail_kernel")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "systemd_unit"
    assert "unavailable" in entity.governance["limits"]["mem_min"]["reason"].lower()


def test_nonzero_systemctl_result_does_not_claim_systemd_ownership() -> None:
    """Nonzero systemctl result does not claim systemd ownership."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        # systemctl fails with nonzero returncode
        return ShowResult(stdout="", stderr="Unit not found", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_min": MetricValue(100, "exact")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "raw_write"


def test_systemd_memory_values_infinity_max_unset_and_malformed() -> None:
    """Systemd infinity/max are unlimited and match live unlimited; [not set]/n/a/malformed are unset."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    # Test infinity/max -> unlimited, matches live unlimited -> systemd_unit
    def mock_runner_unlimited(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(
            stdout="MemoryHigh=infinity\nFragmentPath=/etc/systemd/system/test.slice\n",
            returncode=0,
        )

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_high": MetricValue(None, "unlimited")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner_unlimited)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "systemd_unit"

    # Test [not set]/n/a/malformed -> unset, no recorded origin
    for systemd_text in ["[not set]", "n/a", "not_a_number"]:

        def mock_runner_unset(unit: str, properties: tuple[str, ...]) -> ShowResult:
            return ShowResult(
                stdout=f"MemoryHigh={systemd_text}\nFragmentPath=/etc/systemd/system/test2.slice\n",
                returncode=0,
            )

        frame2 = Frame(
            schema_version=1,
            ts=100.0,
            interval_s=5.0,
            host={},
            entities={
                "test2.slice": EntityFrame(
                    entity=Entity("test2.slice", "slice", ""),
                    metrics={"mem_high": MetricValue(1024, "exact")},
                )
            },
        )
        annotate_frame_governance(frame2, mock_runner_unset)
        entity2 = frame2.entities["test2.slice"]
        assert entity2.governance is not None
        # unset values don't claim systemd origin, so it's raw_write (unmanaged)
        assert entity2.governance["summary"]["origin"] == "raw_write"


def test_protected_leaf_clamped_by_ancestor_shows_red_with_clamping_ancestor() -> None:
    """Protected leaf with finite request clamped by smaller ancestor shows red with ancestor key/value."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "parent.slice": EntityFrame(
                entity=Entity("parent.slice", "slice", ""),
                metrics={"mem_min": MetricValue(512, "exact")},
            ),
            "parent.slice/child.scope": EntityFrame(
                entity=Entity("parent.slice/child.scope", "scope", "parent.slice", is_protected=True),
                metrics={"mem_min": MetricValue(1024, "exact")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)
    child = frame.entities["parent.slice/child.scope"]
    assert child.governance is not None
    assert child.governance["summary"]["severity"] == "red"
    assert child.governance["limits"]["mem_min"]["severity"] == "red"
    clamped = child.governance["effective_memory_min"]["clamped_by"]
    assert clamped is not None
    assert clamped["key"] == "parent.slice"
    assert clamped["value"] == 512


def test_unlimited_ancestor_chain_produces_unlimited_effective_value() -> None:
    """Chain of only unlimited values has unlimited effective value."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "": EntityFrame(
                entity=Entity("", "root", None),
                metrics={"mem_min": MetricValue(None, "unlimited")},
            ),
            "parent.slice": EntityFrame(
                entity=Entity("parent.slice", "slice", ""),
                metrics={"mem_min": MetricValue(None, "unlimited")},
            ),
            "parent.slice/child.scope": EntityFrame(
                entity=Entity("parent.slice/child.scope", "scope", "parent.slice"),
                metrics={"mem_min": MetricValue(None, "unlimited")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)
    child = frame.entities["parent.slice/child.scope"]
    assert child.governance is not None
    assert child.governance["effective_memory_min"]["value"] is None
    assert child.governance["effective_memory_min"]["src"] == "unlimited"


def test_unavailable_ancestor_produces_unavailable_effective_source() -> None:
    """Unavailable ancestor produces unavailable effective source."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "": EntityFrame(
                entity=Entity("", "root", None),
                metrics={"mem_min": MetricValue(100, "exact")},
            ),
            "parent.slice": EntityFrame(
                entity=Entity("parent.slice", "slice", ""),
                metrics={"mem_min": MetricValue(None, "unavail_kernel")},
            ),
            "parent.slice/child.scope": EntityFrame(
                entity=Entity("parent.slice/child.scope", "scope", "parent.slice"),
                metrics={"mem_min": MetricValue(100, "exact")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)
    child = frame.entities["parent.slice/child.scope"]
    assert child.governance is not None
    assert child.governance["effective_memory_min"]["src"] == "unavail_kernel"


def test_unmanaged_non_default_live_mem_min_is_raw_write_warn() -> None:
    """Unmanaged non-default live mem_min is raw-write warn and names the leaf."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_min": MetricValue(1024, "exact")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "raw_write"
    assert entity.governance["limits"]["mem_min"]["severity"] == "warn"
    assert "test.slice" in entity.governance["limits"]["mem_min"]["reason"]


def test_docker_scope_at_known_default_is_docker_default_without_drift() -> None:
    """Docker scope left at its known default is docker_default without drift."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    docker_key = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "": EntityFrame(
                entity=Entity("", "root", None),
                metrics={"mem_min": MetricValue(0, "exact")},
            ),
            "system.slice": EntityFrame(
                entity=Entity("system.slice", "slice", ""),
                metrics={"mem_min": MetricValue(0, "exact")},
            ),
            docker_key: EntityFrame(
                entity=Entity(docker_key, "scope", "system.slice"),
                metrics={"mem_min": MetricValue(0, "exact")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities[docker_key]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "docker_default"
    assert entity.governance["summary"]["drift"] is False


def test_entity_with_no_fragment_or_dropin_vs_runtime_dropin() -> None:
    """Entity without fragment/dropin vs runtime dropin show correct origins."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        if unit == "fragment.slice":
            # Has fragment path with matching MemoryHigh value
            return ShowResult(
                stdout="FragmentPath=/etc/systemd/system/fragment.slice\nDropInPaths=\nMemoryHigh=100\n",
                returncode=0,
            )
        elif unit == "runtime.slice":
            # Has runtime dropin with matching MemoryHigh value
            return ShowResult(
                stdout="FragmentPath=\nDropInPaths=/run/systemd/system.control/runtime.slice.d/override.conf\nMemoryHigh=200\n",
                returncode=0,
            )
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "fragment.slice": EntityFrame(
                entity=Entity("fragment.slice", "slice", ""),
                metrics={"mem_high": MetricValue(100, "exact")},
            ),
            "runtime.slice": EntityFrame(
                entity=Entity("runtime.slice", "slice", ""),
                metrics={"mem_high": MetricValue(200, "exact")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)

    fragment = frame.entities["fragment.slice"]
    assert fragment.governance is not None
    assert fragment.governance["summary"]["origin"] == "systemd_unit"

    runtime = frame.entities["runtime.slice"]
    assert runtime.governance is not None
    assert runtime.governance["summary"]["origin"] == "systemd_runtime_dropin"


def test_protected_child_under_root_not_clamped_by_root() -> None:
    """Protected child under root; _effective_memory_min removes root "" from non-root path, so child value is effective."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "": EntityFrame(
                entity=Entity("", "root", None),
                metrics={"mem_min": MetricValue(512, "exact")},
            ),
            "child.scope": EntityFrame(
                entity=Entity("child.scope", "scope", "", is_protected=True),
                metrics={"mem_min": MetricValue(1024, "exact")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)
    child = frame.entities["child.scope"]
    assert child.governance is not None
    # Root removed from path, so child's own value (1024) is effective
    assert child.governance["summary"]["severity"] == "warn"
    assert child.governance["summary"]["origin"] == "raw_write"
    assert child.governance["effective_memory_min"]["value"] == 1024
    assert child.governance["effective_memory_min"]["clamped_by"] == {"key": "child.scope", "value": 1024}


def test_systemctl_record_without_fragment_or_dropin_paths_yields_raw_write() -> None:
    """Systemctl record present but no FragmentPath/DropInPaths; recorded_origin is None, live is non-default."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        # Unit found but no fragment or dropin paths
        return ShowResult(
            stdout="MemoryHigh=100\nFragmentPath=\nDropInPaths=\n",
            returncode=0,
        )

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_high": MetricValue(100, "exact")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    # No fragment/dropin means recorded_origin is None; non-default live value is raw_write
    assert entity.governance["summary"]["origin"] == "raw_write"
    assert entity.governance["limits"]["mem_high"]["recorded_origin"] is None
    assert entity.governance["limits"]["mem_high"]["severity"] == "warn"


def test_ancestor_chain_with_unlimited_then_finite_child_exercises_saw_unlimited() -> None:
    """Ancestor chain including unlimited then finite child exercises saw_unlimited branch."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "": EntityFrame(
                entity=Entity("", "root", None),
                metrics={"mem_min": MetricValue(100, "exact")},
            ),
            "parent.slice": EntityFrame(
                entity=Entity("parent.slice", "slice", ""),
                metrics={"mem_min": MetricValue(None, "unlimited")},
            ),
            "parent.slice/child.scope": EntityFrame(
                entity=Entity("parent.slice/child.scope", "scope", "parent.slice"),
                metrics={"mem_min": MetricValue(512, "exact")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)
    child = frame.entities["parent.slice/child.scope"]
    assert child.governance is not None
    # Root excluded from child path; parent is unlimited, child finite 512 is the only finite value
    assert child.governance["effective_memory_min"]["clamped_by"] == {"key": "parent.slice/child.scope", "value": 512}


def test_systemd_memory_max_with_live_finite_value_produces_raw_write_reason() -> None:
    """Systemd MemoryHigh=max with live finite value produces raw-write reason containing 'max'."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(
            stdout="MemoryHigh=max\nFragmentPath=/etc/systemd/system/test.slice\n",
            returncode=0,
        )

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_high": MetricValue(512, "exact")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "raw_write"
    assert entity.governance["limits"]["mem_high"]["severity"] == "warn"
    assert "max" in entity.governance["limits"]["mem_high"]["reason"]


def test_entity_with_two_independent_unmanaged_drifted_limits() -> None:
    """Entity with two unmanaged non-default finite limits yields two drifted_limits and reasons."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={
                    "mem_min": MetricValue(512, "exact"),  # unmanaged, non-default (default 0)
                    "mem_low": MetricValue(256, "exact"),  # unmanaged, non-default (default 0)
                },
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    assert entity.governance["summary"]["origin"] == "raw_write"
    assert len(entity.governance["summary"]["drifted_limits"]) == 2
    assert "mem_min" in entity.governance["summary"]["drifted_limits"]
    assert "mem_low" in entity.governance["summary"]["drifted_limits"]
    assert len(entity.governance["summary"]["reasons"]) == 2


def test_returncode_nonzero_means_found_false_despite_parsed_stdout() -> None:
    """Returncode != 0 means found=false, so no recorded_origin despite parsed stdout."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        # found=false because returncode != 0, despite having parsed stdout
        return ShowResult(
            stdout="MemoryHigh=100\n",
            returncode=1,
        )

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "test.slice": EntityFrame(
                entity=Entity("test.slice", "slice", ""),
                metrics={"mem_high": MetricValue(100, "exact")},
            )
        },
    )
    annotate_frame_governance(frame, mock_runner)
    entity = frame.entities["test.slice"]
    assert entity.governance is not None
    # found=false because returncode != 0, so no recorded_origin and raw_write despite parsed stdout
    assert entity.governance["summary"]["origin"] == "raw_write"
    assert entity.governance["limits"]["mem_high"]["recorded_origin"] is None


def test_parent_with_float_metric_value_exercises_effective_scan_fallthrough() -> None:
    """Parent with non-int metric value (float) skipped in finite scan; child finite value governs effective."""
    from topos.drift.origin import annotate_frame_governance, ShowResult
    from topos.model import Entity, EntityFrame, Frame, MetricValue

    def mock_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", returncode=1)

    frame = Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={},
        entities={
            "": EntityFrame(
                entity=Entity("", "root", None),
                metrics={"mem_min": MetricValue(100, "exact")},
            ),
            "parent.slice": EntityFrame(
                entity=Entity("parent.slice", "slice", ""),
                metrics={"mem_min": MetricValue(1.5, "exact")},
            ),
            "parent.slice/child.scope": EntityFrame(
                entity=Entity("parent.slice/child.scope", "scope", "parent.slice"),
                metrics={"mem_min": MetricValue(512, "exact")},
            ),
        },
    )
    annotate_frame_governance(frame, mock_runner)
    child = frame.entities["parent.slice/child.scope"]
    assert child.governance is not None
    # Parent's float value is skipped; child's 512 is the only finite value governing effective
    assert child.governance["effective_memory_min"]["value"] == 512
    assert child.governance["effective_memory_min"]["clamped_by"] == {"key": "parent.slice/child.scope", "value": 512}
