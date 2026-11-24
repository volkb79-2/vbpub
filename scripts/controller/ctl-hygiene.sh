#!/usr/bin/env bash
# Hygiene notifier for the controller's memory index, handoff files and backlogs (P-plat-22b part C;
# spec: docs/design/agctl/CONTEXT-ECONOMY.md section 5). Report-only.
#
# usage: ctl-hygiene.sh            (no arguments; run by ctl-doctor.sh, or by hand)
# Output: one line per finding, `HYGIENE <rule> <path>: <value> (threshold <t>)`, nothing when all is well.
# Always exits 0. It never moves, edits or creates a file; it reads files and runs one read-only `git ls-files`.
# "Today" is the UTC date. Rules (spec: CONTEXT-ECONOMY.md section 5, plus review item R2-4):
#   M1 MEMORY.md size       > 12288 B or > 70 lines (WARN); > 150 lines (ALERT; only the first 200 lines are loaded)
#   M2 stale marker         a line with IN PROGRESS / CURRENT / TEMPORARY whose first date is > 3 days old, or undated
#   M3 dead link            a ](<name>.md) target that is missing from the memory directory
#   H1 handoff blocks       more than 2 `## RESUME HERE #N` / `## UPDATE ` blocks in a handoff/state file
#   H2 handoff size         such a file larger than 24576 B
#   S1 stale successor      handovers/vbpub-<topic>-successor.md > 24 h older than <topic>/STATE.md; any other file there
#   B1 closed backlog       closed entries are > 30% of a tracked backlog's bytes (or the file's entries cannot be parsed)
# Test seams (all optional): HYGIENE_MEMORY_DIR (default ~/.claude/projects/-workspaces-vbpub/memory),
# HYGIENE_REPO (default: the repository of this script; B1 reads its tracked backlogs), HYGIENE_STATE_DIR (default: the
# .controller-state of the repository's MAIN checkout), HYGIENE_TODAY (YYYY-MM-DD).
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo=${HYGIENE_REPO:-$(cd -- "$SCRIPT_DIR/../.." && pwd -P)}
memory_dir=${HYGIENE_MEMORY_DIR:-${HOME:-}/.claude/projects/-workspaces-vbpub/memory}
state_dir=${HYGIENE_STATE_DIR:-}
if [[ -z $state_dir ]]; then
  # .controller-state is untracked and lives in the main checkout, not in a linked worktree.
  common=$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || common=''
  if [[ $common == */.git ]]; then state_dir=${common%/.git}/.controller-state; else state_dir=$repo/.controller-state; fi
fi
python3 -I - "$repo" "$memory_dir" "$state_dir" "${HYGIENE_TODAY:-}" <<'PY' || true
import datetime
import os
import re
import subprocess
import sys

repo, memory_dir, state_dir, today_arg = sys.argv[1:5]
today = datetime.datetime.now(datetime.timezone.utc).date()
try:
    today = datetime.date.fromisoformat(today_arg) if today_arg else today
except ValueError:
    print("HYGIENE error HYGIENE_TODAY: ValueError (threshold a YYYY-MM-DD date)")
state_name = os.path.basename(state_dir.rstrip("/")) or ".controller-state"
TOPICS = ("platform", "core", "assay", "hostobs", "edge", "rollback")


def finding(rule, where, value, threshold):
    print(f"HYGIENE {rule} {where}: {value} (threshold {threshold})")


def read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def state_rel(path):
    return state_name + "/" + os.path.relpath(path, state_dir)


def section(name):
    """Run one rule group; an exception becomes an `error` finding and never drops the groups after it."""
    def wrap(fn):
        try:
            fn()
        except Exception as exc:
            finding("error", name, type(exc).__name__, "no exception")
        return fn
    return wrap


# ---- M1, M2, M3: the memory index --------------------------------------------------------------------------------------
@section("M1-M3")
def memory_rules():
    memory = os.path.join(memory_dir, "MEMORY.md")
    data = read(memory)
    if data is not None:
        lines = data.decode("utf-8", errors="replace").splitlines()
        if len(data) > 12288:
            finding("M1", memory, f"{len(data)} B", "12288 B WARN")
        if len(lines) > 150:
            finding("M1", memory, f"{len(lines)} lines", "150 lines ALERT, Claude Code loads only the first 200")
        elif len(lines) > 70:
            finding("M1", memory, f"{len(lines)} lines", "70 lines WARN")
        marker = re.compile(r"IN PROGRESS|CURRENT|TEMPORARY")
        date = re.compile(r"\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b")
        link = re.compile(r"\]\(([^)/\s]+\.md)\)")
        for number, line in enumerate(lines, 1):
            if marker.search(line):
                found = date.search(line)
                try:
                    age = (today - datetime.date.fromisoformat(found.group(0))).days if found else None
                except ValueError:
                    age = None
                if age is None:
                    finding("M2", f"{memory}:{number}", "undated marker line", "a date within 3 days")
                elif age > 3:
                    finding("M2", f"{memory}:{number}", f"{found.group(0)} is {age} days old", "3 days")
            for target in link.findall(line):
                if not os.path.isfile(os.path.join(memory_dir, target)):
                    finding("M3", f"{memory}:{number}", f"dead link {target}", "the target exists in the memory directory")

