# run-gate — consumer & adoption guide

How a project adopts `run-gate.py`, how partner tools plug in, and what the
lane declarations look like per project type. Companion to `README.md`
(design authority) and **`SPEC.md` (normative contract)**. BUILT as of P01
(2026-08-22); first adopter nyxloom.

Two sibling guides answer the questions this file does not: **`LANE-AUTHORING.md`**
(what makes a lane good — which tools belong in run-gate, in assay, or inside
the suite; determinism, exit-code truth, budgets, per-language recipes, the
best-practice checklist and the open TODOs) and **`REMOTE-LANES-BUILDKITE.md`**
(enrolling a remote host as a Buildkite agent, triggering and collecting from
this host, the image on remote hosts, and the ciu/run-gate/assay seams).

## The adoption steps (any project)

1. **Get the script.**
   - vbpub-internal: `ln -s ../run-gate-project/run-gate.py run-gate.py`
     at the project root (relative symlink, committed, exec bit on).
   - external repo (dstdns, groop): copy the file to the project root,
     commit it. The in-file `__revision__` is your drift marker — estate
     sweeps compare it; update by re-copying.
2. **Declare lanes** in `run-gate.toml` next to it (final schema below —
   parsed by run-gate.py ONLY; no other tool may read this file). Every
   `[environments.<name>]` needs an explicit `mode`. When upgrading an older
   config, run `./run-gate.py migrate-modes run-gate.toml` and, if present,
   `./run-gate.py migrate-modes run-gate.root.toml`; review and commit the
   text diff. Shared environment facts do NOT belong here if a repo-root
   `run-gate.root.toml` already defines them (see below). Central config
   lookup stops at the project's Git toplevel, so a nested standalone
   repository needs its own `run-gate.root.toml` to share environment facts.
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
6. **Profiling (RG-55/RG-57) needs nothing from a consumer to be SAFE, but
   one thing to be PRECISE.** run-gate profiles EVERY lane kind — container,
   exec, AND bare-host (RG-57) — with no consumer action at all: a coarse
   in-lane sample (cgroup-based for container/exec lanes; for bare-host,
   `os.wait4()` reaps the lane's own child and records `ru_maxrss * 1024`
   in bytes, with no cgroup/pressure/DAMON/events data, RG-57) when no daemon
   answers, every time, unless
   `[profile] enabled = false` or `RUN_GATE_PROFILE=off`. For the PRECISE
   path (exact `memory.peak`, DAMON hot-set), the **cgroup-profiler daemon
   is host infrastructure, started once from the vbpub checkout** — `cd
   scripts/cgroup-profiler && ciu up` — never per-project, and never
   something a consumer's own gate config starts or stops. A runner
   without `docker exec` rights to that daemon (a locked-down CI box, a
   sandboxed agent) should set `RUN_GATE_PROFILE=off` rather than eat one
   `docker exec`-per-tick failure per profiled lane; `doctor`'s "profiler"
   check reports the daemon's reachability either way. A Docker permission,
   transport, or `docker ps` failure is reported as **state unknown**, never
   as “daemon absent” with a start-it remedy. On the bare-host daemon path,
   run-gate resolves `/etc/hostname` only as a candidate and compares that
   Docker object's mount namespace with its own; a mismatch or unverifiable
   identity safely takes the `wait4()` fallback. `run-gate.footprint.json`
   (written by `./run-gate.py footprint --write`, once a lane's history has
   at least one profiled PASS run) is **TRACKED — commit it**, unlike
   `.run-gate/` itself (still gitignored, per-instance telemetry): the
   manifest is a distilled, portable BUDGET meant to travel with the
   config it describes, not a fact about this one checkout.

   `RUN_GATE_PROFILE` (`"on"|"off"`, checked BEFORE any config table) is
   the ambient override: `off` disables profiling for EVERY lane
   unconditionally — no token, no daemon call, no sampler, regardless of
   `[profile]`/a lane's own `profile =` — and `on` FORCE-ENABLES it even
   over a lane's own `profile = false` (checked LAST, after config and the
   lane override, so it always wins). Any other value refuses at load, by
   name — a mistyped override is a refusal, never a silent no-op.

   <!-- run-gate-config -->
   ```toml
   schema_version = 1

   # run-gate.toml or run-gate.root.toml — [profile]: central/project,
   # whole-table shadowing
   # (the SAME rule [history] uses — a project's own [profile] REPLACES a
   # central one entirely, never a per-key merge)
   [profile]
   enabled = true                # default true; false disables every lane
                                  # (a lane's own `profile = false` disables
                                  # just that one; RUN_GATE_PROFILE=on/off
                                  # overrides both, see above)
   daemon = "cgprofile-host-daemon"  # the daemon CONTAINER NAME (default shown)
   interval = "1s"                # the `budget` grammar; basic/daemon sample tick
   damon = true                   # default true; DAMON hot-set classification
                                  # when the daemon supports it (basic-path
                                  # sampling never has DAMON data either way)

   # run-gate.toml or run-gate.root.toml — [footprint]: same whole-table
   # shadowing rule; consumed
   # by `doctor`'s staleness/drift check and by `footprint --write`
   [footprint]
   tolerance_pct = 25             # default 25; doctor WARNs when the live
                                  # history median peak differs from the
                                  # committed manifest by more than this
   max_age_days = 30              # default 30; doctor WARNs when the
                                  # manifest's distilled_at is older than this
   ```

## Central defaults (vbpub monorepo)

`run-gate.root.toml` at the REPO ROOT holds environment facts once for all
internal projects (`[environments.<name>]`: `image`, optional `cgroup_slice`
or `cgroup_slice_env`) and, since RG-16, SHARED LANES too: every package can use
the identical lane without copying definitions. Discovery: nearest STRICT
ancestor of the project dir; project entries shadow a central name entirely
(whole table, no field merging — same rule for environments and lanes).
Internal assay lanes omit `assay_command` and `pins`; run-gate installs the
selected worktree's `assay/` source in the lane environment, and the verdict
records the runtime version and source commit. Explicit command + pin sidecars
remain available for copied/external consumers. Copied-script repos (dstdns)
are self-contained unless they grow their own `run-gate.root.toml`.

<!-- run-gate-config -->
```toml
# repo-root run-gate.root.toml — one shared internal assay lane every package
# inherits; the selected worktree's ../assay source is installed at run time:
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.assay-shared]
kind = "assay"
environment = "tester-unified"
assay_lane = "gate"
clean_tree = false
```

