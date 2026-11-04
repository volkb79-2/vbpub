"""Registry request economy for ``cmru versions`` (KI-121): newest-first OCI scan, 429 handling, resume."""
from __future__ import annotations

import json
import types
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

import pytest
from cli_extended import CliFailure

from cmru import version_registry as registry
from cmru import versions

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=30)
FRESH = NOW - timedelta(days=2)


def _http_error(code: int, **headers: str) -> urllib.error.HTTPError:
    message = Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return urllib.error.HTTPError("https://registry.example/x", code, "err", message, None)


# --- 429 classification and bounded retry ---------------------------------------------------

def test_rate_limited_error_reads_hub_and_retry_after_headers():
    hub = registry._rate_limited("https://hub/x", {"RateLimit-Limit": "100;w=3600", "ratelimit-remaining": "0;w=3600"})
    assert isinstance(hub, registry.RegistryRateLimitedError)
    assert hub.retry_after is None and hub.window == 3600
    assert "ratelimit-limit 100;w=3600" in str(hub) and "ratelimit-remaining 0;w=3600" in str(hub)
    retry = registry._rate_limited("https://hub/x", {"Retry-After": " 7 "})
    assert retry.retry_after == 7 and retry.window is None and "Retry-After 7s" in str(retry)
    bare = registry._rate_limited("https://hub/x", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert bare.retry_after is None and str(bare).endswith("https://hub/x")


def test_plain_request_maps_429_to_rate_limited(monkeypatch):
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(_http_error(429, Retry_After="5")))
    with pytest.raises(registry.RegistryRateLimitedError) as info:
        registry._request("https://registry.example/x")
    assert info.value.retry_after == 5


def _client() -> registry._OCIClient:
    return registry._OCIClient("https://registry.example", "acme/app", {})


def test_authorized_request_retries_short_retry_after_then_succeeds(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []

    def once(self, url, headers=None):
        calls.append(1)
        if len(calls) < 3:
            raise registry.RegistryRateLimitedError("slow down", retry_after=0)
        return b"ok", {}

    monkeypatch.setattr(registry._OCIClient, "_authorized_request_once", once)
    monkeypatch.setattr(registry, "_sleep", sleeps.append)
    assert _client()._authorized_request("https://registry.example/x") == (b"ok", {})
    assert len(calls) == 3 and sleeps == [1, 1]


@pytest.mark.parametrize("retry_after", [None, registry.MAX_RETRY_AFTER_SECONDS + 1, 2])
def test_authorized_request_surfaces_unretryable_or_exhausted_429(monkeypatch, retry_after):
    calls: list[int] = []
    sleeps: list[float] = []

    def once(self, url, headers=None):
        calls.append(1)
        raise registry.RegistryRateLimitedError("limited", retry_after=retry_after)

    monkeypatch.setattr(registry._OCIClient, "_authorized_request_once", once)
    monkeypatch.setattr(registry, "_sleep", sleeps.append)
    with pytest.raises(registry.RegistryRateLimitedError):
        _client()._authorized_request("https://registry.example/x")
    if retry_after == 2:
        assert len(calls) == registry.MAX_RATE_LIMIT_RETRIES + 1 and len(sleeps) == registry.MAX_RATE_LIMIT_RETRIES
    else:
        assert len(calls) == 1 and sleeps == []


def test_authorized_request_once_maps_429_before_auth_challenge(monkeypatch):
    monkeypatch.setattr(
        registry, "_urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(_http_error(429, ratelimit_remaining="0;w=3600", www_authenticate='Bearer realm="https://auth/x"')),
    )
    with pytest.raises(registry.RegistryRateLimitedError) as info:
        _client()._authorized_request_once("https://registry.example/x")
    assert info.value.window == 3600


# --- newest-first OCI scan ------------------------------------------------------------------

def _scan(monkeypatch, tags: list[str], stamps: dict[str, datetime], cutoff, constraint="*"):
    asked: list[str] = []
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: tags)

    def stamp(_self, tag):
        asked.append(tag)
        return stamps[tag], "fixture"

    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", stamp)
    found = registry.oci_candidates({"image": "ghcr.io/acme/app", "tag": "v{version}"}, constraint, cutoff)
    return found, asked


def test_cutoff_scan_stops_at_newest_eligible_version(monkeypatch):
    stamps = {f"v1.{minor}.0": OLD for minor in range(10)}
    stamps["v1.11.0"] = FRESH
    found, asked = _scan(
        monkeypatch, [*stamps, "latest"], stamps, NOW - timedelta(days=14),
    )
    assert asked == ["v1.11.0", "v1.9.0"]
    assert set(found) == {"1.11.0", "1.9.0"}


