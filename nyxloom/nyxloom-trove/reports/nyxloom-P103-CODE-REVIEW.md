# nyxloom-P103 — adversarial code review (fresh reviewer)

**Verdict: ACCEPT.**

Reviewed range `6322251b..a9226fdf` (5 commits) across BOTH `nyxloom/` and the
sibling `pwmcp/` in worktree `/workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance`.
Reviewer: fresh session, no carve context. Method: blind diff pass first, then the
935-line handoff, then LOG/REPORT reconciliation, then independent live re-running
of O1–O5 from clean state. Every material claim below was re-derived by this
reviewer, not accepted from the REPORT.

Note on the missing artifact: there is no `nyxloom-P103-CARVE-REVIEW.md` in the
repo (the carve's 4 review rounds were folded into carve commits without a
persisted report). This file closes that gap for the implementation review; the
carve-review trail remains lost and I re-derived the reasoning from the handoff
and the live tree rather than inheriting it.

---

## 1. Blocking findings

**None.** No blockers. I tried to break this package on the axes below and it held.

---

## 2. What I verified independently

### 2.1 Zero-Python claim — CONFIRMED

`git diff --stat 6322251b..a9226fdf -- '*.py'` is empty. 18 files changed, all
TOML/TOML.j2/YAML/Markdown. The package's central premise (the registered gate
structurally cannot verify it) is sound.

### 2.2 Forbid-list integrity — CONFIRMED

`git diff 6322251b..a9226fdf -- infra/slices/ ntfy/server.yml run-gate.toml` is
empty at both the `nyxloom/` and worktree-root spellings. `pwmcp/ciu.global.toml.j2`
(VERIFY-ONLY per the handoff) is untouched and still declares zero governance
(`grep -c governance` → 0), so the higher-precedence layer cannot shadow the
defaults-layer table.

### 2.3 Scope compliance — CONFIRMED

All 18 changed files map to a declared `scope.touch` entry. Nothing outside scope.

### 2.4 Every pinned verbatim block — CONFIRMED byte-for-byte (scripted)

I extracted the pinned fenced blocks from the handoff by line range and compared
them programmatically (not by eye) against what landed:

| Work item | Target | Result |
|---|---|---|
| 1a | `nyxloom/ciu.global.defaults.toml.j2` | **MATCH** |
| 1b | `pwmcp/ciu.global.defaults.toml.j2` (extra header + shared block, `4g`) | **MATCH** |
| 2 | `nyxloom/pwmcp-instance/ciu.defaults.toml.j2` `[pwmcp.governance]` | **MATCH** |
| 2b | `nyxloom/nyxloomd/ciu.defaults.toml.j2` `[nyxloomd.governance]` | **MATCH** |
| 3b | `nyxloom/ntfy/docker-compose.yml` replacement block | **MATCH** |

Work item 5's NL-6 replacement text is *described*, not pinned verbatim, in the
handoff (contrary to the dispatch brief's phrasing) — judged on content instead;
see §2.9.

### 2.5 The `ntfy` stale-slice fix (Work items 3a–3f) — the most failure-prone part, CONFIRMED

This is where I expected to find the defect. I did not.

- **O2(i)** — `cgroup_parent` absent from all four files
  (`ntfy/ciu.compose.yml.j2`, `ntfy/ciu.defaults.toml.j2`,
  `nyxloomd/ciu.defaults.toml.j2`, `nyxloomd/ciu.toml`). Both the Jinja block and
  the TOML keys are gone, which is what the carve's O2 `negative` clause demands
  (deleting only the keys and leaving the Jinja block would have passed the rest
  of O2 while leaving a template reading a key no config supplies).
- **O2(ii)** — `grep -c cgroup_parent ntfy/ciu.compose.yml` on my own dry-run
  render → `0`.
- **O2b — content-equivalence, proven semantically rather than textually.** I
  parsed both files with a YAML parser and compared the governance key set:
  `ntfy/docker-compose.yml`'s inline block is **semantically identical** to the
  ciu-rendered `ntfy/.ciu/ciu.compose.overlay.yml` (the two differ only in list
  indentation depth). Against `nyxloomd`'s overlay it differs in exactly one key,
  `mem_limit` (2g vs 6g), as designed. ntfy's other hardening (`user 1003:1003`,
  `read_only`, `cap_drop ALL`, `no-new-privileges`, bounded json-file logs, pinned
  image) is intact.
