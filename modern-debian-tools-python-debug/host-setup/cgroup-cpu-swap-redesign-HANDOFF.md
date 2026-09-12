# Handoff: cgroup CPU/swap/zswap governance redesign — review-fix-commit remaining

Written 2026-09-12 by a Claude Code session that was stopped mid-task by the
operator ("we need to stop... write a handoff prompt for a 3rd party agent").
Everything below is uncommitted working-tree state as of this file's own
creation — **nothing described here has been committed yet.**

## What this session did (context for the fixes below)

Starting point: a size-breakdown/AI-CLI-tools task on the mdt devcontainer
image, which led into "what cgroup do we actually land in, and does mdt's
documented IO governance actually work?" investigation via `host-escape`,
which led into a full redesign of `host-setup/`'s dev-tier cgroup v2
governance. In order:

1. Added `nyxloom`/`claudelink`/`pi` AI CLI tools to the Dockerfile; converted
   a `COPY` of build-time-only download artifacts to a `--mount=type=bind`
   (fixed a ~970MB dead-weight layer); split `aider` into its own RUN layer
   after venv setup for cleaner layer attribution.
2. New `scripts/report-image-size-breakdown.py` — mechanically re-runnable
   image size breakdown, two views (by-layer / by-logical-group), prints an
   unattributed-bytes diff, stamps the report with the git commit that built
   it. `scripts/test_report_image_size_breakdown.py` is its test suite.
3. New `customization/mdt` CLI (`host-exec`/`host-shell`/`doctor`
   subcommands) replacing the old ad hoc `host-escape` script; `host-escape`
   is now a thin wrapper delegating to `mdt host-exec`/`host-shell` for
   backward compatibility (existing callers of `host-escape` keep working —
   this compat shim is INTENTIONAL, unlike the ones removed in step 5 below).
   `mdt doctor` re-checks/fixes cgroup2 mount flags
   (`memory_recursiveprot`/`nsdelegate`/`memory_hugetlb_accounting`) and runs
   automatically after every `host-exec`/`host-shell`.
4. Investigated live cgroup placement using `mdt host-exec`. Confirmed the
   devcontainer and `dev-buildkitd.slice` are genuinely governed; found a
   REAL, previously-undocumented gap: the DEFAULT `docker` driver (not just
   Buildx's `docker-container` driver, which `docs/BUILD-ARCHITECTURE.md`
   already documented as unreliable) also fails to land its build-step exec
   cgroup under `dev.slice` — verified live, landed at a malformed
   `system.slice/dev-background.slice:docker:<id>`.
