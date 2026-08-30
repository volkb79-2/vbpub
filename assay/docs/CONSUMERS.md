# Consuming Assay from another repository

Assay is a release artifact, not an estate-only source import. A consumer can use either
the wheel or the matching zipapp from one immutable `assay-v*` GitHub Release. The wheel is
the normal integration; the zipapp is the zero-install option for a gate image that already
has Python.

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

## JavaScript/TypeScript lanes (R1 only)

`judge.language = "javascript"` is a changed-line coverage lane over
`.js`/`.jsx`/`.ts`/`.tsx` — one language name for all four. It resolves at
**R1 only**: R2 waits on a real architectural ruling (B037: a native JS/TS
mutation engine, or ingesting Stryker's evidence), and R3 is unwired, so a
lane declaring either is refused `ERROR`/`BAD_LANE_CONFIG` before anything
runs.

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
      clean: false,                // REQUIRED inside an assay snapshot -- see below
    },
  },
})
```

`reporter: ['json']` writes `.assay/coverage-final.json`. Do not use
`json-summary` (totals only, no per-file detail) or `lcov` (a different
registry format — if you prefer it, declare `format = "lcov"` instead and
point at `lcov.info`).

**`clean: false` is REQUIRED for any lane assay judges — measured, not a
style preference (B049).** Vitest's own default (`coverage.clean = true`)
deletes and recreates `reportsDirectory` before writing to it. assay's own
`safeio.reserve_output` (`runner.py:1692`) opens and holds that directory's
own file handle *before* your lane's command runs, specifically so it can
read the artifact back afterward without a second, race-prone directory
walk (B006(b)); `reservation.consume()` (`runner.py:1771`) then reads through
that SAME held handle once the command exits. A tool that deletes and
recreates the directory — rather than writing into the one directory assay
already opened — silently orphans that handle: it now points at an empty,
unlinked directory, so `consume()` finds nothing, even though a fully
populated `coverage-final.json` genuinely exists on disk at that path by the
time your command exits. This reads as `NO_MEASUREMENT`/`EMPTY_COVERAGE` —
"your tests produced no coverage" — for a lane that in fact ran cleanly and
covered everything; nothing about the failure points at `coverage.clean`.
Measured directly: an otherwise fully-covered real lane run through the real
CLI returns `EMPTY_COVERAGE` with Vitest's default `clean: true`, and `PASS`
with the correct 100% figure the moment `clean: false` is added and nothing
else changes. `cleanOnRerun` (Vitest's watch-mode-only sibling) is
irrelevant to `vitest run` and does not need setting. This is a real,
filed gap in assay's own reservation mechanism, not a JS-specific
requirement in principle — see **B049** for the underlying defect and why
it is not fixed in this release.

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
`node_modules` and `vitest.config.ts` all resolve, so the lane's `argv`
must `cd` there (until B043's `cwd` retires the wrapper — Wave B) and its
`artifact` path must be spelled relative to the PROJECT root, not the app
root:

```toml
schema_version = 2

[lanes.ui_unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["bash", "-c",
  "npm ci --offline --no-audit --no-fund --prefix applications/webapp-ui-react && npx --no-install --prefix applications/webapp-ui-react vitest run --coverage --root applications/webapp-ui-react"]
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
```

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

#### (b) The declared speed path — `isolation.link_paths` (Wave B, schema v9)

Rebuilding the closure inside every snapshot is correct but not free: a
`link_paths` declaration (`[lanes.<n>.isolation] link_paths = [...]`) will
let a lane symlink an already-materialised `node_modules` directly into its
snapshot instead of reinstalling it, recorded in the verdict's
`snapshot_policy.link_paths` so a reviewer can always tell a lane ran
against a purely-committed snapshot from one that borrowed part of the
checkout. This is **not implemented in this release** — it rides the same
schema-v9 cut as B045's declared coverage producer (B041 acceptance item;
tracked in `4-backlog.md` B041). Pattern (a) above is the one every consumer
adopts today.

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

**`require_branch = true` will refuse this format.** `branch_capability` is
`"unavailable"`, deliberately: istanbul's `branchMap` means one thing under
`@vitest/coverage-istanbul` (real per-arm arcs) and a different thing under
`@vitest/coverage-v8` (v8's own executed/unexecuted ranges), and a lane
declares the format, not the producer — so no honest single translation
exists yet, and a fabricated branch percentage is worse than an absent one.
Leave `require_branch` unset (or `false`) on a JavaScript lane; B038 tracks
adding real arc support once a producer can be declared.

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
instrumenter and are unaffected.** Jest's `coverageProvider: "v8"` remains
genuinely unmeasured — treat it as unsafe until a committed witness says
otherwise. `c8` (a separate `coverage-final.json` producer some non-Vitest
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
matches, whether or not any test ever imports it (`resolveConfig`,
`vitest@4.1.11`'s own `chunks/coverage.*.js`: matched files are transformed
for instrumentation regardless of import, so an untested file reads as
measured-and-uncovered rather than silently absent). **This is not Vitest's
own hardcoded `coverage.exclude`** — that list covers only the one resolved
config file actually in use, the `test.include` test-name glob, and declared
setup files, never an arbitrary `*.config.*`/`*.stories.*` name (measured:
under a plain `coverage.include = ['src/**']`, `.stories.tsx` and
`.config.ts` files that happen to sit under `src/` DO appear in
`coverage-final.json`, every statement at count `0`). The net effect a
consumer actually has to avoid is the same regardless of the mechanism: a
changed line inside one of these files is judged like ordinary source
(the adapter's `is_test_path` does not exempt it) against a record that can
never show anything but uncovered — fail-closed, and visible in the verdict,
never a silent pass.

## Browser coverage of a UI as an R1 lane

Every JavaScript lane shape above judges UNIT-level Vitest coverage. A
Playwright/browser suite exercises the same UI a different way — through a
real DOM, real user events, a real running build — and can be judged too,
with no assay change: `judge.language = "javascript"` and
`format = "coverage-istanbul-json"` are the SAME declaration either way,
because `vite-plugin-istanbul` (`babel-plugin-istanbul`'s own instrumenter,
the identical one `@vitest/coverage-istanbul` uses) produces the identical
artifact shape a Playwright run's own `window.__coverage__` dump already is.

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
  a gate having to parse `argv` itself (B043's `cwd` will sharpen `argv0`
  further once a lane can declare a working directory other than the
  snapshot root — until then a `bash -c "cd … && …"` wrapper's real command
  is still hidden behind `argv0 = "bash"`, exactly as it is from
  `assay run`'s own preflight today).
- **`environment_command`** is a boolean, not the probe's own argv: a gate
  only needs to know ONE declares a probe must pass in the invoking
  environment before snapshot work, never to re-implement or repeat it
  (DESIGN-GUIDE §4).

`cwd`, `link_paths` and `coverage.producer` are always `null`/`[]` in this
release — they are declarable starting at Wave B's schema v9 (B043, B041's
`link_paths`, B045) — so a consumer reading them today learns nothing beyond
"not yet declared," and its key-handling code does not need an
`inventory_schema` branch to add them later: `inventory_schema` stays `1`
until an EXISTING key's meaning changes, never merely because a new key was
added.

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
