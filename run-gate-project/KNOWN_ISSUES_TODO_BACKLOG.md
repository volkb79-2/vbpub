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
| RG-21 | linked-worktree checkouts break host-path-mapped lanes (srdm covergate evidence) | Minor | FIXED 2026-08-31 (rev 26) — directions 2+3 (doctor warning + docs); direction 1 is harness-side, not run-gate's to build |
| RG-22 | `git config --global safe.directory "*"` fails when global config already has safe.directory entries | Minor | FIXED 2026-08-24 |
| RG-23 | exec-mode's hardcoded env-forward allowlist was dropped with no consumer migration; unmigrated consumers silently stop forwarding `RUN_LIVE_TESTS`/`MOCK_MODE` | Major | FIXED 2026-08-31 (rev 25) — run-gate half; dstdns half open in its own repo |
| RG-24 | `resolve_container_name()` derives an exec-mode container's name from the shared-`.git`-owning repo's `ciu.global.toml`, never the judged worktree's own — a multi-instance (Mode-B) worktree's live lane silently targets the WRONG deployed container | Major | FIXED 2026-08-31 (rev 24) |
| RG-25 | `doctor`/`--check-env` cannot see that an assay lane's language needs a toolchain (node, go helper) in its environment — consume `assay lanes --json` (assay B044) for a per-lane fitness check; backport of ciu CIU-72 (b) | Enhancement | FIXED 2026-08-31 (rev 27) |
| RG-26 | no `--base REF` passthrough to `assay run --request-base` — assay B019 (≥ 3.0.0) unusable from the gate; delegating lanes DERIVED from `assay lanes --json`, no new lane key; backport of ciu CIU-72 (c), absorbs v8 proposal N12 | Major | FIXED 2026-08-31 (rev 28) |
| RG-28 | `run_host_lane` raised `KeyError('argv')` for a `kind = "assay"` lane on the built-in `host` environment — a config the validator ACCEPTS, so a traceback for a legal declaration (R-04) | Minor | FIXED 2026-08-31 (rev 28) |
| RG-29 | `cmru/run-gate.toml [lanes.assay]` pins a sidecar (`tools/assay/assay-2.2.0.pyz.sha256`) that no longer exists — cmru vendored 2.3.0 — which makes run-gate-project's OWN gate lane red via `validate-pointers` | Major | FIXED 2026-08-31 — cmru-side config (`cmru/run-gate.toml`), all four filename sites moved to 2.3.0 |
| RG-27 | run-gate has no persisted per-lane-per-commit invocation history and no query verb — a controller deciding sync-vs-async/defer rigor has no data; retriaged from ciu CIU-55 (2026-08-25) to run-gate, which is the layer with direct invocation visibility in the current (pre-v8) architecture | Enhancement | FIXED 2026-08-31 (rev 30, run-gate-P03) — `history [LANE] [--json]` verb; store `<project>/.run-gate/history.json`, per (judged worktree × project) |
| RG-30 | `doctor` and `--check-env` both pass `None` to `resolve_repo_and_worktree` (`run-gate.py:1789`, `:2068`) instead of the caller's `--worktree` value, so `doctor --worktree B` silently reports the INVOKING tree's answers, not B's — including RG-21's worktree-specific host-lane git-view WARN, which is exactly the per-tree answer that can legitimately differ | Medium | FIXED 2026-08-31 (rev 31, run-gate-P04) — new shared `resolve_worktree_scope()` (validates a real git worktree, refuses by name otherwise); `doctor`'s per-tree checks (git identity, RG-21 warning, mountinfo) and the shared `assay_toolchain_findings()` probe's `cd` target (used by both `doctor` and `--check-env`) now resolve/relocate under `--worktree`, disclosed in the report; `--check-env`'s env-drift scan follows it too and refuses upfront on a bad override (no per-check ledger to degrade into). SPEC `R-37` (`R-37a`/`R-37b`/`R-37c`). `./run-gate.py selftest` green: 394 passed, 2 skipped, diff-coverage 22/22 = 100.0%, exit 0 (commit `929be064`) |
| RG-31 | `assay_toolchain_findings()`'s own `resolve_repo_and_worktree` call (the toolchain-fitness probe shared by `doctor` check 5 and `--check-env`) still takes the RAW `worktree_override` string, not RG-30's new validated `resolve_worktree_scope()` — so a bad `--worktree` combined with an assay lane present degrades safely (a `[SKIP]` on that check, no false-`[OK]`) but with a MISLEADING reason string (blames "an assay older than 3.2.0" rather than naming the real `--worktree` problem, which `doctor` check 3 already reported correctly two checks earlier in the same report) | Low | FIXED 2026-09-01 (rev 32) — routed through `resolve_worktree_scope()`, the same validated resolver check 3 and `--check-env` already use; a bad override now raises the identical `GateError` and the existing per-lane `except GateError` SKIP handler reports the real cause, never a guess about assay's version. New regression test `test_bad_worktree_skip_names_the_real_problem_not_assay_version` asserts the SKIP line repeats check 3's own "not a directory" cause and never says "older than 3.2.0". `./run-gate.py selftest --allow-dirty` green: 395 passed, 2 skipped, diff-coverage 0/0 = 100.0% (pre-commit run), exit 0 |
| RG-32 | `[lanes.*.pins.*].budget` is silently inert — run-gate never read it, the governing value is the target `assay.toml`'s own `[lanes.<assay_lane>] budget`, and the key sits one nesting level below a REAL lane-level `budget` that reads identically (misread three times in one dstdns session) | Major | FIXED 2026-09-02 (rev 34, SPEC `R-08a`) — **BREAKING**: refused at load by name with the owner and the remedy; pin tables now validate their keys (`sha256`, `version`, nothing else), and a misplaced key that is itself a LANE key is named as one ("move it, do not delete it"); migration is TWO rounds over 18 of 35 dstdns lanes as measured 2026-09-03 (parsed, RW-13/RW-30; re-measure command in CHANGES), in CHANGES |
| RG-33 | `kind = "assay"` mutation lanes never receive `--resume` (or `--progress`), so a budget-capped retry re-tests every mutant from #1 — dstdns `sql-mutation`, three 120-minute retries spent on the first of four target files, `.assay/mutation-state/` never written | Major | FIXED 2026-09-02 (rev 33, SPEC `R-38`) — every assay-kind invocation now carries `--resume --progress .assay/progress-<assay_lane>.jsonl` unconditionally (no-ops without R2, per assay's own contract); a pin declaring a judge older than 2.4.1 refuses by name at argv construction; five new tests in `TestResumeAndProgressAlways` including the executed host-runner argv and the dry-run docker argv line; assay's own gate script mirrors it in the assay wave |
| RG-34 | a `kind = "command"` container lane whose `argv[0]` is a bare relative script path resolves against the container's `--workdir`, so it dies with `exit 127` in any container that mounts only the judged worktree (dstdns P152's `schema` lane, 100% reproducible) while working under the shared full-repo mount | Major | FIXED 2026-09-02 (rev 34, SPEC `R-30b`) — run-gate's half: `doctor` names the lane, the element, the fix and the mechanism; a WARNING, never a refusal, and run-gate never rewrites a consumer's argv. CLOSED 2026-09-03 (RW-26): the argv edit itself is dstdns-side, so the live `scale-admission` hit is a line in the dstdns notification, not an acceptance box run-gate can never tick |
| RG-35 | a lane's container outlives a dead run-gate client (`docker run -d` … `rm -f` in a `finally` the client never reaches), but nothing re-attaches: exit status, evidence and history are lost and the next invocation starts a DUPLICATE container for the same lane — the one-gate rule broken by the tool | Major | FIXED 2026-09-02 (rev 34, SPEC `R-39`) — `.run-gate/inflight/<lane>.json`, automatic re-attach/collect/report-lost, `--fresh` escape, commit mismatch refused |
| RG-36 | the only liveness bound for a long assay lane is a GUESSED total `budget` (advisory here, hard in assay); rev 33's progress file makes rate/ETA/stall observable but run-gate reads none of it | Major | FIXED 2026-09-02 (rev 34, SPEC `R-40`) — the COARSE half: 30 s progress disclosure with rate/ETA, no-events disclosed once and never a fault, optional `stall_timeout` lane key (assay lanes only; stops the lane only while RUNNING and silent that long, never on total elapsed). Exact timing = E-3, needs assay B065; the code already prefers an event's `elapsed_s`, so B065 makes it exact with no rewrite |
| RG-37 | exec-mode container derivation (`run-gate.py` `resolve_container_name`, R-14a) reads `deploy.project_name` + `deploy.environment_tag` (fallback `deploy.network_name`) from the consumer's rendered `ciu.global.toml`; a CIU v8 checkout (SPEC-V8 draft.3, ciu CIU-92) renders `ciu.resolved.toml` instead, with identities as data under `[resolved.identities.<realization>.<service>] container_name`, and has no `deploy` table — every dstdns exec lane would fail container resolution the day dstdns moves to v8, while the operator decided (2026-09-02) that run-gate STAYS maintained in parallel with `ciu gate` and is "aligned with future changes in ciu v8" | Major | OPEN 2026-09-02 — filed from the v8 design review (ciu `docs/CIU-V8-ADVERSARIAL-REVIEW-2026-09-02.md` R-01, proposal §4.4 V8-19 / §4.11 N18): additive lookup order — when `ciu.resolved.toml` exists in the judged checkout, resolve `environments.<n>.container_name` (or a new `exec_in = "<realization>.<service>"` key) through `resolved.identities`, otherwise keep the v7 path; `kind = "sequence"` in-process conjunction lanes (N21) are the second alignment item |
| RG-41 | a container `kind = "command"` lane has NO liveness signal at all: `stall_timeout` is refused there (rev 34, R-40c — it is judged from a progress file only an assay lane writes), so the lane shape most likely to hang is the one run-gate cannot bound except by a `budget` it never enforces | Major | OPEN 2026-09-02 — RW-9: judge silence from the LOG STREAM run-gate already tails (`docker logs -f`), same "silence, never elapsed" semantics as `R-40`, with the SOURCE of the signal disclosed at start (`progress file` vs `log stream`); E-3 candidate (23.5.0) |
| RG-40 | `tools/coverage_gate.py` takes its changed-line numbers from `git diff base..HEAD` (committed) but its coverage from the file ON DISK, so running the `selftest` lane with `--allow-dirty` over an uncommitted change reports lines as uncovered that are covered — the two are offset by whatever the working tree added above them | Medium | OPEN 2026-09-02 — measured twice in the rev-34 wave (`175/177 (98.9%)` dirty → `153/153 (100.0%)` on the same code once committed); either diff the WORKING TREE when the tree is dirty, or refuse/disclose the mismatch instead of printing a number nobody can act on |
| RG-38 | resume state lives under the JUDGED project root, so a fresh worktree per run (cmru release transaction, Mode-B instances) loses it and a retry restarts from mutant #1 despite `--resume` | Medium | OPEN 2026-09-02 — bind-mount a per-repo durable `.run-gate/assay-state/<project>/` at the state path; needs assay B066 (`--state-dir`); copy-in/out fallback until then |

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

