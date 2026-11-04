"""Shared helpers for the bot-protection tests: load a saved page and derive what a browser reports."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "blocked"
LONG_FILLER = "A perfectly ordinary sentence about benchmarks and their methodology. " * 120


@dataclass(frozen=True)
class SavedPage:
    title: str
    text: str
    frames: list[str]


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.text: list[str] = []
        self.frames: list[str] = []
        self._in_title = False
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag == "body":
            self._in_body = True
        elif tag == "iframe":
            self.frames.append(dict(attrs).get("src") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._in_body and data.strip():
            self.text.append(data.strip())


def load_page(name: str) -> SavedPage:
    """Parse a saved fixture the way a browser would report it (title, visible text, frame URLs)."""

    parser = _Parser()
    parser.feed((FIXTURES / name).read_text(encoding="utf-8").replace("@@LONG@@", LONG_FILLER))
    return SavedPage(parser.title, "\n".join(parser.text), parser.frames)
