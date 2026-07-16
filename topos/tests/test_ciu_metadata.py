from __future__ import annotations

from topos.collect.dockerjoin import (
    _parse_phase,
    detect_ciu_from_labels,
    detect_ciu_inferred,
    enrich_entities,
)
from topos.config import CiuConfig, ToposConfig
from topos.model import (
    CiuMeta,
    DockerMeta,
    Entity,
    EntityFrame,
    Frame,
    MetricValue,
    ciu_from_jsonable,
    ciu_to_jsonable,
    entity_from_jsonable,
    entity_to_jsonable,
    frame_from_jsonable,
    frame_to_jsonable,
)

FULL_ID = "a" * 64
# Configured stack roots are repo-relative paths.  Compose derives its project
# name from the stack *directory name*, so these are matched on their last path
# segment ("infra/redis-core" -> compose project "redis-core").
KNOWN_STACKS = {"infra/redis-core", "app/web", "monitoring/prometheus"}


# ---------------------------------------------------------------------------
# Phase parsing (_parse_phase)
# ---------------------------------------------------------------------------


class TestParsePhase:
    def test_valid_phase_1(self) -> None:
        assert _parse_phase("phase_1") == ("phase_1", 1)

    def test_valid_phase_2(self) -> None:
        assert _parse_phase("phase_2") == ("phase_2", 2)

    def test_valid_phase_10(self) -> None:
        """phase_10 is valid: numeric ordering means 10 > 2."""
        assert _parse_phase("phase_10") == ("phase_10", 10)

    def test_absent_empty_string(self) -> None:
        """An empty label is genuine absence: both fields are None."""
        assert _parse_phase("") == (None, None)

    def test_none_input(self) -> None:
        """No label at all is genuine absence: both fields are None."""
        assert _parse_phase(None) == (None, None)

    def test_malformed_no_number(self) -> None:
        """Present-but-unparseable keeps the raw string; only phase is None."""
        assert _parse_phase("phase_") == ("phase_", None)

    def test_malformed_alpha(self) -> None:
        assert _parse_phase("phase_abc") == ("phase_abc", None)

    def test_malformed_negative(self) -> None:
        assert _parse_phase("phase_-1") == ("phase_-1", None)

    def test_malformed_garbage(self) -> None:
        assert _parse_phase("not_a_phase") == ("not_a_phase", None)

    def test_unparseable_is_distinct_from_absent(self) -> None:
        """Unparseable phase and absent phase are different states."""
        unparseable = _parse_phase("phase_abc")
        absent = _parse_phase(None)
        assert unparseable != absent
        assert unparseable[0] is not None
        assert absent[0] is None

    def test_whitespace(self) -> None:
        assert _parse_phase("  phase_3  ") == ("phase_3", 3)


# ---------------------------------------------------------------------------
# Label-confirmed detection
# ---------------------------------------------------------------------------


