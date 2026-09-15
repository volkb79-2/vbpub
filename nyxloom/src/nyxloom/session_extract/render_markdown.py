"""`extract --render-markdown`: render one kept event's prose as markdown,
the way the CLI that produced it rendered it live.

Why this is its own module: `rich` is a real dependency but only this one
flag needs it, so `render.py` (imported by every extract path, including
--json) must never import it. render.py takes an opaque per-block callable
instead; this module is what cli.py hands it.

Why `rich` and not `pygments` (which `--highlight` uses): these are two
genuinely different jobs. `rich.markdown.Markdown` RENDERS -- `**bold**`
becomes real terminal bold and the asterisks are GONE, a `#` header becomes
a styled heading, a fenced block becomes a framed, syntax-highlighted
panel. That is right for reading a brief and wrong for copying prose back
out of the terminal, because the markup characters a later paste would need
no longer exist on screen. `--highlight` (highlight.py) exists for exactly
that other case. Neither is a substitute for the other, so both libraries
are carried deliberately.

Scope is the event's own prose ONLY. render.py applies this per kept event,
before blocks are joined with the `---` separators, the bracketed gap/
stop-reason notes, the `<!-- nyxloom-extract: ... -->` footer, and cli.py's
own `════ TASK ════` banner -- none of which is markdown and all of which a
whole-output markdown render would mangle (a `---` line is a horizontal
rule; an HTML comment disappears entirely).
"""

from __future__ import annotations


def render_markdown(text: str, color: bool = True, width: int | None = None) -> str:
    """One block's markdown, rendered to a string (ANSI styling included
    when `color`). `width` overrides rich's own terminal-width detection --
    passed explicitly by tests so their expectations don't depend on the
    terminal the suite happens to run in.
    """
    import io

    from rich.console import Console
    from rich.markdown import Markdown

    console = Console(
        # A Console always writes somewhere; pointing it at a throwaway
        # buffer (rather than leaving it on stdout and relying on record=)
        # is what makes this a pure text->text function. `quiet=True` is NOT
        # the way to do that -- it suppresses the recording too, leaving
        # export_text() empty.
        file=io.StringIO(),
        record=True,
        width=width,
        # force_terminal keeps styling on even though the target is a plain
        # buffer; cli.py has already decided whether color is wanted (its
        # --color/--no-color, defaulting to stdout.isatty()).
        force_terminal=color,
        no_color=not color,
    )
    console.print(Markdown(text))
    rendered = console.export_text(styles=color)
    # rich pads every line out to the full console width. Harmless on screen,
    # but this output also gets piped and diffed, so the padding is stripped
    # per line -- after any style reset, so a code block's own BACKGROUND
    # padding (inside the escape sequence, not after it) still survives.
    # Trailing newlines are the join's business (render.py's separators), not
    # a block's own.
    return "\n".join(line.rstrip() for line in rendered.split("\n")).rstrip("\n")
