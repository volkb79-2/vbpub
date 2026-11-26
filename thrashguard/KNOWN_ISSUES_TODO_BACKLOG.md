<!-- backlog-format: 1; prefix: TG- -->

# thrashguard backlog

Normative behaviour lives in [`docs/design/thrashguard/DESIGN.md`](../docs/design/thrashguard/DESIGN.md);
current behaviour only in the README, docs and SPEC-like files. Views:
[`INDEX.md`](INDEX.md), closed items in [`archive/`](archive/).

## TG-1 — P1 `observe` is the only cut; every act path is P2

**State:** open · **Severity:** medium · **Owner:** core · **Links:** docs/design/thrashguard/DESIGN.md

**Problem.** The design's MVP splits into P1 `observe`, P2 `act`, P3 `victims` and
P4 `estate` (design §5). P1 compiles no act path, so `mode = enforce` must be
refused with a one-line reason and a test must assert no cgroup write and no
signal exists in the crate. **Wanted.** P1 finishes with config, PSI poll,
hysteresis, clock, the two sockets, the candidate store and container→scope
resolution, `events.jsonl`, `mode=report`, the CLI and static units, all green in
the registered lane; P2 lands freeze/thaw only with the VM acceptance of §5.

## TG-2 — the deferred P2/P3/P4 behaviour, as one pointer each

**State:** parked · **Severity:** medium · **Owner:** core · **Links:** docs/design/thrashguard/DESIGN.md, TG-1

**Problem.** P2 (freeze/thaw, `memory.reclaim`, read-back, death sweep,
`enforce`, the M1 thaw queue), P3 (`newest`/`refault`/`smallest-enough`, aging,
invariants) and P4 (run-gate interim registrant, thrashguard→hostobs emission,
`host-facts/2`, fleetd place-and-register, agctl snapshot, mdt-cockpit deploy
offer) are out of scope for the P1 wave and must not appear as code or as a lane
that pretends to judge them. **Wanted.** Revive each by opening its own wave with
its own exit criteria from design §5; never as "finish P1".

## TG-3 — The frozen-time consumer path does not exist in hostobs yet

**State:** blocked · **Severity:** medium · **Owner:** hostobs · **Links:** blocked-by hostobs M3, docs/design/thrashguard/DESIGN.md

**Problem.** Design §4a lists exactly three missing hostobs parts: the `annotate`
verb is `served: false`, the `events` read verb is planned M3, and freeze state
is not a sampled metric in `crates/model/src/metric.rs`. Until all three land,
every consumer must report the frozen total as `unknown` — never `0`. **Wanted.**
hostobs M3 plus one metric-registration item; thrashguard P4 emits
`thrashguard.freeze` / `thrashguard.thaw` only against those, and its own
`events.jsonl` is a crash record, not the consumer path.

## TG-4 — P4 must state how a cockpit-side run-gate reaches the registrant socket

**State:** open · **Severity:** medium · **Owner:** core · **Links:** docs/design/thrashguard/DESIGN.md, TG-2

**Problem.** Design §4e requires run-gate on the host to be the interim registrant
and says it "needs the `thrashguard-registrant` group membership (Q5) and nothing
else". That is a statement about the group, not about reachability: the gate
container runs inside the cockpit's Docker, the socket lives at
`/run/thrashguard/registrant/thrashguard.sock` **on the host**, and a container
with `--network none` and no `/run` bind sees neither. The §4e text does not
name the mount, the namespace, or the transport, so an implementer would guess.
**Wanted.** P4's own item states the mechanism explicitly — which process
resolves the host path (`nsenter`/host runner vs a bind-mount of
`/run/thrashguard/registrant`), what that does to the container's containment
argument (root in the container can reach the socket anyway, so the boundary
stays the scope resolution), and what happens on a host where the socket is
absent. Until that is decided P4 does not register from a container.

## TG-5 — No R2 (mutation) lane exists for this crate

**State:** open · **Severity:** medium · **Owner:** core · **Links:** thrashguard/run-gate.toml, assay/docs/CONSUMERS.md