- **The plain-compose path actually parses and applies.** `docker compose -f
  docker-compose.yml config` exits 0 with no warnings and resolves
  `mem_limit: 2147483648` (2 GiB), `mem_reservation: 268435456` (256 MiB),
  `memswap_limit: 18253611008` (17 GiB), `cgroup_parent: dev-background.slice`,
  blkio 200/400 on `/dev/vda`. The live deployment path will genuinely apply
  these on the next `docker compose up -d`.
- **O2b's premise verified against the live host**, not assumed:
  `docker inspect nyxloom-ntfy` reports compose project `ntfy` with
  `config_files=/workspaces/vbpub/nyxloom/ntfy/docker-compose.yml` — a plain
  `docker compose` deployment that ciu governance never touches. The inline caps
  are genuinely necessary; deleting line 14 without replacing it (as an earlier
  carve revision specified) would have made the live path strictly worse.
- **The bug is real and live right now.** That same container currently runs
  `CgroupParent=nyxloom.slice Memory=0 MemorySwap=0` — the only container on this
  host in that fictional slice. This is not a theoretical fix.

### 2.6 `nyxloom.slice` residue — oracle passes, but see finding A1

The pinned O2(iii) command returns nothing (exit 1) at HEAD, and returns exactly
the four files the handoff names at `6322251b`. It is a genuine before/after
discriminator. The dispatch brief's stronger framing ("`git grep` across the whole
worktree returns ZERO hits") is **not** satisfied and was never the package's
contract — residue remains in `infra/slices/` (forbid-listed), `docs/`, the
backlog and the handoff, all deliberately outside the pinned globs. See A1 for a
real blind spot I did find in the glob itself.

### 2.7 Live verification O1/O3/O4 — independently reproduced from clean state

Checked `docker ps`/`uptime` first (load 6.27, shared host). All runs `--dry-run`;
no stack brought up.

- **O1** (`ciu up --profile default --dry-run --define-root "$PWD"`, ciu 7.11.0):
  exit 0; both stacks' overlays carry exactly one
  `cgroup_parent: dev-background.slice`; both `[GOVERNANCE]` lines report
  `device=/dev/vda (explicit)` and `services_injected=1 exempt=0`.
- **O4**: three distinct ceilings in one sweep — `ntfy 2g`, `nyxloomd 6g`,
  `pwmcp-instance 4g` — each traceable to the file that declares it. The per-stack
  override tables are real, not a root-value coincidence.
- **O3** (`pwmcp`, `ciu up --dir . --dry-run`): exit 0, overlay carries
  `cgroup_parent: dev-background.slice` + `mem_limit: 4g`.

I did **not** rely on the REPORT's pasted output. My output matches it, including
the `pwmcp-016b19-network` name (deterministic instance hash).

**Mechanism cross-check against ciu source** (`ciu 7.11.0`, installed copy verified
byte-identical to the in-worktree source): per-stack `[<root_key>.governance]`
override is real and is a shallow per-key merge over the root table
(`governance.py:442-486`, called at `engine.py:1767-1769`), layered
`GOVERNANCE_DEFAULTS` → global `[governance]` → stack table. The handoff's warning
that the table must be keyed `[pwmcp.governance]` (the stack's single non-reserved
top-level key) and that `[pwmcp-instance.governance]` would be silently ignored is
correct (`config_model.py:1110-1156`). `resolve_cgroup_parent`'s `""` →
`$CGROUP_PARENT_DEV_BACKGROUND` → `[S15.2]` chain is confirmed at
`governance.py:288-312`, and that variable **is** set in this devcontainer
(`CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`) — so the carve's insistence
on the committed literal is well-founded, not pedantry.

### 2.8 O5 mutation oracle — re-run by me, planted and reverted, CONFIRMED

