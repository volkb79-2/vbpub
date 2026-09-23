# Consuming cmru — making your product releasable

This is the **HOW** for adopting cmru. The **WHAT** is [`../README.md`](../README.md); the
normative **WHY** is [`SPEC.md`](SPEC.md) (the `S-…` clause IDs cited below). If you are not
sure cmru is even the right tool for an artifact, read the border question first:
[`../../docs/ciu-vs-cmru.md`](../../docs/ciu-vs-cmru.md) — *is this artifact published for
external consumption? yes → cmru; no → ciu.*

cmru versions, tags, builds, and publishes **independently-versioned products that share one
GitHub Releases page**. You make a product releasable by giving it a portable `cmru.toml` and
one entry in the nearest CMRU root's `cmru.orchestration.toml`. cmru owns the generic source mechanics
(isolated worktrees, generated history, tags); your project owns every release *phase* command
explicitly.

Before selecting a release verb, a consumer can inspect the installed build
with this pasteable probe:

    cmru --version

It prints one `cmru <version>` line on stdout and exits 0. The equivalent native
verb is `cmru version`. Help, usage, and configuration diagnostics from
`cmru`, `cmru-agent`, and `cmru-controller`, including nested verbs, begin with
their CMRU headline as line 1; normal command output is unchanged.
The documented `python3 -m cmru.handlers` calls are explicit project-step
library adapters rather than a separately versioned operator entrypoint, so
they are outside this top-level identity surface.

---

## 1. The two files

A product is releasable when two files exist. Nothing is auto-discovered beyond them.

**`<project>/cmru.toml`** — travels with the product; its complete release/build/test/publish
contract. In an orchestrated estate, repository and registry facts live in the central file
below. Secrets never live here. Minimal wheel example (see
[`../../cmru.project.sample.toml`](../../cmru.project.sample.toml) for the annotated original):

```toml
schema_version = 1

[runtime]
kind = "none" # use "ciu" only when the project owns declared CIU roots

[project]
id = "example-wheel"
description = "Example wheel project"
prefix = "example-wheel-v"        # the tag prefix cmru owns; SemVer follows it
artifacts = ["wheel"]             # an output INVENTORY, not a behaviour switch
template_revision = 4

[project.version]
strategy = "scm"
bump = "conventional"             # version derived from Conventional Commits since the last tag

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
# Add only outputs the release gate actually writes; these are retained separately
# from publishable artifacts and bound to the gated commit.
# evidence_paths = ["coverage.json"]

# Every phase is project-owned and explicit. The gate always runs through tester-unified.
[steps.run-tests]
quiet = true
commands = [
  { label = "gate", argv = ["cmru", "tester-gate", "--cwd", ".", "--", "/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"], cwd = "." },
]

[steps.build]
quiet = true
commands = [
  { label = "build wheel", argv = ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."], cwd = "." },
]

[steps.push]
quiet = true
commands = [
  { label = "publish wheel", argv = ["python3", "-m", "cmru.handlers", "wheel-publish", "--prefix", "example-wheel", "--cwd", ".", "--notes-env", "EXAMPLE_RELEASE_NOTES"], cwd = "." },
]
```

The runtime declaration is mandatory and closed. Paste `kind = "none"` for a
self-contained project step; use `kind = "ciu"` when the step deliberately
uses CIU's isolated workspace/runtime adapter. CMRU passes
`CMRU_WORKSPACE_ID`, `CMRU_WORKSPACE_PATH`, and `CMRU_SOURCE_GIT_ROOT` to the
step, so its evidence can name the candidate that produced it. It never
guesses from a `docker` command.

**`<cmru-root>/cmru.orchestration.toml`** — central coordination; project commands remain in
project files. The nearest file found while walking ancestors establishes the CMRU root and may
serve several repositories below it. Each entry points at a portable project-local
`cmru.toml` (see [`../../cmru.orchestration.sample.toml`](../../cmru.orchestration.sample.toml)):

```toml
schema_version = 1

[github]
owner = "your-github-owner"
repo = "your-repository"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order    = ["example-wheel"]
default_projects = ["example-wheel"]
default_steps    = ["run-tests", "build", "push"]
execution_mode   = "project-first"

[orchestration.project.example-wheel]
config = "example-wheel/cmru.toml"
depends_on = []

[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = ["example-wheel-latest"]
ghcr_packages = ["*"]
ghcr_delete_packages = []
```

The two snippets above are a complete loadable pair: save the project snippet as
`example-wheel/cmru.toml` and the central snippet as `cmru.orchestration.toml`, then run
`cmru standards`. For a standalone project that has no central file, use the annotated
[`cmru.project.sample.toml`](../../cmru.project.sample.toml), which keeps its own
`[github]` and `[targets]` facts.

`cmru.toml` is one grammar for every verb (`S-CLI`/`S2`, KI-03/KI-05). Unknown fields, a
committed `[github].token`, retired central `[projects]`/`[registry]` tables, or an omitted
required field are **rejected with exit 2**, not ignored. Validate before you rely on anything:

```
cmru standards        # conformance of every declared project's contract
```

---

