#!/usr/bin/env python3
"""Read-only, exact-build source verification for the TrueNAS patch adapters.

``verify`` checks the supported build's complete pre-patch target hash set,
then asks GNU patch to dry-run every adapter patch with zero fuzz. It never
changes the target tree. ``upstream`` cheaply checks the current official ISO
directory and TrueNAS middleware source head without connecting to v1001.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import unquote
from urllib.request import Request, urlopen


PATCH_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PATCH_ROOT / "supported-builds.json"
DOWNLOAD_ROOT = "https://download.truenas.com"
SOURCE_ROOTS = {
    "middleware": "https://raw.githubusercontent.com/truenas/middleware",
    "truenas_pylibvirt": "https://raw.githubusercontent.com/truenas/truenas_pylibvirt",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
BUILD_RE = re.compile(r"^\d+\.\d+\.\d+-(?:RC\.\d+|MASTER\+\d{8}-\d{6})$")


class DriftError(RuntimeError):
    """A build or source tree is not safe for this adapter."""


@dataclass(frozen=True)
class HashIssue:
    path: str
    expected: str
    actual: str | None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(unquote(value).replace("\\u002b", "+"))


def read_target_hashes(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DriftError(f"cannot read target manifest {path}: {exc}") from exc
    result: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or SHA256_RE.fullmatch(fields[0]) is None:
            raise DriftError(f"invalid target manifest row at {path}:{number}")
        name = fields[1]
        target = PurePosixPath(name)
        if (target.is_absolute() or ".." in target.parts or not target.parts
                or target.parts[0] not in {"middlewared", "truenas_pylibvirt"}):
            raise DriftError(f"unsafe target path at {path}:{number}: {name!r}")
        if name in result:
            raise DriftError(f"duplicate target path at {path}:{number}: {name!r}")
        result[name] = fields[0]
    if not result:
        raise DriftError(f"target manifest is empty: {path}")
    return result


def load_supported_builds(path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriftError(f"cannot read supported-build manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise DriftError(f"unsupported supported-build manifest schema: {path}")
    rows = raw.get("supported_builds")
    if not isinstance(rows, dict) or not rows:
        raise DriftError(f"supported-build manifest has no builds: {path}")
    result: dict[str, dict[str, Any]] = {}
    for version, row in rows.items():
        if not isinstance(version, str) or BUILD_RE.fullmatch(version) is None or not isinstance(row, dict):
            raise DriftError(f"invalid supported-build entry: {version!r}")
        directory = row.get("directory")
        target_count = row.get("target_count")
        target_manifest_sha256 = row.get("target_manifest_sha256")
        patches = row.get("patches")
        train = row.get("train")
        adapter_id = row.get("adapter_id")
        install_bundle_sha256 = row.get("install_bundle_sha256")
        if (not isinstance(directory, str) or not directory or "/" in directory
                or type(target_count) is not int or target_count <= 0
                or not isinstance(target_manifest_sha256, str)
                or SHA256_RE.fullmatch(target_manifest_sha256) is None
                or not isinstance(patches, list) or not patches
                or not all(isinstance(name, str) and name.endswith(".patch") for name in patches)
                or len(set(patches)) != len(patches)
                or not isinstance(train, str) or not train
                or not isinstance(adapter_id, str) or not adapter_id):
            raise DriftError(f"invalid adapter metadata for supported build {version!r}")
        if install_bundle_sha256 is not None and (
            not isinstance(install_bundle_sha256, str)
            or SHA256_RE.fullmatch(install_bundle_sha256) is None
        ):
            raise DriftError(f"invalid install bundle digest for supported build {version!r}")
        adapter_root = PATCH_ROOT / directory
        target_path = adapter_root / "targets.sha256"
        if not target_path.is_file():
            raise DriftError(f"supported build {version} has no target manifest: {target_path}")
        actual_manifest_hash = sha256_file(target_path)
        if actual_manifest_hash != target_manifest_sha256:
            raise DriftError(
                f"supported-build manifest drift for {version}: targets.sha256 "
                f"expected={target_manifest_sha256} actual={actual_manifest_hash}"
            )
        targets = read_target_hashes(target_path)
        if len(targets) != target_count:
            raise DriftError(
                f"supported-build manifest drift for {version}: target count "
                f"expected={target_count} actual={len(targets)}"
            )
        adapter_manifest_path = adapter_root / "adapter.json"
        try:
            adapter_manifest = json.loads(adapter_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DriftError(f"invalid exact-build adapter manifest {adapter_manifest_path}: {exc}") from exc
        if not isinstance(adapter_manifest, dict) or (
            adapter_manifest.get("id") != adapter_id
            or adapter_manifest.get("version") != version
            or adapter_manifest.get("train") != train
            or adapter_manifest.get("target_count") != target_count
            or adapter_manifest.get("target_manifest_sha256") != target_manifest_sha256
        ):
            raise DriftError(f"adapter manifest does not match supported-build entry for {version}")
        for patch_name in patches:
            patch_path = adapter_root / patch_name
            if not patch_path.is_file() or patch_path.is_symlink():
                raise DriftError(f"missing or unsafe adapter patch for {version}: {patch_path}")
        result[version] = {
            **row,
            "version": version,
            "adapter_root": adapter_root,
            "targets": targets,
            "adapter_manifest": adapter_manifest,
        }
    return result


def get_supported_build(version: str) -> dict[str, Any]:
    builds = load_supported_builds()
    try:
        return builds[version]
    except KeyError as exc:
        allowed = ", ".join(sorted(builds))
        raise DriftError(f"unsupported TrueNAS build {version!r}; registered builds: {allowed}") from exc


def compare_target_hashes(expected: dict[str, str], actual: dict[str, str | None]) -> list[HashIssue]:
    issues: list[HashIssue] = []
    for name in sorted(expected):
        observed = actual.get(name)
        if observed != expected[name]:
            issues.append(HashIssue(name, expected[name], observed))
    for name in sorted(set(actual) - set(expected)):
        issues.append(HashIssue(name, "<not-in-supported-build>", actual[name]))
    return issues


def render_issues(issues: list[HashIssue]) -> str:
    lines: list[str] = []
    for issue in issues:
        if issue.actual is None:
            lines.append(f"MISSING {issue.path} expected={issue.expected} actual=<missing>")
        elif issue.expected == "<not-in-supported-build>":
            lines.append(f"UNEXPECTED {issue.path} expected=<not-in-supported-build> actual={issue.actual}")
        else:
            lines.append(f"HASH {issue.path} expected={issue.expected} actual={issue.actual}")
    return "\n".join(lines)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_target_tree(expected: dict[str, str], root: Path) -> list[HashIssue]:
    actual: dict[str, str | None] = {}
    for name in expected:
        relative = PurePosixPath(name)
        path = root.joinpath(*relative.parts)
        current = root
        unsafe = False
        for part in relative.parts:
            current /= part
            unsafe = unsafe or current.is_symlink()
        if unsafe or not path.is_file():
            actual[name] = None
        else:
            actual[name] = sha256_file(path)
    return compare_target_hashes(expected, actual)


def patch_dry_run_argv(patch_path: Path, *, reverse: bool = False) -> list[str]:
    argv = ["patch", "--dry-run", "--batch", "--fuzz=0", "-p1"]
    if reverse:
        argv.append("--reverse")
    return [*argv, "-i", str(patch_path)]


def verify_build(
    version: str,
    root: Path,
    *,
    run_patch: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    build = get_supported_build(version)
    issues = inspect_target_tree(build["targets"], root)
    if issues:
        raise DriftError(
            f"pre-patch source fingerprint mismatch for {version}; refusing every patch hunk:\n"
            + render_issues(issues)
        )
    for patch_name in build["patches"]:
        patch_path = build["adapter_root"] / patch_name
        result = run_patch(
            patch_dry_run_argv(patch_path),
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise DriftError(
                f"zero-fuzz patch dry-run failed for {version} ({patch_name}); "
                f"pre-patch hashes matched, but patch anchors differ:\n{detail}"
            )
    return [f"PASS {version}: {len(build['targets'])} pre-patch target hashes and "
            f"{len(build['patches'])} zero-fuzz patch dry-runs match"]


def verify_installed_build(
    version: str,
    root: Path,
    record_path: Path,
    *,
    run_patch: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    build = get_supported_build(version)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriftError(f"cannot read installed adapter record {record_path}: {exc}") from exc
    if not isinstance(record, dict) or record.get("version") != version:
        raise DriftError(f"installed adapter record does not identify exact build {version}")
    target_after = record.get("target_after")
    if not isinstance(target_after, dict) or set(target_after) != set(build["targets"]):
        raise DriftError(
            f"installed adapter record has an incomplete target_after map for {version}; "
            f"expected {len(build['targets'])} exact targets"
        )
    issues = inspect_target_tree(target_after, root)
    if issues:
        raise DriftError(
            f"installed source hash mismatch for {version}; adapter self-test failed:\n"
            + render_issues(issues)
        )
    helpers = record.get("helpers")
    if not isinstance(helpers, dict) or not helpers:
        raise DriftError(f"installed adapter record has no helper hashes for {version}")
    helper_issues: list[HashIssue] = []
    for name, digest in sorted(helpers.items()):
        target = PurePosixPath(name) if isinstance(name, str) else PurePosixPath("invalid")
        path = root.joinpath(*target.parts)
        actual = sha256_file(path) if not target.is_absolute() and ".." not in target.parts and path.is_file() and not path.is_symlink() else None
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or actual != digest:
            helper_issues.append(HashIssue(str(name), str(digest), actual))
    if helper_issues:
        raise DriftError(
            f"installed helper hash mismatch for {version}; adapter self-test failed:\n"
            + render_issues(helper_issues)
        )
    for patch_name in build["patches"]:
        patch_path = build["adapter_root"] / patch_name
        result = run_patch(
            patch_dry_run_argv(patch_path, reverse=True),
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise DriftError(
                f"reverse zero-fuzz patch self-test failed for {version} ({patch_name}):\n{detail}"
            )
    return [f"PASS {version}: {len(target_after)} installed target hashes, "
            f"{len(helpers)} helper hashes, and reverse zero-fuzz patch dry-runs match"]


def diagnose_candidate(
    reference_build: str,
    root: Path,
    candidate_version: str,
    *,
    run_patch: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Compare an unregistered candidate tree with a named adapter, without edits."""
    reference = get_supported_build(reference_build)
    registered = candidate_version in load_supported_builds()
    print(f"candidate_version={candidate_version or '<unspecified>'}")
    print(f"reference_build={reference_build} target_count={len(reference['targets'])}")
    if candidate_version and not registered:
        print(f"UNSUPPORTED_BUILD {candidate_version}: no adapter is registered; this diagnostic does not authorize apply")
    issues = inspect_target_tree(reference["targets"], root)
    if issues:
        print("PREIMAGE-DIFFS:")
        print(render_issues(issues))
    else:
        print("PREIMAGE-MATCH: every reference target hash matches")
    failed_patches: list[str] = []
    for patch_name in reference["patches"]:
        patch_path = reference["adapter_root"] / patch_name
        result = run_patch(
            patch_dry_run_argv(patch_path), cwd=root, text=True,
            capture_output=True, check=False,
        )
        if result.returncode:
            failed_patches.append(patch_name)
            detail = (result.stderr or result.stdout).strip()
            print(f"ANCHOR-DIFF {patch_name}: {detail}")
        else:
            print(f"ANCHORS-MATCH {patch_name}")
    if issues or failed_patches or (candidate_version and not registered):
        print("RESULT=REVIEW_REQUIRED; diagnostic was read-only and cannot authorize installation")
        return 2
    print("RESULT=REFERENCE_MATCH; exact-build registration is still required before installation")
    return 0