## Lane schema (final — what run-gate.py actually validates)

<!-- run-gate-config -->
```toml
# run-gate.toml — parsed by run-gate.py only
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"
cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"
forward_env = ["SCHEMA_GATE_PW"]

[lanes.suite]
kind = "command"
environment = "tester-unified"
argv = ["bash", "-c", "pytest -q"]  # {worktree} may be substituted
budget = "20m"                      # hard wall-clock bound; state remains resumable
clean_tree = true                   # default TRUE; false needs a written reason
description = "one-line what/why"   # optional; shown by --help (never by --list)
required_env = ["SCHEMA_GATE_PW"]   # optional; gate refuses to start if unset/empty,
                                    # and (container lanes) if not on the env's forward_env
artifacts = ["coverage.json"]       # optional; paths printed after EVERY run (success or
                                    # failure); {worktree} substituted; relative entries
                                    # resolve against the effective project dir; assay
                                    # lanes always disclose .assay/verdict-<lane>.json too
profile = false                     # optional (RG-55): opt this lane OUT of profiling
                                    # entirely; or a table {enabled, damon} overriding
                                    # [profile]'s own defaults for this lane only

# RG-20 resources (optional sub-table — declare RAM so admission can protect
# the host; `resources.memory` supersedes lane-level `memory`, never declare both):
[lanes.suite.resources]
memory = "2g"                       # hard RAM cap (--memory) + admission accounting:
                                    # refused if slice usage + this exceeds memory.max
memory_swap = "16g"                 # --memory-swap; tight RAM + ample swap absorbs bursts
cpu_weight = 100                    # advisory 1..10000 (printed; no portable docker flag)
io_weight = 100                     # advisory 1..10000 (printed; no portable docker flag)
shared = ["pg-main"]                # serialize with any other gate declaring the same name
cpus = "2"                          # RG-48: docker run --cpus (decimal string, > 0).
                                    # Same key on [environments.<name>.resources] as a
                                    # FALLBACK for lanes that declare none of their own
                                    # (this lane's own value wins when both are set) --
                                    # the only resources key an environment accepts.
```

For an `assay` lane, replace `kind`/`argv` with `kind = "assay"` and
`assay_lane = "<name declared in assay.toml>"`. Internal source mode omits
`assay_command` and pins. External immutable mode supplies both, plus the
`[lanes.<name>.pins.assay]` version and SHA-256 table; the target assay lane,
not the pin table, owns its mutation budget.

## Closed results and runner mode migration

Every invocation returns one of five statuses: **PASS 0**, **FAIL 1**,
**ERROR 2**, **NOT_RUN 3**, or **BUDGET_EXCEEDED 4**. The result and
`./run-gate.py <lane> --json` retain the lane's raw `exit_code`; command
statuses map to PASS only at zero, and pytest 5 is FAIL with reason
`pytest-collected-no-tests`. Assay's `NO_MEASUREMENT` and `INCONCLUSIVE`
outcomes are FAIL. Use verdict plus the raw code; never infer that run-gate
itself returned an ERROR from a raw lane code of 2.

### Init reaping and resource events

When another tool starts run-gate inside a container, give it an init reaper.
Without one, run-gate refuses to start as PID 1. A Docker launcher adds
`--init` to its existing `docker run` options. In Compose, add `init: true`
to the existing service while keeping its mounts and cgroup placement:

```yaml
services:
  test-runner:
    image: tester-unified:local
    init: true
    # Keep the service's existing cgroup_parent, mounts and command here.
```

For each real lane, run-gate compares the `pids.events` `max` and
`memory.events` `oom_kill` counters in the cgroup containing the run-gate
process. A counter increase forces ERROR/2 even when the raw lane command
returns zero; `--json` retains that raw status in `exit_code`. If the cgroup
path or counters cannot be read, run-gate returns an infrastructure ERROR
instead of certifying an unobserved run, retaining any raw status already
known. `--dry-run` does not read or change these counters. The read-only
`--version` operation remains available when run-gate is PID 1.

An older config can be migrated without reserializing its TOML or dropping
comments:

```console
./run-gate.py migrate-modes run-gate.toml
./run-gate.py migrate-modes run-gate.root.toml  # if present
```

The command edits one file per call. Review its diff and make each environment
mode explicit: `ephemeral` starts a fresh image, `exec` targets an external
persistent runner, and `host` runs a subprocess on the invoking host. Names
do not select behavior. This config migration does not rewrite wrappers; audit
callers that branch on raw command exit codes:

- nyxloom `[gates.*]` pointers that only distinguish zero from non-zero keep
  their meaning;
- dstdns `gate-slot.sh` must stop treating a lane's old raw status as the
  semaphore's exit-75 signal (RG-80 removes that wrapper); review its
  `gate-base.sh` and any scripts that inspect nested run-gate statuses;
- review CI workflow shell branches and `cmru tester-gate` status handling;
- reclassify tests that asserted raw command passthrough to assert both the
  closed verdict and retained raw `exit_code`.

The migration and wrapper audit are part of one consumer cutover. A script
that wants to distinguish a run-gate refusal should use the closed outer
status; a shell conjunction still collapses a nested refusal into its own
non-zero command result until it moves to a native sequence lane.

Environment facts resolution order (no silent fallbacks anywhere):
`cgroup_slice` declared on the environment → the variable named by
`cgroup_slice_env` → `$CGROUP_PARENT_DEV_GATES` (hard error if the selected
source is absent); physical repo root DERIVED from `/proc/self/mountinfo`;
where systemd is reachable, require `LoadState=loaded` and a non-empty
`FragmentPath`. An auto-created typo slice can report loaded without an
installed unit file, so LoadState alone is insufficient. Container lanes
rely on the outer gate launcher for host-side placement verification and
dual-mount (physical + namespace views) the judged worktree and the git
common dir for worktree gitfiles; outside the devcontainer namespace — where
no second view is derivable — the lane refuses rather than silently mounting
once; declare the alias with `$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'`
(host side must equal the repo root).