## 2. The gate environment — the one thing that trips up first-timers

Your `tester-gate` step needs container/resource inputs that are **not usually set in your
project's own `cmru.toml [env]`**. The estate supplies them once, for every project, in
`cmru.orchestration.toml` under **`[orchestration.defaults.env]`** (a project's own `[env]`
overrides a value only where it has a genuinely different requirement). The required set:

| variable | meaning |
|---|---|
| `CMRU_TESTER_UNIFIED_IMAGE` | the tester-unified gate container image |
| `CMRU_TESTER_MEMORY` | gate container memory ceiling (no default — refuses unbounded) |
| `CMRU_TESTER_MEMORY_SWAP` | combined mem+swap total (Docker semantics) |
| `CMRU_TESTER_CPUS` | gate container CPU ceiling |
| `CMRU_TESTER_CGROUP_PROBE_IMAGE` | host-systemd slice probe image |
| `CMRU_TESTER_CGROUP_PARENT` | required host gates slice (`${CGROUP_PARENT_DEV_GATES}`) |
| `CMRU_TESTER_DIND_IMAGE` | **only** with `--enable-docker` (nested Docker daemon) |
| `CMRU_WHEEL_BUILDER_IMAGE` | required by `wheel-build` |

These reach the step through `cmru release`. **`cmru standards` checks this exact set** against
your declared config statically, and at runtime `tester-gate` validates the same set up front
(SPEC `S2.6a`, KI-17): if anything is missing it aborts **once, naming every missing variable
together**, before any container spins up — and the message names
`cmru.orchestration.toml`, not your project's `cmru.toml`. (In terse messages the estate writes
this env block as "`[env]`" for short; the literal table in the orchestration file is
`[orchestration.defaults.env]`.)

### Reproducing a gate step by hand

When a release goes red, you copy the failing step's `argv` and run it directly. Export the
orchestration env block first — the preflight will tell you the complete list in one shot if you
forget, but it is faster to set it before the first try:

```sh
export CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local \
       CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
       CMRU_TESTER_CGROUP_PROBE_IMAGE=debian:trixie-slim
# then run the step's argv
```

### The CMRU R0-R3 gate

For the vbpub CMRU checkout, use the project entrypoint rather than a cockpit
pytest command:

```sh
./run-gate.py --list
./run-gate.py assay
./run-gate.py gate
```

The `assay` lane installs Assay from the selected worktree, snapshots the
repository with the three declared Topos fixture omissions, and judges R0
(full tests), R1 (100% line and branch coverage), R2 (native mutation), and
R3 (import-break canary). R2 enables liveness monitoring, and a mutant stops
after its first failed test (`--maxfail=1`); passing full-suite runs still
execute every test. Every assay invocation resumes and writes
`.assay/progress-cmru.jsonl`; the verdict is `.assay/verdict-cmru.json`.
`gate` additionally runs CMRU's release-specific coverage, mutation, canary,
and real-enrollment evidence lanes.

---

## 3. The release flow, and what an isolated transaction is

```
cmru status                       # what would release, and at what version bump
cmru release <name>     # one source-first transaction: gate → tag → build → publish → promote
cmru release                      # changed projects, one transaction per Git family (S-CLI.5a)
```

`cmru release` never publishes from your working tree (`S-CLI.5`). It fetches `origin/main`,
refuses local-only `main` commits the snapshot would omit, and creates a temporary worktree at
that exact remote commit, named (`S-CLI.5b`, KI-16, ciu-aligned):

```
.worktrees/cmru-release-<YYYYMMDD_HHMMSS>-<scope>-<workspace-id>
       e.g. .worktrees/cmru-release-20260819_143022-assay-abc123
```

Flat, chronologically sortable, and **the branch name is byte-for-byte the directory name** —
the same 1:1 scheme ciu uses. A successful release removes the worktree; a **failure retains
it** for diagnosis and prints its exact path. The origin candidate branch is also retained. CMRU
records the allocator's canonical identity input so the visible six-character token and the
structured workspace context remain the same fact across resume and cleanup.
publishes from the exact gated candidate SHA and only then fast-forwards `origin/main`; if a
concurrent update rejects that final promotion, CMRU does not rebase the candidate or create a
source revert. Inspect the retained candidate and resolve the external publication explicitly
before abandoning it. List and clean retained ones:

```
cmru worktrees                                   # every retained failed build/release worktree
cmru cleanup --discard-build-worktree <PATH> --yes
```

For a script, use `cmru worktrees --json`. `prunable: true` reports Git's
worktree-registration marker; it does not prove the checkout directory is
absent or visible in the current filesystem namespace. The `source_commit`
field preserves Git's reported HEAD even for prunable entries. CMRU withholds
resume/discard commands for those entries; consumers should likewise validate
the exact checkout before acting on it.

When the central CMRU root registers projects from independent Git repositories, the same
selection is dispatched as one transaction per Git family. Each repository therefore gets its
own lock, candidate branch, workspace identity, and promotion result; cross-repository release
is coordinated in order but is not one atomic Git commit.