def latest_official_iso(html: str, series: int) -> str:
    parser = LinkParser()
    parser.feed(html)
    prefix = f"TrueNAS-{series}.0.0-"
    names = {
        Path(link).name for link in parser.links
        if Path(link).name.startswith(prefix) and Path(link).name.endswith(".iso")
        and not Path(link).name.endswith(".iso.sha256")
    }
    # download.truenas.com serves a Storj directory page. Raw HTML contains
    # the shell only; the managed browser renders object names as visible text.
    names.update(re.findall(
        rf"(?<![A-Za-z0-9_.+-]){re.escape(prefix)}[A-Za-z0-9_.+-]+\.iso(?![A-Za-z0-9_.+-])",
        html,
    ))
    if not names:
        raise DriftError(f"official download directory has no ISO entries for TrueNAS {series}")
    timed = [name for name in names if re.search(r"\d{8}-\d{6}\.iso$", name)]
    return max(timed or names)


def latest_official_rc_directory(rendered_text: str, series: int) -> str:
    pattern = re.compile(rf"(?<![A-Za-z0-9_.+-]){series}\.0\.0-RC\.(\d+)/")
    revisions = [int(match.group(1)) for match in pattern.finditer(rendered_text)]
    if not revisions:
        raise DriftError(f"official TrueNAS {series} RC listing has no release-candidate directories")
    return f"{series}.0.0-RC.{max(revisions)}/"


