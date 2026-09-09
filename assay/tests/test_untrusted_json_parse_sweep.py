"""B072/B074's sweep, as a guard that DERIVES its own subject.

``json.loads`` is CPython's recursive-descent parser: on a deeply nested but
well-formed document it raises ``RecursionError`` at the real C-stack
boundary — a ``RuntimeError`` subclass, **not** a ``ValueError``. A parser
guarding only ``json.JSONDecodeError`` (or only ``(JSONDecodeError,
ValueError)``) therefore crashes the whole process instead of producing the
judged refusal it exists to produce. That gap has now been found and fixed
four separate times: ``adjudication.py`` (`f0126b35`), ``attestation.py``
(B072), ``verify.py`` (B074), and the two coverage parsers plus the Go
statement-position oracle (B074's second sweep).

**Why this module exists, and why it does not hard-code anything.** The
first version of this guard was a hard-coded three-element tuple of file
paths, checked by matching an exact ``except (...)`` string. It was unfit in
two independent ways, and a reviewer found both:

1. **It could not see a site nobody had listed.** The sweep that produced
   that tuple used ``grep ... src/assay/*.py``, a glob that never descends
   into ``adapters/``, ``coverage_parsers/`` or ``mutation_parsers/``. It
   reported 7 sites; there are 11. Two of the four it missed were genuinely
   untrusted and reproducibly crashed.
2. **It could not see a guard written in a different ORDER.**
   ``mutation_parsers/mutation_report_json.py`` already carried the same
   three names as ``(json.JSONDecodeError, RecursionError, ValueError)`` —
   correct, and invisible to an exact-string match. A "fourth variant"
   already existed, undetected, by the very test whose docstring claimed one
   could not appear unnoticed.

So this module derives the site list from the source itself, by walking the
AST of every module under ``src/assay`` — the same population the sweep is
about, found the same way every time it runs — and it asks whether the
enclosing ``try`` names ``RecursionError``, not how that clause is spelled.
A new module, a new parser, or a re-ordered tuple is all handled without
anyone remembering to update a list.

Every site is then either **guarded** or in :data:`TRUSTED_SITES`, which is
an explicit, reasoned allowlist rather than a silence. The bar for entering
it is B074's own ``provenance.py`` reasoning: the bytes are produced by
assay or by its own installation, not by a consumer's project or an external
tool, AND a crash there would mean a broken build rather than a bad
artifact.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import pytest

from conftest import PROJECT_ROOT

SOURCE_ROOT = PROJECT_ROOT / "src" / "assay"


@dataclass(frozen=True)
class ParseSite:
    """One ``json.loads``/``json.load`` call in assay's own source."""

    module: str  # POSIX-relative to src/assay, e.g. "coverage_parsers/parse.py"
    function: str  # enclosing function name, or "<module>"
    lineno: int
    guarded: bool  # an enclosing `try` names RecursionError

    @property
    def identity(self) -> tuple[str, str]:
        """Line numbers drift with every edit; this does not."""
        return (self.module, self.function)


