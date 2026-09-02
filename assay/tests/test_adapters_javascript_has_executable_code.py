"""B036/B038(b) — ``JavaScriptAdapter.has_executable_code``: the NoCode
distinction, answered narrowly and fail-closed (A-343).

``evaluate.py`` consults this ONLY for a changed, considered, non-test source
file with NO entry at all in the coverage artifact. srdm's asymmetry decides
the direction of every uncertain case: a wrong ``False`` is a SILENT EXCUSE
(the file is quietly dropped from the judgement), a wrong ``True`` is at worst
a visible false failure the author can see and fix. So exactly THREE cases
answer ``False`` -- a TypeScript declaration file, text with nothing but
whitespace and comments in it, and (B045/B038(b)) a ``.ts``/``.tsx`` module
whose every top-level statement is a type declaration -- and everything else,
including anything this scan cannot finish reading, answers ``True``.

Negative: answering ``True`` for a ``.d.ts`` file turns every changed
declaration file into a false coverage failure (a declaration file CANNOT
contain executable code and is reported by no coverage tool, measured against
both real Vitest providers in ``test_coverage_istanbul_real_fixtures.py``).
Answering ``False`` for a file this scan could not parse waves a real module
through as "expectedly code-free".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.adapters.javascript import JavaScriptAdapter

ADAPTER = JavaScriptAdapter()


# --- the two cases that are genuinely code-free -----------------------------


@pytest.mark.parametrize(
    "path",
    ["src/types.d.ts", "src/api/schema.d.ts", "src/types.d.mts", "src/types.d.cts"],
)
def test_a_declaration_file_has_no_executable_code_whatever_it_contains(path: str):
    """Decided from the PATH alone, because TypeScript's own grammar decides
    it: a declaration file may hold only ambient declarations. The body below
    is deliberately full of type machinery a naive content scan would call
    code."""
    declaration = (
        "declare module '@mantine/core/styles.css' {}\n"
        "export interface Pageable { page: number; size: number }\n"
        "export type Handler = (event: MouseEvent) => void\n"
        "export declare function helper(value: number): string\n"
    )
    assert ADAPTER.has_executable_code(path, declaration) is False


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n\n\n",
        "   \t  \n",
        "// a placeholder module, nothing here yet\n",
        "/* everything in this file is commented out\n   for now */\n",
        "// leading\n/* block */\n   // trailing\n",
    ],
)
def test_text_with_only_whitespace_and_comments_has_no_executable_code(text: str):
    """The empty-``__init__.py`` analogue: a file a coverage tool is
    EXPECTED to be silent about."""
    assert ADAPTER.has_executable_code("src/placeholder.ts", text) is False


# --- everything else, fail-closed -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "export const answer = 42\n",
        "console.log('side effect')\n",
        "export default function App() { return null }\n",
        "import './styles.css'\n",
        "export * from './roles'\n",
        # B045/B038(b) MOVED the three type-only shapes that used to sit here
        # into `test_a_type_only_module_has_no_executable_code` below. What
        # stays is the discrimination that matters: `export type`/`interface`
        # are recognised, and every OTHER `export` still answers True.
        "export typeGuard = 1\n",
        "export interfaceName()\n",
        "typeof window\n",
    ],
)
def test_a_module_with_any_surviving_content_has_executable_code(text: str):
    assert ADAPTER.has_executable_code("src/module.ts", text) is True


def test_an_unterminated_block_comment_fails_closed():
    """The scan cannot know what the rest of the file contained, so it
    refuses to claim the file is code-free (A-087's own fail-closed
    direction, adopted verbatim from the Go adapter's lexer)."""
    assert ADAPTER.has_executable_code("src/broken.ts", "/* never closed\n") is True


# --- B038(b): the type-only module ------------------------------------------
#
# The gap this closes, in one sentence: a `.ts` module holding only type
# declarations is ERASED by the TypeScript compiler, so no instrumenter emits
# a record for it, so `evaluate.py` rule 4 saw a changed file absent from the
# artifact and — before B045 — was told by this method that it had code, and
# failed the lane. The fixture pair below is the measured witness and its
# control, not two hand-written strings: both files are real, both are read by
# `test_coverage_istanbul_real_fixtures.py`, and `typesonly.ts` is genuinely
# absent from the committed `@vitest/coverage-istanbul` artifact.

PROBE_JS_SRC = Path(__file__).resolve().parent / "fixtures/coverage/probe-js/src"


def test_the_real_type_only_fixture_has_no_executable_code():
    text = (PROBE_JS_SRC / "typesonly.ts").read_text(encoding="utf-8")
    assert ADAPTER.has_executable_code("src/typesonly.ts", text) is False


def test_the_real_control_with_one_runtime_export_has_executable_code():
    """`orphan.ts` is `typesonly.ts`'s control: same directory, same size
    class, one `export function` instead of the types. If this ever answered
    `False`, the lexer would be laundering real uncovered code as NoCode --
    the silent-excuse direction."""
    text = (PROBE_JS_SRC / "orphan.ts").read_text(encoding="utf-8")
    assert ADAPTER.has_executable_code("src/orphan.ts", text) is True


@pytest.mark.parametrize(
    "text",
    [
        "export type WidgetList = Widget[]\n",
        "export interface Widget { id: string }\n",
        "import type { Pageable } from './types'\nexport type P = Pageable\n",
        "type Internal = string\n",
        "interface Internal { a: number }\n",
        "declare global { interface Window { x: number } }\n",
        # A bracket inside a string literal must not unbalance the depth
        # count -- the reason literals are skipped rather than ignored.
        "type Brace = '{'\n",
        # Blank lines and comments interleaved with declarations.
        "import type { A } from './a'\n\n// a note\nexport type B = A\n",
        # Multi-line bodies: the newlines inside the braces are at depth 1
        # and so are not statement separators.
        "export interface W {\n  id: string\n  page: number\n}\n",
    ],
)
def test_a_type_only_module_has_no_executable_code(text: str):
    assert ADAPTER.has_executable_code("src/types.ts", text) is False
    assert ADAPTER.has_executable_code("src/types.tsx", text) is False


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("export type A = 1; console.log(1)\n", "a runtime statement after a `;`"),
        ("export type A = 1\nconsole.log(1)\n", "a runtime statement on its own line"),
        ("export typeGuard = 1\n", "an identifier that merely STARTS with `type`"),
        ("export interfaceName()\n", "an identifier that merely starts with `interface`"),
        ("typeof window\n", "`typeof`, not `type `"),
        ("export type Mode =\n  | 'read'\n  | 'write'\n", "a multi-line declaration"),
        ("export interface A {\n  b: string\n", "an unclosed brace"),
        ("export type A = 1\n}\nconsole.log(2)\n", "an unmatched closing brace"),
        ("export type A = 'unterminated\n", "an unterminated string literal"),
        (
            "export type T = `a${'b'}c`\nconsole.log(1)\n",
            "a template substitution the scan will not follow",
        ),
        ("import './side.css'\n", "a side-effect import"),
        ("export * from './roles'\n", "a runtime re-export"),
    ],
)
def test_everything_the_type_only_lexer_does_not_recognise_fails_closed(
    text: str, why: str
):
    """Every one of these answers "has code". Four of them (`typeGuard`, the
    template substitution, the unclosed brace, the unmatched closer) are
    regression pins for fail-OPEN answers the first draft of this lexer
    actually gave, found by probing it rather than by reading it: without the
    trailing space in the prefix table `export typeGuard = 1` parsed as a type
    declaration, and without the all-or-nothing `None` return a `${`
    substitution swallowed the following `console.log` into the type
    declaration's own segment."""
    assert ADAPTER.has_executable_code("src/module.ts", text) is True, why


