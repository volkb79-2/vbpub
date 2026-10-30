"""Parse and inspect ordered Git patch series with stable change metadata."""

from __future__ import annotations

import hashlib
import re
import shlex
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import tomllib


class SeriesError(ValueError):
    """A patch series is malformed or violates its declared convention."""


@dataclass(frozen=True)
class Change:
    """One logical change exported by ``git format-patch``."""

    path: Path
    subject: str
    change_id: str | None
    capability: str | None
    role: str | None
    risk: str | None
    requires: tuple[str, ...] | None
    files: tuple[str, ...]
    insertions: int
    deletions: int
    patch_sha256: str


@dataclass(frozen=True)
class ChangeDelta:
    """Presence and patch-byte change for one stable ``Change-Id``."""

    change_id: str
    status: str
    before: Change | None
    after: Change | None


_TRAILER_KEYS = ("Change-Id", "Capability", "Role", "Risk", "Requires")
_CHANGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_UPSTREAM_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def parse(series_dir: Path | str) -> list[Change]:
    """Read ``*.patch`` files in filename order from a series directory."""
    root = Path(series_dir)
    if not root.is_dir():
        raise SeriesError(f"series path is not a directory: {root}")
    return [_parse_patch(path) for path in sorted(root.glob("*.patch"))]


def _parse_patch(path: Path) -> Change:
    try:
        content = path.read_bytes()
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SeriesError(f"patch is not UTF-8: {path}") from exc

    subject = ""
    fields: dict[str, list[str]] = defaultdict(list)
    in_message = True
    in_diff = False
    in_hunk = False
    files: list[str] = []
    old_path: str | None = None
    fallback_path: str | None = None
    fallback_metadata = False
    fallback_has_file = False
    has_binary = False
    insertions = 0
    deletions = 0

    for line in lines:
        if in_message:
            if line == "---":
                in_message = False
                in_diff = True
                continue
            if line.startswith("Subject:"):
                subject = _subject_text(line.partition(":")[2].strip())
            for key in _TRAILER_KEYS:
                prefix = f"{key}:"
                if line.startswith(prefix):
                    fields[key].append(line[len(prefix) :].strip())
        else:
            if line.startswith("@@"):
                in_hunk = True
            elif in_hunk and line.startswith("+"):
                insertions += 1
            elif in_hunk and line.startswith("-"):
                deletions += 1
            elif line.startswith("diff --git "):
                if fallback_metadata and not fallback_has_file:
                    _append_file(files, fallback_path)
                in_hunk = False
                old_path = None
                fallback_metadata = False
                fallback_has_file = False
                try:
                    header = shlex.split(line)
                except ValueError as exc:
                    raise SeriesError(f"patch has an invalid diff header: {path}") from exc
                if len(header) >= 4:
                    old_diff_path = _patch_path(header[2])
                    new_diff_path = _patch_path(header[3])
                    fallback_path = (
                        new_diff_path if new_diff_path is not None else old_diff_path
                    )
                else:
                    fallback_path = None
            elif line.startswith("--- "):
                in_hunk = False
                old_path = _patch_path(line[4:])
            elif line.startswith("+++ "):
                in_hunk = False
                new_path = _patch_path(line[4:])
                selected = new_path if new_path is not None else old_path
                _append_file(files, selected)
                fallback_has_file = selected is not None
                old_path = None
            elif line.startswith("rename from "):
                fallback_metadata = True
                fallback_path = _metadata_path(line[len("rename from ") :], path)
            elif line.startswith("rename to "):
                fallback_metadata = True
                fallback_path = _metadata_path(line[len("rename to ") :], path)
            elif line.startswith(
                ("old mode ", "new mode ", "new file mode ", "deleted file mode ")
            ):
                fallback_metadata = True
            elif line == "GIT binary patch" or line.startswith("Binary files "):
                has_binary = True

    if fallback_metadata and not fallback_has_file:
        _append_file(files, fallback_path)

    if not subject:
        raise SeriesError(f"patch has no Subject header: {path}")
    if has_binary:
        raise SeriesError(f"binary patches are unsupported: {path}")
    if not in_diff or not files:
        raise SeriesError(
            f"patch has no supported file-change metadata (text, rename, or mode): {path}"
        )
    for key in _TRAILER_KEYS[:-1]:
        if len(fields[key]) > 1:
            raise SeriesError(f"patch has duplicate {key} trailer: {path}")

    raw_requires = fields["Requires"]
    requires: tuple[str, ...] | None
    if not raw_requires:
        requires = None
    else:
        values: list[str] = []
        for item in raw_requires:
            if item.lower() == "none":
                continue
            values.extend(part.strip() for part in item.split(","))
        if any(not item for item in values):
            raise SeriesError(f"patch has an empty Requires value: {path}")
        requires = tuple(values)

    def one(key: str) -> str | None:
        values = fields[key]
        return values[0] if values else None

    return Change(
        path=path,
        subject=subject,
        change_id=one("Change-Id"),
        capability=one("Capability"),
        role=one("Role"),
        risk=one("Risk"),
        requires=requires,
        files=tuple(files),
        insertions=insertions,
        deletions=deletions,
        patch_sha256=hashlib.sha256(content).hexdigest(),
    )