**Problem.** `thrashguard/run-gate.toml` declares `lint` and `r0-r1` only. A
declared-but-never-run R2 lane is not evidence, so none is declared; but the
release rule wants R2 asynchronously on the newest release, and a campaign has
never been designed for a crate this small. **Wanted.** A cargo-mutants lane over
the changed lines of `src/` with a mutant-count estimate from
`cargo mutants --list --in-diff <base>` and a measured per-mutant cost, declared
only when there is a budget figure. P1's release therefore records "R2 pending".

## TG-7 — The deploy surface is committed but only documented as a recipe

**State:** open · **Severity:** low · **Owner:** core · **Links:** thrashguard/deploy/, docs/CONSUMERS.md, docs/design/thrashguard/DESIGN.md §5

**Problem.** `deploy/systemd/{thrashguard.service,thrashguard.slice,thrashguard-registrant.socket,thrashguard-reader.socket}`
and `deploy/tmpfiles.d/thrashguard.conf` exist, and `docs/CONSUMERS.md` §2 shows
the `install`/`groupadd`/`systemd-tmpfiles`/`systemctl` commands an operator
pastes. What does not exist is the installer those commands imply: no package,
no `install.sh`, no upgrade path, so a released binary cannot get onto a host
without a human copying files out of the checkout. The design also promises
that mdt-cockpit may **offer** it through a `MDT_THRASHGUARD` host-setup key
(design §5 "Deploy"), which nobody has built. **Wanted.** One installer that
takes a released binary (or a tarball) and installs the units, the groups and
the `/etc` skeleton idempotently, plus the mdt-cockpit offer key if the operator
wants the offer. Until it exists the README says "a host without mdt-cockpit
runs it standalone" — true only for a human with a shell.

## TG-8 — `serve`'s session handling is untested against a real listener

**State:** open · **Severity:** medium · **Owner:** core · **Links:** thrashguard/src/daemon.rs, TG-1

**Problem.** `daemon::serve` (the two-thread loop, the accept cap and the
per-session handler) is covered only through the pieces it composes: the session
framing over `UnixStream::pair`, the request dispatch, and `bind`. The loop
itself — accept, spawn, cap at `MAX_SESSIONS`, drop a peer that never sends
(`SESSION_READ_TIMEOUT`) — is never started by a test, because a test that runs
it needs a listener thread it must also stop, and the estate forbids a fixed
sleep as a synchronisation primitive. So a defect in the loop's own shape would
be found by a live probe, not by the lane. **Wanted.** Either a test that drives
the loop deterministically (an injected listener whose accept is driven by the
test, so no sleep races) or a recorded live acceptance probe, and the choice
written here. Not urgent for P1 — nothing acts — but P2's daemon restart path
depends on it.

## TG-9 — `LISTEN_FDS` adoption has no live acceptance yet

**State:** open · **Severity:** high · **Owner:** core · **Links:** thrashguard/src/activation.rs, docs/design/thrashguard/DESIGN.md §3 D7

