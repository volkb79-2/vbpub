# run-gate — the per-project gate entrypoint

**Status:** BUILT (P01, 2026-08-22). First consumer: **nyxloom** (controller
amendment A1 — ciu was under parallel development and is DEFERRED; its assay
lane ships construction-tested with live proof at its adoption). This README
remains the design authority; **`SPEC.md` is the normative implementation
contract** the code and tests adhere to (`__revision__` in `run-gate.py`
tracks it). Build-and-adopt handoff: `HANDOFF-P01-build-and-adopt-ciu.md`;
chronological build log incl. every failure:
`HANDOFF-P01-build-and-adopt-ciu-LOG.md`.

## Built deltas vs this design (all recorded, none silent)

The build stayed faithful to the intent; four things crystallized differently
than the prose predicted (full rationale in `SPEC.md` §8 and the LOG):

1. **Central defaults (controller A2):** shared environment facts live in a
   repo-root `run-gate.toml` — the NEAREST STRICT ANCESTOR of the project
   dir. Environments only; `[lanes.*]` there is rejected. Project tables
   shadow a central name entirely (auditable override, no field merging).
2. **Config discovery:** the project config is found next to the INVOKED
   script path WITHOUT resolving symlinks (a symlink's parent is the
   project), CWD as fallback. CWD-first (the handoff's wording) breaks
   `nyxloom/run-gate.py --list` from the repo root.
3. **Slice policy (controller A3):** `$CGROUP_PARENT_DEV_BACKGROUND`
   ambient resolution is the default; `cgroup_slice` may be DECLARED on an
   environment as explicit policy (LoadState-verified where systemd is
   reachable). nyxloom's dev gate migrated OFF its hardcoded
   `nyxloom-gates.slice` literal (prod-instance intent).
4. **Lane schema final:** `memory` (docker `--memory`, per-lane RAM
   overrides), `clean_tree` (default TRUE — refusals are the doctrine;
   nyxloom adopts `false` explicitly until NL-1), `assay_command` REQUIRED
   and explicit (the tool never invents an assay invocation), `budget`
   advisory-only.

## Intent — what problem this solves

Every project in this estate has a gate: the command whose green verdict means
"this change may ship". Today the *runnable mechanics* of that gate — the
`docker run` line, the cgroup slice, the mount pairs, the artifact-pin
verification, the clean-tree requirement — live in the **consumer's** config
(nyxloom's `nyxloom.toml [gates]` argv strings, cmru's `cmru.toml` step lists),
duplicated per consumer and per project, executed by machinery the project
itself never tests.

The measured cost of that arrangement, from a single review day (2026-08-20,
ciu checkpoint P07):

1. The committed gate argv had **never executed end-to-end** — it was validated
   with a substitute interpreter — and carried **three defects**, each invisible
   to a green 100%-coverage suite:
   - a missing `-e CGROUP_PARENT_DEV_BACKGROUND` (the image doesn't bake the
     var; env-passthrough cannot pass what does not exist),
   - an unconditional `systemctl` LoadState check that can never pass in a
     containerized context (the devcontainer ships a *shim* systemctl that
     exits 0 with advisory stdout),
   - `sha256sum -c` resolving the pin's bare filename against the wrong CWD.
2. The previous checkpoint (P06) had the same failure class: a `docker exec`
   argv pinned against a *fake* docker that real docker rejects (`--` executed
   as the in-container command, exit 127).
3. Manual gate runs require a four-trap recipe (cgroup env passthrough,
   dual-path mounts for worktree gitfiles, `safe.directory`, detached run form)
   that lives in AGENTS.md prose and is re-derived by every human who needs it.

**Root cause, stated once:** invocation mechanics that live in config strings
are code that nothing tests and only one consumer exercises. An argv proves
construction, not acceptance.

**The fix:** ONE small, tested program — `run-gate.py` — owned per project,
visible at the project root, carrying ALL of the mechanics. Every consumer
(nyxloomd, cmru, Buildkite, a CLI agent, a human) runs the same file:

```
./run-gate.py <lane>        # run one gate lane
./run-gate.py --list        # machine-readable lane inventory (for CI fan-out)
./run-gate.py --help        # usage(), incl. the tool revision
```

A defect fixed in the tool is fixed for every consumer at once, and the
four-trap recipe becomes executable code with its own test suite instead of
doctrine prose.

## Design

### One parser, argv for everyone (the tar-pit line)

The estate explicitly REJECTED a neutral `gates.toml` meta-schema that all
consumers parse (D-110): N parsers of a shared format is a meta-CI system —
schema drift, per-consumer bugs, governance forever. What we build instead:

- **`run-gate.py`** — the ONLY parser. A shared tool, developed here.
- **`run-gate.toml`** — per project, next to the script. Declarative lane
  definitions read by run-gate.py alone. No other program ever parses it.
- **Consumers speak argv.** nyxloom's `[gates]` entry shrinks to
  `argv = ["./run-gate.py", "<lane>"]`. Buildkite steps, cmru release gates,
  and humans use the identical invocation.

Same TOML bytes as the rejected design — completely different maintenance
surface, because exactly one program owns the schema (the
`pyproject.toml`/`assay.toml` pattern).

### Orchestration vs judgment — the assay split

`run-gate.toml` owns **orchestration**: which environment (container image,
cgroup slice, mounts), which pins to verify, clean-tree policy, budgets, and
the command to run. It does NOT own quality **judgment**.

For projects that adopt **assay**, judgment (coverage floors, R-levels,
changed-line policy, isolation snapshots) stays in `assay.toml`, and the
`run-gate.toml` lane is a thin wrapper referencing the assay lane by name:

```toml
[lanes.ciu]
kind = "assay"            # run-gate wraps: env setup + pin verify + `assay run ciu`
assay_lane = "ciu"        # judgment policy lives in assay.toml — one registry each
environment = "tester-unified"
```

No duplicate lane registry: `run-gate.toml` = where/how it runs,
`assay.toml` = what counts as passing. Projects that cannot adopt assay
declare `kind = "command"` lanes and get everything except assay's judgment.

### Environment mechanics the tool must own (the hard-won list)

These are the exact behaviors whose absence caused measured failures; they are
the tool's reason to exist and MUST be implemented + tested:

- **Cgroup placement:** resolve the slice ONLY from
  `$CGROUP_PARENT_DEV_BACKGROUND` (no literal, no fallback — absent is a hard
  error, AGENTS §4.2a), pass it BOTH as `--cgroup-parent` AND `-e` into the
  container (suites read it ambiently). LoadState pre-check ONLY where systemd
  is reachable (`[ -d /run/systemd/system ]`) — containerized contexts skip it.
- **Path namespaces:** derive the physical repo root from
  `/proc/self/mountinfo` (the bind mount whose mount point contains the repo;
  cmru `tester-gate` precedent — never from `ciu.env`, whose generated values
  have been observed stale); dual-mount physical AND devcontainer
  paths so worktree gitfiles resolve; `git config --global safe.directory '*'`
  inside the gate container.
- **Artifact pins:** sha256 verification executed FROM the pin file's
  directory (`cd <dir> && sha256sum -c <pin>`), fail-closed.
- **Clean tree:** refuse a dirty judged tree by default (assay lanes get this
  from assay; command lanes get it from the tool) — a gate over uncommitted
  state is not evidence.
- **Run form:** detached container + wait + logs (survives terminal loss);
  the gate's exit status is the judged job's own — no wrapper/pipe masking.
- **Verdict discipline:** print WHERE the verdict artifact lives; never bury
  it in a stream a consumer might truncate.

### Distribution — symlink inside vbpub, copy outside

The tool is a **mini-project of the vbpub monorepo** (this directory): its own
tests, its own revision discipline (`__revision__ = N` in-file, printed by
`--help`). Consumption:

- **vbpub-internal projects** (ciu, cmru, assay, nyxloom, tester-unified):
  `run-gate.py` at the project root is a **relative symlink** to
  `../run-gate-project/run-gate.py`. One edit updates all; git tracks symlinks.
- **External repos** (dstdns, groop): **copy** the file in; the in-file
  `__revision__` is the drift detector (estate sweeps compare it against this
  directory's source of truth). A cross-repo symlink dies on fresh clone.
- **Why not a pip wheel:** chick-egg. A fresh clone of any repo must be able
  to run its gate with zero release artifacts available (same reasoning that
  moved assay from per-repo vendored pyz blobs to being baked into the
  tester-unified image, built from in-repo source via the cmru dependency
  chain — D-110.2). A single-file stdlib-only script needs no install step.

Consequence: **run-gate.py is stdlib-only** (tomllib, argparse, subprocess,
hashlib, pathlib). Any richer dependency belongs in the environments it
launches, not in the launcher.

### Future: async long lanes

Mutation campaigns and fuzzing become additional `run-gate.toml` lanes with
large budgets, executed asynchronously by Buildkite agents on remote hosts —
which call the SAME `./run-gate.py <lane>`. nyxloomd, the operator, and a
controller session are equal triggers. (Backlogged: assay B009 forward note;
carved after the dstdns config-cutover.)

## Decision trail

- dstdns `nyxloom-trove/decisions.md` **D-110** (gate layering, assay
  distribution, assay.toml role, async lanes) and **D-111** (+amendment:
  run-gate.py/run-gate.toml naming, this home, one-parser rationale,
  distribution model).
- ciu backlog **CIU-40** (build + first adoption), assay backlog **B009**
  (assay.toml role docs + image-baked distribution), assay **B008** (the
  merge-tip R1 base-resolution finding from the same review day).
- vbpub `AGENTS.md` "Manual tester-unified gate runs — the four traps" — the
  prose this tool turns into tested code (the section gains a pointer here
  when the tool ships, and per-project AGENTS.md name `run-gate.py` as the
  canonical starting point IN the adoption carve, never before).
