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

"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from cmru.release_notes import ReleaseNotes, compose_release_body

API_BASE = "https://api.github.com"

_FULL_SHA = re.compile(r"[0-9a-f]{40}")

#: After a CREATE, GitHub may not serve the new release at once. Reads by release ID are
#: attempted this many times (finite, never unbounded), pausing between attempts; an
#: update reads an already-visible release once. Pausing goes through :func:`_pause` so
#: a test injects it and never waits.
READBACK_ATTEMPTS = 5
_READBACK_PAUSES = (1.0, 2.0, 4.0, 8.0)


def _pause(seconds: float) -> None:
    time.sleep(seconds)


# ─── version helpers ─────────────────────────────────────────────────────────
def is_release_version(version: str) -> bool:
    """True only for a clean tagged release (no ``.dev`` / ``+local`` / dirty segment)."""
    return ".dev" not in version and "+" not in version


def version_to_tag(prefix: str, version: str) -> str:
    """Release/git tag for a version; sanitize PEP 440 ``+`` for ref names."""
    return f"{prefix}-v{version}".replace("+", "-")


def asset_name_for_tag(tag: str, suffix: str, variant: Optional[str] = None) -> str:
    """The ONE spelling of a release bundle's asset name, from its release tag.

    ``<tag><suffix>`` (``<tag>-<variant><suffix>`` for a per-interpreter variant), e.g.
    ``tls-edge-v2.0.0.tar.xz``. The producer (:func:`publish_versioned`), the host resolver
    (``hosts/github.py``) and the rendered ``get.py`` (``expected_asset_name`` in
    ``templates/get.py.tmpl``, kept equal by a test) all name the asset this way; a project's
    build-local file name never decides what installers download (KI-89 / PWMCP-07).
    """
    return f"{tag}-{variant}{suffix}" if variant else f"{tag}{suffix}"


