# P-physical-root-mount-table — REPORT

Date: 2026-07-15 · Result: **done** · Base commit: `ce70ef7` (vbpub HEAD at start)

## Root cause (confirmed live before the fix)

`_detect_physical_repo_root()` (`src/ciu/workspace_env.py`) ignored its
`repo_root` argument beyond the pre-set-env check: its only detection path
ran `docker ps --format '{{.Label "devcontainer.local_folder"}}'` and took
the first non-empty line — i.e. the **devcontainer's own** origin label,
identical for every `repo_root` passed to it. Confirmed live:
`/workspaces/vbpub/ciu.env` (tracked, pre-fix) held
`PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"`, `REPO_NAME="dstdns"`,
`INSTANCE_ID="98535c"` — the dstdns devcontainer's own identity, not vbpub's.

## Fix

Added a new primary detection step to `_detect_physical_repo_root()`,
inserted between the pre-set-env check (S2.7: a pre-set value always wins)
and the pre-existing docker-ps fallback:

- `_parse_mountinfo(text)` — parses `/proc/self/mountinfo` lines into
  `(mount_point, mount_root)` pairs (fields 4/5 before the literal `" - "`
  separator; stable regardless of the variable-length optional-fields
  block). The rootfs entry (`mount_point == "/"`) is excluded — it carries
  no per-repo bind-mount signal.
- `_physical_root_from_mountinfo(repo_root)` — ports the longest
  destination-prefix-match algorithm from `physical_path()` in
  `tls-edge/scripts/render_standalone.py` (which does the same thing
  against `docker inspect` Mounts for a *sibling* container). Here we only
  ever need this process's own mount table, so the port reads
  `/proc/self/mountinfo` directly instead of shelling out to
  `docker inspect`. Returns `None` (not identity) when unreadable or no
  destination prefix-matches, so callers can fall through per Contract 2.
- `_detect_physical_repo_root()` now tries: pre-set `PHYSICAL_REPO_ROOT` env
  → mountinfo longest-match → existing docker-ps devcontainer-origin
  fallback → identity (native host, S1.9). No signature change.

**Contract 3** (REPO_NAME / INSTANCE_ID / DOCKER_NETWORK_INTERNAL): no code
change needed — `_compute_network_name(physical_root)` already derives all
three from whatever `physical_root` it's handed, so fixing Contract 1
automatically corrects them for the right repo. **Flag for the spec
maintainer** (not touched — spec edits are out of scope): `docs/SPEC.md`
S2.7's `PHYSICAL_REPO_ROOT` detection row still reads
*"`devcontainer.local_folder` label via `docker ps`; native host:
`= REPO_ROOT`"* — it does not mention the mountinfo path at all and should
be updated to describe the new precedence order.

**Contract 5** (`--define-root`): also no separate code change — the CLI
already threads `--define-root` through `resolve_env_root` →
`bootstrap_env_init(env_root)` → `generate_ciu_env(env_root)` →
`_detect_physical_repo_root(env_root)` unchanged; it "didn't reach the
physical derivation" only because the physical derivation itself was
origin-blind. Fixing Contract 1 fixes Contract 5 as a consequence
(confirmed by the Oracle 4 live smoke run below).

## Files touched

- `src/ciu/workspace_env.py` — added `_MOUNTINFO_PATH`, `_parse_mountinfo`,
  `_physical_root_from_mountinfo`; extended `_detect_physical_repo_root`.
  No signature or behavior change to any other public function.
- `tests/tests/test_physical_root_mount_table.py` (new) — 14 tests.
- `handoff/reports/P-physical-root-REPORT.md` (this file).

No commits made (per Rules). `tls-edge` not touched. `src/ciu/paths.py`
(`to_physical_path`) not touched.

## Oracles

| # | Oracle | Result |
|---|---|---|
| 1 | mountinfo fixture longest-match (incl. nested catch-all losing to specific) | **PASS** — `TestMountinfoLongestMatch` (8 tests) |
| 2 | Fallback when repo_root absent from mountinfo fixture — characterized pre-existing behavior | **PASS** — `TestFallbackWhenMountinfoYieldsNothing` (3 tests: preset-env-still-wins, docker-ps fallback, identity fallback) |
| 3 | Contract 4 regression bound (dstdns-shaped fixture) | **PASS** — `TestRegressionBoundDstdns` (2 tests: unit-level + `generate_ciu_env` end-to-end) |
| 4 | Live smoke | **PASS** — see below |
| 5 | Full suite green | **PASS** — see below |

**Oracle 4 — live smoke (commands run, output below):**

```
$ cd /workspaces/vbpub && /workspaces/dstdns/.venv/bin/ciu env generate
[INFO] Generating ciu.env (S2.8 bootstrap)...
[INFO] Creating docker network: vbpub-fae1b8-network
[INFO] Connecting devcontainer dstdns-devcontainer-vb to vbpub-fae1b8-network
[SUCCESS] Generated /workspaces/vbpub/ciu.env

$ grep PHYSICAL_REPO_ROOT /workspaces/vbpub/ciu.env
export PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/vbpub"
```

Matches required `/home/vb/volkb79-2/vbpub` exactly. `REPO_NAME` became
`vbpub`, `INSTANCE_ID` became `fae1b8` (`sha256("/home/vb/volkb79-2/vbpub")[:6]`),
`DOCKER_NETWORK_INTERNAL` became `vbpub-fae1b8-network` — all now correctly
derived from vbpub's own physical root instead of dstdns's.

Restore:
```
$ git -C /workspaces/vbpub checkout -- ciu.env
$ git -C /workspaces/vbpub status --porcelain -- ciu.env   # (no output — clean)
```

Regenerate dstdns's own env, unchanged (Contract 4 live check):
```
$ /workspaces/dstdns/.venv/bin/ciu env generate
[INFO] Generating ciu.env (S2.8 bootstrap)...
[SUCCESS] Generated /workspaces/dstdns/ciu.env

$ git -C /workspaces/dstdns diff -- ciu.env   # (no output)
$ git -C /workspaces/dstdns status --porcelain -- ciu.env   # (no output — clean)
```
Byte-identical to git HEAD, confirmed no diff.

**Side effect note:** `ciu env generate` runs the full S2.8 bootstrap (not
just env-file write), so the vbpub smoke run also created the
`vbpub-fae1b8-network` Docker network and attached this devcontainer to it
(dstdns's network attachment was untouched — the dstdns regen found its
network/attachment already in place, hence no `[INFO] Creating/Connecting`
lines on that run). This is an inherent, anticipated side effect of running
the literal Oracle 4 command as specified, not a code-path I could avoid
short of calling the lower-level `generate_ciu_env` instead of `ciu env
generate`. Left in place — not in scope to reverse per the Rules (only the
`ciu.env` restore was specified).

**Oracle 5 — full suite (`run-ciu-tests.py`, the canonical release gate per
README: "The only ciu-owned release helper is `run-ciu-tests.py`"):**

```
$ /workspaces/vbpub/.venv/bin/python run-ciu-tests.py -q
...
Required test coverage of 75% reached. Total coverage: 75.96%
928 passed in 8.02s
```
(914 pre-existing + 14 new; baseline pre-change run was 914 passed, 0 failed.)

## Oracle tally

5 oracles, all pass (0 fail). 928/928 tests pass; coverage 75.96% ≥ 75% floor.
