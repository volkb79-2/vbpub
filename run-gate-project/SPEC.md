# run-gate SPEC — normative implementation contract

**Status:** NORMATIVE for the P01 build. Rev 2: adds exec-mode + extra-mounts. Rev 3: backlog-sweep
amendments (RG-15 `R-21` effective-tree execution; RG-11 reserved exit codes in `R-04`; RG-3
`R-23` dual-mount guard). Rev 4: continues the backlog sweep — RG-16 central lanes (`R-22`),
RG-17/RG-19 declared inputs (`R-24`), RG-1 override-reachability guard (`R-25`), RG-12
failing-container evidence (`R-26`), RG-10 declared `artifacts` + unconditional evidence-path
disclosure (`R-08`, `R-18`), RG-2 pointer↔lane linkage verb (`R-27`), RG-8 `--dry-run`
plan rehearsal (`R-28`), RG-20 resource-aware admission (`R-29`, lane `resources`
key in `R-08`), RG-9 doctor preflight verb (`R-30`), RG-14 wheel as second
artifact (`R-31`), RG-13 adoption hygiene + estate pairing sweep (`R-32`).
Rev 5: adversarial-review hardening (two fresh reviewers; every confirmed
defect its own commit) — one size grammar for all declaration sites,
sorted-order lock acquisition + admission-before-wait + O_NOFOLLOW/0600
locks, pointer collector certifies the console-script form while exempting
discovery/prose, exec-lane slice/argv naming-only disclosure, central-lanes
docs truth, evidence captured only on failure at 0600, doctor survives
broken hosts and exec envs need no slice, normalized verdict dedup,
whole-token pin-version match, reserved lane names + symmetric sidecar
checks.
Rev 6: RG-22 — `R-19a`'s safe.directory write is idempotent under
pre-existing entries (`--replace-all`).
Rev 7: release-adoption program — `R-31` amended (wheel version now DERIVED
from the git tag by setuptools-scm, not `__revision__`; the two-tier split is
now stated explicitly), new `R-33` (estate release orchestration + a
diff-coverage floor pending a total-100% campaign).
Rev 8: RG-27 lane invocation history — new `R-36` (the two-slot store, its
per-instance scoping, the history-eligibility conjunction and the `history`
query verb), with `R-01` (verb), `R-06` (`[history]` top-level table) and
`R-08` (reserved lane name) amended to match.
Distilled from `README.md` (design
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
  known lanes and the config path. `--check-env` also runs the assay-lane
  toolchain fitness check (`R-34`): its env-drift half stays advisory (exit
  0), a toolchain FAIL exits 2. `history [LANE] [--json]` (RG-27) is the
  lane-timing query verb (`R-36`); the usage text also documents the store
  location, the `[history] keep` bound and the history-eligibility rule.
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
  source, resolved slice + its source (on exec lanes this is naming-only
  disclosure — `docker exec` can neither place nor cap work), and (container
  AND exec lanes) the fully assembled docker argv with forwarded values
  redacted (RG-19). Mechanics are visible, never buried.

## 3. Config schema (both files; `schema_version` must equal 1)

- `R-06` Top-level keys: `schema_version` (required), `environments`,
  `lanes`, `history`. Unknown top-level key → error naming key + file. A
  central config's `[lanes.*]` are legal (R-22). `[history]` (RG-27) takes
  one optional key, `keep` (integer ≥ 1, default 10 — a bool is refused,
  not silently read as 1); a project `[history]` shadows the central one
  entirely, per R-09's rule.
- `R-07` `[environments.<name>]`: `image` (non-empty string, required),
  `cgroup_slice` (optional non-empty string), `forward_env` (optional,
  unique list of valid environment-variable names; values are forwarded to
  container lanes only when set). Redefining `host` → error.
- `R-08` `[lanes.<name>]` keys: `kind` (`"command"`|`"assay"`), `environment`
  (non-empty string), `argv` (command kind: non-empty string list),
  `assay_lane` + `assay_command` (assay kind: both required; `assay_command`
  is a non-empty string list — the tool NEVER invents an assay invocation),
  `pins` (assay kind: table of `{sha256 = "<path>", version = "<str>"}` —
  `sha256` required, path relative to the project and existence-checked at
  load (project lanes and inherited central lanes alike); a declared
  `version` is VERIFIED in-lane via `<assay_command> --version`, which must
  succeed and report it as a WHOLE punctuation-delimited token (declared
  `2.1` does NOT match reported `2.11.0`; one decorative leading `v` is
  tolerated; review fix) — declaring it asserts
  the command honors that convention,
  RG-4), `clean_tree` (bool, default **true**), `budget` (`\d+[smh]`,
  advisory only), `memory` (`\d+[bkmg]?`, docker `--memory`),
  `description` (optional non-empty string, one line, shown by `--help`),
  `required_env` (optional unique list of valid environment-variable names
  this lane's tests REQUIRE — enforced per R-24), `artifacts` (optional
  NON-EMPTY list of non-empty path strings the lane is expected to leave
  behind — disclosed after every run per R-18; `{worktree}` tokens are
  substituted, relative entries resolve against the effective project dir).
  Lane names `doctor`, `validate-pointers` and `history` are RESERVED (they
  collide with CLI verbs) and refused at load; a lane named like a verb
  could never be invoked anyway.
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
  field merging); both views show the effective set — the human usage marks
  inherited entries `*`, while `--list` stays a plain machine table.
  Per-consumer check at load: a central lane's pin sidecars must
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
  on the environment, or derived from a `ciu.global.toml` [deploy]
  table (`project_name + environment_tag`, falling back to
  `network_name stripped of "-network"`). **Which `ciu.global.toml` (RG-24):
  the JUDGED WORKTREE's own is preferred; the repo's is the fallback.** This
  one resolution path is deliberately worktree-scoped, unlike every other
  `repo`-relative resolution in the tool: `repo` (R-13) is the checkout
  owning the shared `.git` — the MAIN checkout for any linked worktree —
  which is the right authority for object-store questions and the WRONG one
  for a LIVE DEPLOYED container. A multi-instance worktree (dstdns "Mode-B":
  `ciu worktree adopt` gives a worktree its own stack, its own rendered
  `ciu.global.toml`, its own network and its own runner) would otherwise have
  its lane exec'd into the main landscape's runner — a partial, believable
  failure, because the inner `cd <effective project dir>` still reaches the
  right FILES and only the container's baked network/env are wrong. The
  precedence is ADDITIVE: a worktree that is not itself an adopted instance
  (no own `ciu.global.toml`) keeps repo-relative resolution unchanged.
  The resolution SOURCE printed with the container name (R-05) names the
  scope — `judged worktree:` or `repo:` — followed by the file used.
  Missing config → hard error naming BOTH candidate paths when they differ.
  If the resolved container is not running → hard error whose
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
  effective project dir — or, for a `kind = "assay"` lane on the built-in
  `host` environment, the same assay inner the container runners build
  (RG-28: the validator accepts that combination, and the runner used to
  raise `KeyError('argv')` on it — a traceback for a legal config, which
  `R-04` calls a defect) (R-21). A command host lane uses no docker and no
  safe.directory — that trap is container-specific; an assay host lane
  inherits the shared inner, whose `GIT_CONFIG_GLOBAL` isolation (`R-19a`)
  keeps that write out of the operator's own git config. Exit passthrough
  identical either way.

