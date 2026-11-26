"""The run records: legacy flat `<name>.*` files and registry `<id>/run.json` records (DESIGN 4/4a).

This reader is pure file parsing. It opens nothing for writing, reads no stream, no prompt and
no transcript, and never decides whether a lock is held (that is `sources`, which owns
`/proc/locks`). Per run name it answers:

* the outcome, with the documented rule that any `REVIEW-*` token wins over `EXIT=0` (a review
  that modified the tree did not pass);
* the live context (`latest_total_tokens`) of a `.live` file;
* the last `READY`/`YIELD` line of `.last.txt`, which is how the `<run>-y<N>` yield chain is
  evidenced (E-021);
* which extensions the record set actually contains, so a caller can tell an absent source from
  an unreadable one.

A registry record (`<dir>/<id>/run.json`, DESIGN section 4) is read only when it is present,
parses, and carries the declared schema AND its own id as its directory name — a subdirectory
that fails any of those is ignored rather than guessed at. When both a registry record and a
legacy flat file exist for one name, the registry's values win: a run cannot have two routes.

Every read is bounded (`bounded.head`/`bounded.tail`): a run's stream sits in the same
directory and may be gigabytes, and this reader must never be the reason memory fills.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import bounded

ACTIVE = "active"
ENDED = "ended"
RUN_JSON = "run.json"
RUN_SCHEMA = "agctl-run/1"
REGISTRY_STATES = ("launching", "running", "ended")

# The extensions of a legacy record set, longest suffix first so `last.txt` is not read as `txt`.
EXTENSIONS = ("last.txt", "done", "live", "wt", "route", "thread", "lock")

OUTCOME_PREFIX = "OUTCOME="
EXIT_PREFIX = "EXIT="
REVIEW_PREFIX = "REVIEW-"
READY_PREFIX = "READY run="
YIELD_PREFIX = "YIELD run="
# A generated id's stamp: `-20261125T042108` or `-20261124_183122`.
STAMP_TOKEN_RE = re.compile(r"^[0-9]{8}[_T][0-9]{6}$")
CHAIN_RE = re.compile(r"^(.*)-y([0-9]+)$")
BOUNDARY_RE = re.compile(r'boundary="([^"]*)"')


@dataclass(frozen=True)
class RunRecord:
    """One run name's record set, merged across the configured directories."""

    id: str
    source: str
    files: tuple[str, ...]
    exit_code: int | None
    outcome: str  # the `.done` class, or a registry outcome; a `REVIEW-*` token when present
    review: str  # the first `REVIEW-*` token, "" when absent
    route: str
    thread: str
    worktree: str
    tokens: int | None  # `latest_total_tokens` of `.live`
    live_mtime: float | None
    last_state: str  # "READY" or "YIELD" (the last such line), "" when neither
    last_run: str  # the run name the last READY/YIELD line names
    gauge: str
    boundary: str
    mark: str
    registry_state: str = ""  # "launching"|"running"|"ended" of a run.json record
    paths: dict[str, Path] = field(default_factory=dict, repr=False)

    def state(self) -> str:
        """`ended` or `active`; DIED is the caller's decision from the lock, never this reader's."""

        if self.registry_state == "ended" or "done" in self.files:
            return ENDED
        return ACTIVE

    def path(self, extension: str) -> Path | None:
        """Where this record's `extension` was found (``None`` when it has none).

        Each extension resolves on its own: the first configured directory that holds a file of
        that name wins, so an archived `.done` does not hide a still-present `.lock`.
        """

        return self.paths.get(extension)


@dataclass(frozen=True)
class RunSet:
    """Every record of the configured runs directories, and which of them were readable."""

    records: tuple[RunRecord, ...]
    dirs: tuple[str, ...]
    readable: tuple[str, ...]
    # `(directory, reason)` for each configured directory that exists but could not be listed:
    # one unreadable directory must not cost the records of the others.
    unreadable: tuple[tuple[str, str], ...] = ()

    def by_id(self, run_id: str) -> RunRecord | None:
        for record in self.records:
            if record.id == run_id:
                return record
        return None

    def for_key(self, key: str) -> tuple[RunRecord, ...]:
        """Records whose name belongs to the package `key` (the label convention of 4a)."""

        return tuple(record for record in self.records if package_key(record.id) == key)


def _first_line(text: str | None) -> str:
    if text is None:
        return ""
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


