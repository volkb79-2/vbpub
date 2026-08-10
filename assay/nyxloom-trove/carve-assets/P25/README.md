# P25 locked real-Topos qualification packet

These files are carver-owned specification and independent evidence. P25's
implementer and reviewer must not edit them.

The packet is pinned to vbpub
`9f522a72d37b9cb5beb1939ceca1978c9fc4ef23` and Topos tree
`1bc8a51296b74e536bf60b534efb2fc938dcc389`. Read
`topos-input-manifest.json` before the harness: it names all critical blobs,
the 966-entry tracked input, the three exact absolute symlinks deleted only in
the prospective disposable baseline, the five retained relative symlinks, and
the 965-entry forced baseline index. An ordinary `git add` is a known false
construction: it silently omits four tracked-but-now-ignored Docker fixtures
and turns a targeted green into 13 full-suite failures.

`qualification-manifest.json` is the hand oracle for pass, missing, excluded,
and comment-only 0/0 lines. `expected/` contains complete v4 templates; runtime
timestamps/OIDs/version/witness paths are normalized only after their exact
real values are asserted. `fixtures/coverage-witness.py` copies, but never
interprets, the bounded coverage bytes Assay consumes inside its ephemeral
snapshot. The unmodified pinned Topos evaluator consumes that copy.

`release/assay-1.2.5-py3-none-any.whl` is a positive qualification fixture,
not a published Assay release. `build_release_fixture.py` built it twice from
only the exact tracked P25 Assay source tree using P24's five-wheel offline
closure, fixed identity/tag/epoch, and the two results were byte-identical.
`release-manifest.json` was created by P24's landed helper. The production
harness copies and installs these exact bytes via the helper's sole PEP 508
line plus pip `--require-hashes`; it never selects a wheel by glob.

`probe_topos_qualification.py` is the carver's complete tracer, not production
code. In the real network-disabled tester-unified container and validated
background cgroup it witnessed the full 2,923-test current-equivalent PASS,
5/5 parity, 4/5 line-7 failure parity, expected exclusion asymmetry, and 0/0
comment parity. Its compact exact record is `probe-results.json`.

`skeleton/qualify_topos.py` freezes the production interface, pins, scenario
objects, normalization logic, CLI, and TODO boundaries. Copy it to
`gate/python/qualify_topos.py`; do not redesign it. Promote `fixtures/`,
`expected/`, and `release/` to the exact production paths in the handoff.

Quick locked acceptance from `assay/`:

```text
python -m pytest nyxloom-trove/carve-assets/P25/test_acceptance.py -q -p no:randomly
```

Before implementation, the quick suite is a controlled red only at the absent
production promotion/wiring. After implementation it must be entirely green.
It does not replace the live Topos proof: the controller runs Assay's complete
registered tester-unified gate foreground and requires the new
`ASSAY_GATE_PHASE=topos-qualified` marker in addition to every P24 marker and
the final outer receipt.