def test_a_js_file_is_never_type_only():
    """`type`/`interface` are not JavaScript. A `.js` file containing them is
    not an erased module -- it is a file that will not run, and calling it
    code-free would hide exactly that."""
    assert ADAPTER.has_executable_code("src/module.js", "export type A = 1\n") is True
    assert ADAPTER.has_executable_code("src/module.jsx", "interface A { b: 1 }\n") is True


def test_a_comment_delimiter_inside_a_string_still_fails_closed():
    """This scan deliberately does not carry JS's string/template-literal
    grammar (its one question does not need it), so a ``//`` inside a literal
    is read as a comment. The mis-read is in the fail-closed direction: the
    literal's own opening quote survives ahead of the mask, so the file still
    answers ``True``."""
    assert (
        ADAPTER.has_executable_code("src/url.ts", "export const u = 'http://x'\n")
        is True
    )


def test_a_declaration_file_is_still_this_adapters_source():
    """``.d.ts`` matches ``*.ts`` and is therefore CONSIDERED, then classified
    as code-free -- deliberately, rather than being made invisible by the
    source globs. "This is my source, and it has no executable content" and
    "this is not my source" are different facts, and only the first one leaves
    a changed declaration file visible in the ``considered`` count."""
    import fnmatch

    assert any(
        fnmatch.fnmatch("src/types.d.ts", glob) for glob in ADAPTER.source_globs
    )
