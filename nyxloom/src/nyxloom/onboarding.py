"""F2: the onboarding engine + non-AI wizard. PACKAGE F2.

docs/nyxloom-operating-model.md §2 describes the full onboarding FLOW (one
engine, three surfaces -- CLI/UI/ntfy). This module builds ONLY step 1 (the
non-AI, deterministic, menu-free wizard) + the spine instantiation that
follows it -- never the AI scan (F3) or the guided questionnaire (F4).

INTERFACE CONTRACT:

- `WizardAnswers` -- the recorded wizard selections (maturity, docs_present,
  mode, scan_paths). No AI/LLM is consulted to produce OR interpret these;
  they are inert data a surface (CLI flags today; a UI menu or an ntfy/
  intake exchange later) collects and hands in. Constructing one validates
  `maturity`/`mode` against the closed choice sets (fail fast, never a
  silent typo).
- `run_wizard(project_root, answers)` -- the engine entry point. Given a
  project root and already-collected answers:
    1. Ensures the project has a trove, scaffolding one via `scaffold_trove`
       if none exists yet (never duplicates `cli.cmd_init`'s own scaffold --
       `cli.cmd_init` is now a thin wrapper AROUND `scaffold_trove`, so the
       trove layout has exactly one source of truth).
    2. Instantiates any MISSING spine doc (1-north-star.md .. 4-backlog.md,
       docs/spine-documents-spec.md) with minimal-valid frontmatter (schema-
       checked by `lint.lint_spine` -- see tests/test_onboarding.py). An
       ALREADY-PRESENT spine doc is left completely untouched, never
       overwritten -- a human or a later F3/F4 pass may already have started
       filling it in.
    3. Wires any MISSING `nyxloom.toml` [project] spine key (north_star/
       product_definition/roadmap/backlog) at the numeric-prefixed path. An
       already-set key (any value) is left untouched -- an explicit repoint
       is authoritative.
    4. Records `answers` to `<trove>/onboarding-answers.json` -- this ALWAYS
       reflects the latest call's answers (overwritten on re-run with new
       answers), since it is wizard STATE, not user content. `load_answers`
       reloads it.
  Idempotent + safe to re-run: steps 2/3 never touch already-there content;
  only the answers file (step 4) is meant to move.
- `scaffold_trove(project_folder)` -- PACKAGE P23's trove scaffold, MOVED
  here from cli.py so `run_wizard` (and `cli.cmd_init`) share one
  implementation. Raises `TroveAlreadyExists` / `TemplatesMissing`; never
  overwrites.

Deliberately NOT built here:
- Any read of the project's actual code/docs content (the `/review`-style
  assessment scan) -- `scan_paths` is recorded, never walked, by THIS
  module; see onboarding_scan.py (PACKAGE F3, built separately so this
  wizard core stays non-AI) for the read-only agent pass that consumes it.
- Any interactive/guided authoring of the spine docs' real content (F4,
  not yet built) -- the spine docs this engine creates are intentionally
  thin placeholders.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# errors

class OnboardingError(Exception):
    """Base class for onboarding-engine errors (F2)."""


class TroveAlreadyExists(OnboardingError):
    def __init__(self, trove_dir: Path):
        self.trove_dir = trove_dir
        super().__init__(f"{trove_dir} already exists")


class TemplatesMissing(OnboardingError):
    def __init__(self, template_dir: Path):
        self.template_dir = template_dir
        super().__init__(f"bundled trove templates not found under {template_dir}")


# ---------------------------------------------------------------------------
# the non-AI wizard's answers

MATURITY_CHOICES = ("empty", "partial", "mature")
MODE_CHOICES = ("derive-from-code", "code-good-docs-absent", "greenfield-define-it")


@dataclass
class WizardAnswers:
    """Recorded answers from the non-AI onboarding wizard (step 1,
    docs/nyxloom-operating-model.md §2). No AI/LLM is consulted to produce
    or interpret these -- a surface (CLI flags, a UI menu, or an ntfy/intake
    exchange) collects them and hands them in; F3 (scan) / F4 (questionnaire)
    later read the recorded copy (`load_answers`) to enrich their prompts.

    maturity:     empty | partial | mature
    docs_present: whether the project already has real docs
    mode:         derive-from-code | code-good-docs-absent | greenfield-define-it
    scan_paths:   which paths a later scan (F3) should read; recorded, not
                  walked by this package
    """

    maturity: str
    docs_present: bool
    mode: str
    scan_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.maturity not in MATURITY_CHOICES:
            raise ValueError(
                f"invalid maturity {self.maturity!r}; must be one of {MATURITY_CHOICES}"
            )
        if self.mode not in MODE_CHOICES:
            raise ValueError(
                f"invalid mode {self.mode!r}; must be one of {MODE_CHOICES}"
            )

    def to_dict(self) -> dict:
        return {
            "maturity": self.maturity,
            "docs_present": self.docs_present,
            "mode": self.mode,
            "scan_paths": list(self.scan_paths),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WizardAnswers":
        return cls(
            maturity=data["maturity"],
            docs_present=bool(data["docs_present"]),
            mode=data["mode"],
            scan_paths=list(data.get("scan_paths", [])),
        )


_ANSWERS_FILENAME = "onboarding-answers.json"


def answers_path(trove_dir: Path) -> Path:
    """Where a project's wizard answers are recorded -- inside the trove
    (travels with the project, readable regardless of whether the project
    is registered with a daemon) rather than under paths.py's daemon-runtime
    XDG state (that state root is never committed to a consumer repo;
    onboarding answers are project content F3/F4 later read)."""
    return trove_dir / _ANSWERS_FILENAME


def _record_answers(trove_dir: Path, answers: WizardAnswers) -> Path:
    path = answers_path(trove_dir)
    path.write_text(
        json.dumps(answers.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_answers(trove_dir: Path) -> WizardAnswers:
    """Reload the most recently recorded wizard answers. Raises
    FileNotFoundError if `run_wizard` has not run for this trove yet."""
    path = answers_path(trove_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    return WizardAnswers.from_dict(data)


# ---------------------------------------------------------------------------
# trove scaffold (PACKAGE P23, moved here from cli.py so onboarding can
# reuse it without duplicating -- cli.cmd_init is now a thin wrapper)

# PACKAGE F2 note: `roadmap`/`backlog` [project] keys are deliberately ABSENT
# here (unlike the pre-F2 template, which pointed them at the legacy
# unprefixed `roadmap.md`/`backlog.md` -- config fields lint_spine's S3 rule
# is the ONLY reader of, so that default silently violated the numeric-
# prefix naming convention the moment F1 shipped). `run_wizard`'s
# `_wire_spine_keys` step is what sets north_star/product_definition/
# roadmap/backlog, at the correct `<N>-<kind>.md` paths, once a trove
# exists -- `scaffold_trove` alone (a bare `init`, no `onboard`) leaves a
# project with no adopted spine yet, which is the documented, valid
# "hasn't onboarded" state (config.py: "adopting the spine is optional").
# The plain `roadmap.md`/`backlog.md` FILES below are unrelated: they are
# the pre-F1 backlog_items.py (PACKAGE P28) convention, whose
# `resolve_path` hardcodes that filename regardless of this config key.
_INIT_NYXLOOM_TOML = '''\
[project]
id = "{project_id}"
default_branch = "main"

trove = "nyxloom-trove"

handoff_globs   = ["nyxloom-trove/handoffs/*.md"]
reports_dir     = "nyxloom-trove/reports"
decisions_inbox = "nyxloom-trove/decisions.md"
archive_dir     = "nyxloom-trove/archive"
archive_keep_visible = 10
agent_logs      = "nyxloom-trove/agent-logs"

worktree_root = "../.worktrees"

# Docs nyxloom READS but does not manage (live in this project's own tree).
# Fill in as needed -- see nyxloom-trove/STANDARD.md "declaration model".
[refs]

# Declare the project's real gate(s) here -- NEVER the devcontainer (cockpit
# doctrine). See nyxloom-trove/STANDARD.md.
# [gates.<name>]
# argv = ["bash", "-c", "..."]
# phase = "implementation"
# timeout_seconds = 1800
# environment = "..."

[policy]
max_active_tasks = 3
ready_queue_target = 5
max_attempts_per_task = 3
merge_mode = "manual"
retention_days = 60
reconcile_interval_seconds = 30
http_port = 8942

[notify]
'''


def scaffold_trove(project_folder: Path, *, trove_name: str = "nyxloom-trove") -> Path:
    """Scaffold `<project_folder>/<trove_name>/` from this package's bundled
    templates (STANDARD.md + AUTHORING.md copied verbatim, a fresh
    nyxloom.toml with [project] id = basename(<project_folder>)).

    Raises TroveAlreadyExists if the trove folder already exists (never
    overwrites) and TemplatesMissing if this package's own bundled
    STANDARD.md/AUTHORING.md cannot be found. `run_wizard` calls this only
    when no trove exists yet; `cli.cmd_init` is a thin wrapper around it.
    """
    import shutil

    trove_dir = project_folder / trove_name
    if trove_dir.exists():
        raise TroveAlreadyExists(trove_dir)

    # This package's own canonical trove ships STANDARD.md/AUTHORING.md;
    # src/nyxloom/onboarding.py -> src/ -> nyxloom/ (repo root of this package).
    template_dir = Path(__file__).resolve().parent.parent.parent / "nyxloom-trove"
    standard_src = template_dir / "STANDARD.md"
    authoring_src = template_dir / "AUTHORING.md"
    if not standard_src.exists() or not authoring_src.exists():
        raise TemplatesMissing(template_dir)

    trove_dir.mkdir(parents=True)
    shutil.copyfile(standard_src, trove_dir / "STANDARD.md")
    shutil.copyfile(authoring_src, trove_dir / "AUTHORING.md")

    project_id = project_folder.resolve().name
    (trove_dir / "nyxloom.toml").write_text(
        _INIT_NYXLOOM_TOML.format(project_id=project_id), encoding="utf-8"
    )

    (trove_dir / "handoffs").mkdir()
    (trove_dir / "reports").mkdir()
    (trove_dir / "archive").mkdir()
    (trove_dir / "archive" / ".gitkeep").touch()
    (trove_dir / "agent-logs").mkdir()
    (trove_dir / "agent-logs" / ".gitkeep").touch()
    (trove_dir / "decisions.md").write_text(
        f"# {project_id} dev decisions inbox — product calls awaiting the user (D-<NNN>).\n",
        encoding="utf-8",
    )
    (trove_dir / "roadmap.md").write_text(
        f"# {project_id} dev roadmap\n", encoding="utf-8"
    )
    (trove_dir / "backlog.md").write_text(
        f"# {project_id} dev backlog — un-carved ideas\n", encoding="utf-8"
    )
    (trove_dir / ".gitignore").write_text("agent-logs/\n", encoding="utf-8")

    return trove_dir


# ---------------------------------------------------------------------------
# spine instantiation (docs/spine-documents-spec.md, PACKAGE F1 schemas)

# (nyxloom.toml [project] key, numeric-prefixed filename, frontmatter `kind`)
_SPINE_DOC_SPECS: tuple[tuple[str, str, str], ...] = (
    ("north_star", "1-north-star.md", "north-star"),
    ("product_definition", "2-product-definition.md", "product-definition"),
    ("roadmap", "3-roadmap.md", "roadmap"),
    ("backlog", "4-backlog.md", "backlog"),
)


def _minimal_spine_content(kind: str, project_id: str) -> str:
    """Minimal-valid frontmatter (schema-checked by lint.lint_spine's S1)
    plus a short placeholder body -- the body is human prose, never
    machine-parsed (docs/spine-documents-spec.md's format decision), so its
    exact wording carries no contract."""
    if kind == "north-star":
        fm = {"kind": "north-star", "schema_version": 1}
        heading = "north star"
        note = (
            "> Replace this body with the project's real vision narrative -- via\n"
            "> the guided questionnaire (F4, not yet built) or by hand.\n"
        )
    elif kind == "product-definition":
        fm = {
            "kind": "product-definition",
            "schema_version": 1,
            "product_version": 1,
            "features": [],
        }
        heading = "product definition"
        note = (
            "> `features:` starts empty -- the guided questionnaire (F4) or the\n"
            "> AI assessment scan (F3) populate it, not this engine.\n"
        )
    elif kind == "roadmap":
        fm = {"kind": "roadmap", "schema_version": 1, "milestones": []}
        heading = "roadmap"
        note = "> `milestones:` starts empty -- populated once product-definition is.\n"
    elif kind == "backlog":
        fm = {"kind": "backlog", "schema_version": 1, "items": []}
        heading = "backlog"
        note = "> `items:` starts empty -- populated by the questionnaire or by hand.\n"
    else:  # pragma: no cover -- _SPINE_DOC_SPECS is the only caller
        raise ValueError(f"unknown spine kind: {kind!r}")

    fm_text = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False)
    body = (
        f"# {project_id} — {heading}\n\n"
        f"> Auto-generated minimal-valid placeholder (`nyxloom onboard`, PACKAGE F2).\n"
        f"{note}"
    )
    return f"---\n{fm_text}---\n\n{body}"


def _instantiate_spine(trove_dir: Path, project_id: str) -> tuple[list[str], list[str]]:
    """Write any MISSING spine doc; leave an already-present one untouched.
    Returns (created_relpaths, skipped_relpaths), both relative to the
    project root (trove_dir's parent)."""
    created: list[str] = []
    skipped: list[str] = []
    for _field_name, filename, kind in _SPINE_DOC_SPECS:
        doc_path = trove_dir / filename
        rel = f"{trove_dir.name}/{filename}"
        if doc_path.exists():
            skipped.append(rel)
            continue
        doc_path.write_text(_minimal_spine_content(kind, project_id), encoding="utf-8")
        created.append(rel)
    return created, skipped


# ---------------------------------------------------------------------------
# nyxloom.toml spine-key wiring

_TOML_KEY_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _wire_spine_keys(toml_path: Path, trove_name: str) -> list[str]:
    """Append any MISSING north_star/product_definition/roadmap/backlog key
    to the `[project]` section of `toml_path` (surgical text insert -- no
    TOML writer dependency, mirroring config.update_project_policy's
    technique). An already-set key (any value) is left untouched -- an
    explicit repoint is authoritative. Returns the newly-added key names
    (empty list on the idempotent re-run case)."""
    desired = {
        "north_star": f"{trove_name}/1-north-star.md",
        "product_definition": f"{trove_name}/2-product-definition.md",
        "roadmap": f"{trove_name}/3-roadmap.md",
        "backlog": f"{trove_name}/4-backlog.md",
    }

    text = toml_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    section_start: int | None = None
    section_end = len(lines)
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "[project]":
            section_start = i
        elif section_start is not None and i > section_start and stripped.startswith("[") and stripped.endswith("]"):
            section_end = i
            break
    if section_start is None:
        raise OnboardingError(f"no [project] section found in {toml_path}")

    present: set[str] = set()
    for i in range(section_start + 1, section_end):
        m = _TOML_KEY_LINE_RE.match(lines[i])
        if m:
            present.add(m.group(1))

    missing = {k: v for k, v in desired.items() if k not in present}
    if not missing:
        return []

    new_lines = [f'{key} = "{value}"\n' for key, value in missing.items()]
    lines[section_end:section_end] = new_lines

    toml_path.write_text("".join(lines), encoding="utf-8")
    return list(missing.keys())


def _project_id_from_toml(toml_path: Path, fallback: str) -> str:
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return fallback
    pid = data.get("project", {}).get("id")
    return pid if isinstance(pid, str) and pid else fallback


# ---------------------------------------------------------------------------
# the engine entry point

@dataclass
class OnboardResult:
    trove_dir: Path
    created_docs: list[str]
    skipped_docs: list[str]
    wired_keys: list[str]
    answers_path: Path


def run_wizard(
    project_root: Path,
    answers: WizardAnswers,
    *,
    trove_name: str = "nyxloom-trove",
) -> OnboardResult:
    """The F2 engine entry point -- NON-AI, deterministic, no `input()` in
    this core (a surface collects `answers` and passes them in; see
    cli.cmd_onboard for the scriptable CLI surface). See the module
    docstring for the full 4-step contract. Idempotent: safe to call
    repeatedly, including with different answers on each call.
    """
    trove_dir = project_root / trove_name
    if not trove_dir.is_dir():
        scaffold_trove(project_root, trove_name=trove_name)

    toml_path = trove_dir / "nyxloom.toml"
    if not toml_path.is_file():
        raise OnboardingError(
            f"{toml_path} not found -- cannot wire spine keys "
            "(trove exists but has no nyxloom.toml)"
        )

    project_id = _project_id_from_toml(toml_path, project_root.resolve().name)

    created_docs, skipped_docs = _instantiate_spine(trove_dir, project_id)
    wired_keys = _wire_spine_keys(toml_path, trove_name)
    recorded_path = _record_answers(trove_dir, answers)

    return OnboardResult(
        trove_dir=trove_dir,
        created_docs=created_docs,
        skipped_docs=skipped_docs,
        wired_keys=wired_keys,
        answers_path=recorded_path,
    )


# ---------------------------------------------------------------------------
# F3 (DONE, 2026-07-17): the `/review`-style read-only assessment-scan agent
# lives in onboarding_scan.py (run_assessment_scan(project_root, answers) ->
# AssessmentResult, stored at `<trove>/onboarding-assessment.json`) -- kept
# out of this module so the non-AI wizard core above stays pure (no AI/LLM
# consulted anywhere in run_wizard). cli.cmd_onboard's `--scan` flag calls it
# AFTER run_wizard returns, with the very answers just recorded here.

# TODO(F4): the guided questionnaire (extends intake_chat.py) that actually
# AUTHORS north-star-first / product-definition / roadmap / backlog content
# for a project whose spine this engine only seeded with minimal-valid
# placeholders (see _minimal_spine_content). Also reads load_answers() to
# set its depth/starting point. NOT built in this package -- out of F2's
# scope.
