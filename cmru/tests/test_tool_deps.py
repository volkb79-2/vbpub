"""Tests for cmru.tool_deps (S15): declared tool-dependency verification.

Three DISTINCT checks per dependency -- integrity, authenticity, freshness --
kept apart in code (three separate CheckOutcome fields) and covered here as
three separate concerns. Required scenarios (see the brief this package
implements): the real cmru/assay pin today (authentic but stale -- must report
stale, not corrupt); authentic-and-current (must pass); an integrity failure;
an authenticity (hash) mismatch; no release published yet (unresolved, not a
failure); network unavailable (unresolved, not a failure, and must not hang).

No real network call anywhere in this file: every test monkeypatches
``tool_deps.urlopen`` or one of the small internal seams built on top of it.
"""
from __future__ import annotations

import dataclasses
import io
import json
import socket
import urllib.error
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import tool_deps
from cmru.config import ToolDependency

VALID_SHA = "6224f784f96f5ad9d10264a69dd69594639959c5eda847dcede822a7adc515bf"


def _dep(**overrides) -> ToolDependency:
    fields = dict(
        project="assay", version="1.0.0", path="tools/assay/assay-1.0.0.pyz", sha256=VALID_SHA,
    )
    fields.update(overrides)
    return ToolDependency(**fields)


def _http_error(status: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", status, "err", {}, io.BytesIO(body))


class _Response:
    def __init__(self, status: int = 200, body: bytes = b"{}"):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self._body


# --- _get: the one network transport seam --------------------------------------

def test_get_returns_status_and_body_on_success(monkeypatch):
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: _Response(200, b"hi"))
    assert tool_deps._get("https://x", timeout=5) == (200, b"hi")


def test_get_returns_status_and_body_from_http_error(monkeypatch):
    monkeypatch.setattr(
        tool_deps, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(_http_error(404, b"nope")),
    )
    assert tool_deps._get("https://x", timeout=5) == (404, b"nope")


def test_get_http_error_with_no_fp_returns_empty_body(monkeypatch):
    error = _http_error(500)
    error.fp = None
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: (_ for _ in ()).throw(error))
    assert tool_deps._get("https://x", timeout=5) == (500, b"")


def test_get_raises_network_unavailable_on_timeout(monkeypatch):
    monkeypatch.setattr(
        tool_deps, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(socket.timeout("timed out")),
    )
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps._get("https://x", timeout=5)


def test_get_raises_network_unavailable_on_url_error(monkeypatch):
    monkeypatch.setattr(
        tool_deps, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(urllib.error.URLError("boom")),
    )
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps._get("https://x", timeout=5)


def test_get_raises_network_unavailable_on_http_exception(monkeypatch):
    """http.client.HTTPException (IncompleteRead, BadStatusLine, ...) is NOT a
    subclass of OSError/URLError -- a truncated/malformed response mid-transfer
    must still map to NetworkUnavailable, not escape as a raw exception that
    the release-plan phase would treat as a genuine mid-release failure."""
    import http.client

    monkeypatch.setattr(
        tool_deps, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(http.client.IncompleteRead(b"partial")),
    )
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps._get("https://x", timeout=5)


# --- _github_get_json / _download_asset -----------------------------------------

def test_github_get_json_returns_none_on_404_by_default(monkeypatch):
    """The default (`on_404="absent"`) is for a lookup where 404 legitimately
    means "this exact thing doesn't exist" (e.g. one release tag)."""
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (404, b""))
    assert tool_deps._github_get_json("/x", timeout=5) is None


def test_github_get_json_raises_on_404_when_on_404_is_error(monkeypatch):
    """B1: `on_404="error"` is for a lookup where 404 means the endpoint itself
    could not be reached as expected (the releases LIST for an inaccessible
    repo) -- it must never collapse to the same "absent" outcome as a single
    missing tag."""
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (404, b""))
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps._github_get_json("/x", timeout=5, on_404="error")


# --- `if status >= 400:` boundary, pinned exactly (this is the newest,
# least-exercised code in the package -- the B1 rewrite) -------------------------
#
# NOTE: every case below uses a genuinely VALID JSON body. A `>=` swapped to
# `>` lets status 400 fall through this line into `json.loads(body)` instead
# of raising here -- but if the body is *invalid* JSON, that fall-through
# ALSO ends in NetworkUnavailable (via the JSONDecodeError branch below), just
# with a different message. Asserting only the exception TYPE against an
# invalid body is therefore blind to this exact mutant: both the real code
# and the mutant raise the same exception class, for different reasons. A
# valid body forces the two to diverge -- the mutant returns the parsed dict
# instead of raising, and the message ("returned HTTP 400" vs "unparseable
# response") is asserted explicitly so the two causes can never be conflated.

def test_github_get_json_status_399_does_not_raise(monkeypatch):
    """Paired control: 399 is still a "success" status here -- it must fall
    through to the ordinary JSON-parse path, never NetworkUnavailable."""
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (399, b'{"ok": true}'))
    assert tool_deps._github_get_json("/x", timeout=5) == {"ok": True}


def test_github_get_json_status_400_raises_even_with_a_genuinely_valid_json_body(monkeypatch):
    """The regression test for the surviving GtE->Gt mutant."""
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (400, b'{"ok": true}'))
    with pytest.raises(tool_deps.NetworkUnavailable, match="returned HTTP 400"):
        tool_deps._github_get_json("/x", timeout=5)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_github_get_json_raises_above_400_naming_the_exact_status(monkeypatch, status):
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (status, b'{"ok": true}'))
    with pytest.raises(tool_deps.NetworkUnavailable, match=f"returned HTTP {status}"):
        tool_deps._github_get_json("/x", timeout=5)


