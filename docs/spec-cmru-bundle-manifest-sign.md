# SPEC B — CMRU Deterministic Bundle + Manifest + Signing

| Field | Value |
|---|---|
| **Spec ID** | B |
| **Repo** | `vbpub/cmru` (generic — zero consumer/project knowledge) |
| **Owns** | **Seam 3** — the release manifest JSON schema |
| **Consumed by** | SPEC A (installer *verifies* the manifest + signature) · SPEC F (dstdns *emits* a manifest via this machinery) |
| **Hard deps** | none (can start immediately) |
| **Status** | Ready to implement |
| **Master plan** | `/workspaces/dstdns/docs/plan-cmru-remote-deployment.md` (v2) §3.6, §10 (release product), §5 Seam 3 |

> **Greenfield policy (vbpub house rule):** no compatibility shims, no dual code paths. If a
> behaviour changes, update code + tests + `cmru/docs/SPEC.md` in the same change.

---

## Worktree directive

```
Worktree: create a git worktree for branch `feat/cmru-bundle-manifest-sign` at
/tmp/vbpub-cmru-bundle-manifest-sign and do all work there — never modify /workspaces/vbpub directly:
  git worktree add -b feat/cmru-bundle-manifest-sign /tmp/vbpub-cmru-bundle-manifest-sign main
```

Base on local `main` (it carries the unreleased `get.py` migration and other commits this
work assumes). Run the cmru test suite from inside the worktree.

---

## 1. Goal

Extend cmru's existing `bundle` artifact capability so a project can produce:

1. A **byte-deterministic** release bundle built from an explicit **git-tracked allowlist**
   (never a recursive archive of the working tree), and
2. A **release manifest** (`manifest.json`) describing exactly what the bundle pins
   (versions, wheel checksums, image digests, schema versions), plus
3. A **detached minisign signature** (`manifest.json.minisig`) authenticating publisher
   intent.

cmru provides the **generic mechanics only**. The consuming project (e.g. dstdns, SPEC F)
supplies the allowlist, the image digest map, and the schema-version values. cmru hardcodes
no project file paths (SPEC.md S-REL.3).

Why this matters: the manifest is the **root of authenticity** for remote deployment. The
installer (SPEC A) verifies the signature, then trusts the manifest; because the manifest
pins images by **digest** (content-addressed), image authenticity is transitive and no
separate in-registry image signing is needed for v1.

---

## 2. Background / ground truth (what exists today)

- **`cmru/src/cmru/bundle.py`** — `run_bundle(config_path)` builds a composite archive from a
  TOML config: optional client wheel (`build_wheel`), copy declared files/dirs
  (`copy_sources`), then `create_archive` which calls **`shutil.make_archive`**. pwmcp uses
  this today (`artifacts = ["oci-image", "bundle"]`). **It is NOT deterministic** —
  `shutil.make_archive` does not normalize member order, mtime, uid/gid, or mode, and
  `copy_sources` copies whatever is on disk (no allowlist, no excludes).
- **`cmru/src/cmru/delegated.py`** — S7 delegated-tool wrappers. `cosign_sign(artifact, key=…)`
  already shells out to `cosign sign-blob --yes [--key …]`. Pattern to copy: `_which(tool)` →
  skip/exit-3 if absent per `required`, else `_run(argv)`; exits via `cmru.exit_codes`
  (`PREREQ_MISSING`=3, `FAILURE`=1).
- **`cmru/src/cmru/release.py`** — `sha256_file()` and `write_sha256_sidecar()` (writes
  `<name>.sha256` in `sha256sum -c` format). Reuse for checksums; do NOT reimplement.
- **Runner / reproducibility** — the cmru runner already sets `SOURCE_DATE_EPOCH` to the HEAD
  commit timestamp before every step (SPEC.md S3.3 / S9). Read it from the environment; do
  not call `date`/`time.time()`.
- **`cmru/docs/SPEC.md`** — S1 (artifact model: `bundle` is a listed type), S9
  (reproducibility / byte-identical contract S9.4). Update both.

**Missing (this spec adds):** determinism normalization, allowlist-driven membership +
excludes, manifest assembly/emission, detached manifest signing + verification helper.

---

## 3. Frozen contract — Release manifest JSON schema (Seam 3)

Emit exactly this shape (embed verbatim; SPEC A and SPEC F depend on it byte-for-byte):

