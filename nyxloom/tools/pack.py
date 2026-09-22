#!/usr/bin/env python3
"""Role-aware orientation-pack builder (model-free).

REPLACES `pack.sh` (deleted; §4.1 greenfield — no shim, no dual path). The pack is
the orientation payload itself: verbatim file content assembled by a script, so it
costs zero model tokens to produce and cannot hallucinate. Everything here is a
mechanisation of a MEASURED rule from the context-lifecycle experiment log
(`vbpub/nyxloom/docs/design-context-lifecycle-experiments.md`); the citations below
are load-bearing — do not "improve" a rule without a new measurement.

    pack.py build  --handoff <path> --role implementer|carver|reviewer
                   [--range main...<tip>] [--out-dir <dir>] [--extra <path[:a-b]>]...
                   [--slice <path:a-b>]... [--exclude <glob>]...
                   [--budget <tokens>] [--dry-run] [--force]
    pack.py delta  --handoff <path> --since <rev> [--role ...] [--out <file>]
    pack.py verify <out-dir>
    pack.py score  <out-dir> --transcript <jsonl>

WHY EACH READ-LIST RULE EXISTS
------------------------------
E-002 (REDEFINED) — "Read-list derivation (no model) … the list is data, produced by
  scripts."  The whole file is that script. Section headers are `=== <path> ===` /
  `=== <path>:<a>-<b> ===` because downstream tooling (`jsonl-metrics.py overlap`,
  `pack.py verify|score`) parses that prefix.

implementer / carver
  * handoff FULL — it is the contract; every oracle is verified against its text.
  * every `scope.touch` path FULL (E-002 addendum 4, "Gap 2 — slice-vs-edit
    mismatch": route-body slices sufficed for files being deleted, but every file
    being EDITED needed a full read anyway because the dead wiring lives in the
    module head; and addendum 6's "content right, bytes wrong" — Edit needs
    byte-exact CURRENT strings, so only a full-file entry at the exact
    input_revision can substitute for the pre-edit read). A directory in
    `scope.touch` is listed by NAME only plus a note — packing a whole tree is the
    dead-weight failure mode E-006 measured (75% of an implementer pack unread).
  * "Context to read first" items — FULL when the item names a bare file, SLICE when
    it names `path:a-b`, `path` + `lines a-b`, or `path` + `§N` (E-002 first live
    run: curation of 4 giants took a naive 265k-token pack down to 42k). `§N` / `## N`
    headings are resolved for markdown. `decisions.md` is NEVER packed FULL — it is
    a 3.5k-line append-only ledger; it enters only as D-section slices.
  * ledger `D-NNN` references anywhere in the handoff → the `## D-NNN` section slice
    from `nyxloom-trove/decisions.md` (E-006 conclusions: `decisions.md` is one of
    the recurring `read_set∖pack` files every role re-fetches by hand).
  * gate-adjacent artifacts ALWAYS: the `[gates.<g>]` block of `nyxloom.toml` for
    every declared gate, the `[lanes.<l>]` blocks of `assay.toml`, and any
    conftest/coverage-gate file an oracle names (E-002 addendum 5, "New
    pack-curation rule (gate-adjacent artifacts)": the pack must carry the gates the
    package can trip — all three misses there were audit findings).
  * `scope.forbid` → a names-only table, never content. The forbidden set must be
    KNOWN (an agent that edits a forbidden file fails review) but its content is by
    definition not the edit set, so packing it is pure dead weight.

reviewer (requires --range) — CONTROLLER-DEPRECATED 2026-08-26, operator
  directive: "no orientation prompt for the reviewer (proved not working
  well)." Measured live on dstdns-P135: even after E-006's own curation
  rules (below) plus manual `--exclude` of 3 generated artifacts, the pack
  still landed at 395k tokens for one mid-sized 24-file package — the
  role built specifically to fix over-inclusion (E-006 Task B) itself
  became the worst offender, because a reviewer's real subject (a diff)
  has no natural size ceiling the way an implementer's scope.touch does.
  REPLACEMENT: no pre-built pack — dispatch the reviewer with a lean
  prompt naming the handoff + diff range + prior LOG/REPORT directly, and
  a standing self-checkpoint instruction (write a BRIEF + a self-authored
  `/compact`-style retention prompt at major checkpoints; the controller
  respawns a fresh successor from that prompt rather than resuming the
  full transcript — "compact from outside"). See
  `nyxloom-trove/CONTROLLER-BRIEF.md` hard rules. The code below is left
  in place (not proven unusable for every case, e.g. a genuinely small
  diff) but is no longer the controller's default reviewer dispatch path.
  E-006 Task B measured 75-76% of an implementer-scoped pack unread by a code
  reviewer, and named the replacement: `{files changed in the diff} +
  {prior-round LOG/REPORT} + {the standing cross-reference set}`, comprehension
  slices dropped, consumer sweep PRE-TABULATED.
  * every file in `git diff --name-only <range>`: FULL at the TIP side (what is
    being reviewed is the post-state) plus a per-file unified diff section
    `=== diff <path> ===` (the reviewer's actual subject). Files deleted by the diff
    get the diff section only.
  * handoff FULL + the ledger D-sections it names (same rule as above).
  * prior-round `LOG.md`/`REPORT.md` for this package FULL — "every reviewer reads it
    to check claims against reality" (E-006 conclusions (a)).
  * standing set: `GUIDE.md` §2–§3 slices + the handoff's "Context to read first"
    items — but only those NOT already FULL from the diff (E-006: "with the
    implementer's comprehension-only slices dropped").
  * CONSUMER SWEEP, PRE-TABULATED: for every symbol deleted/renamed by the diff and
    every file it deletes, a `git grep -n` table across ALL tracked file types at
    the tip, emitted as one `=== sweep ===` section. E-002 addendum: all 10 blocking
    carve defects came from OUTSIDE the pack; addendum 4: all 13 consumer files were
    found by the implementer's own grep; dstdns D-128 #10: "a grep executed but not
    tabulated is an assertion, not a measurement."
  * (E-002 addendum 7 — the P113 measurement: both the implementer-shaped AND the
    reviewer-shaped pack missed the SAME 19 real-need files — gate scripts and
    security/contract tests adjacent to the diff, not reached by any rule above.
    Four rules close that gap, each named for its citation below.)
  * 7a — GATE SCRIPTS ALWAYS: `scripts/testing-exec.sh`, `scripts/schema-gate.sh`,
    plus any script named in the argv of a gate the handoff declares. The
    `[gates.*]` TABLE is already packed by the shared gate-adjacent rule above;
    P113 widened a gate's *argv* and the reviewer needed the script itself, not
    just its declaration.
  * 7b — TEST-SIBLING sweep: for every test file the diff changes or adds, the
    OTHER test files in the same directory that import the same subject module —
    cheap heuristic (same top-level `from ...` / `import ...` line text as the
    changed test), capped at 6 matches per changed test file's directory, the
    cap logged as a warning. P113's missed `test_prefix_parity.py` /
    `test_corpus_api.py` / etc. were exactly this shape.
  * 7c — every test file path an oracle's `observable` text names → FULL. An
    oracle's own wording is a stronger "must be checked" signal than
    diff-adjacency.
  * 7d — `tests/security/` files that reference a route string
    (`@router.<verb>("...")`) touched by the diff → FULL. P113's missed
    `test_authz_boundary.py` / `test_a36c_route_corpus_run.py` were this shape.

delta (E-005) — a frozen pack at rev R stays valid as main moves: diffs VERBATIM for
  read-list files that changed, names-only for the rest, and a mechanical threshold
  report (>20 changed files or >10 commits ⇒ re-orient fresh rather than extend).

SIZING (E-002 "Sizing rule") — breadth is an economics dial: broad packs amortise
  across many short forks, narrow task-cut packs win for one long-running
  implementer. The builder prints the token estimate and screams above 150k so the
  dial is always in view. Estimate is bytes/4 and UNDERCOUNTS code (measured 2.56
  B/tok on a real pack) — treat it as a floor.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

TOKEN_WARN = 150_000
BYTES_PER_TOKEN = 4

# Hard budget, enforced (not just warned) — operator directive 2026-08-26 after
# TOKEN_WARN alone proved toothless in practice (P135's implementer pack hit 245k
# post-manual-exclude, its reviewer pack hit 395k; the warning was printed and
# ignored both times because nothing ever acted on it). `group<=1` entries — the
# handoff contract and the scope.touch/diff files actually under edit or review —
# are NEVER evicted, even if they alone exceed the budget: those are the load-
# bearing "important, most stable" core (E-002 addendum 4/6, "content right, bytes
# wrong"), not optional context. Everything at group>=2 (context-to-read-first,
# decisions.md slices, gate config, test siblings, the consumer sweep, --extra) is
# evicted lowest-priority-first (highest group number first) once the running
# total would cross the budget. Every eviction is named in a warning — never a
# silent drop (same discipline as --exclude).
TOKEN_BUDGET_DEFAULT = 100_000

# Files that are wholesale-REGENERATED by a script (`scripts/regen-openapi.py`,
# `npm run gen:api`, ...), never hand-edited byte-by-byte — measured live on
# dstdns-P135: openapi.json x2 + schema.d.ts alone were ~275k of a 649k pack,
# and BOTH are correctly named in scope.touch (the implementer DOES touch/commit
# them) yet E-002 addendum 4/6's "byte-exact current content for Edit" rationale
# does not apply to them — the implementer's edit tool for these is a shell
# command, not Edit-tool string matching, so there is nothing to match bytes
# against. Auto-downgraded to a names+size stub regardless of group/protection;
# --include-generated GLOB overrides for the rare case of actually debugging the
# regen script's OUTPUT byte-for-byte.
GENERATED_ARTIFACT_GLOBS = (
    "*/openapi.json", "openapi.json",
    "*/schema.d.ts", "schema.d.ts",
    "*.generated.*", "*/package-lock.json", "package-lock.json",
)


def is_generated_artifact(path: str, overrides: tuple[str, ...] = ()) -> bool:
    base = path.rsplit("/", 1)[-1]
    if any(fnmatch.fnmatch(path, g) for g in overrides):
        return False
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(base, g)
               for g in GENERATED_ARTIFACT_GLOBS)
SWEEP_HIT_CAP = 80

# decisions.md is append-only and huge; it is never a FULL entry (see docstring).
NEVER_FULL = ("nyxloom-trove/decisions.md",)

SOURCE_EXT_RE = (
    r"(?:py|md|toml|sh|j2|ya?ml|json|txt|cfg|ini|sql|ts|tsx|js|jsx|css|html|Dockerfile)"
)
PATH_TOKEN_RE = re.compile(
    r"(?<![\w/.])((?:[\w.\-]+/)+[\w.\-]+\." + SOURCE_EXT_RE + r")"
    r"(?::(\d+)(?:-(\d+))?)?"
)
LINES_RE = re.compile(r"lines\s+(\d+)\s*[-–]\s*(\d+)")
SECTION_MARK_RE = re.compile(r"§\s*(\d+)")
DREF_RE = re.compile(r"\bD-(\d{3})([a-z])?\b")
DEFCLASS_DIFF_RE = re.compile(r"^([-+])(\s*)(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
SWEEP_NOISE_HITS = 200
PY_DEF_RE = re.compile(r"^(\s*)(?:@|async\s+def\s|def\s|class\s)")
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TOML_TABLE_RE = re.compile(r"^\[([^\]]+)\]")

# E-002 addendum 7 (reviewer pack-curation gap closed).
TEST_FILE_RE = re.compile(r"(^|/)(test_[\w.\-]+\.py|conftest\.py)$")
ROUTER_ROUTE_RE = re.compile(r'@router\.\w+\(\s*["\']([^"\']+)["\']')
GATE_SCRIPT_ALWAYS = ("scripts/testing-exec.sh", "scripts/schema-gate.sh")
TEST_SIBLING_CAP = 6
WORKTREE_PATH_RE = re.compile(r"^\.worktrees/[^/]+/")
ABS_REPO_PREFIX = "/workspaces/dstdns/"


class PackError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise PackError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PackError(f"not a git repo: {start}")
    return Path(proc.stdout.strip())


def blob_exists(root: Path, rev: str, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{rev}:{path}"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def _decode_or_stub(raw: bytes, path: str) -> str:
    """Binary blobs (PNG/fonts in a moved UI tree) are NOT packed — a stub names
    them so the section count still matches the read-list (a reviewer-range diff
    over a `git mv` of a whole UI tree hit a 0x89 PNG header, 2026-08-21)."""
    if b"\0" in raw[:8192]:
        return f"(binary file, {len(raw)} bytes — not packed: {path})\n"
    return raw.decode("utf-8", errors="replace")


def read_source(root: Path, rev: str, path: str) -> tuple[str, str]:
    """Return (text, provenance). Prefer the blob at `rev` (stamp-consistent);
    fall back to the working tree for an uncommitted carve (a real dstdns case:
    handoffs are sometimes dispatched before the carve commit lands)."""
    if blob_exists(root, rev, path):
        raw = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"],
                             capture_output=True).stdout
        return _decode_or_stub(raw, path), "blob"
    fs = root / path
    if fs.is_file():
        return _decode_or_stub(fs.read_bytes(), path), "worktree"
    raise PackError(f"missing path: {path} (neither {rev}:{path} nor the working tree)")


def norm_path(p: str) -> str:
    p = p.strip().strip("`\"'")
    while p.startswith("./"):
        p = p[2:]
    return p


def path_exists(root: Path, rev: str, path: str) -> bool:
    return blob_exists(root, rev, path) or (root / path).exists()


_TRACKED: dict[tuple[str, str], list[str]] = {}


def tracked_files(root: Path, rev: str) -> list[str]:
    key = (str(root), rev)
    if key not in _TRACKED:
        out = git(root, "ls-tree", "-r", "--name-only", rev, check=False)
        _TRACKED[key] = [ln for ln in out.splitlines() if ln]
    return _TRACKED[key]


def resolve_prose_path(root: Path, rev: str, tok: str) -> str | None:
    """Handoff prose abbreviates paths (`worker_db/main.py` for
    `applications/worker-db/src/worker_db/main.py`). Resolve a token by unique
    suffix match against the tracked tree; ambiguous or unknown tokens are NOT
    paths the handoff promised, so they are skipped with a warning rather than
    failing the build (only scope.touch / --extra / derived rules hard-fail)."""
    tok = norm_path(tok)
    if path_exists(root, rev, tok):
        return tok
    cands = [f for f in tracked_files(root, rev) if f.endswith("/" + tok)]
    return cands[0] if len(cands) == 1 else None


# ---------------------------------------------------------------------------
# handoff parsing
# ---------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PackError("handoff has no YAML frontmatter (first line must be '---')")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise PackError("handoff frontmatter is not terminated by '---'")


def _minimal_frontmatter(fm_text: str) -> dict:
    """Fallback parser (no PyYAML): enough for id / input_revision / gates /
    scope.touch / scope.forbid — the only keys read-list derivation needs."""
    out: dict = {"scope": {"touch": [], "forbid": []}}
    section = None
    sub = None
    for raw in fm_text.splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and stripped.endswith(":") and ":" in stripped:
            section = stripped[:-1].strip()
            sub = None
            continue
        if indent == 0:
            section = None
            key, _, val = stripped.partition(":")
            val = val.strip().strip('"').strip("'")
            if key and val:
                if val.startswith("[") and val.endswith("]"):
                    out[key] = [v.strip().strip('"').strip("'")
                                for v in val[1:-1].split(",") if v.strip()]
                else:
                    out[key] = val
            continue
        if section == "scope":
            if stripped.endswith(":") and not stripped.startswith("-"):
                sub = stripped[:-1].strip()
                out["scope"].setdefault(sub, [])
                continue
            if stripped.startswith("-") and sub:
                out["scope"][sub].append(stripped[1:].strip().strip('"').strip("'"))
    return out


def parse_frontmatter(fm_text: str) -> dict:
    try:
        import yaml  # PyYAML is present in the cockpit and in app-base
    except ImportError:
        return _minimal_frontmatter(fm_text)
    try:
        data = yaml.safe_load(fm_text)
    except Exception:
        return _minimal_frontmatter(fm_text)
    if not isinstance(data, dict):
        return _minimal_frontmatter(fm_text)
    data.setdefault("scope", {})
    if not isinstance(data.get("scope"), dict):
        data["scope"] = {}
    data["scope"].setdefault("touch", [])
    data["scope"].setdefault("forbid", [])
    return data


@dataclass
class Handoff:
    path: str
    fm: dict
    body: str
    raw: str

    @property
    def id(self) -> str:
        return str(self.fm.get("id") or Path(self.path).stem)

    @property
    def input_revision(self) -> str:
        return str(self.fm.get("input_revision") or "unstated")

    @property
    def slug(self) -> str:
        m = re.match(r"^[\w]+-P\d+[a-z]?-(.+)$", self.id)
        return m.group(1) if m else self.id

    @property
    def package_prefix(self) -> str:
        """`dstdns-P113` from `dstdns-P113-gate-argv-and-stale-tests`."""
        m = re.match(r"^([\w]+-P\d+[a-z]?)-", self.id)
        return m.group(1) if m else self.id

    @property
    def touch(self) -> list[str]:
        return [str(p) for p in (self.fm.get("scope", {}).get("touch") or [])]

    @property
    def forbid(self) -> list[str]:
        return [str(p) for p in (self.fm.get("scope", {}).get("forbid") or [])]

    @property
    def gates(self) -> list[str]:
        g = self.fm.get("gates") or []
        return [str(x) for x in g] if isinstance(g, list) else [str(g)]

    @property
    def oracle_text(self) -> str:
        return json.dumps(self.fm.get("oracles", []), default=str)

    def context_section(self) -> str:
        m = re.search(r"^##\s+Context to read first.*$", self.body, re.M)
        if not m:
            return ""
        rest = self.body[m.end():]
        nxt = re.search(r"^##\s+", rest, re.M)
        return rest[: nxt.start()] if nxt else rest


def load_handoff(root: Path, path: str) -> Handoff:
    fs = root / path
    if not fs.is_file():
        raise PackError(f"handoff not found: {path}")
    raw = fs.read_text()
    fm_text, body = split_frontmatter(raw)
    return Handoff(path=path, fm=parse_frontmatter(fm_text), body=body, raw=raw)


# ---------------------------------------------------------------------------
# slice-boundary expansion
# ---------------------------------------------------------------------------

def expand_slice(text: str, start: int, end: int, path: str) -> tuple[int, int]:
    """Expand [start,end] (1-based, inclusive) out to enclosing syntactic
    boundaries so a slice never begins or ends mid-construct — the E-002
    addendum 5 rule that a slice's title must describe what the byte range
    actually holds starts with the range being a whole construct."""
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return 1, 1
    start = max(1, min(start, n))
    end = max(start, min(end, n))
    suffix = Path(path).suffix
    if suffix == ".py":
        return _expand_python(lines, start, end)
    if suffix == ".toml":
        return _expand_toml(lines, start, end)
    if suffix in (".md", ".markdown"):
        return _expand_markdown(lines, start, end)
    return start, end


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _expand_python(lines: list[str], start: int, end: int) -> tuple[int, int]:
    n = len(lines)
    # walk back to the enclosing def/class header (the outermost one whose body
    # still contains `start`)
    anchor = None
    anchor_indent = None
    for i in range(start - 1, -1, -1):
        line = lines[i]
        if not line.strip():
            continue
        m = PY_DEF_RE.match(line)
        if m and (anchor_indent is None or _indent(line) < anchor_indent):
            anchor = i + 1
            anchor_indent = _indent(line)
            if anchor_indent == 0:
                break
    if anchor is not None:
        # include a contiguous decorator/comment run immediately above the header
        j = anchor
        while j > 1 and lines[j - 2].lstrip().startswith(("@", "#")):
            j -= 1
        start = j
        # end of that construct: next non-blank line at indent <= anchor_indent
        k = end
        while k < n:
            line = lines[k]
            if line.strip() and _indent(line) <= (anchor_indent or 0):
                break
            k += 1
        end = max(end, k)
    return start, end


def _expand_toml(lines: list[str], start: int, end: int) -> tuple[int, int]:
    n = len(lines)
    table = None
    for i in range(start - 1, -1, -1):
        m = TOML_TABLE_RE.match(lines[i])
        if m:
            table = m.group(1)
            start = i + 1
            break
    if table is None:
        return start, end
    k = end
    while k < n:
        m = TOML_TABLE_RE.match(lines[k])
        if m and not m.group(1).startswith(table + "."):
            break
        k += 1
    return start, max(end, k)


def md_heading_lines(lines: list[str]) -> dict[int, int]:
    """{0-based index: heading level} for headings OUTSIDE fenced code blocks.
    Shell comments inside a ``` fence look exactly like `# heading` — GUIDE.md §2
    is full of them, and treating one as a heading truncates the slice."""
    out: dict[int, int] = {}
    fence = False
    for i, line in enumerate(lines):
        st = line.strip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
            continue
        if fence:
            continue
        m = MD_HEADING_RE.match(line)
        if m:
            out[i] = len(m.group(1))
    return out


def _expand_markdown(lines: list[str], start: int, end: int) -> tuple[int, int]:
    n = len(lines)
    heads = md_heading_lines(lines)
    level = None
    for i in range(start - 1, -1, -1):
        if i in heads:
            level = heads[i]
            start = i + 1
            break
    if level is None:
        return start, end
    k = end
    while k < n:
        if k in heads and heads[k] <= level:
            break
        k += 1
    return start, max(end, k)


def find_markdown_section(text: str, number: str) -> tuple[int, int] | None:
    """Resolve `§N` / `## N` (e.g. GUIDE.md `## 2. Environment setup contract`)."""
    lines = text.splitlines()
    heads = md_heading_lines(lines)
    for i, level in sorted(heads.items()):
        title = MD_HEADING_RE.match(lines[i]).group(2).strip()
        if re.match(rf"^{re.escape(number)}([.)\s]|$)", title):
            for j in range(i + 1, len(lines)):
                if j in heads and heads[j] <= level:
                    return i + 1, j
            return i + 1, len(lines)
    return None


def find_decision_section(text: str, dnum: str) -> tuple[int, int] | None:
    """`## D-139 · …` up to the next `## D-` heading (D-139a is its own heading)."""
    lines = text.splitlines()
    pat = re.compile(rf"^##\s+D-{re.escape(dnum)}(\s|·|:|$)")  # D-139a is its own heading
    for i, line in enumerate(lines):
        if pat.match(line):
            for j in range(i + 1, len(lines)):
                if re.match(r"^##\s+D-\d", lines[j]):
                    return i + 1, j
            return i + 1, len(lines)
    return None


def find_toml_table(text: str, table: str) -> tuple[int, int] | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = TOML_TABLE_RE.match(line)
        if m and m.group(1) == table:
            for j in range(i + 1, len(lines)):
                m2 = TOML_TABLE_RE.match(lines[j])
                if m2 and not m2.group(1).startswith(table + "."):
                    return i + 1, j
            return i + 1, len(lines)
    return None


def list_toml_tables(text: str, prefix: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = TOML_TABLE_RE.match(line)
        if m and m.group(1).startswith(prefix) and "." not in m.group(1)[len(prefix):]:
            out.append(m.group(1))
    return out


# ---------------------------------------------------------------------------
# read-list model
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    path: str
    kind: str                    # full | slice | diff | sweep | names
    why: str
    start: int | None = None
    end: int | None = None
    group: int = 0
    payload: str | None = None   # pre-rendered body (diff/sweep/names)
    title: str | None = None     # section header override

    def key(self) -> tuple:
        return (self.path, self.kind, self.start, self.end)

    def label(self) -> str:
        if self.start and self.end:
            return f"{self.path}:{self.start}-{self.end}"
        return self.path

    def header(self) -> str:
        if self.title:
            return self.title
        if self.kind == "diff":
            return f"diff {self.path}"
        return self.label()


PACK_OUTPUT_RE = re.compile(r"^nyxloom-trove/orientation/[^/]+/(pack\.md|read-list\.txt)$")


class ReadList:
    def __init__(self) -> None:
        self._entries: list[Entry] = []
        self._seen: set[tuple] = set()
        self._full: set[str] = set()

    def add(self, entry: Entry) -> None:
        if PACK_OUTPUT_RE.match(entry.path):
            return   # a pack's own outputs are never pack inputs (a handoff's "Context to
                     # read first" names its pack; packing it recursed 133k tokens once)
        if entry.kind == "slice" and entry.path in self._full:
            return                       # a FULL entry already carries these bytes
        if entry.key() in self._seen:
            return
        self._seen.add(entry.key())
        if entry.kind == "full":
            if entry.path in self._full:
                return
            self._full.add(entry.path)
            # a FULL entry supersedes any slice of the same file
            self._entries = [e for e in self._entries
                             if not (e.kind == "slice" and e.path == entry.path)]
        self._entries.append(entry)

    def entries(self) -> list[Entry]:
        return sorted(
            self._entries,
            key=lambda e: (e.group, e.path, e.start or 0, e.kind),
        )


# ---------------------------------------------------------------------------
# derivation helpers
# ---------------------------------------------------------------------------

def parse_extra(spec: str) -> tuple[str, int | None, int | None]:
    m = re.match(r"^(.*?)(?::(\d+)(?:-(\d+))?)?$", spec)
    path = m.group(1)
    a = int(m.group(2)) if m.group(2) else None
    b = int(m.group(3)) if m.group(3) else (a if a else None)
    return path, a, b


def context_items(handoff: Handoff) -> list[tuple[str, int | None, int | None, str]]:
    """(path, start, end, why) from 'Context to read first'."""
    section = handoff.context_section()
    if not section.strip():
        return []
    blocks = re.split(r"^\s*(?=\d+\.\s)", section, flags=re.M)
    out = []
    for block in blocks:
        if not block.strip():
            continue
        first = " ".join(block.split())[:110]
        line_range = LINES_RE.search(block)
        sect_marks = SECTION_MARK_RE.findall(block)
        for m in PATH_TOKEN_RE.finditer(block):
            path, a, b = m.group(1), m.group(2), m.group(3)
            if a:
                start = int(a)
                end = int(b) if b else int(a)
                out.append((path, start, end, f"context item — {first}"))
            elif line_range:
                out.append((path, int(line_range.group(1)), int(line_range.group(2)),
                            f"context item (lines) — {first}"))
            elif sect_marks and Path(path).suffix in (".md", ".markdown"):
                for num in sect_marks:
                    out.append((path, -int(num), None, f"context item §{num} — {first}"))
            else:
                out.append((path, None, None, f"context item — {first}"))
    return out


def add_context_entries(rl: ReadList, root: Path, rev: str, handoff: Handoff,
                        group: int, missing: list[str], reviewer: bool = False,
                        warnings: list[str] | None = None) -> None:
    for raw_path, start, end, why in context_items(handoff):
        path = resolve_prose_path(root, rev, raw_path)
        if path is None:
            if warnings is not None:
                warnings.append(f"context-item token is not a resolvable tracked path "
                                f"(skipped): {raw_path}")
            continue
        text, _ = read_source(root, rev, path)
        if start is not None and start < 0:                # §N marker
            rng = find_markdown_section(text, str(-start))
            if not rng:
                continue
            rl.add(Entry(path, "slice", why, rng[0], rng[1], group))
            continue
        if start is not None:
            a, b = expand_slice(text, start, end or start, path)
            rl.add(Entry(path, "slice", why, a, b, group))
            continue
        if any(path.endswith(nf) for nf in NEVER_FULL):
            continue                                       # D-sections only
        if reviewer:
            # E-006: reviewers get the standing cross-reference set, not
            # comprehension reads of the whole subsystem. A bare-file context item
            # enters as a slice of its head, or FULL only when it is small.
            n = len(text.splitlines())
            if n > 400:
                rl.add(Entry(path, "slice", why + " [head]", 1, 200, group))
                continue
        rl.add(Entry(path, "full", why, None, None, group))


def add_decision_entries(rl: ReadList, root: Path, rev: str, handoff: Handoff,
                         group: int, missing: list[str]) -> None:
    dpath = "nyxloom-trove/decisions.md"
    refs = sorted({m.group(1) + (m.group(2) or "") for m in DREF_RE.finditer(handoff.raw)})
    if not refs:
        return
    if not path_exists(root, rev, dpath):
        missing.append(f"{dpath} (D-refs {', '.join(refs)} in the handoff)")
        return
    text, _ = read_source(root, rev, dpath)
    for dnum in refs:
        rng = find_decision_section(text, dnum)
        if not rng:
            continue
        rl.add(Entry(dpath, "slice", f"ledger D-{dnum} — referenced by the handoff",
                     rng[0], rng[1], group))


def add_gate_entries(rl: ReadList, root: Path, rev: str, handoff: Handoff,
                     group: int, missing: list[str]) -> None:
    """E-002 addendum 5: the pack must carry the gates the package can trip."""
    ntoml = "nyxloom-trove/nyxloom.toml"
    if path_exists(root, rev, ntoml):
        text, _ = read_source(root, rev, ntoml)
        gates = handoff.gates or [t.split(".", 1)[1] for t in list_toml_tables(text, "gates.")]
        for g in gates:
            rng = find_toml_table(text, f"gates.{g}")
            if rng:
                rl.add(Entry(ntoml, "slice", f"gate-adjacent — [gates.{g}] the declared gate",
                             rng[0], rng[1], group))
    atoml = "assay.toml"
    if path_exists(root, rev, atoml):
        text, _ = read_source(root, rev, atoml)
        named = [t for t in list_toml_tables(text, "lanes.")
                 if t.split(".", 1)[1] in handoff.raw]
        for t in (named or [t for t in list_toml_tables(text, "lanes.") if t == "lanes.mock"]):
            rng = find_toml_table(text, t)
            if rng:
                rl.add(Entry(atoml, "slice", f"gate-adjacent — [{t}] coverage lane",
                             rng[0], rng[1], group))
    # conftest / coverage-gate machinery an oracle names
    hay = handoff.raw
    for m in PATH_TOKEN_RE.finditer(hay):
        p = m.group(1)
        base = Path(p).name
        if ("conftest" in base or "coverage_gate" in base or "coverage-gate" in base
                or base == "schema-gate.sh"):
            resolved = resolve_prose_path(root, rev, p)
            if resolved:
                rl.add(Entry(resolved, "full",
                             "gate-adjacent — oracle-coupled gate machinery",
                             None, None, group))


def _top_level_import_lines(text: str) -> set[str]:
    return {ln.strip() for ln in text.splitlines()
            if ln.startswith(("from ", "import "))}


def add_gate_script_entries(rl: ReadList, root: Path, rev: str, handoff: Handoff,
                            group: int) -> None:
    """E-002 addendum 7a: the reviewer pack must carry the gate SCRIPTS
    themselves, not just their `[gates.*]` declarations (add_gate_entries's
    older rule, above) — P113 widened a gate's argv and the reviewer needed to
    read `testing-exec.sh`/`schema-gate.sh` to see what changed. ALWAYS both,
    plus any script named in the argv of a gate the handoff declares."""
    for p in GATE_SCRIPT_ALWAYS:
        if path_exists(root, rev, p):
            rl.add(Entry(p, "full", "gate-adjacent script — ALWAYS (E-002 addendum 7a)",
                         None, None, group))
    ntoml = "nyxloom-trove/nyxloom.toml"
    if not path_exists(root, rev, ntoml):
        return
    text, _ = read_source(root, rev, ntoml)
    gates = handoff.gates or [t.split(".", 1)[1] for t in list_toml_tables(text, "gates.")]
    for g in gates:
        rng = find_toml_table(text, f"gates.{g}")
        if not rng:
            continue
        block = "\n".join(text.splitlines()[rng[0] - 1:rng[1]])
        for m in PATH_TOKEN_RE.finditer(block):
            p = m.group(1)
            if p in GATE_SCRIPT_ALWAYS:
                continue
            resolved = resolve_prose_path(root, rev, p)
            if resolved:
                rl.add(Entry(resolved, "full",
                             f"gate-adjacent script named in [gates.{g}] argv "
                             f"(E-002 addendum 7a)", None, None, group))


def add_test_sibling_entries(rl: ReadList, root: Path, tip: str, files: list[str],
                             gone: set[str], group: int, warnings: list[str]) -> None:
    """E-002 addendum 7b: for every test file changed/added by the diff, pack
    the OTHER test files in the same directory that import the same subject
    module — a cheap heuristic (same top-level `from ...`/`import ...` LINE
    TEXT as the changed test), capped at 6 matches per changed test file's
    directory with the cap logged. P113's missed `test_prefix_parity.py` /
    `test_corpus_api.py` / `test_classification_routes_reflected.py` / etc.
    were exactly this shape: siblings of a changed test, named nowhere in the
    handoff."""
    changed_tests = [p for p in files if p not in gone and TEST_FILE_RE.search(p)]
    for p in changed_tests:
        if not blob_exists(root, tip, p):
            continue
        text, _ = read_source(root, tip, p)
        imports = _top_level_import_lines(text)
        if not imports:
            continue
        directory = str(Path(p).parent)
        siblings = sorted(
            f for f in tracked_files(root, tip)
            if str(Path(f).parent) == directory and f != p and TEST_FILE_RE.search(f)
        )
        matched = [f for f in siblings
                  if _top_level_import_lines(read_source(root, tip, f)[0]) & imports]
        for sib in matched[:TEST_SIBLING_CAP]:
            rl.add(Entry(sib, "full",
                         f"test sibling of {p} — shares a top-level import "
                         f"(E-002 addendum 7b)", None, None, group))
        if len(matched) > TEST_SIBLING_CAP:
            warnings.append(
                f"test-sibling cap: {directory}/ had {len(matched)} sibling(s) of {p} "
                f"sharing a top-level import, packed {TEST_SIBLING_CAP} "
                f"(capped, E-002 addendum 7b)")


def add_oracle_test_entries(rl: ReadList, root: Path, rev: str, handoff: Handoff,
                            group: int) -> None:
    """E-002 addendum 7c: every test file path an oracle's `observable` text
    names is FULL for the reviewer — the oracle's own wording is a stronger
    "what must be checked" signal than diff-adjacency."""
    for o in (handoff.fm.get("oracles") or []):
        obs = str(o.get("observable", "")) if isinstance(o, dict) else str(o)
        for m in PATH_TOKEN_RE.finditer(obs):
            p = m.group(1)
            if not TEST_FILE_RE.search(p):
                continue
            resolved = resolve_prose_path(root, rev, p)
            if resolved:
                rl.add(Entry(resolved, "full",
                             "oracle observable names this test file "
                             "(E-002 addendum 7c)", None, None, group))


def add_security_route_entries(rl: ReadList, root: Path, base: str, tip: str,
                               files: list[str], group: int) -> None:
    """E-002 addendum 7d: `tests/security/` files that reference a route string
    (`@router.<verb>("...")` literal) touched by the diff are FULL — the P113
    gap named `test_authz_boundary.py`/`test_a36c_route_corpus_run.py`
    exactly: security/contract suites that consume the changed surface but
    aren't themselves in the diff."""
    routes: set[str] = set()
    for p in files:
        dtext = git(root, "diff", base, tip, "--", p)
        routes.update(ROUTER_ROUTE_RE.findall(dtext))
    if not routes:
        return
    for f in tracked_files(root, tip):
        if not f.startswith("tests/security/"):
            continue
        text, _ = read_source(root, tip, f)
        if any(r in text for r in routes):
            rl.add(Entry(f, "full",
                         "tests/security/ references a route touched by the diff "
                         "(E-002 addendum 7d)", None, None, group))


