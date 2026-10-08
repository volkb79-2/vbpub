"""The A-182 trust boundary around ``assay.verify`` (W10 O5, CD27).

``verify.py`` is the raw verifier: it re-derives every verdict rule on its own and must
never share a producer helper.  W10 put the shared record decorators and guard predicates
in two leaf modules (``assay.records``, ``assay.guards``); those may be used by the producer
side only.  This file pins the boundary from four angles, each as a pure checker over
source text (or a namespace) that is fed the real tree and synthetic offenders:

* (a) the exact set of ``(module, name)`` pairs ``verify.py`` imports;
* (b) the defining module of every name bound in ``vars(assay.verify)``;
* (c) the leaf modules and ``candidate_identity`` import nothing from ``assay``;
* (d) no other source module imports a ``_``-private name from ``verify``.
"""

from __future__ import annotations

import ast
import importlib
import types
from pathlib import Path

from conftest import PROJECT_ROOT

PACKAGE_DIR = PROJECT_ROOT / "src" / "assay"

LEAF_MODULES = frozenset({"assay.guards", "assay.records"})
#: Producer-side verdict helpers the raw verifier must never call (A-182).
PRODUCER_ONLY_VERDICT_NAMES = frozenset({"claim_for", "claim_carries", "_require_policy_iff_attempted"})

_VERDICT_NAMES = (
    "CLAIM_DETAIL_BYTES",
    "CampaignBinding",
    "CanaryAttempt",
    "CanaryResult",
    "Claim",
    "Coverage",
    "DISCARD_REASONS",
    "EXIT_CODES",
    "Evidence",
    "EvidenceDeclaration",
    "EquivalenceLedger",
    "Helper",
    "JudgeProvenance",
    "Judgment",
    "JudgmentR1",
    "JudgmentR2",
    "JudgmentR3",
    "JudgmentR4",
    "JudgmentResolved",
    "MUTATION_BUCKETS",
    "MutantOutcome",
    "Mutation",
    "MutationExecution",
    "MutationProducerTool",
    "MutationWitnessReceipt",
    "MutantEvidence",
    "Outcome",
    "REASON_CODES",
    "ReasonCode",
    "R2BaselineFacts",
    "R2Command",
    "RedFirstResult",
    "ResourceLimitEvidence",
    "SnapshotPolicy",
    "SourcePosition",
    "VERDICT_SCHEMA_VERSION",
    "Verdict",
    "WorktreeIntegrity",
    "is_ingested_operator",
    "operator_language",
    "rollup",
)

#: The committed base: every assay-internal name ``verify.py`` imported before W10.
VERIFY_IMPORT_BASE = frozenset(
    {("assay.candidate_identity", "candidate_id_from_fields"), ("assay.mutation", "judge_mutation")}
    | {
        ("assay.r2_command", name)
        for name in (
            "R2_APPENDED",
            "R2_TRANSFORM_ID",
            "UnrecognizedCoverageOption",
            "transform_argv",
        )
    }
    | {("assay.verdict", name) for name in _VERDICT_NAMES}
)


def module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE_DIR.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def known_modules() -> set[str]:
    return {module_name(p) for p in PACKAGE_DIR.rglob("*.py")}