class TestDetectCiuFromLabels:
    def test_all_labels_present(self) -> None:
        labels = {
            "ciu.managed": "true",
            "ciu.stack": "infra/redis-core",
            "ciu.phase": "phase_2",
        }
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.source == "label"
        assert meta.stack == "infra/redis-core"
        assert meta.phase_raw == "phase_2"
        assert meta.phase == 2

    def test_stack_only(self) -> None:
        """ciu.managed + ciu.stack, no phase."""
        labels = {
            "ciu.managed": "true",
            "ciu.stack": "infra/redis-core",
        }
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.source == "label"
        assert meta.stack == "infra/redis-core"
        assert meta.phase_raw is None
        assert meta.phase is None

    def test_managed_only(self) -> None:
        """ciu.managed=true alone — minimal valid label set."""
        labels = {"ciu.managed": "true"}
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.source == "label"
        assert meta.stack is None
        assert meta.phase_raw is None
        assert meta.phase is None

    def test_ciu_managed_not_true_is_not_confirmed(self) -> None:
        labels = {"ciu.managed": "false"}
        assert detect_ciu_from_labels(labels) is None

    def test_no_ciu_labels_at_all(self) -> None:
        labels = {"com.docker.compose.project": "some-project"}
        assert detect_ciu_from_labels(labels) is None

    def test_empty_labels(self) -> None:
        assert detect_ciu_from_labels({}) is None

    def test_malformed_phase_is_not_a_crash(self) -> None:
        """Malformed ciu.phase → phase=None, raw kept, no exception."""
        labels = {
            "ciu.managed": "true",
            "ciu.stack": "app/web",
            "ciu.phase": "phase_",
        }
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.source == "label"
        assert meta.stack == "app/web"
        assert meta.phase_raw == "phase_"
        assert meta.phase is None

    def test_malformed_phase_alpha(self) -> None:
        labels = {
            "ciu.managed": "true",
            "ciu.stack": "app/web",
            "ciu.phase": "phase_abc",
        }
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.phase_raw == "phase_abc"
        assert meta.phase is None

    def test_malformed_phase_negative(self) -> None:
        labels = {
            "ciu.managed": "true",
            "ciu.stack": "app/web",
            "ciu.phase": "phase_-1",
        }
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.phase_raw == "phase_-1"
        assert meta.phase is None

    def test_absent_phase_label_keeps_both_none(self) -> None:
        """Absent phase is distinguishable from an unparseable one."""
        meta = detect_ciu_from_labels({"ciu.managed": "true", "ciu.stack": "app/web"})
        assert meta is not None
        assert meta.phase_raw is None
        assert meta.phase is None


# ---------------------------------------------------------------------------
# Inferred detection
# ---------------------------------------------------------------------------


class TestDetectCiuInferred:
    """Compose project names can never contain "/" ([a-z0-9][a-z0-9_-]*).

    The configured stack root may be a path ("infra/redis-core"); it is matched
    against the compose project on its last path segment ("redis-core").
    """

    def test_matches_stack_root_and_name_pattern(self) -> None:
        """Real compose project + ^<project>-<env>-<name>$ container name."""
        meta = detect_ciu_inferred(
            compose_project="redis-core",
            container_name="redis-core-prod-redis01",
            known_stack_roots=KNOWN_STACKS,
        )
        assert meta is not None
        assert meta.source == "inferred"
        assert meta.stack == "redis-core"
        assert meta.phase_raw is None
        assert meta.phase is None

    def test_path_style_stack_root_matches_on_basename(self) -> None:
        """A configured "infra/redis-core" matches compose project "redis-core"."""
        meta = detect_ciu_inferred(
            compose_project="redis-core",
            container_name="redis-core-prod-redis01",
            known_stack_roots={"infra/redis-core"},
        )
        assert meta is not None
        assert meta.stack == "redis-core"

    def test_compose_project_not_in_known_stacks(self) -> None:
        meta = detect_ciu_inferred(
            compose_project="unknown-stack",
            container_name="unknown-stack-prod-app",
            known_stack_roots=KNOWN_STACKS,
        )
        assert meta is None

    def test_name_does_not_match_pattern(self) -> None:
        """Container name has no <env>-<name> tail — no inference."""
        meta = detect_ciu_inferred(
            compose_project="web",
            container_name="web-bare",
            known_stack_roots=KNOWN_STACKS,
        )
        assert meta is None

    def test_no_compose_project(self) -> None:
        """No compose project → no inference."""
        meta = detect_ciu_inferred(
            compose_project=None,
            container_name="some-name",
            known_stack_roots=KNOWN_STACKS,
        )
        assert meta is None

    def test_empty_stack_roots(self) -> None:
        """No known stack roots → no inference."""
        meta = detect_ciu_inferred(
            compose_project="web",
            container_name="web-prod-web01",
            known_stack_roots=set(),
        )
        assert meta is None

    def test_inferred_is_distinct_from_label(self) -> None:
        """Inferred CiuMeta has source='inferred' not 'label'."""
        meta = detect_ciu_inferred(
            compose_project="redis-core",
            container_name="redis-core-prod-redis01",
            known_stack_roots=KNOWN_STACKS,
        )
        assert meta is not None
        assert meta.source == "inferred"
        # A label-confirmed container would have source="label"
        assert meta.source != "label"

    def test_name_pattern_variants(self) -> None:
        """Container names with hyphens in the <name> part still match."""
        meta = detect_ciu_inferred(
            compose_project="web",
            container_name="web-staging-web-api-v2",
            known_stack_roots=KNOWN_STACKS,
        )
        assert meta is not None
        assert meta.source == "inferred"
        assert meta.stack == "web"

    # -- Regression: false positives fixed by anchoring the name to the project

    def test_unrelated_container_name_is_rejected(self) -> None:
        """Regression: an unrelated container sharing the compose project was
        claimed as ciu-managed, because the old code matched the bare
        ^<a>-<b>-<c>$ shape instead of anchoring on the project prefix."""
        assert (
            detect_ciu_inferred(
                compose_project="redis-core",
                container_name="totally-unrelated-thing",
                known_stack_roots={"redis-core"},
            )
            is None
        )

    def test_pterodactyl_uuid_container_is_rejected(self) -> None:
        """Regression: a Pterodactyl UUID-named container matched the bare
        hyphen shape and was falsely inferred as a ciu container."""
        assert (
            detect_ciu_inferred(
                compose_project="redis-core",
                container_name="3f2b1a9c-1111-4222-8333-444455556666",
                known_stack_roots={"redis-core"},
            )
            is None
        )

    def test_unconfigured_project_is_rejected(self) -> None:
        """Regression: a well-formed container in a project that is NOT a
        configured known stack must never be inferred as ciu-managed."""
        assert (
            detect_ciu_inferred(
                compose_project="other-project",
                container_name="other-project-prod-x",
                known_stack_roots={"redis-core"},
            )
            is None
        )


