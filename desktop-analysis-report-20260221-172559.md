# Desktop Scaling Diagnostic Report
- Generated: 2026-02-21T17:26:38.849908

## System Summary
- Hostname: garuda-xfce
- Kernel: 6.12.65-1-lts
- Distro: Garuda Linux
- Base distro: arch
- CPU: Intel(R) Core(TM) i7-4790 CPU @ 3.60GHz (8 cores)
- RAM: 15856 MB (15.5 GB)
- GPU: 02.0 Display controller [0380]: Intel Corporation Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics Controller [8086:0412]
- OpenGL renderer: unknown
- Boot mode: uefi
- Secure Boot: unknown
- BIOS: American Megatrends Inc. 0601 (03/16/2017)
- Mainboard: H81M-P-SI
- System model: OEGStone H81M-P-SI

### GPU Inventory
- GPU[1] model: 02.0 Display controller [0380]: Intel Corporation Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics Controller [8086:0412]
- GPU[1] active driver: i915
- GPU[1] possible drivers: i915
- GPU[2] model: 00.0 VGA compatible controller [0300]: NVIDIA Corporation GP108 [GeForce GT 1030] [10de:1d01]
- GPU[2] active driver: nouveau
- GPU[2] possible drivers: nouveau
## Session
- Session type: x11
- Desktop: XFCE
- Compositor/WM: xfwm4
- XWayland present: False

## Desktop Pipeline Packages
- Package manager: pacman

| Component | Package | Version |
|---|---|---|
| Display server (Wayland) | wayland | 1.24.0-1 |
| Display server (X11) | xorg-server | 21.1.21-1 |
| Mesa / GL stack | mesa | 1:25.3.3-2 |
| Input stack | libinput | 1.30.1-1 |
| Input stack | libinput-tools | 1.30.1-1 |
| Desktop shell | xfce4-session | 4.20.3-2 |
| Desktop shell | xfce4-panel | 4.20.6-1 |
| Compositor / WM | xfwm4 | 4.20.0-2 |
| Session manager | xfce4-session | 4.20.3-2 |
| Display manager | lightdm | 1:1.32.0-6 |
| Launcher | xfce4-appfinder | 4.20.0-2 |
| Scaling tools | xfce4-settings | 4.20.3-1 |

### Active Runtime Components
| Role | Process | Package | Version |
|---|---|---|---|
| Compositor / WM | xfwm4 | xfwm4 | 4.20.0-2 |
| Session manager | xfce4-session | xfce4-session | 4.20.3-2 |
| Display manager | lightdm | lightdm | 1:1.32.0-6 |
| Display server (X11) | Xorg | xorg-server | 21.1.21-1 |

## Package Manager Diagnostics
- Package manager: pacman
- Installability probe: False
- Immutable environment: False
- Likely live environment: True
- Package resolution: 0 / 7 missing tools mapped to installable packages
- Live detection reasons: rootfs:overlay
- Installability reasons: likely-live-environment, no-package-candidates-resolved
- Install attempted: False
- Install result: live-environment

### Out-of-sync mappings
- lshw: no-available-candidate; candidates=[lshw] available=[none]
- glxinfo: no-available-candidate; candidates=[mesa-utils] available=[none]
- vulkaninfo: no-available-candidate; candidates=[vulkan-tools] available=[none]
- glmark2: no-available-candidate; candidates=[glmark2] available=[none]
- mangohud: no-available-candidate; candidates=[mangohud] available=[none]
- glxgears: no-available-candidate; candidates=[mesa-utils] available=[none]
- xlsclients: no-available-candidate; candidates=[xorg-xlsclients] available=[none]

### Package manager checks
```json
{
  "syncdb": {
    "ok": false,
    "stderr": "error: you cannot perform this operation unless you are root.",
    "stdout_excerpt": ""
  }
}
```

## Inspection Coverage
- Coverage score: 88 / 100
- Coverage level: good
- Missing runtime role mappings: Launcher
- Flags:
  - OpenGL renderer unresolved

## Gaming Optimization Signals
- Kernel: 6.12.65-1-lts
- Kernel flavor tags: none
- zram enabled: yes
- CPU governor: schedutil
- Platform profile: unknown
- gamemoded active: no
- gamemode service state: unknown
- gamescope active: no
- steam active: no

### Gaming Tool Binaries
- gamemoderun: no
- gamescope: no
- mangohud: no
- steam: no
- wine: no
- proton: no

### Distro-profile package probes
| Package | Version |
|---|---|
| vulkan-icd-loader | 1.4.335.0-1 |
| mesa | 1:25.3.3-2 |

## Operational Hints
- Live environment detected: package installation/remediation is intentionally treated as non-persistent for this run.
- CPU governor 'schedutil' is generally suitable for gaming workloads.
- zram swap is active, which may improve responsiveness during memory pressure.
- gamemode launcher not found; per-game CPU/IO optimization toggles may be unavailable.
- gamescope binary not found; fullscreen/session isolation optimizations are unavailable.
- Using distro profile probe set: Arch-like profile (Garuda).

## Scaling
- Reference: 1.0x
- Start: 1.0x  (detected via: xfconf-query Gdk/WindowScalingFactor)
- Baseline: 1.0x  (detected via: xfconf-query Gdk/WindowScalingFactor)
- Fractional case tested: yes

## Test Matrix

- base_1.0 requested: 1.0x
- base_1.0 status: skipped (no-active-desktop-session)
- base_1.0 detected: 1.0x
- base_1.0 FPS: n/a
- base_1.0 tool: skipped
- base_1.0 benchmark note: none
- base_1.0 RAM used: 1230 MB

- integer_2.0 requested: 2.0x
- integer_2.0 status: skipped (no-active-desktop-session)
- integer_2.0 detected: 1.0x
- integer_2.0 FPS: n/a
- integer_2.0 tool: skipped
- integer_2.0 benchmark note: none
- integer_2.0 RAM used: 1230 MB

- fractional requested: 1.25x
- fractional status: skipped (no-active-desktop-session)
- fractional detected: 1.0x
- fractional FPS: n/a
- fractional tool: skipped
- fractional benchmark note: none
- fractional RAM used: 1230 MB

## FPS Benchmark
- Baseline tool: skipped
- Baseline FPS: n/a
- Target FPS: n/a

## RAM Usage
- Used at baseline: 1230 MB
- Available at baseline: 14626 MB
- Used at target scale: 1230 MB  (delta: +0 MB)

## Mouse Smoothness
- Assessment: likely smooth
- Notes: X11 display server (pointer events via X server); libinput present (smooth acceleration profiles available); Active refresh rate: 60.0 Hz; Mouse event capture: disabled by default (use --mouse-test to enable)

```json
{
  "device": "/dev/input/event3",
  "method": "disabled",
  "ok": false,
  "error": "disabled by default (use --mouse-test to enable)"
}
```

## Driver Suitability
- Assessment: may be unsuitable
- Notes: ⚠  nouveau (open-source) driver for NVIDIA GPU. Consider proprietary driver for full performance.

### Proprietary NVIDIA install guidance
- Proprietary NVIDIA driver is not active on Garuda Linux.
- Install packages: sudo pacman -S nvidia nvidia-utils
- Reboot and verify: lsmod | grep -E 'nvidia|nouveau'

### NVIDIA Activation Diagnostics
- NVIDIA module active: no
- nouveau active: yes

```json
{
  "relevant": true,
  "gpu_model_detected": "00.0 VGA compatible controller [0300]: NVIDIA Corporation GP108 [GeForce GT 1030] [10de:1d01]",
  "open_module_support_likely": false,
  "open_module_support_reason": "legacy-nvidia-generation-detected (pre-turing; prefer proprietary module)",
  "package_hardware_mismatch": false,
  "package_hardware_mismatch_reason": "",
  "nvidia_vendor_repository": "Use distro-integrated NVIDIA repos/packages (RPM Fusion/Nobara on Fedora-like). NVIDIA .run/self-managed DKMS exists but is not recommended for this tool's automated path.",
  "nouveau_active": true,
  "nvidia_module_active": false,
  "open_gsp_mismatch": false,
  "open_gsp_mismatch_reason": "",
  "installed_open_packages": [],
  "installed_proprietary_packages": [
    "linux-firmware-nvidia"
  ],
  "recommended_package": "",
  "candidate_packages": [],
  "suited_package": "",
  "checks": {
    "nvidia_smi": {
      "ok": false,
      "stdout": "",
      "stderr": "nvidia-smi command not found"
    },
    "modinfo_nvidia": {
      "ok": false,
      "stdout": "",
      "stderr": "modinfo: ERROR: Module nvidia not found."
    },
    "open_gsp_mismatch": {
      "ok": false,
      "detected": false,
      "reason": "",
      "stdout_excerpt": "-- No entries --"
    },
    "installed_nvidia_packages_unified": {
      "ok": true,
      "open": [],
      "proprietary": [
        "linux-firmware-nvidia"
      ]
    },
    "package_hardware_compat": {
      "ok": true,
      "gpu_model": "00.0 VGA compatible controller [0300]: NVIDIA Corporation GP108 [GeForce GT 1030] [10de:1d01]",
      "open_module_support_likely": false,
      "reason": "legacy-nvidia-generation-detected (pre-turing; prefer proprietary module)",
      "installed_open_packages": [],
      "installed_proprietary_packages": [
        "linux-firmware-nvidia"
      ],
      "mismatch_reason": ""
    }
  },
  "simulations": {},
  "command_block": [],
  "command_block_nvidia_cuda_repo": [],
  "command_block_nvidia_runfile": [
    "# NVIDIA .run installer path (advanced; conflicts with package-managed stacks)",
    "sudo dnf remove -y 'akmod-nvidia*' 'kmod-nvidia*' 'xorg-x11-drv-nvidia*' 'nvidia-driver*' 'libnvidia*' || true",
    "# Prefer running from TTY/root console; avoid isolating from an active desktop session.",
    "# Download the .run installer to /tmp, then execute via bash:",
    "sudo bash /tmp/NVIDIA-Linux-*.run --dkms --no-nouveau-check",
    "sudo dracut -f || true",
    "sudo reboot",
    "# after reboot:",
    "nvidia-smi",
    "modinfo nvidia | head -n 20"
  ],
  "options": [
    "Option A (quick check): reboot and verify modules with: lsmod | grep -E 'nvidia|nouveau'",
    "Option B (packaging source): prefer distro-integrated NVIDIA repos/packages; NVIDIA upstream .run/DKMS path exists but is advanced and can conflict with package-manager managed drivers.",
    "Option H (fallback only): use NVIDIA CUDA/Fedora repository path only if distro-integrated package candidates are unavailable or broken.",
    "Option I (NVIDIA .run installer): advanced fallback path; remove distro NVIDIA packages first and expect manual maintenance after kernel updates.",
    "Option D (nouveau still active): ensure proprietary module loads first; if needed, apply distro-supported nouveau blacklist workflow and regenerate initramfs",
    "Option E (diagnostics): collect dmesg/journal errors for nvidia/nouveau module load failures"
  ],
  "notes": [],
  "auto_remediation": {
    "offered": true,
    "accepted": false,
    "attempted": false,
    "ok": false,
    "reason": "user-skipped",
    "issues_detected": [
      "nouveau-active"
    ],
    "action_recommended": true,
    "reboot_required": false,
    "actions": [],
    "logs": [],
    "post_check": {},
    "selected_mode": "skip",
    "runfile_candidate": {},
    "runfile_source": "",
    "runfile_path": "",
    "execution_class": ""
  }
}
```

