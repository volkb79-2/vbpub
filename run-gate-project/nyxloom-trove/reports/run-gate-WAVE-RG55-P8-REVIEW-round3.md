# run-gate-WAVE-RG55-P8 — adversarial review, round 3 (final)

**Verdict: ACCEPT.** Unambiguously: `mdt-dev-slices` at `e326cc9b` is
mergeable into `main` as it stands. Both round-2 blockers (B7, B8) are fixed
and independently re-verified with my own probes; every accepted S-item
landed; the registered gate is green; nothing blocking remains. One
residual, explicitly **non-blocking**, is recorded below (R1) for whoever
next edits `check.sh` — it does not need to land before merge, and I am not
asking for a round 4.

Base `main@11ac5d67`, round-2 tip `ae38d55a`, round-3 tip `e326cc9b`
(4 commits). No host mutation (no `systemctl`, no `systemd-tmpfiles`, nothing
written under `/etc`), no commits to the branch, the registered gate run
**once**, everything under `nice -n 19` / `ionice -c 3`. Host memory PSI
checked first: `full avg10 = 0.85`.

---

## B7 — byte-size parser — **PASS**

`scripts/check.sh:69-97` is my prescribed `awk` parser verbatim, and the
call site (`:119-131`) routes `?` to a `warn` naming the form, never to the
`fail` branch. Two independent probes.

**1. The function itself**, extracted verbatim and driven directly
(awk in use on this host: `mawk 1.3.4`, so large-value exactness was worth
checking, not assuming):

```
_bytes_of "6G"       -> 6442450944        _bytes_of "50%"      -> ?
_bytes_of "4.5G"     -> 4831838208        _bytes_of "bogus"    -> ?
_bytes_of "7.5G"     -> 8053063680        _bytes_of "infinity" -> ?
_bytes_of "10.5G"    -> 11274289152       _bytes_of "max"      -> max
_bytes_of "32G"      -> 34359738368       _bytes_of ""         -> <empty>
_bytes_of "1T"       -> 1099511627776     _bytes_of "1024"     -> 1024
_bytes_of "2Gi"      -> 2147483648        _bytes_of "3GB"      -> 3221225472
```

No bash arithmetic remains, so no input can produce a syntax error. Exactness
cross-checked against Python for `6G`/`32G`/`48G`/`1T` — identical, so mawk's
`printf "%d"` is not truncating at 2^31 (all are well under 2^53). The `Gi`
and `GB` spellings systemd also accepts parse correctly, which the regex
handles deliberately.

**2. End-to-end through the real `check.sh`**, against a fake `CG` tree and a
fake `$CONF` with `systemctl`/`findmnt`/`docker` stubbed on `PATH` — hermetic,
no host access — covering exactly the coordinator's checklist:

```
4.5G / 2.5G, kernel matches   OK   memory.max=4831838208 matches DEV_GATES_MEMORY_MAX=4.5G
                              OK   memory.high=2684354560 matches DEV_GATES_MEMORY_HIGH=2.5G
50% / 30%                     WARN DEV_GATES_MEMORY_MAX=50% is not byte-comparable … not checked
                              WARN DEV_GATES_MEMORY_HIGH=30% is not byte-comparable … not checked
6G / 4G, kernel = max         FAIL memory.max=max but DEV_GATES_MEMORY_MAX=6G (6442450944 bytes) …
                              FAIL memory.high=max but DEV_GATES_MEMORY_HIGH=4G (4294967296 bytes) …
6G / 4G, kernel matches       OK / OK
keys absent from $CONF        WARN / WARN  (unchanged, still correct)
```

`4.5G` → OK, `50%` → WARN not FAIL, `max` while configured → FAIL. Exactly as
specified, and no stderr noise anywhere. The round-2 defect (a correctly
configured host hard-failing `mdt-host-check.sh`, exit 1) is gone.

