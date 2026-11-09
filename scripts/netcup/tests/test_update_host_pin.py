"""The post-reinstall host-key pin update uses fake ssh-keyscan/ssh-keygen only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


NETCUP_DIR = Path(__file__).resolve().parents[1]
UPDATE_PIN = NETCUP_DIR / "update-host-pin.sh"


def _tools(tmp_path: Path, keyscan_mode: str = "key"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    keyscan = bindir / "ssh-keyscan"
    keyscan.write_text(
        "#!/bin/bash\n"
        "for last do :; done\n"
        "case \"$FAKE_KEYSCAN_MODE\" in\n"
        "  banner) printf '# %s:22 SSH-2.0-OpenSSH_10.0\\n' \"$last\" ;;&\n"
        "  two) printf '%s ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISECONDKEY\\n' \"$last\" ;;&\n"
        "esac\n"
        "printf '%s ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEY\\n' \"$last\"\n",
        encoding="utf-8",
    )
    keyscan.chmod(0o755)
    keygen = bindir / "ssh-keygen"
    keygen.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$SSH_KEYGEN_LOG\"\n"
        "if [ \"$1\" = '-lf' ]; then printf '256 %s fake (ED25519)\\n' \"$FAKE_FINGERPRINT\"; exit 0; fi\n"
        # Real OpenSSH leaves "<file>.old" with the previous contents after -R.
        "if [ \"$1\" = '-R' ]; then cp -p \"$4\" \"$4.old\"; awk -v host=\"$2\" '$1 != host { print }' \"$4\" > \"$4.tmp\"; mv \"$4.tmp\" \"$4\"; exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    keygen.chmod(0o755)
    return bindir


def _run_update(tmp_path: Path, pin: Path, fingerprint: str, keyscan_mode: str = "key"):
    bindir = _tools(tmp_path, keyscan_mode)
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        SSH_KEYGEN_LOG=str(tmp_path / "ssh-keygen.log"),
        FAKE_FINGERPRINT=fingerprint,
        FAKE_KEYSCAN_MODE=keyscan_mode,
    )
    return subprocess.run(
        ["bash", str(UPDATE_PIN), "v1001", "--pin-file", str(pin), "--fingerprint", "SHA256:approved"],
        cwd=NETCUP_DIR,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_host_pin_update_refuses_fingerprint_mismatch_without_changing_pin(tmp_path):
    pin = tmp_path / "ssh_known_hosts-v1001-20261105"
    original = "152.53.166.181 ssh-ed25519 OLDKEY\nv1001.vxxu.de ssh-ed25519 OLDHOSTKEY\n"
    pin.write_text(original, encoding="utf-8")

    result = _run_update(tmp_path, pin, "SHA256:unexpected")

    assert result.returncode != 0
    assert "fingerprint mismatch" in result.stderr
    assert pin.read_text(encoding="utf-8") == original
    assert "-lf" in (tmp_path / "ssh-keygen.log").read_text(encoding="utf-8")


def test_host_pin_update_leaves_nothing_but_the_pin_beside_it(tmp_path):
    pins = tmp_path / "pins"
    pins.mkdir()
    pin = pins / "ssh_known_hosts-v1001-20261105"
    pin.write_text("152.53.166.181 ssh-ed25519 OLDKEY\nv1001.vxxu.de ssh-ed25519 OLDHOSTKEY\n", encoding="utf-8")

    result = _run_update(tmp_path, pin, "SHA256:approved")

    assert result.returncode == 0, result.stderr
    # ssh-keygen -R leaves "<file>.old" holding the previous host key: no stale key material.
    assert sorted(path.name for path in pins.iterdir()) == [pin.name]
    refused = tmp_path / "refused"
    refused.mkdir()
    other = refused / "ssh_known_hosts-v1001-20261105"
    other.write_text("152.53.166.181 ssh-ed25519 OLDKEY\n", encoding="utf-8")
    (tmp_path / "second").mkdir()
    assert _run_update(tmp_path / "second", other, "SHA256:unexpected").returncode != 0
    assert sorted(path.name for path in refused.iterdir()) == [other.name]


def test_host_pin_update_replaces_only_the_reviewed_host_records(tmp_path):
    pin = tmp_path / "ssh_known_hosts-v1001-20261105"
    pin.write_text(
        "152.53.166.181 ssh-ed25519 OLDKEY\n"
        "v1001.vxxu.de ssh-ed25519 OLDHOSTKEY\n"
        "other.example ssh-ed25519 KEEPKEY\n",
        encoding="utf-8",
    )

    result = _run_update(tmp_path, pin, "SHA256:approved")

    assert result.returncode == 0, result.stderr
    updated = pin.read_text(encoding="utf-8")
    assert "OLDKEY" not in updated and "OLDHOSTKEY" not in updated
    assert "other.example ssh-ed25519 KEEPKEY" in updated
    assert "152.53.166.181 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEY" in updated
    assert pin.stat().st_mode & 0o777 == 0o600
    calls = (tmp_path / "ssh-keygen.log").read_text(encoding="utf-8")
    assert "-lf " in calls and "-R 152.53.166.181 -f " in calls and "-R v1001.vxxu.de -f " in calls


def test_host_pin_update_ignores_the_keyscan_banner_and_stores_only_the_key(tmp_path):
    pin = tmp_path / "ssh_known_hosts-v1001-20261105"
    pin.write_text("152.53.166.181 ssh-ed25519 OLDKEY\n", encoding="utf-8")

    result = _run_update(tmp_path, pin, "SHA256:approved", "banner")

    assert result.returncode == 0, result.stderr
    updated = pin.read_text(encoding="utf-8")
    assert "SSH-2.0-OpenSSH" not in updated
    assert updated.count("AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEY") == 1


def test_host_pin_update_still_refuses_two_scanned_keys(tmp_path):
    pin = tmp_path / "ssh_known_hosts-v1001-20261105"
    original = "152.53.166.181 ssh-ed25519 OLDKEY\n"
    pin.write_text(original, encoding="utf-8")

    result = _run_update(tmp_path, pin, "SHA256:approved", "two")

    assert result.returncode != 0
    assert "expected exactly one scanned ed25519 host key" in result.stderr
    assert pin.read_text(encoding="utf-8") == original
