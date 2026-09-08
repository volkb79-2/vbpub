# Plan — interactive `host-setup.env` wizard

Status: **carved, ready for implementation** (see `git log` for the fix this
plan rides alongside: `install.sh`'s `RENDER_VARS` was missing
`DEV_MEMORY_MIN_GUARANTEED_CEILING` entirely — already fixed separately,
independent of this plan, so the wizard's flagship section actually reaches
a rendered unit file once built).

Companion docs: `CGROUP-NOTES.md` (§"Per-container `memory.min` guarantees"
is the section this wizard's step 5 walks an operator through), `README.md`
("Quick start" gets a companion entry once this ships),
`host-setup.env.example` (the file this wizard replaces hand-editing of).

Sibling package, different repo/subproject:
`ciu/nyxloom-trove/handoffs/ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md`
builds the consumer-side (ciu governance) half of the same memory.min design
— **forbidden territory for this package**, see Scope/forbid below.

---

## Why

`sudo vi /etc/mdt/host-setup.env` (the current "Quick start" step 2) hands an
operator a 296-line file with 30+ tunables spanning IO device discovery, a
disk-saturating benchmark, five slices' worth of memory/CPU/IO shaping, and
Docker daemon keys — sized, in the shipped example, for one specific host
(15.6Gi, ~70G swap, a co-located production game server). There is no guided
path from "I just cloned this" to "a host-setup.env sized for THIS host."
This plan builds that guided path as one standalone interactive script,
without touching the render/apply machinery it feeds.

---

## Context to read first (in this order)

1. `README.md` — "Quick start", "Tiering model", "What gets installed".
   Know the whole system before guiding anyone through one piece of it.
2. `host-setup.env.example` — read the WHOLE file once. This is the
   authoritative list of what to walk, in what order, and the comment above
   each key is the guidance text you adapt (not copy verbatim — see step 5
   below for why the memory-min-guaranteed section specifically must NOT
   reuse this file's own worked numbers).
3. `install.sh` — the `RENDER_VARS` list (~line 108, now includes
   `DEV_MEMORY_MIN_GUARANTEED_CEILING`), the `render()` function (drops any
   directive whose value resolves to empty — "not set" means "not applied,"
   never a fallback number), the IO-device auto-discovery block (`findmnt`
   against `/var/lib/docker` then `/`), the `--with-baseline`/`--force`
   flag handling, and the seed-from-example step this wizard runs BEFORE
   instead of.
4. `CGROUP-NOTES.md` §"Per-container `memory.min` guarantees" — read in
   full. This is the section your step 5 below turns into an interactive
   walkthrough. Note especially "Why not just add MemoryMin to
   dev-background.slice" (the redistribution-leak mechanism) and "The one
   invariant that actually matters" (why `dev.slice` and
   `dev-memory_min_guaranteed.slice` must carry the exact same value) — an
   operator who doesn't understand these two things before typing a number
   will misconfigure it.
5. `scripts/mdt-io-baseline.py` — `parse_args()` (line 75, the `--force`
   flag and env-var-seeded defaults you will invoke this script with, not
   reimplement), `cache_is_fresh(path)` (line 98), `maybe_warn_containers_running()`
   (line 110, already owns the quiet-window warning + 5s abort window —
   reuse by invoking the script, do not duplicate this warning yourself),
   `write_cache_atomic` (~line 237, the atomic-write pattern — temp file in
   the same directory + `os.replace` — your own `/etc/mdt/host-setup.env`
   write must use the identical pattern), `main()` (~line 273).
6. `run-gate.toml` (repo root) — read its header comment. This subproject's
   gate is a `py_compile` syntax smoke over every tracked `.py` file, by
   deliberate policy ("Image-build tooling: no unit suite exists"). Do not
   introduce a pytest requirement this project has explicitly opted out of;
   the existing `scripts/test_*.py` files at the mdt repo ROOT (a different,
   unrelated tool family — `test_install_ai_cli_tools.py` etc.) are not
   evidence this convention applies to `host-setup/scripts/` too. Structure
   your code with cleanly separable pure functions anyway (mirrors
   `mdt-io-baseline.py`'s own separation of parse/compute functions from the
   interactive/IO glue) — for reviewability, not because a test suite is
   required.

---

## Work

1. New file `scripts/mdt-host-setup-wizard.py` — standalone script, no
   package, `argparse`-based `main()`, matching the `scripts/` directory's
   existing convention (every sibling script there is standalone).

2. New `install.sh --wizard` flag: when passed, run this script BEFORE
   (instead of) the existing "seed `/etc/mdt/host-setup.env` from the
   example" block (~line 60-68), then fall through into the SAME
   render/apply logic that already exists, completely unmodified. Do not
   touch anything downstream of the config file existing — the wizard's
   only contract with the rest of `install.sh` is "produce a valid
   `/etc/mdt/host-setup.env`," identical in shape to what a human hand-edit
   would produce.

3. Template-surgery write strategy, NOT generate-from-scratch: read
   `host-setup.env.example` as the base text; for each key the wizard
   walks, replace only that key's `KEY=value` line via a targeted
   substitution (e.g. a compiled `^KEY=.*$` regex per key, applied once each
   — do not use a single blanket regex that could match a key name
   appearing inside another key's comment text). Every comment, blank line,
   and section header is preserved byte-for-byte for every key NOT walked
   interactively. This file is unusually heavily documented — losing that
   documentation on first wizard run would be a real regression, not a
   cosmetic one.

4. Flow — walk `host-setup.env.example`'s own section order. For every
   step, show the CURRENT value (from `/etc/mdt/host-setup.env` if it
   already exists, else the example's own default) as the prompt default;
   Enter accepts it unchanged. Sections, in order:

   a. **IO device** (`IO_DEV_PATH`) — run the same `findmnt` auto-discovery
      `install.sh` already does (`/var/lib/docker` then `/`); offer as
      default, allow override.

   b. **IO baseline** — check `IO_BASELINE_ENV`'s freshness
      (`mdt-io-baseline.py`'s own `cache_is_fresh`, imported as a module —
      `scripts/` has no `__init__.py` so import via
      `importlib.util.spec_from_file_location` or add `scripts/` to
      `sys.path` at runtime, whichever reads cleaner against this
      directory's existing style; if importing proves awkward, invoking it
      as a subprocess with `--force` omitted and inspecting its own stdout
      is an acceptable fallback — do not reimplement its freshness logic
      inline either way). If missing or stale: ask whether to run it now
      (say plainly: ~4 minutes, saturates the disk, quiet window) —
      `subprocess.run(["python3", "<path>/mdt-io-baseline.py"], check=False)`,
      letting IT own the quiet-window warning and the 30-day freshness
      check; do not duplicate either.

   c. **IO cap percentages** (`DEV_IO_CAP_PCT`, `SWEEP_IO_CAP_PCT`) —
      present the 60-80% band reasoning inline (adapted from
      `host-setup.env.example`'s own comment and README's "The IO
      baseline" section: never 100%, a saturated device queues everything
      behind the burst; below ~60% you're just throttling ordinary work).

   d. **Per-tier memory** (`DEV_INTERACTIVE_MEMORY_*`,
      `DEV_BACKGROUND_MEMORY_*`, `DEV_BUILDKITD_MEMORY_*`) — read this
      host's OWN `/proc/meminfo` (`MemTotal`, `MemAvailable`) and swap
      total (`/proc/meminfo`'s `SwapTotal`, or `/proc/swaps`). Propose
      scaled defaults derived from THIS host's numbers, not the shipped
      example's 15.6Gi/~70G-swap figures — state the derivation inline
      (e.g. "this host has N GiB total, M GiB currently available; a
      starting split of ... reserves headroom for production tiers you may
      be sharing this host with"). Let every proposed number be overridden.

   e. **Memory-min-guaranteed ceiling** (`DEV_MEMORY_MIN_GUARANTEED_CEILING`)
      — the section this whole plan exists for. Manual entry (an operator
      types the final number), but the wizard's job is to make that entry
      informed, generically, using THIS host's own data — no
      `CGROUP-NOTES.md` `MEASUREMENTS.md`-style worked example or
      gstammtisch-specific number appears anywhere in this step's text.
      Cover, in this order:
      - **What it is**: a hard floor (unlike `MemoryLow`'s soft/best-effort
        protection) — memory this cgroup keeps even under host-wide
        pressure, protected from reclaim.
      - **Where it's set, and why two places**: `dev.slice` (root) AND
        `dev-memory_min_guaranteed.slice` (a sibling of the interactive/
        background tiers just configured in step d — NOT nested under
        either), pinned to the exact same value on purpose. Explain the
        redistribution mechanism in plain terms: cgroup v2 hands a
        parent's unclaimed protection down to whichever child is using
        memory, proportionally — so a generous number on `dev.slice` alone
        leaks protection to the interactive/background tiers, defeating
        the point of a dedicated guaranteed tier.
      - **The effect / who actually gets protected**: this is opt-in per
        container — a stack must explicitly place itself on
        `dev-memory_min_guaranteed.slice` (`governance.cgroup_parent` in
        its ciu config) and declare its own claim (`governance.mem_min`).
        Setting this ceiling alone protects nothing by itself; it only
        raises the total a stack COULD claim. (Note for the operator:
        ciu's own admission control enforcing this ceiling against live
        claims is a separate, in-flight piece of work — link
        `ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md` by relative
        path in the wizard's printed text, do not re-explain ciu's design
        here.)
      - **A suggestion grounded in THIS host's own numbers**: show current
        `MemAvailable` and what step d's tiers already earmarked (sum of
        the interactive/background/buildkitd `MemoryHigh`/`Max` values just
        configured), then propose a SMALL, explicitly conservative fraction
        of what's left over — state the exact formula and the reasoning in
        the prompt text (not a black-box number). Keep the default answer
        to "set a value at all" **empty** — leaving this unset (mechanism
        stays fully inert, matching `host-setup.env.example`'s own shipped
        default) must be at least as easy to choose as typing a number;
        never make emptiness the awkward path through the prompt.

   f. **buildkitd** (`DEV_BUILDKITD_IMAGE`, `DEV_BUILDKITD_CPU_QUOTA` — note
      inline that leaving CPU quota blank means "auto-detect `nproc - 2`
      cores at install time," a DIFFERENT empty-means convention from every
      other key in this file; say so explicitly so an operator doesn't
      assume "empty = uncapped" here).

   g. **Docker daemon.json keys this tool owns**
      (`DOCKER_DAEMON_CGROUP_PARENT`, `DOCKER_SCOPE_BACKSTOP_MEMORY_*`).

   h. Write the result atomically to `/etc/mdt/host-setup.env` (temp file
      in `/etc/mdt/`, then `os.replace` — mirror
      `mdt-io-baseline.py`'s `write_cache_atomic` pattern exactly, cited
      above). Then offer to run `install.sh` (with `--with-baseline` if
      step b didn't already run the benchmark) immediately, or print the
      exact next command and exit.

5. `README.md` — add the wizard to "Quick start" (as an alternative to, not
   a silent replacement of, `sudo vi /etc/mdt/host-setup.env` — some
   operators will still want the raw file) and a row in "What gets
   installed".

---

## Oracles

This subproject's real gate (`run-gate.toml`'s `smoke` lane) is
`py_compile` over every tracked `.py` file — that must pass, full stop. Do
NOT add a pytest suite; see Context item 6.

Beyond the gate, verify functionally (script these as literal, runnable
shell steps in your REPORT, not prose claims):

1. **Byte-preservation oracle** — run the wizard against a SCRATCH copy of
   `host-setup.env.example` (never the real file), answering every prompt
   with Enter (accept every default). Diff the result against the
   original: every line NOT among the keys actually walked (comments,
   section headers, blank lines, every key from a section this wizard
   doesn't cover) must be byte-identical. **Controlled wrong
   implementation**: swap the targeted per-key regex for a blanket
   substitution across the whole file text and confirm this diff now shows
   spurious changes (e.g. a key name appearing inside another section's
   comment gets corrupted) — this proves the byte-preservation oracle
   actually catches the failure mode it exists for, not just a "looks
   fine" check.
2. **Value-lands oracle** — run the wizard again, this time typing a
   distinct, deliberately-recognizable value at 3-4 prompts spanning
   different sections (e.g. `IO_DEV_PATH`, one `DEV_BACKGROUND_MEMORY_*`
   key, `DEV_MEMORY_MIN_GUARANTEED_CEILING`). Grep the output file:
   each typed value must appear on its own `KEY=value` line, and no OTHER
   key's value may have changed from the example's original.
3. **Empty-stays-empty oracle** — for the memory-min-guaranteed step
   specifically, run the wizard and press Enter through it (accept the
   empty default). Confirm `DEV_MEMORY_MIN_GUARANTEED_CEILING=` (empty) in
   the output, then run `install.sh`'s actual `render()` step (or a
   minimal harness invoking the identical `RENDER_VARS`+`sed` logic) against
   the produced file and confirm the rendered `dev.slice` and
   `dev-memory_min_guaranteed.slice` carry NO `MemoryMin=` line at all —
   this closes the loop against the render-side bug this plan's sibling
   fix (already committed, see `git log -- install.sh`) just closed; don't
   let a wizard regression reopen it from the other direction.
4. **Live-sizing oracle** — confirm step d's and step e's proposed numbers
   actually change when run against two different `/proc/meminfo`
   fixtures (mock the read, don't require two physical hosts) — pin this
   as a real assertion (numbers differ, and both are demonstrably derived
   FROM the fixture's `MemAvailable`, not a hardcoded constant), proving
   "look at this host's own sizing" is real behavior, not a claim in the
   prompt text with a static number underneath.
5. Manual interactive run transcript (paste the actual terminal output, or
   the closest scriptable equivalent — e.g. feeding canned answers via
   stdin) through the FULL flow at least once in your REPORT, end to end.

---

## Scope / forbid

**Touch:** `host-setup/scripts/mdt-host-setup-wizard.py` (new),
`host-setup/install.sh` (the new `--wizard` flag only — do not touch
`RENDER_VARS`, the render/apply logic, or anything else in this file),
`host-setup/README.md` (Quick start + What-gets-installed rows only).

**Forbid:** `ciu/` (any path — Package `ciu-P50` owns the consumer-side
admission-control/injection mechanism; this package never touches ciu
source, even to "help" by pre-wiring something), `host-setup/CGROUP-NOTES.md`
(already accurate and complete for this feature — a wizard that finds its
own explanatory text needs deviates from CGROUP-NOTES.md's design is a
BLOCKED trigger, not a doc edit here), `host-setup/units/*.in`,
`host-setup/host-setup.env.example` (the wizard reads this file, never
writes to it), any file under `modern-debian-tools-python-debug/` outside
`host-setup/`.

If a contract item above requires touching a file not listed in Touch, or
the wizard's proposed numbers/explanations for step e turn out to
contradict CGROUP-NOTES.md's actual design once you re-read it against your
implementation — **STOP**.

**BLOCKED rule:** if a named contract cannot be met as specified, or scope
requires a forbidden file, STOP — write `BLOCKED: <reason>` to your LOG,
commit, and exit. Do NOT improvise a workaround.

---

## Process requirements

- Fresh implementer, zero prior context beyond this document and the live
  repo.
- Real gate: `./run-gate.py smoke` (repo root) — read the verdict in a
  separate step, never off a piped tail.
- LOG/REPORT: `nyxloom-trove/reports/` doesn't exist for this subproject
  (no nyxloom-trove here) — write
  `host-setup/mdt-host-setup-wizard-{LOG,REPORT}.md` instead, same
  per-commit LOG / per-oracle-evidence REPORT convention as the ciu
  handoffs' own process section, adapted since there is no trove `reports/`
  directory to place them in.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K6ZFTkbjEnh4haBGDUGyuJ
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge.
- **Host is shared with a production game server** — 8 cores, load hit 85
  on 2026-09-02 from concurrent xdist pytest + an uncapped gate container:
  serial work under nice/ionice, no builds concurrent with anything else,
  and remember step b's own benchmark saturates the disk for ~4 minutes —
  never run it without checking for other activity first (the script
  already warns; don't suppress or skip past that warning while testing).
- Closing discipline: claim only what you ran, with the actual diff/grep
  output from the oracles above — not a description of what they would
  show.