#: Sites deliberately NOT required to catch ``RecursionError``, each with the
#: reason it is trusted. Keyed by ``(module, enclosing function)`` — never by
#: line number, which drifts on any edit above it (a real defect the reviewer
#: caught in B074's own write-up).
#:
#: The bar is B074's ``provenance.py`` reasoning, applied uniformly: the bytes
#: come from assay itself or from its own installation, never from a
#: consumer's project or an external tool, and a failure means a broken build
#: rather than a bad artifact. Anything a TARGET PROJECT or an external
#: toolchain produced is untrusted, full stop, however well-behaved that
#: producer usually is.
TRUSTED_SITES: dict[tuple[str, str], str] = {
    ("verdict.py", "load_schema"): (
        "assay's OWN shipped package resource, read through importlib "
        "resources from the installed distribution. If this will not parse, "
        "the build is broken -- there is no consumer artifact involved and no "
        "refusal that would mean anything."
    ),
    ("provenance.py", "_installed_wheel_digest"): (
        "the installed distribution's own pip-written `direct_url.json` "
        "metadata -- assay's own build artifact, not a consumer's document. "
        "The enclosing function is best-effort and already returns None on "
        "every fault (OSError, ValueError, non-dict, missing members), so a "
        "RecursionError here would mean a broken install rather than a bad "
        "artifact. (B074 affirmed this judgment rather than reversing it.)"
    ),
    ("mutation.py", "_load_validated_state_record"): (
        "assay's OWN mutation-state record, written by assay itself earlier "
        "in the same run (or an earlier shard of it) through "
        "`_write_mutation_state_record`, and read back bounded by "
        "MUTATION_STATE_RECORD_LIMIT. Not consumer input: a malformed one "
        "already raises MutationStateError by design, and its own depth is "
        "assay's to control."
    ),
}


def _collect_sites() -> list[ParseSite]:
    """Every ``json.loads``/``json.load`` call under ``src/assay``, with
    whether an enclosing ``try`` catches ``RecursionError``.

    Walks the AST rather than grepping text so that a call split across lines,
    an unusual whitespace style, or a comment mentioning ``json.loads`` cannot
    change the answer. `**/*.py` -- the recursive glob -- is the whole point:
    the original sweep's `src/assay/*.py` is exactly what hid four sites.
    """
    sites: list[ParseSite] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, module: str) -> None:
            self.module = module
            self.functions: list[str] = []
            self.handlers: list[list[ast.excepthandler]] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Try(self, node: ast.Try) -> None:
            # Only the `try` BODY is protected by these handlers; the `except`
            # and `else`/`finally` arms are not, so they are visited outside.
            self.handlers.append(list(node.handlers))
            for statement in node.body:
                self.visit(statement)
            self.handlers.pop()
            for arm in (*node.handlers, *node.orelse, *node.finalbody):
                self.visit(arm)

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"loads", "load"}
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
            ):
                sites.append(
                    ParseSite(
                        module=self.module,
                        function=".".join(self.functions) or "<module>",
                        lineno=node.lineno,
                        guarded=self._catches_recursion_error(),
                    )
                )
            self.generic_visit(node)

        def _catches_recursion_error(self) -> bool:
            for handlers in self.handlers:
                for handler in handlers:
                    if handler.type is None:
                        continue  # a bare `except:` -- not what this asks for
                    names = (
                        handler.type.elts
                        if isinstance(handler.type, ast.Tuple)
                        else [handler.type]
                    )
                    for name in names:
                        # Order-independent by construction: this asks whether
                        # the NAME appears anywhere in the clause, never how
                        # the clause is spelled.
                        if isinstance(name, ast.Name) and name.id == "RecursionError":
                            return True
            return False

    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        module = path.relative_to(SOURCE_ROOT).as_posix()
        Visitor(module).visit(ast.parse(path.read_text(encoding="utf-8")))
    return sites


def test_the_sweep_finds_a_plausible_population():
    """Guards the guard: if the walk silently found nothing -- a moved source
    root, a glob typo, a renamed package -- every assertion below would pass
    vacuously, which is precisely how the first version of this test managed
    to be green while missing four sites."""
    sites = _collect_sites()
    assert len(sites) >= 10, f"implausibly few json parse sites found: {sites}"
    modules = {site.module for site in sites}
    # At least one site from each subpackage the ORIGINAL sweep's
    # `src/assay/*.py` glob could not see. Named explicitly, because "the
    # glob descends" is the single fact that sweep got wrong.
    assert any(m.startswith("adapters/") for m in modules), modules
    assert any(m.startswith("coverage_parsers/") for m in modules), modules
    assert any(m.startswith("mutation_parsers/") for m in modules), modules


