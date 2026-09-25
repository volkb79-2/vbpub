"""Public ExtractConfig values refuse invalid numeric and closed-vocabulary inputs."""

from __future__ import annotations

import pytest

from nyxloom.session_extract.config import ExtractConfig


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"max_words": -2}, "max_words must be"),
        ({"max_compactions": -2}, "max_compactions must be"),
        ({"max_time_minutes": -2}, "max_time_minutes must be"),
        ({"max_checkpoints": 0}, "max_checkpoints must be"),
        ({"insert_blank_lines": -2}, "insert_blank_lines must be"),
        ({"min_gap_to_annotate": -1}, "min_gap_to_annotate must be"),
        ({"show_timestamps": "middle"}, "show_timestamps must be"),
        ({"extract_metadata": "middle"}, "extract_metadata must be"),
        ({"epochs": "0"}, "epochs must be N, A:B, or all"),
        ({"epochs": "2:1"}, "epochs range must satisfy"),
    ],
)
def test_extract_config_rejects_invalid_public_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ExtractConfig(**kwargs)


def test_legacy_compaction_limit_name_remains_a_compatibility_alias():
    config = ExtractConfig(max_lifecycle_markers=2)

    assert config.max_compactions == 2