def _subject_text(value: str) -> str:
    """Remove the format-patch marker while retaining the authored subject."""
    match = re.match(r"^\[PATCH(?: [^]]+)?\]\s*(.*)$", value)
    return match.group(1).strip() if match else value


def _patch_path(value: str) -> str | None:
    if value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        return value[2:]
    return value


def _metadata_path(value: str, patch_path: Path) -> str:
    """Parse a Git rename path, retaining spaces in an unquoted path."""
    try:
        fields = shlex.split(value)
    except ValueError as exc:
        raise SeriesError(f"patch has invalid rename metadata: {patch_path}") from exc
    if len(fields) == 1:
        return fields[0]
    return value


def _append_file(files: list[str], value: str | None) -> None:
    if value is not None and value not in files:
        files.append(value)


def load_vocabulary(path: Path | str) -> dict[str, frozenset[str]]:
    """Load the consumer's closed capability, role and risk vocabularies."""
    try:
        with Path(path).open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SeriesError(f"cannot read vocabulary {path}: {exc}") from exc
    if (
        type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
    ):
        raise SeriesError(f"vocabulary {path} must declare schema_version = 1")
    vocabulary: dict[str, frozenset[str]] = {}
    for name in ("capabilities", "roles", "risks"):
        values = document.get(name)
        if not isinstance(values, list) or any(
            not isinstance(item, str) for item in values
        ):
            raise SeriesError(
                f"vocabulary {path} must define {name} as an array of strings"
            )
        vocabulary[name] = frozenset(values)
    return vocabulary


def check(changes: Sequence[Change], vocabulary: Mapping[str, Iterable[str]]) -> None:
    """Validate required trailers, closed vocabularies and dependency closure."""
    categories = {"capabilities": "capability", "roles": "role", "risks": "risk"}
    missing_vocab = categories.keys() - vocabulary.keys()
    if missing_vocab:
        raise SeriesError(
            f"vocabulary is missing categories: {', '.join(sorted(missing_vocab))}"
        )
    allowed = {key: set(vocabulary[key]) for key in categories}
    by_id: dict[str, Change] = {}
    for change in changes:
        missing = [
            field
            for field, value in (
                ("Change-Id", change.change_id),
                ("Capability", change.capability),
                ("Role", change.role),
                ("Risk", change.risk),
                ("Requires", change.requires),
            )
            if value is None or value == ""
        ]
        if missing:
            raise SeriesError(
                f"{change.path.name} is missing required headers: {', '.join(missing)}"
            )
        assert change.change_id is not None
        if not _CHANGE_ID.fullmatch(change.change_id):
            raise SeriesError(
                f"invalid Change-Id {change.change_id!r} in {change.path.name}"
            )
        if change.change_id in by_id:
            raise SeriesError(f"duplicate Change-Id {change.change_id!r}")
        by_id[change.change_id] = change
        for key, value in (
            ("capabilities", change.capability),
            ("roles", change.role),
            ("risks", change.risk),
        ):
            assert value is not None
            if value not in allowed[key]:
                label = key[:-1].capitalize() if key != "capabilities" else "Capability"
                raise SeriesError(f"unknown {label} {value!r} in {change.path.name}")
        assert change.requires is not None
        if len(set(change.requires)) != len(change.requires):
            raise SeriesError(f"duplicate Requires value in {change.path.name}")

    for change in changes:
        assert change.change_id is not None and change.requires is not None
        for dependency in change.requires:
            if dependency not in by_id:
                raise SeriesError(
                    f"unresolved Requires {dependency!r} in Change-Id {change.change_id!r}"
                )

    complete: set[str] = set()
    active: set[str] = set()

    def visit(change_id: str) -> None:
        if change_id in complete:
            return
        if change_id in active:
            raise SeriesError(f"dependency cycle includes Change-Id {change_id!r}")
        active.add(change_id)
        change = by_id[change_id]
        assert change.requires is not None
        for dependency in change.requires:
            visit(dependency)
        active.remove(change_id)
        complete.add(change_id)

    for change_id in by_id:
        visit(change_id)

    positions = {change.change_id: index for index, change in enumerate(changes)}
    for change in changes:
        assert change.change_id is not None and change.requires is not None
        for dependency in change.requires:
            if positions[dependency] >= positions[change.change_id]:
                raise SeriesError(
                    f"required Change-Id {dependency!r} must appear before dependent "
                    f"Change-Id {change.change_id!r}"
                )


