"""GA3 v1: onboarding offers to build a missing verification gate
(docs/plan-gate-adoption.md §GA3).

**v1 scope is deliberately narrow: DETECT + OFFER, not scaffold.** A project
with NO declared `[gates.*]` can be registered and onboarded today with no
signal that its merges cannot be verified (`reference/STANDARD.md`'s "gate
contract": nyxloom trusts the declared gate blindly). This module surfaces
that gap at onboarding time instead of staying silent about it -- it prints
guidance naming the concrete next step (declare a gate, then run
`nyxloom gate verify`), but it does **not** author a Dockerfile, write a
`[gates.*]` TOML section, or build a container. That scaffolding is v2
(docs/plan-gate-adoption.md §GA3 "v2 scaffolding deferred").

Deterministic and AI-free, mirroring F2's onboarding.py convention (module
docstring there: "run_wizard ... No AI/LLM is consulted"). `assess_gate`
does exactly one cheap, no-subprocess check
(`gate_runner.select_verification_gate`) and returns a `GateOffer` describing
what it found -- never runs the gate itself, never dispatches `nyxloom gate
verify` inline (that's a minutes-long real gate invocation; GA4's periodic
carver re-check already covers the "does it still work" cadence once a gate
exists).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ProjectConfig
from .gate_runner import select_verification_gate


@dataclass(frozen=True)
class GateOffer:
    """The result of assessing whether a project has a usable verification
    gate. `has_gate=False` means `ProjectConfig.gates` (via
    `gate_runner.select_verification_gate`) yielded nothing at all -- no
    `phase="implementation"` or `phase="post-merge"` gate declared."""

    has_gate: bool
    gate_id: str | None
    recommendation: str


_NO_GATE_RECOMMENDATION = (
    "no verification gate declared for this project -- nyxloom cannot "
    "verify its merges (reference/STANDARD.md \"What nyxloom requires of a "
    "project (the gate contract)\"). Next step: declare a `[gates.<name>]` "
    "in nyxloom.toml running this project's test suite in a runtime-"
    "faithful, isolated environment (never the interactive cockpit), with "
    "`argv` (the `{worktree}` placeholder), `phase = \"implementation\"`, "
    "and a real `timeout_seconds` -- ideally with a changed-line coverage "
    "floor (ecosystem `coverage_gate.py`/`cargo llvm-cov`/`nyc`) so it "
    "catches untested new branches, not just import errors. Once declared, "
    "run `nyxloom gate verify <project>` to prove it actually rejects "
    "broken code (not just `argv=[\"true\"]` laundering every merge) before "
    "trusting it."
)


def _has_gate_recommendation(gate_id: str) -> str:
    return (
        f"gate '{gate_id}' declared -- this onboarding pass does not run it "
        "(that's a real, possibly minutes-long invocation); run "
        "`nyxloom gate verify <project>` yourself to confirm it rejects "
        "broken code, or rely on the carver's periodic re-check "
        "(`gate_verify_interval_days`, docs/plan-gate-adoption.md §GA4) "
        "once the project is dispatching."
    )


def assess_gate(project_root: Path) -> GateOffer:
    """Load `project_root`'s `ProjectConfig` and check whether it declares a
    usable verification gate (`gate_runner.select_verification_gate`).

    Deterministic, no subprocess, no AI/LLM -- a plain config read. Callers
    must ensure `project_root` already has a trove (or legacy
    `.nyxloom/project.toml`) before calling this; `cmd_onboard`'s `--check-
    gate` step runs strictly after `run_wizard` for exactly that reason."""
    cfg = ProjectConfig.load(project_root)
    gate = select_verification_gate(cfg)
    if gate is None:
        return GateOffer(has_gate=False, gate_id=None,
                          recommendation=_NO_GATE_RECOMMENDATION)
    return GateOffer(has_gate=True, gate_id=gate.gate_id,
                      recommendation=_has_gate_recommendation(gate.gate_id))