def dir_listing(root: Path, rev: str, path: str) -> str:
    out = git(root, "ls-tree", "-r", "--name-only", rev, "--", path.rstrip("/") + "/")
    names = [ln for ln in out.splitlines() if ln.strip()]
    if not names:
        names = sorted(str(p.relative_to(root)) for p in (root / path).rglob("*") if p.is_file())
    body = "\n".join(f"  {n}" for n in names)
    return (f"(directory in scope — NAMES ONLY, content deliberately not packed: a whole\n"
            f"tree is the dead-weight failure mode E-006 measured. Read what you need.)\n"
            f"{body}\n")


# ---------------------------------------------------------------------------
# reviewer-only derivation
# ---------------------------------------------------------------------------

def parse_range(root: Path, rng: str) -> tuple[str, str, str]:
    if "..." in rng:
        left, right = rng.split("...", 1)
        base = git(root, "merge-base", left or "HEAD", right or "HEAD").strip()
    elif ".." in rng:
        left, right = rng.split("..", 1)
        base = git(root, "rev-parse", left or "HEAD").strip()
    else:
        left, right = "HEAD", rng
        base = git(root, "merge-base", "HEAD", rng).strip()
    tip = git(root, "rev-parse", right or "HEAD").strip()
    return base, tip, rng


