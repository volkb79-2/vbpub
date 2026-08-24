# run-gate SPEC — normative implementation contract

**Status:** NORMATIVE for the P01 build. Rev 2: adds exec-mode + extra-mounts. Rev 3: backlog-sweep
amendments (RG-15 `R-21` effective-tree execution; RG-11 reserved exit codes in `R-04`; RG-3
`R-23` dual-mount guard). Rev 4: continues the backlog sweep — RG-16 central lanes (`R-22`),
RG-17/RG-19 declared inputs (`R-24`), RG-1 override-reachability guard (`R-25`), RG-12
failing-container evidence (`R-26`), RG-10 declared `artifacts` + unconditional evidence-path
disclosure (`R-08`, `R-18`), RG-2 pointer↔lane linkage verb (`R-27`). Distilled from `README.md` (design
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
  and (since Rev 3, RG-16) shared lanes inherited by every consumer.
- **environment** — a named container/host execution fact set: `image`
  (required), `cgroup_slice` (optional), `mode` (optional, `"ephemeral"`
  or `"exec"`, default `"ephemeral"`), `forward_env` (optional list of
  environment-variable names). `host` is a built-in name (never definable)
  meaning "no container".
- **judged worktree** — the git toplevel containing the project dir, unless
  `--worktree` overrides (the daemon case).
- **repo root** — the checkout owning the shared `.git` (for a linked
  worktree: the common dir's parent). Mounts operate at THIS level.

## 2. CLI contract

- `R-01` `<lane>` runs one lane; `--list` emits `name<TAB>kind<TAB>environment`
  per lane, sorted by name — THREE columns, stable, machine-readable, never
  extended (consumers parse it). `--help`/no-args prints revision + the
  human lane table — each lane's `clean_tree`, advisory `budget`, `memory`,
  and declared `description` — plus a FLAGS section (`--worktree`;
  `--allow-dirty` with the caveat that assay lanes still enforce assay's own
  clean-tree rule) and an ENVIRONMENT CONTRACT section naming every
  environment variable the tool reads and when it fails
  (`CGROUP_PARENT_DEV_BACKGROUND`, `RUN_GATE_EXTRA_MOUNTS`,
  `RUN_GATE_MOUNT_ALIAS`) plus `--check-env` (R-24 drift sweep) (RG-7);
  unknown lane exits non-zero naming the
  known lanes and the config path.
- `R-02` `--worktree PATH` overrides the judged worktree (daemon substitutes
  its attempt path textually before invoking).
- `R-03` `--allow-dirty` bypasses the clean-tree pre-check (assay lanes still
  fail closed inside assay itself — this flag never weakens assay).
- `R-04` Every config/env error is ONE line on stderr naming the offending
  key AND file; a traceback reaching the user for any config/usage error is a
  defect. Reserved exit codes (RG-11): the LANE'S OWN status passes through
  unchanged; tool refusals and failures use **2** = configuration/refusal
  (bad or unknown key/lane/environment, dirty tree, preflight refusals) and
  **3** = execution-infrastructure failure (docker/git/mountinfo could not do
  their job). Uniform legacy exit 1 for every refusal is superseded.
  `{worktree}` in a lane argv is substituted textually with the judged
  worktree path (all occurrences, every element). Because consumer pointers
  embed that path into `bash -c` STRINGS unquoted (RG-5), the resolved
  worktree must be gate-safe — `^[A-Za-z0-9_./][A-Za-z0-9_./-]*$`, i.e. no
  whitespace or shell metacharacters anywhere in the path; any lane kind run
  from a tree at an unsafe path refuses (exit 2) before execution, naming
  the offending characters.
- `R-05` The tool prints, before executing: revision, lane name, environment
  source, resolved slice + its source, and (container lanes) the full docker
  argv. Mechanics are visible, never buried.

## 3. Config schema (both files; `schema_version` must equal 1)

- `R-06` Top-level keys: `schema_version` (required), `environments`,
  `lanes`. Unknown top-level key → error naming key + file. A central
  config's `[lanes.*]` are legal (R-22).
- `R-07` `[environments.<name>]`: `image` (non-empty string, required),
  `cgroup_slice` (optional non-empty string), `forward_env` (optional,
  unique list of valid environment-variable names; values are forwarded to
  container lanes only when set). Redefining `host` → error.
- `R-08` `[lanes.<name>]` keys: `kind` (`"command"`|`"assay"`), `environment`
  (non-empty string), `argv` (command kind: non-empty string list),
  `assay_lane` + `assay_command` (assay kind: both required; `assay_command`
  is a non-empty string list — the tool NEVER invents an assay invocation),
  `pins` (assay kind: table of `{sha256 = "<path>", version = "<str>"}` —
  `sha256` required, path relative to the project; a declared `version` is
  VERIFIED in-lane via `<assay_command> --version`, which must succeed and
  report it — declaring it asserts the command honors that convention,
  RG-4), `clean_tree` (bool, default **true**), `budget` (`\d+[smh]`,
  advisory only), `memory` (`\d+[bkmg]?`, docker `--memory`),
  `description` (optional non-empty string, one line, shown by `--help`),
  `required_env` (optional unique list of valid environment-variable names
  this lane's tests REQUIRE — enforced per R-24), `artifacts` (optional
  NON-EMPTY list of non-empty path strings the lane is expected to leave
  behind — disclosed after every run per R-18; `{worktree}` tokens are
  substituted, relative entries resolve against the effective project dir).
- `R-09` Environment resolution: project `[environments.<name>]` shadows the
  central one entirely (same name = project wins, no field merging); an
  undefined name → error naming the lane and BOTH candidate files. The
  environment's origin (project vs central + path) is printed (R-05).

- `R-22` **Shared central lanes (RG-16):** a central config may define
  `[lanes.*]`, schema-validated wherever it lives. The EFFECTIVE lane set of
  a project = its own lanes shadowing central lanes BY NAME (whole lane — no
  field merging); `--list`/usage show the effective set, inherited entries
  marked `*`. Per-consumer check at load: a central lane's pin sidecars must
  exist relative to the CONSUMING project dir, else refusal naming lane,
  sidecar, and both files (vendor it or shadow the lane). Free-form argv
  strings are deliberately never stat'd — they are shell text, not declared
  paths. Malformed central tables fail loudly as always.

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
  what to fix. If the resolved container is not running → hard error whose
  START REMEDY names the authority the name was resolved FROM (RG-6):
  declared `container_name` → the project's OWN deployment authority;
  ciu.global.toml-derived → the ciu lifecycle (`ciu render` if stale, then
  `ciu up`) naming the config file used. A non-ciu project must never be
  prescribed a ciu command. No silent fallback.

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
  CGROUP_PARENT_DEV_BACKGROUND=<slice> <mounts per R-23>
  [--memory M] <image> bash -c <inner>` — the slice passed BOTH ways
  (`--cgroup-parent` AND `-e`), the repo dual-mounted (physical AND
  namespace paths — worktree gitfiles), `--rm` never used (explicit
  `docker rm -f` in a finally).
- `R-16` **Inner command** (both kinds) starts `set -euo pipefail && git
  config --global safe.directory '*' && ...`. Command kind: the lane argv
  (`{worktree}`-substituted, shell-quoted) appended. Assay kind: `cd
  <effective project dir>` first (R-21), then per pin `(cd <pin's parent dir>
  && sha256sum -c <bare filename>)` — verification FROM the pin file's own
  directory — then `mkdir -p .assay`, then `<assay_command> run <assay_lane>
  --file assay.toml --verdict-json .assay/verdict-<assay_lane>.json`.
- `R-17` **Run form + status:** `docker logs -f` streams; `docker wait`
  supplies the exit code, which IS the tool's exit code (no masking); an
  unreadable exit status → hard error ("refusing to guess"), never 0.
- `R-18` **Verdict discipline:** after EVERY lane exit — any kind, any
  runner mode, success or failure — the gate says where the evidence
  landed: assay lanes always print the verdict artifact path
  (`<effective project dir>/.assay/verdict-<assay_lane>.json`,
  namespace-visible); declared `artifacts` entries each print as
  `run-gate: artifact: <path>` (absolute-or-effective-project-dir-relative,
  `{worktree}`-substituted, deduplicated against the verdict convention);
  every lane prints a final `lane '<name>' exit <code>` line. Disclosure is
  unconditional — a FAILED lane names its evidence paths too.
- `R-19` **Host lanes** exec the substituted argv directly with cwd = the
  effective project dir (R-21; no docker, no safe.directory — that trap is
  container-specific); exit passthrough identical.

- `R-19a` **safe.directory scope:** both ephemeral and exec inner commands set
  `GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig` before running `git config --global
  safe.directory '*'`. This avoids writing to `~/.gitconfig`, which may be
  read-only or shared across containers.
- `R-20` `budget` is parsed, validated, and PRINTED as advisory; the tool
  does not enforce it (consumers may).

- `R-21` **Effective tree (RG-15):** all user-declared execution paths —
  the assay `cd`, pin verification, verdict/artifact locations, command-argv
  `{worktree}` substitution, host-lane cwd — resolve against the JUDGED
  WORKTREE via `<worktree>/<project-relative-to-invocation-toplevel>`. With
  no `--worktree` override this is exactly the invocation project dir.
  Refusal when the project dir lies outside its own toplevel (nothing then
  defines its position inside the override tree). Existence inside the
  override tree is NEVER pre-checked with a local stat — the override tree
  may live in another mount namespace; the inner `cd` fails loudly where the
  right view exists.

- `R-23` **Dual-mount guard (RG-3):** R-15's two repo views must stay
  DISTINCT. When the derived physical path equals the namespace path (bare
  host — mountinfo offers no alias), emitting both `-v` flags would collapse
  into one silent single mount diverging from the documented recipe, so the
  gate refuses (exit 2) unless `$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'`
  declares the second view. The alias's host side must equal this gate's
  repo root; malformed or mismatched declarations are refused by name.
  Both views always bind-mount the SAME physical tree — only their
  container-side paths differ.

- `R-24` **Declared inputs (RG-17/RG-19):** a lane's `required_env` names
  are verified by the GATE, never discovered by test failure. Before any
  execution (every lane kind): each name must be present and non-empty in
  the invoking environment — else refusal (exit 2) naming the lane and
  variable. For container lanes additionally: every required name MUST be
  on the environment's `forward_env` allowlist (else it can never reach the
  lane — refuse at load). Every container-lane start prints WHICH
  forwarding keys were present and which were declared-but-absent — NAMES
  ONLY, never values; the printed docker argv masks forwarded
  `-e KEY=...` payloads for the same reason. `--check-env` runs an ADVISORY
  drift sweep over the project's Python sources (`os.environ[...]`,
  `os.environ.get(...)`, `getenv(...)` literals) flagging names covered by
  neither `forward_env` nor `required_env`; it warns and exits 0 — the
  enforcement mechanisms are `required_env` + preflight.

