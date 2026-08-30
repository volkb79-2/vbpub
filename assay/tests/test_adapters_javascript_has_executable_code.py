"""B036 — ``JavaScriptAdapter.has_executable_code``: the NoCode distinction,
answered narrowly and fail-closed (A-343).

``evaluate.py`` consults this ONLY for a changed, considered, non-test source
file with NO entry at all in the coverage artifact. srdm's asymmetry decides
the direction of every uncertain case: a wrong ``False`` is a SILENT EXCUSE
(the file is quietly dropped from the judgement), a wrong ``True`` is at worst
a visible false failure the author can see and fix. So exactly two cases
answer ``False`` -- a TypeScript declaration file, and text with nothing but
whitespace and comments in it -- and everything else, including anything this
scan cannot finish reading, answers ``True``.

Negative: answering ``True`` for a ``.d.ts`` file turns every changed
declaration file into a false coverage failure (a declaration file CANNOT
contain executable code and is reported by no coverage tool, measured against
both real Vitest providers in ``test_coverage_istanbul_real_fixtures.py``).
Answering ``False`` for a file this scan could not parse waves a real module
through as "expectedly code-free".
"""

from __future__ import annotations

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
        # A type-only module. Deliberately NOT claimed as code-free: deciding
        # it needs real TypeScript type-erasure semantics, which is the
        # hand-written-parser overreach B037's own scope boundary exists to
        # rule on. Under `@vitest/coverage-v8` it never arises (the artifact
        # reports such a module with an empty statement map, so this method is
        # not consulted at all); under `@vitest/coverage-istanbul` it is the
        # known false-failure case, tracked as B038.
        "export type WidgetList = Widget[]\n",
        "export interface Widget { id: string }\n",
        "import type { Pageable } from './types'\nexport type P = Pageable\n",
    ],
)
def test_a_module_with_any_surviving_content_has_executable_code(text: str):
    assert ADAPTER.has_executable_code("src/module.ts", text) is True


def test_an_unterminated_block_comment_fails_closed():
    """The scan cannot know what the rest of the file contained, so it
    refuses to claim the file is code-free (A-087's own fail-closed
    direction, adopted verbatim from the Go adapter's lexer)."""
    assert ADAPTER.has_executable_code("src/broken.ts", "/* never closed\n") is True


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
