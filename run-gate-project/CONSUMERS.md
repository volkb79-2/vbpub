# run-gate — consumer & adoption guide

How a project adopts `run-gate.py`, how partner tools plug in, and what the
lane declarations look like per project type. Companion to `README.md`
(design authority) and **`SPEC.md` (normative contract)**. BUILT as of P01
(2026-08-22); first adopter nyxloom.

## The adoption steps (any project)

1. **Get the script.**
   - vbpub-internal: `ln -s ../run-gate-project/run-gate.py run-gate.py`
     at the project root (relative symlink, committed, exec bit on).
   - external repo (dstdns, groop): copy the file to the project root,
     commit it. The in-file `__revision__` is your drift marker — estate
     sweeps compare it; update by re-copying.
2. **Declare lanes** in `run-gate.toml` next to it (final schema below —
   parsed by run-gate.py ONLY; no other tool may read this file). Shared
   environment facts do NOT belong here if a repo-root central config
   already defines them (see below).
3. **Point consumers at lanes** — e.g. nyxloom's `[gates.<name>]`:
   `argv = ["bash", "-c", "cd {worktree}/<proj> && ./run-gate.py --worktree {worktree} <lane>"]`.
   `{worktree}` is substituted textually into a shell string, so judged
   trees must live at gate-safe paths (letters/digits `_ . / -`; no spaces or
   shell metacharacters) — the gate refuses any other path, every lane kind.
   The consumer adds NO test logic of its own — no suite argv, no lane
   sequencing, no coverage flags. All test definitions live in
   `run-gate.toml` (the SSOT); if multiple sub-lanes must run together,
   declare a conjunction lane in `run-gate.toml` (see below) and point the
   consumer at it. Keep `asserts`/`timeout_seconds` as the daemon's own
   policy.
3a. **Certify the linkage (RG-2).** Add ONE project test that runs
   `./run-gate.py validate-pointers <your-consumer-document>` (exit 0 = every
   pointer names a lane the SSOT really declares, with the canonical
   `--worktree {worktree}` shape). This is what makes a renamed lane go RED
   at TEST time instead of dying as `unknown lane` at dispatch time — the
   pointer is part of the dispatched surface, so it is certified like any
   other artifact, not assumed.
4. **AGENTS.md/README**: add one line naming `./run-gate.py` as the canonical test
   entrypoint (do this IN the adoption commit — docs never lead the tool).
5. **Gitignore the artifacts.** Lanes write evidence into the tree —
   `.assay/`, `coverage.json`, and anything else a lane's `artifacts` list
   names. The vbpub root `.gitignore` already covers internal projects; a
   copied-script repo MUST replicate the entries for every path its lanes
   write, or the NEXT lane's `clean_tree` check refuses mysteriously on
   yesterday's evidence. Treat the union of declared `artifacts` lists as
   the checklist.

## Central defaults (vbpub monorepo)

`run-gate.toml` at the REPO ROOT holds environment facts once for all
internal projects (`[environments.<name>]`: `image`, optional
`cgroup_slice`) and, since RG-16, SHARED LANES too: every package can use
the identical lane without copying definitions. Discovery: nearest STRICT
ancestor of the project dir; project entries shadow a central name entirely
(whole table, no field merging — same rule for environments and lanes). A
central lane's pin sidecars must exist in each consuming project — the gate
refuses at load naming both files when a project doesn't vendor them; shadow
the lane locally to opt out. Copied-script repos (dstdns) are self-contained
unless they grow their own root file.

```toml
# repo-root run-gate.toml — one shared assay lane every package inherits;
# paths are relative to EACH consuming project (which must vendor them):
[lanes.assay-shared]
kind = "assay"
environment = "tester-unified"
assay_lane = "gate"
assay_command = ["./tools/assay/assay.pyz"]
clean_tree = false

[lanes.assay-shared.pins.assay]
version = "2.1.0"
sha256 = "tools/assay/assay.pyz.sha256"
```

## Lane schema (final — what run-gate.py actually validates)

