# Using the hostobs Rust lanes

The M0 workspace is a toolchain and packaging scaffold, not a host monitor.
From `hostobs/`, run the registered lanes:

```sh
run-gate lint
run-gate selftest
```

Both use `tester-unified-rust` with Docker networking disabled. The image
contains the stable Rust toolchain, cargo tools, Python for the cache/floor
helpers, and the crate archive closure for this exact `Cargo.lock`. Do not
run Cargo from the cockpit or use a network fallback. The Cargo wrapper
refuses before invoking Cargo if the mounted lock, baked lock digest, crate
set, lock checksums, or cached archive bytes disagree.

Changing a Rust dependency or `Cargo.lock` requires rebuilding
`tester-unified-rust` from the reviewed lock before the lanes can pass. This
initial image build is a local bootstrap; CMRU-owned version resolution and
image refresh belong to the later R-5 work package.
