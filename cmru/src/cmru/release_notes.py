"""Curated release notes for GitHub Release bodies (KI-75).

A CMRU-managed release body is the generated install/artifact block followed by
the project's own curated section for exactly the released version, taken from
its changelog. ONE builder (:func:`compose_release_body`) produces the body for
the publisher and for ``cmru release --dry-run``, so the preview can never
describe text the publisher will not send.

Absence of notes is never silent: a project either declares no changelog
(``release.changelog = false``, source ``none``) or its dated
``## [X.Y.Z] - YYYY-MM-DD`` section must exist exactly once and carry content.
A missing, malformed, duplicated, empty, or wrong-version section refuses
before any tag or upload; nothing falls back to a generic prefix, to
``[Unreleased]``, or to another version's text.

The author's pending ``## [Unreleased]`` entries (migration notes included) are
folded into the dated version section in the disposable release candidate before
the gate and tag. The publisher then reads that section from the exact tagged
commit. A dry run restores its input, while a failed release never promotes the
candidate. ``--no-build`` and image pushes without ``oci-image-release`` still
carry the notes in the tagged changelog, without publishing a GitHub Release body;
an image project that places ``oci-image-release`` after its push does publish
one (KI-78).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Protected input CMRU gives the publishing step: ``changelog:<project-relative
#: path>`` or ``none``. Projects cannot declare it (it is a reserved name).
NOTES_SOURCE_ENV = "CMRU_RELEASE_NOTES_SOURCE"
NO_NOTES = "none"

#: Stand-ins the dry-run preview prints for the two facts only a build produces.
ARTIFACT_PLACEHOLDER = "<artifact file name, known after the build>"
SHA256_PLACEHOLDER = "<sha256 of the built artifact>"

#: Where CMRU may insert release sections; also ends an ``[Unreleased]`` body.
HISTORY_MARKER = "<!-- cmru: release history -->"
#: Delimit the operator-authored notes folded into a candidate before its gate.
PENDING_NOTES_BEGIN = "<!-- cmru: pending release notes begin -->"
PENDING_NOTES_END = "<!-- cmru: pending release notes end -->"


class ReleaseNotesError(RuntimeError):
    """The requested curated release notes cannot be read or are unusable."""


@dataclass(frozen=True)
class ReleaseNotes:
    """One version's curated section, read from the release source commit.

    ``text`` is the dated version section, verbatim. CMRU folds pending notes
    into this section in the disposable release candidate before the gate and
    tag, so the publisher never combines a tagged section with mutable checkout
    content.
    """

    version: str
    source: str
    text: str


@dataclass(frozen=True)
class MarkdownHeading:
    """An H2 heading outside HTML comments and fenced code blocks."""

    text: str
    start: int
    end: int


def _mask_html_comments(markdown: str) -> str:
    """Mask HTML comments outside fenced code while preserving offsets/newlines."""
    output: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    in_comment = False
    for line in markdown.splitlines(keepends=True):
        if fence_char is not None:
            output.append(line)
            content = line.rstrip("\r\n")
            if re.match(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*$", content):
                fence_char = None
                fence_length = 0
            continue

        chars = list(line)
        index = 0
        while index < len(line):
            if in_comment:
                if line.startswith("-->", index):
                    chars[index:index + 3] = "   "
                    index += 3
                    in_comment = False
                    continue
                if line[index] not in "\r\n":
                    chars[index] = " "
                index += 1
                continue
            if line.startswith("<!--", index):
                chars[index:index + 4] = "    "
                index += 4
                in_comment = True
                continue
            index += 1

        masked = "".join(chars)
        output.append(masked)
        content = masked.rstrip("\r\n")
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
        if opening is not None:
            marker = opening.group(1)
            info = opening.group(2)
            if marker[0] != "`" or "`" not in info:
                fence_char = marker[0]
                fence_length = len(marker)
    return "".join(output)


def html_comments_outside_fenced_code(markdown: str) -> tuple[str, ...]:
    """Return actual HTML comments, excluding comment-like text in code fences."""
    masked = _mask_html_comments(markdown)
    return tuple(
        match.group(0)
        for match in re.finditer(r"<!--.*?-->", markdown, re.DOTALL)
        if masked[match.start():match.end()] != match.group(0)
    )


def html_comment_positions_outside_fenced_code(markdown: str, comment: str) -> tuple[int, ...]:
    """Source offsets of a literal control comment outside fenced code."""
    masked = _mask_html_comments(markdown)
    return tuple(
        match.start()
        for match in re.finditer(re.escape(comment), markdown)
        if masked[match.start():match.end()] != comment
    )


def markdown_h2_headings(markdown: str) -> tuple[MarkdownHeading, ...]:
    """Return top-level H2 source sections, excluding comments and code fences."""
    masked = _mask_html_comments(markdown)
    headings: list[MarkdownHeading] = []
    offset = 0
    fence_char: str | None = None
    fence_length = 0
    for line in masked.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if fence_char is not None:
            if re.match(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*$", content):
                fence_char = None
                fence_length = 0
            offset += len(line)
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
        if opening is not None:
            marker = opening.group(1)
            info = opening.group(2)
            if marker[0] != "`" or "`" not in info:
                fence_char = marker[0]
                fence_length = len(marker)
            offset += len(line)
            continue
        heading = re.match(r"^ {0,3}##[ \t]+(.*)$", content)
        if heading is not None:
            # CommonMark permits up to three leading spaces before an ATX
            # heading and one or more spaces or tabs after its markers. Keep a
            # canonical semantic spelling while ``end`` stays tied to the
            # source line's actual character span.
            heading_text = "## " + heading.group(1).rstrip()
            headings.append(MarkdownHeading(
                heading_text, offset, offset + len(content.rstrip())
            ))
        offset += len(line)
    return tuple(headings)


def unreleased_span(markdown: str) -> tuple[int, int] | None:
    """Character span of the unique ``## [Unreleased]`` body, or ``None``.

    Near-miss and duplicate Unreleased headings refuse instead of hiding notes.
    """
    candidates = []
    for heading in markdown_h2_headings(markdown):
        value = heading.text[3:].strip()
        folded = value.casefold()
        if folded == "unreleased" or folded.startswith("[unreleased]"):
            candidates.append((heading, value == "[Unreleased]"))
    if not candidates:
        return None
    if len(candidates) != 1 or not candidates[0][1]:
        raise ReleaseNotesError(
            "the changelog must have at most one exact `## [Unreleased]` heading; "
            "fix its spelling and remove duplicates"
        )
    heading = candidates[0][0]
    rest = markdown[heading.end:]
    history_markers = [
        position - heading.end
        for position in html_comment_positions_outside_fenced_code(markdown, HISTORY_MARKER)
        if position > heading.end
    ]
    ends = [i for i in (
        *history_markers,
        *(item.start - heading.end for item in markdown_h2_headings(markdown)
          if item.start > heading.start),
    ) if i >= 0]
    return heading.end, heading.end + (min(ends) if ends else len(rest))


def unreleased_body(markdown: str) -> str:
    """The non-comment text of the ``[Unreleased]`` section (``""`` when empty)."""
    span = unreleased_span(markdown)
    if span is None:
        return ""
    return _mask_html_comments(markdown[span[0]:span[1]]).strip()


def declaration_for(changelog: str | None) -> str:
    """The protected declaration CMRU passes a publisher for a project."""
    return f"changelog:{changelog}" if changelog else NO_NOTES


def extract_release_section(markdown: str, version: str, *, source: str) -> ReleaseNotes:
    """Return the exact ``## [version] - date`` section, or refuse precisely."""
    headings = markdown_h2_headings(markdown)
    named = [item for item in headings if item.text.startswith(f"## [{version}]")]
    dated = [
        item for item in named
        if re.fullmatch(rf"## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}", item.text)
    ]
    if len(dated) > 1:
        raise ReleaseNotesError(
            f"{source} has {len(dated)} `## [{version}]` sections; a release body "
            "needs exactly one"
        )
    if not dated:
        if named:
            raise ReleaseNotesError(
                f"{source}: the heading `{named[0].text}` is not "
                f"`## [{version}] - YYYY-MM-DD`; date the section before releasing"
            )
        found = ", ".join(
            item.text.removeprefix("## ") for item in headings[:5]
        ) or "no `## ` sections"
        raise ReleaseNotesError(
            f"{source} has no `## [{version}] - YYYY-MM-DD` section for this release "
            f"(found: {found}); CMRU never substitutes [Unreleased] or another version"
        )
    start = dated[0].start
    later = [item.start for item in headings if item.start > start]
    section = markdown[start:min(later) if later else len(markdown)].rstrip()
    body = _mask_html_comments(section.split("\n", 1)[1] if "\n" in section else "").strip()
    if not body:
        raise ReleaseNotesError(
            f"{source}: the `## [{version}]` section has no release notes; write them "
            "or declare the project without a changelog (release.changelog = false)"
        )
    return ReleaseNotes(version=version, source=source, text=section)


