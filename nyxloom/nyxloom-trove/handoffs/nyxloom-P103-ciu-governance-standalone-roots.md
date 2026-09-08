---
schema_version: 1
id: nyxloom-P103-ciu-governance-standalone-roots
project: nyxloom
title: "Declare [governance] on nyxloom's and pwmcp's ciu roots; retire the fictional nyxloom.slice"
tier: luna-high
input_revision: "ff3d5303"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/backlog/NL-6-nyxloom-s-and-pwmcp-s-ciu-roots-declare-no-governance-nyxloomd.md
scope:
  touch:
    - "ciu.global.defaults.toml.j2"            # THE fix, half 1. Insert Work item 1's pinned `[governance]` block immediately BEFORE the existing `[ciu]` table at line 33. Measured at input_revision: this file has NO `[governance]` table (verified `git grep -n governance -- nyxloom pwmcp`, tabulated below). The defaults layer is the correct home, NOT the gitignored site layer: `config_model.deep_merge` (ciu/src/ciu/config_model.py:558-571) iterates `override.items()` only, so a higher layer that omits the table cannot suppress it -- verified empirically on pwmcp, whose tracked higher-precedence `ciu.global.toml.j2` carries no `[governance]` and did NOT suppress the defaults-layer table
    - "pwmcp/ciu.global.defaults.toml.j2"        # THE fix, half 2 -- Work item 1(b). READ THE PATH CAVEAT: this is relative to the WORKTREE ROOT, not to the nyxloom project root that every other entry here is relative to. pwmcp/ is a SIBLING project in the same monorepo checkout and is NOT a registered nyxloom project (it has no nyxloom-trove/), so there is no other trove this half could be carved in, and NL-6 covers both roots from nyxloom's backlog. nyxloom lint CANNOT validate this entry: L7 hard-errors on any "../" or absolute path (src/nyxloom/lint.py:1017-1024), and this bare form is accepted only because L7 exempts a scope.touch path that does not exist under cfg.root as "a file to be created" (lint.py:1044-1045) -- it resolves to the nonexistent nyxloom/pwmcp/..., NOT to the real file. This is a known, deliberately-accepted lint blind spot, filed by Work item 7; it is recorded here rather than quietly relied upon. The implementer edits <worktree>/pwmcp/ciu.global.defaults.toml.j2 and proves it via O3, which reads the real overlay
    - "pwmcp-instance/ciu.defaults.toml.j2"    # per-stack memory override, Work item 2. Add `[pwmcp.governance] mem_limit = "4g"` (the stack's root key is `pwmcp`, not `pwmcp-instance` -- confirmed: the rendered overlay's service key is `pwmcp`). Reason is measured, not invented: line 34 declares `shm_size = "2gb"`, and cgroup v2 charges /dev/shm pages to the container's own memory cgroup, so the root's 2g would be consumed by the shm allocation alone. See "Memory sizing" below for why this override is safe even if that accounting claim were wrong
    - "ntfy/ciu.compose.yml.j2"                # THE load-bearing correction the backlog entry MISSES. Delete lines 13-15 (`{% if ntfy.runtime.cgroup_parent %}` / `    cgroup_parent: {{ ntfy.runtime.cgroup_parent }}` / `{% endif %}`). ciu governance NEVER overwrites a compose key the stack author already emits (ciu/src/ciu/governance.py:1016-1038, `if "cgroup_parent" not in author_keys`; SPEC.md:2917-2923 "the stack author's rendered compose always wins"), so leaving this block makes ntfy the ONE service that keeps `nyxloom.slice` after the fix. MEASURED, not inferred -- the tracer bullet in "Probe log" shows ntfy's overlay receiving mem_limit/blkio but NO cgroup_parent until this block is gone
    - "ntfy/docker-compose.yml"                # line 14, `    cgroup_parent: nyxloom.slice`. Delete it. This is the pre-rendered plain-compose sibling; both this file's own header (lines 2-3) and ntfy/ciu.compose.yml.j2's ("keep both in sync: edit HERE, re-render or hand-sync") make it a REQUIRED companion edit. Not a ciu path -- it is the fallback a plain `docker compose up` uses, and leaving it would keep the fictional slice alive on exactly that path
    - "ntfy/ciu.defaults.toml.j2"              # line 24-25 (the comment `# systemd slice for the nyxloom service family.` and `cgroup_parent = "nyxloom.slice"`). Delete both. After the template edit above nothing reads this key: `[<stack>.runtime]` is NOT a ciu-recognised table (no `[runtime]` anywhere in ciu/docs/SPEC.md; ciu reads it never, warns never -- S15.13's unknown-key WARN covers the `[governance]` table ONLY, ciu/src/ciu/governance.py:384-395), so its sole consumer was the Jinja reference just deleted
    - "nyxloomd/ciu.defaults.toml.j2"          # line 24-25, same two lines (comment reads `(shared with ntfy)`). Delete both. Already dead at input_revision with NO template consumer at all: `grep -n cgroup_parent nyxloomd/ciu.compose.yml.j2` returns ZERO hits, which is exactly the "dead config key that looks like placement and produces none" incident docs/plan-resource-governance.md:130-141 records
    - "nyxloomd/ciu.toml"                      # line 8-9, `[nyxloomd.runtime]` / `cgroup_parent = "nyxloom.slice"`. Delete the key line. NOTE this file is TRACKED although `.gitignore:202` lists `**/ciu.toml` (tracked files ignore .gitignore) AND it is a `ciu render` OUTPUT -- `ciu render --profile default` rewrites it. Verified the rewrite is comment-only at input_revision (values identical), but the implementer MUST `git diff` it after any render and must not commit a render's comment-stripping as part of this package. See "Environment setup" for the exact restore step
    - "docs/plan-resource-governance.md"       # Work item 4 ONLY: append the pinned correction note after the D-G0a block (lines 143-157) and after the duplicate "Two ciu defaults that would bite nyxloom" block (lines 221-231). BOTH blocks assert `cgroup_parent` defaults to `besteffort.slice`; that is FALSE for ciu 7.11.0 -- `GOVERNANCE_DEFAULTS["cgroup_parent"] = ""` (ciu/src/ciu/governance.py:67) and `resolve_cgroup_parent` RAISES when neither the key nor $CGROUP_PARENT_DEV_BACKGROUND resolves (governance.py:288-312; SPEC.md:2862-2865 "No hardcoded fallback"). Do NOT rewrite the historical narrative -- append only. Listed in scope precisely so Work item 4 cannot route the implementer into a BLOCKED exit on an unlisted path
    - "nyxloom-trove/backlog/NL-6-nyxloom-s-and-pwmcp-s-ciu-roots-declare-no-governance-nyxloomd.md"  # status open -> fixed via the CLI, AND replace the "Observed mechanism"/"Oracles" sections with Work item 5's PINNED text. Three of the entry's own claims are FALSE at input_revision and must not survive as a fixed entry: (a) "the rendered compose carries no cgroup_parent on any service" -- ntfy's carries `nyxloom.slice`; (b) "`ciu render` -> the rendered compose" -- `ciu render` renders TOML ONLY, never the compose (ciu/src/ciu/deploy.py:1677-1693); (c) "`ciu check` passes the governance stage" as an oracle -- that stage is shape-only (deploy.py:2746-2755) and PASSES at input_revision with zero governance declared, i.e. it is a hollow oracle
    - "nyxloom-trove/backlog/INDEX.md"         # GENERATED (line 1 header: "GENERATED by `nyxloom backlog index` -- do not edit; regenerate.") and enforced by BLG3. MUST be regenerated by the CLI, never hand-edited. Note the tester-unified gate does NOT run `nyxloom lint`, so a stale INDEX ships green unless Work item 5(c)'s BLG-findings read is produced
    - "nyxloom-trove/reports/nyxloom-P103-LOG.md"      # NEW: per-commit LOG, estate standard contract
    - "nyxloom-trove/reports/nyxloom-P103-REPORT.md"   # NEW: per-oracle evidence. The gate CANNOT check any oracle in this package (zero Python changed -- see "Gate argv"), so every oracle's command output AND the O5 controlled-break output are run BY HAND and pasted here. This file is where the proof lives; an empty or paraphrased REPORT is a failed package
    - "nyxloom-trove/reports/nyxloom-P103-BRIEF.md"    # authorised checkpoint artefact (E-008 clause in escalate_if); expected unused, this is a small package
    - "nyxloom-trove/reports/nyxloom-P103-COMPACT.md"  # authorised checkpoint artefact (the self-authored retention prompt); expected unused
  forbid:
    - "infra/slices/"                          # the nyxloom-*.slice unit templates (D-G1) are a HOST-SETUP task needing root + `systemctl daemon-reload`, which the daemon does not and must not have (docs/plan-resource-governance.md D-G2). This package deliberately targets the ALREADY-INSTALLED `dev-background.slice` instead; installing new slices is a separate operator-run package
    - "ntfy/server.yml"                        # ntfy's auth/attachment governance surface. The word "governance" in its header is about ntfy's OWN deny-all config, NOT cgroups. Unrelated; do not touch
    - "run-gate.toml"                          # gate-container governance is a SEPARATE mechanism ($CGROUP_PARENT_DEV_BACKGROUND via run-gate.py, nyxloom-trove/nyxloom.toml:74-76). This package governs DEPLOYED stacks only. Changing gate governance would alter every lane's runtime and is explicitly out of scope per NL-6's own "no change to run-gate's gate governance"