def diff_files(root: Path, base: str, tip: str) -> list[str]:
    return [ln for ln in git(root, "diff", "--name-only", base, tip).splitlines() if ln]


def renamed_files(root: Path, base: str, tip: str) -> dict[str, str]:
    """new_path -> old_path for PURE renames (R100). A `git mv` of a whole tree is
    'changed' to --name-only, but its content is byte-identical — a reviewer verifies
    the move with `--stat -M100%`, not by re-reading 136 files (P117, 2026-08-21)."""
    out = {}
    for ln in git(root, "diff", "--name-status", "-M100%", base, tip).splitlines():
        parts = ln.split("\t")
        if parts and parts[0] == "R100" and len(parts) == 3:
            out[parts[2]] = parts[1]
    return out


DIFF_ONLY_MIN_CHARS = 32_000      # ~8k tokens: a file this big with a tiny diff is diff-only
DIFF_ONLY_MAX_HUNKS = 2


def added_files(root: Path, base: str, tip: str) -> list[str]:
    out = git(root, "diff", "--name-only", "--diff-filter=A", base, tip)
    return [l for l in out.splitlines() if l.strip()]


def deleted_files(root: Path, base: str, tip: str) -> list[str]:
    return [ln for ln in git(root, "diff", "--diff-filter=D", "--name-only",
                             base, tip).splitlines() if ln]


