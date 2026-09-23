---
kind: backlog-entry
schema_version: 1
id: CP-4
title: "test_store.py::test_new_run_id_is_unique_even_for_the_same_instant is a pre-existing probabilistic flake (birthday-paradox collision on a 4-hex-char random suffix)"
status: open
type: "bugfix"
severity: "low"
provenance: "RG-55 wave, cgprofile-P1-DAEMON C4, 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

`tests/test_store.py::test_new_run_id_is_unique_even_for_the_same_instant`
draws `store.new_run_id(when=<fixed timestamp>)` 50 times and asserts all
50 ids are distinct. `new_run_id` appends `os.urandom(2).hex()` (4 hex
chars, a 65536-value space) to the fixed-timestamp prefix, so 50 draws from
that space collide with probability ≈ 1 − exp(−50·49 / (2·65536)) ≈ 1.8%
(birthday paradox) — genuinely probabilistic, not something a code change
in an unrelated module can trigger or fix.

It failed exactly once during RG-55 `cgprofile-P1-DAEMON` C4 work, on the
first post-commit clean-tree `run-gate.py r0-r1` run against commit
`8ba01138b2150e5f7f27f4b26f9714c1352effe9` (`49 == 50`, one collision
among the 50 draws). Re-running the exact same lane immediately after,
with no code change, passed (`1048 passed`). Run in isolation three more
times locally, it passed every time — consistent with the ~1.8% math
above, not a real regression.

## Why cgroup-profiler owns it

`new_run_id` and this test live entirely in this project's own
`lib/store.py` / `tests/test_store.py`, predating RG-55 entirely
(commits `4af86d57` and `d5df216d`) and untouched by the RG-55 P1 daemon
package. No other vbpub tool or consumer has any input into this function.

## Proposed contract

Not designed here — this entry tracks the flake, not the fix. Two
directions worth considering when someone picks this up: (a) widen the
random suffix (`os.urandom(3)` or more) to shrink the collision
probability to effectively zero for any realistic draw count a real test
would use, or (b) treat a rare within-tolerance collision as expected and
assert `len(ids) >= 49` (or similar) with a comment explaining the
birthday-paradox math instead of asserting exact uniqueness across 50
draws from a 65536-value space. Do not pick one without checking whether
anything downstream actually relies on the *current* 4-hex-char width
(directory names on disk, existing recorded run ids, etc.).

## Oracles

A fix should be verified by running the test at least, say, 200 times in a
loop (or computing the exact probability analytically for whatever new
width/assertion is chosen) and confirming the flake rate drops to
effectively zero — not by a single passing run, which is exactly how this
one has looked "fine" for the entire life of the project until now.

## SPEC ownership

`scripts/cgroup-profiler/lib/store.py` (`new_run_id`), its own test file
`tests/test_store.py`.

## Provenance

RG-55 wave, `cgprofile-P1-DAEMON` C4 (`lib/serve.py`), discovered
2026-09-12 during the post-commit clean-tree gate run for commit
`8ba01138b2150e5f7f27f4b26f9714c1352effe9`. See
`nyxloom-trove/reports/cgprofile-P1-DAEMON-LOG.md` commit 5 / C4 entry.
