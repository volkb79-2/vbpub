from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cli_extended.parser import UsageError

PATCHER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCHER_DIR))
import cli  # noqa: E402


def test_cli_declares_discovery_download_local_and_upstream_create_routes():
    app = cli.build_cli()

    assert app.parse_args(["releases"]).verb == "releases"
    assert app.parse_args(["browse", "--family", "TrueNAS-28-Nightlies"]).verb == "browse"
    download = app.parse_args(
        [
            "download",
            "--upstream-version",
            "27.0.0-RC.1",
            "--family",
            "TrueNAS-27-RC",
            "--output",
            "/tmp/TrueNAS.iso",
            "--yes",
        ]
    )
    assert download.upstream_version == "27.0.0-RC.1"
    assert download.output == Path("/tmp/TrueNAS.iso")

    upstream_create = app.parse_args(
        [
            "create",
            "--upstream-version",
            "27.0.0-RC.1",
            "--family",
            "TrueNAS-27-RC",
            "--output-iso",
            "/tmp/patched.iso",
            "--delta-output",
            "/tmp/patched.vcdiff",
            "--boot-pool-size-gib",
            "16",
            "--yes",
        ]
    )
    assert upstream_create.upstream_version == "27.0.0-RC.1"
    assert upstream_create.boot_pool_size_gib == 16
    assert upstream_create.flavor is None

    local_create = app.parse_args(
        [
            "create",
            "--input-iso",
            "/tmp/vanilla.iso",
            "--expected-input-sha256",
            "a" * 64,
            "--output-iso",
            "/tmp/patched.iso",
            "--delta-output",
            "/tmp/patched.vcdiff",
            "--boot-pool-size-gib",
            "24",
        ]
    )
    assert local_create.input_iso == Path("/tmp/vanilla.iso")
    assert local_create.expected_input_sha256 == "a" * 64

    unattended_create = app.parse_args(
        [
            "create",
            "--input-iso",
            "/tmp/vanilla.iso",
            "--expected-input-sha256",
            "a" * 64,
            "--output-iso",
            "/tmp/unattended.iso",
            "--delta-output",
            "/tmp/unattended.vcdiff",
            "--boot-pool-size-gib",
            "16",
            "--flavor",
            "unattended",
        ]
    )
    assert unattended_create.flavor == "unattended"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["create", "--output-iso", "/tmp/out.iso", "--delta-output", "/tmp/out.vcdiff", "--boot-pool-size-gib", "16"],
            "one of the arguments --upstream-version --input-iso is required",
        ),
        (
            ["create", "--upstream-version", "27.0.0-RC.1", "--family", "TrueNAS-27-RC", "--output-iso", "/tmp/out.iso", "--delta-output", "/tmp/out.vcdiff", "--boot-pool-size-gib", "15"],
            "boot-pool size must be an integer from 16 to 1024 GiB",
        ),
        (
            ["create", "--input-iso", "/tmp/in.iso", "--expected-input-sha256", "a" * 64, "--output-iso", "/tmp/out.iso", "--delta-output", "/tmp/out.vcdiff", "--boot-pool-size-gib", "16", "--flavor", "unknown"],
            "invalid choice",
        ),
    ],
)
def test_cli_rejects_missing_or_conflicting_inputs(argv, message):
    with pytest.raises(UsageError) as exc:
        cli.build_cli().parse_args(argv)
    assert message in str(exc.value)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["create", "--input-iso", "/tmp/in.iso", "--output-iso", "/tmp/out.iso", "--delta-output", "/tmp/out.vcdiff", "--boot-pool-size-gib", "16"],
            "local ISO mode requires both --input-iso and --expected-input-sha256",
        ),
        (
            ["create", "--upstream-version", "27.0.0-RC.1", "--expected-input-sha256", "a" * 64, "--output-iso", "/tmp/out.iso", "--delta-output", "/tmp/out.vcdiff", "--boot-pool-size-gib", "16"],
            "--expected-input-sha256 can only be used with --input-iso",
        ),
    ],
)
def test_cli_refuses_source_option_misuse_before_preflight(argv, message):
    args = cli.build_cli().parse_args(argv)

    with pytest.raises(cli.CliFailure, match=message):
        cli._create(args, runtime=None)
