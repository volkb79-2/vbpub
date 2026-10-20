"""The Soulmask cgroups egg: valid JSON and a correctly rendering conditional LD_PRELOAD (SMT-02).

``game_stuff/`` has no gate lane of its own, so this lives with the gstammtisch-guide tests, which
the ``r0-r1`` lane runs.

The startup is rendered through the SAME two steps the ptero-eggs steamcmd entrypoint performs
(``steamcmd/entrypoint.sh`` in github.com/Ptero-Eggs/yolks): ``{{VAR}}`` -> ``${VAR}`` with sed, then
``eval``. Only two path tokens are swapped so nothing real runs: steamcmd becomes ``true`` and the
game binary becomes ``sh -c 'printf %s "$LD_PRELOAD"'``, which prints the LD_PRELOAD the game would
have received (the ``LD_PRELOAD=...`` assignment prefix still applies to it).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EGG = Path(__file__).resolve().parents[3] / "game_stuff" / "soulmask" / "egg-soulmask-rcon-ksm-cgroups.json"
EGGS = sorted(EGG.parent.glob("egg-*.json"))

STEAMCMD = "/home/container/steamcmd/steamcmd.sh"
GAME = "/home/container/WS/Binaries/Linux/WSServer-Linux-Shipping"
KSM = "/home/container/ksm-optin.so"
EAT = "/home/container/libeatmydata.so"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


def _egg() -> dict:
    return json.loads(EGG.read_text(encoding="utf-8"))


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
    script = (
        "MODIFIED_STARTUP=$(echo ${STARTUP} | sed -e 's/{{/${/g' -e 's/}}/}/g')\n"
        "eval ${MODIFIED_STARTUP}\n"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        env={**env, "STARTUP": startup, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


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
    import re

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


def test_eatmydata_applies_to_the_game_only_not_to_steamcmd() -> None:
    startup = _egg()["startup"]
    # The LD_PRELOAD assignment prefixes the game binary; the steamcmd branch comes before it.
    assert startup.index(STEAMCMD) < startup.index("LD_PRELOAD=") < startup.index(GAME)
    assert startup.count("LD_PRELOAD=") == 1