- `R-19a` **safe.directory scope:** both ephemeral and exec inner commands set
  `GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig` before running `git config
  --global --replace-all safe.directory '*'`. This avoids writing to
  `~/.gitconfig`, which may be read-only or shared across containers.
  `--replace-all` (RG-22) makes the write idempotent regardless of prior
  state: a plain single-value `git config --global safe.directory '*'` fails
  with "cannot overwrite multiple values" the moment the isolated gitconfig
  already carries more than one `safe.directory` entry — reachable in
  practice wherever exec-mode reuses that file across invocations sharing a
  host or another process writes to it under the same `GIT_CONFIG_GLOBAL`.
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
  lane — refused on the run path before anything executes, NOT at load:
  listing/inspection verbs stay usable). Every container-lane start prints WHICH
  forwarding keys were present and which were declared-but-absent — NAMES
  ONLY, never values; the printed docker argv masks forwarded
  `-e KEY=...` payloads for the same reason. `--check-env` runs an ADVISORY
  drift sweep over the project's Python sources flagging names covered by
  neither `forward_env` nor `required_env`; it warns and exits 0 — the
  enforcement mechanisms are `required_env` + preflight.

- `R-24a` **Forwarding is DECLARED, never implicit (RG-23).** The ONLY
  variable an exec- or container-mode lane forwards without a declaration is
  `CGROUP_PARENT_DEV_BACKGROUND` (infrastructure the tool itself owns).
  Everything else must be named in the environment's `forward_env`.
  **Breaking change, and the migration it requires:** revisions before this
  one hardcoded `MOCK_MODE` and `RUN_LIVE_TESTS` into the exec-mode
  forwarding loop. That allowlist was replaced by `forward_env` with no
  migration pass, so any consumer that relied on either name silently stopped
  receiving it — and the failure is a false GREEN, not an error: a suite that
  skips its live tests on the flag's absence exits 0 having executed none of
  them. Every exec-mode consumer relying on those two names MUST add them to
  its environment's `forward_env`, and SHOULD add them to the lane's
  `required_env` so absence refuses loudly (R-24) instead of skipping
  quietly. No implicit name is coming back: a value that has an
  authoritative source (the consumer's own config) must not be shadowed by a
  literal in the tool.

- `R-24b` **What the drift sweep can and cannot see (RG-23).** The sweep is
  AST-based: `os.environ["X"]`, `os.environ.get/setdefault/pop("X", …)`,
  `getenv("X")`, `"X" in os.environ`, and — the shape that motivated this —
  a literal passed to the project's OWN env-reader helper (a function that
  reads the environment through one of its parameters, e.g.
  `_env_flag_enabled("RUN_LIVE_TESTS")` whose body does
  `os.getenv(name, "")`). Bound-method parameter offsets are accounted for.
  It CANNOT see a name assembled at runtime (`os.getenv(prefix + suffix)`),
  a name read in a non-Python source, or one reached through an
  indirection the pass does not model — so a clean sweep is evidence, never
  a certificate, and `--check-env` stays advisory. A source file that does
  not parse is reported as such and falls back to the old line regex: "could
  not read it" is never rendered as "there is nothing there".

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
  line is rarely last). Evidence is captured ONLY for a failing run — a
  green lane leaves nothing behind — and is written mode 0600 (container
  logs may echo credential material the suite exercised; review fix).
  Evidence capture is best-effort and NEVER changes
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