def _read_changelog(project_dir: Path, relative: str, commit: str | None) -> tuple[str, str]:
    """Read the changelog from the working tree, or from ``commit`` when given."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise ReleaseNotesError(
            f"release notes source {relative!r} must be a project-relative path"
        )
    if commit is None:
        target = project_dir / path
        try:
            return target.read_text(encoding="utf-8"), str(target)
        except (OSError, UnicodeError) as exc:
            raise ReleaseNotesError(f"cannot read release notes source {target}: {exc}") from exc
    prefix = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", "--show-prefix"],
        capture_output=True, text=True, check=False,
    )
    shown = subprocess.run(
        ["git", "-C", str(project_dir), "show", f"{commit}:{prefix.stdout.strip()}{path.as_posix()}"],
        capture_output=True, check=False,
    )
    where = f"{path.as_posix()} at {commit[:12]}"
    if prefix.returncode or shown.returncode:
        detail = (shown.stderr or prefix.stderr).decode("utf-8", "replace").strip()
        raise ReleaseNotesError(f"cannot read release notes source {where}: {detail}")
    try:
        return shown.stdout.decode("utf-8"), where
    except UnicodeError as exc:
        raise ReleaseNotesError(f"release notes source {where} is not UTF-8: {exc}") from exc


def load_release_notes(
    project_dir: Path, declaration: str, version: str, *, commit: str | None = None,
) -> ReleaseNotes | None:
    """Resolve one version's notes from a protected declaration; ``None`` = declared none."""
    if declaration == NO_NOTES:
        return None
    kind, _separator, relative = declaration.partition(":")
    if kind != "changelog" or not relative:
        raise ReleaseNotesError(
            f"{NOTES_SOURCE_ENV}={declaration!r} is malformed; expected "
            "`changelog:<project-relative path>` or `none`"
        )
    text, where = _read_changelog(project_dir, relative, commit)
    return extract_release_section(text, version, source=where)


def declared_source() -> str:
    """The publisher's declared notes source, or a refusal if CMRU gave none."""
    value = os.environ.get(NOTES_SOURCE_ENV)
    if not value:
        raise ReleaseNotesError(
            f"{NOTES_SOURCE_ENV} is not set. Publish through `cmru release` or "
            "`cmru publish` (they declare it), or export it yourself: "
            "`changelog:<project-relative path>` to include a version's curated "
            "section, or `none` for a project without a changelog"
        )
    return value


def compose_release_body(headline: str, generated: str, notes: ReleaseNotes | None) -> str:
    """The one place a release body is assembled.

    ``headline`` is the opening line (the operator's supplementary
    ``--notes-env`` text or ``<prefix> <version>``); ``generated`` is the
    install/artifact block; the curated section, when present, follows verbatim.
    Pending notes are folded into that section in the candidate before its gate
    and tag, so the tag is the release body's only notes source.
    """
    body = headline + generated
    if notes is not None:
        body += "\n\n" + notes.text
    return body
