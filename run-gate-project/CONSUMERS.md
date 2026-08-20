# run-gate — consumer & adoption guide

How a project adopts `run-gate.py`, how partner tools plug in, and what the
lane declarations look like per project type. Companion to `README.md`
(design authority). Everything here is DESIGNED, pending the P01 build.

## The adoption steps (any project)

1. **Get the script.**
   - vbpub-internal: `ln -s ../run-gate-project/run-gate.py run-gate.py`
     at the project root (relative symlink, committed).
   - external repo (dstdns, groop): copy the file to the project root,
     commit it. The in-file `__revision__` is your drift marker — estate
     sweeps compare it; update by re-copying.
2. **Declare lanes** in `run-gate.toml` next to it (schema below — parsed by
   run-gate.py ONLY; no other tool may read this file).
3. **Point consumers at argv.**
   - nyxloom: `[gates.<name>] argv = ["./run-gate.py", "<lane>"]` — nothing
     else in the argv. Keep `asserts`/`timeout_seconds` as the daemon's own
     policy.
   - cmru: the release-gate step becomes the same argv.
   - CI (Buildkite, later): one step per lane from `./run-gate.py --list`.
   - Humans/agents: `./run-gate.py <lane>` — no recipe, no traps.
4. **AGENTS.md**: add one line naming `./run-gate.py` as the canonical test
   entrypoint (do this IN the adoption commit — docs never lead the tool).

## Lane kinds

```toml
# run-gate.toml — parsed by run-gate.py only
schema_version = 1

[lanes.<name>]
kind = "assay" | "command"
environment = "tester-unified" | "test-runner" | "host" | "<image:tag>"
budget = "20m"                      # advisory wall-clock; consumers may enforce
```

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
[lanes.ciu.pins]
assay = { version = "2.1.0" }       # verified against the judge the image carries
```

Division of labor, spelled out:

| concern | owner |
|---|---|
| container image, mounts, cgroup slice, env passthrough | run-gate.toml |
| artifact pins (assay version/sha), clean-tree refusal | run-gate.toml / assay (S18.4) |
| suite argv, coverage floors, R0/R1/R3, isolation snapshot | assay.toml |
| verdict artifact + PASS/FAIL meaning | assay |
| WHEN a lane must pass (release policy) | the project's release config (cmru) |

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

## Per-project-type recipes

**Python service repo with assay (ciu, cmru, assay itself, nyxloom):** the
`kind="assay"` shape above. First adopter: **ciu** (HANDOFF-P01) — its
current `nyxloom.toml [gates.tester-unified]` argv (the docker/cgroup/sha
incantation) moves INTO the tool and the gate entry becomes two tokens.

**Python app estate with its own runner (dstdns):** dstdns's gate is
`./scripts/testing-exec.sh` into its `test-runner` container with schema-gate
pre-step and flock serialization. Adoption = copy the script, declare
`kind="command"` lanes wrapping the EXISTING testing-exec path (flock included
as orchestration), keep `assay.toml` lanes for the whole-target coverage work
as they land (B1-style). Timed per D-111: at dstdns's next gate change, not as
a standalone package.

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
- **nyxloom:** gates become thin argv pointers; the daemon keeps scheduling,
  timeouts, and asserts. The four-trap manual recipe in vbpub AGENTS.md is
  superseded for adopted projects (the section gains a pointer here).
- **cmru:** release gates call the same lanes; cmru's dependency checking is
  what makes the fresh-clone story work for the image-baked assay judge
  (build order: assay wheel → tester-unified image → gates run).
- **Buildkite (future):** agents on the remote hosts run long lanes
  (mutation, fuzz) via the identical entrypoint; `--list` output is the
  pipeline generator's input. Design note lives in assay B009.

## Anti-goals (read before extending)

- NO second parser of `run-gate.toml`, ever. A consumer that wants lane
  metadata calls `./run-gate.py --list` (stable, machine-readable output).
- NO judgment policy in `run-gate.toml` — floors and rigor belong to assay.
- NO non-stdlib imports in run-gate.py — the launcher must run on a fresh
  clone with zero installs.
- NO silent defaults for environment facts (slice names, physical paths):
  DERIVE or READ or FAIL, per AGENTS §4.2a.