- `R-28` **Dry run (RG-8):** `--dry-run` rehearses the gate WITHOUT running
  the JUDGED lane: every preflight runs exactly as live (config validation,
  required-env, worktree resolution + charset guard, override-reachability,
  comparison-base resolution per `R-35`, clean-tree — so `--allow-dirty`
  composes), then the fully assembled plan is printed and exit is 0.
  Container lanes print the identical docker argv
  a live run would (same assembly code path; only the container NAME differs
  by pid/epoch) and start nothing; exec lanes rehearse name resolution AND
  the runner-running preflight (a stopped runner reports its real refusal),
  print the identical redacted docker-exec argv a live run would, but exec
  nothing; host lanes print the argv and cwd and run nothing. No
  evidence-path disclosure on a dry run — no JUDGED lane ran, no artifact
  landed.
  **What a dry run DOES execute (amended for `R-35`):** an assay lane's
  read-only `assay lanes --json` inventory probe is itself a preflight — it
  is what resolves the comparison base that the printed plan must show — so
  it runs, in a short container of its own, exactly as it does live. "Nothing
  runs" was true of `R-28` before `R-35` and is not true now; the promise
  `--dry-run` makes is that **no judged lane starts**, which is the promise
  a caller actually relies on. `doctor`'s probes (`R-34`) are the same class
  of read-only preflight.

