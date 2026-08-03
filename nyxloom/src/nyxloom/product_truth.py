"""Standing product-truth fact registry (CR-01, DR-04; amendment §3.2).

The amendment's own finding: CR-01's one-time archive/cleanup pass is stale
within its own program (CR-04 deletes the file backend and re-falsifies
README's "files are the database" headline days later). The fix it mandates
is a STANDING check, not a cleanup: a small, explicit set of machine-known
facts (state backend, trove path, merge mode, daemon mode, active milestone,
declared interpreter, authoritative gate), each with exactly one place a
human declares it and exactly one place the machine proves it, compared by
an ordinary pytest test (tests/test_product_truth.py) under the existing
`tester-unified` gate -- no gate argv change.

Design: a DESCRIPTOR per fact (`ProductFact`), not a whole-document
snapshot. A doc declares its value with a small HTML-comment marker
(invisible in rendered Markdown, immune to prose edits elsewhere in the
file, trivially greppable):

    <!-- product-truth:state_backend=sqlite -->

`actual_value` computes the SAME key from a real, already-authoritative
source (nyxloom.toml, nyxloomd/docker-compose.yml, sys.version_info, a spine
doc's own frontmatter) -- never a second hardcoded copy of the doc's claim.
Every function here is a pure `(root) -> value` read (or, for `interpreter`,
a pure `() -> value` read of the running interpreter); none has side
effects, so a test can point `root` at a synthetic tmp_path tree to prove
the comparison actually discriminates (F: no two-hardcoded-lists-with-no-
oracle anti-pattern).
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import frontmatter

_MARKER_RE = r"<!--\s*product-truth:{key}=(\S+?)\s*-->"


def extract_marker(text: str, key: str) -> str | None:
    """The declared value for `key` from a `<!-- product-truth:key=value -->`
    marker in `text`, or None if the marker is absent."""
    m = re.search(_MARKER_RE.format(key=re.escape(key)), text)
    return m.group(1) if m else None


def declared_value(root: Path, doc_relpath: str, key: str) -> str | None:
    """The declared value for `key`, read from the marker in the doc at
    `root / doc_relpath`. None if the doc or the marker is missing."""
    path = root / doc_relpath
    if not path.exists():
        return None
    return extract_marker(path.read_text(encoding="utf-8"), key)


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _nyxloom_toml_path(root: Path) -> Path:
    p = root / "nyxloom-trove" / "nyxloom.toml"
    if p.exists():
        return p
    return root / ".nyxloom" / "project.toml"


def _compose_service(root: Path) -> dict:
    compose_path = root / "nyxloomd" / "docker-compose.yml"
    if not compose_path.exists():
        return {}
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    return (data.get("services") or {}).get("nyxloomd") or {}


# ---------------------------------------------------------------------------
# actual-value readers: one per registered fact key. Each takes `root` (a
# project root, real or synthetic) and returns the CURRENT machine value, or
# None when the source it needs isn't present.

def actual_state_backend(root: Path) -> str | None:
    """Which store the CODE actually implements.

    CR-04b changed this fact's source, which is exactly what CR-01's standing
    rule exists to force. It used to read `NYXLOOM_STATE_BACKEND` out of the
    compose file -- a SELECTOR, meaningful only while two backends existed.
    That variable is gone, so reading it would report "unavailable" forever
    and the check would fail on a tree that is perfectly consistent.

    The fact now is whether `storage.py` still selects at all -- the literal
    token appearing anywhere in that module, prose included, counts as still
    selecting, because a reader who finds it there has to go and check. Its
    presence is
    also precisely CR-04's acceptance ("no production reference to
    NYXLOOM_STATE_BACKEND or live file backend remains"), so the standing
    document check and that acceptance are one mechanism rather than two that
    can disagree.
    """
    store = root / "src" / "nyxloom" / "storage.py"
    if not store.exists():
        return None
    return "selectable" if "NYXLOOM_STATE_BACKEND" in store.read_text(
        encoding="utf-8") else "sqlite"


def actual_daemon_mode(root: Path) -> str | None:
    service = _compose_service(root)
    if not service:
        return None
    return "resident" if service.get("restart") == "unless-stopped" else "non-resident"


def actual_merge_mode(root: Path) -> str | None:
    data = _load_toml(_nyxloom_toml_path(root))
    value = data.get("policy", {}).get("merge_mode")
    return str(value) if value is not None else None


def actual_trove_dirname(root: Path) -> str | None:
    if (root / "nyxloom-trove" / "nyxloom.toml").exists():
        return "nyxloom-trove"
    if (root / ".nyxloom" / "project.toml").exists():
        return ".nyxloom"
    return None


def actual_interpreter(root: Path) -> str:      # noqa: ARG001 -- uniform (root) signature
    """The MAJOR.MINOR of the interpreter actually running this test. Under
    the real tester-unified gate this is the gate's own pinned interpreter
    (STANDING.md: 3.14); under a cockpit run it's whatever the cockpit uses
    -- the whole point of the fact is to catch drift wherever it runs."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def actual_gate_ids(root: Path) -> set[str]:
    data = _load_toml(_nyxloom_toml_path(root))
    return set(data.get("gates", {}).keys())


