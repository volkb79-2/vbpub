"""`--highlight`: color markdown SOURCE, leaving every character in place.

The case this exists for, and the reason it is NOT the same thing as
`--render-markdown` (render_markdown.py, via `rich`): copying real markdown
back out of a terminal. A running Claude Code / Codex / opencode pane only
ever RENDERS the model's markdown -- the terminal shows bold text, not
`**bold**` -- so what you select and copy has already lost the markup. Piping
the same session through a renderer again reproduces exactly that loss.
`pygments` instead colors the source the way an editor colors a markdown
file: every `#`, `**`, backtick and list dash is still there, so what you
copy out is still markdown.

Both are therefore carried deliberately, and they are mutually exclusive per
run -- rendering and preserving are opposite goals, not two settings of one.

Colorless mode returns the text UNCHANGED rather than running the lexer for
nothing: without ANSI there is no highlighting to apply, and the whole point
here is that the characters are untouched.
"""

from __future__ import annotations


def highlight_markdown(text: str, color: bool = True) -> str:
    """Markdown source with ANSI syntax coloring (or verbatim when
    `color` is False)."""
    if not color:
        return text

    from pygments import highlight
    from pygments.formatters import TerminalFormatter
    from pygments.lexers import MarkdownLexer

    # pygments appends a trailing newline of its own; block joining is the
    # caller's business (render.py's separators, follow.py's stream), same
    # convention as render_markdown.py.
    return highlight(text, MarkdownLexer(), TerminalFormatter()).rstrip("\n")
