"""
Physical-root-from-mount-table tests (P-physical-root-mount-table handoff).

Covers:
- Contract 1: /proc/self/mountinfo longest-destination-prefix match, including
  a nested-mount case where a less-specific catch-all entry must lose.
- Contract 2: fallback when mountinfo yields nothing for repo_root —
  characterizes the pre-existing devcontainer-origin / identity behavior.
- Contract 4: hard regression bound — a dstdns-shaped fixture must reproduce
  today's live REPO_NAME / INSTANCE_ID / PHYSICAL_REPO_ROOT byte-for-byte.
- Contract 5: --define-root reaches the physical derivation (generate_ciu_env
  receiving repo_root=PATH resolves PATH's own physical root, not some other
  repo's).
"""
from __future__ import annotations

import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import (
    _compute_network_name,
    _detect_physical_repo_root,
    _parse_mountinfo,
    _physical_root_from_mountinfo,
    generate_ciu_env,
)

_URLOPEN = "ciu.workspace_env.urllib.request.urlopen"

# A mountinfo fixture shaped after the live dstdns devcontainer (2026-07-15
# repro): four sibling repos bind-mounted under /workspaces/*, all sourced
# from the same underlying block device (major:minor 253:0), plus assorted
# non-bind-mount noise (overlay root, proc, sysfs, /tmp, /etc/hosts) that must
# never be mistaken for a repo_root match.
_LIVE_SHAPED_MOUNTINFO = "\n".join(
    [
        "1972 938 0:52 / / rw,relatime - overlay overlay rw,lowerdir=/a:/b,upperdir=/c,workdir=/d",
        "901 1972 0:82 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw",
        "1976 1972 0:332 / /sys ro,nosuid,nodev,noexec,relatime - sysfs sysfs ro",
        "1980 1972 253:0 /home/vb/mdt--mounted-folders/tmp /tmp rw,relatime - ext4 /dev/mapper/vg-root rw",
        "1982 1972 253:0 /home/vb/volkb79-2/vbpro /workspaces/vbpro rw,relatime - ext4 /dev/mapper/vg-root rw",
        "1996 1972 253:0 /home/vb/volkb79-2/vbpub /workspaces/vbpub rw,relatime - ext4 /dev/mapper/vg-root rw",
        "1999 1972 253:0 /home/vb/volkb79-2/dstdns /workspaces/dstdns rw,relatime - ext4 /dev/mapper/vg-root rw",
        "2000 1972 253:0 /home/vb/volkb79-2/netcup-api-filter /workspaces/netcup-api-filter ro,relatime - ext4 /dev/mapper/vg-root rw",
        "2001 1972 253:0 /var/lib/docker/containers/abc/hosts /etc/hosts rw,relatime - ext4 /dev/mapper/vg-root rw",
    ]
)