def test_every_untrusted_json_parse_site_catches_RecursionError():
    """The sweep itself. Every site is guarded, or explicitly trusted with a
    written reason -- there is no third disposition, and no way to be silent."""
    unguarded = [
        site
        for site in _collect_sites()
        if not site.guarded and site.identity not in TRUSTED_SITES
    ]
    assert not unguarded, (
        "these json parse sites neither catch RecursionError nor appear in "
        "TRUSTED_SITES with a stated reason:\n"
        + "\n".join(
            f"  src/assay/{s.module}:{s.lineno} (in {s.function})" for s in unguarded
        )
        + "\n\nEither widen the except clause (B072/B074's one-line shape) or "
        "add the site to TRUSTED_SITES with the reason it is assay's own "
        "bytes rather than a consumer's or an external tool's."
    )


def test_no_trusted_entry_is_stale():
    """An allowlist that outlives its site is a lie the next sweep inherits.
    If a trusted site is deleted, renamed, or has since been guarded anyway,
    its entry must go."""
    sites = {site.identity: site for site in _collect_sites()}
    for identity in TRUSTED_SITES:
        assert identity in sites, (
            f"TRUSTED_SITES names {identity}, which no longer parses JSON -- "
            f"remove the entry"
        )
        assert not sites[identity].guarded, (
            f"TRUSTED_SITES names {identity}, but it now catches "
            f"RecursionError anyway -- remove the exemption rather than "
            f"leaving a claim that it needs one"
        )


@pytest.mark.parametrize(
    "identity",
    [
        ("adjudication.py", "evaluate_provenance"),
        ("attestation.py", "parse_attestation"),
        ("verify.py", "verify_text"),
        ("coverage_parsers/coverage_py_json.py", "parse"),
        ("coverage_parsers/coverage_istanbul_json.py", "parse"),
        ("mutation_parsers/mutation_report_json.py", "parse"),
        ("mutation_parsers/mutation_report_json.py", "sniff"),
        ("adapters/go_stmtpos.py", "_read_document"),
        # (B078) The vitest result report: a document written by the
        # consumer's own test runner, read to decide R0. Squarely a "bytes
        # assay did not write" site, and the derived sweep is what caught it
        # unguarded in the first place.
        ("result_reports/vitest_json.py", "read"),
    ],
)
def test_each_known_untrusted_site_is_still_present_and_guarded(identity):
    """The derived sweep above cannot notice a site that DISAPPEARS -- delete
    a parser and its assertion silently stops applying. These nine are the
    ones known to read bytes assay did not write, so each is pinned by name:
    a rename is fine and shows up here as a clear failure, a silent deletion
    of a guard is not."""
    sites = {site.identity: site for site in _collect_sites()}
    assert identity in sites, (
        f"{identity} no longer parses JSON -- if that is intentional, remove "
        f"it from this list; if it moved, update the identity"
    )
    assert sites[identity].guarded, f"{identity} lost its RecursionError guard"


def test_the_guard_is_order_independent():
    """The specific fault that let a variant hide from the previous version
    of this test: `mutation_report_json.py` spells the same three names as
    `(json.JSONDecodeError, RecursionError, ValueError)`, which an exact
    string match on `(json.JSONDecodeError, ValueError, RecursionError)`
    reports as missing. Proven against the real module rather than a
    synthetic one, so it stays true only while the real spelling differs."""
    source = (SOURCE_ROOT / "mutation_parsers" / "mutation_report_json.py").read_text(
        encoding="utf-8"
    )
    canonical = "except (json.JSONDecodeError, ValueError, RecursionError)"
    assert canonical not in source, (
        "this module used to differ in ORDER from the canonical spelling, "
        "which is what made it the proof that an exact-string guard is "
        "unfit; if it has been normalised, point this test at whichever "
        "site now differs, or delete it and say why in the commit"
    )
    sites = {site.identity: site for site in _collect_sites()}
    assert sites[("mutation_parsers/mutation_report_json.py", "parse")].guarded
