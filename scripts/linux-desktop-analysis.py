#!/usr/bin/env python3
"""
Linux Desktop Scaling Diagnostics
==================================
Gathers system info, performance data, and an efficiency assessment of
desktop scaling — including mouse smoothness and driver suitability for
the hardware.

Workflow
--------
1. Collect baseline system info (CPU, RAM, GPU, display, driver).
2. Detect the current (baseline) desktop scale factor.
3. Run an FPS benchmark at the current scale.
4. Prompt the user to switch to a new target scale.
5. Run an FPS benchmark at the new scale and compare results.
6. Reason about RAM usage before and after.
7. Report whether the observed performance is reasonable for the hardware
   and whether the scaling implementation appears efficient.

Usage
-----
    python3 linux-desktop-analysis.py [--non-interactive] [--scale TARGET]
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class SystemInfo:
    hostname: str = ""
    os_pretty: str = ""
    kernel: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    gpu_model: str = ""
    gpu_driver: str = ""
    opengl_renderer: str = ""
    display_server: str = ""
    desktop_env: str = ""


@dataclass
class ScalingInfo:
    factor: float = 1.0
    source: str = ""           # how it was detected
    fractional: bool = False   # True when scale is not an integer


@dataclass
class PerfSample:
    scale: float = 1.0
    fps: float = 0.0
    ram_used_mb: int = 0
    ram_available_mb: int = 0
    duration_s: float = 5.0


@dataclass
class DiagReport:
    system: SystemInfo = field(default_factory=SystemInfo)
    baseline: ScalingInfo = field(default_factory=ScalingInfo)
    target: ScalingInfo = field(default_factory=ScalingInfo)
    perf_baseline: Optional[PerfSample] = None
    perf_target: Optional[PerfSample] = None
    mouse_smooth: Optional[bool] = None
    mouse_notes: str = ""
    driver_suitable: Optional[bool] = None
    driver_notes: str = ""
    assessment: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a subprocess; return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"
    except Exception as exc:  # noqa: BLE001
        return -3, "", str(exc)


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _section(title: str) -> None:
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print('=' * width)


def _bullet(label: str, value: object) -> None:
    print(f"  {label:<30} {value}")


# ---------------------------------------------------------------------------
# System information gathering
# ---------------------------------------------------------------------------

def collect_system_info() -> SystemInfo:
    info = SystemInfo()
    info.hostname = platform.node()
    info.kernel = platform.release()

    # OS pretty name
    for line in _read_file("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            info.os_pretty = line.split("=", 1)[1].strip().strip('"')
            break

    # CPU
    for line in _read_file("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            info.cpu_model = line.split(":", 1)[1].strip()
            break
    info.cpu_cores = os.cpu_count() or 0

    # RAM
    for line in _read_file("/proc/meminfo").splitlines():
        if line.startswith("MemTotal"):
            info.ram_total_mb = int(line.split()[1]) // 1024
        elif line.startswith("MemAvailable"):
            info.ram_available_mb = int(line.split()[1]) // 1024

    # GPU from lspci
    rc, out, _ = _run(["lspci"])
    if rc == 0:
        for line in out.splitlines():
            lower = line.lower()
            if any(k in lower for k in ("vga", "3d controller", "display controller")):
                info.gpu_model = line.split(":", 2)[-1].strip()
                break

    # OpenGL renderer / driver via glxinfo
    rc, out, _ = _run(["glxinfo", "-B"], timeout=8)
    if rc != 0:
        rc, out, _ = _run(["glxinfo"], timeout=8)
    if rc == 0:
        for line in out.splitlines():
            if "OpenGL renderer" in line:
                info.opengl_renderer = line.split(":", 1)[1].strip()
            elif "OpenGL vendor" in line and not info.gpu_driver:
                info.gpu_driver = line.split(":", 1)[1].strip()

    # Kernel GPU driver (first matching module)
    rc, out, _ = _run(["lspci", "-k"])
    if rc == 0:
        in_gpu = False
        for line in out.splitlines():
            lower = line.lower()
            if any(k in lower for k in ("vga", "3d controller", "display controller")):
                in_gpu = True
            elif in_gpu and "kernel driver in use" in lower:
                info.gpu_driver = line.split(":", 1)[1].strip()
                break
            elif in_gpu and line.strip() == "":
                in_gpu = False

    # Display server
    wayland_disp = os.environ.get("WAYLAND_DISPLAY", "")
    x_disp = os.environ.get("DISPLAY", "")
    if wayland_disp:
        info.display_server = f"Wayland ({wayland_disp})"
    elif x_disp:
        info.display_server = f"X11 ({x_disp})"
    else:
        info.display_server = "unknown (no DISPLAY / WAYLAND_DISPLAY)"

    # Desktop environment
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "GDMSESSION"):
        val = os.environ.get(var, "")
        if val:
            info.desktop_env = val
            break
    if not info.desktop_env:
        info.desktop_env = "unknown"

    return info


# ---------------------------------------------------------------------------
# RAM snapshot
# ---------------------------------------------------------------------------

def _ram_available_mb() -> int:
    for line in _read_file("/proc/meminfo").splitlines():
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) // 1024
    return 0


def _ram_used_mb(total_mb: int) -> int:
    avail = _ram_available_mb()
    return total_mb - avail


# ---------------------------------------------------------------------------
# Scaling detection
# ---------------------------------------------------------------------------

def detect_scaling(info: SystemInfo) -> ScalingInfo:
    """Try multiple methods to detect the current desktop scaling factor."""
    de = info.desktop_env.lower()
    ds = info.display_server.lower()

    # ---- GNOME (gsettings) ----
    if "gnome" in de or "unity" in de:
        scale = _detect_gnome_scale()
        if scale is not None:
            return scale

    # ---- KDE / Plasma ----
    if "kde" in de or "plasma" in de:
        scale = _detect_kde_scale()
        if scale is not None:
            return scale

    # ---- Xfce ----
    if "xfce" in de:
        scale = _detect_xfce_scale()
        if scale is not None:
            return scale

    # ---- Generic xrandr fallback (X11 only) ----
    if "x11" in ds or "x" in ds:
        scale = _detect_xrandr_scale()
        if scale is not None:
            return scale

    # ---- Fallback ----
    return ScalingInfo(factor=1.0, source="fallback (assumed 1×)")


def _detect_gnome_scale() -> Optional[ScalingInfo]:
    # Integer scale
    rc, out, _ = _run(["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"])
    if rc == 0:
        m = re.search(r"(\d+)", out)
        if m:
            factor = float(m.group(1))
            if factor >= 1:
                # Also check text-scaling-factor for fractional component
                rc2, out2, _ = _run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor"]
                )
                if rc2 == 0:
                    m2 = re.search(r"(\d+(?:\.\d+)?)", out2)
                    if m2:
                        text_scale = float(m2.group(1))
                        if text_scale != 1.0:
                            return ScalingInfo(
                                factor=factor * text_scale,
                                source="gsettings (integer × text-scaling-factor)",
                                fractional=True,
                            )
                return ScalingInfo(factor=factor, source="gsettings scaling-factor")

    # GNOME 47+ fractional scaling via mutter experimental features
    rc, out, _ = _run(
        ["gsettings", "get", "org.gnome.mutter", "experimental-features"]
    )
    if rc == 0 and "scale-monitor-framebuffer" in out:
        # Read the actual scale from mutter display config if possible
        rc2, out2, _ = _run([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Mutter.DisplayConfig",
            "--object-path", "/org/gnome/Mutter/DisplayConfig",
            "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState",
        ])
        if rc2 == 0:
            m = re.search(r"<double (\d+(?:\.\d+)?)>", out2)
            if m:
                return ScalingInfo(
                    factor=float(m.group(1)),
                    source="gsettings (Mutter fractional)",
                    fractional=True,
                )
    return None


def _detect_kde_scale() -> Optional[ScalingInfo]:
    # KDE Plasma 6: kscreen-doctor
    rc, out, _ = _run(["kscreen-doctor", "--outputs"])
    if rc == 0:
        m = re.search(r"Scale:\s*(\d+(?:\.\d+)?)", out)
        if m:
            factor = float(m.group(1))
            return ScalingInfo(
                factor=factor,
                source="kscreen-doctor",
                fractional=not factor.is_integer(),
            )

    # KDE Plasma 5 / kreadconfig5
    rc, out, _ = _run(["kreadconfig5", "--group", "KScreen", "--key", "ScaleFactor"])
    if rc == 0 and out.strip():
        try:
            factor = float(out.strip())
            return ScalingInfo(
                factor=factor,
                source="kreadconfig5 KScreen/ScaleFactor",
                fractional=not factor.is_integer(),
            )
        except ValueError:
            pass

    return None


def _detect_xfce_scale() -> Optional[ScalingInfo]:
    rc, out, _ = _run(
        ["xfconf-query", "-c", "xsettings", "-p", "/Gdk/WindowScalingFactor"]
    )
    if rc == 0 and out.strip():
        try:
            factor = float(out.strip())
            return ScalingInfo(factor=factor, source="xfconf Gdk/WindowScalingFactor")
        except ValueError:
            pass
    return None


def _detect_xrandr_scale() -> Optional[ScalingInfo]:
    rc, out, _ = _run(["xrandr", "--query"])
    if rc != 0:
        return None
    # Look for "scale: 1.00x1.00" in verbose output or Screen dimensions
    # Use connected outputs and detect effective DPI scaling
    # A simpler heuristic: look for non-1.0 transform in xrandr --verbose
    rc2, out2, _ = _run(["xrandr", "--verbose"])
    if rc2 == 0:
        m = re.search(r"Transform:\s+(\S+)\s+(\S+)\s+(\S+)", out2)
        if m:
            try:
                sx = float(m.group(1))
                if sx != 1.0:
                    return ScalingInfo(
                        factor=round(1.0 / sx, 4),
                        source="xrandr transform matrix",
                        fractional=True,
                    )
            except ValueError:
                pass
    return ScalingInfo(factor=1.0, source="xrandr (assumed 1×)")


# ---------------------------------------------------------------------------
# Scaling change (prompt-based)
# ---------------------------------------------------------------------------

def set_scaling(info: SystemInfo, factor: float) -> bool:
    """Attempt to apply the given scaling factor for the current DE."""
    de = info.desktop_env.lower()

    if "gnome" in de or "unity" in de:
        int_factor = max(1, round(factor))
        rc, _, err = _run(
            ["gsettings", "set", "org.gnome.desktop.interface",
             "scaling-factor", str(int_factor)]
        )
        if rc != 0:
            print(f"  [warn] gsettings set failed: {err.strip()}")
            return False
        return True

    if "kde" in de or "plasma" in de:
        rc, _, err = _run(["kscreen-doctor", f"output.1.scale.{factor}"])
        if rc != 0:
            print(f"  [warn] kscreen-doctor failed: {err.strip()}")
            return False
        return True

    if "xfce" in de:
        int_factor = max(1, round(factor))
        rc, _, err = _run(
            ["xfconf-query", "-c", "xsettings", "-p",
             "/Gdk/WindowScalingFactor", "-s", str(int_factor)]
        )
        if rc != 0:
            print(f"  [warn] xfconf-query set failed: {err.strip()}")
            return False
        return True

    # Generic xrandr
    rc, out, _ = _run(["xrandr", "--query"])
    if rc == 0:
        connected = [
            line.split()[0]
            for line in out.splitlines()
            if " connected" in line
        ]
        if connected:
            output = connected[0]
            scale_str = f"{factor}x{factor}"
            rc2, _, err = _run(["xrandr", "--output", output, "--scale", scale_str])
            if rc2 == 0:
                return True
            print(f"  [warn] xrandr --scale failed: {err.strip()}")
    return False


# ---------------------------------------------------------------------------
# FPS benchmark
# ---------------------------------------------------------------------------

_BENCH_DURATION = 5  # seconds


def measure_fps(duration: int = _BENCH_DURATION) -> float:
    """
    Run glxgears for `duration` seconds and parse the reported FPS.
    Returns 0.0 on failure.
    """
    cmd = ["glxgears", "-info"]

    # Prefer headless/offscreen when DISPLAY is not set
    env = os.environ.copy()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        time.sleep(duration + 1)
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    except FileNotFoundError:
        return 0.0
    except Exception:
        return 0.0

    # glxgears prints "NNN frames in M.M seconds = NNN.NNN FPS"
    fps_values = []
    for line in out.splitlines():
        m = re.search(r"=\s*([\d.]+)\s*FPS", line, re.IGNORECASE)
        if m:
            try:
                fps_values.append(float(m.group(1)))
            except ValueError:
                pass

    if fps_values:
        return round(sum(fps_values) / len(fps_values), 1)
    return 0.0


# ---------------------------------------------------------------------------
# Mouse smoothness assessment
# ---------------------------------------------------------------------------

def assess_mouse_smoothness(info: SystemInfo) -> tuple[bool, str]:
    """
    Heuristically assess mouse smoothness based on display server, driver,
    and whether pointer acceleration / libinput is active.
    Returns (smooth: bool, notes: str).
    """
    notes_parts = []

    # Wayland generally provides better smoothness (no X11 round-trips)
    is_wayland = "wayland" in info.display_server.lower()
    if is_wayland:
        notes_parts.append("Wayland display server (low-latency pointer path)")
    else:
        notes_parts.append("X11 display server (pointer events via X server)")

    # libinput check
    rc, out, _ = _run(["libinput", "--version"])
    has_libinput = rc == 0
    if has_libinput:
        ver = out.strip()
        notes_parts.append(f"libinput {ver} present (smooth acceleration profiles available)")
    else:
        notes_parts.append("libinput not found — evdev/synaptics driver may be active")

    # xinput for X11 acceleration
    if not is_wayland:
        rc2, out2, _ = _run(["xinput", "--list", "--short"])
        if rc2 == 0:
            pointer_count = out2.lower().count("pointer")
            notes_parts.append(f"xinput: {pointer_count} pointer device(s) listed")

    # Refresh rate heuristic: >60 Hz gives smoother feel
    rc3, out3, _ = _run(["xrandr", "--query"])
    refresh = 0.0
    if rc3 == 0:
        in_connected = False
        for line in out3.splitlines():
            # Track connected output sections to avoid parsing disconnected monitors
            if " connected" in line:
                in_connected = True
            elif re.match(r"^\S", line) and " connected" not in line:
                in_connected = False
            if in_connected:
                # Active mode has "*" after the rate, e.g. "60.00*+" or "144.00*"
                m = re.search(r"(\d+(?:\.\d+)?)\*", line)
                if m:
                    try:
                        refresh = float(m.group(1))
                        break
                    except ValueError:
                        pass
    if refresh:
        notes_parts.append(f"Active refresh rate: {refresh} Hz")

    smooth = is_wayland or has_libinput
    if refresh and refresh < 60:
        smooth = False
        notes_parts.append("Refresh rate below 60 Hz — noticeable jitter likely")

    return smooth, "; ".join(notes_parts)


# ---------------------------------------------------------------------------
# Driver suitability assessment
# ---------------------------------------------------------------------------

def assess_driver(info: SystemInfo) -> tuple[bool, str]:
    """
    Assess whether the active GPU driver is suitable for the hardware.
    Returns (suitable: bool, notes: str).
    """
    driver = info.gpu_driver.lower()
    gpu = info.gpu_model.lower()
    renderer = info.opengl_renderer.lower()
    notes = []

    # Detect software rendering
    if any(k in renderer for k in ("llvmpipe", "softpipe", "software rasterizer")):
        notes.append(
            "⚠  Software rasterizer active — GPU acceleration is NOT in use. "
            "Install the appropriate driver package."
        )
        return False, " ".join(notes)

    # NVIDIA
    if "nvidia" in gpu or "nvidia" in driver:
        if "nouveau" in driver:
            notes.append(
                "nouveau (open-source) driver active for NVIDIA GPU. "
                "For full performance and proper scaling, consider the proprietary nvidia driver."
            )
            return False, " ".join(notes)
        notes.append(f"NVIDIA proprietary driver '{driver}' active — suitable.")
        return True, " ".join(notes)

    # AMD
    if any(k in gpu for k in ("amd", "radeon", "amdgpu")):
        if "amdgpu" in driver or "radeon" in driver:
            notes.append(f"AMD open-source driver '{driver}' active — suitable.")
            return True, " ".join(notes)
        notes.append(f"Unexpected driver '{driver}' for AMD GPU.")
        return False, " ".join(notes)

    # Intel
    if "intel" in gpu or "i915" in driver or "iris" in driver:
        notes.append(f"Intel driver '{driver}' active — suitable.")
        return True, " ".join(notes)

    # VirtualBox / VMware / QEMU (virtual machines)
    for virt in ("virtualbox", "vmware", "virtio", "vboxvideo", "qxl", "bochs"):
        if virt in gpu or virt in driver or virt in renderer:
            notes.append(
                f"Virtual GPU detected ('{driver}'). Scaling performance depends on "
                "host GPU and guest additions/tools installation."
            )
            return True, " ".join(notes)

    notes.append(f"Driver '{driver}' detected — manual verification recommended.")
    return True, " ".join(notes)


# ---------------------------------------------------------------------------
# Performance assessment
# ---------------------------------------------------------------------------

_FPS_GOOD_THRESHOLD = 200     # glxgears is synthetic; values >> game FPS
_FPS_ACCEPTABLE_RATIO = 0.80  # new-scale FPS / baseline FPS must stay above this


def assess_performance(
    system: SystemInfo,
    baseline: PerfSample,
    target: Optional[PerfSample],
) -> str:
    lines = []

    # RAM reasoning
    ram_used_pct = (
        baseline.ram_used_mb / system.ram_total_mb * 100
        if system.ram_total_mb
        else 0
    )
    lines.append(
        f"RAM: {baseline.ram_used_mb} MB used / {system.ram_total_mb} MB total "
        f"({ram_used_pct:.0f}%) at baseline scale {baseline.scale}×."
    )
    if ram_used_pct > 85:
        lines.append(
            "  ⚠  High memory pressure detected. Fractional scaling may increase "
            "buffer memory usage and worsen performance further."
        )
    elif ram_used_pct > 60:
        lines.append(
            "  ℹ  Moderate memory usage. Monitor for increase with fractional scaling "
            "due to additional framebuffer allocations."
        )
    else:
        lines.append("  ✓  Memory usage is comfortable.")

    if target is None:
        lines.append("No target-scale performance data collected (skipped).")
        return "\n".join(lines)

    ram_delta_mb = target.ram_used_mb - baseline.ram_used_mb
    lines.append(
        f"RAM at target scale {target.scale}×: {target.ram_used_mb} MB used "
        f"({'↑ +' if ram_delta_mb >= 0 else '↓ '}{abs(ram_delta_mb)} MB vs baseline)."
    )
    if ram_delta_mb > 200:
        lines.append(
            "  ⚠  Significant RAM increase with the new scale — likely due to "
            "extra framebuffer copies (e.g. viewport-scaled Wayland surface buffers)."
        )
    elif ram_delta_mb > 50:
        lines.append("  ℹ  Minor RAM increase — within expected range for scaling overhead.")
    else:
        lines.append("  ✓  RAM usage stable across scale change.")

    # FPS reasoning
    if baseline.fps > 0 and target.fps > 0:
        fps_ratio = target.fps / baseline.fps
        lines.append(
            f"FPS: baseline {baseline.fps} → target {target.fps} "
            f"(ratio {fps_ratio:.2f})."
        )
        if fps_ratio >= _FPS_ACCEPTABLE_RATIO:
            lines.append(
                "  ✓  FPS drop is within acceptable range "
                f"(≥ {int(_FPS_ACCEPTABLE_RATIO * 100)}% of baseline). "
                "Scaling implementation appears efficient."
            )
        else:
            lines.append(
                f"  ⚠  FPS dropped by more than {int((1 - _FPS_ACCEPTABLE_RATIO) * 100)}% "
                "under the new scale. This may indicate inefficient compositing or "
                "missing GPU acceleration."
            )
    elif baseline.fps == 0:
        lines.append("  ℹ  Could not measure baseline FPS (glxgears unavailable).")
    else:
        lines.append("  ℹ  Could not measure target FPS.")

    # Hardware expectations
    if system.ram_total_mb >= 8192:
        lines.append(
            f"Hardware profile: {system.ram_total_mb} MB RAM / {system.cpu_cores} CPU cores "
            "— sufficient for fractional scaling at typical resolutions."
        )
    elif system.ram_total_mb >= 4096:
        lines.append(
            f"Hardware profile: {system.ram_total_mb} MB RAM / {system.cpu_cores} CPU cores "
            "— adequate for 1× or 2× integer scaling; fractional scaling may cause "
            "occasional frame drops on heavy desktops."
        )
    else:
        lines.append(
            f"Hardware profile: {system.ram_total_mb} MB RAM / {system.cpu_cores} CPU cores "
            "— low-resource system; prefer integer scaling (1× or 2×) to minimise overhead."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(report: DiagReport) -> None:
    s = report.system

    _section("System Information")
    _bullet("Hostname", s.hostname)
    _bullet("OS", s.os_pretty or "unknown")
    _bullet("Kernel", s.kernel)
    _bullet("CPU", s.cpu_model or "unknown")
    _bullet("CPU cores", s.cpu_cores)
    _bullet("RAM total", f"{s.ram_total_mb} MB ({s.ram_total_mb / 1024:.1f} GB)")
    _bullet("GPU", s.gpu_model or "unknown")
    _bullet("GPU driver", s.gpu_driver or "unknown")
    _bullet("OpenGL renderer", s.opengl_renderer or "unknown")
    _bullet("Display server", s.display_server)
    _bullet("Desktop env", s.desktop_env)

    _section("Scaling — Baseline")
    _bullet("Factor", f"{report.baseline.factor}×")
    _bullet("Detected via", report.baseline.source)
    _bullet("Fractional", "yes" if report.baseline.fractional else "no")

    _section("Scaling — Target")
    _bullet("Factor", f"{report.target.factor}×")
    _bullet("Applied via", report.target.source or "n/a")

    if report.perf_baseline:
        _section("Performance — Baseline Scale")
        p = report.perf_baseline
        _bullet("Scale", f"{p.scale}×")
        _bullet("FPS (glxgears)", p.fps if p.fps else "n/a")
        _bullet("RAM used", f"{p.ram_used_mb} MB")
        _bullet("RAM available", f"{p.ram_available_mb} MB")

    if report.perf_target:
        _section("Performance — Target Scale")
        p = report.perf_target
        _bullet("Scale", f"{p.scale}×")
        _bullet("FPS (glxgears)", p.fps if p.fps else "n/a")
        _bullet("RAM used", f"{p.ram_used_mb} MB")
        _bullet("RAM available", f"{p.ram_available_mb} MB")

    _section("Mouse Smoothness")
    smooth_str = "likely smooth" if report.mouse_smooth else "potentially degraded"
    _bullet("Assessment", smooth_str)
    print(f"  {report.mouse_notes[:120] if report.mouse_notes else 'n/a'}")

    _section("Driver Suitability")
    suitable_str = "✓ suitable" if report.driver_suitable else "⚠ may be unsuitable"
    _bullet("Assessment", suitable_str)
    for note in report.driver_notes.split(";"):
        if note.strip():
            print(f"  {note.strip()}")

    _section("Efficiency & Performance Assessment")
    print(report.assessment)

    print(f"\n{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    interactive = not args.non_interactive

    print("\n[*] Linux Desktop Scaling Diagnostics")
    print("    Collecting system information...")

    system = collect_system_info()
    baseline_scaling = detect_scaling(system)

    # ---- Baseline performance snapshot ----
    print(f"\n[*] Baseline scale detected: {baseline_scaling.factor}× "
          f"(via {baseline_scaling.source})")

    print("\n[*] Measuring baseline FPS (glxgears, ~5 s)...")
    baseline_fps = measure_fps(_BENCH_DURATION)
    if baseline_fps == 0.0:
        print("    glxgears not available or display not accessible — FPS skipped.")
    else:
        print(f"    Baseline FPS: {baseline_fps}")

    perf_baseline = PerfSample(
        scale=baseline_scaling.factor,
        fps=baseline_fps,
        ram_used_mb=_ram_used_mb(system.ram_total_mb),
        ram_available_mb=_ram_available_mb(),
        duration_s=_BENCH_DURATION,
    )

    # ---- Prompt for target scale ----
    target_factor: Optional[float] = None
    if args.scale is not None:
        target_factor = args.scale
    elif interactive:
        print(
            f"\n[?] Current scale is {baseline_scaling.factor}×. "
            "Enter a new target scale factor (e.g. 1.5, 2) "
            "or press Enter to skip: ",
            end="",
            flush=True,
        )
        try:
            raw = input().strip()
            if raw:
                target_factor = float(raw)
        except (ValueError, EOFError):
            pass

    report = DiagReport(
        system=system,
        baseline=baseline_scaling,
        target=ScalingInfo(factor=baseline_scaling.factor, source="no change"),
        perf_baseline=perf_baseline,
    )

    perf_target: Optional[PerfSample] = None

    if target_factor is not None and target_factor != baseline_scaling.factor:
        print(f"\n[*] Applying target scale: {target_factor}×...")
        applied = set_scaling(system, target_factor)
        target_source = ""
        if applied:
            print("    Scale applied. Waiting 2 s for compositor to settle...")
            time.sleep(2)
            target_source = f"set programmatically ({system.desktop_env})"
        else:
            print(
                "    Could not apply scale automatically. "
                "Please apply it manually and press Enter to continue.",
                end="",
                flush=True,
            )
            if interactive:
                try:
                    input()
                except EOFError:
                    pass
            target_source = "applied manually"

        print(f"\n[*] Measuring target FPS (glxgears, ~{_BENCH_DURATION} s)...")
        target_fps = measure_fps(_BENCH_DURATION)
        if target_fps == 0.0:
            print("    glxgears not available or display not accessible — FPS skipped.")
        else:
            print(f"    Target FPS: {target_fps}")

        perf_target = PerfSample(
            scale=target_factor,
            fps=target_fps,
            ram_used_mb=_ram_used_mb(system.ram_total_mb),
            ram_available_mb=_ram_available_mb(),
            duration_s=_BENCH_DURATION,
        )

        report.target = ScalingInfo(
            factor=target_factor,
            source=target_source,
            fractional=not target_factor.is_integer(),
        )
        report.perf_target = perf_target

        # Restore original scale
        print(f"\n[*] Restoring baseline scale ({baseline_scaling.factor}×)...")
        restored = set_scaling(system, baseline_scaling.factor)
        if not restored:
            print(
                f"    Could not restore scale automatically. "
                f"Please manually restore to {baseline_scaling.factor}× if desired."
            )

    # ---- Mouse and driver assessment ----
    print("\n[*] Assessing mouse smoothness...")
    mouse_smooth, mouse_notes = assess_mouse_smoothness(system)

    print("[*] Assessing driver suitability...")
    driver_suitable, driver_notes = assess_driver(system)

    report.mouse_smooth = mouse_smooth
    report.mouse_notes = mouse_notes
    report.driver_suitable = driver_suitable
    report.driver_notes = driver_notes
    report.assessment = assess_performance(system, perf_baseline, perf_target)

    print_report(report)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="linux-desktop-analysis.py",
        description=(
            "Linux Desktop Scaling Diagnostics — gather system info, "
            "measure FPS at baseline and target scale, and assess "
            "performance efficiency."
        ),
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip all interactive prompts (useful for automation).",
    )
    p.add_argument(
        "--scale",
        type=float,
        metavar="FACTOR",
        help="Target scale factor to test (e.g. 1.5, 2). "
             "Skips the interactive prompt.",
    )
    return p


if __name__ == "__main__":
    parser = _build_parser()
    sys.exit(run(parser.parse_args()))
