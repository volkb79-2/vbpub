# Socket-carrier goldens (contract §8.1/§8.6, RG-55 P6 C6)

One `<verb>-request.json` + `<verb>-response.json` pair per verb. These are
the bytes the run-gate socket client (P5) is written against; `docs/PROTOCOL.md`
is the prose that goes with them.

**How they are produced.** `tests/test_serve_socket_carrier.py` runs a real
`SessionServer` accept loop on a temp socket and drives every verb over the
socket carrier with a plain stdlib client; the contract-1 requests are the
shape the retired 1.x in-image `ctl` translator produced (kept in the test
as the wire oracle, so these files stay byte-identical for 1.x consumers).
The contract-2 documents (`config*`, `version-c2*`, `status-c2*`,
`export*`) are produced by `tests/test_serve_contract2.py` and
`tests/test_serve_export.py`. The `report` pair is named `report-removed-*`:
a contract-1 `report` request answered by a 2.x daemon with the additive
`report-removed` error (PROTOCOL 10.1). Regenerate with

```bash
CGPROFILE_REGEN_SOCKET_GOLDENS=1 python3 -m pytest tests/test_serve_socket_carrier.py \
    tests/test_serve_contract2.py tests/test_serve_export.py -q
```

and read the diff before committing it — a change here is a change to what
a consumer parses.

**Determinism.** Fixed wall clock (`2026-09-12T10:15:00Z`), fixed session
ids, a per-thread counting sampler clock, a fake cgroup/proc tree, DAMON
forced "available" with every session started `--damon off`, and a sampler
sleep that lets each session take exactly one sample.

**The only normalization:** the test's own tmp paths are rewritten to the
production defaults — the sessions directory to `/var/lib/cgprofile/sessions`
and the socket to `/run/cgprofile/ctl.sock` (contract §1.8). Everything else
is the live document.

**Reading them together.** They come from ONE run of a single daemon, in
this order: `version`, `host` (no session live yet — hence
`sessions_live: 0`), `start`, `status` (one live session, one sample),
`stop`, `report`, `gc`. `gc` reports `kept: 2` because the scenario also
drives a second session (on a separate connection, to prove the documents
are stable per session) before the `gc` call; it is not a second session
hiding in the other fixtures.

**`watch` (§8.2), the streaming verb.** `watch-request.json` was frozen in
C6, ahead of the implementation, so P5 could code against it; C7 answers
that exact request, and a test asserts the request shape did not move.
Because the verb writes one object per line on a connection that stays
open, `watch-response.json` is the **array of lines** that one connection
carried, in order: `reading` (on attach), `reading` (woken by the `stop`),
`verdict` (the state change), `end`. Each of the three line SHAPES is also
frozen on its own, one per file, in the parent directory
(`../watch-reading.json`, `../watch-verdict.json`, `../watch-end.json`).

The state change in that stream is driven deliberately: the scenario feeds
BOTH sessions' liveness trackers two synthetic readings that cross the idle
bound (`_force_stalled`), so the stream carries a real `verdict` line and
the two sessions' `stop` documents still describe the same lane. That is
also why `stop-response.json`'s Summary says `watch.state: "stalled"` — the
state machine's own transitions are proven in `tests/test_liveness.py`, while
fake-cgroup enforcement write/refusal oracles live in
`tests/test_serve_placement.py`. This fixture does not claim a live kernel
kill; that requires a separate acceptance probe.

`../status-v1.1.json` and `../summary-v1.1.json` are the §8.4/§8.7
documents whole — the v1 goldens under `fixtures/contract/` are still
byte-identical after the new keys are stripped, which a test asserts.
