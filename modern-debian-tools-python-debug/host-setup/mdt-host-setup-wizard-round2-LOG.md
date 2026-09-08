# mdt-host-setup-wizard round 2 — LOG

Package: `modern-debian-tools-python-debug/host-setup/plan-host-setup-wizard-round2.md`.
Worktree `/workspaces/vbpub/.worktrees/mdt-host-setup-wizard-r2`, branch
`mdt-host-setup-wizard-r2`, based on `main` at `d9a0fc24` plus the plan's own
carve commit `c23967f1`.

Follow-up to `plan-host-setup-wizard.md` (round 1, shipped, merged `8fc9dccb`;
its own LOG/REPORT are the `mdt-host-setup-wizard-{LOG,REPORT}.md` pair beside
this file, and this pair continues that convention with a `-round2` infix).

Fresh implementer, zero prior context beyond the plan and the live repo. All
nine items implemented, verified by real scratch runs, real gate run clean.
**Not merged to `main`** — a fresh adversarial reviewer verifies first, per the
plan's own Process requirements.

Code commit: **`7a02c8a9`** — `fix(host-setup): wizard round 2 --
resolve_default() blank-value bug, memory-min invariant check, UX pass`.

No BLOCKED conditions. No forbidden file touched.

---

## What changed

| File | Change |
|---|---|
| `host-setup/scripts/mdt-host-setup-wizard.py` → `host-setup/mdt-host-setup-wizard.py` | `git mv` (item 6), mode `100644` → `100755` (item 5), and rewritten for items 1/2/3/4/8 |
| `host-setup/install.sh` | `--wizard` block's invocation path, and the header comment's path reference (item 6) |
| `host-setup/README.md` | the "What gets installed" row's path (item 6) |
| `host-setup/scripts/check.sh` | new `dev-memory_min_guaranteed.slice` verdict section (item 9) |

Untouched, deliberately: `scripts/mdt-io-baseline.py` (reused, never modified),
`CGROUP-NOTES.md` (read for item 9's invariant, not edited), `ciu/` (a
different subproject), and every `io.cost`/latency measurement question (item 7
is informational-only).

---

## Item-by-item

### 1. `--with-baseline` is no longer re-asked after a successful run

`step_io_baseline()` returned `None`; it now returns `bool`. `True` **only**
when this call actually invoked `mdt-io-baseline.py` and got exit 0. Every
other path returns `False` — cache already fresh, operator declined, script
missing, nonzero exit. `main()` threads that through as
`baseline_measured_now` and, when it is `True`, replaces the `"Also pass
--with-baseline…?"` sub-question with a printed line explaining the skip
rather than silently omitting the prompt.

One edge worth naming for the reviewer: `check_baseline_freshness()` falls back
to reporting `"stale"` when importing `mdt-io-baseline.py` genuinely fails, and
`mdt-io-baseline.py`'s own `main()` returns 0 both when it measured and when it
found its cache already fresh and just re-printed it. So a
failed-import + already-fresh-cache host can reach `True` without a benchmark
having actually run. That is the right answer anyway — the cache *is* fresh, so
re-running it under `--with-baseline` would still buy nothing — but it means
the returned bool is precisely "there is a fresh measurement as of now", which
is what the decision needs, rather than literally "fio spun the disk".

### 2. Input validation

`ask()` and `walk_key()` take `validate: Callable[[str], str | None] | None`.
`None` back means acceptable; a string is an error message, printed, and the
prompt is re-asked. Eight validators, wired to every field with a real shape
constraint.

Three design calls the reviewer should weigh:

- **Size strings reuse `_SIZE_RE`, not `parse_size_to_kib(...) > 0`.** The
  plan's instruction was to reuse `parse_size_to_kib` and not write a second
  parser; the plan also describes it as "already raises on garbage", which it
  does not — it returns `0` for garbage *and* for the string `"0"`. Since
  `propose_memory_tiers()` itself produces `"0"` for every swap key on a
  swapless host, a `> 0` test would reject the wizard's own proposal. The
  validators therefore call a new one-line `is_size_string()` that matches
  `parse_size_to_kib`'s **own** `_SIZE_RE` — the same parser, not a second one.
- **The 60-80 IO-cap band WARNs; 1..100 hard-refuses.** Reasoning in the
  REPORT §Judgment calls.
- **A non-`.slice` cgroup-parent WARNs; empty hard-refuses.** Same section.

EOF safety: a validation loop that re-prompts an exhausted stdin would spin
forever. `ask()` detects `EOFError`, and if the defaulted value is *also*
invalid it prints a `WARN:` line and accepts it rather than looping — visible
in the transcript, never silent, never a hang. This is only reachable when the
DEFAULT itself is invalid.

### 3. Terminal-width-aware output + highlighting

One module-level `out()` replaces every human-facing `print()`. Width is
`min(shutil.get_terminal_size(fallback=(80,24)).columns, 120)`; reflow is
`textwrap.wrap` with `break_long_words=False, break_on_hyphens=False` so device
paths, unit names and `--flags` are never cut in half. Each source line is one
logical paragraph, wrapped independently, so blank lines and the two bullet
lists survive; `indent`/`hang` give the bullets their alignment.

Highlighting convention: `` `backtick spans` ``, matching the Markdown
convention every comment and doc in this repo already uses — so the plain-text
rendering reads correctly on its own rather than looking like leftover markup.
Colour gate is the estate's existing three-part test, copied from
`ciu/src/ciu/output.py` and `cmru/src/cmru/output.py`: `isatty` **and**
`NO_COLOR` unset **and** `TERM != dumb`.