**3. The regression assertion (9) is real.** `tests/test-render.sh:216-283`
extracts `_bytes_of` out of `check.sh` with the *same* guarded-sed discipline
B2 established — start anchor, end anchor, host-mutation content scan before
`source` — which is the right shape and means a future `_bytes_of` cannot
drift from what `check.sh` runs. I reverted the parser to the round-2
bash-arithmetic implementation in a scratch copy:

```
FAIL: _bytes_of '4.5G' -> '', want '4831838208'
FAIL: _bytes_of regression case failed (B7) -- see stderr above        exit 1
```

**RED, on the exact case.**

### Residual R1 (non-blocking, recorded as the coordinator asked)

I am **fine with `warn`** for a non-byte-comparable value — it was my own
prescription and the implementer applied it literally and correctly. Naming
the one thing it costs, for the record and for whoever next touches this file:

`check.sh:119-126` cannot distinguish *legal-but-uncheckable* (`50%`, which
systemd accepts and applies) from *illegal* (`bogus`, which systemd rejects,
dropping the directive and leaving the slice **unbounded**). Both take the `?`
path. Verified live on the fake host:

```
DEV_GATES_MEMORY_MAX=bogus, kernel memory.max=max
  WARN DEV_GATES_MEMORY_MAX=bogus is not byte-comparable (percentage or non-size form) -- effective memory.max=max not checked
```

So a typo'd size yields an unbounded gates slice with a `warn` rather than the
B3 `fail`. Three things keep this from blocking: the WARN names the variable
**and prints `effective memory.max=max` in the same line**, so the unbounded
state is visible, not hidden; `install.sh`'s `MISSING_KEYS` WARN and the
wizard's `validate_size_required` cover the two ways the key normally gets
set; and the whole class is a typo in a root-edited file, not a default.
If anyone wants it closed later, it is four lines — split the `?` arm:

```bash
if [ "$cfg_bytes" = "?" ]; then
  case "$cfg" in
    *%) warn "$var=$cfg is a percentage -- effective $prop=$eff not byte-comparable, not checked" ;;
    *)  fail "$var=$cfg is not a valid systemd size -- systemd will reject the directive and drop it, leaving $prop=$eff (unbounded). Fix the value in $CONF and re-run install.sh." ;;
  esac
```

and extend assertion (9) with `check "50%" "?"` (already there) plus a call-site
case. Not required for merge.

---

## B8 — the fail-open claim — **PASS**

`README.md:170-184` now reads, in full and correctly:

> …it protects a **devcontainer that was not rebuilt** …, **not** a **host
> that was not upgraded** (the slice unit missing). That case is **NOT
> self-announcing**: a `--cgroup-parent` naming a slice with no unit file
> fails **open** — systemd auto-creates an unlimited transient slice and the
> container starts normally (`units/docker-scope-default-limits.conf.in`,
> `AGENTS.md`), which is exactly why the `docker-.scope.d` backstop exists.
> It is caught by `mdt-host-check.sh` (`dev-gates.slice has no unit file`, a
> hard FAIL) and by a consumer that verifies `LoadState=loaded` before launch,
> as `AGENTS.md` already requires — **not** by docker …

This now agrees with all four in-repo statements of the verified behaviour
(`AGENTS.md:108`, `docker-scope-default-limits.conf.in:9-12`,
`host-setup.env.example:287-289`, `check.sh:193`), and it names catch
mechanisms that exist **today** — `mdt-host-check.sh`'s hard FAIL is real
(`check.sh:25-35`, `dev-gates` is in that loop; verified in round 1) and the
`LoadState=loaded` rule is already normative in `AGENTS.md`. Naming those
rather than run-gate `doctor`/`ctl host` is more accurate than the
coordinator's paraphrase, since those are P5/P6 work that does not exist yet;
I prefer what shipped.