def _token_value(text: str, prefix: str) -> str:
    """The value of the first `<prefix><value>` token of `text` (``""`` when absent)."""

    for token in text.split():
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def parse_done(text: str | None) -> tuple[int | None, str, str]:
    """`(exit_code, outcome class, review token)` of a `.done` file.

    `EXIT=` and `OUTCOME=` are read from the FIRST line (`pi-impl.sh:530` writes
    `EXIT=<n> OUTCOME=<class>`). A `REVIEW-*` token anywhere in the file is a review's own
    verdict on the launch (`pi-review.sh:210` appends it); it wins over `EXIT=0`, because a
    review that modified the tree did not pass. The token is returned separately so a caller
    can print both facts.
    """

    if text is None:
        return None, "", ""
    lines = text.splitlines()
    first = lines[0] if lines else ""
    exit_text = _token_value(first, EXIT_PREFIX)
    exit_code = int(exit_text) if exit_text.isdigit() else None
    payload = _token_value(first, OUTCOME_PREFIX)
    review = ""
    for line in lines:
        token = next((part for part in line.split() if part.startswith(REVIEW_PREFIX)), "")
        if token:
            review = token
            break
    return exit_code, review or payload, review


def parse_last(text: str | None) -> tuple[str, str, str, str, str]:
    """`(state, run, gauge, boundary, mark)` of the LAST READY/YIELD line of a `last.txt`.

    `state` is `"READY"`, `"YIELD"` or `""`. The LAST such line is the run's current position:
    an earlier one describes a previous block of a resumed run.
    """

    if text is None:
        return "", "", "", "", ""
    state = run = gauge = boundary = mark = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(READY_PREFIX):
            # `READY run=` with no value (an agent quoting the format, or a truncated write)
            # has no run name: the position stays stated-empty instead of raising IndexError
            # and losing the whole estate view.
            tokens = stripped[len(READY_PREFIX):].split()
            state, run = "READY", tokens[0] if tokens else ""
            gauge = boundary = ""
            mark = _token_value(stripped, "mark=")
        elif stripped.startswith(YIELD_PREFIX):
            tokens = stripped[len(YIELD_PREFIX):].split()
            state, run = "YIELD", tokens[0] if tokens else ""
            gauge = _token_value(stripped, "gauge=")
            match = BOUNDARY_RE.search(stripped)
            boundary = match.group(1) if match else ""
            mark = ""
    return state, run, gauge, boundary, mark


def parse_live(text: str | None) -> tuple[int | None, float | None]:
    """`(latest_total_tokens, updated_epoch)` of a `.live` document; unknown is not zero."""

    if text is None:
        return None, None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(document, dict):
        return None, None
    tokens = document.get("latest_total_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int):
        tokens = None
    updated = document.get("updated_epoch")
    if isinstance(updated, bool) or not isinstance(updated, (int, float)):
        updated = None
    return tokens, float(updated) if updated is not None else None


def parse_registry(text: str | None, directory: str) -> dict[str, object] | None:
    """A `runs/<id>/run.json` document, or ``None`` when it is not a valid record of `directory`.

    Three things must hold: it parses as a JSON object, it declares the registry schema, and its
    own `id` equals its directory name. Anything else is ignored: a reader that accepted a
    half-written or foreign record would report another run's identity under this name.
    """

    if text is None:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict) or document.get("schema") != RUN_SCHEMA:
        return None
    return document if document.get("id") == directory else None


def package_key(name: str) -> str:
    """The package key of a branch name or a run label (DESIGN 4a's join key).

    Dropped: the generated stamp (`-20261125T042108`, `-20261124_183122`) and the yield-chain
    suffix (`-y<N>`, E-021). KEPT: everything else, because everything else is what tells two
    packages of one topic apart — `core-ciu-r2fix` and `core-cmru-ki208` are two packages, and
    a key that collapsed them to `core` would print one row standing for four worktrees.

    The real estate separates the two stages deliberately: a branch carries a topic and a slug
    (`core-ciu-r2fix`, `edge-relgate-20261125`, `plat-22c-resumebusy-...`), while a run label
    carries a topic and a designator (`plat-22c-pi`, `core-p08-fix1`). This function therefore
    does NOT shift the package key onto the designator; `topic_of` is the other half of the
    same convention and the `plan_id` cell reports the designator a `STATE.md` line names.
    """

    tokens = [token for token in name.split("-") if token]
    kept = [token for token in tokens if not STAMP_TOKEN_RE.match(token)]
    if not kept:
        return name
    base = "-".join(kept)
    chain = CHAIN_RE.match(base)
    return chain.group(1) if chain else base


def topic_of(name: str) -> str:
    """The topic of a branch name, run label or package key: its first token.

    This is the ONLY thing a `STATE.md` line can be matched on today (`P-core-08` names the
    designator, not the branch slug), and it is reported as `unverified` prose for exactly
    that reason.
    """

    tokens = [token for token in name.split("-") if token]
    kept = [token for token in tokens if not STAMP_TOKEN_RE.match(token)]
    return kept[0] if kept else name