def test_github_get_json_raises_on_unparseable_body_with_a_message_naming_that_cause(monkeypatch):
    """Kept as its own case, distinguished by MESSAGE from the bad-status path
    right above (both raise the same exception type -- that alone is not
    enough, per the 400 regression above)."""
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (200, b"not json"))
    with pytest.raises(tool_deps.NetworkUnavailable, match="unparseable response"):
        tool_deps._github_get_json("/x", timeout=5)


def test_github_get_json_returns_parsed_body_on_success(monkeypatch):
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (200, b'{"a": 1}'))
    assert tool_deps._github_get_json("/x", timeout=5) == {"a": 1}


def test_download_asset_returns_bytes_on_200(monkeypatch):
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (200, b"payload"))
    assert tool_deps._download_asset("https://x", timeout=5) == b"payload"


def test_download_asset_raises_on_non_200(monkeypatch):
    monkeypatch.setattr(tool_deps, "_get", lambda url, timeout: (403, b""))
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps._download_asset("https://x", timeout=5)


# --- _list_releases / resolve_latest_release ------------------------------------

def test_list_releases_single_short_page(monkeypatch):
    monkeypatch.setattr(
        tool_deps, "_github_get_json", lambda path, timeout, on_404: [{"tag_name": "a"}],
    )
    assert tool_deps._list_releases("o", "r", timeout=5) == [{"tag_name": "a"}]


def test_list_releases_passes_on_404_error_so_an_inaccessible_repo_never_reads_as_empty(monkeypatch):
    """B1, at the seam: `_list_releases` MUST ask `_github_get_json` to raise
    (not return None) on 404 -- locks in the fix at the call-site level, in
    addition to the measured-HTTP-level tests below."""
    seen = []
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda path, timeout, on_404: (seen.append(on_404), [])[1],
    )
    tool_deps._list_releases("o", "r", timeout=5)
    assert seen == ["error"]


def test_list_releases_paginates_on_a_full_page(monkeypatch):
    calls = []

    def fake(path, timeout, on_404):
        calls.append(path)
        # NOTE: `per_page=100` always contains the substring "page=1", so this
        # must check the trailing `&page=<N>` specifically, not `in`.
        if path.endswith("page=1"):
            return [{"tag_name": f"t{i}"} for i in range(100)]
        return [{"tag_name": "last"}]

    monkeypatch.setattr(tool_deps, "_github_get_json", fake)
    out = tool_deps._list_releases("o", "r", timeout=5)
    assert len(out) == 101
    assert len(calls) == 2


# --- B1: measured against GitHub's real API (see the coordinator's report) --
#   real repo, has releases   -> HTTP 200  body starts [{"url":...
#   real repo, ZERO releases  -> HTTP 200  body []
#   absent / private repo     -> HTTP 404
# A 404 on the LIST endpoint can never mean "zero releases": that is 200+[].

def test_list_releases_raises_when_the_repository_is_inaccessible_measured_at_the_http_level(monkeypatch):
    """The actual regression test: a 404 on the releases LIST (repo missing,
    renamed, private, or misspelled) MUST NOT be read as bootstrap."""
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: (_ for _ in ()).throw(_http_error(404, b"")))
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps._list_releases("o", "r", timeout=5)


def test_list_releases_returns_empty_for_a_real_repo_with_zero_releases_measured_at_the_http_level(monkeypatch):
    """Paired control: the genuine bootstrap state (200 + []) must still work
    after the 404 fix -- this is the state the fix must NOT break."""
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: _Response(200, b"[]"))
    assert tool_deps._list_releases("o", "r", timeout=5) == []


def test_list_releases_returns_releases_for_a_real_repo_with_releases_measured_at_the_http_level(monkeypatch):
    """Paired control: the ordinary case (200 + a real releases array)."""
    body = json.dumps([{"tag_name": "assay-v1.0.0"}]).encode()
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: _Response(200, body))
    assert tool_deps._list_releases("o", "r", timeout=5) == [{"tag_name": "assay-v1.0.0"}]


def test_resolve_latest_release_picks_highest_semver_ignoring_drafts_prereleases_and_other_prefixes(monkeypatch):
    releases = [
        {"tag_name": "assay-v1.0.0", "assets": [{"name": "a.pyz", "browser_download_url": "u1"}]},
        {"tag_name": "assay-v2.1.0", "assets": [{"name": "b.pyz", "browser_download_url": "u2"}]},
        {"tag_name": "assay-v9.9.9", "draft": True},
        {"tag_name": "assay-v9.9.8", "prerelease": True},
        {"tag_name": "other-v5.0.0"},
    ]
    monkeypatch.setattr(tool_deps, "_list_releases", lambda owner, repo, timeout: releases)
    result = tool_deps.resolve_latest_release("o", "r", "assay-v", timeout=5)
    assert result == {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": [{"name": "b.pyz", "url": "u2"}]}


def test_resolve_latest_release_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(tool_deps, "_list_releases", lambda owner, repo, timeout: [])
    assert tool_deps.resolve_latest_release("o", "r", "assay-v", timeout=5) is None


def test_resolve_latest_release_still_reads_a_genuinely_empty_catalog_as_bootstrap(monkeypatch):
    """B1 paired control, one level up: resolve_latest_release (what
    verify_tool_dependency actually calls) still returns None -- the bootstrap
    signal downstream code depends on -- for a REAL repo with zero releases,
    proven at the HTTP layer, not by mocking _list_releases away."""
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: _Response(200, b"[]"))
    assert tool_deps.resolve_latest_release("o", "r", "assay-v", timeout=5) is None


