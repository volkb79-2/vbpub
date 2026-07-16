# nyxloom-P38-dashboard-bridge-network — REVIEW

Reviewer: independent frontier reviewer (Opus 4.8), fresh session. Date: 2026-07-16.
Branch: `feat/nyxloom-P38-dashboard-bridge-network` @ `249e88b` (+ my fix commit).
Handoff: `nyxloom-trove/handoffs/nyxloom-P38-dashboard-bridge-network.md`.

## Verdict

**APPROVED.** All three oracles are met, and the change's central safety
property — *never 0.0.0.0 while still on host-networking* — holds: the bind
flip and the host-net→bridge move ship in the same commit, in both compose
files.

I checked the one thing that would have made this a rejection: whether
host-networking was load-bearing for something the handoff did **not** tell the
implementer to check. It was not (see "Escalation checks" below).

I fixed three test-strength defects myself (F1–F3), each proven by mutation
before and after the fix. F1 was a genuinely hollow assertion. Two findings I
did **not** fix are recorded as F4 (a handoff self-contradiction) and F5 (a
pre-existing notify.py inconsistency this task surfaces but does not own).

Do NOT merge — per role contract, this branch is left for the pipeline.

## Verified git state (not the receipt)

Receipt fields were not trusted; git state read directly.

- `git log main..feat/…` → exactly one implementer commit, `249e88b`.
  Merge-base `7e454d5` (the P37 merge), so `depends_on` is satisfied.
- The real worktree is
  `/workspaces/vbpub/.worktrees/feat/nyxloom-P38-dashboard-bridge-network`,
  **not** the `/workspaces/vbpub/nyxloom` path the packet lists (that checkout
  is on `main`). It was **clean** — the packet's "no uncommitted changes" claim
  is confirmed. The modified `legacy-workflow-origin/*.md` +
  `nyxloom-trove/backlog.md` in the main checkout predate this task.
- Repo root is `/workspaces/vbpub`; `nyxloom/` is a subdirectory. The gate's
  `{worktree}` is therefore the **repo root**, not the `nyxloom/` dir.

## Gate — re-run, not trusted

Ran the handoff's declared `tester-unified` gate myself against the branch
worktree:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P38-dashboard-bridge-network/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

**554 passed, exit 0** — both at `249e88b` and again after my fixes. No
skips, no xfails masking the new tests.

## Oracle verification

| Oracle | Verdict | Evidence |
| --- | --- | --- |
| O1 — bind configurable via `policy.http_bind`, default `127.0.0.1` | **MET** | `daemon.py:1909` now `ThreadingHTTPServer((bind, port), …)`; the hardcoded `"127.0.0.1"` is gone. `_chosen_port()` → `_chosen_http()` returns `(port, bind)` from the min-`http_port` project; rename is complete (no stale refs in `src/`, `tests/`, `docs/`). Default asserted loopback. |
| O2 — off host-net onto a ciu bridge, 0.0.0.0 bind, healthcheck on the bind, DooD + repo binds kept | **MET** | Both compose files: `network_mode: host` removed, `NYXLOOM_HTTP_BIND: "0.0.0.0"`, `nyxloomd-net` join with alias `nyxloomd`, healthcheck → `/dev/tcp/0.0.0.0/…`, `docker.sock` + repo binds intact. Network name agrees across both files (see below). |
| O2 negative — "0.0.0.0 left on host-networking" | **AVOIDED** | The bind flip and the host-net removal are in the *same* commit and the *same* hunks. There is no intermediate state on the branch where 0.0.0.0 coexists with host-net. |
| O3 — doctor names the reachable host | **MET** | `cmd_doctor` prints both the loopback and the `nyxloomd` bridge alias when the bind is `0.0.0.0`/`::`, and loopback-only otherwise. |

### Cross-file consistency of the network name (checked, correct)

The whole devcontainer join step depends on the network name being **stable and
identical** across the two deploy paths. It is:
`nyxloomd/ciu.toml` sets `container_prefix = "nyxloom-prod"`, so the `.j2`
renders `nyxloom-prod-nyxloomd-net` — byte-identical to the hardcoded name in
`docker-compose.yml`. Had these drifted, the documented join step would target
the wrong network on one path.

### `nyxloomd/ciu.compose.yml` is stale — and that is fine

That file still contains `network_mode: host` and the old 127.0.0.1
healthcheck, and the O2 test does not check it. Not a defect: it is **untracked**
(`git ls-files nyxloomd/` lists only the `.j2`, the tomls, the Dockerfile and
`docker-compose.yml`) — a local ciu render artifact, regenerated from the `.j2`
on `ciu up`. The `.j2` is the source of truth and is correct.

## Escalation checks (the reject conditions)

The handoff's `escalate_if` names DooD and repo-binds. Both survive (asserted by
the O2 test). I also checked the dependency the handoff did **not** name, since
that is where a silent breakage would hide:

- **ntfy / notifications.** `nyxloom.toml` sets
  `ntfy_url = "https://nyxloom.gstammtisch.dchive.de"` — a **public FQDN**
  fronted by traefik on the `ingress_public` network, not a host-loopback
  service. Outbound to a public name works over a bridge via NAT, so the move
  does **not** break notifications or the ntfy command listener. Had ntfy been
  reachable only at `127.0.0.1:<port>` on the host, this would have been a
  rejection.
