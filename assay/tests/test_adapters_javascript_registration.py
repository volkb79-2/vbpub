"""B036 — the JavaScript/TypeScript adapter registers cleanly through P05's
own, UNMODIFIED ``registry.py`` (this package touches no line of it, mirroring
``test_adapters_go_registration.py``'s and ``test_adapters_sql_registration.py``'s
own O3 proofs one language over), declares the protocol surface this package
chose transparently, and reaches **R1 only**.

The R1-only entry is the whole reason ``judge.language = "javascript"`` at R2
is refused: :meth:`~assay.adapters.javascript.JavaScriptAdapter.
generate_mutation_sites` is unconditionally ``"UNSUPPORTED"`` because whether
JS/TS mutation should be native or should ingest an external producer's
evidence is the ruling **B037** exists to force, and a registry entry naming a
level this build cannot reach is exactly the declared-capability lie this
project exists to remove (DESIGN-GUIDE §7).
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from assay.adapters.go import GoAdapter
from assay.adapters.javascript import JavaScriptAdapter
from assay.adapters.python import PythonAdapter
from assay.adapters.sql import SqlAdapter
from assay.cli import _built_in_registry
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import AssayError, Outcome, ReasonCode
from assay.evaluate import evaluate_coverage
from assay.registry import RegistryEntry, get_adapter, new_registry


def test_the_javascript_adapter_registers_under_its_own_declared_name():
    adapter = JavaScriptAdapter()
    registry = new_registry(RegistryEntry(adapter=adapter, rigor=frozenset({"R1"})))

    assert get_adapter(registry, "javascript", "R1") is adapter
    assert adapter.name == "javascript"


def test_the_javascript_adapter_declares_the_expected_protocol_surface():
    """Transparency for every union decision this package made, as literal
    values rather than prose: one adapter for four extensions (A-340), three
    generated directories excluded each with a cited source, no span
    attribution (A-342, measured against real Vitest output for BOTH
    providers), and no external tool -- this module never shells out."""
    adapter = JavaScriptAdapter()

    assert adapter.source_globs == ("*.js", "*.jsx", "*.ts", "*.tsx")
    assert adapter.excluded_dir_names == frozenset(
        {"node_modules", "dist", "coverage"}
    )
    assert adapter.requires_span_attribution is False
    assert adapter.external_tools == ()


def test_normalize_coverage_key_is_a_no_op_for_every_key_shape():
    """A-341's own claim, pinned directly (round-1 review, Minor: an INERT
    wrong strip -- one whose prefix never matches a real key -- survived the
    whole suite, because every other test only ever exercises keys that a
    correct implementation leaves alone anyway).

    Istanbul's absolute keys are reconciled by the CORE
    (:func:`assay.evaluate._to_repo_relative_key`); there is no
    language-specific prefix for this adapter to strip, so the method must
    return EVERY key unchanged -- absolute, relative, Windows-shaped, and the
    empty string alike."""
    adapter = JavaScriptAdapter()

    for key in (
        "/workspaces/repo/src/App.tsx",
        "/build/agent/7/applications/ui/src/App.tsx",
        "src/App.tsx",
        "./src/App.tsx",
        "C:\\Users\\dev\\project\\src\\App.tsx",
        "node_modules/pkg/index.js",
        "",
    ):
        assert adapter.normalize_coverage_key(key) == key


def test_the_javascript_adapters_statement_spans_returns_none_unconditionally():
    """``evaluate.py`` never calls this for an adapter declaring
    ``requires_span_attribution=False``, so its body is trivial by design
    (A-101) -- proven directly rather than by the absence of a call site."""
    adapter = JavaScriptAdapter()

    assert adapter.statement_spans("export const a = 1\n") is None
    assert adapter.statement_spans("") is None


def test_generate_mutation_sites_is_unconditionally_unsupported():
    """Go's own precedent (A-183): an ADAPTER with no mutation engine returns
    the whole-adapter marker, never an empty tuple (which would assert a
    supported analysis ran and found nothing mutable) and never a
    ``MutationDiscoveryError`` (which would assert an engine failed). No
    argument is consulted, so this holds for any input -- including source
    that is full of obviously-mutable comparisons."""
    adapter = JavaScriptAdapter()
    mutable = "export const ok = (a: number, b: number) => a > b && a !== 0\n"

    assert (
        adapter.generate_mutation_sites(
            mutable, {1}, operators=("python:compare-swap",), limit=10
        )
        == "UNSUPPORTED"
    )
    assert (
        adapter.generate_mutation_sites("", set(), operators=(), limit=0)
        == "UNSUPPORTED"
    )


def test_a_registry_built_javascript_adapter_evaluates_coverage_identically():
    """No hidden state or behaviour differs between an adapter constructed
    directly and one retrieved back out of a fresh ``Registry`` -- the
    registry is a pure lookup (P05's own O2), proven by actually driving
    ``evaluate_coverage`` with the registry-obtained instance."""
    registry = new_registry(
        RegistryEntry(adapter=JavaScriptAdapter(), rigor=frozenset({"R1"}))
    )
    adapter = get_adapter(registry, "javascript", "R1")

    added = AddedLines(by_file=MappingProxyType({"src/app.ts": frozenset({1})}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                "src/app.ts": FileCoverage(
                    executed=frozenset({1}), missing=frozenset(), excluded=None
                )
            }
        )
    )

    def read_source_text(path: str) -> str:
        raise AssertionError("this file has a coverage entry")

    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=Path("/repo"),
        project_root=Path("/repo"),
        source_root_paths=(Path("/repo"),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )

    assert result.outcome is Outcome.PASS
    assert result.covered == 1


def test_all_four_adapters_coexist_in_one_registry_each_independently_addressable():
    """The mechanical proof of additivity, now at four languages: registering
    every real adapter together, none shadows or is required by another, and
    the registry (and every module it depends on) needed zero changes to
    accommodate the fourth.

    Round-1 review nitpick: an earlier version of this test said "four" and
    registered three -- `GoAdapter` was missing, so the claim in its own name
    was not the claim it made. Go is registered here at R1 purely to prove
    coexistence; `cli._built_in_registry` still reaches it at no rigor level
    at all, which is a different fact and is pinned separately below."""
    javascript = JavaScriptAdapter()
    python = PythonAdapter()
    sql = SqlAdapter()
    go = GoAdapter()
    registry = new_registry(
        RegistryEntry(adapter=javascript, rigor=frozenset({"R1"})),
        RegistryEntry(adapter=python, rigor=frozenset({"R1"})),
        RegistryEntry(adapter=sql, rigor=frozenset({"R2"})),
        RegistryEntry(adapter=go, rigor=frozenset({"R1"})),
    )

    assert get_adapter(registry, "javascript", "R1") is javascript
    assert get_adapter(registry, "python", "R1") is python
    assert get_adapter(registry, "sql", "R2") is sql
    assert get_adapter(registry, "go", "R1") is go
    assert len(registry.entries) == 4


# --- R1-only: at R2/R3 this build's own registry refuses --------------------


def test_the_built_in_registry_refuses_javascript_at_r3(rigor: str = "R3"):
    """Driven through ``cli._built_in_registry()`` itself, not a hand-built
    one: this is THIS BUILD's capability declaration, and the refusal is the
    same ``ERROR``/``BAD_LANE_CONFIG`` an entirely unregistered language gets
    (A-139).

    **B046 removed R2 from this refusal, deliberately.** R2 is now registered
    -- through the INGESTED path only: the lane's own argv runs StrykerJS in
    the private snapshot and assay judges the report, so
    ``generate_mutation_sites`` is still unconditionally ``"UNSUPPORTED"`` and
    a NATIVE javascript R2 lane is still not constructible (it would have to
    declare non-empty ``operators``, and no ``javascript`` operator catalogue
    exists). See ``test_the_registry_does_not_open_the_NATIVE_r2_path`` below,
    which is what keeps that guarantee tested rather than assumed.

    R3 stays refused: the two canary injection methods are real
    implementations, but a producer path is a separate claim from a method
    existing (DESIGN-GUIDE §7), and no real canary PAIR has ever run.
    """
    with pytest.raises(AssayError) as excinfo:
        get_adapter(_built_in_registry(), "javascript", rigor)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "javascript" in str(excinfo.value)
    assert "['R1', 'R2']" in str(excinfo.value)


@pytest.mark.parametrize("rigor", ["R1", "R2"])
def test_the_built_in_registry_resolves_javascript_at_r1_and_r2(rigor: str):
    """The paired must-succeed control: the identical registry, at the levels
    it declares, resolves cleanly rather than refusing everything."""
    adapter = get_adapter(_built_in_registry(), "javascript", rigor)

    assert isinstance(adapter, JavaScriptAdapter)
    assert adapter.name == "javascript"


def test_the_registry_does_not_open_the_NATIVE_r2_path(project):
    """B046's load-bearing negative: registering ``javascript`` at R2 admits
    the INGESTED path and nothing else.

    Two independent things still refuse a native javascript R2 lane, and
    neither is this registry. First, the config loader: a native R2 lane must
    declare non-empty ``judge.mutation.operators``, and
    ``MUTATION_OPERATORS_BY_LANGUAGE`` has no ``javascript`` entry, so every
    operator such a lane could spell is FOREIGN to it. Second, the adapter
    itself: ``generate_mutation_sites`` is unconditionally ``"UNSUPPORTED"``,
    which ``run_mutation`` renders as
    ``INCONCLUSIVE``/``MUTATION_UNSUPPORTED`` -- a stated absence of
    capability, never a PASS.
    """
    from assay.config import load_lane_file
    from assay.errors import LaneConfigError
    from assay.vocabulary import MUTATION_OPERATORS_BY_LANGUAGE

    assert "javascript" not in MUTATION_OPERATORS_BY_LANGUAGE
    assert JavaScriptAdapter().generate_mutation_sites(
        "export const x = 1\n", {1}, operators=(), limit=10
    ) == "UNSUPPORTED"

    project.dir("src")
    native_js_r2 = """\
schema_version = 2

[lanes.ui]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["npx", "stryker", "run"]
env = {}
env_passthrough = ["PATH"]
budget = "10m"
allow_argv_append = false

[lanes.ui.isolation]
snapshot_selection = "repository"

[lanes.ui.judge]
language = "javascript"
source_roots = ["src"]
base = "main"

[lanes.ui.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:compare-swap"]
"""
    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(project.write(native_js_r2))
    assert "javascript" in str(excinfo.value)


def test_the_built_in_registry_still_refuses_an_unregistered_language():
    """B036 registered a language; it did not open the registry up. An
    unknown name is refused by the same choke point, with the registered
    names listed -- and ``javascript`` is now among them, which is what makes
    the R2 refusal above a DIFFERENT fact from this one."""
    with pytest.raises(AssayError) as excinfo:
        get_adapter(_built_in_registry(), "typescript", "R1")

    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not a language this registry knows" in str(excinfo.value)
    assert "javascript" in str(excinfo.value)


def test_the_built_in_registry_names_exactly_the_languages_this_build_reaches():
    """A literal expectation, so adding or dropping a language cannot happen
    silently -- the registry IS this build's capability declaration."""
    entries = _built_in_registry().entries

    assert {name: sorted(entry.rigor) for name, entry in entries.items()} == {
        "python": ["R1", "R2", "R3"],
        "sql": ["R2"],
        # B046: javascript gained R2, through the INGESTED path only.
        "javascript": ["R1", "R2"],
    }