**FIXED 2026-08-31 (rev 26) — directions 2 and 3. Direction 1 is deliberately
NOT taken here.**

Direction 1 (mount the common gitdir / hand over `GIT_DIR`) is the real fix
and it is HARNESS-side: the `docker run` that mounts only `$repo_root`
belongs to `shared-ramdisk-depot-manager/tools/gate.sh`, not to run-gate,
which owns neither that argv nor srdm's repo. Building it here would mean
run-gate reaching into a consumer's own container construction — the exact
inversion the one-parser design (D-110) exists to prevent. What run-gate CAN
own is telling the operator before the lane dies mid-run, and telling every
future harness author how to fix their own mount.

- **Direction 2 (`doctor` warning), `R-30a`:** `linked_worktree_gitdir()`
  returns the absolute gitdir when `<worktree>/.git` is a FILE whose target
  lies OUTSIDE the tree, and `None` for both benign shapes (plain checkout;
  gitfile pointing inside the tree — that one travels with any mount, so
  reporting it would be a false alarm). `doctor` emits ONE `[WARN]` naming
  the worktree, the gitdir, the exact symptom (`not a git repository:
  <gitdir>`) and three remedies. It never moves doctor's exit code:
  run-gate is not defective here, and a warning that overstated itself into
  a refusal would block a lane that works fine on the main checkout.
  **Scoped to projects declaring an `environment = "host"` lane** — the only
  kind that can reach such a harness, since run-gate's own container/exec
  lanes dual-mount the REPO root (`R-23`) and cannot hit this. With a host
  lane and a plain checkout the check records `[OK]`, so a reader can tell
  it ran rather than inferring health from silence.
- **Direction 3 (document):** CONSUMERS "Host lanes that delegate to a
  host-path-mounting harness (RG-21)" — the real srdm error verbatim, the
  doctor line, and THREE pasteable harness-side fixes (mount
  `--git-common-dir`, export `GIT_DIR`, or declare the lane main-checkout-
  only in its `description`), plus the `SRDM_HOST_REPO_ROOT` note that a
  worktree's host path cannot be auto-derived from `docker inspect`.

Tests: `TestLinkedWorktreeHostLaneWarning` ×7 — the gitdir helper in all four
shapes (plain checkout, real `git worktree add`, gitfile pointing inside the
tree, gitfile with no `gitdir:` line) and doctor in all three states (linked
worktree + host lane → WARN naming the symptom and the remedies; plain
checkout + host lane → OK; linked worktree, container lane only → the check
does not appear at all).


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

**FIXED 2026-08-31 (rev 25) — the run-gate half. The dstdns half stays OPEN
in its own repo (cross-repo pointer, deliberately not owned here).**

1. *Breaking change documented, with the migration.* SPEC `R-24a` (forwarding
   is DECLARED, never implicit — `CGROUP_PARENT_DEV_BACKGROUND` is the sole
   exception, being infrastructure the tool itself owns), CONSUMERS "BREAKING
   CHANGE — migrate if you use `mode = "exec"`" (a pasteable two-half
   migration: `forward_env` restores the old behaviour, `required_env` is
   what converts the silent-skip into a loud refusal), README env-forwarding
   bullet. The implicit names deliberately do NOT return: reinstating them
   would re-create the shadowing default the declarative key removed. The
   entry's own preferred shape — document + consider extending `--check-env`
   — is what shipped; the allowlist was NOT restored.