def deleted_symbols(root: Path, base: str, tip: str) -> list[str]:
    """Module-level `def`/`class` names the diff removes and does not re-add.

    Indent > 0 is deliberately EXCLUDED: a nested helper or a method of a local
    stub class (`all`, `first`, `commit`, `execute` on a fake SQLAlchemy session —
    all real in the P113 diff) cannot be imported by another module, and sweeping
    such a name returns thousands of unrelated hits. The sweep exists to find
    CONSUMERS of a removed API; only the module-level names have any."""
    text = git(root, "diff", "-U0", base, tip)
    removed, added = [], set()
    for line in text.splitlines():
        m = DEFCLASS_DIFF_RE.match(line)
        if not m:
            continue
        sign, indent, name = m.group(1), m.group(2), m.group(3)
        if sign == "+":
            added.add(name)
        elif len(indent) == 0:
            removed.append(name)
    out, seen = [], set()
    for name in removed:
        if name in added or name in seen or name.startswith("__"):
            continue
        seen.add(name)
        out.append(name)
    return out


def sweep_table(root: Path, tip: str, terms: list[str]) -> str:
    """CONSUMER SWEEP, pre-tabulated (E-006 recommendation; dstdns D-128 #10).
    All tracked file types at the tip; the orientation directory is excluded so a
    pack never matches itself."""
    if not terms:
        return ("No symbol or file was deleted/renamed by this diff — no consumer\n"
                "sweep is owed. (Absence measured, not assumed.)\n")
    chunks = [
        "Consumer sweep — `git grep -n -F <term>` at the tip across ALL tracked files\n"
        "(pathspec excludes nyxloom-trove/orientation so packs do not match themselves).\n"
        "A term with ZERO hits is a clean retirement; any hit is a live consumer of a\n"
        "thing this diff removed and must be adjudicated by the reviewer.\n"
    ]
    for term in terms:
        proc = subprocess.run(
            ["git", "-C", str(root), "grep", "-n", "-F", "-e", term, tip, "--",
             ".", ":(exclude)nyxloom-trove/orientation"],
            capture_output=True, text=True,
        )
        hits = [ln[len(tip) + 1:] for ln in proc.stdout.splitlines() if ln]
        if len(hits) > SWEEP_NOISE_HITS:
            chunks.append(f"\n--- term: {term} — {len(hits)} hit(s): TOO COMMON TO SWEEP "
                          f"(>{SWEEP_NOISE_HITS}); the name is not discriminating. "
                          f"Sweep it by hand with a qualified pattern if it matters.")
            continue
        chunks.append(f"\n--- term: {term} — {len(hits)} hit(s)")
        if not hits:
            chunks.append("    (none — clean)")
        for hit in hits[:SWEEP_HIT_CAP]:
            chunks.append(f"    {hit}")
        if len(hits) > SWEEP_HIT_CAP:
            chunks.append(f"    … {len(hits) - SWEEP_HIT_CAP} more hits truncated "
                          f"(re-run the grep for the tail)")
    return "\n".join(chunks) + "\n"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

