# Consuming Assay from another repository

Assay is a release artifact, not an estate-only source import. A consumer can use either
the wheel or the matching zipapp from one immutable `assay-v*` GitHub Release. The wheel is
the normal integration; the zipapp is the zero-install option for a gate image that already
has Python.

## What `assay.toml` is, and what it is not (B009)

**`assay.toml` is an adapter and judgment-policy file for a project that has
adopted assay** — its coverage floors, which rigor levels it declares, its
isolation policy, and where its own entrypoints are. It belongs in the
project it judges, beside that project's source.

**It is not an estate-wide lane registry.** A project that cannot adopt assay
— host or shell tooling, anything whose "tests" are not a command assay can
judge inside a snapshot — declares its gates in its own project-root gate
script and simply does not have an `assay.toml`. Assay is invoked FROM such
scripts, at the points where it judges something, not made a precondition for
having a gate at all. "This repository must be assay-judged before release"
is a release policy, decided per project; it is not a reason to put an
`assay.toml` everywhere.

The division of labour with an orchestrator is the same one, one level up:
**`assay.toml` owns judgment; the orchestrator's own config owns
orchestration** — which lanes exist, what environment each runs in, what
artifacts are collected. In this estate that orchestrator is `run-gate`, and
its `run-gate.toml` references an assay-judged lane by name (`assay_lane`)
rather than restating the judgment.

### How consumers actually get the judge, as of 2026-09-02

Stated as MEASURED rather than as intended, because the two have differed:

| consumer | how it gets assay |
|---|---|
| `ciu` | vendored pinned zipapp, `tools/assay/assay-3.2.0.pyz` + `.sha256`, invoked through `run-gate.toml`'s `assay_command` |
| `cmru` | vendored pinned zipapp, `tools/assay/assay-2.3.0.pyz` (also its `[orchestration]` release-order pin) |
| `nyxloom` | vendored pinned zipapp, `tools/assay/assay-4.0.0.pyz` |
| `dstdns` | vendored pinned zipapp, `tools/assay/assay-4.0.0.pyz`, with a `[lanes.*.pins.assay]` sha256 block per lane |
| `assay` itself | builds its own wheel in-repo and installs it into a clean venv for the gate; it never imports its own source under test |

So **every consumer today vendors a pinned, sha256-verified `.pyz`.** Nothing
is baked into a shared image, and no consumer resolves assay from `PATH`. If
you are adopting assay now, that is the pattern to copy: it is fresh-clone
safe, needs no network at gate time, and the pin is what makes a verdict
attributable to a judge you verified (see `judge_provenance` below).

A later estate direction — bake the judge into the shared gate image and keep
only a version pin per repository — is recorded in the backlog (B009 item 2)
and is **not** the shipped state. Do not write a gate that assumes it.

**Forward note.** Long asynchronous lanes (mutation campaigns, fuzzing) are
planned as ordinary assay lanes with large budgets, triggered remotely and
invoked through the same project gate script; see B007's multi-target canary
for the bounded-long-judgment shape.

## Obtain and verify an immutable release

Download one named release's wheel and `release-manifest.json`, then verify them before
installation. Obtain `gate/distribution/release_wheel.py` from that same immutable tag's source
archive; it is stdlib-only and deliberately runs before Assay is installed:

```bash
python3 release_wheel.py verify \
  --wheel assay-<version>-py3-none-any.whl \
  --manifest release-manifest.json \
  > requirements.assay.txt
python3 -m pip install --require-hashes -r requirements.assay.txt
```

Use a concrete tag/version in automation. A floating release lookup is a policy decision for
your updater, not something Assay guesses. The manifest binds the wheel name, PEP 440 version,
and SHA-256; `pip --require-hashes` then checks the same bytes it opens.

If installing into a gate image is undesirable, download the release's matching `assay-*.pyz`
and its `.sha256` sidecar, verify the sidecar, and invoke it directly:

```bash
sha256sum -c assay-<version>.pyz.sha256
/opt/tester-venv/bin/python tools/assay/assay-<version>.pyz lanes --file assay.toml
```

The zipapp is useful for hermetic CI because it has no runtime dependencies. Its test command
still needs the project's test tools in the gate environment.

## Add a lane, then gate it

Copy [`../templates/consumer-assay.toml`](../templates/consumer-assay.toml) to the consumer
repository as `assay.toml`. Start with the exact existing test argv as an R0 lane. That gives
one schema-validated, machine-readable result without claiming coverage or mutation evidence.

```bash
assay lanes --file assay.toml
assay run unit --file assay.toml --verdict-json .assay/verdict-unit.json
assay verify .assay/verdict-unit.json
```

Treat the `assay run` exit code as the gate decision and retain the JSON verdict as CI evidence.
`assay verify` validates an existing verdict; it is not a substitute for running the lane.

Adopt R1 only after the existing command can produce a real coverage artifact for the selected
source roots **and** the lane declares `[lanes.<name>.isolation]` (below — required the moment a
lane claims R1, R2 or R3; a lane that omits it refuses to load with `BAD_LANE_CONFIG` and no
further explanation, so declare it in the same edit that adds `rigor = [..., "R1"]`, not after).
Adopt R2 and R3 deliberately: mutation and canary runs execute isolated snapshots and are most
valuable on changed high-risk code, but cost materially more time and scratch disk. Set a small
`max_mutants` and a lane budget first; expand only after observing real runs.

## Declare your snapshot selection

Every lane that claims R1, R2 or R3 must add an `[isolation]` table — there is no default and no
inference from where `assay.toml` sits, and an R0-only lane must *not* declare one (it never
snapshots, so a declared policy there is inert config and refused). Two closed values:

<!-- assay-doc-example:skip reason="fragment -- shows only the [isolation] table shape, not a complete lane; the whole-target and monorepo examples below are the complete, load-bearing lanes" -->
```toml
[lanes.unit.isolation]
snapshot_selection = "repository"
```

`"repository"` materialises the whole resolved commit and is the right choice for almost every
project: nothing to declare beyond the one line above, and it is what every R1+ lane has always
run against.