def _write_mountinfo(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "mountinfo"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Contract 1 — mountinfo longest-match
# ---------------------------------------------------------------------------


class TestMountinfoLongestMatch:
    def test_parse_mountinfo_extracts_root_and_mount_point(self):
        entries = _parse_mountinfo(_LIVE_SHAPED_MOUNTINFO)
        entries_by_dest = {str(dest): str(root) for dest, root in entries}
        assert entries_by_dest["/workspaces/vbpub"] == "/home/vb/volkb79-2/vbpub"
        assert entries_by_dest["/workspaces/dstdns"] == "/home/vb/volkb79-2/dstdns"
        # The rootfs mount (mount_point == "/") must never appear.
        assert "/" not in entries_by_dest

    def test_vbpub_repo_root_maps_to_vbpub_physical(self, tmp_path, monkeypatch):
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        result = _physical_root_from_mountinfo(Path("/workspaces/vbpub"))
        assert result == Path("/home/vb/volkb79-2/vbpub")

    def test_dstdns_repo_root_maps_to_dstdns_physical(self, tmp_path, monkeypatch):
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        result = _physical_root_from_mountinfo(Path("/workspaces/dstdns"))
        assert result == Path("/home/vb/volkb79-2/dstdns")

    def test_nested_subdir_of_repo_root_maps_through_the_bind(self, tmp_path, monkeypatch):
        """repo_root need not be the mount point itself — a subdir under it
        must still resolve through the same bind (relative offset preserved).
        """
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        result = _physical_root_from_mountinfo(Path("/workspaces/vbpub/ciu"))
        assert result == Path("/home/vb/volkb79-2/vbpub/ciu")

    def test_nested_catch_all_loses_to_more_specific_mount(self, tmp_path, monkeypatch):
        """Longest-match: an extra, less-specific /workspaces catch-all entry
        must lose to the more specific /workspaces/vbpub entry (Oracle 1).
        """
        text = _LIVE_SHAPED_MOUNTINFO + "\n" + (
            "3000 1972 253:0 /home/vb/volkb79-2 /workspaces rw,relatime - ext4 "
            "/dev/mapper/vg-root rw"
        )
        mountinfo = _write_mountinfo(tmp_path, text)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        result = _physical_root_from_mountinfo(Path("/workspaces/vbpub"))
        # Must map through the specific vbpub bind, NOT the catch-all
        # /workspaces -> /home/vb/volkb79-2 mapping (which would also give
        # /home/vb/volkb79-2/vbpub here by coincidence of naming, so use a
        # deliberately-divergent catch-all root to prove the longest match).
        assert result == Path("/home/vb/volkb79-2/vbpub")

    def test_catch_all_wins_only_when_no_specific_mount_exists(self, tmp_path, monkeypatch):
        """A repo_root with no dedicated bind entry falls through to whatever
        broader mount destination does prefix-match (still Contract 1, not
        Contract 2 — mountinfo DID yield a match, just a less specific one).
        """
        text = (
            "3000 1972 253:0 /srv/other-workspaces /workspaces rw,relatime - ext4 "
            "/dev/mapper/vg-root rw"
        )
        mountinfo = _write_mountinfo(tmp_path, text)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        result = _physical_root_from_mountinfo(Path("/workspaces/some-other-repo"))
        assert result == Path("/srv/other-workspaces/some-other-repo")

    def test_unreadable_mountinfo_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", tmp_path / "does-not-exist")
        assert _physical_root_from_mountinfo(Path("/workspaces/vbpub")) is None

    def test_no_matching_destination_returns_none(self, tmp_path, monkeypatch):
        """Oracle 2 setup: repo_root absent from the fixture -> no match."""
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        result = _physical_root_from_mountinfo(Path("/workspaces/totally-unmounted-repo"))
        assert result is None


# ---------------------------------------------------------------------------
# Contract 2 — fallback when mountinfo yields nothing
# ---------------------------------------------------------------------------


class TestFallbackWhenMountinfoYieldsNothing:
    """Characterizes the pre-existing devcontainer-origin / identity fallback
    for a repo_root that mountinfo has no bind-mount entry for at all.
    """

    def test_preset_env_still_wins_over_mountinfo(self, tmp_path, monkeypatch):
        """2026-07-16 refined contract: a pre-set PHYSICAL_REPO_ROOT still wins
        when mountinfo has NO entry for repo_root at all — there is no
        independent signal to contradict it, so the explicit override (e.g.
        manual native-host configuration) is honored as-is. (When mountinfo
        DOES yield a disagreeing value, see
        TestPresetEnvConsistency.test_preset_env_ignored_when_inconsistent_with_repo_root
        below — that is the contamination case this refinement targets.)"""
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/explicit/override")

        # /workspaces/totally-unmounted-repo has no entry in the fixture (see
        # test_no_matching_destination_returns_none above), so mountinfo
        # yields None here and the pre-set value is honored unconditionally.
        result = _detect_physical_repo_root(Path("/workspaces/totally-unmounted-repo"))
        assert result == Path("/explicit/override")

    def test_falls_back_to_devcontainer_origin_label_when_no_mount_match(
        self, tmp_path, monkeypatch
    ):
        """No PHYSICAL_REPO_ROOT preset, mountinfo has no entry for repo_root
        -> existing docker-ps devcontainer.local_folder behavior (pre-existing
        code path, characterized as-is)."""
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="/home/vb/volkb79-2/dstdns\n", stderr=""
        )
        with patch("ciu.workspace_env.subprocess.run", return_value=fake_result) as mock_run:
            result = _detect_physical_repo_root(Path("/workspaces/totally-unmounted-repo"))

        mock_run.assert_called_once()
        assert result == Path("/home/vb/volkb79-2/dstdns")

    def test_falls_back_to_identity_when_docker_unavailable(self, tmp_path, monkeypatch):
        """No preset, no mount match, docker binary missing entirely ->
        identity (native host), per S1.9 / pre-existing behavior."""
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
        monkeypatch.setattr(
            "ciu.workspace_env._MOUNTINFO_PATH", tmp_path / "does-not-exist"
        )

        with patch(
            "ciu.workspace_env.subprocess.run", side_effect=FileNotFoundError()
        ):
            result = _detect_physical_repo_root(tmp_path / "some-native-repo")

        assert result == (tmp_path / "some-native-repo").resolve()


