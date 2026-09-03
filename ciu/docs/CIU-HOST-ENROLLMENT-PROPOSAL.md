# CIU Proposal — Remote Host Enrollment via `get.py` (`ciu host enroll`)

**Status:** PROPOSAL — not yet normative, not yet filed as a backlog entry (see §7).
**Author:** dstdns controller session, 2026-09-03, on operator request. Not reviewed, not
interviewed against the operator beyond the originating decision this builds on.
**Target:** additive to whichever CIU major line is current when accepted — v7 (`ciu.hosts.toml`
`[deploy.hosts.<h>]`, `SPEC.md` S14) today, v8 (`ciu.hosts.toml` `[hosts.<h>]`, `SPEC-V8.md` S7.2/
S17.1/S17.3–S17.4, currently draft.6 and actively churning) once it lands. This proposal is
written against **both** schemas explicitly (§3.4) because v8 was mid-review at time of writing
and a design pinned to a single draft would likely be stale before anyone reads it.
**Relationship to other documents:** does not touch, extend, or contradict `CIU-V8-*` (the
integrated-model proposal) or `SPEC-V8.md` — it proposes a step that happens **before** any of
that machinery is reachable (§1), and its only requirement of either schema is "write one entry
into the host-inventory file with these fields" (§3.4).

---

## 0. Where this comes from

dstdns's `docs/spec-configuration-and-landscape.md` decision **D-097** (2026-08-19, operator
interview) designed and then **deferred** a piece of the remote-deployment transport story:

> updates PUSH-ONLY via `ciu ssh`/`ciu up --host` … enrollment secrets in ciu's LOCAL secret store
> (not Vault) … self-hosted `get.py --bootstrap-url --token` (behind tls-edge, GitHub-independent)
> DESIGNED + DEFERRED.

Nothing further was written down at the time — D-097 names the shape and stops. Separately, this
session's dstdns-P171 carve (2026-09-02/03) surfaced ciu's existing `.ciu.hosts.toml` + `ciu ssh`/
`ciu up --host` mechanism for placing seeded-infra targets on a real remote host, and noted in
passing that host enrollment today is entirely manual (`ssh-keygen` once, `ssh-keyscan` once for
TOFU, hand-edit the TOML) — and drafted a never-filed placeholder backlog idea, tentatively
"CIU-93 `ciu remote setup <host>`", with no design behind it. This proposal is that design.

## 1. The gap

CIU already has a complete, well-specified mechanism for **an already-trusted host**:

- **v7** `SPEC.md` S14: `.ciu.hosts.toml` host inventory (`ssh_host`/`ssh_user`/`ssh_port`/
  `ssh_key`/`known_host`/…), `ciu ssh <host>`, `ciu up --host <host>` (render-on-target),
  `ciu up --host <host> --thin` (docker-optional push→activate, activation contract
  `bootstrap|apply|health|rollback` — S14.6, "the same shape as the cmru ProjectAdapter"),
  host-scoped local secrets for pre-Vault material (S14.3a), fail-closed host-key pinning
  with a documented TOFU escape hatch (S14.4a, `CIU_SSH_INSECURE_TOFU=1`).
- **v8 draft.6** `SPEC-V8.md` S7.2/S17.1/S17.3–S17.4: the same shape, restated — `[hosts.<h>]`
  in `ciu.hosts.toml`, `known_host` required unless `local` or TOFU, `[activate] bootstrap|
  apply|health` (v8 drops `rollback` from the host contract — rollback becomes CIU's own state
  machine over the `releases/`+`candidate`+`current`+`previous` chain, S17.3–S17.4), host-scoped
  `[secrets.<entry>]`.

Every one of these verbs **assumes `ciu.hosts.toml` already has a working entry**: a reachable
`ssh_host`, a private key CIU can use, and a pinned `known_host`. Nothing in either spec describes
how that entry gets created for a host that has never spoken to this ciu checkout before — today
it is 100% manual: an operator runs `ssh-keygen` by hand, copies the public half onto the target
through some out-of-band channel (cloud-init, a password login, a provider's console), runs
`ssh-keyscan` to pin the host key, and hand-writes the TOML row. `CIU_SSH_INSECURE_TOFU=1` covers
the "pin the host key without a manual keyscan" half of the trust problem; nothing covers the
"get an authorized key onto the target and back an inventory row out of it" half.

This is exactly the gap D-097 named and deferred: a **self-hosted, GitHub-independent**
bootstrap installer (`get.py`, behind tls-edge, token-authenticated) that a remote admin runs
**once, by hand, on a bare host with nothing on it but a Python 3 interpreter**, closing the loop
back to a populated, working `ciu.hosts.toml` entry — without requiring pre-existing SSH trust,
without a GitHub account, and without installing Docker or a general-purpose CIU runtime on hosts
that don't need it (mirroring S14.6/S17's `docker_optional` design intent).