def test_resolve_latest_release_raises_instead_of_reading_bootstrap_when_the_repo_is_inaccessible(monkeypatch):
    """B1's actual regression, one level up: an inaccessible repo (404 on the
    list) must propagate as NetworkUnavailable out of resolve_latest_release,
    never silently collapse to the same None a genuinely empty catalog returns."""
    monkeypatch.setattr(tool_deps, "urlopen", lambda req, timeout: (_ for _ in ()).throw(_http_error(404, b"")))
    with pytest.raises(tool_deps.NetworkUnavailable):
        tool_deps.resolve_latest_release("o", "r", "assay-v", timeout=5)


# --- _check_integrity: local only, always resolvable ----------------------------

def test_check_integrity_fails_when_file_missing(tmp_path):
    outcome = tool_deps._check_integrity(tmp_path, _dep())
    assert outcome.status == "fail"
    assert outcome.reason == "missing-file"


def test_check_integrity_passes_when_bytes_match(tmp_path):
    target = tmp_path / "tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    outcome = tool_deps._check_integrity(tmp_path, _dep(sha256=sha256(b"payload").hexdigest()))
    assert outcome.status == "pass"
    assert outcome.reason is None


def test_check_integrity_fails_when_bytes_mismatch(tmp_path):
    target = tmp_path / "tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    outcome = tool_deps._check_integrity(tmp_path, _dep(sha256=VALID_SHA))
    assert outcome.status == "fail"
    assert outcome.reason == "hash-mismatch"


# --- verify_tool_dependency: the full three-check state machine ----------------

@pytest.fixture()
def vendored(tmp_path):
    """A project_root with the dependency's bytes vendored and integrity-clean."""
    target = tmp_path / "tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    return tmp_path, sha256(b"payload").hexdigest()


def test_verify_reports_network_error_when_the_release_catalog_is_unreachable(monkeypatch, vendored):
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: (_ for _ in ()).throw(tool_deps.NetworkUnavailable("down")),
    )
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.integrity.status == "pass"
    assert status.authenticity == status.freshness
    assert status.authenticity.status == "unresolved"
    assert status.authenticity.reason == "network-error"
    assert status.highest_known_version is None


def test_verify_reports_no_release_when_nothing_is_published_yet(monkeypatch, vendored):
    root, digest = vendored
    monkeypatch.setattr(tool_deps, "resolve_latest_release", lambda *a, **k: None)
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.integrity.status == "pass"
    assert status.authenticity.status == "unresolved"
    assert status.authenticity.reason == "no-release"
    assert status.freshness.status == "unresolved"
    assert status.freshness.reason == "no-release"


def test_verify_reports_authentic_and_current_without_a_second_network_call(monkeypatch, vendored):
    """The REQUIRED "authentic and current" case. Also proves the fast path: when
    the pin IS the highest release, the assets already resolved are reused --
    _github_get_json (fetch-by-tag) must NEVER be called."""
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch a second release")),
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda url, timeout: b"payload")
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.integrity.status == "pass"
    assert status.freshness.status == "pass"
    assert status.authenticity.status == "pass"
    assert status.authenticity.reason is None
    assert status.highest_known_version == "1.0.0"


def test_verify_reports_authentic_but_stale_the_real_cmru_assay_state(monkeypatch, vendored):
    """The REQUIRED "authentic but stale" case -- exactly cmru's real pin today
    (1.0.0, while assay ships 2.1.0). Must report stale, never corrupt."""
    root, digest = vendored

    def fake_resolve(owner, repo, prefix, *, timeout):
        return {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []}

    def fake_release_by_tag(path, *, timeout):
        assert path == "/repos/o/r/releases/tags/assay-v1.0.0"
        return {"assets": [{"name": "assay-1.0.0.pyz", "browser_download_url": "https://asset"}]}

    monkeypatch.setattr(tool_deps, "resolve_latest_release", fake_resolve)
    monkeypatch.setattr(tool_deps, "_github_get_json", fake_release_by_tag)
    monkeypatch.setattr(tool_deps, "_download_asset", lambda url, timeout: b"payload")

    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.integrity.status == "pass"
    assert status.authenticity.status == "pass"  # authentic
    assert status.freshness.status == "fail"
    assert status.freshness.reason == "stale"      # stale, NOT corrupt
    assert status.highest_known_version == "2.1.0"


# --- the freshness comparison itself, pinned exactly at all three outcomes --
# (this single `elif pinned_key < highest_key:` decides stale vs. current; a
# compare-swap here inverts staleness silently, which is the whole feature
# failing in the direction that matters most -- fail-open)

def test_freshness_pinned_less_than_highest_is_reported_stale(monkeypatch, tmp_path):
    """pinned 1.0.0 < highest 2.1.0 -> fail/stale."""
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    monkeypatch.setattr(tool_deps, "_github_get_json", lambda *a, **k: None)
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v",
        dependency=_dep(version="1.0.0"), project_root=tmp_path, consumer="cmru",
    )
    assert status.freshness.status == "fail"
    assert status.freshness.reason == "stale"


