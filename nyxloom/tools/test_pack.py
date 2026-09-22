"""Tests for the orientation-pack builder (tools/pack.py).

TOOLING TEST, not a project's own app gate. Run directly:

    python -m pytest tools/test_pack.py -q

It builds a throwaway git repo per test (fake handoff + frontmatter + a
decisions.md with D-sections + a two-commit diff range) and asserts the
behaviours the E-00x rules require: the role read-lists differ as specified,
slices expand to construct boundaries, verify() catches a tampered FULL section,
and score() computes the pack-vs-read-set overlap from a synthetic JSONL.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load_pack():
    spec = importlib.util.spec_from_file_location("pack_mod", HERE / "pack.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pack_mod"] = mod          # @dataclass needs the module registered
    spec.loader.exec_module(mod)
    return mod


pack = _load_pack()


# ---------------------------------------------------------------------------
# fixture repo
# ---------------------------------------------------------------------------

HANDOFF = '''---
schema_version: 1
id: fake-P07-sample-package
project: fake
title: "a sample package"
input_revision: "deadbeef"
gates: [test-runner]
scope:
  touch:
    - "src/widget.py"                  # the edit target
    - "docs/notes.md"                  # prose to rewrite
    - "src/pkg/"                       # a DIRECTORY in scope
  forbid:
    - "src/secret.py"                  # never edited
oracles:
  - id: O1
    observable: "tests/conftest.py fixtures stay green"
    gate: "test-runner"
gates_note: ignored
---

# P07 sample

**One sentence:** do the thing.

## Context to read first (in this order)

1. `nyxloom-trove/decisions.md` **D-001** and D-002 — why this package exists.
2. `src/helper.py:12-13` — the helper the widget calls.
3. `nyxloom-trove/GUIDE.md` §2 — the environment contract.
4. `src/small.py` whole — small enough to pack full.

## Contract

1. Edit the widget.
'''

DECISIONS = """# Decisions

## D-001 · 2026-01-01 · the first decision
body one
more one

## D-002 · 2026-01-02 · the second decision
body two

## D-003 · 2026-01-03 · unrelated
body three
"""

GUIDE = """# guide

## 1. Ground rules
one

## 2. Environment setup contract
setup line
```bash
# this is a shell comment inside a fence, NOT a heading
echo hi
```
still section two

## 3. Multi-stack
three
"""

WIDGET = '''"""widget module."""

import os


def alpha(x):
    """alpha."""
    y = x + 1
    return y


class Widget:
    def method(self):
        return 1


def omega():
    return 0
'''

HELPER = '''def helper_one():
    a = 1
    b = 2
    return a + b


def helper_two():
    return 3
'''

NYXLOOM_TOML = """[project]
name = "fake"

[gates.test-runner]
argv = ["scripts/gate-helper.sh", "pytest", "tests/"]
timeout = 900

[gates.test-runner.env]
MOCK_MODE = "true"

[gates.release]
argv = ["true"]
"""

ASSAY_TOML = """[lanes.mock]
argv = ["pytest", "tests/"]

[lanes.mock.judge]
model = "x"