def bundle_asset_name(prefix: str, version: str, suffix: str,
                      variant: Optional[str] = None) -> str:
    """Canonical asset name for ``version`` of ``prefix``: ``<prefix>-v<version>[-<variant>]<suffix>``."""
    return asset_name_for_tag(version_to_tag(prefix, version), suffix, variant)


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
        status, body = self._request(
            "GET", self._repo_url(f"/releases/tags/{quote(tag, safe='')}"),
        )
        if status == 404:
            return None
        if status >= 400:
            self._fail(f"fetch release {tag}", status, body)
        return json.loads(body)

    def get_release(self, release_id: int) -> Optional[Dict[str, Any]]:
        """Read one release by its numeric ID (None when GitHub does not serve it)."""
        status, body = self._request("GET", self._repo_url(f"/releases/{release_id}"))
        if status == 404:
            return None
        if status >= 400:
            self._fail(f"fetch release {release_id}", status, body)
        return json.loads(body)

    def get_tag_commit(self, tag: str) -> Optional[str]:
        """Resolve an existing lightweight or annotated Git tag to its commit."""
        commit_ref = quote(tag, safe="")
        status, body = self._request("GET", self._repo_url(f"/commits/{commit_ref}"))
        if status == 404:
            return None
        if status >= 400:
            self._fail(f"fetch Git tag {tag}", status, body)
        try:
            commit = json.loads(body)
        except ValueError:
            self._fail(f"decode Git tag {tag}", status, body)
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            self._fail(f"resolve Git tag {tag}", 0, body)
        return sha

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

    def _verified_existing_release(
        self, tag: str, expected_release_id: int, expected_tag_commit: str,
        *, expected_target_commit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Recheck the exact release object and tag target before a retained mutation.

        ``expected_target_commit`` additionally refuses a release whose
        ``target_commitish`` is a full commit SHA other than that commit (a branch
        name or an absent field is not a commit claim and is not compared). Callers
        that have no such commit leave it unset and are unchanged.
        """
        release = self.get_release_by_tag(tag)
        if release is None:
            _die(f"refusing to update GitHub Release {tag}: the captured release is no longer available")
        if not isinstance(release, dict):
            _die(f"refusing to update GitHub Release {tag}: the returned record is malformed")
        release_id = release.get("id")
        if type(release_id) is not int or release_id != expected_release_id:
            _die(
                f"refusing to update GitHub Release {tag}: its release ID changed "
                f"from {expected_release_id} to {release_id!r}"
            )
        if release.get("tag_name") != tag:
            _die(f"refusing to update GitHub Release {tag}: the returned tag identity is malformed")
        if expected_target_commit is not None:
            target = release.get("target_commitish")
            if (
                isinstance(target, str) and _FULL_SHA.fullmatch(target)
                and target != expected_target_commit
            ):
                _die(
                    f"refusing to update GitHub Release {tag}: it now targets commit "
                    f"{target}, not the release commit {expected_target_commit}"
                )
        current_commit = self.get_tag_commit(tag)
        if current_commit != expected_tag_commit:
            _die(
                f"refusing to update GitHub Release {tag}: its tag target changed "
                f"from {expected_tag_commit} to {current_commit!r}"
            )
        if not isinstance(release.get("upload_url"), str) or not release["upload_url"]:
            self._fail(f"release {tag} missing upload_url", 0, json.dumps(release))
        return release

    # composite -----------------------------------------------------------------
    def publish(self, tag: str, title: str, notes: str, assets: List[Path],
                *, recreate: bool = False,
                target_commitish: Optional[str] = None,
                require_existing_release: bool = False,
                expected_release_id: Optional[int] = None,
                expected_tag_commit: Optional[str] = None) -> Dict[str, Any]:
        """Create/refresh ``tag`` and (re)upload ``assets`` (same-named ones replaced)."""
        if require_existing_release:
            if recreate:
                _die(f"refusing to recreate GitHub Release {tag}: existing refs must remain untouched")
            if type(expected_release_id) is not int or expected_release_id <= 0:
                _die(f"refusing to update GitHub Release {tag}: no valid captured release ID was supplied")
            if not isinstance(expected_tag_commit, str) or not re.fullmatch(
                r"[0-9a-f]{40}", expected_tag_commit,
            ):
                _die(f"refusing to update GitHub Release {tag}: no valid captured tag commit was supplied")
            # Recheck directly before changing release metadata. GitHub has no
            # conditional update API, so later asset mutations repeat this check.
            release = self._verified_existing_release(
                tag, expected_release_id, expected_tag_commit,
            )
            self.update_release(expected_release_id, title, notes)
            release = self._verified_existing_release(
                tag, expected_release_id, expected_tag_commit,
            )
            rid, upload_url = expected_release_id, release["upload_url"]
            for asset in assets:
                # Re-fetch assets for each name, then recheck target identity
                # immediately before deleting or uploading.
                current_assets = {a.get("name"): a for a in self.list_assets(rid)}
                old = current_assets.get(asset.name)
                if old and type(old.get("id")) is int:
                    self._verified_existing_release(
                        tag, expected_release_id, expected_tag_commit,
                    )
                    self.delete_asset(old["id"])
                checked = self._verified_existing_release(
                    tag, expected_release_id, expected_tag_commit,
                )
                self.upload_asset(str(checked["upload_url"]), asset, asset.name)
            return release

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

    def publish_release_body(
        self, tag: str, title: str, notes: str, *, tag_commit: str,
    ) -> Dict[str, Any]:
        """Create or refresh ONLY the title and body of ``tag``'s GitHub Release.

        For a release with no CMRU-uploaded asset (an OCI image project: the
        image was pushed to its registry by the project's own step). ``tag``
        must already exist on the remote at exactly ``tag_commit``: CMRU pushes
        the release tag before its push step, and this method never creates or
        moves a Git ref itself. Every PRE-write refusal happens before any
        release mutation (post-write findings are described below); the only
        writes are one create (a missing release, bound to
        ``tag`` and ``tag_commit``) or one metadata PATCH (name and body). No
        asset is listed, deleted or uploaded, so a retry can never disturb an
        existing asset, whatever its name.

        What is and is not guaranteed. An EXISTING release is re-fetched and its
        exact ID, tag identity, commit target (when it is a full SHA) and remote
        tag target are re-verified immediately before the PATCH, so a change
        detected there refuses with no mutation. GitHub offers no conditional
        update and no way to make the tag check atomic with a release CREATE, so
        the protection is the read-back after EVERY write.

        Once a POST or PATCH has been SENT, nothing about it is assumed. An HTTP
        error, a transport failure, a malformed success body or a malformed record
        all mean the write MAY have been applied, so none of them is reported as a
        clean refusal and none invites a blind retry; CMRU never resends the POST
        or PATCH. Instead the release is read back: by its returned ID, or by the
        captured release ID for an update, or by tag when a create returned no
        usable ID. A create's read by ID is attempted a finite number of times
        (:data:`READBACK_ATTEMPTS`), because GitHub may not serve a just-created
        release at once; "not yet visible" is a different finding from "the tag
        now belongs to another release". The record must carry the exact ID (when
        one is known), tag and body, a full-SHA ``target_commitish`` may only be
        ``tag_commit`` (a branch name is not a commit claim), and the remote tag must
        still resolve to ``tag_commit``. If all of that holds the publication is
        reported, even when the write's own response was unusable; otherwise the
        error says the outcome is unknown or that the release WAS WRITTEN, that
        nothing was rolled back, and that the release and the tag must be inspected
        before any retry. A run is never reported as successful on the strength of
        the write's own response alone.
        """
        if not isinstance(tag_commit, str) or not _FULL_SHA.fullmatch(tag_commit):
            _die(
                f"refusing to publish GitHub Release {tag}: no valid 40-hex "
                "release commit was supplied"
            )
        remote_commit = self.get_tag_commit(tag)
        if remote_commit is None:
            _die(
                f"refusing to publish GitHub Release {tag}: the remote has no such tag. "
                "`cmru release` pushes the release tag before its push step runs; "
                "this publisher never creates a tag itself"
            )
        if remote_commit != tag_commit:
            _die(
                f"refusing to publish GitHub Release {tag}: the remote tag points at "
                f"{remote_commit}, not the release commit {tag_commit}"
            )
        existing = self.get_release_by_tag(tag)
        if existing is None:
            record, cause = self._send_write(
                "POST", "/releases",
                {"tag_name": tag, "name": title, "body": notes, "target_commitish": tag_commit},
            )
            response_id = None
            if cause is None:
                response_id = self._usable_response_id(tag, record)
                if response_id is None:
                    cause = "the success response is not a valid release record for this tag"
            return self._confirm_write(
                tag, tag_commit, notes, release_id=response_id, cause=cause,
                attempts=READBACK_ATTEMPTS,
            )
        release_id = self._release_record_id(tag, existing)
        target = existing.get("target_commitish")
        if isinstance(target, str) and _FULL_SHA.fullmatch(target) and target != tag_commit:
            _die(
                f"refusing to update GitHub Release {tag}: it targets commit "
                f"{target}, not the release commit {tag_commit}"
            )
        # Re-fetch and verify the exact captured release ID, tag identity,
        # commit target and remote tag target IMMEDIATELY before the only
        # mutation. A change found here refuses with no PATCH sent.
        self._verified_existing_release(
            tag, release_id, tag_commit, expected_target_commit=tag_commit,
        )
        record, cause = self._send_write(
            "PATCH", f"/releases/{release_id}", {"name": title, "body": notes},
        )
        if cause is None and self._usable_response_id(tag, record, expected=release_id) is None:
            cause = "the success response is not the captured release's record"
        return self._confirm_write(
            tag, tag_commit, notes, release_id=release_id, cause=cause, attempts=1,
        )

    def _send_write(
        self, method: str, suffix: str, payload: Dict[str, Any],
    ) -> tuple[Any, Optional[str]]:
        """Send ONE mutation and classify what came back; never raise, never resend.

        Returns ``(decoded response, None)`` for a decodable success status, or
        ``(None, cause)`` for anything else. Every ``cause`` means the request WAS
        sent, so the write may have been applied: the caller must read back before
        saying anything about the outcome.
        """
        data = json.dumps(payload).encode()
        try:
            status, body = self._request(
                method, self._repo_url(suffix), data=data, content_type="application/json",
            )
        except (OSError, http.client.HTTPException, ValueError) as exc:
            return None, f"the request failed in transit ({exc!r})"
        if status >= 400:
            excerpt = body.strip().replace("\n", " ")[:200]
            return None, f"GitHub answered HTTP {status}" + (f" ({excerpt})" if excerpt else "")
        try:
            return json.loads(body), None
        except ValueError:
            return None, "the success response was not valid JSON"

    @staticmethod
    def _usable_response_id(tag: str, record: Any, *, expected: Optional[int] = None) -> Optional[int]:
        """The positive integer ID of a write's response record, or None if unusable."""
        if not isinstance(record, dict):
            return None
        release_id = record.get("id")
        if type(release_id) is not int or release_id <= 0:
            return None
        if record.get("tag_name") != tag:
            return None
        if expected is not None and release_id != expected:
            return None
        return release_id

    def _confirm_write(
        self, tag: str, tag_commit: str, notes: str, *, release_id: Optional[int],
        cause: Optional[str], attempts: int,
    ) -> Dict[str, Any]:
        """Read the release and tag back AFTER a sent write; return the record, or report.

        ``release_id`` is the ID to read by (a create's usable response ID, or an
        update's captured ID); None means read by tag. ``cause`` is why the write's own
        response cannot be trusted (None when it was a usable success). The write is
        never repeated, whatever the read-back finds.
        """
        problems: list[str] = []
        after: Any = None
        read_failure: Optional[str] = None
        try:
            for attempt in range(attempts):
                after = (
                    self.get_release(release_id) if release_id is not None
                    else self.get_release_by_tag(tag)
                )
                if after is not None:
                    break
                if attempt + 1 < attempts:
                    _pause(_READBACK_PAUSES[min(attempt, len(_READBACK_PAUSES) - 1)])
            if after is None:
                # Not served: say whether the TAG now belongs to another release (a
                # replacement) or simply nothing is visible (yet). Never conflate them.
                other = self.get_release_by_tag(tag) if release_id is not None else None
                other_id = other.get("id") if isinstance(other, dict) else None
                if release_id is not None and type(other_id) is int and other_id != release_id:
                    problems.append(f"its release ID is now {other_id!r}, not {release_id}")
                elif release_id is not None:
                    problems.append(
                        f"release {release_id} was not visible after {attempts} read(s) "
                        "(not yet visible, or removed)"
                    )
                else:
                    problems.append(f"no release for the tag was visible after {attempts} read(s)")
            elif not isinstance(after, dict):
                problems.append("the release read-back is missing or malformed")
            else:
                found_id = after.get("id")
                if release_id is not None:
                    if type(found_id) is not int or found_id != release_id:
                        problems.append(f"its release ID is now {found_id!r}, not {release_id}")
                elif type(found_id) is not int or found_id <= 0:
                    problems.append(f"its release ID is malformed ({found_id!r})")
                if after.get("tag_name") != tag:
                    problems.append(f"it now reports tag {after.get('tag_name')!r}")
                if after.get("body") != notes:
                    problems.append("its body is not the intended body (it was changed or replaced)")
                target = after.get("target_commitish")
                if isinstance(target, str) and _FULL_SHA.fullmatch(target) and target != tag_commit:
                    problems.append(
                        f"it now targets commit {target}, not the release commit {tag_commit}"
                    )
            final_commit = self.get_tag_commit(tag)
            if final_commit != tag_commit:
                problems.append(
                    f"the remote tag now points at {final_commit!r}, "
                    f"not the release commit {tag_commit}"
                )
        except (SystemExit, OSError, http.client.HTTPException, ValueError) as exc:
            # SystemExit: a GitHub error already printed by ``_fail``. OSError/ValueError:
            # a transport failure or an undecodable response that nothing else catches.
            read_failure = "" if isinstance(exc, SystemExit) else f" ({exc!r})"
        if cause is None:
            if read_failure is not None:
                _die(
                    f"GitHub Release {tag} WAS WRITTEN, but reading it back to verify it failed"
                    f"{read_failure}. The write was NOT rolled back and its outcome is unverified: "
                    "inspect the release and the tag before any retry"
                )
            if problems:
                _die(
                    f"GitHub Release {tag} was written, but {'; '.join(problems)}. The write was "
                    "NOT rolled back and the release may not carry the intended body: inspect "
                    "the release and the tag before any retry"
                )
            return after
        # The write was sent but its answer cannot be trusted: only the read-back can say.
        if read_failure is None and not problems:
            print(
                f"[WARN] the write to GitHub Release {tag} got no usable answer ({cause}), but "
                "reading the release back verified its exact ID, tag, body and target and the "
                "remote tag; it was NOT resent",
                file=sys.stderr,
            )
            return after
        finding = (
            f"reading it back failed{read_failure}" if read_failure is not None
            else f"the read-back found that {'; '.join(problems)}"
        )
        _die(
            f"the write to GitHub Release {tag} has an UNKNOWN outcome: {cause}. The release "
            f"MAY OR MAY NOT have been written and nothing was rolled back; {finding}. CMRU "
            "never resends a create or an update: inspect the release and the tag before any retry"
        )

    def _release_record_id(self, tag: str, release: Any) -> int:
        """The positive integer ID of an EXISTING release record that carries ``tag`` exactly."""
        lead = f"refusing to publish GitHub Release {tag}:"
        if not isinstance(release, dict):
            _die(f"{lead} the returned record is malformed")
        release_id = release.get("id")
        if type(release_id) is not int or release_id <= 0:
            _die(f"{lead} the returned release ID is malformed")
        if release.get("tag_name") != tag:
            _die(f"{lead} the returned record is for tag {release.get('tag_name')!r}")
        return release_id

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


# ─── release body (KI-75) ────────────────────────────────────────────────────
def _resolve_pointer_text(prefix: str) -> str:
    return (
        f"Resolve latest programmatically by scanning `{prefix}-v*` releases "
        f"(highest semver); see cmru docs/SPEC.md S5."
    )


def build_release_body(
    prefix: str, version: str, *, artifact_name: str, sha256: str,
    notes: Optional[str] = None, release_notes: Optional[ReleaseNotes] = None,
) -> str:
    """The GitHub Release body of a single-artifact release.

    The publisher and ``cmru release --dry-run`` both call this function, so a
    preview shows exactly what is published. ``notes`` is the operator's
    supplementary opening text (``--notes-env``); ``release_notes`` is the
    project's curated section for ``version`` and always comes last.
    """
    generated = (
        f"\n\n**Artifact:** `{artifact_name}`\n"
        f"**SHA256:** `{sha256}`\n\n"
        f"Verify: `sha256sum -c {artifact_name}.sha256`\n\n"
        f"{_resolve_pointer_text(prefix)}"
    )
    return compose_release_body(notes or f"{prefix} {version}", generated, release_notes)


# A registry host (optionally with a port) or one repository path component,
# per the OCI distribution name grammar (lower-case path components).
_IMAGE_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?")
_IMAGE_PATH_PART = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_IMAGE_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


def validate_oci_image_repository(image: str) -> str:
    """``image`` unchanged if it is a valid ``<registry>/<repository>`` reference.

    The reference carries NO tag and NO digest (a pushed image gets its tag from
    the release version, never from configuration). Raises ``ValueError`` naming
    the problem. The publisher, the release-plan validator and the dry-run
    preview all call this, so none can accept a reference another refuses.

    Accepted syntax: the first ``/``-separated component is an EXPLICIT registry,
    by the same rule Docker applies when it reads a reference: it contains a
    ``.`` (``ghcr.io``, ``registry.example.com``), or a ``:port``
    (``myhost:5000``, ``localhost:5000``), or is exactly ``localhost``. Anything
    else (``registry/team``, ``myorg/app``) would be read by Docker as a Docker
    Hub namespace, so the generated ``docker pull`` would target a different
    registry than the one written: it is refused, and Docker Hub must be spelled
    ``docker.io/<namespace>/<name>``. What follows is one or more lower-case
    repository path components (``a-z0-9`` separated by ``.``, ``_``, ``__`` or
    ``-``). IPv6 bracket hosts are not accepted.
    """
    if not isinstance(image, str) or not image or image != image.strip():
        raise ValueError("--image must be a non-empty repository reference")
    if "@" in image:
        raise ValueError(
            f"--image {image!r} carries a digest; pass the repository only "
            "(<registry>/<repository>)"
        )
    parts = image.split("/")
    if len(parts) < 2 or not all(parts):
        raise ValueError(
            f"--image {image!r} must be <registry>/<repository> with no empty path component"
        )
    if ":" in parts[-1]:
        raise ValueError(
            f"--image {image!r} carries a tag; pass the repository only, the tag is "
            "derived from the release version"
        )
    if not _IMAGE_HOST.fullmatch(parts[0]) or not all(
        _IMAGE_PATH_PART.fullmatch(part) for part in parts[1:]
    ):
        raise ValueError(f"--image {image!r} is not a valid <registry>/<repository> reference")
    registry_name, _colon, _port = parts[0].partition(":")
    if "." not in registry_name and not _port and registry_name != "localhost":
        raise ValueError(
            f"--image {image!r}: its first component {parts[0]!r} has no '.' or ':port' and "
            "is not 'localhost', so Docker would read it as a Docker Hub namespace, not a "
            "registry, and `docker pull` would fetch a different image than the one "
            f"released. Write the registry explicitly (for example ghcr.io/{image}, or "
            f"docker.io/{image} for Docker Hub)"
        )
    return image


def oci_image_reference(image: str, version: str) -> str:
    """The exact ``<registry>/<repository>:<version>`` coordinate of a release.

    Raises ``ValueError`` naming the problem; both the publisher and the dry-run
    preview call this through :func:`build_oci_image_release_body`, so neither
    can accept a reference the other refuses.
    """
    validate_oci_image_repository(image)
    if not _IMAGE_TAG.fullmatch(version):
        raise ValueError(f"release version {version!r} is not a valid image tag")
    return f"{image}:{version}"


def build_oci_image_release_body(
    image: str, version: str, *, release_notes: Optional[ReleaseNotes] = None,
) -> str:
    """The GitHub Release body of an image-only release (KI-78).

    There is no uploaded artifact, so there is no artifact name or SHA256: the
    body opens with the exact versioned image coordinate and its pull command,
    then ends with the project's curated section for ``version`` (the tagged
    changelog section; see :func:`cmru.release_notes.compose_release_body`).
    ``oci-image-release`` and ``cmru release --dry-run`` both call this
    function, so a preview shows exactly what is published.
    """
    reference = oci_image_reference(image, version)
    return compose_release_body(
        f"**Image:** `{reference}`", f"\n\nPull: `docker pull {reference}`", release_notes,
    )


def build_variants_release_body(
    prefix: str, version: str, *, records: Sequence[Dict[str, Any]],
    notes: Optional[str] = None, release_notes: Optional[ReleaseNotes] = None,
) -> str:
    """The GitHub Release body of a multi-variant release (same contract as above)."""
    variant_lines = "\n".join(f"- `{r['asset']}` — `{r['sha256']}`" for r in records)
    generated = (
        f"\n\n**Variants** ({len(records)}), one asset each under this tag "
        f"(select at install time with `--variant <name>`):\n{variant_lines}\n\n"
        f"Verify: `sha256sum -c <asset>.sha256`\n\n"
        f"{_resolve_pointer_text(prefix)}"
    )
    return compose_release_body(notes or f"{prefix} {version}", generated, release_notes)


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
    require_existing_targets: bool = False,
    latest_pointer_recreate: bool = True,
    expected_tag_commit: Optional[str] = None,
    release_notes: Optional[ReleaseNotes] = None,
) -> Dict[str, Any]:
    """Publish ``asset_path`` for ``version`` under the uniform scheme.

    The primary asset keeps its file name (a wheel must never be renamed). For an installer
    bundle that name must be :func:`bundle_asset_name`; the producer owns that (KI-89:
    ``tests/test_installer_asset_names.py`` checks every installer project's producer).

    ``release_notes`` is the project's curated section for ``version`` (KI-75);
    it follows the generated install/artifact block in the release body.

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
    if require_existing_targets and (
        target_commitish is not None or (latest_pointer and latest_pointer_recreate)
    ):
        _die(
            "require_existing_targets cannot create or recreate Git refs; "
            "omit target_commitish and disable latest_pointer_recreate"
        )

    digest = sha256_file(asset_path)
    sidecar = write_sha256_sidecar(asset_path)
    extras = list(extra_assets or [])
    result: Dict[str, Any] = {"version": version, "sha256": digest,
                              "release_tag": None, "asset_url": None}

    released = is_release_version(version)
    release_tag = version_to_tag(prefix, version) if released else None
    latest_tag = f"{prefix}-latest"
    update_latest_pointer = latest_pointer
    if require_existing_targets and released and latest_pointer:
        newer_version = _newer_release_version(gh, prefix, version)
        if newer_version is not None:
            update_latest_pointer = False
            print(
                f"[INFO] Keeping {latest_tag} on newer {prefix}-v{newer_version}; "
                f"retained build {release_tag} is older"
            )
    if require_existing_targets:
        required_tags = ([release_tag] if release_tag else [])
        if latest_pointer:
            required_tags.append(latest_tag)
        resolved: dict[str, Optional[str]] = {}
        resolved_release_ids: dict[str, int] = {}
        for tag in required_tags:
            existing_release = gh.get_release_by_tag(tag)
            if existing_release is None:
                _die(
                    f"build-output publication cannot verify the existing GitHub Release {tag}; "
                    "confirm it exists and is accessible"
                )
            if not isinstance(existing_release, dict):
                _die(
                    f"build-output publication received a malformed GitHub Release "
                    f"record for {tag}"
                )
            release_id = existing_release.get("id")
            if type(release_id) is not int or release_id <= 0:
                _die(
                    f"build-output publication cannot verify the existing GitHub Release ID "
                    f"for {tag}"
                )
            if existing_release.get("tag_name") != tag:
                _die(
                    f"build-output publication received a malformed GitHub Release identity "
                    f"for {tag}"
                )
            resolved_release_ids[tag] = release_id
            resolved[tag] = gh.get_tag_commit(tag)
            if resolved[tag] is None:
                _die(
                    f"build-output publication cannot verify the existing Git tag {tag}; "
                    "confirm it exists and is accessible"
                )
        if release_tag and (
            not expected_tag_commit or resolved[release_tag] != expected_tag_commit
        ):
            _die(
                f"build-output source commit {expected_tag_commit!r} "
                f"does not match existing release tag {release_tag} "
                f"({resolved[release_tag]})"
            )

    def publish_assets(
        tag: str, title: str, body: str, assets: List[Path], *, recreate: bool = False,
    ) -> Dict[str, Any]:
        options: dict[str, Any] = {
            "recreate": recreate,
            "target_commitish": target_commitish,
        }
        if require_existing_targets:
            options["require_existing_release"] = True
            options["expected_release_id"] = resolved_release_ids[tag]
            options["expected_tag_commit"] = resolved[tag]
        return gh.publish(tag, title, body, assets, **options)

    if released:
        assert release_tag is not None
        body = build_release_body(
            prefix, version, artifact_name=asset_path.name, sha256=digest,
            notes=notes, release_notes=release_notes,
        )
        publish_assets(release_tag, release_tag, body, [asset_path, sidecar, *extras])
        result["release_tag"] = release_tag
        result["asset_url"] = gh.asset_download_url(release_tag, asset_path.name)
        print(f"[INFO] Published immutable release {release_tag} (+ .sha256)")
    else:
        print(f"[INFO] Dev build {version} — no immutable version release")

    if latest_pointer:
        if released:
            if update_latest_pointer and require_existing_targets:
                newer_version = _newer_release_version(gh, prefix, version)
                if newer_version is not None:
                    update_latest_pointer = False
                    print(
                        f"[INFO] Keeping {latest_tag} on newer {prefix}-v{newer_version}; "
                        f"retained build {release_tag} is older"
                    )
            if update_latest_pointer:
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
                publish_assets(
                    latest_tag, latest_tag,
                    f"{prefix} latest → {version} (thin pointer; see latest.json)",
                    [manifest], recreate=latest_pointer_recreate,
                )
                print(f"[INFO] Refreshed thin pointer {latest_tag} → {result['release_tag']}")
        else:
            publish_assets(
                latest_tag, latest_tag, f"{prefix} latest (dev → {version})",
                [asset_path, sidecar], recreate=latest_pointer_recreate,
            )
            print(f"[INFO] Moved {latest_tag} (dev asset → {version})")

    return result


def _newer_release_version(gh: GitHubReleases, prefix: str, version: str) -> str | None:
    """Return the highest current version when it is newer than a retained release."""
    latest = gh.resolve_latest(prefix)
    if latest is None:
        return None
    latest_version = latest.get("version")
    if not isinstance(latest_version, str) or not is_release_version(latest_version):
        _die(f"cannot safely compare latest release version for {prefix!r}: malformed result")
    if _semver_key(latest_version) > _semver_key(version):
        return latest_version
    return None


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
    return bundle_asset_name(prefix, version, suffix, variant=variant)


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
    release_notes: Optional[ReleaseNotes] = None,
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
        body = build_variants_release_body(
            prefix, version, records=records, notes=notes, release_notes=release_notes,
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


# ─── reusable publication glue ────────────────────────────────────────────────
# These are the per-artifact helpers every wheel project re-implemented (find the
# built wheel, read its version, assert the published "latest" is well-formed). They
# now live here once so a project can route through cmru.handlers instead of
# carrying its own copy.
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

    Generic over artifact type (wheel ``.whl``, tarball ``.tar.xz``, …) so explicit
    project commands can reuse discovery logic rather than carrying a copy.

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
        meta = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if meta is None:
            _die(f"No Version field in {Path(wheel_path).name} METADATA")
        for line in zf.read(meta).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    _die(f"No Version field in {Path(wheel_path).name} METADATA")


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
    (wheel ``.whl``, tarball ``.tar.xz``, …) so explicit project commands can reuse it.
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
        _die(f"Release {prefix}-v{info['version']} has multiple {artifact_suffix} assets: "
             f"{matches}; refusing ambiguous artifact selection")
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