def closure(changes: Sequence[Change], selected: Iterable[str]) -> list[Change]:
    """Return selected changes and requirements in valid application order."""
    by_id: dict[str, Change] = {}
    for change in changes:
        if change.change_id is None:
            raise SeriesError(f"{change.path.name} has no Change-Id")
        if change.change_id in by_id:
            raise SeriesError(f"duplicate Change-Id {change.change_id!r}")
        by_id[change.change_id] = change

    included: set[str] = set()

    def include(change_id: str) -> None:
        if change_id not in by_id:
            raise SeriesError(f"unknown selected Change-Id or dependency {change_id!r}")
        if change_id in included:
            return
        included.add(change_id)
        requires = by_id[change_id].requires
        if requires is not None:
            for dependency in requires:
                include(dependency)

    for change_id in selected:
        include(change_id)

    positions = {change.change_id: index for index, change in enumerate(changes)}
    dependencies: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = defaultdict(set)
    for change_id in included:
        requires = by_id[change_id].requires or ()
        dependencies[change_id] = set(requires) & included
        for dependency in dependencies[change_id]:
            dependents[dependency].add(change_id)

    ready = sorted(
        (change_id for change_id in included if not dependencies[change_id]),
        key=positions.__getitem__,
    )
    ordered: list[Change] = []
    while ready:
        change_id = ready.pop(0)
        ordered.append(by_id[change_id])
        for dependent in sorted(dependents[change_id], key=positions.__getitem__):
            dependencies[dependent].remove(change_id)
            if not dependencies[dependent]:
                ready.append(dependent)
                ready.sort(key=positions.__getitem__)
    if len(ordered) != len(included):
        raise SeriesError("dependency cycle in selected closure")
    return ordered


def render(
    columns: Mapping[str, Sequence[Change]] | Sequence[tuple[str, Sequence[Change]]],
) -> str:
    """Render capability and role inventories with one column per series."""
    pairs = list(columns.items()) if isinstance(columns, Mapping) else list(columns)
    labels = [label for label, _ in pairs]
    if len(labels) != len(set(labels)):
        raise SeriesError("duplicate column label")
    if any(not label for label in labels):
        raise SeriesError("column labels must not be empty")

    sections: list[str] = []
    for title, attribute in (("Capabilities", "capability"), ("Roles", "role")):
        totals: dict[str, dict[str, list[int]]] = defaultdict(dict)
        for label, changes in pairs:
            for change in changes:
                value = getattr(change, attribute)
                if value is None:
                    continue
                stats = totals[value].setdefault(label, [0, 0, 0])
                stats[0] += 1
                stats[1] += change.insertions
                stats[2] += change.deletions
        first_column = "Capability" if attribute == "capability" else "Role"
        header = "| " + " | ".join([first_column, *labels]) + " |"
        separator = "| " + " | ".join(["---", *["---" for _ in labels]]) + " |"
        rows = [header, separator]
        for value in sorted(totals):
            cells = [_escape(value)]
            for label, _ in pairs:
                stats = totals[value].get(label)
                cells.append(_stats_cell(stats))
            rows.append("| " + " | ".join(cells) + " |")
        sections.append(f"## {title}\n\n" + "\n".join(rows))
    return "\n\n".join(sections) + "\n"


def _stats_cell(stats: list[int] | None) -> str:
    if stats is None:
        return "—"
    count, additions, deletions = stats
    noun = "change" if count == 1 else "changes"
    return f"{count} {noun} (+{additions}/-{deletions})"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def delta(before: Sequence[Change], after: Sequence[Change]) -> list[ChangeDelta]:
    """Compare two series by stable Change-Id and exact patch-file digest."""
    old = _unique_index(before)
    new = _unique_index(after)
    result: list[ChangeDelta] = []
    for change_id in sorted(old.keys() | new.keys()):
        previous = old.get(change_id)
        current = new.get(change_id)
        if previous is None:
            status = "added"
        elif current is None:
            status = "removed"
        elif previous.patch_sha256 == current.patch_sha256:
            status = "unchanged"
        else:
            status = "changed"
        result.append(ChangeDelta(change_id, status, previous, current))
    return result


def _unique_index(changes: Sequence[Change]) -> dict[str, Change]:
    indexed: dict[str, Change] = {}
    for change in changes:
        if change.change_id is None:
            raise SeriesError(f"{change.path.name} has no Change-Id")
        if change.change_id in indexed:
            raise SeriesError(f"duplicate Change-Id {change.change_id!r}")
        indexed[change.change_id] = change
    return indexed


def series_digest(changes: Sequence[Change], upstream_commit: str) -> str:
    """Hash the exact upstream commit and ordered patch bytes.

    Length-prefixed fields make the encoding unambiguous. No clock or build
    date, filename or library version is accepted, so unrelated metadata
    cannot perturb the series identity.
    """
    if not _UPSTREAM_COMMIT.fullmatch(upstream_commit):
        raise SeriesError(
            "upstream commit must be a full 40- or 64-character lowercase SHA"
        )
    digest = hashlib.sha256()

    def add(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    add(upstream_commit.encode("ascii"))
    for change in changes:
        add(change.path.read_bytes())
    return digest.hexdigest()