[lanes.other]
argv = ["true"]
"""


def _run(root: Path, *args: str) -> str:
    proc = subprocess.run(args, cwd=root, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "git", "init", "-q", "-b", "main")
    _run(root, "git", "config", "user.email", "t@example.com")
    _run(root, "git", "config", "user.name", "t")
    _write(root, "nyxloom-trove/handoffs/fake-P07-sample-package.md", HANDOFF)
    _write(root, "nyxloom-trove/decisions.md", DECISIONS)
    _write(root, "nyxloom-trove/GUIDE.md", GUIDE)
    _write(root, "nyxloom-trove/nyxloom.toml", NYXLOOM_TOML)
    _write(root, "assay.toml", ASSAY_TOML)
    _write(root, "src/widget.py", WIDGET)
    _write(root, "src/helper.py", HELPER)
    _write(root, "src/small.py", "SMALL = 1\n")
    _write(root, "src/secret.py", "SECRET = 1\n")
    _write(root, "src/pkg/__init__.py", "")
    _write(root, "src/pkg/inner.py", "INNER = 1\n")
    _write(root, "src/doomed.py", "def doomed_symbol():\n    return 1\n")
    _write(root, "src/consumer.py", "from src.doomed import doomed_symbol\n")
    _write(root, "docs/notes.md", "# notes\n\nold prose\n")
    _write(root, "tests/conftest.py", "FIXTURE = 1\n")
    # E-002 addendum 7 fixture additions (reviewer-only rules 7a-7d).
    _write(root, "scripts/testing-exec.sh", "#!/bin/bash\necho gate\n")
    _write(root, "scripts/schema-gate.sh", "#!/bin/bash\necho schema\n")
    _write(root, "scripts/gate-helper.sh", "#!/bin/bash\necho helper\n")
    _write(root, "src/routes.py",
           'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n'
           '@router.get("/api/widget")\ndef get_widget():\n    return {}\n')
    _write(root, "tests/security/test_widget_routes.py",
           '"""Security checks."""\n\nTOUCHED_ROUTE = "/api/widget/save"\n')
    _write(root, "tests/security/test_unrelated_security.py",
           '"""Unrelated security checks."""\n\nOTHER = "/api/other"\n')
    _write(root, "tests/test_widget_omega.py",
           "from src.widget import omega\n\n\ndef test_omega():\n    assert omega() == 0\n")
    for i in range(8):
        _write(root, f"tests/test_widget_alpha_{i}.py",
               "from src.widget import alpha\n\n\n"
               f"def test_alpha_{i}():\n    assert alpha(1) == 2\n")
    _run(root, "git", "add", "-A")
    _run(root, "git", "commit", "-qm", "base")
    return root


@pytest.fixture()
def repo_with_branch(repo: Path) -> Path:
    _run(repo, "git", "checkout", "-qb", "feature")
    (repo / "src/widget.py").write_text(WIDGET.replace("def omega():\n    return 0\n", ""))
    (repo / "docs/notes.md").write_text("# notes\n\nnew prose\n")
    _run(repo, "git", "rm", "-q", "src/doomed.py")
    _write(repo, "nyxloom-trove/reports/fake-P07-LOG.md", "# LOG\n\nran the gate\n")
    _write(repo, "nyxloom-trove/reports/fake-P07-REPORT.md", "# REPORT\n\nresults\n")
    # E-002 addendum 7b: a new test file sharing an import with 8 base siblings
    # (cap must trim to 6, log the overflow).
    _write(repo, "tests/test_widget_alpha.py",
           "from src.widget import alpha\n\n\ndef test_alpha():\n    assert alpha(1) == 2\n")
    # E-002 addendum 7d: a new route added by this diff; test_widget_routes.py
    # (written at base) already contains its literal path.
    routes_text = (repo / "src/routes.py").read_text()
    routes_text += '\n\n@router.post("/api/widget/save")\ndef save_widget():\n    return {}\n'
    (repo / "src/routes.py").write_text(routes_text)
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "feature work")
    _run(repo, "git", "checkout", "-q", "main")
    return repo


HANDOFF_PATH = "nyxloom-trove/handoffs/fake-P07-sample-package.md"


def _labels(p) -> set[str]:
    """Section headers — the keys a reader of pack.md actually sees."""
    return {e.header() for e in p.entries}


def _kinds(p) -> dict[str, str]:
    return {e.header(): e.kind for e in p.entries}


# ---------------------------------------------------------------------------
# handoff parsing
# ---------------------------------------------------------------------------

def test_frontmatter_parses_scope_and_ids(repo: Path):
    h = pack.load_handoff(repo, HANDOFF_PATH)
    assert h.id == "fake-P07-sample-package"
    assert h.slug == "sample-package"
    assert h.package_prefix == "fake-P07"
    assert h.input_revision == "deadbeef"
    assert h.touch == ["src/widget.py", "docs/notes.md", "src/pkg/"]
    assert h.forbid == ["src/secret.py"]
    assert h.gates == ["test-runner"]


def test_minimal_frontmatter_fallback_matches_yaml(repo: Path):
    """The PyYAML-free path must yield the same scope/id facts."""
    raw = (repo / HANDOFF_PATH).read_text()
    fm_text, _body = pack.split_frontmatter(raw)
    fallback = pack._minimal_frontmatter(fm_text)
    assert fallback["id"] == "fake-P07-sample-package"
    assert fallback["scope"]["touch"] == ["src/widget.py", "docs/notes.md", "src/pkg/"]
    assert fallback["scope"]["forbid"] == ["src/secret.py"]
    assert fallback["gates"] == ["test-runner"]


# ---------------------------------------------------------------------------
# role read-lists
# ---------------------------------------------------------------------------

def test_implementer_readlist_rules(repo: Path):
    p = pack.build_pack(repo, HANDOFF_PATH, "implementer")
    kinds = _kinds(p)
    # handoff + every scope.touch FILE is FULL (E-002 addendum 4/6)
    assert kinds[HANDOFF_PATH] == "full"
    assert kinds["src/widget.py"] == "full"
    assert kinds["docs/notes.md"] == "full"
    # a scope.touch DIRECTORY is names-only
    assert kinds["src/pkg/ (names only)"] == "names"
    assert "src/pkg/inner.py" in p.body and "INNER = 1" not in p.body
    # context items: path:a-b -> slice, bare small file -> full, §N -> md slice
    assert any(lbl.startswith("src/helper.py:") for lbl in kinds)
    assert kinds["src/small.py"] == "full"
    assert any(lbl.startswith("nyxloom-trove/GUIDE.md:") for lbl in kinds)
    # ledger D-refs -> D-section slices, decisions.md never FULL
    dsec = [lbl for lbl in kinds if lbl.startswith("nyxloom-trove/decisions.md")]
    assert len(dsec) == 2 and all(":" in lbl for lbl in dsec)
    # gate-adjacent artifacts ALWAYS (E-002 addendum 5)
    assert any(lbl.startswith("nyxloom-trove/nyxloom.toml:") for lbl in kinds)
    assert any(lbl.startswith("assay.toml:") for lbl in kinds)
    assert kinds["tests/conftest.py"] == "full"      # named by an oracle
    # forbid: names only, content never packed
    assert kinds["forbidden (names only)"] == "names"
    assert "src/secret.py" in p.body and "SECRET = 1" not in p.body


def test_gate_slice_is_the_declared_gate_table_only(repo: Path):
    p = pack.build_pack(repo, HANDOFF_PATH, "implementer")
    lbl = next(l for l in _labels(p) if l.startswith("nyxloom-trove/nyxloom.toml:"))
    body = _section_body(p, lbl)
    assert "[gates.test-runner]" in body
    assert "[gates.test-runner.env]" in body     # subtable belongs to its parent
    assert "[gates.release]" not in body         # the next top-level table ends it


def test_reviewer_readlist_rules(repo_with_branch: Path):
    p = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer",
                        rng="main...feature")
    kinds = _kinds(p)
    # every diff file gets a diff section; surviving ones also FULL at the tip
    assert kinds["diff src/widget.py"] == "diff"
    assert kinds["src/widget.py"] == "full"
    assert kinds["diff src/doomed.py"] == "diff"
    assert "src/doomed.py" not in kinds          # deleted at the tip: diff only
    # prior-round LOG/REPORT FULL (E-006 conclusion (a))
    assert kinds["nyxloom-trove/reports/fake-P07-LOG.md"] == "full"
    assert kinds["nyxloom-trove/reports/fake-P07-REPORT.md"] == "full"
    # standing set: GUIDE §2 AND §3
    guide = sorted(l for l in kinds if l.startswith("nyxloom-trove/GUIDE.md:"))
    assert len(guide) == 2
    # pre-tabulated consumer sweep
    assert kinds["sweep"] == "sweep"
    sweep = _section_body(p, "sweep")
    assert "doomed_symbol" in sweep and "src/consumer.py" in sweep


def test_reviewer_drops_implementer_dead_weight(repo_with_branch: Path):
    """E-006: 75% of an implementer pack is dead weight for a reviewer."""
    impl = pack.build_pack(repo_with_branch, HANDOFF_PATH, "implementer")
    rev = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer",
                          rng="main...feature")
    i, r = _labels(impl), _labels(rev)
    assert i != r
    # implementer-only: the forbid table, the scope directory listing, gate slices
    impl_only = {"forbidden (names only)", "src/pkg/ (names only)"}
    assert impl_only <= i
    assert not (impl_only & r)
    # reviewer-only: diffs, the sweep, the prior-round artifacts
    assert "sweep" in r and "sweep" not in i
    assert any(l.startswith("diff ") for l in r)
    assert not any(l.startswith("diff ") for l in i)
    assert "nyxloom-trove/reports/fake-P07-LOG.md" in r


# ---------------------------------------------------------------------------
# E-002 addendum 7 — reviewer pack-curation gap (P113)
# ---------------------------------------------------------------------------

def test_reviewer_gate_scripts_always_packed(repo_with_branch: Path):
    """7a: testing-exec.sh/schema-gate.sh ALWAYS, plus a script named in the
    declared gate's argv."""
    p = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer",
                        rng="main...feature")
    kinds = _kinds(p)
    assert kinds["scripts/testing-exec.sh"] == "full"
    assert kinds["scripts/schema-gate.sh"] == "full"
    assert kinds["scripts/gate-helper.sh"] == "full"  # named in [gates.test-runner] argv