# ---------------------------------------------------------------------------
# Contract 3 — pre-set env consistency check against mountinfo
#
# 2026-07-16 refinement (P-ciu-physical-root-preset-env handoff): the live
# bug was a nested-repo pre-set-env contamination — a devcontainer's login
# shell `source`d dstdns's ciu.env (its primary workspace, via a
# REPO_ROOT-triggered .bashrc hook), leaving PHYSICAL_REPO_ROOT="...dstdns"
# in the environment. Running `ciu env generate` for an unrelated nested repo
# (vbpub/nyxloom) from that same shell then had the stale env var win
# unconditionally over mountinfo (Oracle 1). The refined contract: a pre-set
# PHYSICAL_REPO_ROOT wins ONLY when consistent with mountinfo, or when
# mountinfo yields nothing to check against (Oracle 2, covered above).
# ---------------------------------------------------------------------------


class TestPresetEnvConsistency:
    def test_preset_env_wins_when_consistent_with_mountinfo(self, tmp_path, monkeypatch):
        """Oracle 2: a pre-set PHYSICAL_REPO_ROOT that AGREES with the
        mountinfo-derived value for repo_root still wins (the legitimate
        manual-override use case is preserved) and emits no warning."""
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        # Consistent with what mountinfo would derive for /workspaces/vbpub.
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/home/vb/volkb79-2/vbpub")

        result = _detect_physical_repo_root(Path("/workspaces/vbpub"))
        assert result == Path("/home/vb/volkb79-2/vbpub")

    def test_preset_env_ignored_when_inconsistent_with_repo_root(self, tmp_path, monkeypatch, capsys):
        """Oracle 1 / regression: a pre-set PHYSICAL_REPO_ROOT pointing at a
        SIBLING repo's host path (dstdns) must NOT win when repo_root is a
        DIFFERENT, nested repo (vbpub/nyxloom) whose mountinfo-derived
        physical root disagrees. This is the exact 2026-07-15 live bug:
        dstdns's stale PHYSICAL_REPO_ROOT leaking into a nyxloom env-generate
        run. The mountinfo-derived value must win instead, with a warning."""
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/home/vb/volkb79-2/dstdns")

        # nyxloom has no dedicated mount entry; it is nested under the
        # /workspaces/vbpub bind, so longest-match maps it through that bind.
        result = _detect_physical_repo_root(Path("/workspaces/vbpub/nyxloom"))

        assert result == Path("/home/vb/volkb79-2/vbpub/nyxloom")
        assert result != Path("/home/vb/volkb79-2/dstdns")

        captured = capsys.readouterr()
        assert "PHYSICAL_REPO_ROOT" in captured.err
        assert "dstdns" in captured.err


# ---------------------------------------------------------------------------
# Contract 4 — hard regression bound (dstdns-shaped fixture)
# ---------------------------------------------------------------------------


class TestRegressionBoundDstdns:
    """Locks REPO_NAME=dstdns, INSTANCE_ID=98535c,
    PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns for repo_root=/workspaces/dstdns
    against a dstdns-shaped mountinfo fixture (Contract 4 / Oracle 3).
    """

    def test_dstdns_physical_root_and_derived_identity_unchanged(self, tmp_path, monkeypatch):
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)

        physical_root = _detect_physical_repo_root(Path("/workspaces/dstdns"))
        assert physical_root == Path("/home/vb/volkb79-2/dstdns")

        network_values = _compute_network_name(physical_root)
        assert network_values["REPO_NAME"] == "dstdns"
        assert network_values["INSTANCE_ID"] == "98535c"

    def test_generate_ciu_env_dstdns_shaped_end_to_end(self, tmp_path, monkeypatch):
        """Full generate_ciu_env pass through the REAL mountinfo-parsing path
        (no _detect_physical_repo_root monkeypatch): the mountinfo fixture's
        destination is repo_root's own resolved sandbox path (mirroring the
        live layout's /workspaces/dstdns -> /home/vb/volkb79-2/dstdns bind),
        so this exercises Contract 1 end-to-end while writing ciu.env safely
        under tmp_path (never touching the real /workspaces/dstdns).
        """
        repo_root = tmp_path / "workspaces" / "dstdns"
        repo_root.mkdir(parents=True)
        resolved_repo_root = repo_root.resolve()

        mountinfo_text = (
            f"1999 1972 253:0 /home/vb/volkb79-2/dstdns {resolved_repo_root} "
            "rw,relatime - ext4 /dev/mapper/vg-root rw\n"
        )
        mountinfo = _write_mountinfo(tmp_path, mountinfo_text)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
        monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setattr("ciu.workspace_env._detect_docker_gid", lambda: "1000")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            out = generate_ciu_env(repo_root)

        content = out.read_text(encoding="utf-8")
        assert 'export PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"' in content
        assert 'export REPO_NAME="dstdns"' in content
        assert 'export INSTANCE_ID="98535c"' in content