def test_freshness_pinned_equal_to_highest_is_reported_current(monkeypatch, tmp_path):
    """pinned 1.0.0 == highest 1.0.0 -> pass."""
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "1.0.0", "tag": "assay-v1.0.0", "assets": []},
    )
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v",
        dependency=_dep(version="1.0.0"), project_root=tmp_path, consumer="cmru",
    )
    assert status.freshness.status == "pass"
    assert status.freshness.reason is None


def test_freshness_pinned_greater_than_highest_is_reported_ahead(monkeypatch, tmp_path):
    """pinned 2.0.0 > highest 1.0.0 -> fail/ahead-of-known-releases."""
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "1.0.0", "tag": "assay-v1.0.0", "assets": []},
    )
    monkeypatch.setattr(tool_deps, "_github_get_json", lambda *a, **k: None)
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v",
        dependency=_dep(version="2.0.0"), project_root=tmp_path, consumer="cmru",
    )
    assert status.freshness.status == "fail"
    assert status.freshness.reason == "ahead-of-known-releases"


def test_freshness_comparison_is_numeric_not_lexicographic(monkeypatch, tmp_path):
    """S5.2, numeric-aware case: pinned 1.10.0 vs highest 1.9.0. Numerically
    pinned IS newer (1.10.0 > 1.9.0), so this MUST read as "ahead", never
    "stale". Plain string comparison gets this backwards -- "1.10.0" <
    "1.9.0" lexicographically (comparing the second component character by
    character, '1' < '9') -- which would wrongly report a materially NEWER
    pin as stale: fail-open on staleness in exactly the direction that
    matters, silently."""
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "1.9.0", "tag": "assay-v1.9.0", "assets": []},
    )
    monkeypatch.setattr(tool_deps, "_github_get_json", lambda *a, **k: None)
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v",
        dependency=_dep(version="1.10.0"), project_root=tmp_path, consumer="cmru",
    )
    assert status.freshness.status == "fail"
    assert status.freshness.reason == "ahead-of-known-releases"  # NOT "stale"


def test_verify_reports_version_not_published_when_the_pinned_tag_has_no_release(monkeypatch, vendored):
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    monkeypatch.setattr(tool_deps, "_github_get_json", lambda *a, **k: None)
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.freshness.status == "fail" and status.freshness.reason == "stale"
    assert status.authenticity.status == "fail"
    assert status.authenticity.reason == "version-not-published"


def test_verify_reports_network_error_fetching_the_specific_pinned_release(monkeypatch, vendored):
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda *a, **k: (_ for _ in ()).throw(tool_deps.NetworkUnavailable("down")),
    )
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.freshness.status == "fail"  # already resolved before the failing call
    assert status.authenticity.status == "unresolved"
    assert status.authenticity.reason == "network-error"


def test_verify_reports_ahead_of_known_releases_and_asset_missing_when_pin_is_ahead_of_every_release(monkeypatch, vendored):
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "0.9.0", "tag": "assay-v0.9.0", "assets": []},
    )
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda *a, **k: {"assets": [{"name": "wrong-name.pyz", "browser_download_url": "https://asset"}]},
    )
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.freshness.status == "fail"
    assert status.freshness.reason == "ahead-of-known-releases"
    assert status.authenticity.status == "fail"
    assert status.authenticity.reason == "asset-missing"


def test_verify_freshness_message_never_overclaims_when_a_draft_exists_under_the_exact_pinned_tag(monkeypatch, vendored):
    """Issue 5: `resolve_latest_release` deliberately excludes drafts/prereleases
    from its "highest" computation, so freshness's "ahead" branch must NOT claim
    the pin "is not among published releases" -- a draft or prerelease release
    can still exist under that EXACT tag, and the authenticity check's own
    direct, exact-tag lookup can find and confirm it, as it does here. The old
    wording would have directly contradicted a PASSING authenticity outcome on
    the very same status."""
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "0.9.0", "tag": "assay-v0.9.0", "assets": []},
    )
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda *a, **k: {"assets": [{"name": "assay-1.0.0.pyz", "browser_download_url": "https://asset"}]},
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"payload")

    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.authenticity.status == "pass"  # a real release under this exact tag WAS found
    assert status.freshness.status == "fail"
    assert status.freshness.reason == "ahead-of-known-releases"
    assert "is not among published" not in status.freshness.detail
    assert "non-draft, non-prerelease" in status.freshness.detail


def test_verify_reports_network_error_downloading_the_published_asset(monkeypatch, vendored):
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(
        tool_deps, "_download_asset",
        lambda *a, **k: (_ for _ in ()).throw(tool_deps.NetworkUnavailable("down")),
    )
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.freshness.status == "pass"
    assert status.authenticity.status == "unresolved"
    assert status.authenticity.reason == "network-error"


def test_verify_reports_hash_mismatch_when_the_published_bytes_disagree(monkeypatch, vendored):
    """The REQUIRED "hash that does not match the published asset" case."""
    root, digest = vendored
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"totally different bytes")
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(sha256=digest),
        project_root=root, consumer="cmru",
    )
    assert status.integrity.status == "pass"  # local file is fine on its own
    assert status.authenticity.status == "fail"
    assert status.authenticity.reason == "hash-mismatch"


