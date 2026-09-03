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
Rev 9: RG-30 — `doctor` and `--check-env` honor `--worktree` (`R-30`/`R-34`
amended): both passed `None` to `resolve_repo_and_worktree` instead of the
caller's override, so `doctor --worktree B` silently reported the INVOKING
tree's answers under B's name — the same read-scope hazard `R-36i` (RG-27
B1) closed for `history`, closed here for the last remaining instance.
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
  **those TWO keys and no others: an unrecognized key under
  `[lanes.<name>.pins.<pin>]` is refused at load exactly as an unrecognized
  lane key is (RG-32, rev 34), and `budget` is refused with its own message
  naming the value's real owner** —
  `sha256` required, path relative to the project and existence-checked at
  load (project lanes and inherited central lanes alike); a declared
  `version` is VERIFIED in-lane via `<assay_command> --version`, which must
  succeed and report it as a WHOLE punctuation-delimited token (declared
  `2.1` does NOT match reported `2.11.0`; one decorative leading `v` is
  tolerated; review fix) — declaring it asserts
  the command honors that convention,
  RG-4), `clean_tree` (bool, default **true**), `budget` (`\d+[smh]`,
  advisory only), `stall_timeout` (`\d+[smh]`, assay lanes ONLY — bounds
  SILENCE in the lane's progress file, never total elapsed time; `R-40c`),
  `memory` (`\d+[bkmg]?`, docker `--memory`),
  `description` (optional non-empty string, one line, shown by `--help`),
  `required_env` (optional unique list of valid environment-variable names
  this lane's tests REQUIRE — enforced per R-24), `artifacts` (optional
  NON-EMPTY list of non-empty path strings the lane is expected to leave
  behind — disclosed after every run per R-18; `{worktree}` tokens are
  substituted, relative entries resolve against the effective project dir).
  Lane names `doctor`, `validate-pointers` and `history` are RESERVED (they
  collide with CLI verbs) and refused at load; a lane named like a verb
  could never be invoked anyway. `history` joined the set in rev 30 -- a
  LOAD-TIME breaking change for any consumer that had declared
  `[lanes.history]` (no estate project had; flagged in CHANGES and CONSUMERS
  for copied-script repos).
  advisory only), `memory` (`\d+[bkmg]?`, docker `--memory`),
  `description` (optional non-empty string, one line, shown by `--help`),
  `required_env` (optional unique list of valid environment-variable names
  this lane's tests REQUIRE — enforced per R-24), `artifacts` (optional
  NON-EMPTY list of non-empty path strings the lane is expected to leave
  behind — disclosed after every run per R-18; `{worktree}` tokens are
  substituted, relative entries resolve against the effective project dir).