```json
{
  "schema_version": 1,
  "project": "<name>",
  "tag": "<prefix><semver>",
  "source_commit": "<sha>",
  "created": "<ISO8601 derived from SOURCE_DATE_EPOCH>",
  "cmru": {"version": "x.y.z", "wheel": "vendor/cmru-x.y.z-...whl", "sha256": "<hex>"},
  "ciu":  {"version": "x.y.z", "wheel": "vendor/ciu-x.y.z-...whl",  "sha256": "<hex>"},
  "installer_schema_version": 1,
  "host_config_schema_version": 1,
  "images": {
    "<service>": {
      "repository": "ghcr.io/<owner>/<name>-<service>",
      "tag": "<prefix><semver>",
      "digest": "sha256:<hex>"
    }
  },
  "platform": {"min_python": "3.11", "arch": ["amd64"]},
  "upgrade": {"min_from": "<tag>", "rollback_to": ["<tag>"]}
}
```

Rules:
- `manifest.json` MUST be serialized **canonically** so it is itself deterministic: UTF-8,
  `sort_keys=True`, `separators=(",", ":")` (or a fixed indent — pick one and pin it in
  SPEC.md), trailing newline. Two builds of the same input MUST produce identical bytes.
- `created` is derived from `SOURCE_DATE_EPOCH` (`datetime.fromtimestamp(epoch, tz=UTC)`),
  never wall-clock.
- `images`, `installer_schema_version`, `host_config_schema_version`, and `upgrade` are
  **inputs supplied by the project** (SPEC F) — cmru assembles them, it does not invent them.
- `cmru`/`ciu` wheel `sha256` come from `release.sha256_file()` over the bundled wheels.

### Signature

- Detached: `manifest.json.minisig` via **minisign** (Ed25519).
- The minisign **trusted comment** (signed, tamper-evident) MUST be:
  `project=<name> tag=<tag> manifest_sha256=<hex>` where `<hex>` is `sha256(manifest.json)`.
  This binds the signature to the exact manifest bytes even if an attacker swaps files.
- **Key generation:** `minisign -G -p minisign.pub -s minisign.key` (document this). The
  **secret key** is a release-time secret resolved with the **same discipline as the cmru
  GitHub token (SPEC.md S2.4)**: from an env var / a gitignored secret file — **never
  committed**, never in `cmru.toml`. The **public key** is published (and is distributed to
  hosts via the deployment enrollment seed, per the master plan).
- Verification helper (used by tests here and by SPEC A on hosts):
  `minisign -Vm manifest.json -p minisign.pub` — exit 0 only if signature + trusted comment
  verify against the bundled manifest.

---

## 4. Determinism rules (the byte-identical contract)

1. **Allowlist, not walk.** Membership comes from an explicit list of **git-tracked** paths
   (resolved via `git ls-files`-style tracking on the project), expanded to files. Never
   recursively archive the working directory — the checkout carries large runtime state.
2. **Hard excludes** (belt-and-suspenders even if an allowlisted dir contains them): `.git`,
   `.ciu`, rendered `*.toml`/compose outputs, `ciu.env`, volume/data dirs, secret stores,
   certificates, tokens, `__pycache__`/caches, test output, runtime logs.
3. **Normalize every tar member:** sort members by path (stable, locale-independent — use
   bytes/`C` ordering); set `mtime = SOURCE_DATE_EPOCH`; `uid = gid = 0`,
   `uname = gname = ""`; normalize mode (e.g. `0644` files / `0755` dirs, preserve the
   executable bit only where intended); drop device/char/fifo nodes.
4. **Pin compression.** Fixed format + fixed xz preset (e.g. `xz -6`, no timestamp in the
   container). Do not use `shutil.make_archive` for the deterministic path — write the tar
   explicitly with the `tarfile` module so every field is controlled.
5. **Build-twice gate.** A test builds the bundle twice from the same commit and asserts
   **identical sha256** (S9.4).

---

## 5. Implementation tasks (file-by-file)

1. **`bundle.py` — deterministic archiver.**
   - Add an `allowlist` input (list of git-tracked project-relative paths/globs) and an
     `exclude` rule set (§4.2). Replace the `create_archive`/`shutil.make_archive` path with
     a `_write_deterministic_tar(members, out_path)` that opens `tarfile.open(mode="w:xz")`
     and writes each `TarInfo` with normalized fields (§4.3). Keep the existing
     `copy_sources`/`build_wheel` flow only where still needed; the bundle members for the
     deterministic path come from the allowlist + the built wheels + the generated manifest.
   - Read `SOURCE_DATE_EPOCH` from env; fail clearly if unset when determinism is requested.
   - Keep `bundle.py` generic: all project specifics arrive via the config table / function
     args, never literals.
