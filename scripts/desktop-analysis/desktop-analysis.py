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
_TRACE_SNIPPET_LIMIT = 1200


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
    env_markers = []
    if env:
        for key in ("GALLIUM_HUD", "GALLIUM_HUD_PERIOD", "MANGOHUD", "MANGOHUD_CONFIG"):
            value = env.get(key)
            if value:
                env_markers.append(f"{key}={value}")
    env_suffix = f", env_markers={env_markers}" if env_markers else ""
    trace(f"run_cmd start: cmd='{cmd_str}', timeout={timeout}{env_suffix}")
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


def resolve_report_output_path(requested_path: str) -> tuple[str, str]:
    """Resolve a writable report path; fall back to /tmp when target is not writable."""
    target = Path(requested_path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8"):
            pass
        return str(target), ""
    except Exception as exc:  # noqa: BLE001
        fallback = Path("/tmp") / target.name
        try:
            with open(fallback, "a", encoding="utf-8"):
                pass
            return str(fallback), (
                f"Output path '{target}' not writable ({exc}); using fallback '{fallback}'."
            )
        except Exception as fallback_exc:  # noqa: BLE001
            return "", (
                f"Output path '{target}' not writable ({exc}) and fallback '{fallback}' failed ({fallback_exc})."
            )


def _probe_directory_writable(path: Path) -> tuple[bool, str]:
    """Return whether a directory is writable by creating and deleting a tiny probe file."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".desktop-analysis-writecheck-{os.getpid()}-{int(time.time() * 1000)}.tmp"
        with open(probe, "w", encoding="utf-8"):
            pass
        probe.unlink(missing_ok=True)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def preflight_writable_paths(requested_output: str) -> dict:
    """Validate writable startup cwd and report destination directory."""
    cwd = Path.cwd()
    cwd_ok, cwd_err = _probe_directory_writable(cwd)

    output_requested = Path(requested_output).expanduser()
    output_target = output_requested if output_requested.is_absolute() else (cwd / output_requested)
    output_dir = output_target.parent
    out_ok, out_err = _probe_directory_writable(output_dir)

    return {
        "cwd": str(cwd),
        "cwd_writable": cwd_ok,
        "cwd_error": cwd_err,
        "output_target": str(output_target),
        "output_dir": str(output_dir),
        "output_dir_writable": out_ok,
        "output_dir_error": out_err,
    }


def analyze_kwin_crash_signals(journalctl_sections: dict) -> dict:
    """Analyze journal/coredump snippets for common KWin crash or freeze signatures."""
    signals: list[str] = []
    next_steps: list[str] = []

    combined_chunks: list[str] = []
    for sec in (journalctl_sections or {}).values():
        if not isinstance(sec, dict):
            continue
        combined_chunks.append(sec.get("stdout", "") or "")
        combined_chunks.append(sec.get("stderr", "") or "")
    text = "\n".join(combined_chunks)
    text_l = text.lower()

    score = 0
    if "prepareatomicpresentation" in text_l or "kwin::drmpipeline" in text_l:
        signals.append("KWin DRM atomic presentation path errors detected (prepareAtomicPresentation/DrmPipeline).")
        score += 4
    if "kwin_scene_opengl" in text_l and "gl_invalid" in text_l:
        signals.append("OpenGL compositor errors detected (kwin_scene_opengl + GL_INVALID_*).")
        score += 3
    if "failed to create framebuffer" in text_l or "kwin_wayland_drm" in text_l:
        signals.append("KWin Wayland DRM framebuffer/output failures detected.")
        score += 3
    if "kwin_wayland" in text_l and ("segfault" in text_l or "segmentation fault" in text_l or "sigsegv" in text_l):
        signals.append("KWin segfault signature detected in logs.")
        score += 5
    if "coredumpctl" in (journalctl_sections or {}) and (journalctl_sections.get("coredumpctl", {}).get("stdout", "") or "").strip():
        signals.append("coredumpctl returned entries for kwin_wayland.")
        score += 4

    if score >= 7:
        level = "high"
    elif score >= 3:
        level = "medium"
    elif score > 0:
        level = "low"
    else:
        level = "none"

    if level != "none":
        next_steps.extend([
            "journalctl --user-unit plasma-kwin_wayland -b --no-pager",
            "journalctl --user -b --no-pager --grep 'kwin_wayland_drm|kwin_scene_opengl|GL_INVALID|prepareAtomicPresentation|EGL|xwayland|drm'",
            "journalctl -k -b --no-pager --grep 'drm|nvidia|nouveau|amdgpu|i915|simpledrm'",
            "coredumpctl list kwin_wayland --no-pager",
        ])

    return {
        "risk_level": level,
        "score": score,
        "signals": signals,
        "next_steps": next_steps,
    }


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


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from terminal output."""
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text or "")


def parse_numeric_scalar(text: str) -> Optional[float]:
    """Parse a plain numeric scalar (optionally prefixed by uint32) safely."""
    cleaned = strip_ansi(text).strip()
    m = re.match(r"^(?:uint32\s+)?([0-9]+(?:\.[0-9]+)?)$", cleaned)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


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

    # Explicit distro support: Pop!_OS, Bazzite, Nobara, Garuda, Regata
    # Map derivatives to their package ecosystem family.
    if os_id == "pop" or "ubuntu" in id_like or os_id == "ubuntu":
        return "ubuntu"
    if "fedora" in id_like or os_id in ("fedora", "nobara", "bazzite"):
        return "fedora"
    if "arch" in id_like or os_id in ("arch", "manjaro", "endeavouros", "garuda"):
        return "arch"
    if "debian" in id_like or os_id == "debian":
        return "debian"
    if "suse" in id_like or os_id in ("opensuse", "suse", "regata"):
        return "suse"
    return "unknown"


def detect_pkg_manager() -> Optional[str]:
    for pm in ("apt-get", "dnf", "pacman", "zypper", "rpm-ostree"):
        if command_exists(pm):
            return pm
    return None


def detect_immutable() -> bool:
    return command_exists("rpm-ostree")


def detect_live_environment() -> dict:
    """Best-effort detection of live/installer environment where package installs may be restricted."""
    markers = {
        "/run/initramfs/live": Path("/run/initramfs/live").exists(),
        "/run/archiso": Path("/run/archiso").exists(),
        "/run/live/medium": Path("/run/live/medium").exists(),
        "/cdrom": Path("/cdrom").exists(),
    }
    cmdline = read_file("/proc/cmdline").lower()
    cmdline_live_tokens = [
        "boot=live",
        "rd.live.image",
        "liveimg",
        "root=live:",
        "rd.live.ram",
        "fedora-media",
    ]
    cmdline_live = any(tok in cmdline for tok in cmdline_live_tokens)
    root_fs_type = ""
    if command_exists("findmnt"):
        res = run_cmd(["findmnt", "-n", "-o", "FSTYPE", "/"], timeout=10)
        if res.get("ok"):
            root_fs_type = (res.get("stdout", "") or "").strip()
    in_container = (
        Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
        or bool(os.environ.get("container"))
    )
    rootfs_live = root_fs_type in {"squashfs", "overlay"} and not in_container
    likely_live = any(markers.values()) or cmdline_live or rootfs_live
    reasons = []
    for marker, present in markers.items():
        if present:
            reasons.append(f"marker:{marker}")
    if cmdline_live:
        reasons.append("kernel-cmdline-live-token")
    if rootfs_live:
        reasons.append(f"rootfs:{root_fs_type}")
    if in_container:
        reasons.append("container-environment")
    return {
        "likely_live": bool(likely_live),
        "reasons": reasons,
        "root_fs_type": root_fs_type or "unknown",
        "in_container": bool(in_container),
    }


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


def configure_passwordless_sudo(priv: dict, target_user: str) -> dict:
    """Configure passwordless sudo for target user via /etc/sudoers.d entry."""
    result = {
        "attempted": False,
        "ok": False,
        "target_user": target_user,
        "sudoers_file": "/etc/sudoers.d/90-desktop-analysis-nopasswd",
        "steps": [],
        "reason": "",
    }
    if not target_user:
        result["reason"] = "target-user-empty"
        return result

    def _run_privileged(cmd: list[str], timeout: int = 60) -> dict:
        full_cmd = ensure_sudo(cmd, priv)
        if not full_cmd:
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "sudo unavailable or authentication failed",
                "cmd": " ".join(cmd),
                "error": "no-sudo",
            }
        return run_cmd(full_cmd, timeout=timeout)

    result["attempted"] = True
    temp_path = f"/tmp/desktop-analysis-sudoers-{target_user}.tmp"
    content = f"{target_user} ALL=(ALL) NOPASSWD: ALL\n"

    try:
        Path(temp_path).write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"temp-write-failed:{exc}"
        return result

    result["steps"].append({
        "step": "write-temp-sudoers",
        "ok": True,
        "cmd": f"python-write {temp_path}",
        "stdout": content.strip(),
        "stderr": "",
        "returncode": 0,
    })

    install_res = _run_privileged([
        "install", "-m", "0440", temp_path, result["sudoers_file"],
    ], timeout=60)
    install_res["step"] = "install-sudoers-file"
    result["steps"].append(install_res)

    visudo_cmd = ["visudo", "-cf", result["sudoers_file"]]
    if command_exists("visudo"):
        validate_res = _run_privileged(visudo_cmd, timeout=60)
        validate_res["step"] = "validate-sudoers-file"
        result["steps"].append(validate_res)
    else:
        result["steps"].append({
            "step": "validate-sudoers-file",
            "ok": False,
            "cmd": "visudo -cf",
            "stdout": "",
            "stderr": "visudo not available; validation skipped",
            "returncode": 127,
        })

    remove_temp = run_cmd(["rm", "-f", temp_path], timeout=15)
    remove_temp["step"] = "cleanup-temp-sudoers"
    result["steps"].append(remove_temp)

    critical_steps = [
        step for step in result["steps"]
        if step.get("step") in {"install-sudoers-file", "validate-sudoers-file"}
    ]
    result["ok"] = all(step.get("ok", False) for step in critical_steps if step.get("step") != "validate-sudoers-file")
    if command_exists("visudo"):
        validation = next((s for s in result["steps"] if s.get("step") == "validate-sudoers-file"), None)
        result["ok"] = bool(result["ok"] and validation and validation.get("ok", False))

    if not result["ok"]:
        result["reason"] = "sudoers-setup-failed"
    else:
        result["reason"] = "sudoers-setup-complete"
    return result


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
    "xlsclients":     {
        "ubuntu": "x11-utils",
        "debian": "x11-utils",
        "fedora": ["xorg-x11-utils", "xorg-x11-apps", "xlsclients"],
        "arch": "xorg-xlsclients",
        "suse": ["xorg-x11-utils", "xorg-x11"],
    },
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
        if pm and not _package_exists(pm, package_spec):
            return None
        return package_spec

    if isinstance(package_spec, list):
        if not package_spec:
            return None
        if not pm:
            return package_spec[0]
        for candidate in package_spec:
            if _package_exists(pm, candidate):
                return candidate
        return None

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


def resolve_package_plan(missing_cmds: list, base_distro: str, pm: Optional[str]) -> dict:
    """Resolve and validate package candidates per missing tool before any install attempt."""
    entries: list[dict] = []
    installable_packages: set[str] = set()
    out_of_sync: list[dict] = []

    for tool in missing_cmds:
        pkg_map = _PKG_MAP.get(tool, {})
        package_spec = pkg_map.get(base_distro) or pkg_map.get("ubuntu")

        if package_spec is None:
            entry = {
                "tool": tool,
                "status": "no-mapping",
                "candidates": [],
                "available_candidates": [],
                "selected_package": "",
            }
            entries.append(entry)
            out_of_sync.append(entry)
            continue

        candidates = [package_spec] if isinstance(package_spec, str) else list(package_spec)
        available_candidates: list[str] = []
        if pm:
            for candidate in candidates:
                if candidate and _package_exists(pm, candidate):
                    available_candidates.append(candidate)

        selected = _resolve_package_candidate(pm, package_spec)
        status = "resolved" if selected else "no-available-candidate"

        entry = {
            "tool": tool,
            "status": status,
            "candidates": candidates,
            "available_candidates": available_candidates,
            "selected_package": selected or "",
        }
        entries.append(entry)

        if selected:
            installable_packages.add(selected)
        else:
            out_of_sync.append(entry)

    return {
        "entries": entries,
        "installable_packages": sorted(installable_packages),
        "out_of_sync": out_of_sync,
        "missing_tool_count": len(missing_cmds),
        "resolved_tool_count": len([entry for entry in entries if entry.get("status") == "resolved"]),
    }


def gather_package_manager_diagnostics(pm: Optional[str], run_user_cmd, base_distro: str, requested_packages: list[str]) -> dict:
    """Collect package-manager and repo health details for install troubleshooting."""
    diag = {
        "pm": pm or "unknown",
        "base_distro": base_distro,
        "can_install": False,
        "reasons": [],
        "checks": {},
        "resolved_packages": requested_packages or [],
        "live_env": detect_live_environment(),
        "immutable": detect_immutable(),
    }
    if not pm:
        diag["reasons"].append("no-package-manager-detected")
        return diag

    live_env = diag["live_env"]
    if live_env.get("likely_live"):
        diag["reasons"].append("likely-live-environment")
    if diag["immutable"]:
        diag["reasons"].append("immutable-image-environment")

    if pm == "apt-get":
        policy = run_user_cmd(["apt-cache", "policy"], timeout=30) if command_exists("apt-cache") else {"ok": False}
        diag["checks"]["repo_policy"] = {
            "ok": policy.get("ok", False),
            "stderr": (policy.get("stderr", "") or "")[:3000],
            "stdout_excerpt": (policy.get("stdout", "") or "")[:8000],
        }
        diag["can_install"] = bool(policy.get("ok", False)) and not live_env.get("likely_live")
    elif pm == "dnf":
        repolist = run_user_cmd(["dnf", "-q", "repolist", "--enabled"], timeout=45)
        diag["checks"]["repolist_enabled"] = {
            "ok": repolist.get("ok", False),
            "stderr": (replist_err := (repolist.get("stderr", "") or ""))[:3000],
            "stdout_excerpt": (repolist.get("stdout", "") or "")[:8000],
        }
        diag["can_install"] = bool(repolist.get("ok", False)) and not live_env.get("likely_live")
        if not repolist.get("ok"):
            if "cannot" in replist_err.lower() or "error" in replist_err.lower():
                diag["reasons"].append("dnf-repolist-failed")
    elif pm == "pacman":
        sync_db = run_user_cmd(["pacman", "-Sy", "--print-format", "%n", "--noconfirm"], timeout=45)
        diag["checks"]["syncdb"] = {
            "ok": sync_db.get("ok", False),
            "stderr": (sync_db.get("stderr", "") or "")[:3000],
            "stdout_excerpt": (sync_db.get("stdout", "") or "")[:8000],
        }
        diag["can_install"] = bool(sync_db.get("ok", False)) and not live_env.get("likely_live")
    elif pm == "zypper":
        repos = run_user_cmd(["zypper", "--non-interactive", "repos", "-d"], timeout=45)
        diag["checks"]["repos"] = {
            "ok": repos.get("ok", False),
            "stderr": (repos.get("stderr", "") or "")[:3000],
            "stdout_excerpt": (repos.get("stdout", "") or "")[:8000],
        }
        diag["can_install"] = bool(repos.get("ok", False)) and not live_env.get("likely_live")
    else:
        diag["reasons"].append(f"unsupported-install-probe:{pm}")

    if not requested_packages:
        diag["reasons"].append("no-package-candidates-resolved")

    if not diag["can_install"] and not diag["reasons"]:
        diag["reasons"].append("installability-undetermined")
    return diag


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


