# SPEC MINISIGN — Enable minisign bundle signing (fast-follow)

**Spec ID:** MINISIGN
**Repo:** `vbpub` (cmru release config + consumers).
**Status:** ready. Created 2026-06-21 as the "sign as fast-follow" half of the release-time
minisign decision (the release shipped artifacts **unsigned** = prior status quo; this spec turns
signing on, with a real pubkey-distribution plan rather than a rushed wire-up).

> Self-contained. The signing *mechanism* already exists (cmru `delegated.minisign_sign` +
> `get.py.tmpl` `_verify_minisign`); only the per-project *config* + the *trust-root distribution*
> are missing.

---

## 0. What already exists (no work needed)
- **Sign side:** `cmru/src/cmru/delegated.py` `minisign_sign(blob, *, secret_key, trusted_comment)`
  and the release hook (`delegated.py:308-336`) that reads `[project.<name>.delegated.minisign]`
  (`enabled`, `secret_key_env` | `secret_key_file`, `trusted_comment`, `required`).
- **Verify side:** `cmru/templates/get.py.tmpl` `_verify_minisign()` runs `minisign -Vm manifest -P <pub>`
  before extraction; CLI arg `--manifest-pubkey` / env `CMRU_MINISIGN_PUBKEY`.
- **Tool:** minisign is now in the `mdt` toolchain image (`apt/packages.list`) and the cmru agent
  carries `minisign_pubkey` through enrollment (`agent/cli.py`).
- **Bundle hygiene:** `cmru/src/cmru/bundle.py:64` already excludes `minisign.key` from any bundle.

## 1. The generated trust root (this session)
A fresh Ed25519 keypair was generated for the release toolchain:
- **Public key (safe to publish/pin):** `RWS3E3vAMFRhE+IFwPRKkv1VcLeqZIzKShZeB+QjX7u2iOMK7WfqEwk4`
- **Key ID:** `13615430C07B13B7`
- **Secret key:** `~/.minisign/minisign.key` (mode 0600, no passphrase — automation key).

⚠ **Persistence:** the secret key currently lives only in the devcontainer `$HOME`, which is **not**
durable across a container rebuild. Because this release shipped **unsigned**, the pubkey is **not yet
published**, so regenerating is currently harmless. BEFORE enabling signing (Task A), move the secret
key to a durable, gitignored store (alongside `cmru.secret.toml`) and treat the pubkey above as the
committed trust root — or generate the real production key at that point and update this doc.

## 2. Tasks

### Task A — wire signing for bundle-producing projects
For each project that publishes a cmru installer/manifest bundle (start with **tls-edge** — it already
has `[project.tls-edge.installer]` with `manifest_name`/`signature_name`; assess **pwmcp** and the cmru
self-bundle), add to `cmru.toml`:
```toml
[project.<name>.delegated.minisign]
enabled         = true
secret_key_file = "/durable/path/minisign.key"   # or secret_key_env = "CMRU_MINISIGN_SECKEY"
trusted_comment = "project=<name> version={{version}}"
required        = true                            # fail the release if signing fails
```
Confirm the manifest + `.minisig` sidecar are published next to the bundle.

### Task B — pubkey distribution (the actual hard part)
Decide + implement how consumers obtain and pin the pubkey so verification is meaningful:
1. Commit the pubkey to a tracked, discoverable path (e.g. `cmru/keys/minisign.pub` + reference in
   SPEC B), so `get.py --manifest-pubkey <file>` and `CMRU_MINISIGN_PUBKEY` resolve it.
2. Document the key ID + rotation policy in SPEC B (`cmru/docs/`).
3. Update the dstdns consumer/enrollment path to pass the pubkey (`agent enroll --minisign-pubkey`).

### Task C — make verification non-optional once distributed
`get.py.tmpl:425` currently *skips* verification when no pubkey is supplied ("No --manifest-pubkey
provided; skipping"). After Task B, change the default in the release/install path so a missing pubkey
is an error for first-party installs (keep the escape hatch only for explicit `--insecure`).

## 3. Acceptance
1. A released bundle ships `manifest.json` + `manifest.json.minisig`; `minisign -Vm` verifies it with
   the committed pubkey.
2. `get.py` install fails closed when the signature is missing/invalid (post Task C).
3. The secret key lives in a durable gitignored store; the pubkey + key ID are documented in SPEC B.
4. dstdns enrollment passes the pubkey end-to-end.
