"""Public CLI-version contracts: one resolver, no literal fallback (AC-01, F7)."""
from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cli_extended import VersionLookupError  # noqa: E402
from cli_extended import identity as cx_identity  # noqa: E402
from ciu import cli, cli_utils  # noqa: E402


def _metadata_unavailable(_package: str) -> str:
    raise importlib.metadata.PackageNotFoundError("ciu")


def _install(monkeypatch: pytest.MonkeyPatch, *, metadata: str | None, built: str | None) -> None:
    if metadata is None:
        monkeypatch.setattr(cx_identity, "installed_version", _metadata_unavailable)
    else:
        monkeypatch.setattr(cx_identity, "installed_version", lambda _package: metadata)
    monkeypatch.setattr(cli_utils, "_built_version", lambda: built)


@pytest.mark.parametrize(
    ("metadata", "built", "expected"),
    [
        ("2.4.1", None, "2.4.1"),
        (None, "3.1.0", "3.1.0"),
        ("2.4.1", "2.4.1", "2.4.1"),
    ],
    ids=("installed-metadata", "built-tree-without-install", "both-agree"),
)
def test_cli_version_comes_from_metadata_and_or_the_build(
    monkeypatch: pytest.MonkeyPatch, metadata: str | None, built: str | None, expected: str,
) -> None:
    _install(monkeypatch, metadata=metadata, built=built)

    assert cli_utils.get_cli_version() == expected
    identity = cli_utils.ciu_identity()
    assert (identity.name, identity.command, identity.long_name) == (
        "CIU", "ciu", "Container Infrastructure Utility",
    )


def test_cli_version_sources_that_disagree_are_a_loud_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale editable install must never silently win over the build's version."""
    _install(monkeypatch, metadata="2.4.1", built="2.5.0")

    with pytest.raises(VersionLookupError, match="disagree"):
        cli_utils.get_cli_version()


def test_cli_version_has_no_literal_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A source tree that was neither built nor installed has NO version, not 'unknown'."""
    _install(monkeypatch, metadata=None, built=None)

    with pytest.raises(VersionLookupError):
        cli_utils.get_cli_version()
    assert not hasattr(sys.modules["ciu"], "__version__")


def test_main_exits_3_naming_the_cause_when_no_version_resolves(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Environment/bootstrap failure (SPEC S10.3): exit 3, one clear [ERROR] line."""
    _install(monkeypatch, metadata=None, built=None)
    monkeypatch.setattr(sys, "argv", ["ciu", "version"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert err.startswith("[ERROR] ciu is not installed as a distribution")


def test_built_version_probe_reads_the_generated_module(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    fake = types.ModuleType("ciu._version")
    fake.version = "9.9.9"
    monkeypatch.setitem(sys.modules, "ciu._version", fake)
    assert cli_utils._built_version() == "9.9.9"

    monkeypatch.setitem(sys.modules, "ciu._version", None)  # import raises ImportError
    assert cli_utils._built_version() is None
