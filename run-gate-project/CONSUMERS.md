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
   `.assay/`, `coverage.json`, `.run-gate/` (RG-27 lane history), and
   anything else a lane's `artifacts` list names. The vbpub root
   `.gitignore` already covers internal projects; a copied-script repo MUST
   replicate the entries for every path its lanes write, or the NEXT lane's
   `clean_tree` check refuses mysteriously on yesterday's evidence. Treat
   the union of declared `artifacts` lists plus `.run-gate/` as the
   checklist. Only the last of these is self-enforcing — run-gate refuses
   to write an un-ignored history store and names the remedy ("What each
   lane costs" below).

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
# ...and NOTHING else. A pin table takes these two keys; any other is
# refused at load (RG-32, rev 34). In particular `budget` here was never
# enforced and is now a refusal: a kind = "assay" lane's real budget lives
# in the TARGET assay.toml's [lanes.<assay_lane>], and run-gate's own
# lane-level `budget` (one level up, no `pins` in the path) stays advisory.
```

Environment facts resolution order (no silent fallbacks anywhere):
`cgroup_slice` declared on the environment → `$CGROUP_PARENT_DEV_BACKGROUND`
(hard error if absent); physical repo root DERIVED from `/proc/self/mountinfo`;
LoadState pre-check only where systemd is reachable. Container lanes
dual-mount the repo (physical + namespace views) for worktree gitfiles;
outside the devcontainer namespace — where no second view is derivable — the
lane refuses rather than silently mounting once; declare the alias with
`$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'` (host side must equal the repo root).

> **BREAKING CHANGE — migrate if you use `mode = "exec"` (RG-23).** Early
> revisions hardcoded `MOCK_MODE` and `RUN_LIVE_TESTS` into the exec-mode
> forwarding loop; they were replaced by declarative `forward_env` with no
> migration pass. If your lane relies on either name, **it is not reaching
> your container today** — and the symptom is a false GREEN, not an error: a
> suite that skips its live tests when the flag is absent exits 0 having run
> none of them. Migrate both halves:
>
> ```toml
> [environments.test-runner]
> image = "yourproj/test-runner:latest"
> mode = "exec"
> forward_env = ["RUN_LIVE_TESTS", "MOCK_MODE"]   # was implicit; now required
>
> [lanes.release]
> kind = "command"
> environment = "test-runner"
> argv = ["bash", "-c", "cd {worktree} && pytest -m 'integration or e2e' -q"]
> required_env = ["RUN_LIVE_TESTS"]   # absence now REFUSES instead of skipping
> ```
>
> `forward_env` alone restores the old behaviour; adding `required_env` is
> what turns the silent-skip class into a loud refusal, and is why the
> implicit names are not coming back — a value with an authoritative source
> (your config) must not be shadowed by a literal inside the tool. Audit
> your own configs with `./run-gate.py --check-env` (limits below) and
> `grep -n forward_env run-gate.toml`. Estate audit at rev 25: no vbpub
> project declares `mode = "exec"` or `forward_env` at all, so the confirmed
> blast radius is dstdns, which is tracked in its own repo.

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

**What `--check-env` can and cannot see (RG-23).** It parses each `*.py` and
reports `os.environ["X"]`, `os.environ.get/setdefault/pop("X", …)`,
`getenv("X")`, `"X" in os.environ`, **and** a literal handed to your own
env-reader helper — a function that reads the environment through one of its
parameters, which is how the flag that motivated this hid from the previous
line-based sweep:

```python
def _env_flag_enabled(name):        # the read is here …
    return os.getenv(name, "").lower() in ("1", "true")

RUN_LIVE = _env_flag_enabled("RUN_LIVE_TESTS")    # … the NAME is here
```

```
run-gate: env-drift: $RUN_LIVE_TESTS referenced in conftest.py:6
  (helper _env_flag_enabled()) is neither forwarded nor declared
  required_env — add it to the environment's forward_env or the lane's
  required_env
```

It CANNOT see a name assembled at runtime (`os.getenv(prefix + suffix)`), a
name read from a non-Python source, or an indirection it does not model. A
file that does not parse is reported by name and falls back to the old line
regex, so a parse failure is never silently reported as "nothing found".
**A clean sweep is evidence, not a certificate** — it stays advisory (always
exit 0 for drift); `required_env` is the mechanism that actually refuses.

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

**Where a lane's dependency closure lives (assay B041 / ciu CIU-73).** assay
runs the lane command in a private snapshot of the *committed* tree —
gitignored trees such as `node_modules/` are absent by construction, and
`environment_command` cannot vouch for them (it runs in the invoking
environment, not the snapshot). A JavaScript lane therefore rebuilds its
closure OFFLINE from the committed lockfile as the first step of its own
argv (`npm ci --offline …`, then `npx --no-install vitest run --coverage`)
out of a package cache the ENVIRONMENT provides: for an `exec` environment,
bake the cache into the runner image at build from the same lockfile, or add
a volume to the runner's own stack; for an ephemeral environment,
`RUN_GATE_EXTRA_MOUNTS=/var/cache/<project>/npm=/opt/npm-cache`. Python
(venv) and Go (`GOMODCACHE`) closures are out-of-tree and need nothing.

**Preflight the toolchain instead of discovering it mid-run (RG-25).**
`./run-gate.py doctor` and `./run-gate.py --check-env` now ask the JUDGE what
each `kind = "assay"` lane needs — `<assay_command> lanes --json --file
assay.toml` (assay ≥ 3.2.0) run INSIDE the lane's own environment — and then
check that environment for it. run-gate still never parses `assay.toml`:

```
run-gate: doctor: [OK]   lane 'ui-unit' toolchain: node, npm
run-gate: doctor: [FAIL] lane 'ui-unit' toolchain: needs npm in environment
  'test-runner' (project /repo/run-gate.toml) — assay would reach
  MISSING_EXTERNAL_TOOL/NO_MEASUREMENT mid-run instead
run-gate: doctor: [FAIL] lane 'ui-unit' toolchain: assay lane 'ui_unit' is not
  declared in assay.toml (declared: py_unit, sql_schema) — this lane can only
  ERROR at run time
run-gate: doctor: [SKIP] lane 'ui-unit' toolchain: `assay lanes --json` did not
  run in environment 'test-runner' (exit 2: unrecognized arguments: --json) —
  an assay older than 3.2.0 has no inventory (B044). The pin declares the
  version this lane needs; run-gate does not impose a floor it never declared
```

Read the statuses precisely: **`[FAIL]` only ever states a fact the judge
established** (a tool it named is absent; a lane it does not declare).
Everything meaning *"I could not determine this"* — an older judge, an
unreachable environment, an inventory schema this run-gate does not read, a
`host` environment, no docker — is `[SKIP]` with the reason, and **never
turns a healthy project red**. `doctor` exits 2 only on FAIL; `--check-env`
exits 2 on a toolchain FAIL while its env-drift half stays advisory.

**`--worktree` redirects the whole report, not just the run (RG-30).**
`./run-gate.py doctor --worktree B` and `./run-gate.py --check-env
--worktree B` probe B's environment, scan B's Python sources, and read B's
git identity — never the invoking checkout's under B's name. `doctor` names
the selected tree up front and repeats it on the `[OK] git` line; a
`--worktree` that names no real git worktree fails the `git` check loudly
(never a silent `[OK]`) while the rest of the report still runs. `--check-env`
has no per-check ledger for a bad override to land in gracefully, so it
refuses outright instead of scanning nothing under the wrong tree's name.

Which tools get checked: `external_tools` and `argv0` as the inventory
reports them, plus the toolchain implied by `language` (`javascript` →
`node`, `npm`; `go` → `go`). That last mapping lives in run-gate only
because assay 3.2.0 reports `external_tools: []` for every shipped adapter
and documents the language fact in prose instead; a language run-gate has no
fact for is reported with an explicit caveat on the line rather than being
treated as "nothing needed".

> **`doctor` and `--check-env` START CONTAINERS for this check.** Fitness
> cannot be read, only observed, so the inventory question and the
> `command -v` checks execute inside the lane's own environment. They are
> short-lived and read-only (`assay lanes` runs nothing; `command -v` is a
> shell builtin), they judge nothing and write nothing into your tree, and
> ephemeral ones carry `--cgroup-parent` like every container run-gate
> starts. The cost is bounded: **one inventory probe per (environment,
> `assay_command`) plus one batched `command -v` probe per environment** —
> not per lane. A project with no `kind = "assay"` lane starts nothing at
> all, and neither verb ever starts your judged lane. If you run `doctor` in
> a context where starting a container is unacceptable, that is the check to
> know about.

### Lanes that take their comparison base from the gate (RG-26)

assay ≥ 3.0.0 lets a changed-line lane omit `judge.base` and declare
`judge.base_source = "request"` instead — the PR-scoped shape, where the
orchestrator owns which branch point is being judged. Pass it with `--base`:

```toml
# assay.toml — the judgment half owns the fact
[lanes.p129_enumeration_cursor.judge]
mode = "changed_lines"
base_source = "request"        # the gate supplies the base; assay never guesses
```

```toml
# run-gate.toml — the orchestration half restates NOTHING
[lanes.cursor]
kind = "assay"
environment = "tester-unified"
assay_lane = "p129_enumeration_cursor"
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-3.2.0.pyz"]
```

```bash
./run-gate.py cursor --base "$(git merge-base HEAD origin/main)"
# run-gate: comparison base 4c6eb2b6… (from --base) → --request-base
```

```bash
./run-gate.py cursor          # no --base: the judged tree's own upstream
# run-gate: comparison base 4c6eb2b6… (from merge-base HEAD @{upstream}) → --request-base
```

There is **no `run-gate.toml` key** for this — run-gate DERIVES it by asking
the judge (`assay lanes --json`), so the fact has exactly one spelling. What
that costs you: an assay lane invocation now issues one short read-only
inventory probe in its environment before the judged run.

Refusals, all exit 2 and all naming the lane:

| situation | what happens |
|---|---|
| delegating lane, no `--base`, tree has no upstream | `lane 'cursor' delegates its comparison base; pass --base REF (worktree has no upstream)` — a guessed base is not a base |
| `--base` on a lane whose `base_source` is not `"request"` | refused, naming the value assay declared (assay would refuse it anyway; this refuses earlier and clearer) |
| `--base` on a command lane with no `{base}` token | refused — the ref could only be silently dropped |
| `--base` with a judge too old to answer (`assay lanes --json` missing) | refused, naming assay **3.2.0** (B044) as the version that carries the inventory |
| no `--base`, judge too old | **nothing changes** — the old judge keeps working exactly as before |

**Conjunction lanes propagate it** the same way they propagate `--worktree`
(RG-1): a token in the lane's own argv.

```toml
[lanes.gate]
kind = "command"
environment = "host"
argv = ["bash", "-c",
        "./run-gate.py --worktree {worktree} --base {base} cursor && \
         ./run-gate.py --worktree {worktree} unit"]
```

A lane carrying `{base}` resolves its ref by the same rules above, so
`./run-gate.py gate` on a tree with no upstream refuses instead of
substituting an empty string.

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

   Both tool names certify: the script form (`./run-gate.py …`) and — since
   RG-14 — the installed console script (`exec run-gate --worktree {worktree}
   unit`). Discovery snippets (`--list`, `--help`, `--check-env`) and the
   reserved verbs (`doctor`, `validate-pointers`) name no lane by design and
   are exempt from the lane check; prose-named fields (`label`,
   `description`, …) are never parsed as invocations. One deliberate limit:
   a path-anchored console form (`/usr/local/bin/run-gate …`) is NOT
   recognized — it stays uncertified (fail closed) rather than waved through;
   invoke it as bare `run-gate` if you want it certified.
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

**`--fresh` does NOT fan out into a conjunction (RG-35, rev 34), and there is
no token for it.** Unlike `--worktree` and `{base}`, which must reach every
sub-lane or the gate judges the wrong thing, `--fresh` REMOVES a running
container: propagating it would destroy every sub-lane's inflight container,
including ones legitimately still running for another commit or another
client. It stays per-invocation. Nothing is lost by that, because a sub-lane
that has a container it cannot attach to refuses on its own terms and the
refusal reaches the operator through the chain — verified 2026-09-02 against
a host conjunction whose sub-lane carried a mismatched inflight record:

```
$ ./run-gate.py gate                                    # exit 2
run-gate: rev 34 | lane gate | env built-in 'host'
run-gate: lane 'sub' has an inflight container run-gate-conj-sub-1-1 (started
2026-09-02T11:00:00Z, running) judging commit deaddead… , but <tree> is now at
d9e396ed… — run-gate will not attach that run to this commit, and will not
start a second container for the same lane. Wait for it to finish, or re-run
with --fresh (which removes run-gate-conj-sub-1-1 first)
run-gate: lane 'gate' exit 2
```

The `&&` chain stops at the refusing sub-lane (the step after it never ran),
the message names THAT sub-lane, its container and `--fresh`, and exit 2
passes through to the conjunction. The operator applies `--fresh` to that
sub-lane directly: `./run-gate.py sub --fresh`. A consumer that wants one
sub-lane always fresh writes `--fresh` into that sub-invocation's own static
argv inside the conjunction — and thereby forfeits re-attach for it, which
is a deliberate trade, not a default.

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

### Host lanes that delegate to a host-path-mounting harness (RG-21)

If a `kind = "command"`, `environment = "host"` lane shells out to your own
script that bind-mounts the repo into a container by HOST path
(srdm's `tools/gate.sh`: `repo_root` = the git toplevel, one
`-v "$host_repo_root:$repo_root"`), that lane is **main-checkout-only today
when the judged tree is a linked worktree.** run-gate is not the defect —
`{worktree}` forwarding and exit-status passthrough are correct — but a
linked worktree's `.git` is a FILE naming an absolute gitdir under the MAIN
checkout, which your single mount does not include, so every in-container git
plumbing call dies:

```
covergate: git rev-list --parents -n 1 HEAD failed: exit status 128:
fatal: not a git repository: /workspaces/vbpub/.git/worktrees/run-gate-rg-sweep
```

`./run-gate.py doctor` names the condition BEFORE the lane fails mid-run:

```
run-gate: doctor: [WARN] host-lane git view (RG-21): /repo/.worktrees/w1 is a
  LINKED worktree; its gitdir is /repo/.git/worktrees/w1, OUTSIDE the tree.
  run-gate's own container lanes are fine (they dual-mount the repo root), but
  a host lane delegating to a harness that bind-mounts only the judged tree by
  host path will fail with 'not a git repository: …'. Mount the common gitdir
  into that container too, or pass it as GIT_DIR, or run the lane from the
  main checkout
```

Fix it in YOUR harness, one of three ways — run-gate cannot do it for you,
because it does not own that `docker run`:

```bash
# 1. mount the common gitdir at the path the gitfile records (preferred):
common=$(git -C "$repo_root" rev-parse --git-common-dir)
docker run -v "$host_repo_root:$repo_root" -v "$common:$common" …

# 2. or hand the container an explicit GIT_DIR (still needs the mount above):
docker run … -e GIT_DIR="$common" …

# 3. or run this lane from the main checkout only, and say so in its
#    `description` so the next person does not rediscover it at 2am.
```

Note that `run-gate`'s OWN container/exec lanes never hit this: `R-23`
dual-mounts the REPO root, so the gitdir is inside the mount by construction.
Related: an auto-derived host path for a worktree (e.g. `SRDM_HOST_REPO_ROOT`)
cannot be inferred from `docker inspect`, which maps only the devcontainer's
own `/workspaces/<repo>` — export it explicitly.

## What each lane costs — the `history` verb (RG-27)

Every lane run now leaves a record behind, so "how long does this lane take,
and how does that compare to recent runs" stops being something an operator
remembers until the terminal scrolls. run-gate **measures and persists**; it
decides no rigor/defer policy — that is yours to build on top of this data.

### Adopt it (one line, and it is enforced)

Add the store to your `.gitignore`. This is not a reminder — run-gate asks git
before every write, refuses to write an un-ignored store, and tells you why:

```gitignore
# run-gate lane invocation history (RG-27)
.run-gate/
```

> **BREAKING CHANGE (load-time) — a lane named `history` (RG-27).** `history`
> is now a CLI verb, so it joined `doctor` and `validate-pointers` as a
> RESERVED lane name and `[lanes.history]` is refused when the config loads,
> naming the file. No project in this estate declares one, so nothing here
> moved; a **copied-script repo** that happens to have a lane by that name
> must rename it (the lane was already unreachable — the verb would have won)
> before adopting rev 30.

```
run-gate: WARNING: lane history not recorded: /repo/proj/.run-gate is not
fully git-ignored, and writing there would leave the judged tree dirty for the
NEXT lane's clean-tree check — add '.run-gate/' to the .gitignore covering /repo
```

The lane's own verdict and exit status are untouched either way: telemetry is
a note in the margin, never the product. The vbpub root `.gitignore` already
carries the entry for internal projects; a **copied-script repo must add it**.

Optionally declare how much trend to keep (default 10 commits per lane; a
central `run-gate.toml` may declare it once and a project shadows it whole,
the R-09 rule):

```toml
# run-gate.toml
schema_version = 1

[history]
keep = 20
```

### Read it

```console
$ ./run-gate.py history selftest
run-gate rev 30 — lane invocation history
store: /workspaces/vbpub/run-gate-project/.run-gate/history.json
keep:  10  (default (10))

lane selftest
  latest:  pass exit 0  61.4s  4c6eb2b6a1f0  2026-08-31T11:02:17Z
           worktree /workspaces/vbpub
  history: 3 of at most 10 commit(s), oldest first
    COMMIT        OUTCOME   DURATION  STARTED
    9f1c0aa41b3d  pass         58.9s  2026-08-30T18:44:02Z
    b2884e76c0d1  fail          7.2s  2026-08-31T09:15:30Z
    4c6eb2b6a1f0  pass         61.4s  2026-08-31T11:02:17Z
    passes: n=2 median 60.2s (min 58.9s, max 61.4s)
    completed (passes + fails): n=3 median 58.9s (min 7.2s, max 61.4s)
```

`./run-gate.py history` with no lane reports every declared lane. The verb
runs no lane, starts no container, and exits 0 whenever the query itself
worked — an empty store is an answer, not a failure.

**`--worktree` redirects the READ, exactly as it redirects a run.** The store
is per (judged worktree x project), so a query about another tree must name
it — and the answer says which tree it describes:

```console
$ ./run-gate.py history selftest --worktree /workspaces/vbpub/.worktrees/feat-x
run-gate rev 30 — lane invocation history
tree:  /workspaces/vbpub/.worktrees/feat-x  (--worktree; this answer describes THAT tree, not the invoking checkout)
store: /workspaces/vbpub/.worktrees/feat-x/run-gate-project/.run-gate/history.json
```

In `--json` that tree appears as `worktree_scope` (`null` when the flag is
absent). A `--worktree` that is not a directory refuses (exit 2), and one
that is not a git work tree refuses (exit 3) — never a quiet fallback to the
invoking checkout's store, which would hand you tree A's medians under tree
B's name.

**`--json` is honored by `history` alone.** Every other verb refuses it by
name rather than printing its human form anyway (`--list` is already a
machine table).

### Consume it

`--json` is the machine form. Same data, same slots:

```console
$ ./run-gate.py history selftest --json
{
  "keep": 10,
  "keep_source": "default (10)",
  "lanes": {
    "selftest": {
      "history": [
        {
          "commit": "9f1c0aa41b3d…",
          "dirty": false,
          "duration_seconds": 58.9,
          "excluded_reason": null,
          "exit_code": 0,
          "git_operation": null,
          "history_eligible": true,
          "lane": "selftest",
          "outcome": "pass",
          "repo": "/workspaces/vbpub",
          "revision": 30,
          "started_at": "2026-08-30T18:44:02Z",
          "worktree": "/workspaces/vbpub"
        }
      ],
      "latest": { "…": "same shape, ANY outcome" },
      "stats": {
        "completed": {"count": 3, "min_seconds": 7.2,
                      "median_seconds": 58.9, "max_seconds": 61.4},
        "passes":    {"count": 2, "min_seconds": 58.9,
                      "median_seconds": 60.2, "max_seconds": 61.4}
      }
    }
  },
  "revision": 30,
  "schema": 1,
  "store": "/workspaces/vbpub/run-gate-project/.run-gate/history.json"
}
```

```bash
# "is this lane cheap enough to always run?" — ask the PASS series, not the
# mixed one: a red lane short-circuits, so its duration is not the cost of
# running the lane, only the cost of failing it.
./run-gate.py history selftest --json \
  | python3 -c 'import json,sys; s=json.load(sys.stdin)["lanes"]["selftest"]["stats"]["passes"]; print(s["median_seconds"])'
```

### The two slots, and why they differ

| slot | what it holds | when it updates |
|---|---|---|
| `latest` | the most recent invocation, **whatever happened to it** — pass, fail, tool error, Ctrl-C, dirty tree, mid-rebase | every invocation, always |
| `history` | a curated trend series keyed by **(lane, commit)**, bounded to the last `keep` commits | only when the measurement is trustworthy (below) |

A run reaches `history` only if it **completed with its own exit status**, on
a **clean** tree, with **no git operation in flight**, at a **resolvable
commit**. Otherwise `latest` still moves and the entry records why it was held
back:

```
  latest:  error  0.1s  4c6eb2b6a1f0  2026-08-31T11:40:03Z
           worktree /workspaces/vbpub
           NOT in history: the judged tree was dirty — the duration does not
           belong to this commit
```

Three things follow that are easy to get wrong, so they are stated:

- **A completed FAIL is kept**, with `"outcome": "fail"`. Its duration is real
  data. That is why the stats are split — merge `passes` and `completed`
  yourself only if your policy wants them merged.
- **`clean_tree = false` does not exclude you.** The test is whether the tree
  *was* dirty, not whether dirt was permitted.
- **`--dry-run` records nothing**, and neither does a configuration error
  (unknown lane, bad key) — no lane started, so there is no result.

### Two worktrees, two stores

The store lives at `<project>/.run-gate/history.json` **inside the judged
tree**, so `--worktree B` writes B's measurement into B's store and never into
the invoking checkout's. That is the concurrency answer: parallel worktree
gates address different files and never contend. Two lanes of one project in
one tree do contend, and are serialized on `.run-gate/history.lock` with an
atomic replace of the store — bounded, unlike the `resources.shared` lock: a
gate never hangs waiting to write telemetry.

Because the replace is atomic, readers take no lock. `history` answers
correctly while a gate is mid-write.

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

**Multi-instance worktrees (RG-24): the container name follows the JUDGED
TREE.** If your worktrees get their own isolated stacks (`ciu worktree
adopt` — each with its own rendered `ciu.global.toml`, its own network and
its own `test-runner`), run-gate derives the container name from
`<worktree>/ciu.global.toml` when that file exists, and only falls back to
`<repo>/ciu.global.toml` when it does not. This is the ONE place run-gate
prefers the worktree over the repo (elsewhere `repo` — the checkout owning
the shared `.git`, i.e. your main checkout — is the right authority). Do NOT
work around a wrong container by pinning `container_name` in the tracked
`run-gate.toml`: that literal is correct for exactly one running instance and
wrong for the next worktree created. Verify which config decided it — the
pre-execution disclosure names the scope:

```
$ ./run-gate.py test-runner --worktree /repo/.worktrees/p147b
run-gate: rev 24 | lane test-runner | env project /repo/run-gate.toml |
  container p147b-8a6bc3-test-runner (ciu.global.toml
  deploy.project_name+environment_tag (judged worktree:
  /repo/.worktrees/p147b/ciu.global.toml)) | slice … (…)
```

A `repo:` scope on a worktree you *did* adopt means its `ciu.global.toml` was
never rendered there — run `ciu render` in the worktree, don't declare a name.
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
bytes, built by cmru's wheel-publish and tagged `run-gate-v<version>`. The
wheel NEVER becomes required — no adoption step, lane, or check may assume
an install; if you have the script you need nothing else.

**Two SEPARATE version numbers, two SEPARATE jobs — do not conflate them:**
- `__revision__` (inside the script) is the copy-drift marker: bump it
  whenever `run-gate.py` behavior changes, and it is what CONSUMERS step 2
  compares to decide whether YOUR copy needs re-syncing.
- The wheel's semver tag (`run-gate-vX.Y.Z`, derived from the git tag by
  setuptools-scm) is the pip/GitHub-Release publish identity. It moves only
  when a release is cut through cmru; it says nothing about whether your
  copied script is stale.
A `pip install`ed wheel and a freshly-copied script can therefore report
DIFFERENT numbers (revision vs. version) at the same moment — that is by
design, not drift. The one invariant both obey: the wheel's `run_gate.py`
is always byte-identical to the canonical script, whatever either number
says.

## Anti-goals (read before extending)

- NO second parser of `run-gate.toml`, ever. A consumer that wants lane
  metadata calls `./run-gate.py --list` (stable, machine-readable output).
- NO judgment policy in `run-gate.toml` — floors and rigor belong to assay.
- NO non-stdlib imports in run-gate.py — the launcher must run on a fresh
  clone with zero installs.
- NO silent defaults for environment facts (slice names, physical paths):
  DERIVE or READ or FAIL, per AGENTS §4.2a.
