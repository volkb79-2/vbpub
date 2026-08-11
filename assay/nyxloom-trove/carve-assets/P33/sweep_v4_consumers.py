#!/usr/bin/env python3
"""Inventory every consumer of a frozen expected-artifact inside the REGISTERED
GATE's transitive execution path. Carver-owned locked asset.

History, because it explains the shape:

* Round 1 found the gate running P26's locked v4 acceptance suite. The repair
  retired that invocation and added one scanner pattern for it.
* Round 2 found `gate/python/qualify_topos.py` comparing against locked **P25**
  templates at the next gate step. The repair produced v1 of this sweep.
* Round 3 found v1's own closure claim false on five independent grounds, with a
  real missed consumer: `gate/distribution/release_wheel.py`.

A-141's rule — *a defect class closes when its inventory closes, not when its
instance does* — applies to the inventory tool as much as to the defect. v2
closes round 3's five gaps:

1. **Seeds.** The gate's primary Python invocation is `assay run tester-unified`,
   whose argv lives in `assay.toml`, not in the gate script. v1 never read it and
   reached `tests/` only incidentally, via a failure-only diagnostic line. v2
   seeds from the gate script AND `assay.toml`'s declared argv AND the gate
   script's inline `python - <<PYEOF` heredocs.
2. **Subprocess targets.** `qualify_topos.py` shells out to
   `gate/distribution/release_wheel.py` via `/`-joined components. v2 applies the
   same component logic to executable targets, so a shelled-to helper enters the
   closure.
3. **Dotted imports.** v1 resolved `a.b.c` by leaf name only, so
   `src/assay/adapters/**` and `src/assay/coverage_parsers/**` were structurally
   unreachable. v2 resolves through the real package layout.
4. **`from . import X`.** `ImportFrom` with `module=None` was skipped — nine
   occurrences, and `safeio.py` provably fell out. v2 handles it.
5. **The predicate.** v1 asked "does the text mention one of four directory
   names, and match one of five regexes". That missed `read_bytes() ==` against
   locked P24 assets entirely. v2 asks the real question: *does this file read a
   path under a carver-owned frozen tree, by any idiom, and compare it?*

Completeness is not provable for a dynamic language and this does not claim it.
What it claims is that the five demonstrated gaps are closed, and
`test_sweep_finds_a_planted_decoy_consumer` in the locked suite plants a
synthetic consumer the sweep must find — so a regression in this file's own logic
is caught by the gate rather than by a fifth review round.

Run from the assay project root:
    python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py
    python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py --json
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "tools" / "tester-unified-gate.sh"
LANE_CONFIG = ROOT / "assay.toml"
SRC = ROOT / "src"

#: Trees holding a carver-frozen expectation. A file that reads one of these and
#: compares is a consumer, whatever idiom it uses. Derived from where frozen
#: expectations actually live, not from a list of pretty names.
FROZEN_TREES = (
    "nyxloom-trove/carve-assets",
    "gate/python/release",
    "gate/python/fixtures",
    "tests/fixtures",
)

#: Any idiom that compares a read document/bytes against something.
COMPARISON = re.compile(
    r"(==\s*expected|!=\s*expected|expected\s*[=!]=|normalized\s*[=!]=|"
    r"assert\s+\w+\s*==|read_bytes\(\)\s*==|read_text\(\)\s*==|"
    r"json\.loads\([^)]*\)\s*[=!]=|verify_document|verify_text|"
    r"_substitute_template|_EXPECTED_ROOT|assertEqual)"
)


def _seg_variants(tree: str) -> list[str]:
    """A frozen tree spelled as a literal path, or built from ADJACENT components.

    Round 4: an earlier version also matched bare single tokens ("P25",
    "expected", "fixtures"). That produced 17 false positives -- and worse, it
    classified `tests/test_self_hosting.py` as a DIRECT consumer on a bare-token
    hit when it actually reads its artifact path from `os.environ`. Anyone doing
    the obvious cleanup of that noise would have silently dropped a real
    consumer. Requiring at least two adjacent components removes the noise
    without hiding anything: a genuine reader either spells the path or joins its
    parts in sequence.
    """
    parts = tree.split("/")
    out = [tree, tree.replace("/", '" / "'), tree.replace("/", "', '")]
    for a, b in zip(parts, parts[1:]):
        out += [
            f'"{a}" / "{b}"', f'"{a}", "{b}"', f"{a}/{b}",
            f"'{a}' / '{b}'", f"'{a}', '{b}'",
            # head-of-chain: Path("tests") / "fixtures"
            f'Path("{a}") / "{b}"', f"Path('{a}') / '{b}'",
        ]
    return out


def lives_in_frozen_tree(path: Path) -> str | None:
    """A file located INSIDE a frozen tree that resolves paths relative to itself.

    Round 5's missed consumer: `carve-assets/P20/test_acceptance.py` does
    `ASSET_ROOT = Path(__file__).resolve().parent` and reads its expectations
    from there. No string in it names a frozen tree, so NO text matcher can ever
    find it -- this is a fact about the file's LOCATION, not its contents. It
    appeared in an earlier inventory only by accident, because it mentions
    `os.environ` elsewhere for an unrelated reason.
    """
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        return None
    for tree in FROZEN_TREES:
        if rel.startswith(tree + "/"):
            return tree
    return None


def reads_frozen_tree(text: str) -> list[str]:
    """Textual detection of a frozen-tree read. **Bounded, and named as such.**

    Round 5 probed ten construction idioms and this matched three. It now covers
    more -- including the head-of-chain `Path("tests") / "fixtures"` shape the
    old docstring wrongly claimed -- but text matching cannot be complete for a
    dynamic language, and the honest statement is:

      COVERED : literal paths; adjacent `/`-joined string components; adjacent
                comma-joined components; `Path(a) / b` heads; a module constant
                whose own assignment names the tree.
      NOT COVERED : a path assembled from variables across statements, from a
                config value, or from a caller. Those are what the two indirect
                categories exist for, and why `_supplying_callers` tracks edges
                rather than trusting this function.

    This is a filter, not a proof. The decoy test and the frozen-consumer test
    are what make its output falsifiable.
    """
    hits = []
    for tree in FROZEN_TREES:
        if any(v in text for v in _seg_variants(tree)):
            hits.append(tree)
    return hits


# --- seeds --------------------------------------------------------------------

def _paths_in(text: str) -> list[Path]:
    out = []
    for m in re.finditer(r'([A-Za-z0-9_][A-Za-z0-9_./-]*\.py)\b', text):
        rel = m.group(1)
        for cand in (ROOT / rel, ROOT / rel.split("assay/", 1)[-1]):
            if cand.exists() and cand.is_file():
                out.append(cand.resolve())
    return out


def seeds() -> tuple[list[Path], list[str]]:
    notes: list[str] = []
    found: set[Path] = set()

    gate_text = GATE.read_text()
    found.update(_paths_in(gate_text))
    for m in re.finditer(r'-m pytest\s+(?:\S+\s+)?(tests)\b', gate_text):
        found.add((ROOT / "tests").resolve())
    notes.append("gate script: paths + pytest targets")

    # gap 1: the gate's inline heredocs
    for m in re.finditer(r"<<'PYEOF'(.*?)PYEOF", gate_text, re.S):
        found.update(_paths_in(m.group(1)))
    notes.append("gate script: inline python heredocs scanned")

    # gap 1: the REAL primary invocation -- `assay run tester-unified`, whose
    # argv lives in assay.toml, never in the gate script.
    if LANE_CONFIG.exists():
        cfg = LANE_CONFIG.read_text()
        found.update(_paths_in(cfg))
        for m in re.finditer(r'argv\s*=\s*\[([^\]]*)\]', cfg):
            argv = m.group(1)
            for t in re.findall(r'"([^"]+)"', argv):
                p = (ROOT / t).resolve()
                if p.exists():
                    found.add(p)
            notes.append(f"assay.toml argv: {argv.strip()}")
    return sorted(found), notes


# --- closure ------------------------------------------------------------------

def _resolve_module(name: str, here: Path) -> list[Path]:
    """Resolve a dotted module through the REAL package layout (gap 3)."""
    rel = name.replace(".", "/")
    cands = [
        SRC / f"{rel}.py",
        SRC / rel / "__init__.py",
        here.parent / f"{rel}.py",
        here.parent / rel / "__init__.py",
    ]
    tail = name.split(".")[-1]
    cands += [here.parent / f"{tail}.py", SRC / "assay" / f"{tail}.py"]
    out = []
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        # never leave this project: a leaf-name fallback can otherwise pull in a
        # sibling project's module and make the closure meaningless.
        if c.exists() and c.is_file() and r.is_relative_to(ROOT):
            out.append(r)
    return out


def deps(path: Path) -> set[Path]:
    out: set[Path] = set()
    try:
        text = path.read_text()
        tree = ast.parse(text)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.update(_resolve_module(a.name, path))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.update(_resolve_module(node.module, path))
                for a in node.names:  # `from pkg import mod`
                    out.update(_resolve_module(f"{node.module}.{a.name}", path))
            else:
                # gap 4: `from . import X` -- module is None
                for a in node.names:
                    out.update(_resolve_module(a.name, path))
    # gap 2: subprocess targets built from joined components
    for m in re.finditer(r'"([A-Za-z0-9_-]+)"\s*/\s*"([A-Za-z0-9_.-]+\.py)"', text):
        for cand in ROOT.rglob(m.group(2)):
            out.add(cand.resolve())
    for m in re.finditer(r'([A-Za-z0-9_./-]+\.py)', text):
        cand = (ROOT / m.group(1)).resolve()
        if cand.exists() and cand.is_file():
            out.add(cand)
    out.discard(path.resolve())
    return {d for d in out if d.is_relative_to(ROOT)}


def closure(seed_paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    queue = list(seed_paths)
    while queue:
        item = queue.pop()
        item = item.resolve()
        if item in seen:
            continue
        seen.add(item)
        if item.is_dir():
            queue.extend(item.rglob("*.py"))
            continue
        queue.extend(d for d in deps(item) if d not in seen)
    return sorted(p for p in seen if p.is_file() and p.suffix == ".py")


#: A file that compares a document whose PATH ARRIVES FROM ELSEWHERE cannot name
#: a frozen tree, so no name-based predicate can ever find it. Two distinct
#: sources, each detected separately because round 4 showed one heuristic cannot
#: cover both:
#:
#:   argv    -- `gate/distribution/release_wheel.py`, which the gate invokes with
#:              `gate/python/release/P25/release-manifest.json` on its command
#:              line. Round 3 found it.
#:   environ -- `tests/test_self_hosting.py`, which reads its artifact path from
#:              `os.environ`. Round 4 found it, and found that it was in the
#:              manifest only by a bare-token false positive -- so the obvious
#:              cleanup of that noise would have silently dropped a REAL consumer.
INDIRECT_ARGV = re.compile(r"(--manifest|argparse|sys\.argv)")
INDIRECT_ENV = re.compile(r"os\.environ|getenv")


def _env_var_names(text: str) -> list[str]:
    """Environment variables this file reads a path out of.

    Resolves module-level constants: `tests/test_self_hosting.py` writes
    `ENV_VAR = "ASSAY_SELF_HOSTING_VERDICT"` and then `os.environ.get(ENV_VAR)`,
    so a regex over the `environ` call alone sees a local name and nothing
    useful. Round 5's live instance turned on exactly this.
    """
    names: set[str] = set()
    consts = dict(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=\s*[\'"]([A-Z0-9_]+)[\'"]',
                             text, re.M))
    for m in re.finditer(r'environ(?:\.get)?[\(\[]\s*([A-Za-z_][A-Za-z0-9_]*|[\'"][A-Z0-9_]+[\'"])',
                         text):
        token = m.group(1).strip("'\"")
        names.add(consts.get(token, token))
    for m in re.finditer(r'getenv\(\s*([A-Za-z_][A-Za-z0-9_]*|[\'"][A-Z0-9_]+[\'"])', text):
        token = m.group(1).strip("'\"")
        names.add(consts.get(token, token))
    return sorted(n for n in names if n.isupper() and len(n) > 3)


def _env_suppliers(names: list[str], files: list[Path]) -> list[str]:
    """Anything in the gate's execution path that SETS one of those variables.

    The supplier is frequently the gate SHELL SCRIPT rather than a Python file --
    `ASSAY_SELF_HOSTING_VERDICT` is exported by `tester-unified-gate.sh`, not by
    any module. Scanning only the Python closure dropped a real consumer, so the
    gate script is searched too.
    """
    out = []
    for f in [GATE, *files]:
        try:
            text = f.read_text()
        except Exception:
            continue
        if not reads_frozen_tree(text) and "verdict" not in text:
            continue
        for n in names:
            # the setter may be shell (`NAME="$path"`), a dict entry
            # (`env["NAME"] = ...`), or a kwarg -- all three spellings count.
            if re.search(rf"(?:[\"']{n}[\"']\s*\]?\s*[:=]|(?<![A-Z_]){n}\s*=)", text):
                out.append(f"{f.relative_to(ROOT)}::{n}")
                break
    return sorted(set(out))


def _subprocess_callers(target: Path, files: list[Path]) -> list[str]:
    """Closure members that invoke `target` AND name a frozen tree.

    This is what separates a genuine indirect consumer from library noise.
    `release_wheel.py` is shelled to by `qualify_topos.py`, which holds the
    frozen path -- so the pair is a real consumer. `src/assay/verify.py` matches
    the same comparison regexes but is *imported*, never invoked with a frozen
    path, so it is noise and round 4 was right to call it out.
    """
    out = []
    for f in files:
        if f == target:
            continue
        try:
            text = f.read_text()
        except Exception:
            continue
        if target.name in text and reads_frozen_tree(text):
            out.append(str(f.relative_to(ROOT)))
    return out


def find_consumers() -> list[dict]:
    files = closure(seeds()[0])
    out = []
    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        if not COMPARISON.search(text):
            continue
        trees = reads_frozen_tree(text)
        own = lives_in_frozen_tree(f)
        if own and re.search(r"__file__|\bparent\b", text) and own not in trees:
            trees = trees + [own]
        if trees:
            kind = "direct"
        else:
            # An indirect candidate is only a consumer if some OTHER closure
            # member actually hands it a frozen path. Without that edge it is a
            # library that happens to compare documents -- noise, not a finding.
            callers = _subprocess_callers(f, files)
            env_names = _env_var_names(text)
            env_suppliers = _env_suppliers(env_names, files) if env_names else []
            if INDIRECT_ARGV.search(text) and callers:
                kind = "indirect-path-from-argv"
            elif env_names and env_suppliers:
                # Round 5: the environ branch had NO supplier guard, so any file
                # merely mentioning os.environ was reported. Two false positives,
                # and -- worse -- it masked a real consumer that appeared only
                # because it happened to mention os.environ for an unrelated
                # reason. An environ entry is a finding only when some other
                # closure member actually SETS that variable to a frozen path.
                kind = "indirect-path-from-environ"
            else:
                continue
        entry = {
            "path": str(f.relative_to(ROOT)),
            "frozen_trees": trees,
            "kind": kind,
            "v4_verdict_consumer": bool(
                re.search(r'v4-template|schema_version|verify_document', text)
            ),
        }
        if kind == "indirect-path-from-argv":
            entry["frozen_path_supplied_by"] = callers
        elif kind == "indirect-path-from-environ":
            entry["frozen_path_supplied_by"] = env_suppliers
        out.append(entry)
    return out


def main() -> int:
    as_json = "--json" in sys.argv
    seed_paths, notes = seeds()
    consumers = find_consumers()
    if as_json:
        print(json.dumps({"consumers": consumers}, indent=2))
        return 0
    print("=== seeds ===")
    for n in notes:
        print("   ", n)
    for s in seed_paths:
        print("   ", s.relative_to(ROOT))
    print(f"\n=== transitive closure: {len(closure(seed_paths))} python files ===")
    print(f"\n=== CONSUMERS OF A FROZEN EXPECTATION: {len(consumers)} ===")
    for c in consumers:
        flag = "  <-- compares a VERDICT artifact" if c["v4_verdict_consumer"] else ""
        print(f"\n  {c['path']}{flag}")
        print(f"      frozen trees: {c['frozen_trees']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