### NVIDIA Recovery Options
- Option A (quick check): reboot and verify modules with: lsmod | grep -E 'nvidia|nouveau'
- Option B (packaging source): prefer distro-integrated NVIDIA repos/packages; NVIDIA upstream .run/DKMS path exists but is advanced and can conflict with package-manager managed drivers.
- Option H (fallback only): use NVIDIA CUDA/Fedora repository path only if distro-integrated package candidates are unavailable or broken.
- Option I (NVIDIA .run installer): advanced fallback path; remove distro NVIDIA packages first and expect manual maintenance after kernel updates.
- Option D (nouveau still active): ensure proprietary module loads first; if needed, apply distro-supported nouveau blacklist workflow and regenerate initramfs
- Option E (diagnostics): collect dmesg/journal errors for nvidia/nouveau module load failures

### NVIDIA Direct Install Commands (.run Installer)
```bash
# NVIDIA .run installer path (advanced; conflicts with package-managed stacks)
sudo dnf remove -y 'akmod-nvidia*' 'kmod-nvidia*' 'xorg-x11-drv-nvidia*' 'nvidia-driver*' 'libnvidia*' || true
# Prefer running from TTY/root console; avoid isolating from an active desktop session.
# Download the .run installer to /tmp, then execute via bash:
sudo bash /tmp/NVIDIA-Linux-*.run --dkms --no-nouveau-check
sudo dracut -f || true
sudo reboot
# after reboot:
nvidia-smi
modinfo nvidia | head -n 20
```

### NVIDIA Auto Remediation
- Offered: yes
- Attempted: no
- Result: user-skipped
- Execution class: 
- Issues detected: nouveau-active
- Action recommended: yes
- Reboot required: no
- Runfile source: n/a
- Runfile path: n/a

```json
{
  "offered": true,
  "accepted": false,
  "attempted": false,
  "ok": false,
  "reason": "user-skipped",
  "issues_detected": [
    "nouveau-active"
  ],
  "action_recommended": true,
  "reboot_required": false,
  "actions": [],
  "logs": [],
  "post_check": {},
  "selected_mode": "skip",
  "runfile_candidate": {},
  "runfile_source": "",
  "runfile_path": "",
  "execution_class": ""
}
```

```json
{
  "drivers": {
    "i915": {
      "module": "i915",
      "version": "",
      "filename": "/lib/modules/6.12.65-1-lts/kernel/drivers/gpu/drm/i915/i915.ko.zst"
    },
    "nouveau": {
      "module": "nouveau",
      "version": "",
      "filename": "/lib/modules/6.12.65-1-lts/kernel/drivers/gpu/drm/nouveau/nouveau.ko.zst"
    }
  },
  "driver_type": "open-source",
  "loaded": [
    "i915",
    "nouveau"
  ]
}
```

## XWayland Analysis
```json
{
  "xwayland_clients": null,
  "xwayland_clients_list": "",
  "notes": ""
}
```

## Pipeline Analysis
- Pipeline: x11 + integer-scaling
- GPU path: nvidia-nouveau
- Expected efficiency: moderate

```json
{
  "pipeline_class": "x11 + integer-scaling",
  "session_type": "x11",
  "desktop": "XFCE",
  "compositor": "xfwm4",
  "render_path": "x11",
  "scale_path": "integer-scaling",
  "gpu_path": "nvidia-nouveau",
  "xwayland_clients": 0,
  "reference_scale": 1.0,
  "target_scale": null,
  "is_fractional_test": false,
  "benchmark_tool": "skipped",
  "output_topology": {
    "backend": "xrandr",
    "outputs": [
      {
        "name": "HDMI-2",
        "scale": 1.0,
        "refresh_hz": 60.0,
        "mode": "3840x2160",
        "focused": false
      }
    ],
    "notes": []
  },
  "efficiency_expectation": "moderate",
  "likely_bottlenecks": [
    "nouveau driver may limit throughput and frame pacing"
  ],
  "rationale": [
    "NVIDIA open-source driver can underperform compared to proprietary stack on many cards"
  ],
  "gaming_compat_hints": []
}
```

## Desktop Present FPS Strategy
- Strategy: X11 compositor/present strategy
- Primary: X compositor telemetry when available
- Fallback: Output refresh + benchmark + frame-time proxy

```json
{
  "strategy": {
    "id": "x11-generic",
    "name": "X11 compositor/present strategy",
    "primary": "X compositor telemetry when available",
    "fallback": "Output refresh + benchmark + frame-time proxy",
    "tools": [
      "xrandr"
    ]
  },
  "tool_availability": {
    "xrandr": true
  },
  "probes": {
    "xrandr": {
      "ok": true,
      "active_refresh_hz": 60.0
    }
  },
  "notes": []
}
```

## Compositor Diagnostics
- Compositor: xfwm4
- Strategy id: x11-generic

```json
{
  "strategy_id": "x11-generic",
  "compositor": "xfwm4",
  "probes": {
    "compositor-ps": {
      "ok": true,
      "stdout_excerpt": "2131  1.0 127776    323"
    },
    "x11-wm-check": {
      "ok": true,
      "stdout_excerpt": "_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x800032"
    }
  },
  "notes": []
}
```

## Efficiency & Performance Assessment
RAM: 1230 MB used / 15856 MB total (8%) at baseline scale 1.0x.
  ✓  Memory usage is comfortable.
FPS benchmark unavailable (glmark2/glxgears not accessible in the active desktop session).
Hardware profile: 15856 MB RAM / 8 cores — sufficient for fractional scaling at typical resolutions.

## Findings & Reasoning
- 'nouveau' driver active for NVIDIA GPU: consider proprietary driver for better performance.
- GPU driver may be unsuitable — see Driver Suitability section.
- Determined scaling pipeline: x11 + integer-scaling (expected efficiency: moderate).
- Scale mismatch in integer_2.0: requested 2.0x, detected 1.0x. Compositor likely rejected or reverted this change.
- Scale mismatch in fractional: requested 1.25x, detected 1.0x. Compositor likely rejected or reverted this change.
- NVIDIA issue detected and logged; remediation was not applied in this run.

## Memory Breakdown (top processes by RSS)
```json
{
  "ok": true,
  "top": [
    [
      "xfdesktop",
      151592
    ],
    [
      "xfwm4",
      127776
    ],
    [
      "Xorg",
      117224
    ],
    [
      "xfce4-terminal",
      74812
    ],
    [
      "xfce4-session",
      66504
    ],
    [
      "blueman-applet",
      62648
    ],
    [
      "evolution-alarm",
      56612
    ],
    [
      "nm-applet",
      55156
    ],
    [
      "redshift-gtk",
      54228
    ],
    [
      "Thunar",
      51656
    ]
  ]
}
```

