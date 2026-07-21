"""GitHub Releases publishing + resolution for the vbpub monorepo.

This is the single implementation every project's publish script routes through, so
the release scheme stays uniform and reproducible across products (ciu, pwmcp, tls-edge).

Design (see docs/SPEC.md S4–S5):

  * Immutable ``<prefix>-v<semver>`` releases are the source of truth. Each carries the
    artifact PLUS a ``<artifact>.sha256`` sidecar, and records the checksum in the notes.
  * "latest" is *resolved*, not duplicated: :func:`resolve_latest` scans ``<prefix>-v*``
    releases and returns the highest semver. This works in a monorepo where GitHub's
    repo-global "Latest" badge cannot be per-project.
  * ``<prefix>-latest`` survives only as a **thin redirect**: a single ``latest.json``
    manifest (version + tag + asset name + sha256 + download URL). No heavy asset dup —
    a stable discovery URL for humans/external consumers, nothing more.

Stdlib only (urllib/json/hashlib) so project publish scripts can import it without
installing anything — they just add ``cmru/src`` to ``sys.path``.

Moved from ``release_manager.github_release`` in P1; ``release_manager.github_release``
is now a re-export shim kept for backwards compatibility until P6.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"


# ─── version helpers ─────────────────────────────────────────────────────────
def is_release_version(version: str) -> bool:
    """True only for a clean tagged release (no ``.dev`` / ``+local`` / dirty segment)."""
    return ".dev" not in version and "+" not in version


def version_to_tag(prefix: str, version: str) -> str:
    """Release/git tag for a version; sanitize PEP 440 ``+`` for ref names."""
    return f"{prefix}-v{version}".replace("+", "-")


def _semver_key(version: str) -> tuple:
    """Sort key for ``<prefix>-v`` versions. Tolerates suffixes like ``1.61.0-r2``.

    Each dot/dash-separated chunk is tokenised into maximal digit / non-digit runs so
    numeric runs compare as INTEGERS: ``r10`` sorts *above* ``r2`` (lexical comparison
    would invert them and :func:`resolve_latest` would pick the older release). Digit
    run → ``(0, int, "")``; text run → ``(1, 0, str)`` (digit runs sort before text). A
    plain ``1.61.0`` sorts below ``1.61.0-r2`` (shorter token tuple is the prefix).
    """
    tokens: list = []
    for chunk in version.replace("-", ".").split("."):
        for run in re.findall(r"\d+|\D+", chunk):
            tokens.append((0, int(run), "") if run.isdigit() else (1, 0, run))
    return tuple(tokens)


# ─── checksums ───────────────────────────────────────────────────────────────
def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_sha256_sidecar(path: Path) -> Path:
    """Write ``<name>.sha256`` next to ``path`` in ``sha256sum``-verifiable format."""
    digest = sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return sidecar


# ─── GitHub Releases REST client ─────────────────────────────────────────────
class GitHubReleases:
    """Thin REST wrapper for one repo. Mirrors the helpers the per-project publish
    scripts used to each carry, so they can be deleted in favour of this."""

    def __init__(self, owner: str, repo: str, token: str, api_base: str = API_BASE) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
        self.api_base = api_base

    # low level -----------------------------------------------------------------
    def _request(self, method: str, url: str, data: bytes | None = None,
                 content_type: str | None = None) -> tuple[int, str]:
        headers = {
            "Accept": "application/vnd.github+json",
        }
        # Only send an Authorization header when a token is present. An empty
        # "Bearer " header makes GitHub return 401 even for public repos, which
        # breaks unauthenticated reads (e.g. resolving a public wheel).
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(url, method=method, headers=headers, data=data)
        try:
            with urlopen(req) as resp:
                return resp.status, resp.read().decode("utf-8")
        except HTTPError as exc:
            return exc.code, (exc.read().decode("utf-8") if exc.fp else "")

    def _fail(self, msg: str, status: int, body: str) -> None:
        print(f"[ERROR] {msg}\n[ERROR] HTTP {status}: {body}", file=sys.stderr)
        raise SystemExit(1)

    def _repo_url(self, suffix: str) -> str:
        return f"{self.api_base}/repos/{self.owner}/{self.repo}{suffix}"

    # releases ------------------------------------------------------------------
    def get_release_by_tag(self, tag: str) -> Optional[Dict[str, Any]]:
        status, body = self._request("GET", self._repo_url(f"/releases/tags/{tag}"))
        if status == 404:
            return None
        if status >= 400:
            self._fail(f"fetch release {tag}", status, body)
        return json.loads(body)

    def list_releases(self, per_page: int = 100) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        page = 1
        while True:
            status, body = self._request(
                "GET", self._repo_url(f"/releases?per_page={per_page}&page={page}"))
            if status >= 400:
                self._fail("list releases", status, body)
            batch = json.loads(body)
            out.extend(batch)
            if len(batch) < per_page:
                return out
            page += 1

    def create_release(self, tag: str, title: str, notes: str,
                       target_commitish: Optional[str] = None) -> Dict[str, Any]:
        obj: Dict[str, Any] = {"tag_name": tag, "name": title, "body": notes}
        if target_commitish:
            obj["target_commitish"] = target_commitish
        payload = json.dumps(obj).encode()
        status, body = self._request("POST", self._repo_url("/releases"),
                                     data=payload, content_type="application/json")
        if status >= 400:
            self._fail(f"create release {tag}", status, body)
        return json.loads(body)

    def update_release(self, release_id: int, title: str, notes: str) -> Dict[str, Any]:
        payload = json.dumps({"name": title, "body": notes}).encode()
        status, body = self._request("PATCH", self._repo_url(f"/releases/{release_id}"),
                                     data=payload, content_type="application/json")
        if status >= 400:
            self._fail(f"update release {release_id}", status, body)
        return json.loads(body)

    def delete_release(self, release_id: int) -> None:
        status, body = self._request("DELETE", self._repo_url(f"/releases/{release_id}"))
        if status >= 400:
            self._fail(f"delete release {release_id}", status, body)

    # assets --------------------------------------------------------------------
    def list_assets(self, release_id: int) -> List[Dict[str, Any]]:
        status, body = self._request("GET", self._repo_url(f"/releases/{release_id}/assets"))
        if status >= 400:
            self._fail(f"list assets {release_id}", status, body)
        return json.loads(body)

    def delete_asset(self, asset_id: int) -> None:
        status, body = self._request("DELETE", self._repo_url(f"/releases/assets/{asset_id}"))
        if status >= 400:
            self._fail(f"delete asset {asset_id}", status, body)

    def upload_asset(self, upload_url: str, asset_path: Path, asset_name: str) -> None:
        url = f"{upload_url.split('{', 1)[0]}?name={asset_name}"
        status, body = self._request("POST", url, data=asset_path.read_bytes(),
                                     content_type="application/octet-stream")
        if status >= 400:
            self._fail(f"upload asset {asset_name}", status, body)

    # composite -----------------------------------------------------------------
    def publish(self, tag: str, title: str, notes: str, assets: List[Path],
                *, recreate: bool = False,
                target_commitish: Optional[str] = None) -> Dict[str, Any]:
        """Create/refresh ``tag`` and (re)upload ``assets`` (same-named ones replaced)."""
        release = self.get_release_by_tag(tag)
        if release is None:
            release = self.create_release(tag, title, notes, target_commitish)
        elif recreate and release.get("id"):
            self.delete_release(int(release["id"]))
            release = self.create_release(tag, title, notes, target_commitish)
        elif release.get("id"):
            self.update_release(int(release["id"]), title, notes)

        rid, upload_url = release.get("id"), release.get("upload_url")
        if not rid or not upload_url:
            self._fail(f"release {tag} missing id/upload_url", 0, json.dumps(release))

        existing = {a.get("name"): a for a in self.list_assets(int(rid))}
        for asset in assets:
            old = existing.get(asset.name)
            if old and old.get("id"):
                self.delete_asset(int(old["id"]))
            self.upload_asset(str(upload_url), asset, asset.name)
        return release

    def asset_download_url(self, tag: str, asset_name: str) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/releases/download/{tag}/{asset_name}"

    # resolution ----------------------------------------------------------------
    def resolve_latest(self, prefix: str) -> Optional[Dict[str, Any]]:
        """Highest-semver ``<prefix>-v*`` release. The monorepo-safe "latest".

        Returns ``{version, tag, assets:[{name,url}...]}`` or ``None`` if none exist.
        Ignores the thin ``<prefix>-latest`` redirect and any prereleases/drafts.
        """
        marker = f"{prefix}-v"
        candidates = []
        for rel in self.list_releases():
            tag = rel.get("tag_name", "")
            if not tag.startswith(marker) or rel.get("draft") or rel.get("prerelease"):
                continue
            candidates.append((tag[len(marker):], rel))
        if not candidates:
            return None
        version, rel = max(candidates, key=lambda c: _semver_key(c[0]))
        return {
            "version": version,
            "tag": rel.get("tag_name"),
            "assets": [{"name": a.get("name"),
                        "url": a.get("browser_download_url")}
                       for a in rel.get("assets", [])],
        }


# ─── high-level publish entrypoint ───────────────────────────────────────────
def publish_versioned(
    gh: GitHubReleases,
    *,
    prefix: str,
    version: str,
    asset_path: Path,
    notes: Optional[str] = None,
    extra_assets: Optional[List[Path]] = None,
    latest_pointer: bool = True,
    target_commitish: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish ``asset_path`` for ``version`` under the uniform scheme.

    * Always writes + uploads a ``<asset>.sha256`` sidecar and records the digest in
      the release notes (reproducibility baseline).
    * Clean release version → immutable ``<prefix>-v<version>`` release with
      ``[asset, sidecar, *extra_assets]``.
    * ``latest_pointer`` → refresh the thin ``<prefix>-latest`` redirect:
        - release build: a single ``latest.json`` manifest pointing at the versioned
          release (NO heavy asset duplication).
        - dev/dirty build (no version tag): upload the real asset to ``-latest`` so CI
          consumers still have something to fetch.

    Returns ``{version, release_tag|None, sha256, asset_url|None}``.
    """
    digest = sha256_file(asset_path)
    sidecar = write_sha256_sidecar(asset_path)
    extras = list(extra_assets or [])
    result: Dict[str, Any] = {"version": version, "sha256": digest,
                              "release_tag": None, "asset_url": None}

    released = is_release_version(version)
    if released:
        release_tag = version_to_tag(prefix, version)
        body = (notes or f"{prefix} {version}") + (
            f"\n\n**Artifact:** `{asset_path.name}`\n"
            f"**SHA256:** `{digest}`\n\n"
            f"Verify: `sha256sum -c {asset_path.name}.sha256`\n\n"
            f"Resolve latest programmatically by scanning `{prefix}-v*` releases "
            f"(highest semver); see cmru docs/SPEC.md S5."
        )
        gh.publish(release_tag, release_tag, body, [asset_path, sidecar, *extras],
                   target_commitish=target_commitish)
        result["release_tag"] = release_tag
        result["asset_url"] = gh.asset_download_url(release_tag, asset_path.name)
        print(f"[INFO] Published immutable release {release_tag} (+ .sha256)")
    else:
        print(f"[INFO] Dev build {version} — no immutable version release")

    if latest_pointer:
        latest_tag = f"{prefix}-latest"
        if released:
            manifest = asset_path.with_name("latest.json")
            manifest.write_text(json.dumps({
                "project": prefix,
                "version": version,
                "tag": result["release_tag"],
                "asset": asset_path.name,
                "sha256": digest,
                "url": result["asset_url"],
                "note": "thin redirect — the real artifact lives on the versioned release",
            }, indent=2) + "\n", encoding="utf-8")
            gh.publish(latest_tag, latest_tag,
                       f"{prefix} latest → {version} (thin pointer; see latest.json)",
                       [manifest], recreate=True)
            print(f"[INFO] Refreshed thin pointer {latest_tag} → {result['release_tag']}")
        else:
            gh.publish(latest_tag, latest_tag, f"{prefix} latest (dev → {version})",
                       [asset_path, sidecar], recreate=True)
            print(f"[INFO] Moved {latest_tag} (dev asset → {version})")

    return result


