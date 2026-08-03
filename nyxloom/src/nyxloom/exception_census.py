"""Broad-exception census and the allow-list oracle over it (CR-02b, DR-03).

WHY THIS MODULE EXISTS
----------------------
CR-02a removed the fail-open handlers from the snapshot fan-in and replaced
them with typed descriptors. That fixed the instances someone found. It did
not stop the next one from being written, and it left no way to answer "is
the fan-in clean?" other than reading 15 modules by hand.

A broad ``except Exception`` is not itself a defect. It is a defect when the
value it substitutes is one the caller cannot tell from a real reading -- the
``lint_clean=True`` case CR-02a's docstring opens with. So the rule this
module enforces is not "no broad handlers"; it is:

    Every broad handler is CLASSIFIED, and no handler in an authority-bearing
    path is unclassified.

CLASSIFICATION
--------------
A handler declares its class in a trailing comment on its own ``except``
line, which is where a reader and a reviewer both already look::

    except Exception as exc:  # census: advisory-degradation (CR-02b)

The vocabulary is CLOSED (:data:`CENSUS_CLASSES`). Four classes, separated by
what each one is allowed to do with authority:

``authority-bearing, fail-closed``
    The handler catches on a path that authorizes an effect, and its
    substituted value REFUSES. Legal, and the only legal class on such a path.

``process-boundary translation``
    The handler converts a foreign failure (subprocess, filesystem, parser)
    into a typed value the caller must interpret -- ``snapshot.acquire`` is
    the canonical one. It does not decide anything itself.

``advisory-degradation``
    The handler's domain can only ever REDUCE what the system will do. A
    probe that cannot run marks a route unusable; it can never mark one
    usable. Degradation must still be recorded, but it does not stop a pass.

``cleanup/containment``
    The handler runs where the effect it guards has already failed or already
    committed: reporting an error, closing a resource, notifying about a
    refusal. It cannot turn a refusal into a permission because there is no
    permission left to grant.

THE TWO RULES
-------------
1. **Fan-in handlers are fully classified.** :func:`is_fanin_function` derives
   the fan-in STRUCTURALLY -- a function that takes a ``builder`` parameter or
   names ``SnapshotBuilder``/``snapshot.acquire``/``SnapshotInput`` is by
   construction part of the snapshot boundary. Derived, not listed, so it
   cannot drift from the code: a new acquisition site joins the rule the
   moment it is written, without anyone remembering to add it here.

2. **Everything else is budgeted, owned, and monotonically shrinking.**
   :data:`LEGACY_BUDGET` records, per module, how many unclassified broad
   handlers remain and which package owns retiring them. The oracle fails BOTH
   directions: over budget means new unclassified debt, and UNDER budget means
   debt was repaid without recording it. The second half is what keeps the
   census a census rather than a ceiling nobody ever lowers.

The budget is deliberately per MODULE rather than per handler. A per-handler
registry keyed by line or function would have to be rewritten wholesale by
CR-05/CR-06/CR-07, which move most of ``daemon.py`` -- turning a safety
mechanism into merge friction, which is how safety mechanisms get deleted.
A count per module survives the move and still refuses new debt.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: The closed classification vocabulary. See the module docstring for what
#: each class is permitted to do with authority.
CENSUS_CLASSES: frozenset[str] = frozenset({
    "authority-bearing, fail-closed",
    "process-boundary translation",
    "advisory-degradation",
    "cleanup/containment",
})

#: Names whose presence in a function body makes it part of the snapshot
#: fan-in. Kept as a marker list rather than an import graph because the
#: property that matters is "this function participates in building or
#: classifying a snapshot input", which is exactly what naming these does.
_FANIN_MARKERS = ("SnapshotBuilder", "snapshot.acquire", "SnapshotInput")

#: Unclassified broad handlers remaining per module, with the package that
#: owns retiring them. These predate CR-02 and are NOT known to be safe --
#: they are known to be UNREVIEWED, which is a different and more honest
#: claim. The owning package classifies them when it rewrites the module.
#:
#: A number here may only go DOWN, and it must go down in the same commit
#: that removes the handler.
LEGACY_BUDGET: dict[str, int] = {
    # Control plane -- the largest pool, retired as CR-05 moves effect code
    # out of the god object and CR-06 decomposes the planner.
    # CR-05a lowered this from 35: two handlers left with the code they
    # guarded (the journal's notify containment and the gate-verify probe's
    # degradation), and both are CLASSIFIED in their new module rather than
    # inherited as debt. This is the per-module budget doing what it was
    # designed for -- a move lowers a number instead of rewriting a registry.
    "daemon.py": 33,
    # Diagnostics: doctor and render both read the world defensively so a
    # broken project still produces a readable report. CR-16 owns doctor's
    # (liveness reporting must not itself go dark); CR-11 owns render's.
    "doctor.py": 15,
    "render.py": 13,
    # Human-facing chat/CLI surfaces. CR-14 owns the interruption/answer
    # path; CR-01 already owns the CLI's document-truth reporting.
    "decision_chat.py": 7,
    "cli.py": 6,
    "commands.py": 5,
    # Store and process boundaries. CR-04 owns the store's; CR-13a owns the
    # wrapper's, since containment changes what a launch failure even means.
    "storage_sqlite.py": 3,
    "wrapper.py": 3,
    "migrate_store.py": 1,
    # Route/provider surfaces, retired by the routing packages.
    "benchmark_sources.py": 3,
    "adapters.py": 2,
    "resync.py": 2,
    "notify.py": 2,
    "free_models.py": 1,
    "route_doctor.py": 1,
    # CR-15's credential store. One handler, retired with CR-13a's per-route
    # secret injection.
    "control_auth.py": 1,
}


@dataclass(frozen=True)
class Handler:
    """One broad exception handler found in the source tree."""

    module: str
    lineno: int
    function: str
    census_class: str | None
    in_fanin: bool

    @property
    def classified(self) -> bool:
        return self.census_class is not None

    def __str__(self) -> str:
        where = f"{self.module}:{self.lineno} in {self.function}()"
        return f"{where} [{self.census_class or 'UNCLASSIFIED'}]"


def is_broad(handler: ast.ExceptHandler) -> bool:
    """True for a bare ``except:``, ``except Exception``/``BaseException``, or
    a tuple containing either.

    A tuple counts: ``except (ValueError, Exception)`` catches everything, and
    listing a narrow type beside a broad one reads as narrow at a glance --
    which is precisely why it has to be caught mechanically.
    """
    node = handler.type
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in ("Exception", "BaseException")
    if isinstance(node, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in ("Exception", "BaseException")
                   for e in node.elts)
    return False


def parse_census_class(line: str) -> str | None:
    """The declared class from an ``except`` line's trailing comment, or None.

    Only a class from :data:`CENSUS_CLASSES` is recognised: a tag naming
    something else is treated as ABSENT rather than accepted, so inventing a
    fifth class quietly is not a way past the oracle.
    """
    marker = "census:"
    if marker not in line:
        return None
    tail = line.split(marker, 1)[1].strip()
    for known in CENSUS_CLASSES:
        if tail.startswith(known):
            return known
    return None


def is_fanin_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when `fn` participates in the snapshot fan-in.

    Structural, not a list: a function either takes a ``builder`` or names one
    of the snapshot types. Both are things the author had to write to be part
    of the fan-in at all, so a new acquisition site cannot join the code
    without joining this rule.
    """
    args = [a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs]
    if "builder" in args:
        return True
    body = ast.unparse(fn)
    return any(marker in body for marker in _FANIN_MARKERS)


