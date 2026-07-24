"""D-CORRECT-2 rename: behavioral contract oracles.

All five oracles from the F017 implementation spec, including the negative
guard that the four *AGENT_TIER constants are unchanged.
"""

import pytest

from nyxloom import decision_chat, intake_chat, onboarding_scan, onboarding_questionnaire
from nyxloom.config import RouteDef, Routes
from nyxloom.stages import compose, stage_context, validate_pipeline
from nyxloom.types import Attempt, Role


# ============================================================================
# Oracle 1: Legacy statefile role value still loads (_missing_ alias)
# ============================================================================

class TestLegacyRoleValue:
    """The _missing_ classmethod on Role maps the old serialized value."""

    def test_legacy_value_maps_to_new(self):
        assert Role("frontier-review") is Role.REVIEW_INDEPENDENT

    def test_new_value_maps_to_self(self):
        assert Role("review-independent") is Role.REVIEW_INDEPENDENT

    def test_nonexistent_value_raises(self):
        with pytest.raises(ValueError):
            Role("not-a-real-role")


# ============================================================================
# Oracle 2: Attempt.from_dict with legacy role loads end-to-end
# ============================================================================

class TestAttemptFromDict:
    """Attempt deserialization preserves the legacy role value."""

    def test_legacy_role_loads(self):
        d = {
            "attempt_id": "att-test-1",
            "role": "frontier-review",
            "state": "EXITED",
            "route": {
                "route_id": "fake-cli",
                "cli": "fake",
                "model": "fake-model",
            },
            "started": "2026-07-20T00:00:00+00:00",
        }
        attempt = Attempt.from_dict(d)
        assert attempt.role is Role.REVIEW_INDEPENDENT

    def test_new_role_loads(self):
        d = {
            "attempt_id": "att-test-2",
            "role": "review-independent",
            "state": "EXITED",
            "route": {
                "route_id": "fake-cli",
                "cli": "fake",
                "model": "fake-model",
            },
            "started": "2026-07-20T00:00:00+00:00",
        }
        attempt = Attempt.from_dict(d)
        assert attempt.role is Role.REVIEW_INDEPENDENT


# ============================================================================
# Oracle 3: for_role legacy-tier fallback
# ============================================================================

class TestForRoleFallback:
    """Routes.for_role resolves via the legacy tier name."""

    def test_flagged_wins(self):
        """A route with role_default matching the new value is returned."""
        routes = Routes(
            revision="test",
            tiers={},
            routes={
                "flagged": RouteDef(
                    route_id="flagged", cli="c", model="m",
                    role_default="review-independent",
                ),
            },
        )
        result = routes.for_role("review-independent")
        assert len(result) == 1
        assert result[0].route_id == "flagged"

    def test_tier_fallback(self):
        """A tier named after the new role value resolves."""
        routes = Routes(
            revision="test",
            tiers={"review-independent": ["route-a"]},
            routes={
                "route-a": RouteDef(route_id="route-a", cli="c", model="m"),
            },
        )
        result = routes.for_role("review-independent")
        assert len(result) == 1
        assert result[0].route_id == "route-a"

    def test_legacy_tier_fallback(self):
        """When only a 'frontier-review' tier exists, for_role still resolves."""
        routes = Routes(
            revision="test",
            tiers={"frontier-review": ["route-legacy"]},
            routes={
                "route-legacy": RouteDef(route_id="route-legacy", cli="c", model="m"),
            },
        )
        result = routes.for_role("review-independent")
        assert len(result) == 1
        assert result[0].route_id == "route-legacy"

    def test_unknown_role_returns_empty(self):
        routes = Routes(
            revision="test",
            tiers={"other": ["route-x"]},
            routes={
                "route-x": RouteDef(route_id="route-x", cli="c", model="m"),
            },
        )
        assert routes.for_role("no-such-role") == []

    def test_implementer_still_works(self):
        """Normal role resolution for other roles is unaffected."""
        routes = Routes(
            revision="test",
            tiers={"implementer": ["impl"]},
            routes={
                "impl": RouteDef(route_id="impl", cli="c", model="m"),
            },
        )
        result = routes.for_role("implementer")
        assert len(result) == 1
        assert result[0].route_id == "impl"


# ============================================================================
# Oracle 4 (GUARD): the four AGENT_TIER constants are UNCHANGED
# ============================================================================

class TestAgentTierConstantsUntouched:
    """The OUT-OF-SCOPE tier constants must not have been renamed."""

    def test_decision_tier_unchanged(self):
        assert decision_chat.DECISION_AGENT_TIER == "frontier-review"

    def test_intake_tier_unchanged(self):
        assert intake_chat.INTAKE_AGENT_TIER == "frontier-review"

    def test_assessment_tier_unchanged(self):
        assert onboarding_scan.ASSESSMENT_AGENT_TIER == "frontier-review"

    def test_questionnaire_tier_unchanged(self):
        assert onboarding_questionnaire.QUESTIONNAIRE_AGENT_TIER == "frontier-review"


# ============================================================================
# Oracle 5: Stage/preset renamed
# ============================================================================

class TestStagePresetRenamed:
    """The STAGES dict, DEFAULT_PIPELINE, and PRESETS use the new key."""

    def test_stage_exists(self):
        from nyxloom.stages import STAGE_REGISTRY
        assert "review_independent" in STAGE_REGISTRY

    def test_old_stage_key_absent(self):
        from nyxloom.stages import STAGE_REGISTRY
        assert "frontier_review" not in STAGE_REGISTRY

    def test_stage_has_correct_role(self):
        from nyxloom.stages import STAGE_REGISTRY
        assert STAGE_REGISTRY["review_independent"].role is Role.REVIEW_INDEPENDENT

    def test_in_default_pipeline(self):
        from nyxloom.stages import DEFAULT_PIPELINE
        assert "review_independent" in DEFAULT_PIPELINE

    def test_in_gated_preset(self):
        from nyxloom.stages import PRESETS
        assert "review_independent" in PRESETS["gated"]

    def test_in_lean_preset(self):
        from nyxloom.stages import PRESETS
        assert "review_independent" in PRESETS["lean"]