**What an ephemeral container sees (RG-86).** Only the worktree under test
and the git metadata it needs — never the main checkout or any other
worktree, so another checkout's git-ignored files (secrets, local configs)
are not visible. Concretely, for a linked worktree: the worktree itself
(ALL of its own files, git-ignored overlays and rendered files such as
`ciu.rendered/`, `ciu.secret-copy.*`, `ciu.env` included — ciu worktree
instances rely on this), the git common dir read-write (assay's repository
snapshot runs `git worktree add`; ciu v8 SPEC S16.4.9), `<repo>/.run-gate`
for assay lanes without a declared `state_root`, and whatever the operator
adds through `RUN_GATE_EXTRA_MOUNTS`. `<common>/config` inside the container
is a per-run copy with credentials removed (URL userinfo, `credential.*`,
`*.extraheader`, password/token keys, credential-bearing `url.*.insteadOf`),
so a remote URL that embeds a token on the host reads without it in the
container; `git status`, `log`, `diff` and `rev-parse` work as usual.

**Main checkout.** An ephemeral lane or probe whose judged tree is a plain
checkout (not a linked worktree) is REFUSED: the whole checkout, including
every git-ignored file, would be visible. Judge a worktree (`--worktree`), or
opt in explicitly with `--allow-main-checkout` / `RUN_GATE_ALLOW_MAIN_CHECKOUT=1`;
the opt-in WARNs, and still sanitizes the git config. `mode = "exec"` lanes
are never refused and not changed: their containers are owned by ciu or the
project's stack. Disposable CI clones (Buildkite agents) are plain checkouts:
set the opt-in in the agent environment.

> **BREAKING CHANGE — migrate if you use `mode = "exec"` (RG-23).** Early
> revisions hardcoded `MOCK_MODE` and `RUN_LIVE_TESTS` into the exec-mode
> forwarding loop; they were replaced by declarative `forward_env` with no
> migration pass. If your lane relies on either name, **it is not reaching
> your container today** — and the symptom is a false GREEN, not an error: a
> suite that skips its live tests when the flag is absent exits 0 having run
> none of them. Migrate both halves:
>
> <!-- run-gate-config -->
> ```toml
> schema_version = 1
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

### Assay lanes — projects that adopt assay (the quality partnership)

run-gate.py does the ORCHESTRATION (environment, mounts, cgroup, optional
artifact verification, clean `--init` detached run), then invokes assay; **assay
does the JUDGMENT** — its lane in `assay.toml` owns argv-under-test, coverage
floors, R-levels, changed-line policy, snapshot isolation. Two files, two
owners, no duplicated registry. Internal vbpub projects use source mode:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.ciu]
kind = "assay"
assay_lane = "ciu"                  # -> assay.toml [lanes.ciu]
environment = "tester-unified"
# No assay_command or pins: run-gate installs <selected worktree>/assay.
```

For a consumer outside this monorepo, retain the explicit immutable boundary:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.ciu]
kind = "assay"
assay_lane = "ciu"
environment = "tester-unified"
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<version>.pyz"]

[lanes.ciu.pins.assay]
version = "<version>"
sha256 = "tools/assay/assay-<version>.pyz.sha256"
```

The internal source mode is selected by omitting both fields. The source is
installed with `--no-deps` from the selected tree, so no registry or ambient
Assay installation is consulted. Run-gate uses Python isolated mode (`-I`)
for both the source check and the actual `assay.cli` command, so `PYTHONPATH`
does not select another package. Before the lane runs, it inspects the
consumer directory without executing its contents: a regular local `assay`
module or package is rejected, while a namespace-only `assay/` directory is
allowed. It then resolves the installed package spec to the selected tree's
`assay/src/assay/__init__.py`. Afterward it requires a non-empty
`assay_version` and a full Git `commit` in the verdict that matches the run
record's selected commit. Missing identity or a different commit makes the
lane ERROR. This is source identity, not an artifact digest; external
immutable mode continues to require `judge_provenance`. Run-Gate accepts full
SHA-1 and SHA-256 commit IDs here; Assay's P22 snapshot source still requires
SHA-1 object storage for high-rigor snapshot lanes.

An inflight Assay record stores whether that run launched in source or
artifact mode. Re-attachment uses the recorded mode even if `run-gate.toml`
has changed since launch; it does not reinterpret an old verdict under the
current config. If the lane has changed from Assay to another kind, it also
refuses rather than treating the old Assay result as a command result.
Inflight schema 2 adds this field, so an older schema is
refused instead of guessed. For a container-runner record, a missing or
invalid mode, or an Assay artifact path that differs from the lane's derived
verdict/progress paths, is refused before the Assay inventory Docker probe
or admission, and the record is preserved. An Assay artifact path on a
non-Assay record is also refused. A
lost container's old mode does not carry over to its fresh
replacement. A promoted follower retains the launch-time mode until its
caller parses the verdict; the history record and parsed result do not expose
that private field. For an ephemeral-container lane, after confirming the old
container has stopped, `./run-gate.py <lane> --fresh` discards that record and
starts under the current config. Host and exec lanes cannot use `--fresh`; ask
the named runner's lifecycle owner to confirm it has stopped, then remove the
stale recovery record at the path named in the refusal. On an older-schema
record marked with a non-container `runner` (such as `exec`), `--fresh` also
refuses; use that runner's lifecycle owner to confirm the run is over before
removing the record.
Before imported Assay inventory or sequence admission, Run-Gate checks the
requested lane's recovery record and every sequence node and member. If a
lane was changed to `kind = "sequence"` while its old Assay or command
container record remains, Run-Gate refuses before starting members. Ask the
prior runner's lifecycle owner to confirm it has stopped, then remove the
record at the path named in the refusal. A record owned by `exec` is also
refused before inventory or admission, including when `--fresh` was requested.

If a lane changes from an ephemeral container to host or exec while its old
container record remains, Run-Gate refuses before imported Assay inventory or
lane execution. The current mode cannot safely re-attach to or replace that
container; ask its prior lifecycle owner to confirm it has stopped, then
remove the recovery record named in the refusal.

Assay inventory and toolchain probes in ephemeral environments receive the
same configured container user and RUN_GATE_EXTRA_MOUNTS as the judged lane.
An imported command or tool mounted there can therefore be checked before the
lane starts, under the permissions it will actually have.

Division of labor, spelled out:

| concern | owner |
|---|---|
| container image, mounts, cgroup slice, env passthrough | run-gate.toml |
| external artifact pins, clean-tree refusal | run-gate.toml / assay (S18.4) |
| suite argv, coverage floors, R0/R1/R3, isolation snapshot | assay.toml |
| verdict artifact + PASS/FAIL meaning | assay |
| WHEN a lane must pass (release policy) | the project's release config (cmru) |

### Resume, progress, and durable assay state

Every assay invocation receives `--resume`,
`--progress .assay/progress-<assay_lane>.jsonl`, and `--state-dir`.
Verdict and progress files stay under the effective project directory in
`.assay/`; keep that directory git-ignored. The state directory is outside the
judged worktree so a deleted or recreated worktree can resume:

```text
<checkout owning the shared .git>/.run-gate/assay-state/<project-relative-path>/
```

For a nested project, the key includes its path relative to the checkout, so
two projects named `backend` do not share state. A project outside that
checkout uses the separate `assay-state-external/<sha256>` namespace, keyed
by its resolved absolute path. By default the state root is
`<checkout owning the shared .git>/.run-gate`. A container environment whose
durable mount is at another in-container path declares that exact path as
`state_root` in its `[environments.<name>]` table. The runner owner must mount
the corresponding durable host directory read-write before the gate runs;
run-gate never creates a root-owned directory in an exec runner's disposable
container layer. For example, if the runner mounts the checkout's `.run-gate`
at `/workspace/project/.run-gate`, declare:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.test-runner]
mode = "exec"
image = "yourproj/test-runner:local"
container_name = "yourproj-test-runner"
state_root = "/workspace/project/.run-gate"
```

