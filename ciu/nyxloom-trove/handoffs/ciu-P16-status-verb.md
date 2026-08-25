---
schema_version: 1
id: ciu-P16-status-verb
project: ciu
component: cli
title: "New read-only `ciu status [--profile NAME] [--json]` verb: selected stacks -> compose projects -> running containers -> health -> image reference"
tier: implement-2
input_revision: "370ea8141f7f69399a751f2d5731a8ccf5419921"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-6"}
stack: none
depends_on: [P15]
session: fresh
scope:
  touch:
    - "src/ciu/deploy.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_cli_status.py"
    - "docs/SPEC.md"
    - "docs/FEATURES.md"
    - "README.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P16-status-verb-LOG.md"
  forbid:
    - "src/ciu/diagnose.py"
    - "src/ciu/engine.py"
    - "src/ciu/deploy_pkg/health.py"
    - "src/ciu/config_model.py"
    - "src/ciu/worktree.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-resolution
    observable: "action_status(repo_root, profile, selection) resolves EVERY selected stack (from deploy.build_selection, same call chain `ciu up --profile` uses — load_global_config -> resolve_profiles -> build_selection) to its compose project via a new extracted single-stack helper (mirror _stack_compose_projects's existing per-entry branching: engine.compose_project_name when deploy.project_name/environment_tag are BOTH set, else engine.identity_compose_project_name — reuse both verbatim, do not reimplement the branching), then calls diagnose._inspect(project) (already project-scoped Docker inspect, already returns full container State/Config JSON — reuse it, do not hand-roll a second docker ps/inspect pair) to get that stack's containers. A stack directory that does not exist on disk is skipped with a named reason in its row (matching _stack_compose_projects's existing is_dir() skip), never silently dropped from output."
    negative: "reimplementing compose-project derivation inline instead of reusing engine.compose_project_name/identity_compose_project_name; a second bespoke docker inspect call when diagnose._inspect already does this; a missing stack directory vanishing from the report with no trace"
    gate: "tester-unified"
  - id: O2-envelope
    observable: "`ciu status --json` emits ONE document: {schema_version: 1, profile: <resolved profile name or null for default/all>, stacks: [{path, name, phase_key, compose_project, containers: [{name, status, image}]}]}. `status` per container is health_pkg.classify()'s existing closed vocabulary (healthy/starting/unhealthy/no-healthcheck/not-found) applied to the container's own State dict from the inspect payload already fetched — do not re-derive status a different way than the rest of the codebase does. `image` is the container's Config.Image field verbatim (no normalization -- that's provenance's job, not status's). Without --json, human output is one line per stack: `<name>  <compose_project>  <container>=<status> ...` (exact formatting your choice, but every field above must be represented)."
    negative: "a home-grown status vocabulary that doesn't match classify()'s existing values; inventing a new schema_version numbering scheme instead of the plain top-level int every other machine document in this codebase uses (worktree.py, deploy.py provenance -- grep `schema_version` for the pattern)"
    gate: "tester-unified"
  - id: O3-cli
    observable: "`ciu status [--profile NAME] [--json]` is wired in cli.py's verb dispatch (mirror the argparse-per-verb inline pattern used by `dev`/`bake`/`provenance`) and appears in `_USAGE`/`_VERB_HELP`. Read-only: no compose up/down/build/exec is invoked; a docker daemon failure produces a clean error message and non-zero exit (2), never a stack trace to the user."
    negative: "status silently succeeding with an empty report when Docker itself is unreachable (that is a determination failure, not 'nothing running' -- the two must not collapse into the same output, per AGENTS.md 'a check is only as strong as what it actually compares')"
    gate: "tester-unified"
  - id: O4-docs
    observable: "README.md gains a one-line feature bullet (mirror the existing `ciu diagnose` bullet's voice/length). docs/FEATURES.md's CLI table gains a `status` row. docs/SPEC.md documents the verb (new S-numbered subsection under S7 or S17, your call — pick whichever existing section this most resembles and say why in the LOG). docs/CONSUMERS.md gets one worked --json example. CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md CIU-QOL-6 row -> FIXED with evidence."
    negative: "a new verb shipped without a SPEC section (violates AGENTS.md's 'user-facing docs are part of the change' rule) or without a FEATURES.md row"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "diagnose._inspect is in scope.forbid for a reason -- if calling it from deploy.py would require a change to diagnose.py itself (not just an import), BLOCKED naming the exact incompatibility rather than editing a forbidden file"
  - "a docker daemon failure inside diagnose._inspect raises something action_status cannot cleanly distinguish from 'zero containers running' -- BLOCKED naming the ambiguity rather than silently treating both as the same status (this is the exact anti-pattern AGENTS.md's 'absence for emptiness' names)"
mutexes: [merge-lane]
review_focus:
  - "the false-PASS attack: Docker daemon unreachable entirely -- does `ciu status` report an empty/healthy-looking result, or does it clearly fail? (AGENTS.md 'A check is only as strong as what it actually compares', anti-pattern #2 'Absence for emptiness')"
  - "a stack selected by the profile but not yet deployed (no compose project exists yet) -- does its row say so plainly, or does it error out the whole command (one missing stack should not hide every other stack's status)"
  - "no compose up/down/exec side effect anywhere in this code path -- status is read-only by name and by contract"
---

# ciu-P16 — `ciu status` verb (CIU-QOL-6)

## Context to read first
1. `docs/BACKLOG-2026-08-24.md#CIU-QOL-6` (already in your context via
   `source`) — the ask and its proposed surface.