def parse_lspci_gpu_inventory(lspci_text: str) -> list[dict]:
    """Parse lspci -nnk GPU entries including active/possible kernel drivers."""
    gpus: list[dict] = []
    current: Optional[dict] = None
    for raw_line in (lspci_text or "").splitlines():
        line = raw_line.rstrip()
        if re.match(r"^[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9]\s", line):
            if current:
                gpus.append(current)
                current = None
            if any(x in line.lower() for x in ("vga compatible controller", "3d controller", "display controller")):
                slot = line.split()[0]
                m_desc = re.search(r":\s*(.+?)(?:\s*\(rev\s+[0-9a-fA-F]+\))?$", line)
                current = {
                    "slot": slot,
                    "model": (m_desc.group(1).strip() if m_desc else line.strip()),
                    "driver_in_use": "",
                    "kernel_modules": [],
                }
            continue
        if current is None:
            continue
        if "Kernel driver in use:" in line:
            current["driver_in_use"] = line.split(":", 1)[1].strip()
        elif "Kernel modules:" in line:
            mods = [m.strip() for m in line.split(":", 1)[1].split(",") if m.strip()]
            current["kernel_modules"] = mods
    if current:
        gpus.append(current)
    return gpus


def parse_possible_nvidia_drivers(base_distro: str, run_user_cmd) -> dict:
    """Collect possible/recommended NVIDIA driver packages from distro tools."""
    data = {
        "recommended": "",
        "available": [],
        "raw": "",
    }
    if base_distro not in ("ubuntu", "debian"):
        return data
    if command_exists("ubuntu-drivers"):
        ud = run_user_cmd(["ubuntu-drivers", "devices"], timeout=40)
        if ud.get("ok"):
            data["raw"] = ud.get("stdout", "")[:8000]
            for line in ud.get("stdout", "").splitlines():
                m = re.search(r"driver\s*:\s*([^\s]+)", line)
                if not m:
                    continue
                pkg = m.group(1).strip()
                if pkg not in data["available"]:
                    data["available"].append(pkg)
                if "recommended" in line.lower():
                    data["recommended"] = pkg
    return data

def _query_package_version(pm: Optional[str], package: str, run_user_cmd) -> Optional[str]:
    """Best-effort package version lookup across package managers."""
    if not pm or not package:
        return None

    if pm == "apt-get" and command_exists("dpkg-query"):
        res = run_user_cmd([
            "dpkg-query", "-W", "-f=${db:Status-Status}\\t${Version}\\n", package
        ], timeout=20)
        if res.get("ok") and res.get("stdout"):
            line = res["stdout"].strip().splitlines()[-1]
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip() == "installed":
                return parts[1].strip()
        return None

    if pm in {"dnf", "zypper", "rpm-ostree"} and command_exists("rpm"):
        res = run_user_cmd(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}\\n", package], timeout=20)
        if res.get("ok") and res.get("stdout"):
            lines = [ln.strip() for ln in res["stdout"].splitlines() if ln.strip()]
            uniq = []
            for ln in lines:
                if ln not in uniq:
                    uniq.append(ln)
            if uniq and "is not installed" not in "\n".join(uniq).lower():
                return ", ".join(uniq[:2])
        return None

    if pm == "pacman" and command_exists("pacman"):
        res = run_user_cmd(["pacman", "-Q", package], timeout=20)
        if res.get("ok") and res.get("stdout"):
            parts = res["stdout"].strip().split()
            if len(parts) >= 2:
                return parts[1].strip()
        return None

    return None


def gather_desktop_pipeline_packages(
    base_distro: str,
    session_type: str,
    desktop: str,
    wm_comp: dict,
    processes: list,
    driver_info: dict,
    possible_nvidia_drivers: dict,
    run_user_cmd,
) -> dict:
    """Collect major desktop pipeline packages and installed versions."""
    pm = detect_pkg_manager()
    desktop_lc = (desktop or "").lower()
    compositor_lc = (wm_comp.get("compositor", "") or "").lower()
    session_lc = (session_type or "").lower()

    components: list[tuple[str, list[str]]] = [
        ("Display server (Wayland)", ["xorg-x11-server-Xwayland", "xwayland", "wayland", "wayland-protocols"]),
        ("Display server (X11)", ["xserver-xorg-core", "xorg-x11-server-Xorg", "xorg-server"]),
        ("Mesa / GL stack", [
            "mesa-vulkan-drivers",
            "libgl1-mesa-dri",
            "libglx-mesa0",
            "mesa-utils",
            "mesa",
            "mesa-dri-drivers",
        ]),
        ("Input stack", ["libinput10", "libinput", "libinput-tools", "xserver-xorg-input-libinput"]),
    ]

    if "kde" in desktop_lc or "plasma" in desktop_lc:
        components.extend([
            ("Desktop shell", ["plasma-desktop", "plasma-workspace"]),
            ("Compositor / WM", ["kwin-wayland", "kwin-x11", "kwin", "kwin-common"]),
            ("Session manager", ["plasma-workspace", "plasma-session"]),
            ("Display manager", ["sddm"]),
            ("Launcher", ["plasma-workspace", "krunner"]),
            ("Scaling tools", ["kscreen"]),
        ])
    elif "gnome" in desktop_lc:
        components.extend([
            ("Desktop shell", ["gnome-shell"]),
            ("Compositor / WM", ["mutter"]),
            ("Session manager", ["gnome-session-bin", "gnome-session"]),
            ("Display manager", ["gdm3", "gdm"]),
            ("Launcher", ["gnome-shell", "gnome-session-bin"]),
            ("Scaling tools", ["gnome-control-center"]),
        ])
    elif "cinnamon" in desktop_lc:
        components.extend([
            ("Desktop shell", ["cinnamon"]),
            ("Compositor / WM", ["muffin"]),
            ("Session manager", ["cinnamon-session"]),
            ("Display manager", ["lightdm", "gdm3"]),
            ("Launcher", ["cinnamon", "rofi"]),
        ])
    elif "cosmic" in desktop_lc:
        components.extend([
            ("Desktop shell", ["cosmic-session", "cosmic-comp"]),
            ("Compositor / WM", ["cosmic-comp"]),
            ("Session manager", ["cosmic-session"]),
            ("Display manager", ["gdm3", "gdm", "sddm"]),
            ("Launcher", ["cosmic-launcher", "pop-launcher"]),
            ("Scaling tools", ["cosmic-settings", "gnome-control-center"]),
        ])
    elif "sway" in desktop_lc:
        components.extend([
            ("Desktop shell", ["sway"]),
            ("Compositor / WM", ["sway", "wlroots"]),
            ("Session manager", ["sway", "systemd"]),
            ("Display manager", ["greetd", "lightdm", "sddm", "gdm3"]),
            ("Launcher", ["wofi", "bemenu", "rofi"]),
            ("Scaling tools", ["wlr-randr", "sway"]),
        ])
    elif "hypr" in desktop_lc:
        components.extend([
            ("Desktop shell", ["hyprland"]),
            ("Compositor / WM", ["hyprland", "wlroots"]),
            ("Session manager", ["hyprland", "systemd"]),
            ("Display manager", ["greetd", "sddm", "gdm3", "lightdm"]),
            ("Launcher", ["wofi", "rofi", "fuzzel"]),
            ("Scaling tools", ["wlr-randr", "hyprland"]),
        ])
    elif "xfce" in desktop_lc:
        components.extend([
            ("Desktop shell", ["xfce4-session", "xfce4-panel"]),
            ("Compositor / WM", ["xfwm4", "picom"]),
            ("Session manager", ["xfce4-session"]),
            ("Display manager", ["lightdm", "gdm3", "sddm"]),
            ("Launcher", ["xfce4-appfinder", "rofi"]),
            ("Scaling tools", ["xfce4-settings"]),
        ])
    elif "mate" in desktop_lc:
        components.extend([
            ("Desktop shell", ["mate-desktop", "mate-panel"]),
            ("Compositor / WM", ["marco"]),
            ("Session manager", ["mate-session-manager", "mate-session"]),
            ("Display manager", ["lightdm", "gdm3", "sddm"]),
            ("Launcher", ["mate-panel", "rofi"]),
            ("Scaling tools", ["mate-control-center"]),
        ])
    elif "lxqt" in desktop_lc:
        components.extend([
            ("Desktop shell", ["lxqt-session", "lxqt-panel"]),
            ("Compositor / WM", ["openbox", "kwin-x11", "picom"]),
            ("Session manager", ["lxqt-session"]),
            ("Display manager", ["sddm", "lightdm", "gdm3"]),
            ("Launcher", ["lxqt-runner", "rofi"]),
            ("Scaling tools", ["lxqt-config"]),
        ])
    elif desktop_lc == "i3" or "i3" in compositor_lc:
        components.extend([
            ("Desktop shell", ["i3-wm", "i3"]),
            ("Compositor / WM", ["i3-wm", "i3", "picom"]),
            ("Session manager", ["i3-wm", "systemd"]),
            ("Display manager", ["lightdm", "gdm3", "sddm"]),
            ("Launcher", ["dmenu", "rofi"]),
            ("Scaling tools", ["xrandr", "arandr"]),
        ])

    uses_nvidia = "nvidia" in set(driver_info.get("loaded", [])) or "nvidia" in (compositor_lc + desktop_lc)
    if uses_nvidia or possible_nvidia_drivers.get("available"):
        nvidia_candidates = []
        nvidia_candidates.extend(possible_nvidia_drivers.get("available", [])[:12])
        nvidia_candidates.extend([
            "nvidia-driver",
            "nvidia-utils",
            "nvidia-dkms",
            "nvidia-kernel-common",
            "libnvidia-gl-535",
            "libnvidia-gl-550",
            "libnvidia-gl-560",
            "libnvidia-gl-570",
            "libnvidia-gl-580",
        ])
        components.append(("NVIDIA driver stack", nvidia_candidates))

    # Runtime process role mapping: helps identify active pipeline components.
    process_candidates: dict[str, list[tuple[str, list[str]]]] = {
        "Compositor / WM": [
            ("kwin_wayland", ["kwin-wayland", "kwin"]),
            ("kwin_x11", ["kwin-x11", "kwin"]),
            ("gnome-shell", ["gnome-shell", "mutter"]),
            ("mutter", ["mutter"]),
            ("cosmic-comp", ["cosmic-comp"]),
            ("sway", ["sway"]),
            ("Hyprland", ["hyprland"]),
            ("xfwm4", ["xfwm4"]),
            ("muffin", ["muffin"]),
            ("marco", ["marco"]),
            ("openbox", ["openbox"]),
            ("i3", ["i3-wm", "i3"]),
            ("picom", ["picom"]),
        ],
        "Session manager": [
            ("gnome-session", ["gnome-session-bin", "gnome-session"]),
            ("startplasma", ["plasma-workspace", "plasma-session"]),
            ("startplasma-wayland", ["plasma-workspace", "plasma-session"]),
            ("startplasma-x11", ["plasma-workspace", "plasma-session"]),
            ("xfce4-session", ["xfce4-session"]),
            ("mate-session", ["mate-session-manager", "mate-session"]),
            ("lxqt-session", ["lxqt-session"]),
            ("cinnamon-session", ["cinnamon-session"]),
            ("cosmic-session", ["cosmic-session"]),
        ],
        "Display manager": [
            ("sddm", ["sddm"]),
            ("gdm3", ["gdm3", "gdm"]),
            ("lightdm", ["lightdm"]),
            ("greetd", ["greetd"]),
        ],
        "Launcher": [
            ("krunner", ["plasma-workspace", "krunner"]),
            ("plasmashell", ["plasma-workspace", "plasma-desktop"]),
            ("cosmic-launcher", ["cosmic-launcher", "pop-launcher"]),
            ("rofi", ["rofi"]),
            ("wofi", ["wofi"]),
            ("dmenu", ["dmenu"]),
            ("xfce4-appfinder", ["xfce4-appfinder"]),
            ("lxqt-runner", ["lxqt-runner"]),
        ],
        "Display server (Wayland)": [
            ("Xwayland", ["xorg-x11-server-Xwayland", "xwayland"]),
        ],
        "Display server (X11)": [
            ("Xorg", ["xserver-xorg-core", "xorg-server"]),
        ],
    }

    active_runtime = []
    proc_comm_values = []
    for p in processes or []:
        first = (p.strip().split(" ", 1)[0] if p.strip() else "").strip()
        if first:
            proc_comm_values.append(first)

    for role, entries in process_candidates.items():
        for proc_name, pkg_candidates in entries:
            if not any(proc_name.lower() == comm.lower() for comm in proc_comm_values):
                continue
            pkg_name = None
            version = None
            for candidate in pkg_candidates:
                found_version = _query_package_version(pm, candidate, run_user_cmd)
                if found_version:
                    pkg_name = candidate
                    version = found_version
                    break
            active_runtime.append({
                "role": role,
                "process": proc_name,
                "package": pkg_name or "unknown",
                "version": version or "unknown",
            })

    rows = []
    seen_pairs = set()

    for component, candidates in components:
        found_for_component = 0
        for pkg in candidates:
            version = _query_package_version(pm, pkg, run_user_cmd)
            if not version:
                continue
            key = (component, pkg)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            rows.append({
                "component": component,
                "package": pkg,
                "version": version,
            })
            found_for_component += 1
            if component != "NVIDIA driver stack" and found_for_component >= 2:
                break

    if session_lc == "wayland" and not any(r["component"] == "Display server (Wayland)" for r in rows):
        rows.append({"component": "Display server (Wayland)", "package": "(not resolved)", "version": "unknown"})
    if "x11" in session_lc and not any(r["component"] == "Display server (X11)" for r in rows):
        rows.append({"component": "Display server (X11)", "package": "(not resolved)", "version": "unknown"})

    return {
        "package_manager": pm or "unknown",
        "rows": rows,
        "active_runtime": active_runtime,
    }


def evaluate_inspection_coverage(
    pipeline_packages: dict,
    session_type: str,
    desktop: str,
    renderer: str,
    gpu_inventory: list,
) -> dict:
    """Compute a quick coverage score and list missing inspection signals."""
    rows = pipeline_packages.get("rows", []) or []
    active_runtime = pipeline_packages.get("active_runtime", []) or []

    role_requirements = [
        "Compositor / WM",
        "Session manager",
        "Launcher",
    ]

    desktop_lc = (desktop or "").lower()
    if any(k in desktop_lc for k in ("kde", "plasma", "gnome", "xfce", "mate", "lxqt", "cinnamon", "cosmic")):
        role_requirements.append("Display manager")

    if "wayland" in (session_type or "").lower():
        role_requirements.append("Display server (Wayland)")
    if "x11" in (session_type or "").lower():
        role_requirements.append("Display server (X11)")

    must_have_components = set(role_requirements + ["Mesa / GL stack", "Input stack"])
    available_components = {str(r.get("component", "")) for r in rows}

    missing_components = sorted([
        comp for comp in must_have_components
        if comp not in available_components
    ])

    runtime_roles = {str(a.get("role", "")) for a in active_runtime}
    missing_runtime_roles = sorted([
        role for role in role_requirements
        if role not in runtime_roles
    ])

    flags = []
    if not renderer:
        flags.append("OpenGL renderer unresolved")
    if not gpu_inventory:
        flags.append("GPU inventory unresolved")
    if (session_type or "unknown") == "unknown":
        flags.append("Session type unresolved")
    if (desktop or "unknown") == "unknown":
        flags.append("Desktop session unresolved")
    if not rows:
        flags.append("No package versions resolved")
    if not active_runtime:
        flags.append("No active runtime components mapped")

    total_checks = len(must_have_components) + len(role_requirements) + 4
    failed_checks = len(missing_components) + len(missing_runtime_roles) + len(flags)
    score = max(0, int(round(100 * (total_checks - failed_checks) / total_checks))) if total_checks > 0 else 0

    if score >= 85:
        level = "good"
    elif score >= 60:
        level = "partial"
    else:
        level = "weak"

    return {
        "score": score,
        "level": level,
        "missing_components": missing_components,
        "missing_runtime_roles": missing_runtime_roles,
        "flags": flags,
    }