# ---------------------------------------------------------------------------
# Negative: non-ciu container is not annotated
# ---------------------------------------------------------------------------


class TestNegativeNonCiu:
    def test_plain_container_no_ciu(self) -> None:
        """A bare docker-run container with no compose labels is not ciu-annotated."""
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> list[dict]:
            return [{"Id": FULL_ID, "Name": "/my-app", "Config": {"Image": "ubuntu:latest"}}]

        result = enrich_entities(entities, inspect, known_stack_roots=KNOWN_STACKS)
        entity = result[key]
        # Docker metadata exists but no CIU
        assert entity.docker is not None
        assert entity.ciu is None

    def test_ciu_managed_false_is_not_ciu(self) -> None:
        """ciu.managed=false should not trigger label-confirmed detection."""
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> list[dict]:
            return [{
                "Id": FULL_ID,
                "Name": "/my-app",
                "Config": {
                    "Image": "ubuntu:latest",
                    "Labels": {"ciu.managed": "false", "ciu.stack": "app/web"},
                },
            }]

        result = enrich_entities(entities, inspect, known_stack_roots=KNOWN_STACKS)
        assert result[key].ciu is None

    def test_inferred_not_confused_with_label(self) -> None:
        """A label-confirmed and inferred container produce different sources."""
        # Label-confirmed
        label_key = f"system.slice/docker-{'b' * 64}.scope"
        label_entities = {label_key: Entity(key=label_key, kind="scope", parent="system.slice")}

        def label_inspect(_cid: str) -> list[dict]:
            return [{
                "Id": "b" * 64,
                "Name": "/redis-core-prod-redis01",
                "Config": {
                    "Image": "redis:7",
                    "Labels": {
                        "ciu.managed": "true",
                        "ciu.stack": "infra/redis-core",
                        "ciu.phase": "phase_1",
                    },
                },
            }]

        label_result = enrich_entities(label_entities, label_inspect, known_stack_roots=KNOWN_STACKS)
        assert label_result[label_key].ciu is not None
        assert label_result[label_key].ciu.source == "label"

        # Inferred
        inf_key = f"system.slice/docker-{'c' * 64}.scope"
        inf_entities = {inf_key: Entity(key=inf_key, kind="scope", parent="system.slice")}

        def inf_inspect(_cid: str) -> list[dict]:
            return [{
                "Id": "c" * 64,
                "Name": "/redis-core-prod-redis02",
                "Config": {
                    "Image": "redis:7",
                    "Labels": {"com.docker.compose.project": "redis-core"},
                },
            }]

        inf_result = enrich_entities(inf_entities, inf_inspect, known_stack_roots=KNOWN_STACKS)
        assert inf_result[inf_key].ciu is not None
        assert inf_result[inf_key].ciu.source == "inferred"


