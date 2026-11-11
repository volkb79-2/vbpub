# hostobs workspace bootstrap

This is the M0 Rust workspace used to qualify the Rust toolchain and offline
crate-cache path. It has the planned `proto`, `model`, `kernel`, `store`,
`eval`, `act`, and `client` library boundaries plus the `probe` and `act`
binary packages. The checked-in code is only a compilable scaffold; it does
not implement the host monitor or claim the hostobs/1 protocol.

The existing topos sampler is the source for the first functional imports.
Plan v4 maps its collector/process helpers into `kernel`, storage helpers into
`store`, and its existing probe setup into `bins/probe`. Those code moves and
behavioral changes remain later hostobs work; this R-1 baseline does not
invent them.

Use the registered `lint` and `selftest` lanes from this directory. Cargo is
offline and locked, and each Cargo invocation checks the mounted
`Cargo.lock` against the image's lock-specific cache manifest first. See
[`docs/CONSUMERS.md`](docs/CONSUMERS.md) for the lane commands and
[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md) for the boundary decisions.