- **Healthcheck viability.** `</dev/tcp/0.0.0.0/8942` is unusual, so I did not
  assume it works — I ran it in `tester-unified:local` against a real
  `0.0.0.0`-bound `ThreadingHTTPServer`: **CONNECTS** (Linux remaps a connect to
  `0.0.0.0` onto loopback). No unhealthy-container regression.

Neither escalation condition fires. The design note's claim that host-net is not
load-bearing is confirmed rather than taken on faith.

## Findings I fixed (committed to this branch)

### F1 — HOLLOW: the bridge-alias assertion passed with the alias deleted

`tests/test_daemon.py` asserted the stable alias with:

```python
assert re.search(r"^[ \t]*-[ \t]*nyxloomd\b", text, re.M)
```

`\b` matches at the `d`/`-` boundary, so **`- nyxloomd-net` satisfies it** — the
ordinary compose list form for joining a network. The assertion therefore did
not test the alias at all; it tested that the string `nyxloomd` appears after a
dash. It passed today only by accident of the mapping form being used.

**Proven, not asserted.** I built a mutant with the `aliases:` block deleted and
the join rewritten to the list form (`- nyxloomd-net`) — a natural refactor. The
original assertion **passed** on that mutant. Since O2's stable alias is exactly
what the documented devcontainer join step depends on, this is the assertion
that most needed to be real.

Fixed by anchoring on the end of the list item and requiring the `aliases:`
block itself:

```python
_BRIDGE_ALIAS = re.compile(r"^[ \t]*-[ \t]*nyxloomd[ \t]*(?:#.*)?$", re.M)
```

Re-ran the same mutant: now **FAILS** (`AssertionError`, the alias message).

### F2 — GAP: O2's healthcheck clause had no assertion

O2 states the healthcheck "targets the bind address". Both compose files were
correctly changed to `/dev/tcp/0.0.0.0/…`, but **no test asserted it** — the
oracle clause was unverified, and a revert to the 127.0.0.1 probe would have
stayed green. Added `_HEALTHCHECK_ON_BIND`. Mutant (probe reverted to
`127.0.0.1`): green before the fix, **FAILS** after.

### F3 — OVERCLAIM: the precedence test tested no precedence

`test_http_bind_env_override_takes_precedence_over_toml` set the env var but
**never set an `http_bind` in the toml**, so the "toml value" it claimed to
override was just the `127.0.0.1` dataclass default. It proved "env var works",
not "env beats toml" — while the compose files depend on precisely the
precedence, since `nyxloom.toml` is bind-mounted identically into host and
container. Fixed by pinning the toml to an explicit `127.0.0.1` (with a guard
asserting the write landed, since `_set_http_bind` silently no-ops if `[policy]`
is absent) before setting the env var. Mutant (override removed from
`config.py`): **FAILS** as it should.

All three fixes verified together: **554 passed, exit 0**.

## Findings I did NOT fix

### F4 — The handoff contradicts itself on scope (handoff defect, not implementer defect)

`scope.touch` lists five files and "Scope / forbid" says *"Touch ONLY the five
files in `scope.touch`"* — but `src/nyxloom/cli.py` is **not** among them, while
Work item 4 ("`cmd_doctor`: print the bridge-reachable dashboard address (O3)")
and oracle O3 itself **mandate** a `cli.py` edit. O3 is unachievable without it.

The implementer touched `cli.py` and was **right** to: an oracle outranks a
scope list that omits the file the oracle names. Recording this against the
handoff-authoring process, not the implementation — `scope.touch` should have
included `src/nyxloom/cli.py`. No other out-of-scope files were touched (the
REPORT is the expected exception).

### F5 — Pre-existing: notify.py click URLs still hardcode `127.0.0.1:8942`

`notify.py` builds ntfy click targets as `http://127.0.0.1:8942/www/...` in
eight places. Those are the same "unreachable from another netns" trap P38 fixes
for `doctor` — a notification tapped anywhere other than the daemon host still
lands on a dead link. **Out of scope** (`notify.py` is not in `scope.touch`, and
the port is hardcoded there independently of `policy.http_port`, so it is a
pre-existing bug this task merely makes more visible). Worth a backlog item;
not grounds to hold P38.

## Notes (no action)

- The compose files **create** the bridge network rather than joining an
  `external: true` one (the idiom `ntfy/ciu.compose.yml` uses for
  `ingress_public`). This is a deliberate, coherent inversion — nyxloomd *owns*
  the network and the devcontainer joins it as external, which the REPORT
  documents. It does imply nyxloomd must be up before the devcontainer attaches.
  Acceptable; flagging only so the ordering is not a surprise.
- The `nyxloomd` alias is hardcoded in `cli.py`'s doctor output, coupling it to
  the compose alias. Tolerable, and the alias is now genuinely pinned by the
  O2 test (post-F1).
- The REPORT is accurate: its oracle→test table matches reality, and its claim
  that the toml loader needed no change (`Policy(**data.get("policy", {}))`
  picks up new keys generically) is correct, not a dodge.
- `topos/nyxloom-trove/reports/P38-*` belong to an unrelated project's P38 and
  are not part of this task.