@dataclass
class Pack:
    handoff: Handoff
    role: str
    stamp: str
    entries: list[Entry]
    header: str
    body: str
    readlist: str
    tokens: int
    warnings: list[str] = field(default_factory=list)


def derive(root: Path, handoff: Handoff, role: str, rng: str | None,
           extras: list[str], slices: list[str] | None = None,
           include_generated: tuple[str, ...] = ()) -> tuple[list[Entry], str, list[str]]:
    rl = ReadList()

    def generated_stub(p: str, rev_for_stat: str) -> Entry:
        text, _ = read_source(root, rev_for_stat, p)
        n_lines = text.count("\n") + 1
        n_bytes = len(text.encode())
        return Entry(p, "names",
                     "GENERATED ARTIFACT — regenerated wholesale by a script "
                     "(never hand-edited byte-by-byte), so E-002 addendum 4/6's "
                     "byte-exact-for-Edit rule does not apply; full content skipped "
                     "by design. --include-generated overrides.",
                     None, None, 1,
                     payload=f"({n_bytes:,} bytes, {n_lines:,} lines — regenerate, "
                              f"don't hand-diff)\n",
                     title=f"{p} (generated artifact — names only)")
    missing: list[str] = []
    warnings: list[str] = []
    # --slice PATH:a-b — operator override: pack a scope.touch file as a SLICE
    # instead of FULL. The E-002 addendum 4/6 rule (edit targets FULL) is the
    # default because "content right, bytes wrong" is a measured failure class;
    # the override exists for the measured counter-case (P116 carve, 2026-08-21):
    # an 18k-token doc in scope for a ONE-LINE repoint packed FULL and pushed the
    # pack past the 150k sizing rule. The slice header says so, and the
    # implementer re-reads the file at Edit time.
    overrides: dict[str, tuple[int, int]] = {}
    for spec in (slices or []):
        sp, sa, sb = parse_extra(spec)
        if sa is None:
            raise PackError(f"--slice needs a line range: {spec!r} (PATH:a-b)")
        overrides[sp] = (sa, sb or sa)

    if role == "reviewer":
        if not rng:
            raise PackError("--role reviewer requires --range (e.g. --range main...<tip>)")
        base, tip, _ = parse_range(root, rng)
        rev = tip
        files = diff_files(root, base, tip)
        gone = set(deleted_files(root, base, tip))
        renamed = renamed_files(root, base, tip)
        if renamed:
            table = ("PURE RENAMES (R100 — byte-identical, verify with "
                     "`git diff --stat -M100% <range>`; not packed):\n" +
                     "".join(f"  {old} -> {new}\n" for new, old in sorted(renamed.items())))
            rl.add(Entry("renames", "names", f"{len(renamed)} pure renames (R100) — names only",
                         None, None, 0, payload=table, title="renames"))
            warnings.append(f"{len(renamed)} R100 renames packed as names only")
        rename_paths = set(renamed) | set(renamed.values())
        added = set(added_files(root, base, tip))
        for p in sorted(files):
            if p in rename_paths:
                continue
            if is_generated_artifact(p, include_generated):
                rl.add(generated_stub(p, tip))
                warnings.append(f"generated artifact (names only, not diffed/read — "
                                 f"regenerate and byte-compare instead): {p}")
                continue
            if p in added:
                # a file the diff ADDS: its unified diff IS its full text, so a
                # diff section would duplicate the FULL byte for byte (P116: 23
                # new files packed twice, ~90k tokens of pure repetition)
                rl.add(Entry(p, "full", "ADDED by the diff — FULL at the tip is the "
                                        "whole diff (no separate diff section)", None, None, 1))
                continue
            dtext = git(root, "diff", base, tip, "--", p)
            rl.add(Entry(p, "diff", "changed by the diff under review — unified diff",
                         None, None, 0, payload=dtext))
            if p in gone:
                warnings.append(f"deleted by the diff (diff section only): {p}")
                continue
            if not blob_exists(root, tip, p):
                missing.append(f"{p} (in the diff but absent at the tip)")
                continue
            if p.endswith((".md", ".markdown")) and not p.startswith("nyxloom-trove/"):
                warnings.append(f"diff-only (prose doc — a reviewer verifies docs from the diff): {p}")
                continue
            full_text, _ = read_source(root, tip, p)
            if (len(full_text) > DIFF_ONLY_MIN_CHARS
                    and dtext.count("\n@@") <= DIFF_ONLY_MAX_HUNKS):
                warnings.append(f"diff-only (large file, <= {DIFF_ONLY_MAX_HUNKS} hunks): {p}")
                continue
            rl.add(Entry(p, "full", "changed by the diff — FULL at the tip side "
                                    "(the post-state under review)", None, None, 1))
        # the handoff itself
        if path_exists(root, rev, handoff.path):
            rl.add(Entry(handoff.path, "full", "the package contract — every oracle is "
                                               "verified against this text", None, None, 2))
        add_decision_entries(rl, root, rev, handoff, 3, missing)
        for suffix in ("LOG", "REPORT"):
            rp = f"nyxloom-trove/reports/{handoff.package_prefix}-{suffix}.md"
            if path_exists(root, rev, rp):
                rl.add(Entry(rp, "full",
                             f"prior-round {suffix} — every reviewer checks its claims "
                             f"against reality (E-006)", None, None, 4))
        guide = "nyxloom-trove/GUIDE.md"
        if path_exists(root, rev, guide):
            gtext, _ = read_source(root, rev, guide)
            for num in ("2", "3"):
                sec = find_markdown_section(gtext, num)
                if sec:
                    rl.add(Entry(guide, "slice", f"standing set — GUIDE §{num}",
                                 sec[0], sec[1], 5))
        add_context_entries(rl, root, rev, handoff, 6, missing, reviewer=True,
                            warnings=warnings)
        add_gate_script_entries(rl, root, rev, handoff, 7)
        add_test_sibling_entries(rl, root, tip, files, gone, 8, warnings)
        add_oracle_test_entries(rl, root, rev, handoff, 9)
        add_security_route_entries(rl, root, base, tip, files, 10)
        terms = deleted_symbols(root, base, tip) + sorted(gone)
        rl.add(Entry("sweep", "sweep",
                     "PRE-TABULATED consumer sweep for every symbol/file this diff "
                     "removes (E-006; D-128 #10)", None, None, 11,
                     payload=sweep_table(root, tip, terms), title="sweep"))
    else:
        rev = git(root, "rev-parse", "HEAD").strip()
        rl.add(Entry(handoff.path, "full",
                     "the package contract itself: scope/forbid, oracles, the edit set",
                     None, None, 0))
        for p in handoff.touch:
            p = p.rstrip()
            if p.endswith("/") or (root / p).is_dir():
                if not (root / p).is_dir():
                    # a NEW directory the package creates (e.g. a top-level
                    # retired-legacy/) is legitimately absent — same rule as a
                    # new file below, never a hard failure (P117 carve, 2026-08-21)
                    warnings.append(f"scope.touch directory does not exist yet (new directory): {p}")
                    continue
                rl.add(Entry(p.rstrip("/"), "names",
                             "scope.touch DIRECTORY — names only", None, None, 1,
                             payload=dir_listing(root, rev, p),
                             title=f"{p.rstrip('/')}/ (names only)"))
                continue
            if not path_exists(root, rev, p):
                # a NEW file the package creates is legitimately absent
                warnings.append(f"scope.touch path does not exist yet (new file): {p}")
                continue
            if p in overrides:
                # operator-explicit range: honoured as given (clamped to the file),
                # NOT expanded to the enclosing def/class — the expansion rule
                # serves comprehension slices; here the operator already knows the
                # edit is a one-liner (a one-line comment inside a 2,700-line class
                # would otherwise pull the whole class: measured 28k tokens, P117)
                text, _ = read_source(root, rev, p)
                n = text.count("\n") + 1
                a = max(1, min(overrides[p][0], n)); b = max(a, min(overrides[p][1], n))
                rl.add(Entry(p, "slice",
                             "scope.touch — SLICE by operator override (--slice): a "
                             "small-edit target; COMPREHENSION ONLY — re-read the "
                             "file at Edit time (E-002 addendum 4/6 still holds)",
                             a, b, 1))
                continue
            if is_generated_artifact(p, include_generated):
                rl.add(generated_stub(p, rev))
                continue
            rl.add(Entry(p, "full",
                         "scope.touch — FULL: Edit needs byte-exact current content "
                         "(E-002 addendum 4/6)", None, None, 1))
        add_context_entries(rl, root, rev, handoff, 2, missing, warnings=warnings)
        add_decision_entries(rl, root, rev, handoff, 3, missing)
        add_gate_entries(rl, root, rev, handoff, 4, missing)
        if handoff.forbid:
            body = ("scope.forbid — these paths must NOT be edited. Names only: their\n"
                    "content is by definition not the edit set, so packing it is dead\n"
                    "weight (E-006). Know the boundary; read a file only if you must.\n"
                    + "\n".join(f"  {p}" for p in handoff.forbid) + "\n")
            rl.add(Entry("forbidden", "names", "scope.forbid — names only, never content",
                         None, None, 5, payload=body, title="forbidden (names only)"))

    for spec in extras:
        path, a, b = parse_extra(spec)
        if not path_exists(root, rev, path):
            missing.append(f"{path} (--extra)")
            continue
        if a:
            text, _ = read_source(root, rev, path)
            a, b = expand_slice(text, a, b or a, path)
            rl.add(Entry(path, "slice", "--extra (operator-added)", a, b, 12))
        else:
            rl.add(Entry(path, "full", "--extra (operator-added)", None, None, 12))

    if missing:
        raise PackError("missing paths:\n  " + "\n  ".join(missing))
    return rl.entries(), rev, warnings