def test_verify_reports_integrity_failure_alongside_a_passing_authenticity_check(monkeypatch, tmp_path):
    """Integrity and authenticity are independent: a corrupted local file with a
    still-authentic-looking published release must be reported as an integrity
    failure, never silently swallowed by the other two checks."""
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"published bytes")
    status = tool_deps.verify_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v",
        dependency=_dep(sha256=sha256(b"published bytes").hexdigest()),
        project_root=tmp_path, consumer="cmru",  # nothing vendored on disk at all
    )
    assert status.integrity.status == "fail"
    assert status.integrity.reason == "missing-file"
    assert status.authenticity.status == "pass"


# --- verify_project: resolves each dependency's PROVIDER for its prefix --------

def test_verify_project_returns_empty_tuple_when_nothing_is_declared(tmp_path):
    project = SimpleNamespace(name="cmru", project_root=tmp_path, tool_dependencies=())
    assert tool_deps.verify_project(owner="o", repo="r", project=project, projects={}) == ()


def test_verify_project_reports_fail_for_a_provider_missing_from_the_estate(tmp_path):
    project = SimpleNamespace(
        name="cmru", project_root=tmp_path, tool_dependencies=[_dep(project="ghost")],
    )
    statuses = tool_deps.verify_project(owner="o", repo="r", project=project, projects={"cmru": project})
    assert len(statuses) == 1
    assert statuses[0].authenticity.status == "fail"
    assert statuses[0].authenticity.reason == "unknown-provider"
    assert statuses[0].authenticity == statuses[0].freshness
    assert statuses[0].integrity.status == "fail"  # nothing vendored either, in this fixture


def test_verify_project_resolves_the_provider_projects_own_prefix(monkeypatch, tmp_path):
    target = tmp_path / "tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    digest = sha256(b"payload").hexdigest()

    seen_prefixes = []

    def fake_resolve(owner, repo, prefix, *, timeout):
        seen_prefixes.append(prefix)
        return {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        }

    monkeypatch.setattr(tool_deps, "resolve_latest_release", fake_resolve)
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"payload")

    cmru = SimpleNamespace(
        name="cmru", project_root=tmp_path, tool_dependencies=[_dep(sha256=digest)],
    )
    assay = SimpleNamespace(name="assay", prefix="assay-v")
    statuses = tool_deps.verify_project(
        owner="o", repo="r", project=cmru, projects={"cmru": cmru, "assay": assay},
    )
    assert len(statuses) == 1
    assert statuses[0].authenticity.status == "pass"
    assert seen_prefixes == ["assay-v"]


# --- CheckOutcome / ToolDependencyStatus immutability ---------------------------
# Both are shared result objects passed between the three checks and every
# reporter (render_status, status_as_dict, is_blocking, the release preflight).
# If either became mutable, a caller could rewrite a verdict after it was
# computed -- assert frozen-ness directly, each with a paired construct-and-read
# control so the frozen-ness assertion isn't trivially true because
# construction itself is broken.

def test_constructing_and_reading_a_check_outcome_works_normally():
    outcome = tool_deps.CheckOutcome("pass", None, "ok")
    assert outcome.status == "pass"
    assert outcome.reason is None
    assert outcome.detail == "ok"


def test_a_check_outcome_is_frozen():
    outcome = tool_deps.CheckOutcome("pass", None, "ok")
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.status = "fail"


def test_constructing_and_reading_a_tool_dependency_status_works_normally():
    dependency = _dep()
    ok = tool_deps.CheckOutcome("pass", None, "ok")
    status = tool_deps.ToolDependencyStatus("cmru", dependency, ok, ok, ok, "1.0.0")
    assert status.consumer == "cmru"
    assert status.dependency is dependency
    assert status.integrity is ok
    assert status.highest_known_version == "1.0.0"


def test_a_tool_dependency_status_is_frozen():
    ok = tool_deps.CheckOutcome("pass", None, "ok")
    status = tool_deps.ToolDependencyStatus("cmru", _dep(), ok, ok, ok, "1.0.0")
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.freshness = tool_deps.CheckOutcome("fail", "stale", "rewritten")


# --- is_blocking ----------------------------------------------------------------

_PASS = tool_deps.CheckOutcome("pass", None, "ok")
_FAIL = tool_deps.CheckOutcome("fail", "x", "bad")
_UNRESOLVED = tool_deps.CheckOutcome("unresolved", "no-release", "n/a")


def _status(*, integrity=_PASS, authenticity=_PASS, freshness=_PASS) -> tool_deps.ToolDependencyStatus:
    return tool_deps.ToolDependencyStatus("cmru", _dep(), integrity, authenticity, freshness, None)


@pytest.mark.parametrize(
    "status, allow_stale, expected",
    [
        (_status(), False, False),
        (_status(), True, False),
        (_status(integrity=_FAIL), False, True),
        (_status(integrity=_FAIL), True, True),  # no override for integrity, ever
        (_status(authenticity=_FAIL), False, True),
        (_status(authenticity=_FAIL), True, True),  # no override for authenticity, ever
        (_status(freshness=_FAIL), False, True),
        (_status(freshness=_FAIL), True, False),  # the ONE overridable outcome
        (_status(authenticity=_UNRESOLVED, freshness=_UNRESOLVED), False, False),
        (_status(authenticity=_UNRESOLVED, freshness=_UNRESOLVED), True, False),
    ],
)
def test_is_blocking(status, allow_stale, expected):
    assert tool_deps.is_blocking(status, allow_stale=allow_stale) is expected