def rendered_official_directory(url: str) -> str:
    """Read a JavaScript-rendered Storj listing through the configured PWMCP."""
    pwmcp = shutil.which("pwmcp")
    if pwmcp is None:
        raise DriftError(
            "official download page is a JavaScript-rendered Storj listing and raw HTML has no ISO names; "
            "install/configure PWMCP and retry (pwmcp inspect is required only for this listing)"
        )
    with tempfile.TemporaryDirectory(prefix="truenas-official-listing-") as temporary:
        output = Path(temporary) / "listing.txt"
        result = subprocess.run(
            [pwmcp, "inspect", url, "--wait-until", "networkidle", "--wait-for", 'a[href*=".iso"]',
             "--timeout-ms", "60000",
             "--format", "text", "--output", str(output)],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise DriftError(f"PWMCP could not render official download listing {url}: {detail}")
        try:
            rendered = output.read_text(encoding="utf-8")
        except OSError as exc:
            raise DriftError(f"PWMCP returned success without rendered listing text for {url}: {exc}") from exc
        if not rendered.strip():
            raise DriftError(f"PWMCP rendered an empty official download listing for {url}")
        return rendered


def official_latest_iso(channel: str, index_url: str, series: int) -> str:
    raw_listing = fetch_url(index_url).decode("utf-8", "replace")
    try:
        return latest_official_iso(raw_listing, series)
    except DriftError:
        pass
    rendered = rendered_official_directory(index_url)
    try:
        return latest_official_iso(rendered, series)
    except DriftError:
        if not channel.endswith("-RC"):
            raise
    rc_directory = latest_official_rc_directory(rendered, series)
    candidate_url = index_url.rstrip("/") + "/" + rc_directory
    candidate_raw = fetch_url(candidate_url).decode("utf-8", "replace")
    try:
        iso = latest_official_iso(candidate_raw, series)
    except DriftError:
        iso = latest_official_iso(rendered_official_directory(candidate_url), series)
    return rc_directory + iso


def official_index_url(channel: str) -> tuple[str, int]:
    match = re.fullmatch(r"(\d+)-(Nightlies|RC)", channel)
    if match is None:
        raise DriftError("channel must be like 27-Nightlies or 27-RC")
    series, release_type = int(match.group(1)), match.group(2)
    directory = f"TrueNAS-{series}-Nightlies" if release_type == "Nightlies" else f"TrueNAS-{series}-RC"
    return f"{DOWNLOAD_ROOT}/{directory}/", series


def upstream_source_location(target: str) -> tuple[str, str]:
    if target.startswith("middlewared/"):
        return "middleware", f"src/middlewared/{target}"
    if target.startswith("truenas_pylibvirt/"):
        return "truenas_pylibvirt", target
    raise DriftError(f"no official source repository mapping for adapter target {target!r}")


def fetch_url(url: str, *, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": "vbpub-truenas-patch-drift/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise DriftError(f"official source fetch failed for {url}: {exc}") from exc


def upstream_check(channel: str, reference_build: str, source_ref: str = "master") -> int:
    build = get_supported_build(reference_build)
    index_url, series = official_index_url(channel)
    if not build["train"].startswith(f"TrueNAS-{series}-"):
        raise DriftError(f"reference build {reference_build} does not belong to channel {channel}")
    latest_iso = official_latest_iso(channel, index_url, series)
    print(f"official_channel={channel} latest_iso={latest_iso}")
    source_repositories = sorted({upstream_source_location(name)[0] for name in build["targets"]})
    print(f"source_ref={source_ref}")
    for repository in source_repositories:
        print(f"source_repository[{repository}]=https://github.com/truenas/{repository}")
        source_sha = "unknown"
        try:
            commit_value = json.loads(fetch_url(
                f"https://api.github.com/repos/truenas/{repository}/commits/{source_ref}"
            ).decode("utf-8"))
            if isinstance(commit_value, dict) and isinstance(commit_value.get("sha"), str):
                source_sha = commit_value["sha"]
        except (DriftError, json.JSONDecodeError):
            pass
        print(f"source_commit[{repository}]={source_sha}")

    with tempfile.TemporaryDirectory(prefix="truenas-patch-source-") as temporary:
        root = Path(temporary)
        actual: dict[str, str | None] = {}
        for target in sorted(build["targets"]):
            repository, relpath = upstream_source_location(target)
            path = root.joinpath(*PurePosixPath(target).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"{SOURCE_ROOTS[repository]}/{source_ref}/{relpath}"
            try:
                content = fetch_url(url)
            except DriftError as exc:
                actual[target] = None
                print(f"SOURCE-MISSING {target}: {exc}")
                continue
            path.write_bytes(content)
            actual[target] = hashlib.sha256(content).hexdigest()
        issues = compare_target_hashes(build["targets"], actual)
        if issues:
            print("SOURCE-HASH-DIFFS:")
            print(render_issues(issues))
        else:
            print("SOURCE-HASHES-MATCH: every target matches the supported build")

        failed_patches: list[str] = []
        for patch_name in build["patches"]:
            patch_path = build["adapter_root"] / patch_name
            result = subprocess.run(
                patch_dry_run_argv(patch_path), cwd=root, text=True,
                capture_output=True, check=False,
            )
            if result.returncode:
                failed_patches.append(patch_name)
                print(f"ANCHOR-DRIFT {patch_name}: {(result.stderr or result.stdout).strip()}")
        if not failed_patches:
            print(f"ZERO-FUZZ-ANCHORS-PASS: {len(build['patches'])} patch files")
    if issues or failed_patches:
        print("RESULT=REVIEW_REQUIRED; source HEAD is advisory and does not add a supported release build")
        return 2
    print("RESULT=ANCHORS_MATCH; source HEAD is advisory and does not add a supported release build")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="verify an extracted pre-patch target tree without changing it")
    verify.add_argument("--version", required=True, help="exact registered TrueNAS build string")
    verify.add_argument("--root", type=Path, required=True, help="middlewared Python package root")
    verify.add_argument("--installed-manifest", type=Path,
                        help="check post-apply hashes and reverse patches from this install record")
    diagnose = subparsers.add_parser("diagnose", help="report target and anchor drift for an unregistered build")
    diagnose.add_argument("--candidate-version", default="",
                          help="candidate build string; unregistered values remain blocked")
    diagnose.add_argument("--reference-build", required=True,
                          help="closest exact registered adapter whose hashes/patches are compared")
    diagnose.add_argument("--root", type=Path, required=True,
                          help="candidate middleware Python package root")
    upstream = subparsers.add_parser("upstream", help="compare a supported adapter with official source HEAD")
    upstream.add_argument("--channel", required=True, help="official directory, e.g. 27-Nightlies")
    upstream.add_argument("--reference-build", required=True, help="last exact adapter to use as the patch reference")
    upstream.add_argument("--source-ref", default="master", help="TrueNAS middleware source ref (default: master)")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            check = (verify_installed_build(args.version, args.root, args.installed_manifest)
                     if args.installed_manifest else verify_build(args.version, args.root))
            for line in check:
                print(line)
            print("RESULT=PASS; no files were changed")
            return 0
        if args.command == "diagnose":
            return diagnose_candidate(args.reference_build, args.root, args.candidate_version)
        return upstream_check(args.channel, args.reference_build, args.source_ref)
    except DriftError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