```toml
# run-gate.toml — parsed by run-gate.py only
schema_version = 1

[lanes.<name>]
kind = "assay" | "command"
environment = "tester-unified" | "test-runner" | "host" | "<any central/project env name>"
budget = "20m"                      # advisory wall-clock; printed, never enforced here
memory = "4g"                       # optional docker --memory (per-lane RAM override)
clean_tree = true                   # default TRUE; false needs a written reason
description = "one-line what/why"   # optional; shown by --help (never by --list)
required_env = ["SCHEMA_GATE_PW"]   # optional; gate refuses to start if unset/empty,
                                    # and (container lanes) if not on the env's forward_env
artifacts = ["coverage.json"]       # optional; paths printed after EVERY run (success or
                                    # failure); {worktree} substituted; relative entries
                                    # resolve against the effective project dir; assay
                                    # lanes always disclose .assay/verdict-<lane>.json too

# RG-20 resources (optional sub-table — declare RAM so admission can protect
# the host; supersedes the top-level `memory` key, never both):
[lanes.<name>.resources]
memory = "1g"                       # hard RAM cap (--memory) + admission accounting:
                                    # refused if slice usage + this exceeds memory.max
memory_swap = "16g"                 # --memory-swap; tight RAM + ample swap absorbs bursts
cpu_weight = 100                    # advisory 1..10000 (printed; no portable docker flag)
io_weight = 100                     # advisory 1..10000 (printed; no portable docker flag)
shared = ["pg-main"]                # serialize with any other gate declaring the same name

# command kind:
argv = ["bash", "-c", "..."]        # required, non-empty; {worktree} substituted

# assay kind (all required — the tool never invents an assay invocation):
assay_lane = "ciu"                  # -> assay.toml [lanes.ciu]
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-2.1.0.pyz"]
[lanes.<name>.pins.assay]
version = "2.1.0"                   # DECLARING it = a claim: the lane verifies <assay_command> --version reports it
sha256 = "tools/assay/assay-2.1.0.pyz.sha256"   # verified FROM its own directory
```

Environment facts resolution order (no silent fallbacks anywhere):
`cgroup_slice` declared on the environment → `$CGROUP_PARENT_DEV_BACKGROUND`
(hard error if absent); physical repo root DERIVED from `/proc/self/mountinfo`;
LoadState pre-check only where systemd is reachable. Container lanes
dual-mount the repo (physical + namespace views) for worktree gitfiles;
outside the devcontainer namespace — where no second view is derivable — the
lane refuses rather than silently mounting once; declare the alias with
`$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'` (host side must equal the repo root).

Container lanes forward only the implicit cgroup infrastructure variable plus
the environment's explicit `forward_env = ["SCHEMA_GATE_DSN"]` allowlist. A
declared but unset value remains absent rather than becoming a default; the
lane's own required-input policy must fail loudly when absence matters.
Lanes that NEED a variable declare `required_env = ["SCHEMA_GATE_PW"]`: the
gate refuses to start unless each name is present and non-empty, verifies
container lanes can actually receive it (it must be on `forward_env`), and
prints which forwarding keys were present at start — names only, never
values (the docker-argv print redacts them too). Run
`./run-gate.py --check-env` for an advisory sweep of env references in the
project's Python sources that no lane forwards or requires.

### `kind = "assay"` — projects that adopt assay (the quality partnership)

run-gate.py does the ORCHESTRATION (environment, mounts, cgroup, pin verify,
clean tree, detached run), then invokes the pinned assay CLI; **assay does the
JUDGMENT** — its lane in `assay.toml` owns argv-under-test, coverage floors,
R-levels, changed-line policy, snapshot isolation. Two files, two owners, no
duplicated registry:

```toml
[lanes.ciu]
kind = "assay"
assay_lane = "ciu"                  # -> assay.toml [lanes.ciu]
environment = "tester-unified"
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-2.1.0.pyz"]

[lanes.ciu.pins.assay]
version = "2.1.0"                   # verified against the judge the image carries
sha256 = "tools/assay/assay-2.1.0.pyz.sha256"
```

Division of labor, spelled out:

| concern | owner |
|---|---|
| container image, mounts, cgroup slice, env passthrough | run-gate.toml |
| artifact pins (assay version/sha), clean-tree refusal | run-gate.toml / assay (S18.4) |
| suite argv, coverage floors, R0/R1/R3, isolation snapshot | assay.toml |
| verdict artifact + PASS/FAIL meaning | assay |
| WHEN a lane must pass (release policy) | the project's release config (cmru) |

### Worked example — run-gate × assay, end to end

The halves are documented separately (this file owns orchestration;
[`../assay/docs/CONSUMERS.md`](../assay/docs/CONSUMERS.md) owns judgment).
Here is the whole seam on one page:

1. **Get the judge** into the project with its sidecar:
   ```bash
   mkdir -p tools/assay && cp /path/to/assay-<version>.pyz{,.sha256} tools/assay/
   (cd tools/assay && sha256sum -c assay-<version>.pyz.sha256)
   ```
2. **Declare one R0 lane** in the project root as `assay.toml` — start from
   `assay/templates/consumer-assay.toml`; R0 claims nothing but a
   schema-validated verdict:
   ```toml
   schema_version = 2

   [lanes.unit]
   scope = "S1"
   rigor = ["R0"]
   enforcement = "gate"
   argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"]
   env = {}
   env_passthrough = []
   budget = "20m"
   allow_argv_append = false
   ```
