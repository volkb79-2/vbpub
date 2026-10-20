"""The Soulmask cgroups egg: valid JSON and a correctly rendering conditional LD_PRELOAD (SMT-02).

``game_stuff/`` has no gate lane of its own, so this lives with the gstammtisch-guide tests, which
the ``r0-r1`` lane runs (``tests`` dir, no ``os_integration`` marker).

The startup is rendered through the REAL upstream entrypoint text, vendored unchanged as
``fixtures/yolks-steamcmd-entrypoint.sh`` (source, date and git blob sha in the ``.SOURCE`` file beside
it; the blob sha is asserted below). Only the tail of that script from ``# Replace Startup Variables``
is run (the earlier part sleeps, does ``cd /home/container`` and runs steamcmd). That tail is the
``sed`` substitution of ``{{VAR}}`` -> ``${VAR}``, the ``echo -e`` banner and ``eval ${MODIFIED_STARTUP}``.
Only two path tokens of the egg's startup are swapped so nothing real runs: steamcmd becomes ``true`` and
the game binary becomes ``sh -c 'printf %s "$LD_PRELOAD"'``, which prints the LD_PRELOAD the game would
have received (the ``LD_PRELOAD=...`` assignment prefix still applies to it).

A missing ``bash`` FAILS this module (``test_bash_is_available``); it is never skipped silently.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EGG = HERE.parents[2] / "game_stuff" / "soulmask" / "egg-soulmask-rcon-ksm-cgroups.json"
EGGS = sorted(EGG.parent.glob("egg-*.json"))
ENTRYPOINT = HERE / "fixtures" / "yolks-steamcmd-entrypoint.sh"
ENTRYPOINT_BLOB_SHA = "ff11716af2aa2882e475920a11482cd0ca51d0ce"
TAIL_MARKER = "# Replace Startup Variables"

STEAMCMD = "/home/container/steamcmd/steamcmd.sh"
GAME = "/home/container/WS/Binaries/Linux/WSServer-Linux-Shipping"
KSM = "/home/container/ksm-optin.so"
EAT = "/home/container/libeatmydata.so"


def _egg() -> dict:
    return json.loads(EGG.read_text(encoding="utf-8"))


def _entrypoint_tail() -> str:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert text.count(TAIL_MARKER) == 1
    tail = text[text.index(TAIL_MARKER):]
    # the two lines the egg relies on, exactly as upstream has them
    assert "MODIFIED_STARTUP=$(echo ${STARTUP} | sed -e 's/{{/${/g' -e 's/}}/}/g')" in tail
    assert "eval ${MODIFIED_STARTUP}" in tail
    return tail


def _render_preload(eatmydata: str | None) -> str:
    """Return the LD_PRELOAD value the game process would receive."""
    egg = _egg()
    startup = egg["startup"]
    assert startup.count(STEAMCMD) == 1 and startup.count(GAME) == 1
    startup = startup.replace(STEAMCMD, "true").replace(GAME, "sh -c 'printf %s \"$LD_PRELOAD\"' --")
    env = {v["env_variable"]: v["default_value"] for v in egg["variables"]}
    if eatmydata is None:
        env.pop("EATMYDATA")
    else:
        env["EATMYDATA"] = eatmydata
    proc = subprocess.run(
        ["bash", "-c", _entrypoint_tail()],
        env={**env, "STARTUP": startup, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # stdout = the echo -e banner line, then the game stand-in's printf (no trailing newline)
    banner, _, printed = proc.stdout.partition("\n")
    assert banner.startswith(":/home/container$ ")
    return printed


def test_bash_is_available() -> None:
    assert shutil.which("bash"), "bash is required: the egg render tests cannot run without it"


def test_vendored_entrypoint_is_the_recorded_upstream_text() -> None:
    data = ENTRYPOINT.read_bytes()
    blob_sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    assert blob_sha == ENTRYPOINT_BLOB_SHA


@pytest.mark.parametrize("path", EGGS, ids=lambda p: p.name)
def test_every_soulmask_egg_is_valid_json_with_a_startup(path: Path) -> None:
    egg = json.loads(path.read_text(encoding="utf-8"))
    assert egg["startup"].strip()
    assert all(v["env_variable"] for v in egg["variables"])


def test_eatmydata_variable_defaults_off_and_is_admin_only() -> None:
    (var,) = [v for v in _egg()["variables"] if v["env_variable"] == "EATMYDATA"]
    assert var["default_value"] == "0"  # the safe value: fsync stays real
    assert var["user_editable"] is False  # a crash-consistency trade-off is an admin decision
    assert "boolean" in var["rules"]


def test_every_startup_placeholder_has_a_variable() -> None:
    egg = _egg()
    used = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", egg["startup"]))
    defined = {v["env_variable"] for v in egg["variables"]}
    assert used <= defined, used - defined


def test_eatmydata_off_preloads_only_the_ksm_shim() -> None:
    assert _render_preload("0") == KSM


def test_eatmydata_on_appends_to_the_same_ld_preload() -> None:
    assert _render_preload("1") == f"{KSM}:{EAT}"


def test_eatmydata_unset_fails_closed_to_real_fsync() -> None:
    assert _render_preload(None) == KSM


def test_eatmydata_empty_fails_closed_to_real_fsync() -> None:
    assert _render_preload("") == KSM


def test_eatmydata_applies_to_the_game_only_not_to_steamcmd() -> None:
    startup = _egg()["startup"]
    # The LD_PRELOAD assignment prefixes the game binary; the steamcmd branch comes before it.
    assert startup.index(STEAMCMD) < startup.index("LD_PRELOAD=") < startup.index(GAME)
    assert startup.count("LD_PRELOAD=") == 1
