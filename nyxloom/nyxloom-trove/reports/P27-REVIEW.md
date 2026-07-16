# P27-REVIEW — independent frontier review (merge gate)

Reviewer: Opus 4.8, fresh session. Single-task packet.
Date: 2026-07-16. Commit reviewed: `34a5e3f`.

## Verdict

**APPROVED — no review-fixes required.**

Both defects the handoff names are genuinely fixed, both oracles hold under
adversarial checks, and the change is scope-clean. I re-ran the declared gate
myself (exit 0, 445/445) and mutation-tested both oracles rather than trusting
the tests' presence. I made no code changes: I found nothing to fix.

The package is also stronger than its own diff suggests, because it resolved
the open question the handoff flagged as a possible BLOCKED trigger (Work item
3) correctly and without leaving scope.

## Git state (verified, not taken from the receipt)

- `git log main..feat/nyxloom-P27-project-mount-reachability` → exactly one
  commit, `34a5e3f`.
- Worktree `/workspaces/vbpub/.worktrees/feat/nyxloom-P27-project-mount-reachability`
  → `git status --porcelain` empty. The packet's "no uncommitted changes" claim
  is accurate; nothing was left behind to be lost on teardown.
- Files touched = exactly the six in `scope.touch`. No forbidden file
  (`daemon.py` / `reconcile.py` / `storage.py` / `config.py` / `ciu.toml`) is in
  the commit.

## Gate (re-run by me)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P27-project-mount-reachability/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

→ **445 passed, pytest exit 0.** No failures, no errors.

## Oracle O1 — daemon-container resolver

**Holds.** Verified four ways:

1. **Live, against the real container.** Under a bare `/usr/bin/python3` with
   `PYTHONPATH` unset and no venv:
   ```
   from nyxloom.adapters import find_controller_container
   find_controller_container()  ->  'nyxloom-prod-nyxloomd'
   ```
   This is the actual running daemon, resolved by the real code path — not a
   mock. The pre-P27 predicate (`"nyxloom" in name and "controller" in name`)
   returns `None` here, which is precisely the negative the oracle describes.

2. **`nyxloom-prod` is not hardcoded.** The no-prefix path matches the
   `-nyxloomd` suffix, which is the service-name half that every
   `container_prefix` produces; the optional `container_prefix` argument matches
   `<prefix>-nyxloomd` exactly. `ciu.toml` was read, not bent.

3. **Override / None / degradation all preserved.** `$NYXLOOM_CONTAINER` wins
   when it names a running container; missing `docker`, a failing `docker ps`,
   and no-match all return `None` so the host fallback still works. Covered by
   6 unit tests.

4. **Mutation test (anti-hollow).** I re-introduced the exact pre-P27 predicate
   in `adapters.py` and re-ran the O1 tests:
   ```
   FAILED test_find_controller_container_matches_prod_nyxloomd
   FAILED test_find_controller_container_old_controller_pattern_no_longer_required
   ```
   The tests fail on the real bug. They are load-bearing, not decorative. (The
   mutation was reverted; worktree left clean.)

**Importability was the root cause and is properly addressed.** The handoff's
diagnosis — "this bug shipped untested because `exec-nyxloom.py` is hyphenated
and cannot be imported" — is answered by moving the resolver into
`nyxloom.adapters` and importing it. That is the fix that stops the *class* of
bug, not just this instance.

## Work item 3 — the `docker exec` argv (the most valuable finding in the package)

The handoff flagged this as unverified and as a potential BLOCKED trigger. The
implementer verified it and fixed it in scope. I confirmed both halves against
the live container:

```
docker exec nyxloom-prod-nyxloomd nyxloom status
  -> OCI runtime exec failed: exec: "nyxloom": executable file not found in $PATH

docker exec nyxloom-prod-nyxloomd /opt/nyxloom-venv/bin/python -m nyxloom.cli status
  -> exit 0, real daemon task table
```

`CONTROLLER_PYTHON = "/opt/nyxloom-venv/bin/python"` matches the daemon's own
compose `command:` (`exec /opt/nyxloom-venv/bin/python -m nyxloom.cli daemon`)
and the `Dockerfile`'s venv.

This matters more than it looks: fixing the resolver *alone* would have
converted a silent-wrong-ledger bug into a hard `exec` failure on every
`exec-nyxloom` call. The two fixes are only correct together, and the package
shipped them together.

## Oracle O2 — compose template / pre-rendered sibling parity

**Holds.** Verified three ways:

1. Both `ciu.compose.yml.j2` and `docker-compose.yml` carry the netcup bind at
   the same physical-path convention as the other repos
   (`/home/vb/volkb79-2/netcup-api-filter:/workspaces/netcup-api-filter`).

2. **Mutation test (anti-hollow).** Deleting the netcup line from
   `docker-compose.yml` only — i.e. manufacturing exactly the drift the oracle
   exists to catch — fails both tests:
   ```
   FAILED test_nyxloomd_compose_template_and_sibling_mounts_agree
   FAILED test_nyxloomd_compose_mounts_netcup_api_filter
   ```
   Reverted; worktree clean.

