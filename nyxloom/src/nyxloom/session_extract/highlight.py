"""`--highlight`: color markdown SOURCE, leaving every character in place.

The case this exists for, and the reason it is NOT the same thing as
`--render-markdown` (render_markdown.py, via `rich`): copying real markdown
back out of a terminal. A running Claude Code / Codex / Reasonix / opencode pane only
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

import re


def highlight_markdown(text: str, color: bool = True) -> str:
    """Markdown source with ANSI syntax coloring (or verbatim when
    `color` is False)."""
    if not color:
        return text
    if not text:
        return text

    from pygments import highlight
    from pygments.formatters import TerminalFormatter
    from pygments.lexers import MarkdownLexer

    # Pygments normalizes line endings and treats all trailing newlines as its
    # own formatter terminator. Keep the source's trailing newline suffix out
    # of the lexer, then restore it verbatim along with internal line endings.
    trailing_start = len(text)
    while trailing_start:
        match = re.search(r"(?:\r\n|\r|\n)\Z", text[:trailing_start])
        if match is None or match.end() != trailing_start:
            break
        trailing_start = match.start()
    body = text[:trailing_start]
    trailing = text[trailing_start:]
    if not body:
        return text
    line_endings = re.findall(r"\r\n|\r|\n", body)
    body = re.sub(r"\r\n|\r", "\n", body)
    rendered = highlight(body, MarkdownLexer(), TerminalFormatter())
    if rendered.endswith("\n"):
        rendered = rendered[:-1]
    rendered_parts = rendered.split("\n")
    if len(rendered_parts) == len(line_endings) + 1:
        rendered = "".join(
            part + (line_endings[i] if i < len(line_endings) else "")
            for i, part in enumerate(rendered_parts)
        )
    return rendered + trailing