def test_cutoff_scan_examines_everything_when_nothing_is_eligible(monkeypatch):
    stamps = {"v1.0.0": FRESH, "v1.1.0": FRESH}
    found, asked = _scan(monkeypatch, list(stamps), stamps, NOW - timedelta(days=14))
    assert asked == ["v1.1.0", "v1.0.0"] and set(found) == {"1.0.0", "1.1.0"}


def test_cutoff_scan_finishes_a_group_of_equal_versions_before_stopping(monkeypatch):
    stamps = {"v1.0": FRESH, "v1.0.0": OLD, "v0.9.0": OLD}
    found, asked = _scan(monkeypatch, list(stamps), stamps, NOW - timedelta(days=14))
    assert sorted(asked) == ["v1.0", "v1.0.0"]
    assert set(found) == {"1.0", "1.0.0"}


def test_candidates_forwards_cutoff_only_to_oci(monkeypatch):
    seen: list[object] = []
    monkeypatch.setattr(registry, "oci_candidates", lambda source, constraint, cutoff=None: seen.append(cutoff) or {})
    assert registry.candidates("oci", {"image": "x/y", "tag": "{version}"}, "*", NOW) == {}
    assert seen == [NOW]
    monkeypatch.setattr(registry, "pypi_candidates", lambda source, constraint: {"k": 1})
    assert registry.candidates("pypi", {}, "*", NOW) == {"k": 1}


# --- _resolve_target wiring -----------------------------------------------------------------

def _oci_target(**extra):
    return {"mode": "single", "constraint": "*", "oci": {"image": "docker.io/library/alpine", "tag": "{version}"}, **extra}


def test_single_oci_target_gets_the_age_cutoff(monkeypatch):
    seen: list[tuple] = []

    def fake(source_type, source, constraint, *extra):
        seen.append(extra)
        return {"1.0.0": registry.Candidate("1.0.0", OLD, "fixture", "1.0.0")}

    monkeypatch.setattr(versions, "candidates", fake)
    result = versions._resolve_target("oci.alpine", _oci_target(), age_window_days=14, resolved_at=NOW, owner="root")
    assert result.version == "1.0.0"
    assert seen == [(NOW - timedelta(days=14),)]


def test_multi_source_target_keeps_the_full_scan(monkeypatch):
    seen: list[tuple] = []

    def fake(source_type, source, constraint, *extra):
        seen.append(extra)
        return {"1.0.0": registry.Candidate("1.0.0", OLD, "fixture", "1.0.0")}

    monkeypatch.setattr(versions, "candidates", fake)
    target = {
        "mode": "aligned", "constraint": "*",
        "oci": {"image": "docker.io/library/alpine", "tag": "{version}"},
        "pypi": {"name": "alpine", "registry": "https://pypi.org"},
    }
    versions._resolve_target("oci.alpine", target, age_window_days=14, resolved_at=NOW, owner="root")
    assert seen == [(), ()]


def test_resolve_target_reports_rate_limit_as_distinct_error(monkeypatch):
    def limited(*_args):
        raise registry.RegistryRateLimitedError("HTTP 429", window=3600)

    monkeypatch.setattr(versions, "candidates", limited)
    with pytest.raises(versions.VersionsRateLimitedError, match="oci.alpine.*HTTP 429"):
        versions._resolve_target("oci.alpine", _oci_target(), age_window_days=14, resolved_at=NOW, owner="root")
    assert issubclass(versions.VersionsRateLimitedError, versions.VersionsPrerequisiteError)


def test_cli_exit_code_five_for_rate_limits(monkeypatch):
    for error in (versions.VersionsRateLimitedError("limited"), registry.RegistryRateLimitedError("limited")):
        monkeypatch.setattr(versions, "_dispatch_versions", lambda _args, _e=error: (_ for _ in ()).throw(_e))
        with pytest.raises(CliFailure) as info:
            versions._run_versions(types.SimpleNamespace(), None)
        assert info.value.exit_code == versions.exit_codes.RATE_LIMITED == 5


# --- resume store ---------------------------------------------------------------------------

def _result(target_id: str = "oci.alpine", resolved_at: datetime = NOW) -> versions.TargetResult:
    return versions.TargetResult(
        target_id=target_id, version="3.20.1", resolved_at=resolved_at,
        age_cutoff=resolved_at - timedelta(days=14),
        sources={"oci": registry.Candidate("3.20.1", OLD, "oci-registry-last-modified", "3.20.1", "sha256:" + "a" * 64)},
        override=True, reason="why", expires="2026-12-01", owner="root",
    )


