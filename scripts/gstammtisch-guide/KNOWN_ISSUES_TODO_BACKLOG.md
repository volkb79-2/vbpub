# gstammtisch-guide — backlog

## GG-1 — the nsenter test has no VM lane that would ever run it

**Status:** OPEN (filed by mdt-cockpit MDTC-02 T7, 2026-10-18).

**Observed.** `tests/test_soulmask_monitor_r1.py::test_nsenter_binary_exists_but_real_netns_join_needs_a_real_host`
executes a real low-level OS binary (`nsenter`). It is now marked `os_integration` and the gate lanes
(`run-gate.toml` r0-r1 and `assay.toml` r2) deselect it explicitly (`-m "not os_integration"`),
because the minimal `tester-base` image carries no `nsenter`-class binaries and a quiet skip proves
nothing. The test body documents a known limitation rather than asserting nsenter behaviour, and
this project has no QEMU VM lane, so nothing ever runs it.

**Fix direction.** Either build a small `r1-vm-real-nsenter` lane on the shared `testing/vm`
harness (see `scripts/debian-install-v2/testing/vm/`; run the marked test with
`VBPUB_REQUIRE_OS_INTEGRATION=1`, where a skip is a failure) and make the test assert a real netns
join between two guest namespaces, or delete the placeholder test if the deployment-host smoke test
in README.md remains the only authority.

**Oracles.** In the VM lane the test passes with `nsenter` present and FAILS (not skips) when the
binary is absent; the gate containers report it as deselected, never as passed or skipped.