def test_reviewer_test_sibling_sweep_capped_and_logged(repo_with_branch: Path):
    """7b: siblings sharing a top-level import are packed FULL, capped at 6 per
    directory, with the overflow logged as a warning."""
    p = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer",
                        rng="main...feature")
    kinds = _kinds(p)
    matched = [f"tests/test_widget_alpha_{i}.py" for i in range(8)]
    packed = [m for m in matched if kinds.get(m) == "full"]
    assert len(packed) == 6                     # capped at 6, not all 8
    # a sibling with a DIFFERENT top-level import must not be swept in
    assert "tests/test_widget_omega.py" not in kinds
    assert any("test-sibling cap" in w and "capped" in w for w in p.warnings)


def test_reviewer_oracle_named_test_file_packed(repo_with_branch: Path):
    """7c: a test file path named in an oracle's `observable` text is FULL,
    even though the reviewer role otherwise never packs the implementer's
    oracle-named conftest.py."""
    p = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer",
                        rng="main...feature")
    assert _kinds(p)["tests/conftest.py"] == "full"


def test_reviewer_security_route_reference_packed(repo_with_branch: Path):
    """7d: a tests/security/ file referencing a route string the diff touches
    is FULL; one referencing an unrelated route is not."""
    p = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer",
                        rng="main...feature")
    kinds = _kinds(p)
    assert kinds["tests/security/test_widget_routes.py"] == "full"
    assert "tests/security/test_unrelated_security.py" not in kinds