def test_is_blocking_default_allow_stale_is_false():
    assert tool_deps.is_blocking(_status(freshness=_FAIL)) is True


# --- render_status / status_as_dict ----------------------------------------------

def test_render_status_names_each_check_distinctly_with_reason_when_present():
    status = _status(freshness=tool_deps.CheckOutcome("fail", "stale", "behind 2.1.0"))
    text = tool_deps.render_status(status)
    assert "cmru: tool dependency assay@1.0.0 (tools/assay/assay-1.0.0.pyz)" in text
    assert "integrity" in text and "PASS" in text
    assert "authenticity" in text
    assert "freshness    FAIL (stale) -- behind 2.1.0" in text


def test_render_status_omits_the_parenthetical_reason_for_a_plain_pass():
    status = _status()
    text = tool_deps.render_status(status)
    assert "PASS --" in text
    assert "PASS (" not in text


def test_status_as_dict_shape():
    status = _status(freshness=tool_deps.CheckOutcome("fail", "stale", "behind"))
    data = tool_deps.status_as_dict(status)
    assert data["consumer"] == "cmru"
    assert data["project"] == "assay"
    assert data["version"] == "1.0.0"
    assert data["sha256"] == VALID_SHA
    assert data["freshness"] == {"status": "fail", "reason": "stale", "detail": "behind"}
    assert data["integrity"]["status"] == "pass"


# --- refresh_tool_dependency + the surgical TOML rewrite ------------------------

def _config_with_entry(tmp_path: Path, project: str = "assay", version: str = "1.0.0",
                        path: str = "tools/assay/assay-1.0.0.pyz", sha256_value: str = VALID_SHA) -> Path:
    config = tmp_path / "cmru.toml"
    config.write_text(
        "schema_version = 1\n\n[project]\nid = \"cmru\"\n\n"
        f'[[project.tool_dependencies]]\nproject = "{project}"\nversion = "{version}"\n'
        f'path    = "{path}"\nsha256  = "{sha256_value}"\n\n'
        "[project.version]\nstrategy = \"scm\"\n",
        encoding="utf-8",
    )
    return config


def test_refresh_raises_when_provider_has_no_published_release(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_deps, "resolve_latest_release", lambda *a, **k: None)
    config = _config_with_entry(tmp_path)
    with pytest.raises(RuntimeError, match="no published release"):
        tool_deps.refresh_tool_dependency(
            owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(),
            project_root=tmp_path, config_path=config,
        )


def test_refresh_raises_when_the_pinned_version_is_not_embedded_in_the_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    config = _config_with_entry(tmp_path, path="tools/assay/assay.pyz")
    with pytest.raises(RuntimeError, match="cannot infer the refreshed filename"):
        tool_deps.refresh_tool_dependency(
            owner="o", repo="r", provider_prefix="assay-v",
            dependency=_dep(path="tools/assay/assay.pyz"),
            project_root=tmp_path, config_path=config,
        )


def test_refresh_raises_when_the_new_release_has_no_matching_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    config = _config_with_entry(tmp_path)
    with pytest.raises(RuntimeError, match="no asset named"):
        tool_deps.refresh_tool_dependency(
            owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(),
            project_root=tmp_path, config_path=config,
        )


def test_refresh_renames_writes_bytes_removes_the_old_file_and_rewrites_the_pin(monkeypatch, tmp_path):
    old = tmp_path / "tools/assay/assay-1.0.0.pyz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old bytes")
    old_sidecar = old.with_name(old.name + ".sha256")
    old_sidecar.write_text("old\n", encoding="utf-8")

    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "2.1.0", "tag": "assay-v2.1.0",
            "assets": [{"name": "assay-2.1.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"new bytes")

    config = _config_with_entry(tmp_path)
    new_dependency = tool_deps.refresh_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(),
        project_root=tmp_path, config_path=config,
    )

    assert new_dependency.version == "2.1.0"
    assert new_dependency.path == "tools/assay/assay-2.1.0.pyz"
    assert new_dependency.sha256 == sha256(b"new bytes").hexdigest()

    new_file = tmp_path / "tools/assay/assay-2.1.0.pyz"
    assert new_file.read_bytes() == b"new bytes"
    assert new_file.with_name(new_file.name + ".sha256").read_text().startswith(new_dependency.sha256)
    assert not old.exists()
    assert not old_sidecar.exists()

    rewritten = config.read_text(encoding="utf-8")
    assert 'version = "2.1.0"' in rewritten
    assert 'path    = "tools/assay/assay-2.1.0.pyz"' in rewritten
    assert new_dependency.sha256 in rewritten


def test_refresh_removes_the_old_file_even_when_it_has_no_sidecar(monkeypatch, tmp_path):
    """old_absolute != new_absolute, old file exists, but its .sha256 sidecar
    does not -- the sidecar removal must be skipped, not raise."""
    old = tmp_path / "tools/assay/assay-1.0.0.pyz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old bytes")

    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "2.1.0", "tag": "assay-v2.1.0",
            "assets": [{"name": "assay-2.1.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"new bytes")
    config = _config_with_entry(tmp_path)

    tool_deps.refresh_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(),
        project_root=tmp_path, config_path=config,
    )
    assert not old.exists()
    assert (tmp_path / "tools/assay/assay-2.1.0.pyz").exists()


def test_refresh_when_nothing_was_vendored_yet_skips_the_old_file_cleanup(monkeypatch, tmp_path):
    """old_absolute != new_absolute but the old file never existed on disk --
    the cleanup branch must be skipped, not raise."""
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "2.1.0", "tag": "assay-v2.1.0",
            "assets": [{"name": "assay-2.1.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"new bytes")
    config = _config_with_entry(tmp_path)

    new_dependency = tool_deps.refresh_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(),
        project_root=tmp_path, config_path=config,
    )
    assert (tmp_path / "tools/assay/assay-2.1.0.pyz").exists()
    assert new_dependency.version == "2.1.0"


def test_refresh_when_already_at_the_latest_version_rewrites_in_place(monkeypatch, tmp_path):
    """old_absolute == new_absolute (same filename): the removal branch's first
    operand is False and must short-circuit, never touching the just-written file."""
    old = tmp_path / "tools/assay/assay-1.0.0.pyz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old bytes")

    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"refreshed bytes")
    config = _config_with_entry(tmp_path)

    new_dependency = tool_deps.refresh_tool_dependency(
        owner="o", repo="r", provider_prefix="assay-v", dependency=_dep(),
        project_root=tmp_path, config_path=config,
    )
    assert new_dependency.path == "tools/assay/assay-1.0.0.pyz"
    assert old.read_bytes() == b"refreshed bytes"


