"""Declared tool dependencies: verification (S15).

`cmru.toml` may declare `[[project.tool_dependencies]]` -- a first-party artifact
this project's OWN tests/tooling consume (see `cmru.config.ToolDependency` and
`cmru.dependencies` for the declaration + graph-reporting halves). This module is
the VERIFICATION half: three DISTINCT checks per declared dependency, never
conflated with one another, in either code or their messages:

* **Integrity** -- do the vendored bytes match the recorded ``sha256``? Purely
  local; never touches the network; always resolvable (pass/fail only).
* **Authenticity** -- does that recorded hash equal the PUBLISHED release asset's
  digest, for that project and exact pinned version? This is the name-vs-object
  check for artifacts: a file named ``assay-1.0.0.pyz`` is not thereby assay
  1.0.0. Bytes downloaded from the published release are hashed and compared;
  the filename is used only to locate which asset to download, never as
  evidence of authenticity by itself.
* **Freshness** -- is the pinned version the HIGHEST released version for that
  project's tag prefix? This is the staleness check, and it is independent of
  authenticity: a pin can be authentic (really is what it claims to be) and
  simultaneously stale (a newer real release exists).

Authenticity and freshness require the published-release catalog, which is
network state that WILL sometimes be absent (a fresh clone with nothing released
yet) or unreachable (no network). Both are then reported as "unresolved", a
THIRD, explicit outcome distinct from "pass" and "fail": a mismatch must never
surface as "could not check", and "could not check" must never surface as
success. Integrity has no such state -- it is always resolvable.

**This module performs real network I/O and MUST NOT be imported/exercised by
cmru's own test suite in a way that reaches the network** (tests monkeypatch
:func:`urlopen` at this module's own seam). Verification runs only from the
`cmru tool-deps` verb and the release preflight (`cmru.cli`); never during
`cmru tester-gate` / `pytest`. That is deliberate: the whole reason cmru vendors
a pinned artifact instead of fetching it at test time is so the test suite stays
hermetic, reproducible, and bootstrappable from a bare clone with no network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cmru import exit_codes
from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME

if TYPE_CHECKING:
    from cmru.config import ToolDependency

API_BASE = "https://api.github.com"
# A stalled network call must never hang a release (brief constraint): every
# request this module makes carries an explicit timeout.
DEFAULT_TIMEOUT = 10


class NetworkUnavailable(RuntimeError):
    """GitHub could not be reached, or answered with something untrustworthy
    (connect/DNS/timeout failure, or an unexpected HTTP status/body). Distinct
    from a clean 404 ("this exact thing is absent", handled as ``None``): this
    is "we don't know", never silently folded into "no" or "yes".
    """


# --- outcomes ----------------------------------------------------------------

@dataclass(frozen=True)
class CheckOutcome:
    """The result of exactly ONE of the three checks, for one dependency.

    ``status`` is exactly one of ``"pass"``, ``"fail"``, ``"unresolved"``.
    ``"unresolved"`` always carries a ``reason`` of ``"no-release"`` or
    ``"network-error"`` -- never silently reported as ``"pass"`` (a mismatch
    presenting as "could not check" would be worse than useless) nor as
    ``"fail"`` ("could not check" presenting as a real defect would block every
    project's release for a reason that isn't the operator's to fix).
    """
    status: str             # "pass" | "fail" | "unresolved"
    reason: Optional[str]   # short machine-readable code, or None for a plain pass
    detail: str             # human-readable, kind-specific explanation


@dataclass(frozen=True)
class ToolDependencyStatus:
    """The complete verification result for one declared tool dependency."""
    consumer: str
    dependency: "ToolDependency"
    integrity: CheckOutcome
    authenticity: CheckOutcome
    freshness: CheckOutcome
    highest_known_version: Optional[str]


def is_blocking(status: ToolDependencyStatus, *, allow_stale: bool = False) -> bool:
    """True if ``status`` must refuse a release / fail ``cmru tool-deps`` by default.

    Integrity and authenticity failures ALWAYS block -- they are objective
    evidence of a bad artifact (corrupted bytes, or bytes that are not the
    published object), and no flag in this feature overrides either. Only
    freshness (staleness is a policy judgement, not a corruption signal) has an
    override, and only that one: ``--allow-stale-tool-deps``. "unresolved"
    outcomes (bootstrap / network trouble) never block -- they are not failures.
    """
    if status.integrity.status == "fail":
        return True
    if status.authenticity.status == "fail":
        return True
    if status.freshness.status == "fail" and not allow_stale:
        return True
    return False


def render_status(status: ToolDependencyStatus) -> str:
    dependency = status.dependency
    lines = [
        f"{status.consumer}: tool dependency {dependency.project}@{dependency.version} "
        f"({dependency.path})"
    ]
    markers = {"pass": "PASS", "fail": "FAIL", "unresolved": "UNRESOLVED"}
    for label, outcome in (
        ("integrity", status.integrity),
        ("authenticity", status.authenticity),
        ("freshness", status.freshness),
    ):
        marker = markers[outcome.status]
        reason = f" ({outcome.reason})" if outcome.reason else ""
        lines.append(f"  {label:<12} {marker}{reason} -- {outcome.detail}")
    return "\n".join(lines)


def _outcome_dict(outcome: CheckOutcome) -> dict:
    return {"status": outcome.status, "reason": outcome.reason, "detail": outcome.detail}


def status_as_dict(status: ToolDependencyStatus) -> dict:
    dependency = status.dependency
    return {
        "consumer": status.consumer,
        "project": dependency.project,
        "version": dependency.version,
        "path": dependency.path,
        "sha256": dependency.sha256,
        "highest_known_version": status.highest_known_version,
        "integrity": _outcome_dict(status.integrity),
        "authenticity": _outcome_dict(status.authenticity),
        "freshness": _outcome_dict(status.freshness),
    }


# --- network transport (own timeout-bearing seam; deliberately NOT release.py's
# GitHubReleases -- that client's tests monkeypatch a bare `urlopen(req)` call with
# no `timeout=` kwarg all over the suite, so adding one there would break them) ---

def _get(url: str, *, timeout: int) -> tuple[int, bytes]:
    request = Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "cmru-tool-deps"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, (exc.read() if exc.fp else b"")
    except (URLError, OSError, TimeoutError, HTTPException) as exc:
        # HTTPException (IncompleteRead, BadStatusLine, ...) is NOT a subclass of
        # OSError/URLError -- a truncated/malformed response mid-transfer would
        # otherwise escape this function as a raw exception, propagate through
        # the release-plan phase uncaught, and get treated as a genuine
        # mid-release failure (worktree retained) for what was only a flaky
        # network read.
        raise NetworkUnavailable(str(exc)) from exc


def _github_get_json(path: str, *, timeout: int, on_404: str = "absent") -> Optional[dict]:
    """GET one GitHub REST path.

    ``on_404="absent"`` (default): a 404 means this EXACT thing legitimately
    does not exist (e.g. no release under one specific tag) -- returns
    ``None``, a real, checked fact.

    ``on_404="error"``: a 404 means the endpoint itself could not be reached
    as expected (e.g. listing releases for a repo that is missing, renamed,
    private, or misspelled in ``[github]``) -- raises NetworkUnavailable.
    Measured: a repository with genuinely ZERO releases returns HTTP 200 with
    an empty ``[]`` body, never 404 -- so a 404 on the LIST endpoint can NEVER
    mean "nothing published yet" (S15.4's bootstrap case). Conflating the two
    would let an inaccessible repo silently and permanently stop being
    checked, reported as the benign "could not check" outcome forever -- the
    exact conflation S15.4/S15.5 exist to prevent.

    Either way, any other non-2xx status, or a body that doesn't parse as
    JSON, is NetworkUnavailable -- never silently treated as absent.
    """
    status, body = _get(f"{API_BASE}{path}", timeout=timeout)
    if status == 404:
        if on_404 == "absent":
            return None
        raise NetworkUnavailable(
            f"GitHub API {path} returned HTTP 404 -- the repository is missing, renamed, "
            "private, or misspelled in [github]; this is NOT the same as a repository with "
            "zero releases (which returns 200 and an empty list)"
        )
    if status >= 400:
        raise NetworkUnavailable(f"GitHub API {path} returned HTTP {status}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise NetworkUnavailable(f"GitHub API {path} returned an unparseable response") from exc


def _download_asset(url: str, *, timeout: int) -> bytes:
    status, body = _get(url, timeout=timeout)
    if status != 200:
        raise NetworkUnavailable(f"downloading {url} returned HTTP {status}")
    return body


def _list_releases(owner: str, repo: str, *, timeout: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        # on_404="error": see _github_get_json's docstring -- a 404 here means
        # the repository itself is inaccessible, never "zero releases" (S15
        # B1: that reads 200 + []). Every page uses the same rule; GitHub
        # returns 200 + [] past the last page of a real repo, never 404.
        data = _github_get_json(
            f"/repos/{owner}/{repo}/releases?per_page=100&page={page}", timeout=timeout,
            on_404="error",
        )
        batch = data or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


def resolve_latest_release(owner: str, repo: str, prefix: str, *, timeout: int) -> Optional[dict]:
    """Highest-semver ``<prefix><semver>`` release, or ``None`` if none exist yet
    (bootstrap: a fresh estate with nothing released). ``prefix`` is a project's
    FULL declared ``ProjectS2Config.prefix`` (e.g. ``"assay-v"``, already ending in
    ``-v`` -- S0's own terminology: "tag = <prefix><semver>", direct concatenation,
    no second ``-v`` inserted here). Mirrors
    ``cmru.release.GitHubReleases.resolve_latest`` (same "highest semver, ignore
    drafts/prereleases/the thin -latest pointer" rule -- that function instead
    takes the BARE project id and inserts ``-v`` itself, a different convention
    used by ``cmru.handlers``' ``--prefix`` CLI argument) but with an enforced
    per-request timeout and errors distinguished by kind."""
    from cmru.release import _semver_key

    releases = _list_releases(owner, repo, timeout=timeout)
    marker = prefix
    candidates = []
    for rel in releases:
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
        "assets": [
            {"name": a.get("name"), "url": a.get("browser_download_url")}
            for a in rel.get("assets", [])
        ],
    }


# --- the three checks ---------------------------------------------------------

def _check_integrity(project_root: Path, dependency: "ToolDependency") -> CheckOutcome:
    """Local only: never touches the network, always resolvable. This is what
    cmru's existing ``sha256sum -c *.sha256`` test step already proves; it is
    made explicit in the model here rather than duplicated."""
    target = project_root / dependency.path
    if not target.is_file():
        return CheckOutcome(
            "fail", "missing-file", f"{dependency.path} does not exist under {project_root}",
        )
    actual = sha256(target.read_bytes()).hexdigest()
    if actual == dependency.sha256:
        return CheckOutcome(
            "pass", None, f"{dependency.path} bytes match the recorded sha256",
        )
    return CheckOutcome(
        "fail", "hash-mismatch",
        f"{dependency.path} bytes do NOT match the recorded sha256 (actual {actual})",
    )


def verify_tool_dependency(
    *, owner: str, repo: str, provider_prefix: str, dependency: "ToolDependency",
    project_root: Path, consumer: str, timeout: int = DEFAULT_TIMEOUT,
) -> ToolDependencyStatus:
    """Run all three checks for ONE declared dependency."""
    integrity = _check_integrity(project_root, dependency)

    try:
        resolved = resolve_latest_release(owner, repo, provider_prefix, timeout=timeout)
    except NetworkUnavailable as exc:
        unresolved = CheckOutcome(
            "unresolved", "network-error",
            f"could not reach GitHub to verify {dependency.project!r}: {exc}",
        )
        return ToolDependencyStatus(consumer, dependency, integrity, unresolved, unresolved, None)

    if resolved is None:
        unresolved = CheckOutcome(
            "unresolved", "no-release",
            f"no published release exists yet for project {dependency.project!r} (tag prefix "
            f"{provider_prefix!r}); bootstrap state -- nothing published to compare the pin "
            "against yet",
        )
        return ToolDependencyStatus(consumer, dependency, integrity, unresolved, unresolved, None)

    highest = resolved["version"]
    pinned_key = _semver_key(dependency.version)
    highest_key = _semver_key(highest)
    # Ordered so every comparison here DISCRIMINATES: `<` and `>` are checked
    # first, each against the full three-way space, with equality falling to
    # the final `else` rather than being special-cased first. A leading
    # `if pinned_key == highest_key: ... elif pinned_key < highest_key: ...`
    # makes `<` reachable only once `!=` is already known -- `<` and `<=` are
    # then semantically IDENTICAL in that branch (an equivalent mutant no
    # test can kill). With equality last, `<` -> `<=` would send an equal pin
    # down "stale" and `>` -> `>=` would send it down "ahead" -- both wrong,
    # and both caught by the pinned-equals-highest test below.
    if pinned_key < highest_key:
        freshness = CheckOutcome(
            "fail", "stale",
            f"pinned version {dependency.version} is behind the highest published "
            f"{dependency.project} release {highest}",
        )
    elif pinned_key > highest_key:
        # NOTE: this only says what was actually checked -- pinned is higher than
        # every NON-DRAFT, NON-PRERELEASE release resolve_latest_release scanned
        # (it deliberately excludes both, S15). It must NOT claim "not among
        # published releases": a draft or prerelease could exist under this
        # exact tag (resolve_latest_release would never see it, since it is
        # excluded from the "highest" candidate set) -- the authenticity check
        # right below does its own direct, exact-tag lookup and can still find
        # and confirm one. Overclaiming here would then contradict a PASSING
        # authenticity outcome on the very same status.
        freshness = CheckOutcome(
            "fail", "ahead-of-known-releases",
            f"pinned version {dependency.version} is higher than every published, "
            f"non-draft, non-prerelease {dependency.project} release found (highest is "
            f"{highest}); this check has not confirmed this pin corresponds to any release "
            "at all",
        )
    else:
        freshness = CheckOutcome(
            "pass", None,
            f"pinned version {dependency.version} is the highest published "
            f"{dependency.project} release",
        )

    tag = f"{provider_prefix}{dependency.version}"
    if pinned_key == highest_key:
        # Already resolved above -- avoid a second network round trip for the
        # common (fresh, authentic) case.
        assets = resolved["assets"]
    else:
        try:
            release = _github_get_json(f"/repos/{owner}/{repo}/releases/tags/{tag}", timeout=timeout)
        except NetworkUnavailable as exc:
            authenticity = CheckOutcome(
                "unresolved", "network-error",
                f"could not reach GitHub to verify the published {tag!r} asset: {exc}",
            )
            return ToolDependencyStatus(consumer, dependency, integrity, authenticity, freshness, highest)
        if release is None:
            authenticity = CheckOutcome(
                "fail", "version-not-published",
                f"declared version {dependency.version} has no published release under tag "
                f"{tag!r}; the vendored artifact's authenticity cannot be confirmed",
            )
            return ToolDependencyStatus(consumer, dependency, integrity, authenticity, freshness, highest)
        assets = [
            {"name": a.get("name"), "url": a.get("browser_download_url")}
            for a in release.get("assets", [])
        ]

    asset_name = Path(dependency.path).name
    asset = next((a for a in assets if a["name"] == asset_name), None)
    if asset is None:
        authenticity = CheckOutcome(
            "fail", "asset-missing",
            f"published release {tag!r} has no asset named {asset_name!r}",
        )
        return ToolDependencyStatus(consumer, dependency, integrity, authenticity, freshness, highest)

    try:
        published_bytes = _download_asset(asset["url"], timeout=timeout)
    except NetworkUnavailable as exc:
        authenticity = CheckOutcome(
            "unresolved", "network-error",
            f"could not download the published {asset_name!r} bytes to verify: {exc}",
        )
        return ToolDependencyStatus(consumer, dependency, integrity, authenticity, freshness, highest)

    published_digest = sha256(published_bytes).hexdigest()
    if published_digest == dependency.sha256:
        authenticity = CheckOutcome(
            "pass", None,
            f"recorded sha256 matches the published {asset_name!r} bytes for {tag!r} "
            "(bytes compared, never the filename or version string)",
        )
    else:
        authenticity = CheckOutcome(
            "fail", "hash-mismatch",
            f"recorded sha256 {dependency.sha256} does NOT match the published {asset_name!r} "
            f"bytes for {tag!r} (published digest {published_digest}) -- the vendored file is "
            "not the published object",
        )
    return ToolDependencyStatus(consumer, dependency, integrity, authenticity, freshness, highest)


def _semver_key(version: str) -> tuple:
    from cmru.release import _semver_key as key

    return key(version)


def verify_project(
    *, owner: str, repo: str, project: object, projects: Mapping[str, object],
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[ToolDependencyStatus, ...]:
    """Verify every tool dependency ``project`` declares. ``projects`` resolves
    each dependency's PROVIDER project (for its GitHub tag ``prefix``) -- this
    is why the check needs the whole loaded estate, not just ``project`` alone."""
    statuses: list[ToolDependencyStatus] = []
    for dependency in getattr(project, "tool_dependencies", ()) or ():
        provider = projects.get(dependency.project)
        if provider is None:
            # Two independent guards keep this unreachable via the CLI today:
            # an orchestration-mode load already rejects an unknown provider at
            # `load_config` time (dependencies.py's build_report), and a
            # single-project-mode load -- which can never see a sibling
            # provider at all, even for a perfectly real, authentic pin -- is
            # refused up front by tool_deps_main's own orchestration-required
            # check (S15.6/B2) before verify_project is ever called. This
            # branch is defensive depth for a direct caller of this function
            # (verified by a direct unit test) that skips both.
            unresolved = CheckOutcome(
                "fail", "unknown-provider",
                f"tool dependency provider project {dependency.project!r} is not part of the "
                "loaded estate configuration",
            )
            integrity = _check_integrity(Path(project.project_root), dependency)
            statuses.append(
                ToolDependencyStatus(project.name, dependency, integrity, unresolved, unresolved, None)
            )
            continue
        statuses.append(
            verify_tool_dependency(
                owner=owner, repo=repo, provider_prefix=provider.prefix, dependency=dependency,
                project_root=Path(project.project_root), consumer=project.name, timeout=timeout,
            )
        )
    return tuple(statuses)


# --- explicit refresh (never automatic) ---------------------------------------

def refresh_tool_dependency(
    *, owner: str, repo: str, provider_prefix: str, dependency: "ToolDependency",
    project_root: Path, config_path: Path, timeout: int = DEFAULT_TIMEOUT,
) -> "ToolDependency":
    """Re-vendor ``dependency`` from its provider's latest published release, and
    rewrite the pin + hash in ``config_path``. Deliberately requires an explicit,
    separate CLI invocation (``cmru tool-deps --refresh``) -- verification never
    calls this on its own."""
    resolved = resolve_latest_release(owner, repo, provider_prefix, timeout=timeout)
    if resolved is None:
        raise RuntimeError(
            f"cannot refresh {dependency.project!r}: no published release exists yet for tag "
            f"prefix {provider_prefix!r}"
        )
    new_version = resolved["version"]
    old_name = Path(dependency.path).name
    if dependency.version not in old_name:
        raise RuntimeError(
            f"cannot infer the refreshed filename: {dependency.path!r} does not contain the "
            f"pinned version {dependency.version!r}"
        )
    new_name = old_name.replace(dependency.version, new_version)
    asset = next((a for a in resolved["assets"] if a["name"] == new_name), None)
    if asset is None:
        raise RuntimeError(
            f"published release {resolved['tag']!r} has no asset named {new_name!r}"
        )

    published_bytes = _download_asset(asset["url"], timeout=timeout)
    new_digest = sha256(published_bytes).hexdigest()
    new_relative_path = str(Path(dependency.path).with_name(new_name))

    old_absolute = project_root / dependency.path
    new_absolute = project_root / new_relative_path
    new_absolute.parent.mkdir(parents=True, exist_ok=True)
    new_absolute.write_bytes(published_bytes)
    new_absolute.with_name(new_absolute.name + ".sha256").write_text(
        f"{new_digest}  {new_name}\n", encoding="utf-8",
    )
    if old_absolute != new_absolute and old_absolute.exists():
        old_absolute.unlink()
        old_sidecar = old_absolute.with_name(old_absolute.name + ".sha256")
        if old_sidecar.exists():
            old_sidecar.unlink()

    new_dependency = replace(
        dependency, version=new_version, path=new_relative_path, sha256=new_digest,
    )
    _rewrite_tool_dependency_toml(config_path, dependency, new_dependency)
    return new_dependency


_TOOL_DEPENDENCY_BLOCK_RE = re.compile(r"(?ms)^\[\[project\.tool_dependencies\]\]\n(.*?)(?=^\[|\Z)")


def _rewrite_tool_dependency_toml(
    config_path: Path, old: "ToolDependency", new: "ToolDependency",
) -> None:
    """Surgical text edit of exactly one ``[[project.tool_dependencies]]`` entry
    -- the same marked-region-only editing discipline as
    ``dependencies.write_comment_block`` and ``standards._update_project_revision``,
    so the rest of a hand-formatted ``cmru.toml`` is left byte-for-byte alone."""
    contents = config_path.read_text(encoding="utf-8")
    target = None
    for match in _TOOL_DEPENDENCY_BLOCK_RE.finditer(contents):
        if re.search(rf'(?m)^\s*project\s*=\s*"{re.escape(old.project)}"\s*$', match.group(1)):
            target = match
            break
    if target is None:
        raise ValueError(
            f"{config_path}: no [[project.tool_dependencies]] entry found for project "
            f"{old.project!r}"
        )
    body = target.group(1)
    body = re.sub(r'(?m)^(\s*version\s*=\s*)"[^"]*"', rf'\1"{new.version}"', body, count=1)
    body = re.sub(r'(?m)^(\s*path\s*=\s*)"[^"]*"', rf'\1"{new.path}"', body, count=1)
    body = re.sub(r'(?m)^(\s*sha256\s*=\s*)"[^"]*"', rf'\1"{new.sha256}"', body, count=1)
    updated = contents[:target.start(1)] + body + contents[target.end(1):]
    config_path.write_text(updated, encoding="utf-8")


# --- CLI verb ------------------------------------------------------------------

def tool_deps_main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify declared first-party tool dependencies (S15): integrity (bytes match "
            "the recorded hash), authenticity (that hash matches the PUBLISHED release "
            "asset's bytes -- never the filename or version string), and freshness (the "
            "pin is the highest published version). Performs network I/O; never run this "
            "from the test suite."
        )
    )
    parser.add_argument(
        "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
    )
    parser.add_argument(
        "--project", action="append",
        help="Check only this project's declarations (repeatable; default: every orchestrated project)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable per-dependency records")
    parser.add_argument(
        "--allow-stale-tool-deps", action="store_true",
        help=(
            "Do not fail merely because a pin is behind the highest published release. "
            "Integrity and authenticity failures still fail -- there is no override for those."
        ),
    )
    parser.add_argument(
        "--refresh", metavar="PROVIDER_PROJECT",
        help=(
            "Re-vendor PROVIDER_PROJECT's declared artifact from its latest published release "
            "and rewrite the pin + hash in the declaring project's cmru.toml. Never runs "
            "automatically -- an explicit, separate invocation."
        ),
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Network timeout in seconds for each GitHub request (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args(argv)

    # Import lazily: cli dispatches this verb, and is itself the configuration
    # model used by the report (same pattern as cmru.standards.standards_main).
    from cmru.cli import _resolve_config, load_config

    config_path = _resolve_config(args.config)
    repo_root, projects, project_order, *_rest = load_config(config_path)
    github_config = _rest[-2]

    selected = args.project or list(projects)
    unknown = sorted(set(selected) - set(projects))
    if unknown:
        parser.error(f"unknown project(s): {', '.join(unknown)}")

    # S15.6/B2: a single-project load (`cmru.toml`, e.g. run from a project
    # directory, or `--config <project>/cmru.toml`) only EVER sees its own
    # project -- `load_config` cannot resolve a sibling PROVIDER project's tag
    # prefix from there, no matter how real and authentic the pin is. That is a
    # gap in what THIS INVOCATION can resolve, not a finding about the vendored
    # artifact -- it must never be reported as an authenticity failure (which
    # would be unfixable: there is no override for authenticity). Refuse the
    # invocation itself, explicitly, before any check runs.
    if config_path.name != ORCHESTRATION_CONFIG_FILENAME:
        declaring = [name for name in selected if getattr(projects[name], "tool_dependencies", ())]
        if declaring:
            parser.error(
                f"cmru tool-deps needs {ORCHESTRATION_CONFIG_FILENAME} to resolve a tool "
                "dependency's PROVIDER project (its release tag prefix) -- a single-project "
                f"load ({config_path.name}) only ever sees its own project, never a sibling: "
                f"{', '.join(declaring)} declare one. Re-run with --config pointing at the "
                f"estate's {ORCHESTRATION_CONFIG_FILENAME}, or from the estate root."
            )

    if args.refresh:
        _run_refresh(
            projects=projects, selected=selected, provider_id=args.refresh,
            owner=github_config.owner, repo=github_config.repo, timeout=args.timeout,
        )
        return

    statuses: list[ToolDependencyStatus] = []
    for name in selected:
        statuses.extend(
            verify_project(
                owner=github_config.owner, repo=github_config.repo,
                project=projects[name], projects=projects, timeout=args.timeout,
            )
        )

    if not statuses:
        if args.json:
            print(json.dumps([]))
        else:
            print("[INFO] cmru tool-deps: no project declares a tool dependency.", flush=True)
        return

    if args.json:
        print(json.dumps([status_as_dict(s) for s in statuses], indent=2, sort_keys=True))
    else:
        for status in statuses:
            print(render_status(status), flush=True)

    blocking = [s for s in statuses if is_blocking(s, allow_stale=args.allow_stale_tool_deps)]
    if blocking:
        plural = "y is" if len(statuses) == 1 else "ies are"
        print(
            f"[ERROR] cmru tool-deps: {len(blocking)} of {len(statuses)} declared dependenc{plural} "
            "blocking (a stale or mismatched tool dependency is an error by default; "
            "--allow-stale-tool-deps overrides staleness only).",
            file=sys.stderr, flush=True,
        )
        raise SystemExit(exit_codes.CONFIG_ERROR)
    if not args.json:
        plural = "y is" if len(statuses) == 1 else "ies are"
        print(f"[INFO] cmru tool-deps: {len(statuses)} declared dependenc{plural} OK.", flush=True)


def _run_refresh(
    *, projects: Mapping[str, object], selected: Iterable[str], provider_id: str,
    owner: str, repo: str, timeout: int,
) -> None:
    if provider_id not in projects:
        print(f"[ERROR] cmru tool-deps --refresh: unknown project {provider_id!r}", file=sys.stderr, flush=True)
        raise SystemExit(exit_codes.CONFIG_ERROR)
    provider = projects[provider_id]
    touched = 0
    for name in selected:
        project = projects[name]
        for dependency in getattr(project, "tool_dependencies", ()) or ():
            if dependency.project != provider_id:
                continue
            config_path = Path(project.project_root) / PROJECT_CONFIG_FILENAME
            new_dependency = refresh_tool_dependency(
                owner=owner, repo=repo, provider_prefix=provider.prefix, dependency=dependency,
                project_root=Path(project.project_root), config_path=config_path, timeout=timeout,
            )
            print(
                f"[INFO] cmru tool-deps --refresh: {name}: {provider_id} {dependency.version} -> "
                f"{new_dependency.version} ({new_dependency.path})",
                flush=True,
            )
            touched += 1
    if not touched:
        print(
            f"[ERROR] cmru tool-deps --refresh: no selected project declares a tool dependency "
            f"on {provider_id!r}",
            file=sys.stderr, flush=True,
        )
        raise SystemExit(exit_codes.CONFIG_ERROR)