def yield_chains(records: tuple[RunRecord, ...]) -> dict[str, tuple[str, ...]]:
    """The `<run>-y<N>` chains: base run id -> its ordered members.

    Membership comes from the names alone; the evidence for a link is the READY/YIELD line of
    that member's own `last.txt` (`RunRecord.last_state`/`last_run`).
    """

    groups: dict[str, dict[int, str]] = {}
    for record in records:
        match = CHAIN_RE.match(record.id)
        if match:
            groups.setdefault(match.group(1), {})[int(match.group(2))] = record.id
        else:
            groups.setdefault(record.id, {})
    chains: dict[str, tuple[str, ...]] = {}
    for base, members in groups.items():
        ordered = [members[number] for number in sorted(members)]
        chains[base] = tuple([base, *ordered]) if base not in ordered else tuple(ordered)
    return chains


def extension_of(name: str) -> str:
    """The legacy record extension of a file name, or ``""`` when it is not a record file."""

    for extension in EXTENSIONS:
        suffix = f".{extension}"
        if name.endswith(suffix) and len(name) > len(suffix):
            return extension
    return ""


def _registry_value(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    return value if isinstance(value, str) else ""


def _record(
    run_id: str,
    source: str,
    paths: dict[str, Path],
    registry: dict[str, object] | None,
) -> RunRecord:
    exit_code, outcome, review = parse_done(bounded.head(paths["done"]) if "done" in paths else None)
    tokens, _updated = parse_live(bounded.head(paths["live"]) if "live" in paths else None)
    last_state, last_run, gauge, boundary, mark = parse_last(
        # A STREAMING scan, not a tail window: `last.txt` can hold kilobytes of prose after the
        # run's last READY/YIELD line, and a tail read would report no position at all — which
        # is indistinguishable from a file that never had one (L3 of the round-1 review).
        bounded.last_line_matching(paths["last.txt"], (READY_PREFIX, YIELD_PREFIX))
        if "last.txt" in paths
        else None
    )
    live_mtime = None
    if "live" in paths:
        try:
            live_mtime = paths["live"].stat().st_mtime
        except OSError:
            live_mtime = None
    route = _first_line(bounded.head(paths["route"])) if "route" in paths else ""
    thread = _first_line(bounded.head(paths["thread"])) if "thread" in paths else ""
    worktree = _first_line(bounded.head(paths["wt"])) if "wt" in paths else ""
    registry_state = ""
    if registry is not None:
        # The registry is the machine record: its values WIN over a legacy file of the same
        # name, because one run cannot have two routes or two outcomes.
        route = _registry_value(registry, "route") or route
        thread = _registry_value(registry, "thread") or thread
        worktree = _registry_value(registry, "worktree") or worktree
        outcome = _registry_value(registry, "outcome") or outcome
        candidate = _registry_value(registry, "state")
        registry_state = candidate if candidate in REGISTRY_STATES else ""
        registry_exit = registry.get("exit")
        if isinstance(registry_exit, int) and not isinstance(registry_exit, bool):
            exit_code = registry_exit
    return RunRecord(
        id=run_id,
        source=source,
        files=tuple(sorted((*paths, *( () if registry is None else (RUN_JSON, ) )))),
        paths=dict(paths),
        exit_code=exit_code,
        outcome=outcome,
        review=review,
        route=route,
        thread=thread,
        worktree=worktree,
        tokens=tokens,
        live_mtime=live_mtime,
        last_state=last_state,
        last_run=last_run,
        gauge=gauge,
        boundary=boundary,
        mark=mark,
        registry_state=registry_state,
    )


def read(config) -> RunSet:
    """Read every configured runs directory; the FIRST directory wins for the same record.

    A directory that is absent is not an error and not "no runs": it is left out of `readable`,
    which the caller reports when none of them could be read.
    """

    paths: dict[str, dict[str, Path]] = {}
    sources: dict[str, str] = {}
    registries: dict[str, dict[str, object]] = {}
    readable: list[str] = []
    unreadable: list[tuple[str, str]] = []
    for directory in config.runs_dirs:
        if not directory.is_dir():
            continue
        entries, reason = bounded.walk(directory)
        if reason:
            unreadable.append((str(directory), reason))
            continue
        readable.append(str(directory))
        for entry in entries:
            if entry.is_dir():
                document = parse_registry(bounded.head(entry / RUN_JSON), entry.name)
                if document is not None:
                    registries.setdefault(entry.name, document)
                    paths.setdefault(entry.name, {})
                    sources.setdefault(entry.name, str(directory))
                continue
            if not entry.is_file():
                continue
            extension = extension_of(entry.name)
            if not extension:
                continue
            run_id = entry.name[: -(len(extension) + 1)]
            paths.setdefault(run_id, {}).setdefault(extension, entry)
            sources.setdefault(run_id, str(directory))
    records = tuple(
        _record(run_id, sources[run_id], paths[run_id], registries.get(run_id))
        for run_id in sorted(paths)
    )
    return RunSet(
        records=records,
        dirs=tuple(str(path) for path in config.runs_dirs),
        readable=tuple(readable),
        unreadable=tuple(unreadable),
    )