2. **`manifest.py` — new module.**
   - `build_manifest(*, project, tag, source_commit, cmru_wheel, ciu_wheel, images,
     installer_schema_version, host_config_schema_version, platform, upgrade) -> dict` —
     assembles the §3 dict; computes wheel `sha256` via `release.sha256_file`; derives
     `created` from `SOURCE_DATE_EPOCH`.
   - `write_manifest(manifest: dict, out_path: Path) -> Path` — canonical serialization (§3
     rules); returns the path. Add `manifest_sha256(path) -> str` helper.
   - The **image digest map is an input** (assembled by the project's build step, SPEC F);
     `manifest.py` validates its shape but never queries a registry itself.
3. **`delegated.py` — minisign sibling.**
   - Add `minisign_sign(blob: Path, *, secret_key: str, trusted_comment: str, required=False)`
     and `minisign_verify(blob: Path, *, public_key: str, required=False) -> bool`, mirroring
     the `cosign_sign` skip/exit-3/failure conventions. `sign` shells out to
     `minisign -S -s <key> -m <blob> -t "<trusted_comment>"`; `verify` to
     `minisign -Vm <blob> -p <public_key>`.
   - Leave `cosign_sign` in place — cosign remains available for **optional** in-registry
     image signing as later defense-in-depth (not used in v1).
   - Extend `run_delegated_config` to honour a `[project.<name>.delegated.minisign]` table
     (`enabled`, `secret_key` source, `required`) if you wire signing through delegated
     config; otherwise call `minisign_sign` directly from the manifest/bundle flow.
4. **Config wiring.** Add a `[project.<name>.bundle]` / `[project.<name>.manifest]` block (or
   extend the existing bundle config) carrying: `allowlist`, `exclude`, wheel sources,
   `manifest` inputs (schema versions, platform, upgrade), and the minisign key source.
   Validate strictly (unknown keys rejected, SPEC.md S2.3). Keep field naming consistent with
   cmru's existing validation conventions.
5. **`cmru/docs/SPEC.md`.** Update S1 (bundle now = deterministic archive + manifest +
   signature) and S9 (add the manifest-canonicalization + build-twice rule). Note minisign as
   the manifest signer alongside cosign (image signing) under S7.

---

## 6. Out of scope (other specs own these)

- The **dstdns** bundle contents / allowlist / image digest map / schema-version values →
  **SPEC F**.
- Installer-side download + signature **verification** on the host → **SPEC A**.
- The `[project.X.installer]` schema → **SPEC A**.

---

## 7. Acceptance criteria & tests

Add tests under `cmru/tests/` (follow the existing `test_*` style; mock external tools where
appropriate, but run real `minisign` if available, else skip-with-note):

1. **Deterministic build:** build the bundle twice from the same commit/`SOURCE_DATE_EPOCH` →
   **identical sha256**. Flip `SOURCE_DATE_EPOCH` → digest changes (proves it's wired).
2. **Allowlist + excludes:** given a fixture tree containing `.git`, `.ciu`, a rendered
   `*.toml`, a fake secret file, and a log → assert **none** appear in the archive; only
   allowlisted paths do.
3. **Manifest schema:** `build_manifest(...)` produces all §3 keys; canonical serialization
   is byte-stable across two calls; `created` tracks `SOURCE_DATE_EPOCH`.
4. **minisign round-trip:** sign `manifest.json`; `minisign_verify` returns true; mutate one
   byte of the manifest → verify returns false; mutate the trusted comment / swap a different
   manifest with a stale comment → verify fails.
5. **Trusted comment binds the manifest:** assert the comment carries the real
   `manifest_sha256`.
6. **Missing secret key** (env/file unset) when signing is requested → clear error (not a
   silent unsigned bundle).
7. **Image map is input, not invented:** with no image map provided, manifest emission for a
   project that declares images fails fast; cmru never reaches out to a registry.

Run: the project's existing cmru test entrypoint (e.g. `python -m pytest cmru/tests -v` or the
repo's `run-*-tests.py`). All green before done.

---

## 8. Done definition

- Deterministic bundle + canonical `manifest.json` + `manifest.json.minisig` produced from
  config; build-twice-identical proven by test.
- `manifest.py` + minisign helpers landed; cosign untouched and still available.
- No project-specific paths in cmru; all specifics arrive via config/args.
- `cmru/docs/SPEC.md` S1/S7/S9 updated; full cmru suite green in the worktree.
- Commit on `feat/cmru-bundle-manifest-sign`; end the commit message with the cmru repo's
  standard co-author trailer.
