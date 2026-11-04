from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import sys
from urllib.error import HTTPError

import pytest


ACCEPTANCE = Path(__file__).parents[1] / "acceptance"
PATCH_ROOT = Path(__file__).parents[1]
SCRIPT = ACCEPTANCE / "patch_drift.py"


def load_script():
    spec = importlib.util.spec_from_file_location("truenas_patch_drift", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_supported_build_manifest_covers_only_exact_registered_adapters() -> None:
    drift = load_script()
    builds = drift.load_supported_builds()

    assert set(builds) == {
        "27.0.0-RC.1",
        "27.0.0-MASTER+20261029-173417",
        "28.0.0-MASTER+20261029-175210",
    }
    for version, build in builds.items():
        manifest = PATCH_ROOT / build["directory"] / "targets.sha256"
        assert build["target_count"] == len(drift.read_target_hashes(manifest))
        assert hashlib.sha256(manifest.read_bytes()).hexdigest() == build["target_manifest_sha256"]
        assert build["patches"]
        assert all((build["series_root"] / name).is_file() for name in build["patches"])
        assert all((PATCH_ROOT / build["directory"] / "legacy" / name).is_file()
                   for name in build["legacy_patches"])


def test_unknown_or_unlisted_build_fails_with_exact_supported_build_list() -> None:
    drift = load_script()

    with pytest.raises(drift.DriftError, match=r"27.0.0-MASTER\+20261029-173417"):
        drift.get_supported_build("27.0.0-RC.2")

    with pytest.raises(drift.DriftError, match=r"28.0.0-MASTER\+20261029-175210"):
        drift.get_supported_build("27.0.0-GA")

    with pytest.raises(drift.DriftError, match=r"28.0.0-MASTER\+20261029-175210"):
        drift.get_supported_build("28.0.0-MASTER+20261027-175229")


def test_preimage_comparison_reports_every_changed_and_missing_target() -> None:
    drift = load_script()
    expected = {
        "middlewared/plugins/pool_/dataset.py": "a" * 64,
        "middlewared/plugins/zfs/utils.py": "b" * 64,
        "middlewared/plugins/vm/info.py": "c" * 64,
    }
    actual = {
        "middlewared/plugins/pool_/dataset.py": "a" * 64,
        "middlewared/plugins/zfs/utils.py": "d" * 64,
    }

    issues = drift.compare_target_hashes(expected, actual)

    assert [(issue.path, issue.expected, issue.actual) for issue in issues] == [
        ("middlewared/plugins/vm/info.py", "c" * 64, None),
        ("middlewared/plugins/zfs/utils.py", "b" * 64, "d" * 64),
    ]
    rendered = drift.render_issues(issues)
    assert "MISSING middlewared/plugins/vm/info.py" in rendered
    assert f"HASH middlewared/plugins/zfs/utils.py expected={'b' * 64} actual={'d' * 64}" in rendered


def test_patch_dry_run_is_zero_fuzz_and_never_an_apply() -> None:
    drift = load_script()

    argv = drift.patch_dry_run_argv(Path("/tmp/patch.diff"))

    assert argv == ["patch", "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", "/tmp/patch.diff"]
    assert "--dry-run" in argv
    assert not any(arg == "--batch" and index + 1 < len(argv) and argv[index + 1] == "-i"
                   for index, arg in enumerate(argv))


def test_hash_mismatch_stops_before_patch_hunks_are_considered(tmp_path: Path) -> None:
    drift = load_script()
    expected = {"middlewared/plugins/example.py": hashlib.sha256(b"known\n").hexdigest()}
    (tmp_path / "middlewared/plugins").mkdir(parents=True)
    (tmp_path / "middlewared/plugins/example.py").write_text("drifted\n")

    issues = drift.inspect_target_tree(expected, tmp_path)

    assert len(issues) == 1
    assert issues[0].actual == hashlib.sha256(b"drifted\n").hexdigest()


def test_unknown_candidate_diagnostic_reports_precise_diff_without_mutating(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch,
                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    drift = load_script()
    candidate = tmp_path / "middlewared/plugins/example.py"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("candidate source\n", encoding="utf-8")
    before = candidate.read_bytes()
    reference = {
        "version": "27.0.0-MASTER+20261027-173422",
        "targets": {"middlewared/plugins/example.py": hashlib.sha256(b"supported source\n").hexdigest()},
        "patches": ["example.patch"],
        "series_root": tmp_path / "adapter" / "series",
        "adapter_root": tmp_path / "adapter",
    }
    monkeypatch.setattr(drift, "get_supported_build", lambda _version: reference)
    monkeypatch.setattr(drift, "load_supported_builds", lambda: {})
    monkeypatch.setattr(
        drift, "patch_dry_run_argv", lambda _path: ["patch", "--dry-run", "--fuzz=0"]
    )

    status = drift.diagnose_candidate(
        "27.0.0-MASTER+20261027-173422", tmp_path, "27.0.0-RC.2",
        run_patch=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    output = capsys.readouterr().out
    assert status == 2
    assert "UNSUPPORTED_BUILD 27.0.0-RC.2" in output
    assert "HASH middlewared/plugins/example.py" in output
    assert candidate.read_bytes() == before


def test_official_directory_parser_selects_latest_exact_iso_and_ignores_checksums() -> None:
    drift = load_script()
    html = """
      <a href="TrueNAS-27.0.0-MASTER+20261027-173422.iso">latest</a>
      <a href="TrueNAS-27.0.0-MASTER+20261027-173422.iso.sha256">checksum</a>
      <a href="TrueNAS-27.0.0-MASTER+20261025-173420.iso">older</a>
    """

    assert drift.latest_official_iso(html, series=27) == "TrueNAS-27.0.0-MASTER+20261027-173422.iso"


def test_official_directory_parser_strips_storj_query_suffixes() -> None:
    drift = load_script()
    html = '''
      <a href="./TrueNAS-27.0.0-MASTER+20261029-173417.iso?wrap=1">latest</a>
      <a href="./TrueNAS-27.0.0-MASTER+20261029-173417.iso.sha256?download=1">checksum</a>
    '''

    assert drift.latest_official_iso(html, series=27) == "TrueNAS-27.0.0-MASTER+20261029-173417.iso"


def test_official_updater_feed_selects_latest_valid_release(monkeypatch: pytest.MonkeyPatch) -> None:
    drift = load_script()
    rows = {
        "27.0.0-MASTER+20261028-173400": {
            "version": "27.0.0-MASTER+20261028-173400", "filename": "older.update",
            "profile": "DEVELOPER", "checksum": "a" * 64, "filesize": 10,
        },
        "27.0.0-MASTER+20261029-173417": {
            "version": "27.0.0-MASTER+20261029-173417", "filename": "latest.update",
            "profile": "DEVELOPER", "checksum": "b" * 64, "filesize": 20,
        },
    }
    observed: list[str] = []

    def fake_fetch(url: str) -> bytes:
        observed.append(url)
        return __import__("json").dumps(rows).encode()

    monkeypatch.setattr(drift, "fetch_url", fake_fetch)

    latest = drift.official_latest_update("27-Nightlies")

    assert latest["version"] == "27.0.0-MASTER+20261029-173417"
    assert latest["train"] == "TrueNAS-27-Nightlies"
    assert latest["checksum"] == "b" * 64
    assert observed == ["https://auto-public.sys.truenas.net/TrueNAS-27-Nightlies/releases.json"]


def test_fetch_url_retries_only_bounded_http_503(monkeypatch: pytest.MonkeyPatch) -> None:
    drift = load_script()
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise HTTPError("https://example.test/feed", 503, "Service Unavailable", {}, io.BytesIO(b""))
        return io.BytesIO(b"ok")

    sleeps: list[int] = []
    monkeypatch.setattr(drift, "urlopen", fake_urlopen)
    monkeypatch.setattr(drift.time, "sleep", sleeps.append)

    assert drift.fetch_url("https://example.test/feed") == b"ok"
    assert calls == 3
    assert sleeps == [1, 2]


def test_official_directory_parser_handles_rendered_storj_listing_text() -> None:
    drift = load_script()
    rendered_text = """
      TrueNAS-27.0.0-MASTER+20261026-173525.iso\n2.71 GB
      TrueNAS-27.0.0-MASTER+20261027-173422.iso.sha256\n64 B
      TrueNAS-27.0.0-MASTER+20261027-173422.iso\n2.71 GB
    """

    assert drift.latest_official_iso(rendered_text, series=27) == "TrueNAS-27.0.0-MASTER+20261027-173422.iso"


def test_official_rc_directory_selects_highest_numbered_candidate() -> None:
    drift = load_script()

    assert drift.latest_official_rc_directory(
        "27.0.0-RC.1/\n27.0.0-RC.2/\n", series=27
    ) == "27.0.0-RC.2/"

    with pytest.raises(drift.DriftError, match="no release-candidate directories"):
        drift.latest_official_rc_directory("No objects found.", series=27)


def test_official_rc_root_resolves_latest_versioned_iso(monkeypatch: pytest.MonkeyPatch) -> None:
    drift = load_script()
    calls: list[str] = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        return b"<html>Storj shell</html>"

    def fake_render(url: str) -> str:
        calls.append(url)
        if url.endswith("TrueNAS-27-RC/"):
            return "27.0.0-RC.1/\n27.0.0-RC.2/"
        return "TrueNAS-27.0.0-RC.2.iso\n64 B"

    monkeypatch.setattr(drift, "fetch_url", fake_fetch)
    monkeypatch.setattr(drift, "rendered_official_directory", fake_render)

    result = drift.official_latest_iso(
        "27-RC", "https://download.truenas.com/TrueNAS-27-RC/", 27
    )

    assert result == "27.0.0-RC.2/TrueNAS-27.0.0-RC.2.iso"
    assert any(url.endswith("27.0.0-RC.2/") for url in calls)


def test_upstream_source_paths_use_each_true_nas_repository() -> None:
    drift = load_script()

    assert drift.upstream_source_location("middlewared/plugins/pool_/dataset.py") == (
        "middleware", "src/middlewared/middlewared/plugins/pool_/dataset.py"
    )
    assert drift.upstream_source_location("truenas_pylibvirt/domain/vm/xml.py") == (
        "truenas_pylibvirt", "truenas_pylibvirt/domain/vm/xml.py"
    )


def test_rendered_official_directory_uses_configured_pwmcp(monkeypatch: pytest.MonkeyPatch) -> None:
    drift = load_script()
    monkeypatch.setattr(drift.shutil, "which", lambda name: "/usr/bin/pwmcp" if name == "pwmcp" else None)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        output = Path(argv[argv.index("--output") + 1])
        output.write_text("TrueNAS-27.0.0-MASTER+20261027-173422.iso\\n2.71 GB", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(drift.subprocess, "run", fake_run)

    rendered = drift.rendered_official_directory("https://download.truenas.com/TrueNAS-27-Nightlies/")

    assert "TrueNAS-27.0.0-MASTER+20261027-173422.iso" in rendered
    assert "--wait-until" in observed["argv"]
    assert "networkidle" in observed["argv"]
    assert "--wait-for" in observed["argv"]
    assert "a[href]" in observed["argv"]
    assert observed["kwargs"]["timeout"] == 90
