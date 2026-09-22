"""Property coverage for CMRU's workspace naming and scope grammar."""
from hypothesis import given, settings, strategies as st

from cmru import transaction


@settings(max_examples=64, deadline=None, derandomize=True, database=None)
@given(scope=st.one_of(st.none(), st.text(min_size=0, max_size=40)))
def test_scope_sanitization_is_nonempty_and_branch_safe(scope):
    value = transaction._sanitize_scope(scope)
    assert value
    assert value == value.lower()
    assert all(char in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in value)


@settings(max_examples=64, deadline=None, derandomize=True, database=None)
@given(purpose=st.sampled_from(["release", "build"]), scope=st.text(min_size=0, max_size=20))
def test_transaction_branch_contains_one_visible_base36_identity(purpose, scope):
    branch = transaction._new_transaction_branch(
        purpose, scope, identity="a1b2c3"
    )
    assert branch.startswith(f"cmru-{purpose}-")
    assert "a1b2c3" in branch
    assert transaction.workspace_purpose(branch) == purpose
    assert transaction._worktree_dirname(branch) == branch