# ─── multi-variant publish (S-REL.6) ─────────────────────────────────────────
@dataclass(frozen=True)
class VariantArtifact:
    """One built per-interpreter variant to publish under a multi-variant release.

    ``asset_path`` is the artifact the build produced for this variant; it is uploaded
    (renamed if needed) as ``<prefix>-v<version>-<name><suffix>``. ``extra_assets`` are
    per-variant sidecars uploaded alongside it (for a ``bundle``: its ``manifest.json``
    and ``manifest.json.minisig``), namespaced by the variant so they never collide.
    """
    name: str
    asset_path: Path
    extra_assets: Sequence[Path] = ()
    label: Optional[str] = None


def variant_asset_name(prefix: str, version: str, variant: str, suffix: str) -> str:
    """Deterministic per-variant asset name: ``<prefix>-v<version>-<variant><suffix>``."""
    return f"{version_to_tag(prefix, version)}-{variant}{suffix}"


def _place_named(src: Path, target_name: str) -> Path:
    """Ensure a file named ``target_name`` exists next to ``src`` (copy iff renamed)."""
    dest = src.with_name(target_name)
    if src.name != target_name:
        shutil.copy2(src, dest)
    return dest


def publish_versioned_variants(
    gh: GitHubReleases,
    *,
    prefix: str,
    version: str,
    variants: Sequence[VariantArtifact],
    asset_suffix: str,
    notes: Optional[str] = None,
    latest_pointer: bool = True,
    target_commitish: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish N per-interpreter variants under ONE ``<prefix>-v<version>`` release.

    Each variant contributes a deterministically-named asset
    (``<prefix>-v<version>-<name><asset_suffix>``) plus its ``.sha256`` sidecar and any
    per-variant ``extra_assets`` (for ``bundle``: manifest.json + .minisig). The thin
    ``<prefix>-latest`` pointer records the full variant list and every hash, so an
    installer can present the choice and verify the selected one (see get.py ``--variant``).

    Mirrors :func:`publish_versioned` (single-asset path) but for the variant matrix;
    the single-asset function is intentionally left byte-for-byte unchanged.

    Returns ``{version, release_tag|None, variants:[{name, asset, sha256, url, label}]}``.
    """
    if not variants:
        _die("publish_versioned_variants requires at least one variant")

    released = is_release_version(version)
    release_tag = version_to_tag(prefix, version)

    records: List[Dict[str, Any]] = []
    assets: List[Path] = []
    for v in variants:
        canonical = variant_asset_name(prefix, version, v.name, asset_suffix)
        asset = _place_named(Path(v.asset_path), canonical)
        digest = sha256_file(asset)
        sidecar = write_sha256_sidecar(asset)
        assets.extend([asset, sidecar])
        for extra in v.extra_assets:
            extra = Path(extra)
            extra_name = f"{release_tag}-{v.name}.{extra.name}"
            assets.append(_place_named(extra, extra_name))
        records.append({
            "name": v.name,
            "asset": canonical,
            "sha256": digest,
            "url": gh.asset_download_url(release_tag, canonical) if released else None,
            "label": v.label,
        })

    result: Dict[str, Any] = {"version": version, "release_tag": None, "variants": records}

    if released:
        variant_lines = "\n".join(
            f"- `{r['asset']}` — `{r['sha256']}`" for r in records
        )
        body = (notes or f"{prefix} {version}") + (
            f"\n\n**Variants** ({len(records)}), one asset each under this tag "
            f"(select at install time with `--variant <name>`):\n{variant_lines}\n\n"
            f"Verify: `sha256sum -c <asset>.sha256`\n\n"
            f"Resolve latest programmatically by scanning `{prefix}-v*` releases "
            f"(highest semver); see cmru docs/SPEC.md S5."
        )
        gh.publish(release_tag, release_tag, body, assets, target_commitish=target_commitish)
        result["release_tag"] = release_tag
        print(f"[INFO] Published immutable multi-variant release {release_tag} "
              f"({', '.join(r['name'] for r in records)})")
    else:
        print(f"[INFO] Dev build {version} — no immutable version release")

    if latest_pointer:
        latest_tag = f"{prefix}-latest"
        if released:
            manifest = Path(variants[0].asset_path).with_name("latest.json")
            manifest.write_text(json.dumps({
                "project": prefix,
                "version": version,
                "tag": release_tag,
                "variants": [
                    {"name": r["name"], "asset": r["asset"], "sha256": r["sha256"],
                     "url": r["url"], "label": r["label"]}
                    for r in records
                ],
                "note": "multi-variant release — pick a variant at install time "
                        "(get.py --variant <name>); assets live on the versioned release",
            }, indent=2) + "\n", encoding="utf-8")
            gh.publish(latest_tag, latest_tag,
                       f"{prefix} latest → {version} "
                       f"({len(records)} variants; thin pointer, see latest.json)",
                       [manifest], recreate=True)
            print(f"[INFO] Refreshed thin pointer {latest_tag} → {release_tag}")
        else:
            gh.publish(latest_tag, latest_tag, f"{prefix} latest (dev → {version})",
                       assets, recreate=True)
            print(f"[INFO] Moved {latest_tag} (dev variant assets → {version})")

    return result


# ─── profile glue (was duplicated across project publish scripts) ─────────────
# These are the per-artifact helpers every wheel project re-implemented (find the
# built wheel, read its version, assert the published "latest" is well-formed). They
# now live here once so a project can route through cmru's built-in handlers (see
# cmru.handlers) instead of carrying its own copy.
def _die(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def _matches_variant(name: str, variant: str, suffix: Optional[str]) -> bool:
    """True if asset ``name`` belongs to ``variant`` (named ``<base>-<variant><suffix>``).

    When ``suffix`` is known it is stripped first so a dotted version (e.g. ``1.0.0``)
    is never mistaken for a file extension; the remaining base must end in ``-<variant>``.
    """
    base = name[: -len(suffix)] if (suffix and name.endswith(suffix)) else name
    return base.endswith(f"-{variant}")


def find_artifact(directory: Path, glob: str, *, variant: Optional[str] = None,
                  suffix: Optional[str] = None) -> Path:
    """Return the single artifact the build step produced (never rebuilds — a rebuild
    would differ in bytes/version from what was tested). Errors if 0 or >1 match.

    Generic over artifact type (wheel ``.whl``, tarball ``.tar.xz``, …) — every profile
    can route through this rather than carrying its own discovery logic.

    Multi-variant (S-REL.6): when ``variant`` is given, the glob matches are further
    narrowed to the single asset named ``<base>-<variant><suffix>``. This is why a
    multi-variant build (several ``<prefix>-v*`` files in dist/) no longer trips the
    ">1 match" guard — resolution is by (tag, variant), so each variant resolves to
    exactly one file while genuine duplicates within a variant still error."""
    directory = Path(directory)
    matches = sorted(directory.glob(glob))
    if variant is not None:
        matches = [m for m in matches if _matches_variant(m.name, variant, suffix)]
    if not matches:
        extra = f" for variant {variant!r}" if variant is not None else ""
        _die(f"No artifact in {directory} (glob: {glob}){extra}. Run the build step first.")
    if len(matches) > 1:
        _die(f"Multiple artifacts in {directory}: {[m.name for m in matches]}; clean + rebuild.")
    return matches[0]


def find_built_wheel(dist_dir: Path, wheel_glob: str) -> Path:
    """Return the single wheel the build step produced. Alias for :func:`find_artifact`."""
    return find_artifact(dist_dir, wheel_glob)


def read_wheel_version(wheel_path: Path) -> str:
    """Canonical version from the wheel METADATA (single source of truth)."""
    import zipfile

    with zipfile.ZipFile(wheel_path) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        for line in zf.read(meta).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    _die(f"No Version field in {Path(wheel_path).name} METADATA")
    return ""  # unreachable (_die raises)


def validate_latest_release(
    gh: "GitHubReleases",
    prefix: str,
    *,
    artifact_suffix: str = ".whl",
    require_sha256: bool = True,
    retries: int = 6,
    delay: int = 5,
) -> Dict[str, Any]:
    """Assert the resolved "latest" for ``prefix`` is a well-formed release.

    Resolves the highest-semver ``<prefix>-v*`` release (the monorepo-safe "latest",
    *not* the thin ``-latest`` pointer), asserts it carries an artifact ending in
    ``artifact_suffix`` plus (optionally) a matching ``.sha256`` sidecar. Retries to
    absorb GitHub's brief releases-list eventual-consistency right after a publish.

    Returns ``{version, tag, asset, url, sha256_url}``. Generic over artifact type
    (wheel ``.whl``, tarball ``.tar.xz``, …) so every profile can reuse it.
    """
    import time

    info = None
    for attempt in range(retries):
        info = gh.resolve_latest(prefix)
        if info is not None:
            break
        if attempt < retries - 1:
            print(f"[INFO] No {prefix}-v* release visible yet; retrying ({attempt + 1}/{retries})…")
            time.sleep(delay)
    if info is None:
        _die(f"No {prefix}-v* releases found in the repository")

    assets = {a["name"]: a["url"] for a in info["assets"]}
    matches = [n for n in assets if n.endswith(artifact_suffix)]
    if not matches:
        _die(f"Release {prefix}-v{info['version']} has no {artifact_suffix} asset")
    if len(matches) > 1:
        print(f"[WARN] Multiple {artifact_suffix} assets in {prefix}-v{info['version']}: "
              f"{matches}; using first")
    primary = matches[0]
    sidecar = primary + ".sha256"
    if require_sha256 and sidecar not in assets:
        _die(f"Release {prefix}-v{info['version']} is missing the {sidecar} sidecar; "
             "cannot verify integrity")
    return {
        "version": info["version"],
        "tag": info["tag"],
        "asset": primary,
        "url": assets[primary],
        "sha256_url": assets.get(sidecar),
    }
