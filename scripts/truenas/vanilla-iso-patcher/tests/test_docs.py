from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


DOCS = ("README.md", "DESIGN-GUIDE.md", "CONSUMERS.md", "COMPATIBILITY.md")
USER_DOCS = ("README.md", "CONSUMERS.md")
HERE = Path(__file__).resolve().parents[1]


def _slug(heading: str) -> str:
    heading = re.sub(r"`([^`]+)`", r"\1", heading).lower()
    heading = re.sub(r"[^\w -]", "", heading)
    return re.sub(r"\s+", "-", heading.strip())


def _anchors(path: Path) -> set[str]:
    result: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            result.add(_slug(match.group(1)))
    return result


def test_local_document_links_and_anchors_resolve():
    for source_name in DOCS:
        source = HERE / source_name
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            target = target.split(maxsplit=1)[0]
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            target_path = unquote(parsed.path)
            target_file = (source.parent / target_path).resolve() if target_path else source
            assert target_file.is_file(), f"{source_name} links to missing file {target_path!r}"
            if parsed.fragment:
                assert _slug(unquote(parsed.fragment)) in _anchors(target_file), (
                    f"{source_name} links to missing anchor {parsed.fragment!r} "
                    f"in {target_file.name}"
                )


def test_user_docs_name_every_cli_verb():
    user_text = "\n".join((HERE / name).read_text(encoding="utf-8") for name in USER_DOCS)
    for verb in ("releases", "browse", "download", "create"):
        assert re.search(rf"\b{verb}\b", user_text), f"CLI verb {verb!r} is undocumented"