- `R-29` **Resource-aware admission (RG-20):** gates are admitted by RAM
  headroom and shared-infra collision, not serialized globally. Lane key
  `[lanes.<name>.resources]`: `memory` (size; supersedes top-level
  `memory` — declaring both is refused), `memory_swap` (size → docker
  `--memory-swap`; tight RAM + ample swap per cmru's proven pattern),
  `cpu_weight`/`io_weight` (integers 1..10000 — VALIDATED and PRINTED as
  advisory; `docker run` has no portable cgroup-v2 flag for them, and
  pretending otherwise would be enforcement theater), `shared` (list of
  service names). **Memory half:** ephemeral container lanes account
  against their slice's cgroupfs truth at admission time —
  `memory.current + declared <= memory.max` read from
  `$RUN_GATE_CGROUPFS_ROOT` (default `/sys/fs/cgroup`; systemd dash-nesting:
  `dev-background.slice` → `dev.slice/dev-background.slice`). Over budget →
  refusal naming current usage, budget, declared need, and the overage. No
  derivable ceiling (`max`, hidden cgroupfs) → loud WARNING, admission by
  shared-infra rules only. This counts EVERYTHING in the slice (kernel
  truth), so no cross-process bookkeeping can drift. **Shared-infra half:**
  lanes declaring the same `shared` name serialize on a per-name flock
  (`/tmp/run-gate-shared-<name>.lock`) — the second gate WAITS with a
  notice, then proceeds; fully isolated instances never meet and run
  concurrently. Locks are acquired AFTER all fast-fail preflights (a
  blocking wait never precedes refusals — slice-memory admission runs
  before any wait) and released in `finally`; acquisition is in
  sorted-name order, a canonical global order that makes hold-and-wait
  cycles impossible regardless of how each project lists its services.
  `--dry-run` plans the serialization but never blocks. Host/exec lanes get
  shared-infra rules only — their RAM does not land in this tool's slice.
  On an exec lane a `cgroup_slice`/`resources.memory` declaration draws a
  loud naming-only WARNING (docker exec has neither placement nor caps;
  pretending otherwise would be enforcement theater), never a refusal.

- `R-30` **Doctor (RG-9):** `doctor` recomposes the implemented preflights
  into one first-contact command — docker present; per-environment slice
  resolution + LoadState where systemd reachable (exec environments are
  checked naming-only — they need no slice; a host with the systemd run-dir
  but no runnable `systemctl` degrades to a loud skip, never a traceback);
  physical-path
  derivability from mountinfo (bare-host view = warning naming
  `$RUN_GATE_MOUNT_ALIAS`); git worktree resolution and `/tmp`
  writability for `GIT_CONFIG_GLOBAL`; referenced images present locally
  (advisory — a missing image may legitimately pull). One `[OK]`/`[WARN]`/
  `[FAIL]`/`[SKIP]` line per check plus a summary counting all four; exit 2
  iff any FAIL. Doctor judges nothing and writes nothing, but since `R-34`
  it **does start containers**: short-lived read-only probes, bounded at ONE
  inventory probe per (environment, `assay_command`) plus ONE batched
  `command -v` probe per environment — never one per lane, and none at all
  for a project with no `kind = "assay"` lane. That count is a claim, so a
  test owns it (`test_probe_cost_is_one_inventory_plus_one_tool_probe_per_
  environment`): a cost stated in the spec and not measured is a cost that
  drifts. The probes are also why the summary now reports SKIPs — "could not
  determine" must be visible, not absorbed into silence. Doctor must itself
  survive a broken host: a
  preflight that tracebacks on exactly the machine that needs it defeats
  its purpose (git/docker absent → FAIL lines, never a traceback).

- `R-34` **Assay-lane toolchain fitness (RG-25).** For every `kind = "assay"`
  lane whose environment resolves, `doctor` and `--check-env` ask the JUDGE
  what the lane needs — `<assay_command> lanes --json --file assay.toml`
  (assay ≥ 3.2.0, B044) executed INSIDE that environment — and then check the
  environment for it. run-gate never parses `assay.toml`; the `assay_lane`
  name stays a string it passes through, exactly as before.
  - **One probe path.** `build_env_probe_argv()` is the ONLY place that
    knows how to reach an environment for a short read-only command; it
    reuses `resolve_container_name()` (exec) and
    `physical_path()`/`dual_mount_flags()` (ephemeral). A probe is attached
    and captured rather than detached like a lane run (`R-17`), which is safe
    here and only here because a probe's result is a preflight line, never a
    verdict. Ephemeral probes carry `--cgroup-parent` like any container this
    tool starts; where no slice is derivable the probe SKIPs rather than
    running unconfined.
  - **Probe count.** The inventory is asked once per (environment,
    `assay_command`) — two lanes sharing an environment AND a pinned judge
    ask once; different judges ask separately, since caching across them
    would answer with the wrong one. The `command -v` check is asked once per
    ENVIRONMENT, over the UNION of every lane's tools: what is on a `PATH` is
    a property of the environment, not of the lane asking. Each lane is still
    judged against ITS OWN tool list, so batching never smears one lane's
    missing tool onto another.
  - **Tools checked** = `external_tools` ∪ `argv0` (READ from the inventory)
    ∪ the `language` toolchain. The language table exists only because
    assay's own docs state that fact in prose and not in the inventory
    (`external_tools` is `()` for every shipped adapter in 3.2.0), and an
    unmapped language attaches a CAVEAT to the line rather than being
    silently treated as "nothing needed".
  - **Statuses.** `[FAIL]` ONLY for a fact the inventory established: a named
    tool absent from the environment (naming lane, tool AND environment), or
    an `assay_lane` the judge does not declare (naming what it does declare).
    `[OK]` lists the tools verified. Everything meaning "I could not
    determine this" is `[SKIP]` with the reason — judge unreachable, an assay
    with no `--json`, non-JSON output, `inventory_schema != 1` (with the
    value), a `host` environment, docker absent, an unresolvable slice.
    **An older judge can never turn a healthy project red:** the pin declares
    the version the lane needs, and run-gate does not impose a floor it never
    declared.
  - **Exit codes.** `doctor` counts SKIPs in its summary and still exits 2
    only on FAIL. `--check-env`'s env-drift half stays advisory (exit 0 — it
    is a heuristic); a toolchain FAIL exits **2**, because the judge itself
    established it.

