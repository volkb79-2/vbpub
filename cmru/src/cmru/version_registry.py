"""Registry metadata readers used by ``cmru versions``.

All providers distinguish an unavailable registry from an empty release list.
Timestamps are carried alongside versions so callers cannot accidentally turn
missing evidence into "nothing newer exists".
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping


USER_AGENT = "cmru-versions/1"
REQUEST_TIMEOUT_SECONDS = 25
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_REGISTRY_ITEMS = 5000


class RegistryError(RuntimeError):
    """A registry request or its age evidence could not be trusted."""


class RegistryNotFoundError(RegistryError):
    """A registry explicitly returned HTTP 404 for a requested resource."""


@dataclass(frozen=True)
class Candidate:
    version: str
    released_at: datetime
    age_source: str
    tag: str | None = None


def _iso_datetime(value: object, where: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{where} has no release timestamp")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RegistryError(f"{where} has an invalid release timestamp {raw!r}") from exc
    if parsed.tzinfo is None:
        raise RegistryError(f"{where} has a timestamp without a timezone")
    return parsed.astimezone(timezone.utc)


def _auth_headers(source: Mapping[str, str]) -> dict[str, str]:
    token_env = source.get("token_env")
    username_env = source.get("username_env")
    password_env = source.get("password_env")
    if token_env:
        token = os.environ.get(token_env, "")
        if not token:
            raise RegistryError(f"required registry token environment variable {token_env} is unset or empty")
        return {"Authorization": f"Bearer {token}"}
    if username_env:
        username = os.environ.get(username_env, "")
        password = os.environ.get(password_env or "", "")
        if not username or not password:
            missing = [name for name, value in ((username_env, username), (password_env or "", password)) if not value]
            raise RegistryError("required registry credential environment variable(s) are unset or empty: " + ", ".join(missing))
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {}


class _RegistryRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow secure registry redirects without forwarding credentials cross-origin."""

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int]:
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError as exc:
            raise RegistryError(f"refusing registry redirect with an invalid URL: {url}") from exc
        if "#" in url:
            raise RegistryError(f"refusing registry URL with a fragment: {url}")
        if parts.scheme.lower() != "https" or not parts.hostname:
            raise RegistryError(f"refusing unsafe non-HTTPS registry redirect to {url}")
        if parts.username is not None or parts.password is not None:
            raise RegistryError(f"refusing registry redirect with embedded credentials: {url}")
        try:
            port = parts.port
        except ValueError as exc:
            raise RegistryError(f"refusing registry redirect with an invalid port: {url}") from exc
        if parts.netloc.endswith(":"):
            raise RegistryError(f"refusing registry redirect with an invalid port: {url}")
        if port is None:
            port = 443
        elif not 1 <= port <= 65535:
            raise RegistryError(f"refusing registry redirect with an invalid port: {url}")
        return parts.scheme.lower(), parts.hostname.lower(), port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source_origin = self._origin(req.full_url)
        destination_origin = self._origin(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and source_origin != destination_origin:
            for request_headers in (redirected.headers, redirected.unredirected_hdrs):
                for name in tuple(request_headers):
                    if name.casefold() == "authorization":
                        del request_headers[name]
        return redirected


def _urlopen(request: urllib.request.Request, *, timeout: int):
    """Open a registry request with the CMRU redirect credential policy."""
    opener = urllib.request.build_opener(_RegistryRedirectHandler())
    return opener.open(request, timeout=timeout)


def _request(url: str, headers: Mapping[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with _urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_METADATA_BYTES + 1)
            if len(body) > MAX_METADATA_BYTES:
                raise RegistryError(f"registry response exceeds {MAX_METADATA_BYTES} bytes: {url}")
            return body, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_METADATA_BYTES + 1) if exc.fp else b""
        excerpt = body[:500].decode("utf-8", errors="replace").replace("\n", " ")
        detail = f": {excerpt}" if excerpt else ""
        error_type = RegistryNotFoundError if exc.code == 404 else RegistryError
        raise error_type(f"registry returned HTTP {exc.code} for {url}{detail}") from exc
    except urllib.error.URLError as exc:
        raise RegistryError(f"could not reach registry {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RegistryError(f"registry request timed out: {url}") from exc
    except OSError as exc:
        raise RegistryError(f"registry request failed for {url}: {exc}") from exc


def _json(url: str, headers: Mapping[str, str] | None = None) -> tuple[object, dict[str, str]]:
    body, response_headers = _request(url, headers)
    try:
        return json.loads(body), response_headers
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"registry returned invalid JSON for {url}") from exc


_SEMVER = re.compile(
    r"^v?(?P<major>0|[1-9][0-9]*)(?:\.(?P<minor>0|[1-9][0-9]*))?"
    r"(?:\.(?P<patch>0|[1-9][0-9]*))?(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def _semver_key(value: str) -> tuple | None:
    """Return a SemVer-compatible ordering key for common ecosystem versions."""
    match = _SEMVER.fullmatch(value.strip())
    if not match:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    patch = int(match.group("patch") or 0)
    prerelease = match.group("pre")
    if prerelease is None:
        pre_key: tuple = ()
        stable = 1
    else:
        stable = 0
        fields = prerelease.split(".")
        pre_key = tuple(
            (0, int(field)) if field.isdigit() else (1, field.lower())
            for field in fields
        )
    return major, minor, patch, stable, pre_key


_COMPARATOR = re.compile(r"^(~=|>=|<=|==|!=|>|<|\^|~)?\s*(.+)$")


def _constraint_parts(constraint: str) -> list[tuple[str, tuple]]:
    """Parse the shared SemVer subset used for cross-registry alignment."""
    if constraint.strip() in {"*", "x", "X"}:
        return []
    parts: list[tuple[str, tuple]] = []
    for text in constraint.split(","):
        match = _COMPARATOR.fullmatch(text.strip())
        if not match:
            raise RegistryError(
                f"unsupported version constraint {constraint!r}; use comma-separated "
                "SemVer comparisons such as '>=1.2.0,<2.0.0'"
            )
        operator = match.group(1) or "=="
        raw_version = match.group(2)
        key = _semver_key(raw_version)
        if key is None:
            raise RegistryError(f"unsupported version constraint {constraint!r}; expected SemVer versions")
        if operator in {"^", "~", "~="}:
            major, minor, patch, _stable, _pre = key
            parts.append((">=", key))
            if operator == "^":
                if major > 0:
                    upper = (major + 1, 0, 0, 1, ())
                elif minor > 0:
                    upper = (0, minor + 1, 0, 1, ())
                else:
                    upper = (0, 0, patch + 1, 1, ())
            elif operator == "~":
                precision = len(raw_version.lstrip("v").split("+", 1)[0].split("-", 1)[0].split("."))
                upper = (major + 1, 0, 0, 1, ()) if precision == 1 else (major, minor + 1, 0, 1, ())
            else:
                # PEP 440 compatible-release syntax: ~=1.4 means <2.0,
                # while ~=1.4.5 means <1.5. This keeps common Python
                # requirements useful without importing a runtime parser.
                precision = len(raw_version.lstrip("v").split("+", 1)[0].split("-", 1)[0].split("."))
                upper = (major + 1, 0, 0, 1, ()) if precision <= 2 else (major, minor + 1, 0, 1, ())
            parts.append(("<", upper))
        else:
            parts.append((operator, key))
    return parts


def validate_constraint(constraint: str) -> None:
    """Raise when a constraint is outside the documented common SemVer subset."""
    if not isinstance(constraint, str) or not constraint.strip():
        raise RegistryError("version constraint must be a non-empty string")
    _constraint_parts(constraint)


def version_satisfies(version: str, constraint: str) -> bool:
    key = _semver_key(version)
    if key is None:
        return False
    for operator, bound in _constraint_parts(constraint):
        if operator == ">=" and not key >= bound:
            return False
        if operator == ">" and not key > bound:
            return False
        if operator == "<=" and not key <= bound:
            return False
        if operator == "<" and not key < bound:
            return False
        if operator == "==" and not key == bound:
            return False
        if operator == "!=" and not key != bound:
            return False
    return True


def normalized_version(version: str) -> str:
    """Normalize a SemVer source spelling for cross-source intersection."""
    value = version.strip()
    return value[1:] if value.startswith("v") else value


def pypi_candidates(source: Mapping[str, str], constraint: str = "*") -> dict[str, Candidate]:
    name = source["name"]
    registry = source["registry"].rstrip("/")
    path = urllib.parse.quote(name, safe="")
    if registry.endswith("/simple"):
        url = f"{registry[:-7]}/pypi/{path}/json"
    elif registry.endswith("/pypi"):
        url = f"{registry}/{path}/json"
    else:
        url = f"{registry}/pypi/{path}/json"
    headers = _auth_headers(source)
    token_env = source.get("token_env")
    if token_env:
        token = os.environ.get(token_env, "")
        if not token:
            raise RegistryError(f"required registry token environment variable {token_env} is unset or empty")
        encoded = base64.b64encode(f"__token__:{token}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {encoded}"}
    payload, _headers = _json(url, headers)
    if not isinstance(payload, dict) or not isinstance(payload.get("releases"), dict):
        raise RegistryError(f"PyPI-compatible response for {name!r} has no releases table")
    result: dict[str, Candidate] = {}
    for version, files in payload["releases"].items():
        if (
            not isinstance(version, str) or not version_satisfies(version, constraint)
        ):
            continue
        if not isinstance(files, list) or not files:
            continue
        files = [item for item in files if isinstance(item, dict) and not item.get("yanked")]
        if not files:
            continue
        timestamps = [
            item.get("upload-time") or item.get("upload_time_iso_8601") or item.get("upload_time")
            for item in files
        ]
        valid = [stamp for stamp in timestamps if isinstance(stamp, str) and stamp.strip()]
        if not valid:
            raise RegistryError(f"PyPI release {name}=={version} has no upload timestamp")
        uploaded = max(_iso_datetime(stamp, f"PyPI release {name}=={version}") for stamp in valid)
        key = normalized_version(version)
        result[key] = Candidate(version=version, released_at=uploaded, age_source="pypi-upload-time")
    return result


def npm_candidates(source: Mapping[str, str], constraint: str = "*") -> dict[str, Candidate]:
    name = source["name"]
    registry = source["registry"].rstrip("/")
    url = f"{registry}/{urllib.parse.quote(name, safe='@/')}"
    payload, _headers = _json(url, _auth_headers(source))
    if not isinstance(payload, dict) or not isinstance(payload.get("versions"), dict):
        raise RegistryError(f"npm-compatible response for {name!r} has no versions table")
    times = payload.get("time")
    if not isinstance(times, dict):
        raise RegistryError(f"npm-compatible response for {name!r} has no version timestamps")
    result: dict[str, Candidate] = {}
    for version in payload["versions"]:
        if (
            not isinstance(version, str) or not version_satisfies(version, constraint)
        ):
            continue
        version_record = payload["versions"][version]
        if isinstance(version_record, dict) and version_record.get("deprecated"):
            continue
        stamp = times.get(version)
        if not isinstance(stamp, str) or not stamp.strip():
            raise RegistryError(f"npm release {name}@{version} has no publication timestamp")
        result[normalized_version(version)] = Candidate(
            version=version,
            released_at=_iso_datetime(stamp, f"npm release {name}@{version}"),
            age_source="npm-registry-time",
        )
    return result


def _go_escape(value: str) -> str:
    return "".join("!" + ch.lower() if ch.isupper() else ch for ch in value)


def _go_info(proxy: str, module: str, version: str, source: Mapping[str, str]) -> Candidate:
    escaped_module = "/".join(_go_escape(part) for part in module.split("/"))
    escaped_version = _go_escape(version)
    url = f"{proxy.rstrip('/')}/{escaped_module}/@v/{urllib.parse.quote(escaped_version, safe='!.-+')}.info"
    payload, _headers = _json(url, _auth_headers(source))
    if not isinstance(payload, dict) or payload.get("Version") != version:
        raise RegistryError(f"Go proxy returned invalid version metadata for {module}@{version}")
    return Candidate(
        version=version,
        released_at=_iso_datetime(payload.get("Time"), f"Go module {module}@{version}"),
        age_source="go-proxy-info-vcs-commit-time",
    )


_GO_PSEUDO_VERSION = re.compile(
    r"^v\d+\.\d+\.\d+-(?:[0-9A-Za-z.-]*\.)?\d{14}-[0-9a-f]{12}$"
)


def _go_pseudo_versions_in_constraint(constraint: str) -> set[str]:
    """Return published Go pseudo-versions that can seed a constrained search.

    The proxy's @v/list intentionally omits pseudo-versions. A go.mod-derived
    lower bound is nevertheless an exact, already-known candidate and can be
    checked through its .info endpoint.
    """
    found: set[str] = set()
    for part in constraint.split(","):
        match = _COMPARATOR.fullmatch(part.strip())
        if not match:
            continue
        operator = match.group(1) or "=="
        version = match.group(2).strip()
        if (
            operator in {"==", ">", ">="}
            and _GO_PSEUDO_VERSION.fullmatch(version)
        ):
            found.add(version)
    return found


def _go_latest(proxy: str, module: str, source: Mapping[str, str]) -> Candidate:
    escaped_module = "/".join(_go_escape(part) for part in module.split("/"))
    url = f"{proxy.rstrip('/')}/{escaped_module}/@latest"
    payload, _headers = _json(url, _auth_headers(source))
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("Version"), str)
        or not payload["Version"].startswith("v")
        or _semver_key(payload["Version"]) is None
    ):
        raise RegistryError(f"Go proxy returned invalid latest-version metadata for {module}")
    version = payload["Version"]
    return Candidate(
        version=version,
        released_at=_iso_datetime(payload.get("Time"), f"Go module {module}@{version}"),
        age_source="go-proxy-info-vcs-commit-time",
    )