def test_rewrite_tool_dependency_toml_raises_when_the_entry_is_not_found(tmp_path):
    config = _config_with_entry(tmp_path, project="assay")
    with pytest.raises(ValueError, match="no \\[\\[project.tool_dependencies\\]\\] entry"):
        tool_deps._rewrite_tool_dependency_toml(config, _dep(project="ciu"), _dep(project="ciu", version="9.9.9"))


def test_rewrite_tool_dependency_toml_edits_only_the_matching_block(tmp_path):
    config = tmp_path / "cmru.toml"
    config.write_text(
        "schema_version = 1\n\n[project]\nid = \"cmru\"\n\n"
        '[[project.tool_dependencies]]\nproject = "ciu"\nversion = "1.0.0"\n'
        'path    = "tools/ciu/ciu-1.0.0.pyz"\nsha256  = "' + ("a" * 64) + '"\n\n'
        '[[project.tool_dependencies]]\nproject = "assay"\nversion = "1.0.0"\n'
        f'path    = "tools/assay/assay-1.0.0.pyz"\nsha256  = "{VALID_SHA}"\n',
        encoding="utf-8",
    )
    old = _dep(project="assay")
    new = _dep(project="assay", version="2.1.0", path="tools/assay/assay-2.1.0.pyz", sha256="b" * 64)
    tool_deps._rewrite_tool_dependency_toml(config, old, new)
    text = config.read_text(encoding="utf-8")
    assert 'project = "ciu"\nversion = "1.0.0"' in text  # untouched
    assert 'project = "assay"\nversion = "2.1.0"' in text
    assert "b" * 64 in text
    assert VALID_SHA not in text


# --- tool_deps_main: the CLI verb -----------------------------------------------

def _cli_config(tmp_path, *, cmru_tool_deps=(), assay_prefix="assay-v"):
    cmru_root = tmp_path / "cmru"
    cmru_root.mkdir(exist_ok=True)
    cmru = SimpleNamespace(
        name="cmru", project_root=cmru_root, prefix="cmru-v", tool_dependencies=cmru_tool_deps,
    )
    assay = SimpleNamespace(name="assay", project_root=tmp_path / "assay", prefix=assay_prefix, tool_dependencies=())
    projects = {"cmru": cmru, "assay": assay}
    github_config = SimpleNamespace(owner="o", repo="r")
    return (
        tmp_path, projects, ["cmru", "assay"], ["cmru", "assay"], [], "project-first", {},
        None, github_config, None,
    )


def test_tool_deps_main_reports_no_op_when_nothing_is_declared(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda cfg: _cli_config(tmp_path))
    tool_deps.tool_deps_main([])
    out = capsys.readouterr().out
    assert "no project declares a tool dependency" in out


def test_tool_deps_main_json_output_when_nothing_is_declared_is_still_valid_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda cfg: _cli_config(tmp_path))
    tool_deps.tool_deps_main(["--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_tool_deps_main_errors_on_an_unknown_project(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda cfg: _cli_config(tmp_path))
    with pytest.raises(SystemExit) as exc:
        tool_deps.tool_deps_main(["--project", "ghost"])
    assert exc.value.code == 2


# --- B2: a single-project load can never resolve a sibling PROVIDER project --

def test_tool_deps_main_refuses_a_single_project_load_that_declares_a_tool_dependency(monkeypatch, tmp_path, capsys):
    """The regression test: `--config cmru/cmru.toml` (or running from that
    project directory) MUST NOT report the exact pin an estate-scoped run
    proves authentic as an 'authenticity FAIL (unknown-provider)' -- it must
    refuse the INVOCATION, with exit 2, and a message that says why."""
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config", lambda cfg: _cli_config(tmp_path, cmru_tool_deps=(_dep(),)),
    )
    with pytest.raises(SystemExit) as exc:
        tool_deps.tool_deps_main([])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "cmru.orchestration.toml" in err
    assert "authenticity" not in err.lower()  # never phrased as a finding about the artifact


def test_tool_deps_main_allows_a_single_project_load_that_declares_nothing(monkeypatch, tmp_path, capsys):
    """Paired control: the B2 guard must not fire merely because this is a
    single-project load -- only when there is something it cannot resolve."""
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda cfg: _cli_config(tmp_path))
    tool_deps.tool_deps_main([])  # must NOT raise
    assert "no project declares a tool dependency" in capsys.readouterr().out


