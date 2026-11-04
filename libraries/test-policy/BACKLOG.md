# test-policy backlog (TP)

* **TP-01 release contract.** No tag, cmru contract or wheel publish yet; the plugin is consumed with `-p estate_test_policy.plugin` and `PYTHONPATH`. Needs a `test-policy-vX.Y.Z` contract and the tester-unified image carrying the wheel (memo 6.2c: loaded by tester-unified lanes).
* **TP-02 FL-03 root cause.** `test_runner_cancellation_stops_and_removes_its_owned_container` (assay): run `flakehunt --recipe slow-cpu` and `cpu-contended` on it, find the cause (60 s budget versus trap deferral), fix, prove with 20 green repeats.
* **TP-03 shell lint.** A `shellcheck` plus rule for `set -o pipefail` scripts: no `| head` or `| grep -q` after a producer that keeps writing (FL-01/FL-02 class).
* **TP-04 plugin extensions from the memo.** Literal `/tmp/` paths; `/proc/pressure`, `os.cpu_count()` or `/proc/meminfo` reads inside assertions; helper-function sleeps (call-graph one level deep); a `hardware_property` alias of `estate_allow`.
* **TP-05 `run-gate flakes` inventory and the release-gate flakehunt pass** (memo 6.2a and the release rule): needs run-gate history grouping by (lane, commit, image digest). Not started.
* **TP-06 dstdns adoption.** Run the plugin over dstdns tests (FL-11 candidates) and convert fixed sleeps to events or polls.
* **TP-07 flakehunt follow-ups.** `order-shared` profile (random order seeds, xdist, each failing test alone); per-test-id x profile counts across several selections; `--pass-m` default per profile.