For a host environment, run-gate uses the default checkout path directly;
`state_root` is only meaningful for container environments. For a bare-host
lane, create `<checkout>/.run-gate` on the host and make it writable by the
lane user before the first assay run; run-gate does not synthesize this root
as a preflight side effect. Before Assay runs, run-gate checks that the
selected root exists and is writable by the lane user, then checks the
deepest existing directory along that lane's keyed state path. This catches
an old state directory owned by a different user before Assay starts. A
missing or unwritable root or ancestor is refused
before the assay command with NOT_RUN/`state-mount`, naming the path and
applicable remedy; it is never reported as an assay FAIL. The `doctor`
command checks once per assay environment, and `--dry-run` prints the planned
probe. `state_root` is a container path, so
run-gate does not stat it in the host namespace. Pin versions below **5.2.0**
are refused before the lane starts because that is the first Assay release
with `--state-dir` (the other two flags require only 2.4.1). The run header
names the resolved state directory. See the
[mount rationale](docs/DESIGN-GUIDE.md#assay-resume-state-needs-an-environment-owned-mount)
for why run-gate does not create this root inside a runner.

### Shared assay lane imports

An external-assay consumer can pin the judge once and import the exact lane
set it exposes. `lanes = "all"` imports every reported lane; a string list
selects exact names (globs are refused). A project lane with an imported name
overrides it. The nested shape matches CIU v8's
`[testing.judge] import` contract:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.test-runner]
mode = "exec"
image = "yourproj/test-runner:local"
container_name = "yourproj-test-runner"
state_root = "/workspace/project/.run-gate"

[assay]
command = ["/opt/tester-venv/bin/python", "tools/assay/assay-7.2.0.pyz"]
import = { environment = "test-runner", lanes = "all" }

[assay.pins.assay]
version = "7.2.0"
sha256 = "tools/assay/assay-7.2.0.pyz.sha256"
```

The verifier asks `assay lanes --json` inside `test-runner`, refuses a failed
or malformed inventory, and verifies the same pin for every imported lane.
Inspect the effective names with `./run-gate.py --list`.

### Selective lane requests

Selective assay controls apply to one assay lane and are refused on command
lanes and sequences. The reuse verdict must resolve inside the judged
worktree. `--rejudge` can be repeated, and `--rejudge-outcome` selects an
Assay outcome bucket: `killed`, `survived`, `crashed`, `budget_exceeded`,
`equivalent`, or `hung`; the Assay CLI alias `error` means `crashed`.

```console
./run-gate.py mutation --reuse-from .assay/verdict-mutation.json \
  --rejudge tests/test_schema.py::test_one --rejudge-outcome survived
```

Command lanes accept per-run arguments only when `accepts_args = true` is
declared. The separator keeps request arguments out of run-gate's own option
parser, and the arguments are appended to that single command's argv:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.local]
mode = "host"

[lanes.schema]
kind = "command"
environment = "local"
argv = ["pytest"]
accepts_args = true
```

```console
./run-gate.py schema -- tests/schema/test_one.py
```

Sequences and command composites refuse request arguments because their
members own separate argv declarations.

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
each `kind = "assay"` lane needs — the resolved assay command's `lanes
--json --file assay.toml` (assay ≥ 3.2.0) run INSIDE the lane's own
environment — and then
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
> `command -v` checks execute inside the lane's own environment. `doctor`
> also probes Assay's durable state root once per assay environment, as the
> lane user; a live assay invocation probes it once per lane. `--check-env`
> does not run the state-root check. These probes are
> short-lived and read-only (`assay lanes` runs nothing; `command -v` is a
> shell builtin), they judge nothing and write nothing into your tree, and
> ephemeral ones carry `--cgroup-parent` like every container run-gate
> starts. The cost is bounded: `doctor` runs **one inventory probe per
> (environment, judge identity), one batched `command -v` probe per
> environment, and one state-root probe per assay environment**;
> `--check-env` runs only the first two; a live assay invocation adds one
> state-root probe for its lane. A project with no `kind = "assay"` lane
> starts none of these probes, and neither verb ever starts your judged
> lane. If you run `doctor` in a context where starting a container is
> unacceptable, that is the check to know about.

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

<!-- run-gate-config -->
```toml
# run-gate.toml — the orchestration half restates NOTHING
schema_version = 1
[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.cursor]
kind = "assay"
environment = "tester-unified"
assay_lane = "p129_enumeration_cursor"
   # Internal source mode: omit assay_command and pins.