3. **Declare the run-gate lane** in `run-gate.toml` — orchestration + pin:
   ```toml
   [lanes.unit]
   kind = "assay"
   assay_lane = "unit"                 # -> assay.toml [lanes.unit]
   environment = "tester-unified"
   assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<version>.pyz"]

   [lanes.unit.pins.assay]
   version = "<version>"               # verified via <assay_command> --version
   sha256 = "tools/assay/assay-<version>.pyz.sha256"   # verified from its own dir
   ```
4. **Point the consumer** at it in the canonical, `validate-pointers`-certifiable
   form:
   `argv = ["bash", "-c", "cd {worktree}/<proj> && exec ./run-gate.py --worktree {worktree} unit"]`
5. **First run:** `./run-gate.py unit` — run-gate verifies the pin, runs the
   lane in the declared environment, prints the verdict path
   (`.assay/verdict-unit.json`) on success AND failure, and passes assay's
   exit status through as the gate decision.
6. **Read the evidence:** `assay verify .assay/verdict-unit.json`
   re-validates the retained verdict later. Keep `.assay/` gitignored
   (adoption step 5) so yesterday's evidence never dirties today's tree.

Adopting R1/R2/R3 (coverage floors, mutation, canary) is an `assay.toml`
edit per assay's docs — the run-gate lane above does not change.

### `kind = "command"` — projects that cannot (or need not) adopt assay

The lane runs a command in the declared environment with the same
orchestration guarantees, and the command's exit status is the verdict:

```toml
[lanes.suite]
kind = "command"
environment = "test-runner"
argv = ["pytest", "tests/", "-q"]
clean_tree = true
```

### Gate-conjunction lanes — when one consumer must run several sub-lanes