## 2. What already exists to build on

**`get.py` is not hypothetical — it is a working, in-production mechanism**, just not yet
pointed at this problem:

- `cmru get-py --project <name> [--config <toml>]` renders `templates/get.py.tmpl` (191-line
  template, `cmru/src/cmru/getpy.py` is the renderer) into a **self-contained, stdlib-only**
  Python 3 script: transactional install/update/rollback via a `releases/<tag>/` +
  atomic `current` symlink swap (`os.replace`, never a plain rename — survives a crash mid-swap),
  SHA256 + minisign-signed-manifest verification before extraction, system/user scope, an
  exclusive flock so two installer runs never race, tar-path-traversal/symlink-escape hardening
  on extraction, and a **pluggable adapter seam** (`ENTRYPOINT`, invoked for `bootstrap|apply|
  health|rollback` — the *exact same four verbs* S14.6/S17.4 give a `--thin` host's `activate`
  contract).
- **`vbpub/tls-edge/get.py` is a live consumer** of this generator (`# generated by cmru get-py`
  in its own header) — proof the pattern is production-hardened, not a sketch: token auth
  (`--github-token`/`-file`/`-stdin`/env, with file-permission and ownership checks), HTTPS-only
  + host-allowlisted downloads with an `Authorization`-header-stripping redirect handler, wheel
  SHA256 verification before `pip install --no-index`, and a SIGINT/SIGTERM handler that cleans
  up a partial staging dir rather than leaving one behind.
- Its **one and only** hard-coded dependency on GitHub is the download backend
  (`RELEASES_API = https://api.github.com/repos/{owner}/{repo}/releases`, `_ALLOWED_HOSTS =
  {api.github.com, github.com, *.githubusercontent.com}`) — everything else (transactional
  install, adapter seam, verification chain, locking, scope handling) is backend-agnostic.

