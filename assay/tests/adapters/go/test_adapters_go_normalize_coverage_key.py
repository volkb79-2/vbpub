"""O2 (A-099's own reasoning, applied to Go) --
``GoAdapter.normalize_coverage_key``'s OWN prefix-strip boundary safety.
Never a retest of ``evaluate.py``'s already-proven, adapter-independent
source-root boundary matching (P05's own concern, out of this package's
scope). This module's mutation target lives entirely inside ``go.py``'s
own ``normalize_coverage_key``.

Mirrors ``covergate/main.go``'s own ``stripModulePrefix`` directly: a Go
cover profile names a file by its full IMPORT path
(``"srdm/internal/store/store.go"``), while ``git diff`` names the same
file relative to the repo top (``"internal/store/store.go"``) --
:attr:`~assay.adapters.go.GoAdapter.module_path` names the declared module
segment to strip, mirroring
:attr:`~assay.adapters.python.PythonAdapter.coverage_key_prefix`'s own
boundary-safe design (the fixtures below are the direct Go analogue of
``test_adapters_python_normalize_coverage_key.py``'s own).
"""

from __future__ import annotations

from assay.adapters.go import GoAdapter


def test_no_module_path_configured_returns_the_key_unchanged():
    adapter = GoAdapter()
    assert adapter.normalize_coverage_key("internal/store/store.go") == (
        "internal/store/store.go"
    )


def test_a_configured_module_path_is_stripped_at_the_path_segment_boundary():
    adapter = GoAdapter(module_path="srdm")
    assert (
        adapter.normalize_coverage_key("srdm/internal/store/store.go")
        == "internal/store/store.go"
    )


def test_a_sibling_module_path_is_left_unchanged_not_mis_stripped():
    """The real hazard ``stripModulePrefix``'s own boundary-safe
    ``TrimPrefix(key, module+"/")`` guards against: ``srdm_legacy`` is a
    DIFFERENT, unrelated module that merely shares the ``srdm`` prefix's
    characters. A naive ``key.removeprefix("srdm")`` would produce the
    nonsensical ``"_legacy/internal/x.go"``."""
    adapter = GoAdapter(module_path="srdm")
    assert (
        adapter.normalize_coverage_key("srdm_legacy/internal/x.go")
        == "srdm_legacy/internal/x.go"
    )


def test_a_key_equal_to_the_bare_module_path_with_no_trailing_segment_is_unchanged():
    adapter = GoAdapter(module_path="srdm")
    assert adapter.normalize_coverage_key("srdm") == "srdm"


def test_a_key_with_an_unrelated_module_path_is_left_unchanged():
    adapter = GoAdapter(module_path="srdm")
    assert (
        adapter.normalize_coverage_key("otherpkg/internal/x.go")
        == "otherpkg/internal/x.go"
    )
