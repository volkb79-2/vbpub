"""O2 (half 2/2) — the explicit adapter registry.

The negative this defends (verbatim): *adding a .py filter, AST import, or
default adapter makes ... the unknown language silently select Python.*
:func:`assay.registry.get_adapter` must refuse a name it was never handed —
never fall back to any adapter, real or fake. P17 adds a second negative: a
*known* language whose registered entry does not name the requested rigor
level must ALSO be refused — an adapter shipping (P08's Go) is not the same
claim as this build having wired a producer path to it at that level
(DESIGN-GUIDE §7).
"""

from __future__ import annotations

import pytest
from conftest import FakeAdapter

from assay.errors import AssayError, Outcome, ReasonCode
from assay.registry import RegistryEntry, get_adapter, new_registry


def _entry(name: str, *, rigor: frozenset[str] = frozenset({"R1"})) -> RegistryEntry:
    return RegistryEntry(adapter=FakeAdapter(name=name), rigor=rigor)


def test_a_registered_adapter_is_returned_by_its_own_name_at_a_reachable_rigor():
    zzz = FakeAdapter(name="zzz")
    registry = new_registry(RegistryEntry(adapter=zzz, rigor=frozenset({"R1"})))

    assert get_adapter(registry, "zzz", "R1") is zzz


def test_an_unregistered_language_is_refused_not_defaulted():
    registry = new_registry(_entry("zzz"))

    with pytest.raises(AssayError) as excinfo:
        get_adapter(registry, "python", "R1")

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "python" in str(excinfo.value)


def test_a_known_language_at_an_unreachable_rigor_is_refused():
    registry = new_registry(_entry("python", rigor=frozenset({"R1"})))

    with pytest.raises(AssayError) as excinfo:
        get_adapter(registry, "python", "R2")

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "R2" in str(excinfo.value)
    assert "R1" in str(excinfo.value)


def test_an_empty_registry_refuses_every_language():
    registry = new_registry()

    with pytest.raises(AssayError):
        get_adapter(registry, "zzz", "R1")


def test_two_adapters_sharing_a_name_are_refused_at_construction():
    with pytest.raises(ValueError, match="zzz"):
        new_registry(_entry("zzz"), _entry("zzz"))


def test_an_entry_with_no_reachable_rigor_is_refused_at_construction():
    with pytest.raises(ValueError, match="no reachable rigor"):
        RegistryEntry(adapter=FakeAdapter(name="zzz"), rigor=frozenset())


def test_an_entry_naming_r0_is_refused_at_construction():
    with pytest.raises(ValueError, match="R0"):
        RegistryEntry(adapter=FakeAdapter(name="zzz"), rigor=frozenset({"R0"}))


def test_two_fresh_registries_do_not_share_state():
    """No process-global mutation: registering an adapter in one registry
    must not make it visible through another (AUTHORING.md §3b.B)."""
    only_zzz = new_registry(_entry("zzz"))
    only_yyy = new_registry(_entry("yyy"))

    assert get_adapter(only_zzz, "zzz", "R1").name == "zzz"
    with pytest.raises(AssayError):
        get_adapter(only_zzz, "yyy", "R1")
    assert get_adapter(only_yyy, "yyy", "R1").name == "yyy"
    with pytest.raises(AssayError):
        get_adapter(only_yyy, "zzz", "R1")


def test_registering_multiple_distinct_adapters_keeps_each_independently_addressable():
    zzz = FakeAdapter(name="zzz")
    yyy = FakeAdapter(name="yyy", source_globs=("*.yyy",))
    registry = new_registry(
        RegistryEntry(adapter=zzz, rigor=frozenset({"R1"})),
        RegistryEntry(adapter=yyy, rigor=frozenset({"R1", "R2"})),
    )

    assert get_adapter(registry, "zzz", "R1") is zzz
    assert get_adapter(registry, "yyy", "R1") is yyy
    assert get_adapter(registry, "yyy", "R2") is yyy
    with pytest.raises(AssayError):
        get_adapter(registry, "zzz", "R2")