def resolved_imports(importer: str, source: str, is_package: bool, known: set[str]) -> set[tuple[str, str | None]]:
    """Every ``(module, name)`` an ``Import``/``ImportFrom`` node binds, relative levels resolved.

    ``import a.b`` gives ``("a.b", None)``; ``from a import b`` gives ``("a", "b")``, except that
    when ``a.b`` is itself a known module it gives ``("a.b", None)`` (a module import).
    """
    package = importer if is_package else importer.rpartition(".")[0]
    found: set[tuple[str, str | None]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update((alias.name, None) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")
                up = up[: len(up) - (node.level - 1)]
                base = ".".join(up + ([node.module] if node.module else []))
            for alias in node.names:
                if f"{base}.{alias.name}" in known:
                    found.add((f"{base}.{alias.name}", None))
                else:
                    found.add((base, alias.name))
    return found


def assay_imports(importer: str, source: str, is_package: bool, known: set[str]) -> set[tuple[str, str | None]]:
    return {
        pair
        for pair in resolved_imports(importer, source, is_package, known)
        if pair[0] == "assay" or pair[0].startswith("assay.")
    }


def defining_module(value: object) -> str | None:
    if isinstance(value, types.ModuleType):
        return value.__name__
    return getattr(value, "__module__", None)


def real_resolver(module: str, name: str) -> str | None:
    return defining_module(getattr(importlib.import_module(module), name, None))


def import_violations(pairs: set[tuple[str, str | None]], resolve) -> list[str]:
    """Imports of a verify source that cross the A-182 boundary (leaf module, producer helper, re-export)."""
    bad: list[str] = []
    for module, name in sorted(pairs, key=lambda p: (p[0], p[1] or "")):
        label = f"{module}.{name}" if name else module
        if module in LEAF_MODULES:
            bad.append(f"{label}: imports a shared leaf module")
        elif module == "assay.verdict" and name in PRODUCER_ONLY_VERDICT_NAMES:
            bad.append(f"{label}: imports a producer-only verdict helper")
        elif name is not None and resolve(module, name) in LEAF_MODULES:
            bad.append(f"{label}: re-exports a name defined in {resolve(module, name)}")
    return bad


def producer_helper_uses(source: str) -> list[str]:
    """Uses of a producer-only verdict helper by name or attribute, however it was reached."""
    bad: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in PRODUCER_ONLY_VERDICT_NAMES:
            bad.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in PRODUCER_ONLY_VERDICT_NAMES:
            bad.append(f"line {node.lineno}: .{node.attr}")
    return bad


def binding_violations(namespace: dict[str, object], producer_helpers: dict[str, object]) -> list[str]:
    """Names of a namespace whose defining module is a leaf module, or which are a producer-only helper."""
    bad: list[str] = []
    for name, value in sorted(namespace.items()):
        if defining_module(value) in LEAF_MODULES:
            bad.append(f"{name}: defined in {defining_module(value)}")
        for helper, helper_value in producer_helpers.items():
            if value is helper_value:
                bad.append(f"{name}: is verdict.{helper}")
    return bad


def private_verify_imports(importer: str, source: str, is_package: bool, known: set[str]) -> list[str]:
    """``_``-prefixed names of ``assay.verify`` that ``source`` imports or reaches by attribute."""
    if importer == "assay.verify":
        return []
    bad: list[str] = []
    aliases: set[str] = set()
    tree = ast.parse(source)
    package = importer if is_package else importer.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "assay.verify":
                    aliases.add(alias.asname or "assay.verify")
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package.split(".")
                up = up[: len(up) - (node.level - 1)]
                base = ".".join(up + ([node.module] if node.module else []))
            for alias in node.names:
                if base == "assay.verify" and alias.name.startswith("_"):
                    bad.append(f"{importer}: from verify import {alias.name}")
                if f"{base}.{alias.name}" == "assay.verify":
                    aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_") and ast.unparse(node.value) in aliases:
            bad.append(f"{importer}: reaches verify.{node.attr}")
    return bad


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _verify_source() -> str:
    return _source(PACKAGE_DIR / "verify.py")


def _imports_of_verify() -> set[tuple[str, str | None]]:
    return resolved_imports("assay.verify", _verify_source(), False, known_modules())


# --------------------------------------------------------------------------
# (a) the imported pairs
# --------------------------------------------------------------------------


def test_verify_imports_exactly_the_committed_base_of_assay_names():
    pairs = assay_imports("assay.verify", _verify_source(), False, known_modules())
    assert pairs == VERIFY_IMPORT_BASE


def test_verify_imports_neither_guards_nor_records():
    modules = {module for module, _ in assay_imports("assay.verify", _verify_source(), False, known_modules())}
    assert modules.isdisjoint(LEAF_MODULES)
    assert import_violations(_imports_of_verify(), real_resolver) == []


def test_verify_never_names_a_producer_only_verdict_helper():
    assert producer_helper_uses(_verify_source()) == []


def test_the_pair_walk_reports_added_and_missing_names():
    known = known_modules()
    added = "from .guards import is_strict_int\nfrom .mutation import judge_mutation\n"
    assert assay_imports("assay.verify", added, False, known) == {
        ("assay.guards", "is_strict_int"),
        ("assay.mutation", "judge_mutation"),
    }
    assert assay_imports("assay.verify", added, False, known) != VERIFY_IMPORT_BASE
    assert assay_imports("assay.verify", "", False, known) != VERIFY_IMPORT_BASE
    assert assay_imports("assay.verify", "import json\nfrom os import path\n", False, known) == set()
    assert assay_imports("assay.verify", "from . import guards\n", False, known) == {("assay.guards", None)}
    assert assay_imports("assay.verify", "import assay.records\n", False, known) == {("assay.records", None)}


def test_the_import_checker_refuses_a_leaf_module_import_in_verify():
    known = known_modules()
    for source, expected in (
        ("from .guards import is_strict_int\n", "assay.guards.is_strict_int: imports a shared leaf module"),
        ("from .records import record\n", "assay.records.record: imports a shared leaf module"),
        ("import assay.guards\n", "assay.guards: imports a shared leaf module"),
        ("from . import records\n", "assay.records: imports a shared leaf module"),
    ):
        pairs = assay_imports("assay.verify", source, False, known)
        assert import_violations(pairs, real_resolver) == [expected], source


def test_the_import_checker_refuses_a_re_exported_leaf_name_and_a_producer_helper():
    known = known_modules()
    verdict = importlib.import_module("assay.verdict")
    # ``is_strict_int`` is defined in guards and merely re-bound by verdict: only the defining module shows it.
    assert real_resolver("assay.verdict", "is_strict_int") == "assay.guards"
    pairs = assay_imports("assay.verify", "from .verdict import is_strict_int\n", False, known)
    assert import_violations(pairs, real_resolver) == [
        "assay.verdict.is_strict_int: re-exports a name defined in assay.guards"
    ]
    for helper in sorted(PRODUCER_ONLY_VERDICT_NAMES):
        assert hasattr(verdict, helper)
        pairs = assay_imports("assay.verify", f"from .verdict import {helper}\n", False, known)
        assert import_violations(pairs, real_resolver) == [
            f"assay.verdict.{helper}: imports a producer-only verdict helper"
        ]
    assert import_violations(assay_imports("assay.verify", "from .verdict import Claim\n", False, known), real_resolver) == []


def test_the_use_checker_refuses_a_producer_helper_call_however_it_is_reached():
    assert producer_helper_uses("import assay.verdict as verdict\nverdict.claim_for(claims, 'R1')\n") == [
        "line 2: .claim_for"
    ]
    assert producer_helper_uses("from . import verdict\nx = verdict.claim_carries(c, 'p')\n") == [
        "line 2: .claim_carries"
    ]
    assert producer_helper_uses("f = _require_policy_iff_attempted\n") == ["line 1: _require_policy_iff_attempted"]
    assert producer_helper_uses("from .verdict import Claim\nClaim.claimed\n") == []


# --------------------------------------------------------------------------
# (b) the defining module of every bound name
# --------------------------------------------------------------------------


def test_no_name_bound_in_verify_is_defined_in_a_leaf_module_or_is_a_producer_helper():
    verify = importlib.import_module("assay.verify")
    verdict = importlib.import_module("assay.verdict")
    helpers = {name: getattr(verdict, name) for name in sorted(PRODUCER_ONLY_VERDICT_NAMES)}
    namespace = dict(vars(verify))
    assert "verify_document" in namespace
    assert binding_violations(namespace, helpers) == []


def test_the_binding_checker_sees_a_re_export_that_leaves_the_module_set_unchanged():
    verdict = importlib.import_module("assay.verdict")
    guards = importlib.import_module("assay.guards")
    records = importlib.import_module("assay.records")
    helpers = {name: getattr(verdict, name) for name in sorted(PRODUCER_ONLY_VERDICT_NAMES)}
    # a name re-bound through verdict is still a guards function, wherever it is imported from
    assert binding_violations({"is_strict_int": verdict.is_strict_int}, helpers) == [
        "is_strict_int: defined in assay.guards"
    ]
    assert binding_violations({"record": records.record}, helpers) == ["record: defined in assay.records"]
    assert binding_violations({"guards": guards}, helpers) == ["guards: defined in assay.guards"]
    assert binding_violations({"alias": verdict.claim_for}, helpers) == ["alias: is verdict.claim_for"]
    assert binding_violations({"Claim": verdict.Claim, "json": importlib.import_module("json")}, helpers) == []


# --------------------------------------------------------------------------
# (c) the leaves import no assay module
# --------------------------------------------------------------------------

_LEAF_PATHS = ("guards.py", "records.py", "candidate_identity.py")


def test_guards_records_and_candidate_identity_import_no_assay_module():
    known = known_modules()
    for filename in _LEAF_PATHS:
        path = PACKAGE_DIR / filename
        assert path.is_file(), filename
        found = assay_imports(module_name(path), _source(path), False, known)
        assert found == set(), (filename, found)


def test_the_leaf_checker_refuses_every_kind_of_assay_import():
    known = known_modules()
    for source, expected in (
        ("from .verdict import Claim\n", {("assay.verdict", "Claim")}),
        ("import assay\n", {("assay", None)}),
        ("import assay.guards\n", {("assay.guards", None)}),
        ("from assay import guards\n", {("assay.guards", None)}),
        ("from . import records\n", {("assay.records", None)}),
        ("def f():\n    from .errors import AssayError\n", {("assay.errors", "AssayError")}),
    ):
        assert assay_imports("assay.candidate_identity", source, False, known) == expected, source
    assert assay_imports("assay.candidate_identity", "import hashlib\nfrom __future__ import annotations\n", False, known) == set()


# --------------------------------------------------------------------------
# (d) no private verify name leaves verify
# --------------------------------------------------------------------------


def test_no_source_module_but_verify_imports_a_private_name_from_verify():
    known = known_modules()
    offenders: list[str] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        importer = module_name(path)
        offenders += private_verify_imports(importer, _source(path), path.name == "__init__.py", known)
    assert offenders == []


def test_the_private_import_checker_refuses_each_way_of_reaching_a_verify_private():
    known = known_modules()
    cases = (
        ("from .verify import _raw_claim\n", ["assay.reuse: from verify import _raw_claim"]),
        ("from assay.verify import _raw_claim as raw\n", ["assay.reuse: from verify import _raw_claim"]),
        ("from . import verify\nverify._raw_claim(1)\n", ["assay.reuse: reaches verify._raw_claim"]),
        ("import assay.verify as v\nv._raw_claim(1)\n", ["assay.reuse: reaches verify._raw_claim"]),
        ("import assay.verify\nassay.verify._raw_claim(1)\n", ["assay.reuse: reaches verify._raw_claim"]),
    )
    for source, expected in cases:
        assert private_verify_imports("assay.reuse", source, False, known) == expected, source
    for source in (
        "from .verify import verify_document\n",
        "from . import verify\nverify.verify_document(1)\n",
        "from .verdict import _private\n",
    ):
        assert private_verify_imports("assay.reuse", source, False, known) == [], source
    assert private_verify_imports("assay.verify", "from .verify import _x\n", False, known) == []