```

```bash
./run-gate.py cursor --base "$(git merge-base HEAD origin/main)"
# run-gate: comparison base 4c6eb2b6… (from --base) → --request-base
```

```bash
./run-gate.py cursor          # no --base: the judged tree's own upstream
# run-gate: comparison base 4c6eb2b6… (from merge-base HEAD @{upstream}) → --request-base
```

**In a `ciu worktree` the default is the COMMIT that worktree forked from**
(RG-51). `ciu worktree create|add` records that ref as `base_ref` in
`ciu.worktree-instance.json`, and run-gate prefers it over `@{upstream}`:

```bash
./run-gate.py cursor          # no --base, inside a ciu-managed worktree
# run-gate: /w/proj/ciu.worktree-instance.json pins this tree's fork point at 9f2c1ab… (its base_ref still resolves there) — using that COMMIT instead of merge-base HEAD @{upstream} (4c6eb2b6…)
# run-gate: comparison base 9f2c1ab… (from ciu.worktree-instance.json fork point) → --request-base
```

Why this is the better default: `@{upstream}` is a REMOTE-tracking ref, so
any workflow that batches commits locally before pushing lets it drift behind
local work with nobody changing a line of config — and every changed-line
judgment against it silently widens from "this change's diff" to "everything
since the drift began" (assay's own README, "Pitfall: a `judge.base` literal
pointing at a remote-tracking ref rots silently"). The recorded `base_ref` is
normally a LOCAL branch, which does not drift that way — and what run-gate
hands the judge is not that name but the COMMIT it has just verified the
branch still forks from, so nothing can move it in between. Both the commit
taken and the `@{upstream}` ref it displaced are printed, so you can SEE the
drift.

One boundary this cannot cross: run-gate chooses the base, the judge decides
what to do with it, and assay's `resolve_base` does NOT always compute
`merge-base(base, HEAD)` — when HEAD is a merge commit it returns HEAD's
first parent and discards the supplied base entirely (assay B008). A gate run
on a merge of a sibling topic branch can therefore still judge less than you
expect, under any base, including `--base`. Tracked as `RG-54`.

**Which projects it covers in a monorepo.** run-gate looks in the judged
worktree root and then in the project dir inside it, because ciu writes the
record at that worktree's CIU root. A worktree created with
`ciu_root_offset = "."` therefore covers every project in it; one rooted at a
single project (`ciu_root_offset = "run-gate-project"`) covers that project
only — a gate run for a sibling project in the same worktree finds no record
and falls through to `@{upstream}` exactly as before.

**When a record is present but NOT used.** run-gate uses the record only when
its `branch` still matches the tree, git resolves `base_ref` to a LOCAL branch
there, `merge-base(base_ref, HEAD)` still equals the `fork_point_sha` ciu
recorded at creation, and that branch does not already contain the tree's
HEAD. Anything else is ignored with a reason on stdout:

```bash
# run-gate: ignoring /w/proj/ciu.worktree-instance.json: the shared history of 'main' and this tree has MOVED since the record was written: merge-base is now 388737cc2461, the recorded fork point is 9f2c1ab0de34. Whichever way it moved — 'main' absorbed this branch (a merge or a fast-forward), or this branch took 'main' in (a merge or a rebase), or the record describes a different tree — judging against the new merge-base could silently drop work this branch really did, so the record is spent
```

The message names the possible causes without asserting one: the SAME
inequality is produced by merging the base in or rebasing onto it, and
telling an operator to tear down a worktree for a reason that did not happen
is its own defect.

Those rules are load-bearing, not fussiness — each was found by adversarial
review as ways this feature could pass a lane that should have failed:

- `ciu worktree adopt` records the adopted checkout's **HEAD**, and
  `merge-base` against an ancestor of HEAD collapses to that commit. Run the
  gate right after an adopt and it would judge *zero* changed lines.
- A worktree whose work has been merged into its base and not torn down has a
  real, live `base_ref = "main"` that `main` has since absorbed — same
  collapse, and it was true of 4 of the 7 real ciu worktrees in this estate
  when the check was added.
- A worktree whose base has since absorbed its work and which then got one
  more commit looks healthy to every other check — `merge-base` has quietly
  moved to the pre-merge branch tip. Only comparing against the recorded
  fork point catches it.
- A branch that committed work and then REVERTED it passes every check about
  the commit graph and still produces an empty diff, which a changed-line
  floor scores as `0/0 = 100%`. No work escapes the judge there — there is
  none — but a pass on nothing reads exactly like a pass on something, so
  the record loses to a base that has something to judge.

**What it costs you.** Keeping a long-lived worktree current SPENDS the
record: merging the base INTO the branch, or rebasing onto it, moves the
shared history just as surely as merging the other way, so the gate falls
back to `@{upstream}` precisely where an operator was being diligent. That
is a loss of benefit rather than a new hazard — the fallback errs WIDE — but
it means the feature goes inert until the worktree is recreated. Nothing
available locally distinguishes that from a base that absorbed the branch;
it is the same missing fact CIU-106 exists to supply, one level further up.

A frozen id, a tag, a remote-tracking ref, a deleted branch and the tree's own
branch all fail one of the clauses, so none of them displaces `@{upstream}`.
Note this is a **safe** failure: falling back is exactly the pre-RG-51
behaviour.

**Requires ciu CIU-106.** `fork_point_sha` is recorded by `ciu worktree
create|add` only since that change, and `ciu worktree adopt` never records
one. run-gate fails CLOSED without it — a worktree created by an older ciu
keeps its previous `@{upstream}` behaviour until it is recreated, and says so
rather than guessing. What run-gate then hands the judge is the fork COMMIT
itself, not the branch name, so nothing can move it between the check and the
judge's own `merge-base`.

Nothing to configure and nothing to opt out of: `--base` still wins outright,
and an absent, unreadable, non-JSON or otherwise unusable record degrades to
the `@{upstream}` line above. A plain checkout never has this file at all.
run-gate reads the filename, never imports `ciu`.

There is **no `run-gate.toml` key** for this — run-gate DERIVES it by asking
the judge (`assay lanes --json`), so the fact has exactly one spelling. What
that costs you: an assay lane invocation now issues one short read-only
inventory probe in its environment before the judged run.

Refusals name the lane and map to the closed result table. A missing base is
NOT_RUN 3/`no-base`; malformed config or an unsupported flag is ERROR 2:

| situation | what happens |
|---|---|
| delegating lane, no `--base`, no worktree record, tree has no upstream | `lane 'cursor' delegates its comparison base; pass --base REF (worktree has no upstream)` — a guessed base is not a base |
| `--base` on a lane whose `base_source` is not `"request"` | refused, naming the value assay declared (assay would refuse it anyway; this refuses earlier and clearer) |
| `--base` on a command lane with no `{base}` token | refused — the ref could only be silently dropped |
| `--base` with a judge too old to answer (`assay lanes --json` missing) | refused, naming assay **3.2.0** (B044) as the version that carries the inventory |
| no `--base`, judge too old | **nothing changes** — the old judge keeps working exactly as before |

**Conjunction lanes propagate it** the same way they propagate `--worktree`
(RG-1): a token in the lane's own argv.

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.host]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.gate]
kind = "command"
environment = "host"
argv = ["bash", "-c",
        '''./run-gate.py --worktree {worktree} --base {base} cursor &&
           ./run-gate.py --worktree {worktree} unit''']
```

