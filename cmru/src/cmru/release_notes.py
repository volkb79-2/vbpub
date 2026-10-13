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
a SUPPLEMENT, never a substitute: they ride below the version section in the
body, stay in the changelog through a dry run or a failed release, and are moved
into that version's history only after the publish succeeded (``changelog.
consume_published_notes``, KI-78).
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

_HEADING_LINE = re.compile(r"^## .*$", re.MULTILINE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_PLAIN_UNRELEASED = re.compile(r"^## \[Unreleased\][ \t]*$", re.MULTILINE)

#: Where CMRU may insert release sections; also ends an ``[Unreleased]`` body.
HISTORY_MARKER = "<!-- cmru: release history -->"
#: Written above notes that moved from ``[Unreleased]`` into a published version.
CONSUMED_MARKER = "<!-- cmru: unreleased notes published with this release -->"


class ReleaseNotesError(RuntimeError):
    """The requested curated release notes cannot be read or are unusable."""


@dataclass(frozen=True)
class ReleaseNotes:
    """One version's curated section plus the pending ``[Unreleased]`` notes.

    ``text`` is the dated version section, verbatim. ``unreleased`` is the
    author's ``## [Unreleased]`` body (HTML comments removed, otherwise verbatim,
    migration notes included); empty when there is none. It is appended to the
    release body below the section and moved into the version's history only
    after the publish succeeds (:func:`cmru.changelog.consume_published_notes`).
    """

    version: str
    source: str
    text: str
    unreleased: str = ""


def unreleased_span(markdown: str) -> tuple[int, int] | None:
    """Character span of a plain ``## [Unreleased]`` body, or ``None`` if absent.

    The body runs to the next ``## `` heading or the history marker.
    """
    match = _PLAIN_UNRELEASED.search(markdown)
    if match is None:
        return None
    rest = markdown[match.end():]
    ends = [i for i in (
        rest.find(HISTORY_MARKER),
        *(m.start() for m in re.finditer(r"^## ", rest, re.MULTILINE)),
    ) if i >= 0]
    return match.end(), match.end() + (min(ends) if ends else len(rest))


def unreleased_body(markdown: str) -> str:
    """The non-comment text of the ``[Unreleased]`` section (``""`` when empty)."""
    span = unreleased_span(markdown)
    if span is None:
        return ""
    return _HTML_COMMENT.sub("", markdown[span[0]:span[1]]).strip()


def declaration_for(changelog: str | None) -> str:
    """The protected declaration CMRU passes a publisher for a project."""
    return f"changelog:{changelog}" if changelog else NO_NOTES


def extract_release_section(markdown: str, version: str, *, source: str) -> ReleaseNotes:
    """Return the exact ``## [version] - date`` section, or refuse precisely."""
    headings = list(_HEADING_LINE.finditer(markdown))
    named = [item for item in headings if item.group(0).startswith(f"## [{version}]")]
    dated = [
        item for item in named
        if re.fullmatch(rf"## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}", item.group(0))
    ]
    if len(dated) > 1:
        raise ReleaseNotesError(
            f"{source} has {len(dated)} `## [{version}]` sections; a release body "
            "needs exactly one"
        )
    if not dated:
        if named:
            raise ReleaseNotesError(
                f"{source}: the heading `{named[0].group(0)}` is not "
                f"`## [{version}] - YYYY-MM-DD`; date the section before releasing"
            )
        found = ", ".join(
            item.group(0).removeprefix("## ") for item in headings[:5]
        ) or "no `## ` sections"
        raise ReleaseNotesError(
            f"{source} has no `## [{version}] - YYYY-MM-DD` section for this release "
            f"(found: {found}); CMRU never substitutes [Unreleased] or another version"
        )
    start = dated[0].start()
    later = [item.start() for item in headings if item.start() > start]
    section = markdown[start:min(later) if later else len(markdown)].rstrip()
    body = _HTML_COMMENT.sub("", section.split("\n", 1)[1] if "\n" in section else "").strip()
    if not body:
        raise ReleaseNotesError(
            f"{source}: the `## [{version}]` section has no release notes; write them "
            "or declare the project without a changelog (release.changelog = false)"
        )
    return ReleaseNotes(
        version=version, source=source, text=section, unreleased=unreleased_body(markdown),
    )


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
    install/artifact block; the curated section, when present, follows verbatim,
    and the pending ``[Unreleased]`` notes come last, also verbatim, so the
    generic install/usage text is always first.
    """
    body = headline + generated
    if notes is not None:
        body += "\n\n" + notes.text
        if notes.unreleased:
            body += "\n\n" + notes.unreleased
    return body