def test_carver_role_matches_implementer_shape(repo: Path):
    a = _labels(pack.build_pack(repo, HANDOFF_PATH, "implementer"))
    b = _labels(pack.build_pack(repo, HANDOFF_PATH, "carver"))
    assert a == b


def test_reviewer_requires_range(repo: Path):
    with pytest.raises(pack.PackError):
        pack.build_pack(repo, HANDOFF_PATH, "reviewer")


def test_slice_override_packs_a_touch_file_as_slice(repo: Path):
    full = _kinds(pack.build_pack(repo, HANDOFF_PATH, "implementer"))
    assert full["src/widget.py"] == "full"
    sliced = pack.build_pack(repo, HANDOFF_PATH, "implementer",
                             slices=["src/widget.py:1-2"])
    kinds = _kinds(sliced)
    assert kinds.get("src/widget.py:1-2") == "slice", sorted(kinds)   # honoured as given, not expanded
    assert "src/widget.py" not in kinds
    assert "COMPREHENSION ONLY" in sliced.readlist   # the reason lives in read-list.txt
    with pytest.raises(pack.PackError):
        pack.build_pack(repo, HANDOFF_PATH, "implementer", slices=["src/widget.py"])


def test_new_scope_directory_is_a_warning_not_an_error(repo: Path):
    text = (repo / HANDOFF_PATH).read_text()
    text = text.replace('    - "src/widget.py"', '    - "src/widget.py"\n    - "brand-new-dir/"', 1)
    (repo / HANDOFF_PATH).write_text(text)
    p = pack.build_pack(repo, HANDOFF_PATH, "implementer")
    assert any("new directory" in w and "brand-new-dir/" in w for w in p.warnings)


