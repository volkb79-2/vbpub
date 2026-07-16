from __future__ import annotations

from topos.grouping import SOURCE_MIXED, CiuGroup, GroupedEntities, _phase_sort_key, group_entities
from topos.model import CiuMeta, DockerMeta, Entity, EntityFrame, Frame, MetricValue


# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------

FULL_ID = "a" * 64


def _make_entity(
    key: str,
    name: str,
    *,
    stack: str | None = None,
    phase_raw: str | None = None,
    phase: int | None = None,
    source: str = "label",
    ciu: bool = True,
) -> EntityFrame:
    """Create an EntityFrame with optional CIU metadata.

    When *ciu* is False the entity has no CiuMeta at all (simulating a
    non-ciu-managed container).
    """
    ciu_meta: CiuMeta | None = None
    if ciu:
        ciu_meta = CiuMeta(stack=stack, phase_raw=phase_raw, phase=phase, source=source)
    docker = DockerMeta(
        cid=key[-12:],
        full_id=key,
        name=name,
        image="test:latest",
        compose_project=stack or "test-stack",
    )
    entity = Entity(key=key, kind="scope", parent="system.slice", docker=docker, ciu=ciu_meta)
    return EntityFrame(entity=entity, metrics={"ram": MetricValue(42.0, "exact", raw=42)})


def _make_frame(entity_frames: list[EntityFrame]) -> Frame:
    return Frame(
        schema_version=1,
        ts=1000.0,
        interval_s=5.0,
        host={"host_load1": MetricValue(0.1, "host")},
        entities={ef.entity.key: ef for ef in entity_frames},
    )


# ---------------------------------------------------------------------------
# Phase sort key (_phase_sort_key)
# ---------------------------------------------------------------------------


class TestPhaseSortKey:
    """Phase sort key ensures numeric ordering and correct group placement."""

    def test_valid_phases_sort_numerically(self) -> None:
        """phase_2 sorts before phase_10 numerically, not lexicographically."""
        key_1 = _phase_sort_key(1, "phase_1")
        key_2 = _phase_sort_key(2, "phase_2")
        key_10 = _phase_sort_key(10, "phase_10")
        assert key_1 < key_2 < key_10

    def test_unparseable_after_valid(self) -> None:
        """Unparseable phase (phase_raw set, phase=None) sorts after valid."""
        valid = _phase_sort_key(2, "phase_2")
        unparseable = _phase_sort_key(None, "phase_x")
        assert valid < unparseable

    def test_absent_after_unparseable(self) -> None:
        """Absent phase (both None) sorts after unparseable."""
        unparseable = _phase_sort_key(None, "phase_x")
        absent = _phase_sort_key(None, None)
        assert unparseable < absent

    def test_valid_phase_0(self) -> None:
        """Phase 0 is a valid numeric phase and sorts correctly."""
        key_0 = _phase_sort_key(0, "phase_0")
        key_1 = _phase_sort_key(1, "phase_1")
        assert key_0 < key_1


# ---------------------------------------------------------------------------
# Acceptance Oracle 1: Numeric phase ordering
# ---------------------------------------------------------------------------


