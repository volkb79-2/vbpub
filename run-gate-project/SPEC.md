# run-gate SPEC — normative implementation contract

**Status:** NORMATIVE for the P01 build. Rev 2: adds exec-mode + extra-mounts. Distilled from `README.md` (design
authority), `CONSUMERS.md` (adoption contract), `HANDOFF-P01` (build contract)
and the controller's session amendments (§8). Requirement IDs (`R-xx`) are the
adherence targets: the implementation conforms to THESE sentences; the
adversarial review cites them. Where this spec and the prose documents
disagree, §8 amendments win, then README, then CONSUMERS.

## 1. Terms

- **project config** — `run-gate.toml` in the directory of the *invoked script
  path* (symlink's parent, never the symlink target's dir), CWD as fallback.
  Defines lanes; may also define environments.
- **central config** — the nearest `run-gate.toml` in a STRICT ancestor
  directory of the project dir. Optional. Defines shared environment facts
  ONLY (lanes are rejected there).
- **environment** — a named container/host execution fact set: `image`
  (required), `cgroup_slice` (optional), `mode` (optional, `"ephemeral"`
  or `"exec"`, default `"ephemeral"`). `host` is a built-in name (never
  definable) meaning "no container".
- **judged worktree** — the git toplevel containing the project dir, unless
  `--worktree` overrides (the daemon case).
- **repo root** — the checkout owning the shared `.git` (for a linked
  worktree: the common dir's parent). Mounts operate at THIS level.

## 2. CLI contract

- `R-01` `<lane>` runs one lane; `--list` emits `name<TAB>kind<TAB>environment`
  per lane, sorted by name; `--help`/no-args prints revision + lane table;
  unknown lane exits non-zero naming the known lanes and the config path.
- `R-02` `--worktree PATH` overrides the judged worktree (daemon substitutes
  its attempt path textually before invoking).
- `R-03` `--allow-dirty` bypasses the clean-tree pre-check (assay lanes still
  fail closed inside assay itself — this flag never weakens assay).
- `R-04` Every config/env error is ONE line on stderr, exit 1, naming the
  offending key AND file. A traceback reaching the user for any config/usage
  error is a defect. `{worktree}` in a lane argv is substituted textually with
  the judged worktree path (all occurrences, every element).
- `R-05` The tool prints, before executing: revision, lane name, environment
  source, resolved slice + its source, and (container lanes) the full docker
  argv. Mechanics are visible, never buried.

## 3. Config schema (both files; `schema_version` must equal 1)

- `R-06` Top-level keys: `schema_version` (required), `environments`,
  `lanes`. Unknown top-level key → error naming key + file. A central config
  containing `[lanes.*]` → error.
- `R-07` `[environments.<name>]`: `image` (non-empty string, required),
  `cgroup_slice` (optional non-empty string). Redefining `host` → error.
- `R-08` `[lanes.<name>]` keys: `kind` (`"command"`|`"assay"`), `environment`
  (non-empty string), `argv` (command kind: non-empty string list),
  `assay_lane` + `assay_command` (assay kind: both required; `assay_command`
  is a non-empty string list — the tool NEVER invents an assay invocation),
  `pins` (assay kind: table of `{sha256 = "<path>", version = "<str>"}` —
  `sha256` required, path relative to the project), `clean_tree` (bool,
  default **true**), `budget` (`\d+[smh]`, advisory only), `memory`
  (`\d+[bkmg]?`, docker `--memory`).
- `R-09` Environment resolution: project `[environments.<name>]` shadows the
  central one entirely (same name = project wins, no field merging); an
  undefined name → error naming the lane and BOTH candidate files. The
  environment's origin (project vs central + path) is printed (R-05).

## 4. Environment-fact resolution (DERIVE / READ / FAIL — never invent)

- `R-10` **Slice:** the environment's declared `cgroup_slice` wins (source
  printed as declared policy); otherwise `$CGROUP_PARENT_DEV_BACKGROUND`
  (required — absent is a hard error naming the var and the declared-slice
  alternative). No literal, no fallback anywhere in the source.
- `R-11` **LoadState pre-check** runs ONLY where systemd is reachable
  (`[ -d /run/systemd/system ]` equivalent); elsewhere skipped (containerized
  contexts ship a shim). Not-loaded → hard error warning about fail-open
  transient slices.
- `R-12` **Physical repo root:** derived from `/proc/self/mountinfo` (bind
  mount whose mount point contains the repo root; longest mount point wins;
  octal escapes `\040 \011 \012 \134` decoded; the `/` overlay never used).
  Outside a container (`/.dockerenv` absent) the path is already physical.
  Inside a container with no containing bind mount → hard error (a guess
  would silently mount the wrong tree).
- `R-13` **Repo/worktree:** `git rev-parse --show-toplevel` (judged tree) and
  `--git-common-dir` (repo root = its parent for linked worktrees). git
  failure → one-line error carrying git's own last stderr line.

## 5. Execution contract