# ---------------------------------------------------------------------------
# Malformed phase labels
# ---------------------------------------------------------------------------


class TestMalformedPhase:
    def test_phase_no_crash(self) -> None:
        """Malformed phase labels never raise."""
        labels = {"ciu.managed": "true", "ciu.phase": "phase_"}
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.phase is None
        assert meta.phase_raw == "phase_"

    def test_phase_alpha(self) -> None:
        labels = {"ciu.managed": "true", "ciu.phase": "phase_abc"}
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.phase is None
        assert meta.phase_raw == "phase_abc"

    def test_phase_missing(self) -> None:
        labels = {"ciu.managed": "true"}
        meta = detect_ciu_from_labels(labels)
        assert meta is not None
        assert meta.phase is None
        assert meta.phase_raw is None

    def test_phase_negative_is_unparseable_not_absent(self) -> None:
        """A negative phase number is unparseable — the raw label is kept."""
        assert _parse_phase("phase_-1") == ("phase_-1", None)


# ---------------------------------------------------------------------------
# Frame-schema compatibility
# ---------------------------------------------------------------------------


class TestFrameSchema:
    """Existing frames without ciu fields must still parse correctly."""

    def test_pre_p76_fixture_still_parses(self) -> None:
        """An existing pre-P76 fixture (no ciu field) deserializes without error."""
        fixture = {
            "schema_version": 1,
            "ts": 1000.0,
            "interval_s": 5.0,
            "host": {"host_load1": [0.1, "host"]},
            "entities": {
                "x.slice": {
                    "entity": {
                        "key": "x.slice",
                        "kind": "slice",
                        "parent": "",
                        "docker": None,
                        "tier": None,
                        "is_protected": False,
                    },
                    "metrics": {
                        "ram": [123, "exact", 123],
                    },
                    "findings": [],
                },
            },
        }
        # No 'ciu' key anywhere — this is a pre-P76 fixture
        frame = frame_from_jsonable(fixture)
        entity_frame = frame.entities["x.slice"]
        assert entity_frame.entity.ciu is None
        assert entity_frame.entity.docker is None

    def test_no_ciu_containers_serializes_without_ciu_noise(self) -> None:
        """A frame with no CIU-managed containers omits the ciu key entirely."""
        frame = Frame(
            1,
            1000.0,
            5.0,
            {"host_load1": MetricValue(0.1, "host")},
            {
                "x.slice": EntityFrame(
                    Entity("x.slice", "slice", ""),
                    {"ram": MetricValue(123, "exact", raw=123)},
                ),
            },
        )
        jsonable = frame_to_jsonable(frame)
        entity_json = jsonable["entities"]["x.slice"]["entity"]
        # The key is omitted, not emitted as null.
        assert "ciu" not in entity_json
        # Round-trip
        restored = frame_from_jsonable(jsonable)
        assert restored == frame

    def test_entity_to_jsonable_omits_ciu_when_none(self) -> None:
        """entity_to_jsonable omits "ciu" rather than emitting "ciu": null."""
        entity = Entity(key="x.slice", kind="slice", parent="")
        jsonable = entity_to_jsonable(entity)
        assert "ciu" not in jsonable
        assert entity_from_jsonable(jsonable) == entity

    def test_ciu_meta_round_trip(self) -> None:
        """CiuMeta serializes and deserializes correctly."""
        meta = CiuMeta(stack="app/web", phase_raw="phase_2", phase=2, source="label")
        jsonable = ciu_to_jsonable(meta)
        assert jsonable == {"stack": "app/web", "phase_raw": "phase_2", "phase": 2, "source": "label"}
        restored = ciu_from_jsonable(jsonable)
        assert restored == meta

    def test_ciu_meta_unparseable_phase_round_trip(self) -> None:
        """An unparseable phase_raw survives serialization with phase=None."""
        meta = CiuMeta(stack="app/web", phase_raw="phase_abc", phase=None, source="label")
        restored = ciu_from_jsonable(ciu_to_jsonable(meta))
        assert restored == meta
        assert restored is not None
        assert restored.phase_raw == "phase_abc"
        assert restored.phase is None

    def test_ciu_meta_none_round_trip(self) -> None:
        assert ciu_to_jsonable(None) is None
        assert ciu_from_jsonable(None) is None

    def test_frame_with_ciu_round_trip(self) -> None:
        """A frame with CIU metadata survives serialization/deserialization."""
        frame = Frame(
            1,
            1000.0,
            5.0,
            {"host_load1": MetricValue(0.1, "host")},
            {
                "x.scope": EntityFrame(
                    Entity(
                        "x.scope",
                        "scope",
                        "system.slice",
                        docker=DockerMeta(
                            cid="aabbccddeeff",
                            full_id="a" * 64,
                            name="redis-core-prod-redis01",
                            image="redis:7",
                            compose_project="redis-core",
                        ),
                        ciu=CiuMeta(
                            stack="infra/redis-core",
                            phase_raw="phase_1",
                            phase=1,
                            source="label",
                        ),
                    ),
                    {"ram": MetricValue(456, "exact", raw=456)},
                ),
            },
        )
        jsonable = frame_to_jsonable(frame)
        restored = frame_from_jsonable(jsonable)
        assert restored == frame
        assert restored.entities["x.scope"].entity.ciu is not None
        assert restored.entities["x.scope"].entity.ciu.stack == "infra/redis-core"

    def test_dockerjoin_integration_with_entity_round_trip(self) -> None:
        """Entity with ciu metadata serializes/deserializes through entity helpers."""
        entity = Entity(
            key="system.slice/docker-ffff.scope",
            kind="scope",
            parent="system.slice",
            docker=DockerMeta(
                cid="ffffffffffff",
                full_id="f" * 64,
                name="web-prod-web01",
                image="nginx:latest",
                compose_project="web",
            ),
            ciu=CiuMeta(stack="app/web", phase_raw="phase_1", phase=1, source="label"),
        )
        jsonable = entity_to_jsonable(entity)
        restored = entity_from_jsonable(jsonable)
        assert restored == entity