def gather_gaming_optimization_signals(
    base_distro: str,
    session_type: str,
    desktop: str,
    wm_comp: dict,
    processes: list,
    run_user_cmd,
) -> dict:
    """Collect kernel/system optimization signals relevant to gaming operation."""
    pm = detect_pkg_manager()
    kernel_release = platform.release()
    kernel_lc = kernel_release.lower()

    # Kernel flavor tags frequently associated with gaming/low-latency tuning.
    flavor_tags = [
        "zen", "xanmod", "liquorix", "bore", "tkg", "nobara", "bazzite", "garuda",
        "rt", "lowlatency",
    ]
    kernel_flavors = sorted([tag for tag in flavor_tags if tag in kernel_lc])

    # zram / swap signals
    swaps = read_file("/proc/swaps")
    zram_lines = [ln for ln in swaps.splitlines()[1:] if ln.strip() and "zram" in ln]
    zram_enabled = bool(zram_lines)
    zram_devices = []
    for ln in zram_lines:
        parts = ln.split()
        if len(parts) >= 5:
            zram_devices.append({
                "device": parts[0],
                "size_kb": parts[2],
                "used_kb": parts[3],
                "priority": parts[4],
            })

    # CPU governor / energy profile
    governor = read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    energy_profile = read_file("/sys/firmware/acpi/platform_profile")

    # Runtime daemons/tools/processes
    process_names = [(p.strip().split(" ", 1)[0] if p.strip() else "") for p in (processes or [])]
    process_names_lc = [p.lower() for p in process_names if p]

    gamemoded_active = "gamemoded" in process_names_lc
    gamescope_active = "gamescope" in process_names_lc
    steam_active = any(name in process_names_lc for name in ("steam", "steamwebhelper"))

    gamemode_service = run_cmd(["systemctl", "is-active", "gamemoded"], timeout=10)
    combined_text = ((gamemode_service.get("stdout", "") or "") + "\n" + (gamemode_service.get("stderr", "") or "")).strip()
    combined_lc = combined_text.lower()
    if "systemd" in combined_lc and "not running" in combined_lc:
        gamemode_service_state = "not-available (no systemd init context)"
    elif gamemode_service.get("ok"):
        first_line = (gamemode_service.get("stdout", "") or "").strip().splitlines()
        gamemode_service_state = first_line[0].strip() if first_line else "unknown"
    else:
        gamemode_service_state = "unknown"

    # Distro-profile-specific package probes
    profile_probe_candidates = {
        "fedora": [
            "gamemode", "gamescope", "mangohud", "steam", "wine", "obs-studio", "vulkan-loader", "mesa-vulkan-drivers",
        ],
        "arch": [
            "gamemode", "gamescope", "mangohud", "steam", "wine", "obs-studio", "vulkan-icd-loader", "mesa",
        ],
        "ubuntu": [
            "gamemode", "gamescope", "mangohud", "steam", "wine", "obs-studio", "vulkan-tools", "mesa-vulkan-drivers",
        ],
        "suse": [
            "gamemode", "gamescope", "mangohud", "steam", "wine", "obs-studio", "vulkan-tools", "Mesa-vulkan-drivers",
        ],
        "debian": [
            "gamemode", "gamescope", "mangohud", "steam", "wine", "obs-studio", "vulkan-tools", "mesa-vulkan-drivers",
        ],
    }
    probe_list = profile_probe_candidates.get(base_distro, profile_probe_candidates.get("debian", []))
    package_probe = []
    for pkg in probe_list:
        version = _query_package_version(pm, pkg, run_user_cmd)
        if version:
            package_probe.append({"package": pkg, "version": version})

    # Binary availability checks complement package probes.
    binary_checks = {
        "gamemoderun": command_exists("gamemoderun"),
        "gamescope": command_exists("gamescope"),
        "mangohud": command_exists("mangohud"),
        "steam": command_exists("steam"),
        "wine": command_exists("wine"),
        "proton": command_exists("proton"),
    }

    return {
        "base_distro": base_distro,
        "package_manager": pm or "unknown",
        "session_type": session_type,
        "desktop": desktop,
        "compositor": wm_comp.get("compositor", "unknown"),
        "kernel_release": kernel_release,
        "kernel_flavor_tags": kernel_flavors,
        "zram_enabled": zram_enabled,
        "zram_devices": zram_devices,
        "cpu_governor": governor or "unknown",
        "platform_profile": energy_profile or "unknown",
        "gamemoded_active": gamemoded_active,
        "gamemode_service_state": gamemode_service_state,
        "gamescope_active": gamescope_active,
        "steam_active": steam_active,
        "binary_checks": binary_checks,
        "profile_package_probe": package_probe,
    }


def build_operational_hints(base_distro: str, gaming_signals: dict, live_env: dict | None = None) -> list[str]:
    """Generate immutable/mutable and distro-profile-aware operational hints."""
    hints = []
    live = bool((live_env or {}).get("likely_live"))
    immutable = detect_immutable() or base_distro == "fedora" and "bazzite" in (gaming_signals.get("kernel_release", "").lower())

    if immutable:
        hints.append(
            "Immutable/image-based environment detected or likely: prefer rpm-ostree/flatpak workflows and avoid direct package-manager assumptions."
        )
    elif live:
        hints.append(
            "Live environment detected: package installation/remediation is intentionally treated as non-persistent for this run."
        )
    else:
        hints.append(
            "Mutable environment detected: standard package-manager based diagnostics and remediation are expected to work."
        )

    governor = (gaming_signals.get("cpu_governor") or "").lower()
    if governor in {"powersave", "conservative"}:
        hints.append("CPU governor is power-saving oriented; this can reduce frame-time stability under gaming load.")
    elif governor in {"performance", "schedutil"}:
        hints.append(f"CPU governor '{governor}' is generally suitable for gaming workloads.")

    if gaming_signals.get("zram_enabled"):
        hints.append("zram swap is active, which may improve responsiveness during memory pressure.")
    else:
        hints.append("zram swap not detected; memory pressure behavior may be less smooth during heavy gaming workloads.")

    if not gaming_signals.get("binary_checks", {}).get("gamemoderun"):
        hints.append("gamemode launcher not found; per-game CPU/IO optimization toggles may be unavailable.")
    if not gaming_signals.get("binary_checks", {}).get("gamescope"):
        hints.append("gamescope binary not found; fullscreen/session isolation optimizations are unavailable.")

    profile_map = {
        "fedora": "Fedora-like profile (Nobara/Bazzite)",
        "arch": "Arch-like profile (Garuda)",
        "ubuntu": "Ubuntu-like profile (Pop!_OS)",
        "suse": "openSUSE-like profile (Regata)",
    }
    hints.append(f"Using distro profile probe set: {profile_map.get(base_distro, base_distro)}.")

    return hints


def gather_platform_firmware_security_info(run_user_cmd) -> dict:
    """Collect BIOS/firmware + Secure Boot context for diagnostics."""
    dmi_files = {
        "bios_vendor": "/sys/class/dmi/id/bios_vendor",
        "bios_version": "/sys/class/dmi/id/bios_version",
        "bios_date": "/sys/class/dmi/id/bios_date",
        "sys_vendor": "/sys/class/dmi/id/sys_vendor",
        "product_name": "/sys/class/dmi/id/product_name",
        "board_name": "/sys/class/dmi/id/board_name",
    }
    info = {k: read_file(v) for k, v in dmi_files.items()}
    info["boot_mode"] = "uefi" if os.path.isdir("/sys/firmware/efi") else "legacy-bios"
    info["kernel_cmdline"] = read_file("/proc/cmdline")

    secure_boot = {
        "available": command_exists("mokutil"),
        "state": "unknown",
        "raw": "",
    }
    if secure_boot["available"]:
        sb = run_user_cmd(["mokutil", "--sb-state"], timeout=20)
        raw = (sb.get("stdout", "") + "\n" + sb.get("stderr", "")).strip()
        secure_boot["raw"] = raw
        raw_lc = raw.lower()
        if "enabled" in raw_lc:
            secure_boot["state"] = "enabled"
        elif "disabled" in raw_lc:
            secure_boot["state"] = "disabled"
    info["secure_boot"] = secure_boot
    return info


def should_guard_scale_switching(session_type: str, desktop: str, driver_info: dict, enable_scale_safety_guard: bool) -> tuple[bool, str]:
    """Return whether scale switching should be guarded to avoid compositor instability."""
    if not enable_scale_safety_guard:
        return False, "disabled-by-default"
    is_wayland = "wayland" in (session_type or "").lower()
    is_kde = "kde" in (desktop or "").lower() or "plasma" in (desktop or "").lower()
    uses_nouveau = "nouveau" in set(driver_info.get("loaded", []))
    if is_wayland and is_kde and uses_nouveau:
        return True, "kde-wayland+nouveau risk guard"
    return False, ""


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
            m = re.search(r"[Ss]cale:\s*([0-9.]+)", strip_ansi(res["stdout"]))
            if m:
                return float(m.group(1)), "wlr-randr"

    # 2. kscreen-doctor (KDE Wayland)
    if command_exists("kscreen-doctor"):
        res = run_user_cmd(["kscreen-doctor", "--outputs"])
        if res["ok"]:
            m = re.search(r"Scale:\s*([0-9.]+)", strip_ansi(res["stdout"]))
            if m:
                return float(m.group(1)), "kscreen-doctor"

    # 3. gsettings (GNOME integer scale)
    if command_exists("gsettings") and any(x in de for x in ("gnome", "ubuntu", "cinnamon")):
        res = run_user_cmd(["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"])
        if res["ok"]:
            factor = parse_numeric_scalar(res["stdout"])
            if factor is not None and factor >= 1:
                res2 = run_user_cmd([
                    "gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor",
                ])
                if res2["ok"]:
                    text_factor = parse_numeric_scalar(res2["stdout"])
                    if text_factor is not None and text_factor != 1.0:
                        return round(factor * text_factor, 4), "gsettings (integer x text-scaling-factor)"
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
        out = run_user_cmd(["kscreen-doctor", "--outputs"], timeout=25)
        output_ids = []
        if out.get("ok"):
            for line in strip_ansi(out.get("stdout", "")).splitlines():
                m = re.match(r"\s*Output:\s*(\d+)\s+", line)
                if m:
                    output_ids.append(m.group(1))
        if not output_ids:
            output_ids = ["1"]
        for output_id in output_ids:
            res = run_user_cmd(["kscreen-doctor", f"output.{output_id}.scale.{factor}"])
            if res["ok"]:
                return True, f"kscreen-doctor output.{output_id}"

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

    gaming_compat_hints = []
    compositor_lc = (compositor or "").lower()
    desktop_lc = (desktop or "").lower()
    if is_wayland and ("hypr" in compositor_lc or "hypr" in desktop_lc):
        gaming_compat_hints.append("Hyprland Wayland sessions may need compositor-specific overlay/capture setup (MangoHud, OBS, gamescope).")
    if is_wayland and ("sway" in compositor_lc or "sway" in desktop_lc):
        gaming_compat_hints.append("Sway sessions can require explicit XWayland/window rules for legacy game launchers and overlays.")
    if is_wayland and ("wayfire" in compositor_lc or "wayfire" in desktop_lc):
        gaming_compat_hints.append("Wayfire plugin configuration may influence frame pacing and fullscreen behavior.")
    if is_wayland and ("cosmic" in compositor_lc or "cosmic" in desktop_lc):
        gaming_compat_hints.append("COSMIC transition-era builds may vary in gaming overlay/capture maturity across releases.")
    if is_wayland and "kwin" in compositor_lc:
        gaming_compat_hints.append("KWin Wayland: prefer windowed benchmark mode if fullscreen causes instability.")
    if render_path == "wayland-mixed-with-xwayland":
        gaming_compat_hints.append("Mixed Wayland/XWayland workloads can increase latency variance for some games.")

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
        "gaming_compat_hints": gaming_compat_hints,
    }


# ---------------------------------------------------------------------------
# FPS benchmarking
# ---------------------------------------------------------------------------

def _resolve_fps_mode(fps_mode: str, session_type: str, desktop: str) -> str:
    if fps_mode != "auto":
        return fps_mode
    session_lc = (session_type or "").lower()
    desktop_lc = (desktop or "").lower()
    if "wayland" in session_lc and ("kde" in desktop_lc or "plasma" in desktop_lc):
        return "windowed"
    return "fullscreen"


def _build_glmark_cmd(tool: str, resolved_mode: str, fps_window_size: str) -> list[str]:
    cmd = [tool]
    if resolved_mode == "fullscreen":
        cmd.append("--fullscreen")
    elif resolved_mode == "offscreen":
        cmd.append("--off-screen")
    elif resolved_mode == "windowed":
        cmd.extend(["--size", fps_window_size])
    else:
        cmd.append("--fullscreen")
    return cmd


def _run_glmark2(
    run_user_cmd,
    duration_s: int = 15,
    resolved_mode: str = "fullscreen",
    fps_window_size: str = "1920x1080",
) -> tuple:
    """Run glmark2(-wayland). Returns (score_like_value, tool)."""
    
    for tool in ("glmark2-wayland", "glmark2"):
        if not command_exists(tool):
            continue
        # glmark2 full suite can take long; parse partial output on timeout.
        cmd = _build_glmark_cmd(tool, resolved_mode, fps_window_size)
        res = run_user_cmd(cmd, duration_s + 5)
        combined = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
        if res.get("ok") or res.get("returncode") in (0, 124):
            for line in combined.splitlines():
                m = re.search(r"glmark2 Score:\s*(\d+)", line, re.IGNORECASE)
                if m:
                    return float(m.group(1)), f"{tool} ({resolved_mode})"

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
                return round(sum(fps_vals) / len(fps_vals), 1), f"{tool} ({resolved_mode}, partial)"
    return 0.0, ""


