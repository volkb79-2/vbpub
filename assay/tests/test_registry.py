"""O2 (half 2/2) — the explicit adapter registry.

The negative this defends (verbatim): *adding a .py filter, AST import, or
default adapter makes ... the unknown language silently select Python.*
:func:`assay.registry.get_adapter` must refuse a name it was never handed —
never fall back to any adapter, real or fake.
"""

from __future__ import annotations

import pytest
from conftest import FakeAdapter

from assay.errors import AssayError, Outcome, ReasonCode
from assay.registry import get_adapter, new_registry


def test_a_registered_adapter_is_returned_by_its_own_name():
    zzz = FakeAdapter(name="zzz")
    registry = new_registry(zzz)

    assert get_adapter(registry, "zzz") is zzz


def test_an_unregistered_language_is_refused_not_defaulted():
    registry = new_registry(FakeAdapter(name="zzz"))

    with pytest.raises(AssayError) as excinfo:
        get_adapter(registry, "python")

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "python" in str(excinfo.value)


def test_an_empty_registry_refuses_every_language():
    registry = new_registry()

    with pytest.raises(AssayError):
        get_adapter(registry, "zzz")


def test_two_adapters_sharing_a_name_are_refused_at_construction():
    with pytest.raises(ValueError, match="zzz"):
        new_registry(FakeAdapter(name="zzz"), FakeAdapter(name="zzz"))


def test_two_fresh_registries_do_not_share_state():
    """No process-global mutation: registering an adapter in one registry
    must not make it visible through another (AUTHORING.md §3b.B)."""
    only_zzz = new_registry(FakeAdapter(name="zzz"))
    only_yyy = new_registry(FakeAdapter(name="yyy"))

    assert get_adapter(only_zzz, "zzz").name == "zzz"
    with pytest.raises(AssayError):
        get_adapter(only_zzz, "yyy")
    assert get_adapter(only_yyy, "yyy").name == "yyy"
    with pytest.raises(AssayError):
        get_adapter(only_yyy, "zzz")


def test_registering_multiple_distinct_adapters_keeps_each_independently_addressable():
    zzz = FakeAdapter(name="zzz")
    yyy = FakeAdapter(name="yyy", source_globs=("*.yyy",))
    registry = new_registry(zzz, yyy)

    assert get_adapter(registry, "zzz") is zzz
    assert get_adapter(registry, "yyy") is yyy