# ---------------------------------------------------------------------------
# enrich_entities integration tests
# ---------------------------------------------------------------------------


class TestEnrichEntitiesIntegration:
    """Full-pipeline tests through enrich_entities."""

    def test_label_confirmed_through_enrich(self) -> None:
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> list[dict]:
            return [{
                "Id": FULL_ID,
                "Name": "/redis-core-prod-redis01",
                "Config": {
                    "Image": "redis:7",
                    "Labels": {
                        "ciu.managed": "true",
                        "ciu.stack": "infra/redis-core",
                        "ciu.phase": "phase_2",
                        "com.docker.compose.project": "redis-core",
                    },
                },
            }]

        result = enrich_entities(entities, inspect, known_stack_roots=set())
        entity = result[key]
        assert entity.ciu is not None
        assert entity.ciu.source == "label"  # labels win even without stack roots
        assert entity.ciu.stack == "infra/redis-core"
        assert entity.ciu.phase == 2

    def test_inferred_through_enrich(self) -> None:
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> list[dict]:
            return [{
                "Id": FULL_ID,
                "Name": "/redis-core-prod-redis01",
                "Config": {
                    "Image": "redis:7",
                    "Labels": {"com.docker.compose.project": "redis-core"},
                },
            }]

        # KNOWN_STACKS carries "infra/redis-core"; compose ships "redis-core".
        result = enrich_entities(entities, inspect, known_stack_roots=KNOWN_STACKS)
        entity = result[key]
        assert entity.ciu is not None
        assert entity.ciu.source == "inferred"
        assert entity.ciu.stack == "redis-core"

    def test_uuid_container_in_known_project_not_inferred(self) -> None:
        """Regression: a Pterodactyl UUID container in a known compose project
        must not be annotated as ciu-managed by the full pipeline."""
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> list[dict]:
            return [{
                "Id": FULL_ID,
                "Name": "/3f2b1a9c-1111-4222-8333-444455556666",
                "Config": {
                    "Image": "ghcr.io/pterodactyl/yolks:java_17",
                    "Labels": {"com.docker.compose.project": "redis-core"},
                },
            }]

        result = enrich_entities(entities, inspect, known_stack_roots=KNOWN_STACKS)
        entity = result[key]
        assert entity.docker is not None
        assert entity.docker.ptero_uuid == "3f2b1a9c-1111-4222-8333-444455556666"
        assert entity.ciu is None

    def test_no_inference_without_stack_roots(self) -> None:
        """Without known_stack_roots, inference is disabled."""
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> list[dict]:
            return [{
                "Id": FULL_ID,
                "Name": "/redis-core-prod-redis01",
                "Config": {
                    "Image": "redis:7",
                    "Labels": {"com.docker.compose.project": "redis-core"},
                },
            }]

        result = enrich_entities(entities, inspect, known_stack_roots=set())
        entity = result[key]
        assert entity.ciu is None

    def test_inspect_error_graceful(self) -> None:
        """When docker inspect fails, docker and ciu are both None."""
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> None:
            return None

        result = enrich_entities(entities, inspect, known_stack_roots=KNOWN_STACKS)
        entity = result[key]
        assert entity.docker is None
        assert entity.ciu is None

    def test_non_docker_entity_untouched(self) -> None:
        """Non-docker entities pass through without CIU metadata."""
        key = "system.slice/some.service"
        entities = {key: Entity(key=key, kind="service", parent="system.slice")}
        result = enrich_entities(entities, known_stack_roots=KNOWN_STACKS)
        assert key in result
        assert result[key].docker is None
        assert result[key].ciu is None


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestCiuConfig:
    def test_ciu_config_defaults(self) -> None:
        """Default CiuConfig has empty known_stacks."""
        config = ToposConfig()
        assert config.ciu.known_stacks == ()

    def test_ciu_config_custom(self) -> None:
        config = ToposConfig(ciu=CiuConfig(known_stacks=("infra/redis-core",)))
        assert config.ciu.known_stacks == ("infra/redis-core",)

    def test_ciu_config_digest_stable(self) -> None:
        """ciu config is included in the config digest."""
        config_a = ToposConfig()
        config_b = ToposConfig(ciu=CiuConfig(known_stacks=("infra/redis-core",)))
        assert config_a.digest() != config_b.digest()