**Problem.** `activation::plan` is unit-tested in both directions, but
`activation::adopt` — the single audited `unsafe` block that turns a raw
descriptor into a `UnixListener` — cannot be: a test would have to be systemd,
and passing this process's own descriptors would test `std`, not the contract.
The systemd `.socket` units use `Accept=no` with `FileDescriptorName=registrant`
/ `reader`, so the names and the order are the unit files' facts; they are
declared in `FD_NAMES` and nothing yet proves the two agree. The `unsafe` block
is therefore audited by reading, not by a run. **Wanted.** A VM acceptance (the
design's §5 test host) that starts both `.socket` units, connects a client to
each, and shows `serve` adopting descriptor 3 and 4 by name; and a lint or test
that compares `FD_NAMES` against the `FileDescriptorName=` values in
`deploy/systemd/*.socket`, so a rename cannot silently pass. Do that **before**
P2, because a mis-adopted descriptor would serve the wrong role's verbs.

## TG-10 — No `INDEX.md` can be generated for this backlog

**State:** open · **Severity:** low · **Owner:** platform · **Links:** docs/BACKLOG-FORMAT.md, TG-1

**Problem.** `docs/BACKLOG-FORMAT.md` names `agctl backlog index` as the generator
of `<project>/INDEX.md`, and the estate's `agctl backlog check` compares that file
against regenerated output. This checkout's `agctl` has no backlog module at all
(the verb is documented, not built), and nyxloom's `backlog` verb manages trove
entries instead of these files. So `thrashguard/INDEX.md` cannot be produced by
the documented tool, and a hand-written index would be a file that pretends to be
generated. **Wanted.** Either the verb is built (the platform side of this), or
the format document records that `INDEX.md` is optional until it is, and
`check` tolerates its absence. Until then this project ships no `INDEX.md`.

## TG-11 — The project is not registered in `cmru.orchestration.toml`, and cannot be yet

**State:** blocked · **Severity:** medium · **Owner:** core · **Links:** blocked-by operator, cmru.orchestration.toml, docs/BOOTSTRAP-FROM-ZERO.md, docs/design/thrashguard/DESIGN.md §5

**Problem.** Design §5 packages thrashguard as a `cmru`-managed repo project, and
`estate-lint` judges every project the orchestration declares. Registration needs
an `[orchestration.project.thrashguard]` entry, which needs a `config` path
(`cmru/src/cmru/config.py:1739`), which needs a `thrashguard/cmru.toml` — and that
file needs a real release contract: `[project] artifacts`, a `build_step`, and
explicitly declared `run-tests` and `push` steps
(`config.py:1508-1530`). A Rust binary is not a wheel, and the only artifact kind
that fits is `tarball` (`config.py:690`); but cmru ships **no tarball build handler
at all** (only `tarball-publish`/`tarball-validate`, `handlers.py:1254`, `:1277`)
and no Rust build path outside the gate image. So a `cmru.toml` written today
would have a build step that cannot build anything, which is exactly the
"declared but never run" shape the estate forbids. Registering it in the graph
without a config is refused by the loader, and nothing in the estate currently
requires the entry: a project that is absent from the graph is simply not judged.

**Wanted.** An operator decision on one of: (a) a small `tools/package-release.sh`
that builds the binary in `tester-unified-rust` and emits
`dist/thrashguard-v<ver>.tar.xz`, wired as the `build` step with `tarball-publish`
as `push`, then the orchestration entry plus a regenerated `project-order` block;
(b) register the project as an `oci-image` artifact instead, building the daemon
image with the existing `oci-image-build` handler; (c) leave it unregistered until
the first release wave and record that in the design. Until one is chosen do not
touch `cmru.orchestration.toml`: a half-valid config breaks `cmru dependencies`
for the whole estate, and every test that loads the root orchestration parses it.

## TG-12 — Registrations are not bound to their socket issuer

**State:** open · **Severity:** high · **Owner:** core · **Links:** thrashguard/src/daemon.rs, docs/design/thrashguard/DESIGN.md §3, §4e

**Problem.** The registrant group controls who can pass the socket's DAC check,
but the daemon stores no kernel-derived issuer with a registration. Any peer that
can reach the registrant socket can `update`, `heartbeat` or `deregister` any
registration id. Container root may bypass socket DAC, and scope resolution
constrains the target cgroup but does not prove ownership. Stable Rust does not
expose [`UnixStream::peer_cred`](https://doc.rust-lang.org/stable/std/os/unix/net/struct.UnixStream.html#method.peer_cred);
the standard-library API is nightly-only, so P1
does not add unsafe code or a dependency to guess at peer identity.

**Wanted.** Before P2 enables `enforce`, define and capture a kernel-derived
issuer identity for each registration and refuse cross-issuer update, heartbeat
and deregister with `not-yours`. The identity contract must address the process
or cgroup incarnation as well as uid; client-supplied `owner`, `run` or pid is
not identity. Until then, P2 enforce is blocked.
