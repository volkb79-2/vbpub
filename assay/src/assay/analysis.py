"""Archive review artifacts and inspect recorded facts without judging a release."""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TextIO

from . import __version__, git
from .errors import AssayError
from .verify import verify_text

SCHEMA_VERSION = 1
JOB_MARKER = "ASSAY_ANALYSIS_JOB_EXIT"


def _json(text: str):
    def finite(value):
        raise ValueError(f"non-finite JSON literal: {value}")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=finite)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds decoder limit") from exc


def _digest(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _read(path: Path, *, errors: str = "strict") -> tuple[str, dict]:
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {path}")
    data = path.read_bytes()
    return data.decode("utf-8", errors=errors), {"path": str(path.resolve()),
                                 "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _relative(name: str) -> PurePosixPath:
    relative = PurePosixPath(name)
    if (name in ("", ".") or relative.is_absolute() or relative.as_posix() != name
            or ".." in relative.parts or relative.parts[0] == "manifest.json"):
        raise ValueError(f"unsafe or reserved archive name: {name!r}")
    return relative


def _named(items) -> dict:
    result = {}
    for name, *values in items:
        if not name or name in result:
            raise ValueError(f"empty or duplicate name: {name!r}")
        result[name] = values
    return result


def collect(output: Path, artifacts, receipt_path: Path | None = None) -> dict:
    """Create a fresh archive; its manifest describes the copied bytes only."""
    expected = {}
    artifacts = list(artifacts)
    if receipt_path is not None:
        text, fingerprint = _read(receipt_path)
        document = _json(text)
        if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
                or document["schema_version"] != SCHEMA_VERSION):
            raise ValueError("unsupported receipt schema_version")
        if set(document) != {"schema_version", "analyzer_version", "head", "tree", "worktree",
                             "current_worktree_status", "jobs", "assay_verdicts", "progress"}:
            raise ValueError("malformed receipt fields")
        if (any(not isinstance(document[key], str) or not document[key] for key in
                ("head", "tree", "analyzer_version", "worktree"))
                or any(not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", document[key])
                       for key in ("head", "tree"))):
            raise ValueError("malformed receipt Git identity or provenance")
        if (document["current_worktree_status"] != "clean"
                or any(not isinstance(document[key], dict) for key in ("jobs", "assay_verdicts", "progress"))
                or not any(document[key] for key in ("jobs", "assay_verdicts", "progress"))):
            raise ValueError("malformed or empty receipt input graph")
        expected["receipt.json"] = fingerprint
        for name, job in document["jobs"].items():
            for suffix, item in job["files"].items():
                expected[f"jobs/{name}/{suffix}"] = item
        for field in ("assay_verdicts", "progress"):
            for name, item in document[field].items():
                expected[f"{field}/{name}"] = item["artifact"]
        for name, item in expected.items():
            if (not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}
                    or not isinstance(item["path"], str) or not item["path"]
                    or not isinstance(item["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or type(item["bytes"]) is not int or item["bytes"] < 0):
                raise ValueError(f"malformed receipt fingerprint: {name}")
            artifacts.append((name, item["path"]))
    selected = _named(artifacts)
    if not selected:
        raise ValueError("at least one artifact is required")
    paths = {}
    for name, (source,) in selected.items():
        _relative(name)
        paths[name] = Path(source).resolve(strict=True)
        if not paths[name].is_file():
            raise ValueError(f"artifact is not a regular file: {source}")
    names = set(paths)
    for name in paths:
        if any(parent.as_posix() in names for parent in PurePosixPath(name).parents):
            raise ValueError(f"file/directory archive collision: {name!r}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Existing archives are never refreshed or overwritten.
    try:
        manifest = {"schema_version": SCHEMA_VERSION, "artifacts": {}}
        for name, source in paths.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            digest = _digest(target)
            if name in expected and digest != {key: expected[name][key] for key in ("sha256", "bytes")}:
                raise ValueError(f"artifact changed since receipt inspection: {name}")
            manifest["artifacts"][name] = {"source": str(source), **digest}
        _write_new(output / "manifest.json", manifest)
    except BaseException:
        shutil.rmtree(output)
        raise
    return manifest


def check_archive(directory: Path) -> dict:
    """Check a relocated archive against its manifest, never original sources."""
    directory = directory.resolve(strict=True)
    text, artifact = _read(directory / "manifest.json")
    manifest = _json(text)
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "artifacts"}:
        raise ValueError("malformed archive manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported archive schema_version")
    entries = manifest["artifacts"]
    if not isinstance(entries, dict) or not entries:
        raise ValueError("archive must contain named artifacts")
    for name, entry in entries.items():
        relative = _relative(name)
        path = directory.joinpath(*relative.parts)
        if not path.resolve(strict=True).is_relative_to(directory):
            raise ValueError(f"artifact escapes archive: {name}")
        if not isinstance(entry, dict) or set(entry) != {"source", "sha256", "bytes"}:
            raise ValueError(f"malformed manifest entry: {name}")
        if (not isinstance(entry["source"], str) or not entry["source"]
                or not isinstance(entry["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or type(entry["bytes"]) is not int or entry["bytes"] < 0):
            raise ValueError(f"malformed artifact fingerprint: {name}")
        if _digest(path) != {key: entry[key] for key in ("sha256", "bytes")}:
            raise ValueError(f"artifact integrity mismatch: {name}")
    paths = list(directory.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise ValueError("collected archives must not contain symlinks")
    actual = {p.relative_to(directory).as_posix() for p in paths
              if p.is_file() and p != directory / "manifest.json"}
    if actual != set(entries):
        raise ValueError("archive has unexpected or missing files")
    return {"schema_version": SCHEMA_VERSION, "validation": "archive-byte-integrity",
            "artifact_count": len(entries), "manifest": artifact}


def _git(worktree: Path, *args: str) -> str:
    return git.run(worktree, *args)


def _status(worktree: Path) -> str:
    paths = git.dirty_paths(worktree)
    return "".join(path + "\n" for path in paths)


def _output_location(root: Path, output: Path, *, directory: bool = False) -> None:
    output = output.resolve()
    if output.is_relative_to(root):
        relative = output.relative_to(root).as_posix()
        tracked = _git(root, "ls-files", "--cached", "-z", "--", relative)
        targets = ([relative + "/record.json"] + [relative + "/job" + suffix for suffix in
                   (".identity", ".final-identity", ".status", ".final-status", ".log")]
                   if directory else [relative])
        origins = [git.ignore_rule_source(root, target) for target in targets]
        if any(origin is None for origin in origins) or tracked:
            raise ValueError("output inside the worktree must be untracked and git-ignored")
        if any(PurePosixPath(origin).is_absolute() or ".." in PurePosixPath(origin).parts
               or (origin != ".gitignore" and not origin.endswith("/.gitignore")) for origin in origins):
            raise ValueError("output must be ignored by repository .gitignore, not personal excludes")


def _identity(worktree: Path, expected: str) -> tuple[str, str]:
    head, tree = _git(worktree, "rev-parse", "HEAD", "HEAD^{tree}").splitlines()
    if head != expected:
        raise ValueError(f"current HEAD {head} differs from expected HEAD {expected}")
    if _status(worktree):
        raise ValueError("worktree is dirty; cannot bind a current-tree receipt")
    return head, tree


def _marker(text: str, marker: str) -> int:
    if not marker or "\n" in marker or "\r" in marker or "=" in marker:
        raise ValueError("job marker must be a nonempty single-line name without '='")
    lines = text.splitlines()
    matches = [re.fullmatch(re.escape(marker) + r"=(-?\d+)", line) for line in lines]
    found = [match for match in matches if match is not None]
    if len(found) != 1 or not lines or matches[-1] is None:
        raise ValueError(f"log must end with exactly one {marker}=<job exit> line")
    return int(found[0].group(1))


def _recorded(prefix: Path, marker: str, head: str, tree: str) -> dict:
    files = {suffix: Path(str(prefix) + suffix) for suffix in
             (".identity", ".final-identity", ".status", ".final-status", ".log")}
    expected = head + "\n" + tree + "\n"
    contents = {suffix: _read(path, errors="backslashreplace" if suffix == ".log" else "strict")
                for suffix, path in files.items()}
    for suffix in (".identity", ".final-identity"):
        if contents[suffix][0] != expected:
            raise ValueError(f"stale or mismatched Git identity: {files[suffix]}")
    for suffix in (".status", ".final-status"):
        if contents[suffix][0]:
            raise ValueError(f"recorded worktree was dirty: {files[suffix]}")
    log = contents[".log"][0]
    first = log.splitlines()[0] if log else ""
    if not first.startswith("COMMAND="):
        raise ValueError("recorded log must begin with COMMAND=<JSON argv>")
    command = _json(first.removeprefix("COMMAND="))
    if (not isinstance(command, list) or not command or not command[0]
            or any(not isinstance(arg, str) for arg in command)):
        raise ValueError("COMMAND must be a JSON string argv with a nonempty executable")
    return {"kind": "recorded", "command": command,
            "job_exit": _marker(log, marker),
            "files": {suffix: data[1] for suffix, data in contents.items()}}


def _tester_run(directory: Path, head: str) -> dict:
    files = ("launch.txt", "container.inspect.json", "docker-wait.exit",
             "container.log", "launch-memory-pressure.txt")
    contents = {name: _read(directory / name,
                            errors="backslashreplace" if name == "container.log" else "strict")
                for name in files}
    launch = {}
    for line in contents["launch.txt"][0].splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in launch:
            raise ValueError("malformed or duplicate launcher metadata")
        launch[key] = value
    if launch["git_head"] != head:
        raise ValueError("tester launch names a different HEAD")
    inspect = _json(contents["container.inspect.json"][0])
    if not isinstance(inspect, list) or len(inspect) != 1 or not isinstance(inspect[0], dict):
        raise ValueError("tester inspect must contain exactly one Docker object")
    container = inspect[0]
    comparisons = ((container["Id"], launch["container_id"]),
                   (container["Name"].removeprefix("/"), launch["container_name"]),
                   (container["Config"]["User"], launch["user"]),
                   (container["Config"]["WorkingDir"], launch["workdir"]),
                   (container["HostConfig"]["CgroupParent"], launch["cgroup_parent"]))
    if any(actual != declared for actual, declared in comparisons):
        raise ValueError("Docker inspect differs from launcher metadata")
    if "network_mode" in launch and container["HostConfig"]["NetworkMode"] != launch["network_mode"]:
        raise ValueError("Docker network mode differs from launcher metadata")
    nano_cpus = container["HostConfig"]["NanoCpus"]
    if type(nano_cpus) is not int or str(nano_cpus) != launch["nano_cpus"]:
        raise ValueError("Docker CPU value differs from launcher metadata")
    wait = contents["docker-wait.exit"][0].strip()
    if not re.fullmatch(r"\d+", wait):
        raise ValueError("Docker wait must contain one numeric job status")
    log = contents["container.log"][0]
    if _marker(log, "TESTER_UNIFIED_JOB_EXIT") != int(wait):
        raise ValueError("container job marker differs from Docker wait status")
    return {"kind": "tester-unified", "job_exit": int(wait), "launch": launch,
            "inspect_phase": "launcher-pre-wait",  # Not a final/OOM inspect.
            "launch_memory_pressure": contents["launch-memory-pressure.txt"][0],
            "files": {name: data[1] for name, data in contents.items()}}


def inspect_verdict(path: Path, expected: str) -> dict:
    text, artifact = _read(path)
    document = _json(text)
    failures = verify_text(text)
    if failures:
        raise ValueError("invalid Assay verdict: " + "; ".join(failures))
    integrity = document.get("worktree_integrity") or {}
    overridden = integrity.get(
        "overridden_dirty_paths", []
    )
    if overridden:
        raise ValueError(
            "verdict records --allow-dirty overrides; release receipts refuse "
            f"these uncommitted paths by default: {', '.join(overridden)}"
        )
    if document["commit"] != expected:
        raise ValueError(f"verdict commit {document['commit']} differs from {expected}")
    return {"validation": "schema-and-internal-consistency-plus-expected-commit",
            "artifact": artifact,
            "summary": {key: document[key] for key in
                        ("lane", "commit", "assay_version", "outcome", "exit_code",
                         "started", "ended", "declared_rigor")},
            "verdict": document}


def inspect_progress(path: Path, expected: str) -> dict:
    """Segment appended JSONL by run; retain explicit facts, including partial runs."""
    text, artifact = _read(path)
    runs = []
    current = None
    for line_number, line in enumerate(text.splitlines(), 1):
        event = _json(line)
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            raise ValueError(f"malformed progress event at line {line_number}")
        if event["event"] == "run":
            if not isinstance(event.get("commit"), str) or not event["commit"]:
                raise ValueError(f"run lacks commit at line {line_number}")
            current = {"line": line_number, "run": event, "event_counts": {}, "milestones": []}
            if event["commit"] == expected:
                runs.append(current)
        elif current is None:
            raise ValueError("progress event precedes first run identity")
        if "commit" in event and event["commit"] != current["run"]["commit"]:
            raise ValueError(f"progress event has conflicting commit at line {line_number}")
        name = event["event"]
        current["event_counts"][name] = current["event_counts"].get(name, 0) + 1
        if name in ("candidates", "resume", "end", "verdict_written"):
            current["milestones"].append({"line": line_number, "event": event})
    if not runs:
        raise ValueError(f"progress has no run at expected commit {expected}")
    return {"schema_version": SCHEMA_VERSION, "validation": "progress-framing-and-expected-commit",
            "artifact": artifact, "runs": runs}


# Report limits are diagnostic policy, independent of verdict/receipt validation.
_REPORT_WINDOW = 64 * 1024
_REPORT_RECORD_LIMIT = 1024 * 1024
_REPORT_LINE_LIMIT = 512
_REPORT_FRESH_SECONDS = 120
_REPORT_JSON_DEPTH_LIMIT = 512
_REPORT_EXITS = {"pass": 0, "running": 3, "fail": 1, "evidence_error": 2}


class _ReportJSONFramer:
    """Bounded syntax check for an oversized, unterminated JSONL record."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.stack = []
        self.root_done = False
        self.mode = "normal"
        self.string_role = None
        self.unicode_left = 0
        self.literal = None
        self.literal_index = 0
        self.number_state = None
        self.state = "incomplete"

    def _mark_value_started(self):
        if self.stack:
            self.stack[-1]["state"] = "comma_or_end"
        else:
            self.root_done = True

    def _start_value(self, char):
        if char == '"':
            self._mark_value_started()
            self.mode = "string"
            self.string_role = "value"
        elif char in "{[":
            self._mark_value_started()
            if len(self.stack) >= _REPORT_JSON_DEPTH_LIMIT:
                self.state = "indeterminate"
                return
            self.stack.append({"kind": "object" if char == "{" else "array",
                               "state": "key_or_end" if char == "{" else "value_or_end"})
        elif char in "tfn":
            self._mark_value_started()
            self.mode = "literal"
            self.literal = {"t": "true", "f": "false", "n": "null"}[char]
            self.literal_index = 1
            if self.literal_index == len(self.literal):
                self.mode = "normal"
        elif char == "-":
            self._mark_value_started()
            self.mode = "number"
            self.number_state = "minus"
        elif char == "0":
            self._mark_value_started()
            self.mode = "number"
            self.number_state = "zero"
        elif "1" <= char <= "9":
            self._mark_value_started()
            self.mode = "number"
            self.number_state = "integer"
        else:
            self.state = "invalid"

    def _number_char(self, char):
        state = self.number_state
        if state == "minus":
            if char == "0":
                self.number_state = "zero"
            elif "1" <= char <= "9":
                self.number_state = "integer"
            else:
                self.state = "invalid"
        elif state == "zero":
            if char == ".":
                self.number_state = "dot"
            elif char in "eE":
                self.number_state = "exponent"
            elif char.isdigit():
                self.state = "invalid"
            elif char in " \t\r\n,]}":
                self.mode = "normal"
                self._char(char)
            else:
                self.state = "invalid"
        elif state == "integer":
            if "0" <= char <= "9":
                return
            if char == ".":
                self.number_state = "dot"
            elif char in "eE":
                self.number_state = "exponent"
            elif char in " \t\r\n,]}":
                self.mode = "normal"
                self._char(char)
            else:
                self.state = "invalid"
        elif state == "dot":
            if "0" <= char <= "9":
                self.number_state = "fraction"
            else:
                self.state = "invalid"
        elif state == "fraction":
            if "0" <= char <= "9":
                return
            if char in "eE":
                self.number_state = "exponent"
            elif char in " \t\r\n,]}":
                self.mode = "normal"
                self._char(char)
            else:
                self.state = "invalid"
        elif state == "exponent":
            if char in "+-":
                self.number_state = "exponent_sign"
            elif "0" <= char <= "9":
                self.number_state = "exponent_digits"
            else:
                self.state = "invalid"
        elif state == "exponent_sign":
            if "0" <= char <= "9":
                self.number_state = "exponent_digits"
            else:
                self.state = "invalid"
        elif state == "exponent_digits":
            if "0" <= char <= "9":
                return
            if char in " \t\r\n,]}":
                self.mode = "normal"
                self._char(char)
            else:
                self.state = "invalid"

    def _close_container(self, char):
        if not self.stack:
            self.state = "invalid"
            return
        top = self.stack[-1]
        valid_state = ("key_or_end", "comma_or_end") if top["kind"] == "object" else (
            "value_or_end", "comma_or_end")
        expected = "}" if top["kind"] == "object" else "]"
        if char != expected or top["state"] not in valid_state:
            self.state = "invalid"
            return
        self.stack.pop()

    def _char(self, char):
        if self.state in ("invalid", "indeterminate"):
            return
        if self.mode == "string":
            if char == '"':
                self.mode = "normal"
                if self.string_role == "key":
                    self.stack[-1]["state"] = "colon"
                self.string_role = None
            elif char == "\\":
                self.mode = "escape"
            elif ord(char) < 0x20:
                self.state = "invalid"
            return
        if self.mode == "escape":
            if char == "u":
                self.mode = "unicode"
                self.unicode_left = 4
            elif char in '"\\/bfnrt':
                self.mode = "string"
            else:
                self.state = "invalid"
            return
        if self.mode == "unicode":
            if char not in "0123456789abcdefABCDEF":
                self.state = "invalid"
                return
            self.unicode_left -= 1
            if self.unicode_left == 0:
                self.mode = "string"
            return
        if self.mode == "literal":
            if char != self.literal[self.literal_index]:
                self.state = "invalid"
                return
            self.literal_index += 1
            if self.literal_index == len(self.literal):
                self.mode = "normal"
            return
        if self.mode == "number":
            self._number_char(char)
            return
        if char in " \t\r\n":
            return
        if not self.stack:
            if self.root_done:
                self.state = "invalid"
            else:
                self._start_value(char)
            return
        top = self.stack[-1]
        state = top["state"]
        if top["kind"] == "object":
            if state == "key_or_end" and char == "}":
                self._close_container(char)
            elif state in ("key_or_end", "key") and char == '"':
                self.mode = "string"
                self.string_role = "key"
            elif state == "colon" and char == ":":
                top["state"] = "value"
            elif state == "value":
                self._start_value(char)
            elif state == "comma_or_end" and char in ",}":
                if char == ",":
                    top["state"] = "key"
                else:
                    self._close_container(char)
            else:
                self.state = "invalid"
        elif state == "value_or_end" and char == "]":
            self._close_container(char)
        elif state in ("value_or_end", "value"):
            self._start_value(char)
        elif state == "comma_or_end" and char in ",]":
            if char == ",":
                top["state"] = "value"
            else:
                self._close_container(char)
        else:
            self.state = "invalid"

    def feed(self, raw):
        if self.state in ("invalid", "indeterminate"):
            return
        try:
            text = self.decoder.decode(raw, final=False)
        except UnicodeDecodeError:
            self.state = "invalid"
            return
        for char in text:
            self._char(char)
            if self.state in ("invalid", "indeterminate"):
                break

    def finish(self):
        if self.state in ("invalid", "indeterminate"):
            return self.state
        try:
            remainder = self.decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            return "invalid"
        for char in remainder:
            self._char(char)
        if self.state in ("invalid", "indeterminate"):
            return self.state
        if self.mode == "number" and self.number_state in (
                "zero", "integer", "fraction", "exponent_digits"):
            self.mode = "normal"
        if self.mode != "normal" or self.stack or not self.root_done:
            return "incomplete"
        return "complete"


def _report_commit(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
        raise argparse.ArgumentTypeError("expected commit must be full lowercase 40- or 64-hex")
    return value


def _report_max_errors(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("max-errors must be an integer from 0 to 10") from None
    if not 0 <= number <= 10:
        raise argparse.ArgumentTypeError("max-errors must be an integer from 0 to 10")
    return number


def _report_now() -> datetime:
    """Observe freshness after the progress snapshot has been read."""
    return datetime.now(UTC)


def _report_diagnostic(errors: dict, source: str, message: str, limit: int) -> None:
    # One record is one displayed line, even when a child emits control bytes.
    message = json.dumps(message, ensure_ascii=True)[1:-1]
    errors["count"] += 1
    if len(message) > _REPORT_LINE_LIMIT:
        errors["truncated"] = True
        message = message[:_REPORT_LINE_LIMIT - 3] + "..."
    if len(errors["records"]) < limit:
        errors["records"].append({"source": source, "message": message})
    else:
        errors["truncated"] = True


def _report_blocks(path: Path, artifact: dict):
    """Hash and consume the same bounded snapshot, including malformed inputs."""
    artifact["path"] = str(path.resolve())
    # Nonblocking open plus fstat also refuses a FIFO swapped in after a stat.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("input is not a regular file")
        remaining = info.st_size
        digest = hashlib.sha256()
        size = 0
        while remaining:
            block = stream.read(min(_REPORT_WINDOW, remaining))
            if not block:
                raise ValueError("input was truncated during report snapshot")
            remaining -= len(block)
            size += len(block)
            digest.update(block)
            yield block
        artifact.update(sha256=digest.hexdigest(), bytes=size)


def _report_verdict(path: Path, artifact: dict, lane: dict) -> None:
    raw = b"".join(_report_blocks(path, artifact))
    text = raw.decode("utf-8")
    document = _json(text)
    if not isinstance(document, dict):
        raise TypeError("Assay verdict must be a JSON object")
    if isinstance(document.get("commit"), str) and re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", document["commit"]):
        lane["actual_commit"] = document["commit"]
    failures = verify_text(text)
    if failures:
        raise ValueError("invalid Assay verdict: " + "; ".join(failures))
    for key in ("outcome", "exit_code", "reason_code"):
        lane[key] = document.get(key)
    lane["overridden_dirty_paths"] = (document.get("worktree_integrity") or {}).get(
        "overridden_dirty_paths", [])
    # References remain labels: never resolve/open an implicitly named artifact.
    def references(value, field=""):
        if isinstance(value, dict):
            for key, child in sorted(value.items()):
                location = f"{field}.{key}" if field else key
                if (key == "artifact" or key.endswith("_artifact")) and isinstance(child, str):
                    lane["evidence_artifacts"].append({"field": location, "path": child})
                else:
                    references(child, location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                references(child, f"{field}[{index}]")
    references(document)
    if document["lane"] != lane["name"]:
        raise ValueError(f"verdict lane {document['lane']!r} differs from {lane['name']!r}")
    if document["commit"] != lane["expected_commit"]:
        raise ValueError(f"verdict commit {document['commit']} differs from {lane['expected_commit']}")
    lane["status"] = "pass" if document["outcome"] == "PASS" and document["exit_code"] == 0 else "fail"


def _report_progress(path: Path, artifact: dict, lane: dict) -> None:
    from .mutation import PROGRESS_EVENTS

    progress = {"actual_commit": None, "run_line": None, "latest_event": None,
                "latest_phase": None, "emitted_at": None, "freshness": "unknown",
                "event_counts": {}, "candidate_counts": {}, "terminal": False,
                "torn_final_record": False, "details_truncated": False}
    lane["progress"] = progress
    failure = None
    oversized_pending = False
    oversized_framer = None
    line_number = 0
    pending = bytearray()
    count_keys = ("candidate_total", "candidate_index", "selected_total", "pending_total",
                  "resumed_total", "rejected_total", "rejudged_total")

    def consume(raw, terminated):
        nonlocal line_number
        line_number += 1
        try:
            event = _json(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            if not terminated:
                progress["torn_final_record"] = True
                return
            raise ValueError(f"malformed progress JSON at line {line_number}: {exc}") from exc
        if not isinstance(event, dict) or event.get("event") not in PROGRESS_EVENTS:
            raise ValueError(f"malformed progress event at line {line_number}")
        name = event["event"]
        if name == "run":
            commit = event.get("commit")
            if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
                raise ValueError(f"run lacks a full commit at line {line_number}")
            if event.get("lane") != lane["name"]:
                raise ValueError(f"run lane {event.get('lane')!r} differs from {lane['name']!r}")
            progress.update(actual_commit=commit, run_line=line_number, latest_event=None,
                            latest_phase=None, emitted_at=None, event_counts={},
                            candidate_counts={}, terminal=False)
        if progress["run_line"] is None:
            raise ValueError("progress event precedes first run identity")
        if "lane" in event and event["lane"] != lane["name"]:
            raise ValueError(f"progress lane {event['lane']!r} differs from {lane['name']!r}")
        if "commit" in event and event["commit"] != progress["actual_commit"]:
            raise ValueError(f"progress event has conflicting commit at line {line_number}")
        if "phase" in event and not isinstance(event["phase"], str):
            raise ValueError(f"malformed progress phase at line {line_number}")
        counts = progress["candidate_counts"]
        for key in count_keys:
            if key in event and event[key] is not None:
                if type(event[key]) is not int or event[key] < 0:
                    raise ValueError(f"malformed progress count {key} at line {line_number}")
                counts[key] = event[key]
        progress["event_counts"][name] = progress["event_counts"].get(name, 0) + 1
        progress["latest_event"] = name
        phase = event.get("phase")
        if isinstance(phase, str) and len(phase) > _REPORT_LINE_LIMIT:
            progress["latest_phase"] = phase[:_REPORT_LINE_LIMIT - 3] + "..."
            progress["details_truncated"] = True
        else:
            progress["latest_phase"] = phase
        emitted_at = event.get("emitted_at")
        if isinstance(emitted_at, str) and len(emitted_at) > _REPORT_LINE_LIMIT:
            progress["emitted_at"] = emitted_at[:_REPORT_LINE_LIMIT - 3] + "..."
            progress["details_truncated"] = True
        else:
            progress["emitted_at"] = emitted_at if isinstance(emitted_at, str) else None
        progress["terminal"] |= name in ("verdict_written", "end")

    # Stop parsing on the first invalid complete record, but finish hashing.
    for block in _report_blocks(path, artifact):
        if failure is not None:
            continue
        if oversized_pending:
            if b"\n" in block:
                failure = ValueError("progress record exceeds 1 MiB")
            else:
                oversized_framer.feed(block)
            continue
        records = (bytes(pending) + block).split(b"\n")
        pending = bytearray(records.pop())
        for raw in records:
            try:
                if len(raw) > _REPORT_RECORD_LIMIT:
                    raise ValueError("progress record exceeds 1 MiB")
                consume(raw, True)
            except (ValueError, TypeError) as exc:
                failure = exc
                pending.clear()
                break
        if failure is None and len(pending) > _REPORT_RECORD_LIMIT:
            oversized_framer = _ReportJSONFramer()
            oversized_framer.feed(pending)
            pending.clear()
            oversized_pending = True
    if failure is None and oversized_pending:
        framing = oversized_framer.finish()
        if framing in ("complete", "indeterminate"):
            failure = ValueError("progress record exceeds 1 MiB and is complete or cannot be classified")
        else:
            progress["torn_final_record"] = True
    elif failure is None and pending:
        try:
            consume(pending, False)
        except (ValueError, TypeError) as exc:
            failure = exc
    if lane["actual_commit"] is None:
        lane["actual_commit"] = progress["actual_commit"]
    if failure is not None:
        raise failure
    if progress["run_line"] is None:
        raise ValueError("progress has no complete run header")
    if progress["actual_commit"] != lane["expected_commit"]:
        raise ValueError(f"latest progress commit {progress['actual_commit']} differs from {lane['expected_commit']}")
    now = _report_now()
    try:
        emitted = datetime.fromisoformat(progress["emitted_at"])
        if emitted.tzinfo is not None:
            age = (now - emitted).total_seconds()
            progress["freshness"] = "fresh" if 0 <= age <= _REPORT_FRESH_SECONDS else (
                "future" if age < 0 else "stale")
    except (ValueError, TypeError, OverflowError):
        pass


def _report_log(path: Path, artifact: dict, lane: dict, limit: int) -> None:
    tail = bytearray()
    boundary_byte = None
    for block in _report_blocks(path, artifact):
        combined = tail + block
        if len(combined) > _REPORT_WINDOW:
            removed = len(combined) - _REPORT_WINDOW
            boundary_byte = combined[removed - 1]
            tail = bytearray(combined[removed:])
        else:
            tail = combined
    omitted = artifact["bytes"] > len(tail)
    lane["errors"]["truncated"] |= omitted
    if omitted:  # Keep a complete first line when the byte window starts at a boundary.
        at_line_boundary = boundary_byte == 10 or boundary_byte == 13
        if boundary_byte == 13 and tail.startswith(b"\n"):
            del tail[:1]
        if not at_line_boundary:
            separators = [index for index in (tail.find(b"\n"), tail.find(b"\r")) if index >= 0]
            if separators:
                _, separator, remainder = tail.partition(tail[min(separators):min(separators) + 1])
                tail = remainder
                if separator == b"\r" and tail.startswith(b"\n"):
                    del tail[:1]
            else:
                tail.clear()
    selected = deque(maxlen=limit)
    count = 0
    phase = None
    for line in tail.decode("utf-8", errors="backslashreplace").splitlines():
        if line.startswith("ASSAY_GATE_PHASE="):
            phase_value = line.removeprefix("ASSAY_GATE_PHASE=")
            phase = phase_value[:_REPORT_LINE_LIMIT - 3] + "..." if len(phase_value) > _REPORT_LINE_LIMIT else phase_value
            lane["errors"]["truncated"] |= len(phase_value) > _REPORT_LINE_LIMIT
        if re.search(r"error|failed|exception|traceback", line, re.IGNORECASE):
            count += 1
            selected.append(line)
    if lane["latest_phase"] is None and phase is not None:
        lane.update(latest_phase=phase, phase_source="log")
    lane["errors"]["count"] += count - len(selected)
    lane["errors"]["truncated"] |= count > len(selected)
    for line in selected:
        _report_diagnostic(lane["errors"], "log", line, limit)


def report(expected: str, verdicts, progress, logs, max_errors: int = 5) -> dict:
    """Read explicit evidence once; status comes only from bound structured facts."""
    _report_commit(expected)
    if type(max_errors) is not int or not 0 <= max_errors <= 10:
        raise ValueError("max-errors must be an integer from 0 to 10")
    inputs = {"verdict": _named(verdicts), "progress": _named(progress), "log": _named(logs)}
    names = sorted(set().union(*inputs.values()))
    if not names:
        raise ValueError("report requires at least one explicit input")
    result = {"schema_version": SCHEMA_VERSION, "expected_commit": expected, "exit_code": 0, "lanes": []}
    for name in names:
        lane = {"name": name, "status": "evidence_error", "expected_commit": expected,
                "actual_commit": None, "outcome": None, "exit_code": None, "reason_code": None,
                "progress": None, "latest_phase": None, "phase_source": None,
                "overridden_dirty_paths": [], "evidence_artifacts": [], "inputs": {},
                "errors": {"records": [], "count": 0, "truncated": False}}
        invalid = False
        for kind, selected in inputs.items():
            if name not in selected:
                continue
            path = Path(selected[name][0])
            artifact = {"path": str(path.absolute()), "sha256": None, "bytes": None}
            lane["inputs"][kind] = artifact
            try:
                if kind == "verdict":
                    _report_verdict(path, artifact, lane)
                elif kind == "progress":
                    _report_progress(path, artifact, lane)
                    lane["latest_phase"] = lane["progress"]["latest_phase"]
                    if lane["latest_phase"] is not None:
                        lane["phase_source"] = "progress"
                else:
                    _report_log(path, artifact, lane, max_errors)
            except (OSError, ValueError, RecursionError, KeyError, TypeError) as exc:
                invalid = True
                _report_diagnostic(lane["errors"], kind, f"{artifact['path']}: {exc}", max_errors)
        if invalid:
            lane["status"] = "evidence_error"
        elif name not in inputs["verdict"]:
            stream = lane["progress"]
            if stream and not stream["terminal"] and stream["freshness"] == "fresh":
                lane["status"] = "running"
            else:
                message = ("terminal progress has no supplied verdict" if stream and stream["terminal"]
                           else f"progress freshness is {stream['freshness']}" if stream
                           else "no verdict or progress facts supplied")
                _report_diagnostic(lane["errors"], "evidence", message, max_errors)
        result["lanes"].append(lane)
    priority = max(("pass", "running", "fail", "evidence_error").index(lane["status"])
                   for lane in result["lanes"])
    result["exit_code"] = _REPORT_EXITS[("pass", "running", "fail", "evidence_error")[priority]]
    return result


def _report_text(result: dict, stdout: TextIO) -> None:
    # JSON quoting keeps untrusted path/phase characters on their own line.
    def display(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    for lane in result["lanes"]:
        print(f"{display(lane['name'])}: {lane['status']} expected={lane['expected_commit']} "
              f"actual={display(lane['actual_commit'])} outcome={display(lane['outcome'])} "
              f"exit={display(lane['exit_code'])} reason={display(lane['reason_code'])}", file=stdout)
        for kind, artifact in lane["inputs"].items():
            print(f"  {kind}: {display(artifact['path'])} bytes={artifact['bytes']} "
                  f"sha256={artifact['sha256']}", file=stdout)
        if lane["progress"] is not None:
            print("  progress facts: " + display(lane["progress"]), file=stdout)
        if lane["latest_phase"] is not None:
            print(f"  phase ({lane['phase_source']}): {display(lane['latest_phase'])}", file=stdout)
        for artifact in lane["evidence_artifacts"]:
            print(f"  evidence {artifact['field']}: {display(artifact['path'])}", file=stdout)
        if lane["overridden_dirty_paths"]:
            print("  overridden dirty paths: " + display(lane["overridden_dirty_paths"]), file=stdout)
        errors = lane["errors"]
        print(f"  diagnostics: count={errors['count']} shown={len(errors['records'])} "
              f"truncated={str(errors['truncated']).lower()}", file=stdout)
        for record in errors["records"]:
            print(f"  {record['source']} diagnostic: {record['message']}", file=stdout)


def record(worktree: Path, expected: str, output: Path, command: list[str]) -> tuple[dict, int]:
    """Capture command evidence. A changed tree is recorded, never silently certified."""
    root = git.repo_top(worktree)
    head, tree = _identity(root, expected)
    _output_location(root, output, directory=True)
    if not command or not command[0] or any(not isinstance(arg, str) for arg in command):
        raise ValueError("record requires a command after --")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    prefix = output / "job"
    Path(str(prefix) + ".identity").write_text(head + "\n" + tree + "\n", encoding="utf-8")
    Path(str(prefix) + ".status").write_text("", encoding="utf-8")
    with Path(str(prefix) + ".log").open("wb") as log:
        log.write(("COMMAND=" + json.dumps(command) + "\n").encode("utf-8"))
        log.flush()
        result = subprocess.run(command, cwd=worktree, stdout=log, stderr=subprocess.STDOUT)
        log.write(f"\n{JOB_MARKER}={result.returncode}\n".encode("utf-8"))
    Path(str(prefix) + ".final-identity").write_text(
        _git(root, "rev-parse", "HEAD", "HEAD^{tree}"), encoding="utf-8")
    Path(str(prefix) + ".final-status").write_text(
        _status(root), encoding="utf-8")
    fact = {"schema_version": SCHEMA_VERSION, "command": command, "job_exit": result.returncode,
            "prefix": str(prefix.resolve()), "marker": JOB_MARKER}
    _write_new(output / "record.json", fact)
    return fact, result.returncode if result.returncode >= 0 else 128 - result.returncode


def receipt(worktree: Path, expected: str, recorded, tester_runs, verdicts, progress=()) -> dict:
    worktree = git.repo_top(worktree)
    head, tree = _identity(worktree, expected)
    selected = _named(recorded)
    testers = _named(tester_runs)
    if selected.keys() & testers.keys():
        raise ValueError("job names overlap between recorded and tester runs")
    artifacts = _named(verdicts)
    streams = _named(progress)
    if not selected and not testers and not artifacts and not streams:
        raise ValueError("at least one recorded job, tester run, verdict or progress stream is required")
    result = {"schema_version": SCHEMA_VERSION, "analyzer_version": __version__, "head": head, "tree": tree,
              "worktree": str(worktree), "current_worktree_status": "clean",
              "jobs": {}, "assay_verdicts": {}, "progress": {}}
    for name, (prefix, marker) in selected.items():
        result["jobs"][name] = _recorded(Path(prefix), marker, head, tree)
    for name, (directory,) in testers.items():
        result["jobs"][name] = _tester_run(Path(directory), head)
    for name, (path,) in artifacts.items():
        result["assay_verdicts"][name] = inspect_verdict(Path(path), head)
    for name, (path,) in streams.items():
        result["progress"][name] = inspect_progress(Path(path), head)
    if _identity(worktree, expected) != (head, tree):
        raise ValueError("Git identity changed while inspecting evidence")
    return result


def _write_new(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".assay-analysis-", delete=False) as stream:
        scratch = Path(stream.name)
        try:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        except BaseException:
            scratch.unlink()
            raise
    try:
        os.link(scratch, path)  # Publish completely, without overwriting any file.
    finally:
        scratch.unlink()


def build_analyze_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("analyze", help="collect and inspect existing review artifacts")
    commands = parser.add_subparsers(dest="analysis_command", required=True)
    archive = commands.add_parser("collect", help="create a fresh artifact archive and manifest")
    archive.add_argument("--output", type=Path, required=True)
    archive.add_argument("--artifact", metavar=("NAME", "FILE"), nargs=2, action="append", default=[])
    archive.add_argument("--receipt", type=Path, help="include a receipt and all its fingerprinted inputs")
    check = commands.add_parser("check", help="check a collected archive's byte integrity")
    check.add_argument("directory", type=Path)
    launcher = commands.add_parser("launcher", help="inspect existing tester-unified launch evidence")
    launcher.add_argument("directory", type=Path)
    launcher.add_argument("--expected-commit", required=True)
    producer = commands.add_parser("record", help="record one explicit command and its actual exit")
    producer.add_argument("--worktree", type=Path, required=True)
    producer.add_argument("--expected-head", required=True)
    producer.add_argument("--output", type=Path, required=True)
    producer.add_argument("argv", nargs=argparse.REMAINDER)
    bound = commands.add_parser("receipt", help="bind recorded facts to a current clean worktree")
    bound.add_argument("--worktree", type=Path, required=True)
    bound.add_argument("--expected-head", required=True)
    bound.add_argument("--recorded", metavar=("NAME", "PREFIX", "MARKER"), nargs=3, action="append", default=[])
    bound.add_argument("--tester-run", metavar=("NAME", "DIRECTORY"), nargs=2, action="append", default=[])
    bound.add_argument("--verdict", metavar=("NAME", "FILE"), nargs=2, action="append", default=[])
    bound.add_argument("--progress", metavar=("NAME", "FILE"), nargs=2, action="append", default=[])
    bound.add_argument("--output", type=Path, required=True)
    verdict = commands.add_parser("verdict", help="inspect an Assay verdict at an explicit commit")
    verdict.add_argument("path", type=Path)
    verdict.add_argument("--expected-commit", required=True)
    verdict.add_argument("--format", choices=("json", "text"), default="json")
    snapshot = commands.add_parser("report", help="bounded snapshot of explicit lane evidence")
    snapshot.add_argument("--expected-commit", type=_report_commit, required=True)
    for option in ("verdict", "progress", "log"):
        snapshot.add_argument("--" + option, metavar=("LANE", "FILE"), nargs=2,
                              action="append", default=[])
    snapshot.add_argument("--format", choices=("json", "text"), default="json")
    snapshot.add_argument("--max-errors", type=_report_max_errors, default=5)
    progress = commands.add_parser("progress", help="summarize matching runs in appended progress JSONL")
    progress.add_argument("path", type=Path)
    progress.add_argument("--expected-commit", required=True)


def cmd_analyze(args: argparse.Namespace, *, stdout: TextIO, stderr: TextIO) -> int:
    code = 0
    try:
        if args.analysis_command == "collect":
            result = collect(args.output, args.artifact, args.receipt)
        elif args.analysis_command == "check":
            result = check_archive(args.directory)
        elif args.analysis_command == "launcher":
            result = _tester_run(args.directory, args.expected_commit)
            result["validation"] = "launcher-record-consistency-and-expected-commit"
        elif args.analysis_command == "record":
            if args.argv[:1] != ["--"]:
                raise ValueError("record requires a command after --")
            command = args.argv[1:]
            result, code = record(args.worktree, args.expected_head, args.output, command)
        elif args.analysis_command == "verdict":
            result = inspect_verdict(args.path, args.expected_commit)
            if args.format == "text":
                fact = result["summary"]
                print(f"{fact['lane']}: {fact['outcome']} (recorded exit {fact['exit_code']}) "
                      f"at {fact['commit']}; Assay {fact['assay_version']}", file=stdout)
                for claim in result["verdict"]["claims"]:
                    print(f"  {claim['rigor']}: {claim['status']}" +
                          (f" / {claim['reason_code']}" if claim.get("reason_code") else ""), file=stdout)
                    if "coverage" in claim:
                        coverage = claim["coverage"]
                        branches = (f"branches {coverage['branches_covered']}/{coverage['branches_total']}"
                                    if {"branches_covered", "branches_total"} <= coverage.keys()
                                    else "branch counts absent")
                        print(f"    lines {coverage['covered']}/{coverage['executable']}; {branches}", file=stdout)
                    if "mutation" in claim:
                        mutation = claim["mutation"]
                        buckets = ", ".join(f"{key}={len(value)}" for key, value in mutation.items()
                                            if isinstance(value, list))
                        print(f"    mutants {mutation['total']}; {buckets}", file=stdout)
                if fact["outcome"] != "PASS":
                    for stream in ("stdout", "stderr"):
                        tail_key = f"result_{stream}_tail"
                        dropped_key = f"result_{stream}_dropped_bytes"
                        document = result["verdict"]
                        if tail_key in document:
                            print(f"  captured {stream} tail:", file=stdout)
                            print(document[tail_key], file=stdout)
                        if dropped_key in document:
                            print(f"  {stream} dropped bytes: {document[dropped_key]}", file=stdout)
                return 0
        elif args.analysis_command == "report":
            result = report(args.expected_commit, args.verdict, args.progress, args.log, args.max_errors)
            code = result["exit_code"]
            if args.format == "text":
                _report_text(result, stdout)
                return code
        elif args.analysis_command == "progress":
            result = inspect_progress(args.path, args.expected_commit)
        else:
            result = receipt(args.worktree, args.expected_head, args.recorded,
                             args.tester_run, args.verdict, args.progress)
            output = args.output.resolve()
            root = Path(result["worktree"])
            _output_location(root, output)
            _write_new(output, result)
    except (OSError, ValueError, RecursionError, KeyError, TypeError,
            AttributeError, AssayError, subprocess.CalledProcessError) as exc:
        print(f"assay analyze: {exc}", file=stderr)
        return 2 if args.analysis_command == "report" else 1
    print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    return code