def render(root: Path, handoff: Handoff, role: str, rev: str, entries: list[Entry],
           rng: str | None, warnings: list[str], budget: int = TOKEN_BUDGET_DEFAULT) -> Pack:
    sections: list[tuple[Entry, str]] = []
    for e in entries:
        if e.payload is not None:
            body = e.payload
        elif e.kind == "full":
            body, _ = read_source(root, rev, e.path)
        elif e.kind == "slice":
            text, _ = read_source(root, rev, e.path)
            lines = text.splitlines()
            body = "\n".join(lines[e.start - 1:e.end]) + "\n"
        else:
            body = ""
        if body and not body.endswith("\n"):
            body += "\n"
        sections.append((e, body))

    if budget and budget > 0:
        protected_tokens = sum(len(b.encode()) // BYTES_PER_TOKEN
                                for e, b in sections if e.group <= 1)
        remaining = budget - protected_tokens
        evicted: list[tuple[str, int]] = []
        kept: list[tuple[Entry, str]] = []
        for e, b in sections:
            cost = len(b.encode()) // BYTES_PER_TOKEN
            if e.group <= 1 or cost <= max(remaining, 0):
                kept.append((e, b))
                if e.group > 1:
                    remaining -= cost
            else:
                evicted.append((e.header(), cost))
                stub = (f"[EVICTED — over the ~{budget:,}-token budget; this section "
                        f"was ~{cost:,} tokens. why: {e.why}. Read directly if needed: "
                        f"{e.label()}]\n")
                kept.append((e, stub))
        sections = kept
        if evicted:
            saved = sum(c for _, c in evicted)
            warnings.append(
                f"BUDGET: evicted {len(evicted)} section(s) (~{saved:,} tokens) to stay "
                f"under ~{budget:,} (protected group<=1 — handoff + edit/review "
                f"targets — never evicted): "
                + ", ".join(f"{h} (~{c:,}tok)" for h, c in evicted))
        if protected_tokens > budget:
            warnings.append(
                f"BUDGET: protected core alone (~{protected_tokens:,} tokens: handoff + "
                f"scope.touch/diff files) already exceeds the ~{budget:,} budget — nothing "
                f"more could be evicted to close the gap; this is real, non-negotiable size, "
                f"not a bug in the eviction logic.")

    body_text = "".join(f"=== {e.header()} ===\n{b}\n" for e, b in sections)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    stamp_short = rev[:8]
    tokens = (len(body_text.encode()) // BYTES_PER_TOKEN)

    head = [
        f"# {handoff.package_prefix} {handoff.slug} — orientation pack ({role})",
        f"stamp: {rev} — {now}",
        f"handoff: {handoff.path} (input_revision {handoff.input_revision})",
    ]
    if rng:
        base, tip, _ = parse_range(root, rng)
        head.append(f"range: {rng} (base {base[:8]} → tip {tip[:8]}) — FULL sections are "
                    f"the TIP side")
    head += [
        f"sections: {len(sections)} · ~{tokens // 1000}k tokens (bytes/4 — UNDERCOUNTS "
        f"code, measured 2.56 B/tok; treat as a floor)",
        f"Reconcile: if your worktree base differs from the stamp, "
        f"`git diff {stamp_short}..HEAD -- <path>` before trusting any section.",
        "FULL sections = the edit set (byte-exact as of the stamp). SLICE sections are "
        "comprehension-only: never Edit from a slice — re-read the live file first "
        "(Edit needs byte-exact current content).",
        "Built by nyxloom-trove/orientation/pack.py (model-free: no model produced or "
        "summarised any byte below).",
    ]
    if role == "reviewer":
        head.append("Reviewer pack (E-006): diff-changed files + prior-round LOG/REPORT + "
                    "standing cross-reference set; comprehension slices dropped; the "
                    "consumer sweep is PRE-TABULATED in the `=== sweep ===` section.")
    header = "\n".join(head) + "\n"

    rl_lines = [f"{e.label()}  {e.kind}  {e.why}" for e in entries]
    readlist = "\n".join(rl_lines) + "\n"

    if tokens > TOKEN_WARN:
        biggest = sorted(sections, key=lambda s: -len(s[1]))[:8]
        warnings.append(
            f"SIZE WARNING: ~{tokens:,} tokens > {TOKEN_WARN:,}. Largest sections:\n  "
            + "\n  ".join(f"{len(b)//BYTES_PER_TOKEN:>7,} tok  {e.header()}"
                          for e, b in biggest)
            + "\n  Sizing rule (E-002): narrow the pack for a single long-running "
              "implementer; breadth only pays across many short forks.")
    return Pack(handoff=handoff, role=role, stamp=rev, entries=entries, header=header,
                body=body_text, readlist=readlist, tokens=tokens, warnings=warnings)


def build_pack(root: Path, handoff_path: str, role: str, rng: str | None = None,
               extras: list[str] | None = None, slices: list[str] | None = None,
               excludes: list[str] | None = None,
               budget: int = TOKEN_BUDGET_DEFAULT,
               include_generated: list[str] | None = None) -> Pack:
    handoff = load_handoff(root, handoff_path)
    entries, rev, warnings = derive(root, handoff, role, rng, extras or [], slices or [],
                                     tuple(include_generated or ()))
    if excludes:
        # --exclude GLOB — operator curation knob (E-006: three reviewers used 0% of
        # their packs; the 7b sibling sweep alone was ~30 files on P116). Never
        # silent: every dropped section is named in a warning.
        kept, dropped = [], []
        for e in entries:
            if any(fnmatch.fnmatch(e.path, g) for g in excludes):
                dropped.append(e.label())
            else:
                kept.append(e)
        entries = kept
        for glob in excludes:
            if not any(fnmatch.fnmatch(d.split(":")[0], glob) for d in dropped):
                warnings.append(f"--exclude {glob} matched nothing")
        if dropped:
            warnings.append(f"excluded by --exclude ({len(dropped)}): " + ", ".join(dropped))
    return render(root, handoff, role, rev, entries, rng, warnings, budget)


def cmd_build(args) -> int:
    root = repo_root(Path.cwd())
    handoff_path = rel(root, args.handoff)
    budget = args.budget if args.budget is not None else TOKEN_BUDGET_DEFAULT
    pack = build_pack(root, handoff_path, args.role, args.range, args.extra or [],
                      getattr(args, "slice", None) or [], args.exclude or [], budget,
                      args.include_generated or [])

    print(f"pack: {pack.handoff.package_prefix} {pack.handoff.slug}  role={pack.role}  "
          f"stamp={pack.stamp[:8]}")
    print(f"  sections={len(pack.entries)}  bytes={len(pack.body.encode()):,}  "
          f"~{pack.tokens:,} tokens")
    kinds: dict[str, int] = {}
    for e in pack.entries:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print("  kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    for e in pack.entries:
        # kind column already says diff/names/sweep — print the bare label
        print(f"    {e.kind:<6} {e.label()}")
    for w in pack.warnings:
        print(f"  WARN: {w}")

    if args.dry_run:
        print("  (dry run — nothing written)")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else (
        root / "nyxloom-trove/orientation" / pack.handoff.slug)
    out_dir = out_dir if out_dir.is_absolute() else root / out_dir
    pack_md = out_dir / "pack.md"
    if pack_md.exists() and not args.force:
        print(f"  REFUSING to overwrite {pack_md} (pass --force). Hand-built packs are "
              f"evidence; never clobber one by accident.", file=sys.stderr)
        return 3
    out_dir.mkdir(parents=True, exist_ok=True)
    pack_md.write_text(pack.header + "\n" + pack.body)
    (out_dir / "read-list.txt").write_text(pack.readlist)
    print(f"  wrote {pack_md} and {out_dir/'read-list.txt'}")
    return 0


def rel(root: Path, p: str) -> str:
    path = Path(p)
    if path.is_absolute():
        return str(path.relative_to(root))
    # tolerate being run from a subdirectory
    cand = (Path.cwd() / path).resolve()
    try:
        return str(cand.relative_to(root))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# delta (E-005)
# ---------------------------------------------------------------------------

def cmd_delta(args) -> int:
    root = repo_root(Path.cwd())
    handoff = load_handoff(root, rel(root, args.handoff))
    entries, rev, _ = derive(root, handoff, args.role, args.range, [])
    listed = {e.path for e in entries if e.kind in ("full", "slice")}
    since = git(root, "rev-parse", args.since).strip()
    head = git(root, "rev-parse", "HEAD").strip()
    changed = [ln for ln in git(root, "diff", "--name-only", since, head).splitlines() if ln]
    n_commits = int(git(root, "rev-list", "--count", f"{since}..{head}").strip() or 0)

    out = [f"# Orientation delta {since[:8]}..{head[:8]} — commits={n_commits} "
           f"changed_files={len(changed)}",
           f"# handoff: {handoff.path}  role: {args.role}"]
    if len(changed) > 20 or n_commits > 10:
        out.append("# THRESHOLD EXCEEDED (>20 changed files or >10 commits) — re-orient "
                   "fresh instead of extending this pack (E-005).")
    out.append("")
    out.append("## Changed files ON the read-list — diffs verbatim")
    on = sorted(set(changed) & listed)
    if not on:
        out.append("(none — the pack's own read-list is untouched by this window)")
    for p in on:
        out.append("")
        out.append(f"=== diff {p} ===")
        out.append(git(root, "diff", since, head, "--", p).rstrip("\n"))
    out.append("")
    out.append("## Changed files OFF the read-list — names only (awareness)")
    off = sorted(set(changed) - listed)
    out += [f"- {p}" for p in off] or ["(none)"]
    text = "\n".join(out) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"delta: {args.out}  {len(text.encode()):,}B  ~{len(text.encode())//4:,} "
              f"tokens  commits={n_commits} changed={len(changed)}")
    else:
        sys.stdout.write(text)
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"^=== (.*) ===$")


