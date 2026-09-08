# mdt-host-setup-wizard — LOG

Package: `modern-debian-tools-python-debug/host-setup/plan-host-setup-wizard.md`.
Worktree `/workspaces/vbpub/.worktrees/mdt-host-setup-wizard`, branch
`mdt-host-setup-wizard`, based on `main` at `27a0fcc1` (the sibling
`DEV_MEMORY_MIN_GUARANTEED_CEILING` render fix the plan rides alongside),
plus the plan's own carve commit `7bd8ba67`.

Fresh implementer, zero prior context beyond the plan and the live repo.
Built, functionally verified against every oracle the plan specifies, real
gate run clean. **Not merged to `main`** — a fresh adversarial reviewer
verifies first, per the plan's own Process requirements.

Final commit: **`f9c1623f`** — `feat(host-setup): interactive
host-setup.env wizard (install.sh --wizard)`.

---

## What was built

1. `scripts/mdt-host-setup-wizard.py` — new, standalone (no package,
   `argparse`-based `main()`, matching every sibling script in this
   directory).
2. `install.sh` — new `--wizard` flag only (RENDER_VARS/render()/everything
   else untouched): runs the wizard BEFORE (instead of) the existing
   cp-based seed/re-seed block, backing up any pre-existing
   `/etc/mdt/host-setup.env` first, then falls through into the unmodified
   render/apply logic.
3. `README.md` — Quick start gained the wizard as an alternative to (not a
   silent replacement of) the hand-edit step; What-gets-installed gained a
   row.

## Design

**Walk order** matches `host-setup.env.example`'s own section order exactly
(Work step 4a-g): IO device -> IO baseline freshness check -> IO cap
percentages -> per-tier memory -> memory-min-guaranteed ceiling -> buildkitd
-> Docker daemon.json keys.

**Template-surgery write strategy**: `apply_value()` replaces ONLY the
`^KEY=...$` line for a walked key (anchored, `re.MULTILINE`, captures and
reuses that line's own quote style from the template), applied once per key
against `host-setup.env.example`'s own text — never a blanket/unanchored
match, and never generated from scratch. Raises `TemplateError` if a key
isn't found as an assignment exactly once (a template-drift guard).

**Default resolution** (`resolve_default()`) is the one rule every walked
key goes through: an already-configured host's own value (read from
`/etc/mdt/host-setup.env` if it exists) wins if present; otherwise a
host-scaled proposal (per-tier memory keys only); otherwise the example's
own shipped default. For `DEV_MEMORY_MIN_GUARANTEED_CEILING` specifically,
`proposal=None` is passed unconditionally — the prompt's advisory
"suggestion" text is computed and shown, but the actual DEFAULT (what Enter
selects) is never overridden to it, so leaving the ceiling unset is
structurally exactly as easy as typing a number, never the awkward path
(plan's explicit requirement).

**Host-scaled memory proposals** (`propose_memory_tiers()`,
`propose_memory_min_guaranteed_suggestion()`) read `/proc/meminfo` at run
time (`--meminfo-path`, default `/proc/meminfo`) and `/proc/swaps` as a
fallback if `SwapTotal` is somehow absent from meminfo. HIGH/LOW/MAX figures
scale off `MemAvailable` (what's actually free on THIS host right now,
already net of a co-located production tier); MemoryMin (a small, stable
protection floor) scales off `MemTotal` instead, deliberately. None of
these numbers, nor the percentages themselves, come from
`host-setup.env.example`'s own shipped figures (15.6Gi/~70G swap) or from
`CGROUP-NOTES.md`'s D-373 worked example (367M) — both explicitly forbidden
by the plan.

**Reuse, not reimplementation**: `check_baseline_freshness()` imports
`mdt-io-baseline.py` (hyphenated filename, `importlib.util.spec_from_file_location`)
and calls its own `cache_is_fresh()`; the actual benchmark run is a
`subprocess.run([sys.executable, str(io_baseline_script)], check=False)`
with no `--force`, letting that script own the quiet-window warning and the
30-day freshness re-check. `write_atomic()` mirrors
`mdt-io-baseline.py`'s `write_cache_atomic` pattern exactly (temp file in
the same directory, `fsync`, `os.replace`).

## Two real bugs caught and fixed during implementation (before commit)

