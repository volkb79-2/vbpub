"""P34/W6 — the SQL adapter registers cleanly through P05's own,
unmodified ``registry.py`` (this package touches no line of it, mirroring
``test_adapters_python_registration.py``'s own O3 proof one language over),
and only at R2: the registry entry ``cli._built_in_registry`` builds names
``rigor=frozenset({"R2"})`` for :class:`~assay.adapters.sql.SqlAdapter`,
which is what makes its five raising methods (``has_executable_code``,
``normalize_coverage_key``, ``statement_spans``, ``inject_import_break``,
``inject_uncovered_line``) provably unreachable through the CLI -- R1 and
R3 never resolve an adapter for a language this registry declares R2-only.
"""

from __future__ import annotations

import pytest

from assay.adapters.sql import SqlAdapter
from assay.errors import Outcome, ReasonCode
from assay.registry import RegistryEntry, get_adapter, new_registry


def test_the_sql_adapter_registers_under_its_own_declared_name():
    adapter = SqlAdapter()
    registry = new_registry(RegistryEntry(adapter=adapter, rigor=frozenset({"R2"})))

    assert get_adapter(registry, "sql", "R2") is adapter
    assert adapter.name == "sql"


def test_the_sql_adapter_declares_the_expected_protocol_surface():
    """Route (i) (§4.1): a stdlib-only lexer, never an external tool --
    ``external_tools`` is the genuine empty tuple, and
    ``requires_span_attribution`` is False because R1 (the only rigor level
    that reads it) never resolves this adapter at all."""
    adapter = SqlAdapter()

    assert adapter.source_globs == ("*.sql",)
    assert adapter.external_tools == ()
    assert adapter.requires_span_attribution is False


def test_a_registry_built_sql_adapter_generates_mutants_identically_to_a_direct_one():
    """The mirror of the Python file's own O2 proof: no hidden state or
    behaviour differs between an adapter constructed directly and one
    retrieved back out of a fresh ``Registry`` -- driven here with a real
    ``sql:drop-check`` site rather than a coverage profile, since R2 (not
    R1) is the level this registry actually reaches for SQL."""
    registry = new_registry(RegistryEntry(adapter=SqlAdapter(), rigor=frozenset({"R2"})))
    adapter = get_adapter(registry, "sql", "R2")

    source = "CREATE TABLE t (a INT, CONSTRAINT ck CHECK (a > 0));\n"
    sites = adapter.generate_mutation_sites(
        source, {1}, operators=("sql:drop-check",), limit=10
    )

    assert len(sites) == 1
    assert sites[0].operator == "sql:drop-check"
    assert sites[0].replacement == b"CHECK (true)"


# --- R2-only: at R1/R3 this registry entry refuses (§3.7) --------------------


@pytest.mark.parametrize("rigor", ["R1", "R3"])
def test_get_adapter_refuses_sql_outside_its_r2_only_entry(rigor: str):
    """(P34/§3.7) ``judge.language = "sql"`` declaring R1 or R3 is
    ``ERROR``/``BAD_LANE_CONFIG`` at THIS choke point -- the identical
    mechanism that refuses an entirely unregistered language, applied to a
    KNOWN one at a level this build never wired it to (A-139), proving W6's
    ``rigor=frozenset({"R2"})`` is what makes the refusal fire."""
    registry = new_registry(RegistryEntry(adapter=SqlAdapter(), rigor=frozenset({"R2"})))

    with pytest.raises(Exception) as excinfo:
        get_adapter(registry, "sql", rigor)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_get_adapter_accepts_sql_at_r2_the_must_succeed_control():
    """The paired must-succeed control for the R1/R3 refusals above: the
    identical registry, at the ONE level it actually declares, resolves
    cleanly rather than refusing everything regardless of level."""
    adapter = SqlAdapter()
    registry = new_registry(RegistryEntry(adapter=adapter, rigor=frozenset({"R2"})))

    assert get_adapter(registry, "sql", "R2") is adapter
