# SPEC A — CMRU Installer Contract v2 (`get.py`)

| | |
|---|---|
| **Spec ID** | A |
| **Repo** | `vbpub/cmru` |
| **Owns** | Seam 1 — installer config schema + project-adapter invocation contract |
| **Depends on** | Seam 3 — release manifest JSON (SPEC B, `vbpub/docs/spec-cmru-bundle-manifest-sign.md`) |
| **Consumed by** | SPEC F (dstdns adapter + release product), SPEC G (agent installs via this library) |
| **Status** | Ready to implement |
| **Master plan** | `/workspaces/dstdns/docs/plan-cmru-remote-deployment.md` (v2) — §3.3, §6 (A), §8 |

> This doc is self-contained. You do **not** need the master plan to implement it, but §8
> there has the bootstrap/transport context if you want the bigger picture.

---

## Worktree directive (do this first)

```
Worktree: create a git worktree for branch `feat/cmru-installer-v2` at
/tmp/vbpub-cmru-installer-v2 and do all work there — never modify /workspaces/vbpub directly:
  git worktree add -b feat/cmru-installer-v2 /tmp/vbpub-cmru-installer-v2 main
```

Base on the local `main` (it carries the unreleased `get.sh`→`get.py` migration that this
spec builds on). All paths below are relative to the cmru package root
`/tmp/vbpub-cmru-installer-v2/cmru/` unless noted.

---

## 1. Goal

Rewrite the emitted `get.py` from a narrow download-and-extract script into a **transactional
installer**: private-repo asset auth, **atomic** install/update/rollback with a `current`
symlink, system/user scope, install of bundled CMRU+CIU wheels into a private venv,
**SHA256 + minisign-signed-manifest** verification before extraction, and a defined
**project-adapter invocation contract**. The installer stays Python-3 **stdlib-only** (it
shells out to `minisign`, `docker`, and the project adapter). This is greenfield per
`AGENTS.md` §4.1 — replace `[getsh]` with `[installer]`; do **not** parse both.

---

## 2. Background / ground truth (what exists today)

The generator and the only live consumer:

- **`src/cmru/getpy.py`** — the emitter. `render_get_py()` (line 62) does `[[PLACEHOLDER]]`
  string substitution on a template; `render_from_config()` (line 131) reads
  `proj.getsh` and **raises if `[getsh]` is absent** (lines 138-141);
  `_render_preserve_func()` (line 22) code-generates the preserve/restore helpers;
  `getpy_main()` (line 162) is the `cmru get-py --project <name> --config <toml>` CLI.
- **`templates/get.py.tmpl`** — the rendered installer template (`[[VARNAME]]` syntax).
- **`src/cmru/config.py`** — `GetShConfig` dataclass (line 61: `install_dir`, `preserve`,
  `deps`, `next_steps`); parsed at lines 192-226 (`if "getsh" in raw:`); attached as
  `ProjectS2Config.getsh` (line 86). Config validation is fail-fast / unknown-keys-rejected
  (SPEC.md S2.3 / V09) — extend that machinery, don't bypass it.
- **`vbpub/tls-edge/get.py`** — the **only** project currently emitting an installer. Study
  it as the reference for what to generalize:
  - `download_and_verify()` (line 164): downloads `<tag>.tar.xz` + `.sha256` sidecar,
    compares SHA256, `fatal()` on mismatch (lines 178-187). **No signature.**
  - `artifact_install()` (line 192): extracts with `tarfile`, strips the top-level path
    component, and uses `filter="data"` only on py≥3.12 (lines 206-209). **Extracts straight
    into `project_home`, not a staging dir; no atomic swap.**
  - `preserve_config_files()` / `restore_config_files()` (lines 250-275): `.pre-update`
    backup/restore of named config files.
  - `do_install`/`do_update` (lines 279-376): version-file based, `git_install` air-gapped
    fallback (no checksum, warns).