# ---- H1, H2: handoff and state files -----------------------------------------------------------------------------------
@section("H1-H2")
def handoff_rules():
    files = [os.path.join(state_dir, "HANDOFF.md")]
    files += [os.path.join(state_dir, topic, "HANDOFF.md") for topic in ("platform", "assay")]
    files.append(os.path.join(state_dir, "edge", "HANDOVER.md"))
    files += [os.path.join(state_dir, topic, "STATE.md") for topic in TOPICS]
    block = re.compile(r"^## (RESUME HERE #[0-9]+|UPDATE )")
    for path in files:
        data = read(path)
        if data is None:
            continue
        blocks = sum(1 for line in data.decode("utf-8", errors="replace").splitlines() if block.match(line))
        parent = os.path.basename(os.path.dirname(path))
        topic = "main" if os.path.dirname(path) == state_dir.rstrip("/") else parent
        if blocks > 2:
            finding("H1", state_rel(path), f"{blocks} blocks",
                    f"2; archive the older ones to {state_name}/archive/{topic}-{today.isoformat()}/")
        if len(data) > 24576:
            finding("H2", state_rel(path), f"{len(data)} B", "24576 B")

# ---- S1: successor handovers -------------------------------------------------------------------------------------------
@section("S1")
def successor_rules():
    handovers = os.path.join(state_dir, "handovers")
    if os.path.isdir(handovers):
        programme = {f"vbpub-{topic}-successor.md": topic for topic in TOPICS}
        for name in sorted(os.listdir(handovers)):
            path = os.path.join(handovers, name)
            if not os.path.isfile(path):
                continue
            if name not in programme:
                finding("S1", state_rel(path), "not-a-programme-controller", "vbpub-<topic>-successor.md")
                continue
            state = os.path.join(state_dir, programme[name], "STATE.md")
            if os.path.isfile(state):
                behind = os.stat(state).st_mtime - os.stat(path).st_mtime
                if behind > 24 * 3600:
                    finding("S1", state_rel(path), f"{behind / 3600:.1f} h older than {state_rel(state)}", "24 h")

# ---- B1: closed backlog entries ----------------------------------------------------------------------------------------
@section("B1")
def backlog_rules():
    CLOSED = {"DONE", "FIXED", "CLOSED", "RESOLVED", "IMPLEMENTED", "SUPERSEDED", "WONTFIX"}
    # An entry heading carries an ID with at least one digit: CIU-104, CTL-01, B001, IOB-BL-40 (R2-4).
    heading = re.compile(r"^#{2,3} ([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\b")
    status = re.compile(r"^\s*(\*\*)?Status:?(\*\*)?:?\s*(\*\*)?([A-Z-]+)")
    try:
        listed = subprocess.run(
            ["git", "-C", repo, "ls-files", "-z", "--", "*KNOWN_ISSUES_TODO_BACKLOG.md", "*/nyxloom-trove/4-backlog.md"],
            capture_output=True, check=True).stdout.decode("utf-8", errors="replace").split("\0")
    except (OSError, subprocess.CalledProcessError):
        listed = []
    for rel in sorted(p for p in listed if p):
        data = read(os.path.join(repo, rel))
        if not data:
            continue
        entries = []   # [closed?, bytes, status resolved?]
        status_lines = 0
        for raw in data.splitlines(keepends=True):
            line = raw.decode("utf-8", errors="replace")
            match = heading.match(line)
            if match and any(ch.isdigit() for ch in match.group(1)):
                in_heading = {w for inner in re.findall(r"\(([^)]*)\)", line) for w in re.findall(r"[A-Z-]+", inner)}
                entries.append([bool(in_heading & CLOSED), 0, False])
            found = status.match(line)
            if found:
                status_lines += 1
                if entries and not entries[-1][2]:
                    entries[-1][2] = True
                    if found.group(4).strip("-") in CLOSED:
                        entries[-1][0] = True
            if entries:
                entries[-1][1] += len(raw)
        if not entries:
            if status_lines:
                finding("B1", rel, f"unparsed: 0 entries but {status_lines} Status lines", "at least 1 entry heading")
            continue
        closed = sum(size for is_closed, size, _ in entries if is_closed)
        if closed * 100 > 30 * len(data):
            count = sum(1 for e in entries if e[0])
            finding("B1", rel, f"{closed * 100 // len(data)}% of {len(data)} B in {count} closed of {len(entries)} entries", "30%")
PY
exit 0