class TestOracle1NumericPhaseOrdering:
    """Grouped entities within a stack sort by numeric phase (1, 2, 10)."""

    def test_phases_in_numeric_order(self) -> None:
        """A stack with phases 1, 2, and 10 renders in that order.

        Assert against the grouping function's output — a test that sorts
        the list itself would prove nothing.
        """
        frame = _make_frame([
            _make_entity("scope-10", "container-10", stack="app/web", phase_raw="phase_10", phase=10),
            _make_entity("scope-1", "container-1", stack="app/web", phase_raw="phase_1", phase=1),
            _make_entity("scope-2", "container-2", stack="app/web", phase_raw="phase_2", phase=2),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 3
        phases = [g.phase for g in result.groups]
        assert phases == [1, 2, 10], f"expected [1, 2, 10] got {phases}"

    def test_same_phase_groups_together(self) -> None:
        """Entities with the same phase end up in the same group."""
        frame = _make_frame([
            _make_entity("scope-a", "container-a", stack="app/web", phase_raw="phase_2", phase=2),
            _make_entity("scope-b", "container-b", stack="app/web", phase_raw="phase_2", phase=2),
            _make_entity("scope-c", "container-c", stack="app/web", phase_raw="phase_1", phase=1),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 2
        # Phase 1 group
        assert result.groups[0].phase == 1
        assert len(result.groups[0].entity_frames) == 1
        # Phase 2 group
        assert result.groups[1].phase == 2
        assert len(result.groups[1].entity_frames) == 2


# ---------------------------------------------------------------------------
# Acceptance Oracle 2: Unparseable phase does not sort as zero
# ---------------------------------------------------------------------------


class TestOracle2UnparseablePhaseNotZero:
    """An entity with an unparseable phase is distinguishable and sorted
    correctly — it does not silently sort as zero."""

    def test_unparseable_sorts_after_valid_before_absent(self) -> None:
        """Unparseable phase sorts after valid phases, before absent.

        Valid phase (phase=2) → unparseable (phase_raw="phase_x", phase=None)
        → absent (both None).
        """
        frame = _make_frame([
            _make_entity("scope-absent", "absent", stack="app/web"),
            _make_entity("scope-valid", "valid", stack="app/web", phase_raw="phase_2", phase=2),
            _make_entity("scope-bad", "bad", stack="app/web", phase_raw="phase_x", phase=None),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 3
        # Order: valid phase → unparseable → absent
        assert result.groups[0].phase == 2
        assert result.groups[1].phase is None
        assert result.groups[1].phase_raw == "phase_x"
        assert result.groups[2].phase is None
        assert result.groups[2].phase_raw is None

    def test_unparseable_is_distinct_from_absent(self) -> None:
        """Unparseable and absent produce different group keys."""
        frame = _make_frame([
            _make_entity("scope-bad", "bad", stack="app/web", phase_raw="phase_abc", phase=None),
            _make_entity("scope-none", "none", stack="app/web"),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 2
        # Two distinct groups — they did not collapse into one
        unparseable_group = result.groups[0]
        absent_group = result.groups[1]
        assert unparseable_group.phase is None
        assert unparseable_group.phase_raw == "phase_abc"
        assert absent_group.phase is None
        assert absent_group.phase_raw is None

    def test_unparseable_never_sorts_as_zero(self) -> None:
        """An unparseable phase must not sort as phase 0.

        If the code accidentally treated None as 0, the entities would
        be in the same group.
        """
        frame = _make_frame([
            _make_entity("scope-zero", "zero", stack="app/web", phase_raw="phase_0", phase=0),
            _make_entity("scope-bad", "bad", stack="app/web", phase_raw="phase_x", phase=None),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 2
        # Phase 0 is valid and sorts before unparseable
        assert result.groups[0].phase == 0
        assert result.groups[1].phase is None


# ---------------------------------------------------------------------------
# Acceptance Oracle 3: Ungrouped entities are untouched
# ---------------------------------------------------------------------------


class TestOracle3UngroupedUntouched:
    """A frame with zero CIU-managed containers must not be affected."""

    def test_no_ciu_entities_returns_ungrouped_only(self) -> None:
        """All entities without CIU end up in ungrouped — no synthetic group."""
        frame = _make_frame([
            _make_entity("scope-a", "container-a", ciu=False),
            _make_entity("scope-b", "container-b", ciu=False),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 0
        assert len(result.ungrouped) == 2

    def test_ciu_none_is_same_as_missing_ciu(self) -> None:
        """Entity with ciu=None explicitly is also ungrouped."""
        e1 = _make_entity("scope-a", "container-a", stack="app/web", phase_raw="phase_1", phase=1)
        e2 = _make_entity("scope-b", "container-b", ciu=False)
        e2.entity.ciu = None  # explicit None, same as missing
        frame = _make_frame([e1, e2])
        result = group_entities(frame)
        assert len(result.groups) == 1
        assert len(result.ungrouped) == 1
        assert result.ungrouped[0].entity.key == "scope-b"

    def test_ungrouped_entities_keep_original_keys(self) -> None:
        """Ungrouped entities keep their original entity keys — no rewriting."""
        frame = _make_frame([
            _make_entity("system.slice/docker-a.scope", "container-a", ciu=False),
        ])
        result = group_entities(frame)
        assert result.ungrouped[0].entity.key == "system.slice/docker-a.scope"


# ---------------------------------------------------------------------------
# Acceptance Oracle 4: Tier is visible
# ---------------------------------------------------------------------------


class TestOracle4TierVisible:
    """Label-confirmed and inferred entities in the same stack are
    distinguishable."""

    def test_mixed_sources_are_not_promoted_to_label(self) -> None:
        """A group holding both tiers is 'mixed' -- never promoted to 'label'.

        Promoting would render the inferred entity under a '(label)' header,
        hiding exactly the mis-attribution the inference heuristic can make
        (P76's review found it claiming unrelated containers).
        """
        frame = _make_frame([
            _make_entity("scope-inferred", "inf-01", stack="app/web", source="inferred"),
            _make_entity("scope-label", "lab-01", stack="app/web", source="label"),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 1
        assert result.groups[0].source == SOURCE_MIXED

    def test_pure_inferred_group_has_inferred_source(self) -> None:
        """A group with only inferred entities keeps source='inferred'."""
        frame = _make_frame([
            _make_entity("scope-a", "inf-01", stack="app/web", source="inferred"),
            _make_entity("scope-b", "inf-02", stack="app/web", source="inferred"),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 1
        assert result.groups[0].source == "inferred"

    def test_different_sources_same_stack_share_a_group_but_not_a_tier(self) -> None:
        """Group key stays (stack, phase) -- the tier does not split it, and the
        group's own tier reports the mixture honestly."""
        frame = _make_frame([
            _make_entity("scope-a", "inf-01", stack="app/web", phase_raw="phase_1", phase=1, source="inferred"),
            _make_entity("scope-b", "lab-01", stack="app/web", phase_raw="phase_1", phase=1, source="label"),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 1
        assert result.groups[0].source == SOURCE_MIXED
        # Each entity keeps its own tier -- the view marks them individually.
        sources = {ef.entity.key: ef.entity.ciu.source for ef in result.groups[0].entity_frames}
        assert sources == {"scope-a": "inferred", "scope-b": "label"}


# ---------------------------------------------------------------------------
# Acceptance Oracle 5: Mixed frame
# ---------------------------------------------------------------------------


class TestOracle5MixedFrame:
    """Two stacks, three phases, plus ungrouped entities and a container
    whose ciu is None — exact group membership sets, no entity lost,
    no entity duplicated across groups."""

    def test_mixed_frame_group_membership(self) -> None:
        """Assert exact counts: no entity lost, no entity duplicated."""
        frames: list[EntityFrame] = [
            # Stack "app/web": phase 1 (2 entities), phase 2 (1 entity)
            _make_entity("scope-w1", "web-01", stack="app/web", phase_raw="phase_1", phase=1),
            _make_entity("scope-w2", "web-02", stack="app/web", phase_raw="phase_1", phase=1),
            _make_entity("scope-w3", "web-03", stack="app/web", phase_raw="phase_2", phase=2),
            # Stack "infra/redis": phase 1 (1 entity), unparseable phase (1)
            _make_entity("scope-r1", "redis-01", stack="infra/redis", phase_raw="phase_1", phase=1),
            _make_entity("scope-r2", "redis-02", stack="infra/redis", phase_raw="phase_x", phase=None),
            # Ungrouped (no CIU)
            _make_entity("scope-u1", "unmanaged-01", ciu=False),
            _make_entity("scope-u2", "unmanaged-02", ciu=False),
            # ciu=None explicitly
            _make_entity("scope-u3", "unmanaged-03", ciu=False),
        ]
        frame = _make_frame(frames)

        # Patch the third unmanaged entity to have ciu=None explicitly
        frame.entities["scope-u3"].entity.ciu = None

        result = group_entities(frame)

        # Total entities check: all 8 accounted for
        grouped_count = sum(len(g.entity_frames) for g in result.groups)
        ungrouped_count = len(result.ungrouped)
        assert grouped_count + ungrouped_count == 8, (
            f"expected 8 total entities, got {grouped_count + ungrouped_count}"
        )
        assert ungrouped_count == 3

        # Two stacks, 4 groups total (app/web:2, infra/redis:2)
        assert len(result.groups) == 4, f"expected 4 groups, got {len(result.groups)}"

        # Group 1: app/web phase 1 (2 entities)
        g1 = result.groups[0]
        assert g1.stack == "app/web"
        assert g1.phase == 1
        assert len(g1.entity_frames) == 2

        # Group 2: app/web phase 2 (1 entity)
        g2 = result.groups[1]
        assert g2.stack == "app/web"
        assert g2.phase == 2
        assert len(g2.entity_frames) == 1

        # Group 3: infra/redis phase 1 (1 entity) — phase 1 before unparseable
        g3 = result.groups[2]
        assert g3.stack == "infra/redis"
        assert g3.phase == 1
        assert len(g3.entity_frames) == 1

        # Group 4: infra/redis unparseable (1 entity)
        g4 = result.groups[3]
        assert g4.stack == "infra/redis"
        assert g4.phase is None
        assert g4.phase_raw == "phase_x"
        assert len(g4.entity_frames) == 1

    def test_no_entity_duplicated(self) -> None:
        """Every entity appears in exactly one group or ungrouped list."""
        frames: list[EntityFrame] = [
            _make_entity("scope-a", "a", stack="s1", phase_raw="phase_1", phase=1),
            _make_entity("scope-b", "b", stack="s1", phase_raw="phase_1", phase=1),
            _make_entity("scope-c", "c", stack="s2", phase_raw="phase_2", phase=2),
            _make_entity("scope-d", "d", ciu=False),
        ]
        frame = _make_frame(frames)
        result = group_entities(frame)

        all_keys: set[str] = set()
        for group in result.groups:
            for ef in group.entity_frames:
                assert ef.entity.key not in all_keys, f"duplicate key {ef.entity.key}"
                all_keys.add(ef.entity.key)
        for ef in result.ungrouped:
            assert ef.entity.key not in all_keys, f"duplicate key {ef.entity.key}"
            all_keys.add(ef.entity.key)

        assert all_keys == {"scope-a", "scope-b", "scope-c", "scope-d"}


# ---------------------------------------------------------------------------
# Group-level edge cases
# ---------------------------------------------------------------------------


class TestGroupingEdgeCases:
    def test_empty_frame(self) -> None:
        """An empty frame produces empty groups and empty ungrouped."""
        frame = _make_frame([])
        result = group_entities(frame)
        assert len(result.groups) == 0
        assert len(result.ungrouped) == 0

    def test_multiple_stacks_sort_alphabetically(self) -> None:
        """Stacks sort alphabetically when phases are equal."""
        frame = _make_frame([
            _make_entity("scope-b", "b", stack="beta", phase_raw="phase_1", phase=1),
            _make_entity("scope-a", "a", stack="alpha", phase_raw="phase_1", phase=1),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 2
        assert result.groups[0].stack == "alpha"
        assert result.groups[1].stack == "beta"

    def test_none_stack_sorts_as_empty_string(self) -> None:
        """A group with stack=None is sorted as empty string."""
        frame = _make_frame([
            _make_entity("scope-a", "a", stack=None, phase_raw="phase_1", phase=1),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 1
        assert result.groups[0].stack is None

    def test_multiple_entities_same_stack_and_phase(self) -> None:
        """Multiple entities with same (stack, phase) end up in same group."""
        frame = _make_frame([
            _make_entity("scope-a", "a", stack="app/web", phase_raw="phase_1", phase=1),
            _make_entity("scope-b", "b", stack="app/web", phase_raw="phase_1", phase=1),
            _make_entity("scope-c", "c", stack="app/web", phase_raw="phase_1", phase=1),
        ])
        result = group_entities(frame)
        assert len(result.groups) == 1
        assert len(result.groups[0].entity_frames) == 3