def header_to_label(header: str) -> tuple[str, str | None]:
    """Section header -> (read-list label, kind) — the inverse of Entry.header().
    read-list.txt is path-first by contract, so `=== diff <p> ===` and
    `=== <p> ===` share one label and are told apart by kind."""
    if header.startswith("diff "):
        return header[5:], "diff"
    if header == "sweep":
        return "sweep", "sweep"
    if header.endswith(" (names only)"):
        return header[: -len(" (names only)")].rstrip("/"), "names"
    return header, None


def parse_pack(pack_md: Path) -> tuple[dict, list[tuple[str, str]]]:
    text = pack_md.read_text()
    lines = text.splitlines(keepends=True)
    meta: dict = {}
    sections: list[tuple[str, list[str]]] = []
    cur: list[str] | None = None
    for line in lines:
        m = SECTION_RE.match(line.rstrip("\n"))
        if m:
            sections.append((m.group(1), []))
            cur = sections[-1][1]
            continue
        if cur is None:
            if line.startswith("stamp:"):
                meta["stamp"] = line.split(":", 1)[1].strip().split()[0]
            elif line.startswith("handoff:"):
                meta["handoff"] = line.split(":", 1)[1].strip().split()[0]
            continue
        cur.append(line)
    out = []
    for name, buf in sections:
        body = "".join(buf)
        if body.endswith("\n\n"):
            body = body[:-1]          # drop the single separator blank line
        out.append((name, body))
    return meta, out