- `R-25` **Override-reachability guard (RG-1):** a container command lane
  (ephemeral or exec) invoked with `--worktree` whose argv contains NO
  `{worktree}` token is refused (exit 2) before execution: sub-steps would
  re-derive their own tree and judge something else — the silent false-PASS
  class. Assay lanes relocate automatically (R-21) and host lanes relocate
  via cwd, so both are exempt. Conjunction lanes declare
  `--worktree {worktree}` in EVERY sub-invocation (CONSUMERS recipe; cmru's
  `[lanes.gate]` is the reference shape).

- `R-26` **Failing-container evidence (RG-12):** container logs are copied
  to the evidence directory (`$RUN_GATE_EVIDENCE_DIR`, default
  `/tmp/run-gate`) BEFORE the `rm -f`; a failed lane prints the preserved
  path — readable AFTER the container is gone. A failed `docker run`
  preserves partial logs the same way and its refusal shows up to the last
  10 stderr lines (pull/network failures are multi-line; the interesting
  line is rarely last). Evidence capture is best-effort and NEVER changes
  the lane's exit status; exec-mode containers are externally owned and are
  never removed nor captured here.

- `R-27` **Pointer↔lane linkage (RG-2):** `validate-pointers CONSUMER.toml
  [--root DIR]` certifies every run-gate invocation inside a consumer
  document (any TOML: a trove `[gates.*]`, release-step files) against the
  SSOT lane table it names. Per invocation: exactly one `{worktree}`-
  relative cd target whose project has a `run-gate.toml` (a pointer with no
  cd resolves to the document's own directory when that is a project);
  whenever the pointer substitutes `{worktree}` at all, every invocation
  must carry `--worktree {worktree}`; exactly one positional lane name that
  EXISTS in the effective lane set — loaded with the REAL parser, central
  inheritance included, never re-parsed here. Defects print one line each;
  exit **2** if any defect, else 0; documents that never invoke run-gate are
  trivially clean. `{worktree}` stands for the git toplevel of the pointer
  file unless `--root` says otherwise. Adoption includes one linkage test
  per project running this verb against its own consumer document — the
  dispatched artifact is certified by a test, not assumed (a renamed lane
  goes RED at test time, never at daemon dispatch time).

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