- `R-08a` **Pin tables carry two keys, and `budget` is not one of them
  (RG-32).** `[lanes.<name>.pins.<pin>]` accepts `sha256` and `version`
  ONLY. A `budget` key there is refused at load, by name, with the message
  `pin '<pin>' declares 'budget' — run-gate never enforced it; the lane's
  budget lives in the consumer's assay.toml [lanes.<assay_lane>] (delete
  this key; the lane-level run-gate 'budget' stays advisory)`. This is an
  `R-04`-class defect, not a nit: the key sat one nesting level below a
  REAL, load-bearing `budget` that looks identical when read, and it did
  nothing. Measured cost — three readers on one dstdns session (two
  independent Opus review agents and the controller) each read
  `pins.assay.budget = "90m"` as the governing bound of a mutation lane
  whose `assay.toml` actually declared `120m`; the two numbers had drifted
  with nothing able to notice. Renaming it to `budget_hint` plus a
  drift check was REJECTED: the check would be a second reading of an
  assay-owned fact, which `R-35` already forbids for the comparison base,
  and a decorative key is still a key a reviewer must learn to ignore. The
  generic unknown-key refusal is the durable half — a pin table that
  accepted anything is how `budget` came to live there. **BREAKING** for any
  consumer that declares the key today; the migration is recorded in
  CHANGES, and it is not one deletion (see below).
  - **A misplaced LANE key is named as one, and the remedy is MOVE, not
    delete.** When an unrecognized pin key is itself a legal lane key, the
    refusal says so: `'clean_tree' is a lane key; it belongs one level up in
    [lanes.<n>], where it is load-bearing — move it, do not delete it (under
    a pin table it has never done anything, so the lane has been running
    with the default instead)`. Without this clause the refusal is the
    generic `unknown key(s) clean_tree (allowed: sha256, version)`, whose
    obvious remedy is deletion — and deleting a misplaced
    `clean_tree = false` silently flips that lane to the default `true`,
    i.e. `R-08a` would rename its own defect class rather than close it.
    `budget` is deliberately excluded from this clause and keeps its own
    message: it is the one misplaced key whose remedy really is deletion,
    because the value that governs the lane lives in the consumer's
    `assay.toml`, not one level up. Both checks run BEFORE the generic one.
  - **Measured consumer impact (2026-09-02, parsed with this loader over
    every lane of `/workspaces/dstdns/run-gate.toml`, not text-grepped):**
    **13 of 29** dstdns lanes refuse at load on `pins.assay.budget`, and
    **four** of those (`assay-dlq`, `assay`, `sql-mutation`,
    `assay-p129-enumeration-cursor`) also carry `clean_tree = false` in the
    same misplaced position. The migration is therefore TWO rounds: the
    `budget` refusal fires first and masks the `clean_tree` one. The four
    inert `clean_tree = false` lines are a live consumer defect this check
    just discovered — those lanes have been running `clean_tree = true` all
    along.

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
  --file assay.toml --verdict-json .assay/verdict-<assay_lane>.json --resume
  --progress .assay/progress-<assay_lane>.jsonl` (the two trailing flags
  unconditionally, `R-38`), then `--request-base REF` only for a delegating
  lane (`R-35`).
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
  Read-scope under `--worktree` (RG-30) is `R-37`.

- `R-34` **Assay-lane toolchain fitness (RG-25).** For every `kind = "assay"`
  lane whose environment resolves, `doctor` and `--check-env` ask the JUDGE
  what the lane needs — `<assay_command> lanes --json --file assay.toml`
  (assay ≥ 3.2.0, B044) executed INSIDE that environment — and then check the
  environment for it. run-gate never parses `assay.toml`; the `assay_lane`
  name stays a string it passes through, exactly as before.
  The probe's own read-scope under `--worktree` (RG-30) is `R-37`.
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

- `R-30b` **Unprefixed script path in a container command lane (RG-34).**
  `doctor` emits ONE `[WARN]` per `kind = "command"` lane on a NON-host
  environment whose `argv[0]` is a relative path containing `/` and not
  starting with `{worktree}` — naming the lane, the element, the fix
  (`"{worktree}/<path>"`) and the mechanism: a container that mounts only
  the judged worktree (a Mode-B instance's own runner, not the shared one)
  has nothing at the bare repo root the `--workdir` names, so the argv dies
  with `No such file or directory` there while working under a full-repo
  mount. Measured on dstdns P152: `[lanes.schema] argv =
  ["scripts/schema-gate.sh", "{worktree}"]` — the ARGUMENT templated, the
  script path not — `lane 'schema' exit 127` against a dedicated-container
  worktree, 100% reproducible, while the sibling `p128-schema-lineage` lane
  (`["bash", "{worktree}/scripts/p128-assay-schema.sh"]`) was correct.
  A **warning, never a refusal**, and it does not change doctor's exit code:
  the same argv is CORRECT under a full-repo mount, and which mount a lane
  gets is not visible to run-gate statically — a refusal would break working
  consumers to prevent a hazard that may not apply to them. run-gate does
  not rewrite argv either: the fix is one edit in the consumer's own config,
  and a tool that silently rewrote a declared command would be a worse
  defect than the one it patched. The check reads the DECLARATION only, so
  it still answers for a lane whose environment failed to resolve. With at
  least one container command lane and nothing to flag it records one
  `[OK]`, so a reader can tell it ran (`R-30a`'s precedent).

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
    completed and reported its own status AND a duration was actually
    measured -- an entry without one is not a measurement; (2) the judged
    tree was clean at
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
    A corrupt store is replaced, not fatal. Flushing a record is **at most
    once**, and the claim is staked before the work: the normal-path flush
    runs inside the tool's own exception scope and is not instantaneous (it
    spawns `git check-ignore` and may wait up to the lock bound), so an
    interrupt landing there reaches the abort handler, which flushes again --
    that second flush must be a clean no-op, or the interrupt is replaced by
    a traceback from the telemetry, inverting exactly the priority this
    requirement sets.
  - **`R-36i` The query verb.** `history [LANE] [--worktree PATH] [--json]`.
    No LANE reports every declared lane; an unknown LANE refuses (exit 2)
    naming the known lanes and the config path, exactly like an unknown lane
    on the run path.
    **Read scope follows `--worktree` exactly as the write scope does**
    (`R-36f`): the store is per (judged worktree x project), so a query given
    `--worktree B` reads B's store and never the invoking checkout's --
    answering with A's medians under B's name would be the silent
    substitution this whole requirement exists to prevent, and it is the
    hazard class `R-25`/`R-35` already legislate against for `--worktree` and
    `--base`. The resolved tree is DISCLOSED in both output forms (a `tree:`
    line; a `worktree_scope` key, `null` when unflagged). Resolution is
    opt-in: an unflagged query stays git-free and keeps answering where git
    cannot. An override that is not a directory refuses (exit 2) and one that
    is not a git work tree refuses (exit 3, carrying git's own line) -- a read
    has no downstream to fail in, so falling back to the invoking checkout
    here would reintroduce the same substitution through the error path.
    `--json` is honored by this verb ALONE; every other invocation refuses it
    by name (exit 2) rather than silently printing its human form.
    Default output is a human table (store path, `keep` + its source, and
    per lane: `latest` with its exclusion reason when it has one, the
    bounded series oldest-first, and the split stats); `--json` emits the
    same data machine-readably (`schema`, `revision`, `store`, `keep`,
    `keep_source`, per-lane `latest`/`history`/`stats`). The verb runs no
    lane, starts no container, and exits 0 whenever the QUERY succeeded —
    an empty store is an answer, not a failure. `history` joins `doctor` and
    `validate-pointers` as a reserved lane name (R-08).

- `R-37` **`doctor`/`--check-env` read scope under `--worktree` (RG-30).**
  `doctor` and `--check-env` both passed `None` to `resolve_repo_and_worktree`
  instead of the caller's `--worktree`, so `doctor --worktree B` silently
  reported the INVOKING tree's answers under B's name — including `R-30a`'s
  worktree-specific host-lane git-view WARN, exactly the kind of per-tree
  answer that legitimately differs between trees. `R-36i` (RG-27) closed the
  identical hazard for `history`; this closes the last remaining instance
  estate-wide, with the same disclosure discipline.
  - **`R-37a` `doctor`'s per-tree checks follow `--worktree`.** Git identity,
    the `R-30a` host-lane git view, and mountinfo all describe a TREE —
    without `--worktree` that is the invoking checkout, WITH it that is the
    named tree, resolved and validated by `resolve_worktree_scope()` (a bad
    override is not a real git worktree, refused by name) INSIDE the same
    try/except the "git" check already wraps every failure in: a garbage
    `--worktree` becomes a `[FAIL] git` record — same as every other
    broken-host case doctor already survives — rather than reaching the
    `R-30a` check at all, whose "no gitdir file here" reads as "plain
    checkout, nothing to warn about" and would otherwise print a FALSE
    `[OK]` for a tree that does not exist. A disclosure line names the
    selected tree up front, and the "git" record itself repeats it (`R-05`).
  - **`R-37b` The toolchain probe's `cd` target follows it too.** Both the
    `repo`/`worktree` a probe mounts AND the `cd` target it runs inside
    (`assay_toolchain_findings()`) follow the SAME override — mounting the
    selected tree's repo while `cd`ing into the invoking checkout's absolute
    project path would not probe the selected tree, it would run against a
    directory the probe container never mounted (or, coincidentally, the
    wrong one). Shared by `doctor` (check 5) and `--check-env`'s toolchain
    half — one relocation, two callers.
  - **`R-37c` `--check-env`'s env-drift scan follows it too, and refuses
    upfront rather than degrading.** The advisory Python-source scan reads
    the SELECTED tree's sources (`resolve_worktree_scope()`), not the
    invoking checkout's. Unlike `doctor`, `--check-env` has no per-check
    ledger for a bad override to land in gracefully, so resolution happens
    upfront and a bad `--worktree` refuses the whole command (exit 2 not a
    directory, exit 3 not a git work tree, carrying git's own line) rather
    than silently scanning nothing under a nonexistent tree's name — the
    same refuse-loud shape `R-36i` uses for `history`'s read side.

- `R-38` **Every assay lane resumes and reports progress (RG-33).** The
  assay-kind inner command carries `--resume --progress
  .assay/progress-<assay_lane>.jsonl` on EVERY invocation, on every runner
  (container, exec, host), live and dry. Measured cause: dstdns's
  `sql-mutation` lane (2026-09-02) spent three 120-minute retries re-testing
  the first of four target files from mutant #1 because the argv never
  carried `--resume` and `.assay/mutation-state/` had never been written.
  - **Unconditional, not rigor-gated.** assay ignores both flags on a lane
    that declares no R2 (its own `--progress` help says so; resume state is
    only read or written by the mutation sweep), so on an R0/R1 lane they
    cost nothing, and a lane that later gains R2 resumes from its first
    retry without a run-gate change. Deriving "has R2" from the inventory
    was rejected: a second reading of an assay-owned fact for no behavioural
    gain (`R-35`'s own rule).
  - **Resume never masks a change.** A candidate's id folds in the mutated
    file's exact source bytes, span, replacement and operator; an edited
    file re-executes every candidate touching it (assay CONSUMERS §"Resume
    and shard a long mutation lane"). run-gate adds no policy of its own.
  - **Location.** Both artifacts live beside the verdict under `.assay/` —
    the directory the inner creates one step earlier and every adopter
    git-ignores (`R-32`) — because a progress file anywhere in the judged
    tree makes assay refuse `NO_MEASUREMENT`/`DIRTY_TREE` on that lane's
    NEXT run. The verdict does not name either path (assay's contract),
    `artifacts = [".assay/progress-<lane>.jsonl"]` is how a consumer has it
    printed after every run (`R-08`).
  - **Judge floor, refused by name.** `--resume` shipped in assay 2.4.0 and
    `--progress` in 2.4.1. A pin whose declared `version` is below **2.4.1**
    refuses at argv construction — exit 2, naming the lane, the pin, the
    declared version, the floor and the remedy (re-pin the judge) — rather
    than failing inside the container by argparse under a message run-gate
    never wrote. A pin that declares no version is not checked (there is no
    claim to hold it to); an older judge then fails the lane loudly with
    assay's own `unrecognized arguments` line, never silently.

- `R-39` **Re-attach: a lane's container outlives its client, and is found
  again (RG-35).** A container lane runs detached (`R-15`) and is removed by
  an explicit `docker rm -f` in a `finally`. When the CLIENT dies — SIGKILL,
  a devcontainer restart, a harness that reaps a background command — that
  `finally` never runs: the container keeps going, the judge still writes its
  verdict into the bind-mounted worktree, and nobody collects the exit
  status, the evidence (`R-26`) or the history record (`R-36`). Until rev 34
  the next invocation of the same lane then started a SECOND container for
  the same commit. It does not any more.
  - **`R-39a` The inflight record.** On a SUCCESSFUL `docker run -d`, and
    only then, run-gate writes `<effective project dir>/.run-gate/inflight/
    <lane>.json`: container name and id, `started_at` (UTC ISO) and
    `started_epoch`, the judged commit, worktree, project dir, the lane's
    verdict and progress paths (`null` for a command lane), and
    `__revision__`. Scope is (judged worktree x project x lane) BY
    CONSTRUCTION — `R-21` already relocates the effective project dir into
    the judged tree, so two worktrees' gates address two different files and
    never meet, which is `R-36f`'s scoping answer reused rather than
    re-derived. The record's WRITE is serialised by a sibling
    `inflight.lock` (`O_NOFOLLOW`, 0600, bounded) plus
    write-temp-then-`os.replace`; that lock spans the write and nothing
    else, and it is NOT what arbitrates two live clients — `R-39e` is. The
    record's `schema` is CHECKED on read, not merely written: a record of
    another schema is disclosed by name and treated as no record, because an
    old client reading a future record under today's rules is the silent
    misread a version field exists to prevent. The
    record therefore also names its OWNER: `owner_pid`, `owner_start` (the
    process start time, field 22 of `/proc/<pid>/stat`, so a recycled pid
    cannot impersonate the owner), `boot_id` (a pid means nothing across
    a reboot) and `pid_ns` (the PID namespace's inode — a pid means nothing
    across a namespace either, and `boot_id` alone cannot say so because it
    is host-GLOBAL: every container on one host reads the same value). The
    store must be
    git-ignored and that is CHECKED, exactly as `R-36g` checks it, over all
    three paths the writer can leave behind. A store that cannot be
    confirmed ignored, or cannot be written, degrades to ONE warning that
    says what is lost ("if this client dies … the next invocation will start
    a second one") and the lane RUNS: refusing to write is not refusing to
    run, and run-gate has never made an un-ignored `.run-gate/` fatal.
  - **`R-39b` The decision, taken before anything is built.** With a record
    present, run-gate first asks whether the record's OWNER is still alive
    (`R-39e`); if it is, this invocation FOLLOWS and none of the branches
    below apply. Otherwise `docker inspect` answers for the named container
    and exactly one of five things happens, each DISCLOSED by name (`R-05`)
    — silence here is what turns a surviving container into a duplicate:
    **running + same commit** → re-attach (`run-gate: re-attached to <name>
    (started <t>, running for <m>m <s>s)`, then PLAIN `docker logs -f` +
    `docker wait` — plain, because `logs -f` already replays the container's
    log from its FIRST line before it follows, so a `--since` filter could
    only ever SUBTRACT lines a reconnecting client is entitled to; and the
    only stamp available to filter with is run-gate's own, taken after
    `docker run -d` returned and truncated to whole seconds, so it would
    subtract exactly the container's opening output); **exited + same
    commit** → collect
    (`run-gate: collected <name> (exited <code> at <t>)`) and finish exactly
    as an attached run would; **gone** → say so, record that run as
    `aborted`, clear the record, run fresh; **a different commit** → refuse,
    exit 2, naming both commits, the container, and `--fresh`; **`--fresh`**
    → remove the named container (by name, disclosed), clear the record, run
    anew. An `inspect` answer that parses as NEITHER a state nor a gone
    signal refuses (exit 3) rather than guessing — guessing "gone" would
    start the duplicate this requirement exists to prevent.
    - **What "gone" is, exactly.** ONLY `No such object` / `No such
      container` on `docker inspect`'s stderr. Every other inspect failure —
      an unreachable daemon above all — is INFRASTRUCTURE: exit 3, the
      record UNTOUCHED, no history write. Reading a daemon outage as gone
      (rev 34's first cut did) writes a false `aborted` entry and deletes
      the only thing on disk that can find the still-running container once
      the daemon returns, which is the loss `R-39` exists to end.
    - **The name is not the identity.** Container names are deterministic
      per (environment, repo, lane), so a record that outlives its container
      can name a DIFFERENT container that later took the same name. `{{.Id}}`
      rides along in the same `inspect` format (no extra call) and a
      mismatch is treated as GONE, disclosed by name: `a different container
      now wears this name; run-gate will not touch it`. The record is
      cleared and the lane runs fresh — run-gate never re-attaches to, and
      never removes, a container it cannot identify as its own. Automatic re-attach with `--fresh` as the
    escape, rather than a refusal requiring an `--attach` flag, is decision
    D4 of the post-v10 plan: a restart is the COMMON case after a client
    death, and a manual step is exactly what the host's one-container rule
    was written to avoid.
  - **`R-39c` One finish, one history record.** The fresh, re-attached and
    collected paths share ONE tail (`await_container`): logs, `docker wait`,
    evidence on failure, `docker rm -f`, record cleared in the SAME `finally`
    that removes the container, artifacts disclosed. History records the run
    ONCE, with the outcome from the real exit code and the duration measured
    from the CONTAINER, not from the seconds this client happened to be
    attached.
    - **Which container clock, by path.** A RE-ATTACH measures `now −
      started_epoch`: the client is watching the container finish, so its own
      clock is the container's. A COLLECT does not — the container exited at
      some arbitrary earlier moment and the idle gap since is not part of the
      run — so it takes `FinishedAt − StartedAt` from the same `docker
      inspect` that answered the state question, no extra call. Left as `now
      − started_epoch` it charged one overnight collect with the whole night
      (`duration_seconds: 10800.028` for a container that had exited three
      hours earlier), inside the median/min/max series `R-27` exists to make
      trustworthy. Docker's stamps are hand-parsed: NANOSECOND fractions, and
      the year-1 zero value `0001-01-01T00:00:00Z` that means "never set" (a
      RUNNING container's `FinishedAt` is exactly that). Either stamp
      missing, unparsable or zero, or a finish before its start → the
      record's own `started_at` answers, as before; a duration is never
      invented from half a pair.
    A run whose container is gone is recorded
    `aborted`, never a pass; a container that disappears mid-collect leaves
    `docker wait` unreadable, which is already an exit-3 refusal, never a
    pass.
    - **What the invocation says about itself.** The usual `rev | lane | env
      | slice` header belongs to a run this client STARTED; a re-attach or a
      follow prints `rev | lane | re-attach` / `| follow` instead, because
      the header's mounts and slice are claims this invocation never made.
      The lane's own BOUNDS are the opposite case and print on all three
      paths (`print_lane_bounds`): `budget` and `stall_timeout` are facts
      about the lane, and a re-attached lane that was never told its
      `stall_timeout` got stopped against a number its own output had never
      mentioned. On a FOLLOW the `stall_timeout` line also names the owning
      pid as the client that will act on it — the same fact, without
      implying this invocation would be the one to stop the container.
      The ARTIFACTS a re-attach or follow discloses are the ones the RECORD
      declared (`verdict`, `progress`), not the ones this invocation's
      config would construct: they are what THAT run was told to write, and
      the live config is not the authority for a run already in flight.
      Presence of the key is the test, so a record written before rev 34
      falls back to the config and a command lane's recorded `null`
      correctly discloses no verdict.
  - **`R-39d` Where it does not apply.** `--dry-run` DISCLOSES a record and
    names what a live run would do with it, and changes nothing — it does
    not attach, collect, clear or remove. The disclosure resolves HEAD and
    the owner FIRST and walks the live decision's branches in the live
    decision's order, so it names the refusal, the follow or the `--fresh`
    removal where those are what would happen; announcing "re-attach or
    collect" for a record the live run refuses is worse than saying nothing,
    because a dry run's whole audience is someone about to act on it. Host lanes and exec lanes start no
    container of run-gate's own, so they have no record and refuse `--fresh`
    by name (`R-25`/`R-35`'s rule), as do `doctor`, `history`,
    `validate-pointers` and a bare invocation. A conjunction lane carries the
    behaviour to each SUB-lane it invokes; the conjunction itself is a
    command lane with no container of its own and no record. `--fresh` is
    per-invocation and deliberately does NOT fan out into a conjunction (no
    token, unlike `--worktree` and `{base}`): it REMOVES a container, and
    propagating it would destroy sub-lane containers that are legitimately
    running. Nothing is lost — a sub-lane that cannot attach refuses on its
    own terms, the `&&` chain stops there, and the refusal naming that
    sub-lane, its container and `--fresh` passes out through the conjunction
    as exit 2 (verified; CONSUMERS "Gate-conjunction lanes" carries the
    transcript and the shape a consumer writes if it wants one sub-lane
    always fresh).
  - **`R-39e` Two clients, one lane: the live owner is FOLLOWED, never
    hijacked.** Scope answers two worktrees (`R-39a`); it does not answer two
    terminals on ONE tree, and that case is common precisely because the
    host's rule is one gate container at a time ACROSS agents. The record's
    owner identity (`owner_pid` + `owner_start` + `boot_id` + `pid_ns`) is
    checked before anything else: the owner is ALIVE when the boot id matches
    this boot, the pid exists, and its start time is the recorded one — a
    conjunction, so a recycled pid and a post-reboot pid both read as DEAD.
    - **Another PID namespace → liveness UNKNOWN → treated as ALIVE.** When
      the record's `pid_ns` is not this client's, the pid cannot be looked up
      here AT ALL, and the answer is not "dead" — it is "I could never have
      seen it". `boot_id` cannot catch this on its own: it is host-global, so
      two clients in two containers that bind-mount the same worktree (this
      host does exactly that) both match on boot and then each read the
      other's live owner as dead — which is `R-39e`'s hijack, back again
      across a namespace boundary. Unknown resolves to ALIVE in the same
      direction every other "could not determine" in this decision takes:
      the run is FOLLOWED, `--fresh` REFUSES, and nothing is removed. The
      boundary itself is disclosed by name, because an assumption of life is
      a different claim from a reading of it. A record written before rev 34
      recorded the inode cannot be compared, so the question is not asked and
      the boot + start-time conjunction answers alone.
    - **Owner alive** → this client FOLLOWS: `run-gate: following <name>
      (owner pid <N>, started <t>)`, then the same `docker logs -f` stream
      and the same exit code — and it removes NOTHING. Not the container, not
      the record, not a history entry: all three belong to the client that
      started the run and is still there to do them, which is what keeps
      `R-39c`'s "ONCE" true with two clients attached. `docker wait` is
      issued BEFORE the log stream and runs concurrently with it, because the
      owner removes the container within milliseconds of its exit and a wait
      issued after that removal would answer "No such container" — a
      follower reporting exit 3 on a lane it just watched pass. The price is
      one EXTRA docker client held open for the lane's whole duration, and
      it is accepted deliberately: the alternative loses the follower's exit
      code on every clean finish, which is every run that matters.
    - **Owner alive, `--fresh`** → REFUSED (exit 2), naming the pid.
      run-gate never removes another client's container; `--fresh` is an
      escape from a container nobody is watching.
    - **Owner alive, container already gone** → the decision is RE-READ
      first, `OWNER_RACE_REPOLLS = 3` times in all (the first read included)
      at `OWNER_RACE_PAUSE_SECONDS = 0.5` apart, ~1 s in total. The two
      facts can only both hold inside the owner's own `docker rm -f` →
      `clear_inflight_record` window, which is microseconds wide, so the
      second read normally finds NO RECORD: this client then says so and
      runs fresh, because a refusal naming a pid whose run is already over
      is worse than a one-second wait. If the record vanished → run fresh.
      If the owner died meanwhile → the ordinary gone-container path
      (`aborted`, cleared, fresh). Only if the owner is STILL alive after
      the full window is it refused (exit 2) naming the pid, record
      untouched: the owner owns that outcome, and a second `aborted` written
      from here would be a second result for one run. Bounded, never a wait
      loop — an owner genuinely wedged between those two statements must
      still produce the refusal.
    - **Owner dead** (or a record from before rev 34, which names no owner)
      → `R-39b` unchanged: re-attach, collect, report-and-clear, or refuse on
      a commit mismatch. "After its client dies" is now literally what the
      code checks.
    - Rejected: a lock held for the LIFETIME of the run, which would refuse
      the second terminal outright and lose the follow — the operator's most
      common second invocation is "show me what it is doing", not "start
      another one".

- `R-40` **Progress-judged liveness (RG-36).** `budget` is advisory here and
  a hard lane-wide bound in assay, so the only way to bound a long mutation
  lane was to guess a TOTAL: dstdns raised `sql-mutation` from 90m to 120m
  and it still could not finish a window (`R-38`'s transcript). Since rev 33
  every assay lane writes `.assay/progress-<assay_lane>.jsonl` with a
  `candidate_index`/`candidate_total` per candidate, so health is read off
  the file instead — rate, ETA, and the load-bearing one, SILENCE.
  - **`R-40a` Disclosure, every `PROGRESS_POLL_SECONDS = 30`.** While an
    assay lane's container runs, run-gate reads the file the same `R-38`
    constructs and prints, AT MOST once per poll and only when something
    changed: `run-gate: progress <lane>: candidate <i>/<N>, <rate>/min, ETA
    <m>m`. The FIRST observation is a baseline and prints the count alone: a
    single event with no clock in the file is not a measurement, and
    inventing a rate would be the guess this replaces. 30 s judges a
    15-minute `stall_timeout` to within 3%. Each poll costs a `stat()`, a
    full `read_text()` and a `json.loads()` per line — the file is
    append-only and no offset is remembered — so a four-hour lane costs 480
    of those, which on a 4 000-candidate mutation lane is ~2M line parses
    over the run: cheap beside the judge it is watching, but not the "480
    `stat()`s" this paragraph claimed before review round 1 (N1). An
    incremental read from a saved offset would remove the parse half and is
    the obvious step if the file ever grows enough to matter. Disclosure
    only (`R-05`) — nothing here decides anything.
  - **`R-40b` No events is disclosed ONCE and is never a fault.** A missing
    file, a file holding only the `run` header (an R0/R1 lane — one command,
    no candidates), or a judge that writes none, prints `run-gate: progress
    <lane>: no candidate events (not an R2 lane, or the judge writes none)`
    exactly once and is treated as healthy. A torn last line (the judge is
    mid-append) is skipped, not fatal.
    - **A file that VANISHES mid-run is not this case.** Once an event has
      been seen, a file that becomes unreadable — deleted, truncated,
      replaced — gets its own sentence naming the last event it held, and it
      is SILENCE: the stall clock keeps running from the last real movement,
      not from the vanishing. Printing `no candidate events` there was both
      untrue and disabling — stall detection stopped forever, so a lane that
      lost its progress file became permanently unstoppable at the moment it
      most needed bounding (review round 1 N4). The notice is armed once per
      disappearance; a file that comes back reports normally again.
  - **`R-40c` `stall_timeout`, an optional lane key with the `budget`
    grammar.** The lane is stopped ONLY when the container is STILL RUNNING
    and the progress file has not advanced for that long — `docker rm -f`,
    evidence saved (`R-26`), exit 3, naming the stall, the last event seen
    and the age. **NEVER on total elapsed time**: `budget` stays advisory and
    its disclosure is unchanged. The "still running" half is structural, not
    asserted: the check only ever runs in the poll of a `docker logs -f` that
    has not returned. A lane whose file never appears cannot stall by this
    rule (`R-40b` says so out loud), which is what keeps the key from killing
    R0/R1 containers. Declared on a `kind = "command"` lane it is REFUSED at
    load — a command lane writes no progress file, so the key could never do
    anything, and an inert key that reads like a real one is the defect
    `R-08a` was filed for one key over.
    - **Where the silence is measured FROM: the FILE.** Real movement
      restarts the clock; the watch's FIRST observation is not movement —
      there is no earlier event for it to have moved from; it is the
      baseline `R-40a` already calls it. Its age is therefore taken from the
      file itself, `wall_now − mtime`, **which for a re-attach is a moment
      before this client existed.** That answers both cases exactly: a
      container ALREADY frozen when this client arrived is as silent as its
      file is old and stalls at once (the `R-39` re-attach case — the run
      being judged started in another process, possibly hours ago), while a
      lane whose first candidate arrives after a long startup has an mtime
      of ~now and gets its FULL window.
      Seeding the clock from the watch's CONSTRUCTION instead — rev 34's
      first cut — bought the first case at the price of the second, which is
      the case `R-40` exists for: an assay mutation lane's startup is image
      entry, `safe.directory`, a Postgres provision, collection, the
      baseline suite and mutant generation, all before candidate #1, so a
      lane declaring the `stall_timeout = "15m"` this spec recommends it was
      announced and stopped by the SAME `poll()` (`candidate 1/172`
      printed, then `has not advanced for 1200s`) — a self-refuting message
      an operator reads only after `docker rm -f` has run.
    - **Which file, on a re-attach.** The one the RECORD names
      (`R-39a`'s `progress`), not the one this invocation's config would
      construct: it is the file that run was told to write, and a lane
      retargeted between the two invocations would otherwise be judged by
      the silence of a file nobody was ever going to append to. Presence of
      the key decides, so a record written before rev 34 falls back to the
      config.
  - **`R-40d` Coarse now, exact later, same code.** assay's events carry no
    timestamp today, so the rate is measured against run-gate's OWN clock
    from the first event it observed and advancement is the file's mtime.
    Where an event already carries `elapsed_s` (assay B065) it is PREFERRED,
    so the same implementation becomes exact when B065 lands — no rewrite,
    and no second reading of the same fact.
  - **`R-40e` The documented shape.** For a mutation lane: a generous assay
    `budget` + `judge.mutation.budget_per_candidate` + run-gate
    `stall_timeout`. For R0/R1: `budget` is the command's own bound and
    there is nothing to add.

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