def cmd_verify(args) -> int:
    root = repo_root(Path.cwd())
    out_dir = Path(args.out_dir)
    out_dir = out_dir if out_dir.is_absolute() else root / out_dir
    pack_md = out_dir / "pack.md"
    rl_path = out_dir / "read-list.txt"
    if not pack_md.is_file() or not rl_path.is_file():
        print(f"verify: {out_dir} is not a pack directory (need pack.md + read-list.txt)",
              file=sys.stderr)
        return 2
    meta, sections = parse_pack(pack_md)
    rl_lines = [ln for ln in rl_path.read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    fails: list[str] = []
    warns: list[str] = []

    print(f"verify: {out_dir}")
    print(f"  stamp={meta.get('stamp','?')}  sections={len(sections)}  "
          f"read-list={len(rl_lines)}")
    if len(sections) != len(rl_lines):
        fails.append(f"section count {len(sections)} != read-list entries {len(rl_lines)}")

    stamp = meta.get("stamp")
    if stamp:
        head = git(root, "rev-parse", "HEAD").strip()
        if head != stamp:
            counts = git(root, "rev-list", "--left-right", "--count",
                         f"{stamp}...{head}", check=False).split()
            behind, ahead = (counts + ["?", "?"])[:2]
            warns.append(f"stamp != HEAD (HEAD is {ahead} commit(s) ahead of / {behind} "
                         f"behind the stamp) — expected for a committed pack; reconcile "
                         f"per the header sentence")

    labels: dict[str, set[str]] = {}
    for ln in rl_lines:
        parts = ln.split("  ")
        label = parts[0].strip()
        kind = parts[1].strip() if len(parts) > 1 else "?"
        labels.setdefault(label, set()).add(kind)

    checked = 0
    evicted_full = 0
    for header, body in sections:
        name, hkind = header_to_label(header)
        if hkind is not None:                      # diff / sweep / names sections
            continue
        if "full" not in labels.get(name, set()):  # a slice is not byte-checkable
            continue
        if body.startswith("[EVICTED — "):
            # The build step itself replaced this section with a disclosed
            # budget-eviction stub (see cost>remaining branch above) — that is
            # an honest "read directly if needed" pointer, not drift from the
            # stamp, so it must not be byte-diffed against the full source.
            evicted_full += 1
            continue
        try:
            src, _ = read_source(root, stamp or "HEAD", name)
        except PackError as exc:
            fails.append(f"{name}: {exc}")
            continue
        if not src.endswith("\n"):
            src += "\n"
        checked += 1
        if src != body:
            fails.append(f"{name}: FULL section differs from {stamp[:8]}:{name} "
                         f"({len(body)}B in pack vs {len(src)}B at the stamp)")
    print(f"  byte-diffed {checked} FULL section(s) against the stamp"
          + (f"; {evicted_full} more labeled full but evicted under budget "
             f"(disclosed stub, not byte-checked)" if evicted_full else ""))
    for w in warns:
        print(f"  WARN: {w}")
    for f in fails:
        print(f"  FAIL: {f}")
    print("  VERDICT: " + ("FAIL" if fails else "OK"))
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# score (E-006 Task B)
# ---------------------------------------------------------------------------

def _load_metrics():
    here = Path(__file__).resolve().parent
    mod_path = here / "jsonl-metrics.py"
    if not mod_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("jsonl_metrics", mod_path)
    mod = importlib.util.module_from_spec(spec)
    # register BEFORE exec: @dataclass resolves annotations via sys.modules
    sys.modules.setdefault("jsonl_metrics", mod)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def transcript_readset(transcript: Path) -> dict[str, int]:
    mod = _load_metrics()
    if mod is not None and hasattr(mod, "compute_readset"):
        counts, _prov = mod.compute_readset(transcript)
        return dict(counts)
    here = Path(__file__).resolve().parent
    proc = subprocess.run(
        [sys.executable, str(here / "jsonl-metrics.py"), "readset", str(transcript),
         "--json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise PackError(f"jsonl-metrics readset failed: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    out: dict[str, int] = {}
    for _name, d in data.items():
        for row in d["files"]:
            out[row["path"]] = out.get(row["path"], 0) + row["count"]
    return out


def pack_fileset(out_dir: Path) -> dict[str, str]:
    rl = out_dir / "read-list.txt"
    files: dict[str, str] = {}
    for ln in rl.read_text().splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        parts = ln.split("  ")
        label = parts[0].strip()
        kind = parts[1].strip() if len(parts) > 1 else "?"
        path = re.sub(r":\d+(-\d+)?$", "", label)
        if kind in ("sweep", "names"):
            continue          # synthetic sections: no packed content to score
        files.setdefault(path, kind)
        if kind == "full":
            files[path] = "full"
    return files


def normalize_read_path(p: str) -> str:
    """Fold a worktree-relative or absolute-repo read path to its canonical form
    (E-002 addendum 7 path-normalization gap): the P113 reviewer read
    `nyxloom.toml`/`dstdns-P113-LOG.md`/`REPORT.md`/two test files via their
    `.worktrees/p113-gate-argv-and-stale-tests/…` path, and the scorer's
    exact-path match reported them as "missing" though the *same file* by
    canonical path sat in that pack's "unused" list — the addendum measured
    this as undercounting the reviewer-shaped pack's ∩ from 6/22 (27.3%)
    toward ~11/22 (~50%)."""
    if p.startswith(ABS_REPO_PREFIX):
        p = p[len(ABS_REPO_PREFIX):]
    return WORKTREE_PATH_RE.sub("", p)


def cmd_score(args) -> int:
    root = repo_root(Path.cwd())
    out_dir = Path(args.out_dir)
    out_dir = out_dir if out_dir.is_absolute() else root / out_dir
    pack_files = pack_fileset(out_dir)
    raw_read = transcript_readset(Path(args.transcript))
    read: dict[str, int] = {}
    n_normalized = 0
    for p, c in raw_read.items():
        np = normalize_read_path(p)
        if np != p:
            n_normalized += 1
        read[np] = read.get(np, 0) + c
    meta_files = {f"{out_dir}/pack.md", f"{out_dir}/read-list.txt", "pack.md",
                  "read-list.txt"}
    read = {p: c for p, c in read.items()
            if p not in meta_files and not p.endswith("/pack.md")
            and not p.endswith("/read-list.txt")}

    used = {p: read[p] for p in pack_files if p in read}
    unused = sorted(p for p in pack_files if p not in read)
    missing = sorted((p for p in read if p not in pack_files), key=lambda p: -read[p])

    n_pack, n_read = len(pack_files), len(read)
    print(f"score: pack={out_dir.name}  transcript={Path(args.transcript).name}")
    print(f"  path-normalization: {n_normalized} read path(s) folded "
          f"(.worktrees/<name>/ prefix or absolute repo-root prefix stripped, "
          f"E-002 addendum 7)")
    print(f"  pack files={n_pack}  read-set={n_read}  ∩={len(used)}  "
          f"∩/read-set={len(used)/n_read*100 if n_read else 0:.1f}%  "
          f"∩/pack={len(used)/n_pack*100 if n_pack else 0:.1f}%")
    print(f"\n  USED ({len(used)}) — pack files the agent actually read:")
    for p, c in sorted(used.items(), key=lambda kv: -kv[1]):
        print(f"    {c:>3}x  {pack_files[p]:<6} {p}")
    print(f"\n  UNUSED ({len(unused)}) — dead weight (E-006's dominant failure mode):")
    for p in unused:
        print(f"         {pack_files[p]:<6} {p}")
    print(f"\n  MISSING ({len(missing)}) — read but NOT packed (curation gaps):")
    for p in missing:
        print(f"    {read[p]:>3}x         {p}")
    if args.json:
        print(json.dumps(dict(pack=len(pack_files), read_set=len(read),
                              used=sorted(used), unused=unused, missing=missing,
                              normalized=n_normalized),
                         indent=2))
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a role-aware orientation pack")
    b.add_argument("--handoff", required=True)
    b.add_argument("--role", required=True,
                   choices=["implementer", "carver", "reviewer"])
    b.add_argument("--range", help="reviewer only, e.g. main...<tip>")
    b.add_argument("--out-dir")
    b.add_argument("--extra", action="append", metavar="PATH[:a-b]")
    b.add_argument("--slice", action="append", metavar="PATH:a-b",
                   help="pack this scope.touch file as a SLICE, not FULL (small-edit target)")
    b.add_argument("--exclude", action="append", metavar="GLOB",
                   help="drop derived sections whose path matches (fnmatch); "
                        "each drop is named in a warning, never silent")
    b.add_argument("--budget", type=int, default=None, metavar="TOKENS",
                   help=f"hard token cap, enforced by evicting lowest-priority "
                        f"sections first (default {TOKEN_BUDGET_DEFAULT:,}; pass 0 "
                        f"to disable and fall back to TOKEN_WARN-only warning). "
                        f"The handoff + scope.touch/diff files are never evicted.")
    b.add_argument("--include-generated", action="append", metavar="GLOB",
                   help="force FULL content for a generated-artifact path that "
                        "would otherwise be auto-downgraded to a names-only stub "
                        "(openapi.json, schema.d.ts, package-lock.json, ...)")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--force", action="store_true",
                   help="overwrite an existing pack.md in --out-dir")
    b.set_defaults(func=cmd_build)

    d = sub.add_parser("delta", help="E-005 append-delta since a rev")
    d.add_argument("--handoff", required=True)
    d.add_argument("--since", required=True)
    d.add_argument("--role", default="implementer",
                   choices=["implementer", "carver", "reviewer"])
    d.add_argument("--range", help="reviewer role only")
    d.add_argument("--out")
    d.set_defaults(func=cmd_delta)

    v = sub.add_parser("verify", help="check a built pack against its stamp")
    v.add_argument("out_dir")
    v.set_defaults(func=cmd_verify)

    s = sub.add_parser("score", help="pack fileset vs an agent's real read-set")
    s.add_argument("out_dir")
    s.add_argument("--transcript", required=True)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_score)

    args = ap.parse_args()
    try:
        return args.func(args)
    except PackError as exc:
        print(f"pack.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