def _run_glmark2_with_hud(
    run_user_cmd,
    duration_s: int = 15,
    resolved_mode: str = "fullscreen",
    fps_window_size: str = "1920x1080",
) -> tuple:
    """Run glmark2 via MangoHud wrapper when available."""
    if not command_exists("mangohud"):
        return 0.0, ""

    for tool in ("glmark2-wayland", "glmark2"):
        if not command_exists(tool):
            continue
        glmark_cmd = _build_glmark_cmd(tool, resolved_mode, fps_window_size)
        res = run_user_cmd(["mangohud"] + glmark_cmd, timeout=duration_s + 5)
        combined = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
        if res.get("ok") or res.get("returncode") in (0, 124):
            for line in combined.splitlines():
                m = re.search(r"glmark2 Score:\s*(\d+)", line, re.IGNORECASE)
                if m:
                    return float(m.group(1)), f"mangohud+{tool} ({resolved_mode})"

            fps_vals = []
            for line in combined.splitlines():
                m = re.search(r"\bFPS:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
                if m:
                    try:
                        fps_vals.append(float(m.group(1)))
                    except ValueError:
                        pass
            if fps_vals:
                return round(sum(fps_vals) / len(fps_vals), 1), f"mangohud+{tool} ({resolved_mode}, partial)"
    return 0.0, ""


def _run_glmark2_with_gallium_hud(
    run_user_cmd,
    duration_s: int = 15,
    resolved_mode: str = "fullscreen",
    fps_window_size: str = "1920x1080",
) -> tuple:
    """Run glmark2 with Mesa GALLIUM_HUD enabled as fallback when MangoHud is unavailable."""
    for tool in ("glmark2-wayland", "glmark2"):
        if not command_exists(tool):
            continue
        env = {
            "GALLIUM_HUD": "simple,fps",
            "GALLIUM_HUD_PERIOD": "0.5",
        }
        cmd = _build_glmark_cmd(tool, resolved_mode, fps_window_size)
        res = run_user_cmd(cmd, timeout=duration_s + 5, extra_env=env)
        combined = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
        if res.get("ok") or res.get("returncode") in (0, 124):
            for line in combined.splitlines():
                m = re.search(r"glmark2 Score:\s*(\d+)", line, re.IGNORECASE)
                if m:
                    return float(m.group(1)), f"gallium_hud+{tool} ({resolved_mode})"

            fps_vals = []
            for line in combined.splitlines():
                m = re.search(r"\bFPS:\s*([0-9]+(?:\.[0-9]+)?)", line, re.IGNORECASE)
                if m:
                    try:
                        fps_vals.append(float(m.group(1)))
                    except ValueError:
                        pass
            if fps_vals:
                return round(sum(fps_vals) / len(fps_vals), 1), f"gallium_hud+{tool} ({resolved_mode}, partial)"
    return 0.0, ""


def _run_glxgears(run_user_cmd, duration_s: int = 5) -> float:
    """Run glxgears for duration_s seconds in the active session env. Returns average FPS."""
    if not command_exists("glxgears"):
        return 0.0
    try:
        # Use SIGINT and a slightly longer runtime so glxgears has a chance to emit at least one FPS sample.
        timeout_s = max(6, int(duration_s) + 1)
        res = run_user_cmd(["timeout", "-s", "INT", f"{timeout_s}s", "glxgears", "-info"], timeout=timeout_s + 5)
        out = "\n".join([res.get("stdout", ""), res.get("stderr", "")])
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


def measure_fps(
    run_user_cmd,
    allow_glxgears_fallback: bool = False,
    fps_mode: str = "auto",
    fps_window_size: str = "1920x1080",
    session_type: str = "",
    desktop: str = "",
) -> tuple:
    """Measure FPS using best available tool. Returns (fps, tool_name)."""
    resolved_mode = _resolve_fps_mode(fps_mode, session_type, desktop)
    trace(
        "measure_fps config: "
        f"requested_mode={fps_mode}, resolved_mode={resolved_mode}, "
        f"window_size={fps_window_size}"
    )

    fps, tool = _run_glmark2_with_hud(
        run_user_cmd,
        resolved_mode=resolved_mode,
        fps_window_size=fps_window_size,
    )
    if fps > 0:
        return fps, tool

    fps, tool = _run_glmark2_with_gallium_hud(
        run_user_cmd,
        resolved_mode=resolved_mode,
        fps_window_size=fps_window_size,
    )
    if fps > 0:
        return fps, tool

    fps, tool = _run_glmark2(
        run_user_cmd,
        resolved_mode=resolved_mode,
        fps_window_size=fps_window_size,
    )
    if fps > 0:
        return fps, tool
    if allow_glxgears_fallback:
        fps = _run_glxgears(run_user_cmd)
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
        for _ in range(4):
            time.sleep(1)
            confirmed_scale, _ = detect_current_scale(session_env, desktop, run_user_cmd, home_dir)
            if abs(confirmed_scale - target_scale) < 0.03:
                return True, method
        return False, f"{method} (no-confirmation)"

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
            "Detect recommended package: ubuntu-drivers devices",
            "Install recommended: sudo ubuntu-drivers autoinstall",
            "If still on nouveau: check Secure Boot state (mokutil --sb-state)",
            "List installed NVIDIA packages: dpkg -l | grep -E '^ii\\s+nvidia|nvidia-driver'",
            "Check available driver branches: apt-cache search '^nvidia-driver-[0-9]+'",
            "Install explicit branch from distro repo (example): sudo apt install nvidia-driver-550",
            "Rebuild initramfs and reboot: sudo update-initramfs -u && sudo reboot",
            "Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    elif base_distro == "fedora":
        lines += [
            "Enable RPM Fusion nonfree if not yet enabled",
            "If open variant is present, remove it first: sudo dnf remove -y akmod-nvidia-open kmod-nvidia-open-dkms",
            "Install proprietary stack: sudo dnf install -y akmod-nvidia xorg-x11-drv-nvidia xorg-x11-drv-nvidia-libs",
            "Build module/initramfs before reboot: sudo akmods --force && sudo dracut -f",
            "Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    elif base_distro == "arch":
        lines += [
            "Install packages: sudo pacman -S nvidia nvidia-utils",
            "Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    elif base_distro == "suse":
        lines += [
            "Enable NVIDIA SUSE repo or use distro driver workflow",
            "Install proprietary nvidia driver package for your branch",
            "Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    else:
        lines += [
            "Use your distro's NVIDIA packaging guide for proprietary drivers",
            "Reboot and verify: lsmod | grep -E 'nvidia|nouveau'",
        ]
    return lines


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        raw = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in {"y", "yes"}


def _detect_nvidia_context(gpu_lspci: str, renderer: str, gpu_inventory: list[dict]) -> bool:
    text = f"{gpu_lspci} {renderer}".lower()
    if any(token in text for token in ("nvidia", "geforce", "quadro", "tesla", "10de:")):
        return True
    for gpu in gpu_inventory or []:
        model = (gpu or {}).get("model", "").lower()
        if any(token in model for token in ("nvidia", "geforce", "quadro", "tesla", "10de:")):
            return True
    return False


def _detect_open_gsp_mismatch(run_user_cmd) -> dict:
    result = {
        "detected": False,
        "reason": "",
        "journal_excerpt": "",
        "check_ok": False,
    }
    if not command_exists("journalctl"):
        result["reason"] = "journalctl-unavailable"
        return result

    res = run_user_cmd(
        [
            "journalctl", "-k", "-b", "--no-pager", "-n", "4000",
            "--grep", "NVRM|nvidia|GSP|nouveau",
        ],
        timeout=40,
    )
    result["check_ok"] = bool(res.get("ok", False))
    out = (res.get("stdout", "") or "")
    text = out.lower()
    mismatch = (
        ("not supported by open" in text and "does not include the required gpu" in text)
        or ("system processor (gsp)" in text and "probe with driver nvidia failed" in text)
    )
    result["detected"] = bool(mismatch)
    if mismatch:
        result["reason"] = "nvidia-open-gsp-mismatch"
    result["journal_excerpt"] = out[:2500]
    return result


def _collect_installed_nvidia_packages(base_distro: str, run_user_cmd) -> dict:
    packages: list[str] = []
    checks: dict = {}

    if base_distro in ("ubuntu", "debian") and command_exists("dpkg"):
        res = run_user_cmd(["dpkg", "-l"], timeout=60)
        checks["dpkg_l"] = {"ok": bool(res.get("ok", False)), "stderr": (res.get("stderr", "") or "")[:500]}
        if res.get("ok"):
            for line in (res.get("stdout", "") or "").splitlines():
                if not line.startswith("ii"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                pkg = parts[1]
                if "nvidia" in pkg:
                    packages.append(pkg)
    elif base_distro in ("fedora", "suse") and command_exists("rpm"):
        res = run_user_cmd(["rpm", "-qa"], timeout=60)
        checks["rpm_qa"] = {"ok": bool(res.get("ok", False)), "stderr": (res.get("stderr", "") or "")[:500]}
        if res.get("ok"):
            for pkg in (res.get("stdout", "") or "").splitlines():
                if "nvidia" in pkg.lower():
                    packages.append(pkg.strip())
    elif base_distro == "arch" and command_exists("pacman"):
        res = run_user_cmd(["pacman", "-Q"], timeout=60)
        checks["pacman_q"] = {"ok": bool(res.get("ok", False)), "stderr": (res.get("stderr", "") or "")[:500]}
        if res.get("ok"):
            for line in (res.get("stdout", "") or "").splitlines():
                pkg = line.split()[0] if line.split() else ""
                if "nvidia" in pkg.lower():
                    packages.append(pkg)

    open_patterns = [
        r"\bnvidia-open\b",
        r"nvidia-driver-\d+-open",
        r"akmod-nvidia-open",
        r"kmod-nvidia-open",
        r"open-dkms",
        r"xorg-x11-drv-nvidia-open",
    ]
    proprietary_patterns = [
        r"\bakmod-nvidia\b",
        r"\bxorg-x11-drv-nvidia\b",
        r"^nvidia-driver-\d+$",
        r"\bnvidia-utils\b",
        r"\bnvidia-driver-g0\d\b",
        r"\bnvidia\b",
    ]

    open_pkgs: list[str] = []
    proprietary_pkgs: list[str] = []
    for pkg in sorted(set(packages)):
        low = pkg.lower()
        if any(re.search(p, low) for p in open_patterns):
            open_pkgs.append(pkg)
        if any(re.search(p, low) for p in proprietary_patterns):
            proprietary_pkgs.append(pkg)

    return {
        "all": sorted(set(packages)),
        "open": sorted(set(open_pkgs)),
        "proprietary": sorted(set(proprietary_pkgs)),
        "checks": checks,
    }


def _build_nvidia_proprietary_install_plan(base_distro: str, diag: dict) -> dict:
    suited = (diag or {}).get("suited_package", "") or ""
    open_pkgs = list((diag or {}).get("installed_open_packages", []) or [])

    plan = {
        "remove_packages": open_pkgs,
        "install_packages": [],
        "post_commands": [],
    }

    if base_distro == "fedora":
        selected = suited if suited and "open" not in suited else "akmod-nvidia"
        plan["install_packages"] = [selected, "xorg-x11-drv-nvidia", "xorg-x11-drv-nvidia-libs"]
        plan["post_commands"] = ["akmods --force", "dracut -f"]
    elif base_distro in ("ubuntu", "debian"):
        selected = suited if suited and "open" not in suited else "nvidia-driver-550"
        plan["install_packages"] = [selected]
        plan["post_commands"] = ["update-initramfs -u"]
    elif base_distro == "arch":
        if not plan["remove_packages"]:
            plan["remove_packages"] = ["nvidia-open"]
        plan["install_packages"] = ["nvidia", "nvidia-utils"]
        plan["post_commands"] = ["mkinitcpio -P"]
    elif base_distro == "suse":
        if not plan["remove_packages"]:
            plan["remove_packages"] = ["nvidia-open-driver-G06"]
        plan["install_packages"] = ["nvidia-driver-G06"]
        if command_exists("dracut"):
            plan["post_commands"] = ["dracut -f"]

    plan["remove_packages"] = [pkg for pkg in plan["remove_packages"] if pkg]
    plan["install_packages"] = [pkg for pkg in plan["install_packages"] if pkg]
    return plan


def maybe_offer_nvidia_proprietary_remediation(
    *,
    interactive: bool,
    auto_fix_nvidia: bool,
    priv: dict,
    base_distro: str,
    renderer: str,
    run_user_cmd,
    nvidia_activation_diagnostics: dict,
    gpu_lspci: str,
    driver_info: dict,
) -> dict:
    result = {
        "offered": False,
        "accepted": False,
        "attempted": False,
        "ok": False,
        "reason": "",
        "actions": [],
        "logs": [],
        "post_check": {},
    }

    if not interactive and not auto_fix_nvidia:
        result["reason"] = "non-interactive"
        return result
    if not nvidia_activation_diagnostics.get("relevant"):
        result["reason"] = "nvidia-not-relevant"
        return result

    uses_nouveau = bool(nvidia_activation_diagnostics.get("nouveau_active"))
    open_mismatch = bool(nvidia_activation_diagnostics.get("open_gsp_mismatch"))
    software_renderer = any(token in (renderer or "").lower() for token in ("llvmpipe", "softpipe", "software rasterizer"))
    nvidia_module_active = bool(nvidia_activation_diagnostics.get("nvidia_module_active"))

    needs = uses_nouveau or open_mismatch or (software_renderer and not nvidia_module_active)
    if not needs:
        result["reason"] = "no-remediation-needed"
        return result

    reasons = []
    if uses_nouveau:
        reasons.append("nouveau-active")
    if open_mismatch:
        reasons.append("open-gsp-mismatch")
    if software_renderer and not nvidia_module_active:
        reasons.append("software-renderer-without-active-nvidia")

    prompt = (
        "NVIDIA remediation suggested (" + ", ".join(reasons) + "). "
        "Apply automatic proprietary-driver correction now (before reboot)?"
    )
    result["offered"] = True
    if auto_fix_nvidia:
        result["accepted"] = True
        result["reason"] = "auto-fix-enabled"
    else:
        if not _ask_yes_no(prompt, default=False):
            result["reason"] = "user-declined"
            return result
        result["accepted"] = True

    result["attempted"] = True

    plan = _build_nvidia_proprietary_install_plan(base_distro, nvidia_activation_diagnostics)
    result["actions"] = plan

    if not plan.get("install_packages"):
        result["reason"] = "unsupported-distro-or-empty-plan"
        return result

    def _run_privileged(cmd: list[str], timeout: int = 300) -> dict:
        full_cmd = ensure_sudo(cmd, priv)
        if not full_cmd:
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "sudo unavailable or authentication failed",
                "cmd": " ".join(cmd),
                "error": "no-sudo",
            }
        return run_cmd(full_cmd, timeout=timeout)

    def _run_step(step: str, cmd: list[str], timeout: int = 300) -> dict:
        trace(f"nvidia remediation step start: {step} cmd={' '.join(cmd)}")
        log = _run_privileged(cmd, timeout=timeout)
        log["step"] = step
        trace(
            "nvidia remediation step done: "
            f"step={step} ok={log.get('ok')} rc={log.get('returncode')} "
            f"stderr={(log.get('stderr', '') or '')[:300]}"
        )
        return log

    remove_pkgs = plan.get("remove_packages", [])
    if remove_pkgs:
        if base_distro in ("fedora", "suse"):
            if base_distro == "fedora":
                result["logs"].append(_run_step("remove-open-packages", ["dnf", "remove", "-y"] + remove_pkgs, timeout=300))
            else:
                result["logs"].append(_run_step("remove-open-packages", ["zypper", "--non-interactive", "rm"] + remove_pkgs, timeout=300))
        elif base_distro in ("ubuntu", "debian"):
            result["logs"].append(_run_step("remove-open-packages", ["apt-get", "remove", "-y"] + remove_pkgs, timeout=300))
        elif base_distro == "arch":
            result["logs"].append(_run_step("remove-open-packages", ["pacman", "-Rns", "--noconfirm"] + remove_pkgs, timeout=300))

    install_pkgs = plan.get("install_packages", [])
    if base_distro == "fedora":
        result["logs"].append(_run_step("install-proprietary-packages", ["dnf", "install", "-y"] + install_pkgs, timeout=600))
    elif base_distro in ("ubuntu", "debian"):
        result["logs"].append(_run_step("refresh-package-index", ["apt-get", "update"], timeout=180))
        result["logs"].append(_run_step("install-proprietary-packages", ["apt-get", "install", "-y"] + install_pkgs, timeout=600))
    elif base_distro == "arch":
        result["logs"].append(_run_step("install-proprietary-packages", ["pacman", "-Sy", "--noconfirm"] + install_pkgs, timeout=600))
    elif base_distro == "suse":
        result["logs"].append(_run_step("install-proprietary-packages", ["zypper", "--non-interactive", "install"] + install_pkgs, timeout=600))

    for post_cmd in plan.get("post_commands", []):
        cmd = [token for token in post_cmd.split() if token]
        if cmd:
            result["logs"].append(_run_step(f"post-command:{post_cmd}", cmd, timeout=600))

    result["ok"] = all(log.get("ok", False) for log in result.get("logs", [])) if result.get("logs") else False

    mismatch_check = _detect_open_gsp_mismatch(run_user_cmd)
    installed_after = _collect_installed_nvidia_packages(base_distro, run_user_cmd)
    lsmod_after = run_cmd(["lsmod"])
    loaded_after = gather_driver_info(lsmod_after.get("stdout", ""), priv)
    smi_after = run_user_cmd(["nvidia-smi"], timeout=20) if command_exists("nvidia-smi") else {"ok": False, "stderr": "nvidia-smi not found", "stdout": ""}

    result["post_check"] = {
        "open_gsp_mismatch": bool(mismatch_check.get("detected")),
        "open_packages_remaining": installed_after.get("open", []),
        "nvidia_loaded_modules": loaded_after.get("loaded", []),
        "nvidia_smi_ok": bool(smi_after.get("ok", False)),
        "mismatch_check": mismatch_check,
        "needs_reboot": True,
    }

    if result["ok"] and not result["post_check"]["open_packages_remaining"]:
        result["reason"] = "packages-corrected-reboot-required"
    elif not result["ok"]:
        result["reason"] = "install-or-post-step-failed"
    else:
        result["reason"] = "partial-correction"

    return result


def gather_nvidia_activation_diagnostics(
    run_user_cmd,
    base_distro: str,
    gpu_lspci: str,
    renderer: str,
    driver_info: dict,
) -> dict:
    """Collect actionable diagnostics for NVIDIA proprietary driver activation."""
    gpu_text = f"{gpu_lspci} {renderer}".lower()
    loaded = set(driver_info.get("loaded", []))
    nvidia_gpu = ("nvidia" in gpu_text) or ("geforce" in gpu_text) or ("nv" in gpu_text)
    nouveau_active = "nouveau" in loaded
    nvidia_module_active = any(mod.startswith("nvidia") for mod in loaded)

    diag = {
        "relevant": bool(nvidia_gpu),
        "nouveau_active": nouveau_active,
        "nvidia_module_active": nvidia_module_active,
        "open_gsp_mismatch": False,
        "open_gsp_mismatch_reason": "",
        "installed_open_packages": [],
        "installed_proprietary_packages": [],
        "recommended_package": "",
        "candidate_packages": [],
        "suited_package": "",
        "checks": {},
        "simulations": {},
        "command_block": [],
        "options": [],
        "notes": [],
    }

    if not diag["relevant"]:
        diag["notes"].append("NVIDIA GPU not detected in renderer/lspci context.")
        return diag

    secure_boot_enabled = None
    if command_exists("mokutil"):
        sb = run_user_cmd(["mokutil", "--sb-state"], timeout=20)
        diag["checks"]["secure_boot"] = {
            "ok": sb.get("ok", False),
            "stdout": sb.get("stdout", "")[:1000],
            "stderr": sb.get("stderr", "")[:500],
        }
        sb_text = (sb.get("stdout", "") + "\n" + sb.get("stderr", "")).lower()
        if "enabled" in sb_text:
            secure_boot_enabled = True
        elif "disabled" in sb_text:
            secure_boot_enabled = False

    if command_exists("nvidia-smi"):
        smi = run_user_cmd(["nvidia-smi"], timeout=20)
        diag["checks"]["nvidia_smi"] = {
            "ok": smi.get("ok", False),
            "stdout": smi.get("stdout", "")[:1200],
            "stderr": smi.get("stderr", "")[:500],
        }
    else:
        diag["checks"]["nvidia_smi"] = {
            "ok": False,
            "stdout": "",
            "stderr": "nvidia-smi command not found",
        }

    if command_exists("modinfo"):
        mod = run_user_cmd(["modinfo", "nvidia"], timeout=20)
        diag["checks"]["modinfo_nvidia"] = {
            "ok": mod.get("ok", False),
            "stdout": mod.get("stdout", "")[:1000],
            "stderr": mod.get("stderr", "")[:500],
        }

    open_mismatch = _detect_open_gsp_mismatch(run_user_cmd)
    diag["open_gsp_mismatch"] = bool(open_mismatch.get("detected"))
    diag["open_gsp_mismatch_reason"] = open_mismatch.get("reason", "")
    diag["checks"]["open_gsp_mismatch"] = {
        "ok": bool(open_mismatch.get("check_ok", False)),
        "detected": bool(open_mismatch.get("detected")),
        "reason": open_mismatch.get("reason", ""),
        "stdout_excerpt": (open_mismatch.get("journal_excerpt", "") or "")[:2000],
    }

    installed_pkgs = _collect_installed_nvidia_packages(base_distro, run_user_cmd)
    diag["installed_open_packages"] = installed_pkgs.get("open", [])
    diag["installed_proprietary_packages"] = installed_pkgs.get("proprietary", [])
    diag["checks"]["installed_nvidia_packages_unified"] = {
        "ok": True,
        "open": installed_pkgs.get("open", [])[:40],
        "proprietary": installed_pkgs.get("proprietary", [])[:40],
    }

    if base_distro in ("ubuntu", "debian"):
        recommended_package = ""
        candidate_packages = []
        if command_exists("ubuntu-drivers"):
            ud = run_user_cmd(["ubuntu-drivers", "devices"], timeout=40)
            diag["checks"]["ubuntu_drivers_devices"] = {
                "ok": ud.get("ok", False),
                "stdout": ud.get("stdout", "")[:3000],
                "stderr": ud.get("stderr", "")[:1000],
            }
            if ud.get("ok"):
                for line in ud.get("stdout", "").splitlines():
                    m = re.search(r"driver\s*:\s*([^\s]+)", line)
                    if not m:
                        continue
                    pkg = m.group(1).strip()
                    if pkg not in candidate_packages:
                        candidate_packages.append(pkg)
                    if "recommended" in line.lower():
                        recommended_package = pkg

        installed_nvidia_packages = []
        if command_exists("dpkg"):
            dpkg_res = run_user_cmd(["dpkg", "-l"], timeout=60)
            if dpkg_res.get("ok"):
                for line in dpkg_res.get("stdout", "").splitlines():
                    if not line.startswith("ii"):
                        continue
                    if re.search(r"\bnvidia\b|linux-modules-nvidia|system76.*nvidia", line):
                        parts = line.split()
                        if len(parts) >= 2:
                            installed_nvidia_packages.append(parts[1])
            diag["checks"]["installed_nvidia_packages"] = {
                "ok": bool(installed_nvidia_packages),
                "packages": installed_nvidia_packages[:80],
            }

        available_branches = []
        if command_exists("apt-cache"):
            search = run_user_cmd(["apt-cache", "search", "nvidia-driver-"], timeout=30)
            if search.get("ok"):
                for line in search.get("stdout", "").splitlines():
                    m = re.match(r"(nvidia-driver-\d+)\b", line.strip())
                    if m:
                        available_branches.append(m.group(1))
            diag["checks"]["available_driver_branches"] = {
                "ok": bool(available_branches),
                "branches": sorted(set(available_branches))[:20],
            }

        def _highest_driver_branch(pkgs: list[str]) -> str:
            best_name = ""
            best_num = -1
            for pkg in pkgs:
                m = re.match(r"nvidia-driver-(\d+)$", pkg)
                if not m:
                    continue
                try:
                    num = int(m.group(1))
                except ValueError:
                    continue
                if num > best_num:
                    best_num = num
                    best_name = pkg
            return best_name

        suited_package = recommended_package or _highest_driver_branch(
            sorted(set(candidate_packages + available_branches))
        )

        diag["recommended_package"] = recommended_package
        diag["candidate_packages"] = sorted(set(candidate_packages))[:30]
        diag["suited_package"] = suited_package

        to_simulate = [x for x in [recommended_package, suited_package] if x]
        for pkg in sorted(set(to_simulate)):
            if command_exists("apt-get"):
                sim = run_user_cmd(["apt-get", "-s", "install", pkg], timeout=45)
                diag["simulations"][f"apt_sim_{pkg}"] = {
                    "ok": sim.get("ok", False),
                    "returncode": sim.get("returncode"),
                    "stdout_excerpt": sim.get("stdout", "")[:2500],
                    "stderr_excerpt": sim.get("stderr", "")[:800],
                }

    if base_distro == "fedora":
        candidate_priority = [
            "akmod-nvidia",
            "akmod-nvidia-open",
            "xorg-x11-drv-nvidia",
            "kmod-nvidia",
            "kmod-nvidia-open-dkms",
        ]

        candidate_packages: list[str] = []
        recommended_package = ""

        if command_exists("dnf"):
            for pkg in candidate_priority:
                rq = run_user_cmd(
                    ["dnf", "repoquery", "--qf", "%{name}-%{version}-%{release}.%{arch}", pkg],
                    timeout=45,
                )
                stdout = rq.get("stdout", "") or ""
                available = bool(rq.get("ok")) and (pkg in stdout)
                diag["checks"][f"repoquery_{pkg}"] = {
                    "ok": bool(available),
                    "stdout_excerpt": stdout[:1200],
                    "stderr_excerpt": (rq.get("stderr", "") or "")[:500],
                }
                if available:
                    candidate_packages.append(pkg)
                    if not recommended_package:
                        recommended_package = pkg

            if candidate_packages:
                diag["checks"]["available_driver_branches"] = {
                    "ok": True,
                    "branches": sorted(set(candidate_packages)),
                }
            else:
                search = run_user_cmd(["dnf", "search", "nvidia"], timeout=45)
                diag["checks"]["dnf_search_nvidia"] = {
                    "ok": search.get("ok", False),
                    "stdout_excerpt": (search.get("stdout", "") or "")[:2000],
                    "stderr_excerpt": (search.get("stderr", "") or "")[:500],
                }

        suited_package = recommended_package or (candidate_packages[0] if candidate_packages else "")
        diag["recommended_package"] = recommended_package
        diag["candidate_packages"] = sorted(set(candidate_packages))[:30]
        diag["suited_package"] = suited_package

    options = []
    options.append("Option A (quick check): reboot and verify modules with: lsmod | grep -E 'nvidia|nouveau'")

    if secure_boot_enabled and not nvidia_module_active:
        options.append(
            "Option B (Secure Boot path): disable Secure Boot in firmware OR enroll/sign NVIDIA DKMS module (MOK), then reboot"
        )

    installed_pkgs = diag.get("checks", {}).get("installed_nvidia_packages", {}).get("packages", [])
    if base_distro in ("ubuntu", "debian"):
        if diag.get("suited_package"):
            options.append(
                f"Option C (suited package): install '{diag.get('suited_package')}' then reboot"
            )
        if not installed_pkgs and not diag.get("suited_package"):
            options.append(
                "Option C (explicit install): choose an available branch and install it (example: sudo apt install nvidia-driver-550), then sudo update-initramfs -u && sudo reboot"
            )
        elif not nvidia_module_active:
            options.append(
                "Option C (installed but inactive): run sudo update-initramfs -u, check dkms status, reboot, then verify nvidia-smi"
            )
    elif base_distro == "fedora":
        if diag.get("suited_package"):
            options.append(
                f"Option C (suited package): install '{diag.get('suited_package')}' and reboot"
            )
        else:
            options.append(
                "Option C (explicit package check): run 'dnf repoquery akmod-nvidia' and install the matching NVIDIA package from enabled repos"
            )
        if not nvidia_module_active:
            options.append(
                "Option C (installed but inactive): rebuild initramfs if needed (dracut --force), reboot, then verify nvidia-smi"
            )

    if nouveau_active:
        options.append(
            "Option D (nouveau still active): ensure proprietary module loads first; if needed, apply distro-supported nouveau blacklist workflow and regenerate initramfs"
        )

    if diag.get("open_gsp_mismatch"):
        options.append(
            "Option F (open/GSP mismatch detected): remove nvidia-open packages, install proprietary branch, rebuild initramfs, then reboot"
        )

    if not nvidia_module_active:
        options.append("Option E (diagnostics): collect dmesg/journal errors for nvidia/nouveau module load failures")

    diag["command_block"] = build_nvidia_command_block(diag, base_distro)
    diag["options"] = options
    return diag


def build_nvidia_command_block(diag: dict, base_distro: str) -> list[str]:
    """Create practical command block users can copy/paste."""
    if base_distro not in ("ubuntu", "debian", "fedora"):
        return []

    if base_distro == "fedora":
        suited = (diag or {}).get("suited_package", "") or "akmod-nvidia"
        if "open" in suited:
            suited = "akmod-nvidia"
        return [
            "dnf repoquery akmod-nvidia",
            "dnf repoquery xorg-x11-drv-nvidia",
            "sudo dnf remove -y akmod-nvidia-open kmod-nvidia-open-dkms",
            f"sudo dnf install -y {suited}",
            "sudo dnf install -y xorg-x11-drv-nvidia xorg-x11-drv-nvidia-libs",
            "sudo akmods --force",
            "sudo dracut -f",
            "sudo reboot",
            "# after reboot:",
            "lsmod | grep -E 'nvidia|nouveau'",
            "nvidia-smi",
            "journalctl -k -b --no-pager | grep -Ei 'nvidia|nouveau|drm|module'",
        ]

    suited = (diag or {}).get("suited_package", "")
    if "open" in suited:
        suited = ""
    cmds = [
        "ubuntu-drivers devices",
        "lsmod | grep -E 'nvidia|nouveau'",
        "mokutil --sb-state",
    ]
    if suited:
        cmds.append(f"sudo apt-get install -y {suited}")
    else:
        cmds.append("apt-cache search '^nvidia-driver-[0-9]+'")
        cmds.append("sudo apt-get install -y nvidia-driver-550")
    cmds += [
        "sudo update-initramfs -u",
        "sudo reboot",
        "# after reboot:",
        "nvidia-smi",
        "lsmod | grep -E 'nvidia|nouveau'",
        "journalctl -b --no-pager | grep -Ei 'nvidia|nouveau|dkms|secure boot|module'",
    ]
    return cmds


def gather_journalctl_debug(run_user_cmd, journalctl_lines: int = 8000) -> dict:
    """Collect journalctl slices for troubleshooting in markdown report."""
    data = {
        "enabled": True,
        "available": command_exists("journalctl"),
        "lines": int(max(50, journalctl_lines)),
        "sections": {},
        "notes": [],
        "kwin_crash_analysis": {
            "risk_level": "unknown",
            "score": 0,
            "signals": [],
            "next_steps": [],
        },
    }
    if not data["available"]:
        data["notes"].append("journalctl command not available")
        return data

    lines_arg = str(data["lines"])
    commands = {
        "boot_tail": ["journalctl", "-b", "--no-pager", "-n", lines_arg],
        "warnings_and_errors": ["journalctl", "-b", "--no-pager", "-p", "warning", "-n", lines_arg],
        "graphics_filter": [
            "journalctl", "-b", "--no-pager", "-n", lines_arg,
            "--grep", "nvidia|nouveau|kwin|xwayland|drm|gpu|glmark|mangohud",
        ],
        "kwin_user_unit": [
            "journalctl", "--user-unit", "plasma-kwin_wayland", "-b", "--no-pager", "-n", lines_arg,
        ],
        "kwin_focus_user": [
            "journalctl", "--user", "-b", "--no-pager", "-n", lines_arg,
            "--grep", "kwin_wayland_drm|kwin_scene_opengl|GL_INVALID|prepareAtomicPresentation|xwayland|EGL|drm",
        ],
        "kernel_drm_focus": [
            "journalctl", "-k", "-b", "--no-pager", "-n", lines_arg,
            "--grep", "drm|nvidia|nouveau|amdgpu|i915|simpledrm",
        ],
    }
    for key, cmd in commands.items():
        res = run_user_cmd(cmd, timeout=45)
        if (not res.get("ok")) and ("--grep" in cmd):
            # Some journalctl versions are stricter about --grep; keep graceful fallback.
            fallback_cmd = list(cmd)
            grep_idx = fallback_cmd.index("--grep")
            del fallback_cmd[grep_idx:grep_idx + 2]
            res = run_user_cmd(fallback_cmd, timeout=45)
        stderr_text = (res.get("stderr", "") or "")
        section_ok = bool(res.get("ok", False)) or ("No journal files were found." in stderr_text)
        data["sections"][key] = {
            "ok": section_ok,
            "cmd": " ".join(cmd),
            "returncode": res.get("returncode"),
            "stdout": (res.get("stdout", "") or "")[:120000],
            "stderr": stderr_text[:5000],
        }

    if command_exists("coredumpctl"):
        coredump_res = run_user_cmd(["coredumpctl", "list", "kwin_wayland", "--no-pager"], timeout=30)
        data["sections"]["coredumpctl"] = {
            "ok": coredump_res.get("ok", False),
            "cmd": "coredumpctl list kwin_wayland --no-pager",
            "returncode": coredump_res.get("returncode"),
            "stdout": (coredump_res.get("stdout", "") or "")[:120000],
            "stderr": (coredump_res.get("stderr", "") or "")[:5000],
        }

    data["kwin_crash_analysis"] = analyze_kwin_crash_signals(data.get("sections", {}))
    return data


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
        lines.append(
            "FPS benchmark unavailable (glmark2/glxgears not accessible in the active desktop session)."
        )

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
    for hint in pipeline_analysis.get("gaming_compat_hints", [])[:4]:
        conclusions.append(f"Gaming compatibility note: {hint}")
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
    gpu_inventory,
    firmware_security_info,
    possible_nvidia_drivers,
    pipeline_packages,
    inspection_coverage,
    gaming_signals,
    operational_hints,
    driver_info, renderer, baseline_fps, fps_tool, target_fps,
    baseline_used_mb, baseline_avail_mb, target_used_mb,
    smooth, mouse_notes, driver_suitable, driver_notes,
    pipeline_analysis,
    fps_strategy_findings,
    compositor_diagnostics,
    assessment, conclusions,
    nvidia_instructions,
    nvidia_activation_diagnostics,
    journalctl_debug,
    package_manager_diagnostics,
    package_install_result,
    sudo_passwordless_result,
) -> None:
    matrix_has_fractional = any(
        abs(float(run.get("requested_scale", 1.0)) - round(float(run.get("requested_scale", 1.0)))) > 1e-6
        for run in test_runs.values()
    )

    _section("System Information")
    _bullet("Hostname",            platform.node())
    _bullet("OS",                  osr.get("PRETTY_NAME", "unknown"))
    _bullet("Kernel",              platform.release())
    _bullet("Base distro",         base_distro)
    _bullet("CPU",                 cpu_model or "unknown")
    _bullet("CPU cores",           cpu_cores)
    _bullet("RAM total",           f"{ram_total_mb} MB ({ram_total_mb / 1024:.1f} GB)")
    _bullet("GPU",                 gpu_lspci or "unknown")
    if gpu_inventory:
        for idx, gpu in enumerate(gpu_inventory, 1):
            _bullet(f"GPU[{idx}] model", gpu.get("model", "unknown"))
            _bullet(f"GPU[{idx}] active", gpu.get("driver_in_use") or "unknown")
            mods = gpu.get("kernel_modules", [])
            if mods:
                _bullet(f"GPU[{idx}] possible", ", ".join(mods))
    _bullet("OpenGL renderer",     renderer or "unknown")
    _bullet("GPU driver (kernel)", ", ".join(driver_info.get("loaded", [])) or "unknown")
    _bullet("Boot mode", firmware_security_info.get("boot_mode", "unknown"))
    sb = firmware_security_info.get("secure_boot", {})
    _bullet("Secure Boot", sb.get("state", "unknown"))
    _bullet("BIOS", f"{firmware_security_info.get('bios_vendor', '')} {firmware_security_info.get('bios_version', '')} ({firmware_security_info.get('bios_date', '')})".strip())
    _bullet("Mainboard", firmware_security_info.get("board_name", "unknown"))
    _bullet("System", f"{firmware_security_info.get('sys_vendor', '')} {firmware_security_info.get('product_name', '')}".strip())
    if possible_nvidia_drivers.get("recommended"):
        _bullet("NVIDIA recommended", possible_nvidia_drivers.get("recommended"))
    if possible_nvidia_drivers.get("available"):
        _bullet("NVIDIA possible", ", ".join(possible_nvidia_drivers.get("available", [])[:8]))

    _section("Session")
    _bullet("Display server",      session_type)
    _bullet("Desktop env",         desktop)
    _bullet("Compositor / WM",     wm_comp.get("compositor", "unknown"))
    _bullet("XWayland present",    "yes" if xwayland_present else "no")

    _section("Desktop Pipeline Packages")
    _bullet("Package manager", pipeline_packages.get("package_manager", "unknown"))
    rows = pipeline_packages.get("rows", [])
    if rows:
        for row in rows:
            print(f"    - {row.get('component', 'component')}: {row.get('package', 'package')} = {row.get('version', 'unknown')}")
    else:
        print("    - No pipeline package versions resolved")
    active_runtime = pipeline_packages.get("active_runtime", [])
    if active_runtime:
        print("    Active runtime components:")
        for item in active_runtime:
            print(
                "      - "
                f"{item.get('role', 'role')}: process={item.get('process', 'unknown')}, "
                f"package={item.get('package', 'unknown')}, version={item.get('version', 'unknown')}"
            )
    else:
        print("    Active runtime components: none detected in current process list")

    _section("Package Manager Diagnostics")
    _bullet("Package manager", package_manager_diagnostics.get("pm", "unknown"))
    _bullet("Installability probe", "yes" if package_manager_diagnostics.get("can_install") else "no")
    _bullet("Immutable environment", "yes" if package_manager_diagnostics.get("immutable") else "no")
    live_info = package_manager_diagnostics.get("live_env", {}) or {}
    _bullet("Likely live environment", "yes" if live_info.get("likely_live") else "no")
    package_resolution = package_install_result.get("package_resolution", {}) or {}
    _bullet(
        "Package resolution",
        f"{package_resolution.get('resolved_tool_count', 0)} / {package_resolution.get('missing_tool_count', 0)} missing tools mapped to installable packages",
    )
    if live_info.get("reasons"):
        print(f"    Live detection reasons: {', '.join(live_info.get('reasons', [])[:8])}")
    if package_manager_diagnostics.get("reasons"):
        print(f"    Installability reasons: {', '.join(package_manager_diagnostics.get('reasons', [])[:8])}")
    _bullet("Install attempted", "yes" if package_install_result.get("attempted") else "no")
    if package_install_result.get("requested_packages"):
        print(f"    Requested packages: {', '.join(package_install_result.get('requested_packages', [])[:12])}")
    if package_install_result.get("attempted"):
        _bullet("Install success", "yes" if package_install_result.get("ok") else "no")
    _bullet("Install result", package_install_result.get("reason", "not-attempted"))
    if package_resolution.get("out_of_sync"):
        print("    Out-of-sync mappings (script vs distro reality):")
        for entry in package_resolution.get("out_of_sync", [])[:20]:
            tool = entry.get("tool", "unknown")
            status = entry.get("status", "unknown")
            candidates = ", ".join(entry.get("candidates", [])[:8]) or "none"
            available = ", ".join(entry.get("available_candidates", [])[:8]) or "none"
            print(f"      - {tool}: {status}; candidates=[{candidates}] available=[{available}]")

    if sudo_passwordless_result.get("attempted") or sudo_passwordless_result.get("reason") not in {"", "not-requested"}:
        _section("Sudo Configuration")
        _bullet("Passwordless sudo requested", "yes" if sudo_passwordless_result.get("attempted") else "no")
        _bullet("Result", "ok" if sudo_passwordless_result.get("ok") else "failed")
        _bullet("Reason", sudo_passwordless_result.get("reason", "unknown"))
        if sudo_passwordless_result.get("target_user"):
            _bullet("Target user", sudo_passwordless_result.get("target_user"))
        if sudo_passwordless_result.get("sudoers_file"):
            _bullet("Sudoers file", sudo_passwordless_result.get("sudoers_file"))
        for step in sudo_passwordless_result.get("steps", [])[:20]:
            print(
                "    - "
                f"{step.get('step', 'step')}: ok={step.get('ok')} rc={step.get('returncode')} "
                f"cmd={step.get('cmd', '')}"
            )

    _section("Inspection Coverage")
    _bullet("Coverage score", f"{inspection_coverage.get('score', 0)} / 100")
    _bullet("Coverage level", inspection_coverage.get("level", "unknown"))
    missing_components = inspection_coverage.get("missing_components", [])
    missing_runtime_roles = inspection_coverage.get("missing_runtime_roles", [])
    if missing_components:
        print(f"    Missing package components: {', '.join(missing_components)}")
    if missing_runtime_roles:
        print(f"    Missing runtime role mappings: {', '.join(missing_runtime_roles)}")
    flags = inspection_coverage.get("flags", [])
    if flags:
        for flag in flags:
            print(f"    flag: {flag}")

    _section("Gaming Optimization Signals")
    _bullet("Kernel", gaming_signals.get("kernel_release", "unknown"))
    _bullet("Kernel flavor tags", ", ".join(gaming_signals.get("kernel_flavor_tags", [])) or "none")
    _bullet("zram enabled", "yes" if gaming_signals.get("zram_enabled") else "no")
    _bullet("CPU governor", gaming_signals.get("cpu_governor", "unknown"))
    _bullet("Platform profile", gaming_signals.get("platform_profile", "unknown"))
    _bullet("gamemoded active", "yes" if gaming_signals.get("gamemoded_active") else "no")
    _bullet("gamemode service", gaming_signals.get("gamemode_service_state", "unknown"))
    _bullet("gamescope active", "yes" if gaming_signals.get("gamescope_active") else "no")
    _bullet("steam active", "yes" if gaming_signals.get("steam_active") else "no")
    binaries = gaming_signals.get("binary_checks", {})
    for tool_name, available in binaries.items():
        _bullet(f"binary {tool_name}", "yes" if available else "no")
    pkg_probe = gaming_signals.get("profile_package_probe", [])
    if pkg_probe:
        print("    Profile package probes:")
        for item in pkg_probe:
            print(f"      - {item.get('package')}: {item.get('version')}")

    _section("Operational Hints")
    for hint in operational_hints:
        print(f"    - {hint}")

    _section("Scaling")
    _bullet("Reference factor",    f"{_REFERENCE_SCALE}x")
    _bullet("Start factor",        f"{start_scale}x")
    _bullet("Start detected via",  start_scale_source)
    _bullet("Baseline factor",     f"{baseline_scale}x")
    _bullet("Detected via",        baseline_scale_source)
    _bullet("Fractional case tested", "yes" if matrix_has_fractional else "no")

    _section("Test Matrix")
    for case_name, run in test_runs.items():
        _bullet(f"{case_name} scale", f"{run.get('requested_scale')}x")
        if run.get("status"):
            _bullet(f"{case_name} status", run.get("status"))
        _bullet(f"{case_name} detected", f"{run.get('detected_scale')}x")
        _bullet(f"{case_name} fps", run.get("fps") if run.get("fps") else "n/a")
        _bullet(f"{case_name} tool", run.get("fps_tool") or "unavailable")
        if run.get("benchmark_note"):
            _bullet(f"{case_name} benchmark note", run.get("benchmark_note"))
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
    if nvidia_activation_diagnostics.get("relevant"):
        _bullet("NVIDIA module active", "yes" if nvidia_activation_diagnostics.get("nvidia_module_active") else "no")
        _bullet("nouveau active", "yes" if nvidia_activation_diagnostics.get("nouveau_active") else "no")
        checks = nvidia_activation_diagnostics.get("checks", {})
        pkgs = checks.get("installed_nvidia_packages", {}).get("packages", [])
        branches = checks.get("available_driver_branches", {}).get("branches", [])
        if pkgs:
            _bullet("Installed NVIDIA pkgs", ", ".join(pkgs[:8]))
        if branches:
            _bullet("Available branches", ", ".join(branches[:8]))
        for opt in nvidia_activation_diagnostics.get("options", []):
            print(f"    {opt}")
        cmd_block = nvidia_activation_diagnostics.get("command_block", [])
        if cmd_block:
            print("    Suggested command block:")
            print("    ```bash")
            for cmd in cmd_block:
                print(f"    {cmd}")
            print("    ```")
        remediation = nvidia_activation_diagnostics.get("auto_remediation", {})
        if remediation:
            _bullet("NVIDIA auto remediation offered", "yes" if remediation.get("offered") else "no")
            _bullet("NVIDIA auto remediation attempted", "yes" if remediation.get("attempted") else "no")
            _bullet("NVIDIA auto remediation result", "ok" if remediation.get("ok") else remediation.get("reason", "n/a"))
            actions = remediation.get("actions", {})
            if actions:
                print(
                    "    planned actions: "
                    f"remove={actions.get('remove_packages', [])}, "
                    f"install={actions.get('install_packages', [])}, "
                    f"post={actions.get('post_commands', [])}"
                )
            for log in remediation.get("logs", [])[:30]:
                print(
                    "    - "
                    f"{log.get('step', 'step')}: ok={log.get('ok')} rc={log.get('returncode')} "
                    f"cmd={log.get('cmd', '')}"
                )
                stderr_excerpt = (log.get("stderr", "") or "")[:240]
                if stderr_excerpt:
                    print(f"      stderr: {stderr_excerpt}")
            post_check = remediation.get("post_check", {})
            if post_check:
                print(
                    "    post-check: "
                    f"open_mismatch={post_check.get('open_gsp_mismatch')}, "
                    f"open_pkgs_remaining={post_check.get('open_packages_remaining', [])}, "
                    f"nvidia_modules={post_check.get('nvidia_loaded_modules', [])}, "
                    f"nvidia_smi_ok={post_check.get('nvidia_smi_ok')}"
                )

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

    _section("Journalctl Debug Capture")
    _bullet("Enabled", "yes" if journalctl_debug.get("enabled") else "no")
    _bullet("journalctl available", "yes" if journalctl_debug.get("available") else "no")
    _bullet("Captured lines", journalctl_debug.get("lines", "n/a"))
    kwin_analysis = journalctl_debug.get("kwin_crash_analysis", {}) or {}
    _bullet("KWin crash risk", kwin_analysis.get("risk_level", "unknown"))
    _bullet("KWin crash score", kwin_analysis.get("score", "n/a"))
    for signal in kwin_analysis.get("signals", []):
        print(f"    signal: {signal}")
    for step in kwin_analysis.get("next_steps", []):
        print(f"    next: {step}")
    for sec_name, sec_data in journalctl_debug.get("sections", {}).items():
        _bullet(f"Section {sec_name}", "ok" if sec_data.get("ok") else "failed")
        if sec_data.get("stderr"):
            print(f"    stderr: {sec_data.get('stderr')[:200]}")

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
    gpu_inventory,
    firmware_security_info,
    possible_nvidia_drivers,
    pipeline_packages,
    inspection_coverage,
    gaming_signals,
    operational_hints,
    driver_info, renderer, baseline_fps, fps_tool, target_fps,
    baseline_used_mb, baseline_avail_mb, target_used_mb,
    smooth, mouse_notes, mouse_stats,
    driver_suitable, driver_notes,
    pipeline_analysis,
    fps_strategy_findings,
    compositor_diagnostics,
    assessment, conclusions,
    nvidia_instructions,
    nvidia_activation_diagnostics,
    mem_breakdown, ps_output: Optional[str],
    journalctl_debug,
    package_manager_diagnostics,
    package_install_result,
    sudo_passwordless_result,
    trace_log,
    console_log,
) -> None:
    matrix_has_fractional = any(
        abs(float(run.get("requested_scale", 1.0)) - round(float(run.get("requested_scale", 1.0)))) > 1e-6
        for run in test_runs.values()
    )

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
        f"- Boot mode: {firmware_security_info.get('boot_mode', 'unknown')}",
        f"- Secure Boot: {firmware_security_info.get('secure_boot', {}).get('state', 'unknown')}",
        f"- BIOS: {firmware_security_info.get('bios_vendor', '')} {firmware_security_info.get('bios_version', '')} ({firmware_security_info.get('bios_date', '')})".strip(),
        f"- Mainboard: {firmware_security_info.get('board_name', 'unknown')}",
        f"- System model: {firmware_security_info.get('sys_vendor', '')} {firmware_security_info.get('product_name', '')}".strip(),
        "",
        "### GPU Inventory",
    ]
    if gpu_inventory:
        for idx, gpu in enumerate(gpu_inventory, 1):
            lines += [
                f"- GPU[{idx}] model: {gpu.get('model', 'unknown')}",
                f"- GPU[{idx}] active driver: {gpu.get('driver_in_use') or 'unknown'}",
                f"- GPU[{idx}] possible drivers: {', '.join(gpu.get('kernel_modules', [])) if gpu.get('kernel_modules') else 'unknown'}",
            ]
    else:
        lines += ["- No GPU inventory parsed"]

    if possible_nvidia_drivers.get("recommended"):
        lines += [f"- NVIDIA recommended package: {possible_nvidia_drivers.get('recommended')}"]
    if possible_nvidia_drivers.get("available"):
        lines += [f"- NVIDIA possible packages: {', '.join(possible_nvidia_drivers.get('available', [])[:20])}"]

    lines += [
        "## Session",
        f"- Session type: {session_type}",
        f"- Desktop: {desktop}",
        f"- Compositor/WM: {wm_comp.get('compositor', 'unknown')}",
        f"- XWayland present: {xwayland_present}",
        "",
        "## Desktop Pipeline Packages",
        f"- Package manager: {pipeline_packages.get('package_manager', 'unknown')}",
        "",
    ]

    pkg_rows = pipeline_packages.get("rows", [])
    if pkg_rows:
        lines += [
            "| Component | Package | Version |",
            "|---|---|---|",
        ]
        for row in pkg_rows:
            component = str(row.get("component", "")).replace("|", "\\|")
            package = str(row.get("package", "")).replace("|", "\\|")
            version = str(row.get("version", "")).replace("|", "\\|")
            lines.append(f"| {component} | {package} | {version} |")
    else:
        lines += ["- No pipeline package versions resolved"]

    active_runtime = pipeline_packages.get("active_runtime", [])
    if active_runtime:
        lines += [
            "",
            "### Active Runtime Components",
            "| Role | Process | Package | Version |",
            "|---|---|---|---|",
        ]
        for item in active_runtime:
            role = str(item.get("role", "")).replace("|", "\\|")
            process = str(item.get("process", "")).replace("|", "\\|")
            package = str(item.get("package", "")).replace("|", "\\|")
            version = str(item.get("version", "")).replace("|", "\\|")
            lines.append(f"| {role} | {process} | {package} | {version} |")
    else:
        lines += ["", "### Active Runtime Components", "- None detected in current process list"]

    lines += [
        "",
        "## Package Manager Diagnostics",
        f"- Package manager: {package_manager_diagnostics.get('pm', 'unknown')}",
        f"- Installability probe: {package_manager_diagnostics.get('can_install')}",
        f"- Immutable environment: {package_manager_diagnostics.get('immutable')}",
        f"- Likely live environment: {(package_manager_diagnostics.get('live_env', {}) or {}).get('likely_live')}",
    ]
    package_resolution = package_install_result.get("package_resolution", {}) or {}
    lines.append(
        "- Package resolution: "
        f"{package_resolution.get('resolved_tool_count', 0)} / {package_resolution.get('missing_tool_count', 0)} missing tools mapped to installable packages"
    )
    live_reasons = (package_manager_diagnostics.get("live_env", {}) or {}).get("reasons", [])
    if live_reasons:
        lines.append(f"- Live detection reasons: {', '.join(live_reasons[:8])}")
    if package_manager_diagnostics.get("reasons"):
        lines.append(f"- Installability reasons: {', '.join(package_manager_diagnostics.get('reasons', [])[:8])}")
    lines += [
        f"- Install attempted: {package_install_result.get('attempted')}",
        f"- Install result: {package_install_result.get('reason', 'not-attempted')}",
    ]
    if package_install_result.get("requested_packages"):
        lines.append(f"- Requested packages: {', '.join(package_install_result.get('requested_packages', [])[:12])}")
    if package_install_result.get("attempted"):
        lines.append(f"- Install success: {package_install_result.get('ok')}")
    if package_resolution.get("out_of_sync"):
        lines += ["", "### Out-of-sync mappings"]
        for entry in package_resolution.get("out_of_sync", [])[:40]:
            tool = entry.get("tool", "unknown")
            status = entry.get("status", "unknown")
            candidates = ", ".join(entry.get("candidates", [])[:8]) or "none"
            available = ", ".join(entry.get("available_candidates", [])[:8]) or "none"
            lines.append(
                f"- {tool}: {status}; candidates=[{candidates}] available=[{available}]"
            )
    if package_manager_diagnostics.get("checks"):
        lines += ["", "### Package manager checks", "```json", json.dumps(package_manager_diagnostics.get("checks", {}), indent=2), "```"]
    if package_install_result.get("logs"):
        lines += ["", "### Package install logs", "```json", json.dumps(package_install_result.get("logs", []), indent=2), "```"]

    if sudo_passwordless_result.get("attempted") or sudo_passwordless_result.get("reason") not in {"", "not-requested"}:
        lines += [
            "",
            "## Sudo Configuration",
            f"- Passwordless sudo requested: {'yes' if sudo_passwordless_result.get('attempted') else 'no'}",
            f"- Result: {'ok' if sudo_passwordless_result.get('ok') else 'failed'}",
            f"- Reason: {sudo_passwordless_result.get('reason', 'unknown')}",
            f"- Target user: {sudo_passwordless_result.get('target_user', '')}",
            f"- Sudoers file: {sudo_passwordless_result.get('sudoers_file', '')}",
            "",
            "```json",
            json.dumps(sudo_passwordless_result, indent=2),
            "```",
        ]

    lines += [
        "",
        "## Inspection Coverage",
        f"- Coverage score: {inspection_coverage.get('score', 0)} / 100",
        f"- Coverage level: {inspection_coverage.get('level', 'unknown')}",
    ]
    missing_components = inspection_coverage.get("missing_components", [])
    missing_runtime_roles = inspection_coverage.get("missing_runtime_roles", [])
    flags = inspection_coverage.get("flags", [])
    if missing_components:
        lines.append(f"- Missing package components: {', '.join(missing_components)}")
    if missing_runtime_roles:
        lines.append(f"- Missing runtime role mappings: {', '.join(missing_runtime_roles)}")
    if flags:
        lines += ["- Flags:"] + [f"  - {flag}" for flag in flags]

    lines += [
        "",
        "## Gaming Optimization Signals",
        f"- Kernel: {gaming_signals.get('kernel_release', 'unknown')}",
        f"- Kernel flavor tags: {', '.join(gaming_signals.get('kernel_flavor_tags', [])) or 'none'}",
        f"- zram enabled: {'yes' if gaming_signals.get('zram_enabled') else 'no'}",
        f"- CPU governor: {gaming_signals.get('cpu_governor', 'unknown')}",
        f"- Platform profile: {gaming_signals.get('platform_profile', 'unknown')}",
        f"- gamemoded active: {'yes' if gaming_signals.get('gamemoded_active') else 'no'}",
        f"- gamemode service state: {gaming_signals.get('gamemode_service_state', 'unknown')}",
        f"- gamescope active: {'yes' if gaming_signals.get('gamescope_active') else 'no'}",
        f"- steam active: {'yes' if gaming_signals.get('steam_active') else 'no'}",
        "",
        "### Gaming Tool Binaries",
    ]
    for tool_name, available in (gaming_signals.get("binary_checks", {}) or {}).items():
        lines.append(f"- {tool_name}: {'yes' if available else 'no'}")
    profile_probe = gaming_signals.get("profile_package_probe", [])
    if profile_probe:
        lines += [
            "",
            "### Distro-profile package probes",
            "| Package | Version |",
            "|---|---|",
        ]
        for item in profile_probe:
            pkg = str(item.get("package", "")).replace("|", "\\|")
            ver = str(item.get("version", "")).replace("|", "\\|")
            lines.append(f"| {pkg} | {ver} |")

    lines += ["", "## Operational Hints"] + [f"- {hint}" for hint in operational_hints]

    lines += [
        "",
        "## Scaling",
        f"- Reference: {_REFERENCE_SCALE}x",
        f"- Start: {start_scale}x  (detected via: {start_scale_source})",
        f"- Baseline: {baseline_scale}x  (detected via: {baseline_scale_source})",
        f"- Fractional case tested: {'yes' if matrix_has_fractional else 'no'}",
        "",
        "## Test Matrix",
        "",
    ]

    for case_name, run in test_runs.items():
        lines += [
            f"- {case_name} requested: {run.get('requested_scale')}x",
            f"- {case_name} status: {run.get('status', 'ok')}",
            f"- {case_name} detected: {run.get('detected_scale')}x",
            f"- {case_name} FPS: {run.get('fps') if run.get('fps') else 'n/a'}",
            f"- {case_name} tool: {run.get('fps_tool') or 'unavailable'}",
            f"- {case_name} benchmark note: {run.get('benchmark_note') or 'none'}",
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
    if nvidia_activation_diagnostics.get("relevant"):
        lines += [
            "",
            "### NVIDIA Activation Diagnostics",
            f"- NVIDIA module active: {'yes' if nvidia_activation_diagnostics.get('nvidia_module_active') else 'no'}",
            f"- nouveau active: {'yes' if nvidia_activation_diagnostics.get('nouveau_active') else 'no'}",
            "",
            "```json",
            json.dumps(nvidia_activation_diagnostics, indent=2),
            "```",
        ]
        options = nvidia_activation_diagnostics.get("options", [])
        if options:
            lines += ["", "### NVIDIA Recovery Options"] + [f"- {opt}" for opt in options]
        command_block = nvidia_activation_diagnostics.get("command_block", [])
        if command_block:
            lines += ["", "### NVIDIA Suggested Commands", "```bash"] + command_block + ["```"]
        remediation = nvidia_activation_diagnostics.get("auto_remediation", {})
        if remediation:
            lines += [
                "",
                "### NVIDIA Auto Remediation",
                f"- Offered: {'yes' if remediation.get('offered') else 'no'}",
                f"- Attempted: {'yes' if remediation.get('attempted') else 'no'}",
                f"- Result: {'ok' if remediation.get('ok') else remediation.get('reason', 'failed')}",
                "",
                "```json",
                json.dumps(remediation, indent=2),
                "```",
            ]
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

    lines += ["", "## Journalctl Debug"]
    lines += [
        f"- Enabled: {journalctl_debug.get('enabled')}",
        f"- journalctl available: {journalctl_debug.get('available')}",
        f"- Captured lines: {journalctl_debug.get('lines')}",
    ]
    kwin_analysis = journalctl_debug.get("kwin_crash_analysis", {}) or {}
    lines += [
        f"- KWin crash risk: {kwin_analysis.get('risk_level', 'unknown')}",
        f"- KWin crash score: {kwin_analysis.get('score', 0)}",
    ]
    if kwin_analysis.get("signals"):
        lines.append("- KWin crash signals:")
        for signal in kwin_analysis.get("signals", []):
            lines.append(f"  - {signal}")
    if kwin_analysis.get("next_steps"):
        lines.append("- KWin next-step commands:")
        for step in kwin_analysis.get("next_steps", []):
            lines.append(f"  - {step}")
    for note in journalctl_debug.get("notes", []):
        lines.append(f"- Note: {note}")
    for sec_name, sec_data in journalctl_debug.get("sections", {}).items():
        lines += [
            "",
            f"### journalctl section: {sec_name}",
            f"- Command: {sec_data.get('cmd', '')}",
            f"- Success: {sec_data.get('ok')}",
            "```text",
            sec_data.get("stdout", "") or "(no output)",
            "```",
        ]
        if sec_data.get("stderr"):
            lines += ["```text", sec_data.get("stderr"), "```"]

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
    parser.add_argument(
        "--auto-fix-nvidia",
        action="store_true",
        help="Automatically apply NVIDIA proprietary remediation without interactive prompt when mismatch is detected.",
    )
    parser.add_argument(
        "--make-sudo-passwordless",
        action="store_true",
        help="Configure passwordless sudo (NOPASSWD: ALL) for the invoking user via /etc/sudoers.d.",
    )
    parser.add_argument(
        "--fps-mode",
        choices=["auto", "fullscreen", "windowed", "offscreen"],
        default="auto",
        help=(
            "glmark mode policy. auto picks a stable mode per desktop "
            "(KDE Wayland -> windowed, others -> fullscreen)."
        ),
    )
    parser.add_argument(
        "--fps-window-size",
        default="1920x1080",
        help="Window size for --fps-mode windowed (default: 1920x1080).",
    )
    parser.add_argument(
        "--journalctl-lines",
        type=int,
        default=8000,
        help="Number of lines per journalctl debug section in report (default: 8000).",
    )
    parser.add_argument(
        "--no-journalctl",
        action="store_true",
        help="Skip journalctl debug capture in report.",
    )
    parser.add_argument(
        "--enable-scale-safety-guard",
        action="store_true",
        help="Enable safety guard that skips risky scale switching combinations (off by default).",
    )
    args = parser.parse_args()
    interactive = not args.non_interactive
    trace(f"main start: argv={sys.argv}")
    trace(
        "options: "
        f"non_interactive={args.non_interactive}, fractional_scale={args.fractional_scale}, "
        f"scale_alias={args.scale}, mouse_test={args.mouse_test}, "
        f"allow_glxgears_fallback={args.allow_glxgears_fallback}, fps_mode={args.fps_mode}, "
        f"fps_window_size={args.fps_window_size}, no_journalctl={args.no_journalctl}, "
        f"journalctl_lines={args.journalctl_lines}, enable_scale_safety_guard={args.enable_scale_safety_guard}, "
        f"auto_fix_nvidia={args.auto_fix_nvidia}, make_sudo_passwordless={args.make_sudo_passwordless}, "
        f"output='{args.output}'"
    )

    cprint(C_BLUE, "\n[*] Linux Desktop Scaling Diagnostics")
    cprint(C_BLUE,   "    ===================================")

    # ---- Privileges ----
    priv = preflight_privileges()
    cprint(C_GREEN, f"Privilege check: root={priv['is_root']}, sudo_ok={priv['sudo_ok']}")
    for note in priv["notes"]:
        cprint(C_YELLOW, f"  Note: {note}")
    if not (priv.get("is_root") or priv.get("sudo_ok")):
        cprint(C_RED, "[ERROR] Root-capable access is required in this environment.")
        cprint(C_RED, "[ERROR] Open a root shell (e.g., `sudo -i`) and run the script again.")
        return 2

    sudo_passwordless_result = {
        "attempted": False,
        "ok": False,
        "target_user": "",
        "sudoers_file": "",
        "steps": [],
        "reason": "not-requested",
    }
    if args.make_sudo_passwordless:
        target_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
        if not target_user:
            cprint(C_RED, "[ERROR] Could not determine target user for sudoers update.")
            return 6
        cprint(C_YELLOW, f"[WARN] Configuring passwordless sudo for user '{target_user}' (NOPASSWD: ALL).")
        if interactive and not _ask_yes_no("Proceed with passwordless sudo configuration?", default=False):
            sudo_passwordless_result["reason"] = "user-declined"
            cprint(C_YELLOW, "Passwordless sudo setup skipped by user.")
        else:
            sudo_passwordless_result = configure_passwordless_sudo(priv, target_user)
            if sudo_passwordless_result.get("ok"):
                cprint(C_GREEN, f"Passwordless sudo configured for '{target_user}'.")
            else:
                cprint(C_RED, f"Passwordless sudo setup failed: {sudo_passwordless_result.get('reason', 'unknown')}")

    path_preflight = preflight_writable_paths(args.output)
    if not path_preflight.get("cwd_writable"):
        cprint(C_RED, f"[ERROR] Start directory is not writable: {path_preflight.get('cwd')}")
        cprint(C_RED, f"[ERROR] Details: {path_preflight.get('cwd_error', 'permission denied')}")
        cprint(C_RED, "[ERROR] Change to a writable directory before running this script.")
        return 5
    if not path_preflight.get("output_dir_writable"):
        cprint(
            C_YELLOW,
            f"[WARN] Output directory is not writable: {path_preflight.get('output_dir')} "
            f"({path_preflight.get('output_dir_error', 'permission denied')}).",
        )

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
    immutable_env = detect_immutable()
    live_env = detect_live_environment()
    strategy_tools = fps_strategy.get("tools", []) if isinstance(fps_strategy, dict) else []
    wanted = [
        "lspci", "lshw", "glxinfo", "vulkaninfo", "glmark2", "mangohud",
        "glxgears", "xlsclients", "libinput",
    ] + [tool for tool in strategy_tools if tool]
    deduped_wanted = []
    seen = set()
    for cmd in wanted:
        if cmd in seen:
            continue
        seen.add(cmd)
        deduped_wanted.append(cmd)
    missing = [c for c in deduped_wanted if not tool_available(c)]
    package_resolution = resolve_package_plan(missing, base_distro, pm)
    requested_pkgs = package_resolution.get("installable_packages", [])
    package_manager_diagnostics = gather_package_manager_diagnostics(
        pm=pm,
        run_user_cmd=run_user_cmd,
        base_distro=base_distro,
        requested_packages=requested_pkgs,
    )
    package_install_result = {
        "attempted": False,
        "ok": False,
        "installed": [],
        "logs": [],
        "reason": "not-attempted",
        "requested_packages": requested_pkgs,
        "missing_tools": missing,
        "package_resolution": package_resolution,
    }
    trace(
        "tool check: "
        f"wanted={deduped_wanted}, missing={missing}, pkg_manager={pm}, immutable={immutable_env}, "
        f"live={live_env.get('likely_live')}, resolved={package_resolution.get('resolved_tool_count', 0)}/"
        f"{package_resolution.get('missing_tool_count', 0)}"
    )
    if package_resolution.get("out_of_sync"):
        drift_tools = [entry.get("tool", "unknown") for entry in package_resolution.get("out_of_sync", [])]
        cprint(
            C_YELLOW,
            "Package mapping drift detected for missing tools: "
            + ", ".join(drift_tools[:12]),
        )

    if missing and pm and not immutable_env and not live_env.get("likely_live"):
        cprint(C_YELLOW, f"Auto-installing missing tools: {missing}")
        pkgs = requested_pkgs
        if pkgs:
            result = install_packages(pm, pkgs, priv)
            package_install_result = {
                **result,
                "attempted": True,
                "reason": "attempted",
                "requested_packages": pkgs,
                "missing_tools": missing,
                "package_resolution": package_resolution,
            }
            trace(f"package install result: ok={result.get('ok')} installed_candidates={pkgs}")
            for idx, install_log in enumerate(result.get("logs", []), 1):
                trace(
                    f"install log[{idx}]: ok={install_log.get('ok')} rc={install_log.get('returncode')} "
                    f"cmd={install_log.get('cmd', '')} stderr={(install_log.get('stderr', '') or '')[:500]}"
                )
            status = "OK" if result["ok"] else "some packages failed"
            cprint(C_GREEN if result["ok"] else C_YELLOW, f"Package install: {status} ({pkgs})")
        else:
            package_install_result["reason"] = "no-valid-package-candidates"
            cprint(C_YELLOW, "Package install skipped: no installable package candidates resolved for missing tools.")
    elif missing and not pm:
        package_install_result["reason"] = "no-package-manager"
        cprint(C_YELLOW, "Package install skipped: no supported package manager detected.")
    elif missing and immutable_env:
        package_install_result["reason"] = "immutable-environment"
        cprint(C_YELLOW, "Package install skipped: immutable/image-based environment detected.")
    elif missing and live_env.get("likely_live"):
        package_install_result["reason"] = "live-environment"
        cprint(C_YELLOW, "Package install skipped: likely live/installer environment (transient filesystem/repo state).")

    # ---- Graphics info ----
    cprint(C_BLUE, "\n[*] Gathering graphics information...")
    graphics    = gather_graphics_info(run_user_cmd, priv)
    lsmod_text  = graphics.get("lsmod", {}).get("stdout", "")
    driver_info = gather_driver_info(lsmod_text, priv)
    gpu_inventory = parse_lspci_gpu_inventory(graphics.get("lspci", {}).get("stdout", ""))
    firmware_security_info = gather_platform_firmware_security_info(run_user_cmd)
    possible_nvidia_drivers = parse_possible_nvidia_drivers(base_distro, run_user_cmd)
    pipeline_packages = gather_desktop_pipeline_packages(
        base_distro=base_distro,
        session_type=session_type,
        desktop=desktop,
        wm_comp=wm_comp,
        processes=processes,
        driver_info=driver_info,
        possible_nvidia_drivers=possible_nvidia_drivers,
        run_user_cmd=run_user_cmd,
    )

    gpu_lspci = ""
    for line in graphics.get("lspci", {}).get("stdout", "").splitlines():
        if any(k in line.lower() for k in ("vga", "3d controller", "display controller")):
            m_gpu = re.search(r":\s*(.+?)(?:\s*\(rev\s+[0-9a-fA-F]+\))?$", line)
            if m_gpu:
                gpu_lspci = m_gpu.group(1).strip()
            else:
                gpu_lspci = line.strip()
            break

    glxinfo_out = graphics.get("glxinfo", {}).get("stdout", "")
    renderer = ""
    for line in glxinfo_out.splitlines():
        if "OpenGL renderer" in line:
            renderer = line.split(":", 1)[1].strip()
            break

    driver_suitable, driver_notes = assess_driver_suitability(glxinfo_out, driver_info)
    inspection_coverage = evaluate_inspection_coverage(
        pipeline_packages=pipeline_packages,
        session_type=session_type,
        desktop=desktop,
        renderer=renderer,
        gpu_inventory=gpu_inventory,
    )
    gaming_signals = gather_gaming_optimization_signals(
        base_distro=base_distro,
        session_type=session_type,
        desktop=desktop,
        wm_comp=wm_comp,
        processes=processes,
        run_user_cmd=run_user_cmd,
    )
    operational_hints = build_operational_hints(base_distro=base_distro, gaming_signals=gaming_signals, live_env=live_env)

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

    guard_scale, guard_reason = should_guard_scale_switching(
        session_type=session_type,
        desktop=desktop,
        driver_info=driver_info,
        enable_scale_safety_guard=args.enable_scale_safety_guard,
    )
    if guard_scale:
        cprint(C_YELLOW, f"Scale switching safety guard active: {guard_reason}")
        trace(f"scale guard active: reason={guard_reason}")

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

    def run_measurement_case(
        case_name: str,
        requested_scale: float,
        switch_method: str,
        status: str = "ok",
        benchmark_required: bool = False,
    ) -> dict:
        detected_scale, detected_src = detect_current_scale(session_env, desktop, run_user_cmd, home_dir)
        auto_glxgears_fallback = bool(args.allow_glxgears_fallback or benchmark_required)
        if benchmark_required and not args.allow_glxgears_fallback:
            trace(
                f"benchmark fallback auto-enabled for case={case_name} "
                "because scale switch succeeded and primary benchmark was unavailable"
            )
        fps_value, fps_used_tool = measure_fps(
            run_user_cmd,
            allow_glxgears_fallback=auto_glxgears_fallback,
            fps_mode=args.fps_mode,
            fps_window_size=args.fps_window_size,
            session_type=session_type,
            desktop=desktop,
        )
        used_mb, avail_mb = ram_snapshot()
        return {
            "case": case_name,
            "status": status,
            "requested_scale": requested_scale,
            "detected_scale": detected_scale,
            "detected_source": detected_src,
            "switch_method": switch_method,
            "fps": fps_value,
            "fps_tool": fps_used_tool,
            "benchmark_note": (
                "benchmark-required-but-unavailable"
                if benchmark_required and (fps_used_tool == "unavailable" or fps_value <= 0)
                else ""
            ),
            "used_mb": used_mb,
            "avail_mb": avail_mb,
        }

    # Immediate first run at current scale (mapped to nearest required case)
    first_case = _map_start_scale_to_case(start_scale, required_cases)
    trace(f"test matrix first case mapping: start_scale={start_scale} -> {first_case}")
    cprint(C_BLUE, f"\n[*] Immediate start-scale run mapped to case: {first_case}")
    test_runs[first_case] = run_measurement_case(first_case, required_cases[first_case], "start-scale")
    trace(
        f"case result: {first_case} requested={required_cases[first_case]} "
        f"detected={test_runs[first_case].get('detected_scale')} "
        f"fps={test_runs[first_case].get('fps')} tool={test_runs[first_case].get('fps_tool')}"
    )

    # Run remaining required cases
    for case_name, scale_value in required_cases.items():
        if case_name in test_runs:
            continue
        if guard_scale:
            cprint(C_YELLOW, f"\n[*] Skipping scale switch for {case_name}: {guard_reason}")
            detected_scale, detected_src = detect_current_scale(session_env, desktop, run_user_cmd, home_dir)
            used_mb, avail_mb = ram_snapshot()
            test_runs[case_name] = {
                "case": case_name,
                "status": f"skipped ({guard_reason})",
                "requested_scale": scale_value,
                "detected_scale": detected_scale,
                "detected_source": detected_src,
                "switch_method": "skipped",
                "fps": 0.0,
                "fps_tool": "skipped",
                "used_mb": used_mb,
                "avail_mb": avail_mb,
            }
            continue
        cprint(C_BLUE, f"\n[*] Running case {case_name} at {scale_value}x...")
        ok, method = _ensure_scale(session_env, desktop, scale_value, run_user_cmd, interactive)
        if not ok:
            cprint(C_YELLOW, f"    Could not ensure scale {scale_value}x; proceeding with current detected scale.")
            case_status = f"scale-change-failed ({method})"
        else:
            cprint(C_GREEN, f"    Scale ensured via {method}.")
            case_status = "ok"
        test_runs[case_name] = run_measurement_case(
            case_name,
            scale_value,
            method,
            status=case_status,
            benchmark_required=bool(ok),
        )
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
    for case_name, run in test_runs.items():
        try:
            requested = float(run.get("requested_scale", 1.0))
            detected = float(run.get("detected_scale", 1.0))
            if abs(requested - detected) > 0.05:
                conclusions.append(
                    f"Scale mismatch in {case_name}: requested {requested}x, detected {detected}x. "
                    "Compositor likely rejected or reverted this change."
                )
        except Exception:
            continue
    successful_scale_switch_without_benchmark = any(
        (
            str(run.get("status", "")).startswith("ok")
            and str(run.get("switch_method", "")) not in ("start-scale", "already-at-target", "skipped")
            and str(run.get("benchmark_note", "")) == "benchmark-required-but-unavailable"
        )
        for run in test_runs.values()
    )
    if successful_scale_switch_without_benchmark:
        conclusions.append(
            "At least one scale switch succeeded, but no FPS benchmark result could be captured in-session. "
            "Run from an active graphical user session and ensure glmark2 or glxgears can open the display."
        )
    if guard_scale:
        conclusions.append(f"Scale transitions were guarded to avoid known instability: {guard_reason}.")
    nvidia_instructions = []
    nvidia_activation_diagnostics = {
        "relevant": False,
        "nouveau_active": False,
        "nvidia_module_active": False,
        "checks": {},
        "options": [],
        "notes": [],
    }
    nvidia_context = _detect_nvidia_context(gpu_lspci, renderer, gpu_inventory)
    if nvidia_context:
        nvidia_activation_diagnostics = gather_nvidia_activation_diagnostics(
            run_user_cmd=run_user_cmd,
            base_distro=base_distro,
            gpu_lspci=gpu_lspci,
            renderer=renderer,
            driver_info=driver_info,
        )
        if (
            nvidia_activation_diagnostics.get("nouveau_active")
            or nvidia_activation_diagnostics.get("open_gsp_mismatch")
            or not nvidia_activation_diagnostics.get("nvidia_module_active")
        ):
            nvidia_instructions = _nvidia_install_instructions(base_distro, osr)

        remediation_result = maybe_offer_nvidia_proprietary_remediation(
            interactive=interactive,
            auto_fix_nvidia=bool(args.auto_fix_nvidia),
            priv=priv,
            base_distro=base_distro,
            renderer=renderer,
            run_user_cmd=run_user_cmd,
            nvidia_activation_diagnostics=nvidia_activation_diagnostics,
            gpu_lspci=gpu_lspci,
            driver_info=driver_info,
        )
        nvidia_activation_diagnostics["auto_remediation"] = remediation_result
        if remediation_result.get("attempted"):
            post_lsmod = run_cmd(["lsmod"])
            driver_info = gather_driver_info(post_lsmod.get("stdout", ""), priv)
            nvidia_activation_diagnostics = gather_nvidia_activation_diagnostics(
                run_user_cmd=run_user_cmd,
                base_distro=base_distro,
                gpu_lspci=gpu_lspci,
                renderer=renderer,
                driver_info=driver_info,
            )
            nvidia_activation_diagnostics["auto_remediation"] = remediation_result
            if remediation_result.get("ok"):
                conclusions.append(
                    "NVIDIA proprietary remediation was applied automatically. Reboot is still required for final module activation check."
                )
            else:
                conclusions.append(
                    "NVIDIA proprietary remediation was attempted but did not fully complete; check 'NVIDIA Activation Diagnostics' logs."
                )
        trace(
            "nvidia diagnostics: "
            f"relevant={nvidia_activation_diagnostics.get('relevant')} "
            f"nvidia_module_active={nvidia_activation_diagnostics.get('nvidia_module_active')} "
            f"nouveau_active={nvidia_activation_diagnostics.get('nouveau_active')}"
        )
    mem_breakdown = summarize_memory_breakdown()
    if args.no_journalctl:
        journalctl_debug = {
            "enabled": False,
            "available": command_exists("journalctl"),
            "lines": args.journalctl_lines,
            "sections": {},
            "notes": ["journalctl capture disabled by --no-journalctl"],
        }
    else:
        journalctl_debug = gather_journalctl_debug(run_user_cmd, journalctl_lines=args.journalctl_lines)

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
        gpu_lspci=gpu_lspci, gpu_inventory=gpu_inventory,
        firmware_security_info=firmware_security_info,
        possible_nvidia_drivers=possible_nvidia_drivers,
        pipeline_packages=pipeline_packages,
        inspection_coverage=inspection_coverage,
        gaming_signals=gaming_signals,
        operational_hints=operational_hints,
        driver_info=driver_info, renderer=renderer,
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
        nvidia_activation_diagnostics=nvidia_activation_diagnostics,
        journalctl_debug=journalctl_debug,
        package_manager_diagnostics=package_manager_diagnostics,
        package_install_result=package_install_result,
        sudo_passwordless_result=sudo_passwordless_result,
    )

    # ---- Markdown report ----
    ps_output = None
    if not args.no_ps:
        res = run_cmd(["ps", "axu"])
        if res["ok"]:
            ps_output = res["stdout"]

    resolved_output, output_note = resolve_report_output_path(args.output)
    if output_note:
        cprint(C_YELLOW, f"[WARN] {output_note}")
        trace(f"output path adjusted: {output_note}")
    if not resolved_output:
        cprint(C_RED, "[ERROR] No writable output path available for markdown report.")
        return 3

    try:
        write_markdown_report(
            output_path=resolved_output,
            osr=osr, base_distro=base_distro,
            session_type=session_type, desktop=desktop,
            wm_comp=wm_comp, xwayland_present=xwayland_present,
            xwayland_analysis=xwayland_analysis,
            start_scale=start_scale, start_scale_source=start_scale_source,
            baseline_scale=baseline_scale, baseline_scale_source=baseline_scale_source,
            test_runs=test_runs,
            ram_total_mb=ram_total_mb, cpu_model=cpu_model, cpu_cores=os.cpu_count() or 0,
            gpu_lspci=gpu_lspci, gpu_inventory=gpu_inventory,
            firmware_security_info=firmware_security_info,
            possible_nvidia_drivers=possible_nvidia_drivers,
            pipeline_packages=pipeline_packages,
            inspection_coverage=inspection_coverage,
            gaming_signals=gaming_signals,
            operational_hints=operational_hints,
            driver_info=driver_info, renderer=renderer,
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
            nvidia_activation_diagnostics=nvidia_activation_diagnostics,
            mem_breakdown=mem_breakdown, ps_output=ps_output,
            journalctl_debug=journalctl_debug,
            package_manager_diagnostics=package_manager_diagnostics,
            package_install_result=package_install_result,
            sudo_passwordless_result=sudo_passwordless_result,
            trace_log=TRACE_LOG,
            console_log=CONSOLE_LOG,
        )
    except PermissionError as exc:
        cprint(C_RED, f"[ERROR] Failed writing report due to permission error: {exc}")
        cprint(C_RED, "[ERROR] Re-run from a writable directory or set --output /tmp/<name>.md")
        return 4

    trace(f"report write complete: output='{resolved_output}'")
    cprint(C_GREEN, f"\nReport written to: {resolved_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