## Process List (ps axu)
```text
USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.6  0.0  24124 14772 ?        Ss   17:19   0:02 /usr/lib/systemd/systemd --switched-root --system --deserialize=43
root           2  0.0  0.0      0     0 ?        S    17:19   0:00 [kthreadd]
root           3  0.0  0.0      0     0 ?        S    17:19   0:00 [pool_workqueue_release]
root           4  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kvfree_rcu_reclaim]
root           5  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-rcu_gp]
root           6  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-sync_wq]
root           7  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-slub_flushwq]
root           8  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-netns]
root           9  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/0:0-events]
root          10  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/0:1-events]
root          11  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/0:0H-events_highpri]
root          12  0.4  0.0      0     0 ?        I    17:19   0:01 [kworker/u32:0-kvfree_rcu_reclaim]
root          13  0.8  0.0      0     0 ?        I    17:19   0:03 [kworker/u32:1-events_unbound]
root          14  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-mm_percpu_wq]
root          15  0.0  0.0      0     0 ?        I    17:19   0:00 [rcu_tasks_kthread]
root          16  0.0  0.0      0     0 ?        I    17:19   0:00 [rcu_tasks_rude_kthread]
root          17  0.0  0.0      0     0 ?        I    17:19   0:00 [rcu_tasks_trace_kthread]
root          18  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/0]
root          19  0.0  0.0      0     0 ?        I    17:19   0:00 [rcu_preempt]
root          20  0.0  0.0      0     0 ?        S    17:19   0:00 [rcub/0]
root          21  0.0  0.0      0     0 ?        S    17:19   0:00 [rcu_exp_par_gp_kthread_worker/0]
root          22  0.0  0.0      0     0 ?        S    17:19   0:00 [rcu_exp_gp_kthread_worker]
root          23  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/0]
root          24  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/0]
root          25  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/0]
root          26  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/1]
root          27  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/1]
root          28  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/1]
root          29  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/1]
root          30  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/1:0-events]
root          31  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/1:0H-events_highpri]
root          32  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/2]
root          33  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/2]
root          34  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/2]
root          35  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/2]
root          37  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/2:0H-events_highpri]
root          38  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/3]
root          39  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/3]
root          40  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/3]
root          41  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/3]
root          42  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/3:0-events]
root          43  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/3:0H-events_highpri]
root          44  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/4]
root          45  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/4]
root          46  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/4]
root          47  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/4]
root          48  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/4:0-events]
root          49  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/4:0H-events_highpri]
root          50  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/5]
root          51  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/5]
root          52  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/5]
root          53  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/5]
root          54  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/5:0-events]
root          55  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/5:0H-events_highpri]
root          56  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/6]
root          57  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/6]
root          58  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/6]
root          59  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/6]
root          60  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/6:0-events]
root          61  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/6:0H-events_highpri]
root          62  0.0  0.0      0     0 ?        S    17:19   0:00 [cpuhp/7]
root          63  0.0  0.0      0     0 ?        S    17:19   0:00 [idle_inject/7]
root          64  0.0  0.0      0     0 ?        S    17:19   0:00 [migration/7]
root          65  0.0  0.0      0     0 ?        S    17:19   0:00 [ksoftirqd/7]
root          66  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/7:0-events]
root          67  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/7:0H-events_highpri]
root          68  0.0  0.0      0     0 ?        S    17:19   0:00 [kdevtmpfs]
root          69  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-inet_frag_wq]
root          70  0.0  0.0      0     0 ?        S    17:19   0:00 [kauditd]
root          71  0.0  0.0      0     0 ?        S    17:19   0:00 [khungtaskd]
root          72  0.5  0.0      0     0 ?        I    17:19   0:02 [kworker/u32:2-events_unbound]
root          73  0.0  0.0      0     0 ?        S    17:19   0:00 [oom_reaper]
root          74  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-writeback]
root          75  0.0  0.0      0     0 ?        S    17:19   0:00 [kcompactd0]
root          76  0.0  0.0      0     0 ?        SN   17:19   0:00 [ksmd]
root          77  0.0  0.0      0     0 ?        SN   17:19   0:00 [khugepaged]
root          78  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kintegrityd]
root          79  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kblockd]
root          80  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-blkcg_punt_bio]
root          81  0.0  0.0      0     0 ?        S    17:19   0:00 [irq/9-acpi]
root          82  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/1:1-events]
root          83  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/4:1-mm_percpu_wq]
root          85  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-tpm_dev_wq]
root          86  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-ata_sff]
root          87  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-edac-poller]
root          88  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-devfreq_wq]
root          89  0.0  0.0      0     0 ?        S    17:19   0:00 [watchdogd]
root          90  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/2:1-events]
root          91  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-quota_events_unbound]
root          92  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/2:1H-kblockd]
root          93  0.0  0.0      0     0 ?        S    17:19   0:00 [kswapd0]
root          94  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kthrotld]
root          95  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-acpi_thermal_pm]
root          96  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_0]
root          97  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_0]
root          98  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_1]
root          99  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_1]
root         100  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_2]
root         101  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_2]
root         102  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_3]
root         103  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_3]
root         104  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_4]
root         105  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_4]
root         106  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_5]
root         107  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_5]
root         111  0.1  0.0      0     0 ?        I    17:19   0:00 [kworker/u32:6-events_unbound]
root         114  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/2:2-events]
root         115  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-mld]
root         116  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-ipv6_addrconf]
root         123  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kstrp]
root         126  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-zswap-shrink]
root         127  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/u33:0-ttm]
root         131  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/0:1H-kblockd]
root         134  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/7:3-events]
root         169  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/1:1H-kblockd]
root         279  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/3:1H-kblockd]
root         281  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/4:1H-kblockd]
root         350  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/6:1H-kblockd]
root         362  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/5:1H-kblockd]
root         366  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/7:1H-kblockd]
root         477  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/6:2-events]
root         523  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_6]
root         524  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_6]
root         525  0.0  0.0      0     0 ?        S    17:19   0:00 [usb-storage]
root         526  0.0  0.0      0     0 ?        S    17:19   0:00 [scsi_eh_7]
root         527  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-scsi_tmf_7]
root         528  0.4  0.0      0     0 ?        S    17:19   0:01 [usb-storage]
root         533  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-uas]
root         564  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/5:2-events]
root         670  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-cryptd]
root         886  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kdmflush/254:0]
root         897  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kdmflush/254:1]
root        1232  0.0  0.0  46624  9828 ?        Ss   17:20   0:00 /usr/lib/systemd/systemd-journald
root        1259  0.0  0.0  15028  5568 ?        Ss   17:20   0:00 /usr/lib/systemd/systemd-userdbd
root        1263  0.0  0.0      0     0 ?        S    17:20   0:00 [psimon]
systemd+    1266  0.0  0.0  89856  7536 ?        Ssl  17:20   0:00 /usr/lib/systemd/systemd-timesyncd
root        1271  0.1  0.0  38648 12236 ?        Ss   17:20   0:00 /usr/lib/systemd/systemd-udevd
root        1272  0.0  0.0      0     0 ?        I    17:20   0:00 [kworker/3:2-events]
root        1274  0.0  0.0      0     0 ?        S    17:20   0:00 [psimon]
systemd+    1341  0.1  0.0  15588  6564 ?        Ss   17:20   0:00 /usr/lib/systemd/systemd-oomd
root        1355  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/R-led_workqueue]
root        1382  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/R-nvkm-disp]
dbus        1383  0.0  0.0   8784  4472 ?        Ss   17:20   0:00 /usr/bin/dbus-broker-launch --scope system --audit
root        1384  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/R-ttm]
root        1385  0.0  0.0      0     0 ?        S    17:20   0:00 [card1-crtc0]
root        1386  0.0  0.0      0     0 ?        S    17:20   0:00 [card1-crtc1]
root        1388  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/R-ttm]
root        1389  0.0  0.0      0     0 ?        S    17:20   0:00 [card0-crtc0]
root        1390  0.0  0.0      0     0 ?        S    17:20   0:00 [card0-crtc1]
dbus        1399  0.0  0.0   5980  4244 ?        S    17:20   0:00 dbus-broker --log 10 --controller 9 --machine-id 81e68655f9744fe28b28403180e80a55 --max-bytes 536870912 --max-fds 4096 --max-matches 131072 --audit
root        1400  0.6  0.1 414412 24100 ?        Ssl  17:20   0:02 /usr/bin/NetworkManager --no-daemon
avahi       1402  0.1  0.0   6776  4384 ?        Ss   17:20   0:00 avahi-daemon: running [garuda-xfce.local]
polkitd     1409  0.2  0.0 308820 10284 ?        Ssl  17:20   0:00 /usr/lib/polkit-1/polkitd --no-debug --log-level=notice
root        1410  0.0  0.0  15872  7668 ?        Ss   17:20   0:00 /usr/lib/systemd/systemd-logind
avahi       1500  0.0  0.0   6776  1524 ?        S    17:20   0:00 avahi-daemon: chroot helper
root        1519  0.1  0.0 391696 12872 ?        Ssl  17:20   0:00 /usr/bin/ModemManager
root        1644  0.0  0.0 305780  7156 ?        Ssl  17:20   0:00 /usr/bin/lightdm
root        1654  0.0  0.0 309188  7596 ?        Ssl  17:20   0:00 /usr/lib/accounts-daemon
root        1686  4.3  0.7 760960 117224 tty7    Ssl+ 17:20   0:15 /usr/lib/Xorg :0 -seat seat0 -auth /run/lightdm/root/:0 -nolisten tcp vt7 -novtswitch
root        1713  0.0  0.0 174780 11464 ?        Sl   17:20   0:00 lightdm --session-child 13 16
root        1747  0.0  0.0      0     0 ?        S    17:20   0:00 [psimon]
garuda      1750  0.2  0.0  21992 12800 ?        Ss   17:20   0:01 /usr/lib/systemd/systemd --user
garuda      1754  0.0  0.0  23184  4192 ?        S    17:20   0:00 (sd-pam)
garuda      1859  0.0  0.0 182212  9116 ?        Ssl  17:20   0:00 /usr/bin/gnome-keyring-daemon --foreground --components=pkcs11,secrets --control-directory=/run/user/1000/keyring
garuda      1867  0.0  0.0   8232  4004 ?        Ss   17:20   0:00 /usr/bin/dbus-broker-launch --scope user
garuda      1868  0.0  0.0   5564  3792 ?        S    17:20   0:00 dbus-broker --log 11 --controller 10 --machine-id d258e126f2d6ccd6d33d99da6999e95b --max-bytes 100000000000000 --max-fds 25000000000000 --max-matches 5000000000
garuda      1869  0.9  0.4 1005824 66504 ?       Ssl  17:20   0:03 xfce4-session
garuda      2085  0.0  0.0 325352 10476 ?        Ssl  17:20   0:00 /usr/lib/gvfsd
garuda      2091  0.0  0.0 396888  6948 ?        Sl   17:20   0:00 /usr/lib/gvfsd-fuse /run/user/1000/gvfs -f
garuda      2098  0.0  0.0 380936  7972 ?        Ssl  17:20   0:00 /usr/lib/at-spi-bus-launcher
garuda      2107  0.0  0.0   8232  3768 ?        S    17:20   0:00 /usr/bin/dbus-broker-launch --config-file=/usr/share/defaults/at-spi2/accessibility.conf --scope user
garuda      2108  0.0  0.0   4516  2676 ?        S    17:20   0:00 dbus-broker --log 10 --controller 9 --machine-id d258e126f2d6ccd6d33d99da6999e95b --max-bytes 100000000000000 --max-fds 6400000 --max-matches 5000000000
garuda      2109  0.0  0.0 306244  6196 ?        Ssl  17:20   0:00 /usr/lib/xfce4/xfconf/xfconfd
garuda      2115  0.0  0.0 168488  7724 ?        Ssl  17:20   0:00 /usr/lib/at-spi2-registryd --use-gnome-session
garuda      2121  0.0  0.0   8840  1728 ?        Ss   17:20   0:00 /usr/bin/ssh-agent -s
garuda      2129  0.0  0.0 155488  3384 ?        SLs  17:20   0:00 /usr/bin/gpg-agent --supervised
garuda      2131  0.9  0.7 1864628 127776 ?      Sl   17:20   0:03 xfwm4
root        2150  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:1-ttm]
garuda      2168  0.0  0.1 389348 30184 ?        Sl   17:20   0:00 xfsettingsd
garuda      2174  0.0  0.0 163932  5688 ?        Ssl  17:20   0:00 /usr/lib/dconf-service
garuda      2176  0.0  0.0  38200 13548 ?        S<sl 17:20   0:00 /usr/bin/pipewire
garuda      2177  0.3  0.1 407424 18852 ?        S<sl 17:20   0:01 /usr/bin/wireplumber
garuda      2178  0.0  0.0  25328  9016 ?        S<sl 17:20   0:00 /usr/bin/pipewire-pulse
garuda      2187  0.9  0.2 1419776 47932 ?       Sl   17:20   0:03 xfce4-panel
garuda      2198  0.6  0.3 1427124 51656 ?       Sl   17:20   0:02 Thunar --daemon
garuda      2205  0.5  0.9 1747048 151592 ?      Sl   17:20   0:02 xfdesktop
garuda      2214  2.0  0.2 1414776 47548 ?       Sl   17:20   0:07 /usr/lib/xfce4/panel/wrapper-2.0 /usr/lib/xfce4/panel/plugins/libwhiskermenu.so 1 16777223 whiskermenu Whisker Menu Show a menu to easily access installed applications
garuda      2236  0.0  0.0 167548  6536 ?        Ssl  17:20   0:00 /usr/lib/gvfsd-metadata
garuda      2240  0.1  0.2 1349836 43840 ?       Sl   17:20   0:00 /usr/lib/xfce4/panel/wrapper-2.0 /usr/lib/xfce4/panel/plugins/libsystray.so 6 16777225 systray Status Tray Plugin Provides status notifier items (application indicators) and legacy systray items
garuda      2245  0.0  0.2 1275812 45492 ?       Sl   17:20   0:00 /usr/lib/xfce4/panel/wrapper-2.0 /usr/lib/xfce4/panel/plugins/libpulseaudio-plugin.so 8 16777226 pulseaudio PulseAudio Plugin Adjust the audio volume of the PulseAudio sound system
garuda      2248  0.1  0.2 1204052 43276 ?       Sl   17:20   0:00 /usr/lib/xfce4/panel/wrapper-2.0 /usr/lib/xfce4/panel/plugins/libxfce4powermanager.so 9 16777227 power-manager-plugin Power Manager Plugin Display the battery levels of your devices and control the brightness of your display
garuda      2249  0.0  0.2 1275520 42568 ?       Sl   17:20   0:00 /usr/lib/xfce4/panel/wrapper-2.0 /usr/lib/xfce4/panel/plugins/libnotification-plugin.so 10 16777228 notification-plugin Notification Plugin Notification plugin for the Xfce panel
garuda      2251  0.0  0.2 1210128 42800 ?       Sl   17:20   0:00 /usr/lib/xfce4/panel/wrapper-2.0 /usr/lib/xfce4/panel/plugins/libactions.so 14 16777229 actions Action Buttons Log out, lock or other system actions
root        2298  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:2-ttm]
root        2299  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:3-ttm]
root        2300  0.0  0.0      0     0 ?        D<   17:20   0:00 [kworker/u33:4+ttm]
root        2301  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:5-ttm]
root        2302  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:6-ttm]
root        2326  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:7-ttm]
root        2327  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:8-ttm]
root        2328  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:9-ttm]
root        2329  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:10-ttm]
garuda      2363  0.1  0.2 1415564 44880 ?       Ssl  17:20   0:00 /usr/lib/xfce4/notifyd/xfce4-notifyd
root        2374  0.1  0.0 317524 10396 ?        Ssl  17:20   0:00 /usr/lib/upowerd
garuda      2437  2.9  0.3 639612 62648 ?        Sl   17:20   0:10 /usr/bin/python /usr/bin/blueman-applet
garuda      2442  0.1  0.3 1583016 55156 ?       Sl   17:20   0:00 nm-applet
garuda      2464  1.9  0.3 994664 56612 ?        Sl   17:20   0:07 /usr/lib/evolution-data-server/evolution-alarm-notify
garuda      2470  1.4  0.3 494556 54228 ?        Sl   17:20   0:05 python3 /usr/bin/redshift-gtk
garuda      2476  0.0  0.1 313728 27468 ?        Sl   17:20   0:00 /usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1
garuda      2481  0.0  0.1 453820 28636 ?        Sl   17:20   0:00 xfce4-power-manager
garuda      2485  0.1  0.2 653568 33348 ?        Sl   17:20   0:00 xfce4-screensaver
garuda      2516  2.7  0.2 673196 44240 ?        SNsl 17:20   0:09 /usr/lib/tumbler-1/tumblerd
root        2649  0.0  0.0      0     0 ?        D<   17:20   0:00 [kworker/u33:11+ttm]
root        2945  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:12-ttm]
garuda      2979  0.4  0.2 765588 38832 ?        Ssl  17:20   0:01 /usr/lib/evolution-source-registry
garuda      2980  0.0  0.0 379368  6740 ?        Sl   17:20   0:00 /usr/bin/redshift -v
garuda      3022  0.7  0.1 499096 28300 ?        Ssl  17:20   0:02 /usr/lib/goa-daemon
root        3057  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:13-ttm]
root        3058  0.0  0.0      0     0 ?        D<   17:20   0:00 [kworker/u33:14+ttm]
root        3059  0.0  0.0      0     0 ?        I<   17:20   0:00 [kworker/u33:15-ttm]
garuda      3084  0.2  0.0 475280 15632 ?        Ssl  17:20   0:00 /usr/lib/xdg-desktop-portal
garuda      3086  0.9  0.1 898304 25940 ?        Ssl  17:20   0:03 /usr/lib/evolution-calendar-factory
garuda      3118  0.0  0.0 305932  6040 ?        Ssl  17:20   0:00 /usr/lib/xdg-permission-store
garuda      3123  0.0  0.0 539340  6712 ?        Ssl  17:20   0:00 /usr/lib/xdg-document-portal
root        3133  0.0  0.0   2716  2016 ?        Ss   17:20   0:00 fusermount3 -o rw,nosuid,nodev,fsname=portal,auto_unmount,subtype=portal -- /run/user/1000/doc
garuda      3136  0.0  0.1 524004 22816 ?        Ssl  17:20   0:00 /usr/lib/xdg-desktop-portal-gtk
rtkit       3150  0.0  0.0  21960  3472 ?        SNsl 17:20   0:00 /usr/lib/rtkit-daemon
garuda      3158  0.0  0.0 392048 10552 ?        Ssl  17:20   0:00 /usr/lib/goa-identity-service
garuda      3165  0.0  0.0 581296 15160 ?        Ssl  17:20   0:00 /usr/lib/gvfs-udisks2-volume-monitor
root        3176  0.9  0.0      0     0 ?        I    17:20   0:03 [kworker/u32:9-kvfree_rcu_reclaim]
root        3181  0.3  0.0 543572 15228 ?        Ssl  17:20   0:01 /usr/lib/udisks2/udisksd
garuda      3227  0.0  0.1 758528 29320 ?        Ssl  17:20   0:00 /usr/lib/evolution-addressbook-factory
garuda      3230  0.0  0.0 306712  6700 ?        Ssl  17:20   0:00 /usr/lib/gvfs-mtp-volume-monitor
garuda      3243  0.0  0.0 308688  7260 ?        Ssl  17:20   0:00 /usr/lib/gvfs-gphoto2-volume-monitor
garuda      3248  0.0  0.0 306196  6152 ?        Ssl  17:20   0:00 /usr/lib/gvfs-goa-volume-monitor
garuda      3253  0.0  0.0 387912  7840 ?        Ssl  17:20   0:00 /usr/lib/gvfs-afc-volume-monitor
garuda      3279  0.0  0.0 546928 11676 ?        Sl   17:20   0:00 /usr/lib/gvfsd-trash --spawner :1.7 /org/gtk/gvfs/exec_spaw/0
garuda      3515  2.1  0.4 1553608 74812 ?       Sl   17:21   0:06 xfce4-terminal
garuda      3598  0.0  0.0  10740  6612 pts/0    Ss   17:21   0:00 bash
garuda      6181  0.0  0.0 473712 11660 ?        Sl   17:22   0:00 /usr/lib/gvfsd-network --spawner :1.7 /org/gtk/gvfs/exec_spaw/1
garuda      8982  0.0  0.0   9504  6084 pts/1    Ss   17:23   0:00 bash
root        9491  0.0  0.0  20376  8024 pts/1    S+   17:23   0:00 sudo -i
root        9495  0.0  0.0  20376  3060 pts/2    Ss   17:23   0:00 sudo -i
root        9496  0.0  0.0   9124  5792 pts/2    S+   17:23   0:00 -bash
root       10433  0.0  0.0      0     0 ?        I    17:24   0:00 [kworker/7:1-events]
root       10866  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-ib-comp-wq]
root       10867  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-ib-comp-unb-wq]
root       10868  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-ib_mcast]
root       10869  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-ib_nl_sa_wq]
root       10872  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-iw_cm_wq]
root       10873  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-rdma_cm]
root       10874  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-cifsiod]
root       10875  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-smb3decryptd]
root       10876  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-cifsfileinfoput]
root       10877  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-cifsoplockd]
root       10878  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-deferredclose]
root       10879  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-serverclose]
root       10880  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-cfid_put_wq]
root       10881  0.0  0.0      0     0 ?        I<   17:25   0:00 [kworker/R-cifs-dfscache]
root       10883  0.0  0.0      0     0 ?        I    17:25   0:00 [kworker/5:1-events]
root       10884  0.0  0.0      0     0 ?        S    17:25   0:00 [cifsd]
root       11558  0.0  0.0      0     0 ?        I    17:25   0:00 [kworker/0:2-events]
garuda     11632  0.0  0.0   7800  3760 pts/0    S+   17:25   0:00 bash linux-desktop-usb.sh
garuda     11636  0.4  0.1  39288 30168 pts/0    S+   17:25   0:00 python3 /tmp/linux-desktop-analysis-XbTVan.py
root       11683  0.0  0.0  15616  6124 ?        S    17:26   0:00 systemd-userwork: waiting...
root       11684  0.0  0.0  15616  6064 ?        S    17:26   0:00 systemd-userwork: waiting...
root       11685  0.0  0.0  15616  6060 ?        S    17:26   0:00 systemd-userwork: waiting...
root       11758  0.0  0.0      0     0 ?        I    17:26   0:00 [kworker/6:1-events]
root       11759  0.0  0.0      0     0 ?        I    17:26   0:00 [kworker/2:0-events]
garuda     11771  0.0  0.0   9928  4248 pts/0    R+   17:26   0:00 ps axu
```