Branch-wide sweep for the withdrawn wording
(`git grep -niE "fail(s|ing)? outright|docker (run )?refus"` at `e326cc9b`
over `modern-debian-tools-python-debug`, `AGENTS.md`, the P8 records):
the only hits are three pre-existing, unrelated uses about *a container that
OOMs* (`host-setup.env.example:78`, `mdt-host-setup-wizard.py:1015`,
`dev-gates.slice.in:27`) and `README.md:183`, which is the sentence
*withdrawing* the claim. **No live instance of the wrong claim remains**, in
the README or the REPORT.

---

## Accepted S-items

| item | result |
|---|---|
| **S15(r2)** `mdt-cgprofile.conf` everywhere | **PASS.** `git mv` preserved (`R100` in the REPORT's own name-status). Live paths all renamed: `install.sh:204,218,219,220`, `check.sh:188,198,211`, `test-render.sh:206,211-213`, `devcontainer.json:83`, `README.md:249`. Branch-wide `git grep tmpfiles-cgprofile` returns only the LOG/REPORT's historical record of the rename and one `test-render.sh` comment quoting the *old* assertion — no live path. |
| **S16** full-contention share figures | **PASS, and they match my own recomputation exactly.** `README.md:151-158` now states CPU `200/290` = 69.0% with buildkitd and `200/(200+20+20+50+100)` = **51.3%** five-way, IO **37.0%**, and explicitly says "That 83%/83% pair is not the actual worst case". It correctly notes `dev-memory_min_guaranteed.slice` declares neither directive so systemd's default 100 applies — which is the subtle half of the finding. |
| **S20(a)** assertion (7) non-vacuous | **PASS, demonstrated.** Comment-only match (`install` line commented out, filename still present in the text): **RED** — "install.sh has no real 'install … units/mdt-cgprofile.conf … /etc/tmpfiles.d/mdt-cgprofile.conf' line … a comment mentioning the filename is not enough". Removing the `systemd-tmpfiles --create` line: **RED** — "install.sh never applies the cgprofile tmpfiles.d entry". (The second half was my suggestion for symmetry with assertion 5; it is there and it works.) |
| **S20(b)** assertion (8) non-vacuous | **PASS, demonstrated.** Deleting the doctest: `python3 -m doctest -v` itself prints `0 passed. / Test passed.` and **exits 0** — confirming the vacuity was real — while the assertion goes **RED**: "doctest reported 0 tests (would pass vacuously on a deleted doctest, S20(b))". |
| **S19** ordering constraint | **PASS.** `README.md:250-259`, its own bolded paragraph in "## Changes": install this host-setup version **BEFORE** anyone rebuilds a devcontainer from the template, with the reason (`--mount` refuses a missing source) and the consequence stated plainly ("will fail to start, not merely run with a missing feature"). The upgrade sequence also enforces the order by construction — `install.sh` first, then "Then rebuild/recreate your devcontainer". |
| **S17** REPORT `Tip:` | **PASS (as ruled acceptable).** It now states the verifiable command (`git -C .worktrees/mdt-dev-slices log -1 --format=%H`) and names the last code commit the gate ran against, instead of a hash that cannot be self-referential. Better than what I asked for. |
| **S18** REPORT S-labels | **PASS.** The "S7" row is relabeled S14 (the real TMPDIR finding) and the falsely-labeled "S14" row no longer claims an S-number. |
| **S21** B4 line-number citation | Pre-adjudicated non-blocking; not re-raised. |

Pre-adjudicated and not re-raised, as instructed: the commented-out BuildKit
mount precedent vs the unconditional `/run/cgprofile` mount (ruled D-30),
and `check.sh`'s flat-vs-nested cgroupfs convention.

---

## Gate

`nice -n 19 ionice -c 3 ./run-gate.py smoke` from
`.worktrees/mdt-dev-slices/modern-debian-tools-python-debug`, run **once**,
verdict read in a separate step:

```
ok: render()/RENDER_VARS extracted from install.sh and guarded (anchors + host-mutation scan)
ok: every rendered unit is free of unresolved @VAR@ placeholders
ok: dev-gates.slice renders every DEV_GATES_* key from the shipped example, and ManagedOOMSwap=kill stays withdrawn
ok: dev.slice and dev-memory_min_guaranteed.slice render with no MemoryMin (opt-in, unset by default)
ok: no units/*.in template mentions the withdrawn dev-infra.slice
ok: install.sh renders every units/*.in, and starts every *.slice
ok: every @VAR@ placeholder used in a template has a matching assignment in host-setup.env.example
ok: install.sh installs and applies the cgprofile tmpfiles.d entry
ok: wizard's earmark-sum doctest (before/after dev-gates.slice's MemoryHigh) passes, and actually ran a non-zero test count
ok: check.sh's _bytes_of parses legal-systemd non-integer sizes (4.5G) to the correct byte count and returns ? … for non-byte-comparable forms (50%, bogus)
ok: bash -n / py_compile clean on every touched script
ok: shellcheck (warning severity+) clean on every touched shell script
test-render: ALL OK
smoke: OK
run-gate: lane 'smoke' exit 0
```

No leftover container. Worktree clean. Shared checkout untouched — the same
seven pre-existing dirty mdt files as at session start, `host-setup/` and
`AGENTS.md` clean there. Every mutation in this round was applied to a scratch
copy and reverted (`diff -r` identical afterwards, modulo `__pycache__`).

Final scope check, `git diff --name-only 11ac5d67 e326cc9b` — 20 files, all
in scope: `AGENTS.md`, `modern-debian-tools-python-debug/{DEVCONTAINER-LIFECYCLE.md,
run-gate.toml, templates/devcontainer.json, host-setup/**}`, and the two P8
records. Nothing under `run-gate-project/` source, `scripts/cgroup-profiler/`,
`ciu/`, or any other project.

---

## Claims I could not verify (unchanged across all three rounds)

1. **Anything requiring a real host.** No `install.sh`, no `systemctl`, no
   `systemd-tmpfiles` except `--dry-run` (round 2), nothing written under
   `/etc`. So the live behaviour of the tmpfiles entry, the cap watcher
   against a real new scope, `check.sh`'s `/run/cgprofile` mode/owner branch,
   and `systemctl start dev-gates.slice` are reasoned from source and from
   hermetic fakes, not observed on the host. **The operator's own
   `install.sh` + `mdt-host-check.sh` run is the first real-world
   confirmation** — expect `check.sh` to report `dev-gates.slice` OK/OK on
   memory.max/high and `/run/cgprofile exists, mode 0770, owner root:docker`.
2. **Whether `systemd-oomd` is installed and running** on the target host —
   every `ManagedOOM*` directive is a silent no-op without it.
3. **The 2026-08-04 memory finding** (session-memory file, not repo-tracked)
   — taken from the design doc's §1 row.
4. **`docker --mount` refusing a missing source** — documented Docker
   behaviour, not re-proven here (I did not spend a container on it). The
   template, the tmpfiles header, `check.sh`'s hard FAIL and RW-32 all rest
   on it, and the ordering constraint (S19) is now documented either way.
5. **The REPORT's process claims** (checkpoint clause, "ran ONCE per round")
   — no external evidence available to a reviewer.

## Merge note for the controller

Merge `--no-ff`. Two things to carry forward, neither blocking:

* **Operator ordering:** install this host-setup version on the host
  **before** anyone rebuilds a devcontainer from `templates/devcontainer.json`
  (`/run/cgprofile` does not exist on the host today — I checked). The README
  states it; the merge announcement should repeat it.
* **Tell P6** that `/etc/tmpfiles.d/mdt-cgprofile.conf` is mdt-owned, so the
  cgroup-profiler deployment does not ship a second entry for the same
  directory.
* Residual R1 above is worth a line in whatever backlog tracks mdt follow-ups,
  not a package.