- **`src/cmru/release.py`** — `GitHubReleases._request()` (line 89) with the load-bearing
  comment (lines 94-98): *"Only send an Authorization header when a token is present. An
  empty `Bearer ` header makes GitHub return 401 even for public repos."* `list_assets`
  (line 165), `delete_asset` (line 171) exist; **no private-asset-by-ID download** yet.
- **`src/cmru/hosts/github.py`** — `GitHubReleaseHost.resolve_latest()` (line 68) returns
  `{version,tag,asset,sha256,url}` using **`browser_download_url`** (public). `download_url()`
  (line 109). **No asset-ID / private-asset path, no redirect-auth handling.**

**Missing today (this spec adds all of it):** atomic `current` symlink swap, rollback,
staging-dir extraction, scope (system/user), bundled-wheel install into a private venv,
private-asset auth, manifest signature verification, the adapter invocation, and **any
generator tests** (there are none for get.py rendering).

---

## 3. Frozen contracts (this spec OWNS these — implement exactly)

### 3.1 Installer config schema — replaces `[getsh]`

```toml
[project.<name>.installer]
install_dir_system = "/opt/<name>"            # system-scope root
install_dir_user   = "<name>"                 # leaf under $XDG_DATA_HOME/<name>
asset_suffix       = ".tar.xz"
entrypoint         = "scripts/bootstrap.py"   # project adapter, relative to release root
required_commands  = ["python3", "docker", "minisign"]
preserve           = ["shared/host.toml", "shared/ciu.env", "shared/.ciu"]
manifest_name      = "manifest.json"
signature_name     = "manifest.json.minisig"

[[project.<name>.installer.wheels]]
path         = "vendor/cmru-*.whl"            # glob inside the release bundle
distribution = "cmru"

[[project.<name>.installer.wheels]]
path         = "vendor/ciu-*.whl"
distribution = "ciu"
```

Field naming must follow cmru's existing S2 validation conventions; the behaviour above is
fixed. `required_commands` are checked **before any network I/O** (mirrors S6.5).

### 3.2 Release layout (on the target host)

```
<root>/releases/<version>/      # one immutable dir per installed version
<root>/current -> releases/<version>   # atomic symlink — the live release
<root>/shared/                  # preserved config/state across versions (never in a release)
<root>/venv/                    # private interpreter; bundled wheels installed here
```

`<root>` = `install_dir_system` (system scope) or `$XDG_DATA_HOME/<name>` /
`~/.local/share/<name>` (user scope).

### 3.3 Adapter invocation contract (Seam 1)

After the new release is staged + validated, `get.py` invokes the project's trusted adapter:

```
<root>/venv/bin/python <root>/current/<entrypoint> <action> \
    --release-root <root>/releases/<version> \
    --config <root>/shared/host.toml \
    --manifest <root>/releases/<version>/manifest.json
```

- `<action> ∈ {bootstrap, apply, health, rollback}`.
- `bootstrap` runs the **pre-network transport-join** (e.g. Tailscale) **before** any
  cross-host reconcile — it is the first adapter call on a fresh install.
- The adapter's exit codes mirror get.py's (below). A non-zero adapter exit aborts the
  transaction **before** the `current` swap (install/update) — the previous release stays live.

### 3.4 Commands

```
get.py install  --config HOST.toml [--version TAG] [--scope system|user]
get.py update   [--version TAG]
get.py status
get.py rollback [--version TAG]
```

### 3.5 Exit codes (identical to SPEC.md S8 / CIU S10.3)

| Code | Meaning |
|---|---|
| 0 | Success / no change |
| 1 | Download, verification, install, or adapter runtime failure |
| 2 | Configuration / schema failure |
| 3 | Missing prerequisite / required env absent |

---

## 4. Verification (consumes Seam 3 from SPEC B)

Before **any** extraction:

1. Download the `<tag><asset_suffix>` artifact + its `.sha256` sidecar; recompute SHA256 and
   compare (reuse the tls-edge `_sha256`/`download_and_verify` pattern). Mismatch → exit 1.