5. Full redesign of `host-setup/units/*.slice.in` + `host-setup/install.sh` +
   `host-setup/host-setup.env.example` per explicit operator numbers:
   - **CPU**: `dev.slice` gets `CPUQuota` = auto-detect `nproc -
     DEV_CPU_RESERVE_CORES` (default reserve 1) when left empty; every child
     slice (`dev-interactive`/`dev-background`/`dev-gates`/`dev-buildkitd`)
     gets its OWN `CPUQuota` = auto-detect `nproc -
     DEV_SUBSLICE_CPU_RESERVE_CORES` (default reserve 3).
   - **IOPS sub-ceiling**: `dev-gates.slice` and `dev-buildkitd.slice` each
     get a RUNTIME-ONLY (not a static unit line) `IOReadIOPSMax`/
     `IOWriteIOPSMax` at `DEV_SUBSLICE_IOPS_PCT`% (default 60%) of whatever
     `mdt-apply-dev-caps.sh` just measured for `dev.slice` itself. Bandwidth
     deliberately untouched — IOPS only.
   - **Swap cascade**: `DEV_SWAP_CASCADE_PCT` (default 80%) applied
     recursively — host total swap (`/proc/meminfo` `SwapTotal`) → `dev.slice`
     `MemorySwapMax` → each child's own `MemorySwapMax` (80% of `dev.slice`'s
     OWN derived value, not of the host total again) → the reactive
     watcher's per-container `MemorySwapMax` (80% of whichever child's LIVE
     `memory.swap.max` matched). Every level auto-detects when its
     `host-setup.env` var is left empty; an explicit value always overrides
     just that one level.
   - **`MemoryZSwapWriteback`**: added to all five slices as an EXPLICIT,
     INDEPENDENT per-slice yes/no (`DEV_ZSWAP_WRITEBACK`,
     `DEV_INTERACTIVE_ZSWAP_WRITEBACK`, `DEV_BACKGROUND_ZSWAP_WRITEBACK`,
     `DEV_GATES_ZSWAP_WRITEBACK`, `DEV_BUILDKITD_ZSWAP_WRITEBACK`) —
     confirmed live this session that the FILE value does NOT cascade from
     parent to child, but ENFORCEMENT is hierarchical the other way (a `0` on
     ANY ancestor silently disables writeback for the whole subtree below
     it, regardless of what a child's own file says) — see
     `CGROUP-NOTES.md` "zswap writeback — who may page to disk" and
     `units/dev.slice.in`'s own comment. `dev.slice`'s own value is
     documented as a safety anchor only.
   - Renamed the watcher's old `DEV_CAP_MEMORY_MAX`/`DEV_CAP_GATES_MEMORY_MAX`
     to `WATCHER_PER_CONTAINER_MEMORY_MAX`/`WATCHER_PER_CONTAINER_GATES_MEMORY_MAX`,
     added a sibling `WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT` (default 80%).
6. New `host-setup/scripts/mdt-container-caps.lib.sh` — shared per-container
   IO-cap matching/apply logic, extracted from `mdt-apply-dev-caps.sh` so it
   can be sourced by BOTH the periodic sweep AND a new reactive watcher.
7. New `host-setup/scripts/mdt-io-cap-watcher.sh` +
   `host-setup/units/mdt-io-cap-watcher.service` — a `docker events`-driven
   reactive watcher that applies per-container IO caps the instant a
   matching `buildx_buildkit_*`/`*test-runner*`/devcontainer container
   starts, replacing the periodic sweep as the PRIMARY mechanism for that
   (the sweep is now the backstop). `docker events`, not inotify (unlike the
   sibling `mdt-dev-cap-watcher.py` for memory caps), because buildkit
   workers have no fixed, predictable cgroup path to inotify-watch in the
   first place (Buildx's own `cgroup-parent` driver-opt is unreliable under
   the systemd cgroup driver).
8. `scripts/finalize_container_environment.py` gained
   `setup_buildkit_builder()` — idempotently registers the host-managed
   `mdt-buildkitd` worker as the default buildx builder inside a devcontainer
   when `BUILDKIT_HOST` is set, so a consumer repo's plain `docker
   build`/`docker buildx build` gets governed automatically instead of
   needing a manual one-time `docker buildx create`.
9. Extended `host-setup/scripts/check.sh` (new `dev-background.slice`/
   `dev-buildkitd.slice` sections; new `_cpu_quota_check`/`_swap_max_check`/
   `_zswap_check`/`_iops_subceiling_check` shared helpers reused across all
   five slice sections; watcher-service health checks) and
   `host-setup/mdt-host-setup-wizard.py` (two new interactive sections
   walking every new variable above) to match the redesign. Both were
   smoke-tested this session (a synthetic fake `/sys/fs/cgroup` tree for
   `check.sh`; a full canned-answer dry run + existing doctest suite for the
   wizard) — see "Validation already done" below for exact repro commands.
10. Per explicit operator instruction ("no no legacy compat, all clean pls.
    also no backward references in comments."), removed ALL legacy-name
    fallback code this session had introduced or was reviewing, and scrubbed
    "(2026-09-13 review)"/"renamed from"/"previously"/"used to" framing from
    every comment touched this session. **This is why findings #7 and #8
    below are not bugs** — see "Findings that are NOT bugs" section.

Full design rationale for all of the above lives in the already-updated
`host-setup/README.md` ("Tiering model" section) and
`host-setup/host-setup.env.example` (inline per-variable comments) — read
those before changing the design, not just this handoff.

## Uncommitted files (exact scope — commit ONLY these)

```
M  modern-debian-tools-python-debug/Dockerfile
M  modern-debian-tools-python-debug/README.md
M  modern-debian-tools-python-debug/TODO.md
M  modern-debian-tools-python-debug/ai-cli-tools.list
M  modern-debian-tools-python-debug/customization/host-escape
M  modern-debian-tools-python-debug/host-setup/README.md
M  modern-debian-tools-python-debug/host-setup/host-setup.env.example
M  modern-debian-tools-python-debug/host-setup/install.sh
M  modern-debian-tools-python-debug/host-setup/mdt-host-setup-wizard.py
M  modern-debian-tools-python-debug/host-setup/scripts/check.sh
M  modern-debian-tools-python-debug/host-setup/scripts/mdt-apply-dev-caps.sh
M  modern-debian-tools-python-debug/host-setup/scripts/mdt-dev-cap-watcher.py
M  modern-debian-tools-python-debug/host-setup/units/dev-background.slice.in
M  modern-debian-tools-python-debug/host-setup/units/dev-buildkitd.slice.in
M  modern-debian-tools-python-debug/host-setup/units/dev-gates.slice.in
M  modern-debian-tools-python-debug/host-setup/units/dev-interactive.slice.in
M  modern-debian-tools-python-debug/host-setup/units/dev.slice.in
M  modern-debian-tools-python-debug/scripts/finalize_container_environment.py
M  modern-debian-tools-python-debug/scripts/install_ai_cli_tools.py
M  modern-debian-tools-python-debug/scripts/manifest_sections.py
M  modern-debian-tools-python-debug/scripts/stage_tool_artifacts.py
M  modern-debian-tools-python-debug/scripts/test_release_flow.py
?? modern-debian-tools-python-debug/customization/mdt
?? modern-debian-tools-python-debug/host-setup/scripts/mdt-container-caps.lib.sh
?? modern-debian-tools-python-debug/host-setup/scripts/mdt-io-cap-watcher.sh
?? modern-debian-tools-python-debug/host-setup/units/mdt-io-cap-watcher.service
?? modern-debian-tools-python-debug/scripts/report-image-size-breakdown.py
?? modern-debian-tools-python-debug/scripts/test_report_image_size_breakdown.py
```

(Verify with `git status --short -- modern-debian-tools-python-debug` before
committing — other unrelated dirty files exist in this shared checkout from a
DIFFERENT concurrent work stream, RG-55/run-gate-project. Do not touch or
commit those; they are out of scope for this task.)

## Why this isn't committed yet: an independent review found real bugs

Per this repo's standing rule ("every merged change gets an independent
adversarial review, no size exception" — especially warranted here since
this changes resource governance on a host that ALSO runs a production game
server, per estate memory), a `/code-review` pass was launched against this
diff before committing (`Skill("code-review", ...)`, ran as background agent
`@code-review`, task-id `adf8ff647631a09e3`). It fanned out 10 independent
finder agents but explicitly SKIPPED its own verifier pass and gap sweep due
to a time constraint, returning 15 corroboration-weighted candidate findings
with the caveat that some file:line citations are unreliable (the review
agent flagged this itself in at least 3 of the 15). **None of these 15 have
been confirmed by re-reading the actual code except the first two below** —
this session was stopped mid-verification.

### Findings already independently verified by this session (before it stopped)

1. **`host-setup/install.sh` line ~159, the swap-cascade auto-detect**
   (`_host_swap_bytes=$(awk '/^SwapTotal:/{print $2 * 1024}' /proc/meminfo
   2>/dev/null || echo 0)` then `if [ "${_host_swap_bytes:-0}" -gt 0 ]`):
   **CONFIRMED real, but LOW severity.** The `${_host_swap_bytes:-0}`
   expansion already correctly treats an empty awk result (e.g. no
   `SwapTotal:` line matched) the same as `0` — bash's `:-` operator applies
   on both "unset" and "empty string". So the review's literal claim ("meminfo
   parse failed" and "genuinely 0 swap" collapse into ONE branch) is true,
   but on a real root-owned Linux host `/proc/meminfo` is always present and
   readable and `awk` is already a hard dependency elsewhere in this same
   script — so "parse genuinely fails" is a near-impossible path in practice.
   Still worth a small, cheap hardening for defense in depth: distinguish
   "meminfo has a `SwapTotal:` line reading 0" (genuinely no swap on this
   host — completely safe to skip; there is nothing to cap) from "couldn't
   find/read a `SwapTotal:` line at all" (genuinely unknown — should warn
   LOUDLY and different from the "no swap" message, rather than silently
   falling through the same `else` branch with a message that says "no swap
   detected"). Suggested fix (not yet applied):
   ```bash
   if grep -q '^SwapTotal:' /proc/meminfo 2>/dev/null; then
     _host_swap_bytes=$(awk '/^SwapTotal:/{print $2 * 1024}' /proc/meminfo)
   else
     _host_swap_bytes=""
   fi
   if [ -n "$_host_swap_bytes" ] && [ "$_host_swap_bytes" -gt 0 ] 2>/dev/null; then
     # ... existing auto-detect cascade, unchanged ...
   elif [ "$_host_swap_bytes" = "0" ]; then
     echo "no swap on this host (SwapTotal=0) — MemorySwapMax auto-detect skipped; nothing to cap"
   else
     echo "WARN: could not read /proc/meminfo's SwapTotal — MemorySwapMax auto-detect skipped; set DEV_SWAP_MAX/*_MEMORY_SWAP_MAX explicitly in host-setup.env if this host has swap"
   fi
   ```
   Re-check the exact current line numbers before patching — this handoff's
   line citation is from before any other fix in this list was applied and
   may have shifted.

2. **`host-setup/scripts/mdt-dev-cap-watcher.py` lines 119 and 127**
   (`WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT = int(_get("WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT", "80"))`
   and `DEV_SWAP_CASCADE_PCT = int(_get("DEV_SWAP_CASCADE_PCT", "80"))`):
   **LIKELY CONFIRMED, NOT YET FIXED — this session was interrupted while
   verifying it.** Both are bare `int(...)` calls at MODULE LOAD TIME (i.e.
   they run the instant the watcher script/service starts, before any
   per-container logic), with no try/except. `_get()` (defined a few lines
   above, ~line 100) falls back to the os.environ value or the parsed
   `/etc/mdt/host-setup.env` value, or the given default ONLY if the key is
   entirely ABSENT from both — but if an operator sets either key to an
   EMPTY string in `host-setup.env` (a plausible mistake: several neighboring
   keys in the exact same file, e.g. `DEV_SWAP_MAX`, genuinely use
   "empty string = auto-detect" as their documented convention), `_get()`
   returns `""`, and `int("")` raises an uncaught `ValueError` at import
   time. Need to confirm: (a) is `mdt-dev-cap-watcher.service`'s
   `Restart=always` actually set (if so this is a crash-loop, not a one-time
   failure — check `host-setup/units/mdt-dev-cap-watcher.service`); (b) is
   there any existing input validation upstream (the wizard's own
   `validate_pct_1_100`/`validate_nonneg_int` reject empty for these
   analogous keys with a clear error message, so a wizard-produced config
   should never actually contain an empty value here — but a HAND-EDITED
   `/etc/mdt/host-setup.env`, which this whole design explicitly supports
   editing without re-running the wizard, is NOT gated by the wizard's
   validators at all). If confirmed, the fix is a small local helper, e.g.:
   ```python
   def _get_int(name: str, default: int) -> int:
       raw = _get(name, str(default)).strip()
       if not raw:
           return default
       try:
           return int(raw)
       except ValueError:
           log(f"WARN: {name}={raw!r} is not an integer -- using default {default}")
           return default
   ```
   and replace both bare `int(_get(...))` call sites with
   `_get_int("WATCHER_PER_CONTAINER_MEMORY_HIGH_PCT", 80)` /
   `_get_int("DEV_SWAP_CASCADE_PCT", 80)`. Re-verify the exact current line
   numbers before patching.

### Findings NOT yet independently re-verified (do not trust without re-reading the code)

Copied verbatim from the `@code-review` task-notification result. The review
agent's own note: "several of these were found independently by 2-5 different
angles... stronger evidence than a single verifier vote" — but corroboration
across angles run by the SAME fan-out is not the same as an actual verifier
reading the code, and the agent explicitly skipped that step. Treat every
line number as approximate; some are flagged below as explicitly disputed by
the review agent itself.

3. `host-setup/scripts/mdt-dev-cap-watcher.py` (~line 187, agent notes a
   second citation at 244 — re-check): `parse_size()` allegedly only strips a
   single trailing K/M/G/T character, so a value like `4GiB` or `500MB`
   (both wizard-legal size strings elsewhere in this project — see
   `mdt-host-setup-wizard.py`'s `_SIZE_RE`) would raise an uncaught
   `ValueError` in `apply_default_cap()`, called with no try/except directly
   from the inotify main loop. If confirmed, this is a real regression vs.
   the pre-this-session code, which just string-interpolated the raw value
   into systemd's own `set-property` parser (which DOES accept `GiB`/`MB`
   suffixes) rather than parsing it in Python at all.

4. `host-setup/scripts/mdt-apply-dev-caps.sh` (~line 150, agent notes a
   second citation at 158 — re-check): the raw-cgroupfs-write fallback for
   `MemoryZSwapWriteback` (needed on systemd < 256, where `install.sh`
   strips the static unit-file directive entirely) is allegedly applied ONLY
   to `dev-interactive.slice`, never extended to
   `dev-background`/`dev-gates`/`dev-buildkitd.slice` even though this
   session's redesign added the setting to all five slices. **This one was
   already flagged as a possible gap by the PREVIOUS agent in this same
   session** (see the compacted summary this session inherited: "this
   actually seems like a real correctness issue... the raw-write fallback
   in mdt-apply-dev-caps.sh should mirror what the units do now" was
   explicitly noted but never acted on before the session got interrupted
   by an unrelated task). If confirmed, this needs the same raw-write
   block generalized into a loop over all 5 slices with their own
   `DEV_*_ZSWAP_WRITEBACK` var, mirroring the existing
   `dev-interactive.slice`-only block in `mdt-apply-dev-caps.sh`.
   `check.sh`'s own `_zswap_check` helper (added this session) may also need
   its "file absent" vs "value 0 because unset-defaults-to-kernel-1"
   messaging revisited once this is fixed, per the review's compounding
   note.

5. `customization/mdt` (~line 79): `mdt doctor`'s embedded self-heal heredoc
   allegedly sets `set -euo pipefail`, while `mdt-apply-dev-caps.sh` (which
   `mdt doctor` is documented as copying "verbatim" from) sets only `set -uo
   pipefail` (no `-e`) for the identical `uname -r | grep -oE ...`
   kernel-version-parsing line. If the kernel version string fails that
   regex, the `-e` version aborts the WHOLE remount script before it ever
   attempts the `memory_recursiveprot` fix — silently defeating the
   documented purpose of `mdt doctor`. If confirmed, remove `-e` from that
   heredoc (or restructure the kernel-version check to not rely on `set -e`
   forgiveness either way — verify which behavior `mdt-apply-dev-caps.sh`'s
   version actually relies on before copying it verbatim again).

6. `host-setup/scripts/mdt-io-cap-watcher.sh` (~line 63, "independently
   found by 5 different review angles" per the agent): the watcher loads
   config/baseline ONCE at process start; the periodic sweep re-reads both
   fresh every `SWEEP_INTERVAL`. `install.sh`'s `systemctl enable --now` is
   a no-op restart on an already-running unit. If confirmed: an operator who
   edits `host-setup.env` or re-runs `mdt-io-baseline.py` and then re-runs
   `install.sh` gets a sweep that picks up the fresh values immediately, but
   a long-running watcher that keeps applying STALE IOPS/bandwidth values
   and stale match patterns indefinitely — directly contradicting this
   session's own doc claim (`host-setup/README.md`) that the two mechanisms
   "share every line of logic... so the two can't drift apart" (true for the
   CODE, not for the DATA each has loaded). If confirmed, `install.sh` needs
   an explicit `systemctl restart mdt-io-cap-watcher.service` (not
   `enable --now`) whenever it re-renders config that this watcher reads,
   OR the watcher needs to re-source config on some signal/interval of its
   own.

9. `host-setup/tests/test-render.sh` line ~139: allegedly hard-asserts
   `DEV_GATES_MEMORY_SWAP_MAX` is non-empty via `${VAR:?msg}`, but
   `host-setup.env.example` now ships it as `""` by design (this session's
   auto-detect redesign). The review agent claims to have actually RUN this
   test and observed the crash. **This file was not touched or even opened
   by this session at all** — it's a pre-existing render-verification test
   that this session's redesign may have broken as a side effect. HIGH
   priority to check first: if real, this diff currently ships with its own
   test suite broken, silently losing coverage for everything after that
   assertion. Read `host-setup/tests/test-render.sh` in full before deciding
   the fix — either the test's assumption needs updating to allow empty (the
   new intended state) while still catching a GENUINELY missing/mistyped
   var some other way, or (less likely) the auto-detect default is wrong.

10. `host-setup/scripts/mdt-io-cap-watcher.sh` (~line 76): the initial
    `docker ps -q` reconciliation snapshot allegedly completes fully BEFORE
    the `docker events` subscription actually attaches (sequential, not
    concurrent), leaving a real gap rather than the claimed zero-gap
    coverage — a container created in that window is caught by neither pass
    and runs uncapped until the next periodic sweep. If confirmed, likely
    fix is starting the `docker events` subscription (or at least opening
    its pipe) BEFORE the `docker ps -q` snapshot, so any container created
    during the snapshot is still caught by the now-already-open events
    stream.

11. `host-setup/scripts/mdt-container-caps.lib.sh` (~line 105):
    `_mdt_apply_container_caps` allegedly silently returns with no retry and
    no log line if the container's `docker-<id>.scope` cgroup directory
    hasn't materialized yet when it's processed (a systemd/D-Bus race under
    load). Lower priority than 1-10 above; if confirmed, at minimum add a
    log line so this failure mode is operator-visible instead of
    indistinguishable from "didn't match any pattern."

12. `host-setup/install.sh` (~line 916, likely mis-cited — the CPU
    auto-detect logic this session added is much earlier in the file, near
    the swap-cascade code around line 130-150; re-locate before trusting
    this line number): the `nproc - reserve` CPU auto-detect floors at 1
    core; on a host where `nproc <= DEV_CPU_RESERVE_CORES`, the "reserve N
    cores for host/production" guarantee silently collapses to reserving
    ZERO cores (the floor kicks in and gives dev.slice all of the only
    core). Per estate memory this host has 8 cores, so this is a
    correctness bug in the general tool rather than an active risk on THIS
    host today — still worth a `WARN` at minimum if the floor was hit
    (meaning the requested reservation could not actually be honored).

13. `scripts/finalize_container_environment.py` line ~293:
    `setup_buildkit_builder()` allegedly checks only whether a buildx
    builder named `host-buildkitd` already exists and reuses it without
    verifying its registered endpoint matches the CURRENT `BUILDKIT_HOST`.
    If a devcontainer rebuild changes the socket path (or points at a
    different host's `mdt-buildkitd`) while `~/.docker/buildx` config
    persists, `docker buildx use host-buildkitd` would silently keep the
    stale endpoint. If confirmed, fix by comparing the existing builder's
    registered endpoint against `BUILDKIT_HOST` and recreating
    (`docker buildx rm` + `docker buildx create`) on mismatch instead of
    unconditionally reusing by name.

14. `scripts/report-image-size-breakdown.py` line ~174: `base_python_build`'s
    broad `/usr/local/lib/python3.*` glob allegedly double-counts bytes also
    attributed to the narrower `mt_glances` leaf under the same directory,
    and the unattributed-bytes diff is never clamped — so the printed
    UNATTRIBUTED figure could go negative and silently read "(OK)" instead
    of surfacing a real accounting bug. Verify by actually running the
    script against a real built image and checking whether the two
    attribution paths genuinely overlap; if so, either exclude
    `mt_glances`'s own subtree from the broader glob or clamp/flag a
    negative unattributed total explicitly as an error rather than "(OK)".

15. `host-setup/scripts/mdt-dev-cap-watcher.py` "line 2026" — **the review
    agent flagged this exact citation as implausible itself** (the file is
    nowhere near 2000 lines). The claimed MECHANISM (same
    once-loaded-at-startup staleness class as finding #6, applied to
    `WATCHER_PER_CONTAINER_*`/`DEV_SWAP_CASCADE_PCT` in the memory watcher
    instead of the IO watcher) is plausible on its face given finding #2's
    confirmed module-level `int(_get(...))` calls sit in exactly this spot,
    but locate the real line/finding yourself; do not search for "line
    2026".

### Findings that are NOT bugs — do not revert

7. `host-setup/scripts/mdt-container-caps.lib.sh` line ~14: the review flags
   that `_mdt_load_config()` no longer aliases old `BENCH_IO_CAP_PCT`/
   `BENCH_IMAGE_PATTERNS`/`BENCH_NAME_PATTERNS` names to their
   `SWEEP_*`/`TESTRUNNER_*`/`BUILDKIT_*` replacements. **This removal was
   deliberate**, done this session per the operator's own explicit words:
   *"no no legacy compat, all clean pls. also no backward references in
   comments."* Do not re-add this fallback.

8. `host-setup/scripts/mdt-dev-cap-watcher.py` line ~108: the review flags
   that the `DEV_CAP_MEMORY_MAX`/`DEV_CAP_GATES_MEMORY_MAX` →
   `WATCHER_PER_CONTAINER_MEMORY_MAX`/`WATCHER_PER_CONTAINER_GATES_MEMORY_MAX`
   rename has no back-compat fallback. **Also deliberate, same operator
   instruction as #7.** Do not re-add this fallback either.

(If a future operator wants EITHER of these back, that's a new, explicit
ask — not something to infer from an unrelated code-review pass that didn't
have this session's conversation context.)

## What to actually do next

1. Re-read this whole file, then open `host-setup/README.md` and
   `host-setup/host-setup.env.example` to get the full design context before
   touching any code (they carry the "why," not just the "what").
2. For each of findings 3–15 (skip 7 and 8), independently re-read the cited
   file at its CURRENT line numbers (they may have shifted from finding #1's
   fix, if applied) and determine: reproduced-as-described / not reproduced /
   already partially mitigated. Do not batch-trust the review's summaries —
   at least 3 of the 15 have self-flagged unreliable line citations, and NONE
   have been through the review's own verifier step.
3. Fix every CONFIRMED bug. Finding #9 (the broken pre-existing render test)
   is the one most likely to be both real and high-impact — check it first.
4. After fixes, re-run the full validation sweep this session already
   established as the bar for this package:
   - `bash -n` on every touched `.sh` file.
   - `python3 -m py_compile` on every touched `.py` file.
   - `python3 -m doctest host-setup/mdt-host-setup-wizard.py` (has 5 passing
     doctests as of this session; must stay green).
   - A full canned-answer dry run of the wizard (pipe ~60 blank lines into
     it with `--skip-run-offer` and fake `--meminfo-path`/`--swaps-path`
     files) to confirm it still completes without a `TemplateError`.
   - A synthetic-`/sys/fs/cgroup`-tree smoke test of `check.sh` (build a temp
     dir with `dev.slice`/`dev-interactive.slice`/`dev-background.slice`/
     `dev-gates.slice`/`dev-buildkitd.slice` subdirs populated with plausible
     `memory.*`/`cpu.max`/`io.max`/`io.bfq.weight` files and a fake
     `host-setup.env`, then run `CG=$tmp CONF=$fakeenv bash check.sh` and
     confirm the OK/WARN/FAIL/INFO lines match expectations) — this session
     used exactly this technique to validate `check.sh`'s new helpers; redo
     it for anything touched in `check.sh` or the units it reads.
   - If `host-setup/tests/test-render.sh` exists and is runnable in this
     environment, run it too (finding #9 concerns it directly).
5. Re-run `git status --short -- modern-debian-tools-python-debug` to
   reconfirm the exact file list is still what's listed above (plus this
   handoff file itself, plus whatever new fix commits touch).
6. Commit ONLY the files under `modern-debian-tools-python-debug/` (this
   handoff file plus the list above) using `git commit --only -- <paths>`
   per this repo's shared-main convention (vbpub `AGENTS.md`/`CLAUDE.md`) —
   never `git add -A`/`git add .` in this shared checkout, and never touch
   the other dirty files from the concurrent RG-55/run-gate-project work
   stream. Standing operator authorization for vbpub merge/push already
   exists (see estate memory `vbpub-operator-standing-authorization.md`) —
   proceed without asking, but DO paste the final commit message and a
   one-line summary of what was fixed vs. what from the review turned out
   not to be a real bug, so there's a record.
7. Use whatever commit-trailer format your OWN current session's system
   reminder specifies (it changes per session/attribution config — don't
   copy a hardcoded trailer from this handoff).