def test_missing_extra_path_is_a_hard_error(repo: Path):
    with pytest.raises(pack.PackError):
        pack.build_pack(repo, HANDOFF_PATH, "implementer", extras=["src/nope.py"])


# ---------------------------------------------------------------------------
# slice expansion
# ---------------------------------------------------------------------------

def _section_body(p, label: str) -> str:
    marker = f"=== {label} ===\n"
    start = p.body.index(marker) + len(marker)
    nxt = p.body.find("\n=== ", start)
    return p.body[start:nxt if nxt != -1 else len(p.body)]


def test_python_slice_expands_to_enclosing_def(repo: Path):
    text = (repo / "src/widget.py").read_text()
    a, b = pack.expand_slice(text, 8, 8, "src/widget.py")     # inside alpha's body
    lines = text.splitlines()
    assert lines[a - 1].startswith("def alpha")
    assert "return y" in "\n".join(lines[a - 1:b])
    assert "class Widget" not in "\n".join(lines[a - 1:b])


def test_python_slice_expands_to_enclosing_class_for_a_method(repo: Path):
    text = (repo / "src/widget.py").read_text()
    lines = text.splitlines()
    mline = next(i for i, l in enumerate(lines, 1) if "return 1" in l)
    a, b = pack.expand_slice(text, mline, mline, "src/widget.py")
    assert lines[a - 1].startswith("class Widget")
    assert "def omega" not in "\n".join(lines[a - 1:b])


def test_toml_slice_expands_to_the_table(repo: Path):
    text = (repo / "nyxloom-trove/nyxloom.toml").read_text()
    lines = text.splitlines()
    n = next(i for i, l in enumerate(lines, 1) if l.startswith("timeout"))
    a, b = pack.expand_slice(text, n, n, "nyxloom-trove/nyxloom.toml")
    chunk = "\n".join(lines[a - 1:b])
    assert chunk.startswith("[gates.test-runner]")
    assert "[gates.release]" not in chunk


def test_markdown_slice_expands_to_the_heading_and_ignores_fenced_hashes(repo: Path):
    text = (repo / "nyxloom-trove/GUIDE.md").read_text()
    rng = pack.find_markdown_section(text, "2")
    chunk = "\n".join(text.splitlines()[rng[0] - 1:rng[1]])
    assert chunk.startswith("## 2. Environment setup contract")
    # a `#` comment inside a ``` fence must NOT terminate the section
    assert "shell comment inside a fence" in chunk
    assert "still section two" in chunk
    assert "## 3." not in chunk