A lane carrying `{base}` resolves its ref by the same rules above — recorded
worktree fork point included — so `./run-gate.py gate` on a tree with neither
refuses instead of substituting an empty string.

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
3. **Declare the run-gate lane** in `run-gate.toml`. For an internal vbpub
   project, use source mode and omit `assay_command` and `pins`:
   <!-- run-gate-config -->
   ```toml
   schema_version = 1

   [lanes.unit]
   kind = "assay"
   assay_lane = "unit"                 # -> assay.toml [lanes.unit]
   environment = "tester-unified"
   [environments.tester-unified]
   mode = "ephemeral"
   image = "tester-unified:local"
   ```
   run-gate installs the selected worktree's `assay/` source and the verdict
   records the actual version. For a consumer outside vbpub, use the explicit
   command + pin form shown in the previous section.
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
   (`.assay/verdict-unit.json`) on success AND failure, and maps assay's
   verdict through the closed run-gate result table.
6. **Read the evidence:** `assay verify .assay/verdict-unit.json`
   re-validates the retained verdict later. Keep `.assay/` gitignored
   (adoption step 5) so yesterday's evidence never dirties today's tree.

Adopting R1/R2/R3 (coverage floors, mutation, canary) is an `assay.toml`
edit per assay's docs — the run-gate lane above does not change.

### `kind = "command"` — projects that cannot (or need not) adopt assay

The lane runs a command in the declared environment with the same
orchestration guarantees, and the command's exit status is the verdict:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.test-runner]
mode = "ephemeral"
image = "yourproj/test-runner:latest"

[lanes.suite]
kind = "command"
environment = "test-runner"
argv = ["pytest", "tests/", "-q"]
clean_tree = true
```

### Native sequences and trunk bases

Use a native sequence when each step is already a run-gate lane. The member
lanes remain independently visible, but the sequence runs them in order,
records each result, and owns one composite admission ticket. A failing member
stops the default `stop_on = "FAIL"` sequence; set `stop_on = "never"` only
when later steps are meaningful after an earlier failure.

Declare the trunk once. A merge commit at HEAD on that local branch resolves
to its first parent with source `trunk-merge-first-parent`. If HEAD on the
declared trunk is not a merge commit, a request-base sequence returns
NOT_RUN with reason `no-base` instead of guessing a diff boundary. `--base`
still explicitly overrides that derivation. run-gate resolves the base once
and passes it only to members whose assay inventory or `{base}` argv declares
that they accept one.

<!-- run-gate-config -->
```toml
schema_version = 1

[project]
trunk = "main"

[environments.host]
mode = "host"

[lanes.schema]
kind = "command"
environment = "host"
argv = ["bash", "-c", "exec tools/schema-check {base}"]
clean_tree = false

[lanes.unit]
kind = "command"
environment = "host"
argv = ["python3", "-m", "pytest", "tests/unit", "-q"]
clean_tree = false

