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
   dir. At P01 build time environments only (`[lanes.*]` there was
   rejected) — **superseded by RG-16 (`R-22`)**: central `[lanes.*]` are
   legal shared lanes every consuming project inherits BY NAME. Project
   tables shadow a central name entirely (auditable override, no field
   merging).
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
./run-gate.py <lane>          # run one gate lane
./run-gate.py <lane> --base REF   # comparison base for a lane that delegates it
./run-gate.py doctor          # preflight: docker, slices, git, images, assay toolchains
./run-gate.py history [LANE]  # what each lane last did + what it typically costs
./run-gate.py history --json  #   … the same, machine-readable
./run-gate.py --list          # machine-readable lane inventory (for CI fan-out)
./run-gate.py --help          # usage(), incl. the tool revision
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

The split is kept honest by DERIVING assay facts instead of restating them.
run-gate asks the judge (`assay lanes --json`, assay ≥ 3.2.0) two questions
before it runs a lane: **what toolchain does this lane need from its
environment** (RG-25 — reported by `doctor`/`--check-env` instead of
surfacing as a mid-run `MISSING_EXTERNAL_TOOL`) and **does this lane take its
comparison base from the gate** (RG-26 — `judge.base_source = "request"`,
supplied with `./run-gate.py <lane> --base REF`). Neither becomes a
`run-gate.toml` key: a second spelling of a fact `assay.toml` already owns is
the drift this design exists to remove.

Asking has a price, stated rather than hidden: those questions are answered
INSIDE the lane's environment, so `doctor`, `--check-env`, and any assay-lane
invocation (`--dry-run` included) start short read-only probe containers —
one inventory probe per environment+judge, plus one batched `command -v`
probe per environment for the fitness check. They judge nothing, write
nothing, and never start your judged lane; a project with no
`kind = "assay"` lane starts none of them.

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
  paths so worktree gitfiles resolve — when no alias is derivable (bare
  host, `phys == repo`) the lane refuses instead of silently collapsing to
  one mount; declare the second view via
  `$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'`;
  `git config --global safe.directory '*'`
  inside the gate container.
- **Artifact pins:** sha256 verification executed FROM the pin file's
  directory (`cd <dir> && sha256sum -c <pin>`), fail-closed; a declared
  `version` is a claim the artifact must satisfy — the lane probes
  `<assay_command> --version` and refuses mismatches (no provenance theater).
- **Env forwarding is declared, never implicit (RG-23):** a container/exec
  lane forwards `$CGROUP_PARENT_DEV_BACKGROUND` (the tool's own
  infrastructure) plus exactly the environment's `forward_env` list. The
  early hardcoded `MOCK_MODE`/`RUN_LIVE_TESTS` pair is GONE — consumers
  relying on it must migrate (CONSUMERS.md "BREAKING CHANGE"), because its
  absence produces a false GREEN, not an error. `--check-env` sweeps the
  project's Python for env reads no lane forwards or requires, including
  reads wrapped in the project's own helper functions.
- **Clean tree:** refuse a dirty judged tree by default (assay lanes get this
  from assay; command lanes get it from the tool) — a gate over uncommitted
  state is not evidence.
- **Effective tree:** `--worktree` doesn't just redirect checks — the lane
  EXECUTES in the selected tree (assay cd, pin verification, artifacts,
  host-lane cwd relocate; SPEC R-21). Judging checkout A while pointed at
  worktree B is the silent false-PASS class this kills.
- **Run form:** detached container + wait + logs (survives terminal loss);
  the gate's exit status is the judged job's own — no wrapper/pipe masking.
  Tool-level refusals reserve exit 2 (configuration/refusal) vs 3
  (infrastructure) so scripts never parse prose to tell them apart.
- **Gate-safe paths:** `{worktree}` is substituted textually into consumer
  shell strings, so a judged tree at a path with whitespace or shell
  metacharacters is refused up front (every lane kind) instead of
  word-splitting or executing downstream.
- **Verdict discipline:** print WHERE the verdict artifact lives; never bury
  it in a stream a consumer might truncate.
- **Lane cost is measured, not remembered (RG-27):** every run leaves a
  record in a per-(judged worktree × project) `.run-gate/history.json` — a
  `latest` slot holding the most recent invocation whatever happened to it,
  and a bounded per-commit trend series that only trustworthy measurements
  join (completed, clean tree, no rebase in flight, real commit). Read it
  with `./run-gate.py history [LANE] [--json]`. run-gate MEASURES; the
  rigor/defer policy built on the numbers belongs to whoever reads them
  (CONSUMERS.md "What each lane costs"; SPEC `R-36`).

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