def go_candidates(source: Mapping[str, str], constraint: str) -> dict[str, Candidate]:
    module = source["module"]
    proxy = source["proxy"].rstrip("/")
    escaped_module = "/".join(_go_escape(part) for part in module.split("/"))
    url = f"{proxy}/{escaped_module}/@v/list"
    body, _headers = _request(url, _auth_headers(source))
    try:
        listed = body.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise RegistryError(f"Go proxy returned invalid UTF-8 for {module} version list") from exc
    listed_versions = {
        line.strip() for line in listed if line.strip()
    }
    versions = [v for v in listed_versions if version_satisfies(v, constraint)]
    pseudo_seeds = _go_pseudo_versions_in_constraint(constraint) - listed_versions
    if len(versions) + len(pseudo_seeds) > MAX_REGISTRY_ITEMS:
        raise RegistryError(f"Go proxy listed more than {MAX_REGISTRY_ITEMS} candidates for {module}")
    result: dict[str, Candidate] = {}
    candidates_to_check = [*versions, *sorted(pseudo_seeds)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_go_info, proxy, module, version, source): version
            for version in candidates_to_check
        }
        for future in as_completed(futures):
            version = futures[future]
            try:
                candidate = future.result()
            except RegistryNotFoundError:
                if version in pseudo_seeds:
                    # Constraint bounds are useful seeds only when the proxy
                    # actually publishes that pseudo-version. Other failures
                    # remain hard errors; a listed version missing its .info
                    # is inconsistent proxy metadata.
                    continue
                raise
            if version_satisfies(candidate.version, constraint):
                result[normalized_version(candidate.version)] = candidate

    # The Go proxy protocol excludes pseudo-versions from @v/list. Match the
    # Go command's fallback and consult @latest only when the constraint has no
    # listed candidate. Seeded pseudo-versions still let an existing go.mod
    # baseline participate when the proxy has no @latest endpoint.
    if not versions:
        try:
            latest = _go_latest(proxy, module, source)
        except RegistryNotFoundError:
            if not result:
                raise RegistryError(
                    f"Go proxy returned no listed versions and does not provide @latest for {module}"
                )
        else:
            if version_satisfies(latest.version, constraint):
                key = normalized_version(latest.version)
                existing = result.get(key)
                if existing is None:
                    result[key] = latest
                elif latest.released_at != existing.released_at:
                    raise RegistryError(
                        f"Go proxy returned inconsistent timestamps for {module}@{latest.version}"
                    )
    return result


