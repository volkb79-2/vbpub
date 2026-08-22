# run-gate — known issues, TODO, and backlog

Created 2026-08-22 from the adversarial review of the estate-wide adoption
wave (`vbpub@4c6eb2b6..91959b3a`: eight projects adopted run-gate as SSOT
test definition, consumers shrunk to thin pointers, full gate sweep green).
Two independent fresh-eyes reviewers (correctness + consumer UX) plus a CLI
audit; every entry below was confirmed in source before filing. IDs are
`RG-N`, allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`SPEC.md`](SPEC.md) (§9 already tracks some
deferred items — cross-references noted per entry). A FIXED entry means code,
tests, and docs landed together. Relationship to SPEC §9's own open items:
budget enforcement (RG-7 context), async long lanes, dstdns adoption, and
Docker-probe slice verification are NOT re-filed here; they stay owned by
SPEC §9.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| RG-1 | Conjunction lanes silently drop `--worktree` and `--allow-dirty` — a daemon override can vanish into a false PASS | Major | OPEN |
| RG-2 | Pointer↔lane linkage untested estate-wide: meta-tests certify `run-gate.toml` while the daemon executes the trove pointer | Major | OPEN |
| RG-3 | Dual-mount degenerates outside the cockpit namespace (`phys == repo` collapses both mounts) | Minor | OPEN |
| RG-4 | `pins.version` is validated but never checked — provenance theater claiming more than the check performs | Minor | OPEN |
| RG-5 | `{worktree}` textual substitution: quoting/injection surface, doubled sites in pointer argvs | Minor | OPEN |
| RG-6 | Exec-mode refusal prescribes a ciu-specific remedy for every project | Minor | OPEN |
| RG-7 | `usage()`/`--list` do not surface the environment contract or lane metadata (budget, clean_tree, description) | Minor | OPEN |
| RG-8 | No `--dry-run`: resolved docker argv/mounts/slice cannot be inspected without executing | Enhancement | OPEN |
| RG-9 | No `doctor` preflight subcommand | Enhancement | OPEN |
| RG-10 | Verdict/evidence artifact path printed only for ephemeral-container assay lanes; exec-mode prints nothing | Minor | OPEN |
| RG-11 | Uniform exit code 1 for every refusal — scripting cannot distinguish config error / dirty refusal / infrastructure failure | Minor | OPEN |
| RG-12 | Failing-container evidence destroyed: only last stderr line kept, container removed in `finally` | Minor | OPEN |
| RG-13 | Docs gaps: no end-to-end worked example; gitignore obligation unstated; adoption step 4 executed by zero projects; no root-level discovery; budget↔timeout drift unguarded | Minor | OPEN |
| RG-14 | Release model: wheel as second artifact beside the canonical script | Enhancement | OPEN |

---

## RG-1 — conjunction lanes silently drop `--worktree` and `--allow-dirty`

**Found by:** adversarial correctness review of the adoption wave, 2026-08-22.

### Observed mechanism

`cmru/run-gate.toml` `[lanes.gate]` (the conjunction pattern introduced by the
adoption and endorsed by CONSUMERS.md "Gate-conjunction lanes") invokes
sub-lanes as bare sibling calls:

```
argv = ["bash", "-c", "./run-gate.py assay && ./run-gate.py coverage && …"]
```

The argv contains **no `{worktree}` token**, so `substitute_worktree`
(`run-gate.py::substitute_worktree`) is a no-op, and argparse flags given to
the parent invocation are not forwarded — each sub-process re-derives the
judged worktree from *its own* cwd/git toplevel (`resolve_repo_and_worktree`)
and receives none of the parent's `--worktree`/`--allow-dirty`.

### Reproduction

```bash
cd /repo/cmru                       # any checkout whose toplevel is NOT the attempt tree
./run-gate.py gate --worktree /attempts/w1 --allow-dirty
# parent lane: clean_tree=false → no check anywhere
# sub-lanes:   judge /repo (clean), print `lane 'gate' exit 0`
# /attempts/w1 was never tested — silent false PASS
```

