"""Archive review artifacts and inspect recorded facts without judging a release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import TextIO

from . import __version__
from . import git
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
        return 1
    print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    return code