## Journalctl Debug
- Enabled: True
- journalctl available: True
- Captured lines: 8000
- KWin crash risk: none
- KWin crash score: 0

### journalctl section: boot_tail
- Command: journalctl -b --no-pager -n 8000
- Success: True
```text
-- No entries --
```
```text
No journal files were found.
```

### journalctl section: warnings_and_errors
- Command: journalctl -b --no-pager -p warning -n 8000
- Success: True
```text
-- No entries --
```
```text
No journal files were found.
```

### journalctl section: graphics_filter
- Command: journalctl -b --no-pager -n 8000 --grep nvidia|nouveau|kwin|xwayland|drm|gpu|glmark|mangohud
- Success: True
```text
-- No entries --
```
```text
No journal files were found.
```

### journalctl section: kwin_user_unit
- Command: journalctl --user-unit plasma-kwin_wayland -b --no-pager -n 8000
- Success: True
```text
-- No entries --
```
```text
No journal files were found.
```

### journalctl section: kwin_focus_user
- Command: journalctl --user -b --no-pager -n 8000 --grep kwin_wayland_drm|kwin_scene_opengl|GL_INVALID|prepareAtomicPresentation|xwayland|EGL|drm
- Success: True
```text
-- No entries --
```
```text
No journal files were found.
```

