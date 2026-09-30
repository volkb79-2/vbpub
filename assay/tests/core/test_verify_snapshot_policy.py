"""B006(a)/A-269 (wave-1 §6) -- :func:`assay.verify._check_snapshot_policy`,
the raw verifier's own INDEPENDENT check (A-182): every rule it enforces
also exists, worded differently, in :class:`assay.verdict.SnapshotPolicy`'s
``__post_init__`` and in the shipped JSON Schema's ``$defs/snapshot_policy``
-- this module proves the raw layer is independently reachable, never that
"verify_document says no" (which reconstruction alone could also produce,
per this suite's own sibling ``test_verify_layer_independence.py``).

Called DIRECTLY on a minimal ``{"declared_rigor": ..., "snapshot_policy":
...}`` dict, the same "call the raw check directly" idiom that module
already establishes for exactly this reason: a full schema-valid document
would exercise far more machinery than this one function's own branches.
"""

from __future__ import annotations

from assay.verify import _check_snapshot_policy


def _run(document: dict) -> list[str]:
    failures: list[str] = []
    _check_snapshot_policy(document, failures)
    return failures


# --- presence/absence, keyed on declared_rigor --------------------------------


def test_higher_rigor_with_no_snapshot_policy_key_fails():
    failures = _run({"declared_rigor": ["R0", "R1"]})
    assert any("snapshot_policy is absent" in f for f in failures)


def test_r0_only_with_a_snapshot_policy_key_fails():
    failures = _run(
        {"declared_rigor": ["R0"], "snapshot_policy": {"selection": "repository"}}
    )
    assert any("names no higher-rigor level" in f for f in failures)


def test_no_declared_rigor_at_all_with_a_snapshot_policy_key_fails():
    """`declared_rigor` absent -- `isinstance(None, list)` is False, so
    `higher_rigor_declared` is False, matching the "no lane resolved"
    case the model's own docstring names."""
    failures = _run({"snapshot_policy": {"selection": "repository"}})
    assert any("names no higher-rigor level" in f for f in failures)


def test_r0_only_with_no_snapshot_policy_is_clean():
    assert _run({"declared_rigor": ["R0"]}) == []


def test_higher_rigor_with_a_well_formed_repository_policy_is_clean():
    assert _run(
        {"declared_rigor": ["R0", "R2"], "snapshot_policy": {"selection": "repository"}}
    ) == []


def test_a_non_dict_snapshot_policy_short_circuits_without_a_second_failure():
    """Present-but-malformed-shape: the presence/absence rule above does not
    fire twice, and the function returns before touching `.get("selection")`
    on something that is not a mapping at all."""
    failures = _run({"declared_rigor": ["R0", "R1"], "snapshot_policy": "repository"})
    assert failures == []


# --- selection: the closed vocabulary -----------------------------------------


def test_an_unknown_selection_fails_and_stops():
    failures = _run(
        {"declared_rigor": ["R0", "R1"], "snapshot_policy": {"selection": "bogus"}}
    )
    assert len(failures) == 1
    assert "is not one of" in failures[0]


def test_a_missing_selection_key_fails_the_same_way():
    failures = _run({"declared_rigor": ["R0", "R1"], "snapshot_policy": {}})
    assert len(failures) == 1
    assert "is not one of" in failures[0]


# --- selection == "repository": omissions forbidden ---------------------------


def test_repository_selection_with_omissions_present_fails():
    failures = _run(
        {
            "declared_rigor": ["R0", "R1"],
            "snapshot_policy": {
                "selection": "repository",
                "unsafe_symlink_omissions": ["a"],
            },
        }
    )
    assert any("where it is forbidden" in f for f in failures)


def test_repository_selection_with_omissions_none_is_clean():
    assert _run(
        {"declared_rigor": ["R0", "R1"], "snapshot_policy": {"selection": "repository"}}
    ) == []


# --- selection == "repository-minus-unsafe-symlinks": the omission list ------


