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
| RG-1 | Conjunction lanes silently drop `--worktree` and `--allow-dirty` — a daemon override can vanish into a false PASS | Major | FIXED 2026-08-24 |
| RG-2 | Pointer↔lane linkage untested estate-wide: meta-tests certify `run-gate.toml` while the daemon executes the trove pointer | Major | FIXED 2026-08-24 |
| RG-3 | Dual-mount degenerates outside the cockpit namespace (`phys == repo` collapses both mounts) | Minor | FIXED 2026-08-24 |
| RG-4 | `pins.version` is validated but never checked — provenance theater claiming more than the check performs | Minor | FIXED 2026-08-24 |
| RG-5 | `{worktree}` textual substitution: quoting/injection surface, doubled sites in pointer argvs | Minor | FIXED 2026-08-24 |
| RG-6 | Exec-mode refusal prescribes a ciu-specific remedy for every project | Minor | FIXED 2026-08-24 |
| RG-7 | `usage()`/`--list` do not surface the environment contract or lane metadata (budget, clean_tree, description) | Minor | FIXED 2026-08-24 |
| RG-8 | No `--dry-run`: resolved docker argv/mounts/slice cannot be inspected without executing | Enhancement | FIXED 2026-08-24 |
| RG-9 | No `doctor` preflight subcommand | Enhancement | FIXED 2026-08-24 |
| RG-10 | Verdict/evidence artifact path printed only for ephemeral-container assay lanes; exec-mode prints nothing | Minor | FIXED 2026-08-24 |
| RG-11 | Uniform exit code 1 for every refusal — scripting cannot distinguish config error / dirty refusal / infrastructure failure | Minor | FIXED 2026-08-24 |
| RG-12 | Failing-container evidence destroyed: only last stderr line kept, container removed in `finally` | Minor | FIXED 2026-08-24 |
| RG-13 | Docs gaps: no end-to-end worked example; gitignore obligation unstated; adoption step 4 executed by zero projects; no root-level discovery; budget↔timeout drift unguarded | Minor | FIXED 2026-08-24 |
| RG-14 | Release model: wheel as second artifact beside the canonical script | Enhancement | FIXED 2026-08-24 |
| RG-15 | Assay lanes must execute in the selected worktree, not the invoking checkout | Major | FIXED 2026-08-24 |
| RG-16 | Central configs should be allowed to define shared lanes | Major | FIXED 2026-08-24 |
| RG-17 | required-env forwarding completeness (schema-oracle credentials silently dropped) | Major | FIXED 2026-08-24 |
| RG-18 | no pg_dump/PostgreSQL version-mismatch guard for schema lanes | Minor | OPEN — dstdns-side scope (schema-gate.sh), not run-gate.py; see body |
| RG-19 | schema-lane credential propagation must be verified by the gate, not by test failure | Major | FIXED 2026-08-24 |
| RG-20 | replace global gate flock with resource-aware admission | Enhancement | FIXED 2026-08-24 |
| RG-21 | linked-worktree checkouts break host-path-mapped lanes (srdm covergate evidence) | Minor | OPEN 2026-08-24 |
| RG-22 | `git config --global safe.directory "*"` fails when global config already has safe.directory entries | Minor | FIXED 2026-08-24 |
| RG-23 | exec-mode's hardcoded env-forward allowlist was dropped with no consumer migration; unmigrated consumers silently stop forwarding `RUN_LIVE_TESTS`/`MOCK_MODE` | Major | OPEN 2026-08-25 |
| RG-24 | `resolve_container_name()` derives an exec-mode container's name from the shared-`.git`-owning repo's `ciu.global.toml`, never the judged worktree's own — a multi-instance (Mode-B) worktree's live lane silently targets the WRONG deployed container | Major | OPEN 2026-08-30 |

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

**FIXED 2026-08-24** (both halves, per interview; sequenced after RG-15 as
planned):
1. **Forward:** `cmru/run-gate.toml [lanes.gate]` now writes
   `./run-gate.py --worktree {worktree} <sub>` for EVERY sub-invocation —
   the reference shape, mirrored in CONSUMERS' conjunction recipe.
2. **Reject:** SPEC `R-25` — a CONTAINER command lane (ephemeral or exec)
   invoked with `--worktree` whose argv has no `{worktree}` token refuses
   (exit 2) before execution. Assay lanes relocate automatically (R-21) and
   host lanes relocate via cwd, so both are exempt.