### journalctl section: kernel_drm_focus
- Command: journalctl -k -b --no-pager -n 8000 --grep drm|nvidia|nouveau|amdgpu|i915|simpledrm
- Success: True
```text
-- No entries --
```
```text
No journal files were found.
```

### journalctl section: coredumpctl
- Command: coredumpctl list kwin_wayland --no-pager
- Success: False
```text
(no output)
```
```text
No journal files were found.
No coredumps found.
```

## Execution Trace Log
```text
[2026-02-21 17:25:59] main start: argv=['/tmp/linux-desktop-analysis-XbTVan.py']
[2026-02-21 17:25:59] options: non_interactive=False, fractional_scale=1.25, scale_alias=None, mouse_test=False, allow_glxgears_fallback=False, fps_mode=auto, fps_window_size=1920x1080, no_journalctl=False, journalctl_lines=8000, enable_scale_safety_guard=False, fix_nvidia=ask, nvidia_runfile_path_set=False, make_sudo_passwordless=False, output='desktop-analysis-report-20260221-172559.md'
[2026-02-21 17:25:59] run_cmd start: cmd='sudo -n true', timeout=20
[2026-02-21 17:25:59] run_cmd done: rc=0 ok=True stdout='' stderr=''
[2026-02-21 17:25:59] detected distro: id=garuda, base_distro=arch, pretty='Garuda Linux'
[2026-02-21 17:25:59] run_cmd start: cmd='ps -eo comm,args', timeout=20
[2026-02-21 17:25:59] run_cmd done: rc=0 ok=True stdout='COMMAND         COMMAND\nsystemd         /usr/lib/systemd/systemd --switched-root --system --deserialize=43\nkthreadd        [kthreadd]\npool_workqueue_ [pool_workqueue_release]\nkworker/R-kvfre [kworker/R-kvfree_rcu_reclaim]\nkworker/R-rcu_g [kworker/R-rcu_gp]\nkworker/R-sync_ [kworker/R-sync_wq]\nkworker/R-slub_ [kworker/R-slub_flushwq]\nkworker/R-netns [kworker/R-netns]\nkworker/0:0-eve [kworker/0:0-events]\nkworker/0:1-eve [kworker/0:1-events]\nkworker/0:0H-ev [kworker/0:0H-events_highpri]\nkworker/u32:0-l [kworker/u32:0-loop3]\nkworker/u32:1-k [kworker/u32:1-kvfree_rcu_reclaim]\nkworker/R-mm_pe [kworker/R-mm_percpu_wq]\nrcu_tasks_kthre [rcu_tasks_kthread]\nrcu_tasks_rude_ [rcu_tasks_rude_kthread]\nrcu_tasks_trace [rcu_tasks_trace_kthread]\nksoftirqd/0     [ksoftirqd/0]\nrcu_preempt     [rcu_preempt]\nrcub/0          [rcub/0]\nrcu_exp_par_gp_ [rcu_exp_par_gp_kthread_worker/0]\nrcu_exp_gp_kthr [rcu_exp_gp_kthread_worker]\nmigration/0     [migration/0]\nidle_inject/0   [idle_inject/0]\ncpuhp/0         [cpuhp/0]\ncpuhp/1         [cpuhp/1]\nidle_inject/1   [idle_inject/1]\nmigration/1     [migration/1]\nksoftirqd/1     [ksoftirqd/1]\nkworker/1:0-eve [kworker/1:0-events]\nkworker/1:0H-ev [kworker/1:0H-events_high' stderr=''
[2026-02-21 17:25:59] session detection: session_type=x11, desktop=XFCE, compositor=xfwm4
[2026-02-21 17:25:59] run_cmd start: cmd='xrandr --query', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='Screen 0: minimum 320 x 200, current 3840 x 2160, maximum 16384 x 16384\nDP-1 disconnected (normal left inverted right x axis y axis)\nHDMI-2 connected 3840x2160+0+0 (normal left inverted right x axis y axis) 1600mm x 900mm\n   3840x2160     60.00*+  50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   4096x2160     60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   2560x1440    120.00  \n   1920x1080    120.00   100.00   119.88    60.00    60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   1920x1080i    60.00    50.00    59.94  \n   1280x1024     60.02  \n   1152x864      59.97  \n   1280x720      60.00    50.00    59.94  \n   1024x768      60.00  \n   800x600       60.32  \n   720x576       50.00  \n   720x480       60.00    59.94  \n   640x480       60.00    59.94  \n   720x400       70.08  \nVGA-1-1 disconnected (normal left inverted right x axis y axis)\nHDMI-1-1 disconnected (normal left inverted right x axis y axis)' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='xrandr --query', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='Screen 0: minimum 320 x 200, current 3840 x 2160, maximum 16384 x 16384\nDP-1 disconnected (normal left inverted right x axis y axis)\nHDMI-2 connected 3840x2160+0+0 (normal left inverted right x axis y axis) 1600mm x 900mm\n   3840x2160     60.00*+  50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   4096x2160     60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   2560x1440    120.00  \n   1920x1080    120.00   100.00   119.88    60.00    60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   1920x1080i    60.00    50.00    59.94  \n   1280x1024     60.02  \n   1152x864      59.97  \n   1280x720      60.00    50.00    59.94  \n   1024x768      60.00  \n   800x600       60.32  \n   720x576       50.00  \n   720x480       60.00    59.94  \n   640x480       60.00    59.94  \n   720x400       70.08  \nVGA-1-1 disconnected (normal left inverted right x axis y axis)\nHDMI-1-1 disconnected (normal left inverted right x axis y axis)' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='ps -C xfwm4 -o pid=,%cpu=,rss=,etimes=', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='2131  1.0 127776    323' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='xprop -root _NET_SUPPORTING_WM_CHECK', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x800032' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='findmnt -n -o FSTYPE /', timeout=10
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='overlay' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si lshw', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'lshw' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si lshw', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'lshw' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si mesa-utils', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-utils' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si mesa-utils', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-utils' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si vulkan-tools', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'vulkan-tools' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si vulkan-tools', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'vulkan-tools' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si glmark2', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'glmark2' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si glmark2', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'glmark2' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si mangohud', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mangohud' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si mangohud', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mangohud' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si mesa-utils', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-utils' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si mesa-utils', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-utils' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si xorg-xlsclients', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xorg-xlsclients' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Si xorg-xlsclients', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xorg-xlsclients' was not found'
[2026-02-21 17:26:00] run_cmd start: cmd='findmnt -n -o FSTYPE /', timeout=10
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='overlay' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Sy --print-format %n --noconfirm', timeout=45
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='error: you cannot perform this operation unless you are root.'
[2026-02-21 17:26:00] tool check: wanted=['lspci', 'lshw', 'glxinfo', 'vulkaninfo', 'glmark2', 'mangohud', 'glxgears', 'xlsclients', 'libinput', 'xrandr'], missing=['lshw', 'glxinfo', 'vulkaninfo', 'glmark2', 'mangohud', 'glxgears', 'xlsclients'], pkg_manager=pacman, immutable=False, live=True, resolved=0/7
[2026-02-21 17:26:00] run_cmd start: cmd='sudo lspci -nnk', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='00:00.0 Host bridge [0600]: Intel Corporation 4th Gen Core Processor DRAM Controller [8086:0c00] (rev 06)\n	Subsystem: ASUSTeK Computer Inc. Device [1043:8534]\n	Kernel driver in use: hsw_uncore\n00:01.0 PCI bridge [0604]: Intel Corporation Xeon E3-1200 v3/4th Gen Core Processor PCI Express x16 Controller [8086:0c01] (rev 06)\n	Subsystem: ASUSTeK Computer Inc. Device [1043:8534]\n	Kernel driver in use: pcieport\n00:02.0 Display controller [0380]: Intel Corporation Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics Controller [8086:0412] (rev 06)\n	DeviceName:  Onboard IGD\n	Subsystem: ASUSTeK Computer Inc. Device [1043:8534]\n	Kernel driver in use: i915\n	Kernel modules: i915\n00:14.0 USB controller [0c03]: Intel Corporation 8 Series/C220 Series Chipset Family USB xHCI [8086:8c31] (rev 05)\n	Subsystem: ASUSTeK Computer Inc. Device [1043:8534]\n	Kernel driver in use: xhci_hcd\n00:16.0 Communication controller [0780]: Intel Corporation 8 Series/C220 Series Chipset Family MEI Controller #1 [8086:8c3a] (rev 04)\n	Subsystem: ASUSTeK Computer Inc. Device [1043:8534]\n	Kernel modules: mei_me\n00:1a.0 USB controller [0c03]: Intel Corporation 8 Series/C220 Series Chipset Family USB EHCI #2 [8086:8c2' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='sudo lshw -C display', timeout=30
[2026-02-21 17:26:00] run_cmd done: rc=1 ok=False stdout='' stderr='sudo: lshw: command not found'
[2026-02-21 17:26:00] run_cmd start: cmd='lsmod', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='Module                  Size  Used by\ncmac                   12288  1\nnls_utf8               12288  4\ncifs                 2052096  2\ncifs_arc4              12288  1 cifs\nnls_ucs2_utils          8192  1 cifs\nrdma_cm               151552  1 cifs\niw_cm                  65536  1 rdma_cm\nib_cm                 155648  1 rdma_cm\nib_core               540672  4 rdma_cm,cifs,iw_cm,ib_cm\ncifs_md4               12288  1 cifs\ndns_resolver           16384  1 cifs\nnetfs                 598016  1 cifs\nvfat                   24576  1\nfat                   106496  1 vfat\nsnd_seq_dummy          12288  0\nsnd_hrtimer            12288  1\nsnd_seq               135168  7 snd_seq_dummy\nsnd_seq_device         16384  1 snd_seq\nqrtr                   57344  2\nsnd_hda_codec_realtek   221184  1\nintel_rapl_msr         20480  0\nsnd_hda_codec_generic   114688  1 snd_hda_codec_realtek\ni915                 4583424  2\nnouveau              3665920  1\nsnd_hda_scodec_component    20480  1 snd_hda_codec_realtek\nsnd_hda_codec_hdmi     98304  1\nintel_rapl_common      53248  1 intel_rapl_msr\nsnd_hda_intel          69632  2\nsnd_intel_dspcfg       40960  1 snd_hda_intel\nsnd_intel_sdw_acpi     16384  1 snd_intel_dspcfg\nsnd_h' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='sudo modinfo -F version i915', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='sudo modinfo -F filename i915', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='/lib/modules/6.12.65-1-lts/kernel/drivers/gpu/drm/i915/i915.ko.zst' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='sudo modinfo -F version nouveau', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='sudo modinfo -F filename nouveau', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='/lib/modules/6.12.65-1-lts/kernel/drivers/gpu/drm/nouveau/nouveau.ko.zst' stderr=''
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Q xfwm4', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='xfwm4 4.20.0-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Q xfce4-session', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='xfce4-session 4.20.3-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Q lightdm', timeout=20
[2026-02-21 17:26:00] run_cmd done: rc=0 ok=True stdout='lightdm 1:1.32.0-6' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:00] run_cmd start: cmd='pacman -Q xserver-xorg-core', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xserver-xorg-core' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xorg-server', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xorg-server 21.1.21-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xorg-x11-server-Xwayland', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xorg-x11-server-Xwayland' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xwayland', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xwayland' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q wayland', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='wayland 1.24.0-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q wayland-protocols', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'wayland-protocols' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xserver-xorg-core', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xserver-xorg-core' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xorg-x11-server-Xorg', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'xorg-x11-server-Xorg' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xorg-server', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xorg-server 21.1.21-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q mesa-vulkan-drivers', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-vulkan-drivers' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q libgl1-mesa-dri', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'libgl1-mesa-dri' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q libglx-mesa0', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'libglx-mesa0' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q mesa-utils', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-utils' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q mesa', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='mesa 1:25.3.3-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q mesa-dri-drivers', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mesa-dri-drivers' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q libinput10', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'libinput10' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q libinput', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='libinput 1.30.1-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q libinput-tools', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='libinput-tools 1.30.1-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xfce4-session', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xfce4-session 4.20.3-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xfce4-panel', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xfce4-panel 4.20.6-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xfwm4', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xfwm4 4.20.0-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q picom', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'picom' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xfce4-session', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xfce4-session 4.20.3-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q lightdm', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='lightdm 1:1.32.0-6' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q gdm3', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'gdm3' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q sddm', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'sddm' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xfce4-appfinder', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xfce4-appfinder 4.20.0-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q rofi', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'rofi' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q xfce4-settings', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=0 ok=True stdout='xfce4-settings 4.20.3-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:01] run_cmd start: cmd='systemctl is-active gamemoded', timeout=10
[2026-02-21 17:26:01] run_cmd done: rc=4 ok=False stdout='inactive' stderr=''
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q gamemode', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'gamemode' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q gamescope', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'gamescope' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q mangohud', timeout=20
[2026-02-21 17:26:01] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'mangohud' was not found'
[2026-02-21 17:26:01] run_cmd start: cmd='pacman -Q steam', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'steam' was not found'
[2026-02-21 17:26:02] run_cmd start: cmd='pacman -Q wine', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'wine' was not found'
[2026-02-21 17:26:02] run_cmd start: cmd='pacman -Q obs-studio', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=1 ok=False stdout='' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)\nerror: package 'obs-studio' was not found'
[2026-02-21 17:26:02] run_cmd start: cmd='pacman -Q vulkan-icd-loader', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='vulkan-icd-loader 1.4.335.0-1' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:02] run_cmd start: cmd='pacman -Q mesa', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='mesa 1:25.3.3-2' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:02] run_cmd start: cmd='xfconf-query -c xsettings -p /Gdk/WindowScalingFactor', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='1' stderr=''
[2026-02-21 17:26:02] start scale: value=1.0, source=xfconf-query Gdk/WindowScalingFactor
[2026-02-21 17:26:02] desktop matrix skipped: session_type=x11, desktop=xfce, renderer=none
[2026-02-21 17:26:02] run_cmd start: cmd='xfconf-query -c xsettings -p /Gdk/WindowScalingFactor', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='1' stderr=''
[2026-02-21 17:26:02] run_cmd start: cmd='xfconf-query -c xsettings -p /Gdk/WindowScalingFactor', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='1' stderr=''
[2026-02-21 17:26:02] run_cmd start: cmd='xfconf-query -c xsettings -p /Gdk/WindowScalingFactor', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='1' stderr=''
[2026-02-21 17:26:02] run_cmd start: cmd='xrandr --query', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='Screen 0: minimum 320 x 200, current 3840 x 2160, maximum 16384 x 16384\nDP-1 disconnected (normal left inverted right x axis y axis)\nHDMI-2 connected 3840x2160+0+0 (normal left inverted right x axis y axis) 1600mm x 900mm\n   3840x2160     60.00*+  50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   4096x2160     60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   2560x1440    120.00  \n   1920x1080    120.00   100.00   119.88    60.00    60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   1920x1080i    60.00    50.00    59.94  \n   1280x1024     60.02  \n   1152x864      59.97  \n   1280x720      60.00    50.00    59.94  \n   1024x768      60.00  \n   800x600       60.32  \n   720x576       50.00  \n   720x480       60.00    59.94  \n   640x480       60.00    59.94  \n   720x400       70.08  \nVGA-1-1 disconnected (normal left inverted right x axis y axis)\nHDMI-1-1 disconnected (normal left inverted right x axis y axis)' stderr=''
[2026-02-21 17:26:02] run_cmd start: cmd='xrandr --query', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='Screen 0: minimum 320 x 200, current 3840 x 2160, maximum 16384 x 16384\nDP-1 disconnected (normal left inverted right x axis y axis)\nHDMI-2 connected 3840x2160+0+0 (normal left inverted right x axis y axis) 1600mm x 900mm\n   3840x2160     60.00*+  50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   4096x2160     60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   2560x1440    120.00  \n   1920x1080    120.00   100.00   119.88    60.00    60.00    50.00    59.94    30.00    25.00    24.00    29.97    23.98  \n   1920x1080i    60.00    50.00    59.94  \n   1280x1024     60.02  \n   1152x864      59.97  \n   1280x720      60.00    50.00    59.94  \n   1024x768      60.00  \n   800x600       60.32  \n   720x576       50.00  \n   720x480       60.00    59.94  \n   640x480       60.00    59.94  \n   720x400       70.08  \nVGA-1-1 disconnected (normal left inverted right x axis y axis)\nHDMI-1-1 disconnected (normal left inverted right x axis y axis)' stderr=''
[2026-02-21 17:26:02] run_cmd start: cmd='modinfo nvidia', timeout=20
[2026-02-21 17:26:02] run_cmd done: rc=1 ok=False stdout='' stderr='modinfo: ERROR: Module nvidia not found.'
[2026-02-21 17:26:02] run_cmd start: cmd='journalctl -k -b --no-pager -n 4000 --grep NVRM|nvidia|GSP|nouveau', timeout=40
[2026-02-21 17:26:02] run_cmd done: rc=1 ok=False stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:02] run_cmd start: cmd='pacman -Q', timeout=60
[2026-02-21 17:26:02] run_cmd done: rc=0 ok=True stdout='7zip 25.01-1\na52dec 0.8.0-2\naalib 1.4rc5-19\nabiword 3.0.7-2\nabseil-cpp 20250814.1-1\naccountsservice 23.13.9-2\nacl 2.3.2-1\nadwaita-cursors 49.0-1\nadwaita-fonts 49.0-2\nadwaita-icon-theme 49.0-1\nadwaita-icon-theme-legacy 46.2-3\nalsa-card-profiles 1:1.4.9-2\nalsa-firmware 1.2.4-4\nalsa-lib 1.2.15.2-1\nalsa-topology-conf 1.2.5.1-4\nalsa-ucm-conf 1.2.15.2-1\namd-ucode 20260110-1\nandroid-udev 20250525-1\naom 3.13.1-2\nappstream 1.1.1-1\narch-install-scripts 31-1\narchlinux-keyring 20260107-2\narj 3.10.22-14\nat-spi2-core 2.58.3-1\natkmm 2.28.4-1\nattr 2.5.2-1\naudacity 1:3.7.7-1\naudit 4.1.2-2\nautoconf 2.72-1\nautomake 1.18.1-1\nautorandr 1.15-1\navahi 1:0.9rc2-3\nb43-fwcutter 019-6\nbase 3-2\nbase-devel 1-2\nbash 5.3.9-1\nbash-completion 2.17.0-2\nbat 0.26.1-1\nbc 1.08.2-1\nbind 9.20.17-1\nbinutils 2.45.1+r35+g12d0a1dbc1b9-1\nbison 3.8.2-8\nblas 3.12.1-2\nbleachbit 5.0.2-2\nblueman 2.4.6-2\nbluetooth-support 1-7\nbluez 5.85-1\nbluez-hid2hci 5.85-1\nbluez-libs 5.85-1\nbluez-tools 0.2.0-6\nbluez-utils 5.85-1\nbolt 0.9.10-1\nboost-libs 1.89.0-4\nbreeze-icons 6.22.0-1\nbridge-utils 1.7.1-3\nbrightnessctl 0.5.1-3\nbrltty 6.8-6\nbrotli 1.2.0-1\nbtrfs-assistant 2.2-4\nbtrfs-progs 6.17.1-2\nbtrfsmaintenance 0.5.2-3\nbubblewrap 0.11.0-1\nbzip2 ' stderr='warning: database file for 'garuda' does not exist (use '-Sy' to download)\nwarning: database file for 'core' does not exist (use '-Sy' to download)\nwarning: database file for 'extra' does not exist (use '-Sy' to download)\nwarning: database file for 'multilib' does not exist (use '-Sy' to download)\nwarning: database file for 'chaotic-aur' does not exist (use '-Sy' to download)'
[2026-02-21 17:26:38] nvidia diagnostics: relevant=True nvidia_module_active=False nouveau_active=True fix_policy=ask issues=['nouveau-active'] reboot_required=False
[2026-02-21 17:26:38] run_cmd start: cmd='ps -eo comm,rss', timeout=20
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='COMMAND           RSS\nsystemd         14772\nkthreadd            0\npool_workqueue_     0\nkworker/R-kvfre     0\nkworker/R-rcu_g     0\nkworker/R-sync_     0\nkworker/R-slub_     0\nkworker/R-netns     0\nkworker/0:0-eve     0\nkworker/0:1-eve     0\nkworker/0:0H-ev     0\nkworker/u32:0-k     0\nkworker/u32:1-e     0\nkworker/R-mm_pe     0\nrcu_tasks_kthre     0\nrcu_tasks_rude_     0\nrcu_tasks_trace     0\nksoftirqd/0         0\nrcu_preempt         0\nrcub/0              0\nrcu_exp_par_gp_     0\nrcu_exp_gp_kthr     0\nmigration/0         0\nidle_inject/0       0\ncpuhp/0             0\ncpuhp/1             0\nidle_inject/1       0\nmigration/1         0\nksoftirqd/1         0\nkworker/1:0-eve     0\nkworker/1:0H-ev     0\ncpuhp/2             0\nidle_inject/2       0\nmigration/2         0\nksoftirqd/2         0\nkworker/2:0H-ev     0\ncpuhp/3             0\nidle_inject/3       0\nmigration/3         0\nksoftirqd/3         0\nkworker/3:0-eve     0\nkworker/3:0H-ev     0\ncpuhp/4             0\nidle_inject/4       0\nmigration/4         0\nksoftirqd/4         0\nkworker/4:0-eve     0\nkworker/4:0H-ev     0\ncpuhp/5             0\nidle_inject/5       0\nmigration/5         0\nksoftirqd/5         0\nkworker/5:0-eve     0\nkworker/5:0H' stderr=''
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl -b --no-pager -n 8000', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl -b --no-pager -p warning -n 8000', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl -b --no-pager -n 8000 --grep nvidia|nouveau|kwin|xwayland|drm|gpu|glmark|mangohud', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=1 ok=False stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl -b --no-pager -n 8000', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl --user-unit plasma-kwin_wayland -b --no-pager -n 8000', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl --user -b --no-pager -n 8000 --grep kwin_wayland_drm|kwin_scene_opengl|GL_INVALID|prepareAtomicPresentation|xwayland|EGL|drm', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=1 ok=False stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl --user -b --no-pager -n 8000', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl -k -b --no-pager -n 8000 --grep drm|nvidia|nouveau|amdgpu|i915|simpledrm', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=1 ok=False stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='journalctl -k -b --no-pager -n 8000', timeout=45
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='-- No entries --' stderr='No journal files were found.'
[2026-02-21 17:26:38] run_cmd start: cmd='coredumpctl list kwin_wayland --no-pager', timeout=30
[2026-02-21 17:26:38] run_cmd done: rc=1 ok=False stdout='' stderr='No journal files were found.\nNo coredumps found.'
[2026-02-21 17:26:38] run_cmd start: cmd='ps axu', timeout=20
[2026-02-21 17:26:38] run_cmd done: rc=0 ok=True stdout='USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\nroot           1  0.6  0.0  24124 14772 ?        Ss   17:19   0:02 /usr/lib/systemd/systemd --switched-root --system --deserialize=43\nroot           2  0.0  0.0      0     0 ?        S    17:19   0:00 [kthreadd]\nroot           3  0.0  0.0      0     0 ?        S    17:19   0:00 [pool_workqueue_release]\nroot           4  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-kvfree_rcu_reclaim]\nroot           5  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-rcu_gp]\nroot           6  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-sync_wq]\nroot           7  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-slub_flushwq]\nroot           8  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/R-netns]\nroot           9  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/0:0-events]\nroot          10  0.0  0.0      0     0 ?        I    17:19   0:00 [kworker/0:1-events]\nroot          11  0.0  0.0      0     0 ?        I<   17:19   0:00 [kworker/0:0H-events_highpri]\nroot          12  0.4  0.0      0     0 ?        I    17:19   0:01 [kworker/u32:0-kvfree_rcu_reclaim' stderr=''
```