def test_tool_deps_main_refresh_also_refuses_a_single_project_load(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config", lambda cfg: _cli_config(tmp_path, cmru_tool_deps=(_dep(),)),
    )
    with pytest.raises(SystemExit) as exc:
        tool_deps.tool_deps_main(["--refresh", "assay"])
    assert exc.value.code == 2
    assert "cmru.orchestration.toml" in capsys.readouterr().err


def test_tool_deps_main_passes_and_exits_zero(monkeypatch, tmp_path, capsys):
    target = tmp_path / "cmru/tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    digest = sha256(b"payload").hexdigest()

    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda cfg: _cli_config(tmp_path, cmru_tool_deps=[_dep(sha256=digest)]),
    )
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"payload")

    tool_deps.tool_deps_main([])  # must NOT raise SystemExit
    out = capsys.readouterr().out
    assert "OK" in out


def test_tool_deps_main_blocks_on_a_stale_pin_by_default(monkeypatch, tmp_path, capsys):
    target = tmp_path / "cmru/tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    digest = sha256(b"payload").hexdigest()

    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda cfg: _cli_config(tmp_path, cmru_tool_deps=[_dep(sha256=digest)]),
    )
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda *a, **k: {"assets": [{"name": "assay-1.0.0.pyz", "browser_download_url": "https://asset"}]},
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"payload")

    with pytest.raises(SystemExit) as exc:
        tool_deps.tool_deps_main([])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "blocking" in err


def test_tool_deps_main_allow_stale_downgrades_to_a_pass(monkeypatch, tmp_path, capsys):
    target = tmp_path / "cmru/tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    digest = sha256(b"payload").hexdigest()

    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda cfg: _cli_config(tmp_path, cmru_tool_deps=[_dep(sha256=digest)]),
    )
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {"version": "2.1.0", "tag": "assay-v2.1.0", "assets": []},
    )
    monkeypatch.setattr(
        tool_deps, "_github_get_json",
        lambda *a, **k: {"assets": [{"name": "assay-1.0.0.pyz", "browser_download_url": "https://asset"}]},
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"payload")

    tool_deps.tool_deps_main(["--allow-stale-tool-deps"])  # must NOT raise
    assert "OK" in capsys.readouterr().out


def test_tool_deps_main_json_output(monkeypatch, tmp_path, capsys):
    target = tmp_path / "cmru/tools/assay/assay-1.0.0.pyz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    digest = sha256(b"payload").hexdigest()

    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda cfg: _cli_config(tmp_path, cmru_tool_deps=[_dep(sha256=digest)]),
    )
    monkeypatch.setattr(
        tool_deps, "resolve_latest_release",
        lambda *a, **k: {
            "version": "1.0.0", "tag": "assay-v1.0.0",
            "assets": [{"name": "assay-1.0.0.pyz", "url": "https://asset"}],
        },
    )
    monkeypatch.setattr(tool_deps, "_download_asset", lambda *a, **k: b"payload")

    tool_deps.tool_deps_main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "consumer": "cmru", "project": "assay", "version": "1.0.0",
            "path": "tools/assay/assay-1.0.0.pyz", "sha256": digest,
            "highest_known_version": "1.0.0",
            "integrity": {"status": "pass", "reason": None, "detail": payload[0]["integrity"]["detail"]},
            "authenticity": {"status": "pass", "reason": None, "detail": payload[0]["authenticity"]["detail"]},
            "freshness": {"status": "pass", "reason": None, "detail": payload[0]["freshness"]["detail"]},
        }
    ]


def test_tool_deps_main_refresh_reports_unknown_provider(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda cfg: _cli_config(tmp_path))
    with pytest.raises(SystemExit) as exc:
        tool_deps.tool_deps_main(["--refresh", "ghost"])
    assert exc.value.code == 2
    assert "unknown project" in capsys.readouterr().err


def test_tool_deps_main_refresh_reports_no_matching_declaration(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda cfg: _cli_config(tmp_path))
    with pytest.raises(SystemExit) as exc:
        tool_deps.tool_deps_main(["--refresh", "assay"])
    assert exc.value.code == 2
    assert "no selected project declares" in capsys.readouterr().err


def test_tool_deps_main_refresh_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cmru.cli._resolve_config", lambda cfg: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(
        "cmru.cli.load_config",
        # A non-matching declaration (provider "ciu") alongside the matching one
        # (provider "assay") -- proves --refresh assay skips the unrelated entry
        # rather than touching every declared tool dependency.
        lambda cfg: _cli_config(tmp_path, cmru_tool_deps=[_dep(project="ciu"), _dep()]),
    )
    monkeypatch.setattr(
        tool_deps, "refresh_tool_dependency",
        lambda **kwargs: _dep(version="2.1.0", path="tools/assay/assay-2.1.0.pyz", sha256="c" * 64),
    )
    tool_deps.tool_deps_main(["--refresh", "assay"])  # must NOT raise
    out = capsys.readouterr().out
    assert "cmru: assay 1.0.0 -> 2.1.0" in out
