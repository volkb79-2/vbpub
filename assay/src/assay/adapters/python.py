"""The Python :class:`~assay.adapters.base.LanguageAdapter` — the first REAL
adapter, proving the language-free core (P05) is faithful to real Python
semantics (DESIGN-GUIDE §11, A-097/A-098/A-099).

**Scope, precisely (A-098).** Real ``coverage.py`` has a well-documented gap:
a trace hit is attributed to a STATEMENT's own first physical line only, so a
multi-line statement's interior lines (a multi-line dict literal, a call
spanning several lines, ...) never appear in ``executed_lines`` NOR
``missing_lines`` at all. Recovering those interior lines needs an AST
statement-span walk correlated back against the coverage artifact — that is
P07's ``statement_spans`` addition (A-084), reserved because this package has
no scope to touch ``adapters/base.py`` (A-097) and the protocol this package
implements against has no such method. What IS this package's job — and what
every construct below resolves from a SINGLE reported line, never a span —
is: decorators, async/compound-statement headers, docstrings, comments, and
pragma tokens. ``requires_span_attribution = True`` below is this adapter's
own honest declaration that real Python needs P07's future extension; P05's
own evaluation never reads that flag, it exists purely for P07 to discover
which adapters to extend without inspecting behaviour.

**The union, and where each reference actually diverges (Work item 2).**
Three sibling gates were read in full for this package: dstdns
(``scripts/coverage_gate.py``), topos (``tools/coverage_gate.py``), nyxloom
(``src/nyxloom/coverage_gate.py``).

* ``is_test_path`` — dstdns is the SOLE holder
  (``_TEST_FILE_RE``/``_is_test_path``); topos and nyxloom have no
  equivalent at all (confirmed by grep — neither file mentions a test-path
  concept anywhere). The union is therefore dstdns's rule verbatim, adopted
  below as :data:`_TEST_FILE_RE`.
* Pragma/exclusion handling — all three agree line-for-line: a changed line
  the coverage artifact reports in ``excluded_lines`` fails the gate unless
  ``--allow-excluded``/``allow_excluded`` was passed. This is entirely
  ``evaluate.py``'s rule 3 (already proven in P05) plus the coverage-format
  parser's own ``excluded`` field (already proven in P03) — nothing for this
  adapter to compute; the pragma TOKEN itself never needs adapter-side
  recognition.
* ``statement_spans``/decorator+match-case recovery — only dstdns has this,
  and A-098 reserves it for P07.
* Directory-name exclusion — NONE of the three references excludes any
  directory by bare name (confirmed by grep across all three for
  ``__pycache__``/``.venv``/``vendor``/``node_modules``/etc. — zero hits).
  Per DESIGN-GUIDE §5's defaults doctrine ("never invent" a fact no source
  actually supplies), :data:`PythonAdapter.excluded_dir_names` is the
  genuine union of three empty sets: ``frozenset()``. In practice this
  costs nothing — ``__pycache__``/``.venv`` are gitignored in every cited
  project, so ``git diff`` can never surface a changed file under them
  regardless of what this set contains.

**``has_executable_code`` — the one place this adapter classifies file
CONTENT.** Consulted by ``evaluate.py`` ONLY for a considered, adapter-
recognised, non-test file with NO entry at all in the coverage artifact
(srdm's NoCode distinction, restated in ``adapters/base.py``'s own
docstring). Decided with ``ast.parse`` per the P05 brief's own guidance:
a real ``SyntaxError``/``ValueError`` (the latter for e.g. embedded NUL
bytes, the same pair dstdns's own ``statement_spans`` catches) is treated as
``True`` — conservatively assume there IS code rather than silently excusing
an unparseable file, srdm's own asymmetry lesson (a wrong ``False`` is a
silent excuse; a wrong ``True`` is at worst a false failure, and a file this
adapter cannot even parse is never something to wave through as
"expectedly code-free"). A bare docstring expression statement — module,
class, or (in principle) a stray string literal used as an inline comment —
is walked past exactly as ``coverage.py`` itself never traces one: a
docstring is a real ``ast.Expr`` node with a real line, but it is documented
here as belonging to the SAME "not executable" category as a ``#`` comment
or a blank line, matching :mod:`assay.evaluate`'s own rule 4. Only TOP-LEVEL
module statements are inspected (``tree.body``, not a full ``ast.walk``):
a module whose top level is empty-or-docstring-only cannot have nested
executable code either — nesting requires a top-level statement (a ``def``,
``class``, ``if``, ...) to contain it, and any such top-level statement is
itself no longer a bare docstring and already trips this method to
``True`` without needing to look inside it.

**``normalize_coverage_key`` — the language-specific prefix STRIP (A-099).**
DESIGN-GUIDE §11 draws this split explicitly: the prefix-BOUNDARY
reconciliation (is this path under a declared source root) is universal and
lives in ``evaluate.py``'s ``_is_considered``
(``Path.is_relative_to`` — this package has no scope to touch it and does
not re-test it, A-099's own reading); the language-specific prefix STRIP —
Go's module path, srdm's ``stripModulePrefix`` — is an adapter hook. The
real-world Python analogue: this protocol's own repo-top spelling is fixed
by ``git diff``'s own cwd (DESIGN-GUIDE's path contract), but a coverage
artifact consumed alongside it can come from a WIDER cwd than that repo
top — e.g. a shared CI job ran ``coverage run`` from a monorepo checkout
one level above a project that adopts assay standalone with that project's
own directory as ITS repo top (DESIGN-GUIDE §9's self-hosting pattern; this
very estate's own gate invocations already `cd` into a project directory
before running ``pytest``/``coverage`` — see DESIGN-GUIDE §4's own quoted
``cd {worktree}/ciu && pytest`` example, the mirror-image offset). In that
shape coverage.py's own JSON key still carries the project directory's own
name on the front (``"myapp/src/foo.py"``) while ``git diff``, computed
relative to the narrower repo top, has already dropped it
(``"src/foo.py"``) — the key is the LONGER spelling, the diff is the
SHORTER one, and reconciling them is a strip, never an addition.
:attr:`PythonAdapter.coverage_key_prefix` names that known, declared
directory segment (mirroring Go's own module path, which is likewise a
declared fact from ``go.mod``, never derived from the key string alone).
The strip is boundary-safe: it only fires when *key* starts with
``coverage_key_prefix + "/"`` — an EXACT path-segment match — never a bare
``str.removeprefix``/``str.startswith`` on the prefix text alone, which
would also (wrongly) fire on a sibling directory that merely shares the
prefix's characters (e.g. configuring ``coverage_key_prefix="myapp"`` must
strip ``"myapp/pkg/mod.py"`` but must NOT touch
``"myapp_legacy/pkg/mod.py"`` — a different, unrelated project directory
that happens to start with the same letters). An adapter with nothing to
strip (the default, ``coverage_key_prefix=""``) returns *key* unchanged,
exactly as ``adapters/base.py``'s own docstring specifies.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

__all__ = ["PythonAdapter"]

#: Adopted verbatim from dstdns/scripts/coverage_gate.py's own
#: ``_TEST_FILE_RE`` (the sole holder among the three cited reference gates
#: — topos and nyxloom have no test-path concept at all). A path is a test
#: path when it sits under a ``tests/`` segment at any depth, OR its own
#: filename starts with ``test_`` and ends in ``.py``, OR its filename is
#: exactly ``conftest.py``. The ``(^|/)`` anchor on the ``tests/`` branch is
#: what keeps a sibling directory like ``tests_data/`` (or a file
#: ``mytests/thing.py``) from mismatching — the same boundary discipline
#: ``evaluate.py``'s own source-root check applies one layer up, reproduced
#: here at the string level because this regex is this adapter's own, not a
#: retest of that already-proven mechanism (A-099's own reasoning, applied
#: to this file's other boundary-sensitive rule).
_TEST_FILE_RE = re.compile(r"(^|/)(tests/|test_[^/]*\.py$|conftest\.py$)")


def _is_bare_string_statement(node: ast.stmt) -> bool:
    """True for a bare string-literal expression statement — Python's own
    docstring convention (module/class/function/method), or a stray string
    literal used as an inline comment. ``coverage.py`` never traces one of
    these as executable at all, the same as a ``#`` comment; without this
    exclusion a docstring-only module would incorrectly register as "has
    executable code" merely because it contains a real, line-numbered AST
    node.
    """
    if not isinstance(node, ast.Expr):
        return False
    value = node.value
    return isinstance(value, ast.Constant) and isinstance(value.value, str)


@dataclass(frozen=True, kw_only=True)
class PythonAdapter:
    """The Python union of dstdns/topos/nyxloom's changed-line coverage
    gates, implemented against P05's frozen :class:`~assay.adapters.base.
    LanguageAdapter` protocol (A-097) — exactly its five attributes and
    three methods, plus one adapter-private constructor field
    (:attr:`coverage_key_prefix`) that the protocol does not name and does
    not need to: a concrete adapter is free to carry extra state beyond the
    protocol's structural surface, the same way P05's own ``FakeAdapter``
    (``tests/conftest.py``) carries ``key_prefix``/``test_marker``/
    ``no_code_marker`` alongside the five required attributes.
    """

    name: str = "python"
    source_globs: tuple[str, ...] = ("*.py",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = True
    external_tools: tuple[str, ...] = ()
    #: A declared, known directory-segment prefix a coverage artifact's own
    #: keys carry that the diff's spelling does not (or vice versa is never
    #: this method's job — only THIS direction, stripping, matches
    #: ``adapters/base.py``'s own "prefix STRIP" framing). Empty means
    #: nothing to strip — the common case where coverage.py's own cwd
    #: already matches the diff's own repo top.
    coverage_key_prefix: str = ""

    def is_test_path(self, rel_path: str) -> bool:
        return bool(_TEST_FILE_RE.search(rel_path))

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            # Fail CLOSED (srdm's asymmetry, DESIGN-GUIDE §11): an
            # unparseable file is never silently excused as "expectedly
            # code-free" — the worst case here is a false failure the
            # author can see and fix, not a silent measurement gap.
            return True
        for node in tree.body:
            if _is_bare_string_statement(node):
                continue
            return True
        return False

    def normalize_coverage_key(self, key: str) -> str:
        prefix = self.coverage_key_prefix
        if not prefix or not key.startswith(prefix + "/"):
            return key
        return key[len(prefix) + 1 :]