2. `src/ciu/deploy.py`:
   - `_stack_compose_projects` (~line 1913-1953) — READ IN FULL. Your
     per-stack project resolution must mirror its exact branching
     (tags-present → `engine.compose_project_name`; tags-absent →
     `engine.identity_compose_project_name`) but at PER-ENTRY granularity
     (this function currently returns a flat deduplicated list; you need the
     entry→project association, not just the set of projects). Extract a
     single-stack helper both this function and your new `action_status` can
     call, OR inline the same two-line branch in `action_status` if
     extraction feels like more churn than value — your call, but do not let
     the two branches drift out of sync with each other.
   - `main` / `_run` (~line 2689-2746) — the exact resolution chain
     `ciu up --profile` uses: `load_global_config(repo_root)` →
     `resolve_profiles(global_cfg, cli_profiles)` → `build_selection(profile,
     cli_phases)`. Your new verb reuses this chain unchanged for `--profile`
     resolution (a bare `ciu status` with no `--profile` behaves like a bare
     `ciu up` with no `--profile` — default/all phases).
3. `src/ciu/diagnose.py` — READ IN FULL (104 lines). `_inspect(project)`
   (~line 33) is your container-resolution primitive: one Docker Compose
   project label filter → full `docker inspect` JSON per container (Name,
   State incl. Health, Config.Image — everything you need in one call, no
   second round-trip). It is in `scope.forbid` because you are consuming it
   READ-ONLY via import, not modifying it — if you find yourself wanting to
   change it, that's a signal you're solving the wrong problem.
4. `src/ciu/deploy_pkg/health.py` — `classify(inspect_state: dict | None) ->
   str` (~line 26) — the closed status vocabulary
   (healthy/starting/unhealthy/no-healthcheck/not-found) already used
   everywhere else in this codebase for exactly this purpose. Apply it to
   each container's own `State` dict from the `_inspect` payload.
5. `src/ciu/worktree.py` JSON envelope precedent — grep `schema_version` for
   3-4 examples (e.g. `worktree branches`'s `BRANCHES_SCHEMA_VERSION`
   document at ~line 1048) to match the top-level-int + closed-fields
   convention exactly; do not invent a different shape.
6. `src/ciu/cli.py` — the `bake`/`dev`/`provenance` verb blocks (~1337-1400,
   already read during this wave's earlier packages if you have access to
   `nyxloom-trove/ciu-qol-v8prep-wave-BRIEF-2026-08-25.md`'s history — if not,
   read them fresh) for the inline-argparse-per-verb pattern and `_USAGE`/
   `_VERB_HELP` update locations.

## Implementation packet (normative)

### Owned interfaces
- `deploy.py`: `def action_status(repo_root: Path, profile: profiles_pkg.
  Profile, selection: list[dict], *, json_output: bool) -> int`.
- JSON envelope: `{"schema_version": 1, "profile": str | None, "stacks":
  [{"path": str, "name": str, "phase_key": str, "compose_project": str |
  None, "containers": [{"name": str, "status": str, "image": str | None}]}
  ]}`. `compose_project: None` + empty `containers` when the stack directory
  doesn't exist on disk yet (never omit the stack row).

### Construction and state flow
1. Resolve `global_cfg`/`profile`/`selection` via the existing chain (Context
   item 2).
2. For each `entry` in `selection`: resolve `stack_dir`; if missing, emit a
   row with `compose_project: None`, `containers: []`; else resolve its
   compose project (Context item 2's branching); call
   `diagnose._inspect(project)`; for each returned item, `name =
   item["Name"].lstrip("/")`, `status = health_pkg.classify(item.get
   ("State"))`, `image = item.get("Config", {}).get("Image")`.
3. A `RuntimeError` from `_inspect` (Docker daemon unreachable, per its own
   docstring) is NOT caught-and-emptied — it propagates to a top-level
   handler in the CLI verb that prints a clear `[ERROR] ciu status: <reason>`
   to stderr and exits 2. Zero containers for a project that DOES exist is a
   legitimate empty `containers: []` (stack not started) — these two failure
   modes must never collapse into the same output (review_focus's false-PASS
   attack).

### Decision table
| state | outcome |
|---|---|
| stack dir missing | row with `compose_project: None`, empty containers |
| compose project exists, zero containers | row with resolved project, empty containers (stack not started — not an error) |
| compose project exists, containers running | full per-container rows |
| Docker daemon unreachable | verb exits 2 with a clear message — NOT an empty/success report |

### Degrees of freedom
Human (non-JSON) output formatting, whether the single-stack project-resolve
branch is extracted into a shared helper or duplicated inline (keep both call
sites byte-identical in logic either way), and the exact SPEC section number
you file this under.

## Work
1. `action_status` in `deploy.py` per the packet (O1, O2).
2. `ciu status` verb wiring in `cli.py`, `_USAGE`/`_VERB_HELP` (O3).
3. Tests in `tests/tests/test_ciu_cli_status.py`: missing-stack row, empty
   (not-yet-started) project, populated project with mixed container
   statuses, and the Docker-unreachable-vs-empty distinction from
   review_focus (fake the Docker daemon failure via a monkeypatched
   `diagnose._inspect`/subprocess seam — no real Docker calls in tests, per
   AUTHORING.md §3b.E).
4. Docs per O4.
5. LOG at `nyxloom-trove/reports/ciu-P16-status-verb-LOG.md`.

## Environment setup
Same worktree, same venv as prior packages in this wave:
`cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu && .venv/bin/python run-ciu-tests.py`

## BLOCKED rule
Per `escalate_if` above. Forbidden workaround: treating "Docker unreachable"
and "nothing running" as the same reportable state.