`cmru build X` is the local-inspection sibling: it runs prepare/gate/build in a
retained `cmru-build-…` worktree and **never publishes** (KI-10). Do not expect
`cmru build` then `cmru publish` to ship the reviewed artifact — use `cmru release` for that.

After a release transaction, CMRU also cleans up the caller's local `main` when it can. If
that checkout is dirty, including with tracked or untracked files, ignored files, or ignored
directories, CMRU refuses the cleanup rebase before invoking either rebase command and leaves
the files and local ref untouched.
This warning does not put caller edits into the immutable remote snapshot, and
`--allow-uncommitted` does not change that boundary. From a clean caller checkout, use the
remedy printed by CMRU:

```sh
git status --short --ignored
git stash -a                 # or commit/move the caller changes
git rebase origin/main
git stash pop                # only if the stash is the desired work
```

A real rebase conflict is reported as a conflict, separately from a dirty-checkout refusal;
another clean-rebase failure is reported as undetermined rather than guessed to be a conflict.
The same diagnostic is printed after a successful release, a plan refusal, or a child failure;
the primary release result and retained-worktree behavior remain unchanged. See the normative
[`S-CLI.5a`](SPEC.md#s-cli5a--projects-release-one-after-another-not-in-a-shared-batch) contract
and the [caller-main cleanup operations](RELEASE-TRANSACTIONS.md#caller-main-cleanup).

### Retaining gate evidence

If the release gate writes commit-bound evidence, declare each exact file or directory under
`[project.release]`:

```toml
schema_version = 1

[runtime]
kind = "none"

[project]
id = "example-wheel"

[project.release]
evidence_paths = ["coverage.json", ".assay"]
```

CMRU moves those paths into `<project>/evidence/cmru-release/<immutable-id>/` after the whole
release succeeds and writes `evidence.json` with the gated source commit and SHA-256 hashes.
The paths must be project-relative, contain no `..`, and contain no symlink component; a
missing or unsafe declared path fails retention and keeps the release worktree available for
inspection. Use `--discard-evidence-on-release` only when deliberately discarding those
outputs. `evidence_paths` is separate from `artifact_dirs`: evidence proves the gate's input
commit and is not offered to a publisher.

### Previewing an external version

For a project whose version is discovered by a prepare step, declare
`strategy = "external:VAR"` and the generated files under
`release.commit_generated`. `cmru release PROJECT --dry-run` then runs that
declared version query inside its disposable candidate and prints the resulting
release plan. The candidate is removed afterward and no gate, tag, build, push,
or promotion occurs. A prepare command must remain deterministic and may write
only its declared outputs; CMRU refuses any other write.

---

## 4. Failure modes worth knowing before your first release

- **Never hand-tag a cmru-managed project** (KI-12). cmru owns `tag` in its own pipeline, so a
  manual tag is indistinguishable from a completed release — and an unpushed hand-made tag at
  `HEAD` silently produced an *empty* release plan in the incident that filed KI-12. cmru now
  checks the plan against `origin` (the baseline tag must be pushed, under the same name, at the
  same commit) and refuses otherwise with a named remedy. Let cmru create every tag.

- **Pin the cmru you run, and prove it matches the engine** (KI-11). A project step whose `argv`
  begins `cmru …` resolves through the worker's `PATH`, which can be an *older installed wheel*
  than the source engine driving the transaction. Until KI-11 is resolved, install the last
  verified cmru wheel into the gate environment before an estate release and treat a
  source-vs-installed version mismatch as a preflight failure — do not paper over it per project.

- **GHCR package visibility is a one-time UI step** (KI-01). For an OCI product, the first push
  cannot set the package public via any API (a platform limitation); cmru logs a one-time `WARN`
  with the remediation and does **not** fail the release. Set it once in the package settings;
  it persists across all later pushes.

- **A delegated third-party tool step is not a silent option** (KI-04, `S7`). SBOM/signing/etc.
  are not yet a config feature; cmru rejects that surface rather than pretend a step ran.

---

## 5. What cmru does *not* (yet) ship

Adopt with these boundaries in mind — each is a deliberate, fail-closed gap, tracked in
[`../KNOWN_ISSUES_TODO_BACKLOG.md`](../KNOWN_ISSUES_TODO_BACKLOG.md):

- **OCI repack** is guarded off for production (`--repack` exits 2 before any side effect) until
  it proves single-build + registry-digest equivalence (KI-02, `S14`).
- **Durable post-tag publish resume** does not exist: `--resume` is for investigating a retained
  *pre-tag* worktree, not an automatic post-tag retry (KI-06).
- **`release --from-candidate`** (promote a separately-built, remotely-evidenced artifact) is
  deliberately postponed; a local `build.json` explicitly forbids publication (KI-10).

---

## See also

- [`../README.md`](../README.md) — the model, verbs, and templates (WHAT).
- [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md) — why contextual roots, central facts, selection, and native release logging work this way.
- [`SPEC.md`](SPEC.md) — the normative contract (WHY); start at *S-CLI*, *S-REL*, *S2*.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — running cmru against the estate during development.
- [`../../docs/ciu-vs-cmru.md`](../../docs/ciu-vs-cmru.md) — which tool owns an artifact.