2. The bundle contains `manifest.json` + `manifest.json.minisig`. Verify the signature by
   shelling out: `minisign -Vm manifest.json -P <pubkey>` (or `-p <pubkey-file>`), where the
   public key comes from `--manifest-pubkey FILE` or the host config. Failure → exit 1,
   **before extraction**.
3. The manifest's `cmru`/`ciu` wheel `sha256` fields (Seam 3) are checked against the
   extracted wheels before `pip install`. Mismatch → exit 1.

`minisign` is a `required_commands` entry, so its absence is exit 3 (caught pre-network).

> **Why minisign, not cosign:** the manifest pins every image by **digest**, so image
> authenticity is transitive (content-addressed); we only need to sign one blob (the
> manifest) and verify it offline. minisign (Ed25519, single keypair, `-Vm` verify) is the
> minimal tool for that. cosign stays available in `delegated.py` for optional later
> in-registry image signing — out of scope here.

---

## 5. Private GitHub access

- **Public requests send no Authorization header.** Preserve the `release.py:_request`
  invariant (lines 94-98): an empty `Bearer ` 401s public repos. Only attach auth when a
  token is present *and* the asset is private.
- **Private assets resolved by API asset ID.** Extend `hosts/github.py`: list release assets
  via the API (`GET /releases/{id}/assets`, already in `release.py:list_assets`), find the
  asset by name, then download by **asset ID** with `Accept: application/octet-stream`.
- **Redirect handling:** GitHub redirects asset-ID downloads to a signed object-store URL.
  **Strip the Authorization header before following the redirect** (do not use a default
  redirect-following opener that re-sends auth cross-origin). Implement an explicit
  `urllib` redirect handler.
- **Transport hardening:** HTTPS-only; allowlist GitHub asset hosts
  (`api.github.com`, `github.com`, `objects.githubusercontent.com`, `*.githubusercontent.com`,
  release CDN host); reject `http://` and unknown hosts.
- **Token sources & precedence (documented):** `--github-token TOKEN` (warn: leaks via shell
  history/process list) > `--github-token-file FILE` / `--github-token-stdin` > env
  (`GITHUB_TOKEN`/`CMRU_GITHUB_TOKEN`). **Redact** the token from all logs/errors and remove
  it from the adapter's child-process environment.
- **Registry tokens:** pass via `docker login --password-stdin`, **never** argv.

---

## 6. Implementation tasks (file-by-file)

1. **`src/cmru/config.py`** — add the `[installer]` schema:
   - New dataclasses `InstallerWheel` (`path`, `distribution`) and `InstallerConfig`
     (fields in §3.1). Parse under `if "installer" in raw:`; validate via the existing
     fail-fast/unknown-key machinery (new V-rules, see §7). **Remove `GetShConfig` and its
     parse block (lines 61, 192-226)** and the `ProjectS2Config.getsh` field — greenfield,
     no dual parse. A surviving `[getsh]` key must now fail V09 (unknown key) → exit 2.
2. **`src/cmru/getpy.py`** — rewrite `render_from_config()` to read `proj.installer`; expand
   the placeholder set for the new template (scope dirs, wheels, entrypoint, manifest/sig
   names, required_commands, manifest-pubkey arg). Keep the `[[VARNAME]]` engine + the
   unreplaced-placeholder warning (lines 123-127).
3. **`templates/get.py.tmpl`** — rewrite as the transactional installer. Pipeline per
   command:
   `resolve` (S5 resolver / `--version` pin) → `download` (private-aware §5) →
   `verify` (SHA256 + minisign §4) → `stage` (extract into
   `releases/<version>.staging/`, `filter="data"`, **reject** `..`, absolute paths, device
   nodes, and symlink/hardlink escapes — do not rely on `filter="data"` alone, pre-scan
   members) → `install wheels` (create `<root>/venv` via `python3 -m venv`, then
   `venv/bin/pip install --no-index <vendored wheels>` matched by `installer.wheels[*].path`
   globs + sha-checked) → `invoke adapter` (`bootstrap` on install, `apply` on update) →
   **atomic swap** (`os.symlink` to a temp name + `os.replace` onto `current`) → finalize
   (`releases/<version>.staging` → `releases/<version>`) → **retain previous** release dir
   for rollback (prune to keep N, default 2). `preserve`: copy listed paths into
   `<root>/shared/` and re-link, never into the immutable release. Wrap install/update/
   rollback/bootstrap in a **scope-exclusive lock** (`flock` on `<root>/.lock`; handle SIGINT/
   SIGTERM to release + clean staging). `status` prints current/previous versions + health.
   `rollback` re-points `current` to the previous (or `--version`) release and re-runs adapter
   `rollback`.
