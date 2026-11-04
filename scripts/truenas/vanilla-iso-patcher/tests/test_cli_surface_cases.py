from __future__ import annotations

from pathlib import Path
import io
import sys
import tomllib

import pytest
from cli_extended.parser import UsageError

PATCHER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATCHER_ROOT))
import cli  # noqa: E402


with (PATCHER_ROOT / "cli-review.toml").open("rb") as stream:
    _CATALOG = tomllib.load(stream)


def _marked_cases():
    for case in _CATALOG["cases"]:
        if case["state"] != "active":
            continue
        yield pytest.param(
            case,
            id=case["id"],
            marks=pytest.mark.cli_case(case["id"]),
        )


@pytest.mark.parametrize("case", list(_marked_cases()))
def test_reviewed_surface_case(case):
    app = cli.build_cli()
    expected = int(case["expected_exit_status"])
    invocation = list(case["invocation"])

    try:
        args = app.parse_args(invocation)
    except UsageError:
        assert expected == 2
        return

    if expected == 0:
        assert args.verb in {"browse", "create", "download", "releases"}
        if case["id"].endswith("/create/minimum"):
            assert args.flavor is None
        if "/create/option-choice/" in case["id"]:
            if "/--flavor/" in case["id"]:
                assert args.flavor == invocation[-1]
            elif "/--post-install-action/" in case["id"]:
                assert args.post_install_action == invocation[-1]
        return

    if "/constraint-" in case["id"]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        status = app.run(argv=invocation, stdout=stdout, stderr=stderr)
        assert status == expected
        assert case["expected_stderr_contains"] in stderr.getvalue()
        return

    with pytest.raises(cli.CliFailure) as exc:
        cli._create(args, runtime=None)
    assert exc.value.exit_code == expected


def test_post_install_action_requires_an_unattended_flavor():
    app = cli.build_cli()
    stderr = io.StringIO()
    status = app.run(argv=[
        "create", "--input-iso", "./TrueNAS.iso", "--expected-input-sha256", "a" * 64,
        "--output-iso", "./patched.iso", "--delta-output", "./patched.vcdiff",
        "--boot-pool-size-gib", "16", "--post-install-action", "poweroff",
    ], stdout=io.StringIO(), stderr=stderr)
    assert status == 2
    assert "post-install actions apply only to unattended installer flavors" in stderr.getvalue()