Two deliberate carve-outs:
- Multi-word backtick spans are protected from wrapping (spaces inside a span
  are swapped for a sentinel before `textwrap`, restored after), so a span can
  never be split across a line boundary leaving an unmatched backtick.
- `input()` prompts carry **no ANSI, ever**, and prompt labels contain no
  backtick spans. readline counts escape bytes as printable columns when it
  redraws during line editing, so a coloured `input()` prompt corrupts cursor
  arithmetic the first time the operator presses an arrow key. Colour lives on
  the explanatory text; long prompts are instead split so the editable tail is
  short.

### 4. Help text

Reworked throughout: more verbose, one claim per sentence, explicit about the
*why* where the original assumed it. All 27 distinct technical claims in the
original prose were enumerated and checked to survive — the list is in the
REPORT. Nothing weakened, nothing dropped.

(The plan's item-4 examples mention "the `--replace` rotation model" among the
facts to preserve. There is no `--replace` prose anywhere in the wizard — the
nearest thing is `install.sh`'s own `--force` backup-and-reseed, which the
wizard does not describe. Nothing to preserve, and nothing was removed.)

### 5 + 6. Executable, and moved beside `install.sh`

`git mv scripts/mdt-host-setup-wizard.py mdt-host-setup-wizard.py`, mode 755
(git records `100755`). Rationale added to the file header and the README row:
`scripts/` is the payload `install.sh` *installs* onto the host
(`/usr/local/sbin/…`); this wizard is never installed, it only ever runs out of
a checkout, exactly like `install.sh` itself.

Path constants:
- `HERE` is now `host-setup/`, so `HOST_SETUP_DIR = HERE` (was `HERE.parent`,
  which would now resolve one level too far up, to the subproject root).
- `DEFAULT_IO_BASELINE_SCRIPT = HOST_SETUP_DIR / "scripts" / "mdt-io-baseline.py"`
  — `mdt-io-baseline.py` did **not** move; it is installed onto the host, so it
  stays with the rest of the payload.
- `DEFAULT_EXAMPLE`, `DEFAULT_INSTALL_SCRIPT` unchanged in expression, correct
  by construction once `HOST_SETUP_DIR` is right.
- `CIU_P50_RELATIVE_PATH` **unchanged**, as the plan predicted. Verified rather
  than assumed: it is written relative to `host-setup/`, and `host-setup/` is
  now exactly where the script lives, so `HOST_SETUP_DIR / CIU_P50_RELATIVE_PATH`
  resolves to a file that exists (REPORT §6).

### 7. `io.cost` — no code change

Informational per the plan. Nothing done, nothing claimed.

### 8. `resolve_default()` — the real bug

```python
-    if key in cfg_current:
+    if key in cfg_current and cfg_current[key]:
         return cfg_current[key]
```

Reproduced against the unmodified pre-fix code, fixed, and both regression
guards checked for real (REPORT §8). The function's docstring now carries the
whole explanation, including why an empty value can no longer be "remembered"
and why that costs nothing observable.

### 9. `mdt-host-check.sh` memory-min-guaranteed section

New section between the `dev.slice` IO-caps section and the `docker-.scope.d`
backstop section, in the existing `ok`/`warn`/`fail` house style.

- Inert (unit has no `FragmentPath`, or its `MemoryMin` is unset/`0`) → `OK`.
  Never a WARN: `install.sh`'s `render()` drops a directive whose value
  resolves to empty, so an unset `DEV_MEMORY_MIN_GUARANTEED_CEILING` leaves
  both slices with no `MemoryMin` at all, and that is the shipped default.
- Opted in and the two values match exactly → `OK`, quoting the value.
- Opted in and they differ → `FAIL`, naming both values, both failure
  directions (dev.slice higher = leak; lower = the leaf's floor is inert), and
  the fix (one env var, re-run `install.sh`).

Verified against five constructed fixtures via a `systemctl` test double on
`PATH` — no root, no systemd needed (there is none in this container). The
existing checks in this script have no test harness of any kind, so this is a
new verification technique rather than a matched one; the double only answers
`systemctl show <unit> -p <PROP> --value`, which is exactly the call shape the
surrounding checks already use.

---

## Gate

`python3 run-gate.py smoke` from `modern-debian-tools-python-debug/`, against
the clean tree at `7a02c8a9`: **PASS, exit 0**. Verbatim output in the REPORT.
Run under `nice -n 10 ionice -c3` and the verdict read in a separate step, per
the shared-host rule and LESSONS L4.

No pytest suite was added — this subproject's gate is `py_compile` only by
deliberate policy (`run-gate.toml`'s own header comment).

## Host-load discipline

The real IO baseline benchmark (~4 min of saturated disk) was **never run**.
Item 1's three decision paths were exercised with stub `--io-baseline-script`
scripts exiting 0 and 3, and the fourth (cache already fresh) with the real
`mdt-io-baseline.py` against a pre-seeded scratch cache file — the injectable
seams the CLI already provides. `install.sh` was likewise never executed; the
end-of-run offer was pointed at a stub via `--install-script`. Every scratch
artifact lives in a private scratchpad directory; `/etc/mdt/host-setup.env` and
`host-setup.env.example` itself were never written to.

Host load at gate time was 6.62 on 8 cores with live peer stacks present; the
lane is a seconds-long `py_compile` in one container, run niced, with no other
gate container of mine alongside it.