4. **`vbpub/tls-edge/`** — migrate the only consumer: replace `[project.tls-edge.getsh]` with
   `[project.tls-edge.installer]` in `cmru.sample.toml` **and** `cmru.toml`; regenerate
   `tls-edge/get.py` via `cmru get-py`; update `tls-edge` docs/README references. tls-edge has
   no wheels/adapter — confirm the template degrades cleanly when `installer.wheels` is empty
   and `entrypoint` is unset (no adapter call, no venv).
5. **`cmru/docs/SPEC.md`** — rewrite **S6 (get.py Contract)** for the transactional model and
   **S2** for `[installer]` (remove `[getsh]`); add the new V-rules to **S10**.

---

## 7. Out of scope

- Bundle building, manifest emission, and signing — **SPEC B**.
- The dstdns CIU adapter / transport-join implementation — **SPEC F / H**.
- The reconciler-agent that *drives* this installer library — **SPEC G** (it imports the
  install/rollback functions; keep them importable, not just CLI-only).

---

## 8. Acceptance criteria & tests

Add a `tests/test_installer.py` (and extend `tests/test_cli_dispatch.py`). **The current
zero-coverage of get.py generation must be closed.** All must pass:

- **Schema:** valid `[installer]` accepted; legacy `[getsh]` key rejected (exit 2); missing
  required fields rejected; unknown sub-keys rejected.
- **Generator:** `cmru get-py` output is reproducible (byte-identical for identical config);
  no unreplaced `[[…]]` placeholders.
- **Auth:** public request carries **no** Authorization header; private asset resolves by ID;
  401/403 handled with a clear error; redirect to object store **drops** Authorization;
  `http://`/unknown host rejected; CLI token emits a warning and never appears in later
  log output; token file with bad perms/symlink/foreign owner rejected; registry token only
  via stdin.
- **Verify:** SHA256 mismatch and minisign-signature failure each abort **before** extraction.
- **Extraction safety:** path traversal, absolute paths, unsafe symlink/hardlink, and device
  nodes are rejected.
- **Transaction:** install → update → rollback round-trips; an interrupted update (kill
  between stage and swap) leaves the previous `current` live and recovers on re-run; bundled
  wheels land in `<root>/venv`; `preserve`d config/state survive an update; lock contention is
  serialized and SIGINT cleans staging; system vs user scope produce correct paths/ownership.
- **Adapter:** a stub adapter is invoked with the exact argv of §3.3; a non-zero adapter exit
  aborts before the `current` swap.

Run: `cd /tmp/vbpub-cmru-installer-v2/cmru && python -m pytest tests/ -v`
(or the repo's `python3 cmru.py`-equivalent test entry). Confirm the existing suite still
passes after the `getsh` removal.

---

## 9. Done

- `[installer]` parsed/validated; `[getsh]` fully removed; tls-edge migrated and regenerated.
- New `get.py` template implements the full transactional pipeline (§6.3) with private auth
  (§5) and SHA256+minisign verification (§4) and the §3.3 adapter contract.
- Install/rollback functions are importable for SPEC G.
- SPEC.md S2/S6/S10 updated; new tests green; existing suite green.
- Commit on `feat/cmru-installer-v2`; do not merge to `main` until SPEC B's manifest schema
  is finalized (the two share the manifest contract).