def test_decision_section_slice_stops_at_the_next_d_heading(repo: Path):
    text = (repo / "nyxloom-trove/decisions.md").read_text()
    rng = pack.find_decision_section(text, "001")
    chunk = "\n".join(text.splitlines()[rng[0] - 1:rng[1]])
    assert "the first decision" in chunk and "body one" in chunk
    assert "D-002" not in chunk


# ---------------------------------------------------------------------------
# write / verify / delta / score
# ---------------------------------------------------------------------------

def _write_pack(repo: Path, role: str, out: Path, rng=None):
    p = pack.build_pack(repo, HANDOFF_PATH, role, rng=rng)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pack.md").write_text(p.header + "\n" + p.body)
    (out / "read-list.txt").write_text(p.readlist)
    return p


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_verify_ok_then_catches_a_tampered_full_section(repo: Path, tmp_path, monkeypatch,
                                                        capsys):
    out = tmp_path / "packdir"
    _write_pack(repo, "implementer", out)
    monkeypatch.chdir(repo)
    assert pack.cmd_verify(_Args(out_dir=str(out))) == 0

    text = (out / "pack.md").read_text()
    tampered = text.replace("SMALL = 1", "SMALL = 999")
    assert tampered != text
    (out / "pack.md").write_text(tampered)
    assert pack.cmd_verify(_Args(out_dir=str(out))) == 1
    assert "FULL section differs" in capsys.readouterr().out


def test_verify_catches_section_count_mismatch(repo: Path, tmp_path, monkeypatch, capsys):
    out = tmp_path / "packdir"
    _write_pack(repo, "implementer", out)
    monkeypatch.chdir(repo)
    rl = (out / "read-list.txt").read_text().splitlines()
    (out / "read-list.txt").write_text("\n".join(rl[:-1]) + "\n")
    assert pack.cmd_verify(_Args(out_dir=str(out))) == 1
    assert "section count" in capsys.readouterr().out


def test_delta_diffs_readlist_files_and_names_the_rest(repo: Path, tmp_path, monkeypatch,
                                                       capsys):
    base = _run(repo, "git", "rev-parse", "HEAD").strip()
    (repo / "src/widget.py").write_text(WIDGET + "\n# appended\n")
    _write(repo, "src/unrelated.py", "X = 1\n")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "move on")
    monkeypatch.chdir(repo)
    outfile = tmp_path / "delta.md"
    pack.cmd_delta(_Args(handoff=HANDOFF_PATH, since=base, role="implementer",
                         range=None, out=str(outfile)))
    text = outfile.read_text()
    assert "=== diff src/widget.py ===" in text        # on the read-list -> verbatim diff
    assert "+# appended" in text
    assert "- src/unrelated.py" in text                # off the read-list -> name only
    assert "=== diff src/unrelated.py ===" not in text


def test_delta_threshold_report(repo: Path, tmp_path, monkeypatch):
    base = _run(repo, "git", "rev-parse", "HEAD").strip()
    for i in range(21):
        _write(repo, f"src/gen{i}.py", f"X = {i}\n")
    _run(repo, "git", "add", "-A")
    _run(repo, "git", "commit", "-qm", "many files")
    monkeypatch.chdir(repo)
    outfile = tmp_path / "delta.md"
    pack.cmd_delta(_Args(handoff=HANDOFF_PATH, since=base, role="implementer",
                         range=None, out=str(outfile)))
    assert "THRESHOLD EXCEEDED" in outfile.read_text()


def _synthetic_transcript(path: Path, reads: list[str], bash: list[str]) -> None:
    lines = []
    for i, fp in enumerate(reads):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [{"type": "tool_use", "id": f"t{i}", "name": "Read",
                                     "input": {"file_path": fp}}]},
        }))
    for i, cmd in enumerate(bash):
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 1, "output_tokens": 1},
                        "content": [{"type": "tool_use", "id": f"b{i}", "name": "Bash",
                                     "input": {"command": cmd}}]},
        }))
    path.write_text("\n".join(lines) + "\n")