Choose `"repository-minus-unsafe-symlinks"` only when `"repository"` refuses your lane with
`ERROR`/`GIT_FAILED` naming a tracked symlink whose target is absolute or escapes the repository
— typically a monorepo neighbour's deliberate fixture or a vendored container artifact with no
relationship to your own source. See
[the worked monorepo example](#a-monorepo-lane-omitting-declared-unsafe-symlinks) below for the
full shape, what the refusal looks like, and the maintenance obligation it creates.

Both values are recorded verbatim in the verdict's `snapshot_policy` object, so a reviewer can
tell a full-repository verdict from an omission-mode one without re-running anything.

## Run the lane somewhere other than the project root: `cwd` (B043)

A monorepo app's own tooling — `npm run`, `go test ./...` inside a module,
`cargo` — resolves its configuration from the directory it is invoked in, not
from the repository top. Before schema v9 the only way to say that was a shell
wrapper:

<!-- assay-doc-example:skip reason="a one-line ANTI-pattern being argued against, not a lane; the loadable monorepo lane using cwd is in the dependency-closure section" -->
```toml
argv = ["bash", "-c", "cd applications/webapp-ui-react && npm test"]   # don't
```

That wrapper costs three real things. `argv[0]` becomes `bash`, so assay's
`MISSING_EXTERNAL_TOOL` preflight checks for `bash` and never for `npm`.
`allow_argv_append` becomes meaningless — an appended argument lands after the
closing quote, not on the inner command. And shell quoting sits between the
lane file and what actually ran, which `argv_effective` then records opaquely.

Declare the directory instead:

<!-- assay-doc-example:skip reason="the two keys under discussion, quoted on their own; the whole loadable lane using cwd is in the dependency-closure section" -->
```toml
[lanes.ui_unit]
cwd = "applications/webapp-ui-react"
argv = ["npm", "test"]
```

**The grammar.** Repository-top-relative, forward slashes, no leading `/`, no
`..`, no `.` or `.git` component — and it must name a real directory. Anything
else is `ERROR`/`BAD_LANE_CONFIG` at load, naming the key and the path.

**The commit-bound refusal.** One further refusal happens later, at run time,
and names the commit: `cwd` must be a directory **the resolved commit's own
tree contains**. Assay decides that from the commit's tree, not by looking at
the snapshot directory — so it is a statement about what you committed, and
nothing standing in the snapshot for another reason can satisfy it. A `build/`
or `dist/` directory your `.gitignore` covers is the usual way to hit this.

Be precise about what that does and does not promise. A snapshot's *committed*
content is exactly the commit's, but a snapshot is not only committed content:
`link_paths` (§ "Linking a dependency closure into the snapshot", below) is a
declared, recorded way to put working-checkout content into it as symlinks. So
"absent from your commit" does **not** by itself mean "absent from the
snapshot" — an untracked `deps/` that the same lane also links is present
there, as a link into your checkout. What assay guarantees is the thing that
actually matters for `cwd`: your command runs in a directory the commit
contains, never in one reached through such a link, and therefore never in
your own working tree. `cwd` and `link_paths` naming the same path is refused
outright, at load, naming both keys — a linked path is by rule untracked and a
`cwd` is by rule tracked, so no commit can satisfy both. A link *beneath* the
`cwd` (`cwd = "app"` with `link_paths = ["app/node_modules"]`) is the ordinary,
supported shape and is unaffected.

**Where it applies.** The lane command, every R2 candidate re-execution and
every R3 canary run — all of them, always the same resolved directory. There
is no way to have one of them disagree with the others.

**What it does NOT re-root.** Everything else in a lane file stays
project-root-relative, whatever `cwd` says — one path grammar, one meaning
per path (A-271):

| stays project-root-relative | why it matters |
|---|---|
| `judge.coverage.artifact` | the reservation is armed before the command runs, from the project root |
| `judge.mutation.artifact` / `equivalence_artifact` / `kill_signal_artifact` | same reservation discipline |
| `judge.source_roots`, `judge.targets`, `judge.canary.target` | these name repository content, not a working directory |
| `infrastructure` facts | resolved in the invoking context, before any snapshot exists |

So a lane with `cwd = "applications/webapp-ui-react"` writing coverage into
that app still declares
`artifact = "applications/webapp-ui-react/.assay/coverage-final.json"`, with
the prefix, even though its command writes `.assay/coverage-final.json`
without one. That is deliberate: the two paths are read by different parties
at different times, and giving them one grammar is what keeps a path in a
verdict meaning the same thing wherever it appears.

`environment_command` is likewise unaffected. It probes the INVOKING
environment before any snapshot work (see [Add a lane, then gate
it](#add-a-lane-then-gate-it)); it is not the lane command and keeps the
invoking working directory.

**In the verdict.** A lane that declares `cwd` records `cwd_declared` at the
verdict's top level, beside `argv_declared`. A lane that declares none records
*no key at all* — never `"."`. `assay lanes --json` exposes the same value as
`"cwd"` (`null` when undeclared), so a gate tool can preflight the directory
without parsing `argv`.

## A whole-target floor: a coverage gate that survives a docstring-only change

Ordinary R1 judges the `base..HEAD` diff, so "fixing" a method by editing only its docstring
changes zero executable lines and a changed-line floor demands nothing of the method body. If your
gate needs to prove a specific, owned module stays fully covered *regardless of what the diff
touches* — the case a method-reconciliation program runs into first — declare `judge.mode =
"whole_target"` instead. It takes no base, so it runs from any commit including `main`
post-merge, and it judges exactly the files you name, never a directory expansion.

```toml
schema_version = 2

[lanes.redirect_chain]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["python", "-m", "pytest", "tests/unit/test_redirect_logic.py", "-q", "--cov=common.redirect_chain", "--cov-branch", "--cov-report=json:.assay/coverage.json"]
env = {}
env_passthrough = ["PATH"]
budget = "10m"
allow_argv_append = false

[lanes.redirect_chain.isolation]
snapshot_selection = "repository"

[lanes.redirect_chain.judge]
language = "python"
source_roots = ["libs/common/src"]
fail_under = 100.0
allow_excluded = false
require_branch = true
mode = "whole_target"
targets = ["libs/common/src/common/redirect_chain.py"]
# judge.base is FORBIDDEN here -- a whole-target lane resolves no diff at ANY
# tier, so recording one would imply a comparison that never ran. This holds
# for every language and every rigor, R2 included (A-325): `mode` selects the
# SCOPE both R1 and R2 judge under, so a whole-target R2 mutates the declared
# files whole and never reads a comparison commit either.

[lanes.redirect_chain.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/coverage.json"
```

Run it exactly like any other lane — `assay run redirect_chain --verdict-json .assay/verdict.json`
— from a clean tree on any commit, `main` included. The resulting verdict's `judgment.r1` records
`mode: "whole_target"`, `targets: ["libs/common/src/common/redirect_chain.py"]`, and
`require_branch: true`: the effective policy that actually judged, not merely what the lane file
declared. The coverage payload's `covered`/`executable`/`branches_covered`/`branches_total` cover
every line and arc of `redirect_chain.py` as reported by the artifact — not the diff — so a
docstring-only commit still measures the whole module, and a method whose body regresses below
`fail_under` fails the gate even though nothing about it "changed" in `base..HEAD`. If the target
is absent from the coverage artifact, or present with zero executable lines (the `--cov=` typo /
never-imported-module failure mode), the lane refuses `NO_MEASUREMENT`/`TARGET_NOT_MEASURED`
naming the target, rather than silently passing 0/0.

**Before running it, gitignore what your test tooling writes, not only the declared artifact.**
`coverage.py` writes its own `.coverage` data file into the working directory even when
`--cov-report` sends the rendered report elsewhere — inside the snapshot that file is untracked
and unignored, so assay's post-command dirt check correctly refuses the lane. The refusal reads
as `NO_MEASUREMENT`/`DIRTY_TREE`, which looks exactly like "you have uncommitted work", not "your
coverage tool left a data file behind" — the same class of opaque diagnostic B006(b) exists to fix
for `UNREADABLE_ARTIFACT`, one layer over, and it costs a debugging round the first time it bites.
At minimum, for a Python lane:

```text
.coverage
.coverage.*
.assay/
__pycache__/
.pytest_cache/
```

Commit that `.gitignore` in the same change that adds the lane, before the first `assay run`.

Assay honors a **committed, per-directory** `.gitignore` for untracked output,
so the blanket `.assay/` entry above is sufficient — you do not need one
narrow pattern per artifact type. It deliberately does not honor
`.git/info/exclude` or any global/system/configured exclude source: those
live outside the committed tree, so a personal ignore rule there could hide
real untracked source with nothing else reporting it. Modified and staged
tracked files remain dirty regardless of any exclude source.

## Resume and shard a long mutation lane

**A consumer gate passes `--resume --progress <path>` on EVERY lane it runs,
not only its mutation lanes.** Both are no-ops where a lane has nothing to
checkpoint — `--progress` is ignored without R2, and resume state is touched
only by the mutation sweep — so a uniform invocation costs nothing and means a
lane that later gains R2 needs no gate change to become resumable and
observable. Write the progress file outside the tree under judgement: an
untracked path inside it is a `NO_MEASUREMENT`/`DIRTY_TREE` of the gate's own
making. (assay's own registered gate does this to itself, in
`tools/tester-unified-gate.sh`.)

Preview a subset without executing it:

```sh
assay plan <lane> --operators python:compare-swap --shard 0/3
```

Run each shard with resume:

```sh
assay run <lane> --resume --shard 0/3 --verdict-json .assay/shard-0.json
assay run <lane> --resume --shard 1/3 --verdict-json .assay/shard-1.json
assay run <lane> --resume --shard 2/3 --verdict-json .assay/shard-2.json
```

Completed candidates persist under `.assay/mutation-state/`, keyed by a
deterministic candidate id derived from the mutated file's path, its exact
source bytes, the mutated byte span, the replacement bytes, and the operator.
A real source change therefore produces a different id: `--resume` finds no
record under it and re-executes that candidate, rather than detecting or
reporting that the source moved on. A record that contradicts the identity
it is filed under — a mismatched path, source hash, byte span, replacement,
operator, or candidate id, every field folded into the id above — fails the
whole lane as `ERROR`/`UNREADABLE_ARTIFACT` rather than being silently
skipped; this is a signal of a corrupted or hand-edited state file, not an
expected outcome of normal use. `schema_version` is the one required field
NOT folded into the candidate id, so it alone gets the opposite disposition:
a mismatch there is a routine format bump, not corruption, and is treated as
an absent record — silently rerun, without failing the lane. To combine shard
manifests, every manifest must declare the same
schema version, lane, commit, and shard count, cover every zero-based index
exactly once, and contain disjoint candidate IDs whose deterministic
assignment is independently re-verified against the claimed shard index.

## Inject infrastructure facts into an isolated lane

```toml
schema_version = 2

[lanes.sql_example]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "run-sql-lane"]
env = { }
env_passthrough = []
budget = "10m"
allow_argv_append = false

[lanes.sql_example.infrastructure]
network = "required-env:CIU_NETWORK"
cgroup_parent = "derived:deploy.cgroup_parent"
```

`required-env:` reads only the invoking process environment. `derived:` reads
only rendered CIU state at the project root (`ciu.global.toml`). Missing, empty,
or malformed facts refuse before any snapshot or command runs; resolved values
are injected as environment variables named exactly by the table key. The
snapshot itself never receives caller state.

A resolved value must be a non-empty **string** — a `derived:` dotted path
landing on a TOML integer, float, boolean, array, or table refuses rather than
being silently coerced to text (a source's own type choice, e.g. a port
declared as an integer, should not become an env-string fact with no record
that a coercion happened); a consumer wanting a numeric fact as an env var
renders it as a string at the source instead. A resolved value is also bounded
at 64 KiB — well above any real infrastructure fact (ports, hostnames, tokens,
small rendered JSON blobs) and well below where an oversized value would fail
late and opaquely at `E2BIG` on exec. An infrastructure name colliding with a
declared `env` or `env_passthrough` name refuses at load time; the same
collision is refused again at run time as defence-in-depth, so a `Lane`
constructed directly (bypassing the loader) cannot reach it unprotected.

**An R3 canary's side-run sees the same facts.** A lane may declare
`[lanes.<name>.infrastructure]` alongside `rigor = [..., "R3"]`; both halves
of the canary — the known-good control and the transformed variant — run with
the resolved facts in their environment, exactly as the lane's own command
does. Verified end to end with a `derived:` fact and a suite that reads it out
of `os.environ`: the control passes, so the facts really are there (B029).

**If the infrastructure declaration itself is what's unresolvable**, a refusal
that was ALREADY going to happen for some other reason (a bad `--shard`, an
unrelated adapter refusal) still writes a real, schema-valid verdict — but
`env_effective` in that one case is only `lane.env` (never infrastructure or
passthrough values, since neither could be safely completed), and the verdict
carries a sibling `env_effective_incomplete: true` so a consumer never
mistakes that partial value for the real one.

## A monorepo lane: omitting declared unsafe symlinks

A monorepo lane's snapshot walks the **whole** resolved commit, not just its own source roots —
so one tracked symlink anywhere in a sibling project, with a target that is absolute or escapes
the repository, refuses every R1+ lane in every unrelated project, permanently, until it is
either untracked or declared. `"repository-minus-unsafe-symlinks"` lets you keep the fixture and
unblock your own lane without touching it:

```toml
schema_version = 2

[lanes.cmru]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q", "--cov=src/cmru", "--cov-branch", "--cov-report=json:.assay/coverage.json"]
env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }
env_passthrough = []
budget = "20m"
allow_argv_append = false

[lanes.cmru.isolation]
snapshot_selection = "repository-minus-unsafe-symlinks"
unsafe_symlink_omissions = [
  "topos/tests/fixtures/inspect_files/_danger/passwd_link",
  "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
  "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
]

[lanes.cmru.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
base = "origin/main"

[lanes.cmru.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/coverage.json"
```

Every entry is the exact, repo-top-relative Git-tree path of one symlink **leaf** (never a
directory, never a regular file — declaring one that is not itself a P22-unsafe symlink is
refused as `BAD_LANE_CONFIG` at load), listed in strict ascending order, 1 to 64 entries.
Everything else in the repository — including safe symlinks and every other project's ordinary
files — is still materialised; there is no project boundary and nothing to inventory beyond the
unsafe leaves themselves.

**What the refusal looks like when a link is undeclared.** If Topos's tree ever grows a fourth
unsafe symlink that this lane has not listed, the lane refuses exactly as `"repository"` mode
would, but the diagnostic additionally names the declarable spelling:

```text
ERROR/GIT_FAILED: symlink topos/tests/fixtures/inspect_files/_danger/new_link targets the
absolute path '/etc/passwd'; if this is a deliberate fixture, this omission lane may declare
exactly "topos/tests/fixtures/inspect_files/_danger/new_link" in unsafe_symlink_omissions
```

**The maintenance obligation this creates, stated rather than hidden:** a new unsafe symlink
added anywhere in the repository reds this lane until its owner reviews it and either declares it
here or has it untracked. assay never broadens the declared list automatically — an undeclared
link fails closed, on purpose, so nobody's evidence silently widens to cover a fixture nobody
looked at.

**B006(b) — the coverage artifact's parent directory needs no work-around.** assay reserves the
declared `judge.coverage.artifact` path and, inside its own ephemeral snapshot only, creates any
missing parent directories the command's argv needs (`.assay/` above, for example) — never in
your real worktree. A previous work-around of committing a tracked `.gitkeep` file so the
directory pre-existed is no longer necessary; delete it once you have confirmed your lane runs
clean under a v2-capable release.

## SQL/DDL lanes (R2 only)

A SQL lane judges mutation testing over tracked `.sql` files: `judge.language
= "sql"` resolves at **R2 only** — no SQL R1 (DDL has no coverage tool) and
no SQL R3 (A-192 forbids a canary without R1). See
[the design guide](DESIGN-GUIDE.md#sqlddl-mutation-a-stdlib-lexer-not-a-database-connection)
for why, and for the seven closed operator names.

Two `judge.mutation` config keys exist only on a SQL lane:

- **`equivalence_artifact` — REQUIRED.** A project-relative path your
  command writes after applying a mutant. Without it, a mutant that never
  actually mutated a fresh database (residue from a previous run, say) exits
  0 and is recorded `survived` — a false statement about your own tests, not
  a missing feature. assay refuses to load a SQL lane that omits this key.
- **`kill_signal_artifact` — optional.** A project-relative path your
  command writes on a killed mutant, naming the mechanism that refused it
  (a SQLSTATE, a raised exception, anything you choose). Declaring it turns
  on `kill_attribution = "declared"`, which then requires every killed
  mutant in the artifact to carry a `kill_signal` — a killed mutant with none
  is reclassified `crashed` rather than silently accepted.

Both share `judge.coverage.artifact`'s own path grammar (project-relative,
never absolute, never escaping the project root) and **must be gitignored**
— an artifact left untracked-but-present in the snapshot after a mutant runs
leaves the tree dirty and the run refuses `NO_MEASUREMENT`/`DIRTY_TREE`
instead of classifying the mutant at all.

**How a mutant's bucket is decided**, once `equivalence_artifact` is
declared:

| your command exited | the artifact | assay records | why |
|---|---|---|---|
| 0 | absent | `crashed` | you declared an artifact your command didn't write |
| 0 | present, differs from baseline | `survived` | the mutated schema was built and your suite didn't notice |
| 0 | present, equals baseline | `equivalent` | the mutant provably changed nothing |
| non-zero | present, differs from baseline | `killed` | the mutated schema was built, and something refused it |
| non-zero | present, equals baseline | `equivalent` | it never mutated (residue, or a guard that never fires) — the failure is about something else |
| non-zero | absent | `crashed` | the schema never got built — an invalid mutant, not a kill |

### The command order is one token wide: apply, dump, then test (A-279)

**Requirement.** Your project-declared command must write
`equivalence_artifact` only after the schema has been fully and successfully
applied, and **regardless of whether your own test step passes or fails**.
The shape that satisfies this:

```bash
apply-schema.sh && dump-schema.sh && run-schema-tests.sh
```

**never**

```bash
apply-schema.sh && run-schema-tests.sh && dump-schema.sh
```

**Consequence if you get this backwards.** A kill *is* your test step
exiting non-zero. Under `apply && test && dump`, shell `&&` short-circuits
the moment `test` fails, so `dump` never runs — assay has already unlinked
any pre-existing artifact file before your command started, so the
equivalence artifact is simply absent. Per the table above, `(non-zero exit,
absent artifact)` is `crashed`, never `killed`, and a crashed mutant ranks
above every other outcome — so **the very first mutant your suite genuinely
catches renders the whole lane `ERROR`/`EXEC_FAILED`.** This reads as "assay
is broken", not "my gate script is ordered wrong", which is exactly why it
is written here as a requirement rather than left for you to discover from a
red lane.

This is measured on the shipped CLI, not merely reasoned about:
`nyxloom-trove/carve-assets/W3/MANIFEST.md` freezes two repositories,
identical in DDL, lane and mutant, differing only in this one ordering:

| your command | `killed` | `crashed` | lane outcome |
|---|---|---|---|
| `apply && dump && test` | **1** | 0 | `PASS`, exit 0 |
| `apply && test && dump` | **0** | 1 | `ERROR`/`EXEC_FAILED`, exit 2 |

### The `pg_dump` reproducibility obligation

Your dump step must be **byte-reproducible across two invocations against an
unchanged database.** `pg_dump --schema-only` is not, by default: it emits
`\restrict <random>` / `\unrestrict <random>` lines that differ on every
invocation, so two dumps of the *same, unchanged* database have different
SHA-256s (measured on PostgreSQL 18.4). Pin a fixed `--restrict-key`:

```bash
pg_dump --schema-only --no-owner \
  --restrict-key=assayfixedkey0000000000000000000000000000000000000000000000000000 \
  -d "$DB" > "$EQUIVALENCE_ARTIFACT"
```

Two dumps taken this way are byte-identical (measured); without the pin they
differ every time. Get this wrong and the consequence is silent rather than
loud: **every mutant compares unequal to baseline, the `equivalent` bucket is
permanently empty, `ALL_MUTANTS_EQUIVALENT` is permanently unreachable, and
nothing ever goes red for it** — the exact residue false-survival the
`equivalence_artifact` requirement exists to close, reopened one tool over.
assay cannot check your dump step is reproducible; it sees one baseline run.
Your own gate can, and should: dump twice against the same unchanged
database and `cmp` the two files before running your schema tests at all,
turning this obligation into a red-on-violation property of your own gate
rather than a trust you extend silently.

### `BUDGET_EXCEEDED` on a large schema change is a correct refusal, not breakage

A real production DDL corpus (37 files) carries 466 code-context operator
matches, 267 of them `NOT NULL` alone. A change that touches several files
in one commit can exceed a modest default `max_mutants`, and the lane
renders `BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED`. That is assay declining to
sample a claim it cannot make completely — the same discipline as every
other bounded terminal in this project — not a sign your lane is broken.
Raise `max_mutants` deliberately if your schema changes are routinely this
broad, rather than reading the refusal as failure.

### A worked, pasteable SQL lane

```toml
schema_version = 2

[lanes.schema]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["scripts/schema-gate.sh"]
env = {}
env_passthrough = ["PATH"]
budget = "20m"
allow_argv_append = false

[lanes.schema.isolation]
snapshot_selection = "repository"

[lanes.schema.judge]
language = "sql"
source_roots = ["infra/db-init/init-scripts"]
base = "origin/main"

[lanes.schema.judge.mutation]
jobs = 1
max_mutants = 200
operators = [
  "sql:drop-check", "sql:drop-unique", "sql:drop-not-null",
  "sql:drop-foreign-key", "sql:weaken-delete-action",
  "sql:drop-trigger", "sql:widen-check-in",
]
equivalence_artifact = ".assay/schema-dump.sql"
kill_signal_artifact = ".assay/kill-signal.txt"
```

The lane above judges the `base..HEAD` diff, which is why it declares
`judge.base`. To mutate whole schema files regardless of what changed — the
shape a migration-reconciliation gate wants — declare `judge.mode =
"whole_target"` and `judge.targets` instead, and **delete `judge.base`**: under
whole-target scope R2 reads no comparison commit, so a declared base is inert
config and is refused at load (A-325). Every declared target must resolve
inside `judge.source_roots`, exist at the judged commit, and be
adapter-recognised, non-test source; one that is not is refused by name rather
than quietly skipped, so a `PASS` always covers the whole declared set.

`scripts/schema-gate.sh` is yours to write, in the shape the two named
sections above require: apply the (possibly mutated) DDL to a throwaway
database, dump it with a pinned `--restrict-key` to
`.assay/schema-dump.sql` **unconditionally**, then run your schema tests and
let their exit status decide `killed` vs `survived` — never reordered, never
`dump || true` racing ahead of a still-applying schema. Gitignore both
declared artifacts:

```text
.assay/
```

Commit that `.gitignore` in the same change that adds the lane, exactly as
for a coverage artifact above.

## JavaScript/TypeScript lanes (R1, and R2 by ingestion)

`judge.language = "javascript"` is a changed-line lane over
`.js`/`.jsx`/`.ts`/`.tsx` — one language name for all four. It resolves at
**R1** (coverage) and, since schema v9, at **R2 by evidence ingestion** — your
own argv runs StrykerJS inside the snapshot and assay judges the report
([below](#r2-for-javascript-by-ingesting-strykers-report-b046)). Assay still
ships no JS/TS mutation engine, so a *native* R2 lane is still refused. **R3
is unwired**, and a lane declaring it is refused `ERROR`/`BAD_LANE_CONFIG`
before anything runs.

### Make your test runner emit `coverage-final.json`

`format = "coverage-istanbul-json"` is istanbul's own coverage-map document.
nyc/istanbul and Jest (`--coverageReporters=json`) emit it natively; for
Vitest, add a coverage provider and ask for the `json` reporter. Point the
report directory at the same gitignored place your verdict goes:

```bash
npm install --save-dev @vitest/coverage-istanbul
```

**Install the istanbul provider, not the v8 one.** See
[the provider warning](#the-v8-provider-is-not-safe-to-gate-on) below before
reaching for `@vitest/coverage-v8`; it is faster and needs no instrumentation
step, and it will also pass your gate on lines that never ran.

```ts
// vite.config.ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    coverage: {
      provider: 'istanbul',        // NOT 'v8' -- see the warning above
      reporter: ['json'],          // 'json' IS coverage-final.json
      reportsDirectory: '.assay',
      include: ['src/**'],
      clean: false,                // RECOMMENDED inside an assay snapshot -- see below
    },
  },
})
```

`reporter: ['json']` writes `.assay/coverage-final.json`. Do not use
`json-summary` (totals only, no per-file detail) or `lcov` (a different
registry format — if you prefer it, declare `format = "lcov"` instead and
point at `lcov.info`).

**`clean: false` is RECOMMENDED for any lane assay judges — and if you
forget it, assay now names the cause instead of reporting no coverage
(B049/A-408).** Vitest's own default (`coverage.clean = true`) deletes and
recreates `reportsDirectory` before writing to it. assay's own
`safeio.reserve_output` (`runner.py:1692`) opens and holds that directory's
own file handle *before* your lane's command runs, specifically so it can
read the artifact back afterward without a second, race-prone directory
walk (B006(b)); `reservation.consume()` (`runner.py:1771`) then reads through
that SAME held handle once the command exits. A tool that deletes and
recreates the directory — rather than writing into the one directory assay
already opened — orphans that handle: it now points at an empty, unlinked
directory, so `consume()` finds nothing, even though a fully populated
`coverage-final.json` genuinely exists on disk at that path by the time your
command exits.

Up to assay 4.1.0 that read as `NO_MEASUREMENT`/`EMPTY_COVERAGE` — "your
tests produced no coverage" — for a lane that in fact ran cleanly and
covered everything, and nothing about the failure pointed at
`coverage.clean`. **From 5.0.0 it is `ERROR`/`UNREADABLE_ARTIFACT`, and the
message names the directory, the cause and the fix**: *"the directory
'.assay' that assay reserved this output in was deleted and recreated while
the lane's command ran … Turn the tool's clean/rm-first option off (for
example Vitest's `coverage.clean = false`) or have it write into the
existing directory instead of replacing it."* The check is one `fstat` on
the descriptor assay already holds (`st_nlink == 0` on the orphaned
directory inode), so it costs nothing and adds no new race; it applies to
**every** artifact assay reserves, not only coverage — a SQL R2
`equivalence_artifact` and a mutation lane's kill-signal artifact are read
through the same reservation, and a directory-recreating step under either
of those used to report every mutant as `crashed`.

Declaring `clean: false` is still the right thing to do — it is one line, it
keeps the lane on the fast path, and a named refusal is still a refusal.
Measured directly: an otherwise fully-covered real lane run through the real
CLI returned `EMPTY_COVERAGE` with Vitest's default `clean: true` on 4.0.0,
and `PASS` with the correct 100% figure the moment `clean: false` was added
and nothing else changed. `cleanOnRerun` (Vitest's watch-mode-only sibling)
is irrelevant to `vitest run` and does not need setting.

### JavaScript lanes and the dependency closure

**The mechanism, confirmed in source, not assumed.** Every R1/R2/R3 snapshot
is `git read-tree <commit>` into a fresh `tempfile.mkdtemp` (`isolation.py`):
only TRACKED blobs exist there (A-161/A-184 — a lane judges committed
objects, never the working tree). A gitignored `node_modules/` is therefore
absent from the snapshot BY CONSTRUCTION, exactly as intended — this is
correct, and "fix" it by tracking `node_modules` is not a real option.
Python's dependency closure is a venv and Go's is `GOMODCACHE`, both
out-of-tree, so neither adapter ever met this; JavaScript's closure is
in-tree and gitignored, so it is the first language where the snapshot
genuinely needs something the checkout has that the snapshot does not.

**The consequence, and why it is not merely slow.** `npx vitest run
--coverage` inside a snapshot with no `node_modules` does not fail loudly.
`npx` FETCHES a missing package from the npm registry by default — unpinned,
over the network, from inside an isolated snapshot nothing else in this
project ever lets touch the network — unless `--no-install` is passed, in
which case it fails with a clear resolution error instead. Either way,
nothing here is what a consumer who has not read this section expects:
either an unpinned toolchain silently substitutes for the pinned one, or the
lane reads `NO_MEASUREMENT` for a reason that looks like "vitest is
missing" rather than "the dependency closure was never a design consumers
of Python/Go lanes had to think about."

**`environment_command` (B010) cannot vouch for this.** It runs in the
*invoking* environment, before any snapshot work (DESIGN-GUIDE §4) — so a
probe like `node -e "require.resolve('vitest')"` passes in the checkout
(where `node_modules` really is present) while the snapshot the lane's own
command actually runs in still has nothing. Declaring one to "check Node
is available" is fine; declaring one to vouch for the dependency closure is
a false sense of safety.

#### (a) The honest default: the image carries the cache, the snapshot rebuilds the closure OFFLINE

Same doctrine as the image-baked judge (B009): the gate image carries an npm
cache populated from the committed `package-lock.json` — either baked in at
image build time (`npm ci --cache /opt/npm-cache --prefix <app>`, then
discard the resulting tree) or mounted as a persistent `~/.npm/_cacache`
provided by the environment tool (a ciu v8 `[testing.environments.<e>]
extra_mount`, for example). The lane's own argv then starts with the
OFFLINE install and ends with the PINNED, no-fetch runner — both flags doing
real, distinct work: `--offline` fails loudly the instant the cache is
missing anything (never a silent network fetch), and `--no-install` turns a
missing `vitest` binary into a refusal instead of an `npx` fetch.

A worked MONOREPO lane — a real layout, not a root-level toy: the app lives
under `applications/webapp-ui-react/`, which is where its own `package.json`,
`node_modules` and `vitest.config.ts` all resolve, so the lane declares that
directory as its `cwd` (B043, schema v9 — this is what retired the old
`bash -c "cd … && …"` wrapper) while its `artifact` path stays spelled
relative to the PROJECT root, not the app root:

```toml
schema_version = 2

[lanes.ui_unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
cwd = "applications/webapp-ui-react"
argv = ["bash", "-c",
  "npm ci --offline --no-audit --no-fund && npx --no-install vitest run --coverage"]
env = { npm_config_cache = "/opt/npm-cache", CI = "1" }
env_passthrough = ["PATH", "HOME"]
budget = "15m"
allow_argv_append = false

[lanes.ui_unit.isolation]
snapshot_selection = "repository"

[lanes.ui_unit.judge]
language = "javascript"
source_roots = ["applications/webapp-ui-react/src"]
fail_under = 100.0
allow_excluded = false
base_source = "request"

[lanes.ui_unit.judge.coverage]
format = "coverage-istanbul-json"
artifact = "applications/webapp-ui-react/.assay/coverage-final.json"
producer = "istanbul"
```

`producer` is **required** on a `coverage-istanbul-json` lane, and a lane that
omits it is refused at load. That format is written by several toolchains
that disagree about what parts of the document mean, so there is no value
assay could imply that would be correct in every context — see
[Declaring the coverage producer](#declaring-the-coverage-producer-b045)
below for the full vocabulary and the refusals.

A root-level app — no monorepo `cd` needed, `package.json` sits beside
`assay.toml` — keeps the short form instead:
`argv = ["bash", "-c", "npm ci --offline --no-audit --no-fund && npx --no-install vitest run --coverage"]`,
with `artifact = ".assay/coverage-final.json"` (matching the `vite.config.ts`
snippet earlier in this section).

`budget` is a measurement you make, not a number this guide invents — time
the offline install plus the real run once, in the actual gate environment,
and set the budget from that.

**R3 triples this cost, not doubles it.** A canary run is baseline PLUS two
further runs (import-break, uncovered-line), each against its OWN fresh
snapshot — so each one repeats the offline install from a cold `node_modules`
inside that snapshot. Budget a `javascript` R3 lane accordingly once it is
wired — not yet: R3 is registered only after a real-Vitest canary pair has
run (`tests/qualification/test_javascript_real_vitest.py` proves R1 today;
canary coverage is a later step).

Gitignore what the run writes — the coverage directory, and anything your
runner drops beside it — in the same change that adds the lane:

```text
applications/webapp-ui-react/.assay/
```

#### (b) Linking a dependency closure into the snapshot: `link_paths` (B041(b))

Rebuilding the closure inside every snapshot is correct but not free, and R3
triples the cost. `link_paths` is the declared alternative: named directories
from the **invoking checkout** are symlinked into every snapshot the lane
creates, immediately after `read-tree` and before any command runs.

<!-- assay-doc-example:skip reason="the isolation sub-table of the worked lane above, quoted on its own to show the key; the whole loadable lane is in pattern (a)" -->
```toml
[lanes.ui_unit.isolation]
snapshot_selection = "repository"
link_paths = ["applications/webapp-ui-react/node_modules"]
```

**The trade-off, stated plainly.** Pattern (a) is still the honest default.
A snapshot is normally built from committed objects alone, so what it holds
is bound to the recorded commit; a linked path is not — it is whatever the
checkout happened to hold when the lane ran. That is exactly why the verdict
records `snapshot_policy.link_paths` (sorted, and omitted entirely when the
lane declared none): a reviewer reading a verdict with a non-empty
`link_paths` knows the judgment's dependency closure is only as reproducible
as that checkout was.

**The rules, each with its own refusal.**

| rule | refusal when broken |
|---|---|
| repo-top-relative, forward-slash, no `..`, no leading `/`, no `.git` component; strictly ascending, unique, at most 64 entries; declared-but-empty is refused (omit the key instead) | `ERROR`/`BAD_LANE_CONFIG` at **load** |
| the directory must exist in the invoking checkout | `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` at run time, naming the path |
| the path must **not** be tracked at the resolved commit | `ERROR`/`BAD_LANE_CONFIG` — linking a tracked path would replace committed content with working-tree content |
| the path's parent must exist in the snapshot | `ERROR`/`BAD_LANE_CONFIG` — assay never `mkdir -p`s into a snapshot |
| the path must be ignored by a **committed** `.gitignore` | `ERROR`/`BAD_LANE_CONFIG` — see the trailing-slash trap below |
| the lane's own `cwd` must not be the path, or lie beneath it | `ERROR`/`BAD_LANE_CONFIG` at **load**, naming both keys — a linked path is untracked and a `cwd` must be tracked, so no commit satisfies both. A link *beneath* the `cwd` is the ordinary shape and is fine |

An absent directory is `MISSING_EXTERNAL_TOOL` rather than `BAD_LANE_CONFIG`
on purpose: the same lane file is correct on a machine whose image ran the
offline install and incorrect on one that did not, so the lane file is not
what is wrong — a declared prerequisite the environment did not provide is.

**The trailing-slash trap — read this one.** Your existing rule is almost
certainly `node_modules/`, with a trailing slash, because that is what every
JS project's `.gitignore` carries. **It will not work here.** Git treats a
trailing-slash pattern as directory-only, and what assay plants is a
*symlink*, which git does not count as a directory — so the link shows up as
untracked content and assay refuses the lane. Drop the slash:

```text
applications/webapp-ui-react/node_modules
```

That single rule ignores both the real directory in your checkout and the
link in the snapshot. Assay refuses at materialisation, with a message naming
the slash and this fix, rather than letting the lane's own command be blamed
for a `DIRTY_TREE` afterwards.

**Teardown never touches your files.** Snapshot teardown removes the *link*,
never what it points at — proven by a test that plants a real symlink to a
real directory, puts a canary file inside it, tears the snapshot down on both
the success and the failure path, and asserts the canary's bytes are intact.

`excluded_dir_names` already excludes `node_modules` from judging, the link is
not a tracked path so the diff never sees it, and istanbul keys under it are
inert — so linking a closure changes what is *available* to the command, never
what is measured.

### Four things that behave differently from a Python lane

**Absolute paths in the artifact are handled for you.** Istanbul keys every
record by absolute filesystem path (`/build/agent/7/src/App.tsx`), not a
project-relative one. Assay resolves those against the repository top itself,
so a key naming a file inside the repository is matched against the diff
normally and a key naming anything outside it (a dependency, a generated file
under `node_modules`) simply never matches a changed file. There is nothing to
configure, and no `--coverage.reportsDirectory` layout that breaks it.

**`allow_excluded` has nothing to exclude.** This format carries no per-line
exclusion field: an `/* istanbul ignore next */` or `/* v8 ignore next */`
hint leaves no trace a parser can read, so by the time assay sees the document
an ignored line is indistinguishable from a line that was never code.
`exclusion_capability` is reported `"unavailable"`, and the lane's
`allow_excluded` is never consulted. Suppressing a line therefore does not
launder it past the floor — it just makes the line non-code, the same as a
comment.

**Some real lines are not judged at all, and which ones depends on your
provider.** A line the artifact records no statement for falls out of both the
numerator and the denominator — the same treatment a comment gets. Under
`@vitest/coverage-istanbul` that includes **every function signature line,
every function-level closing brace, and a `const x =` line whose recorded
statement starts on its initialiser**: measured on assay's own committed
fixture, 23 non-comment lines across six files. Under `@vitest/coverage-v8`
only 13, all of them type declarations TypeScript erases anyway. The practical
consequence: a diff that touches *only* a function signature can report
`executable = 0` and PASS. This is not a coverage claim about those lines, it
is the absence of one — assay reports what the artifact measured and never
invents a status for a line the instrumenter did not record. If that matters
for your gate, `judge.mode = "whole_target"` judges whole files instead of a
diff.

**`require_branch = true` is legal on this format when — and only when — you
declare `producer = "istanbul"`.** Istanbul's `branchMap` means one thing
under the istanbul instrumenter family (real per-arm arcs) and a different
thing under `@vitest/coverage-v8`/`c8` (v8's own executed/unexecuted ranges),
so the answer depends on a fact the format name does not carry. Declare the
producer and `branch_capability` is `"reported"` with real arcs; declare no
producer and it stays `"unavailable"`, which is a measured refusal rather
than a gap. See **Declaring the coverage producer** below.

**A type-only module is not a coverage failure.** A `.ts`/`.tsx` file whose
top-level statements are all `import type` / `export type` /
`export interface` / `type` / `interface` / `declare` is erased by the
TypeScript compiler, so no instrumenter records it and it is absent from the
artifact. assay classifies such a file as code-free rather than failing the
lane over it. The recogniser is a narrow lexer, not a TypeScript parser:
anything it does not recognise — a declaration split over several top-level
lines, a `.js` file, a runtime statement sharing a line with a type — answers
"has code", which shows up as a visible unmeasured-file failure you can act
on rather than a file silently dropped from the judgement.

### Declaring the coverage producer (B045)

A coverage **format** says what SHAPE a document has. It does not say what
WROTE it — and `coverage-istanbul-json` is written by several toolchains that
disagree about what parts of it mean. Since schema v9 the lane declares the
producer too, and assay records it in the verdict as
`judgment.r1.coverage_producer`.

<!-- assay-doc-example:skip reason="one table of the worked lane above, quoted on its own to show the key in isolation; the whole loadable lane is in the dependency-closure section" -->
```toml
[lanes.ui_unit.judge.coverage]
format = "coverage-istanbul-json"
artifact = "applications/webapp-ui-react/.assay/coverage-final.json"
producer = "istanbul"
```

The vocabulary is closed **per format**. A producer name that is real for a
different format is refused here, because the key answers "what wrote THIS
artifact", not "is this a producer somewhere".

| format | producer values | required? |
|---|---|---|
| `coverage-istanbul-json` | `istanbul` — the babel-plugin-istanbul family: `nyc`/`istanbul`, Jest with its default `babel` provider, `@vitest/coverage-istanbul`, `vite-plugin-istanbul`. They share one instrumenter, so they are one producer for every purpose assay has. | **yes** |
| | `vitest-v8`, `jest-v8`, `c8` — spellable, and **refused at load by name** (see below) | |
| `coverage-py-json` | `coverage.py` — the only producer | no |
| `go-cover` | `go-test` — `go test -coverprofile=<file>`, the unit-test path | no |
| | `covdata` — `go tool covdata textfmt -i=<GOCOVERDIR> -o=<file>`, over the counter data a `go build -cover` binary writes while running. The **integration** path: evidence about a real process, not a test binary. | |
| `lcov`, `cobertura` | *no vocabulary is open yet* — declaring `producer` on one of these is refused | n/a |

**Why it is REQUIRED for `coverage-istanbul-json` and optional elsewhere.** If
an implied `istanbul` were wrong — the lane really runs
`@vitest/coverage-v8` — nothing would fail loudly: the run would report PASS
over lines that never executed. A default is legitimate only when it is
correct in the absence of information, and here it is not. `coverage-py-json`
has exactly one producer, so an omission cannot silently pick the wrong one.

**`go-cover`'s two names shipped with the Go wave, and are OPTIONAL.** They
were deliberately withheld until a build could actually run a Go lane —
shipping a closed vocabulary nothing can produce, check or explain is exactly
the speculative naming this project refuses. The key is optional rather than
required because `go-test` and `covdata` **agree**: both emit `cmd/cover`'s
own text format, from the same instrumenter, so no reading of the artifact
depends on which one wrote it. Declare it anyway when you have the choice —
it records how the evidence was obtained, and `covdata` evidence can cover
code that no unit test executes, which is a materially different claim even
when the bytes are interchangeable.

#### The three producers assay refuses, and how to fix each

Each of these is refused *by name*, at load, with its reason and its fix —
never as "unknown producer", which would tell you that you had made a typo
when in fact your coverage is unsound.

| producer | why it is refused | the fix |
|---|---|---|
| `vitest-v8` | **Measured defect** (A-346, next section): reports never-executed lines as executed when a ternary appears earlier in the same block. Reproduces on both released Vitest majors. | `provider: 'istanbul'` in the Vitest coverage config, install `@vitest/coverage-istanbul`, declare `producer = "istanbul"` |
| `c8` | Remaps v8 ranges the same way and **reproduces the same false greens** (measured: `tests/fixtures/coverage/probe-js-provider-defect-c8/`) | instrument with `nyc`/`istanbul` or `@vitest/coverage-istanbul` |
| `jest-v8` | Jest's `coverageProvider: "v8"` remaps through the same layer and has **not** been measured against a committed witness — refused as *unproven*, which is a weaker ground than the two above and deliberately not blurred with them | Jest's default `coverageProvider: "babel"`, which shares istanbul's instrumenter |

#### What declaring `istanbul` buys you

`require_branch = true` becomes legal on a JavaScript lane. Only the istanbul
family emits `branchMap` entries that are real **arcs** — one location and one
count per branch ARM — which is what a branch percentage has to be computed
from. Every other producer of the same format keeps
`branch_capability = "unavailable"`, and that is a measured refusal rather
than an omission: `@vitest/coverage-v8` emits one location and one count per
branch RECORD, describing v8's own executed ranges, so a single translation
could not be honest for both.

Declaring `istanbul` also closes the **type-only module** gap. Under the
istanbul provider a `.ts` file holding nothing but `export type` /
`interface` declarations is absent from the artifact entirely, so a changed
one used to read as missing coverage. See the "Four things that behave
differently from a Python lane" section for what happens now.

#### Migrating a lane written before this key existed

Add one line. There are no other changes:

```diff
 [lanes.ui_unit.judge.coverage]
 format = "coverage-istanbul-json"
 artifact = "applications/webapp-ui-react/.assay/coverage-final.json"
+producer = "istanbul"
```

...unless your lane really was running the v8 provider, in which case the
refusal is the point and the fix is the provider switch, not the declaration.
Python lanes (`coverage-py-json`) need nothing.

### The v8 provider is not safe to gate on

**`@vitest/coverage-v8` reports lines that provably never executed as
executed.** An assay R1 lane reading its artifact therefore PASSes on those
lines. Use `@vitest/coverage-istanbul` for anything you gate.

The trigger is a conditional (ternary) expression: every line after one, in
the same block, is reported executed even when the block never ran. Minimal
reproduction — the only test calls `k(0)`, which returns at line 2, so lines
7 and 8 provably never run:

```ts
export function k(v: number): number {   // 1
  if (v === 0) return 0                  // 2  <- k(0) returns HERE
  const a =                              // 3
    v > 3                                // 4
      ? 10                               // 5
      : 20                               // 6
  const b = a + 2                        // 7  <- never runs
  return b                               // 8  <- never runs
}                                        // 9
```

```
@vitest/coverage-v8        line 7: executed   line 8: executed   <- WRONG
@vitest/coverage-istanbul  line 7: missing    line 8: missing    <- correct
```

Through assay, with lines 7-8 as the diff and `fail_under = 100.0`:
`PASS 100.0%` under v8, `FAIL 0.0%` under istanbul.

What was measured, so you can judge the risk for yourself:

- both **Vitest 3.2.4 and 4.1.11** — this is not a version to upgrade past;
- **one-line ternaries too** (`const a = v > 3 ? 10 : 20`), not just
  multi-line ones;
- `coverage.experimentalAstAwareRemapping` does **not** fix it;
- a multi-line binary expression, a multi-line call and a multi-line object
  literal do **not** trigger it — so you cannot avoid it by a formatting rule;
- `@vitest/coverage-istanbul` was correct on every case measured.

**assay cannot detect this for you, and does not pretend to.** Both providers
emit the same `coverage-final.json`, and nothing in the document distinguishes
a true execution count from a false one — there is no inconsistency to catch.
Guessing the producer from the artifact's shape is exactly the
declaration-versus-sniffing collapse assay refuses to make. So this is a
choice you make in `vite.config.ts`, and the only safe one today is
`provider: 'istanbul'`.

The witness artifacts are committed under
`tests/fixtures/coverage/probe-js-provider-defect/` (both providers, both
Vitest majors) and re-derived by
`tests/test_coverage_istanbul_provider_accuracy.py` on every run. The ruling
is A-346; B040 tracks it upstream. **`nyc`/`istanbul` and Jest with its
default `babel` coverage provider share `@vitest/coverage-istanbul`'s own
instrumenter and are unaffected.** Jest's `coverageProvider: "v8"` was not
independently measured this wave — treat it as unsafe until a committed
witness says otherwise — but it is not a clean unknown: `@jest/reporters`
depends on `v8-to-istanbul@^9.0.1`, the identical remapper package `c8`
uses (`^9.0.0`), and both ranges resolve to the same latest `9.3.0` absent a
pinning lockfile, so Jest's v8 provider is a strong candidate to share
`c8`'s own measured defect below, not merely an untested unrelated
implementation. `c8` (a separate `coverage-final.json` producer some non-Vitest
JS/TS test runners drive directly) **was measured** (B042 item 2) and is
**not** safe to gate on either: on the identical `probe-js-provider-defect`
ground truth, `c8@12.0.0`'s own `v8-to-istanbul` remapping reports lines
{9, 10, 11, 16, 17, 18} of `shapes.ts` as executed though only a `0`-only
call path was exercised — the same conditional-expression trigger as
`@vitest/coverage-v8`, and the same three shapes (a multi-line binary
expression, call, and object literal) it does NOT trigger on. The exact
false-positive set is not identical to either Vitest v8 reading (`c8`
additionally mis-attributes the ternary's own second arm), so this is a
sibling defect in a shared remapping idea, not evidence Jest's own v8
integration behaves the same way. Witness:
`tests/fixtures/coverage/probe-js-provider-defect-c8/` (the harness, with
PROVENANCE) and
`tests/fixtures/coverage/coverage-istanbul-json.provider-defect.c8.json`
(the artifact), re-derived by the same test module's `C8`/`C8_FALSE_GREENS`
cases.

### What counts as a test file, and what is skipped

A changed file is skipped when its name carries a `.test.`/`.spec.` segment
(Vitest's own default `include` glob), when any path segment is `__tests__`,
or when any path segment is `node_modules`, `dist` or `coverage`. A `.d.ts`
declaration file with no coverage entry is treated as having no executable
code — the expected silence, not a coverage gap. A **type-only `.ts` module**
(only `export type`/`interface`, no runtime value) is reported by
`@vitest/coverage-v8` with an empty statement map and judged as zero
executable lines; under `@vitest/coverage-istanbul` it is absent from the
artifact entirely and would be reported as missing coverage. That is the one
respect in which the v8 provider behaves better, and it is **not** a reason to
use it — see the provider warning below. Until B038 lands, either give such a
module one real runtime export, or keep it out of your declared source roots.

**A `.stories.tsx`, a `src/test/setup.ts`, a `vitest.setup.ts`, or a
`*.config.*` file is not a test path by the adapter's own rule above — none
of them carries a `.test.`/`.spec.` segment or sits under `__tests__/`.**
Keep them out of `source_roots`, and here is precisely why: with
`coverage.include` declared — which every worked lane in this guide does —
Vitest synthesises an ALL-ZERO coverage record for every file the glob
matches (except the small, fixed set below Vitest always removes
regardless of `coverage.include`), whether or not any test ever imports it
— measured directly, not just inferred: under a plain `coverage.include =
['src/**']`, `.stories.tsx` and `.config.ts` files that happen to sit under
`src/` DO appear in `coverage-final.json`, every statement at count `0`.
**This is not Vitest's own hardcoded `coverage.exclude`** — that list covers
only the one resolved config file actually in use, the `test.include`
test-name glob, and declared setup files, never an arbitrary
`*.config.*`/`*.stories.*` name. (Internals, version-scoped — re-locate
before citing on a different release: `resolveConfig`/`vitest@4.1.11`'s own
`chunks/coverage.*.js` computes the hardcoded list at a content-hashed
chunk path that will differ across patch releases. This wave's own
qualification harness and committed fixtures pin `vitest@3.2.4`; the
BEHAVIOUR above was measured on `4.1.11` and was not separately
re-measured on `3.2.4`.) The net effect a consumer actually has to avoid is
the same regardless of the mechanism: a
changed line inside one of these files is judged like ordinary source
(the adapter's `is_test_path` does not exempt it) against a record that can
never show anything but uncovered — fail-closed, and visible in the verdict,
never a silent pass.

### A never-executed file's own instrumentation quirk costs you that file, not the verdict (B054)

The other consequence of that same synthesis: `@vitest/coverage-istanbul`
statically instruments a file your `coverage.include` glob matches even when
no test imports it, and for some constructs — an ordinary braceless
single-statement `if` is the measured one — the record it writes names a
branch arc on a line that record's own `statementMap`/`s` does not classify
as code at all. The record contradicts itself.

Through 4.1.0 assay refused the WHOLE artifact for that, `ERROR`/
`UNREADABLE_ARTIFACT`, so one never-executed file with no relation to your
diff cost every other file's correct coverage data — the opposite of what
changed-line judging promises during an incremental rollout, and a real
reason consumers narrowed `coverage.include` to their tested surface.

That is fixed. The disposition is **per file**, and it depends on one thing
only — whether this lane judges that file:

- **Not judged** (no line of it in the diff, under `changed_lines`; not a
  declared target, under `whole_target`): the contradicting arcs are dropped,
  the file is **named on the diagnostics stream** — never skipped silently —
  and the verdict proceeds on the strength of everything else. A fully
  covered diff still PASSes.
- **Judged**: the lane refuses `ERROR`/`UNREADABLE_ARTIFACT`, naming the file
  AND the arc line. There is no honest number for a file whose own record
  disagrees with itself, and reporting one over a set of arcs assay knows is
  incomplete is exactly the laundering this tool exists to refuse. The remedy
  is in the message: narrow the glob to the tested surface, or keep the file
  out of `judge.source_roots`.

So a broad `coverage.include: ['src/**']` — the shape every worked lane here
uses — is the right shape again. What has NOT changed: an unrecognised branch
`type` in `branchMap` still refuses the artifact whole (it means the declared
`producer` does not describe the document you actually produced), and so does
every other malformed record.

### R2 for JavaScript, by ingesting Stryker's report (B046)

Assay ships no JavaScript mutation engine and does not pretend to. What it
does instead is the thing it has always done for coverage: **your lane's own
argv runs the mutation tool inside the private snapshot, and assay judges the
report it wrote.** Assay never invokes Stryker, and never accepts a report it
did not watch being produced — the report is bound to the resolved commit by
the snapshot itself, and the declared artifact is held through the same
single-owner reservation the coverage artifact uses, so a committed or stale
report cannot satisfy the path.

```toml
schema_version = 2

[lanes.ui_mutation]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
cwd = "applications/webapp-ui-react"
argv = ["bash", "-c",
  "npm ci --offline --no-audit --no-fund && npx --no-install stryker run --reporters json"]
env = { npm_config_cache = "/opt/npm-cache", CI = "1" }
env_passthrough = ["PATH", "HOME"]
budget = "45m"
allow_argv_append = false

[lanes.ui_mutation.isolation]
snapshot_selection = "repository"

[lanes.ui_mutation.judge]
language = "javascript"
source_roots = ["applications/webapp-ui-react/src"]
base_source = "request"

[lanes.ui_mutation.judge.mutation]
format = "mutation-report-json"
artifact = "applications/webapp-ui-react/reports/mutation/mutation.json"
fail_under = 100.0
```

**Set `thresholds.break: null` in your Stryker config.** This is a mandate,
not a preference. Stryker exits non-zero when the score is under ITS OWN
thresholds, and assay reads a non-zero exit as R0's `COMMAND_FAILED` — so a
break threshold makes Stryker's judgment pre-empt assay's, and the lane fails
at R0 before assay ever reads the report. With `break: null` the exit status
carries only crash information and assay judges the score.

**What assay declares, and what it deliberately does not.** `judge.mutation`
on an ingested lane takes exactly three keys: `format`, `artifact`,
`fail_under`. `jobs`, `max_mutants`, `operators` and `equivalence_artifact`
are **refused at load** with a message saying why: they are assay's own
execution policy, and assay decided none of it — Stryker's config chose the
mutators, the concurrency and the ceiling. Declaring them in two places is the
P1 hazard; recording them in the verdict under assay's name would be worse.
`budget_per_candidate`, `shard_index` and `shard_count` are refused for a
related reason: they bound or partition an execution assay performs, and here
assay executes no mutant at all.

**`fail_under` is the mutation-score floor, and it is recorded on the wire.**
Any value in `0.0..100.0` loads. The floor lands in the verdict as
`judgment.r2.fail_under`, which is REQUIRED under `producer = "ingested"` and
FORBIDDEN under `"native"` — a native R2 has no floor at all, so a native
document carrying one would record a policy that nothing applied. The score is
`killed / (killed + survived)` as a percentage; `budget_exceeded`,
`equivalent` and `discarded` are all outside that denominator, each for the
reason its own row below gives. A claim with recorded survivors is
`FAIL`/`MUTANTS_SURVIVED` **iff** the score is below the declared floor, so an
ingested lane at `fail_under = 90.0` can PASS while listing the survivors it
tolerated — and `assay verify` re-derives that same PASS by reading the floor
FROM the document rather than assuming one. Up to schema v9 a lower floor was
refused at load, because v9 had no field that could record WHICH floor
applied; that refusal is gone with the field that replaced it, and only the
`0.0..100.0` range check remains. (**B050**, schema v10 — see the migration
notes.)

**The status map**, each direction chosen for the visible-failure side:

| Stryker status | assay | why |
|---|---|---|
| `Killed` | `killed` | — |
| `Survived` | `survived` | — |
| `NoCoverage` | `survived`, **and** listed in `judgment.r2.survived_uncovered` | a mutant no test exercised is not killed — and it is the worst kind of survivor, so it is listed by position rather than buried in a count |
| `Timeout` | `budget_exceeded` | Stryker's per-mutant timeout IS the per-candidate budget. Never `killed`: a mutant that hung is not one the suite caught |
| `CompileError`, `RuntimeError` | counted in `judgment.r2.discarded` | an invalid mutant assay's native engine never emits. Excluded from the score's denominator, and counted — a report that could not compile most of its mutants measured far less than its score implies |
| `Ignored` | **refuses the lane** | see below |
| `Pending` | **refuses the report** | pending means the run did not finish; incomplete evidence is not weaker evidence |

**An `Ignored` mutant in scope refuses the lane.** Stryker marks a mutant
`Ignored` when your own config suppressed it (`mutator.excludedMutations`, or
a `// Stryker disable` comment). The v9 verdict has no field that can state
that fact, and both alternatives lie: silently dropping it is how a gate gets
made green from inside the mutation tool's own config, and folding it into
`discarded` would report a deliberately suppressed mutant as one that failed
to *compile*. So assay refuses, naming the mutant. Remove the suppression, or
move the line out of the lane's declared scope.

**What the verdict records.** `judgment.r2.producer = "ingested"`;
`producer_tool` = `{name, version, report_schema_version}` copied verbatim
from the report and **declared by artifact, not verified** — it is not a
`helpers[]` entry, because `helpers[]` records tools assay itself invoked;
`survived_uncovered` (untested lines, by position, deduplicated — it lists
places, not mutants); `discarded`; `lines_without_candidates` (in-scope
non-blank lines the tool produced no mutant for at all).

**`discarded` is declared, not verified — read it as the tool's word, not as
assay's finding.** Assay derives the number at ingest, by counting the
`CompileError`/`RuntimeError` mutants the report itself *lists*; `assay
verify` then checks that the wire value is an integer and is not negative,
and nothing else. That is the whole check, and it is deliberate (**B051**,
schema v10 — see the migration notes). A discarded mutant is, by that same
"listed" definition, absent from the document: it is in no mutation bucket,
it is in neither `candidate_count` nor `total`, and its line is not in
`lines_without_candidates` — so a truthful report that discarded 900 mutants
looks byte-for-byte like a truthful one that discarded none, and any upper
bound assay could impose would refuse the honest high-discard report rather
than an inflated one. Concretely: this field set to `9999` on a 109-mutant
ingested document passes `assay verify` clean. **What it cannot do is move a
status.** `discarded` is a count beside the payload and never enters the
mutation buckets, so the score's denominator is unaffected by construction —
an inflated value understates how much was measured, it can never manufacture
a green. If you consume this number, treat it the way you treat
`producer_tool`: evidence about the foreign tool, on the foreign tool's word.
`kill_attribution` is `"unattributed"` and cannot be anything else: a killed
mutant here proves the foreign tool's test command failed, not that it failed
for the reason the mutant created. Every mutant carries its operator as
`stryker:<MutatorName>` — a namespace assay owns, so a foreign mutator name
can never collide with a native operator.

**Scope is assay's, not the tool's.** Stryker mutated whatever its config told
it to; which mutants COUNT is decided by your lane's `mode` — under
`changed_lines` a mutant counts iff its start line is an added line of the
resolved diff, under `whole_target` iff its file is a declared target. Assay
does not read Stryker's own score.

**Refusals worth knowing before you hit them.** The report's `projectRoot`
must equal the directory your command ran in (the snapshot's project root
joined with `cwd`) — a report produced anywhere else is an artifact from
elsewhere. Every `files` key must resolve under a declared `source_roots`
entry. `projectRoot` itself must be present: the upstream schema makes it
optional, assay requires it, because it is the only field saying where the
report's relative keys are anchored. A mutant with no `replacement`, an
unknown status, or a `schemaVersion` major assay has never seen each refuse
with their own message. **Assay reads `mutation-testing-report-schema` major
1** — the major the committed real artifact carries. A later major is refused
as unproven rather than as broken: assay has no report in that shape to have
been measured against, and reading one as if the shape had held is the
assumption the version field exists to prevent. And a command that writes no report at all is
`NO_MEASUREMENT`, never a pass.

**And, new in this release: the report's embedded `source` must be the text
the judged commit actually carries.** For every measured file, assay reads the
committed blob back out of the snapshot your command ran in and compares it
with the `source` the report embeds; a mismatch is
`ERROR`/`UNREADABLE_ARTIFACT`, naming the file. This is a real check rather
than a formality, because assay does not merely quote that text — **every
mutant's line and byte span, and every `lines_without_candidates` entry, is
derived from it.** A report about different text produces positions that are
spelled with your commit's paths and are wrong about your commit's files.

The comparison folds **line endings to `\n`** and **ignores one trailing
newline**; everything else is byte-exact. So a CRLF checkout is fine, and a
missing or extra final newline is fine, and **a reformatted or transpiled
source is not** — that last one is deliberate: if a formatter or a
transpilation step rewrote the file before the mutation tool read it, the
mutants were applied to the rewritten text and carry its line numbers, which
are not your commit's. There is no warning mode and no opt-out key; a switch
that turns this off from inside the lane file would be the lane disabling the
check that is judging it. Three things cause a mismatch in practice, and the
refusal names all three: the report is **stale** (the tool ran before your
last edit), the tool **rewrote** the source before mutating it, or the report
is **foreign** (it describes another checkout). The remedy is the same for
all three — run the mutation tool inside the lane, against the committed tree,
so the report assay reads is the one this commit produced. A measured file the
commit does not track at all is the same refusal, for the same reason: "the
commit has no such content" is the strongest content mismatch there is.
(**B052**, this release — see the migration notes.)

## Browser coverage of a UI as an R1 lane

Every JavaScript lane shape above judges UNIT-level Vitest coverage. A
Playwright/browser suite exercises the same UI a different way — through a
real DOM, real user events, a real running build — and can be judged too,
with no assay change: `judge.language = "javascript"` and
`format = "coverage-istanbul-json"` are the SAME declaration either way,
because `vite-plugin-istanbul` (built on `istanbul-lib-instrument`, the same
instrumenter core `@vitest/coverage-istanbul` uses — verified against this
wave's own committed lockfile,
`tests/fixtures/coverage/probe-js-vite-plugin-istanbul/package-lock.json`:
`vite-plugin-istanbul@9.0.1` depends on `@babel/generator`,
`@istanbuljs/load-nyc-config`, `espree` and `istanbul-lib-instrument@^6.0.3`
— there is no `babel-plugin-istanbul` anywhere in the tree) produces the
identical artifact shape a Playwright run's own `window.__coverage__` dump
already is.

### The recipe

All of this happens INSIDE one lane, in the snapshot — no different in kind
from B041(a)'s offline-install pattern, just a longer pipeline:

1. `npm ci --offline` (B041's own pattern — the snapshot's dependency closure
   is rebuilt from the image-baked cache, never assumed present).
2. `vite build --mode coverage` with `vite-plugin-istanbul` configured
   `forceBuildInstrument: true` — REQUIRED; the plugin does not instrument a
   production build by default (its own README: "Optional boolean to enforce
   the plugin to add instrumentation in build mode. Defaults to false"),
   so omitting it silently produces an uninstrumented `dist/` with no
   coverage capability at all, not a refusal.
3. Serve the build (`vite preview`, or the app's own nginx image) reachable
   from wherever the browser driver runs.
4. Drive the REAL suite against it — this project's own pattern is a
   Python-driven Playwright suite (`pytest -m browser
   tests/e2e/ui/<app>`) — with a fixture that reads `window.__coverage__`
   after every test and merges the maps (`istanbul-lib-coverage`) into one
   `coverage-final.json`.
5. Declare the lane exactly like any other JavaScript R1 lane:
   `format = "coverage-istanbul-json"`,
   `source_roots = ["applications/<app>/src"]`.

**Measure that the artifact's keys are the ORIGINAL `src/**/*.tsx` paths, not
`dist/`, before trusting this pattern in your own project.**
`vite-plugin-istanbul` instruments PRE-transform source, so a correctly
configured build keys its coverage map by the real source path — proved here
against a real, committed artifact
(`tests/fixtures/coverage/coverage-istanbul-json.vite-plugin-istanbul.json`,
`tests/fixtures/coverage/PROVENANCE.md`'s own section, and
`tests/test_coverage_parsers_vite_plugin_istanbul_artifact.py`) rather than
assumed from the plugin's own description. Nothing about the parser changes
for this producer: it is the SAME `coverage-istanbul-json` format every
Vitest artifact in this project already declares, read by the identical,
unmodified code.

`WHERE` is your environment tool's job, not assay's (DESIGN-GUIDE §4): the
tester serving the preview build must be reachable from wherever the browser
driver runs, and the UI needs its backend reachable too (a `requires.services`
declaration, or an `infrastructure` fact naming the backend route, B013).
Scope such a lane `S3`, declared — it depends on more than the snapshot
alone.

### The limit, stated once

The UI CODE this lane judges is the snapshot's — fully bound to the commit,
exactly like any other R1 lane. The API it talks to at runtime is the
DEPLOYED image's — an unverified, declared fact about which backend was
actually running, not something this lane's own coverage claim can attest to
(the same gap B004 exists to close for provenance generally). Until B004
ships verified provenance, an S3 R1 verdict from a lane shaped like this
binds the UI's own code, not the whole system it was exercised against.

A DETACHED `assay judge <artifact>` verb — judging evidence a deployed image
produced outside any snapshot, rather than a command this lane's own
argv ran — is the larger ask this pattern deliberately avoids needing.
**Do not build it before B004**: it would be the first assay judgment with no
commit binding of its own, which is exactly the class of claim this project
exists to refuse making.

## CMRU / tester-unified integration

CMRU's project `cmru.toml` owns the exact gate command. `tester-unified` should **not** bake an
ambient Assay version: that would make a consumer's evidence depend on whichever image happened
to be rebuilt. Instead a consumer pins a wheel in its own gate setup, or vendors the verified
zipapp as an explicit input. A CMRU project can run the latter through its existing gate:

<!-- assay-doc-example:skip reason="run-gate.toml lane config, not an assay lane file -- has no schema_version/[lanes] table and is not parsed by assay's loader" -->
```toml
[lanes.assay]
kind = "assay"
assay_lane = "unit"
environment = "tester-unified"
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<version>.pyz"]
budget = "20m"

[lanes.assay.pins.assay]
version = "<version>"
sha256 = "tools/assay/assay-<version>.pyz.sha256"
```

The product owns the `assay.toml` lane and the pinned Assay artifact; `run-gate.py` owns the isolated
execution boundary. A consumer invokes it as `./run-gate.py assay`; see run-gate's own `CONSUMERS.md`
for orchestration mechanics.

### Preflighting a gate environment with `assay lanes --json` (B044)

A gate tool that owns WHERE a lane runs (`ciu`, `run-gate.py`) needs to know
WHAT a lane needs from its environment before it launches a container for
it — does this project even declare a `javascript` lane, and if so, does the
chosen environment actually have Node on `PATH`? Reading `assay.toml`
directly duplicates assay's own loader and its own registry (which rigor
levels THIS installed build actually reaches for a language), and re-running
`assay run` just to find out is the thing a preflight exists to avoid.
`assay lanes --json` answers both without executing anything:

```bash
assay lanes --json --file assay.toml
```

```json
{
  "inventory_schema": 1,
  "assay_version": "3.2.0",
  "lanes": [
    {
      "name": "ui_unit",
      "scope": "S1",
      "rigor": ["R0", "R1"],
      "enforcement": "gate",
      "language": "javascript",
      "rigor_reachable": ["R1"],
      "coverage": {
        "format": "coverage-istanbul-json",
        "artifact": "applications/webapp-ui-react/.assay/coverage-final.json",
        "producer": null
      },
      "mutation": null,
      "canary": null,
      "base_source": "request",
      "external_tools": [],
      "argv0": "bash",
      "env_required": [],
      "environment_command": false,
      "infrastructure_facts": [],
      "budget": "15m",
      "cwd": null,
      "link_paths": [],
      "snapshot_selection": "repository"
    }
  ]
}
```

A gate wrapper reads this the way `assay run` would, without running it:

- **`rigor` vs `rigor_reachable`.** A level in `rigor` but absent from
  `rigor_reachable` (e.g. a lane declaring `"R2"` for a `language` this
  installed build has not wired R2 for) is a refusal `assay run` will hit
  later; a gate that checks this first turns a mid-run `ERROR`/
  `BAD_LANE_CONFIG` into a preflight failure with the same cause, sooner.
  `rigor_reachable` is `[]` for a `language` this build's registry does not
  know at all — `assay lanes --json` never raises for that, it just reports
  an empty capability set, exactly like it never raises for a mismatched
  `rigor`.
- **`language` selects the toolchain the environment must provide.**
  `"javascript"` needs `node`/`npm` on `PATH` (B041); this is a fact about
  the LANE, read once, instead of a fact CIU's own environment table has to
  restate per project.
- **`base_source`** tells a gate whether it must pass `--request-base` —
  `"request"` — or must not — `"declared"` — before it ever calls
  `assay run`, instead of discovering the mismatch as a refusal (or as a
  second, consumer-side spelling of the same fact, e.g.
  `[testing.lanes.<l>] request_base = true` duplicating what the lane
  already declared). `null` means the lane has no comparison base at all
  (no `judge` table, `judge.mode = "whole_target"`, or neither `R1` nor
  `R2` declared) — passing `--request-base` to one of those is refused by
  `assay run` for the identical reason.
- **`external_tools`** and **`argv0`** are what a `MISSING_EXTERNAL_TOOL`-
  style preflight actually checks against the environment's `PATH`, without
  a gate having to parse `argv` itself. Since schema v9 a lane that declares
  [`cwd`](#run-the-lane-somewhere-other-than-the-project-root-cwd-b043) no
  longer needs a `bash -c "cd … && …"` wrapper, so `argv0` on such a lane
  now names the real command instead of `bash`. A lane that still carries
  the wrapper still hides its command behind `argv0 = "bash"`, exactly as it
  does from `assay run`'s own preflight. **In this release, `external_tools` is
  `()` for every shipped adapter** (Python `adapters/python.py:806`, SQL
  `adapters/sql.py:671`, Go `adapters/go.py:501`, JavaScript
  `adapters/javascript.py:322` — none declares a nonempty tuple), so this
  field is structurally always `[]` today, not a per-lane fact a preflight
  can meaningfully branch on yet. A gate consumer should not build a
  `MISSING_EXTERNAL_TOOL` preflight around this field expecting it to name
  `node`/`npm` for a `javascript` lane — that check today has to come from
  `language` itself (the paragraph above), not from `external_tools`.
- **`environment_command`** is a boolean, not the probe's own argv: a gate
  only needs to know ONE declares a probe must pass in the invoking
  environment before snapshot work, never to re-implement or repeat it
  (DESIGN-GUIDE §4).

- **`cwd`** (B043, schema v9) is the lane's declared working directory or
  `null` when it declared none. `null` is the honest answer for that lane,
  not a placeholder — `"."` would be a value the file never wrote. A
  preflight that wants to check a directory exists in the environment reads
  this rather than parsing a `bash -c "cd …"` wrapper out of `argv`.
- **`link_paths`** (B041(b), schema v9) is the declared
  `[lanes.<n>.isolation] link_paths` list, `[]` when the lane declared none.
  A non-empty list tells a gate that this lane's snapshot will NOT be purely
  committed objects — see [Linking a dependency closure into the
  snapshot](#linking-a-dependency-closure-into-the-snapshot-link_paths-b041b).

`inventory_schema` stays `1`: it changes when an EXISTING key's meaning
changes, never merely because a new key was added — which is why `cwd`,
`link_paths` and `coverage.producer` becoming really declarable at v9 did not
bump it. A consumer written against the previous release, which saw them
always `null`/`[]`, reads real values now with no key-handling change.

A lane file that fails to load exits `2` with the loader's own message on
stderr and **no JSON on stdout** — never a partial document — exactly like
the text form of `assay lanes`.

## Size a mutation lane before running it

For any R2 mutation lane, inspect the workload without executing its command or creating mutant
snapshots:

```bash
assay plan worker_lane --file assay.toml
```

The JSON output reports deterministic candidate IDs, total/per-file/per-operator counts, declared
worker concurrency, and runtime estimates. The candidate IDs and counts are the SAME ones a real
`assay run` of that lane executes — plan resolves them against the same declared source roots, the
same adapter, and the same `max_mutants`/operator selection.

`estimated_serial_seconds`/`estimated_wall_seconds` are a **declaration-derived upper bound, not a
measurement**: they are `candidate_count x budget_per_candidate` (falling back to 60 s per candidate
when the lane declares no bound), divided by declared `jobs` for the wall figure. Assay never times a
baseline to produce them. Treat them as "no longer than", not "about". Use those facts to choose an
optional per-candidate bound:

<!-- assay-doc-example:skip reason="mutation sub-table fragment; the surrounding consumer lane supplies schema_version and the rest of the closed lane grammar" -->
```toml
[lanes.worker_lane.judge.mutation]
jobs = 4
max_mutants = 100
operators = ["python:compare-swap"]
budget_per_candidate = "300s"
```

`python:uuid-equality-swap` and `python:enum-comparison-swap` are **withdrawn**
(A-326). They shipped in 2.3.0 and were measured to produce a byte-identical
subset of `python:compare-swap`'s own sites — same span, same replacement — so
declaring them alongside `compare-swap` emitted every shared site twice and
added no coverage. A lane still naming either is now refused at load; delete
them and keep `python:compare-swap`, which already covers `==`/`!=` swapping.
The two names remained *spellable* at schema v7 so that verdicts already
emitted by 2.3.0/2.4.x kept verifying. **Schema v8 removes the spellings**
(A-331), discharging A-326's own "the spellings go at the next bump" at the
bump it named: neither name appears in the packaged schema's per-language
`oneOf` any more, so a v8 verdict naming one fails validation. Nothing you
need to do differs — the lane-config refusal was already in force at 2.4.2 —
but note two consequences:

- Your v7 verdicts still verify, with a **v7** `assay`. Under v8 they are
  refused on `schema_version` alone (hard cut, A-170), which is why removing
  the spellings costs nothing v8 had not already cost.
- A lane file still carrying either name gets the *withdrawn* refusal naming
  `python:compare-swap` as the replacement, **not** a bare "unknown
  operator(s)" — the withdrawn check deliberately runs first now that the
  catalogue no longer spells them.

A candidate that exceeds this bound is recorded in `budget_exceeded`; the lane continues with other
candidates.

Progress is opt-in. `assay run worker_lane --progress /tmp/worker.progress.jsonl` appends one compact
JSON object per line -- a `run` header naming the commit and start time, then one event after the
baseline and one after every completed candidate, each flushed as it is written, so a monitor can
tail it live. Without the flag no progress file is written at all, and assay never chooses the
location itself. Choose a path OUTSIDE the repository (or a gitignored one): assay's own clean-tree
precondition refuses `NO_MEASUREMENT`/`DIRTY_TREE` on the next run of that lane if the progress file
lands in the work tree. The verdict does not name the destination -- the caller already chose it,
the same way it does for `--verdict-json`.

When a command fails or times out, read the optional top-level
`result_stdout_tail` / `result_stderr_tail` fields for the final error output.
Each tail is at most 64 KiB; its paired `*_dropped_bytes` field says how much
head-side output was omitted. Absent tails mean no captured-output contract
applies (for example, the command never started).

## Adopting a v2-capable release

Verdict schema v8 and lane schema v2 are both hard cuts (no dual-version verifier, no
compatibility shim, no upgrade-in-place — see
[the design guide](DESIGN-GUIDE.md#snapshot-selection-an-affirmative-materialisation-boundary-not-a-sandbox-b006a)
for why interpreting an old lane file as if it declared the new grammar would be exactly the
shadowing default this project forbids elsewhere). That cuts both directions at once: a v2-capable
assay refuses a v1 lane file's now-required `[isolation]` table with `BAD_LANE_CONFIG`, and a
v1-pinned assay cannot parse a v2 file's `[isolation]` table at all — it is simply an unknown key.

So the two moves are **one atomic, consumer-owned commit, never two**:

1. repin your gate to a v2-capable Assay release (wheel or `.pyz`, verified per
   [Obtain and verify an immutable release](#obtain-and-verify-an-immutable-release) above);
2. in the **same commit**, bump `assay.toml`'s `schema_version` to `2` and add the now-required
   `[isolation]` table to every lane that declares R1, R2 or R3.

Splitting these across two commits leaves a real window where the gate is red for a reason that
has nothing to do with your product: land the pin one commit and the schema bump the next, and the
commit in between either runs a v1 assay against a v2 file (rejected as an unknown key) or a v2
assay against your still-v1 file (rejected as a missing `[isolation]` table) — a self-inflicted
outage with a one-line fix that is obvious only once you already know why the gate went red.

## Practices that prevent the failures we actually hit

Every item below cost someone real time — here, in a consumer's repo, or in
assay's own development. None is stylistic.

### When a lane refuses, read the one stderr line — the document does not carry the sentence

The verdict document records a closed `(outcome, reason_code)` pair and no
free text. That is deliberate: the enumerations are a wire contract, and a
consumer's gate must be able to switch on them without parsing prose. But the
pair alone does not say WHICH file was unreadable, WHICH declaration was
unresolvable, or WHICH target failed to resolve — and until 4.2.0 that
sentence, which assay had all along, was discarded the moment the refusal
became a claim.

Every refusal now prints exactly one line, in exactly this shape:

```
assay: {OUTCOME}/{REASON_CODE}: {the sentence naming the cause}
```

for example:

```
assay: ERROR/FORMAT_MISMATCH: declared coverage format 'coverage-py-json', but the artifact's content does not match that format's own signature. The lane's argv may have changed coverage format without updating judge.coverage.format, or the wrong file was named as judge.coverage.artifact.
```

(One physical line — wrapped here only by your terminal.)

Where it goes:

- **From the CLI**, to **stderr**. Machine output is unaffected: stdout still
  carries the three-line run summary, or the whole verdict document under
  `--verdict-json -`. A gate that captures stderr into its log needs no
  change to benefit.
- **From a library caller**, to whatever stream you pass as `diagnostics=` to
  `assay.runner.run_lane`. Nothing is written to the process's stderr behind
  your back; omit the argument and assay stays silent, exactly as before.

Two properties worth relying on: the line appears **once** per refusal (one
emitter, called where the error becomes a claim or a verdict — never twice
under two spellings), and the text after the second colon is the raising
layer's own message, **byte for byte**. It is diagnostic text, not a wire
field: do not parse it, and do not gate on it. Gate on the exit code and the
document; read the line when you need to know why.

**There is no exception to "exactly one line".** A first cut of this feature
left five refusals silent — a dirty work tree (on both the snapshot and the
direct-R0 path), a moved `HEAD`, a missing external tool, an unset
`env_required` variable, and a malformed `--shard` — because those sites
refuse from a literal `(status, reason_code)` and had no message to copy.
They now compose one where the fact is known, so all five name it:

```
assay: NO_MEASUREMENT/DIRTY_TREE: 2 uncommitted file(s) in /repo -- a higher-rigor lane measures the RESOLVED COMMIT from a snapshot, so an uncommitted change is invisible to it. Commit or stash, then re-run. Affected: src/a.py, notes.md
assay: NO_MEASUREMENT/HEAD_CHANGED: HEAD moved between the resolution of the commit under judgment and the start of this lane: the verdict would be labelled 1a2b... but /repo is now at 9f8e... Re-run against the current HEAD.
assay: NO_MEASUREMENT/MISSING_EXTERNAL_TOOL: the 'go' adapter needs the external tool 'go', which is not on PATH in this environment -- install it, or run the lane where it is available. Declared tools: go
assay: ERROR/BAD_LANE_CONFIG: lane 'unit' declares env_required ['DATABASE_URL'] which is not set in the invoking environment -- assay refuses to run a lane whose declared inputs are absent rather than measure it with them missing. Set DATABASE_URL, or drop it from 'env_required'.
assay: ERROR/BAD_LANE_CONFIG: --shard 'one-of-two' is not a shard spec: it must be INDEX/COUNT with zero-based integers and 0 <= INDEX < COUNT (for example --shard 0/4).
```

The complement of that rule also holds, and matters more: **a refusal whose
claim the verdict does not carry prints no line.** One R2 refusal — a
baseline that declared a `judge.mutation.equivalence_artifact` and did not
write it — is decided before the lane's own command outcome is known, and if
that command then fails, the document's R2 claim is `FAIL`/`COMMAND_FAILED`
and the equivalence refusal is discarded. Its line is discarded with it. Every
line you see on stderr corresponds to a refusal that is in the document beside
it.

**A lane that runs out of its budget writes its verdict too.** Until 4.2.0, a
lane whose command outlived `budget` exited 4 with your reserved
`--verdict-json` never written — so a gate that archives the document had
nothing to archive for exactly the runs most worth looking at. It is now
always written, on both dispatch paths, and `assay verify` accepts it: the
outcome is `BUDGET_EXCEEDED`, the reason code `LANE_TIMEOUT`, and the run's
own `argv`, timing and output tails are in it. If the timeout struck during
snapshot cleanup after the work had finished, the affected claim says
`BUDGET_EXCEEDED`/`LANE_TIMEOUT` and not `ERROR`/`GIT_FAILED` — a lane that
ran out of time is told so, never given a Git failure that did not happen.

### Your existing CI script probably cannot be the lane command

A lane command runs inside an **ephemeral snapshot of a commit**, not your
working tree, with no deployed services around it. Four requirements, and a
script written for CI usually fails at least one:

- **No mandatory arguments** beyond what `argv` declares. A wrapper whose first
  positional parameter is required exits non-zero before doing anything, and
  every mutant is then `crashed`.
- **Hermetic.** No dependency on a running deployment, a named container, a
  shared network, or anything discovered from the host. The snapshot has your
  committed files and nothing else.
- **Writes every artifact it declared**, at the right moment — see the SQL
  ordering rule above, which generalises: an artifact assay compares must be
  written before the step that can fail, or it will be missing exactly when it
  matters.
- **Leaves the tree clean.** Anything written into the snapshot and left
  untracked-but-present makes the run `NO_MEASUREMENT`/`DIRTY_TREE`.

A real example of all three failing at once: a consumer's schema gate took a
mandatory positional path, contained no dump step at all, and drove `docker`
against its *deployed* application network. None of that is visible from
reading the script — it only surfaces when a snapshot tries to run it.

### Dependency closures come from the image, never the working tree

The "hermetic" rule above has one consequence specific to JavaScript/
TypeScript: `node_modules` is gitignored, so it does not exist in ANY
snapshot (B041) — Python's venv and Go's `GOMODCACHE` are both out-of-tree
and never met this. The gate IMAGE carries a populated npm cache (built from
the committed lockfile, exactly as it carries the pinned Python/Go
toolchains), and the lane's own argv rebuilds the closure OFFLINE from that
cache before running the pinned, `--no-install` test runner — never an
ambient `node_modules` the image or the invoking checkout happens to have
lying around, which would make the same lane file mean a different, unpinned
thing depending on where it runs. See
[JavaScript lanes and the dependency closure](#javascript-lanes-and-the-dependency-closure)
for the worked pattern.

### Add rigor in order, not all at once

`R0` → `R1` → `R2`, each green before the next. R3 requires R1 by construction.
A lane that declares everything on day one fails for several unrelated reasons
simultaneously, and you cannot tell them apart.

### Keep assay's own output out of the repo under test

Writing `--verdict-json` to a path *inside* the project makes the tree dirty,
and the run refuses `NO_MEASUREMENT`/`DIRTY_TREE` before attempting anything.
We hit this while hand-driving a lane. Write verdicts outside the tree, and
gitignore every declared artifact.

### If equivalence depends on an artifact, that artifact must be byte-reproducible

The `pg_dump --restrict-key` obligation above is one instance of a general
rule. Any producer that embeds a timestamp, a random key, an absolute path, or
a hash-seed ordering will differ between two runs over identical state — and
the whole `equivalent` bucket then silently empties, so **nothing goes red**.
Test it directly: run your artifact step twice against unchanged state and
`cmp` the results. Where practical, do that check *inside* the command so it
fails loudly rather than degrading quietly.

### Read `outcome` and `reason_code` — non-green is not always failure

| terminal | what it means | what to do |
|---|---|---|
| `INCONCLUSIVE`/`NO_MUTANTS` | a supported analysis ran and found nothing eligible | usually fine; check your `source_roots` and changed range |
| `INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT` | every mutant provably changed nothing, so the run says nothing about your tests | almost always a misconfigured artifact — start with reproducibility |
| `BUDGET_EXCEEDED`/`MUTANT_LIMIT_EXCEEDED` | discovery hit `max_mutants` and **stopped before submitting** | a refusal, not a truncated sample. Raise the cap or narrow the change |
| `NO_MEASUREMENT`/… | assay declined to claim anything | fix the environment; re-running unchanged will refuse again |
| `ERROR`/`EXEC_FAILED` | your command failed in a way that is not a kill | read the command's own output; a crashed mutant outranks every other bucket |

### Check `judgment.resolved.base_resolution` after a pre-gate merge

If your workflow merges the base branch into a feature branch before gating
(a routine pre-merge sync), an R1 or R2 lane's `HEAD` is a merge commit.
`judgment.resolved.base_resolution` says which of two ways `judge.base` was
resolved against that HEAD: `"merge-base"` (the usual case — `git merge-base
<declared-base> HEAD`) or `"first-parent"` (HEAD's own pre-merge tip, when
HEAD is itself a merge commit). The two can differ enormously: a lane judged
right after merging main in can see its changed-line/mutation scope narrow to
"whatever the merge itself touched" rather than the branch's own accumulated
work, with no other signal that anything unusual happened. `base_resolution`
is present only when `judgment.resolved.base` is (a lane with no comparison
base to resolve has nothing to classify); a consumer that gates on "did R1/R2
see the whole change" should check it rather than assume `base` alone tells
the story.

### Delegating the comparison base to the invoking gate request (B019)

A lane that must stay portable across branches and worktrees can require
changed-line judging without hardcoding *which* commit it judges against:

<!-- assay-doc-example:skip reason="judge sub-table fragment; the surrounding consumer lane supplies schema_version and the rest of the closed lane grammar" -->
```toml
[lanes.unit.judge]
language = "python"
source_roots = ["src"]
# `judge.base` is ABSENT. This lane's invoker owns the base identity.
base_source = "request"
```

The invoker then supplies it per run:

```bash
assay run unit --request-base "$MERGE_TARGET" --verdict-json verdict.json
assay plan mutate --request-base "$MERGE_TARGET"
```

`--request-base` takes a ref *or* an already-resolved commit, and either goes
through exactly the merge-base resolution `judge.base` always used; the result
is recorded once in `judgment.resolved.base`, with `base_resolution` beside it
as usual. Nothing downstream can tell — or needs to tell — which owner
supplied it.

Three refusals bound this, and each names the one line to change:

| situation | what happens |
|---|---|
| `base_source = "request"`, no `--request-base` given | `ERROR`/`BAD_LANE_CONFIG`. Never a fallback to `HEAD` or a default branch: a changed-line judgment whose base was guessed is not a changed-line judgment. |
| `judge.base` declared **and** `--request-base` given | `ERROR`/`BAD_LANE_CONFIG`. Whichever side lost a precedence contest would be configuration nothing reads, and therefore configuration that cannot fail loudly if it is wrong. Delete one. |
| `--request-base` given to a lane that reads no base (R0/R3 only, or `mode = "whole_target"`) | `ERROR`/`BAD_LANE_CONFIG`, for the same reason `judge.base` is refused there. |

`base_source` is legal only on a lane declaring R1 and/or R2 under
`mode = "changed_lines"` — it is a policy *about* `judge.base`, so it is legal
exactly where `judge.base` is.

#### Migrating an existing lane, and the one case that costs something

A lane pinning a **frozen SHA** migrates for free: delete `base = "<sha>"`, add
`base_source = "request"`, and have the invoker pass that same SHA. Nothing
about what gets judged changes.

The case that genuinely costs something is a lane pinning a **symbolic,
self-updating ref** — `base = "origin/main"` is the common one, and it is what
this estate's own `ciu/assay.toml` lane declares. Such a lane works today from
any checkout with no orchestrator at all: `assay run ciu` just resolves
`origin/main`. Delegating it means that same command becomes a hard refusal
unless an invoker supplies `--request-base`, because refusal (1) above does not
except the lane that *could* have resolved something on its own.

That is the real trade and it is not free. It is the deliberate consequence of
refusing precedence rather than picking it, so decide per lane:

- **Keep `judge.base = "origin/main"`** when the lane must stay runnable by
  hand, standalone, from a developer checkout. It simply is not a delegating
  lane, and nothing forces it to become one.
- **Move to `base_source = "request"`** when an orchestrator owns branch
  awareness and every real invocation comes from it — which is the case
  B019 exists for. Expect the standalone `assay run <lane>` invocation to stop
  working, and give the humans a wrapper that passes `--request-base`.

There is no middle setting, on purpose: a lane that declared both a default and
a delegation would be back to a precedence rule, and one of the two would be
config nothing reads.

### `judge_provenance`: which build emitted this verdict (B018)

`assay_version` is a string; any process can print one. An assay running from
a real build artifact additionally records the artifact itself:

```json
"judge_provenance": {
  "name": "assay",
  "version": "2.5.0",
  "artifact": "wheel",
  "digest_algorithm": "sha256",
  "digest": "2bf81187b3158a010b4f0d8712a7414779f0ee97b72251db0a072314412e82d4"
}
```

`artifact` is `"wheel"` or `"zipapp"`; a release publishes both, with a
`.sha256` sidecar each, and their digests necessarily differ — so the kind is
recorded rather than left for you to guess which file to compare against. The
digest is the artifact's own sha256: for a zipapp, the `.pyz` is hashed
directly; for a wheel install, it is the digest the installer recorded in PEP
610 `direct_url.json`, which is the only statement about the wheel that
survives its own installation.

**The field is optional, and its absence is meaningful.** An invocation with
no identifiable build artifact — a source checkout, an editable install, or an
import that shadows an installed distribution — records **no** identity rather
than a partial or invented one, and prints the reason on stderr. If your gate
resolves and verifies a judge binary before running it, and needs the evidence
bound to that binary, do not merely read the field: **demand it**.

```bash
assay run unit --require-judge-provenance --verdict-json verdict.json
```

That refuses `ERROR`/`BAD_LANE_CONFIG` before any lane work if the running
assay cannot identify itself, so you never receive evidence you cannot
attribute. Compare `judge_provenance.digest` against the digest you verified
on download; `judge_provenance.version` equals the top-level `assay_version`
for any artifact assay itself produced.

#### Which install shapes can be identified — read this before you demand it

`--require-judge-provenance` is a hard refusal, so **how you installed assay
decides whether your gate can use it at all.** The rule is PEP 610's: an
artifact identity exists only where the installer recorded one, and it records
one only for a *direct* install.

| how assay got there | identified? | why |
|---|---|---|
| **pip**: `pip install ./assay-<v>-py3-none-any.whl` | **yes** (`wheel`) | direct file install; pip records the wheel's sha256 in `archive_info.hashes` |
| **pip**: `pip install https://…/assay-<v>-py3-none-any.whl#sha256=…` | **yes** (`wheel`) | direct URL install; pip records the digest, and stores the URL with the `#fragment` already stripped |
| running the `.pyz` (or `PYTHONPATH=<pyz>`) | **yes** (`zipapp`) | the archive is on disk and is hashed directly |
| **`uv pip install` of a wheel, even from a direct URL** | **no** | measured on uv 0.12.1: it writes `direct_url.json` with `"archive_info": {}` — the record exists but carries **no digest**, so there is nothing to report |
| **`pip install assay` / `pip install assay==2.5.0` from an index** | **no** | PEP 610 writes `direct_url.json` only for direct installs. An index install records **no** artifact identity anywhere on disk, and none can be recovered afterwards |
| `pip install -e .`, `pip install <directory>` | no | no build artifact exists to hash |
| a source checkout, or a `sys.path` entry shadowing an install | no | the running code is not the artifact's code (refused by name) |

Note the rule is *"the installer recorded a digest"*, not *"the install was
direct"* — the `uv` row is a direct install that still yields no identity,
because recording the digest is the installer's choice and uv currently
declines. If your gate demands provenance, **pin the installer as well as the
artifact**, and verify one real install of your actual image before relying on
it.

The index-install row is the one that surprises people, and it matters
specifically for **a CI runner image that pip-installs assay from a private
index at image-build time**. That image is perfectly well pinned, and assay
still cannot identify itself inside it — there is nothing on disk to identify.
If your gate demands provenance, install the judge into the image **with pip,
from the wheel file or its URL** (or ship the `.pyz`), not from a bare
requirement specifier. Mounting the verified artifact into the runner works
too. The `.pyz` is the most robust of these: it depends on no installer's
metadata choices at all, because assay hashes the archive it is running from.

Assay refuses rather than synthesising something here, and that is deliberate:
a digest derived from the installed *files* would not equal the digest you
verified on *download*, so it would look like an identity while being
uncomparable — the invented fact this whole field exists to prevent (A-332).

### A green run over an empty subject is not a pass

If a claim's `total` is `0`, nothing was tested — and a gate that goes green on
it is telling you about its own emptiness, not your code. This bit assay itself
more than once: a release embargo iterated an empty tag list, and an audit
looped over an empty tool tuple. **Assert your subject is non-empty**, in your
own gates as well as in ours.

### Writing tests that R2 will not embarrass

R2 mutates your source and asks whether your suite notices. Two patterns
account for most of the mutants that survive a suite its authors were proud of.
Neither is exotic; both have been measured in this estate's own tooling.

**Assert what distinguishes a failure, not merely that one occurred.** If two
different causes raise the same exception type, `pytest.raises(ThatType)` cannot
tell them apart — a mutant that skips one `raise` and falls through to another
passes your test. A real case: a mutant let an HTTP status slip past its
`raise`, the next line failed to parse the body, and the *same* exception class
came out of the second branch. Assert the message, or construct the input so the
mutated path **succeeds** where the correct path refuses.

**A surviving mutant is sometimes a defect in your code's shape.** Given

```python
if   pinned == highest:  ...
elif pinned <  highest:  ...
```

the `elif` runs only when the values differ, so `<` and `<=` behave identically
there and **no test you can write will kill that mutant.** Restructure instead —
lead with `<`, then `>`, and let equality fall to the `else` — and the ordinary
equality test kills it. A redundant guard that makes an operator
non-discriminating is the mirror of a branch that cannot fire: see
[the design guide](DESIGN-GUIDE.md#3a-why-the-rigor-levels-are-not-redundant--three-techniques-three-defect-classes).

If every mutant in a run is inert, assay says so loudly with
`INCONCLUSIVE`/`ALL_MUTANTS_EQUIVALENT` rather than passing you — that run
established nothing about your tests.

### Never edit an expected artifact toward green

If a witnessed/expected artifact stops matching, re-witness it from a real run
and read the diff first. An expectation edited until it passes proves only that
you edited it. The same applies to frozen evidence: capture a new artifact,
never amend an old one.

### Pin the version you consume, and verify it

Take the `.pyz` (or wheel) from a release, verify its published `sha256`, and
pin it. Every release publishes a `.sha256` sidecar and a `release-manifest.json`
next to the artifact. Floating on "latest" means a schema cut can arrive on a
day you were not planning a migration.

### Efficiency

R2 cost is roughly *mutants × (snapshot + full command run)*, so the two levers
that matter are how many mutants you generate and how fast one command run is.
Narrow `source_roots` to what you actually judge, keep `max_mutants` at a value
whose *total* runtime you are willing to pay, and raise `jobs` only as far as
your command tolerates concurrent execution — a command that contends on a
shared fixture will get slower, not faster.

## What is not shipped

Assay has no remote worker, offsite dispatcher, or asynchronous fuzzing service. Its Python
R2/R3 execution is local and isolates each unit in Git-based scratch snapshots. A remote runner
would need an explicit artifact bundle, pinned toolchain/image digest, queue/auth contract,
result attestation, cancellation/timeout semantics, and a way to prove the returned verdict was
for the submitted commit. None of that is implemented or implied by the wheel/zipapp today.

## Go lanes: what exists today, and what a Go lane will require

**`judge.language = "go"` now resolves, at R1 only** (decision A-394).
Registration was deliberately sequenced *after* the statement-attribution
chain below, so a Go lane could never be runnable while the parser would
still report block extents as statement truth. **R2 and R3 are still refused**
`ERROR`/`BAD_LANE_CONFIG`: `generate_mutation_sites` is unconditionally
`UNSUPPORTED`, so there is no mutation producer for a Go lane to reach.

Read point 1 before you plan around this. The single most common way a Go
lane fails is not a missing test — it is a judging environment with no Go
toolchain, and assay will tell you so in those words rather than guessing.

What is worth knowing before you plan a Go lane, because two of these will
surprise an adopter who assumes Go behaves like Python:

**1. A Go lane needs a real Go toolchain on the machine that runs `assay`,
not only on the machine that ran the tests.** Assay normally consumes an
artifact and never re-runs your tools. Go is the exception: `go test
-coverprofile` records a block's byte extent plus a statement *count*, never
the statements' own positions, so assay re-derives those positions from your
source with a Go program it ships (`assay/helpers/go/stmtpos/`, stdlib-only,
run with `GOPROXY=off` and no network). The adapter declares
`external_tools = ("go",)`, so the effective-PATH preflight refuses before
anything runs:

```text
status: NO_MEASUREMENT
reason_code: MISSING_EXTERNAL_TOOL
```

If your gate container builds Go elsewhere and judges in a slim image, that
slim image needs `go` too. Why assay cannot just parse the profile harder:
[DESIGN-GUIDE §11, "Go statement positions"](DESIGN-GUIDE.md#go-statement-positions-come-from-the-source-never-from-the-profile-a-217a-239a-397).

**2. The coverage artifact and the working tree must be the same revision.**
Assay re-runs the segmentation over your source and joins it to your profile
on the exact block extents. If they disagree — a profile carried over from an
earlier commit, a file edited between the test run and the judgment, a
different toolchain — the lane refuses rather than attributing lines. What you
see is the reason code:

```console
$ assay run unit --file assay.toml
unit: ERROR/UNREADABLE_ARTIFACT (exit 2)
```

and the verdict document's R1 claim carries the same `ERROR` /
`UNREADABLE_ARTIFACT` pair with no coverage payload.

The refusal assay *raises* underneath that names the file and the disagreeing
extents:

```text
go statement attribution: 'internal/x.go': the coverage profile and the
source-side oracle disagree about which blocks exist, so they were not
produced from the same revision of this file. 1 record(s) only in the
profile (28.22,29.2); 0 only from the source (none)
```

The extents in it are spelled the way your `.out` file spells them, so they
can be grepped against the artifact directly. **Since 4.2.0 you get that text
from `assay run` too** — on stderr, as the one refusal line described under
["When a lane refuses, read the one stderr
line"](#when-a-lane-refuses-read-the-one-stderr-line--the-document-does-not-carry-the-sentence)
(B053). The verdict document still carries no free-text cause by design, so
the reason code is all a document-reading consumer gets; the sentence naming
the disagreeing extents is on the diagnostics stream.

The fix on your side is to regenerate the profile from the tree you are
judging — never to relax the check, which exists because attributing anyway
would publish a verdict about lines that are not the lines that ran.

**3. A verdict from a Go lane carries `helpers[]`.** It records which
toolchain actually derived those positions, because "which Go compiled this"
is part of what the verdict means:

```json
"helpers": [
  {
    "role": "statement-positions",
    "tool": "go",
    "resolved_path": "/usr/local/go/bin/go",
    "identity": "go version go1.25.14"
  }
]
```

`identity` is what the toolchain reported about ITSELF, from inside the
program it compiled and ran — not a version string assay looked up. Lanes for
every other language OMIT the key entirely: no helper ran, so nothing is
claimed, and `helpers: []` is refused rather than written. A Go lane whose R1
claim carries no coverage payload (an error, a refusal) also omits it — an
entry exists because it produced a claim, so one standing beside no claim
would describe work that judged nothing.

**4. One known limit, stated so you do not discover it in a review: a Go R1
claim is statement-granular TO THE LINE, not to the statement.** An uncovered
statement sharing a physical LINE with a covered one is still reported as
executed — `f := func() int { return 7 }` is two counted statements that both
genuinely begin on that line, and the line did run. This is line
granularity's own limit, which coverage.py shares; it is not fixed by the
source-side oracle and is not claimed to be. The oracle removes the
*fabrications* (a `func` signature line, a closing brace, a `case` label
reported as executable code); it cannot remove this one, because a verdict's
wire schema speaks in line numbers and telling two statements on one line
apart needs a column-granular claim.

**This is a ruling, not an omission (B055/A-413).** Three alternatives were
weighed — leave it as a documented limit, add a per-line "this line contains
an uncovered statement" marker, or go to full column granularity — and the
first was taken: the exposure is toward false PASS on an uncommon shape
(a `func` literal inline in an assignment, a `switch` case body on the `case`
line, `x := 1; y := 2`), coverage.py lanes have carried the identical limit
since assay's first release, and the other two are schema cuts. If your Go
code puts two statements on one line in code you care about gating, split the
line — that is the only thing that changes the measurement.

**5. A second known limit: assay does not judge Go sources carrying `//line`
directives — generated code.** Keep them out of `judge.source_roots`.

A `//line file:line` directive tells the compiler to report positions as if
they came from somewhere else, which is how `goyacc`, `peg`, `ragel` and
similar generators point diagnostics at their own input rather than at the
generated `.go`. `go test -coverprofile` records those remapped positions, so
such a file's coverage records name the *directive's* line numbers, not the
file's — Go's own `TestLineDup` fixture is 24 lines long and its profile
reports lines 100 to 105. `git diff` names physical lines. The two cannot be
joined, by anyone: this is not a check assay could relax.

So the rule is per FILE, and it costs you nothing unless you actually judge
one:

* a `//line`-bearing file with **no changed line in the judged set** is
  IGNORED. Its records contribute nothing and the lane proceeds normally.
  One generated file does not take your lane down.
* a `//line`-bearing file **inside** the judged set refuses:

```console
$ assay run unit --file assay.toml
unit: ERROR/BAD_LANE_CONFIG (exit 2)
```

The verdict's R1 claim carries that same `ERROR` / `BAD_LANE_CONFIG` pair and
no coverage payload. The refusal assay *raises* underneath it names the cause,
the file and the remedy:

```text
'internal/parser/y.go' carries coverage records whose positions were remapped
by a `//line` directive ... so its recorded line numbers are the directive's
and not this file's. This lane judges it: 3 changed line(s) in it are inside
judge.source_roots, and assay will not judge a file whose measured lines
cannot be matched to the lines a diff names -- that would report a clean
percentage over nothing measured. Generated sources belong outside the lane's
judge.source_roots (or its judge.targets)
```

As in limit 2 above, that text is visible to a caller of the evaluation layer
(`assay.evaluate.evaluate_coverage` / `evaluate_targets`) and **not** to an
`assay run` consumer, who gets the reason code — B053. `BAD_LANE_CONFIG` is
shared by several distinct causes on the Go path, so when you see it on a Go
lane, a generated file inside `source_roots` is one of the first things to
check.

The remedy is the same thing you would do for any generated file: keep it out
of `source_roots` (or out of `judge.targets`). What assay will not do is the
quiet alternative — measure the file's virtual line numbers, match nothing,
and report `0 executable of 0 changed` as a clean result. `0/0` is never
`100%`.

The witness for all of this is Go's own: `cmd/cover/cover_test.go`'s
`lineDupContents`, run through the real toolchain and committed with its
recipe at `nyxloom-trove/carve-assets/P27-recarve/`.

**6. Installing assay into a Go gate image needs no Python packaging work.**
The judge needs an *interpreter*, not a toolchain — assay is stdlib-only by
design (A-005) — and a `golang:1.25`-based image already has one:
`/usr/bin/python3` 3.13.5 on Debian trixie, against assay's own
`requires-python = ">=3.11"`. Measured, not assumed:

```console
$ docker run --rm --network=none <your-go-gate-image> python3 --version
Python 3.13.5
```

There is no pip and no ensurepip in that image, so the **zipapp is the
install path**: copy `assay-<version>.pyz` in, check it against its shipped
`.sha256` sidecar, and run it with the interpreter that is already there.

```console
$ sha256sum -c assay-<version>.pyz.sha256
$ python3 assay-<version>.pyz run unit --file assay.toml --verdict-json verdict.json
```

The verdict records which archive judged it —
`judge_provenance.artifact = "zipapp"` plus the archive's own sha256 — so a
consumer can bind a verdict to the artifact that produced it without trusting
a version string. The Go oracle ships INSIDE that archive and is staged out
to a temporary directory when it runs (`go run .` needs a real directory and
a zip member is not one), so nothing has to be unpacked or installed
alongside it.

**7. One Go module per lane, and the lane's project root is that module's
root.** A Go cover profile keys records by import path
(`example.com/svc/internal/store/x.go`) while `git diff` names the same file
`internal/store/x.go`. Assay bridges the two by **reading your module path
out of your own `go.mod`** — the nearest one at or above the directory
holding your `assay.toml`, and no higher than the repository top. There is
no lane key and no CLI flag for it, deliberately: it is your project's fact,
not a literal for you to restate (this is `covergate`'s `-module srdm` flag,
which DESIGN-GUIDE §5 names as an anti-pattern by example). You declare
nothing; it just works:

```console
$ python3 assay-<version>.pyz run unit --file assay.toml --verdict-json verdict.json
```

Two consequences worth planning around:

* **Put `assay.toml` at your module root.** If your repository holds several
  modules, give each one its own lane file at its own module root rather
  than one lane file at the top. A project root sitting *above* several
  modules does not silently pick one — the profile's keys will not be under
  whichever `go.mod` was found first, and the lane refuses
  `ERROR`/`UNREADABLE_ARTIFACT` with a message naming the key, the module
  path assay derived and the `go.mod` it came from.
* **A project root that is in no Go module at all refuses**
  `ERROR`/`BAD_LANE_CONFIG`, naming the paths it looked at. That is a lane
  configuration fault, not a coverage one, and it is reported as such.

**`go.work` is not supported this wave.** Nested modules never appear in
`go test ./...`'s own output, so a workspace's other modules are simply not
measured; a lane pointed at a workspace root surfaces as the first bullet
above rather than as a partial measurement. If you need a workspace, run one
lane per module.

The previously-documented library workaround — building `GoAdapter(module_path=…)`
yourself and calling `runner.run_lane` — is no longer needed for this, and a
`module_path` you construct by hand that disagrees with your `go.mod` is now
refused rather than given precedence.

**8. A worked Go lane, and it is one that really ran.** This is the lane
F008-A5's qualification used against `shared-ramdisk-depot-manager` (srdm) at a
real commit range, unedited except for the base ref. Note what it does NOT
contain: no `cwd`, no module path, and `source_roots` spelled relative to the
lane file's own directory.

```toml
schema_version = 2

[lanes.coverage]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
# Your project's own test command, verbatim. srdm's is `tools/gate.sh:105`;
# only the -coverprofile path is assay's, because assay reserves the artifact
# it is going to read.
argv = ["go", "test", "./...", "-count=1", "-coverpkg=./...", "-covermode=atomic", "-coverprofile=.assay/cover.out"]
# `default_process_runner` REPLACES the child environment, so a Go lane must
# declare what it needs rather than inheriting it. PATH is how the toolchain
# is found at all; GOCACHE/GOMODCACHE keep a warm build cache warm.
env = { GOPROXY = "off", GOFLAGS = "-mod=mod", GOTOOLCHAIN = "local", GOWORK = "off" }
env_passthrough = ["PATH", "HOME", "GOCACHE", "GOMODCACHE"]
budget = "30m"
allow_argv_append = false

[lanes.coverage.isolation]
snapshot_selection = "repository"

[lanes.coverage.judge]
language = "go"
source_roots = ["internal"]     # project-relative, i.e. <module root>/internal
fail_under = 75.0
allow_excluded = false
base = "<the ref your change is measured against>"

[lanes.coverage.judge.coverage]
format = "go-cover"
artifact = ".assay/cover.out"
producer = "go-test"
```

The file lives at `<module root>/assay.toml` — for srdm, inside
`shared-ramdisk-depot-manager/`, not at the repository top. That is point 6
restated as a placement rule, and it is the one thing an adopter with a
subdirectory module gets wrong first: a lane file at the repository top makes
the project root the repository top, `go.mod` is not there, and the lane
refuses `BAD_LANE_CONFIG` before it judges anything.

**Keep `-coverpkg=./...` if you have it.** It is what stops a package with no
test file of its own from vanishing from the profile entirely — the
"file-absence" failure mode, which reports a changed file as either excluded or
wholly uncovered depending on how the tool guesses. It also means one block
gets one record per test binary, which assay folds executed-wins (B061); a
profile without it is smaller and measures less.

**What that lane produced**, through `python3 assay-<version>.pyz run coverage`
inside the Go gate image: `PASS`, 12 files considered, 418 executable changed
lines, 394 covered, 94.3%, and a `helpers[]` entry naming `go version
go1.25.14` as the toolchain that derived the statement positions. srdm's own
`covergate` on the byte-identical profile said 684/639 — a 266-line larger
denominator, every line of which begins no statement. Both tools agree on all
24 statements that are genuinely uncovered; the 21 extra lines `covergate`
names are closing braces and signatures a developer cannot make executable.
That difference is the whole reason A-217 ruled for a source-side oracle, and
it is what a Go consumer gains by moving.