# ---------------------------------------------------------------------------
# Honest absence — three distinct states
# ---------------------------------------------------------------------------


class TestHonestAbsence:
    """Three distinct states: not-ciu-managed, ciu-managed, and unreadable."""

    def test_not_ciu_managed(self) -> None:
        """Container with no ciu labels and no inference → ciu=None."""
        meta = detect_ciu_from_labels({"com.docker.compose.project": "some-project"})
        assert meta is None

    def test_ciu_managed_with_data(self) -> None:
        """Container with ciu labels → CiuMeta with data."""
        meta = detect_ciu_from_labels({"ciu.managed": "true", "ciu.stack": "app/web"})
        assert meta is not None
        assert meta.stack == "app/web"

    def test_inspect_failure_is_distinct(self) -> None:
        """When inspect fails, docker is None AND ciu is None — not an empty CiuMeta."""
        key = f"system.slice/docker-{FULL_ID}.scope"
        entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

        def inspect(_cid: str) -> None:
            return None

        result = enrich_entities(entities, inspect)
        entity = result[key]
        # Both are None (in contrast to a ciu-managed entity with data)
        assert entity.docker is None
        assert entity.ciu is None
        # This is distinguishable from: entity.ciu = CiuMeta() with default values
        # because entity.ciu is None, not a CiuMeta instance
