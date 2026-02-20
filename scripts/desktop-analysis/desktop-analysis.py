#!/usr/bin/env python3
"""
Linux Desktop Scaling Diagnostics
==================================
Merged from collect_scaling_diagnostics_Version14 + linux-desktop-analysis.

Gathers system info, performance data, and an efficiency assessment of desktop
scaling — including mouse smoothness and driver suitability for the hardware.

Supported distros : Ubuntu / Debian / Fedora / Arch / openSUSE / Pop!_OS /
                    Nobara / Bazzite / Manjaro / EndeavourOS / ...
Supported DEs     : GNOME / KDE Plasma / Cinnamon / COSMIC / Sway / Hyprland /
                    i3 / Xfce / MATE / LXQt / wlroots compositors / ...
Supported servers : X11 / Wayland

Usage
-----
    python3 linux-desktop-analysis.py [--scale FACTOR] [--non-interactive]
                                      [--output FILE] [--no-ps] [--mouse-test]
                                      [--allow-glxgears-fallback]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

REPORT_OUTPUT_FILE = f"desktop-analysis-report-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
TRACE_LOG: list[str] = []
CONSOLE_LOG: list[str] = []
_TRACE_SNIPPET_LIMIT = 240


def _timestamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def trace(msg: str) -> None:
    TRACE_LOG.append(f"[{_timestamp()}] {msg}")


def log_console(msg: str) -> None:
    CONSOLE_LOG.append(f"[{_timestamp()}] {msg}")

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

C_RESET  = "\033[0m"
C_GREEN  = "\033[32m"
C_YELLOW = "\033[33m"
C_RED    = "\033[31m"
C_BLUE   = "\033[34m"


def cprint(color: str, msg: str) -> None:
    log_console(msg)
    print(f"{color}{msg}{C_RESET}")


def _section(title: str) -> None:
    log_console(f"== {title} ==")
    print(f"\n{C_BLUE}{'=' * 62}{C_RESET}")
    print(f"{C_BLUE}  {title}{C_RESET}")
    print(f"{C_BLUE}{'=' * 62}{C_RESET}")


def _bullet(label: str, value: object) -> None:
    log_console(f"{label}: {value}")
    print(f"  {label:<34} {value}")


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: list, timeout: int = 20, env: Optional[dict] = None) -> dict:
    """Run a subprocess and return a result dict."""
    cmd_str = " ".join(cmd)
    trace(f"run_cmd start: cmd='{cmd_str}', timeout={timeout}")
    try:
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=env,
        )
        result = {
            "ok": r.returncode == 0,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
            "returncode": r.returncode,
            "error": "",
            "cmd": cmd_str,
        }
        stdout_excerpt = result["stdout"][:_TRACE_SNIPPET_LIMIT].replace("\n", "\\n")
        stderr_excerpt = result["stderr"][:_TRACE_SNIPPET_LIMIT].replace("\n", "\\n")
        trace(
            f"run_cmd done: rc={result['returncode']} ok={result['ok']} "
            f"stdout='{stdout_excerpt}' stderr='{stderr_excerpt}'"
        )
        return result
    except FileNotFoundError:
        trace(f"run_cmd error: command not found: '{cmd_str}'")
        return {"ok": False, "stdout": "", "stderr": "", "returncode": 127,
                "error": "command not found", "cmd": cmd_str}
    except subprocess.TimeoutExpired as exc:
        def _to_text(value) -> str:
            if value is None:
                return ""
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace").strip()
            return str(value).strip()

        stdout = _to_text(exc.stdout)
        stderr = _to_text(exc.stderr)
        trace(
            "run_cmd timeout: "
            f"cmd='{cmd_str}' stdout='{stdout[:_TRACE_SNIPPET_LIMIT].replace(chr(10), '\\n')}' "
            f"stderr='{stderr[:_TRACE_SNIPPET_LIMIT].replace(chr(10), '\\n')}'"
        )
        return {"ok": False, "stdout": stdout, "stderr": stderr, "returncode": 124,
            "error": "timeout", "cmd": cmd_str}
    except Exception as exc:  # noqa: BLE001
        trace(f"run_cmd exception: cmd='{cmd_str}' error='{exc}'")
        return {"ok": False, "stdout": "", "stderr": "", "returncode": 1,
            "error": str(exc), "cmd": cmd_str}


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def command_exists_any(commands: list[str]) -> bool:
    return any(command_exists(cmd) for cmd in commands)


def resolve_command_variant(tool: str) -> str:
    """Resolve command aliases that differ by distro/version."""
    if tool == "qdbus":
        for candidate in ("qdbus", "qdbus6", "qdbus-qt6"):
            if command_exists(candidate):
                return candidate
        return "qdbus"
    return tool


def tool_available(tool: str) -> bool:
    """Tool availability check with alias support."""
    if tool == "qdbus":
        return command_exists_any(["qdbus", "qdbus6", "qdbus-qt6"])
    return command_exists(tool)


def read_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# OS / distro detection
# ---------------------------------------------------------------------------

def read_os_release() -> dict:
    data: dict = {}
    for line in read_file("/etc/os-release").splitlines():
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"')
    return data


def detect_base_distro(osr: dict) -> str:
    os_id   = osr.get("ID", "").lower()
    id_like = osr.get("ID_LIKE", "").lower()
    if os_id == "pop" or "ubuntu" in id_like or os_id == "ubuntu":
        return "ubuntu"
    if "fedora" in id_like or os_id in ("fedora", "nobara", "bazzite"):
        return "fedora"
    if "arch" in id_like or os_id in ("arch", "manjaro", "endeavouros"):
        return "arch"
    if "debian" in id_like or os_id == "debian":
        return "debian"
    if "suse" in id_like or os_id in ("opensuse", "suse"):
        return "suse"
    return "unknown"


def detect_pkg_manager() -> Optional[str]:
    for pm in ("apt-get", "dnf", "pacman", "zypper", "rpm-ostree"):
        if command_exists(pm):
            return pm
    return None


def detect_immutable() -> bool:
    return command_exists("rpm-ostree")


# ---------------------------------------------------------------------------
# Privilege / sudo helpers
# ---------------------------------------------------------------------------

def preflight_privileges() -> dict:
    info: dict = {
        "is_root": os.geteuid() == 0,
        "sudo_available": command_exists("sudo"),
        "sudo_ok": False,
        "notes": [],
    }
    if info["is_root"]:
        info["sudo_ok"] = True
        return info
    if not info["sudo_available"]:
        info["notes"].append("sudo not available; run as root for privileged checks.")
        return info
    res = run_cmd(["sudo", "-n", "true"])
    if res["ok"]:
        info["sudo_ok"] = True
        info["notes"].append("sudo -n succeeded (passwordless).")
        return info
    cprint(C_YELLOW, "This script needs sudo for some checks. Please enter your password if prompted.")
    res = run_cmd(["sudo", "-v"])
    info["sudo_ok"] = res["ok"]
    if not res["ok"]:
        info["notes"].append("sudo authentication failed; privileged checks will be skipped.")
    return info


def ensure_sudo(cmd: list, priv: dict) -> Optional[list]:
    if priv["is_root"]:
        return cmd
    if priv["sudo_ok"]:
        return ["sudo"] + cmd
    return None


# ---------------------------------------------------------------------------
# Package auto-install
# ---------------------------------------------------------------------------

# tool -> {distro_family: package_name or [fallback_package_names]}
_PKG_MAP: dict = {
    "lspci":          {"ubuntu": "pciutils",      "debian": "pciutils",      "fedora": "pciutils",        "arch": "pciutils",        "suse": "pciutils"},
    "lshw":           {"ubuntu": "lshw",           "debian": "lshw",          "fedora": "lshw",            "arch": "lshw",            "suse": "lshw"},
    "glxinfo":        {"ubuntu": "mesa-utils",     "debian": "mesa-utils",    "fedora": "mesa-demos",      "arch": "mesa-utils",      "suse": "Mesa-demo-apps"},
    "glxgears":       {"ubuntu": "mesa-utils",     "debian": "mesa-utils",    "fedora": "mesa-demos",      "arch": "mesa-utils",      "suse": "Mesa-demo-apps"},
    "vulkaninfo":     {"ubuntu": "vulkan-tools",   "debian": "vulkan-tools",  "fedora": "vulkan-tools",    "arch": "vulkan-tools",    "suse": "vulkan-tools"},
    "glmark2":        {"ubuntu": "glmark2",        "debian": "glmark2",       "fedora": "glmark2",         "arch": "glmark2",         "suse": "glmark2"},
    "wayland-info":   {"ubuntu": "wayland-utils",  "debian": "wayland-utils", "fedora": "wayland-utils",   "arch": "wayland-utils",   "suse": "wayland-utils"},
    "wlr-randr":      {"ubuntu": "wlr-randr",      "debian": "wlr-randr",     "fedora": "wlr-randr",       "arch": "wlr-randr",       "suse": "wlr-randr"},
    "xrandr":         {"ubuntu": "x11-xserver-utils", "debian": "x11-xserver-utils", "fedora": "xrandr", "arch": "xorg-xrandr", "suse": "xrandr"},
    "gdbus":          {"ubuntu": "libglib2.0-bin", "debian": "libglib2.0-bin", "fedora": "glib2", "arch": "glib2", "suse": "glib2-tools"},
    "qdbus":          {
        "ubuntu": ["qt6-tools-dev-tools", "qttools5-dev-tools"],
        "debian": ["qt6-tools-dev-tools", "qttools5-dev-tools"],
        "fedora": ["qt6-qttools", "qt5-qttools"],
        "arch": ["qt6-tools", "qt5-tools"],
        "suse": ["qt6-tools", "libqt5-qttools"],
    },
    "mangohud":       {"ubuntu": "mangohud", "debian": "mangohud", "fedora": "mangohud", "arch": "mangohud", "suse": "mangohud"},
    "xlsclients":     {"ubuntu": "x11-utils",      "debian": "x11-utils",     "fedora": "xorg-x11-utils",  "arch": "xorg-xlsclients", "suse": "xorg-x11-utils"},
    "libinput":       {"ubuntu": "libinput-tools",  "debian": "libinput-tools","fedora": "libinput",        "arch": "libinput",        "suse": "libinput-tools"},
    "kscreen-doctor": {"ubuntu": "kscreen",        "debian": "kscreen",       "fedora": "kscreen",         "arch": "kscreen",         "suse": "kscreen"},
}


def _package_exists(pm: str, package: str) -> bool:
    """Best-effort package existence check for distro package managers."""
    if not package:
        return False

    if pm == "apt-get" and command_exists("apt-cache"):
        res = run_cmd(["apt-cache", "show", package], timeout=20)
        return res.get("ok", False) and bool(res.get("stdout"))
    if pm == "dnf":
        res = run_cmd(["dnf", "-q", "info", package], timeout=30)
        return res.get("ok", False)
    if pm == "pacman":
        res = run_cmd(["pacman", "-Si", package], timeout=20)
        return res.get("ok", False)
    if pm == "zypper":
        res = run_cmd(["zypper", "--non-interactive", "info", package], timeout=30)
        return res.get("ok", False)

    return True


def _resolve_package_candidate(pm: Optional[str], package_spec) -> Optional[str]:
    """Resolve package candidate when multiple names exist across distro versions."""
    if isinstance(package_spec, str):
        return package_spec

    if isinstance(package_spec, list):
        if not package_spec:
            return None
        if not pm:
            return package_spec[0]
        for candidate in package_spec:
            if _package_exists(pm, candidate):
                return candidate
        return package_spec[0]

    return None


def _packages_for_distro(missing_cmds: list, base_distro: str) -> list:
    pm = detect_pkg_manager()
    pkgs = set()
    for cmd in missing_cmds:
        pkg_map = _PKG_MAP.get(cmd, {})
        package_spec = pkg_map.get(base_distro) or pkg_map.get("ubuntu")
        pkg = _resolve_package_candidate(pm, package_spec)
        if pkg:
            pkgs.add(pkg)
    return sorted(pkgs)


def install_packages(pm: str, packages: list, priv: dict) -> dict:
    if not packages:
        return {"ok": True, "installed": [], "logs": []}
    logs = []
    if pm == "apt-get":
        cmd = ensure_sudo(["apt-get", "update", "-qq"], priv)
        if cmd:
            logs.append(run_cmd(cmd, timeout=120))
        cmd = ensure_sudo(["apt-get", "install", "-y", "-qq"] + packages, priv)
        if cmd:
            logs.append(run_cmd(cmd, timeout=300))
    elif pm == "dnf":
        cmd = ensure_sudo(["dnf", "-y", "-q", "install"] + packages, priv)
        if cmd:
            logs.append(run_cmd(cmd, timeout=300))
    elif pm == "pacman":
        cmd = ensure_sudo(["pacman", "-Sy", "--noconfirm", "--quiet"] + packages, priv)
        if cmd:
            logs.append(run_cmd(cmd, timeout=300))
    elif pm == "zypper":
        cmd = ensure_sudo(["zypper", "--non-interactive", "install"] + packages, priv)
        if cmd:
            logs.append(run_cmd(cmd, timeout=300))
    ok = all(lg.get("ok") for lg in logs) if logs else False
    return {"ok": ok, "installed": packages if ok else [], "logs": logs}


# ---------------------------------------------------------------------------
# Process / session detection
# ---------------------------------------------------------------------------

def detect_processes() -> list:
    res = run_cmd(["ps", "-eo", "comm,args"])
    return res["stdout"].splitlines() if res["ok"] else []


def parse_xwayland_display(processes: list) -> Optional[str]:
    for p in processes:
        if p.startswith("Xwayland"):
            m = re.search(r"\s(:\d+)\s", p)
            if m:
                return m.group(1)
    return None


def guess_wayland_display(uid: int) -> Optional[str]:
    base = f"/run/user/{uid}"
    if not os.path.isdir(base):
        return None
    candidates = sorted(f for f in os.listdir(base) if f.startswith("wayland-"))
    return candidates[-1] if candidates else None


def detect_session_type(env: dict) -> str:
    return env.get("XDG_SESSION_TYPE", "unknown") or "unknown"


def infer_desktop_session(env: dict, processes: list) -> str:
    desktop = (
        env.get("XDG_CURRENT_DESKTOP")
        or env.get("XDG_SESSION_DESKTOP")
        or env.get("DESKTOP_SESSION")
        or ""
    ).strip()
    if desktop:
        return desktop
    for name, procs in [
        ("COSMIC",   ["cosmic-comp"]),
        ("GNOME",    ["gnome-shell"]),
        ("KDE",      ["plasmashell"]),
        ("Cinnamon", ["cinnamon"]),
        ("Xfce",     ["xfce4-session"]),
        ("Sway",     ["sway"]),
        ("Hyprland", ["Hyprland"]),
        ("i3",       ["i3"]),
        ("MATE",     ["mate-session"]),
        ("LXQt",     ["lxqt-session"]),
        ("Openbox",  ["openbox"]),
        ("Budgie",   ["budgie-wm"]),
    ]:
        for p in processes:
            if any(p.startswith(proc) for proc in procs):
                return name
    return "unknown"


def detect_compositor_wm(processes: list) -> dict:
    known = [
        "cosmic-comp", "kwin_wayland", "kwin_x11", "mutter", "gnome-shell",
        "muffin", "xfwm4", "sway", "Hyprland", "weston", "picom", "compton",
        "i3", "bspwm", "awesome", "qtile", "marco", "openbox", "budgie-wm",
    ]
    found = []
    for p in processes:
        for k in known:
            if (p.startswith(k) or f" {k} " in p) and k not in found:
                found.append(k)
    compositor = found[0] if found else "unknown"
    return {"window_manager": compositor, "compositor": compositor, "found": found}


# ---------------------------------------------------------------------------
# Input device discovery  (fixes NameError from Version14)
# ---------------------------------------------------------------------------

def parse_proc_input_devices() -> list:
    """Parse /proc/bus/input/devices; return list of device dicts."""
    devices: list = []
    content = read_file("/proc/bus/input/devices")
    if not content:
        return devices
    for block in content.split("\n\n"):
        dev: dict = {}
        for line in block.splitlines():
            if line.startswith("N: Name="):
                dev["name"] = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("H: Handlers="):
                dev["handlers"] = line.split("=", 1)[1].strip()
            elif line.startswith("B: EV="):
                dev["ev_bits"] = line.split("=", 1)[1].strip()
        if dev:
            devices.append(dev)
    return devices


def select_mouse_event_device(devices: list) -> Optional[str]:
    """Return /dev/input/eventN for the best mouse/pointer device found."""
    # First pass: name-based
    for dev in devices:
        name     = dev.get("name", "").lower()
        handlers = dev.get("handlers", "")
        if any(k in name for k in ("mouse", "pointer", "trackpad", "touchpad", "trackball")):
            m = re.search(r"event(\d+)", handlers)
            if m:
                return f"/dev/input/event{m.group(1)}"
    # Second pass: handler-based
    for dev in devices:
        handlers = dev.get("handlers", "")
        if "mouse" in handlers.lower():
            m = re.search(r"event(\d+)", handlers)
            if m:
                return f"/dev/input/event{m.group(1)}"
    return None


# ---------------------------------------------------------------------------
# Graphics and driver information
# ---------------------------------------------------------------------------

def gather_graphics_info(run_user_cmd, priv: dict) -> dict:
    info: dict = {}
    cmd = ensure_sudo(["lspci", "-nnk"], priv)
    if cmd:
        info["lspci"] = run_cmd(cmd)
    cmd = ensure_sudo(["lshw", "-C", "display"], priv)
    if cmd:
        info["lshw"] = run_cmd(cmd, timeout=30)
    info["lsmod"] = run_cmd(["lsmod"])
    if command_exists("glxinfo"):
        info["glxinfo"] = run_user_cmd(["glxinfo", "-B"])
    if command_exists("vulkaninfo"):
        info["vulkaninfo"] = run_user_cmd(["vulkaninfo", "--summary"], 30)
    return info


def gather_driver_info(lsmod_text: str, priv: dict) -> dict:
    loaded_all = {line.split()[0] for line in lsmod_text.splitlines() if line.strip()}
    gpu_modules = {"nvidia", "nouveau", "i915", "xe", "amdgpu", "radeon"}
    loaded_gpu = sorted(loaded_all & gpu_modules)
    drivers: dict = {}
    for mod in loaded_gpu:
        def _mf(field: str, m: str = mod) -> str:
            cmd = ensure_sudo(["modinfo", "-F", field, m], priv)
            return run_cmd(cmd).get("stdout", "") if cmd else ""
        drivers[mod] = {"module": mod, "version": _mf("version"), "filename": _mf("filename")}
    driver_type = "proprietary" if "nvidia" in loaded_gpu else "open-source"
    return {"drivers": drivers, "driver_type": driver_type, "loaded": loaded_gpu}


def assess_driver_suitability(glxinfo_stdout: str, driver_info: dict) -> tuple:
    """Return (suitable: bool, notes: str)."""
    renderer = glxinfo_stdout.lower()
    loaded   = driver_info.get("loaded", [])
    notes    = []

    if any(k in renderer for k in ("llvmpipe", "softpipe", "software rasterizer")):
        notes.append(
            "Software rasteriser active — GPU acceleration is NOT in use. "
            "Install the appropriate driver package."
        )
        return False, "⚠  " + " ".join(notes)

    if "nvidia" in loaded:
        notes.append("NVIDIA proprietary driver active (module: nvidia) — suitable.")
        return True, "✓  " + " ".join(notes)
    if "nouveau" in loaded:
        notes.append(
            "nouveau (open-source) driver for NVIDIA GPU. "
            "Consider proprietary driver for full performance."
        )
        return False, "⚠  " + " ".join(notes)
    if "amdgpu" in loaded or "radeon" in loaded:
        mod = "amdgpu" if "amdgpu" in loaded else "radeon"
        notes.append(f"AMD open-source driver '{mod}' active — suitable.")
        return True, "✓  " + " ".join(notes)
    if "i915" in loaded or "xe" in loaded:
        mod = "xe" if "xe" in loaded else "i915"
        notes.append(f"Intel driver '{mod}' active — suitable.")
        return True, "✓  " + " ".join(notes)

    for virt in ("virtualbox", "vmware", "virtio", "vboxvideo", "qxl", "bochs"):
        if virt in renderer:
            notes.append(
                f"Virtual GPU detected ('{virt}'). Performance depends on "
                "host GPU and guest additions."
            )
            return True, "✓  " + " ".join(notes)

    if renderer:
        notes.append(f"Driver not identified via lsmod; renderer: '{renderer[:80]}'.")
    else:
        notes.append("No GPU driver identified — glxinfo unavailable or no DISPLAY.")
    return True, "ℹ  " + " ".join(notes)


# ---------------------------------------------------------------------------
# Display info and scale detection
# ---------------------------------------------------------------------------

def gather_display_info(run_user_cmd) -> dict:
    info: dict = {}
    if command_exists("wayland-info"):
        info["wayland_info"] = run_user_cmd(["wayland-info"])
    if command_exists("wlr-randr"):
        info["wlr_randr"] = run_user_cmd(["wlr-randr"])
    return info


def detect_current_scale(
    session_env: dict,
    desktop: str,
    run_user_cmd,
    home_dir: str,
) -> tuple:
    """
    Try every available method to detect the current desktop scale.
    Priority order:
      1. wlr-randr (Sway / Hyprland / wlroots / COSMIC)
      2. kscreen-doctor (KDE Plasma 6 Wayland)
      3. gsettings integer scale (GNOME)
      4. kreadconfig5 (KDE Plasma 5)
      5. xfconf-query (Xfce)
      6. COSMIC config files
      7. Mutter gdbus (GNOME fractional)
      8. xrandr transform matrix (X11)
      9. HiDPI env vars (GDK_SCALE, QT_SCALE_FACTOR)
     10. Fallback 1x
    Returns (factor: float, source: str).
    """
    de = desktop.lower()

    # 1. wlr-randr (Sway, Hyprland, wlroots, COSMIC)
    if command_exists("wlr-randr"):
        res = run_user_cmd(["wlr-randr"])
        if res["ok"]:
            m = re.search(r"[Ss]cale:\s*([0-9.]+)", res["stdout"])
            if m:
                return float(m.group(1)), "wlr-randr"

    # 2. kscreen-doctor (KDE Wayland)
    if command_exists("kscreen-doctor"):
        res = run_user_cmd(["kscreen-doctor", "--outputs"])
        if res["ok"]:
            m = re.search(r"Scale:\s*([0-9.]+)", res["stdout"])
            if m:
                return float(m.group(1)), "kscreen-doctor"

    # 3. gsettings (GNOME integer scale)
    if command_exists("gsettings"):
        res = run_user_cmd(["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"])
        if res["ok"]:
            m = re.search(r"(\d+)", res["stdout"])
            if m and int(m.group(1)) >= 1:
                factor = float(m.group(1))
                res2 = run_user_cmd([
                    "gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor",
                ])
                if res2["ok"]:
                    m2 = re.search(r"([0-9.]+)", res2["stdout"])
                    if m2 and float(m2.group(1)) != 1.0:
                        return round(factor * float(m2.group(1)), 4), "gsettings (integer x text-scaling-factor)"
                return factor, "gsettings scaling-factor"

    # 4. kreadconfig5 (KDE Plasma 5)
    if command_exists("kreadconfig5"):
        res = run_user_cmd(["kreadconfig5", "--group", "KScreen", "--key", "ScaleFactor"])
        if res["ok"] and res["stdout"].strip():
            try:
                return float(res["stdout"].strip()), "kreadconfig5 KScreen/ScaleFactor"
            except ValueError:
                pass

    # 5. xfconf-query (Xfce)
    if command_exists("xfconf-query"):
        res = run_user_cmd(["xfconf-query", "-c", "xsettings", "-p", "/Gdk/WindowScalingFactor"])
        if res["ok"] and res["stdout"].strip():
            try:
                return float(res["stdout"].strip()), "xfconf-query Gdk/WindowScalingFactor"
            except ValueError:
                pass

    # 6. COSMIC config files
    if "cosmic" in de:
        cosmic_cfg = discover_cosmic_configs(home_dir)
        for path in cosmic_cfg.get("files", []):
            try:
                content = Path(path).read_text(errors="ignore")
                m = re.search(r"scale[^=\n]*=\s*([0-9.]+)", content, re.IGNORECASE)
                if m:
                    return float(m.group(1)), f"COSMIC config ({path})"
            except OSError:
                pass

    # 7. Mutter fractional (GNOME 47+)
    if command_exists("gdbus"):
        res = run_user_cmd([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Mutter.DisplayConfig",
            "--object-path", "/org/gnome/Mutter/DisplayConfig",
            "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState",
        ])
        if res["ok"]:
            m = re.search(r"<double ([0-9.]+)>", res["stdout"])
            if m:
                return float(m.group(1)), "gsettings (Mutter fractional)"

    # 8. xrandr transform (X11 fallback)
    if command_exists("xrandr") and session_env.get("DISPLAY"):
        res = run_user_cmd(["xrandr", "--verbose"])
        if res["ok"]:
            m = re.search(r"Transform:\s+([0-9.]+)\s", res["stdout"])
            if m:
                sx = float(m.group(1))
                if sx != 1.0 and sx > 0:
                    return round(1.0 / sx, 4), "xrandr transform matrix"

    # 9. HiDPI env vars
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR"):
        val = session_env.get(var, "")
        if val:
            try:
                return float(val), f"env {var}"
            except ValueError:
                pass

    return 1.0, "fallback (assumed 1x)"


def set_scale_programmatic(desktop: str, factor: float, run_user_cmd) -> tuple:
    """
    Attempt to apply the given scale programmatically.
    Returns (ok: bool, method: str).
    """
    # wlr-randr (Sway, Hyprland, COSMIC, wlroots)
    if command_exists("wlr-randr"):
        res = run_user_cmd(["wlr-randr"])
        if res["ok"]:
            m = re.match(r"^(\S+)\s+", res["stdout"])
            if m:
                output = m.group(1)
                res2 = run_user_cmd(["wlr-randr", "--output", output, "--scale", str(factor)])
                if res2["ok"]:
                    return True, f"wlr-randr --output {output} --scale {factor}"

    # kscreen-doctor (KDE)
    if command_exists("kscreen-doctor"):
        res = run_user_cmd(["kscreen-doctor", f"output.1.scale.{factor}"])
        if res["ok"]:
            return True, "kscreen-doctor"

    # gsettings (GNOME)
    if command_exists("gsettings"):
        int_factor = max(1, round(factor))
        res = run_user_cmd([
            "gsettings", "set", "org.gnome.desktop.interface",
            "scaling-factor", str(int_factor),
        ])
        if res["ok"]:
            return True, "gsettings"

    # xfconf-query (Xfce)
    if command_exists("xfconf-query"):
        int_factor = max(1, round(factor))
        res = run_user_cmd([
            "xfconf-query", "-c", "xsettings",
            "-p", "/Gdk/WindowScalingFactor", "-s", str(int_factor),
        ])
        if res["ok"]:
            return True, "xfconf-query"

    # xrandr (X11 generic)
    if command_exists("xrandr"):
        res = run_user_cmd(["xrandr", "--query"])
        if res["ok"]:
            connected = [
                line.split()[0]
                for line in res["stdout"].splitlines()
                if " connected" in line
            ]
            if connected:
                scale_str = f"{factor}x{factor}"
                res2 = run_user_cmd(["xrandr", "--output", connected[0], "--scale", scale_str])
                if res2["ok"]:
                    return True, f"xrandr --output {connected[0]} --scale {scale_str}"

    return False, ""


# ---------------------------------------------------------------------------
# COSMIC config helpers
# ---------------------------------------------------------------------------

def discover_cosmic_configs(home_dir: str) -> dict:
    candidates = [
        os.path.join(home_dir, ".config", "cosmic"),
        os.path.join(home_dir, ".config", "cosmic-comp"),
        os.path.join(home_dir, ".config", "cosmic-settings"),
        os.path.join(home_dir, ".local", "share", "cosmic"),
        "/etc/xdg/cosmic",
        "/etc/cosmic",
        "/usr/share/cosmic",
    ]
    found = [p for p in candidates if os.path.exists(p)]
    files = []
    for base in found:
        for root, _, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith((".toml", ".json", ".yaml", ".yml", ".conf", ".ini", ".ron")):
                    files.append(os.path.join(root, fn))
    return {"dirs": found, "files": files}


def extract_scale_from_configs(cfg_files: list) -> list:
    hits = []
    for path in cfg_files:
        try:
            content = Path(path).read_text(errors="ignore").lower()
            if "scale" in content or "fraction" in content:
                hits.append(path)
        except OSError:
            pass
    return hits


# ---------------------------------------------------------------------------
# XWayland analysis
# ---------------------------------------------------------------------------

def analyze_xwayland(run_user_cmd, session_env: dict) -> dict:
    data: dict = {"xwayland_clients": None, "xwayland_clients_list": "", "notes": ""}
    if not session_env.get("DISPLAY"):
        data["notes"] = "DISPLAY not set; cannot query XWayland clients"
        return data
    if command_exists("xlsclients"):
        res = run_user_cmd(["xlsclients"])
        if res.get("ok"):
            clients = [ln for ln in res["stdout"].splitlines() if ln.strip()]
            data["xwayland_clients"] = len(clients)
            data["xwayland_clients_list"] = "\n".join(clients[:50])
            if len(clients) > 50:
                data["notes"] = "xwayland client list truncated to 50 entries"
    return data


def detect_fps_strategy(session_type: str, desktop: str, compositor: str) -> dict:
    """Determine preferred compositor-present FPS strategy for current environment."""
    de = (desktop or "").lower()
    comp = (compositor or "").lower()
    is_wayland = "wayland" in (session_type or "").lower()

    if is_wayland and ("gnome" in de or "mutter" in comp):
        return {
            "id": "gnome-wayland",
            "name": "GNOME/Mutter compositor-present strategy",
            "primary": "Mutter/GNOME telemetry",
            "fallback": "Output refresh + benchmark + frame-time proxy",
            "tools": ["gdbus", "wayland-info", "xrandr"],
        }
    if is_wayland and ("kde" in de or "kwin" in comp or "plasma" in de):
        return {
            "id": "kde-wayland",
            "name": "KDE/KWin compositor-present strategy",
            "primary": "KWin telemetry/debug channels",
            "fallback": "Output refresh + benchmark + frame-time proxy",
            "tools": ["kscreen-doctor", "qdbus", "xrandr"],
        }
    if is_wayland and "hypr" in comp:
        return {
            "id": "hyprland-wayland",
            "name": "Hyprland compositor-present strategy",
            "primary": "hyprctl runtime telemetry",
            "fallback": "Output refresh + benchmark + frame-time proxy",
            "tools": ["hyprctl", "xrandr"],
        }
    if is_wayland and ("sway" in comp or "wlroots" in comp or "wayfire" in comp or "labwc" in comp):
        return {
            "id": "wlroots-wayland",
            "name": "wlroots compositor-present strategy",
            "primary": "Compositor telemetry/debug channels",
            "fallback": "Output topology + benchmark + frame-time proxy",
            "tools": ["wlr-randr", "wayland-info", "xrandr"],
        }
    if is_wayland and "cosmic" in comp:
        return {
            "id": "cosmic-wayland",
            "name": "COSMIC compositor-present strategy",
            "primary": "cosmic-comp telemetry (when available)",
            "fallback": "Output topology + benchmark + frame-time proxy",
            "tools": ["wlr-randr", "wayland-info", "xrandr"],
        }
    return {
        "id": "x11-generic",
        "name": "X11 compositor/present strategy",
        "primary": "X compositor telemetry when available",
        "fallback": "Output refresh + benchmark + frame-time proxy",
        "tools": ["xrandr"],
    }


def gather_fps_strategy_findings(strategy: dict, run_user_cmd, session_type: str) -> dict:
    """Collect environment findings for selected FPS strategy."""
    findings = {
        "strategy": strategy,
        "tool_availability": {},
        "probes": {},
        "notes": [],
    }

    for tool in strategy.get("tools", []):
        findings["tool_availability"][tool] = tool_available(tool)

    # Shared refresh probe
    if command_exists("xrandr"):
        xr = run_user_cmd(["xrandr", "--query"])
        findings["probes"]["xrandr"] = {
            "ok": xr.get("ok", False),
            "active_refresh_hz": _get_active_refresh_hz(run_user_cmd),
        }

    sid = strategy.get("id", "")
    if sid in ("cosmic-wayland", "wlroots-wayland") and command_exists("wlr-randr"):
        rr = run_user_cmd(["wlr-randr"])
        findings["probes"]["wlr-randr"] = {
            "ok": rr.get("ok", False),
            "stdout_excerpt": "\n".join(rr.get("stdout", "").splitlines()[:20]),
        }

    if sid == "hyprland-wayland" and command_exists("hyprctl"):
        hy = run_user_cmd(["hyprctl", "monitors", "-j"])
        findings["probes"]["hyprctl-monitors"] = {
            "ok": hy.get("ok", False),
            "stdout_excerpt": hy.get("stdout", "")[:2000],
        }

    if sid == "gnome-wayland" and command_exists("gdbus"):
        mt = run_user_cmd([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Mutter.DisplayConfig",
            "--object-path", "/org/gnome/Mutter/DisplayConfig",
            "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState",
        ], timeout=30)
        findings["probes"]["mutter-displayconfig"] = {
            "ok": mt.get("ok", False),
            "stdout_excerpt": mt.get("stdout", "")[:2000],
        }

    if sid == "kde-wayland" and command_exists("kscreen-doctor"):
        kd = run_user_cmd(["kscreen-doctor", "--outputs"], timeout=30)
        findings["probes"]["kscreen-doctor"] = {
            "ok": kd.get("ok", False),
            "stdout_excerpt": kd.get("stdout", "")[:2000],
        }

    if sid == "kde-wayland" and tool_available("qdbus"):
        qdbus_cmd = resolve_command_variant("qdbus")
        qd = run_user_cmd([qdbus_cmd, "org.kde.KWin", "/KWin", "supportInformation"], timeout=30)
        findings["probes"]["kwin-support-info"] = {
            "ok": qd.get("ok", False),
            "stdout_excerpt": qd.get("stdout", "")[:2000],
        }

    if not any(v for v in findings["tool_availability"].values()):
        findings["notes"].append("No strategy tools available; relying on benchmark-only fallback")
    if "wayland" in (session_type or "").lower() and not command_exists("wayland-info"):
        findings["notes"].append("wayland-info missing; environment introspection is limited")

    return findings


def gather_compositor_diagnostics(strategy: dict, wm_comp: dict, run_user_cmd, session_env: dict) -> dict:
    """Gather compositor-specific diagnostics for responsiveness interpretation."""
    diagnostics = {
        "strategy_id": strategy.get("id", "unknown"),
        "compositor": wm_comp.get("compositor", "unknown"),
        "probes": {},
        "notes": [],
    }

    compositor_name = (wm_comp.get("compositor") or "").strip()
    if compositor_name:
        ps_probe = run_user_cmd(["ps", "-C", compositor_name, "-o", "pid=,%cpu=,rss=,etimes="], timeout=20)
        diagnostics["probes"]["compositor-ps"] = {
            "ok": ps_probe.get("ok", False),
            "stdout_excerpt": ps_probe.get("stdout", "")[:1000],
        }
    else:
        diagnostics["notes"].append("Compositor process name unknown; skipping process-level probe")

    sid = strategy.get("id", "")
    if sid == "gnome-wayland" and command_exists("gsettings"):
        gs = run_user_cmd(["gsettings", "get", "org.gnome.mutter", "experimental-features"], timeout=20)
        diagnostics["probes"]["gnome-mutter-experimental-features"] = {
            "ok": gs.get("ok", False),
            "stdout_excerpt": gs.get("stdout", "")[:1000],
        }

    if sid == "hyprland-wayland" and command_exists("hyprctl"):
        hp = run_user_cmd(["hyprctl", "-j", "monitors"], timeout=20)
        diagnostics["probes"]["hyprctl-monitors-json"] = {
            "ok": hp.get("ok", False),
            "stdout_excerpt": hp.get("stdout", "")[:2000],
        }

    if sid in ("cosmic-wayland", "wlroots-wayland") and command_exists("wayland-info"):
        wi = run_user_cmd(["wayland-info"], timeout=25)
        diagnostics["probes"]["wayland-info"] = {
            "ok": wi.get("ok", False),
            "stdout_excerpt": wi.get("stdout", "")[:2000],
        }

    if sid == "x11-generic" and session_env.get("DISPLAY") and command_exists("xprop"):
        xp = run_user_cmd(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], timeout=20)
        diagnostics["probes"]["x11-wm-check"] = {
            "ok": xp.get("ok", False),
            "stdout_excerpt": xp.get("stdout", "")[:1000],
        }

    return diagnostics


def gather_output_scaling_topology(run_user_cmd, session_type: str) -> dict:
    """Collect display/output scale + refresh details from session-specific tools."""
    data: dict = {"backend": "unknown", "outputs": [], "notes": []}

    if "wayland" in session_type.lower() and command_exists("wlr-randr"):
        res = run_user_cmd(["wlr-randr"])
        if res.get("ok"):
            data["backend"] = "wlr-randr"
            current: Optional[dict] = None
            for raw in res.get("stdout", "").splitlines():
                line = raw.rstrip()
                if not line:
                    continue
                if not line.startswith(" ") and not line.startswith("\t"):
                    if current:
                        data["outputs"].append(current)
                    output_name = line.split()[0]
                    current = {
                        "name": output_name,
                        "scale": None,
                        "refresh_hz": None,
                        "mode": "",
                        "focused": "(focused)" in line,
                    }
                    continue
                if current is None:
                    continue

                m_mode = re.search(r"(\d+x\d+)\s+px,\s*([0-9.]+)\s*Hz,\s*current", line)
                if m_mode:
                    current["mode"] = m_mode.group(1)
                    try:
                        current["refresh_hz"] = float(m_mode.group(2))
                    except ValueError:
                        pass

                m_scale_a = re.search(r"[Ss]cale:\s*([0-9.]+)", line)
                m_scale_b = re.search(r"scale\s+([0-9.]+)", line)
                scale_match = m_scale_a or m_scale_b
                if scale_match:
                    try:
                        current["scale"] = float(scale_match.group(1))
                    except ValueError:
                        pass

            if current:
                data["outputs"].append(current)
            if not data["outputs"]:
                data["notes"].append("wlr-randr available, but no outputs parsed")
            return data

    if command_exists("xrandr"):
        res = run_user_cmd(["xrandr", "--query"])
        if res.get("ok"):
            data["backend"] = "xrandr"
            current: Optional[dict] = None
            for raw in res.get("stdout", "").splitlines():
                line = raw.rstrip()
                if " connected" in line and not line.startswith(" "):
                    if current:
                        data["outputs"].append(current)
                    output_name = line.split()[0]
                    current = {
                        "name": output_name,
                        "scale": 1.0,
                        "refresh_hz": None,
                        "mode": "",
                        "focused": False,
                    }
                    continue
                if current is None:
                    continue
                m_mode = re.search(r"^\s+(\d+x\d+)\s+.*?([0-9.]+)\*", line)
                if m_mode:
                    current["mode"] = m_mode.group(1)
                    try:
                        current["refresh_hz"] = float(m_mode.group(2))
                    except ValueError:
                        pass
            if current:
                data["outputs"].append(current)
            if not data["outputs"]:
                data["notes"].append("xrandr available, but no outputs parsed")

    return data


def analyze_scaling_pipeline(
    session_type: str,
    desktop: str,
    compositor: str,
    xwayland_analysis: dict,
    driver_info: dict,
    renderer: str,
    reference_scale: float,
    target_scale: Optional[float],
    fps_tool: str,
    output_topology: dict,
) -> dict:
    """Classify the render/scaling pipeline and explain expected efficiency."""
    xwayland_clients = xwayland_analysis.get("xwayland_clients")
    xwayland_clients = xwayland_clients if isinstance(xwayland_clients, int) else 0
    loaded_drivers = set(driver_info.get("loaded", []))
    renderer_lc = (renderer or "").lower()
    is_wayland = "wayland" in (session_type or "").lower()

    scale_values = [reference_scale]
    if target_scale is not None:
        scale_values.append(target_scale)
    is_fractional = any(abs(s - round(s)) > 1e-6 for s in scale_values)

    if "llvmpipe" in renderer_lc or "softpipe" in renderer_lc or "software rasterizer" in renderer_lc:
        gpu_path = "software-rendering"
    elif "nvidia" in loaded_drivers:
        gpu_path = "nvidia-proprietary"
    elif "nouveau" in loaded_drivers:
        gpu_path = "nvidia-nouveau"
    elif "amdgpu" in loaded_drivers or "radeon" in loaded_drivers:
        gpu_path = "amd-mesa"
    elif "i915" in loaded_drivers or "xe" in loaded_drivers:
        gpu_path = "intel-mesa"
    else:
        gpu_path = "unknown-gpu-stack"

    if is_wayland:
        if xwayland_clients > 0:
            render_path = "wayland-mixed-with-xwayland"
        else:
            render_path = "wayland-native"
    else:
        render_path = "x11"

    if is_fractional:
        scale_path = "fractional-scaling (compositor resampling likely)"
    else:
        scale_path = "integer-scaling"

    bottlenecks = []
    rationale = []

    if render_path == "wayland-mixed-with-xwayland":
        bottlenecks.append("XWayland composition path is active")
        rationale.append("X11 clients are translated via XWayland before final Wayland composition")
    if gpu_path == "nvidia-nouveau":
        bottlenecks.append("nouveau driver may limit throughput and frame pacing")
        rationale.append("NVIDIA open-source driver can underperform compared to proprietary stack on many cards")
    if gpu_path == "software-rendering":
        bottlenecks.append("software renderer detected")
        rationale.append("CPU rasterization is significantly slower for compositor and app rendering")
    if is_fractional:
        bottlenecks.append("fractional scaling introduces resampling overhead")
        rationale.append("fractional output scales often require non-integer buffer conversion or compositor filtering")
    if fps_tool and fps_tool.startswith("glmark2"):
        rationale.append("glmark2 score is a coarse throughput signal; frame pacing still needs compositor metrics")

    if gpu_path == "software-rendering":
        efficiency_expectation = "low"
    elif "nouveau" in gpu_path:
        efficiency_expectation = "low-to-moderate" if is_fractional else "moderate"
    elif render_path == "wayland-mixed-with-xwayland":
        efficiency_expectation = "moderate" if is_fractional else "moderate-to-high"
    elif is_fractional:
        efficiency_expectation = "moderate-to-high"
    else:
        efficiency_expectation = "high"

    return {
        "pipeline_class": f"{render_path} + {scale_path}",
        "session_type": session_type,
        "desktop": desktop,
        "compositor": compositor,
        "render_path": render_path,
        "scale_path": scale_path,
        "gpu_path": gpu_path,
        "xwayland_clients": xwayland_clients,
        "reference_scale": reference_scale,
        "target_scale": target_scale,
        "is_fractional_test": is_fractional,
        "benchmark_tool": fps_tool,
        "output_topology": output_topology,
        "efficiency_expectation": efficiency_expectation,
        "likely_bottlenecks": bottlenecks,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# FPS benchmarking
# ---------------------------------------------------------------------------

def _run_glmark2(run_user_cmd, duration_s: int = 15) -> tuple:
    """Run glmark2(-wayland). Returns (score_like_value, tool)."""
    for tool in ("glmark2-wayland", "glmark2"):
        if not command_exists(tool):
            continue
        # glmark2 full suite can take long; parse partial output on timeout.
        res = run_user_cmd([tool, "--fullscreen"], duration_s + 5)
        combined = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
        if res.get("ok") or res.get("returncode") in (0, 124):
            for line in combined.splitlines():
                m = re.search(r"glmark2 Score:\s*(\d+)", line, re.IGNORECASE)
                if m:
                    return float(m.group(1)), tool

            # Fallback: compute scene FPS average from partial benchmark output.
            fps_vals = []
            for line in combined.splitlines():
                m = re.search(r"\bFPS:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
                if m:
                    try:
                        fps_vals.append(float(m.group(1)))
                    except ValueError:
                        pass
            if fps_vals:
                return round(sum(fps_vals) / len(fps_vals), 1), f"{tool} (partial)"
    return 0.0, ""


def _run_glmark2_with_hud(run_user_cmd, duration_s: int = 15) -> tuple:
    """Run glmark2 via MangoHud wrapper when available."""
    if not command_exists("mangohud"):
        return 0.0, ""

    for tool in ("glmark2-wayland", "glmark2"):
        if not command_exists(tool):
            continue
        res = run_user_cmd(["mangohud", tool, "--fullscreen"], timeout=duration_s + 5)
        combined = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
        if res.get("ok") or res.get("returncode") in (0, 124):
            for line in combined.splitlines():
                m = re.search(r"glmark2 Score:\s*(\d+)", line, re.IGNORECASE)
                if m:
                    return float(m.group(1)), f"mangohud+{tool}"

            fps_vals = []
            for line in combined.splitlines():
                m = re.search(r"\bFPS:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
                if m:
                    try:
                        fps_vals.append(float(m.group(1)))
                    except ValueError:
                        pass
            if fps_vals:
                return round(sum(fps_vals) / len(fps_vals), 1), f"mangohud+{tool} (partial)"
    return 0.0, ""


def _run_glmark2_with_gallium_hud(run_user_cmd, duration_s: int = 15) -> tuple:
    """Run glmark2 with Mesa GALLIUM_HUD enabled as fallback when MangoHud is unavailable."""
    for tool in ("glmark2-wayland", "glmark2"):
        if not command_exists(tool):
            continue
        env = {
            "GALLIUM_HUD": "simple,fps",
            "GALLIUM_HUD_PERIOD": "0.5",
        }
        res = run_user_cmd([tool, "--fullscreen"], timeout=duration_s + 5, extra_env=env)
        combined = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
        if res.get("ok") or res.get("returncode") in (0, 124):
            for line in combined.splitlines():
                m = re.search(r"glmark2 Score:\s*(\d+)", line, re.IGNORECASE)
                if m:
                    return float(m.group(1)), f"gallium_hud+{tool}"

            fps_vals = []
            for line in combined.splitlines():
                m = re.search(r"\bFPS:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
                if m:
                    try:
                        fps_vals.append(float(m.group(1)))
                    except ValueError:
                        pass
            if fps_vals:
                return round(sum(fps_vals) / len(fps_vals), 1), f"gallium_hud+{tool} (partial)"
    return 0.0, ""


def _run_glxgears(duration_s: int = 5) -> float:
    """Run glxgears for duration_s seconds. Returns average FPS."""
    if not command_exists("glxgears"):
        return 0.0
    try:
        proc = subprocess.Popen(
            ["glxgears", "-info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(duration_s + 1)
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    except Exception:  # noqa: BLE001
        return 0.0
    fps_vals = []
    for line in out.splitlines():
        m = re.search(r"=\s*([\d.]+)\s*FPS", line, re.IGNORECASE)
        if m:
            try:
                fps_vals.append(float(m.group(1)))
            except ValueError:
                pass
    return round(sum(fps_vals) / len(fps_vals), 1) if fps_vals else 0.0


def measure_fps(run_user_cmd, allow_glxgears_fallback: bool = False) -> tuple:
    """Measure FPS using best available tool. Returns (fps, tool_name)."""
    fps, tool = _run_glmark2_with_hud(run_user_cmd)
    if fps > 0:
        return fps, tool

    fps, tool = _run_glmark2_with_gallium_hud(run_user_cmd)
    if fps > 0:
        return fps, tool

    fps, tool = _run_glmark2(run_user_cmd)
    if fps > 0:
        return fps, tool
    if allow_glxgears_fallback:
        fps = _run_glxgears()
        if fps > 0:
            return fps, "glxgears"
    return 0.0, "unavailable"


def _map_start_scale_to_case(start_scale: float, required_cases: dict) -> str:
    """Map detected start scale to nearest required test case."""
    nearest_name = ""
    nearest_delta = float("inf")
    for name, value in required_cases.items():
        delta = abs(float(start_scale) - float(value))
        if delta < nearest_delta:
            nearest_delta = delta
            nearest_name = name
    return nearest_name


def _ensure_scale(session_env: dict, desktop: str, target_scale: float, run_user_cmd, interactive: bool) -> tuple:
    """Try automatic scale switching, fallback to manual confirmation."""
    home_dir = os.path.expanduser("~")
    current_scale, _ = detect_current_scale(session_env, desktop, run_user_cmd, home_dir)
    if abs(current_scale - target_scale) < 1e-6:
        return True, "already-at-target"

    applied, method = set_scale_programmatic(desktop, target_scale, run_user_cmd)
    if applied:
        time.sleep(2)
        return True, method

    if interactive:
        input(f"    Please set scale to {target_scale}x manually, then press Enter...")
        time.sleep(2)
        confirmed_scale, _ = detect_current_scale(session_env, desktop, run_user_cmd, home_dir)
        return abs(confirmed_scale - target_scale) < 0.02, "manual"

    return False, "unavailable-non-interactive"


def _nvidia_install_instructions(base_distro: str, osr: dict) -> list:
    distro_name = osr.get("PRETTY_NAME", "this distro")
    lines = [f"Proprietary NVIDIA driver is not active on {distro_name}."]
    if base_distro in ("ubuntu", "debian"):
        lines += [
            "  - Detect recommended package: ubuntu-drivers devices",
            "  - Install recommended: sudo ubuntu-drivers autoinstall",
            "  - If still on nouveau: check Secure Boot state (mokutil --sb-state)",
            "  - List installed NVIDIA packages: dpkg -l | grep -E '^ii\\s+nvidia|nvidia-driver'",
            "  - Check available driver branches: apt-cache search '^nvidia-driver-[0-9]+'",
            "  - Install explicit branch from distro repo (example): sudo apt install nvidia-driver-550",
            "  - Rebuild initramfs and reboot: sudo update-initramfs -u && sudo reboot",
            "  - Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    elif base_distro == "fedora":
        lines += [
            "  - Enable RPM Fusion nonfree if not yet enabled",
            "  - Install driver: sudo dnf install akmod-nvidia",
            "  - Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    elif base_distro == "arch":
        lines += [
            "  - Install packages: sudo pacman -S nvidia nvidia-utils",
            "  - Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    elif base_distro == "suse":
        lines += [
            "  - Enable NVIDIA SUSE repo or use distro driver workflow",
            "  - Install proprietary nvidia driver package for your branch",
            "  - Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    else:
        lines += [
            "  - Use your distro's NVIDIA packaging guide for proprietary drivers",
            "  - Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    return lines


# ---------------------------------------------------------------------------
# Mouse smoothness measurement
# ---------------------------------------------------------------------------

def _get_active_refresh_hz(run_user_cmd) -> float:
    """Read active refresh rate from xrandr query output."""
    if not command_exists("xrandr"):
        return 0.0
    res = run_user_cmd(["xrandr", "--query"])
    if not res["ok"]:
        return 0.0
    in_connected = False
    for line in res["stdout"].splitlines():
        if " connected" in line:
            in_connected = True
        elif re.match(r"^\S", line) and " connected" not in line:
            in_connected = False
        if in_connected:
            # Active mode has * after the rate: "  1920x1080  60.00*+"
            m = re.search(r"(\d+(?:\.\d+)?)\*", line)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
    return 0.0


def extract_timestamps_from_events(text: str) -> list:
    times = []
    for line in text.splitlines():
        m = re.search(r"time\s+([0-9]+\.[0-9]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def compute_event_stats(times: list) -> dict:
    if len(times) < 2:
        return {"events": len(times), "duration_s": 0.0, "avg_gap_ms": None, "max_gap_ms": None}
    gaps = [times[i] - times[i - 1] for i in range(1, len(times))]
    return {
        "events": len(times),
        "duration_s": round(max(times) - min(times), 3),
        "avg_gap_ms": round((sum(gaps) / len(gaps)) * 1000.0, 2),
        "max_gap_ms": round(max(gaps) * 1000.0, 2),
    }


def sample_input_events(device_path: Optional[str], priv: dict, interactive: bool = True) -> dict:
    """
    Sample mouse events via libinput debug-events (10 s).
    Requires interactive=True to prompt the user before starting.
    """
    summary: dict = {"device": device_path, "method": "", "ok": False, "error": ""}
    if not device_path:
        summary["error"] = "no input device found"
        return summary
    if not command_exists("libinput"):
        summary["error"] = "libinput not available"
        return summary
    if not interactive:
        summary["error"] = "skipped (non-interactive mode)"
        return summary

    input("Mouse test: move the mouse continuously for 10 s, then press Enter to start...")
    # Use list args (no shell=True) to avoid command injection via device_path
    cmd = ["libinput", "debug-events", "--device", device_path]
    res = run_cmd(cmd, timeout=10)
    summary["method"] = "libinput debug-events"
    summary["returncode"] = res.get("returncode")

    if res["ok"] or res.get("returncode") in (124, 143):
        stats = compute_event_stats(extract_timestamps_from_events(res["stdout"]))
        summary.update(stats)
        summary["ok"] = True
        return summary

    if "Permission denied" in res.get("stderr", "") and priv.get("sudo_ok"):
        cprint(C_YELLOW, "Permission denied; retrying with sudo...")
        sudo_cmd = ensure_sudo(cmd, priv)
        if sudo_cmd:
            res = run_cmd(sudo_cmd, timeout=10)
            summary["method"] = "libinput debug-events (sudo)"
            summary["returncode"] = res.get("returncode")
            if res["ok"] or res.get("returncode") in (124, 143):
                stats = compute_event_stats(extract_timestamps_from_events(res["stdout"]))
                summary.update(stats)
                summary["ok"] = True
                return summary

    summary["error"] = res.get("stderr") or res.get("error") or "unknown error"
    return summary


def assess_mouse_smoothness(session_type: str, mouse_stats: dict, run_user_cmd) -> tuple:
    """Return (smooth: bool, notes: str)."""
    notes = []
    is_wayland = "wayland" in session_type.lower()
    notes.append(
        "Wayland display server (low-latency pointer path)"
        if is_wayland
        else "X11 display server (pointer events via X server)"
    )
    if command_exists("libinput"):
        notes.append("libinput present (smooth acceleration profiles available)")
    else:
        notes.append("libinput not found — evdev/synaptics driver may be active")

    refresh = _get_active_refresh_hz(run_user_cmd)
    if refresh:
        notes.append(f"Active refresh rate: {refresh} Hz")

    if mouse_stats.get("ok") and mouse_stats.get("avg_gap_ms") is not None:
        avg = mouse_stats["avg_gap_ms"]
        notes.append(f"libinput event gap: avg {avg:.1f} ms")
        if avg > 25:
            notes.append("High average event gap — pointer may feel choppy")
    elif mouse_stats.get("error"):
        notes.append(f"Mouse event capture: {mouse_stats.get('error')}")

    smooth = is_wayland or command_exists("libinput")
    if refresh:
        if refresh < 45:
            smooth = False
            notes.append("Very low refresh rate (<45 Hz) — visible cursor judder likely")
        elif refresh < 55:
            notes.append("Moderate refresh rate (<55 Hz) — usually acceptable; perceived lag may come from compositor/GPU load")
    if mouse_stats.get("ok") and (mouse_stats.get("avg_gap_ms") or 0) > 25:
        smooth = False

    return smooth, "; ".join(notes)


# ---------------------------------------------------------------------------
# Memory breakdown
# ---------------------------------------------------------------------------

def ram_snapshot() -> tuple:
    """Return (used_mb, available_mb) from /proc/meminfo."""
    total_kb = avail_kb = 0
    for line in read_file("/proc/meminfo").splitlines():
        if line.startswith("MemTotal"):
            total_kb = int(line.split()[1])
        elif line.startswith("MemAvailable"):
            avail_kb = int(line.split()[1])
    used_mb  = (total_kb - avail_kb) // 1024
    avail_mb = avail_kb // 1024
    return used_mb, avail_mb


def summarize_memory_breakdown() -> dict:
    """RSS-based top-process memory breakdown."""
    res = run_cmd(["ps", "-eo", "comm,rss"])
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "top": []}
    parsed = []
    for line in res["stdout"].splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            try:
                parsed.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    parsed.sort(key=lambda x: x[1], reverse=True)
    return {"ok": True, "top": parsed[:10]}


# ---------------------------------------------------------------------------
# Performance assessment
# ---------------------------------------------------------------------------

_FPS_ACCEPTABLE_RATIO = 0.80
_REFERENCE_SCALE = 1.0


def assess_performance(
    ram_total_mb: int,
    cpu_cores: int,
    baseline_used_mb: int,
    baseline_fps: float,
    baseline_scale: float,
    target_used_mb: Optional[int],
    target_fps: Optional[float],
    target_scale: Optional[float],
) -> str:
    lines = []
    ram_used_pct = (baseline_used_mb / ram_total_mb * 100) if ram_total_mb else 0
    lines.append(
        f"RAM: {baseline_used_mb} MB used / {ram_total_mb} MB total "
        f"({ram_used_pct:.0f}%) at baseline scale {baseline_scale}x."
    )
    if ram_used_pct > 85:
        lines.append(
            "  ⚠  High memory pressure. Fractional scaling may increase "
            "framebuffer allocations and worsen performance further."
        )
    elif ram_used_pct > 60:
        lines.append("  ℹ  Moderate memory usage. Monitor for increase with fractional scaling.")
    else:
        lines.append("  ✓  Memory usage is comfortable.")

    if target_used_mb is not None and target_scale is not None:
        delta = target_used_mb - baseline_used_mb
        lines.append(
            f"RAM at target scale {target_scale}x: {target_used_mb} MB used "
            f"({'+'if delta >= 0 else ''}{delta} MB vs baseline)."
        )
        if delta > 200:
            lines.append(
                "  ⚠  Significant RAM increase — likely extra framebuffer copies "
                "(e.g. viewport-scaled Wayland surface buffers)."
            )
        elif delta > 50:
            lines.append("  ℹ  Minor RAM increase — within expected range for scaling overhead.")
        else:
            lines.append("  ✓  RAM usage stable across scale change.")

    if target_fps is not None and baseline_fps > 0 and target_fps > 0:
        ratio = target_fps / baseline_fps
        if ratio >= _FPS_ACCEPTABLE_RATIO:
            verdict = (
                f"FPS drop within acceptable range (>= {int(_FPS_ACCEPTABLE_RATIO*100)}% "
                "of baseline). Scaling implementation appears efficient."
            )
        else:
            verdict = (
                f"FPS dropped > {int((1-_FPS_ACCEPTABLE_RATIO)*100)}% under new scale. "
                "May indicate inefficient compositing or missing GPU acceleration."
            )
        lines.append(f"FPS: baseline {baseline_fps} -> target {target_fps} (ratio {ratio:.2f}). {verdict}")
    elif target_fps is None:
        lines.append("No target-scale FPS collected (scale change skipped or unavailable).")
    elif baseline_fps == 0:
        lines.append("FPS benchmark unavailable (glmark2 not accessible; optional glxgears fallback disabled).")

    if ram_total_mb >= 8192:
        lines.append(
            f"Hardware profile: {ram_total_mb} MB RAM / {cpu_cores} cores "
            "— sufficient for fractional scaling at typical resolutions."
        )
    elif ram_total_mb >= 4096:
        lines.append(
            f"Hardware profile: {ram_total_mb} MB RAM / {cpu_cores} cores "
            "— adequate for 1x or 2x integer scaling; fractional may cause drops on heavy DEs."
        )
    else:
        lines.append(
            f"Hardware profile: {ram_total_mb} MB RAM / {cpu_cores} cores "
            "— low-resource system; prefer integer scaling (1x or 2x)."
        )
    return "\n".join(lines)


def build_conclusions(
    xwayland_present: bool,
    xwayland_analysis: dict,
    driver_info: dict,
    cosmic_scale_hits: list,
    smooth: bool,
    driver_suitable: bool,
    pipeline_analysis: dict,
) -> list:
    conclusions = []
    if xwayland_present and (xwayland_analysis.get("xwayland_clients") or 0) > 0:
        conclusions.append(
            "Wayland session with active XWayland clients: X11 apps composited "
            "inside Wayland (extra compositing overhead)."
        )
    if "nouveau" in driver_info.get("loaded", []):
        conclusions.append(
            "'nouveau' driver active for NVIDIA GPU: consider proprietary driver for better performance."
        )
    if cosmic_scale_hits:
        conclusions.append(f"COSMIC config files referencing scaling: {cosmic_scale_hits}")
    if not smooth:
        conclusions.append("Mouse smoothness may be degraded — see Mouse Smoothness section.")
    if not driver_suitable:
        conclusions.append("GPU driver may be unsuitable — see Driver Suitability section.")
    if pipeline_analysis.get("pipeline_class"):
        conclusions.append(
            f"Determined scaling pipeline: {pipeline_analysis.get('pipeline_class')} "
            f"(expected efficiency: {pipeline_analysis.get('efficiency_expectation')})."
        )
    return conclusions


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------

def print_console_report(
    osr, base_distro, session_type, desktop, wm_comp, xwayland_present,
    start_scale, start_scale_source,
    baseline_scale, baseline_scale_source,
    test_runs,
    ram_total_mb, cpu_model, cpu_cores, gpu_lspci,
    driver_info, renderer, baseline_fps, fps_tool, target_fps,
    baseline_used_mb, baseline_avail_mb, target_used_mb,
    smooth, mouse_notes, driver_suitable, driver_notes,
    pipeline_analysis,
    fps_strategy_findings,
    compositor_diagnostics,
    assessment, conclusions,
    nvidia_instructions,
) -> None:
    _section("System Information")
    _bullet("Hostname",            platform.node())
    _bullet("OS",                  osr.get("PRETTY_NAME", "unknown"))
    _bullet("Kernel",              platform.release())
    _bullet("Base distro",         base_distro)
    _bullet("CPU",                 cpu_model or "unknown")
    _bullet("CPU cores",           cpu_cores)
    _bullet("RAM total",           f"{ram_total_mb} MB ({ram_total_mb / 1024:.1f} GB)")
    _bullet("GPU",                 gpu_lspci or "unknown")
    _bullet("OpenGL renderer",     renderer or "unknown")
    _bullet("GPU driver (kernel)", ", ".join(driver_info.get("loaded", [])) or "unknown")

    _section("Session")
    _bullet("Display server",      session_type)
    _bullet("Desktop env",         desktop)
    _bullet("Compositor / WM",     wm_comp.get("compositor", "unknown"))
    _bullet("XWayland present",    "yes" if xwayland_present else "no")

    _section("Scaling")
    _bullet("Reference factor",    f"{_REFERENCE_SCALE}x")
    _bullet("Start factor",        f"{start_scale}x")
    _bullet("Start detected via",  start_scale_source)
    _bullet("Baseline factor",     f"{baseline_scale}x")
    _bullet("Detected via",        baseline_scale_source)
    _bullet("Fractional",          "yes" if not float(baseline_scale).is_integer() else "no")

    _section("Test Matrix")
    for case_name, run in test_runs.items():
        _bullet(f"{case_name} scale", f"{run.get('requested_scale')}x")
        _bullet(f"{case_name} detected", f"{run.get('detected_scale')}x")
        _bullet(f"{case_name} fps", run.get("fps") if run.get("fps") else "n/a")
        _bullet(f"{case_name} tool", run.get("fps_tool") or "unavailable")
        _bullet(f"{case_name} RAM used", f"{run.get('used_mb')} MB")

    _section("FPS Benchmark")
    _bullet("Tool",                fps_tool or "unavailable")
    _bullet("Baseline FPS",        baseline_fps if baseline_fps else "n/a")
    if target_fps is not None:
        _bullet("Target FPS",      target_fps if target_fps else "n/a")
        if baseline_fps and target_fps:
            ratio = target_fps / baseline_fps
            verdict = "OK" if ratio >= _FPS_ACCEPTABLE_RATIO else "DEGRADED"
            _bullet("FPS ratio",   f"{ratio:.2f}  [{verdict}]")

    _section("RAM Usage")
    _bullet("Used at baseline",    f"{baseline_used_mb} MB")
    _bullet("Available at baseline", f"{baseline_avail_mb} MB")
    if target_used_mb is not None:
        delta = target_used_mb - baseline_used_mb
        _bullet("Used at target scale", f"{target_used_mb} MB  (delta: {'+'if delta >= 0 else ''}{delta} MB)")

    _section("Mouse Smoothness")
    _bullet("Assessment",          "likely smooth" if smooth else "potentially degraded")
    for note in mouse_notes.split(";"):
        if note.strip():
            print(f"    {note.strip()}")

    _section("Driver Suitability")
    _bullet("Assessment",          "suitable" if driver_suitable else "may be unsuitable")
    print(f"    {driver_notes}")
    if nvidia_instructions:
        for line in nvidia_instructions:
            print(f"    {line}")

    _section("Pipeline Analysis")
    _bullet("Pipeline",            pipeline_analysis.get("pipeline_class", "unknown"))
    _bullet("GPU path",            pipeline_analysis.get("gpu_path", "unknown"))
    _bullet("Expected efficiency", pipeline_analysis.get("efficiency_expectation", "unknown"))
    output_topology = pipeline_analysis.get("output_topology", {})
    outputs = output_topology.get("outputs", [])
    if outputs:
        for out in outputs:
            out_name = out.get("name", "output")
            out_scale = out.get("scale") if out.get("scale") is not None else "n/a"
            out_hz = out.get("refresh_hz") if out.get("refresh_hz") is not None else "n/a"
            out_mode = out.get("mode") or "unknown"
            print(f"    - {out_name}: mode={out_mode}, scale={out_scale}, refresh={out_hz} Hz")
    for reason in pipeline_analysis.get("likely_bottlenecks", []):
        print(f"    bottleneck: {reason}")

    _section("Desktop Present FPS Strategy")
    strategy = fps_strategy_findings.get("strategy", {})
    _bullet("Selected strategy", strategy.get("name", "unknown"))
    _bullet("Primary source", strategy.get("primary", "unknown"))
    _bullet("Fallback source", strategy.get("fallback", "unknown"))
    for tool, ok in fps_strategy_findings.get("tool_availability", {}).items():
        _bullet(f"Tool {tool}", "available" if ok else "missing")
    probes = fps_strategy_findings.get("probes", {})
    if "xrandr" in probes:
        _bullet("Active refresh (xrandr)", probes["xrandr"].get("active_refresh_hz", "n/a"))
    for note in fps_strategy_findings.get("notes", []):
        print(f"    note: {note}")

    _section("Compositor Diagnostics")
    _bullet("Compositor", compositor_diagnostics.get("compositor", "unknown"))
    _bullet("Strategy id", compositor_diagnostics.get("strategy_id", "unknown"))
    for probe_name, probe_data in compositor_diagnostics.get("probes", {}).items():
        _bullet(f"Probe {probe_name}", "ok" if probe_data.get("ok") else "failed")
        excerpt = probe_data.get("stdout_excerpt", "")
        if excerpt:
            print(f"    {excerpt[:300]}")
    for note in compositor_diagnostics.get("notes", []):
        print(f"    note: {note}")

    _section("Efficiency & Performance Assessment")
    print(assessment)

    if conclusions:
        _section("Findings & Reasoning")
        for c in conclusions:
            print(f"  * {c}")

    print(f"\n{C_BLUE}{'=' * 62}{C_RESET}\n")


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def write_markdown_report(
    output_path: str,
    osr, base_distro, session_type, desktop, wm_comp,
    xwayland_present, xwayland_analysis,
    start_scale, start_scale_source,
    baseline_scale, baseline_scale_source,
    test_runs,
    ram_total_mb, cpu_model, cpu_cores, gpu_lspci,
    driver_info, renderer, baseline_fps, fps_tool, target_fps,
    baseline_used_mb, baseline_avail_mb, target_used_mb,
    smooth, mouse_notes, mouse_stats,
    driver_suitable, driver_notes,
    pipeline_analysis,
    fps_strategy_findings,
    compositor_diagnostics,
    assessment, conclusions,
    nvidia_instructions,
    mem_breakdown, ps_output: Optional[str],
    trace_log,
    console_log,
) -> None:
    lines = [
        "# Desktop Scaling Diagnostic Report",
        f"- Generated: {dt.datetime.now().isoformat()}",
        "",
        "## System Summary",
        f"- Hostname: {platform.node()}",
        f"- Kernel: {platform.release()}",
        f"- Distro: {osr.get('PRETTY_NAME', 'unknown')}",
        f"- Base distro: {base_distro}",
        f"- CPU: {cpu_model or 'unknown'} ({cpu_cores} cores)",
        f"- RAM: {ram_total_mb} MB ({ram_total_mb / 1024:.1f} GB)",
        f"- GPU: {gpu_lspci or 'unknown'}",
        f"- OpenGL renderer: {renderer or 'unknown'}",
        "",
        "## Session",
        f"- Session type: {session_type}",
        f"- Desktop: {desktop}",
        f"- Compositor/WM: {wm_comp.get('compositor', 'unknown')}",
        f"- XWayland present: {xwayland_present}",
        "",
        "## Scaling",
        f"- Reference: {_REFERENCE_SCALE}x",
        f"- Start: {start_scale}x  (detected via: {start_scale_source})",
        f"- Baseline: {baseline_scale}x  (detected via: {baseline_scale_source})",
        "",
        "## Test Matrix",
        "",
    ]

    for case_name, run in test_runs.items():
        lines += [
            f"- {case_name} requested: {run.get('requested_scale')}x",
            f"- {case_name} detected: {run.get('detected_scale')}x",
            f"- {case_name} FPS: {run.get('fps') if run.get('fps') else 'n/a'}",
            f"- {case_name} tool: {run.get('fps_tool') or 'unavailable'}",
            f"- {case_name} RAM used: {run.get('used_mb')} MB",
            "",
        ]
    lines += [
        "## FPS Benchmark",
        f"- Baseline tool: {fps_tool or 'unavailable'}",
        f"- Baseline FPS: {baseline_fps if baseline_fps else 'n/a'}",
    ]
    if target_fps is not None:
        lines.append(f"- Target FPS: {target_fps if target_fps else 'n/a'}")
        if baseline_fps and target_fps:
            lines.append(f"- FPS ratio: {target_fps / baseline_fps:.2f}")
    lines += [
        "",
        "## RAM Usage",
        f"- Used at baseline: {baseline_used_mb} MB",
        f"- Available at baseline: {baseline_avail_mb} MB",
    ]
    if target_used_mb is not None:
        delta = target_used_mb - baseline_used_mb
        lines.append(f"- Used at target scale: {target_used_mb} MB  (delta: {'+'if delta >= 0 else ''}{delta} MB)")
    lines += [
        "",
        "## Mouse Smoothness",
        f"- Assessment: {'likely smooth' if smooth else 'potentially degraded'}",
        f"- Notes: {mouse_notes}",
        "",
        "```json",
        json.dumps(mouse_stats, indent=2),
        "```",
        "",
        "## Driver Suitability",
        f"- Assessment: {'suitable' if driver_suitable else 'may be unsuitable'}",
        f"- Notes: {driver_notes}",
    ]
    if nvidia_instructions:
        lines += ["", "### Proprietary NVIDIA install guidance"] + [f"- {x}" for x in nvidia_instructions]
    lines += [
        "",
        "```json",
        json.dumps(driver_info, indent=2),
        "```",
        "",
        "## XWayland Analysis",
        "```json",
        json.dumps(xwayland_analysis, indent=2),
        "```",
        "",
        "## Pipeline Analysis",
        f"- Pipeline: {pipeline_analysis.get('pipeline_class', 'unknown')}",
        f"- GPU path: {pipeline_analysis.get('gpu_path', 'unknown')}",
        f"- Expected efficiency: {pipeline_analysis.get('efficiency_expectation', 'unknown')}",
        "",
        "```json",
        json.dumps(pipeline_analysis, indent=2),
        "```",
        "",
        "## Desktop Present FPS Strategy",
        f"- Strategy: {fps_strategy_findings.get('strategy', {}).get('name', 'unknown')}",
        f"- Primary: {fps_strategy_findings.get('strategy', {}).get('primary', 'unknown')}",
        f"- Fallback: {fps_strategy_findings.get('strategy', {}).get('fallback', 'unknown')}",
        "",
        "```json",
        json.dumps(fps_strategy_findings, indent=2),
        "```",
        "",
        "## Compositor Diagnostics",
        f"- Compositor: {compositor_diagnostics.get('compositor', 'unknown')}",
        f"- Strategy id: {compositor_diagnostics.get('strategy_id', 'unknown')}",
        "",
        "```json",
        json.dumps(compositor_diagnostics, indent=2),
        "```",
        "",
        "## Efficiency & Performance Assessment",
        assessment,
        "",
        "## Findings & Reasoning",
    ]
    for c in (conclusions or ["No strong conclusions; insufficient signals in this run."]):
        lines.append(f"- {c}")
    lines += [
        "",
        "## Memory Breakdown (top processes by RSS)",
        "```json",
        json.dumps(mem_breakdown, indent=2),
        "```",
    ]
    if ps_output:
        lines += ["", "## Process List (ps axu)", "```text", ps_output, "```"]

    lines += [
        "",
        "## Execution Trace Log",
        "```text",
        "\n".join(trace_log[-1200:]) if trace_log else "(no trace entries)",
        "```",
        "",
        "## Console Log",
        "```text",
        "\n".join(console_log[-1200:]) if console_log else "(no console log entries)",
        "```",
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="linux-desktop-analysis.py",
        description=(
            "Linux Desktop Scaling Diagnostics — multi-distro, multi-DE. "
            "Supports Ubuntu/Debian/Fedora/Arch/openSUSE and "
            "GNOME/KDE/Cinnamon/COSMIC/Sway/Hyprland/i3/Xfce/MATE/LXQt."
        ),
    )
    parser.add_argument(
        "--fractional-scale", type=float, metavar="FACTOR", default=1.25,
        help="Fractional scale factor test case (default: 1.25).",
    )
    parser.add_argument(
        "--scale", type=float, metavar="FACTOR",
        help="Deprecated alias for --fractional-scale.",
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="Skip all interactive prompts (useful for automation).",
    )
    parser.add_argument(
        "--output",
        default=REPORT_OUTPUT_FILE,
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--no-ps", action="store_true",
        help="Skip ps axu output in Markdown report.",
    )
    parser.add_argument(
        "--mouse-test", action="store_true",
        help="Enable interactive libinput mouse capture test (disabled by default).",
    )
    parser.add_argument(
        "--allow-glxgears-fallback", action="store_true",
        help="Allow glxgears fallback when glmark2 is unavailable (default: disabled).",
    )
    args = parser.parse_args()
    interactive = not args.non_interactive
    trace(f"main start: argv={sys.argv}")
    trace(
        "options: "
        f"non_interactive={args.non_interactive}, fractional_scale={args.fractional_scale}, "
        f"scale_alias={args.scale}, mouse_test={args.mouse_test}, "
        f"allow_glxgears_fallback={args.allow_glxgears_fallback}, output='{args.output}'"
    )

    cprint(C_BLUE, "\n[*] Linux Desktop Scaling Diagnostics")
    cprint(C_BLUE,   "    ===================================")

    # ---- Privileges ----
    priv = preflight_privileges()
    cprint(C_GREEN, f"Privilege check: root={priv['is_root']}, sudo_ok={priv['sudo_ok']}")
    for note in priv["notes"]:
        cprint(C_YELLOW, f"  Note: {note}")

    # ---- OS / distro ----
    osr = read_os_release()
    base_distro = detect_base_distro(osr)
    trace(f"detected distro: id={osr.get('ID', '')}, base_distro={base_distro}, pretty='{osr.get('PRETTY_NAME', 'unknown')}'")
    cprint(C_GREEN, f"Base distro: {base_distro}  ({osr.get('PRETTY_NAME', 'unknown')})")

    # ---- Session environment ----
    session_env = os.environ.copy()
    if "WAYLAND_DISPLAY" not in session_env:
        guess = guess_wayland_display(os.getuid())
        if guess:
            session_env["WAYLAND_DISPLAY"] = guess
            session_env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

    processes = detect_processes()
    xwayland_display = parse_xwayland_display(processes)
    if xwayland_display and "DISPLAY" not in session_env:
        session_env["DISPLAY"] = xwayland_display

    def run_user_cmd(cmd: list, timeout: int = 20, extra_env: Optional[dict] = None) -> dict:
        env = dict(session_env)
        if extra_env:
            env.update(extra_env)
        return run_cmd(cmd, timeout=timeout, env=env)

    session_type      = detect_session_type(session_env)
    desktop           = infer_desktop_session(session_env, processes)
    wm_comp           = detect_compositor_wm(processes)
    trace(f"session detection: session_type={session_type}, desktop={desktop}, compositor={wm_comp.get('compositor', 'unknown')}")
    xwayland_present  = bool(xwayland_display)
    cprint(C_GREEN, f"Session: {session_type}, Desktop: {desktop}, Compositor: {wm_comp['compositor']}")
    fps_strategy = detect_fps_strategy(session_type, desktop, wm_comp.get("compositor", "unknown"))
    fps_strategy_findings = gather_fps_strategy_findings(fps_strategy, run_user_cmd, session_type)
    compositor_diagnostics = gather_compositor_diagnostics(fps_strategy, wm_comp, run_user_cmd, session_env)
    cprint(C_GREEN, f"FPS strategy: {fps_strategy.get('name', 'unknown')}")

    # ---- Package auto-install ----
    pm = detect_pkg_manager()
    strategy_tools = fps_strategy.get("tools", []) if isinstance(fps_strategy, dict) else []
    wanted = [
        "lspci", "lshw", "glxinfo", "vulkaninfo", "glmark2", "mangohud",
        "xlsclients", "libinput",
    ] + [tool for tool in strategy_tools if tool]
    deduped_wanted = []
    seen = set()
    for cmd in wanted:
        if cmd in seen:
            continue
        seen.add(cmd)
        deduped_wanted.append(cmd)
    missing = [c for c in deduped_wanted if not tool_available(c)]
    trace(f"tool check: wanted={deduped_wanted}, missing={missing}, pkg_manager={pm}, immutable={detect_immutable()}")
    if missing and pm and not detect_immutable():
        cprint(C_YELLOW, f"Auto-installing missing tools: {missing}")
        pkgs = _packages_for_distro(missing, base_distro)
        if pkgs:
            result = install_packages(pm, pkgs, priv)
            trace(f"package install result: ok={result.get('ok')} installed_candidates={pkgs}")
            status = "OK" if result["ok"] else "some packages failed"
            cprint(C_GREEN if result["ok"] else C_YELLOW, f"Package install: {status} ({pkgs})")

    # ---- Graphics info ----
    cprint(C_BLUE, "\n[*] Gathering graphics information...")
    graphics    = gather_graphics_info(run_user_cmd, priv)
    lsmod_text  = graphics.get("lsmod", {}).get("stdout", "")
    driver_info = gather_driver_info(lsmod_text, priv)

    gpu_lspci = ""
    for line in graphics.get("lspci", {}).get("stdout", "").splitlines():
        if any(k in line.lower() for k in ("vga", "3d controller", "display controller")):
            gpu_lspci = (line.split("]")[-1] if "]" in line else line.split(":")[-1]).strip()
            break

    glxinfo_out = graphics.get("glxinfo", {}).get("stdout", "")
    renderer = ""
    for line in glxinfo_out.splitlines():
        if "OpenGL renderer" in line:
            renderer = line.split(":", 1)[1].strip()
            break

    driver_suitable, driver_notes = assess_driver_suitability(glxinfo_out, driver_info)

    # ---- Display / scale detection ----
    home_dir = os.path.expanduser("~")
    _ = gather_display_info(run_user_cmd)
    start_scale, start_scale_source = detect_current_scale(
        session_env, desktop, run_user_cmd, home_dir,
    )

    if start_scale == 1.0 and "fallback" in start_scale_source and interactive:
        raw = input(
            f"Could not auto-detect scale (source: {start_scale_source}). "
            "Enter current scale [1.0]: "
        ).strip()
        if raw:
            try:
                start_scale = float(raw)
            except ValueError:
                pass

    cprint(C_GREEN, f"Start scale: {start_scale}x  (via {start_scale_source})")
    trace(f"start scale: value={start_scale}, source={start_scale_source}")

    # ---- COSMIC config scan ----
    cosmic_cfg        = discover_cosmic_configs(home_dir)
    cosmic_scale_hits = extract_scale_from_configs(cosmic_cfg["files"])

    # ---- XWayland analysis ----
    xwayland_analysis = analyze_xwayland(run_user_cmd, session_env)

    # ---- Required three-run matrix ----
    reference_scale = float(_REFERENCE_SCALE)
    fractional_scale = float(args.scale if args.scale is not None else args.fractional_scale)
    if fractional_scale <= 0:
        fractional_scale = 1.25

    required_cases = {
        "base_1.0": 1.0,
        "integer_2.0": 2.0,
        "fractional": fractional_scale,
    }
    test_runs: dict = {}

    def run_measurement_case(case_name: str, requested_scale: float, switch_method: str) -> dict:
        detected_scale, detected_src = detect_current_scale(session_env, desktop, run_user_cmd, home_dir)
        fps_value, fps_used_tool = measure_fps(
            run_user_cmd,
            allow_glxgears_fallback=args.allow_glxgears_fallback,
        )
        used_mb, avail_mb = ram_snapshot()
        return {
            "case": case_name,
            "requested_scale": requested_scale,
            "detected_scale": detected_scale,
            "detected_source": detected_src,
            "switch_method": switch_method,
            "fps": fps_value,
            "fps_tool": fps_used_tool,
            "used_mb": used_mb,
            "avail_mb": avail_mb,
        }

    # Immediate first run at current scale (mapped to nearest required case)
    first_case = _map_start_scale_to_case(start_scale, required_cases)
    trace(f"test matrix first case mapping: start_scale={start_scale} -> {first_case}")
    cprint(C_BLUE, f"\n[*] Immediate start-scale run mapped to case: {first_case}")
    test_runs[first_case] = run_measurement_case(first_case, required_cases[first_case], "start-scale")

    # Run remaining required cases
    for case_name, scale_value in required_cases.items():
        if case_name in test_runs:
            continue
        cprint(C_BLUE, f"\n[*] Running case {case_name} at {scale_value}x...")
        ok, method = _ensure_scale(session_env, desktop, scale_value, run_user_cmd, interactive)
        if not ok:
            cprint(C_YELLOW, f"    Could not ensure scale {scale_value}x; proceeding with current detected scale.")
        else:
            cprint(C_GREEN, f"    Scale ensured via {method}.")
        test_runs[case_name] = run_measurement_case(case_name, scale_value, method)
        trace(
            f"case result: {case_name} requested={scale_value} "
            f"detected={test_runs[case_name].get('detected_scale')} "
            f"fps={test_runs[case_name].get('fps')} tool={test_runs[case_name].get('fps_tool')}"
        )

    # Normalize baseline references from mandatory 1.0 case
    base_run = test_runs.get("base_1.0", {})
    baseline_scale = float(base_run.get("detected_scale", reference_scale))
    baseline_scale_source = base_run.get("detected_source", "reference baseline policy")
    baseline_fps = float(base_run.get("fps", 0.0) or 0.0)
    fps_tool = base_run.get("fps_tool", "unavailable")
    baseline_used_mb = int(base_run.get("used_mb", 0) or 0)
    baseline_avail_mb = int(base_run.get("avail_mb", 0) or 0)

    frac_run = test_runs.get("fractional", {})
    target_scale = frac_run.get("detected_scale") if frac_run else None
    target_fps = float(frac_run.get("fps", 0.0) or 0.0) if frac_run else None
    target_used_mb = int(frac_run.get("used_mb", 0) or 0) if frac_run else None

    # ---- Mouse test at baseline (optional) ----
    cprint(C_BLUE, "\n[*] Mouse smoothness test (baseline scale)...")
    input_devices = parse_proc_input_devices()
    mouse_device  = select_mouse_event_device(input_devices)
    if args.mouse_test and not mouse_device:
        cprint(C_YELLOW, "    No mouse/pointer input device found in /proc/bus/input/devices.")
    if args.mouse_test:
        baseline_mouse = sample_input_events(mouse_device, priv, interactive)
    else:
        baseline_mouse = {
            "device": mouse_device,
            "method": "disabled",
            "ok": False,
            "error": "disabled by default (use --mouse-test to enable)",
        }

    # ---- Restore start scale ----
    if abs(start_scale - baseline_scale) > 1e-6:
        cprint(C_BLUE, f"\n[*] Restoring start scale ({start_scale}x)...")
        restored, _ = set_scale_programmatic(desktop, start_scale, run_user_cmd)
        if not restored:
            cprint(C_YELLOW, f"    Could not auto-restore. Please manually restore to {start_scale}x.")

    # ---- Assess mouse smoothness ----
    cprint(C_BLUE, "\n[*] Assessing mouse smoothness...")
    smooth, mouse_notes = assess_mouse_smoothness(session_type, baseline_mouse, run_user_cmd)

    # ---- Assessment ----
    cprint(C_BLUE, "[*] Generating assessment...")
    ram_total_mb = baseline_used_mb + baseline_avail_mb
    output_topology = gather_output_scaling_topology(run_user_cmd, session_type)
    pipeline_analysis = analyze_scaling_pipeline(
        session_type=session_type,
        desktop=desktop,
        compositor=wm_comp.get("compositor", "unknown"),
        xwayland_analysis=xwayland_analysis,
        driver_info=driver_info,
        renderer=renderer,
        reference_scale=baseline_scale,
        target_scale=target_scale if (target_scale is not None and abs(target_scale - baseline_scale) > 1e-6) else None,
        fps_tool=fps_tool,
        output_topology=output_topology,
    )
    assessment = assess_performance(
        ram_total_mb=ram_total_mb,
        cpu_cores=os.cpu_count() or 0,
        baseline_used_mb=baseline_used_mb,
        baseline_fps=baseline_fps,
        baseline_scale=baseline_scale,
        target_used_mb=target_used_mb,
        target_fps=target_fps,
        target_scale=target_scale if (target_scale is not None and target_scale != baseline_scale) else None,
    )
    conclusions = build_conclusions(
        xwayland_present, xwayland_analysis,
        driver_info, cosmic_scale_hits,
        smooth, driver_suitable,
        pipeline_analysis,
    )
    nvidia_instructions = []
    if "nouveau" in set(driver_info.get("loaded", [])):
        nvidia_instructions = _nvidia_install_instructions(base_distro, osr)
    mem_breakdown = summarize_memory_breakdown()

    cpu_model = ""
    for line in read_file("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            cpu_model = line.split(":", 1)[1].strip()
            break

    # ---- Console report ----
    print_console_report(
        osr=osr, base_distro=base_distro,
        session_type=session_type, desktop=desktop,
        wm_comp=wm_comp, xwayland_present=xwayland_present,
        start_scale=start_scale, start_scale_source=start_scale_source,
        baseline_scale=baseline_scale, baseline_scale_source=baseline_scale_source,
        test_runs=test_runs,
        ram_total_mb=ram_total_mb, cpu_model=cpu_model, cpu_cores=os.cpu_count() or 0,
        gpu_lspci=gpu_lspci, driver_info=driver_info, renderer=renderer,
        baseline_fps=baseline_fps, fps_tool=fps_tool, target_fps=target_fps,
        baseline_used_mb=baseline_used_mb, baseline_avail_mb=baseline_avail_mb,
        target_used_mb=target_used_mb,
        smooth=smooth, mouse_notes=mouse_notes,
        driver_suitable=driver_suitable, driver_notes=driver_notes,
        pipeline_analysis=pipeline_analysis,
        fps_strategy_findings=fps_strategy_findings,
        compositor_diagnostics=compositor_diagnostics,
        assessment=assessment, conclusions=conclusions,
        nvidia_instructions=nvidia_instructions,
    )

    # ---- Markdown report ----
    ps_output = None
    if not args.no_ps:
        res = run_cmd(["ps", "axu"])
        if res["ok"]:
            ps_output = res["stdout"]

    write_markdown_report(
        output_path=args.output,
        osr=osr, base_distro=base_distro,
        session_type=session_type, desktop=desktop,
        wm_comp=wm_comp, xwayland_present=xwayland_present,
        xwayland_analysis=xwayland_analysis,
        start_scale=start_scale, start_scale_source=start_scale_source,
        baseline_scale=baseline_scale, baseline_scale_source=baseline_scale_source,
        test_runs=test_runs,
        ram_total_mb=ram_total_mb, cpu_model=cpu_model, cpu_cores=os.cpu_count() or 0,
        gpu_lspci=gpu_lspci, driver_info=driver_info, renderer=renderer,
        baseline_fps=baseline_fps, fps_tool=fps_tool, target_fps=target_fps,
        baseline_used_mb=baseline_used_mb, baseline_avail_mb=baseline_avail_mb,
        target_used_mb=target_used_mb,
        smooth=smooth, mouse_notes=mouse_notes, mouse_stats=baseline_mouse,
        driver_suitable=driver_suitable, driver_notes=driver_notes,
        pipeline_analysis=pipeline_analysis,
        fps_strategy_findings=fps_strategy_findings,
        compositor_diagnostics=compositor_diagnostics,
        assessment=assessment, conclusions=conclusions,
        nvidia_instructions=nvidia_instructions,
        mem_breakdown=mem_breakdown, ps_output=ps_output,
        trace_log=TRACE_LOG,
        console_log=CONSOLE_LOG,
    )
    trace(f"report write complete: output='{args.output}'")
    cprint(C_GREEN, f"\nReport written to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