3. **The bind source is real, not a phantom path.** A test asserting two files
   agree cannot tell you the path exists — if the source were wrong, Docker
   would silently create an empty root-owned directory and "registered but
   unreachable" would persist in subtler form. I checked the host:
   `/home/vb/volkb79-2/netcup-api-filter` exists and contains a populated
   `nyxloom-trove/` (`handoffs`, `nyxloom.toml`, `backlog.md`, …). The mount
   will resolve to the real trove.

The test guards against a vacuous pass (`assert template_sources` before the
equality), so two unparseable files cannot green it. That anticipation is
correct and worth noting.

## Scope discipline — the live daemon was not disturbed

The handoff's sharpest constraint ("do NOT restart, rebuild, or `ciu up` the
running daemon — you would be pulling the floor out from under the process
dispatching you"). Verified:

- `nyxloom-prod-nyxloomd`: `StartedAt 2026-07-16T05:36:36Z`, `RestartCount 0`.
  P27 was committed at `07:12:55Z` — the container predates the work and was
  never bounced.
- The live daemon's mounts are still `mdt--mounted-folders`, `vbpub`, `dstdns`,
  `docker.sock` — **no netcup**. The change is authored and inert, exactly as
  specified. The registry was not mutated.

## A regression I specifically probed and did not find

Moving the resolver into `adapters.py` makes `exec-nyxloom.py` import the
`nyxloom` package at module scope — *before* it decides host-vs-container. The
existence of `_host_python()` ("prefer the known project venv (carries
nyxloom's deps); fall back to whatever interpreter is running this script")
implies the wrapper's own interpreter may be dependency-free. If the import
chain reached PyYAML/jsonschema (both installed in the image's venv), the host
fallback would hard-crash where it used to work.

It does not. The chain is `adapters → {config, types}`, `config → {tomllib,
paths, types}` — stdlib only (`tomllib` needs 3.11+; the wrapper already
required 3.10+ for `str | None`). Confirmed empirically: the import succeeds
under bare `/usr/bin/python3` (3.13.5) with no venv and no `PYTHONPATH`. The
`sys.path.insert(0, SRC)` ordering is correct and the host-fallback
`PYTHONPATH` env is untouched. **No regression.**

## Findings (non-blocking — none justify rejection or a fix)

### F1 — No REPORT/LOG on the branch (process gap, carried forward here)

The handoff's "Not in scope — the operator activates" section requires the
REPORT to note the two activation follow-ups. No `P27-REPORT.md` or LOG exists
on the branch, so those steps are recorded nowhere. My role contract forbids me
writing the implementer's REPORT, so I record them here so they are not lost:

**Operator follow-ups (the change is inert until both are done):**
1. `ciu up --dir nyxloom/nyxloomd` — rebuild/recreate the daemon container so
   the netcup mount and the new code take effect (the self-hosting caveat in
   `nyxloom-trove/nyxloom.toml`).
2. `nyxloom project add` for the netcup-api-filter trove, once the mount is live.

This is a documentation gap, not a code defect; the code meets the contract.

### F2 — Ambiguity if two `-nyxloomd` containers ever run

The no-prefix branch returns the first `docker ps` match, so a host running both
`nyxloom-prod-nyxloomd` and `nyxloom-staging-nyxloomd` would resolve by
`docker ps` ordering. Mitigated by `$NYXLOOM_CONTAINER` and by the
`container_prefix` argument, and out of the oracle's scope (which only demands
the prefix not be hardcoded). Backlog-worthy alongside **B11**, not a defect.

### F3 — `container_prefix` is production-dead

Only tests pass it; `exec-nyxloom.py` never reads `ciu.toml` and always calls
`find_controller_container()` bare. It is the documented mechanism for honouring
`container_prefix` exactly, so it is the right API to expose — but nothing in
production exercises it today. Mild YAGNI; acceptable as the oracle names this
surface.

### F4 — The compose parser reads only the first `volumes:` block

`_compose_volume_sources` latches the first `volumes:` and stops at the first
dedent. Safe today: each file has exactly one `volumes:` block, at service level.
A top-level named-volumes block added *above* `services:` would mis-target it —
but it would fail loudly (sets differ / netcup missing), not pass silently, which
is the right failure direction for a drift guard. The docstring already justifies
avoiding `yaml.safe_load` (Jinja placeholders break it). Acceptable.

## Reasoning for the verdict

The package does what the handoff asked, and the parts most likely to be faked —
the two oracles — survive mutation testing against the real bugs. The claims I
could check against live state (resolver output, both `docker exec` argv forms,
the netcup path's existence, the daemon's untouched uptime) all check out, and
the one plausible regression the refactor could have introduced provably did not
occur. Scope was respected exactly, including the constraint not to touch the
running daemon. The only gap is a missing REPORT, which I am not permitted to
write and which blocks nothing in the code.

**APPROVED.** Do not merge on my authority; this review is advisory to the merge
step. Activation remains the operator's, per F1.