- `R-35` **Comparison-base passthrough, `--base REF` (RG-26).** assay 3.0.0
  shipped `judge.base_source = "request"` (B019): a changed-line lane that
  omits `judge.base` and takes its comparison base from the gate, refusing by
  design when invoked without `--request-base`. run-gate had no way to supply
  one, so the judge feature was unusable from every consumer.
  - **Derived, never restated.** Whether a lane delegates is READ from
    `assay lanes --json` through `R-34`'s probe. There is **no new
    `run-gate.toml` key**: `assay.toml` owns the fact, and a second spelling
    of an owned fact is the drift machine this tool exists to remove. The
    probe therefore runs for every `kind = "assay"` lane invocation, not only
    when `--base` is given — it is short, read-only (`assay lanes` executes
    nothing) and shares `R-34`'s single builder.
  - **Resolution.** For a delegating lane the ref is `--base` when given,
    else the judged worktree's `git merge-base HEAD @{upstream}`. No
    upstream → exit 2, `lane 'x' delegates its comparison base; pass --base
    REF (worktree has no upstream)`. There is no fallback to `HEAD` or to a
    default branch name: a changed-line judgment whose base was guessed is
    not a changed-line judgment.
  - **Refusals (all exit 2, all naming the lane).** A lane that does NOT
    delegate, invoked with `--base`: an assay lane whose inventory reports a
    different `base_source` (naming the value assay declared), or a command
    lane whose argv carries no `{base}` token (the ref could only be silently
    dropped — `R-25`'s hazard class). A judge whose inventory cannot be read
    AND `--base` given: exit 2 naming assay **3.2.0** (B044) as the version
    that first carries the inventory. The same judge WITHOUT `--base`:
    behaviour unchanged, nothing appended.
  - **Conjunction propagation** follows `R-25`'s rule — a conjunction lane
    declares it the way it declares `--worktree`, with a `{base}` token in
    its own argv, substituted into every sub-invocation. A `{base}`-carrying
    lane resolves its ref by the same policy above, so it refuses rather than
    substituting an empty string.
  - **Disclosure (`R-05`).** Before execution, live AND dry:
    `run-gate: comparison base <REF> (from --base | merge-base HEAD
    @{upstream}) → --request-base` (or `→ {base} in the lane argv`); the
    printed docker argv carries the appended flag.

- `R-30a` **Linked-worktree host-lane warning (RG-21).** When the project
  declares at least one `environment = "host"` lane AND the judged tree is a
  LINKED worktree whose gitdir lies outside it (`.git` is a FILE naming an
  absolute path under the main checkout), `doctor` emits ONE `[WARN]` naming
  the worktree, the gitdir, the exact symptom (`not a git repository:
  <gitdir>`) and three remedies (mount the common gitdir into the harness's
  container, pass it as `GIT_DIR`, or run the lane from the main checkout).
  It is a warning, never a refusal, and it does not change doctor's exit
  code: run-gate itself is not defective here — `{worktree}` forwarding and
  exit-status passthrough are correct. The defect is one layer DOWN, in a
  harness that bind-mounts only its own `$repo_root` (= the judged tree) by
  host path, where the gitdir then falls outside the mount and every
  in-container git plumbing call fails. run-gate's OWN container lanes are
  unaffected, because `R-23` dual-mounts the REPO root; the check is
  therefore scoped to host lanes, the only kind that can reach such a
  harness — a warning that fires where it cannot bite gets switched off, and
  then it protects nothing. With a host lane declared and a plain checkout,
  the same check records `[OK]`, so a reader can tell it ran.

- `R-31` **Wheel as second artifact (RG-14):** the script stays PRIMARY —
  fresh clone, zero installs, `./run-gate.py --list` unchanged is the design
  win and must never regress. A wheel wraps the SAME bytes for
  pip-installable distribution: `pyproject.toml` maps module `run_gate`
  (committed symlink `run_gate.py -> run-gate.py`, dereferenced at build
  time — py_modules cannot carry a hyphen) and exposes console script
  `run-gate = run_gate:main`; the shipped `run_gate.py` is byte-identical to
  the canonical file. Version identity is TWO-TIER (superseding this rule's
  original `__revision__`-attr coupling, release-adoption program): the
  wheel's version is DERIVED from the git tag (`run-gate-vX.Y.Z`) by
  setuptools-scm — `[tool.setuptools_scm]` with `root = ".."` and
  `--match run-gate-v*`, matching ciu/cmru/assay/topos/nyxloom exactly —
  while `__revision__` inside the script stays the SEPARATE copy-drift
  marker external repos compare; the two never need to agree, and
  CONSUMERS.md states which one governs which decision. A release build
  never runs `git describe` live: cmru's wheel-build step sets
  `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE` from the tag it just
  minted (`cmru.toml`'s `scm_dist = "run_gate"`). Build toolchain pinned
  exactly (assay/ciu precedent); a release tag names the semver version
  (`run-gate-v<version>`) — because the pre-existing tag `run-gate-v22` is
  itself matched by the tag pattern and parses as version `22`, the first
  semver release MUST be numbered `>= run-gate-v23` (e.g. `run-gate-v23.0.0`)
  or version ordering inverts (pip would read a `run-gate-v1.0.0` release as
  older than the untagged `22`-based dev builds that precede it). The wheel
  NEVER becomes required — CONSUMERS.md states it in prose and this contract
  forbids any lane or check that assumes an install.

- `R-32` **Adoption hygiene + estate pairing sweep (RG-13):** the adoption
  contract in CONSUMERS.md is complete enough to execute without tribal
  knowledge: a worked run-gate × assay example stitches both halves; the
  gitignore obligation for lane-written evidence (`.assay/`, declared
  `artifacts`) is stated as an adoption step, because copied-script repos do
  not inherit the monorepo root's ignores and a dirty tree fails the NEXT
  lane's clean-tree check; every adopting project names `./run-gate.py` as
  its canonical test entrypoint in its own README; and the repo-root README
  carries the discovery line (`cd <project> && ./run-gate.py --list`).
  Consumer `timeout_seconds` paired with a lane's advisory `budget` must
  never be TIGHTER than it: `TestEstateBudgetTimeoutPairing` loads each
  nyxloom-trove project's lanes with the REAL parser and enforces
  timeout >= budget wherever a gate argv names the lane as a whole token —
  the drift is caught by test, not by memory.

- `R-33` **Estate release orchestration + coverage floor (release-adoption
  program):** `run-gate-project` is registered in the vbpub-root
  `cmru.orchestration.toml` (`depends_on = []`: it consumes no first-party
  wheel and nothing releases after it that needs its artifact today) and
  ships its own `cmru.toml` (schema identical to ciu/cmru/assay: `[github]`,
  `[targets]`, `[project]` with `id = "run-gate-project"`, which cmru's
  config loader requires to equal the KEY this project is registered under
  in `cmru.orchestration.toml` — that key, not `id` itself, is what drives
  cmru's change-detection watch path; it equals the directory name here
  only by convention). Because the pre-existing tag `run-gate-v22` is a
  bare integer, not semver, cmru's auto-bump cannot parse it and `cmru
  status`/`cmru release` CRASH for this project's first release without an
  explicit `--set-version 23.0.0` override (verified live) — every release
  after that resolves normally. The release gate IS the project's own
  dogfooded lane (`cmru.toml [steps.run-tests]` runs `./run-gate.py
  selftest` — SSOT, D-110/D-111: one parser, no duplicated pytest
  invocation), run in HOST mode deliberately (not tester-unified: this
  suite is self-referential — it exercises `physical_path()`'s real
  `/proc/self/mountinfo` lookup against its own pytest fixtures, which
  breaks under an extra container layer) which chains `pytest` with
  `tools/coverage_gate.py` (vendored from `topos/tools/`, the estate's
  thinnest copy — MIGRATION PENDING per its header, do not fork further;
  its own `--source` default is scoped to `run-gate.py` alone, not the
  whole project, so a bare invocation can't silently reproduce the
  scoping bug this floor was built to prevent). Because TOTAL line+branch
  coverage measured ~47% at adoption time, the floor enforced is DIFF
  coverage at 100% (topos/nyxloom legacy-code pattern: every changed
  executable line must be covered, same-commit) rather than
  `--cov-fail-under=100`; a later campaign to reach a total 100% floor is
  its own backlog item, and only then does the
  lane flip to a total floor like cmru's own.

- `R-36` **Lane invocation history (RG-27).** run-gate is the layer that
  actually starts each lane, so it is the only one holding start/stop and
  exit status first-hand. It RECORDS them and stops there — no rigor/defer
  POLICY lives in this tool; a controller reads the data and decides.

  - **`R-36a` Two slots per lane, two different contracts.** `latest` holds
    the MOST RECENT invocation whatever happened to it — pass, fail, tool
    error, Ctrl-C, dirty tree, mid-rebase — for immediate diagnostics.
    `history` is a curated trend series keyed by **(lane, commit)** and
    bounded to the last `keep` commits (`[history] keep`, default 10, R-06).
    Letting the second inherit the first's permissiveness is the defect this
    requirement exists to prevent: a dirty-tree duration attributed to a
    commit that never ran it, silently overwriting the real measurement.
  - **`R-36b` History eligibility is a CONJUNCTION**, evaluated once, with
    the failing reason recorded on the entry (`excluded_reason`, visible in
    both output forms). A run joins history only when ALL hold: (1) the lane
    completed and reported its own status; (2) the judged tree was clean at
    the moment the run STARTED; (3) no git operation was in flight
    (`rebase-merge`, `rebase-apply`, `MERGE_HEAD`, `CHERRY_PICK_HEAD`,
    `REVERT_HEAD`, `BISECT_LOG`); (4) HEAD resolved to a full commit sha.
    Each of (2)-(4) is INDETERMINABLE-EXCLUDES: "could not determine" never
    collapses into "clean" — a wrong trend entry is invisible, a missing one
    shows up in `count`. Cleanliness is sampled independently of the lane's
    `clean_tree` POLICY: the discriminator is whether the tree WAS dirty,
    never whether dirt was permitted, so a `clean_tree = false` lane run on
    a clean tree is a perfectly good measurement.
  - **`R-36c` Completed fails DO join history** (the design call RG-27
    flagged, resolved yes) — their duration is real measured cost — but they
    are stored WITH their outcome and reported as a SPLIT statistic
    (`passes` vs `completed`), because a failing lane can short-circuit
    (this project's own `pytest && coverage_gate` never reaches the gate
    when pytest is red) and merging the two understates the lane's cost in
    exactly the direction that makes a "cheap, always run it" call wrong.
    run-gate hands over both series; picking one is the consumer's policy.
  - **`R-36d` The reported statistic is the MEDIAN** (with min/max/count),
    never the mean: the named trap is one slow outlier reading as the lane's
    permanent cost, and the mean is precisely the statistic that lets it.
    `max` still reports the outlier — it is information, just not the
    typical cost.
  - **`R-36e` Recording window.** An invocation begins at the clean-tree
    refusal and ends at the lane's own exit status; everything before it is
    a configuration error naming no invocation, and is not recorded at all.
    `--dry-run` records NOTHING (no lane started, so nothing was measured).
    Aborts and infrastructure failures inside the window still update
    `latest` and re-raise unchanged.
  - **`R-36f` Storage — per (judged worktree × project).** The store is
    `<effective project dir>/.run-gate/history.json`; because R-21 already
    relocates the effective project dir into the judged tree, `--worktree B`
    writes B's measurement into B's store, never the invoking checkout's.
    This is the PRIMARY concurrent-write answer and it is a scoping answer,
    not an arbitration one: two worktrees' gates address two different files
    and never meet. The residual case — two lanes of ONE project in ONE tree
    — is arbitrated by an exclusive `flock` on a **sibling** lock file
    (`.run-gate/history.lock`, `O_NOFOLLOW`, 0600) held across the whole
    read-modify-write, plus write-temp-then-`os.replace`. The lock is a
    sibling BECAUSE the store is replaced by rename: a lock taken on the
    store itself would guard an inode nobody writes next. Atomic rename is
    also what lets the query verb read with no lock at all. Unlike R-29's
    shared-infra lock (which blocks forever on purpose — it protects the
    correctness of the run), this one is BOUNDED: a gate that hangs waiting
    to write telemetry has inverted the priority.
  - **`R-36g` The store must be git-ignored, and that is CHECKED, not
    documented.** Before writing, run-gate asks git whether every path the
    recorder can leave behind (store, lock, temp) is ignored; if any is not,
    it refuses to write and prints one warning naming the remedy, rather
    than leaving the tree dirty for the NEXT lane's clean-tree check. Two
    details are load-bearing and were verified against real git: the query
    names the FILES, never the bare directory (`git check-ignore .run-gate`
    answers "not ignored" while the directory does not exist yet, even under
    a `.run-gate/` pattern — which would silence recording on every
    project's first run), and the verdict is read from the REPORTED PATHS,
    never the exit status (`git check-ignore a b` exits 0 when ANY argument
    matches, so the exit status would certify a store whose lock file still
    dirties the tree). Index-aware on purpose: a TRACKED store dirties the
    tree whatever `.gitignore` says.
  - **`R-36h` Recording is best-effort and never changes a verdict.** Every
    failure — unignored store, held lock past the bound, corrupt or
    unreadable store, write error — degrades to ONE warning line on stderr
    (never a traceback, R-04) and leaves the lane's exit status untouched.
    A corrupt store is replaced, not fatal.
  - **`R-36i` The query verb.** `history [LANE] [--json]`. No LANE reports
    every declared lane; an unknown LANE refuses (exit 2) naming the known
    lanes and the config path, exactly like an unknown lane on the run path.
    Default output is a human table (store path, `keep` + its source, and
    per lane: `latest` with its exclusion reason when it has one, the
    bounded series oldest-first, and the split stats); `--json` emits the
    same data machine-readably (`schema`, `revision`, `store`, `keep`,
    `keep_source`, per-lane `latest`/`history`/`stats`). The verb runs no
    lane, starts no container, and exits 0 whenever the QUERY succeeded —
    an empty store is an answer, not a failure. `history` joins `doctor` and
    `validate-pointers` as a reserved lane name (R-08).

## 6. Non-goals (unchanged from CONSUMERS)

No second parser of `run-gate.toml`; no judgment policy here (assay owns
floors/R-levels/verdict meaning); no non-stdlib imports; no silent defaults
for environment facts; no test definitions in consumer configs — the SSOT
is `run-gate.toml`; release policy stays with the consumer (cmru/nyxloom).

## 7. Distribution — script first, wheel second (`R-31`)

PRIMARY, unchanged: vbpub-internal relative symlink
`../run-gate-project/run-gate.py` committed at the project root; external
repos copy the file; `__revision__` is the drift marker; stdlib only, runs
on a fresh clone with zero installs.

SECONDARY: the wheel (`pyproject.toml` here) packages the same bytes as
module `run_gate` with a `run-gate` console script, published through
cmru's wheel-publish and tagged `run-gate-v<version>`. Adoption never
requires it; nothing in the gate's own lanes or checks may assume an
install exists. Version identity is TWO-TIER (superseding RG-14's
original wording here): the wheel's semver version is DERIVED from the
git tag by setuptools-scm, NOT from `__revision__` — the two numbers are
independent and can legitimately disagree at any moment (`R-31`).

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