**The one thing that does not exist yet**: a second download backend in `get.py.tmpl` /
`cmru get-py`'s renderer, for a **self-hosted, token-authenticated bootstrap URL** instead of
GitHub Releases. D-097 named this explicitly ("self-hosted `get.py --bootstrap-url --token`
…GitHub-independent"). This is a **cmru-side prerequisite** for the design below, not something
this proposal can do inside ciu alone — flagged as an open dependency in §6.

## 3. Proposed design

### 3.1 The new verb: `ciu host enroll <name>`

```
ciu host enroll <name> [--ttl 15m] [--host-hint ADDR] [--admin]
```

Run on the **control host** (wherever `ciu.hosts.toml` for this project lives), interactively,
mirroring `ciu init`'s interview-driven posture (v8 P3 "explicitness over magic" — nothing here
should invent a fact the operator didn't confirm):

1. **Generate the enrollment keypair now, control-side**, not target-side. CIU already owns key
   generation via the existing S4/S14.3a secret machinery (`GEN_LOCAL`); reuse it verbatim rather
   than inventing a second key-generation path. Store the private half exactly where a normal
   `ssh_key` entry would point (project secret store, `.ciu/secrets/hosts/<name>/ssh_key` in v7's
   S14.3a namespace shape, or the v8-equivalent `[secrets.hosts.<h>]` store, S10.4). Generating
   control-side (not target-side, and not by having `get.py` mint its own keypair) means the
   private key never transits the network at all — the public half is the only thing that
   travels, over the bootstrap channel, and the target never needs to report anything secret back.
2. **Issue a single-use, short-TTL bootstrap token** (default 15 min, `--ttl` overridable) and a
   bootstrap URL served **behind tls-edge** (D-097's own words) — `https://<control-tls-edge-fqdn>/
   enroll/<token>`. The token is scoped to exactly one enrollment attempt and is invalidated on
   first successful use or TTL expiry, whichever comes first — this is a bootstrap credential, not
   a standing one, and should be treated with the same posture S14.4a gives host-key pinning
   (fail-closed, no silent fallback).
3. **Print the one-liner** for the admin to run *on the target, once, by hand*:
   ```
   curl -fsSL https://<control-tls-edge-fqdn>/enroll/<token> | python3 - bootstrap
   ```
   This is the entire admin-facing surface. No SSH access to the target is assumed to exist yet;
   `curl` + `python3` (stdlib only, matching every existing `get.py` prerequisite check) is the
   full requirement — deliberately at or below `--thin`'s own bar (S14.6/S17: "an SSH shell with
   only POSIX `sh` + `tar`/`unzip` + `touch`, no Docker and no general-purpose Python" was
   `--thin`'s target; enrollment's target is even barer, since it runs *before* SSH exists at all).
4. **`ciu host enroll` then polls** (or holds the interactive session open, matching `ciu init`'s
   synchronous interview shape) for the callback described in §3.3, up to the TTL. On success it
   writes the `ciu.hosts.toml` row (§3.4) and prints it for operator confirmation before
   persisting — never silent-write a fact this consequential (P3/P10). On TTL expiry it reports
   the token dead and exits non-zero; nothing partial is written.

### 3.2 The enrollment `get.py` variant

A **project-level** `get.py` variant (rendered once, served at the bootstrap URL, not per-host)
whose `ENTRYPOINT` implements exactly one adapter action beyond the four `get.py.tmpl` already
knows about: `bootstrap`. Concretely:

- **Download backend**: the new self-hosted backend from §2 — `--bootstrap-url` (baked into the
  rendered script, not typed by the admin) + the single-use `--token` (the trailing path segment
  of the URL the admin already curled, so no *second* secret needs typing — the token that
  authenticated the download **is** the enrollment credential; §3.3 reuses it for the callback).
- **What `bootstrap` does on the target**, in order, fail-fast at every step (AGENTS.md §4.2a
  DERIVE → READ → FAIL, applied host-side): checks for an existing `openssh-server` (refuses with
  a clear message naming the missing package if absent — this proposal does **not** propose
  auto-installing system packages without operator awareness, matching `get.py.tmpl`'s existing
  `check_prerequisites()` philosophy of refusing before any network I/O rather than improvising);
  creates or confirms the deploy user CIU will connect as; appends the **control-generated public
  key** (shipped inside the bootstrap payload, verified via the same SHA256+minisign chain every
  other `get.py` asset already gets — §2) to that user's `authorized_keys`, `0600`, correct owner;
  reads back the host's own SSH host public key (`/etc/ssh/ssh_host_ed25519_key.pub` or
  equivalent) for §3.3's callback.
- **What it deliberately does NOT do**: generate its own keypair (§3.1.1 — the control host owns
  key generation), install Docker, or run any deploy logic. Enrollment's job ends at "CIU can now
  SSH in." Everything after that is S14/S17's existing, already-specified territory.

### 3.3 The callback

A single authenticated HTTPS POST from target back to the same tls-edge-fronted control endpoint,
using the **same token** (proving the request came from the party that successfully downloaded
the bootstrap payload — no new trust primitive introduced), carrying: the reachable address the
admin should confirm (`--host-hint` from §3.1 if given, else the target's own best guess of its
routable address — flagged for **operator confirmation**, never auto-trusted, since a host behind
NAT/a load balancer cannot reliably self-report its externally-reachable name), and the SSH host
public key read in §3.2's last step. `ciu host enroll` receives this, prints it, and asks the
operator to confirm before writing `known_host` — this is TOFU, same as `CIU_SSH_INSECURE_TOFU=1`
already is, just **automated and logged** instead of a manual `ssh-keyscan` — it does not claim a
stronger trust model than S14.4a already accepts, it only removes the manual step of running and
transcribing `ssh-keyscan`'s output by hand.

### 3.4 What gets written

One row, in whichever schema is current (§0's Target note):

**v7** (`SPEC.md` S14.3, `[deploy.hosts.<name>]`):
```toml
[deploy.hosts.<name>]
ssh_host    = "<confirmed address>"
ssh_key     = "ASK_VAULT:hosts/<name>/ssh_key"   # or the S14.3a local-store path
known_host  = "<algo> <base64-key>"              # from §3.3, operator-confirmed
```

**v8** (`SPEC-V8.md` S7.2, `[hosts.<h>]`):
```toml
[hosts.<name>]
ssh_host    = "<confirmed address>"
ssh_key     = "<S10.4 host-scoped secret path>"
known_host  = "<algo> <base64-key>"
```

Nothing else — `bundle_dir`, `push_mode`, `docker_optional`, `[activate]` are all left to the
operator to add afterward, exactly as they would for a hand-written row today. Enrollment's
job is narrowly "get from nothing to a working, pinned SSH connection," not "fully configure a
deploy target."

## 4. Reuse: enrollment and `--thin` activation are the same artifact

This is the strongest argument for shaping it this way rather than inventing a separate
mechanism: `get.py.tmpl`'s adapter seam already speaks the **exact same four-verb vocabulary**
(`bootstrap|apply|health|rollback` in v7, `bootstrap|apply|health` in v8) that S14.6/S17.4's
`[activate]` host contract requires for a `docker_optional` target. A host enrolled via §3.2's
`get.py bootstrap` action, if it is also going to be a `--thin` deploy target, can have the
**same installed `get.py`** serve as its `activate` entrypoint going forward
(`activate = "python3 /opt/<project>/get.py"`, CIU appending the verb per S14.6b) — no second
script, no second install, no drift between "how this host got enrolled" and "how this host gets
deployed to." A host that will run full Docker-based `ciu up --host` instead simply never invokes
`get.py` again after enrollment; the artifact costs it nothing.

## 5. Security posture (mirrors S14.4 + `get.py`'s existing S4/S5 hardening, does not weaken it)

- Bootstrap tokens are single-use, short-TTL, and served only behind tls-edge HTTPS — never a
  plaintext channel (matches `get.py`'s existing HTTPS-only + host-allowlist enforcement, §2).
- The private key never leaves the control host (§3.1.1) — only the public half and a bootstrap
  token cross the wire, both already-disclosable-by-design values.
- `known_host` is still operator-confirmed before being trusted (§3.3) — this proposal automates
  the *mechanics* of TOFU pinning, not the *trust decision* itself; S14.4a's fail-closed posture
  is unchanged.
- The target-side `bootstrap` action never installs system packages or runs deploy logic (§3.2) —
  its blast radius is exactly "one authorized_keys line," which is the minimum needed to make
  every subsequent S14/S17 verb reachable and nothing more.

## 6. Open dependency: cmru's self-hosted `get.py` backend

§2 flags this explicitly: `get.py.tmpl` today only knows how to download from GitHub Releases.
D-097 wants ("GitHub-independent") and this proposal's §3.2 needs a second backend — a
bootstrap-URL-plus-token download path — added to `cmru get-py`'s template/renderer. This is a
**cmru-repo change**, not a ciu one; this proposal's `ciu host enroll` verb is the *consumer* of
that backend, not its owner. Filing that as a companion cmru backlog entry (cross-referenced to
whatever this proposal is filed as) is a prerequisite before `ciu host enroll` can actually be
implemented end-to-end — the verb can be designed and reviewed independently, but not shipped
without it.

## 7. Open questions for the operator (not resolved by this proposal)

1. **Interactive-and-blocking vs. issue-and-return.** §3.1 proposes `ciu host enroll` holds the
   session open polling for the callback (matching `ciu init`'s synchronous interview shape). An
   alternative: issue the token and one-liner, return immediately, and let a separate `ciu host
   enroll --check <name>` verb poll later — better for an admin who won't run the one-liner for
   an hour. No strong argument either way found in existing doctrine; genuinely the operator's
   call.
2. **Does `--host-hint` matter, or should the callback's self-reported address always require
   confirmation regardless?** §3.3 already requires confirmation either way; `--host-hint` is
   only a convenience default for the confirmation prompt.
3. **Where does the bootstrap-URL tls-edge endpoint actually live** — a new tls-edge route on the
   *control* host's own tls-edge instance, or a small standalone service? D-097 says "behind
   tls-edge" but does not say whose. This proposal assumes the control host's own, consistent with
   "no new trust primitive" (§3.3), but does not treat that as settled.
4. **Should this be CIU-93** (formalizing the tentative, never-filed idea from the P171 carve) or
   a fresh backlog number — check `vbpub/ciu/KNOWN_ISSUES_TODO_BACKLOG.md`'s actual current tail
   before filing either way; do not assume CIU-93 is still free.

## 8. What this proposal deliberately does not do

It does not touch `--thin`'s existing push/activate mechanics (S14.6/S17.3–S17.4), does not
propose changes to the host TOML schema beyond the three fields every hand-written row already
has (§3.4), does not introduce Vault into the pre-trust bootstrap path (D-097 was explicit that
enrollment secrets live in ciu's local store, not Vault — this proposal follows that), and does
not attempt to resolve v8's still-churning release/candidate/current model — enrollment happens
strictly before any of that is reachable, so it has no opinion on it.