oracles:
  - id: O1
    observable: >-
      From a FRESH worktree of this branch, `cd nyxloom && ciu up --profile default --dry-run
      --define-root "$PWD"` exits 0, and for BOTH stacks the generated overlay
      `<stack>/.ciu/ciu.compose.overlay.yml` contains the EXACT line
      `    cgroup_parent: dev-background.slice`. Concretely, both
      `grep -c '^    cgroup_parent: dev-background\.slice$' ntfy/.ciu/ciu.compose.overlay.yml`
      and the same on `nyxloomd/.ciu/ciu.compose.overlay.yml` print `1`. Additionally the
      run's `[GOVERNANCE]` log line reports `services_injected=1 exempt=0` for each stack.
      The overlay is the ONLY correct place to look: ciu writes governance to
      `<stack>/.ciu/ciu.compose.overlay.yml` (ciu/src/ciu/composefile.py:1407-1413) and
      merges it via a second `-f` (composefile.py:1598-1613), deliberately leaving the
      rendered `ciu.compose.yml` byte-exact author output (SPEC.md:2930-2932).
    negative: >-
      Grepping `ciu.compose.yml` instead of the overlay is the false-PASS trap this oracle
      exists to close: governance NEVER writes into `ciu.compose.yml`, so that grep returns 0
      both before and after a correct fix, and 1 for ntfy at input_revision for the WRONG
      reason (the author's own `nyxloom.slice` line). The slice literal is pinned because
      `dev-background.slice` is the one slice VERIFIED to exist and be in active use on this
      host (`docker inspect` of 25 running containers at carve time: every one is
      `dev-background.slice`), whereas `nyxloom.slice` exists NOWHERE -- and ciu cannot catch
      that here, because its slice-existence preflight SKIPS inside this devcontainer
      (`[S15.G9-1] systemctl is present but systemd is not PID 1`, observed in the probe log),
      so a fictional slice fails OPEN to an unbounded transient cgroup. An implementation
      that sets `cgroup_parent = ""` and relies on $CGROUP_PARENT_DEV_BACKGROUND ALSO fails
      this oracle by design: that variable is unset in this repo's ciu.env (measured: `grep -i
      cgroup ciu.env` -> no hits), so `resolve_cgroup_parent` would RAISE (governance.py:288-312)
      and the run would exit non-zero rather than silently degrade. Requiring `exempt=0`
      catches an implementation that "passes" by adding the service to `exempt_services`.
    gate: tester-unified
  - id: O2
    observable: >-
      Same fresh-worktree dry-run as O1. The ntfy service is governed by ciu ALONE, not by its
      own template: `grep -c cgroup_parent ntfy/ciu.compose.yml` prints `0`, AND
      `git grep -c 'nyxloom\.slice' -- nyxloom/` prints nothing outside
      `docs/plan-resource-governance.md` and `infra/slices/` (the historical narrative and the
      not-yet-installed unit templates, both deliberately retained). The four config/compose
      occurrences measured at input_revision -- ntfy/ciu.compose.yml.j2:14,
      ntfy/ciu.defaults.toml.j2:25, ntfy/docker-compose.yml:14, nyxloomd/ciu.defaults.toml.j2:25,
      nyxloomd/ciu.toml:9 -- are all gone.
    negative: >-
      An implementation that adds the root `[governance]` table but leaves
      ntfy/ciu.compose.yml.j2's `{% if ntfy.runtime.cgroup_parent %}` block intact still passes
      a naive "governance is declared" check and still passes O1 for nyxloomd -- but ntfy keeps
      `cgroup_parent: nyxloom.slice` and stays unbounded, which is the ENTIRE defect for the one
      service that is actually running in production right now (measured at carve time:
      `docker inspect nyxloom-ntfy` -> `CgroupParent=nyxloom.slice Memory=0 MemorySwap=0
      MemoryReservation=0 BlkioDeviceReadIOps=[]`). This is precisely the failure the backlog
      entry's own proposed contract would have shipped. Deleting the TOML key while leaving the
      Jinja block is equally wrong in the other direction and must not be treated as sufficient:
      it changes nothing observable here (an undefined Jinja name is falsy in the `{% if %}`) but
      leaves a template reading a key no config supplies -- so this oracle requires BOTH the
      template block and the TOML key gone, and additionally the plain-compose sibling
      ntfy/docker-compose.yml:14, which no ciu command renders and which a `ciu`-only check
      therefore cannot catch.
    gate: tester-unified
  - id: O3
    observable: >-
      From the same fresh worktree, `cd pwmcp && ciu up --dir . --dry-run --define-root "$PWD"`
      exits 0 and `.ciu/ciu.compose.overlay.yml` contains BOTH
      `    cgroup_parent: dev-background.slice` and `    mem_limit: 4g`. This proves the second
      root independently: pwmcp is a SEPARATE ciu root (`standalone_root = true`), so nyxloom's
      table cannot reach it -- v7 roots are islands, which is the whole reason NL-6 names two
      files.
    negative: >-
      Running only nyxloom's dry-run and assuming pwmcp follows is the false-PASS: there is no
      inheritance between v7 roots (that is v8's `[ciu] inherit`, SPEC-V8 draft.5 S3.1.5, and
      explicitly NOT built here). An implementation that edits pwmcp/ciu.global.toml.j2 (the
      tracked higher-precedence near-duplicate) INSTEAD of ciu.global.defaults.toml.j2 would
      also pass a local dry-run while violating the contract -- the defaults layer is the
      committed one every fresh worktree gets, and it is the layer NL-6 names. Verify the edit
      landed in ciu.global.defaults.toml.j2 by file path, not merely by observing the overlay.
      Note `--dir .` on pwmcp CREATES a docker network and connects the devcontainer to it
      (`auto_connect_network = true`, observed in the probe log) -- see Environment setup for
      the mandatory teardown; an oracle run that leaves that network behind is not complete.
    gate: tester-unified
  - id: O4
    observable: >-
      The optional tools stack is governed too: `cd nyxloom && ciu up --profile tools --dry-run
      --define-root "$PWD"` exits 0 and `pwmcp-instance/.ciu/ciu.compose.overlay.yml` contains
      `    cgroup_parent: dev-background.slice` AND `    mem_limit: 4g` (the per-stack override
      from Work item 2, NOT the root's 2g).
    negative: >-
      `pwmcp-instance` is deliberately OUTSIDE the default profile (ciu.global.defaults.toml.j2
      lines 25-31), so O1's `--profile default` run NEVER touches it -- checking only the default
      profile and declaring the root covered is the false-PASS. `mem_limit: 4g` rather than `2g`
      is the discriminator that proves the per-stack table was written under the correct root key:
      ciu resolves the per-stack table as `[<root_key>.governance]` where the root key is the
      stack's single non-reserved top-level key (`pwmcp` here, NOT `pwmcp-instance`), so a table
      written as `[pwmcp-instance.governance]` is silently ignored -- ciu warns on unknown keys
      only INSIDE `[governance]` (governance.py:384-395), never on a misplaced table -- and the
      overlay would show the root's `2g`. That silent-ignore is the same class as the dead
      `[<stack>.runtime] cgroup_parent` key this package removes.
    gate: tester-unified
  - id: O5
    observable: >-
      MUTATION-CHECKED, run by hand, output pasted in nyxloom-P103-REPORT.md. Two controlled
      wrong implementations, each of which MUST make O1 fail: (a) flip the committed table to
      `enabled = false` and re-run O1's dry-run -- no overlay `cgroup_parent` line is produced
      for either stack, both greps print `0`, and the `[GOVERNANCE]` log line is absent
      entirely; (b) move the whole `[governance]` block out of `ciu.global.defaults.toml.j2`
      into an untracked site-layer `ciu.global.toml.j2`, then run O1 from a `git stash -u`-clean
      or freshly-cloned worktree -- the table is absent there and both greps print `0`. Restore
      the committed state and re-run O1 green afterwards; the REPORT records all three runs.
    negative: >-
      A break that cannot fail the oracle is not mutation evidence. Do NOT substitute "delete
      the table entirely" for break (a): that is indistinguishable from the input_revision
      baseline and proves only that the baseline is broken, which is already known. Break (a)
      specifically pins that `enabled` is load-bearing rather than decorative -- ciu enforces it
      as a bool (governance.py:398-402) and a table present with `enabled = false` injects
      nothing. Break (b) is the one that pins the LAYER: an implementer who "fixes" this by
      writing the table into the gitignored site layer produces a locally-green dry-run on their
      own machine and ships nothing -- every fresh worktree, and the deployed host, stay
      unbounded. The oracle is over a FRESH worktree's committed content for exactly this reason.
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "`git grep -n '\\[governance\\]' -- nyxloom pwmcp` at dispatch time returns ANY hit -- measured at input_revision as ZERO hits in every tracked ciu.*.toml* file under both roots (tabulated below). A pre-existing table means someone landed part of this fix concurrently and the layering analysis is stale: report BLOCKED naming the file, do not merge two tables"
  - "`pwmcp/ciu.global.toml.j2` has acquired a `[governance]` table between carve and dispatch -- it sits at HIGHER precedence than the file this package edits (ciu/src/ciu/config_model.py:668), so a table there would win per-key over the committed defaults and silently invert the fix. Report BLOCKED; resolving the two-file duplication is Work item 6's filing, not this package's edit"
  - "`ciu version` reports anything other than 7.11.0 -- every semantic in this carve (author-always-wins injection, the no-hardcoded-slice-default, overlay path, per-key deep_merge) was measured against 7.11.0 exactly. A different version means the probe log is stale evidence; re-probe before trusting any oracle, and report BLOCKED if the overlay path or precedence changed"
  - "`ciu up --profile default --dry-run` exits non-zero for a reason OTHER than a governance finding -- e.g. the DooD preflight, a secrets/materialise step, or an external network the tools profile expects. That is an environment fault, not this package's contract; report BLOCKED with the full step output rather than weakening a `--dry-run` oracle to `ciu check` (which is shape-only and PASSES with zero governance declared, i.e. it can never prove this package)"
  - "any oracle would require an actual `ciu up` (no `--dry-run`), a `docker run`, or installing a systemd slice -- report BLOCKED. Every oracle here is deliberately render-only: the host is shared with production game servers, carried load average 9.22/8 cores with a live run-gate container and a full dstdns stack at carve time, and slice installation needs host root the daemon must not have (docs/plan-resource-governance.md D-G2)"
  - "the `[nyxloomd.runtime]` deletion in the TRACKED `nyxloomd/ciu.toml` is reverted by a `ciu render` run and the resulting diff is anything more than the comment-stripping measured at carve time -- report BLOCKED rather than committing a render artifact's incidental changes as part of this package"
  - "a named contract cannot be met as specified, or scope requires a forbidden file"
  - "E-008 checkpoint clause: arm at ~120k context or ~60 tool calls (whichever first), cut at the next coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster end; never on a red gate), repeat every ~40-55 calls, stop when <~40 calls remain. At the cut: write a continuation brief to nyxloom-trove/reports/nyxloom-P103-BRIEF.md plus a self-authored /compact-style retention prompt to nyxloom-trove/reports/nyxloom-P103-COMPACT.md (both authorised touches), commit, and return -- do not resume or fork past the cut yourself. Unlikely to be needed: this is a small config-only package."
---

# nyxloom-P103 — Declare `[governance]` on nyxloom's and pwmcp's ciu roots

Branch: `nyxloom-p103-ciu-governance` ·
worktree `/workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance`.
The nyxloom package is at `<worktree>/nyxloom`; the sibling `pwmcp` root is at
`<worktree>/pwmcp`. Both are inside the same monorepo checkout.

**contract_class: 2d** — every edit is a pinned, verbatim block against a named
anchor line, and the acceptance material is a prepared, carver-probed command
sequence. What is genuinely left to the implementer is prose composition for
three doc/backlog rewrites (Work items 4, 5, 6) and the REPORT.

## Why this package exists — and where the backlog entry is WRONG

NL-6's core claim is TRUE and this package fixes it: neither root declares
`[governance]`, so ciu injects no `cgroup_parent`, no `mem_limit`, and no blkio
caps into any container these roots start, on a host that also runs production
game servers.

**Live proof at carve time**, not a hypothetical — the ntfy container is up right
now:

```
$ docker inspect nyxloom-ntfy --format \
    'CgroupParent={{.HostConfig.CgroupParent}} Memory={{.HostConfig.Memory}} ...'
CgroupParent=nyxloom.slice Memory=0 MemorySwap=0 MemoryReservation=0 BlkioDeviceReadIOps=[]
```

Zero on every limit. And the placement string is **fictional**: `nyxloom.slice`
is installed nowhere (all 25 running containers on this host report
`dev-background.slice`; the `nyxloom-*.slice` units in `infra/slices/` are
uninstalled templates). systemd auto-creates a *transient, unbounded* slice for
a name it does not know, so this is the fail-open case
`docs/plan-resource-governance.md` already documents — a config that reads as
placed and produces no placement.

Three of NL-6's own statements are **false at `input_revision`** and Work item 5
must correct them rather than let them survive as a "fixed" entry:

| NL-6 claim | Measured reality |
|---|---|
| "the rendered compose carries no `cgroup_parent` on any service" | `ntfy/ciu.compose.yml:14` carries `cgroup_parent: nyxloom.slice`, emitted by ntfy's OWN template |
| "`ciu render` → the rendered compose" | `ciu render` renders **TOML only** (`ciu/src/ciu/deploy.py:1677-1693`); it never writes a compose file or an overlay |
| "`ciu check` passes the governance stage" (as an oracle) | that stage is shape-only (`deploy.py:2746-2755`) and **already passes** at `input_revision` with zero governance declared — a hollow oracle |

### The correction the backlog entry misses entirely

**ciu governance never overwrites a compose key the stack author already
emits.** `governance.py:1016-1038` skips any of its six injected keys already
present on that service block; `SPEC.md:2917-2923` states it normatively. So
adding the root table *as NL-6 proposes* leaves ntfy — the one service actually
running — still pinned to `nyxloom.slice`. That is why `scope.touch` is nine
files rather than the two NL-6 names: **every extra file is forced by a measured
fact, none by preference.** The carve-time tracer bullet below shows ntfy's
overlay receiving `mem_limit`/`blkio_config` but *no* `cgroup_parent` until the
template block is deleted.

## Reverse-dependency sweep (tabulated, `git grep`, all tracked file types)

### `[governance]` across both roots — the conflict check

`git grep -n -i governance -- nyxloom pwmcp` at `input_revision`, restricted to
declarations (prose hits in `docs/`, `README`s, `nyxloom-trove/` narrative and
`assay.toml`'s comment are non-declarative and listed as such):

| file | line | disposition |
|---|---|---|
| `nyxloom/ciu.global.defaults.toml.j2` | — | **no `[governance]` table** → Work item 1 adds it |
| `pwmcp/ciu.global.defaults.toml.j2` | — | **no `[governance]` table** → Work item 1 adds it |
| `pwmcp/ciu.global.toml.j2` | — | no table; HIGHER precedence, cannot suppress (per-key deep_merge). VERIFY-ONLY, in `forbid` |
| `nyxloom/{ntfy,nyxloomd,pwmcp-instance}/ciu.defaults.toml.j2` | — | no table → per-stack override added to `pwmcp-instance` only (Work item 2) |
| `nyxloom/nyxloomd/ciu.toml` | — | no table |
| `nyxloom/pwmcp-instance/ciu.compose.yml.j2`, `pwmcp/ciu.compose.yml.j2` | — | no author-set governance key → inject cleanly, no edit needed |
| `nyxloom/docs/plan-resource-governance.md` | 139, 147, 168, 213-231, 273, 283-296, 357, 370 | PROSE/design narrative. Line 273's `[governance]` is a *proposed gate-argv* design (D-G3), not a live table. Work item 4 appends a correction; no other edit |
| `nyxloom/assay.toml` | 45 | comment, mentions ciu's cgroup-governance *tests*. Unrelated |
| `nyxloom/infra/slices/*.slice`, `infra/agent-cli/*` | various | uninstalled unit templates + narrative (D-G1/D-G5). In `forbid` |
| `nyxloom/ntfy/{README.md,server.yml}` | 61, 2 | ntfy's OWN auth governance, not cgroups. In `forbid` |
| `nyxloom/src/nyxloom/gate_scaffold.py` | 52 | comment referencing the plan doc. No code dependency |
| `pwmcp/{README.md,containers/pwmcp/ksm-optin.c}` | 171, 7 | prose + the image's self-contained KSM opt-in. Explains why this package sets **no** `ksm_optin` |

**Conclusion: zero `[governance]` tables exist under either root today**, so this
package creates rather than merges, and no key-path conflict is possible.

### `cgroup_parent` / `nyxloom.slice` — every occurrence

`git grep -n -E 'cgroup_parent|nyxloom\.slice' -- nyxloom pwmcp`, config/compose
files only:

| file | line | current | disposition |
|---|---|---|---|
| `nyxloom/ntfy/ciu.compose.yml.j2` | 13-15 | `{% if ntfy.runtime.cgroup_parent %}` block | **DELETE** (Work item 3a) — the live author-set key that defeats injection |
| `nyxloom/ntfy/docker-compose.yml` | 14 | `    cgroup_parent: nyxloom.slice` | **DELETE** (3b) — pre-rendered plain-compose sibling, no ciu command renders it |
| `nyxloom/ntfy/ciu.defaults.toml.j2` | 24-25 | comment + `cgroup_parent = "nyxloom.slice"` | **DELETE** (3c) — sole consumer was 3a's Jinja reference |
| `nyxloom/nyxloomd/ciu.defaults.toml.j2` | 24-25 | comment + same key | **DELETE** (3d) — already dead: `nyxloomd/ciu.compose.yml.j2` has ZERO `cgroup_parent` hits |
| `nyxloom/nyxloomd/ciu.toml` | 8-9 | `[nyxloomd.runtime]` / same key | **DELETE key line** (3e) — tracked render output, see scope note |
| `nyxloom/pwmcp-instance/*`, `pwmcp/*` | — | none | no edit; inject cleanly |
| `nyxloom/docs/plan-resource-governance.md`, `nyxloom/infra/slices/` | various | narrative + uninstalled units | RETAINED deliberately (O2's exclusion) |

**`[<stack>.runtime]` is not a ciu table at all.** There is no `[runtime]` in
`ciu/docs/SPEC.md`; ciu reads the key never and warns never (S15.13's unknown-key
WARN covers the `[governance]` table only, `governance.py:384-395`). ntfy's copy
was live *solely* through its own Jinja template; nyxloomd's was inert.

## Memory sizing — the decision and its reasoning

**Root level: `mem_limit = "2g"` for nyxloom, `"4g"` for pwmcp. Per-stack: one
override, `[pwmcp.governance] mem_limit = "4g"` for `pwmcp-instance`.**

Reasoning, in the order the evidence forced it:

1. **No measured data exists** for `nyxloomd` or `ntfy` — neither is sized in any
   nyxloom doc, and `docker stats` history is unavailable (nyxloomd is not
   running). `docs/plan-resource-governance.md` **D-G4** rules directly on this:
   *"absolute limits are HOST-owned and live in the slice units; the project
   declares nothing absolute"*, and per-container values are *"an optional
   refinement for a project that has measured itself"*. So inventing per-service
   numbers would violate nyxloom's own standing decision.
2. **A single root-level ceiling is the estate pattern.** dstdns's own root is
   root-level, not per-stack (`dstdns/ciu.global.toml.j2:110-124`, `mem_limit =
   "2g"`), and its comment records why a generous ceiling is free: the parent
   slice's `MemoryMax` *"is the real host-safety backstop regardless of any
   per-container value (cgroup v2 accounts child usage against the parent
   slice)"*. `2g` matches what every dstdns container is verifiably running with
   today (`docker inspect` → `Memory=2147483648`).
3. **The one exception is measured, not invented.** Both pwmcp services declare
   `shm_size = "2gb"` (`nyxloom/pwmcp-instance/ciu.defaults.toml.j2:34`,
   `pwmcp/ciu.defaults.toml.j2:145`, each commented *"Chromium needs real shared
   memory"*). cgroup v2 charges `/dev/shm` pages to the container's own memory
   cgroup, so a 2g cap would be consumed by the shm allocation alone, leaving
   the browser no headroom. `4g` gives the declared shm plus an equal working
   set.
   **This decision is robust even if that accounting claim is wrong:** by (2), a
   more generous per-container ceiling costs nothing in host safety, because the
   slice ceiling is the real backstop. Reviewers should still challenge the
   claim; nothing in the package depends on it being right.
4. **Everything else is left at ciu's default, deliberately.** `mem_swap_limit`
   (17g), `mem_reservation` (256m), `write_iops` (400), `read_iops` (200 fallback)
   are ciu's `GOVERNANCE_DEFAULTS` and are what dstdns runs with in practice.
   `ksm_optin` is **omitted** (`= off`): pwmcp's image already performs its own
   KSM opt-in *"without depending on any consumer's ciu governance overlay"*
   (`pwmcp/containers/pwmcp/ksm-optin.c:7`), and `"builtin"` would make `ciu
   render` require Docker to compile a shim (`SPEC.md:3197-3200`) — new machinery
   this package does not need.
5. **`device = "/dev/vda"` is pinned, not autodetected.** `findmnt --target
   /var/lib/docker` returns EMPTY in this devcontainer (measured), so ciu's
   autodetect silently fails and blkio caps would be skipped entirely
   (`governance.py:1040-1050`, the `if device:` guard). `/dev/vda` is the real
   host disk (`lsblk`: 1T) and is exactly what dstdns's containers run with
   (`BlkioDeviceReadIOps=[/dev/vda:200]`).
6. **`cgroup_parent = "dev-background.slice"`, explicit.** It is the only slice
   verified to exist and be in use. It is NOT left `""`: `$CGROUP_PARENT_DEV_BACKGROUND`
   is unset in this repo's `ciu.env` (measured), and ciu has **no hardcoded slice
   default** — unset + unresolvable is a hard `[S15.2]` error (`governance.py:288-312`).

> **Note for the implementer — `docs/plan-resource-governance.md` contains a
> stale warning that argues against this package.** Its D-G0a block (and its
> duplicate at lines 221-231) claims `cgroup_parent` *"defaults to
> `besteffort.slice`"*. That is **false for ciu 7.11.0**:
> `GOVERNANCE_DEFAULTS["cgroup_parent"] = ""` (`governance.py:67`) and there is
> no hardcoded fallback (`SPEC.md:2862-2865`; `besteffort.slice` survives only in
> ciu's own test fixtures). Because this package sets the key explicitly the
> point is moot either way — but Work item 4 corrects the prose so the next
> reader is not talked out of the fix by a hazard that no longer exists.

## Context to read first

Read in this order; stop when the contract is clear.

1. `nyxloom-trove/backlog/NL-6-*.md` — the whole entry (the premise, and the
   three claims corrected above).
2. This handoff's **Probe log** below — the carver already ran every oracle
   command; do not re-derive the mechanism.
3. `nyxloom/ciu.global.defaults.toml.j2` (all 39 lines) and
   `pwmcp/ciu.global.defaults.toml.j2` (all 30 lines). **Read pwmcp's header
   comment, lines 1-9, in full** — it is BUILD-TEST ONLY and explicitly NOT
   vendored to consumers (*"Shipping this to consumers is what created the
   standalone-root anti-pattern"*). The `[governance]` table added there governs
   the maintainer's local build-test stack only and must not be presented as a
   template consumers copy.
4. `/workspaces/dstdns/ciu.global.toml.j2` lines 95-124 — the reference shape,
   READ-ONLY (forbidden path).
5. `nyxloom/docs/plan-resource-governance.md` — **D-G4** (lines ~286-302, the
   sizing rule) and **D-G0a** (lines 143-157, the stale claim Work item 4
   corrects). Skip the rest.
6. `nyxloom/ntfy/ciu.compose.yml.j2` lines 1-20 and `nyxloom/ntfy/docker-compose.yml`
   lines 1-20 — the author-set key and its sync obligation.

## Implementation packet (normative)

### Work

**1. Add the root `[governance]` table to both roots.**

(a) In `nyxloom/ciu.global.defaults.toml.j2`, insert VERBATIM immediately before
the existing `[ciu]` table (line 33), leaving one blank line after the block:

```toml
# Resource governance (ciu S15.10). Copied per root; RETIRED by ciu v8's
# `[ciu] inherit` (SPEC-V8 draft.5 S3.1.5), which lets one vbpub-root file
# carry this for every subproject. v7 roots are islands -- nothing inherits
# between vbpub/nyxloom, vbpub/pwmcp and dstdns -- so until then each root
# declares its own. See nyxloom-P103 / backlog NL-6.
#
# cgroup_parent is EXPLICIT, never "": ciu has no hardcoded slice default, so
# "" falls back to $CGROUP_PARENT_DEV_BACKGROUND and raises [S15.2] when that
# is unset (as it is in this repo's ciu.env). dev-background.slice is the
# installed, in-use host slice; nyxloom.slice was never installed anywhere and
# a name systemd does not know fails OPEN to an unbounded transient cgroup.
# The nyxloom-*.slice units in infra/slices/ (D-G1) need an operator install
# with host root before they can be named here.
#
# device is EXPLICIT for the same fail-open reason: ciu's autodetect
# (`findmnt --target /var/lib/docker`) returns nothing under
# docker-outside-of-docker, and blkio caps would then be skipped silently.
#
# mem_limit is a first-pass ceiling only. Per D-G4
# (docs/plan-resource-governance.md) absolute limits are HOST-owned and live
# in the slice unit; the slice's MemoryMax is the real backstop, so a generous
# per-container value costs nothing. Tighten once docker-stats data exists.
[governance]
enabled = true
cgroup_parent = "dev-background.slice"
mem_limit = "2g"
device = "/dev/vda"
```

(b) In `pwmcp/ciu.global.defaults.toml.j2`, insert the same block before its
`[ciu]` table (line 24), with `mem_limit = "4g"` and this extra first line
inside the comment header:

```toml
# BUILD-TEST ONLY, like the rest of this file (see the header): this governs
# the maintainer's local `ciu up --dir .` stack. Consumers run pwmcp as a
# sub-stack of THEIR ciu root and inherit THEIR governance -- verified: the
# dstdns-hosted pwmcp container runs in dev-background.slice with a 2g cap
# from dstdns's own root. Do NOT vendor this table.
# mem_limit is 4g, not the estate's 2g: this stack declares shm_size = "2gb"
# (ciu.defaults.toml.j2:145) and cgroup v2 charges /dev/shm to the container's
# own memory cgroup, so 2g would be spent on shm alone.
```

**2. Per-stack override for nyxloom's browser stack.** In
`nyxloom/pwmcp-instance/ciu.defaults.toml.j2`, add:

```toml
# nyxloom-P103: 4g, not the root's 2g -- shm_size = "2gb" above is charged to
# this container's own memory cgroup under cgroup v2. Shallow-merged over the
# root [governance] (ciu S15.10), so the remaining governance keys still
# inherit from it.
[pwmcp.governance]
mem_limit = "4g"
```

The table's root key is **`pwmcp`**, matching the stack's single non-reserved
top-level key — `[pwmcp-instance.governance]` would be silently ignored.

**3. Retire `nyxloom.slice`** — five deletions, exactly as tabulated in the
sweep above: (a) `ntfy/ciu.compose.yml.j2` lines 13-15; (b)
`ntfy/docker-compose.yml` line 14; (c) `ntfy/ciu.defaults.toml.j2` lines 24-25;
(d) `nyxloomd/ciu.defaults.toml.j2` lines 24-25; (e) `nyxloomd/ciu.toml` line 9
(and its now-empty-of-placement `[nyxloomd.runtime]` header stays — it still
carries `run_as_uid`/`run_as_gid`/`docker_gid`).

**4. Correct the stale `besteffort.slice` claim** in
`docs/plan-resource-governance.md`. Append (do NOT rewrite the narrative) a
short dated note after BOTH the D-G0a block and its duplicate at lines 221-231,
stating: as of ciu 7.11.0 `cgroup_parent` has no hardcoded default —
`GOVERNANCE_DEFAULTS["cgroup_parent"] = ""` and an unresolvable value is a hard
`[S15.2]` error, not a silent `besteffort.slice`; nyxloom-P103 sets the key
explicitly regardless. Keep the `device` half of both blocks — it is still true
and is why this package pins `/dev/vda`.

**5. Close NL-6.** (a) Replace its "Observed mechanism and reproduction" and
"Oracles" sections with corrected text covering the three false claims tabulated
above, plus the author-always-wins finding. (b) Set status via the CLI, never by
hand:
`python3 exec-nyxloom.py backlog set-status NL-6 fixed --reason "<one line>"`.
(c) Regenerate the index: `python3 exec-nyxloom.py backlog index`, then paste the
`nyxloom lint` BLG-findings read into the REPORT proving INDEX.md byte-equals a
fresh regeneration.

**6. File one new backlog entry** (do not fix it here): `pwmcp/` carries BOTH a
tracked `ciu.global.defaults.toml.j2` and a tracked `ciu.global.toml.j2` that are
near-duplicates, with the latter at higher precedence and lacking the
BUILD-TEST-ONLY header. Two tracked global layers in one root is a standing
shadowing hazard. Record the ID in the REPORT.

**7. File a second backlog entry** (do not fix it here) against nyxloom's own
lint: **L7 cannot express a `scope.touch` path in a sibling project of the same
monorepo checkout.** A `../`-prefixed or absolute path is a hard error
(`src/nyxloom/lint.py:1017-1024`), while a bare sibling path like
`pwmcp/ciu.global.defaults.toml.j2` is silently ACCEPTED for the wrong reason —
the create-exemption at `lint.py:1044-1045` treats it as a file to be created
under `cfg.root`, so lint validates a path that does not exist and never sees the
real file. This package is the live case: it must edit a second ciu root that no
nyxloom project owns. Note both halves of the defect (the false negative and the
false positive) in the entry. Record the ID in the REPORT.

### Degrees of freedom

Comment wording in Work items 4, 5 and 6, and REPORT/LOG prose. The TOML blocks
in Work items 1-2, the five deletions in Work item 3, and every oracle command
are fixed.

## Probe log (carver-run, ciu 7.11.0, `input_revision` + the proposed edits)

Run at carve time, then fully reverted (`git status` clean, generated artifacts
removed, the `--dry-run`-created docker network disconnected and deleted).

- Baseline, no governance: `nyxloomd` overlay **absent entirely**; `ntfy` overlay
  carried only its configfile bind; `ntfy/ciu.compose.yml:14` =
  `cgroup_parent: nyxloom.slice`. `pwmcp` compose: no governance keys at all.
- `ciu check --profile default` exits **0 with `governance: pass`** at baseline —
  confirming it is a hollow oracle. Without `--profile` it reports *"0 stack
  config(s) rendered"* and is vacuous.
- With the root table added, `ciu up --profile default --dry-run` logged, per stack:
  `[GOVERNANCE] enabled — cgroup_parent=dev-background.slice; mem_limit=2g;
  mem_swap_limit=17g; mem_reservation=256m; read_iops=200 (fallback default);
  write_iops=400; device=/dev/vda (explicit); ksm_optin=off;
  services_injected=1 exempt=0`.
- **The decisive result:** `nyxloomd`'s overlay gained
  `cgroup_parent: dev-background.slice`; `ntfy`'s gained `mem_limit`/
  `memswap_limit`/`mem_reservation`/`blkio_config` but **no `cgroup_parent`** —
  because its own template already set one. After Work item 3a's deletion,
  ntfy's overlay carries `cgroup_parent: dev-background.slice`.
- `pwmcp` (`ciu up --dir . --dry-run`) and `pwmcp-instance`
  (`ciu up --profile tools --dry-run`) both injected the full six-key fragment
  cleanly.
- ciu's slice-existence preflight **SKIPPED**: `[S15.G9-1] systemctl is present
  but systemd is not PID 1 in this mount namespace`. ciu will not catch a bad
  slice name from inside this devcontainer — hence O1 pins the literal.

## Environment setup

No stack is brought up. Every oracle is `--dry-run` only.

```bash
source /workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance/ciu.env
cd /workspaces/vbpub/.worktrees/nyxloom-p103-ciu-governance/nyxloom
ciu up --profile default --dry-run --define-root "$PWD"     # O1, O2
ciu up --profile tools   --dry-run --define-root "$PWD"     # O4
cd ../pwmcp && ciu up --dir . --dry-run --define-root "$PWD" # O3
```

**Mandatory teardown and hygiene** (an oracle run that skips these is incomplete):

1. `ciu up --dir .` on pwmcp CREATES a docker network and connects the
   devcontainer (`auto_connect_network = true`). Remove it:
   `docker network disconnect <net> dstdns-devcontainer-vb && docker network rm <net>`.
   Confirm with `docker network ls`.
2. `ciu render`/`ciu up` REWRITE the tracked `nyxloomd/ciu.toml`. After every
   run, `git diff -- nyxloom/nyxloomd/ciu.toml` and restore anything that is not
   Work item 3e's intended deletion (`git checkout --` it, then re-apply 3e).
3. Delete generated artifacts before committing: `<stack>/.ciu/`,
   `<stack>/ciu.compose.yml`, `<root>/ciu.global.toml`, rendered `ciu.toml`s.
   `git status` must be clean of them.
4. **Host discipline:** this machine is shared with production game servers and
   carried load 9.22/8 cores at carve time. `--dry-run` never calls Docker to
   start anything, so it is safe — but confirm with `docker ps` that you have
   started nothing, and never escalate an oracle to a real `ciu up`.

## Gate argv (verbatim) — and why the gate cannot prove this package

**This package changes ZERO Python.** The registered gate is the only one nyxloom
has and it is a pytest/coverage lane:

```bash
cd {worktree}/nyxloom && ./run-gate.py --worktree {worktree} tester-unified
```

Run it as a **regression check** — it must stay green, proving no Python was
disturbed — but understand what it does *not* do: `asserts = ["tests-pass",
"changed-line-coverage", "canary-verified"]` (`nyxloom-trove/nyxloom.toml:91`).
With no changed Python lines, `changed-line-coverage` is **vacuous**, and no
assert inspects a TOML/Jinja file. **A green tester-unified run is not evidence
for any oracle in this package.** Do not go hunting for a pytest gate that
covers governance config — there is none, by design.

Every oracle O1-O5 is therefore run **BY HAND** via the Environment setup
commands, and its verbatim output pasted into
`nyxloom-trove/reports/nyxloom-P103-REPORT.md`. That file is the proof of this
package; the gate is only the no-regression backstop. This mirrors P101's
precedent for a controlled break the gate cannot assert.

Lint the handoff itself (also not part of the gate):

```bash
cd {worktree}/nyxloom && python3 exec-nyxloom.py lint \
  nyxloom-trove/handoffs/nyxloom-P103-ciu-governance-standalone-roots.md
```

### Expected lint output

`nyxloom lint` on this handoff exits **0** with exactly these warnings and no
errors. Each was verified once at carve time; anything else is drift and should
be investigated, not waved through.

| warning | verdict |
|---|---|
| `L10 handoff size ~11.4k tokens` | over the 10k warn floor, under the 18k error floor. Accepted: the size is the tabulated sweeps and the probe log, which is what stops the implementer re-deriving the mechanism |
| `L13 ... '.ciu/ciu.compose.overlay.yml'`, `'ntfy/.ciu/...'`, `'nyxloomd/.ciu/...'`, `'pwmcp-instance/.ciu/...'` (4) | FALSE POSITIVE. These are ciu's **generated, gitignored** artifacts — the thing the oracles *read*, never edit. Putting a build output in `scope.touch` would be wrong |
| `L13 ... 'ntfy/ciu.compose.yml'` | FALSE POSITIVE, same class: the rendered compose, gitignored |
| `L13 ... 'ciu/src/ciu/composefile.py'`, `'pwmcp/ciu.global.toml.j2'`, `'infra/slices'` | FALSE POSITIVE: read-only citations and forbidden paths, deliberately not in `scope.touch` |
| `L7 cross-repo reference '/workspaces/dstdns'` (x2) | expected — the read-only reference for the `[governance]` shape |
| `L7 relative-up path '../pwmcp'` (x2) | expected — the sibling root this package's second half edits; see the `scope.touch` path caveat and Work item 7 |

## Scope / forbid

See frontmatter — each entry is annotated with why.

**Forbidden paths that frontmatter cannot carry.** L7 hard-errors on any `../` or
absolute entry, so these four are binding but live here in prose instead. Treat a
required edit to any of them as a BLOCKED trigger, exactly as if it were listed:

| forbidden | why |
|---|---|
| the sibling `ciu` project | ciu v7 is MAINTENANCE-ONLY (2026-09-03 decision; v8 is the `ciu8` subproject). NL-6 is a CONSUMER config gap, not a ciu defect — the S15 mechanism behaved exactly as specified throughout the probe. Do not "fix" ciu to make an oracle easier |
| the sibling `ciu8` project | v8's `[ciu] inherit` is what RETIRES this package's per-root duplication. Not this package's to build or anticipate |
| the `dstdns` repo | read-only reference for the `[governance]` shape only. A different repo entirely; never edit |
| `pwmcp`'s `ciu.global.toml.j2` | VERIFY-ONLY. A tracked near-duplicate at HIGHER precedence that declares no `[governance]`; per-key deep_merge means its silence cannot suppress the defaults-layer table (empirically confirmed). Confirm it still has no `[governance]` at dispatch (see `escalate_if`); the duplication is Work item 6's filing, not this package's edit |

Two further points worth repeating:

- **ciu v7 is maintenance-only.** NL-6 is a consumer config gap, not a ciu
  defect: the S15 mechanism behaved exactly as specified throughout the probe.
  Editing `ciu/` or `ciu8/` to make an oracle easier is a BLOCKED condition.
- **`infra/slices/` stays untouched.** Installing `nyxloom-*.slice` needs host
  root (D-G2) and is a separate operator-run package. This package deliberately
  targets the already-installed `dev-background.slice`.

## BLOCKED rule

BLOCKED: if a named contract cannot be met as specified, or scope requires a
forbidden file, STOP — write `BLOCKED: <reason>` to
`nyxloom-trove/reports/nyxloom-P103-LOG.md`, commit, and exit. Do NOT improvise a
workaround. In particular: do not weaken a `--dry-run` oracle to `ciu check`, do
not add a service to `exempt_services` to make an oracle pass, do not name a
slice that is not installed, and do not bring up a real stack. A BLOCKED exit is
a success mode.