@pytest.mark.xfail(
    reason="pack.py's score command requires a co-located jsonl-metrics.py; "
    "that file remains in dstdns (nyxloom-trove/orientation/jsonl-metrics.py) "
    "per an existing cross-repo placement decision, not yet reconciled with "
    "pack.py's relocation here -- see the NL backlog entry filed alongside "
    "this move (2026-09-22).",
    strict=True,
)
def test_score_computes_used_unused_missing(repo: Path, tmp_path, monkeypatch, capsys):
    out = tmp_path / "packdir"
    p = _write_pack(repo, "implementer", out)
    transcript = tmp_path / "session.jsonl"
    _synthetic_transcript(
        transcript,
        reads=["/workspaces/dstdns/src/widget.py",         # in the pack -> used
               "/workspaces/dstdns/src/never_packed.py"],  # not in the pack -> missing
        bash=["cat /workspaces/dstdns/tests/conftest.py"],  # in the pack -> used
    )
    monkeypatch.chdir(repo)
    rc = pack.cmd_score(_Args(out_dir=str(out), transcript=str(transcript), json=False))
    assert rc == 0
    text = capsys.readouterr().out
    assert "src/widget.py" in text
    assert "tests/conftest.py" in text
    assert "src/never_packed.py" in text
    # structural assertions on the tables themselves
    used = text.split("USED (")[1].split(")")[0]
    missing = text.split("MISSING (")[1].split(")")[0]
    assert int(used) == 2
    assert int(missing) == 1
    # a names-only table is not packed CONTENT, so it never counts as a pack file
    assert "src/secret.py" not in text
    unused_block = text.split("UNUSED (")[1]
    assert "docs/notes.md" in unused_block  # an unread pack file IS dead weight


def test_score_pack_fileset_drops_synthetic_sections(repo: Path, tmp_path):
    out = tmp_path / "packdir"
    _write_pack(repo, "implementer", out)
    files = pack.pack_fileset(out)
    assert "forbidden" not in files          # a names-only table is not a file
    assert "src/pkg" not in files            # nor is a names-only directory listing
    assert "src/widget.py" in files and files["src/widget.py"] == "full"
    assert "nyxloom-trove/decisions.md" in files   # slice labels lose their range


# ---------------------------------------------------------------------------
# score: path normalization (E-002 addendum 7)
# ---------------------------------------------------------------------------

def test_normalize_read_path_folds_worktree_and_absolute_prefixes():
    assert pack.normalize_read_path(
        ".worktrees/p113-gate-argv-and-stale-tests/src/widget.py") == "src/widget.py"
    assert pack.normalize_read_path(
        "/workspaces/dstdns/src/widget.py") == "src/widget.py"
    assert pack.normalize_read_path("src/widget.py") == "src/widget.py"   # already canonical


@pytest.mark.xfail(
    reason="pack.py's score command requires a co-located jsonl-metrics.py; "
    "that file remains in dstdns (nyxloom-trove/orientation/jsonl-metrics.py) "
    "per an existing cross-repo placement decision, not yet reconciled with "
    "pack.py's relocation here -- see the NL backlog entry filed alongside "
    "this move (2026-09-22).",
    strict=True,
)
def test_score_folds_worktree_path_duplicate_into_canonical_read(
        repo: Path, tmp_path, monkeypatch, capsys):
    """The measured P113 artifact: the same file read twice, once via a
    `.worktrees/<branch>/...` path and once via the canonical path, must be
    counted as ONE used file with a merged read count — not a spurious
    'missing' entry alongside an 'unused' one."""
    out = tmp_path / "packdir"
    _write_pack(repo, "implementer", out)
    transcript = tmp_path / "session.jsonl"
    _synthetic_transcript(
        transcript,
        reads=[".worktrees/some-branch/src/widget.py",   # worktree-relative
               "/workspaces/dstdns/src/widget.py"],       # absolute, same canonical file
        bash=[],
    )
    monkeypatch.chdir(repo)
    rc = pack.cmd_score(_Args(out_dir=str(out), transcript=str(transcript), json=False))
    assert rc == 0
    text = capsys.readouterr().out
    assert "path-normalization: 1 read path(s) folded" in text
    used_block = text.split("USED (")[1].split("UNUSED")[0]
    assert "src/widget.py" in used_block
    assert "  2x" in used_block          # the two reads merged into one 2x count
    missing_count = int(text.split("MISSING (")[1].split(")")[0])
    assert missing_count == 0            # no spurious "missing" entry