def scan_module(path: Path) -> list[Handler]:
    """Every broad handler in one module, with its class and fan-in status."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))

    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    fanin: dict[ast.AST, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fanin[node] = is_fanin_function(node)

    def enclosing(node: ast.AST) -> ast.AST | None:
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur
            cur = parents.get(cur)
        return None

    found: list[Handler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not is_broad(node):
            continue
        fn = enclosing(node)
        found.append(Handler(
            module=path.name,
            lineno=node.lineno,
            function=fn.name if fn is not None else "<module>",
            census_class=parse_census_class(lines[node.lineno - 1]),
            # A handler at module scope is never in the fan-in: the fan-in is
            # built inside a pass, and import-time code cannot be part of one.
            in_fanin=bool(fn is not None and fanin.get(fn, False)),
        ))
    return found


def scan_tree(source_root: Path) -> list[Handler]:
    """Every broad handler under `source_root`, deterministically ordered."""
    found: list[Handler] = []
    for path in sorted(source_root.rglob("*.py")):
        found.extend(scan_module(path))
    return sorted(found, key=lambda h: (h.module, h.lineno))


def unclassified_by_module(handlers: list[Handler]) -> dict[str, int]:
    """Module -> count of handlers carrying no recognised class."""
    counts: dict[str, int] = {}
    for h in handlers:
        if not h.classified:
            counts[h.module] = counts.get(h.module, 0) + 1
    return counts