- **Break (a) `enabled = false`**: from clean state, `ntfy` overlay drops to its
  configfile bind only (`cgroup_parent` count 0) and `nyxloomd`'s overlay is not
  written at all. O1 fails as designed.
- **Break (b) root table absent**: only one `[GOVERNANCE] disabled` line
  (nyxloomd, which declares its own table); `ntfy` gets none. Critically, the
  per-stack `[nyxloomd.governance] mem_limit = "6g"` **alone does not rescue
  governance** — `enabled` defaults to `False` — proving the committed defaults
  layer is genuinely load-bearing and a gitignored site layer would be a false
  PASS. This is exactly O5's claim.
- Both planted breaks reverted; tree verified clean.

**I independently hit the stale-overlay trap the implementer self-disclosed**, and
it is real: with `enabled = false`, `generate_overlay` early-returns `None`
(`composefile.py:1310-1311`) without deleting or overwriting an existing overlay,
so a second run reads the *previous* run's file and falsely shows injection still
present. Any future O5-style mutation run **must** delete `<stack>/.ciu/` first.
The implementer's disclosure is accurate and their root-cause analysis is correct.

Separately confirmed: `ciu check` prints `governance: pass` even with
`enabled = false` — the carve's "hollow oracle" claim about `ciu check` is
independently true.

### 2.9 Backlog integrity — CONFIRMED

- **NL-6 closed correctly.** `status: fixed`, `closed_date`, `closed_reason`
  present. All three of the entry's original false claims appear **only** inside
  explicit `— FALSE.` corrective framing; none survive as assertions. The
  author-always-wins mechanism the entry's "Proposed contract" missed is now
  stated. (One residual inaccuracy in an untouched section — see A6.)
- **NL-8..NL-11 are real entries, not stubs** — 72, 76, 88 and 83 lines
  respectively, each with a substantive "Observed mechanism and reproduction"
  carrying file:line evidence. This estate's NL-4/NL-5 empty-stub failure did not
  recur.
- **INDEX.md is byte-equal to a fresh CLI regeneration.** I ran
  `python3 exec-nyxloom.py backlog index` and diffed against the committed file:
  identical, `git status` clean afterward. Not hand-edited.
- `nyxloom lint` on the handoff: **exit 0, 0 errors**, warnings only in the
  L10/L13/L7 classes the handoff predicts (L10 size 16933 against an 18k floor).

### 2.10 Regression gate — verified from the artifact, not the paste

`.assay/verdict-tester-unified.json` is **still present** and I read it directly:
`outcome=PASS`, `exit_code=0`, `commit=3a94ca9f459cb29700a8397496ea491751531572`,
`lane=tester-unified`, `argv_modified=false`, judge `assay 4.0.0` zipapp with
sha256 digest. R0 (`tests-pass`) PASS; R1 (`changed-line-coverage`) PASS with
`considered=0, executable=0, covered=0` — genuinely vacuous, exactly as the
handoff predicted. The judged commit `3a94ca9f` is the second-to-last commit;
the tip `a9226fdf` touches only LOG and REPORT, so no code changed after judging.
**This is an artifact read, not an accepted paste.**

### 2.11 Memory sizing — principled, not a guess dressed as a measurement

I checked the load-bearing numbers against the repo and the live host:

- `dev-background.slice.in` + `host-setup.env.example`:
  `MEMORY_HIGH=6G`, `MEMORY_MAX=8G`, `SWAP_MAX=48G`, `CPU_WEIGHT=20` — transcribed
  accurately.
- `mdt-dev-cap-watcher.py`: `DEV_CAP_MEMORY_MAX` defaults to `"1G"` — the "1g by
  omission vs 6g by derivation" argument is real.
- nyxloomd's derivation inputs: `trust = "operator"` routes = **8**,
  `max_active_tasks = 5` — both exactly as claimed.
- `shm_size = "2gb"` at the cited line — the 4g pwmcp rationale is real.
- Live host: every dstdns container runs `dev-background.slice` with
  `Memory=2147483648` (2g); **`besteffort.slice` appears nowhere on this host**
  (25 containers in `dev-background.slice`). Work item 4's doc corrections are
  factually right.