def test_pack_outputs_are_never_pack_inputs():
    rl = pack.ReadList()
    rl.add(pack.Entry("nyxloom-trove/orientation/x/pack.md", "full", "ctx"))
    rl.add(pack.Entry("nyxloom-trove/orientation/x/read-list.txt", "full", "ctx"))
    rl.add(pack.Entry("nyxloom-trove/orientation/x/sweep-tables.md", "full", "ctx"))
    assert [e.path for e in rl.entries()] == ["nyxloom-trove/orientation/x/sweep-tables.md"]


def test_binary_blob_becomes_a_stub_not_a_crash():
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
    out = pack._decode_or_stub(png, "img/logo.png")
    assert out.startswith("(binary file, 72 bytes") and "img/logo.png" in out
    assert pack._decode_or_stub("h\xe9llo".encode("latin-1"), "x.txt").startswith("h")   # lone invalid byte is replaced, not fatal


def test_reviewer_pure_renames_are_names_only_and_big_small_diff_files_are_diff_only(repo: Path):
    import subprocess
    def g(*a): subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("checkout", "-q", "-b", "tip")
    (repo / "big.txt").write_text("x" * (pack.DIFF_ONLY_MIN_CHARS + 10) + "\n")
    g("add", "big.txt"); g("commit", "-qm", "big")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    g("mv", "src/widget.py", "src/moved_widget.py")
    with open(repo / "big.txt", "a") as fh: fh.write("one more line\n")
    g("add", "-A"); g("commit", "-qm", "move + tiny edit")
    p = pack.build_pack(repo, HANDOFF_PATH, "reviewer", rng=f"{base}...HEAD")
    kinds = _kinds(p)
    assert kinds.get("renames") == "names"
    assert "src/moved_widget.py" not in kinds and "src/widget.py" not in kinds
    assert kinds.get("diff big.txt") == "diff" or any(e.kind == "diff" and e.path == "big.txt" for e in p.entries)
    assert not any(e.kind == "full" and e.path == "big.txt" for e in p.entries)
    assert any("R100" in w or "renames" in w for w in p.warnings) and any("diff-only" in w for w in p.warnings)


def test_exclude_drops_sections_loudly(repo: Path):
    base = pack.build_pack(repo, HANDOFF_PATH, "implementer")
    assert "docs/notes.md" in _kinds(base)
    p = pack.build_pack(repo, HANDOFF_PATH, "implementer",
                        excludes=["docs/*.md", "nothing/matches/*"])
    assert "docs/notes.md" not in _kinds(p)
    assert "src/widget.py" in _kinds(p)
    # every drop is named; a glob that matched nothing is called out (no silent knob)
    assert any(w.startswith("excluded by --exclude") and "docs/notes.md" in w for w in p.warnings)
    assert any("nothing/matches/* matched nothing" in w for w in p.warnings)


def test_added_file_is_full_only_never_diff_plus_full(repo_with_branch: Path):
    p = pack.build_pack(repo_with_branch, HANDOFF_PATH, "reviewer", rng="main...feature")
    added = pack.added_files(repo_with_branch, "main", "feature")
    assert added, "fixture must add at least one file on the branch"
    for a in added:
        kinds = [e.kind for e in p.entries if e.path == a]
        assert kinds == ["full"], (a, kinds)
