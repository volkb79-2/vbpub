"""``ciu migration-check`` — stale-artifact detection for a consumer checkout.

Normative contract: docs/SPEC.md §S13.7.

Why this module exists
----------------------
CIU has a standing rule, restated by ciu-P46 and binding on every rename or
removal from here on: **no fallback-reads and no legacy-compat shims anywhere
in the normal code paths.** Every cutover is hard — the old path is gone, not
dual-read. That keeps ``render_global_chain``, S4.16's token resolution, the
hook runner and every other hot path exactly as clean as if the legacy
behaviour had never existed.

The price of that rule, paid honestly, is that an operator whose checkout still
carries a pre-cutover artifact would otherwise get a SILENT break. This module
is where that price is paid: **one** place that knows CIU's own version
history, invoked deliberately (``ciu migration-check``) and automatically (a
``ciu check`` stage, hence every ``ciu up`` via S13.4c), which turns a silent
break into a named finding with a remediation.

Design constraints (all deliberate)
-----------------------------------
* **No detector compares an installed ciu version against anything.** Every
  rule is purely pattern-based — does this file exist, does this table exist,
  is this key shaped a certain way — so it answers correctly regardless of
  which historical CIU last touched the checkout, and stays trivially testable
  in isolation. Version comparison would add an entire class of bugs
  (unreleased dev versions, a checkout touched by two versions, a wheel newer
  than the artifacts it is reading) for no gain.
* **One registry, two entry points.** :data:`RULES` is the single source of
  detector logic; the standalone verb and the ``ciu check`` stage both walk it.
  Duplicating a detector into the stage form is how the two would drift.
* **Findings carry a remediation, not just a complaint.** A migration finding
  whose reader still has to work out what to do is barely better than the
  silent break it replaced.

Extending it
------------
Add a detector function taking ``(repo_root)`` and returning
``list[Finding]``, then add one :class:`Rule` to :data:`RULES`. Nothing else in
this module — or anywhere else in CIU — needs to change: both entry points,
the JSON envelope, the exit-code contract and the ``ciu check`` stage are
already generic over the registry.

Public API
----------
Finding                    : one detection (rule, severity, message, remediation)
Rule                       : one registry entry (name, description, detector)
RULES                      : the ordered registry
run_migration_check(root)  : walk every rule, return findings in registry order
document(root, findings)   : the versioned ``--json`` envelope
main(argv)                 : the ``ciu migration-check`` verb
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ciu.config_constants import (
    GLOBAL_CONFIG_WORKTREE_OVERRIDES,
    WORKSPACE_ENV,
)

#: Versioned JSON envelope (same convention as ``ciu check``/``capabilities``).
MIGRATION_CHECK_SCHEMA_VERSION = 1

#: The severity vocabulary, IDENTICAL to S9.5's hook-preflight findings and to
#: ``deploy.HOOK_FINDING_SEVERITIES``. Reused rather than reinvented so a
#: reader who already knows what a CIU WARN/ERROR means needs to learn nothing
#: new here, and so the ``ciu check`` stage form can route them with the same
#: note/fail split every other stage uses.
SEVERITIES: tuple[str, ...] = ("WARN", "ERROR")

#: Overlay filenames CIU has USED and later retired, oldest first.
#:
#: Deliberately a literal history list, NOT derived from
#: :data:`GLOBAL_CONFIG_WORKTREE_OVERRIDES` — the point of a history is that it
#: keeps naming a file the current code no longer knows about. Equally
#: deliberately, :func:`detect_retired_overlay` filters this list against the
#: CURRENT constant, so while a name is still the live one it produces no
#: finding at all: as of ciu-P46 the rename has not happened, the live overlay
#: is exactly this name, and this rule is correctly silent on every checkout.
#: ciu-P47, which performs the rename, only has to change the constant — this
#: rule goes live on its own, with no edit here and none to the registry.
RETIRED_OVERLAY_NAMES: tuple[str, ...] = ("ciu.global.worktree.toml.j2",)


@dataclass(frozen=True)
class Finding:
    """One detected stale artifact.

    ``rule`` names the detector so a JSON consumer can suppress or route a
    single class of finding without string-matching prose. ``message`` says
    what is true of the checkout; ``remediation`` says what to do about it —
    both are required, because a migration diagnostic that only complains
    leaves its reader exactly where the silent break did.
    """

    rule: str
    severity: str
    message: str
    remediation: str

    def as_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "remediation": self.remediation,
        }


#: A detector: ``repo_root -> findings``. Detectors take the ciu-root and read
#: only from it; none may mutate anything, prompt, or touch the network — the
#: same side-effect-free contract every ``ciu check`` stage carries, since one
#: of this registry's two entry points IS a ``ciu check`` stage.
Detector = Callable[[Path], "list[Finding]"]


@dataclass(frozen=True)
class Rule:
    """One registry entry: a stable ``name``, prose, and its detector."""

    name: str
    description: str
    detector: Detector


# ---------------------------------------------------------------------------
# Rule 1 — a retired overlay filename is still present
# ---------------------------------------------------------------------------

def detect_retired_overlay(repo_root: Path) -> list[Finding]:
    """A previously-used, now-retired global overlay filename still on disk.

    WARN, never ERROR: the file's mere existence does not prove any real
    content was lost — a leftover may be empty, or carry only the CIU-owned
    generated table that has already been rewritten elsewhere. Hard-blocking
    ``ciu up`` over a possibly-empty leftover would be a refusal the operator
    cannot act on without first reading the file anyway.
    """
    findings: list[Finding] = []
    for name in RETIRED_OVERLAY_NAMES:
        if name == GLOBAL_CONFIG_WORKTREE_OVERRIDES:
            # Still the live overlay in this CIU — nothing retired about it.
            continue
        if not (Path(repo_root) / name).exists():
            continue
        findings.append(
            Finding(
                rule="retired-overlay-file",
                severity="WARN",
                message=(
                    f"found '{name}' at the ciu root — this filename is "
                    "retired and its content is NO LONGER merged into any "
                    "render"
                ),
                remediation=(
                    f"move any hand-authored overrides from '{name}' into "
                    f"'{GLOBAL_CONFIG_WORKTREE_OVERRIDES}' by hand, then "
                    f"delete '{name}'. CIU-owned generated facts need no "
                    "hand-copying: re-run `ciu env generate`."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Rule 2 — pre-CIU-75 identity state (ciu.env without the generated facts)
# ---------------------------------------------------------------------------

def detect_stale_identity_facts(repo_root: Path) -> list[Finding]:
    """A checkout still relying on ``ciu.env`` alone for instance identity.

    Since CIU-60/CIU-75 (S3.1b/S3.1c) the ``[ciu.instance.generated]`` table in
    the global overlay is the SOLE identity source CIU itself reads; ``ciu.env``
    is a write-only legacy export. A checkout carrying ``ciu.env`` but no (or
    an incomplete) generated table was last written by a pre-CIU-75 CIU, and
    every identity read there now degrades to "unmanaged" — quietly, because an
    absent record is a legitimate state.

    Gated on ``ciu.env`` existing on purpose: a checkout with NEITHER artifact
    has simply never run ``ciu env generate``, which is a first-run state, not
    a migration.
    """
    repo_root = Path(repo_root)
    if not (repo_root / WORKSPACE_ENV).exists():
        return []

    from ciu import workspace_env

    try:
        facts = workspace_env.read_generated_facts(repo_root)
    except workspace_env.WorkspaceEnvError as exc:
        return [
            Finding(
                rule="stale-identity-facts",
                severity="WARN",
                message=(
                    f"'{WORKSPACE_ENV}' is present but this checkout's "
                    f"[{workspace_env.GENERATED_FACTS_TABLE}] table could not "
                    f"be read: {exc}"
                ),
                remediation=(
                    "fix or remove the malformed table, then run "
                    "`ciu env generate` from this checkout"
                ),
            )
        ]

    missing = [key for key in workspace_env.GENERATED_FACTS_KEYS if key not in facts]
    if not missing:
        return []
    where = (
        "no such table at all"
        if not facts
        else "missing " + ", ".join(missing)
    )
    return [
        Finding(
            rule="stale-identity-facts",
            severity="WARN",
            message=(
                f"'{WORKSPACE_ENV}' is present but "
                f"[{workspace_env.GENERATED_FACTS_TABLE}] in "
                f"'{GLOBAL_CONFIG_WORKTREE_OVERRIDES}' is incomplete "
                f"({where}); this checkout carries pre-CIU-75 identity state "
                "and CIU now reads identity ONLY from that table (S3.1c)"
            ),
            remediation="run `ciu env generate` from this checkout",
        )
    ]


# ---------------------------------------------------------------------------
# Rule 3 — pre-CIU-61 gitignore gaps
# ---------------------------------------------------------------------------

def detect_gitignore_gaps(repo_root: Path) -> list[Finding]:
    """A ``.gitignore`` missing entries CIU-61 added to ``ciu init``'s set.

    The SAME comparison CIU-61 shipped and pinned
    (``scaffold._GITIGNORE_ENTRIES`` against the checkout's real ``.gitignore``,
    normalized the same way ``init_main`` normalizes it so a prior init's
    trailing ``  # why`` comment is not read as part of a pattern) — pointed at
    the CURRENT checkout instead of at a freshly scaffolded one. A repo
    scaffolded before CIU-61 never received the three entries it added, and the
    consequence is silent: a machine-specific, host-path-carrying overlay that
    a developer can commit by accident.

    An ABSENT ``.gitignore`` is deliberately not a finding: that is a checkout
    which never ran ``ciu init`` at all, which is a first-run state rather than
    a stale artifact, and this verb's whole subject is stale artifacts.
    """
    from ciu.scaffold import _GITIGNORE_ENTRIES

    gitignore = Path(repo_root) / ".gitignore"
    try:
        raw = gitignore.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return []
    except (OSError, UnicodeDecodeError) as exc:
        return [
            Finding(
                rule="gitignore-gaps",
                severity="WARN",
                message=f"'{gitignore}' could not be read: {exc}",
                remediation=(
                    "make .gitignore readable, then re-run "
                    "`ciu migration-check`"
                ),
            )
        ]

    present = {
        line.split("  # ")[0].strip()
        for line in raw.splitlines()
        if line.strip()
    }
    missing = [entry for entry, _why in _GITIGNORE_ENTRIES if entry not in present]
    if not missing:
        return []
    return [
        Finding(
            rule="gitignore-gaps",
            severity="WARN",
            message=(
                ".gitignore is missing CIU-generated-artifact entries: "
                + ", ".join(missing)
            ),
            remediation=(
                "add the listed patterns to .gitignore (`ciu init` writes them "
                "for a fresh scaffold; an existing repo adds them by hand) — "
                "each one names a machine-owned artifact that must never be "
                "committed"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule(
        name="retired-overlay-file",
        description="a retired global-overlay filename is still on disk",
        detector=detect_retired_overlay,
    ),
    Rule(
        name="stale-identity-facts",
        description="ciu.env present without complete [ciu.instance.generated] facts",
        detector=detect_stale_identity_facts,
    ),
    Rule(
        name="gitignore-gaps",
        description=".gitignore is missing CIU-61's generated-artifact entries",
        detector=detect_gitignore_gaps,
    ),
)


def run_migration_check(repo_root: Path) -> list[Finding]:
    """Walk every registered rule against *repo_root*, in registry order."""
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule.detector(Path(repo_root)))
    return findings


def document(repo_root: Path, findings: list[Finding]) -> dict:
    """The versioned ``--json`` envelope for one run."""
    return {
        "schema_version": MIGRATION_CHECK_SCHEMA_VERSION,
        "operation": "migration-check",
        "status": "fail" if findings else "pass",
        "repo_root": str(repo_root),
        "rules": [rule.name for rule in RULES],
        "findings": [finding.as_dict() for finding in findings],
    }


def main(argv: list[str]) -> int:
    """``ciu migration-check [--define-root PATH] [--json]`` (S13.7).

    Exit code is **0 when there are no findings and non-zero when there are
    any**, regardless of severity. That is deliberately NOT ``ciu check``'s
    severity-gated contract: this verb exists to be run and acted on, and a
    WARN-only run that exited 0 would be invisible to every script that calls
    it. The ``ciu check`` STAGE form keeps ``ciu check``'s own aggregation
    instead — see ``deploy._check_migration``.
    """
    import argparse

    from .cli import _resolve_repo_root_deploy

    parser = argparse.ArgumentParser(
        prog="ciu migration-check", add_help=False, allow_abbrev=False
    )
    parser.add_argument("--define-root", "--root-folder", dest="define_root",
                        default=None)
    parser.add_argument("--json", dest="json_output", action="store_true",
                        default=False)
    opts = parser.parse_args(argv)

    repo_root = _resolve_repo_root_deploy(opts.define_root)
    findings = run_migration_check(repo_root)

    if opts.json_output:
        print(json.dumps(document(repo_root, findings), indent=2))
        return 0 if not findings else 2

    if not findings:
        print(f"[SUCCESS] migration-check: no stale artifacts found in {repo_root}")
        return 0
    for finding in findings:
        print(
            f"[{finding.severity}] {finding.rule}: {finding.message}",
            file=sys.stderr,
        )
        print(f"          fix: {finding.remediation}", file=sys.stderr)
    print(
        f"[ERROR] migration-check: {len(findings)} finding(s)",
        file=sys.stderr,
    )
    return 2