2. *Decision on `--check-env` (the entry's open question): EXTENDED, not
   documented away.* A sweep whose comparison is narrower than its message
   issues a false certification, which is worse than no check (AGENTS "a
   check is only as strong as what it actually compares") — and this one had
   already certified a clean bill of health over the exact variable whose
   absence made the lane green. `scan_env_references()` replaces the line
   regex with an AST pass that sees `os.environ[...]`,
   `.get/.setdefault/.pop`, `getenv`, `"X" in os.environ`, and a literal
   handed to the project's own env-reader helper (a function reading the
   environment through one of its parameters — dstdns's
   `_env_flag_enabled("RUN_LIVE_TESTS")` shape), with bound-method parameter
   offsets accounted for so a method never reports a name taken from the
   wrong argument position. It remains ADVISORY (exit 0) and `R-24b`
   documents what it still cannot see (runtime-assembled names, non-Python
   sources) so a clean sweep reads as evidence, not a certificate. An
   unparseable file is named and falls back to the old regex — "could not
   read it" is never rendered as "there is nothing there".

3. *Estate audit performed.* At rev 25 NO vbpub project declares
   `mode = "exec"`, none declares `forward_env`, and none references
   `MOCK_MODE`/`RUN_LIVE_TESTS` in any `run-gate.toml` — the estate-side
   blast radius is empty and the confirmed impact is dstdns alone. The audit
   is kept as a TEST (`TestEstateExecForwardEnvAudit`) rather than a note, so
   a future estate exec-mode adopter that reacquires the assumption fails
   here instead of shipping a false green.

Tests: `TestEnvReferenceScan` ×9 (every read shape, the helper oracle, the
async/bound-method offset, the positional-only position, lookalike dict reads
NOT reported, too-few-arguments call sites, `SyntaxError` propagation, and
both end-to-end `--check-env` paths) + `TestEstateExecForwardEnvAudit`.

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

**FIXED 2026-08-31 (rev 24).** `resolve_container_name()` takes the judged
`worktree` (already threaded through `run_exec_lane`) and resolves
`<worktree>/ciu.global.toml` → `<repo>/ciu.global.toml` in that order;
a declared `container_name` remains the top of the precedence chain
(unchanged). Additive, exactly as the entry asks: a worktree without its own
config keeps repo-relative resolution byte-for-byte. Two visibility changes
came with it, because the defect's real cost was that "which config decided
this" was never printed: the resolution source now carries a SCOPE label
(`judged worktree:` / `repo:` + the path), and it is now part of the
pre-execution `container …` disclosure line (R-05), not only of the
not-running refusal. A missing-config refusal names BOTH candidate paths when
they differ — naming one would send an operator to `ciu render` the wrong
tree. The disclosed workaround (`docker exec` by hand into the correct
instance) is NOT reproduced anywhere in the tool; it was evidence-gathering,
not a design. Tests: `TestWorktreeScopedContainerName` ×5 — the regression
oracle runs with BOTH containers present and running in `docker ps`, since a
test where only the right one exists would pass against the buggy code by
`not running` refusal rather than by correct resolution. SPEC `R-14a`;
CONSUMERS "Python app estate with its own runner" (worked disclosure line +
an explicit "do not pin `container_name` as a workaround" warning).

## RG-25 — `doctor`/`--check-env` cannot see that an assay lane's LANGUAGE needs a toolchain in its environment; consume `assay lanes --json` (assay B044) for a per-lane fitness check

**Filed:** 2026-08-30, from the assay 3.1.0 design review
(`assay/nyxloom-trove/reports/assay-3.1-js-adapter-design-review-2026-08-30.md`
§4 D3) — the current-gate backport of ciu **CIU-72 (b)**; backportable
because the gate on the current schema IS run-gate and the change is
additive (the v8 proposal's own §4.11 "ship now" class). **SPEC
ownership:** §2 `R-01` (`--check-env`, `doctor`), `R-05` (disclosure); §5
execution contract (preflight). Code: `cmd_doctor` (`run-gate.py:964`),
`cmd_check_env` (`run-gate.py:1598`), `build_assay_inner`
(`run-gate.py:1141-1180`, the existing in-environment `--version` probe).

### The gap

run-gate reads nothing from `assay.toml` — the `assay_lane` name is a
string it passes through (`build_assay_inner`). `doctor` checks docker,
per-environment resolution and slice state (`run-gate.py:975-1020`);
`--check-env` checks the env-forward contract (RG-17/19/23). Neither can
know that `[lanes.ui-unit] kind = "assay"` → `assay.toml
[lanes.ui_unit] judge.language = "javascript"` needs `node`/`npm` on PATH
inside `environment = "test-runner"`, or that a future Go lane needs
assay's statement-position helper (assay A-239). Today the first sign is
the lane itself: `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` at best; with
`npx` and no `--no-install`, an unpinned registry fetch inside the gate
container (assay B041). The fact is knowable statically; assay B044
(`assay lanes --json`) makes it askable without run-gate ever parsing
`assay.toml` — it asks the judge, the same way it already asks
`--version`.

### Fix (additive; `schema_version = 1` unchanged)

- In `cmd_doctor` and `cmd_check_env`, for every `kind = "assay"` lane whose
  environment resolves: run `<assay_command> lanes --json --file assay.toml`
  INSIDE the lane's environment through the SAME exec/ephemeral probe path
  the pin `--version` check uses (one docker construction site, never a
  second), find the entry named `assay_lane`, and `command -v` each of its
  `external_tools` and its `argv0` inside the environment. Report in
  doctor's existing `record()` format: `[OK] lane 'ui-unit' toolchain:
  node, npm` / `[FAIL] lane 'ui-unit' needs 'node' in environment
  'test-runner'`; `--check-env` exits 2 on a FAIL (its existing severity
  for a broken contract).
- The named `assay_lane` missing from the inventory → `[FAIL] assay lane
  'ui_unit' not declared in assay.toml` — a run-time refusal becomes a
  doctor finding (validate-pointers' spirit).
- Judge unreachable, or an assay without `--json` (older than B044) →
  `[SKIP]` naming why; NEVER a FAIL for an older judge — the pin declares
  the version, run-gate must not require a floor it never declared.
  `inventory_schema != 1` → `[SKIP]` with the value.

### Acceptance

- [ ] `doctor` and `--check-env` emit the per-lane lines; tests with a fake
      `assay_command` script: inventory with `external_tools = ["node"]`
      against a fake environment lacking `node` → FAIL naming
      lane/tool/environment; with `node` → OK; script without `--json` →
      SKIP; lane absent from the inventory → FAIL;
- [ ] grep proves ONE in-environment probe builder shared with the pin
      probe (no second `docker exec`/`docker run` argv construction);
- [ ] SPEC §2 `R-01` (`--check-env`/`doctor`) updated; CONSUMERS
      `kind = "assay"` section names the check beside the closure note
      added 2026-08-30.

**FIXED 2026-08-31 (rev 27), SPEC `R-34` (+ `R-01`, `R-30` amendments).**
`assay_toolchain_findings()` emits one `(status, topic, detail)` per assay
lane; `cmd_doctor` feeds them to its existing `record()` and `cmd_check_env`
prints them and exits 2 on a FAIL. `build_env_probe_argv()` is the single
in-environment probe builder (reusing `resolve_container_name()` for exec and
`physical_path()`/`dual_mount_flags()` for ephemeral); a test asserts by
source inspection that the only functions constructing a `docker run`/`docker
exec` argv are `run_container_lane`, `run_exec_lane` and
`build_env_probe_argv`. Ephemeral probes carry `--cgroup-parent` like any
container this tool starts, and SKIP rather than run unconfined where no
slice is derivable.

**Deviation from the entry's letter, and why (flagged for controller
review).** The entry says to `command -v` "each of its `external_tools` and
its `argv0`". Implemented as `external_tools` ∪ `argv0` ∪ a `language`
toolchain table, because assay 3.2.0's own `docs/CONSUMERS.md` states that
`external_tools` is `()` for EVERY shipped adapter today and that "a gate
consumer should not build a `MISSING_EXTERNAL_TOOL` preflight around this
field expecting it to name node/npm for a javascript lane — that check today
has to come from `language` itself". Following the letter alone would have
shipped a check that reports a clean bill of health for a JavaScript lane in
an environment with no Node — precisely the gap this entry was filed for, and
precisely the false-certification class AGENTS forbids. The entry's OWN
example output (`[OK] lane 'ui-unit' toolchain: node, npm`) is only reachable
via `language`, so the two readings agree on the outcome. The table is kept
minimal (`javascript`, `go` — the two assay documents), and an unmapped
language attaches an explicit caveat to the line rather than being silently
read as "nothing needed".

Deliberately NOT implemented (not in the entry's contract; noted for a future
entry): `rigor` vs `rigor_reachable` — the inventory also exposes rigor levels
a lane declares that THIS assay build cannot reach for its language, which is
a second preflightable mid-run refusal. It belongs in its own entry rather
than smuggled in here.

Tests: `TestAssayToolchainFitness` ×18, driven through a docker shim that
actually EXECUTES the probe script on the host (a shim echoing canned output
would pin construction, not acceptance — the substitute-interpreter failure
this whole project exists to kill). Covered: missing `external_tools` → FAIL
naming lane/tool/environment; present → OK listing them; `language =
"javascript"` against a constructed Node-less PATH → FAIL naming both, then
node-only → FAIL naming only npm, then both → OK (the devcontainer has real
node/npm in `/usr/bin` beside `bash`, so the absent case had to be built);
lane absent from the inventory → FAIL naming what IS declared; judge without
`--json` → SKIP; non-JSON → SKIP; `inventory_schema = 2` → SKIP with the
value; `command -v` transport failure → SKIP, never a clean bill; `host`
environment → SKIP; docker absent → SKIP; no slice derivable → SKIP; exec
environment probes via `docker exec` and starts no container; `--check-env`
exit 2/0; a command-lane project emits no toolchain line at all; and the
one-probe-builder source assertion.

## RG-26 — no `--base REF` passthrough to `assay run --request-base`: B019 (assay ≥ 3.0.0) is unusable from the gate; derive the delegating lanes from `assay lanes --json`, not a new lane key

**Filed:** 2026-08-30, from the assay 3.1.0 design review (§4 D3) — the
current-gate backport of ciu **CIU-72 (c)**; absorbs the v8 proposal's
§4.11 **N12**, which was never filed here (N12 cited "RG-24", but RG-24 is
the exec-mode container-resolution bug; the proposal row now points here).
**SPEC ownership:** §2 CLI (beside `R-02`), `R-05`; §5 assay invocation
(`build_assay_inner`, `run-gate.py:1178-1179`); RG-1's conjunction
propagation rule.

### The gap

`build_assay_inner` always emits `assay run <lane> --file assay.toml
--verdict-json …`. assay 3.0.0 shipped `judge.base_source = "request"` +
`--request-base REF` (B019/A-328): a changed-line lane that leaves
`judge.base` out and takes the comparison base from the gate — the shape
every PR-scoped lane wants (the v8 demo's `p129_enumeration_cursor` shows
it). Such a lane invoked WITHOUT `--request-base` refuses
`ERROR`/`BAD_LANE_CONFIG` by design (assay never falls back to `HEAD`).
run-gate has no `--base` flag, so no consumer can adopt B019 until v8's
`ciu gate` — months of a shipped judge feature sitting unusable.

### Fix (additive)

- `run-gate <lane> [--base REF]`. For a `kind = "assay"` lane: query
  `<assay_command> lanes --json --file assay.toml` in the environment
  (RG-25's shared probe); if the named lane reports `base_source ==
  "request"`, append `--request-base <REF>` to the assay argv, where `REF`
  is `--base` if given, else the judged worktree's `git merge-base HEAD
  @{upstream}`; no upstream → exit 2 `run-gate: lane 'x' delegates its
  comparison base; pass --base REF (worktree has no upstream)`. A lane that
  does NOT delegate, invoked with `--base` → exit 2 naming the lane (assay
  would refuse anyway; refuse earlier and clearer). Conjunction lanes
  propagate `--base` to every sub-invocation (RG-1's rule: an override
  given to the gate reaches every sub-lane).
- **No new `run-gate.toml` key.** The fact lives in `assay.toml` and is
  DERIVED, so the current gate never restates it (v8's S16.5 `request_base`
  restatement is CIU-72 (c)'s concern there; v7 gets the one-spelling
  property for free).
- Judge without `--json` and no `--base` → behaviour unchanged; judge
  without `--json` and `--base` given → exit 2 naming the assay version that
  first carries the inventory (B044).
- `--dry-run` shows the resolved `REF` and the appended flag; `R-05`'s
  pre-execution disclosure prints it.

### Acceptance

- [ ] `--base` accepted on lane and conjunction invocations and propagated;
- [ ] tests: delegating lane + `--base` → assay argv carries
      `--request-base REF`; delegating lane without `--base`, with and
      without an upstream; non-delegating lane + `--base` → exit 2; judge
      without `--json` in both shapes;
- [ ] `--dry-run` and the disclosure show it; SPEC §2/§5 and the CONSUMERS
      worked example updated (a `base_source = "request"` lane beside the
      existing one);
- [ ] N12's row in `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §4.11 points
      here (done 2026-08-30).

**FIXED 2026-08-31 (rev 28), SPEC `R-35`.** `--base REF` is accepted on every
lane invocation; `plan_comparison_base()` decides what (if anything) the lane
gets, and every refusal is exit 2 naming the lane.

- **Delegation is DERIVED, not declared.** `assay_inventory_entry()` reuses
  RG-25's probe; a lane reporting `base_source == "request"` gets
  `--request-base <REF>` appended to its assay argv. No `run-gate.toml` key
  was added, so v7 gets the one-spelling property the v8 proposal's S16.5
  restatement has to work for.
- **Cost, disclosed rather than hidden:** because delegation must be known
  even when `--base` is absent (a delegating lane invoked bare needs the
  merge-base default), the inventory probe runs for EVERY `kind = "assay"`
  lane invocation. It is short, read-only (`assay lanes` executes nothing)
  and uses `R-34`'s single builder. SPEC `R-35` and CONSUMERS both state it.
- **Default ref** is the judged worktree's `git merge-base HEAD @{upstream}`,
  via `derive_upstream_base()` — deliberately not `git_out()`, because a
  missing upstream is an ordinary state to report, not infrastructure to
  abort on. No upstream → the entry's exact refusal. There is no fallback to
  `HEAD` or a default branch name.
- **Conjunction propagation** uses `R-25`'s mechanism rather than inventing a
  second one: a `{base}` token in the conjunction lane's own argv,
  substituted into every sub-invocation, resolved by the same policy (so a
  `{base}` lane on an upstream-less tree refuses instead of substituting an
  empty string). A command lane WITHOUT the token, given `--base`, refuses —
  the two rules would otherwise contradict each other for a conjunction,
  which is a command lane that does not itself delegate.
- **Older judge:** with `--base`, exit 2 naming assay `3.2.0` (B044) as the
  version carrying the inventory; without `--base`, behaviour is byte-for-byte
  unchanged.

Tests: `TestComparisonBasePassthrough` ×14 — delegating + `--base`;
delegating with a real upstream (asserting the actual merge-base SHA reaches
the argv); delegating without an upstream → the exact refusal; non-delegating
+ `--base` → exit 2 naming the declared `base_source`, with NO judged run
started; non-delegating without `--base` → unchanged; old judge both ways;
lane absent from the inventory + `--base`; conjunction propagation (asserted
at fd level, since the sub-shell's stdout is not a Python write);
conjunction without upstream; command lane without the token; a `host`-
environment assay lane probing locally with no docker at all; `--dry-run`
disclosing the ref while starting no judged container; and the substitution
leaving `{base}` alone when no base was resolved.

The test-suite helpers `lane_runs()`/`lane_execs()` were added because an
assay lane now issues a probe before the judged run — `docker_runs(log)[0]`
is no longer necessarily the lane, and four existing tests were silently
asserting against the probe instead (one of them, `test_exec_assay_lane_
judges_selected_worktree`, still PASSED against the probe because both
scripts `cd` to the same tree; it now asserts `--verdict-json` to pin the
judged exec specifically).


---

## RG-27 — persisted per-lane-per-commit invocation history (bounded window) + a query verb, machine- and human-readable

**Filed by:** operator directive, 2026-08-31, retriaging ciu CIU-55 (originally
filed `dstdns/nyxloom-trove/decisions.md` D-204, 2026-08-25). CIU-55 argued CIU
should own this because it holds the per-instance identity and an existing
persisted-state file (`ciu.global.toml`); the operator's re-read: in the
CURRENT (pre-v8, pre-gate-absorption) architecture run-gate is the layer that
actually invokes each lane and already has direct, unmediated visibility into
start/stop timestamps and exit status — CIU would have to wrap or intercept
run-gate's own invocation loop to get the same data first-hand. v8's §4.3.2
absorption of run-gate into `ciu gate` eventually collapses this distinction,
but that is not-yet-implemented; this entry fixes the gate consumers actually
run today. CIU-55 is retained as a pointer to this entry, not deleted (its
"why CIU owns it" reasoning is a real recorded design discussion, now
superseded).

### The gap

Lane duration and outcome are informal: an operator or controller notices a
lane "took a while" from wall-clock observation while waiting on it, and the
observation is lost once the terminal scrolls past it. Nothing durable
records, per lane: how long THIS run took, on THIS commit; how that compares
to recent runs of the same lane; which lanes are cheap-to-always-run vs.
expensive-enough-to-consider deferring. Without this, a provisional-merge /
defer-heavy-rigor policy is a guess dressed as a decision (dstdns explicitly
declined to adopt such a policy blind on 2026-08-25, D-204, for exactly this
reason).

### Proposed contract

- **History**: keyed by (lane, commit), bounded to the last **N commits per
  lane** (default 10, operator-configurable). A rolling window — the oldest
  entry is evicted once the bound is exceeded, never unbounded growth. Each
  history entry: duration, outcome (pass/fail), start timestamp, worktree/
  instance identity.
- **Latest**: a single always-current slot per lane holding the most recent
  invocation's result **regardless of outcome** — pass, fail, error, or
  aborted/interrupted — and regardless of whether that invocation ran
  against a clean, committed HEAD (a dirty-tree or mid-rebase run still
  updates "latest"). Always available to the caller via the query verb.
- **Latest does not feed history**: an unsuccessful or aborted run updates
  "latest" for immediate diagnostics but is NOT appended to the bounded
  per-commit history log — history is a curated trend series for "what does
  this lane typically cost", and an aborted run has no stable duration/
  commit pairing worth keeping in that series. (Flagged as a design call for
  the implementer to confirm: whether a *completed* fail — a real commit ran
  the full lane and it failed — still belongs in history alongside passes,
  since its duration is real data even though its outcome is not a pass.
  Current reading: yes, completed fails join history; only aborted/
  interrupted/dirty-tree runs are excluded.)
- **Query verb**: run-gate gains a subcommand to display/query this data in
  both human-readable (default, a table) and machine-readable (`--json`)
  form — at minimum: latest result for a lane, and its bounded history.
  Exact verb name/flags are an implementer design call, following this
  project's existing `doctor`/`--dry-run`/`--list` subcommand conventions.
- **Storage**: a run-gate-owned, gitignored, per-instance file (location is
  a design call — sibling to how ciu treats `ciu.global.toml` as its
  per-instance persisted-state surface, but run-gate has no existing
  equivalent to extend, so this is greenfield). Concurrent-write safety
  matters: two worktrees' gates can run simultaneously against lanes that
  key into the same file if the storage location is not itself per-instance
  scoped — needs an explicit design answer, not an assumption.
- Explicitly OUT of scope: run-gate does not decide any rigor/defer POLICY
  itself — it measures and persists; a controller (dstdns or otherwise)
  reads the data and decides.

### Oracles

Not yet written — needs a design pass first (storage location, concurrent-
instance write safety, exact verb/flag names, and the completed-fail-vs-
history question flagged above).

- Controlled wrong implementation to watch for: recording only the latest
  invocation with no rolling stat (the same trap CIU-55 flagged) — a single
  slow outlier run would look like the lane's permanent cost, defeating the
  point of informed decision-making.
- Controlled wrong implementation to watch for: an aborted/dirty-tree run
  silently corrupting the bounded history's commit-keyed entries (e.g.
  overwriting a real commit's history slot with a dirty-tree duration).

**SPEC ownership:** new surface — no existing `SPEC.md` section owns
invocation-timing telemetry. Cross-reference: ciu CIU-55 (superseded pointer,
`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`).

### FIXED 2026-08-31 (rev 30, package `run-gate-P03`)

Landed as `SPEC.md` **`R-36` (a-i)** with `R-01`/`R-06`/`R-08` amendments.
79 new tests; `./run-gate.py selftest` green (359 passed, 2 skipped,
diff-coverage **229/229 = 100%**, exit 0). Full record:
`nyxloom-trove/reports/run-gate-P03-{LOG,REPORT}.md`. Several things here
were left to implementer judgment — what was actually decided, and why:

**Verb: `history [LANE] [--json]`** (`R-36i`). A positional verb, not a flag,
because it reads the world and reports — the `doctor` shape; flags in this
project are discovery surfaces (`--list`, `--check-env`, `--dry-run`). No
LANE reports every declared lane; an unknown LANE refuses (exit 2) naming the
known lanes and the config path, exactly like the run path. `--json` mirrors
the existing machine/human split. `history` joins `doctor` and
`validate-pointers` as a RESERVED lane name (`R-08`). Rejected: `stats` (the
most-used answer is a single record, not a statistic) and `timings` (drops
the outcome half of the record).

**Storage: `<effective project dir>/.run-gate/history.json`, JSON**
(`R-36f`). "Per-instance" resolves to **per (judged worktree × project)**,
and it is DERIVED rather than invented: `R-21` already relocates the
effective project dir into the judged tree, so `--worktree B` writes B's
measurement into B's store, and lane-name collisions across the estate
(`selftest` exists in several projects) cannot merge. Anchoring at `repo`
instead — the checkout owning the shared `.git`, i.e. MAIN for every linked
worktree — was rejected as both the contention hazard AND the same false
attribution `R-21` exists to prevent. Format is JSON because run-gate is
stdlib-only and the stdlib has no TOML *writer* (`tomllib` is read-only);
hand-rolling TOML emission for a file rewritten after every run is a bug
farm. `/tmp/run-gate/` (the evidence-dir neighbourhood) was rejected:
evidence is a post-mortem for one failure, history is a series that must
accumulate over days, and `/tmp` is tmpfs on many hosts — the series would
silently reset. No env-var relocation override was added.

**Concurrency: scope first, arbitration second** (`R-36f`). Cross-worktree
contention is eliminated by construction — two worktrees address two files
and never meet; a layout with no collision beats a lock that arbitrates one.
The residual case is real (two lanes of ONE project in ONE tree) and is
serialized by an exclusive `flock` on a **sibling** `.run-gate/history.lock`
(`O_NOFOLLOW`, 0600) held across the whole read-modify-write, plus
write-temp-then-`os.replace`. Sibling because the store is REPLACED by
rename: a lock on the store guards an inode nobody writes next. Atomic
rename is also what lets the query verb read with **no lock at all**. The
wait is BOUNDED (5s), unlike `R-29`'s shared-infra lock which blocks forever
on purpose — that one protects the correctness of the run, this one protects
a measurement, and a gate that hangs to write telemetry has inverted the
priority.

**Gitignore obligation made executable** (`R-36g`). Writing into the judged
tree would otherwise leave it dirty for the NEXT lane's clean-tree check, so
run-gate asks git before every write and REFUSES to write an un-ignored
store, naming the remedy. Two details were verified against real git rather
than assumed, and both would have shipped as silent defects: the query names
the FILES, never the bare directory (`git check-ignore .run-gate` answers
"not ignored" while the directory does not exist yet, even under a
`.run-gate/` pattern — recording would have been dead on every project's
first run and alive on the second), and the verdict is read from the REPORTED
PATHS, never the exit status (`git check-ignore a b` exits 0 when ANY
argument matches — the false-certification shape AGENTS.md names, which would
certify a store whose LOCK file still dirties the tree). Root `.gitignore`
gained `.run-gate/`; CONSUMERS adoption step 5 names it for copied-script
repos.

**Open question 1 — does a COMPLETED fail belong in history? YES, with a
qualification** (`R-36c`). Agreeing with this entry's own reading, but the
plain "yes" is not safe: a failing lane can SHORT-CIRCUIT (this project's own
`pytest && coverage_gate` never reaches the gate when pytest is red), so
fails and passes are not samples of the same quantity and averaging them
understates the lane's cost in exactly the direction that makes a "cheap,
always run it" call wrong. Resolution: fails join history CARRYING their
outcome, and the reported statistic is SPLIT (`stats.passes` vs
`stats.completed`), both published in both output forms. run-gate hands over
both series and picks neither — picking is policy, which is out of scope.
This paid off immediately: the store's first two real entries are this
package's own gate runs, and the failing one took 56.4s against the passing
one's 47.7s (the coverage gate ran to a verdict rather than short-circuiting)
— a pass-only series loses that point, a merged series reports 52.0s as "what
selftest costs" when the answer for a passing run is 47.7s.

**Open question 2 — what else is excluded, and how "unknown" is handled**
(`R-36b`). Eligibility is a conjunction: completed with its own exit status,
clean tree, no git operation in flight (`rebase-merge`, `rebase-apply`,
`MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `BISECT_LOG`), resolvable
commit. Each failure records its reason on the entry, visible in both output
forms. "Could not determine" EXCLUDES — a wrong trend entry is invisible, a
missing one shows up in `count`. Dirtiness is sampled independently of the
lane's `clean_tree` POLICY (the test is whether the tree WAS dirty, never
whether dirt was permitted — otherwise every `clean_tree = false` lane's
series silently halves) and BEFORE the lane runs (a lane that leaves
artifacts must not retro-disqualify its own valid measurement).

**Two further calls the entry did not ask about but which the contract
needs.** Keyed by (lane, commit) means a re-run of the same commit REPLACES
its entry and moves to the tail (eviction = least recently measured);
appending would let ten re-runs of one commit evict nine other commits from a
ten-deep window. And the headline statistic is the **MEDIAN**, never the
mean, with min/max/count — the trap named below is one slow outlier reading
as the lane's permanent cost, and the mean is precisely the statistic that
permits it; `max` still publishes the outlier.

**Retention bound**: `[history] keep` (integer ≥ 1, default 10), a new
top-level config table; a project's shadows the central one entirely per
`R-09`. Declared config, not an env var: how much trend to keep is an
auditable decision, unlike the per-instance data it bounds.

**Recording discipline** (`R-36e`/`R-36h`). An invocation begins at the
clean-tree refusal and ends at the lane's own exit status; earlier failures
are configuration errors naming no invocation and record nothing, and
`--dry-run` records nothing. Refusals and aborts inside the window update
`latest` and re-raise/return unchanged. Every recorder failure — un-ignored
store, held lock past the bound, corrupt store, write error — degrades to ONE
warning line on stderr (never a traceback, `R-04`) and never changes the
lane's exit status.

**Both named controlled-wrong-implementations are caught**, verified by a
20-mutant probe campaign against the real source (all caught, zero survivors,
source restored byte-identical). Trap 1 is caught at both levels it can
occur: structurally (`TestHistoryRollingSeries::
test_series_survives_across_commits_not_just_the_last` — a latest-only store
keeps 1 entry where 3 commits ran) and statistically
(`::test_one_slow_outlier_does_not_become_the_typical_cost` — the reported
median is 10.0s where the mean would be 40.0s, i.e. (10+10+100)/3; `max`
still publishes the 100.0s outlier, which is the separate point that an
outlier stays visible rather than being discarded). Trap 2's literal form is
`TestHistoryEligibilityGuard::test_dirty_run_never_overwrites_the_commits_history_entry`
(clean 10.0s pass on commit C, then a DIRTY 999.0s run on the same C: C's
entry still reads 10.0s, `latest` moved to 999.0s with
`history_eligible: false` and a reason naming the dirt), with siblings for
the aborted, errored, mid-rebase and indeterminate routes to the same
corruption.

**Round-2 review fixes** (adversarial review returned ACCEPT-conditional; two
blockers, both fixed here, each with its own mutant):

- **B1 — `history` ignored `--worktree` and answered with the WRONG tree's
  data**, silently: `cmd_history` ran before `resolve_repo_and_worktree` and
  got the raw project dir, so the write side honored the flag (`R-36f`) and
  the read side did not. Fixed by HONORING it — the verb reads the selected
  tree's store and DISCLOSES which tree it describes (`tree:` line;
  `worktree_scope` in JSON). Resolution is opt-in so an unflagged query stays
  git-free. Writing the error-path test found a second hole in the same fix:
  a READ has no downstream to fail in, so an unresolvable override used to
  compute a store path under a nonexistent tree and answer "(not written
  yet)" — B1 through the error path. A non-directory now refuses (exit 2) and
  a non-work-tree refuses (exit 3, carrying git's own line).
- **B2 — Ctrl-C during the telemetry write became an uncaught `KeyError`.**
  The normal-path flush sits inside the tool's own exception scope and is not
  instantaneous (it spawns `git check-ignore` and can wait up to the 5s lock
  bound), so an interrupt landing there reached the abort handler, which
  flushed the same already-consumed record and raised — replacing the real
  signal with a traceback `R-36h`/`R-04` both forbid. Flushing is now
  at-most-once with the claim staked BEFORE the work, plus a None-safe start
  stamp; a record with no measured duration is refused by the eligibility
  conjunction (clause 1) rather than stored.
- **S1** — `--json` was accepted and ignored outside `history` (`--list
  --json` handed a TSV to a caller asking for JSON). It is now refused by
  name, the same rule as RG-1's `--worktree` and RG-26's `--base`.
- **S2** — the reserved-lane-name change is a LOAD-TIME breaking change for
  any consumer with a `[lanes.history]`; zero estate projects have one, but
  it is now flagged as a BREAKING CHANGE block in CHANGES.md and CONSUMERS.md
  the way RG-23's was.

**Out of scope, honored:** run-gate decides no rigor/defer POLICY. It
measures and persists; a controller reads and decides. Follow-ups a consumer
may want (age-based retention, an estate-wide roll-up, a `--worktree`-aware
query, and the v8-absorption reopening of the scoping question) are named in
the REPORT §7 and deliberately not filed as entries without a real consumer
asking.

## RG-28 — `run_host_lane` raised `KeyError('argv')` for `kind = "assay"` on the built-in `host` environment

**Filed and FIXED 2026-08-31 (rev 28)**, found while implementing RG-26.

`_validate_lane` accepts `kind = "assay"` with `environment = "host"` — no
rule forbids it, and the assay/host combination is a reasonable shape for a
project whose judge runs on the machine (run-gate-project's own selftest lane
is a host lane; an assay-judged sibling would look exactly like this).
`run_host_lane` nonetheless did `substitute_worktree(lane["argv"], …)`
unconditionally, so such a lane died with a `KeyError('argv')` traceback —
`R-04` names a traceback for a config/usage error a defect outright. It now
builds the same assay inner the two container runners build
(`build_assay_inner`), whose `GIT_CONFIG_GLOBAL` isolation (`R-19a`) keeps
the `safe.directory` write out of the operator's own git config. SPEC `R-19`
amended. Covered by `TestComparisonBasePassthrough::
test_host_assay_lane_probes_locally_without_docker`, which drives a host
assay lane end to end with docker never invoked.

## RG-29 — `cmru/run-gate.toml` pins a vanished assay sidecar, which turns run-gate-project's OWN gate red

**Filed:** 2026-08-31, from the run-gate-P02 bundle (RG-21/23/24/25/26). Not
fixed by that package: the defect is in `cmru/run-gate.toml`, outside that
project's scope, and its own scope statement forbade touching it.

**Fixed:** 2026-08-31, directly on `main` by the controller (same pattern as
the earlier `ciu/run-gate.toml` assay-pin fix). All four sites named below
moved from `2.2.0` to `2.3.0`; `sha256sum -c` verified against the vendored
`cmru/tools/assay/assay-2.3.0.pyz` before committing;
`./run-gate.py validate-pointers ../cmru/cmru.toml` confirmed `OK`.

### The bug

`run-gate-project`'s gate lane (`./run-gate.py selftest`) includes
`TestPointerLinkageEstate::test_cmru_release_step_names_a_real_lane`, which
runs `validate-pointers ../cmru/cmru.toml`. That fails:

```
run-gate: DEFECT steps.run-tests.commands[0].argv[argv]: loading
../cmru/run-gate.toml: lane '[lanes.assay]': pin 'assay' sidecar
tools/assay/assay-2.2.0.pyz.sha256 does not exist in this project (../cmru)
— vendor it or shadow the lane
run-gate: validate-pointers FAILED: 1 defect(s) across 0 invocation(s)
```

`cmru/tools/assay/` contains `assay-2.3.0.pyz` + `assay-2.3.0.pyz.sha256`;
`cmru/run-gate.toml` still declares the **2.2.0** sidecar. Confirmed
pre-existing on `main` at `858766d1` (identical failure from the primary
checkout and from a fresh worktree, and `git ls-tree` shows only the 2.3.0
pair tracked).

**Why it matters beyond cmru:** because the selftest lane's argv is
`pytest … && coverage_gate`, a red pytest SHORT-CIRCUITS the diff-coverage
floor — so while this is broken, run-gate's own gate cannot report a coverage
verdict at all, and any consumer reading `./run-gate.py selftest`'s exit
status sees red for a reason unrelated to run-gate.

### Fix (cmru-side)

**FOUR things move together, not one.** `cmru/run-gate.toml` names the
vanished artifact in four places, and bumping only the pin leaves the lane
broken in a different way — it would then verify a 2.3.0 sha256 and
immediately try to execute an `assay-2.2.0.pyz` that is not there:

1. `[lanes.assay.pins.assay] sha256` → `tools/assay/assay-2.3.0.pyz.sha256`;
2. `[lanes.assay.pins.assay] version` → `2.3.0` (it is VERIFIED in-lane
   against `<assay_command> --version`, RG-4/`R-08`, so a stale value fails
   the lane loudly rather than silently);
3. `[lanes.assay] assay_command` (`cmru/run-gate.toml:21`) → the literal
   filename `tools/assay/assay-2.3.0.pyz`;
4. `[lanes.mutation] argv` (`cmru/run-gate.toml:55`) → its
   `--assay-zipapp tools/assay/assay-2.2.0.pyz` carries a fourth copy of the
   same filename, inside a free-form shell string that `validate-pointers`
   deliberately never stats (`R-22`: argv strings are shell text, not
   declared paths), so nothing will tell you about this one.

Alternatively vendor the 2.2.0 pair back. Then re-run
`run-gate-project/run-gate.py selftest`.

### Acceptance

- [x] `./run-gate.py validate-pointers ../cmru/cmru.toml` exits 0;
- [x] `grep -n 'assay-2\.2\.0' cmru/run-gate.toml` returns nothing — pin AND
      `assay_command` both moved;
- [x] `run-gate-project`'s `selftest` lane is green end to end, including
      the diff-coverage step that currently never executes.

## RG-32 — `pins.assay.budget` in `run-gate.toml` is silently inert; the governing value lives only in the consuming lane's `assay.toml`

**Found 2026-09-02 in dstdns**, three independent times in one session
(two different Opus code-review agents plus the controller), on the SAME
`kind = "assay"` lane, each initially misreading which `budget` value
governed a long-running mutation-testing gate before checking with
`tomllib` instead of eyeballing `sed` output.

### The bug

For a `kind = "assay"` lane, `run-gate.toml` accepts a `budget` key
directly under `[lanes.<name>.pins.assay]` (adjacent to `version`, `sha256`,
`clean_tree`) — the shape looks like a normal, load-bearing lane setting,
and both consumer authors and reviewers plausibly assume it constrains or
documents the lane's run time. It does not: `run-gate` never reads
`pins.assay.budget` for anything. The actual governing value, if any, is
whatever the target `assay.toml`'s own `[lanes.<assay_lane>]` block declares
as `budget` — a SEPARATE file, SEPARATE key path, silently unconnected to
the one in `run-gate.toml`. Confirmed via `tomllib` on dstdns's own
`run-gate.toml`:

```
sql-mutation                    lane.budget=None    pins.assay.budget='90m'
assay-p129-enumeration-cursor   lane.budget=None    pins.assay.budget='10m'
```

Both `90m` and `10m` are dead text. `dstdns`'s `assay.toml` separately
declares `budget = "120m"` for the `cw2b_schema` lane `sql-mutation` points
at (raised from `90m` in an unrelated, later, per-project decision) — the
two numbers drifted apart with nothing to notice or prevent it.

**Why it matters beyond one misreading.** `kind = "command"` lanes (e.g.
`scale-admission`, `schema`) DO carry a genuine lane-level `budget` (no
`pins` sub-table), which some consumer-side tests key off of directly
(dstdns's `test_o11b` asserts `timeout_seconds == _budget_to_seconds(budget)`
against that real value). The two shapes look identical at a glance —
same key name, same-looking TOML nesting one level apart — so a reviewer or
implementer has no local signal that one is real and the other is
decorative. This is a `R-04`-class defect (a config value indistinguishable
from a real setting through normal reading, silently doing nothing) rather
than a cosmetic nit.

### Proposed fix

Either (a) `_validate_lane` rejects an unrecognized `budget` key under
`[lanes.*.pins.assay]` outright (a `kind = "assay"` lane's budget, if
run-gate is meant to enforce one at all, belongs at the lane level like
`kind = "command"` lanes, not inside `pins.assay`), or (b) if the intent was
always "the consuming assay.toml owns budget enforcement, run-gate's copy is
purely informational," rename the key (e.g. `budget_hint`) so it cannot be
mistaken for an enforced value, and add a `validate-pointers`-style check
that a declared `budget_hint` still matches the target `assay.toml` lane's
real `budget` at least at declaration time (catching exactly the kind of
silent 90m/120m drift found here).

### Acceptance

- [x] A `pins.assay.budget` key (however named going forward) either
      enforces something real or cannot be typo'd/misread as if it did;
- [~] `validate-pointers` (or an equivalent check) catches a declared value
      that has drifted from the target `assay.toml` lane's real `budget`;
      — **deliberately not built**, see the status note: option (b) was
      rejected, so there is no declared value left to drift.
- [ ] Existing dstdns lanes with a stale `pins.assay.budget` (`sql-mutation`
      `90m` vs real `120m`; likely others — not exhaustively swept from this
      report) get a follow-up cleanup pass once the mechanism is fixed here.
      — **dstdns-side**, and now forced rather than optional: the key
      refuses at load from rev 34, so dstdns must delete it before upgrading
      (controller notifies dstdns-23 at release).

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-08a`), BREAKING

Option **(a) refuse**, per ruling RW-7 of the "resumable, observable gate"
wave. Option (b) — rename to `budget_hint` + a `validate-pointers` drift
check — was rejected on the record: the cross-check would be a SECOND
reading of an assay-owned fact, which `R-35` already forbids for the
comparison base, and a decorative key is still a key every reviewer has to
learn to ignore.

`[lanes.<name>.pins.<pin>]` now validates its keys at all: `sha256` and
`version`, nothing else. `budget` gets its own message rather than the
generic unknown-key one, because the person reading it needs to be told
where the value they meant actually lives:

```
<file> [lanes.sql-mutation].pins.assay: pin 'assay' declares 'budget' —
run-gate never enforced it; the lane's budget lives in the consumer's
assay.toml [lanes.cw2b_schema] (delete this key; the lane-level run-gate
'budget' stays advisory)
```

The generic unknown-key refusal is the durable half of the fix: a pin table
that accepted anything is HOW `budget` came to live there. Four tests in
`TestPinKeysAreValidated`, including one that pins the surviving
lookalike — a real lane-level `budget` still loads and stays advisory.

No vbpub-estate `run-gate.toml` declares the key (swept 2026-09-02, all
eleven configs parsed).

**Amended 2026-09-02 after adversarial review round 1 (B1, ruling RW-13).**
The first consumer-impact sweep was a text `grep`, which cannot tell a
lane-level `budget` from one inside a pin table — the exact nesting confusion
this item is about. PARSED with the rev-34 loader over every dstdns lane:

- **18 of 35 lanes as measured on 2026-09-03** refuse at load, not 2:
  `assay-dlq`, `assay`, `sql-mutation`, `assay-p129-enumeration-cursor`,
  `worker-execution-admission`,
  `worker-execution-admission-r2-{compare,boolop,flips,falsy}`,
  `assay-p169-op-override-projection`,
  `assay-p169-op-override-projection-r2-{compare,boolop,falsy}`,
  `assay-p166-result-dedup`,
  `assay-p166-result-dedup-r2-{compare,boolop,flips,falsy}`. It was 13 of 29
  on 2026-09-02 (dstdns merged the `assay-p166-result-dedup` family in
  between). **Re-measured, not trusted** — the command lives in CHANGES
  `[Unreleased]` beside this entry (RW-30): a hard-coded count of a peer
  repo's config cannot stay true between a review and a merge.
- The migration is **two rounds**: the `budget` refusal fires first and masks
  every other misplaced key in the same table. Four of those lanes
  (`assay-dlq`, `assay`, `sql-mutation`, `assay-p129-enumeration-cursor`)
  then refuse again on `clean_tree = false`, which sits in the same
  misplaced position.
- **A misplaced key that is itself a legal LANE key is now named as one**, so
  the fix does not merely rename its own defect class: `'clean_tree' is a
  lane key; it belongs one level up in [lanes.<n>], where it is load-bearing
  — move it, do not delete it (under a pin table it has never done anything,
  so the lane has been running with the default instead)`. `budget` is
  excluded from that clause and keeps its own message: it is the one
  misplaced key whose remedy really is deletion. Three further tests in
  `TestPinKeysAreValidated` cover the new clause, the plural form and
  `budget`'s precedence over it.
- **Found in passing, and it is dstdns's to file:** those four lanes'
  `clean_tree = false` has been inert since it was written — they have been
  running with `clean_tree = true`.

## RG-33 — `sql-mutation`-style assay mutation lanes never pass `--resume`, so a budget-exceeded retry re-tests everything from scratch

### What's wrong

`assay run` supports `--resume` (persists completed mutation candidates
under `.assay/mutation-state/`, keyed by a deterministic id derived from the
mutated file's path, exact source bytes, mutated byte span, replacement
bytes, and operator — CONSUMERS.md §"Resume and shard a long mutation
lane"). A real source change produces a different id, so `--resume` never
masks a genuine change; it silently re-executes whatever no longer matches.

`run-gate` never passes `--resume` (or `--progress`) when invoking an
assay-kind mutation lane. Confirmed live on dstdns's `sql-mutation` lane
(2026-09-02, retry r3): the actual docker exec argv was `python3
tools/assay/assay-4.0.0.pyz run cw2b_schema --file assay.toml
--verdict-json .assay/verdict-cw2b_schema.json` — no `--resume` anywhere.
That attempt ran its full 120-minute budget, hit `budget_exceeded` on
172 mutants of the FIRST of 4 target files, and ended
`ERROR`/`EXEC_FAILED`. Checking the worktree's `.assay/` directory:
`mutation-state/` does not exist at all, confirming `--resume` has never
been used on this lane — every one of that lane's retries so far has
started file 1 over from mutant #1.

### Why it matters

A mutation lane whose budget is already tight (raised 90m→120m once
already for exactly this reason, per the target `assay.toml`'s own history)
throws away a full budget window's worth of real progress on every retry
that doesn't finish in time — the exact scenario `--resume` exists to
avoid. On a host under any contention (the discovery context here: a
shared dev/test host also running a production workload, CPU-starved per
`/proc/pressure/cpu`), a lane that structurally cannot finish in one
budget window can never finish at all under the current wiring, no matter
how many times it's retried.

### Proposed fix

Have `run-gate` pass `--resume` unconditionally for every `kind = "assay"`
lane invocation whose target declares an `R2` (mutation) rigor — it is
safe by construction (resume never trusts a record whose source no longer
matches) and there is no real scenario where re-testing already-verified
mutants from scratch is the desired behavior. Optionally also wire
`--progress <path-outside-the-worktree>` for observability, per
CONSUMERS.md's own guidance to keep it off the tracked/judged tree.

### Acceptance

- [ ] A `kind = "assay"` lane with `R2` in its target's `declared_rigor`
      gets `--resume` in its constructed argv, verified via the actual
      docker exec command line, not just the TOML declaration;
- [ ] A controlled two-attempt test (first attempt killed/budget-capped
      mid-sweep, second attempt re-run against the same commit) shows the
      second attempt's candidate count for already-completed mutants come
      back from `.assay/mutation-state/` rather than re-executing;
- [ ] A genuine source change between attempts (one target file edited)
      still re-executes every candidate touching that file — resume must
      never mask a real regression.

### Source

`dstdns` P165 (`nyxloom-trove/decisions.md` D-318/D-319), discovered while
investigating why its `sql-mutation` retry needed a full budget window
without finishing even one of its four target files.

### Status — FIXED 2026-09-02 (rev 33, SPEC `R-38`)

Operator directive the same day (vbpub session): "all assay lanes make use of
`resume` and `progress`". Landed as `build_assay_inner` appending `--resume
--progress .assay/progress-<assay_lane>.jsonl` to EVERY assay-kind
invocation — unconditionally rather than rigor-gated, because assay ignores
both on a lane without R2 (its `--progress` help says so, and resume state
is only touched by the mutation sweep), so the proposal's "whose target
declares R2" condition would have been a second reading of an assay-owned
fact for no behavioural gain. Acceptance: (1) DONE — five tests in
`TestResumeAndProgressAlways`, including the executed argv on the host
runner (RG-28's echo judge) and the dry-run docker argv line; (2) and (3)
are assay's contract, not run-gate's, and are proven in assay's own suite
(`tests/test_mutation_resume_sharding.py`,
`tests/test_mutation_progress_budget_plan.py`; the candidate id folds in the
file's exact bytes, so an edited file re-executes every candidate touching
it) — run-gate's suite has no R2 lane and a two-attempt R2 run here would be
a copy of that suite. Judge floor: `--resume` is assay 2.4.0, `--progress`
2.4.1; a pin declaring an older `version` now refuses by name at argv
construction (cmru was the only estate consumer below it, at 2.3.0 —
re-pinned in the same package). Not fixed on the assay side: assay's OWN
gate (`assay/tools/tester-unified-gate.sh`) invokes `assay run` directly,
not through run-gate — routed to the running assay wave to mirror.

## RG-34 — `schema` lane's argv doesn't template `{worktree}` into its own script path, breaking any Mode-B worktree with a dedicated (non-shared) test-runner container

### What's wrong

`run-gate.toml`'s `[lanes.schema]`:

```toml
argv = ["scripts/schema-gate.sh", "{worktree}"]
```

Only the ARGUMENT is templated with `{worktree}`; the script path itself
(`scripts/schema-gate.sh`) is a bare relative string, resolved against
whatever `--workdir` the docker exec uses. The sibling `p128-schema-lineage`
lane gets this right: `argv = ["bash", "{worktree}/scripts/p128-assay-schema.sh"]`.

For MAIN's shared `test-runner` container this is invisible: it bind-mounts
the WHOLE host repo root at `/workspaces/dstdns`
(`docker inspect`: `/home/vb/.../dstdns -> /workspaces/dstdns`), so
`--workdir /workspaces/dstdns` + bare `scripts/schema-gate.sh` happens to
resolve correctly regardless of which worktree's tests are actually being
judged. A Mode-B instance's own DEDICATED test-runner container (one per
worktree, not the shared one) mounts only that worktree's subtree, remapped
to the SAME container path: `docker inspect` on
`p152-...-test-runner` shows `/home/vb/.../dstdns/.worktrees/p152-... ->
/workspaces/dstdns/.worktrees/p152-...` — nothing is mounted at bare
`/workspaces/dstdns` in that container at all. `scripts/schema-gate.sh`
relative to `--workdir /workspaces/dstdns` then resolves to a path that
genuinely doesn't exist in that container's filesystem, even though the
identical file exists both on the host and inside the container under its
real mount point. Confirmed live 2026-09-02: `run-gate gate --worktree
.../p152-real-fault-harness-restart-matrix` failed immediately —
`bash: line 1: scripts/schema-gate.sh: No such file or directory`,
`lane 'schema' exit 127`, `lane 'gate' exit 127` — while
`docker exec p152-...-test-runner sh -c 'ls scripts/schema-gate.sh'`
(relative to the container's own default cwd, which IS its worktree root)
finds the same file without issue.

### Why now, not always

This traces to TODAY's retirement of `./scripts/testing-exec.sh`
(dstdns `CLAUDE.md`, 2026-09-02): the old Mode-B gate path ran
`testing-exec.sh` FROM INSIDE the worktree with the worktree's own `ciu.env`
sourced (GUIDE.md §3.4, "VALIDATED 2026-07-16"), so a bare relative script
path always resolved correctly by construction — cwd itself was already the
worktree root. The direct `run-gate <lane> --worktree` replacement path
never carried that same-cwd guarantee forward for lanes whose argv wasn't
already `{worktree}`-prefixed on the script path.

### Impact

Total, silent-until-hit: the `schema` lane (and therefore the composite
`gate` lane, which runs it) cannot pass against ANY Mode-B worktree with its
own dedicated test-runner container, ever — not flaky, not budget-related,
100% reproducible. Confirmed on `dstdns` P152.

### Proposed fix

Template `{worktree}` into the script-path element of `[lanes.schema]`'s
argv too, matching `p128-schema-lineage`'s already-correct pattern:
`argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]`. More
generally: audit every `kind = "command"` lane's argv for the same
class of defect (a relative script path NOT prefixed with `{worktree}`,
relying on `--workdir` alone) — `schema` was found only because P152
happened to be the first Mode-B package to exercise the composite `gate`
lane's schema sub-lane against a dedicated container since
`testing-exec.sh`'s retirement today.

### Acceptance

- [x] `[lanes.schema]`'s argv resolves correctly against a worktree whose
      test-runner container mounts only that worktree's own subtree (not
      the full repo root) — verified via a real Mode-B dedicated-container
      run, not just main's shared-container case;
      — **dstdns-side**: the argv lives in dstdns's `run-gate.toml`, and
      run-gate deliberately does not rewrite a consumer's declared command.
      **Done at `dstdns@65582354`** (the P152 merge): that lane now reads
      `argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]` with an
      RG-34 comment above it.
      (The one dstdns lane that still trips the new WARN,
      `[lanes.scale-admission]`, is NOT a box here — see the close note
      below, RW-26.)
- [x] Every other `kind = "command"` lane's argv is swept for the same
      unprefixed-script-path pattern — for the vbpub estate, and by
      `doctor` from now on for every consumer;
- [x] A regression test (or `doctor`/`validate-pointers`-style static check)
      catches a lane argv whose first element lacks `{worktree}` when its
      environment is `test-runner` and it takes a `--worktree` argument.

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-30b`) — run-gate's half

Ruling RW-8: **`doctor` warns; run-gate does not rewrite argv.** The argv fix
itself is the consumer's (dstdns P152), and it is one edit; a tool that
silently rewrote a declared command would be a worse defect than the one it
patched. A refusal was rejected too: the same argv is CORRECT under a
full-repo mount, and which mount a lane gets is not visible to run-gate
statically — a check that broke working consumers to prevent a hazard that
may not apply to them would be switched off, and then it protects nothing
(`R-30a`'s own reasoning).

`doctor` now emits ONE `[WARN]` per `kind = "command"` lane on a NON-host
environment whose `argv[0]` is a relative path containing `/` and not
starting with `{worktree}`:

```
run-gate: doctor: [WARN] lane 'scale-admission' argv[0] (RG-34): 'scripts/schema-gate.sh'
is a RELATIVE path, resolved against the container's --workdir instead of the
judged tree — declare it '{worktree}/scripts/schema-gate.sh'. A container that
mounts ONLY the judged worktree (a Mode-B instance's own runner, not the
shared one) has nothing at the bare repo root --workdir names, so this argv
dies there with 'No such file or directory' while working under a full-repo
mount. A warning, not a refusal: which mount the lane gets is not visible to
run-gate statically
```

It reads the DECLARATION only, so it still answers for a lane whose
environment failed to resolve, and with at least one container command lane
and nothing to flag it records one `[OK]` so a reader can tell it ran.
Doctor's exit code is unchanged by it. Six tests in
`TestDoctorNamesUnprefixedScriptPaths`, including the three shapes that must
NOT warn (`{worktree}`-anchored, absolute, bare command name) and the two
lane kinds outside the check (host lanes, whose cwd is the effective project
dir; assay lanes, which have no argv of their own).

**Estate sweep, 2026-09-02** (`tomllib` over every `*/run-gate.toml` in
vbpub, not `grep`): no vbpub lane trips the check.

**Which dstdns lane still trips it (corrected, review round 1 S7).** The
lane this item was FILED from — `[lanes.schema]` — was fixed in the P152
merge itself (`dstdns@65582354`) and now reads
`argv = ["{worktree}/scripts/schema-gate.sh", "{worktree}"]`. Parsing every
dstdns lane with `tomllib` (2026-09-02, re-run 2026-09-03) gives exactly one
hit, and it is a different lane:

```
RG34-FLAG scale-admission scripts/schema-gate.sh | env test-runner
```

(`/workspaces/dstdns/run-gate.toml:81`, `argv = ["scripts/schema-gate.sh",
"{worktree}", "tests/schema/test_scale_admission.py"]`.) The notification to
dstdns names that lane. RG-34 therefore lands with a LIVE consumer hit
rather than an already-fixed one — the transcript above is written for
`scale-admission` accordingly.

**CLOSED as FIXED, 2026-09-03 (RW-26).** run-gate's half is the `doctor`
WARN and it is shipped; that is the whole of what this item can deliver. The
`scale-admission` hit is a fact for the **dstdns notification** and a dstdns
filing, not an acceptance box in run-gate's backlog: the argv lives in
dstdns's `run-gate.toml`, run-gate deliberately never rewrites a consumer's
declared command, and a box run-gate can never tick makes a closed item read
as open forever. Re-measured 2026-09-03 with `tomllib` over every dstdns
lane: still exactly ONE hit, still `scale-admission`.

### Source

`dstdns` P152 (`nyxloom-trove/decisions.md` D-319), discovered live while
finishing P152's own composite gate run under the operator's single-stack
host-contention directive.

## RG-35 — a lane's container outlives a dead run-gate client, but nothing re-attaches; a restart starts a duplicate

**Filed 2026-09-02 (vbpub controller session), from the operator's ask to make
dstdns's "progress artifact + re-attachable runs + unbounded budget" pattern
the default for assay/run-gate too. This is the re-attach leg.**

### What's wrong

`run_container_lane` does `docker run -d --name <lane-pid-ts>` →
`docker logs -f` → `docker wait` → `docker rm -f` in a `finally`
(`run-gate.py:2500-2554`). When the CLIENT dies — SIGKILL, a devcontainer
restart, a harness that reaps a background command (measured the same day:
the Claude harness killed a detached-by-mistake `cmru release` after 33 s
and its inner gate container ran to completion unobserved) — the container
keeps running and the judge still writes `.assay/verdict-<lane>.json` into
the bind-mounted worktree, but nobody collects the exit status, no evidence
is captured (RG-12), no history record is written (RG-27), and the next
invocation of the same lane on the same worktree starts a SECOND container
— the one-gate-at-a-time rule broken by the tool itself, on a host that
shares 8 cores with a production game server (load 85 that afternoon).

### Proposed fix

- On a successful `docker run -d`, write `.run-gate/inflight/<lane>.json`
  (git-ignored, R-36's store discipline): container name and id,
  `started_at`, judged commit, worktree, verdict path, progress path.
- On invocation, if an inflight record names a container that still
  EXISTS: re-attach — `docker logs -f --since <recorded>` + `docker wait` —
  instead of starting one, disclosed as
  `run-gate: re-attached to <name> (started <t>, <elapsed> ago)`; if it has
  already exited: collect exit code, logs (evidence on failure) and the
  verdict, finish the run exactly as an attached one would, then remove the
  container. `--fresh` forces a new run (removes the old container first,
  disclosed by name). The record is cleared in the same `finally` that
  removes the container.
- RG-27 history records a re-attached run once, with the real duration from
  `started_at`.

### Acceptance

- [x] kill -9 the client mid-lane (fake docker AND one live probe — the
      live one is recorded in the wave REPORT): the container finishes; a
      second invocation prints the re-attach line, yields the container's
      real exit code, records history once, and starts NO second container;
- [x] an inflight record whose container is gone (host reboot) is reported
      and cleared, never silently ignored;
- [x] `--dry-run` discloses an existing inflight record.

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-39`)

Landed under the "resumable, observable gate" wave (RW-1..RW-3 of
`/workspaces/vbpub/run-gate-project/nyxloom-trove/WAVE-PROMPT-2026-09-02-resumable-gate.md`
— the MAIN checkout's, not this branch's, whose `nyxloom-trove/` holds
`reports/` only (review round 1 N5); decision D4 of the
post-v10 plan — automatic re-attach with `--fresh` as the escape, not an
`--attach` flag).

**Red proof (the controlled wrong implementation was the shipped rev 33).**
`TestReattachAcrossADeadClient` drives a real client to its death against a
STATEFUL fake docker (`fake_docker_stateful`: `run -d` creates a container,
`inspect` answers from it or exits 1 like `No such object`, `wait` returns
the recorded code, `rm -f` destroys it, a `.hang` marker makes `logs -f`
block). Against rev 33 (measured on a detached worktree at `f6d3a858`):

```
AssertionError: a SECOND container was started for a lane that already had
one running: [… '--name', 'run-gate-repo-suite-1779582-1788385365' …],
             [… '--name', 'run-gate-repo-suite-1779695-1788385369' …]
assert 2 == 1
```

Same test post-fix: one `docker run`, `run-gate: re-attached to
run-gate-repo-suite-…`, exit 0, container removed, record cleared.

**What landed.** `.run-gate/inflight/<lane>.json` written on a successful
`docker run -d` (R-39a); `resolve_inflight()` taking the five-way decision
before anything is built (R-39b: re-attach / collect / report-lost-and-run /
refuse on a commit mismatch / `--fresh`); `await_container()` as the single
finish shared by all three arrival paths (R-39c); `--fresh` refused by name
on host lanes, exec lanes and every verb (R-39d). History records such a run
once, with the duration from the container's own start (RW-3).

**Live acceptance probe** (a fake-docker argv proves construction, not
acceptance — AGENTS.md): one real `tester-unified:local` run, client killed
mid-lane, second invocation re-attaches. Run under the host's
one-container-at-a-time rule; transcript in
`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-REPORT.md`.

## RG-36 — liveness judged from the progress file, not from a guessed wall budget: `stall_timeout` and ETA disclosure for assay lanes

**Filed 2026-09-02, same ask as RG-35; this is the "unbounded budget by
convention" leg.**

### What's wrong

`budget` is advisory in run-gate (`run-gate.py:2519`, printed, never
enforced) and a hard lane-wide bound in assay (`LANE_TIMEOUT`). The only way
to bound a long mutation lane today is to guess a TOTAL number: dstdns raised
`sql-mutation` from 90m to 120m once and it still could not finish a
budget window (RG-33's transcript). Since rev 33 every assay lane writes
`.assay/progress-<lane>.jsonl` — per-candidate events carrying
`candidate_index` / `candidate_total` — so health can be judged from
progress instead: rate, ETA, and stall.

### Proposed fix

- While the container runs, tail the progress file and print
  `run-gate: progress <lane>: candidate 37/172, 1.9/min, ETA 71m` at a fixed
  interval (disclosure only, R-05). No progress file (an R0/R1 lane, or a
  judge that writes none) is disclosed once and never treated as a fault.
- Optional lane key `stall_timeout = "15m"`: the lane is stopped (`docker rm
  -f`, evidence saved, exit 3 naming the stall and the last event seen) only
  when the container is still running AND no event has been appended for
  that long — never on total elapsed time. `budget` stays advisory; the
  documented shape for a mutation lane becomes a generous assay `budget` +
  `judge.mutation.budget_per_candidate` + run-gate `stall_timeout`.
- Dependency: assay's events carry no timestamp today (`_progress_event`,
  `mutation.py:755`), so ETA uses run-gate's own clock from the first event
  it observed and stall uses the file's mtime — coarse but measured; assay
  B065 (per-event `emitted_at` / `elapsed_s`) makes both exact.

### Acceptance

- [x] a progress file advancing under a fake judge produces the ETA line
      with the right arithmetic; a frozen file + running container trips
      the stall at the configured time with evidence saved and exit 3;
- [x] no `stall_timeout` declared → behaviour unchanged;
- [x] an R0/R1 lane (no events) never stalls and says why, once.

### Status — FIXED 2026-09-02 (rev 34, SPEC `R-40`) — the COARSE half

Rulings RW-4/RW-5/RW-6. The "exact timing" half waits on assay **B065**
(per-event `emitted_at`/`elapsed_s`) and is E-3 of the post-v10 plan; the
code here already PREFERS an event's `elapsed_s` where one exists, so B065
makes the same implementation exact without a rewrite.

**Disclosure (`R-40a`/`R-40b`).** While an assay lane's container runs, the
file R-38 already asks for is read every `PROGRESS_POLL_SECONDS = 30` (a
module constant with its reason: 30 s judges a 15-minute `stall_timeout` to
within 3% and costs a 4-hour lane 480 `stat()`s) and, when something changed,
one line is printed:

```
run-gate: progress sql-mutation: candidate 29/172, 1.9/min, ETA 75m
```

The FIRST observation prints the count alone — one event and no clock in the
file is a baseline, not a measurement, and inventing a rate there would be
the guess this replaces. No file, a header-only file (R0/R1), or a torn last
line yields `progress <lane>: no candidate events (not an R2 lane, or the
judge writes none)` exactly ONCE, and is healthy.

**`stall_timeout` (`R-40c`).** Optional lane key, the `budget` grammar,
assay lanes only. The lane is stopped only when the container is STILL
RUNNING and the file has not advanced for that long — and "still running" is
structural, not asserted: the check runs only inside the poll of a `docker
logs -f` that has not returned. Stop = `docker rm -f`, evidence saved, exit
3:

```
run-gate: lane 'mutation' STALLED: the container is still RUNNING but
progress-cw2b_schema.jsonl has not advanced for 900s (stall_timeout 900s);
last event seen: candidate 37/172. The container was removed; container logs
preserved at /tmp/run-gate/run-gate-....log
```

Never on total elapsed time — `budget` stays advisory, its print unchanged,
and the two are disclosed side by side saying which is which. Declared on a
`kind = "command"` lane the key is REFUSED at load: it could never do
anything there, which is exactly RG-32's defect one key over.

**Tests** (22): `TestProgressWatch` drives the arithmetic and the silence
rule on a substituted clock (rate from run-gate's clock, rate from a
B065-shaped `elapsed_s`, no-change prints nothing, no-events disclosed once,
header-only, torn line, stall at exactly the configured age, movement resets
the stall clock, no `stall_timeout` = disclosure only, absent total = no
ETA); `TestStallTimeoutLaneKey` the validation and the two disclosures;
`TestStallEndToEnd` the real container loop through `main()` — a frozen file
under a blocking `docker logs -f` exits 3 with the container removed and the
inflight record cleared, and a file a thread keeps advancing never stalls.

## RG-38 — resume state does not survive an ephemeral worktree (cmru release worktrees, Mode-B worktrees)

**Filed 2026-09-02, same ask; this is the durability half of the resume leg.**

### What's wrong

assay writes `.assay/mutation-state/<candidate-id>.json` under the JUDGED
project root (`mutation.py:797-806`). With a persistent worktree (dstdns's
main checkout) retries now resume (rev 33). cmru's release transaction and
dstdns's Mode-B instances create a FRESH worktree per run, so a retried
release or Mode-B mutation lane starts from mutant #1 despite `--resume`.
Candidate ids fold in the file's exact bytes, span, replacement and
operator, so state is safe to share across worktrees and commits by
construction: a record either matches the candidate exactly or is ignored.

### Proposed fix

Bind-mount a per-repo durable directory (`<repo>/.run-gate/assay-state/
<project>/`, git-ignored) at the container path assay writes to. That needs
assay to accept a state location (`--state-dir`, assay **B066**) because the
path is derived from `project_root` today; until B066 ships, a disclosed
copy-in / copy-out of `.assay/mutation-state/` around the lane is the
fallback. Once B007 (multi-target canary) and F015 (R4) land, the same
directory holds their per-target / per-attempt records.

### Acceptance

- [ ] two invocations on two DIFFERENT worktrees of the same commit: the
      second's progress file shows `event: resume` with `resumed_total > 0`;
- [ ] a source edit between them re-executes every candidate touching the
      edited file (resume must never mask a change — RG-33's third item,
      inherited).

### Source (RG-35, RG-36, RG-38)

Operator, 2026-09-02, after dstdns's own drive()/drive_corpus() fix (progress
JSONL a caller polls; run ids persisted and re-attached; an unbounded budget
once health comes from the progress file): "can we make this a default
pattern/best practice to be used with assay/run-gate as well?" The three
legs map onto RG-35 (re-attach), RG-36 (progress-judged liveness) and
RG-38 + assay B065/B066/B067 (durable resume state, timestamped events,
per-unit bounds). R0/R1 lanes are one command each and cannot resume below
that grain by construction; canary (R3) and red-first (R4) have mutation's
per-unit shape and get the same mechanism through assay B064/B066.

## RG-40 — `coverage_gate.py` reports misleading uncovered lines on a dirty tree

**Found 2026-09-02** while implementing the rev-34 wave, twice, each time
costing a full gate round (~70 s of suite plus the investigation).

### What's wrong

`tools/coverage_gate.py` derives the set of changed lines from
`git diff --relative --unified=0 <base> HEAD -- <source>` — **committed**
state — while `coverage.json` describes the file **on disk**. With a clean
tree those agree. With `--allow-dirty` over an uncommitted change they do
not: every line below the working tree's insertions is offset, so the gate
reports lines as uncovered that the suite covered, and (worse, silently)
would report lines as covered that are not.

Measured, same source in both runs, one commit apart:

```
dirty:      diff-coverage FAIL: 175/177 changed executable lines covered (98.9%)
            Uncovered changed lines: run-gate.py: [3070, 3365]
committed:  diff-coverage OK:   153/153 changed executable lines covered (100.0%)
```

`3070` and `3365` are HEAD-side numbers pointing at lines that, in the file
coverage actually measured, are 22 lines further down.

### Why it matters

`--allow-dirty` is the DOCUMENTED way to gate work in progress (every wave
prompt in this estate uses it), and the number it prints in that mode is not
just imprecise — it names specific line numbers to go and test, which is an
instruction to do the wrong thing. The two rounds this cost were both spent
writing tests for lines that were already covered.

### Proposed fix

Either (a) when the judged tree is dirty, diff the WORKING TREE (`git diff
<base>` with no `HEAD`, plus untracked-file handling) so both halves
describe the same bytes; or (b) detect the mismatch (`git status
--porcelain` on the source path) and DISCLOSE it — "diff-coverage measured
against committed state while the tree is dirty; line numbers may not
correspond" — or refuse outright, which is this estate's usual answer to a
number that cannot be trusted (R-04). (a) is the useful one; (b) is the
minimum.

### Acceptance

- [ ] The `selftest` lane run with `--allow-dirty` over an uncommitted change
      reports the SAME uncovered set as the same code reports once committed;
- [ ] Or, if the answer is (b), the run says plainly that its line numbers
      describe the committed tree and the coverage describes the working one.

### Source

run-gate rev 34's own implementation wave
(`nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-LOG.md`, entries E5 and E7).

## RG-41 — a container `kind = "command"` lane has no liveness signal: judge silence from the LOG STREAM

**Filed 2026-09-02 by controller ruling RW-9**, out of the rev-34 wave's own
decision ask: `stall_timeout` was refused on command lanes, and the refusal
STANDS — but the gap it leaves is real and is this item.

### What's wrong

`R-40c` bounds a lane by SILENCE in `.assay/progress-<assay_lane>.jsonl`.
Only an assay lane writes that file, so `stall_timeout` on a
`kind = "command"` lane could never do anything and is refused at load —
correctly, because an inert key that reads like a real one is exactly
`R-08a`'s defect (RG-32), and run-gate does not guess a second signal it was
never given.

The consequence is that the lane shape MOST likely to hang has no bound at
all. A container command lane is an arbitrary consumer command (a pytest
suite, a schema gate, a conjunction of sub-lanes) running detached on a host
shared with a production workload; the only thing declared about its
duration is `budget`, which run-gate prints and never enforces (`R-15`
disclosure, not a bound). A wedged suite therefore holds a gate container
until a human notices — the failure mode RG-36 was filed to end, still open
for the majority of the estate's lanes.

### Measured evidence

run-gate's own refusal message, rev 34 (`_validate_lane`):

```
<file> [lanes.<n>]: 'stall_timeout' is judged from
.assay/progress-<assay_lane>.jsonl, which only a kind = "assay" lane writes —
a command lane has no progress file and could never stall by this rule; use
the command's own timeout instead (R-40)
```

Scope of the gap, counted with `tomllib` over every `*/run-gate.toml` in
vbpub (2026-09-02): **5 container `kind = "command"` lanes vs 3 container
assay lanes** (plus 9 host lanes, which run in-process and are outside this
question entirely). So `stall_timeout` is available to the minority of the
containerised lanes, and unavailable to the majority — including every
gate-conjunction lane, which is a `kind = "command"` lane by construction
(CONSUMERS "Gate-conjunction lanes") and is the shape nyxloom's daemon
invokes.

### Proposed fix (RW-9)

run-gate ALREADY tails the container's output: `await_container` streams
`docker logs -f` for the whole run. The ARRIVAL TIME of the last line, on
run-gate's own clock, carries exactly the semantics `R-40` gives the
progress file's mtime — a lane that has printed nothing for 15 minutes is
silent in the same sense, and "silence, never total elapsed" is the same
rule. So:

- make `stall_timeout` legal on a container `kind = "command"` lane, judged
  from log-stream silence;
- DISCLOSE the source at start, since the two signals differ in what they
  can miss: `run-gate: stall_timeout 15m (source: progress file)` vs
  `(source: log stream)`. A lane that legitimately prints nothing for long
  stretches (a single silent compile) is the log stream's known blind spot
  and the reason the source must be named, not inferred;
- keep the assay-lane behaviour exactly as `R-40` defines it — the progress
  file stays the better signal where it exists, and an assay lane must not
  silently downgrade to the weaker one;
- the stop, the evidence, the exit code and the message shape are `R-40c`'s
  already: `docker rm -f`, evidence saved, exit 3, naming the stall, the
  last thing seen and the age.

The implementation seam is one line: the poll loop in `await_container`
already runs every `PROGRESS_POLL_SECONDS` and already owns the process
whose output would be timestamped. Reading the stream's arrival times means
`docker logs -f` can no longer inherit run-gate's stdout untouched — that is
the real cost of this item and the reason it is not a footnote to RG-36.

### Acceptance

- [ ] A container command lane declaring `stall_timeout` loads, and its run
      discloses `source: log stream` at start;
- [ ] A lane whose container is running and has printed nothing for the
      declared window is stopped: `docker rm -f`, evidence saved, exit 3,
      naming the age and the last line seen;
- [ ] A lane that keeps printing is never stopped, and its output still
      reaches the operator's terminal unbuffered and in order (the
      pass-through must not regress into a captured-then-replayed stream);
- [ ] An assay lane is unchanged — it still judges from the progress file
      and discloses `source: progress file`, never downgrading to the log
      stream because a file has not appeared yet;
- [ ] **The source-of-signal line is printed on the RE-ATTACH and FOLLOW
      paths too, not only on the fresh one** (controller ruling RW-18, out
      of review round 1's S6). Rev 34's first cut printed `budget` and
      `stall_timeout` on the fresh path alone, so a re-attached lane was
      stopped against a `stall_timeout` its own invocation had never
      mentioned; rev 34 fixes that (`print_lane_bounds`, called from all
      three paths), and this item inherits the rule rather than the defect.
      The `source:` clause belongs on the same line, so adding it to
      `print_lane_bounds` covers every path by construction — a second print
      site is how the two drift apart again.

### Status — OPEN 2026-09-02, E-3 candidate (23.5.0)

Sequenced with the other progress/resume work: E-3 of
`assay/nyxloom-trove/WAVE-PLAN-2026-09-02-after-v10.md` (RG-36 exact timing
once assay B065 lands, RG-38, RG-40). Not implemented in rev 34 by ruling —
the wave shipped the refusal, and this is the answer to what the refusal
costs.
