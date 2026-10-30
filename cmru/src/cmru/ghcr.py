"""GHCR package visibility helpers.

Container packages in GitHub Container Registry default to private when they are
created. cmru's OCI publish scripts call these helpers after ``docker buildx bake
--push`` so the package visibility mirrors the source repository visibility.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"


class PackageVisibilityApiUnsupported(RuntimeError):
    """Raised when GitHub returns no usable endpoint for setting package visibility.

    GitHub exposes NO REST or GraphQL API to change a container package's visibility
    (the PATCH route does not exist -> HTTP 404). Visibility must be set once in the
    web UI; thereafter it persists across pushes. Callers treat this as non-fatal —
    the image has already been pushed successfully by the time we get here.
    """

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"package visibility API unsupported (HTTP {status})")
        self.status = status
        self.body = body


class PackageLookupUnauthorized(RuntimeError):
    """Raised when the token may not read the package (HTTP 401/403).

    A brand-new GHCR package is created private, and a token without
    ``read:packages`` cannot look it up. Like :class:`PackageVisibilityApiUnsupported`
    this arrives AFTER the image push succeeded, so callers degrade it to a warning
    with the one-time remediation (KI-97).
    """

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"package lookup not authorised (HTTP {status})")
        self.status = status
        self.body = body


UI_VISIBILITY_HINT = (
    "Set it ONCE in the UI (Your packages -> {package} -> Package settings -> "
    "Danger Zone -> Change visibility -> {visibility}) and, for a first push, link the "
    "package to its repository there; both then persist for all future pushes."
)
PACKAGES_SCOPE_HINT = (
    "A `gh` login needs `gh auth refresh -h github.com -s read:packages,write:packages`; "
    "a PAT needs the read:packages scope."
)


class GitHubPackages:
    """Minimal GitHub Packages client for GHCR visibility sync."""

    def __init__(self, owner: str, repo: str, token: str, owner_type: str,
                 api_base: str = API_BASE) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
        self.owner_type = owner_type
        self.api_base = api_base

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, str]:
        headers = {
            "Accept": "application/vnd.github+json",
        }
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

    def _fail(self, action: str, status: int, body: str) -> None:
        print(f"[ERROR] {action}\n[ERROR] HTTP {status}: {body}", file=sys.stderr)
        raise SystemExit(1)

    def _repo_url(self) -> str:
        return f"{self.api_base}/repos/{self.owner}/{self.repo}"

    def _package_url(self, package_name: str) -> str:
        if self.owner_type == "org":
            base = f"{self.api_base}/orgs/{self.owner}"
        elif self.owner_type == "user":
            base = f"{self.api_base}/users/{self.owner}"
        else:
            self._fail(
                f"unsupported owner_type for GHCR visibility sync: {self.owner_type!r}",
                0,
                "",
            )
        return f"{base}/packages/container/{package_name}"

    def repo_visibility(self) -> str:
        """Return the source repository visibility (public/private/internal)."""
        status, body = self._request("GET", self._repo_url())
        if status >= 400:
            self._fail(f"fetch repository visibility for {self.owner}/{self.repo}", status, body)
        payload = json.loads(body or "{}")
        visibility = str(payload.get("visibility") or "").strip()
        if visibility:
            return visibility
        if "private" in payload:
            return "private" if payload.get("private") else "public"
        self._fail(
            f"repository response missing visibility for {self.owner}/{self.repo}",
            0,
            body,
        )

    def package_visibility(self, package_name: str) -> Optional[str]:
        """Return the current GHCR package visibility, or None if the package is not visible yet."""
        status, body = self._request("GET", self._package_url(package_name))
        if status == 404:
            return None
        if status in (401, 403):
            raise PackageLookupUnauthorized(status, body)
        if status >= 400:
            self._fail(
                f"fetch GHCR package visibility for {package_name}",
                status,
                body,
            )
        payload = json.loads(body or "{}")
        visibility = str(payload.get("visibility") or "").strip()
        if visibility:
            return visibility
        if "private" in payload:
            return "private" if payload.get("private") else "public"
        return None

    def set_package_visibility(self, package_name: str, visibility: str) -> Dict[str, Any]:
        """Set GHCR package visibility to *visibility* and return the response payload."""
        payload = json.dumps({"visibility": visibility}).encode("utf-8")
        status, body = self._request(
            "PATCH",
            self._package_url(package_name),
            data=payload,
            content_type="application/json",
        )
        if status >= 400:
            # GitHub exposes NO REST/GraphQL endpoint to change container-package
            # visibility (the PATCH route does not exist -> 404). This is expected and
            # MUST NOT be fatal: the image already pushed. Surface it as a typed,
            # catchable signal so callers can warn + continue.
            raise PackageVisibilityApiUnsupported(status, body)
        return json.loads(body or "{}")

    def mirror_package_visibility(
        self,
        package_name: str,
        *,
        expected_visibility: Optional[str] = None,
        retries: int = 6,
        delay: int = 5,
        missing: str = "warn",
    ) -> Optional[str]:
        """Make a package match the repository visibility.

        Returns the visibility that should now be in effect, or ``None`` when the
        package could not be inspected. The package lookup is retried briefly
        because the container registry can lag the push by a few seconds.

        KI-97: this runs AFTER the image push succeeded, mid-release. A package
        that still cannot be found after the retries (a brand-new package lagging
        the API, or a token that may not read it) is therefore NOT a release
        failure by default (``missing="warn"``): the one-time UI remediation is
        printed and ``None`` is returned. ``missing="fail"`` keeps the old hard
        failure for a project that requires the package to pre-exist.
        """
        if missing not in ("warn", "fail"):
            raise ValueError(f"missing must be 'warn' or 'fail', not {missing!r}")
        if expected_visibility is None:
            expected_visibility = self.repo_visibility()

        current: Optional[str] = None
        unauthorized: Optional[PackageLookupUnauthorized] = None
        for attempt in range(retries):
            try:
                current = self.package_visibility(package_name)
            except PackageLookupUnauthorized as exc:
                unauthorized = exc
                break
            if current is not None:
                break
            if attempt < retries - 1:
                time.sleep(delay)
        if current is None:
            if unauthorized is not None:
                cause = (
                    f"the token may not read it (HTTP {unauthorized.status}). "
                    + PACKAGES_SCOPE_HINT
                )
            else:
                cause = (
                    f"it was not found after {retries} lookup(s) over about "
                    f"{max(retries - 1, 0) * delay} s (a brand-new package can lag the "
                    "packages API, or be private to this token)"
                )
            if missing == "fail":
                self._fail(
                    f"GHCR package {package_name} cannot be inspected; cannot sync visibility: "
                    f"{cause}",
                    unauthorized.status if unauthorized is not None else 0,
                    unauthorized.body if unauthorized is not None else "",
                )
            sys.stderr.write(
                f"[WARN] Could not sync GHCR visibility for {package_name}: {cause}. "
                "The image push already succeeded, so the release continues; the package "
                f"may still be private. "
                + UI_VISIBILITY_HINT.format(package=package_name, visibility=expected_visibility)
                + "\n"
            )
            return None

        if current == expected_visibility:
            return expected_visibility

        try:
            updated = self.set_package_visibility(package_name, expected_visibility)
        except PackageVisibilityApiUnsupported as exc:
            sys.stderr.write(
                f"[WARN] Could not set GHCR visibility for {package_name} via API "
                f"(HTTP {exc.status}): GitHub provides no REST/GraphQL endpoint to change "
                f"container-package visibility. The image pushed fine but the package "
                f"stays {current!r}. "
                + UI_VISIBILITY_HINT.format(package=package_name, visibility=expected_visibility)
                + "\n"
            )
            return current or expected_visibility
        new_visibility = str(updated.get("visibility") or "").strip()
        if new_visibility and new_visibility != expected_visibility:
            self._fail(
                f"GHCR package {package_name} visibility sync returned {new_visibility!r} "
                f"instead of {expected_visibility!r}",
                0,
                json.dumps(updated),
            )
        return expected_visibility
