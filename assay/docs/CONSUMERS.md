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
# judge.base is FORBIDDEN here (no R2 declared) -- a whole-target claim
# resolves no diff, so recording one would imply a comparison that never ran.

[lanes.redirect_chain.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/coverage.json"
```

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

## CMRU / tester-unified integration

CMRU's project `cmru.toml` owns the exact gate command. `tester-unified` should **not** bake an
ambient Assay version: that would make a consumer's evidence depend on whichever image happened
to be rebuilt. Instead a consumer pins a wheel in its own gate setup, or vendors the verified
zipapp as an explicit input. A CMRU project can run the latter through its existing gate:

<!-- assay-doc-example:skip reason="cmru.toml step config, not an assay lane file -- has no schema_version/[lanes] table and is not parsed by assay's loader" -->
```toml
[steps.run-tests]
quiet = true
commands = [
  { label = "example: assay lane in tester-unified", argv = ["cmru", "tester-gate", "--cwd", ".", "--", "/opt/tester-venv/bin/python", "tools/assay/assay-<version>.pyz", "run", "unit", "--file", "assay.toml", "--verdict-json", ".assay/verdict-unit.json"], cwd = "." },
]
```

The product, not CMRU, owns the `assay.toml` lane, pinned Assay artifact, and verdict retention.
CMRU only supplies the isolated execution boundary and concise logging.

## Adopting a v2-capable release

Verdict schema v6 and lane schema v2 are both hard cuts (no dual-version verifier, no
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

## What is not shipped

Assay has no remote worker, offsite dispatcher, or asynchronous fuzzing service. Its Python
R2/R3 execution is local and isolates each unit in Git-based scratch snapshots. A remote runner
would need an explicit artifact bundle, pinned toolchain/image digest, queue/auth contract,
result attestation, cancellation/timeout semantics, and a way to prove the returned verdict was
for the submitted commit. None of that is implemented or implied by the wheel/zipapp today.