[lanes.release]
kind = "sequence"
lanes = ["schema", "unit"]
stop_on = "FAIL"
```

The consumer points to `./run-gate.py release`. A direct invocation may pass
`--base <commit>` when it has an explicit base. The sequence summary contains
its ordered member results; `history release` and each member's history keep
their own scope.

### Legacy shell conjunctions

Some callers need one arbitrary shell pipeline rather than a list of lane
members. Keep that as a `kind = "command"` lane only when run-gate cannot
model the steps directly. A shell `&&` chain still reports its wrapper
command's result, so a nested refusal appears as FAIL 1 at the outer lane;
the log retains the nested reason. Native sequences should own registered
gate composition.

#### Keeping an arbitrary shell pipeline

Keep a `kind = "command"` wrapper only when the steps cannot be declared as
run-gate member lanes. A wrapper that calls nested lanes still reports its own
command result: any non-zero nested run becomes outer FAIL 1, while its log
retains the nested reason. `--worktree` must appear in every nested invocation
so the selected tree is preserved. `--fresh` remains an explicit per-sub-lane
choice in this shell form because run-gate cannot inspect a free-form script.

For registered lane composition, use the native sequence above. It keeps each
member's closed result and history, resolves a request base once, and holds one
composite admission ticket across serial execution.

### Consumer examples

**nyxloom `[gates.<name>]` — thin pointer only:**

```toml
[gates.test-runner]
argv = ["bash", "-lc", '''cd /workspaces/dstdns &&
    CGROUP_PARENT_DEV_GATES="${CGROUP_PARENT_DEV_GATES:?...}" &&
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

Scripting against gates: run-gate returns only PASS 0, FAIL 1, ERROR 2,
NOT_RUN 3, or BUDGET_EXCEEDED 4. A command lane's raw status remains in
the result data; ERROR 2 covers configuration and infrastructure failures,
while NOT_RUN 3 means a precondition prevented the judge from starting.

### Daemon-wide gate admission

Admission is an opt-in count cap shared by every run-gate client using one
local Docker daemon. The `[admission]` table is project-local and defaults to
disabled; do not put it in `run-gate.root.toml`. The ticket image must already
exist locally and provide `/bin/true`. run-gate probes it with `--pull=never`
and places ticket containers in the loaded `CGROUP_PARENT_DEV_GATES` slice.
Enabled lane runs and both admission verbs require a local Docker Unix
endpoint; remote Docker contexts refuse because they do not prove the shared
host daemon identity.

<!-- run-gate-config -->
```toml
schema_version = 1

[admission]
enabled = true
ticket_image = "yourproj/test-runner:local"
unreadable_policy = "refuse"
```

An operator publishes the daemon-wide cap from a checkout with this
run-gate config:

```console
./run-gate.py admission set --max-concurrent 2 --unreadable-policy refuse
./run-gate.py admission show
```

Publishing and inspection require a local Docker endpoint. `--replace` advances the
published generation; the newest readable object is the active policy. Each
enabled run creates a numbered ticket with Docker's name-uniqueness check,
then waits in ticket order. Configure `--admission-wait 15m` to change the
default ten-minute wait for one invocation. A full cap at timeout returns
NOT_RUN 3 with reason `no-headroom`. `--override-admission` is a disclosed,
per-invocation exception to a readable count cap; it still records its
ticket. When `enabled = false`, both flags are accepted and ignored with one
notice. With no readable published cap, `unreadable_policy = "refuse"`
returns NOT_RUN; `"unbudgeted"` permits the run without a ticket and says so.

Admission is local to one daemon. A remote Docker context is refused for
`admission set`; do not publish a cap on a different daemon and expect this
client to use it. Resource limits and cgroup placement continue to apply
independently of the admission switch.

### Runner occupancy status

Use `status` when a gate appears to be waiting and you need to see who owns
or is queued for the runner:

```console
./run-gate.py status
./run-gate.py status --worktree .worktrees/feature
./run-gate.py status --json
```

The selected project's `.run-gate/inflight/` records follow `--worktree` and
the config path shown in the report. The exec-lock and Docker admission views
are host-wide: run-gate maps its `/tmp/run-gate-exec-<container>.lock` files
to Linux `/proc/locks` entries, then reports holder and waiter PIDs and their
local liveness. That internal lock is authoritative for run-gate access to
the runner; a project's own wrapper lock cannot reveal runs that did not use
that wrapper. Docker admission output includes the published
`ciu-admission-<generation>` cap, decoded tickets and owner labels, queued or
running state, and exited tombstones.

The command does not start, stop, or remove containers. Its Docker reads are
limited to the admission objects, tickets, and ticket run markers. It does
not repair inflight records or reap tickets. If one source cannot be
read, human output names the failure and JSON returns the available partial
data with `verdict = "ERROR"`, `exit_code = 2` (the closed result table's
ERROR code). It never presents an unreadable source as an empty queue.

When `[admission] enabled = true`, `doctor` also checks that
`ticket_image` exists on the local daemon, a valid published admission object
exists, and its positive `max_concurrent` value is readable. This preflight
uses `docker image inspect` only; it does not run the image. Its remedies are
specific: load the configured image or name a local one, publish a cap with
`run-gate admission set --max-concurrent <desired-N>`, or republish an
unreadable cap with `--replace`. If Docker shows an admission object whose
identity labels are unreadable, doctor names that object and reports that
`admission set` refuses to replace it. Resolve that exact object with the
daemon owner before publishing another cap; doctor will not suggest a
command that cannot succeed safely.

### Consumer timeouts must not cut lanes short

A consumer `timeout_seconds` tighter than the paired lane's `budget`
stops the lane before its declared wall-clock bound and therefore becomes
the effective limit. The rule remains **consumer timeout >= lane budget**
(wider is fine), so run-gate can enforce its hard budget and preserve the
expected resumable outcome. The estate sweep in
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
  run-gate's own container lanes are fine (they dual-mount the worktree and
  the git common dir), but
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
dual-mounts the judged worktree AND the git common dir (RG-86), so the gitdir
is inside the mounts by construction.
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
>
> **BREAKING CHANGE (load-time) — a lane named `footprint` (RG-55, rev 10).**
> Same shape, one more verb: `footprint` joined the reserved set at rev 10.
> No project in this estate declares one either; a copied-script repo with a
> lane by that name must rename it before adopting rev 10 or later.

```
run-gate: WARNING: lane history not recorded: /repo/proj/.run-gate is not
fully git-ignored, and writing there would leave the judged tree dirty for the
NEXT lane's clean-tree check — add '.run-gate/' to the .gitignore covering /repo
```

The lane verdict and mapped exit status are untouched either way: telemetry
is a note in the margin, never the product. The vbpub root `.gitignore` already
carries the entry for internal projects; a **copied-script repo must add it**.

Optionally declare how much trend to keep (default 10 commits per lane; a
central `run-gate.root.toml` may declare it once and a project shadows it whole,
the R-09 rule):

<!-- run-gate-config -->
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

**`--json` is honored by `history` and `footprint` (RG-55) only.** Every
other verb refuses it by name rather than printing its human form anyway
(`--list` is already a
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

### The footprint manifest — a committed budget (RG-55)

`history` answers "what did this lane cost, in THIS checkout's own store" —
per-instance telemetry, gitignored, never committed. `footprint` answers a
different question: "what SHOULD this lane cost, as a number the whole
project can commit and carry forward." It distills the SAME history (PASS +
history-eligible entries that were actually profiled — peak memory,
+baseline, hot-set p90, CPU cores, memory-full stall, alongside duration)
into `run-gate.footprint.json`, next to `run-gate.toml`, and that file is
**TRACKED — commit it** (`.run-gate/` stays ignored; the two are opposite
answers to "does this belong in git" for a reason: one is this checkout's
raw log, the other is the distilled number everyone downstream should see).

Captured from a real, clean-tree, profiled `selftest` PASS on this project
itself (RG-55's own "consequence to exploit": run-gate-project's own lanes
are all bare-host, so RG-57 makes this project self-hosting for RG-61's
own transcript) — a `rusage`-sourced entry, since no `cgprofile-host-daemon`
was reachable in this environment. Verbatim (including the `.run-gate/`
store path, which is checkout-relative — this capture ran from the RG-55
wave's own `rg55-followups-run-gate` worktree, not `run-gate-project/`
directly; the table's own shape is the part that generalizes):

```console
$ ./run-gate.py footprint --write
run-gate rev 42 — lane resource footprint
store: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/.run-gate/history.json
manifest written: /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project/run-gate.footprint.json
  LANE                 RUNS  PEAK(med/max)            +BASE    HOT p90   CORES    STALL   DURATION
  assay-r1                4    266 MiB/289 MiB            -          -    0.61        -     179.5s [source: rusage-maxrss]
  assay-r3                5    198 MiB/201 MiB            -          -    0.74        -      15.9s [source: rusage-maxrss]
  selftest                5    285 MiB/286 MiB            -          -    0.56        -     172.3s [source: rusage-maxrss]
```

(Recaptured RW-51/session 6, round-2 review's own S6 finding: the PRIOR
capture above was one generation stale — its `assay-r1`/`assay-r3` rows
were distilled partly from PRE-repair, pre-`wait4` runs, so they carried
`"peak_at_floor": null` (unknown, because those older records predate the
`floor_bytes` field entirely) even though the surrounding prose claimed
every row was known not to be floor-bound. This capture's `--write` ran
after a fresh, clean-tree `selftest` PASS on the CURRENT tip, so every
contributing entry for all three lanes is `wait4`-era (RW-43/B1) with a
REAL, non-null `peak_at_floor`: all three medians above are honest
`os.wait4()` numbers and none of the three is floor-bound
(`"peak_at_floor": false` for `assay-r1`, `assay-r3`, and `selftest`
alike — `LANE-AUTHORING.md`'s own footprint-budgeting section shows what
a floor-bound entry looks like instead). The manifest's `meta.expected`
consumers get from this also now carries `"source": "rusage-maxrss"`
alongside the medians, per contract Sec 3a (B5, round-2 review).)

A lane with no profiled PASS run yet is simply OMITTED (absent means
unknown, never zero) — `--write` REFUSES outright (exit 2, naming why) when
**no** lane in the store qualifies yet; run a profiled lane first, then
`--write` again. A `LANE` filter queries one lane's numbers without
touching disk, but is REFUSED together with `--write` (a partial write
would silently drop every other lane's committed data).

**What the manifest is FOR, downstream of this package:**

- **`doctor`** reads it: a per-lane WARNING when the LIVE history's median
  peak has drifted more than `[footprint] tolerance_pct` (default 25%) from
  the committed number, and one WARNING when `distilled_at` is older than
  `[footprint] max_age_days` (default 30) — "this number nobody has
  re-measured in a while" is a fact worth surfacing, never a failure.
- **The run path** reads it too: the daemon's `--meta` carries this lane's
  own committed median as `expected` (so the daemon, and a human reading a
  session's own report, can see "measured vs. expected" without a second
  lookup), and the footprint disclosure line prints a `| manifest <n> MiB`
  tail next to the run's own peak and the standing history median — three
  numbers, one line: what just happened, what has been typical, and what
  was budgeted.
- **RG-56 (a later wave, not this one)** is where a footprint becomes an
  ADMISSION signal — scheduling concurrent lanes against a slice's real
  remaining budget instead of a guess. This package only measures and
  commits the number; nothing here decides policy on it yet.

### Failure evidence and selective footprint updates

When an assay lane returns a non-PASS verdict, run-gate prints a short digest
with the verdict, up to five failing node IDs and exception classes, and the
pytest summary line when it can read one. It copies the current verdict and
progress file before a rerun can overwrite them:

```text
<checkout>/.run-gate/failed/<lane>/<run_id>/verdict.json
<checkout>/.run-gate/failed/<lane>/<run_id>/progress.jsonl
```

The archive is git-ignored and retains the latest ten runs per lane. The
history record names the archive when files were copied. A missing or
non-ignored archive root is disclosed as a warning rather than writing
tracked evidence into the judged tree.

For a budget estimate, a completed profiled FAIL (such as an assay mutation
lane with surviving candidates) is a valid duration and resource sample. It
is excluded by default; include it explicitly:

```console
./run-gate.py footprint mutation --include-failed
./run-gate.py footprint --write --include-failed --lane mutation
```

`--lane` is repeatable. A selective write requires an existing schema-1
manifest, replaces only the named lane entries, and preserves every other
entry. The selected lanes still need completed, eligible, profiled history;
aborted, dirty, unprofiled, and budget-stopped runs are not measurements.

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

**Worktree config and runner follow the JUDGED TREE (RG-47/RG-65/RG-79).**
When invoking from a main checkout with `--worktree PATH`, run-gate derives
the project directory as `PATH` plus that project's path relative to the Git
toplevel. It loads `run-gate.toml` and the nearest `run-gate.root.toml` from
that tree. The target project config must exist; otherwise the invocation
refuses instead of using the main checkout's lanes. The header prints the
selected project config path, and a run's history record keeps its path and
SHA-256 (plus the central config path/hash when used).

For an exec lane whose name is derived from CIU, run-gate reads only the
judged worktree's rendered `<worktree>/ciu.global.toml`. If your worktrees
get their own isolated stacks (`ciu worktree create` — each with its own
rendered config, network and `test-runner`), a missing file refuses with a
remedy to start that worktree's runner using
`ciu up --dir <test-runner stack> --deploy --healthcheck`. This remedy
requires CIU 7.15.2 or newer (CIU-124). A stopped runner gets the same remedy
before `docker exec`. run-gate never falls back to
`<repo>/ciu.global.toml`, because that file names the main checkout's
landscape. Do NOT work around a wrong container by pinning `container_name`
in the tracked `run-gate.toml`: that literal is correct for exactly one
running instance and wrong for the next worktree created. Verify which config
decided the lane and runner — the pre-execution disclosure names both:

```
$ ./run-gate.py test-runner --worktree /repo/.worktrees/p147b
run-gate: config: /repo/.worktrees/p147b/run-gate.toml
run-gate: rev 47 | lane test-runner | env project /repo/.worktrees/p147b/run-gate.toml |
  container p147b-8a6bc3-test-runner (ciu.global.toml
  deploy.project_name+environment_tag (judged worktree:
  /repo/.worktrees/p147b/ciu.global.toml)) | slice … (…)
```

A missing or stale `<worktree>/ciu.global.toml` means this worktree's runner
has not been rendered or started — run the worktree's CIU up command, don't
declare a main-checkout name.
Set `$RUN_GATE_EXTRA_MOUNTS=/var/run/docker.sock=/var/run/docker.sock` when a
lane needs Docker-in-Docker. Keep `assay.toml` lanes for the whole-target
coverage work as they land (B1-style).
The implementation gate is declared as a `gate` conjunction lane (see above);
nyxloom consumes it via `./run-gate.py gate --worktree {worktree}`.

<!-- run-gate-config -->
```toml
schema_version = 1
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

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.host]
mode = "ephemeral"
image = "yourproj/host-tools:latest"

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

Parser help, usage, and argument errors start with `RUN-GATE rev <revision> — per-project gate entrypoint`. Preserve that line in gate diagnostics; the revision is read from the shipped script's `__revision__` value.
Before selecting a lane, a consumer can run `./run-gate.py --version`. It
prints exactly one `run-gate rev <revision>` line on stdout, exits 0 without
loading project configuration, and writes nothing to stderr. The revision is
the script copy-drift marker; the
wheel's SemVer is a separate distribution identity.