_OMIT_SELECTION = "repository-minus-unsafe-symlinks"


def _policy(omissions) -> dict:
    return {
        "declared_rigor": ["R0", "R1"],
        "snapshot_policy": {"selection": _OMIT_SELECTION, "unsafe_symlink_omissions": omissions},
    }


def test_omissions_not_a_list_fails():
    failures = _run(_policy("a/b"))
    assert any("must be a list of" in f for f in failures)


def test_omissions_empty_list_fails():
    failures = _run(_policy([]))
    assert any("must be a list of" in f for f in failures)


def test_omissions_over_64_entries_fails():
    failures = _run(_policy([f"path{i}" for i in range(65)]))
    assert any("must be a list of" in f for f in failures)


def test_omissions_exactly_64_entries_is_a_shape_the_count_check_accepts():
    """The boundary control: 64 is IN range, so this must clear the count
    gate and be judged on its own entries -- proven by using entries that
    are themselves valid and strictly ascending."""
    entries = [f"p{i:02d}" for i in range(64)]
    assert entries == sorted(entries)
    assert _run(_policy(entries)) == []


def test_a_non_string_entry_fails():
    failures = _run(_policy([123]))
    assert any("must be a non-empty string" in f for f in failures)


def test_an_empty_string_entry_fails():
    failures = _run(_policy([""]))
    assert any("must be a non-empty string" in f for f in failures)


def test_a_backslash_entry_fails():
    failures = _run(_policy(["a\\b"]))
    assert any("not a normalized forward-slash path" in f for f in failures)


def test_a_null_byte_entry_fails():
    failures = _run(_policy(["a\x00b"]))
    assert any("not a normalized forward-slash path" in f for f in failures)


def test_a_leading_slash_entry_fails():
    failures = _run(_policy(["/a/b"]))
    assert any("not a normalized forward-slash path" in f for f in failures)


def test_a_trailing_slash_entry_fails():
    failures = _run(_policy(["a/b/"]))
    assert any("not a normalized forward-slash path" in f for f in failures)


def test_an_empty_component_entry_fails():
    failures = _run(_policy(["a//b"]))
    assert any("invalid path component" in f for f in failures)


def test_a_dot_component_entry_fails():
    failures = _run(_policy(["a/./b"]))
    assert any("invalid path component" in f for f in failures)


def test_a_dotdot_component_entry_fails():
    failures = _run(_policy(["a/../b"]))
    assert any("invalid path component" in f for f in failures)


def test_a_dot_git_component_entry_fails():
    failures = _run(_policy(["a/.git/b"]))
    assert any("invalid path component" in f for f in failures)


def test_an_unencodable_entry_fails():
    """A lone UTF-16 surrogate codepoint: a real Python `str` value (the
    shape `json.loads` produces from an unpaired `\\ud800` escape) that
    plain `str.encode("utf-8")` refuses -- the one check in this function
    that is not reachable through any ordinary, already-decoded text."""
    failures = _run(_policy(["a/\ud800"]))
    assert any("cannot be encoded as strict UTF-8" in f for f in failures)


def test_an_oversized_entry_fails():
    failures = _run(_policy(["a" * 4097]))
    assert any("exceeds 4096 UTF-8 bytes" in f for f in failures)


def test_a_4096_byte_entry_is_the_shape_the_size_check_accepts():
    entry = "a" * 4096
    assert len(entry.encode("utf-8")) == 4096
    assert _run(_policy([entry])) == []


def test_omissions_out_of_order_fails():
    failures = _run(_policy(["z", "a"]))
    assert any("strictly ascending" in f for f in failures)


def test_omissions_with_a_duplicate_fails_the_strict_ascending_check():
    """Not merely non-descending: EQUAL adjacent entries fail too, per the
    strict `<` comparison."""
    failures = _run(_policy(["a", "a"]))
    assert any("strictly ascending" in f for f in failures)


def test_a_well_formed_ascending_omission_list_is_clean():
    assert _run(_policy(["a/b", "z"])) == []