1. **`walk_key()` parameter order.** First draft defined
   `walk_key(key, label, ...)` but every call site (written afterward, more
   naturally) passed `(label_text, KEY_NAME, ...)` — reversed at every call
   site. Caught by an actual end-to-end run: the printed prompts read
   backwards (`"IO_DEV_PATH (Block device node...)"`), and — more
   seriously — `resolve_default()` was looking up the LABEL text as the
   dict key against `cfg_current`/`example_defaults`, which would never
   match anything, silently degrading every "already configured" default to
   the fallback path. Fixed by swapping the definition's parameter order to
   `(label, key, ...)` to match every call site (rather than fixing ~15 call
   sites individually).
2. **Degenerate memory-min-guaranteed suggestion.** First percentage choice
   for the three per-tier `MemoryHigh` proposals (interactive 35% +
   background 35% + buildkitd 30% of `MemAvailable`) summed to exactly
   100%, which made step e's "leftover after step d's tiers" always
   compute to ~0 on EVERY host — a degenerate, non-host-varying result that
   would have silently failed the live-sizing oracle's actual intent (a
   real, host-varying suggestion). Caught by running the live-sizing oracle
   itself (§4 below) and noticing both fixtures produced 0. Fixed by
   lowering the three percentages to 30/30/25 (85% combined), leaving
   genuine headroom; re-verified against both fixtures afterward (§4 in the
   REPORT) — now a real, non-degenerate, host-varying number.

## Gate

Real gate, run against the clean committed tree (`f9c1623f`):

```
cd modern-debian-tools-python-debug && python3 run-gate.py smoke
```

**PASS**, exit 0. Verdict read in a separate step from the run (redirected
to a file, then read on its own, never off a piped tail) — full transcript
in the REPORT.

## Oracles (functional verification beyond the gate)

All five oracles from the plan run for real against scratch copies of
`host-setup.env.example` (never `/etc/mdt/host-setup.env`, never the
tracked `host-setup.env.example` itself) — full commands and literal output
in the REPORT:

1. **Byte-preservation** — an all-defaults run diffs to exactly the walked
   keys' proposal values, nothing else; the controlled-wrong-implementation
   demonstration (a blanket, unanchored substitution for `DEV_IO_CAP_PCT`)
   corrupts 3 additional comment lines that merely mention the key's name in
   prose (lines 9, 172, 183 of the real example file), confirming the
   anchored implementation is load-bearing, not cosmetic.
2. **Value-lands** — 3 typed canary values across 3 different sections
   (`IO_DEV_PATH`, `DEV_BACKGROUND_MEMORY_SWAP_MAX`,
   `DEV_MEMORY_MIN_GUARANTEED_CEILING`, the plan's own suggested example)
   land on their own `KEY=value` lines; every other diff line matches the
   all-defaults run exactly (no unintended key was touched).
3. **Empty-stays-empty** — accepting the ceiling's empty default, then
   rendering the produced file through a harness that reproduces
   `install.sh`'s exact `RENDER_VARS`/`render()` logic verbatim: neither
   `dev.slice` nor `dev-memory_min_guaranteed.slice` carries a `MemoryMin=`
   line. The positive case (a typed `321M`) renders the identical value
   into both slices, confirming "the one invariant that actually matters"
   survives the wizard -> render pipeline in both directions.
4. **Live-sizing** — two synthetic `/proc/meminfo` fixtures (32G/24G-avail/
   8G-swap vs. 8G/2G-avail/no-swap) produce genuinely different proposals at
   every step-d key and a genuinely different, non-degenerate step-e
   suggestion (150M vs. 0, both correctly derived from each fixture's own
   `MemAvailable` arithmetic shown in the printed formula).
5. **Full manual transcript** — one complete run, stdin from `/dev/null`
   (every prompt, including the trailing "run install.sh now?", accepts its
   default), captured verbatim in the REPORT.

## Scope

Touched exactly the three files the plan's Touch list names:
`host-setup/scripts/mdt-host-setup-wizard.py` (new),
`host-setup/install.sh` (the `--wizard` flag only — diff confirmed nothing
else changed), `host-setup/README.md` (Quick start + What-gets-installed
rows only). Nothing under `ciu/`, `CGROUP-NOTES.md`,
`host-setup/units/*.in`, or `host-setup.env.example` was touched (the
render harness used for oracle 3 reads `units/*.in` read-only and is not
part of the commit — a throwaway oracle script, not shipped).

No BLOCKED condition was hit — every contract item in the plan was
buildable within the stated scope.

---

## Commits

1. `f9c1623f` — `feat(host-setup): interactive host-setup.env wizard
   (install.sh --wizard)` — all three touched files.
2. (this commit) — `docs(host-setup): mdt-host-setup-wizard LOG/REPORT`.