def _parse_bearer_challenge(value: str) -> dict[str, str] | None:
    match = re.match(r"\s*Bearer\s+(.*)$", value, re.IGNORECASE)
    if not match:
        return None
    fields: dict[str, str] = {}
    for key, val in re.findall(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"', match.group(1)):
        fields[key.lower()] = val
    return fields if fields.get("realm") else None


class _OCIClient:
    def __init__(self, registry: str, repository: str, source: Mapping[str, str]):
        self.registry = registry
        self.repository = repository
        self.source = source
        self.token: str | None = None

    def _authorized_request(self, url: str, headers: Mapping[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
        request_headers = {"User-Agent": USER_AGENT, **dict(headers or {})}
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        elif self.source.get("token_env"):
            request_headers.update(_auth_headers(self.source))
        request = urllib.request.Request(url, headers=request_headers)
        try:
            with _urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read(MAX_METADATA_BYTES + 1)
                if len(body) > MAX_METADATA_BYTES:
                    raise RegistryError(f"registry response exceeds {MAX_METADATA_BYTES} bytes: {url}")
                return body, {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            challenge = _parse_bearer_challenge(exc.headers.get("WWW-Authenticate", ""))
            if exc.code != 401 or challenge is None or self.source.get("token_env"):
                body = exc.read(MAX_METADATA_BYTES + 1) if exc.fp else b""
                excerpt = body[:500].decode("utf-8", errors="replace").replace("\n", " ")
                detail = f": {excerpt}" if excerpt else ""
                raise RegistryError(f"OCI registry returned HTTP {exc.code} for {url}{detail}") from exc
        except urllib.error.URLError as exc:
            raise RegistryError(f"could not reach OCI registry {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RegistryError(f"OCI registry request timed out: {url}") from exc
        except OSError as exc:
            raise RegistryError(f"OCI registry request failed for {url}: {exc}") from exc
        realm_url = challenge["realm"]
        try:
            _RegistryRedirectHandler._origin(realm_url)
        except RegistryError as exc:
            raise RegistryError(f"OCI registry provided an unsafe bearer-token realm for {url}") from exc
        realm = urllib.parse.urlsplit(realm_url)
        if "#" in realm_url:
            raise RegistryError(f"OCI registry provided an unsafe bearer-token realm for {url}")
        if self.source.get("username_env"):
            registry_origin = _RegistryRedirectHandler._origin(self.registry)
            allowed_external_realms = (
                {("https", "auth.docker.io", 443)}
                if registry_origin == ("https", "registry-1.docker.io", 443) else set()
            )
            realm_origin = _RegistryRedirectHandler._origin(realm_url)
            if realm_origin != registry_origin and realm_origin not in allowed_external_realms:
                raise RegistryError(
                    f"OCI registry credential realm {realm.netloc!r} is outside the configured registry"
                )
        params = {key: value for key, value in challenge.items() if key in {"service", "scope"}}
        token_url = challenge["realm"] + ("&" if "?" in challenge["realm"] else "?") + urllib.parse.urlencode(params)
        token_headers: dict[str, str] = {}
        if self.source.get("username_env"):
            token_headers = _auth_headers(self.source)
        token_payload, _ = _json(token_url, token_headers)
        if not isinstance(token_payload, dict):
            raise RegistryError(f"OCI registry returned an invalid bearer token response for {url}")
        token = token_payload.get("token") or token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RegistryError(f"OCI registry bearer response omitted a token for {url}")
        self.token = token
        return _request(url, {**request_headers, "Authorization": f"Bearer {token}"})

    def _json(self, url: str, headers: Mapping[str, str] | None = None) -> tuple[dict, dict[str, str]]:
        body, response_headers = self._authorized_request(url, headers)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegistryError(f"OCI registry returned invalid JSON for {url}") from exc
        if not isinstance(payload, dict):
            raise RegistryError(f"OCI registry returned a non-object response for {url}")
        return payload, response_headers

    def tag_list(self) -> list[str]:
        tags: list[str] = []
        url = f"{self.registry}/v2/{self.repository}/tags/list?n=1000"
        expected = urllib.parse.urlsplit(url)
        expected_origin = _RegistryRedirectHandler._origin(url)
        page_count = 0
        while url:
            page_count += 1
            if page_count > 100:
                raise RegistryError(f"OCI tag listing exceeded 100 pages for {self.repository}")
            payload, headers = self._json(url, {"Accept": "application/json"})
            page = payload.get("tags")
            if not isinstance(page, list) or not all(isinstance(t, str) for t in page):
                raise RegistryError(f"OCI registry returned an invalid tag list for {self.repository}")
            tags.extend(page)
            if len(tags) > MAX_REGISTRY_ITEMS:
                raise RegistryError(f"OCI registry listed more than {MAX_REGISTRY_ITEMS} tags for {self.repository}")
            link = headers.get("link", "")
            next_match = re.search(r"<([^>]+)>\s*;\s*rel=\"?next\"?", link, re.IGNORECASE)
            if next_match:
                next_url = urllib.parse.urljoin(url, next_match.group(1))
                parsed = urllib.parse.urlsplit(next_url)
                try:
                    next_origin = _RegistryRedirectHandler._origin(next_url)
                except RegistryError as exc:
                    raise RegistryError(
                        f"OCI registry returned an unsafe pagination link for {self.repository}"
                    ) from exc
                if next_origin != expected_origin or parsed.fragment or parsed.path != expected.path:
                    raise RegistryError(
                        f"OCI registry returned an unsafe pagination link for {self.repository}"
                    )
                url = next_url
            else:
                url = ""
        if not tags:
            raise RegistryError(f"OCI registry returned no tags for {self.repository}")
        return tags

    def tag_timestamp(self, tag: str) -> tuple[datetime, str]:
        accept = ", ".join((
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ))
        url = f"{self.registry}/v2/{self.repository}/manifests/{urllib.parse.quote(tag, safe='._+-') }"
        manifest, headers = self._json(url, {"Accept": accept})
        last_modified = headers.get("last-modified")
        if last_modified:
            return _iso_datetime(last_modified, f"OCI registry tag {tag}"), "oci-registry-last-modified"
        manifests = manifest.get("manifests")
        if manifests is not None and not isinstance(manifests, list):
            raise RegistryError(f"OCI image index for {tag} has an invalid platform manifest list")
        if manifests:
            if len(manifests) > 128:
                raise RegistryError(f"OCI image index for {tag} has more than 128 platform manifests")
            dates: list[datetime] = []
            age_sources: list[str] = []
            for descriptor in manifests:
                if not isinstance(descriptor, dict) or not isinstance(descriptor.get("digest"), str):
                    raise RegistryError(f"OCI image index for {tag} has an invalid platform descriptor")
                child_url = f"{self.registry}/v2/{self.repository}/manifests/{urllib.parse.quote(descriptor['digest'], safe=':._+-')}"
                child, child_headers = self._json(child_url, {"Accept": accept})
                child_last_modified = child_headers.get("last-modified")
                if child_last_modified:
                    dates.append(_iso_datetime(child_last_modified, f"OCI registry manifest {descriptor['digest']}"))
                    age_sources.append("oci-registry-last-modified")
                    continue
                child_created = self._created_time(child)
                if child_created:
                    dates.append(child_created)
                    age_sources.append("oci-image-created-fallback")
                    continue
                child_config = child.get("config")
                if isinstance(child_config, dict) and isinstance(child_config.get("digest"), str):
                    blob_url = f"{self.registry}/v2/{self.repository}/blobs/{urllib.parse.quote(child_config['digest'], safe=':._+-')}"
                    child_body, _ = self._authorized_request(blob_url, {"Accept": "application/json"})
                    try:
                        child_config_doc = json.loads(child_body)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RegistryError(f"OCI image config for {tag} is invalid JSON") from exc
                    if isinstance(child_config_doc, dict):
                        child_date = child_config_doc.get("created")
                        if isinstance(child_date, str) and child_date.strip():
                            dates.append(_iso_datetime(child_date, f"OCI image {tag}"))
                            age_sources.append("oci-image-created-fallback")
            if dates:
                if len(dates) != len(manifests):
                    index_created = self._created_time(manifest)
                    if index_created:
                        return max([index_created, *dates]), "oci-image-created-fallback"
                    raise RegistryError(
                        f"OCI image index for {tag} has platform manifests without a usable timestamp"
                    )
                # The tag can point at several platform manifests; use the
                # newest platform date so every selected platform satisfies
                # the declared age window.
                return max(dates), (
                    "oci-image-created-fallback"
                    if "oci-image-created-fallback" in age_sources
                    else "oci-registry-last-modified"
                )
        created = self._created_time(manifest)
        if created:
            return created, "oci-image-created-fallback"
        config = manifest.get("config")
        if isinstance(config, dict) and isinstance(config.get("digest"), str):
            blob_url = f"{self.registry}/v2/{self.repository}/blobs/{urllib.parse.quote(config['digest'], safe=':._+-')}"
            config_body, _ = self._authorized_request(blob_url, {"Accept": "application/json"})
            try:
                config_doc = json.loads(config_body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RegistryError(f"OCI image config for {tag} is invalid JSON") from exc
            if isinstance(config_doc, dict):
                value = config_doc.get("created")
                if isinstance(value, str) and value.strip():
                    return _iso_datetime(value, f"OCI image {tag}"), "oci-image-created-fallback"
        raise RegistryError(
            f"OCI tag {tag!r} has neither a registry Last-Modified timestamp nor an "
            "org.opencontainers.image.created/config created timestamp"
        )

    @staticmethod
    def _created_time(manifest: Mapping[str, object]) -> datetime | None:
        annotations = manifest.get("annotations")
        if not isinstance(annotations, dict):
            return None
        value = annotations.get("org.opencontainers.image.created")
        if isinstance(value, str) and value.strip():
            return _iso_datetime(value, "OCI image annotation")
        return None


def _oci_identity(image: str) -> tuple[str, str]:
    registry, separator, repository = image.partition("/")
    if not separator:
        raise RegistryError("OCI image must include registry host and repository")
    if not repository:
        raise RegistryError("OCI image must include registry host and repository")
    if registry in {"docker.io", "index.docker.io"}:
        registry = "registry-1.docker.io"
    return f"https://{registry}", repository


def oci_candidates(source: Mapping[str, str], constraint: str) -> dict[str, Candidate]:
    registry, repository = _oci_identity(source["image"])
    client = _OCIClient(registry, repository, source)
    template = source["tag"]
    pattern = re.escape(template)
    pattern = pattern.replace(re.escape("{version}"), r"(?P<version>.+?)")
    tag_re = re.compile("^" + pattern + "$")
    result: dict[str, Candidate] = {}
    matched: list[tuple[str, str]] = []
    for tag in client.tag_list():
        match = tag_re.fullmatch(tag)
        if not match:
            continue
        version = match.group("version")
        if not version_satisfies(version, constraint):
            continue
        matched.append((tag, version))
    if not matched:
        raise RegistryError(f"OCI image {source['image']} has no tags matching its template and constraint")
    if len(matched) > MAX_REGISTRY_ITEMS:
        raise RegistryError(f"OCI image {source['image']} has too many matching tags")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(client.tag_timestamp, tag): (tag, version) for tag, version in matched}
        for future in as_completed(futures):
            tag, version = futures[future]
            stamp, age_source = future.result()
            key = normalized_version(version)
            previous = result.get(key)
            candidate = Candidate(version=version, released_at=stamp, age_source=age_source, tag=tag)
            if previous is None or (candidate.released_at, candidate.tag or "") > (
                previous.released_at, previous.tag or "",
            ):
                result[key] = candidate
    return result


def candidates(source_type: str, source: Mapping[str, str], constraint: str) -> dict[str, Candidate]:
    if source_type == "pypi":
        return pypi_candidates(source, constraint)
    if source_type == "npm":
        return npm_candidates(source, constraint)
    if source_type == "go":
        return go_candidates(source, constraint)
    if source_type == "oci":
        return oci_candidates(source, constraint)
    raise RegistryError(f"unsupported version source type {source_type!r}")


def candidate_for(source_type: str, source: Mapping[str, str], version: str) -> Candidate:
    """Read metadata for one exact version, preserving normal HTTP failure semantics."""
    all_candidates = candidates(source_type, source, f"=={version}")
    candidate = all_candidates.get(normalized_version(version))
    if candidate is None:
        raise RegistryError(f"registry does not publish exact version {version!r}")
    return candidate
