# vbpub Full Build/Release Runbook (2026-06-21)

Distilled from the release scout. **`origin` is a PUBLIC GitHub repo** — every `git push`, tag,
GitHub Release, and GHCR image is public + effectively irreversible. **Get explicit human go before
any push.** Local `main` is ~30+ commits ahead of `origin/main` (nothing pushed yet).

## Project inventory + next versions
| Project | Type | Strategy | Last tag | Next | Notes |
|---|---|---|---|---|---|
| **cmru** | wheel | scm+conventional | `cmru-v1.0.0` | **v1.1.0** (minor) | ships SPEC A/B/G; release FIRST (consumers depend on it) |
| **ciu** | wheel | scm+conventional | `ciu-v3.1.0` | **v4.0.0** (breaking) | ⚠ fix `ciu/docs/SPEC.md` (Status→Active, seed `3.0.0`→`4.0.0`) + commit BEFORE tagging |
| **tls-edge** | tarball | file:VERSION | `tls-edge-v1.0.0` | **v1.1.0** | writes `tls-edge/VERSION`, commits → push that commit after |
| **modern-debian-tools-python-debug** | oci-image | none | — | OCI only | `build-push.py` → ghcr.io; visibility-sync is **best-effort only** (see risk #6) |
| **pwmcp** | oci-image+bundle | delegated | `pwmcp-v1.61.0-r1` | self-versioned | the 1.60 matrix is merged (`6381f7d`); release builds BOTH targets |

## Preconditions
- Clean tree; `python3 -m pytest ciu/tests/` (619) + `cmru/tests/` (312) green.
- **Install `minisign`** (`apt-get install -y minisign` + keypair) BEFORE pwmcp, else the bundle ships
  **unsigned** (defeats SPEC B Seam-3 authenticity). cosign not needed (image-signing deferred).
- Auth ready: `GITHUB_PUSH_PAT` in gitignored `cmru.secret.toml`; GHCR docker login active.

## Ordered commands (each `release` line is IRREVERSIBLE/public)
```bash
# 0. Fix CIU SPEC.md (Status→Active, seed 4.0.0), commit.
# 1. Push branch (makes ~30 commits public):
git -C /workspaces/vbpub push origin HEAD:main          # CONFIRM FIRST
# 2. Dry-run preview (safe):
./cmru.py status ; ./cmru.py release --dry-run
# 3. (optional) install minisign for bundle signing
# 4. Release in order:
./cmru.py release --project cmru                          # -> cmru-v1.1.0 wheel + GitHub Release
./cmru.py release --project ciu --set-version 4.0.0       # -> ciu-v4.0.0 wheel
./cmru.py release --project tls-edge ; git -C /workspaces/vbpub push origin HEAD:main  # push VERSION commit
./cmru.py release --project modern-debian-tools-python-debug   # -> ghcr images
./cmru.py release --project pwmcp                         # -> ghcr 1.60+1.61 + bundle (minisign first!)
# 5. Verify: ./cmru.py status  (should report no changes)
```

## Risk register (from scout)
1. **CIU version** — SPEC says 3.0.0 but `ciu-v3.1.0` exists; correct next is **4.0.0** (fix SPEC.md first).
2. **pwmcp 1.60** — matrix merged; release builds both pypi(1.60) + npm(1.61) tag sets.
3. **minisign absent** → unsigned bundle. Install first if the bundle is a real delivery.
4. **branch not on origin** — push `HEAD:main` BEFORE tags (tags would point at commits origin lacks).
5. **ciu v4.0.0 is breaking** — dstdns consumers pinned to `ciu-v3.1.0` must move to `--profile` (SPEC C) first.
6. **GHCR visibility-sync does NOT work via API** (verified 2026-06-21). GitHub has **no REST/GraphQL
   endpoint** to change container-package visibility: `PATCH …/packages/container/<name>` → 404; classic
   PATs have no `admin:packages` scope; **fine-grained PATs can't use the Packages API** (roadmap#558).
   cmru now logs a **non-fatal warning** (it no longer fails the release — the image still pushes).
   **One-time manual step:** flip each package to Public in the UI (*Your packages → pkg → Package
   settings → Danger Zone → Change visibility → Public*). It then **persists across all future pushes**.
   SPEC S4.7 amended from MUST to best-effort.

## Rollback reality
Pushed tags/images can't be cleanly un-published; failures recover by a new patch release, not deletion.
Do dry-run + verify each step; the local-only state today is fully reversible until the first push.
