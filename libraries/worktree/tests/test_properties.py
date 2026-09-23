"""Property tests for the neutral workspace substrate."""
from pathlib import Path

from hypothesis import given, settings, strategies as st

from worktree import canonical_path, physical_path, workspace_id_for_path


@settings(max_examples=128, deadline=None, derandomize=True, database=None)
@given(parts=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8), max_size=5))
def test_identity_is_exactly_six_lowercase_base36_characters(parts):
    value = workspace_id_for_path(Path("/tmp") / Path(*parts))
    assert len(value) == 6
    assert value == value.lower()
    assert all(char in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value)


@settings(max_examples=128, deadline=None, derandomize=True, database=None)
@given(
    root=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8), max_size=4),
    child=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8), max_size=4),
)
def test_physical_translation_preserves_relative_suffix(root, child):
    logical_root = Path("/logical", *root)
    physical_root = Path("/physical", *root)
    logical_child = logical_root.joinpath(*child)
    assert physical_path(
        logical_child, logical_root=logical_root, physical_root=physical_root
    ) == physical_root.joinpath(*child)
    assert canonical_path(logical_child).is_absolute()