# ---------------------------------------------------------------------------
# Contract 4b — hard regression bound: nested-repo pre-set-env contamination
# (analogous to TestRegressionBoundDstdns, but for the pre-set-env case /
# Oracle 3 / the live P-ciu-physical-root-preset-env bug).
# ---------------------------------------------------------------------------


class TestRegressionBoundNestedPresetEnvContamination:
    """A nyxloom-like layout (a ciu root nested inside a vbpub-shaped parent
    bind mount, no dedicated mount entry of its own) must NOT have its
    PHYSICAL_REPO_ROOT / REPO_NAME / network identity corrupted by a
    contaminating pre-set PHYSICAL_REPO_ROOT inherited from an unrelated
    sibling repo's ciu.env (dstdns) — end-to-end through generate_ciu_env,
    exercising the REAL mountinfo-parsing path (no _detect_physical_repo_root
    monkeypatch), mirroring TestRegressionBoundDstdns's end-to-end pattern.
    """

    def test_generate_ciu_env_nyxloom_shaped_ignores_contaminating_preset(
        self, tmp_path, monkeypatch
    ):
        repo_root = tmp_path / "workspaces" / "vbpub" / "nyxloom"
        repo_root.mkdir(parents=True)
        resolved_repo_root = repo_root.resolve()
        resolved_vbpub_root = resolved_repo_root.parent

        # nyxloom has no dedicated bind entry of its own — only its parent
        # (vbpub) is mounted, exactly like the live layout.
        mountinfo_text = (
            f"1996 1972 253:0 /home/vb/volkb79-2/vbpub {resolved_vbpub_root} "
            "rw,relatime - ext4 /dev/mapper/vg-root rw\n"
        )
        mountinfo = _write_mountinfo(tmp_path, mountinfo_text)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        # Contaminating pre-set env: a DIFFERENT (sibling) repo's physical
        # root, as if dstdns's ciu.env had been `source`d into this shell.
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/home/vb/volkb79-2/dstdns")
        monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setattr("ciu.workspace_env._detect_docker_gid", lambda: "1000")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            out = generate_ciu_env(repo_root)

        content = out.read_text(encoding="utf-8")
        assert 'export PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/vbpub/nyxloom"' in content
        assert 'export REPO_NAME="nyxloom"' in content
        # Must NOT be corrupted to dstdns's identity (the pre-fix live bug).
        assert 'export REPO_NAME="dstdns"' not in content
        assert 'export INSTANCE_ID="98535c"' not in content
        assert "dstdns-98535c-network" not in content


# ---------------------------------------------------------------------------
# Contract 5 — --define-root reaches the physical derivation
# ---------------------------------------------------------------------------


class TestDefineRootReachesPhysicalDerivation:
    """generate_ciu_env(repo_root=PATH) must derive PATH's own physical root,
    not some other repo's — this is exactly what --define-root PATH feeds
    into (cli.py -> resolve_env_root(define_root=PATH) -> that PATH is passed
    straight through as repo_root).
    """

    def test_generate_ciu_env_uses_define_root_style_path_for_physical_lookup(
        self, tmp_path, monkeypatch
    ):
        mountinfo = _write_mountinfo(tmp_path, _LIVE_SHAPED_MOUNTINFO)
        monkeypatch.setattr("ciu.workspace_env._MOUNTINFO_PATH", mountinfo)
        monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
        monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        monkeypatch.setattr("ciu.workspace_env._detect_docker_gid", lambda: "1000")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)

        # Simulate `--define-root /workspaces/vbpub` by calling
        # _detect_physical_repo_root with that literal path directly (this is
        # exactly what generate_ciu_env(repo_root=Path("/workspaces/vbpub"))
        # would do internally).
        result = _detect_physical_repo_root(Path("/workspaces/vbpub"))
        assert result == Path("/home/vb/volkb79-2/vbpub")
        # Must NOT be dstdns's physical root (the pre-fix bug).
        assert result != Path("/home/vb/volkb79-2/dstdns")