def actual_active_milestones(root: Path, roadmap_relpath: str) -> set[str] | None:
    """Milestone ids with status=='active' in the spine roadmap doc's OWN
    frontmatter -- the machine-trusted surface `nyxloom lint`'s S1/S5 rules
    validate. None if the doc is missing or unparsable (fail-closed callers
    treat None as a mismatch against any non-empty declared claim)."""
    path = root / roadmap_relpath
    if not path.exists():
        return None
    try:
        fm, _body, _line = frontmatter.split_frontmatter(path.read_text(encoding="utf-8"))
    except frontmatter.HandoffParseError:
        return None
    return {
        m["id"] for m in fm.get("milestones", [])
        if isinstance(m, dict) and m.get("status") == "active" and isinstance(m.get("id"), str)
    }


@dataclass(frozen=True)
class ProductFact:
    """One registered fact: WHERE a human declares it (a marker in a doc)
    and HOW the machine computes the real value. `actual` takes `root` and
    returns the current value (or None/empty when unavailable) -- kept as a
    plain callable so tests can substitute a synthetic root without
    monkeypatching module internals."""
    key: str
    doc_relpath: str
    description: str
    actual: Callable[[Path], str | None]


FACT_REGISTRY: tuple[ProductFact, ...] = (
    ProductFact(
        key="state_backend",
        doc_relpath="README.md",
        description="the store the code implements (src/nyxloom/storage.py: selectable vs sqlite-only)",
        actual=actual_state_backend,
    ),
    ProductFact(
        key="daemon_mode",
        doc_relpath="README.md",
        description="resident daemon vs. stateless tick (nyxloomd/docker-compose.yml restart policy)",
        actual=actual_daemon_mode,
    ),
    ProductFact(
        key="merge_mode",
        doc_relpath="README.md",
        description="nyxloom-trove/nyxloom.toml [policy].merge_mode",
        actual=actual_merge_mode,
    ),
    ProductFact(
        key="trove_path",
        doc_relpath="nyxloom-trove/README.md",
        description="which config file / folder name is actually the loaded trove",
        actual=actual_trove_dirname,
    ),
    ProductFact(
        key="interpreter",
        doc_relpath="nyxloom-trove/STANDING.md",
        description="the interpreter MAJOR.MINOR actually running",
        actual=actual_interpreter,
    ),
)


def check_fact(root: Path, fact: ProductFact) -> tuple[str | None, str | None]:
    """(declared, actual) for `fact` against `root`. Either may be None
    (doc/marker missing, or machine source unavailable) -- callers decide
    how to render that as a failure."""
    declared = declared_value(root, fact.doc_relpath, fact.key)
    actual = fact.actual(root)
    return declared, actual


def check_authoritative_gate(root: Path, doc_relpath: str = "nyxloom-trove/STANDING.md") -> tuple[str | None, bool]:
    """Special-cased (not a plain equality fact): the declared gate id must
    be a KEY actually present in [gates.*], not compared to a single
    'the' gate string. Returns (declared_gate_id, is_declared_gate_real)."""
    declared = declared_value(root, doc_relpath, "authoritative_gate")
    if declared is None:
        return None, False
    return declared, declared in actual_gate_ids(root)


def check_active_milestone(
    root: Path,
    doc_relpath: str = "nyxloom-trove/3-roadmap.md",
) -> tuple[str | None, set[str] | None]:
    """(declared_id, actual_active_ids) for the roadmap spine doc's own
    self-consistency: a `<!-- product-truth:active_milestone=Mn -->` marker
    in the doc's PROSE must name exactly the milestone(s) its OWN
    frontmatter marks status=='active'."""
    declared = declared_value(root, doc_relpath, "active_milestone")
    actual = actual_active_milestones(root, doc_relpath)
    return declared, actual
