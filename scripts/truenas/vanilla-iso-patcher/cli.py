"""The cli-extended command surface for building bounded-pool installer ISOs."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    Conflicts,
    OptionSpec,
    Requires,
    RequiresChoice,
    VerbGroup,
    VerbSpec,
)

import delta
import upstream
from patch_iso import PatcherError, patch_iso, validate_boot_pool_size
from iso_profiles import FLAVORS


HERE = Path(__file__).resolve().parent
IDENTITY = CliIdentity.resolve(
    name="TRUENAS VANILLA ISO PATCHER",
    command="truenas-vanilla-iso",
    long_name="TrueNAS bounded and unattended installer ISO generator",
    version_file=HERE / "VERSION",
)

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _boot_pool_size(value: str) -> int:
    try:
        number = int(value)
        validate_boot_pool_size(number)
    except (ValueError, PatcherError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return number


def _option(
    flags: tuple[str, ...],
    description: str,
    *,
    group: str = "OPTIONS",
    metavar: str | None = None,
    mutually_exclusive_group: str | None = None,
    mutually_exclusive_required: bool = False,
    **parser_kwargs: Any,
) -> OptionSpec:
    return OptionSpec(
        flags,
        description,
        group=group,
        metavar=metavar,
        parser_kwargs=parser_kwargs,
        mutually_exclusive_group=mutually_exclusive_group,
        mutually_exclusive_required=mutually_exclusive_required,
    )


def _asset_table(assets: list[upstream.UpstreamIso]) -> list[dict[str, str]]:
    return [
        {
            "family": asset.family,
            "version": asset.version,
            "filename": asset.filename,
            "release_id": asset.release_id,
            "iso_url": asset.iso_url,
            "checksum_url": asset.checksum_url,
        }
        for asset in assets
    ]


def _natural_version(version: str) -> tuple[tuple[int, str], ...]:
    return tuple(
        (0, f"{int(part):020d}") if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", version)
        if part
    )


def _sorted_assets(assets: list[upstream.UpstreamIso]) -> list[upstream.UpstreamIso]:
    return sorted(
        assets,
        key=lambda asset: (asset.family.casefold(), _natural_version(asset.version)),
        reverse=True,
    )


def _select_asset(args: Any, runtime: Any) -> upstream.UpstreamIso:
    assets = upstream.discover_releases()
    if args.family:
        assets = [asset for asset in assets if asset.family == args.family]
    if args.version_prefix:
        assets = [asset for asset in assets if asset.version.startswith(args.version_prefix)]
    if not assets:
        raise CliFailure("the official ISO catalog has no releases matching those filters")

    grouped: dict[str, list[upstream.UpstreamIso]] = defaultdict(list)
    for asset in assets:
        grouped[asset.family].append(asset)
    families = sorted(grouped, key=str.casefold)
    family = args.family or runtime.prompts.select("Select an upstream release family", families)
    family_assets = _sorted_assets(grouped[family])
    labels = tuple(f"{asset.version} — {asset.filename}" for asset in family_assets)
    chosen_label = runtime.prompts.select("Select the installer subversion", labels)
    return family_assets[labels.index(chosen_label)]


def _output_path(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.parent.is_dir():
        raise CliFailure(f"output directory does not exist: {resolved.parent}")
    if resolved.exists():
        raise CliFailure(f"output already exists; choose another path: {resolved}")
    return resolved


def _write_exclusive(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _save_download_checksum(iso_path: Path, digest: str) -> Path:
    sidecar_path = Path(str(iso_path) + ".sha256")
    if sidecar_path.exists():
        raise CliFailure(f"checksum sidecar already exists; choose another ISO path: {sidecar_path}")
    _write_exclusive(sidecar_path, f"{digest}  {iso_path.name}\n".encode("ascii"))
    return sidecar_path


def _download_asset(
    asset: upstream.UpstreamIso,
    output_path: Path,
    runtime: Any,
    *,
    checksum: str | None = None,
) -> tuple[Path, str]:
    output_path = _output_path(output_path)
    checksum = checksum or upstream.fetch_published_checksum(asset)
    runtime.output.info(f"TrueNAS published SHA-256: {checksum}")
    published_iso = False
    try:
        digest = upstream.download_verified_iso(
            asset,
            output_path,
            expected_sha256=checksum,
            progress=runtime.output.info,
        )
        published_iso = True
        checksum_path = _save_download_checksum(output_path, digest)
    except (upstream.UpstreamError, OSError, CliFailure) as exc:
        # If the ISO was linked successfully but its sidecar could not be
        # published, remove the ISO from this invocation rather than leave an
        # artifact that looks complete.
        if published_iso:
            output_path.unlink(missing_ok=True)
        if isinstance(exc, CliFailure):
            raise
        raise CliFailure(str(exc)) from exc
    runtime.output.info(f"Downloaded verified ISO: {output_path}")
    runtime.output.info(f"Checksum sidecar: {checksum_path}")
    return output_path, digest


def _releases(args: Any, runtime: Any) -> None:
    assets = upstream.discover_releases()
    if args.family:
        assets = [asset for asset in assets if asset.family == args.family]
    if args.version_prefix:
        assets = [asset for asset in assets if asset.version.startswith(args.version_prefix)]
    assets = _sorted_assets(assets)
    if runtime.json_mode:
        runtime.output.primary(_asset_table(assets))
        return
    if not assets:
        runtime.output.primary("No installer ISO entries with matching SHA-256 sidecars match those filters.")
        return
    rows = ["UPSTREAM FAMILY / VERSION | ISO FILE"]
    rows.extend(f"{asset.release_id} | {asset.filename}" for asset in assets)
    runtime.output.primary("\n".join(rows))


def _browse(args: Any, runtime: Any) -> None:
    asset = _select_asset(args, runtime)
    suggested = args.output or Path.cwd() / asset.filename
    if args.output is None:
        entered = runtime.prompts.text("Output path for the vanilla ISO", default=str(suggested))
        output_path = Path(entered).expanduser()
    else:
        output_path = args.output
    output_path = _output_path(output_path)
    checksum = upstream.fetch_published_checksum(asset)
    runtime.output.info(f"Selected: {asset.release_id} ({asset.filename})")
    runtime.output.info(f"Published SHA-256: {checksum}")
    if not runtime.confirm(f"Download this verified ISO to {output_path}?"):
        return
    _download_asset(asset, output_path, runtime, checksum=checksum)


def _download(args: Any, runtime: Any) -> None:
    assets = upstream.discover_releases()
    try:
        asset = upstream.resolve_release(assets, args.upstream_version, args.family)
        checksum = upstream.fetch_published_checksum(asset)
    except upstream.UpstreamError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    output_path = _output_path(args.output)
    runtime.output.info(f"Selected: {asset.release_id} ({asset.filename})")
    runtime.output.info(f"Published SHA-256: {checksum}")
    if not runtime.confirm(f"Download this verified ISO to {output_path}?"):
        return
    _download_asset(asset, output_path, runtime, checksum=checksum)


def _create(args: Any, runtime: Any) -> None:
    flavor = args.flavor or "bounded"
    post_install_action = args.post_install_action or "reboot"
    if args.input_iso is not None:
        if SHA256_RE.fullmatch(args.expected_input_sha256 or "") is None:
            raise CliFailure(
                "local ISO mode requires both --input-iso and --expected-input-sha256",
                exit_code=2,
            )
        if args.family:
            raise CliFailure("--family can only be used with --upstream-version", exit_code=2)
    elif args.expected_input_sha256:
        raise CliFailure(
            "--expected-input-sha256 can only be used with --input-iso", exit_code=2
        )

    try:
        validate_boot_pool_size(args.boot_pool_size_gib)
    except PatcherError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc

    output_iso = _output_path(args.output_iso)
    delta_output = _output_path(args.delta_output)
    manifest_output = Path(str(delta_output) + ".json")
    if output_iso in (delta_output, manifest_output) or delta_output == manifest_output:
        raise CliFailure("output ISO, delta, and delta manifest paths must be different", exit_code=2)
    if manifest_output.exists():
        raise CliFailure(f"delta manifest already exists: {manifest_output}")
    if not manifest_output.parent.is_dir():
        raise CliFailure(f"manifest directory does not exist: {manifest_output.parent}")

    try:
        xdelta_executable, xdelta_version = delta.resolve_xdelta(args.xdelta)
    except delta.DeltaError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    if os.geteuid() != 0:
        raise CliFailure("run create as root so SquashFS ownership and device metadata are preserved")
    for tool in ("xorriso", "unsquashfs", "mksquashfs"):
        if not shutil.which(tool):
            raise CliFailure(f"required tool {tool!r} is not installed or not on PATH")

    selected_asset: upstream.UpstreamIso | None = None
    input_iso: Path | None = None
    expected_sha256: str | None = None
    upstream_checksum: str | None = None
    try:
        if args.input_iso is not None:
            input_iso = args.input_iso.expanduser().resolve(strict=True)
            expected_sha256 = args.expected_input_sha256.lower()
            actual = delta.sha256_file(input_iso)
            if actual != expected_sha256:
                raise CliFailure(
                    f"input ISO SHA-256 mismatch: expected {expected_sha256}, got {actual}", exit_code=2
                )
            upstream_checksum = None
        else:
            releases = upstream.discover_releases()
            selected_asset = upstream.resolve_release(releases, args.upstream_version, args.family)
            upstream_checksum = upstream.fetch_published_checksum(selected_asset)
            input_iso = None
    except (upstream.UpstreamError, OSError) as exc:
        raise CliFailure(str(exc), exit_code=2) from exc

    release_text = selected_asset.release_id if selected_asset else str(input_iso)
    runtime.output.info(f"Selected source: {release_text}")
    if upstream_checksum:
        runtime.output.info(f"Published source SHA-256: {upstream_checksum}")
    runtime.output.info(f"Output ISO: {output_iso}")
    runtime.output.info(f"VCDIFF delta: {delta_output}")
    runtime.output.info(f"boot-pool partition size: {args.boot_pool_size_gib} GiB")
    if flavor != "bounded":
        runtime.output.info(
            "UNATTENDED FLAVOR: the default boot entry erases every accepted target disk without confirmation. "
            "Initial web administrator: truenas_admin / truenas; change this fixed password immediately after first boot."
        )
    if flavor == "unattended-relaxed":
        runtime.output.info(
            "EXPERIMENTAL: after install, this flavor applies the exact-release boot-pool adapter before reboot."
        )
    if not runtime.confirm("Create the patched ISO, verified xdelta patch, and Windows manifest?"):
        return

    created: list[Path] = []
    source_sha256: str
    source_filename: str
    source_url: str | None = None
    checksum_url: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix=".vanilla-iso-build-", dir=output_iso.parent) as work_name:
            work = Path(work_name)
            staged_iso = work / output_iso.name
            if selected_asset is not None:
                assert input_iso is None and upstream_checksum is not None
                input_iso = work / selected_asset.filename
                runtime.output.info(f"Downloading upstream ISO: {selected_asset.iso_url}")
                source_sha256 = upstream.download_verified_iso(
                    selected_asset,
                    input_iso,
                    expected_sha256=upstream_checksum,
                    progress=runtime.output.info,
                )
                source_filename = selected_asset.filename
                source_url = selected_asset.iso_url
                checksum_url = selected_asset.checksum_url
            else:
                assert input_iso is not None and expected_sha256 is not None
                source_sha256 = expected_sha256
                source_filename = input_iso.name

            runtime.output.info(
                f"Rebuilding installer ISO with the requested boot-pool partition size and {flavor} flavor."
            )
            patched = patch_iso(
                input_iso,
                staged_iso,
                args.boot_pool_size_gib,
                expected_sha256=source_sha256,
                flavor=flavor,
                post_install_action=post_install_action,
            )
            if patched["source_sha256"] != source_sha256:
                raise CliFailure("ISO generator did not preserve the verified source digest")

            with tempfile.TemporaryDirectory(prefix=".vanilla-iso-delta-", dir=delta_output.parent) as delta_name:
                delta_work = Path(delta_name)
                staged_delta = delta_work / delta_output.name
                delta_result = delta.create_and_verify_delta(
                    xdelta_executable,
                    input_iso,
                    staged_iso,
                    staged_delta,
                    progress=runtime.output.info,
                    expected_source_sha256=source_sha256,
                )
                target_bytes = staged_iso.stat().st_size
                manifest = {
                    "schema_version": 1,
                    "format": "VCDIFF",
                    "source": {
                        "filename": source_filename,
                        "bytes": input_iso.stat().st_size,
                        "sha256": source_sha256,
                        "url": source_url,
                        "checksum_url": checksum_url,
                    },
                    "target": {
                        "filename": output_iso.name,
                        "bytes": target_bytes,
                        "sha256": str(patched["output_sha256"]),
                        "boot_pool_size_gib": args.boot_pool_size_gib,
                        "flavor": flavor,
                        "post_install_action": post_install_action,
                        "relaxation_adapter": patched["relaxation_adapter"] or None,
                    },
                    "delta": {
                        "filename": delta_output.name,
                        "bytes": int(delta_result["delta_bytes"]),
                        "sha256": str(delta_result["delta_sha256"]),
                    },
                    "xdelta": {"version": xdelta_version},
                }
                if delta_result["xdelta_version"] != xdelta_version:
                    raise CliFailure("xdelta version changed between preflight and encode")

                with tempfile.TemporaryDirectory(prefix=".vanilla-iso-manifest-", dir=manifest_output.parent) as manifest_name:
                    manifest_work = Path(manifest_name)
                    staged_manifest = manifest_work / manifest_output.name
                    with staged_manifest.open("x", encoding="utf-8") as stream:
                        json.dump(manifest, stream, indent=2, sort_keys=True)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())

                    for staged, destination in (
                        (staged_iso, output_iso),
                        (staged_delta, delta_output),
                        (staged_manifest, manifest_output),
                    ):
                        try:
                            os.link(staged, destination)
                        except FileExistsError as exc:
                            raise CliFailure(f"output appeared during build; refusing to overwrite: {destination}") from exc
                        created.append(destination)
                    runtime.output.info(f"Patched ISO SHA-256: {patched['output_sha256']}")
                    runtime.output.info(f"Delta SHA-256: {delta_result['delta_sha256']}")
                    runtime.output.info(
                        f"Delta size: {delta_result['delta_bytes']:,} bytes "
                        f"({delta_result['delta_bytes'] / max(target_bytes, 1):.3%} of patched ISO)"
                    )
                    runtime.output.info(f"Manifest: {manifest_output}")
    except (PatcherError, upstream.UpstreamError, delta.DeltaError, OSError, CliFailure) as exc:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        if isinstance(exc, CliFailure):
            raise
        raise CliFailure(str(exc)) from exc


def build_cli() -> Any:
    registry = CliRegistry(
        IDENTITY,
        prog="truenas-vanilla-iso",
        description="Discover official TrueNAS ISOs, verify their checksums, and build bounded or unattended installation media.",
        getting_started=(
            "truenas-vanilla-iso releases",
            "truenas-vanilla-iso browse",
            "truenas-vanilla-iso create --help",
        ),
        logging_logger="truenas_vanilla_iso_patcher",
        unexpected_exceptions="report",
        identity_banner="never",
        error_help="usage",
        expected_exceptions=(upstream.UpstreamError, delta.DeltaError, PatcherError, OSError),
    )
    registry.register(
        VerbSpec(
            "releases",
            description="list upstream installer ISOs with published SHA-256 sidecar links",
            group=VerbGroup.EXPLORATION.value,
            options=(
                _option(("--family",), "limit results to one upstream release family", group="FILTERS", metavar="FAMILY"),
                _option(("--version-prefix",), "limit results to versions beginning with this text", group="FILTERS", metavar="PREFIX"),
            ),
            handler=_releases,
        )
    )
    registry.register(
        VerbSpec(
            "browse",
            description="interactively select an upstream release and download its verified ISO",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            examples=("truenas-vanilla-iso browse", "truenas-vanilla-iso browse --family TrueNAS-28-Nightlies --version-prefix 202610"),
            options=(
                _option(("--family",), "restrict interactive selection to one release family", group="FILTERS", metavar="FAMILY"),
                _option(("--version-prefix",), "restrict version choices to this prefix", group="FILTERS", metavar="PREFIX"),
                _option(("--output",), "destination path for the downloaded ISO", group="OUTPUT", metavar="ISO", type=_path),
            ),
            handler=_browse,
        )
    )
    registry.register(
        VerbSpec(
            "download",
            description="download an exact upstream version after verifying its published SHA-256",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            examples=("truenas-vanilla-iso download --upstream-version 27.0.0-RC.1 --family TrueNAS-27-RC --output ./TrueNAS-27.iso",),
            options=(
                _option(("--upstream-version",), "exact installer subversion to download", group="SELECTION", metavar="VERSION", required=True),
                _option(("--family",), "release family when the version is ambiguous", group="SELECTION", metavar="FAMILY"),
                _option(("--output",), "destination path for the downloaded ISO", group="OUTPUT", metavar="ISO", type=_path, required=True),
            ),
            handler=_download,
        )
    )
    registry.register(
        VerbSpec(
            "create",
            description="create bounded, unattended, or experimental unattended-relaxed installer media, a verified xdelta VCDIFF patch, and its Windows manifest",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            examples=(
                "truenas-vanilla-iso create --upstream-version 27.0.0-RC.1 --family TrueNAS-27-RC --output-iso ./TrueNAS-bounded.iso --delta-output ./TrueNAS-bounded.vcdiff --boot-pool-size-gib 16",
                "truenas-vanilla-iso create --input-iso ./TrueNAS.iso --expected-input-sha256 <sha256> --output-iso ./TrueNAS-unattended.iso --delta-output ./TrueNAS-unattended.vcdiff --boot-pool-size-gib 16 --flavor unattended",
                "truenas-vanilla-iso create --input-iso ./TrueNAS-27.iso --expected-input-sha256 <sha256> --output-iso ./TrueNAS-27-unattended-relaxed.iso --delta-output ./TrueNAS-27-unattended-relaxed.vcdiff --boot-pool-size-gib 16 --flavor unattended-relaxed --post-install-action poweroff",
            ),
            options=(
                _option(("--upstream-version",), "exact upstream installer subversion to download and patch", group="SOURCE", metavar="VERSION", mutually_exclusive_group="source", mutually_exclusive_required=True),
                _option(("--input-iso",), "local vanilla installer ISO to patch", group="SOURCE", metavar="ISO", type=_path, mutually_exclusive_group="source", mutually_exclusive_required=True),
                _option(("--family",), "upstream release family when the version is ambiguous", group="SOURCE", metavar="FAMILY"),
                _option(("--expected-input-sha256",), "required trusted SHA-256 for a locally supplied ISO", group="SOURCE", metavar="SHA256"),
                _option(("--output-iso",), "path for the patched installer ISO", group="OUTPUT", metavar="ISO", type=_path, required=True),
                _option(("--delta-output",), "path for the xdelta VCDIFF patch", group="OUTPUT", metavar="VCDIFF", type=_path, required=True),
                _option(("--boot-pool-size-gib",), "explicit partition-3 size in GiB (16–1024)", group="PATCH", metavar="GIB", type=_boot_pool_size, required=True),
                _option(("--flavor",), "ISO behavior: bounded (default), unattended, or experimental unattended-relaxed", group="PATCH", metavar="FLAVOR", choices=FLAVORS, default=None),
                _option(("--post-install-action",), "After a successful unattended install, reboot (default) or power off; poweroff supports provider-side ISO-detach handoff", group="PATCH", metavar="ACTION", choices=("reboot", "poweroff"), default=None),
                _option(("--xdelta",), "xdelta3 3.x executable; defaults to xdelta3 on PATH", group="TOOLS", metavar="PATH", type=_path),
            ),
            constraints=(
                Requires("--input-iso", ("--expected-input-sha256",), "local ISO input must be checksum-pinned"),
                Conflicts(("--upstream-version", "--expected-input-sha256"), "the published checksum is fetched for upstream releases"),
                Conflicts(("--input-iso", "--family"), "a local ISO has no upstream family selector"),
                RequiresChoice("--post-install-action", "--flavor", ("unattended", "unattended-relaxed"), "post-install actions apply only to unattended installer flavors"),
            ),
            handler=_create,
        )
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv, interactive_extra="cli-extended[interactive]")