## Console Log
```text
[2026-02-21 17:25:59] 
[*] Linux Desktop Scaling Diagnostics
[2026-02-21 17:25:59]     ===================================
[2026-02-21 17:25:59] Privilege check: root=False, sudo_ok=True
[2026-02-21 17:25:59]   Note: sudo -n succeeded (passwordless).
[2026-02-21 17:25:59] Base distro: arch  (Garuda Linux)
[2026-02-21 17:25:59] Session: x11, Desktop: XFCE, Compositor: xfwm4
[2026-02-21 17:26:00] FPS strategy: X11 compositor/present strategy
[2026-02-21 17:26:00] Package mapping drift detected for missing tools: lshw, glxinfo, vulkaninfo, glmark2, mangohud, glxgears, xlsclients
[2026-02-21 17:26:00] Package install skipped: likely live/installer environment (transient filesystem/repo state).
[2026-02-21 17:26:00] 
[*] Gathering graphics information...
[2026-02-21 17:26:02] Start scale: 1.0x  (via xfconf-query Gdk/WindowScalingFactor)
[2026-02-21 17:26:02] [WARN] No active desktop session detected; skipping scale/FPS matrix.
[2026-02-21 17:26:02] 
[*] Mouse smoothness test (baseline scale)...
[2026-02-21 17:26:02] 
[*] Assessing mouse smoothness...
[2026-02-21 17:26:02] [*] Generating assessment...
[2026-02-21 17:26:38] [WARN] NVIDIA issues detected but remediation was not applied. Use --fix-nvidia <distro-repair|cleanup-only|cuda-repo|runfile|nouveau> or rerun with --fix-nvidia ask.
[2026-02-21 17:26:38] == System Information ==
[2026-02-21 17:26:38] Hostname: garuda-xfce
[2026-02-21 17:26:38] OS: Garuda Linux
[2026-02-21 17:26:38] Kernel: 6.12.65-1-lts
[2026-02-21 17:26:38] Base distro: arch
[2026-02-21 17:26:38] CPU: Intel(R) Core(TM) i7-4790 CPU @ 3.60GHz
[2026-02-21 17:26:38] CPU cores: 8
[2026-02-21 17:26:38] RAM total: 15856 MB (15.5 GB)
[2026-02-21 17:26:38] GPU: 02.0 Display controller [0380]: Intel Corporation Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics Controller [8086:0412]
[2026-02-21 17:26:38] GPU[1] model: 02.0 Display controller [0380]: Intel Corporation Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics Controller [8086:0412]
[2026-02-21 17:26:38] GPU[1] active: i915
[2026-02-21 17:26:38] GPU[1] possible: i915
[2026-02-21 17:26:38] GPU[2] model: 00.0 VGA compatible controller [0300]: NVIDIA Corporation GP108 [GeForce GT 1030] [10de:1d01]
[2026-02-21 17:26:38] GPU[2] active: nouveau
[2026-02-21 17:26:38] GPU[2] possible: nouveau
[2026-02-21 17:26:38] OpenGL renderer: unknown
[2026-02-21 17:26:38] GPU driver (kernel): i915, nouveau
[2026-02-21 17:26:38] Boot mode: uefi
[2026-02-21 17:26:38] Secure Boot: unknown
[2026-02-21 17:26:38] BIOS: American Megatrends Inc. 0601 (03/16/2017)
[2026-02-21 17:26:38] Mainboard: H81M-P-SI
[2026-02-21 17:26:38] System: OEGStone H81M-P-SI
[2026-02-21 17:26:38] == Session ==
[2026-02-21 17:26:38] Display server: x11
[2026-02-21 17:26:38] Desktop env: XFCE
[2026-02-21 17:26:38] Compositor / WM: xfwm4
[2026-02-21 17:26:38] XWayland present: no
[2026-02-21 17:26:38] == Desktop Pipeline Packages ==
[2026-02-21 17:26:38] Package manager: pacman
[2026-02-21 17:26:38] == Package Manager Diagnostics ==
[2026-02-21 17:26:38] Package manager: pacman
[2026-02-21 17:26:38] Installability probe: no
[2026-02-21 17:26:38] Immutable environment: no
[2026-02-21 17:26:38] Likely live environment: yes
[2026-02-21 17:26:38] Package resolution: 0 / 7 missing tools mapped to installable packages
[2026-02-21 17:26:38] Install attempted: no
[2026-02-21 17:26:38] Install result: live-environment
[2026-02-21 17:26:38] == Inspection Coverage ==
[2026-02-21 17:26:38] Coverage score: 88 / 100
[2026-02-21 17:26:38] Coverage level: good
[2026-02-21 17:26:38] == Gaming Optimization Signals ==
[2026-02-21 17:26:38] Kernel: 6.12.65-1-lts
[2026-02-21 17:26:38] Kernel flavor tags: none
[2026-02-21 17:26:38] zram enabled: yes
[2026-02-21 17:26:38] CPU governor: schedutil
[2026-02-21 17:26:38] Platform profile: unknown
[2026-02-21 17:26:38] gamemoded active: no
[2026-02-21 17:26:38] gamemode service: unknown
[2026-02-21 17:26:38] gamescope active: no
[2026-02-21 17:26:38] steam active: no
[2026-02-21 17:26:38] binary gamemoderun: no
[2026-02-21 17:26:38] binary gamescope: no
[2026-02-21 17:26:38] binary mangohud: no
[2026-02-21 17:26:38] binary steam: no
[2026-02-21 17:26:38] binary wine: no
[2026-02-21 17:26:38] binary proton: no
[2026-02-21 17:26:38] == Operational Hints ==
[2026-02-21 17:26:38] == Scaling ==
[2026-02-21 17:26:38] Reference factor: 1.0x
[2026-02-21 17:26:38] Start factor: 1.0x
[2026-02-21 17:26:38] Start detected via: xfconf-query Gdk/WindowScalingFactor
[2026-02-21 17:26:38] Baseline factor: 1.0x
[2026-02-21 17:26:38] Detected via: xfconf-query Gdk/WindowScalingFactor
[2026-02-21 17:26:38] Fractional case tested: yes
[2026-02-21 17:26:38] == Test Matrix ==
[2026-02-21 17:26:38] base_1.0 scale: 1.0x
[2026-02-21 17:26:38] base_1.0 status: skipped (no-active-desktop-session)
[2026-02-21 17:26:38] base_1.0 detected: 1.0x
[2026-02-21 17:26:38] base_1.0 fps: n/a
[2026-02-21 17:26:38] base_1.0 tool: skipped
[2026-02-21 17:26:38] base_1.0 RAM used: 1230 MB
[2026-02-21 17:26:38] integer_2.0 scale: 2.0x
[2026-02-21 17:26:38] integer_2.0 status: skipped (no-active-desktop-session)
[2026-02-21 17:26:38] integer_2.0 detected: 1.0x
[2026-02-21 17:26:38] integer_2.0 fps: n/a
[2026-02-21 17:26:38] integer_2.0 tool: skipped
[2026-02-21 17:26:38] integer_2.0 RAM used: 1230 MB
[2026-02-21 17:26:38] fractional scale: 1.25x
[2026-02-21 17:26:38] fractional status: skipped (no-active-desktop-session)
[2026-02-21 17:26:38] fractional detected: 1.0x
[2026-02-21 17:26:38] fractional fps: n/a
[2026-02-21 17:26:38] fractional tool: skipped
[2026-02-21 17:26:38] fractional RAM used: 1230 MB
[2026-02-21 17:26:38] == FPS Benchmark ==
[2026-02-21 17:26:38] Tool: skipped
[2026-02-21 17:26:38] Baseline FPS: n/a
[2026-02-21 17:26:38] Target FPS: n/a
[2026-02-21 17:26:38] == RAM Usage ==
[2026-02-21 17:26:38] Used at baseline: 1230 MB
[2026-02-21 17:26:38] Available at baseline: 14626 MB
[2026-02-21 17:26:38] Used at target scale: 1230 MB  (delta: +0 MB)
[2026-02-21 17:26:38] == Mouse Smoothness ==
[2026-02-21 17:26:38] Assessment: likely smooth
[2026-02-21 17:26:38] == Driver Suitability ==
[2026-02-21 17:26:38] Assessment: may be unsuitable
[2026-02-21 17:26:38] NVIDIA module active: no
[2026-02-21 17:26:38] nouveau active: yes
[2026-02-21 17:26:38] NVIDIA auto remediation offered: yes
[2026-02-21 17:26:38] NVIDIA auto remediation attempted: no
[2026-02-21 17:26:38] NVIDIA auto remediation result: user-skipped
[2026-02-21 17:26:38] NVIDIA issues detected: nouveau-active
[2026-02-21 17:26:38] NVIDIA action recommended: yes
[2026-02-21 17:26:38] NVIDIA reboot required: no
[2026-02-21 17:26:38] == Pipeline Analysis ==
[2026-02-21 17:26:38] Pipeline: x11 + integer-scaling
[2026-02-21 17:26:38] GPU path: nvidia-nouveau
[2026-02-21 17:26:38] Expected efficiency: moderate
[2026-02-21 17:26:38] == Desktop Present FPS Strategy ==
[2026-02-21 17:26:38] Selected strategy: X11 compositor/present strategy
[2026-02-21 17:26:38] Primary source: X compositor telemetry when available
[2026-02-21 17:26:38] Fallback source: Output refresh + benchmark + frame-time proxy
[2026-02-21 17:26:38] Tool xrandr: available
[2026-02-21 17:26:38] Active refresh (xrandr): 60.0
[2026-02-21 17:26:38] == Compositor Diagnostics ==
[2026-02-21 17:26:38] Compositor: xfwm4
[2026-02-21 17:26:38] Strategy id: x11-generic
[2026-02-21 17:26:38] Probe compositor-ps: ok
[2026-02-21 17:26:38] Probe x11-wm-check: ok
[2026-02-21 17:26:38] == Journalctl Debug Capture ==
[2026-02-21 17:26:38] Enabled: yes
[2026-02-21 17:26:38] journalctl available: yes
[2026-02-21 17:26:38] Captured lines: 8000
[2026-02-21 17:26:38] KWin crash risk: none
[2026-02-21 17:26:38] KWin crash score: 0
[2026-02-21 17:26:38] Section boot_tail: ok
[2026-02-21 17:26:38] Section warnings_and_errors: ok
[2026-02-21 17:26:38] Section graphics_filter: ok
[2026-02-21 17:26:38] Section kwin_user_unit: ok
[2026-02-21 17:26:38] Section kwin_focus_user: ok
[2026-02-21 17:26:38] Section kernel_drm_focus: ok
[2026-02-21 17:26:38] Section coredumpctl: failed
[2026-02-21 17:26:38] == Efficiency & Performance Assessment ==
[2026-02-21 17:26:38] == Findings & Reasoning ==
```