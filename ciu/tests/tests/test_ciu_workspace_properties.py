"""Hypothesis coverage for CIU's root/workspace boundary."""

from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st

from ciu import workspace


@settings(
    max_examples=64,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    root_name=st.text(
        alphabet=st.characters(
            whitelist_categories=("Ll", "Lu", "Nd"),
            whitelist_characters="-_",
        ),
        min_size=1,
        max_size=12,
    ).filter(lambda value: value not in {".", ".."}),
    nested=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=8),
        min_size=0,
        max_size=3,
    ),
)
def test_nearest_committed_root_is_stable_for_nested_paths(
    tmp_path: Path, root_name: str, nested: list[str]
) -> None:
    root = tmp_path / root_name
    root.mkdir(exist_ok=True)
    (root / workspace.GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")
    invocation = root.joinpath(*nested)
    invocation.mkdir(parents=True, exist_ok=True)

    assert workspace.resolve_ciu_root(invocation) == root.resolve()


@settings(
    max_examples=64,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(parts=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=8), max_size=4))
def test_ambient_repo_root_never_changes_explicit_root(tmp_path: Path, parts: list[str]) -> None:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / workspace.GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")
    candidate = root.joinpath(*parts)
    assert workspace.resolve_ciu_root(candidate, root_folder=root) == root.resolve()
