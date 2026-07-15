"""Drift audit + projection rebuild (SPEC §14, draft-1 importer reframed).
PACKAGE P08.

Read-only over consumer repos; the only thing it may WRITE is statefiles,
and only in rebuild(write=True) mode. Doctor is the always-available lint
of runtime-vs-repo coherence — the tool draft 1 called the importer.

INTERFACE CONTRACT (frozen):

- doctor_project(cfg) -> list[DoctorFinding]. CHECKS (kind, severity):
    replay-divergence critical  storage.replay() vs storage.list_states():
                                any task whose to_dict() differs; refs name
                                the task ids (cap 20).
    handoff-lint       error    lint.has_blocking on any discovered handoff
                                (one finding per file, refs=[relpath]).
    dangling-dep       error    frontmatter depends_on task ref with no
                                handoff file and no statefile.
    orphan-worktree    warning  `git -C root worktree list --porcelain`
                                entries under cfg.worktree_root with no
                                non-terminal task whose branch matches.
    missing-worktree   warning  ACTIVE task whose attempt.worktree does not
                                exist on disk.
    stale-receipt      warning  receipt.json present but its attempt state
                                is still RUNNING/PREFLIGHTING (wrapper died
                                between receipt and event; daemon will heal
                                — surfaced so the operator knows).
    unbound-evidence   warning  statefile MERGED/VALIDATING/COMPLETED with
                                merge_commit None.
    legacy-lock        warning  files named .STACK_LOCK/.CARVE_LOCK under
                                the repo (evolution: should become leases).
    stale-pause        info     pause flag older than 7 days.
    orphan-statefile   warning  statefile whose handoff_path no longer
                                exists (unless task terminal).
    decision-hold      info     QUEUED/NEEDS_DECISION task whose D-dep is
                                OPEN (refs the D-id) — visibility, not error.
- rebuild(project, write=False) -> tuple[dict replayed, list[str] diffs]:
  replay events; diff against on-disk statefiles (json-dict equality; diffs
  are 'task_id: <field-path>' strings, cap 50); write=True saves replayed
  over on-disk (the recovery path) AFTER creating .bak copies alongside.
- doctor_all() -> dict[project_id, list[DoctorFinding]] over the registry.
"""

from __future__ import annotations

from .config import ProjectConfig
from .types import DoctorFinding, TaskStateFile


def doctor_project(cfg: ProjectConfig) -> list[DoctorFinding]:
    raise NotImplementedError


def rebuild(project: str, write: bool = False) -> tuple[dict[str, TaskStateFile], list[str]]:
    raise NotImplementedError


def doctor_all() -> dict[str, list[DoctorFinding]]:
    raise NotImplementedError