Discovery worth recording: post-RG-15 the BARE host conjunction is safe BY
CONSTRUCTION (the host runner's cwd relocates into the override tree, so
bare sub-calls derive the same toplevel) — the residual hazard is exactly
the container class, which the guard covers. `--allow-dirty` is NOT
forwarded by any mechanism: conjunctions that want dirty-tolerance write it
per sub-call explicitly; anything else would silently weaken sub-lanes'
clean-tree checks (the loud-refusal direction is the safe one). Oracles:
forwarded shape judges W (nested fixture records the SUB-lane's docker run);
controlled wrong shape (token-less ephemeral lane under --worktree) refuses
with docker never invoked. Tests: TestConjunctionOverrideGuard x5.

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

**FIXED 2026-08-24** (SPEC `R-27`): new verb `run-gate.py validate-pointers
CONSUMER.toml [--root DIR]`. Schema-agnostic: it walks any parsed TOML and
certifies EVERY run-gate invocation it finds (an argv-style list whose first
element is run-gate.py is joined into one pointer — cmru's list-form release
step). Per invocation it enforces exactly one `{worktree}`-relative cd
target whose project has a run-gate.toml (no cd → the document's own
directory when that IS a project), `--worktree {worktree}` present whenever
the pointer substitutes `{worktree}` at all (the RG-1 false-PASS class,
caught at test time), and exactly one positional lane name that EXISTS in
the effective lane set — loaded with the REAL parser including central
inheritance, never a second parse. Exit 2 on any defect; documents that
never invoke run-gate (srdm's gate.sh pointers) are trivially clean. Estate
linkage now runs on every suite run: TestPointerLinkage ×9 construction pins
(renamed-lane oracle goes RED at test time) + TestPointerLinkageEstate —
all five trove nyxloom.toml files plus cmru.toml certified against their
SSOT lanes (6 real invocations, all green today). assay's meta-test
substring assertion restored to STRUCTURAL checks: exact cd target, exact
token list `--worktree {worktree} tester-unified`, lane-exists-in-SSOT.
CONSUMERS adoption gains step 3a ("Certify the linkage").

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

**FIXED 2026-08-24** (explicit env fact — the "5b" explicitness choice):
`dual_mount_flags(repo, phys)` emits the two `-v` views only when they are
DISTINCT. When `phys == repo` (bare host — mountinfo offers no alias) the
lane refuses (exit 2, message names "collapse") unless
`$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'` declares the second view;
malformed entries and a host side ≠ repo root are refused by name. The alias
only ever changes the container-side path of the SECOND view — both flags
always bind-mount the same physical tree. SPEC `R-23`; README path-
namespaces bullet and CONSUMERS resolution-order paragraph amended. Test
note: the end-to-end refusal is driven in-process (`run_gate.main`) because
subprocess runs derive REAL mountinfo views and this devcontainer's `/tmp`
bind mount hides the bare-host collapse from them.

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

**FIXED 2026-08-24** (option a, mechanical): `build_assay_inner` gains an
in-lane probe per pin declaring `version` — `<assay_command> --version` must
succeed and its output match the declaration, else the lane exits 2 naming
both values. Empty declarations rejected at validation. Oracles include a
LIVE shell execution of the generated inner (fake artifact reporting the
wrong/right version). SPEC R-08 + CONSUMERS schema comment updated: declaring
`version` asserts the `--version` convention.

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

**FIXED 2026-08-24** (charset guard now; env-var migration deferred to the
CIU-V7 cutover, per interview): `check_worktree_charset(worktree)` enforces
`^[A-Za-z0-9_./][A-Za-z0-9_./-]*$` on the RESOLVED worktree before any lane
runs — every kind uniformly (the daemon pointer recipe embeds `{worktree}`
into bash strings regardless of what an individual lane does with it).
Refusal is exit 2 naming the offending characters and the reason; mid-path
leading-dash components are deliberately ALLOWED (absolute paths always
start with `/`, so the flag look-alike hazard never materializes — tighter
than the backlog's sketch, which wrongly admitted spaces). SPEC R-02
amended; README "Gate-safe paths" bullet; CONSUMERS adoption step 3.
Revisit at V7 cutover: out-of-band `RUN_GATE_WORKTREE` to shrink pointer
argvs (and RG-2's linkage surface) entirely.

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

**FIXED 2026-08-24** (remedy derived from resolution source, per the entry):
`resolve_container_name` now returns `(name, source, start_remedy)` — a
declared `container_name` yields "start it via YOUR project's deployment
authority", ciu-derived names yield `ciu render`/`ciu up` naming the config
file used. The not-running refusal interpolates the remedy verbatim; the
hardcoded `ciu up --dir tools/test-runner` is gone. SPEC R-14a amended;
CONSUMERS dstdns recipe notes the rule. Oracle covered both ways: declared-
name refusal asserts `"ciu"` appears NOWHERE in stderr; ciu-derived refusal
asserts the lifecycle AND `ciu.global.toml` are named.

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

**FIXED 2026-08-24:** `usage()` gains FLAGS (`--worktree`; `--allow-dirty`
with the explicit two-layer caveat that assay still enforces its own
clean-tree rule) and ENVIRONMENT CONTRACT sections naming all three
variables the tool reads — `CGROUP_PARENT_DEV_BACKGROUND`,
`RUN_GATE_EXTRA_MOUNTS`, `RUN_GATE_MOUNT_ALIAS` — with failure semantics.
The human lane table now shows `clean_tree`, advisory `budget`, `memory`,
and a new validated optional `description` key (one line, `--help` only).
`--list` stays THREE columns by design: it is the machine-readable contract
(CONSUMERS anti-goal) and never grows columns. SPEC R-01/R-08 amended;
CONSUMERS schema comment updated. Gitignore obligation for command-kind
artifacts lands with RG-10/RG-13. Tests: TestUsageEnvironmentContract x5.

## RG-8 — no `--dry-run`

**Enhancement, 2026-08-22 (CLI audit; wanted repeatedly during the adoption
sweep).** The docker argv/mounts/slice/env are fully assembled before
execution (`run_container_lane` prints them, then runs). Add `--dry-run`:
print the plan (image, slice+source, mounts incl. extra mounts, memory, inner
command) and exit 0 without `docker run`. Invaluable for debugging adoption
(mount/slice mistakes) cheaply; zero new machinery.

**Oracle:** `--dry-run <container lane>` performs no `docker run` (fake
docker records argv), prints the identical argv the live run would use.

**FIXED 2026-08-24** (SPEC `R-28`): `--dry-run` on every lane kind. The
flag is a REHEARSAL, not a bypass: all preflights run exactly as live
(config, required-env, worktree resolution + charset guard,
override-reachability, clean-tree — `--allow-dirty` composes), then the
runners return the fully assembled plan and exit 0 instead of executing.
Container lanes print the identical docker argv (same assembly code path;
only `--name` differs by pid/epoch — the oracle normalizes it in tests);
exec lanes rehearse name resolution AND the runner-running check (a stopped
runner gives its real exit-2 refusal); host lanes print argv + cwd. No
evidence-path disclosure on dry runs — nothing ran, nothing landed. Tests:
TestDryRun ×6 including the oracle (live vs dry argv equality with name
normalized) and both preflight rehearsals.

## RG-9 — no `doctor` preflight subcommand

**Enhancement, 2026-08-22 (consumer-UX review).** Recompose existing checks
into `./run-gate.py doctor`: docker present; slice resolvable (var or
declared) + LoadState where systemd reachable; physical-path derivability
from mountinfo; git identity/safe.directory writability; referenced images
exist locally. One command turns four first-contact failure classes into a
preflight a newcomer runs once. All inputs already implemented — pure
recomposition, stdlib only.

**FIXED 2026-08-24** (SPEC `R-30`): `./run-gate.py doctor`. One
`[OK]/[WARN]/[FAIL]` line per check + summary; exit 2 iff any FAIL. Checks:
docker present; per-environment slice resolution + LoadState where systemd
reachable (distinct envs deduplicated); git worktree resolution; mountinfo
derivability (bare-host view = WARN naming `$RUN_GATE_MOUNT_ALIAS`, per
RG-3); `/tmp` writability for GIT_CONFIG_GLOBAL; referenced images present
locally (advisory WARN — a missing image may legitimately pull). Doctor
runs nothing and must itself survive a broken host: a preflight that
tracebacks on exactly the machine that needs it defeats its purpose, so an
unrunnable git is a `[FAIL] git` line, not a traceback. Tests: TestDoctor
×6 (healthy all-OK, unresolvable-slice refusal, missing-image advisory,
docker-absent failure, host-only skip, mountinfo always reported).

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

**FIXED 2026-08-24** (SPEC `R-08` + `R-18` amendment): `artifacts` is a
validated lane key (non-empty list of non-empty strings); new
`print_lane_artifacts` runs after EVERY lane exit in ALL THREE runners —
ephemeral, exec, host — any kind, success or failure. Assay lanes always
disclose `.assay/verdict-<assay_lane>.json` resolved against the EFFECTIVE
project dir (the ephemeral runner's inline print was replaced by the helper,
so the path is now worktree-correct under `--worktree` too, not just
present); declared entries are `{worktree}`-substituted,
absolute-or-project-relative, and deduplicated against the verdict
convention. A failed lane still names its evidence — that is exactly when
the reader needs the paths. cmru's three evidence paths backfilled as
declared `artifacts`. Tests: TestArtifactsDisclosure ×8 (ephemeral command
lane disclosure, verdict dedup, `{worktree}` substitution, exec assay
verdict, host lane, failed-lane disclosure, invalid-key rejection ×2).

## RG-11 — uniform exit code 1 for every refusal

**Found by:** consumer-UX review, 2026-08-22.

All `GateError`s exit 1 (`main()`'s except handler): config errors, dirty-tree
refusals, docker failures, unknown lanes are indistinguishable to scripts.
Messages carry the information; machines don't. With CI fan-out consuming
`--list` (CONSUMERS.md) this becomes load-bearing.

**Proposed reserved codes:** 2 = configuration/refusal (incl. dirty tree,
unknown lane), 3 = execution-infrastructure failure; document in usage().
Cheap now, breaking later.

**FIXED 2026-08-24** (SPEC R-04 amended): `GateError.exit_code` = 2
(configuration/refusal), `GateInfraError` = 3 (docker absent/failing, git
failures, mountinfo underivation, unreadable wait status). Documented in
usage() with a red-guard test; all ten exit-code pins reclassified.

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

**FIXED 2026-08-24** (SPEC `R-26`): `save_container_logs` copies the full
logs to `$RUN_GATE_EVIDENCE_DIR/<container>.log` (default `/tmp/run-gate`)
BEFORE every `rm -f`; a failed lane prints the preserved path, and the
oracle holds — the file exists and reads back after removal. A failed
`docker run` also preserves partial logs and its refusal now shows up to
the last 10 stderr lines (indented block) instead of only the last one.
Capture is best-effort: failure to capture never changes the lane's exit
status, it only downgrades the message to "could NOT be captured".
Exec-mode containers are externally owned — never removed, never captured.
Tests: forced-fail lane (wait 7 → passthrough 7 + readable log),
run-failure with TWO stderr lines both present in the refusal, and the
evidence-dir override honored.

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

**FIXED 2026-08-24 (rev 21) — all five items, closed LAST as the estate
retro:**

1. **Worked example added** to CONSUMERS.md ("Worked example — run-gate ×
   assay, end to end"): pyz + sidecar acquisition → minimal R0 `assay.toml`
   (template keys verbatim) → `kind="assay"` lane with pins → canonical
   consumer pointer → first run → reading/verifying
   `.assay/verdict-<lane>.json`. R1+ adoption noted as an assay.toml-only
   edit.
2. **Gitignore obligation stated** as adoption step 5: copied-script repos
   must replicate the monorepo root's ignores for every path their lanes
   write (union of declared `artifacts` lists = checklist), else the NEXT
   lane's clean-tree check refuses on yesterday's evidence.
3. **Step 4 retro-executed ×9 adopters** (assay, ciu, cmru, nyxloom, topos,
   pwmcp, shared-ramdisk-depot-manager, modern-debian-tools-python-debug,
   plesk-mailbox-create). Deviation with reason: no project carries an
   AGENTS.md, so the canonical-entrypoint line landed in each project's own
   README under "## Testing" (srdm's existing Testing section amended to
   lead with it).
4. **Root-level discovery added**: vbpub root README gained the
   `cd <project> && ./run-gate.py --list` line pointing at CONSUMERS.md.
5. **Budget↔timeout drift guarded by test**, estate-wide:
   `TestEstateBudgetTimeoutPairing` loads each nyxloom-trove project's lanes
   with the REAL parser and asserts consumer `timeout_seconds >= lane
   budget` wherever a gate argv names the lane as a whole token (8 live
   pairings across assay/ciu/nyxloom/topos/srdm; cmru.toml steps carry no
   timeout field — nothing to pair; srdm canary-run.sh names no lane —
   skipped by construction). The sweep caught ONE real drift on its first
   run and it was reconciled: srdm `[gates.privileged-e2e]` timeout 2400s
   truncated the `e2e` lane whose budget is 60m → widened to 3600s with a
   comment naming this rule. Rule documented in CONSUMERS.md ("Consumer
   timeouts must not cut lanes short") + SPEC `R-32`.

With this entry the RG sweep (RG-1…RG-20) is complete.

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

**FIXED 2026-08-24 (rev 20).** `pyproject.toml` wraps the module for
setuptools (toolchain pinned exactly, assay/ciu precedent). The hyphenated
filename cannot be a py_module, so a committed symlink
`run_gate.py -> run-gate.py` gives the build an importable name for the SAME
bytes (dereferenced at copy time); the wheel ships only that module plus
dist-info, byte-identical to the canonical script. Console script
`run-gate = run_gate:main`. Version discipline made structural rather than
test-compared: `[tool.setuptools.dynamic] version = {attr =
"run_gate.__revision__"}` DERIVES the wheel version from the script at build
time — dual bookkeeping (and therefore drift) is impossible by construction,
and tests/test_run_gate.py::TestWheelPackaging pins the derivation plus
builds/installs the wheel in-suite asserting identical `--list` output
between copied script and installed console script. Deliberate deviation:
the entry's `run-gate-vX.Y.Z` tag shape becomes `run-gate-v<derived>` (e.g.
`run-gate-v20`) because the script's whole version story is the bare
revision integer until semver meaning exists; the enforced invariant is
tag-body == wheel version. CONSUMERS.md gained "Distribution — script first,
wheel second"; SPEC §7 rewritten + `R-31`. First release: tag
`run-gate-v21` after merge (the RG-13 rev bump rides along), publish via
cmru's wheel-publish.

**AMENDED 2026-08-24 (release-adoption program).** The `__revision__`-attr
version coupling above is SUPERSEDED, not the wheel-as-second-artifact
design: `bump_version("22")` is unparseable by cmru's conventional-commit
version automation, and an integer counter cannot drive semver. The wheel's
version is now DERIVED from the git tag by setuptools-scm
(`[tool.setuptools_scm]`, matching ciu/cmru/assay/topos/nyxloom exactly);
`__revision__` stays the copy-drift marker but is no longer the version
SOURCE. Two tiers, two jobs — see CONSUMERS.md's "Distribution" section.
SPEC `R-31` rewritten; new `R-33` covers the estate release-orchestration
registration this necessitated. Load-bearing consequence: because the
pre-existing tag `run-gate-v22` itself parses as version `22` under the new
tag pattern, the first real semver release must be numbered `>= v23` or
version ordering inverts.

## RG-15 — assay lanes must execute in the selected worktree, not the invoking checkout

**Filed 2026-08-23 (dstdns repair program; reproduced with linked worktrees).**

### The observation

`run_container_lane` and `run_exec_lane` build assay lanes with the project/config
checkout (`project_dir`) instead of the selected `--worktree`. Invoking
`./run-gate.py <assay-lane> --worktree <linked-worktree>` therefore judged the wrong
tree: dstdns saw `verdict`/`commit` from main while intending to test a feature branch,
and locally committed wrapper fixes were silently not exercised.

### Required behavior

For both command and assay lanes, all user-declared execution paths resolve against the
effective judged tree:

```text
effective_tree = --worktree if provided else invocation toplevel
```

Assay lanes must:
- `cd` into `<effective_tree>`;
- verify pinned artifacts relative to it;
- run assay with its `<effective_tree>`-relative config;
- write verdict/coverage artifacts under `<effective_tree>/.assay/`.

### Oracle

Linked worktree A at commit A plus checkout B at commit B:
`./run-gate.py <assay-lane> --worktree A` must record A's HEAD and write artifacts under
A. Exit-status-only tests are insufficient; assert verdict commit + artifact location.

**FIXED 2026-08-24** (`R-21` in SPEC Rev 3): `effective_project_dir` relocates the
project into the judged tree for both runner modes AND host-lane cwd; pin
verification and verdict paths follow. Oracle landed as `TestEffectiveTreeExecution`
(container assay, exec assay, host cwd, identity-without-override,
outside-toplevel refusal).

## RG-16 — central configs should be allowed to define shared lanes

**Filed 2026-08-23 (same session).**

Current validation rejects any lanes table in a central repo-root config (“central defines
environment facts only”). That blocks the intended estate pattern where every package uses
the identical lane pointer without copying definitions.

### Required policy

Allow central configs to define shared environments AND shared lanes, keeping:

- project entries shadow central entries by name deterministically;
- central lane paths must exist in every consumer project or validation fails;
- malformed central/project tables still fail loudly.

If compatibility is desired, gate via explicit config (e.g. schema_version bump or
`[options] allow_central_lanes = true`) rather than guessing intent from shape.

Local fix reference: dstdns controller branch commit `7b17d331`
(`central and lanes and not envs` interim guard), superseded by this requirement.

**FIXED 2026-08-24** (unconditional admission, per interview): central
`[lanes.*]` schema-validated and inherited; `merge_lanes` shadows by name
wholesale; per-consumer pin-sidecar existence enforced at load naming both
files; argv strings deliberately never stat'd (they are shell text — a check
narrower than its message is the KI-12 class). usage()/`--list` show the
effective set with `*` marking inherited entries; SPEC R-22 + §1 amended,
CONSUMERS central-defaults section rewritten with a real shared-lane recipe.

## RG-17 — env forwarding allowlists silently drop schema-oracle credentials (SCHEMA_GATE_PW)

**Filed 2026-08-23 (dstdns repair program; consumer evidence from P121/P126 sessions).**

### The observation

dstdns' central `run-gate.toml` declared
`forward_env = ["SCHEMA_GATE_DSN", "SCHEMA_GATE_PG_DUMP"]` but omitted `SCHEMA_GATE_PW`.
The schema lane's `as_role` fixture reads `os.environ["SCHEMA_GATE_PW"]`; when absent, the
privilege oracles could not connect as service roles. The mutation helper's equivalence run
reported green while privilege assertions never executed — the exact hollow-green failure
mode the schema lane exists to prevent. Fix required adding `SCHEMA_GATE_PW` (and later
`SCHEMA_GATE_PG_IMAGE`) to the allowlist; each omission was discovered only by manual diff.

### Why this is a run-gate defect class, not a one-off typo

Allowlist-based forwarding is the right security model, but it has no completeness check:
a credential consumed by tests but forgotten in `forward_env` fails silently or, worse,
fails only some assertions. The tool accepts the config without verifying that declared
lane targets consume what they need.

### Proposed contract (either suffices; both compatible)

1. **Declared-consumption check:** lanes may declare
   `required_env = ["NAME", ...]`. Validation refuses to start if any name is missing from
   the resolved environment after forwarding, with an error naming the lane and variable.
2. **Drift sweep:** a lint mode that scans test source for `os.environ[...]` /
   `getenv` literals and warns when such names are neither forwarded nor declared as not
   required.

### Oracles

- Lane declares `required_env = ["X"]`; invoke with X unset ⇒ refuse before execution.
- Controlled wrong implementation: remove `forward_env` entry for a required var ⇒ oracle 1 fires.

**FIXED 2026-08-24** (both proposals, compatible, per the "5b explicitness"
interview choice): lanes gained a validated `required_env` key; the gate
refuses (exit 2) before ANY execution when a declared name is absent or
empty in the invoking environment (RG-19 preflight), and — container lanes
only — when a required name is NOT on the environment's forward_env
allowlist at all (the completeness check this entry demanded: such a
requirement could never reach the lane). The advisory drift sweep is
`--check-env`: scans the project's Python sources for `os.environ[...]` /
`os.environ.get(...)` / `getenv(...)` literals and flags names covered by
neither allowlist nor required_env; heuristic by nature, so it WARNS and
exits 0 — enforcement lives in required_env + preflight. SPEC R-24;
CONSUMERS env-facts paragraph + schema comment. Oracle covered: unset ⇒
refusal with docker never invoked; empty string counts as absent.

## RG-18 — no pg_dump/PostgreSQL version-mismatch guard for schema lanes

**Filed 2026-08-23 (dstdns repair program; consumer evidence: pg_dump 17.11 client vs TimescaleDB PG18 server produced equivalence artifacts that failed silently inside assay snapshots).**

### The observation

dstdns' SQL mutation lane runs `pg_dump` *inside* the server container to guarantee
client/server version match, but the general schema-gate path allowed a runner-baked
pg_dump of a different major version. Mismatches surfaced only as unexplained equivalence
failures during archive inspection — the tooling never named the version pair.

### Proposed contract

`schema-gate.sh` (and any lane that pairs dump client with server) must:

1. read `SHOW server_version_num` from the target server;
2. resolve the matching `pg_dump` binary path (container-internal preferred);
3. refuse loudly with both versions in the error when majors differ;
4. record the resolved versions in any emitted artifact/log line.

### Oracles

- Server PG18 + client PG17 ⇒ refusal naming both versions.
- Matching pair proceeds and records versions in output.

**Sweep-audit note (2026-08-24):** stays OPEN by scope, not neglect — the
guard belongs in `schema-gate.sh` / the dstdns schema lane, not in
run-gate.py; run-gate has no dump/server pairing to guard. Tracked for the
dstdns adoption of these gates.

## RG-19 — schema-lane credential propagation must be verified by the gate, not by test failure

**Filed 2026-08-23 (same evidence as RG-17).**

Schema lanes that provision roles need role passwords (`SCHEMA_GATE_PW`) forwarded into
the runner. When omitted, privilege assertions cannot connect; depending on assertion
shape this is either a loud fixture error or — worse — silently skipped coverage inside an
otherwise green run.

### Required behavior

1. Gate config validation: any lane whose argv/tests reference provisioning credentials
   must declare them in `forward_env`; a static sweep/lint flags undeclared references
   (see RG-17).
2. Runtime preflight: before executing schema tests, verify required credential env vars
   are non-empty; otherwise fail fast naming the missing variable and its consumer.
3. Verdict/log lines record which forwarding keys were present at start (names only,
   never values).

### Oracle

Controlled wrong implementation: remove `SCHEMA_GATE_PW` from forwarding ⇒ gate refuses
pre-execution naming it, instead of tests failing mid-run or skipping.

**FIXED 2026-08-24** (jointly with RG-17, see there): the runtime preflight
is `preflight_required_env` — presence + non-emptiness verified before any
execution, refusal naming lane and variable. The forwarding record is
`log_forwarded_env`, printed at every container-lane start: which
forward_env keys were present and which declared-but-absent — NAMES ONLY,
never values. Discovered during implementation and fixed in the same
entry: the R-05 docker-argv print would have echoed credential VALUES via
`-e KEY=value`; it now masks forwarded payloads (`KEY=<redacted>`) so the
mechanics stay visible without leaking secrets into logs. Test asserts the
sentinel value appears nowhere in the run output.

## RG-20 — replace global gate flock with resource-aware admission

**Filed 2026-08-23 (dstdns repair program; motivated by multi-stack CIU v6+ and cmru's memory-governance pattern).**

### The observation

The current single-gate-at-a-time flock (`/tmp/<project>-testrunner.lock`) serialises all
gates globally, even when they target fully isolated CIU instances with separate networks
and volumes. With multi-stack, this is unnecessarily restrictive: two worktree instances
with independent PG/Redis can run their suites concurrently without contention.

Conversely, the flock does NOT protect against the real hazard — memory pressure. Two
concurrent gates each consuming 2 GB on a host running live services WILL degrade prod,
regardless of whether they share a database.

### The real constraint hierarchy

| Resource | Contention risk | Correct control |
|----------|----------------|----------------|
| CPU | Low (cgroup weights handle fair sharing) | cpu.weight per lane |
| RAM | HIGH (memory bursts cascade into live services) | mem_limit + memswap_limit per lane; sum concurrent lanes against host budget |
| I/O | Medium (heavy DDL/test IO starves other workloads) | io.weight per lane |
| Shared state (same DB volume, same Redis) | HIGH (data corruption / flaky results) | serialize via instance/service-name lock |

CPU weights are sufficient because Linux cgroup CPU scheduling provides proportional
fair-sharing under contention without throttling when idle. RAM is the actual bottleneck
because swap absorbs bursts but cannot prevent OOM cascades into co-resident live
services when combined usage exceeds physical+swap.

cmru's proven pattern (`CMRU_TESTER_MEMORY = "1g"`, `CMRU_TESTER_MEMORY_SWAP = "16g"`)
demonstrates the right shape: tight RAM prevents pressure cascades; ample swap absorbs
transient bursts without OOM kills.

### Proposed contract

Replace global flock with resource-aware admission:

1. Each lane declares `resources.memory`, `resources.io_weight`, `resources.cpu_weight`
   (defaults from config or rigor preset).
2. Gate admission checks:
   - concurrent lanes' summed `resources.memory` fits within the dev-tier slice budget;
   - no shared-infra collision (two lanes targeting the same rendered service name
     cannot run concurrently).
3. Fully isolated instances (separate networks + separate volumes + separate PG/Redis)
   run in parallel freely.
4. Shared-infra serialization uses an instance/service-scoped lock
   (`/tmp/<project>-<service>-gate.lock`), not a global project lock.

### Oracles

- Two isolated instances with disjoint resources ⇒ both gates run concurrently.
- Two gates sharing the same PG instance ⇒ second waits for first.
- Combined declared memory exceeds host dev-tier budget ⇒ second refuses with message
  naming current consumers and required headroom.

**FIXED 2026-08-24** (SPEC `R-29`; interview choice: full §5.7-shaped
admission MINUS rigor presets, budget DERIVED from the slice's cgroupfs
memory.max). Note: run-gate.py itself never had the global flock — that was
the retired dstdns `testing-exec.sh` shim — so this entry IMPLEMENTS
admission rather than removing a lock. Lane key `[lanes.<name>.resources]`:
`memory` (supersedes top-level `memory`; declaring both refused),
`memory_swap` (docker `--memory-swap`, cmru's tight-RAM/ample-swap pattern),
`cpu_weight`/`io_weight` (validated + printed ADVISORY — docker has no
portable cgroup-v2 flag; pretending otherwise would be enforcement theater;
CIU V7 §5.7 owns cgroup-adjacent enforcement), `shared` (service names).
Memory admission reads kernel truth at admission time:
`slice/memory.current + declared <= slice/memory.max` under
`$RUN_GATE_CGROUPFS_ROOT` (default /sys/fs/cgroup, systemd dash-nesting
resolved) — counts EVERYTHING in the slice (other gates AND live services),
so no cross-process bookkeeping can drift. Over budget → exit 2 refusal
naming usage/budget/need/overage; no derivable ceiling → loud warning,
shared-infra-only admission. Shared-infra: per-name flock at
`/tmp/run-gate-shared-<name>.lock`; second gate WAITS with a notice then
proceeds; isolated names never meet. Locks acquired after all fast-fail
preflights, released in finally; `--dry-run` plans but never blocks. Slice
resolution moved from runner into main() so admission and execution share
one resolution. cmru backfilled: all four container lanes declare
1g/16g (the proven CMRU_TESTER_MEMORY values). Tests: TestResourceAdmission
×17 including all three oracles (over-budget refusal with numbers,
same-service serialization with a real held flock, unbounded-slice
degradation).

## RG-21 — linked-worktree checkouts break host-path-mapped lanes (srdm covergate evidence)

**Filed 2026-08-24 (phase-B verification of this sweep; evidence: srdm
coverage lane run from `.worktrees/run-gate-rg-sweep`).**

### The observation

run-gate's `{worktree}` forwarding places lane execution in the selected
worktree correctly, and exit-status passthrough stayed honest — this is NOT a
run-gate.py defect. But a downstream harness that bind-mounts the repo into a
container by HOST path mounts only its own `$repo_root` subtree. From a linked
worktree that subtree is the worktree itself, whose `.git` FILE points at an
absolute gitdir under the MAIN checkout; when that path is not inside the
mount, every in-container git plumbing call fails:

```
covergate: git rev-list --parents -n 1 HEAD failed: exit status 128:
fatal: not a git repository: /workspaces/vbpub/.git/worktrees/run-gate-rg-sweep
```

Same family: `SRDM_HOST_REPO_ROOT` cannot be auto-derived for a worktree path
(the devcontainer's docker inspect maps only `/workspaces/vbpub`), so it must
be exported by hand. Evidence site:
`shared-ramdisk-depot-manager/tools/gate.sh` (`repo_root` = worktree toplevel;
single `-v "$host_repo_root:$repo_root"` mount). On the main checkout the same
lane passes — `.git` is a directory inside the mount.

### Candidate directions

1. Harness-side (real fix): also mount the common gitdir into the container
   (`-v <main>/.git:<expected path>`) or resolve the worktree gitdir and hand
   `GIT_DIR` to the container explicitly.
2. run-gate-side (narrow): `doctor` could WARN when a host-path-mapped lane
   runs from a linked worktree whose gitdir lies outside `{worktree}`
   (detection is cheap: `.git` is a file, not a directory). Listing verbs and
   non-git lanes stay unaffected either way.
3. Document: until one of the above lands, host-path-mapped lanes are
   main-checkout-only when the tree is a linked worktree.

### Oracles

- From a linked worktree, srdm coverage passes (today it fails with the
  gitdir error above).
- `doctor` names the condition before the lane fails mid-run.


## RG-22 — `git config --global safe.directory "*"` fails when global config already has safe.directory entries

**Filed:** 2026-08-24, from dstdns P126/P127 adoption (linked worktrees with
per-worktree instance runners).

### The bug

`build_assay_inner()` and `build_command_inner()` both emit:

```python
shlex.join(["git", "config", "--global", "safe.directory", "*"])
```

Git's `--replace-all` is the correct mode here, but the code omits it. When
`~/.gitconfig` already contains one or more `safe.directory` entries (which is
the NORMAL state after any prior gate run in a multi-worktree estate, or after
any tool that adds a project-specific entry), this command fails:

```
error: cannot overwrite multiple values with a single value
       Use a regexp, --add or --replace-all to change safe.directory.
```

With `set -euo pipefail`, the inner script exits 129 immediately. The lane
reports exit 5 (run-gate's generic failure), and the operator sees only the
cryptic git error — no indication that the fix is trivial.

### When it fires

1. First gate run: works (no pre-existing entry → single-value write succeeds).
2. A second run in a DIFFERENT linked worktree whose runner shares `/root`
   but has a different `.git/config`: also works (still one value).
3. Any scenario where another tool (CIU hooks, IDE integration, or a previous
   run-gate invocation using a DIFFECT gitconfig) has already added a
   project-specific `safe.directory` path alongside `*`: FAILS.

This last case is the dstdns trigger: CIU's per-instance provisioning writes
`safe.directory = /workspaces/dstdns/.worktrees/<name>` into the shared
`/root/.gitconfig`. The next run-gate lane then hits "multiple values" on its
own `*` write.

### Why it matters

The failure is silent in the sense that the error message points at git's
config syntax rather than at run-gate's own missing flag. An operator seeing
"cannot overwrite multiple values" has no reason to suspect a one-line fix in
the harness. Worse, the error is INTERMITTENT from the operator's view: it
appears only after some other tool has populated the config, so the same
command can pass and fail depending on what ran before it.

### Fix

Add `--replace-all` to both call sites:

```diff
-shlex.join(["git", "config", "--global", "safe.directory", "*"]),
+shlex.join(["git", "config", "--global", "--replace-all", "safe.directory", "*"]),
```

Two locations: `build_assay_inner()` (~line 1145) and
`build_command_inner()` (~line 1186). The semantic intent is already "make
this the ONLY safe.directory value for this ephemeral gitconfig" — which is
exactly what `--replace-all` does.

### Alternative considered

Use `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0` env vars instead of writing a file,
sidestepping the overwrite problem entirely:

```python
parts.append("export GIT_CONFIG_COUNT=1")
parts.append("export GIT_CONFIG_KEY_0=safe.directory")
parts.append("export GIT_CONFIG_VALUE_0=*")
```

This avoids mutating any persistent state, which is arguably cleaner (the
gitconfig at `/tmp/run-gate-gitconfig` exists solely as a side-channel for
this one directive). However, `--replace-all` is the minimal change and keeps
the existing file-based mechanism intact.

### Oracle

```bash
# Pre-populate the global config with an extra entry
git config --global --add safe.directory "/some/project"
# Then invoke any exec-mode lane; before fix: exit 129 with "cannot overwrite"
# After fix: passes cleanly
```

**FIXED 2026-08-24 (rev 23).** `--replace-all` added to both call sites, as
proposed above; the `GIT_CONFIG_COUNT` alternative was not taken (minimal
change, no behavior change to the isolated-gitconfig mechanism). SPEC `R-19a`
now states the write is idempotent under pre-existing entries. Oracle landed
as `test_safe_directory_write_survives_preexisting_entries`, which
pre-populates the real isolated gitconfig with two entries and runs the built
inner command as a live subprocess (fails pre-fix with "cannot overwrite
multiple values", passes after).

## RG-23 — exec-mode's hardcoded env-forward allowlist was dropped with no consumer migration; unmigrated consumers silently stop forwarding `RUN_LIVE_TESTS`/`MOCK_MODE`

**Filed:** 2026-08-25, from the vbpub controller's assay 2.1.0→2.3.0
review-gap audit (`vbpub/assay/nyxloom-trove/reports/assay-review-gap-audit-2026-08-25.md`
§5, finding ba-D — filed against assay's commit `ba8908d6` because that
commit's SQL/infrastructure-forwarding work is what motivated this change,
but the defect itself is entirely inside `run-gate.py`).

### The bug

`run_exec_lane` (`run-gate.py:1394`, at `ba8908d6`) replaced a hardcoded
allowlist with a declaration-driven one:

```diff
-for key in ("MOCK_MODE", "RUN_LIVE_TESTS", CGROUP_ENV_VAR):
+for key in (CGROUP_ENV_VAR, *env.get("forward_env", [])):
```

No consumer `.toml` was migrated to add `RUN_LIVE_TESTS`/`MOCK_MODE` to its
`forward_env` list, and neither `SPEC.md` nor `CONSUMERS.md` records the
removal as a breaking change requiring migration. Still live on `main` today
— unchanged since `ba8908d6`.

### Confirmed consumer impact (dstdns)

`/workspaces/dstdns/run-gate.toml`'s `[environments.test-runner]`
`forward_env` (line 12) lists 13 names; `RUN_LIVE_TESTS` is not among them.
`[lanes.release]`'s own comment (line 189) still reads "Consumer must set
RUN_LIVE_TESTS=1 (forwarded by run-gate exec-mode)" — true before this
commit, false since. Reproduced by replaying the exec-lane env-forwarding
logic against the real dstdns `run-gate.toml`: `RUN_LIVE_TESTS` is not among
the `-e` flags the container actually receives.

**Failure scenario:** an operator exports `RUN_LIVE_TESTS=1` as the lane's
own comment instructs and runs `release`
(`pytest -m 'integration or observability or e2e or infra'`). The variable
never enters the container; dstdns's `tests/conftest.py` (`:588`, `:611-613`)
skips every selected test on its absence; an all-skipped pytest run exits 0.
**The lane reports GREEN having executed no live test** — a silent false
green on the exact class of test the lane exists to run.

Neither of run-gate's own safety nets catches it: the `release` lane
declares no `required_env` (so `preflight_required_env` never fires), and
`--check-env`'s `ENV_REF_RE` needs a string literal inside `getenv(...)`,
while dstdns reads the flag through `os.getenv(name, "")` wrapped in a
helper (`_env_flag_enabled(name)`), which the regex does not match.

### Why this is run-gate's bug, not (only) a consumer config gap

The allowlist→declaration change is a breaking API change for every exec-mode
consumer that relied on the two implicit names, shipped with no migration
pass across consumers and no CONSUMERS.md/SPEC.md note that `forward_env`
must now explicitly list them. A silent false-green on a live-test lane is
exactly the failure class run-gate's own `--check-env`/`required_env`
machinery exists to catch, and it does not catch this one.

### Fix

Two independent halves:
1. **run-gate-project (this repo):** document the breaking change in
   `SPEC.md`/`CONSUMERS.md` explicitly (the two names are no longer
   implicit — every consumer relying on them must add them to its own
   `forward_env`), and consider whether `--check-env` should be extended to
   catch the `os.getenv(name, ...)`-wrapped-in-a-helper shape dstdns uses
   (or document that limitation explicitly so consumers know a bare-regex
   check will not see the flag).
2. **dstdns (separate repo, not fixed here):** add `RUN_LIVE_TESTS` to
   `[environments.test-runner]`'s `forward_env`, and preferably add it to
   `[lanes.release]`'s `required_env` so a missing value refuses loudly
   instead of silently skipping every selected test.

### Oracle

A real exec-mode lane, driven through the installed `run-gate.py`, whose
declared `environment_command`/test suite asserts a forwarded env var is
present — before this fix: absent when relying on the old implicit names;
after: present once `forward_env` is corrected, refused loudly by
`required_env` if omitted.

### Acceptance

- [ ] `SPEC.md`/`CONSUMERS.md` document the breaking change and the
      migration every exec-mode consumer must make;
- [ ] a decision recorded on whether `--check-env` should be extended to
      catch helper-wrapped `getenv` reads, or documented as a known
      limitation;
- [ ] every vbpub-estate exec-mode consumer's `forward_env` audited for
      env vars it relied on implicitly before `ba8908d6`;
- [ ] dstdns's own fix (see above) tracked and confirmed landed — cross-repo
      pointer, not owned here.

## RG-24 — `resolve_container_name()` reads `ciu.global.toml` from the shared-`.git`-owning repo, never from the judged worktree, so a multi-instance (Mode-B) worktree's exec-mode lane silently targets the WRONG deployed container

**Filed:** 2026-08-30, dstdns-P147b (`dstdns@1171d8d3`,
`nyxloom-trove/decisions.md` D-247; worktree
`/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e`).

### The bug

`resolve_container_name()` (`run_gate.py:1319-1361`), for an exec-mode
environment with no declared `container_name`, derives the container name
from `repo / "ciu.global.toml"`'s `[deploy] project_name`+`environment_tag`
(or, failing that, `network_name` stripped of `-network`). `repo` here is
NOT the judged worktree — per `resolve_repo_and_worktree()`'s own
docstring, `repo` is deliberately "the checkout owning the shared `.git`"
(worktrees live under it), i.e. the MAIN checkout, for ANY git worktree,
regardless of the `--worktree` CLI argument. This split (`repo` for
git-object-store concerns, `worktree` for judged-tree concerns) is the
right design for locating SOURCE CODE — a linked worktree shares one
object store with its main checkout — but it is the WRONG source for
locating a LIVE DEPLOYED CONTAINER's name under any consumer that runs a
genuinely separate, per-worktree deployment (dstdns's own "Mode-B"
pattern, `nyxloom-trove/GUIDE.md` §3: `ciu worktree adopt` gives a
worktree its OWN isolated stack, its OWN rendered `ciu.global.toml` with a
worktree-specific `project_name`/`environment_tag`, and its OWN persistent
`test-runner` container on its OWN docker network).

### Reproduced

A dstdns Mode-B worktree at
`/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e` has its own
correctly-rendered `ciu.global.toml`
(`project_name = "p147b-vertical-corpus-e2e"`, `environment_tag =
"8a6bc3"`) and its own deployed, healthy
`p147b-vertical-corpus-e2e-8a6bc3-test-runner` container on its own
network. Running `run-gate <live-lane> --worktree
/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e` from within that
worktree (so the worktree's OWN `run-gate.toml` is correctly loaded for
lane/environment table lookup — that half works) nonetheless executed the
lane's pytest INSIDE `dstdns-98535c-test-runner` — the MAIN landscape's
own, separately-deployed, pre-existing `test-runner` container —
confirmed by the failing test's own `controller_url` fixture resolving to
`http://dstdns-98535c-controller:8080` (the MAIN landscape's controller
alias) rather than `http://p147b-vertical-corpus-e2e-8a6bc3-controller:8080`
(this worktree's own). `resolve_container_name()` had derived
`dstdns-98535c-test-runner` from the MAIN checkout's `ciu.global.toml`
(`project_name = "dstdns"`, `environment_tag = "98535c"`), exactly as its
current logic dictates.

**Why the failure mode is partial, not total, and easy to miss:** both
`test-runner` containers bind-mount the SAME host repo root (`.worktrees/`
is a subdirectory of it in both), so the lane's own `cd {worktree} &&
pytest ...` argv still `cd`s to and collects the CORRECT test files even
when exec'd into the wrong container — only that container's OWN baked
runtime environment (network attachment, env vars such as
`CONTROLLER_URL`) is wrong. A lane with no live-network dependency
(dstdns's own MOCK_MODE fast lane; a schema lane provisioning its own
throwaway Postgres) produces IDENTICAL, believable results regardless of
which container ran it — only a lane depending on THIS worktree's own
live, instance-scoped network resources exposes the defect. Worse, a
fixture that shells out to `docker exec <container-name-from-config> ...`
and returns only `.stdout` (discarding `.stderr`/`.returncode`) can fail
completely SILENTLY under this misrouting — an empty string, not a loud
crash — if the wrongly-resolved config also causes it to target a
nonexistent resource in the wrong deployment (observed independently as a
dstdns-side defect, `nyxloom-trove/decisions.md` D-246, compounding this
one's symptoms in the same live run).

### Why no existing mechanism helps

An explicit `container_name` on `[environments.test-runner]` is the only
current escape hatch, and it does not fit: it would have to be hardcoded
to ONE instance's own generated name (`p147b-vertical-corpus-e2e-8a6bc3-
test-runner`) in the TRACKED `run-gate.toml`, which is shared by every
OTHER lane using that environment AND by every future worktree/instance —
correct for exactly one running instance, wrong for the very next one
created (the same class of hazard dstdns's own AGENTS.md §4.2a names for
a "shadowing default": a literal standing in for a value that has an
authoritative source elsewhere).

### Fix

`resolve_container_name()` should read `ciu.global.toml` relative to the
JUDGED WORKTREE (the function already receives enough context — or could
receive the `worktree` parameter already threaded through
`run_exec_lane`'s own call site — to do this) for THIS ONE purpose:
deriving a live deployed container's name. `repo`-relative resolution
remains correct for everything else `resolve_repo_and_worktree()` serves
(e.g. pin-sidecar existence checks, which are legitimately about the
shared object store's own tree, not a live deployment). A worktree with no
own `ciu.global.toml` (i.e. not itself a `ciu worktree adopt`-managed
Mode-B instance) should fall back to the current `repo`-relative
resolution unchanged — this is an ADDITIVE precedence fix, not a
replacement.

### Workaround used (disclosed, not a fix)

This package's own live-lane evidence was gathered via a direct `docker
exec -w <worktree> <correct-instance-test-runner-container> bash -c
'<the lane's own argv, verbatim>'` — the identical command `run-gate`
would run, against the container `run-gate` should have chosen — rather
than trusting `run-gate`'s own container resolution for this one
worktree-scoped live lane.

### Acceptance

- [ ] `resolve_container_name()` (or its caller) accepts/derives the
      judged worktree and prefers `<worktree>/ciu.global.toml` over
      `<repo>/ciu.global.toml` when the former exists and differs;
- [ ] a regression test constructs two `ciu.global.toml`s (one at a fake
      "repo", one at a fake "worktree" beneath it) with different
      `project_name`/`environment_tag` and asserts the worktree's own
      config wins;
- [ ] `SPEC.md`/`CONSUMERS.md` document the worktree-vs-repo distinction
      for this one resolution path explicitly, since it is easy to
      conflate with the (correct, unchanged) repo-relative resolution used
      elsewhere.