Some consumers (nyxloom's daemon) can express only ONE implementation-phase
gate per project. Rather than duplicating a compound command in every
consumer config, declare the conjunction IN `run-gate.toml` so every caller
(daemon, agent, human, CI) gets the identical sequence from the SSOT:

```toml
[lanes.gate]
kind = "command"
environment = "test-runner"
# The implementation-gate conjunction: schema + mock + assay judgment.
# Every consumer runs THIS lane; none duplicates its internals.
# RG-1 rule: EVERY sub-invocation carries --worktree {worktree} — an
# override given to the gate must reach every sub-lane.
argv = [
    "bash", "-c",
    '''RUN_GATE_EXTRA_MOUNTS=/var/run/docker.sock=/var/run/docker.sock ./run-gate.py --worktree {worktree} schema && ./run-gate.py --worktree {worktree} test-runner && ./run-gate.py --worktree {worktree} assay''',
]
clean_tree = false
budget = "75m"
```

The consumer then points at `./run-gate.py <lane>` exactly as for any other
lane — the conjunction is invisible above this boundary. run-gate REFUSES a
container command lane invoked with `--worktree` whose argv contains no
`{worktree}` token (host lanes are exempt — their cwd relocates into the
override tree), so a dropped override fails loudly instead of silently
judging some other tree.

### Consumer examples

**nyxloom `[gates.<name>]` — thin pointer only:**

```toml
[gates.test-runner]
argv = ["bash", "-lc", '''cd /workspaces/dstdns &&
    CGROUP_PARENT_DEV_BACKGROUND="${CGROUP_PARENT_DEV_BACKGROUND:?...}" &&
    ./run-gate.py gate --worktree {worktree}''']
phase = "implementation"
timeout_seconds = 4500
environment = "test-runner"
asserts = ["tests-pass", "canary-verified"]
```

**CI pipeline (Buildkite/future) — same pattern:**

```yaml
commands:
  - ./run-gate.py gate
```

**Human invocation — identical entrypoint:**

```bash
./run-gate.py --list        # discover lanes
./run-gate.py gate          # full implementation gate
./run-gate.py schema        # schema-only iteration
```

`--worktree PATH` selects a DIFFERENT tree to judge (the daemon/dispatch
case): the lane's execution relocates there — assay runs from
`<PATH>/<project>`, pin verification and verdict/artifacts resolve under it,
host lanes get that cwd. The invoking checkout is never judged by side
effect (SPEC R-21).

Scripting against gates: the lane's own exit status passes through
unchanged; run-gate's own refusals reserve **2** = configuration/refusal and
**3** = execution-infrastructure failure, so CI fan-out can distinguish
"your config says no" from "docker/git broke" without parsing stderr.

### Consumer timeouts must not cut lanes short

A consumer `timeout_seconds` tighter than the paired lane's `budget`
silently truncates the lane before its own declared wall-clock expires —
the budget is advisory and printed, but only if the consumer lets the lane
run that long. The rule: **consumer timeout >= lane budget** (wider is
fine). The estate sweep in
`run-gate-project/tests/test_run_gate.py::TestEstateBudgetTimeoutPairing`
enforces this pairing for every trove that points at run-gate lanes (assay's
assert-it pattern, replicated estate-wide); when you add a gate, it joins
the sweep automatically by naming the lane in its argv.

## Per-project-type recipes

**Python service repo with assay (ciu, cmru, assay itself):** the
`kind="assay"` shape above. First adopter was planned as **ciu** (HANDOFF-P01)
but DEFERRED by controller amendment A1 (parallel development); **nyxloom**
adopted first with a `kind="command"` lane (its judgment is still the in-tree
coverage gate until NL-1 migrates it to assay). ciu's adoption will move its
current `nyxloom.toml [gates.tester-unified]` argv INTO the tool and shrink
the gate entry to the two-token form.

**Python app estate with its own runner (dstdns):** dstdns uses `mode = "exec"`
against its CIU-managed persistent `test-runner`. run-gate owns invocation
uniformly (clean-tree, budget, worktree substitution); CIU owns build/deploy/
lifecycle. A not-running refusal prescribes the lifecycle of whichever
authority resolved the container name — declared `container_name` → your
project's own deployment authority; ciu-derived → the ciu lifecycle naming
the config file (never a vbpub-specific remedy for another project's tree).
The old `testing-exec.sh` shim is retired — run-gate execs directly.
Set `$RUN_GATE_EXTRA_MOUNTS=/var/run/docker.sock=/var/run/docker.sock` when a
lane needs Docker-in-Docker. Keep `assay.toml` lanes for the whole-target
coverage work as they land (B1-style).
The implementation gate is declared as a `gate` conjunction lane (see above);
nyxloom consumes it via `./run-gate.py gate --worktree {worktree}`.

```toml
[environments.test-runner]
image = "dstdns/test-runner:latest"
mode = "exec"

[lanes.test-runner]
kind = "command"
environment = "test-runner"
argv = ["bash", "-c", "cd {worktree} && MOCK_MODE=true pytest tests/unit -q"]
clean_tree = false
budget = "30m"
```

**Image-building / host-tooling projects (modern-debian-tools-python-debug,
tester-unified, tester-unified-go):** mostly not Python-coverage material —
assay's R1 judge has nothing to bite. They still get lanes:

```toml
[lanes.build]
kind = "command"
environment = "host"
argv = ["./build.sh", "--check"]     # image builds, smoke boots
[lanes.shellcheck]
kind = "command"
environment = "host"
argv = ["shellcheck", "-x", "host-setup/"]
```

The value here is uniformity: `./run-gate.py --list` answers "how do I test
this?" identically in every repo, and nyxloom/CI wire these projects with the
same two-token argv as the Python ones.

**Go projects (tester-unified-go lineage):** `kind="command"` with
`go test ./... -cover` in the Go image environment; if assay grows a Go
coverage adapter later, the lane flips to `kind="assay"` without any consumer
noticing — that boundary is the point.

## Partner integration notes

- **assay:** see the split table above. assay's own docs document
  `assay.toml`'s role (assay backlog B009); run-gate never re-implements
  judgment, and never bypasses assay's clean-tree/verdict rules.
- **nyxloom:** gates become thin argv pointers to named `run-gate.toml`
  lanes; all test definitions (suite argv, sequencing, conjunctions) live in
  the SSOT. The daemon keeps scheduling, timeouts, and asserts. The four-trap
  manual recipe in vbpub AGENTS.md is superseded for adopted projects (the
  section gains a pointer here).
- **cmru:** release gates call the same lanes; cmru's dependency checking is
  what makes the fresh-clone story work for the image-baked assay judge
  (build order: assay wheel → tester-unified image → gates run).
- **Buildkite (future):** agents on the remote hosts run long lanes
  (mutation, fuzz) via the identical entrypoint; `--list` output is the
  pipeline generator's input. Design note lives in assay B009.

## Distribution — script first, wheel second

The steps above are the PRIMARY distribution and stay canonical: symlink
(internal) or copy (external), zero installs, `__revision__` as the drift
marker. A wheel exists as a SECOND artifact for pip-flavored consumers:
`pip install run-gate` exposes a `run-gate` console script running the SAME
bytes, with the version derived from `__revision__` (never declared twice),
built by cmru's wheel-publish and tagged `run-gate-v<version>`. The wheel
NEVER becomes required — no adoption step, lane, or check may assume an
install; if you have the script you need nothing else.

## Anti-goals (read before extending)

- NO second parser of `run-gate.toml`, ever. A consumer that wants lane
  metadata calls `./run-gate.py --list` (stable, machine-readable output).
- NO judgment policy in `run-gate.toml` — floors and rigor belong to assay.
- NO non-stdlib imports in run-gate.py — the launcher must run on a fresh
  clone with zero installs.
- NO silent defaults for environment facts (slice names, physical paths):
  DERIVE or READ or FAIL, per AGENTS §4.2a.