Not reachable through today's shipped pointer shape (`cd {worktree}/cmru &&
exec ./run-gate.py --worktree {worktree} gate` makes cd-target ≡ override),
which is why the sweep was green — but SPEC R-02's daemon case ("daemon
substitutes its attempt path textually before invoking") is precisely the
invocation class that breaks, and every future conjunction copies this one.

### Why run-gate owns it

Flag semantics are run-gate's contract (R-02/R-03); the tool accepts an
override and then lets a config pattern discard it silently. That is the
estate's masked-default hazard class ("a default is legitimate only when…
if this default is wrong, does anything fail loudly?" — nothing does here).

### Proposed contract (either side suffices; both are compatible)

1. **Forward:** conjunction-conventional substitution token, e.g. sub-invocations
   written as `./run-gate.py --worktree {worktree} assay && …` (cmru-side,
   mechanical), and/or
2. **Reject:** if `<lane>`'s argv contains neither `{worktree}` nor any
   sub-invocation marker, error when `--worktree`/`--allow-dirty` are passed
   to a lane whose kind would ignore them — loud, naming the lane.

### Oracles

- Parent invoked with `--worktree W` ⇒ every sub-lane judges W (assert via a
  fake inner command recording its cwd/toplevel).
- Controlled wrong implementation: today's argv under `--worktree W` must
  fail oracle 1.
- `--allow-dirty` reaches sub-lanes (dirty sub-lane proceeds instead of
  refusing).

**SPEC owner:** §2 (R-02/R-03) + §5 execution contract; CONSUMERS.md
conjunction recipe must change with it.

## RG-2 — pointer↔lane linkage untested: the dispatched artifact is certified by no test

**Found by:** adversarial correctness review, 2026-08-22 (second reviewer
independently flagged the weakened assertion form).

### Observed mechanism

Post-adoption, what nyxloomd actually executes is each project's *pointer*
in `nyxloom-trove/nyxloom.toml [gates.*].argv`
(`… cd {worktree}/<proj> && exec ./run-gate.py --worktree {worktree} <lane>`).
The meta-tests updated during adoption were repointed at `run-gate.toml`
(the SSOT) — correct for mechanics coverage, but **no test reads the
pointer**: renaming a lane in `run-gate.toml` while updating the tests in the
same commit leaves the suite green and every daemon dispatch dying on
`unknown lane '<name>'`. Additionally, `assay/tests/test_cgroup_parent.py::
test_nyxloom_gate_uses_verified_value_without_a_literal_slice` replaced exact
list equality with substring checks (`"run-gate.py" in pointer`), which a
broken pointer (`echo run-gate.py tester-unified`; `cd …/assayX`;
`tester-unified-typo`) satisfies trivially.

Affected today: ciu, assay, topos pointers (each `nyxloom.toml [gates.*]`),
and cmru's `[steps.run-tests]` pointer (same linkage, different consumer).

### Reproduction

```bash
# in any adopted project, e.g. topos:
sed -i 's/topos-suite/topos-suiteX/' topos/run-gate.toml   # rename lane, keep pointer
pytest topos/tests/test_gate_environment.py -q             # green
cd topos && ./run-gate.py topos-suite                      # unknown lane — loud, but only live
```

### Why run-gate owns it

The linkage contract (pointer names a real lane, with the canonical flag
shape) is run-gate's vocabulary. Per-project tests can enforce it, but only
if run-gate defines what a correct pointer IS.

### Proposed contract

A pointer-validation helper, exposed either as `run-gate.py validate-pointers
<path-to-trove-toml>` or as a documented assertion recipe, checking:
pointer parses → contains exactly one `cd <project-dir>` target matching this
project → invokes `./run-gate.py --worktree {worktree} <lane>` → `<lane> ∈
[lanes]`. Then one such test per adopted project. Substring assertions in
assay's meta-test restored to structural checks (parse the pointer string).

### Oracles

- Renamed-lane scenario above must go RED at test time, not dispatch time.
- Controlled wrong implementation: pointer with `assayX` dir or missing
  `--worktree` fails validation.

**SPEC owner:** §2 (CLI contract gains validate verb or documented recipe);
CONSUMERS.md adoption steps gain "add the linkage test".

## RG-3 — dual-mount degenerates outside the cockpit namespace

**Found by:** adversarial correctness review, 2026-08-22.

`run_container_lane` mounts `-v <phys>:<phys> -v <phys>:<repo>`
(`physical_path` derived from `/proc/self/mountinfo`). Inside the devcontainer
phys=`/home/vb/volkb79-2/vbpub`, repo=`/workspaces/vbpub` → two distinct
views, reproducing AGENTS trap #2. On a bare host `/.dockerenv` is absent and
`physical_path` returns the path unchanged (`phys == repo`) → both `-v` flags
collapse and containers see ONLY the host path, whereas every pre-adoption
consumer argv pinned `-v /home/vb/volkb79-2/vbpub:/workspaces/vbpub`
unconditionally. Latent (all current triggers run inside the cockpit) but a
silent divergence from the documented four-traps recipe the tool embodies.

**Proposed contract:** derive the second namespace view from mountinfo when
present; when absent, either declare the constraint loudly at startup
("container lanes assume the devcontainer namespace alias; found none") or
accept an explicit env fact. Never collapse silently.

**Oracle:** simulated mountinfo without an alias → loud refusal or explicit
single-mount notice naming both paths tried; controlled wrong implementation
(today's silent collapse) fails it.

## RG-4 — `pins.version` is validated but never checked (provenance theater)

**Found independently by BOTH reviewers**, 2026-08-22.

`_validate_lane` requires `pins.*.version` as a string (run-gate.py ~:136);
nothing ever reads it (`build_assay_inner` uses only `sha256`). Both shipped
configs declare `version = "2.1.0"` (ciu/run-gate.toml, cmru/run-gate.toml),
and CONSUMERS.md's example comment claims it is "verified against the judge
the image carries" — the message states a conclusion the code never performs.
This is the cmru KI-12 anti-pattern class ("a check's message states a
conclusion; the comparison is narrower").

**Reproduction:** set `version = "9.9.9"` in either config → lane runs
identically green; sha256 sidecar still guards bytes, but the declared
version is fiction.

**Proposed contract:** make it honest — either (a) mechanical: after pin
verify, run `<assay_command> --version` (or read the pyz's embedded version)
inside the container and fail on mismatch with the declared value; or (b)
rename the key to `note`/drop it from the schema and examples. (a) preferred:
cheap, stdlib, closes the gap.

**Oracles:** mismatched version → lane refuses naming both values; equal →
silent; controlled wrong implementation (today's no-check) fails oracle 1.

## RG-5 — `{worktree}` textual substitution: quoting/injection surface

**Found by:** adversarial correctness review, 2026-08-22.

Pointers embed `{worktree}` twice into a `bash -c` STRING
(`cd {worktree}/ciu && exec ./run-gate.py --worktree {worktree} ciu`); a path
containing spaces/shell metacharacters word-splits or executes. Old argvs had
one site and equally held `docker run`, so no privilege boundary changed —
but the surface doubled, and the daemon controls paths only by convention.
Hardening options: reject paths outside `^[A-Za-z0-9_./ -]+$` at the daemon
boundary; or move the worktree out of band entirely (env var consumed by
run-gate, e.g. `RUN_GATE_WORKTREE`, reducing pointers to
`cd "$PWD" && exec ./run-gate.py <lane>`). The latter also shrinks RG-2's
linkage surface.

**SPEC owner:** R-02 (substitution contract).

## RG-6 — exec-mode refusal prescribes a ciu-specific remedy for every project

**Found by:** consumer-UX review, 2026-08-22.

`run_exec_lane`'s not-running refusal hardcodes `start it via 'ciu up --dir
tools/test-runner' or the project's runner lifecycle command` (run-gate.py
~:462-467) for ANY exec-mode project. dstdns — the stated exec-mode adopter —
gets told to run a ciu directory that does not exist there. Violates the
estate rule that a remedy message must prescribe a CORRECT fix.

**Fix:** derive the suggestion from `name_src` (already in hand): declared
`container_name` → "declare/start it in your deployment authority";
ciu.global.toml-derived → name that file and `ciu render`/`ciu up` as
applicable. **Oracle:** exec lane with stopped container → message names the
source actually used; dstdns-shaped project never sees ciu's command.

## RG-7 — usage()/`--list` hide the environment contract and lane metadata

**Found by:** consumer-UX review + CLI audit, 2026-08-22.

`usage()` prints rev, two usage lines, and a lanes table of
`name/kind/environment` only; the flags section documents neither semantics
nor caveats; the environment contract is invisible until first failure:

- `$CGROUP_PARENT_DEV_BACKGROUND` required for container lanes (absent = hard
  error at runtime);
- `RUN_GATE_EXTRA_MOUNTS` colon-separated `host=container` pairs (ephemeral
  lanes only) — documented only in SPEC R-14b;
- `--allow-dirty` says nothing about assay lanes enforcing their OWN
  clean-tree rule regardless (two-layer refusal confuses: user passes the
  flag they were told about, assay refuses mid-streamed-logs);
- budgets/clean_tree/memory exist per lane but appear nowhere;
- exec-mode passthrough allowlist (`MOCK_MODE`, `RUN_LIVE_TESTS`) lives only
  in a code comment; ephemeral lanes have no arbitrary-env mechanism at all.

**Proposed:** ENVIRONMENT section in usage(); table gains budget +
clean_tree columns; optional `description` lane key (validated, shown);
document the gitignore obligation for command-kind artifacts (see RG-13).
Schema change additive; one parser owns it.

## RG-8 — no `--dry-run`

**Enhancement, 2026-08-22 (CLI audit; wanted repeatedly during the adoption
sweep).** The docker argv/mounts/slice/env are fully assembled before
execution (`run_container_lane` prints them, then runs). Add `--dry-run`:
print the plan (image, slice+source, mounts incl. extra mounts, memory, inner
command) and exit 0 without `docker run`. Invaluable for debugging adoption
(mount/slice mistakes) cheaply; zero new machinery.

**Oracle:** `--dry-run <container lane>` performs no `docker run` (fake
docker records argv), prints the identical argv the live run would use.

## RG-9 — no `doctor` preflight subcommand

**Enhancement, 2026-08-22 (consumer-UX review).** Recompose existing checks
into `./run-gate.py doctor`: docker present; slice resolvable (var or
declared) + LoadState where systemd reachable; physical-path derivability
from mountinfo; git identity/safe.directory writability; referenced images
exist locally. One command turns four first-contact failure classes into a
preflight a newcomer runs once. All inputs already implemented — pure
recomposition, stdlib only.

## RG-10 — verdict/evidence path printed only for ephemeral-container assay lanes

**Found by:** consumer-UX review, 2026-08-22 (R-18 intent: "print WHERE the
verdict artifact lives").

`run_container_lane` prints `.assay/verdict-<lane>.json` post-run;
`run_exec_lane` prints nothing equivalent; command-kind lanes' evidence paths
(`.assay/mutation-cmru.json`, `.assay/coverage-canary-cmru.json`) exist only
inside opaque argv strings. After a green run a consumer is often not told
where evidence landed.

**Proposed:** optional `artifacts = ["path", …]` lane key (validated, printed
on every lane exit, `{worktree}`-substituted), defaulting to the assay-verdict
convention for assay-kind lanes in both runner modes. Backfill cmru's three
evidence paths.

## RG-11 — uniform exit code 1 for every refusal

**Found by:** consumer-UX review, 2026-08-22.

All `GateError`s exit 1 (`main()`'s except handler): config errors, dirty-tree
refusals, docker failures, unknown lanes are indistinguishable to scripts.
Messages carry the information; machines don't. With CI fan-out consuming
`--list` (CONSUMERS.md) this becomes load-bearing.

**Proposed reserved codes:** 2 = configuration/refusal (incl. dirty tree,
unknown lane), 3 = execution-infrastructure failure; document in usage().
Cheap now, breaking later.

## RG-12 — failing-container evidence destroyed

**Found by:** consumer-UX review, 2026-08-22.

On failed `docker run` only the LAST stderr line is kept
(`detail[0]`); image-pull/network failures are multi-line and the interesting
line is rarely last. On lane failure the container is `rm -f`'d in `finally`
— post-mortem diagnosis = rerun and stare.

**Proposed:** before removal, copy logs to `/tmp/run-gate/<container>.log`,
print the path on failure; keep ≥ last N stderr lines in the immediate
message. **Oracle:** forced-fail lane leaves a readable log at the printed
path after the container is gone.

## RG-13 — docs gaps (CONSUMERS.md + adoption hygiene)

**Found by:** consumer-UX review, 2026-08-22. Five items, one entry because
they share the same fix surface:

1. **No end-to-end worked example** stitching run-gate × assay: obtaining the
   pyz + sidecar, minimal R0 `assay.toml`, `kind="assay"` lane with pins,
   consumer pointer, first run, reading `.assay/verdict-*.json`. Both halves
   exist separately (CONSUMERS orchestration layer defers to assay's docs;
   assay/docs/CONSUMERS.md covers judgment) — nobody stitches them. Half a
   page of glue.
2. **Gitignore obligation unstated:** command-kind lanes writing artifacts
   into the tree (`.assay/`, `coverage.json`) depend on those being ignored
   or the NEXT lane's clean-tree check refuses mysteriously. The monorepo
   root ignores both for internal projects; copied-script repos must
   replicate — say so.
3. **Adoption step 4 executed by zero projects:** CONSUMERS.md requires one
   AGENTS.md/README line naming `./run-gate.py` as canonical entrypoint; no
   adopted project has any (verified by ls). Retro-execute during the next
   touch of each project.
4. **No root-level discovery affordance:** repo-root has central
   `run-gate.toml` but no pointer down to "cd <project> && ./run-gate.py
   --list"; add one line to the root README.
5. **Budget↔timeout drift unguarded:** every project pairs run-gate `budget`
   with a consumer `timeout_seconds` by manual sync; assay pioneered the
   assert-it test pattern (`test_self_lane.py`). Replicate per project or
   provide an estate sweep.

Related but separate: assay's own CONSUMERS.md teaches the superseded
pre-adoption cmru wiring — filed as assay B011, not here.

## RG-14 — release model: wheel as second artifact beside the canonical script

**Enhancement, 2026-08-22 (operator question, answered in review session).**

Keep symlink-in-monorepo (internal) + copy-external as PRIMARY —
zero-install-on-fresh-clone is the design win and must not regress. Add a
wheel as a SECOND artifact: pyproject wrapping the single stdlib module with
a console-script entry point, tags `run-gate-vX.Y.Z`, published through
cmru's wheel-publish like assay/ciu. Serves pip-managed external repos and
CI images wanting it baked. Discipline: a test asserting wheel version ≡
in-file `__revision__` so copies' drift marker stays truthful; CONSUMERS.md
must state the script remains canonical and the wheel never becomes required.
A pyz zipapp was considered and rejected: adds packaging without covering
anything the wheel doesn't.

**Oracles:** fresh clone with zero installs runs `./run-gate.py --list`
(script path, unchanged); `pip install`ed wheel exposes `run-gate` console
script with identical behavior; version-mismatch between wheel and script
fails the discipline test.
