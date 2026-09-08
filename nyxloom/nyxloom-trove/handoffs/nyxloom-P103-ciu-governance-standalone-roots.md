---
schema_version: 1
id: nyxloom-P103-ciu-governance-standalone-roots
project: nyxloom
title: "Declare [governance] on nyxloom's and pwmcp's ciu roots; retire the fictional nyxloom.slice"
tier: luna-high
input_revision: "b40edff3"
depends_on: []
session: fresh
source:
  kind: roadmap
  ref: nyxloom-trove/backlog/NL-6-nyxloom-s-and-pwmcp-s-ciu-roots-declare-no-governance-nyxloomd.md
scope:
  touch:
    - "ciu.global.defaults.toml.j2"            # THE fix, half 1. Insert Work item 1's pinned `[governance]` block immediately BEFORE the existing `[ciu]` table at line 33. Measured at input_revision: this file has NO `[governance]` table (verified `git grep -n governance -- nyxloom pwmcp`, tabulated below). The defaults layer is the correct home, NOT the gitignored site layer: `config_model.deep_merge` (ciu/src/ciu/config_model.py:558-571) iterates `override.items()` only, so a higher layer that omits the table cannot suppress it -- verified empirically on pwmcp, whose tracked higher-precedence `ciu.global.toml.j2` carries no `[governance]` and did NOT suppress the defaults-layer table
    - "pwmcp/ciu.global.defaults.toml.j2"        # THE fix, half 2 -- Work item 1(b). READ THE PATH CAVEAT: this is relative to the WORKTREE ROOT, not to the nyxloom project root that every other entry here is relative to. pwmcp/ is a SIBLING project in the same monorepo checkout and is NOT a registered nyxloom project (it has no nyxloom-trove/), so there is no other trove this half could be carved in, and NL-6 covers both roots from nyxloom's backlog. nyxloom lint CANNOT validate this entry: L7 hard-errors on any "../" or absolute path (src/nyxloom/lint.py:1017-1024), and this bare form is accepted only because L7 exempts a scope.touch path that does not exist under cfg.root as "a file to be created" (lint.py:1044-1045) -- it resolves to the nonexistent nyxloom/pwmcp/..., NOT to the real file. This is a known, deliberately-accepted lint blind spot, filed by Work item 7; it is recorded here rather than quietly relied upon. The implementer edits <worktree>/pwmcp/ciu.global.defaults.toml.j2 and proves it via O3, which reads the real overlay
    - "pwmcp-instance/ciu.defaults.toml.j2"    # per-stack memory override, Work item 2. Add `[pwmcp.governance] mem_limit = "4g"` (the stack's root key is `pwmcp`, not `pwmcp-instance` -- confirmed: the rendered overlay's service key is `pwmcp`). Reason is measured, not invented: line 34 declares `shm_size = "2gb"` and cgroup v2 charges tmpfs pages to the memcg that faults them in. Note the precise claim: `shm_size` is a CEILING, not a preallocation, so 2g is not consumed at start -- the risk is a Chromium renderer filling /dev/shm under load against a 2g cap. See "Memory sizing" for the full derivation and the measured 8G tier backstop that makes a generous per-container value free
    - "ntfy/ciu.compose.yml.j2"                # THE load-bearing correction the backlog entry MISSES. Delete lines 13-15 (`{% if ntfy.runtime.cgroup_parent %}` / `    cgroup_parent: {{ ntfy.runtime.cgroup_parent }}` / `{% endif %}`). ciu governance NEVER overwrites a compose key the stack author already emits (ciu/src/ciu/governance.py:1016-1038, `if "cgroup_parent" not in author_keys`; SPEC.md:2917-2923 "the stack author's rendered compose always wins"), so leaving this block makes ntfy the ONE service that keeps `nyxloom.slice` after the fix. MEASURED, not inferred -- the tracer bullet in "Probe log" shows ntfy's overlay receiving mem_limit/blkio but NO cgroup_parent until this block is gone
    - "ntfy/docker-compose.yml"                # line 14, `    cgroup_parent: nyxloom.slice`. REPLACE it -- do NOT merely delete it. This is the pre-rendered plain-compose sibling AND it is the file the CURRENTLY RUNNING ntfy container was actually deployed from (measured: `docker inspect nyxloom-ntfy` reports compose project `ntfy`, `config_files=/workspaces/vbpub/nyxloom/ntfy/docker-compose.yml` -- a plain `docker compose -f` deploy, never a ciu one). ciu's governance overlay is a second `-f` that this path never loads, so the caps must be INLINE here. Deleting line 14 without replacement makes the live path strictly worse -- unconfined instead of a transient slice. Work item 3b pins the replacement block; O2b asserts its post-state
    - "ntfy/README.md"                         # line 67 states ntfy's hardening posture as "`nyxloom.slice` cgroup". That is the SAME keep-the-documented-posture-in-sync obligation that puts docker-compose.yml in scope, and it is about the cgroup slice -- NOT about ntfy's auth surface (the "Governance notes" heading at line 61, which is deny-all auth and is deliberately untouched). Work item 3f pins the replacement wording. Listed so the implementer is not routed into a BLOCKED exit on an unlisted path
    - "ntfy/ciu.defaults.toml.j2"              # line 24-25 (the comment `# systemd slice for the nyxloom service family.` and `cgroup_parent = "nyxloom.slice"`). Delete both. After the template edit above nothing reads this key: `[<stack>.runtime]` is NOT a ciu-recognised table (no `[runtime]` anywhere in ciu/docs/SPEC.md; ciu reads it never, warns never -- S15.13's unknown-key WARN covers the `[governance]` table ONLY, ciu/src/ciu/governance.py:384-395), so its sole consumer was the Jinja reference just deleted
    - "nyxloomd/ciu.defaults.toml.j2"          # line 24-25, same two lines (comment reads `(shared with ntfy)`). Delete both. Already dead at input_revision with NO template consumer at all: `grep -n cgroup_parent nyxloomd/ciu.compose.yml.j2` returns ZERO hits, which is exactly the "dead config key that looks like placement and produces none" incident docs/plan-resource-governance.md:130-141 records
    - "nyxloomd/ciu.toml"                      # line 8-9, `[nyxloomd.runtime]` / `cgroup_parent = "nyxloom.slice"`. Delete the key line. NOTE this file is TRACKED although `.gitignore:202` lists `**/ciu.toml` (tracked files ignore .gitignore) AND it is a `ciu render` OUTPUT -- `ciu render --profile default` rewrites it. Verified the rewrite is comment-only at input_revision (values identical), but the implementer MUST `git diff` it after any render and must not commit a render's comment-stripping as part of this package. See "Environment setup" for the exact restore step
    - "docs/plan-resource-governance.md"       # Work item 4 ONLY: append the pinned correction note after the D-G0a block (lines 143-157) and after the duplicate "Two ciu defaults that would bite nyxloom" block (lines 221-231). BOTH blocks assert `cgroup_parent` defaults to `besteffort.slice`; that is FALSE for ciu 7.11.0 -- `GOVERNANCE_DEFAULTS["cgroup_parent"] = ""` (ciu/src/ciu/governance.py:67) and `resolve_cgroup_parent` RAISES when neither the key nor $CGROUP_PARENT_DEV_BACKGROUND resolves (governance.py:288-312; SPEC.md:2862-2865 "No hardcoded fallback"). Do NOT rewrite the historical narrative -- append only. Listed in scope precisely so Work item 4 cannot route the implementer into a BLOCKED exit on an unlisted path
    - "nyxloom-trove/backlog/NL-6-nyxloom-s-and-pwmcp-s-ciu-roots-declare-no-governance-nyxloomd.md"  # status open -> fixed via the CLI, AND replace the "Observed mechanism"/"Oracles" sections with Work item 5's PINNED text. Three of the entry's own claims are FALSE at input_revision and must not survive as a fixed entry: (a) "the rendered compose carries no cgroup_parent on any service" -- ntfy's carries `nyxloom.slice`; (b) "`ciu render` -> the rendered compose" -- `ciu render` renders TOML ONLY, never the compose (ciu/src/ciu/deploy.py:1677-1693); (c) "`ciu check` passes the governance stage" as an oracle -- that stage is shape-only (deploy.py:2746-2755) and PASSES at input_revision with zero governance declared, i.e. it is a hollow oracle
    - "nyxloom-trove/backlog/"                 # DIRECTORY sweep, for the THREE new entries Work items 6, 7 and 8 create via `exec-nyxloom.py backlog new` (pwmcp two-tracked-global-layers; lint L7 cannot express a sibling-project scope path; extend doctor's cgroup-slice-missing check to ciu-declared placement). Their filenames are allocated by the CLI and cannot be pinned at carve time, which is why this is a directory entry rather than three paths. Only ADDING entries here is authorised -- no other backlog file may be edited except NL-6, listed separately above
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
      COMMITTED CONTENT, checked on a FRESH worktree of this branch before anything is run.
      On BOTH roots the declaration is literal and host-independent:
      `grep -qx 'cgroup_parent = "dev-background.slice"' nyxloom/ciu.global.defaults.toml.j2`
      and the same on `pwmcp/ciu.global.defaults.toml.j2`; likewise
      `grep -qx 'device = "/dev/vda"'` on both. THEN, from `nyxloom/`,
      `ciu up --profile default --dry-run --define-root "$PWD"` exits 0 and for BOTH stacks
      `grep -c '^    cgroup_parent: dev-background\.slice$' <stack>/.ciu/ciu.compose.overlay.yml`
      prints `1` (stacks: `ntfy`, `nyxloomd`), and the run's `[GOVERNANCE]` log line reports
      `device=/dev/vda (explicit)` and `services_injected=1 exempt=0` for each.
      The overlay is the ONLY correct place to look: ciu writes governance to
      `<stack>/.ciu/ciu.compose.overlay.yml` (ciu/src/ciu/composefile.py:1407-1413) and merges
      it via a second `-f` (composefile.py:1598-1613), deliberately leaving the rendered
      `ciu.compose.yml` byte-exact author output (SPEC.md:2930-2935).
    negative: >-
      The committed-literal halves are load-bearing and the overlay check alone CANNOT replace
      them. `cgroup_parent = ""` produces a BYTE-IDENTICAL overlay line on this machine and
      passes every overlay-only assertion: `resolve_cgroup_parent` falls back to
      `$CGROUP_PARENT_DEV_BACKGROUND` (ciu/src/ciu/governance.py:288-312), and that variable IS
      set in the process environment here -- `env | grep CGROUP` prints
      `CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`, injected by devcontainer.json's
      containerEnv (SPEC.md:2862-2863). (Do NOT re-measure this with `grep -i cgroup ciu.env`:
      the file has no such key, and reading the FILE instead of the PROCESS environment is the
      exact mistake an earlier revision of this carve made.) `""` is rejected not because it
      fails here but because it makes the committed config host-dependent: it hard-errors
      `[S15.2]` on any host or CI without that ambient variable. Likewise omitting
      `device = "/dev/vda"` passes an overlay `cgroup_parent` check while silently dropping
      every blkio cap -- ciu's autodetect (`findmnt --target /var/lib/docker`) returns nothing
      under docker-outside-of-docker, and the `if device:` guard (governance.py:1042) then skips
      `blkio_config` entirely. `exempt=0` catches an implementation that "passes" by listing the
      service in `exempt_services`. Note ciu CANNOT catch a fictional slice here: its
      slice-existence preflight SKIPS in this devcontainer (`[S15.G9-1] systemctl is present but
      systemd is not PID 1`), so a bad name fails OPEN to an unbounded transient cgroup --
      `dev-background.slice` is pinned because it is the one slice verified installed
      (modern-debian-tools-python-debug/host-setup/units/dev-background.slice.in) and in use by
      every running container on this host.
    gate: tester-unified
  - id: O2
    observable: >-
      ntfy is governed by ciu ALONE, not by its own template. Three assertions, all required.
      (i) The TEMPLATE no longer emits a placement key at all:
      `! grep -q cgroup_parent nyxloom/ntfy/ciu.compose.yml.j2`, and for symmetry
      `! grep -q cgroup_parent nyxloom/ntfy/ciu.defaults.toml.j2`,
      `! grep -q cgroup_parent nyxloom/nyxloomd/ciu.defaults.toml.j2` and
      `! grep -q cgroup_parent nyxloom/nyxloomd/ciu.toml`.
      (ii) The RENDERED compose is clean: from the O1 dry-run,
      `grep -c cgroup_parent nyxloom/ntfy/ciu.compose.yml` prints `0`.
      (iii) The fictional slice literal is gone from the config/compose files of both roots.
      RUN THIS FROM THE WORKTREE ROOT -- the globs are repo-root-relative and silently match
      NOTHING if run from `nyxloom/`:
      `cd <worktree> && git grep -l 'nyxloom\.slice' -- 'nyxloom/**/*.toml' 'nyxloom/**/*.j2' 'nyxloom/**/*.yml' 'pwmcp/**/*.toml' 'pwmcp/**/*.j2' 'pwmcp/**/*.yml'`
      returns NOTHING. Measured at `input_revision` from the worktree root, that same command
      returns exactly FOUR files -- `ntfy/ciu.defaults.toml.j2`, `ntfy/docker-compose.yml`,
      `nyxloomd/ciu.defaults.toml.j2`, `nyxloomd/ciu.toml` -- so it is a real before/after
      discriminator. Prose (`docs/`, `infra/slices/`, the trove) retains the literal deliberately
      and is outside these globs.
    negative: >-
      Do NOT write this oracle as an unbounded `git grep -c 'nyxloom\.slice' -- nyxloom/` with a
      prose exclusion list: that form is UNSATISFIABLE, because this handoff is itself a tracked
      file containing the literal ~20 times, and `ntfy/README.md` and `infra/slices/` match too.
      An earlier revision of this carve shipped exactly that unsatisfiable oracle. Bounding the
      scan to config/compose file TYPES is the dimension that makes it checkable; the exclusion
      is by file type, not by a hand-maintained path list. Assertion (i) is NOT redundant with
      (iii) and must not be dropped: `ntfy/ciu.compose.yml.j2:14` holds
      `{{ ntfy.runtime.cgroup_parent }}` -- the Jinja EXPRESSION, never the literal string
      `nyxloom.slice` -- so it is invisible to (iii)'s glob. Without (i), an implementation that
      deletes the four TOML keys but leaves the Jinja block passes the WHOLE of O2: the rendered
      compose has no `cgroup_parent` (an undefined Jinja name is falsy in the `{% if %}`, so (ii)
      prints 0) and the literal is gone everywhere (iii). That state leaves a template reading a
      key no config supplies -- the same dead-config-key class this package exists to retire --
      and silently re-acquires ntfy's old placement the moment anyone restores the TOML key.
      Separately, an implementation that adds the root `[governance]` table but leaves that block
      intact keeps `nyxloom.slice` on ntfy outright, because ciu never overwrites a compose key
      the author already emits (governance.py:1016-1038; SPEC.md:2917-2923). BOTH the block and
      the keys must go.
    gate: tester-unified
  - id: O2b
    observable: >-
      The PLAIN-COMPOSE path stays governed. `ntfy/docker-compose.yml` -- which receives NO ciu
      overlay -- carries the placement and caps inline after the fix:
      `grep -qx '    cgroup_parent: dev-background.slice' ntfy/docker-compose.yml`, and the same
      file contains `mem_limit: 2g`, `memswap_limit: 17g`, `mem_reservation: 256m`, and a
      `blkio_config:` block naming `/dev/vda` with `rate: 200` (read) and `rate: 400` (write) --
      i.e. it mirrors the fragment ciu injects for ntfy, verified by diffing it against
      `ntfy/.ciu/ciu.compose.overlay.yml` from O1's run.
    negative: >-
      This oracle exists because the ciu overlay CANNOT reach this file, and because the
      currently-running ntfy container is deployed FROM it, not by ciu. Measured at carve time:
      `docker inspect nyxloom-ntfy` reports compose project `ntfy` with
      `config_files=/workspaces/vbpub/nyxloom/ntfy/docker-compose.yml` -- a plain
      `docker compose -f ...` deployment, so ciu governance was never in that path at all. An
      implementation that merely DELETES line 14 (as an earlier revision of this carve specified)
      passes O1 and O2 while making the live deployment path STRICTLY WORSE: it removes the only
      placement ntfy had and adds nothing, so the container lands at Docker's unconfined default
      instead of a transient slice. Deleting without replacing is the false-PASS this oracle
      closes. Note the values are inlined rather than inherited precisely because there is no
      overlay on this path; they must be kept in step with the governance table by hand, which
      the file's own "keep both in sync" header already requires.
    gate: tester-unified
  - id: O3
    observable: >-
      The SECOND root, proven independently. `cd pwmcp && ciu up --dir . --dry-run
      --define-root "$PWD"` exits 0 and `.ciu/ciu.compose.overlay.yml` contains BOTH
      `    cgroup_parent: dev-background.slice` and `    mem_limit: 4g`. File identity is pinned
      too, not merely inferred from the overlay:
      `grep -n '^\[governance\]' pwmcp/ciu.global.defaults.toml.j2` matches, AND
      `! grep -q governance pwmcp/ciu.global.toml.j2` (the table must NOT have been written into
      the higher-precedence tracked near-duplicate).
    negative: >-
      Running only nyxloom's dry-run and assuming pwmcp follows is the false-PASS: v7 roots are
      islands with no inheritance (that is v8's `[ciu] inherit`, explicitly NOT built here).
      The two grep halves are what distinguish the specified implementation from one that edits
      `pwmcp/ciu.global.toml.j2` instead -- that file sits at HIGHER precedence
      (config_model.py:686-696), so editing it produces an IDENTICAL overlay while leaving the
      committed defaults layer -- the layer every fresh worktree gets -- untouched. Note
      `--dir .` on pwmcp CREATES a docker network and connects the devcontainer
      (`auto_connect_network = true`); an oracle run that leaves it behind is not complete, see
      Environment setup.
    gate: tester-unified
  - id: O4
    observable: >-
      Per-stack sizing is real, and the ROOT value is distinct from the OVERRIDE. From `nyxloom/`:
      `ciu up --profile tools --dry-run --define-root "$PWD"` exits 0 and
      `pwmcp-instance/.ciu/ciu.compose.overlay.yml` contains `    cgroup_parent: dev-background.slice`
      AND `    mem_limit: 4g`. In the SAME check, from O1's default-profile run, the three
      stacks carry THREE DISTINCT values: `ntfy/.ciu/ciu.compose.overlay.yml` has
      `    mem_limit: 2g` (the nyxloom root value), `nyxloomd/.ciu/ciu.compose.overlay.yml` has
      `    mem_limit: 6g` (Work item 2b's per-stack override), and `pwmcp-instance` has `4g`.
    negative: >-
      Asserting only `4g` on pwmcp-instance is a false-PASS: an implementation that simply sets
      the nyxloom ROOT `mem_limit = "4g"` and never writes Work item 2's `[pwmcp.governance]`
      table satisfies it, while silently giving `ntfy` and `nyxloomd` 4g too. Requiring THREE
      distinct values in one oracle is what forces both per-stack tables to exist: no single root
      value can satisfy 2g/6g/4g simultaneously. The `6g` half is load-bearing for a second
      reason -- `nyxloomd` runs up to 5 agent CLIs uncontained in-container (see Memory sizing
      2b), so an implementation that leaves it at the root's 2g ships a dispatcher that OOM-kills
      itself and every in-flight agent under normal load, while passing every other assertion. `pwmcp-instance` is also deliberately OUTSIDE the default profile
      (ciu.global.defaults.toml.j2 lines 25-31), so O1's run never touches it -- checking only
      the default profile and declaring the root covered is the other false-PASS. Finally, the
      table's root key must be `pwmcp`, the stack's single non-reserved top-level key, NOT
      `pwmcp-instance`: a misplaced table is silently ignored (ciu warns on unknown keys only
      INSIDE `[governance]`, governance.py:384-395) and the overlay would show the root's value.
    gate: tester-unified
  - id: O5
    observable: >-
      MUTATION-CHECKED, run by hand, all runs pasted verbatim into nyxloom-P103-REPORT.md.
      Break (a), two runs: flip the committed table to `enabled = false`, re-run O1's dry-run --
      no overlay `cgroup_parent` line for either stack (both greps print `0`) and the
      `[GOVERNANCE]` log line is absent entirely; restore and re-run -- green.
      Break (b), the LAYER break, FIVE runs in this exact order: (i) delete the `[governance]`
      block from `ciu.global.defaults.toml.j2`; (ii) write it verbatim into an UNTRACKED
      `ciu.global.toml.j2`; (iii) run O1 -- it PASSES (this run is the point: it records that a
      site-layer "fix" looks green locally); (iv) delete ONLY the untracked `ciu.global.toml.j2`,
      keeping (i) -- run O1, it now FAILS; (v) `git checkout -- ciu.global.defaults.toml.j2` --
      run O1, green again.
    negative: >-
      A break that cannot fail the oracle is not mutation evidence. Do NOT collapse break (b) to
      "move the table to a site layer, then run from a `git stash -u`-clean or freshly-cloned
      worktree" -- an earlier revision of this carve specified exactly that and it is
      SELF-CANCELLING: `git stash -u` stashes BOTH the untracked site file AND the tracked
      deletion, restoring the correct implementation, and a fresh clone of the branch carries the
      committed defaults table too (the move was never committed). Either way O1 passes and
      nothing is proved. Step (iv) -- removing only the site file while the defaults deletion
      stands -- is the run that actually discriminates. Do NOT substitute "delete the table
      entirely" for break (a) either: that is indistinguishable from the input_revision baseline
      and proves only that the baseline is broken, which is already known. Break (a)
      specifically pins that `enabled` is load-bearing rather than decorative (ciu enforces it as
      a bool, governance.py:398-402).
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "`git grep -n '\\[governance\\]' -- nyxloom pwmcp` at dispatch time returns ANY hit -- measured at input_revision as ZERO hits in every tracked ciu.*.toml* file under both roots (tabulated below). A pre-existing table means someone landed part of this fix concurrently and the layering analysis is stale: report BLOCKED naming the file, do not merge two tables"
  - "`pwmcp/ciu.global.toml.j2` has acquired a `[governance]` table between carve and dispatch -- it sits at HIGHER precedence than the file this package edits (ciu/src/ciu/config_model.py:686-696), so a table there would win per-key over the committed defaults and silently invert the fix. Report BLOCKED; resolving the two-file duplication is Work item 6's filing, not this package's edit"
  - "`ciu version` reports anything other than 7.11.0 -- every semantic in this carve (author-always-wins injection, the no-hardcoded-slice-default, overlay path, per-key deep_merge) was measured against 7.11.0 exactly. A different version means the probe log is stale evidence; re-probe before trusting any oracle, and report BLOCKED if the overlay path or precedence changed"
  - "`ciu up --profile default --dry-run` exits non-zero for a reason OTHER than a governance finding -- e.g. the DooD preflight, a secrets/materialise step, or an external network the tools profile expects. That is an environment fault, not this package's contract; report BLOCKED with the full step output rather than weakening a `--dry-run` oracle to `ciu check` (which is shape-only and PASSES with zero governance declared, i.e. it can never prove this package)"
  - "any oracle would require an actual `ciu up` (no `--dry-run`), a `docker run`, or installing a systemd slice -- report BLOCKED. Every oracle here is deliberately render-only: the host is shared with production game servers, carried load average 9.22/8 cores with a live run-gate container and a full dstdns stack at carve time, and slice installation needs host root the daemon must not have (docs/plan-resource-governance.md D-G2)"
  - "the `[nyxloomd.runtime]` deletion in the TRACKED `nyxloomd/ciu.toml` is reverted by a `ciu render` run and the resulting diff is anything more than the comment-stripping measured at carve time -- report BLOCKED rather than committing a render artifact's incidental changes as part of this package"
  - "`grep -c 'trust = \"operator\"' routes.host.toml` returns anything other than 8, or `nyxloom-trove/nyxloom.toml`'s `max_active_tasks` is no longer 5. Both are INPUTS to Work item 2b's nyxloomd ceiling (8 operator-trust routes run UNCONTAINED as in-process children of the daemon -- `containment.py:204-213`, `routes.host.toml:19-20` -- and cover every default implement/review tier). A change in either invalidates the derivation: report BLOCKED and re-derive rather than shipping a stale ceiling"
  - "`dev-background.slice`'s rendered MemoryMax on the live host is not 8G (the value this carve derived from host-setup.env.example, which is the EXAMPLE not a deployed host-setup.env -- no host-setup.env exists in the checkout). The 8G tier backstop is what makes a generous per-container ceiling defensible; if the real unit differs materially, re-derive the per-container numbers rather than shipping the stated reasoning"
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
now. Read the attribution carefully: this container was deployed by **plain
compose**, not by ciu (`docker inspect` reports compose project `ntfy` with
`config_files=/workspaces/vbpub/nyxloom/ntfy/docker-compose.yml`). ciu governance
was never in its path — it is disabled at `input_revision` — so nothing was
"skipped" here. What this proves is the *consequence*; the author-always-wins
mechanism is proved separately, from ciu's source and the tracer bullet:

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
running — still pinned to `nyxloom.slice`. That is why `scope.touch` carries
eleven content files rather than the two NL-6 names: **every extra file is forced
by a measured fact, none by preference.** The carve-time tracer bullet below shows ntfy's
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
| `nyxloom/ntfy/server.yml` | 2 | ntfy's OWN auth governance, not cgroups. In `forbid` |
| `nyxloom/ntfy/README.md` | 61, 67 | line 61 ("Governance notes") is auth, NOT cgroups -- untouched. Line 67 names the `nyxloom.slice` cgroup and IS in `scope.touch` (Work item 3f). The two must not be conflated |
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
| `nyxloom/ntfy/docker-compose.yml` | 14 | `    cgroup_parent: nyxloom.slice` | **REPLACE** (3b) — plain-compose sibling; receives NO ciu overlay AND is the path the running container was deployed from, so the caps go inline |
| `nyxloom/ntfy/README.md` | 67 | documents the posture as `` `nyxloom.slice` cgroup `` | **REWORD** (3f) — same keep-the-documented-posture-in-sync obligation. Missed by the first sweep, which dismissed this file on line 61 (auth governance); line 67 is about the cgroup |
| `nyxloom/ntfy/ciu.defaults.toml.j2` | 24-25 | comment + `cgroup_parent = "nyxloom.slice"` | **DELETE** (3c) — sole consumer was 3a's Jinja reference |
| `nyxloom/nyxloomd/ciu.defaults.toml.j2` | 24-25 | comment + same key | **DELETE** (3d) — already dead: `nyxloomd/ciu.compose.yml.j2` has ZERO `cgroup_parent` hits |
| `nyxloom/nyxloomd/ciu.toml` | 8-9 | `[nyxloomd.runtime]` / same key | **DELETE key line** (3e) — tracked render output, see scope note |
| `nyxloom/pwmcp-instance/*`, `pwmcp/*` | — | none | no edit; inject cleanly |
| `nyxloom/docs/plan-resource-governance.md`, `nyxloom/infra/slices/` | various | narrative + uninstalled units | RETAINED deliberately -- prose and `.slice` units are outside O2(iii)'s config/compose globs |

**`[<stack>.runtime]` is not a ciu table at all.** There is no `[runtime]` in
`ciu/docs/SPEC.md`; ciu reads the key never and warns never (S15.13's unknown-key
WARN covers the `[governance]` table only, `governance.py:384-395`). ntfy's copy
was live *solely* through its own Jinja template; nyxloomd's was inert.

## Memory sizing — the decision and its reasoning

**Root level: `mem_limit = "2g"` for nyxloom, `"4g"` for pwmcp. Per-stack: one
override, `[pwmcp.governance] mem_limit = "4g"` for `pwmcp-instance`.**

### The host backstop, measured

The tier ceiling is not an assumption — it is a rendered systemd unit in this
monorepo,
`modern-debian-tools-python-debug/host-setup/units/dev-background.slice.in`, with
values from `host-setup.env.example`:

| property | value |
|---|---|
| `MemoryHigh` | 6G |
| `MemoryMax` | **8G** |
| `MemorySwapMax` | 48G |
| `CPUWeight` | 20 |
| `ManagedOOMMemoryPressure` | `kill` at 75% |

So `dev-background.slice` caps **every dev container on this host, in aggregate,
at 8G**, and cgroup v2 charges child usage to the parent. That is what makes a
generous per-container ceiling free in host-safety terms: no combination of
per-container values can exceed 8G for the tier.

**A second, decisive fact:** `scripts/mdt-dev-cap-watcher.py` watches the slice
and applies `MemoryMax=DEV_CAP_MEMORY_MAX` (**1G**, `mdt-dev-cap-watcher.py:65-66`)
to any container that arrives *without* asking for its own `--memory`; a caller's
explicit value always wins. Two consequences, both arguing for this package:

- Declaring `mem_limit` explicitly is **required**, not decorative. Left implicit,
  `nyxloomd` would be clamped to 1G by the watcher — far too small.
- Placement is the part that **cannot** be retrofitted: the watcher's own header
  says it "cannot fix cgroup-parent placement itself (create-time only)". Today's
  containers sit in a fictional `nyxloom.slice`, i.e. outside the watcher's reach
  entirely, so they get neither the tier ceiling nor the 1G default. That is the
  gap `cgroup_parent` closes and nothing else can.

**One behaviour change to state plainly, not bury.** `dev-background.slice` sets
`ManagedOOMMemoryPressure=kill` at 75% over an **8G aggregate** shared with 25+
containers. Moving `ntfy` — which is public-facing behind tls-edge — into that
tier makes it OOM-killable under tier pressure for the first time. This is still
strictly better than the status quo (an unbounded transient slice with no ceiling
at all), and the practical risk is low: ntfy is a small Go binary and systemd-oomd
targets the highest-pressure cgroup, which will be a build or gate container long
before it is ntfy. But it IS a posture change for a production-facing service, so
record it in the REPORT and flag it for operator acknowledgement rather than
letting it land silently.

### The per-container numbers

1. **No measured data exists** for `nyxloomd` or `ntfy` — neither is sized in any
   nyxloom doc, and `docker stats` history is unavailable (nyxloomd is not
   running). `docs/plan-resource-governance.md` **D-G4** rules on this:
   *"absolute limits are HOST-owned and live in the slice units; the project
   declares nothing absolute"*.
   **The tension is unavoidable and is resolved here, not cited both ways:** once
   `enabled = true`, ciu injects a `mem_limit` whether or not the project names
   one (default `"1g"`, `governance.py:68`), and leaving it implicit hands the
   number to the watcher's 1G. So D-G4's "declare nothing absolute" is not
   available; the honest reading is that the *authoritative* ceiling remains the
   slice's 8G and the per-container value is a blast-radius bound beneath it.
   That is what these numbers are.
2. **`2g` for nyxloom's root** matches dstdns's own root-level value
   (`dstdns/ciu.global.toml.j2:110-124`) and what every dstdns container verifiably
   runs with (`docker inspect` → `Memory=2147483648`).
   `2g` applies to **ntfy**, which is a single small Go binary — comfortable.
   It does NOT apply to `nyxloomd`; see item 2b.
2b. **`6g` for `nyxloomd`, via `[nyxloomd.governance] mem_limit`.** This is the
   number an earlier revision of this carve got wrong, so the derivation is
   spelled out.
   `nyxloomd` spawns agent legs as its own children (`src/nyxloom/wrapper.py:497`).
   A *contained* leg wraps the argv in `docker run` (`wrapper.py:32`, and the
   "Spawn (the CLI, or the docker client running it)" comment at `:486`), leaving
   only a thin docker client in-container. But **containment is not the default**:
   `containment.py:204-213` returns False for any non-free `trust = "operator"`
   route, `routes.host.toml:19-20` says such a route *"runs as a direct child of
   the daemon, uncontained"*, and **8 routes declare it** — covering
   `[tiers.implement-1]`, `[tiers.implement-2]` and `[tiers.review-3]`, i.e. every
   default implementation and review path. So in the normal case up to
   `max_active_tasks = 5` (`nyxloom-trove/nyxloom.toml:96`) full agent CLIs run
   **in-process inside this container** and are charged to its memory cgroup.
   **Why 6g, and why erring high is the safe direction.** nyxloom's own
   `infra/slices/nyxloom-daemon.slice:30-33` sizes the bare daemon at "at or below
   a Python daemon's realistic resident set" (`MemoryMin=128M`), and its sibling
   `nyxloom-agents.slice:13-16` leaves the agent ceiling explicitly blank —
   *"OPERATOR MUST SET"*. **nyxloom has never sized this either**; the number is a
   genuinely open product question, which is why Work item 9 files it for
   measurement rather than pretending this carve settles it. The interim value
   uses the same asymmetry argument that slice file makes for `MemoryMin`, but
   inverted for a ceiling: too HIGH is harmless — `mem_limit` is a cap, not a
   reservation, it charges nothing unless actually used, and the tier's 8G
   `MemoryMax` remains the real backstop; too LOW is harmful — it OOM-kills the
   dispatcher and every in-flight agent at a threshold we invented. So: daemon
   (~256M) + 5 concurrent CLIs at ~1g each ≈ 5.5g, rounded to **6g**.
   Note this makes the number NOT a guess dressed as a measurement: it is an
   explicitly conservative interim ceiling with a filed follow-up.
3. **`4g` for both pwmcp stacks.** Both declare `shm_size = "2gb"`
   (`nyxloom/pwmcp-instance/ciu.defaults.toml.j2:34`,
   `pwmcp/ciu.defaults.toml.j2:145`, each commented *"Chromium needs real shared
   memory"*), and cgroup v2 charges tmpfs pages to the memcg that faults them in.
   **State the mechanism precisely:** `shm_size` is a tmpfs *ceiling*, not a
   preallocation — only pages actually touched are charged, so 2g would not be
   consumed the moment the container starts. The risk is a Chromium renderer
   filling `/dev/shm` under load and colliding with a 2g cap. 4g gives the declared
   shm plus an equal working set.
4. **Everything else is left at ciu's default, deliberately.** `mem_reservation`
   (256m), `write_iops` (400), `read_iops` (200 fallback) are
   `GOVERNANCE_DEFAULTS` and are what dstdns runs with.
   **`mem_swap_limit` is 17g and that number deserves stating plainly:** in compose
   `memswap_limit` is *memory + swap combined*, so at `mem_limit: 2g` it permits
   ~15g of swap for one container. On a host whose own config comment records
   *"~3GB available / 20GB swap in use with ZERO nyxloom attempts active"*
   (`nyxloom.toml:98-100`) that is generous. It is accepted UNCHANGED here because
   the tier's `MemorySwapMax=48G` is the real bound and because changing it is a
   separate, estate-wide decision affecting dstdns too — but it is recorded, not
   overlooked, and is a fair thing for a future package to tighten.
   `ksm_optin` is **omitted** (`= off`): pwmcp's image already performs its own KSM
   opt-in *"without depending on any consumer's ciu governance overlay"*
   (`pwmcp/containers/pwmcp/ksm-optin.c:7`), and `"builtin"` would make `ciu render`
   require Docker to compile a shim (`SPEC.md:3197-3200`).
5. **`device = "/dev/vda"` is pinned, not autodetected.** `findmnt --target
   /var/lib/docker` returns EMPTY in this devcontainer (measured), so ciu's
   autodetect silently fails and blkio caps would be skipped entirely (the
   `if device:` guard, `governance.py:1042`). `/dev/vda` is the real host disk
   (`lsblk`: 1T) and is what dstdns's containers run with
   (`BlkioDeviceReadIOps=[/dev/vda:200]`). O1 pins it because omitting it is a
   silent-skip false-PASS.
6. **`cgroup_parent = "dev-background.slice"`, explicit.** It is the only slice
   verified installed and in use. It is NOT left `""`: that resolves from the
   ambient `$CGROUP_PARENT_DEV_BACKGROUND`, which **is** set in this
   devcontainer's process environment — so `""` would work here and silently
   hard-error `[S15.2]` on any host or CI without it. O1 pins the committed
   literal for exactly this reason.


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
# "" falls back to the AMBIENT $CGROUP_PARENT_DEV_BACKGROUND -- which IS set in
# this devcontainer's process environment (devcontainer.json containerEnv), and
# is absent on other hosts and in CI, where it raises [S15.2]. Naming the slice
# here keeps the committed config host-independent instead of silently
# depending on an environment variable. dev-background.slice is the
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
# (ciu.defaults.toml.j2:145), and cgroup v2 charges tmpfs pages to the memcg
# that faults them in. shm_size is a CEILING, not a preallocation -- nothing is
# charged at start; the risk is a Chromium renderer filling /dev/shm under load
# against a 2g cap. 4g = the declared shm plus an equal working set.
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

**2b. Per-stack override for the daemon.** In `nyxloomd/ciu.defaults.toml.j2`
(the same file Work item 3d edits), add:

```toml
# nyxloom-P103: 6g, not the root's 2g. 8 routes in routes.host.toml declare
# trust = "operator", which containment.py:204-213 runs UNCONTAINED as direct
# children of the daemon -- so up to max_active_tasks = 5 full agent CLIs run
# in THIS container and are charged to its memory cgroup. A ceiling costs
# nothing unless used (the tier's 8G MemoryMax is the real backstop), while one
# set too low OOM-kills the dispatcher and every in-flight agent. Interim value
# pending real measurement -- see the backlog entry from nyxloom-P103 item 9.
[nyxloomd.governance]
mem_limit = "6g"
```

**3. Retire `nyxloom.slice`** — four deletions, one REPLACEMENT, one doc edit.

Deletions, exactly as tabulated in the sweep above: (a) `ntfy/ciu.compose.yml.j2`
lines 13-15; (c) `ntfy/ciu.defaults.toml.j2` lines 24-25; (d)
`nyxloomd/ciu.defaults.toml.j2` lines 24-25; (e) `nyxloomd/ciu.toml` line 9 (its
`[nyxloomd.runtime]` header stays — it still carries
`run_as_uid`/`run_as_gid`/`docker_gid`).

**(b) `ntfy/docker-compose.yml` line 14 is REPLACED, not deleted.** This path
loads no ciu overlay and is what the running container was deployed from, so the
caps must be inline. Substitute VERBATIM for line 14, at the same indent:

```yaml
    # nyxloom-P103: this plain-compose path receives NO ciu governance overlay
    # (ciu injects via a second `-f` onto .ciu/ciu.compose.overlay.yml), so the
    # caps are inline here and MUST be kept in step by hand with the
    # [governance] table in ../ciu.global.defaults.toml.j2 -- the same
    # "keep both in sync" obligation this file's header already states.
    cgroup_parent: dev-background.slice
    mem_limit: 2g
    memswap_limit: 17g
    mem_reservation: 256m
    blkio_config:
      device_read_iops:
        - path: /dev/vda
          rate: 200
      device_write_iops:
        - path: /dev/vda
          rate: 400
```

Confirm it mirrors ciu's own fragment by diffing against
`ntfy/.ciu/ciu.compose.overlay.yml` from O1's run (O2b).

**(f) `ntfy/README.md` line 67.** It documents the hardening posture as
`` `nyxloom.slice` cgroup ``. Replace that fragment with
`` `dev-background.slice` cgroup (via ciu governance; inline on the
plain-compose path) `` so the documented posture matches what ships. Leave the
"Governance notes" section at line 61 alone — that is ntfy's auth surface, not
cgroups.

**4. Correct the stale `besteffort.slice` claim** in
`docs/plan-resource-governance.md`. Append (do NOT rewrite the narrative) a
short dated note after BOTH the D-G0a block and its duplicate at lines 221-231,
stating: as of ciu 7.11.0 `cgroup_parent` has no hardcoded default —
`GOVERNANCE_DEFAULTS["cgroup_parent"] = ""` and an unresolvable value is a hard
`[S15.2]` error, not a silent `besteffort.slice`; nyxloom-P103 sets the key
explicitly regardless. Keep the `device` half of both blocks — it is still true
and is why this package pins `/dev/vda`.

Two FURTHER stale `besteffort.slice` statements in the same file get the same
treatment: **line 259** and **lines 296-298**. Both describe dstdns as placing
its containers in `besteffort.slice`; dstdns runs `dev-background.slice` today
(measured: every dstdns container on this host). Lines 296-298 matter most — they
are the passage this package's own memory-sizing argument leans on, so leaving
them naming the wrong slice would make the citation unverifiable.

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
shadowing hazard. File it with
`python3 exec-nyxloom.py backlog new --title "<title>" --type bugfix --component ciu-config`
(check `backlog new --help` for the exact flags first), and record the allocated
ID in the REPORT.

**7. File a second backlog entry** (do not fix it here) against nyxloom's own
lint: **L7 cannot express a `scope.touch` path in a sibling project of the same
monorepo checkout.** A `../`-prefixed or absolute path is a hard error
(`src/nyxloom/lint.py:1017-1024`), while a bare sibling path like
`pwmcp/ciu.global.defaults.toml.j2` is silently ACCEPTED for the wrong reason —
the create-exemption at `lint.py:1044-1045` treats it as a file to be created
under `cfg.root`, so lint validates a path that does not exist and never sees the
real file. This package is the live case: it must edit a second ciu root that no
nyxloom project owns. Note both halves of the defect (the false negative and the
false positive) in the entry. File it with the same `backlog new` invocation as Work item 6
(component `nyxloom-lint`) and record the allocated ID in the REPORT.

**8. File a third backlog entry** (do not fix it here): nyxloom's `doctor`
ALREADY implements a critical `cgroup-slice-missing` finding
(`src/nyxloom/doctor.py:80-86, 787, 813-831`) for a `--cgroup-parent=<slice>`
appearing in **gate argv** — but it never inspects a ciu config or compose file.
That is the durable answer to the gap this package works around by hand: ciu's
own slice-existence preflight SKIPS inside the devcontainer (systemd is not PID
1), which is how a fictional `nyxloom.slice` survived in ntfy's config. Extending
`doctor`'s existing check to ciu-declared placement would have caught it. Fold in
the companion recommendation from "Gate argv" — extending
`tests/test_render.py:1451-1505`'s template/sibling-agreement pattern to the
**ntfy** pair, so this package's config fix stops being silently re-introducible
— either in this entry or a sibling one, so it survives past this package.
Record the ID in the REPORT.

**9. File a fourth backlog entry** (do not fix it here): **`nyxloomd`'s memory
ceiling has never been measured by anyone.** `infra/slices/nyxloom-agents.slice:13-16`
leaves the agent-session ceiling blank with *"OPERATOR MUST SET"*, and
`nyxloom-daemon.slice:35-37`'s own "TO REFINE" note already prescribes the
method: run the daemon idle, then under a real dispatch wave, read
`memory.current` + `memory.stat` anon at both points. Work item 2b ships a
deliberately conservative 6g interim ceiling; this entry is the measurement that
replaces it, and it should record that up to `max_active_tasks = 5` agent CLIs
run uncontained inside the daemon container today. Record the ID in the REPORT.

### Degrees of freedom

Comment wording in Work items 4, 5 and 6, and REPORT/LOG prose. The TOML blocks
in Work items 1, 2 and 2b, the four deletions plus the replacement and the doc reword
in Work item 3, and every oracle command
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
With no changed Python lines, `changed-line-coverage` is **vacuous**, and nothing
in the lane asserts a governance key. **A green tester-unified run is not evidence
for any oracle in this package.**

Be precise about *why*, because a blanket "no test reads config" would be false:
`tests/test_render.py:1451-1505`
(`test_nyxloomd_compose_template_and_sibling_mounts_agree`) DOES parse both
`nyxloomd/ciu.compose.yml.j2` and `nyxloomd/docker-compose.yml` inside the gate —
but only for *volume sources*, and only for *nyxloomd*, so it cannot catch this
package's regression. That test is nonetheless the ready-made in-repo pattern for
turning O2/O2b into a permanent gate assertion over the **ntfy** template/sibling
pair, which is how a zero-Python fix stops being silently re-introducible. Doing
so is OUT OF SCOPE here (it would add Python and change the gate's meaning) — but
name it in the REPORT as the recommended follow-up rather than concluding no such
mechanism exists.

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

`nyxloom lint` on this handoff exits **0** with **25 warnings and no errors**
(18 x L13, 6 x L7, 1 x L10). Each class was verified once at carve time; anything
else is drift and should be investigated, not waved through.

| warning | count | verdict |
|---|---|---|
| `L10 handoff size ~16.5k tokens` | 1 | over the 10k warn floor, still under the 18k ERROR floor — but the margin is now thin, so a future revision that adds material should trim rather than assume headroom. The size is the tabulated sweeps, the probe log and the measured memory derivation: the material that stops the implementer re-deriving the mechanism |
| `L13 ... '.ciu/ciu.compose.overlay.yml'` and its per-stack forms | 6 | FALSE POSITIVE. ciu's **generated, gitignored** overlay — the artifact the oracles *read*, never edit. Putting a build output in `scope.touch` would be wrong |
| `L13 ...` paths that ARE in `scope.touch` but written project-prefixed or absolute in oracle prose (`nyxloom/ciu.global.defaults.toml.j2`, `nyxloom/ntfy/ciu.compose.yml.j2`, `nyxloom/ntfy/ciu.defaults.toml.j2`, `nyxloom/nyxloomd/ciu.defaults.toml.j2`, `nyxloom/nyxloomd/ciu.toml`, `workspaces/vbpub/nyxloom/ntfy/docker-compose.yml`) | 6 | FALSE POSITIVE of a distinct kind: L13's matcher does not recognise the prefixed form as the same path. The prefixes are deliberate — O2(iii)'s globs are repo-root-relative and MUST be run from the worktree root, so the oracle spells the paths that way |
| `L13 ... 'nyxloom/ntfy/ciu.compose.yml'` | 1 | FALSE POSITIVE, same class as the overlay: the rendered compose, gitignored |
| `L13 ... 'ciu/src/ciu/governance.py'`, `'ciu/src/ciu/composefile.py'`, `'pwmcp/ciu.global.toml.j2'`, `'infra/slices'`, `'modern-debian-tools-python-debug/host-setup/units/dev-background.slice.in'` | 5 | FALSE POSITIVE: read-only citations and forbidden paths, deliberately not in `scope.touch` |
| `L7 cross-repo reference '/workspaces/dstdns'` | 2 | expected — the read-only reference for the `[governance]` shape |
| `L7 relative-up path '../pwmcp'` | 2 | expected — the sibling root this package's second half edits; see the `scope.touch` path caveat and Work item 7 |
| `L7 relative-up path '../ciu.global.defaults.toml.j2'` | 2 | expected — a relative pointer inside Work item 3b's pinned YAML comment |

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