The reasoning is explicit about which figures are estimates (the ~1g/CLI number)
and files NL-11 to measure them. That is the honest form. Caveat at A5.

---

## 3. Non-blocking findings

**A1 — O2(iii)'s glob has a blind spot at exactly the depth this package's main
edits live.** `git grep -- 'nyxloom/**/*.j2'` does **not** match
`nyxloom/ciu.global.defaults.toml.j2`: git's default pathspec matching requires
`**/` to match at least one directory component, so a project-root-level file is
never scanned. Proof — with explicit `:(glob)` magic the same pattern *does* match
that file, and it contains the `nyxloom.slice` literal at
`nyxloom/ciu.global.defaults.toml.j2:45` (inside Work item 1a's own pinned comment,
explaining why the slice must not be used — semantically correct content). So
O2(iii) passes, and it is still a valid before/after discriminator, but it never
inspects either root's `ciu.global.defaults.toml.j2` — the two files this package's
central change lives in. A `cgroup_parent = "nyxloom.slice"` accidentally typed
into either root would pass O2(iii) silently.
*Not an implementation defect* — the implementer ran the pinned command exactly and
reported it honestly. *Prescription for a future package:* add `'nyxloom/*.j2'
`'pwmcp/*.j2'` (and `*.toml`/`*.yml` equivalents) to the pathspec, or use
`:(glob)` magic throughout.

**A2 — `read_iops = 200` is a ciu *fallback*, now frozen into a committed file.**
ciu's own log line says `read_iops=200 (fallback default (no io-baseline file
found ...))`. `governance.py:575-611` computes `read_iops` as 2/3 of `RIOPS_MAX`
from `/var/lib/ciu/io-baseline.env` or `/var/lib/mdt/io-baseline.env` whenever
either exists; neither exists today. If an operator ever installs an io-baseline
file, ciu's overlay for the *templated* stacks will compute a different value while
`ntfy/docker-compose.yml`'s inline `rate: 200` stays pinned — silent drift on
precisely the path that has no overlay to correct it. The inline comment warns
"MUST be kept in step by hand" but names only the `[governance]` table as the thing
to track. *Prescription:* extend that comment to name the io-baseline trigger, or
fold it into the recommended ntfy template/sibling agreement test.

**A3 — `mem_swap_limit` stays at the 17g default while `mem_limit` is raised per
stack.** ciu's 17g default is paired with its 1g `mem_limit` default (1g RAM +
16g swap). The package now ships three different `mem_limit` values against one
unchanged combined ceiling, so nyxloomd may use ~11g of swap. The handoff records
and accepts this deliberately (Memory sizing item 4) and defers it as an
estate-wide decision affecting dstdns — I agree with the deferral; flagging so the
controller knows it is now spread across three sizes.

**A4 — no `cpus` cap is declared.** ciu supports `governance.cpus` (default `""` =
uncapped, CIU-90 / S15.21) and this package sets none, so per-container CPU stays
unconfined; only the slice's `CPUWeight=20` applies. Consistent with D-G4
(absolute limits are host-owned) and with dstdns, so I judge it correct by design
— but worth stating plainly that "no longer at Docker's unconfined default" is now
true for placement, memory and blkio, and still not for CPU, on a host that has hit
load 85.

**A5 — the 8G backstop comes from `host-setup.env.example`, not a deployed
`host-setup.env`.** I confirmed no `host-setup.env` exists anywhere under
`host-setup/`. The handoff's section heading "The host backstop, **measured**"
overstates this; the REPORT honestly discloses the live value cannot be queried
from inside the devcontainer. The "a generous per-container ceiling is free"
argument rests on that 8G. Non-blocking (the values are the repo's own defaults and
dstdns demonstrably runs 2g against them), but the heading should not say
"measured".

**A6 — NL-6's untouched "Proposed contract" section still recommends the rejected
option.** It reads `cgroup_parent = "dev-background.slice"` *"(or `""`, which ciu
v7 resolves from `$CGROUP_PARENT_DEV_BACKGROUND`)"* — the host-dependent form this
package deliberately rejected and whose rejection O1 exists to enforce. Work item 5
mandated replacing only "Observed mechanism" and "Oracles", so the implementer
complied exactly; but a future reader of the closed entry could follow the stale
advice. One-line fix whenever the entry is next touched.

**A7 — merging does not fix the running container; an operator action remains.**
`nyxloom-ntfy` is live right now at `CgroupParent=nyxloom.slice, Memory=0`. The
package is config-only and correctly brings nothing up, but the fix takes effect
only when someone re-runs `docker compose up -d` from
`/workspaces/vbpub/nyxloom/ntfy/`. Per the handoff's own "one behaviour change to
state plainly", that redeploy also makes ntfy — a public-facing service behind
tls-edge — OOM-killable under `dev-background.slice`'s
`ManagedOOMMemoryPressure=kill` at 75% of an 8G aggregate for the first time. The
handoff asks for **operator acknowledgement** of this; make sure that actually
happens at merge rather than landing silently.

**A8 — cosmetic.** The handoff's Environment-setup step 2 predicts the discarded
`nyxloomd/ciu.toml` render artifact as `3 insertions(+), 8 deletions(-)`; the
actual is `3 insertions(+), 7 deletions(-)`. Both predicted hunks present
(new `[nyxloomd.governance]` table; stripped CR-16 comment block), no third hunk.

---

## 4. Host hygiene

Left clean, verified rather than assumed:

- `git status` clean; no generated `.ciu/`, `ciu.compose.yml`, or `ciu.global.toml`
  artifacts anywhere under `nyxloom/` or `pwmcp/`.
- The one network my O3 run created (`pwmcp-016b19-network`) was disconnected from
  `dstdns-devcontainer-vb` and removed; `docker network ls` is **byte-identical to
  my pre-review snapshot**.
- I started **no** containers (all runs `--dry-run`). The only
  ntfy/pwmcp/nyxloom containers present are pre-existing ones up 11 hours.
- `nyxloom-p103-ciu-governance-2b92d3-network` is the worktree's **own** network,
  recorded in `ciu.worktree-instance.json` under `runtime.network` — correctly left
  alone by the implementer and by me. It is **not** implementer debris (I initially
  suspected it was; it is not).
- No implementer debris found on the host.

---

## 5. Verdict

**ACCEPT — merge it.**

The package does what it claims. It changes zero Python; every pinned verbatim
block landed byte-for-byte; the forbid list and scope are clean; O1–O5 all
reproduce independently from clean state on a fresh reviewer's hands; the mutation
oracle genuinely fails when planted and recovers when reverted; the backlog is
closed and filed correctly with a CLI-regenerated index; and the gate verdict is
PASS read from the artifact itself. The hardest part — the `ntfy` plain-compose
path that the live container is actually deployed from — is not just present but
semantically identical to what ciu renders for the templated path, and Docker
Compose itself parses and resolves it correctly.

The eight findings in §3 are advisory. None blocks the merge: A1, A5, A6 and A8 are
inaccuracies in oracle/prose framing rather than in shipped config; A2, A3 and A4
are correctly-deferred future work; A7 is a post-merge operator action the handoff
already calls for. I would merge as-is and carry A2 and A7 forward — A7 especially,
since the fix has no effect on the running container until it is redeployed, and
that redeploy carries the OOM-posture change the handoff asked to be acknowledged
rather than landed silently.

Two things I want to record as credit rather than criticism: the implementer's
disclosure of the stale-overlay artifact and the accidental `git checkout --`
revert was accurate in both cases — I reproduced the stale-overlay trap myself and
their root-cause analysis of `generate_overlay`'s early-return is correct — and
the REPORT's five disclosed deviations were all real, all minor, and all resolved
the way I would have resolved them.

---

*Reviewed by a fresh adversarial reviewer session, 2026-09-08. All verification
re-run independently; no claim in this file is taken from the REPORT without
re-derivation.*