- `R-14a` **Exec mode (`mode = "exec"`)** runs inside a PERSISTENT,
  externally-managed container via `docker exec`. run-gate refuses to start
  the container itself — that belongs to the project's deployment authority
  (e.g. CIU). The container name is resolved from a declared `container_name`
  on the environment, or derived from the repo's `ciu.global.toml` [deploy]
  table (`project_name + environment_tag`, falling back to
  `network_name stripped of "-network"`). Missing config → hard error naming
  what to fix. If the resolved container is not running → hard error naming
  the lifecycle command to start it; no silent fallback.

- `R-14b` **Extra mounts:** `$RUN_GATE_EXTRA_MOUNTS` is an optional colon-
  separated list of `host=container` pairs appended as `-v` flags to ephemeral
  container lanes only. Malformed entries fail fast. This exists so projects
  that need Docker-in-Docker can pass `/var/run/docker.sock=/var/run/docker.sock`
  without hardcoding infrastructure in the TOML config.

- `R-14` **Clean tree:** unless `clean_tree = false` or `--allow-dirty`, the
  judged worktree must have zero `git status --porcelain` entries; refusal
  names the count, first entry, and the flag escape.
- `R-15` **Container lanes** run detached: `docker run -d --name
  run-gate-<repo>-<lane>-<pid>-<epoch> --cgroup-parent <slice> -e
  CGROUP_PARENT_DEV_BACKGROUND=<slice> -v <phys>:<phys> -v <phys>:<repo>
  [--memory M] <image> bash -c <inner>` — the slice passed BOTH ways
  (`--cgroup-parent` AND `-e`), the repo dual-mounted (physical AND
  namespace paths — worktree gitfiles), `--rm` never used (explicit
  `docker rm -f` in a finally).
- `R-16` **Inner command** (both kinds) starts `set -euo pipefail && git
  config --global safe.directory '*' && ...`. Command kind: the lane argv
  (`{worktree}`-substituted, shell-quoted) appended. Assay kind: `cd
  <project_dir>` first, then per pin `(cd <pin's parent dir> && sha256sum -c
  <bare filename>)` — verification FROM the pin file's own directory — then
  `mkdir -p .assay`, then `<assay_command> run <assay_lane> --file
  assay.toml --verdict-json .assay/verdict-<assay_lane>.json`.
- `R-17` **Run form + status:** `docker logs -f` streams; `docker wait`
  supplies the exit code, which IS the tool's exit code (no masking); an
  unreadable exit status → hard error ("refusing to guess"), never 0.
- `R-18` **Verdict discipline:** assay lanes print the verdict artifact path
  (namespace-visible) after the run; every lane prints a final
  `lane '<name>' exit <code>` line.
- `R-19` **Host lanes** exec the substituted argv directly with cwd = project
  dir (no docker, no safe.directory — that trap is container-specific); exit
  passthrough identical.

- `R-19a` **safe.directory scope:** both ephemeral and exec inner commands set
  `GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig` before running `git config --global
  safe.directory '*'`. This avoids writing to `~/.gitconfig`, which may be
  read-only or shared across containers.
- `R-20` `budget` is parsed, validated, and PRINTED as advisory; the tool
  does not enforce it (consumers may).

## 6. Non-goals (unchanged from CONSUMERS)

No second parser of `run-gate.toml`; no judgment policy here (assay owns
floors/R-levels/verdict meaning); no non-stdlib imports; no silent defaults
for environment facts; no test definitions in consumer configs — the SSOT
is `run-gate.toml`; release policy stays with the consumer (cmru/nyxloom).

## 7. Distribution (unchanged from README/CONSUMERS)

vbpub-internal: relative symlink `../run-gate-project/run-gate.py` committed
at the project root. External repos: copy the file; `__revision__` is the
drift marker. Stdlib only, runs on a fresh clone with zero installs.

## 8. Controller amendments (this session, 2026-08-22)

- **A1 — adoption scope:** first consumer is **nyxloom** (command-kind lane);
  ciu adoption is DEFERRED (ciu under parallel development; its tree is
  off-limits this package). The assay-kind path ships construction-tested
  (fake-docker pins) with live proof deferred to ciu's adoption.
- **A2 — central defaults:** shared environment facts live ONCE in a
  repo-root `run-gate.toml` (vbpub root); projects inherit by the §1
  discovery rule; lanes override per-lane (`memory`, declared
  `cgroup_slice`). No per-project hardcoded slice names.
- **A3 — slice policy:** `$CGROUP_PARENT_DEV_BACKGROUND` is the correct
  ambient default for dev gates. `nyxloom-gates.slice` was intended for a
  FUTURE prod instance outside this repo — nyxloom's dev gate migrates OFF
  the hardcoded literal onto ambient resolution (a declared `cgroup_slice`
  remains available for genuine per-environment policy).

## 9. Open items (explicitly OUT of this package)

Docker-probe slice verification where systemd is unreachable (cmru
tester-gate precedent); budget enforcement; async long lanes (assay B009);
dstdns adoption (timed per D-111); ciu adoption + live assay proof (A1).