def test_resume_path_uses_xdg_cache_then_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    first = versions._resume_path(tmp_path / "cmru.toml")
    assert first.parent == tmp_path / "xdg" / "cmru"
    assert first != versions._resume_path(tmp_path / "other.toml")
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert versions._resume_path(tmp_path / "cmru.toml").parent == Path.home() / ".cache" / "cmru"


def test_resume_store_round_trip_and_staleness(tmp_path):
    path = tmp_path / "state" / "resume.json"
    store = versions._ResumeStore(path, NOW)
    assert store.entries == {}
    fingerprint = store.fingerprint({"mode": "single", "resolved": {"version": "old"}}, 14, "root")
    assert fingerprint == store.fingerprint({"mode": "single"}, 14, "root")
    assert fingerprint != store.fingerprint({"mode": "single"}, 7, "root")
    store.put("root", fingerprint, _result())

    again = versions._ResumeStore(path, NOW + timedelta(hours=23))
    assert again.get("root", "oci.alpine", fingerprint) == _result()
    assert again.get("root", "oci.alpine", "other") is None
    assert again.get("project", "oci.alpine", fingerprint) is None
    assert again.get("root", "missing", fingerprint) is None
    assert versions._ResumeStore(path, NOW + timedelta(hours=25)).get("root", "oci.alpine", fingerprint) is None

    again.entries[again._key("root", "oci.alpine")]["result"] = {"target_id": "oci.alpine"}
    assert again.get("root", "oci.alpine", fingerprint) is None
    again.clear()
    again.clear()
    assert not path.exists()


@pytest.mark.parametrize("content", ["not json", json.dumps({"schema": 2, "entries": {"a": {}}}),
                                     json.dumps({"schema": 1, "entries": []}), json.dumps([1])])
def test_resume_store_ignores_unusable_files(tmp_path, content):
    path = tmp_path / "resume.json"
    path.write_text(content, encoding="utf-8")
    assert versions._ResumeStore(path, NOW).entries == {}


def test_resume_store_save_failure_is_not_fatal(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    store = versions._ResumeStore(blocker / "resume.json", NOW)
    store.put("root", "fp", _result())
    assert len(store.entries) == 1


# --- partial progress and rerun -------------------------------------------------------------

def _forge(*target_ids: str):
    targets = {target_id: _oci_target() for target_id in target_ids}
    return types.SimpleNamespace(versions={"targets": targets}, projects={})


def test_rate_limit_saves_partial_results_and_rerun_resumes(monkeypatch, tmp_path, capsys):
    forge = _forge("oci.a", "oci.b", "oci.c")
    store = versions._ResumeStore(tmp_path / "resume.json", NOW)
    calls: list[str] = []

    def first_run(target_id, target, **kwargs):
        calls.append(target_id)
        if target_id == "oci.c":
            raise versions.VersionsRateLimitedError("target 'oci.c': HTTP 429")
        return _result(target_id, kwargs["resolved_at"])

    monkeypatch.setattr(versions, "_resolve_target", first_run)
    with pytest.raises(versions.VersionsRateLimitedError) as info:
        versions._resolve_all_for_command(forge, [], resolved_at=NOW, resume=store)
    message = str(info.value)
    assert "2 target(s) resolved and saved" in message and str(store.path) in message and "rerun" in message
    assert calls == ["oci.a", "oci.b", "oci.c"]

    calls.clear()
    monkeypatch.setattr(versions, "_resolve_target", lambda target_id, target, **kw: calls.append(target_id) or _result(target_id, kw["resolved_at"]))
    later = versions._ResumeStore(store.path, NOW + timedelta(hours=1))
    root, projects, declarations = versions._resolve_all_for_command(
        forge, [], resolved_at=NOW + timedelta(hours=1), resume=later,
    )
    assert calls == ["oci.c"] and set(root) == {"oci.a", "oci.b", "oci.c"}
    assert "reused 2 target(s)" in capsys.readouterr().err


def test_rate_limit_without_resume_store_propagates_unchanged(monkeypatch):
    monkeypatch.setattr(versions, "_resolve_target", lambda *a, **k: (_ for _ in ()).throw(versions.VersionsRateLimitedError("plain")))
    with pytest.raises(versions.VersionsRateLimitedError, match="^plain$"):
        versions._resolve_all_for_command(_forge("oci.a"), [], resolved_at=NOW)


def test_resolve_targets_without_resume_does_not_touch_the_cache(monkeypatch, capsys):
    monkeypatch.setattr(versions, "_resolve_target", lambda target_id, target, **kw: _result(target_id, kw["resolved_at"]))
    results = versions._resolve_targets(
        {"oci.a": _oci_target()}, age_window_days=14, resolved_at=NOW, owner="root",
    )
    assert set(results) == {"oci.a"} and capsys.readouterr().err == ""
