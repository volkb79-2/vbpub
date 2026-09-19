"""Property checks for CIU's pure path, size, and shared-infra grammars."""

from pathlib import Path

from hypothesis import given, settings, strategies as st

from ciu.governance import parse_size_to_bytes
from ciu.paths import is_under, to_physical_path
from ciu.worktree import _parse_ref_services_arg


_SEGMENT = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122),
                   min_size=1, max_size=8)
_SEGMENTS = st.lists(_SEGMENT, min_size=0, max_size=4)


@settings(max_examples=32, deadline=None)
@given(root_parts=_SEGMENTS, child_parts=_SEGMENTS)
def test_path_containment_accepts_descendants(root_parts: list[str], child_parts: list[str]):
    root = Path("/workspace", *root_parts)
    child = root.joinpath(*child_parts)

    assert is_under(child, root)


@settings(max_examples=32, deadline=None)
@given(root_parts=_SEGMENTS, child_parts=_SEGMENTS)
def test_logical_paths_translate_to_the_same_relative_physical_path(
    root_parts: list[str], child_parts: list[str]
):
    logical_root = Path("/logical", *root_parts)
    physical_root = Path("/physical", *root_parts)
    logical_child = logical_root.joinpath(*child_parts)

    assert to_physical_path(logical_child, logical_root, physical_root) == physical_root.joinpath(
        *child_parts
    )


@settings(max_examples=32, deadline=None)
@given(number=st.integers(min_value=0, max_value=10**12), unit=st.sampled_from(["", "b", "k", "m", "g", "t"]))
def test_size_parser_preserves_docker_binary_units(number: int, unit: str):
    multiplier = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[unit]

    assert parse_size_to_bytes(f" {number}{unit.upper()} ") == number * multiplier


@settings(max_examples=32, deadline=None)
@given(aliases=st.lists(_SEGMENT, min_size=1, max_size=5, unique=True))
def test_shared_infra_aliases_are_order_preserving_and_reference_their_names(
    aliases: list[str],
):
    raw = ",".join(aliases)

    assert _parse_ref_services_arg(raw, label="--ref-services") == tuple(
        (alias, alias) for alias in sorted(aliases)
    )
