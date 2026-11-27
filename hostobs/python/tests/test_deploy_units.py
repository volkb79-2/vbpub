#!/usr/bin/env python3
"""The installed systemd units and strict probe configuration stay one contract."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
DEPLOY = HOSTOBS / "deploy"
SYSTEMD = DEPLOY / "systemd"
CONFIG = DEPLOY / "hostobsd.toml"
SERVICE = SYSTEMD / "hostobs-probe.service"
TMPFILES = DEPLOY / "tmpfiles.d" / "hostobs.conf"
LEVELS = ("observe", "operator", "observer")

HARDENING = {
    "NoNewPrivileges": "yes",
    "ProtectControlGroups": "yes",
    "PrivateNetwork": "yes",
    "RestrictAddressFamilies": "AF_UNIX",
    "SystemCallFilter": "@system-service",
    "AmbientCapabilities": "CAP_DAC_READ_SEARCH",
    "CapabilityBoundingSet": "CAP_DAC_READ_SEARCH",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "LockPersonality": "yes",
    "MemoryDenyWriteExecute": "yes",
    "RestrictNamespaces": "yes",
    "SystemCallArchitectures": "native",
}


def unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """An INI-style key list; repeated keys stay repeated."""
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            raise AssertionError(f"{path}:{number}: not an INI-style key")
        key, value = line.split("=", 1)
        sections[current].setdefault(key.strip(), []).append(value.strip())
    return sections


def tmpfiles_directories(path: Path) -> list[tuple[str, str, str, str, str]]:
    directories = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 6 or fields[0] != "d":
            raise AssertionError(f"{path}:{number}: expected a six-field d line")
        _, directory, mode, owner, group, age = fields
        directories.append((directory, mode, owner, group, age))
    return directories


class DeployUnits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = unit(SERVICE)
        with CONFIG.open("rb") as handle:
            cls.config = tomllib.load(handle)

    def one(self, sections, section: str, key: str, path: Path) -> str:
        values = sections.get(section, {}).get(key, [])
        self.assertEqual(len(values), 1, f"{path}: [{section}] {key} must appear once")
        return values[0]

    def test_service_activation_restart_and_hardening(self):
        service = self.service
        self.assertEqual(
            self.one(service, "Service", "ExecStart", SERVICE),
            "/usr/local/bin/hostobs-probe --config /etc/hostobs/hostobsd.toml",
            f"{SERVICE}: [Service] ExecStart",
        )
        for key in ("User", "Group"):
            self.assertEqual(
                self.one(service, "Service", key, SERVICE),
                "hostobs",
                f"{SERVICE}: [Service] {key}",
            )
        self.assertEqual(
            self.one(service, "Service", "Restart", SERVICE),
            "on-failure",
            f"{SERVICE}: [Service] Restart",
        )
        self.assertEqual(
            self.one(service, "Service", "RestartPreventExitStatus", SERVICE),
            "2",
            f"{SERVICE}: [Service] RestartPreventExitStatus",
        )
        for key, value in {
            "RuntimeDirectory": "hostobs/tiers",
            "RuntimeDirectoryMode": "0700",
            "RuntimeDirectoryPreserve": "restart",
            "StateDirectory": "hostobs/probe",
            "StateDirectoryMode": "0700",
        }.items():
            self.assertEqual(
                self.one(service, "Service", key, SERVICE),
                value,
                f"{SERVICE}: [Service] {key}",
            )
        self.assertEqual(
            self.one(service, "Install", "WantedBy", SERVICE),
            "multi-user.target",
            f"{SERVICE}: [Install] WantedBy",
        )
        for key, value in HARDENING.items():
            self.assertEqual(
                self.one(service, "Service", key, SERVICE),
                value,
                f"{SERVICE}: [Service] {key}",
            )
        forbidden = {
            "ProtectProc": "the probe needs host-wide /proc",
            "ProcSubset": "the probe needs host-wide /proc",
            "PrivatePIDs": "peer process labels need visible peer PIDs in /proc",
            "PrivateUsers": "CAP_DAC_READ_SEARCH must retain host ownership checks",
        }
        for key, reason in forbidden.items():
            self.assertNotIn(
                key,
                service.get("Service", {}),
                f"{SERVICE}: [Service] must not set {key}: {reason}",
            )

    def test_service_lists_all_three_socket_units(self):
        declared = service_socket_units(self.service)
        self.assertEqual(
            sorted(declared),
            sorted(f"hostobs-{level}.socket" for level in LEVELS),
            f"{SERVICE}: [Service] Sockets=",
        )

    def test_every_deployed_level_has_one_exact_socket(self):
        expected_units = {f"hostobs-{level}.socket" for level in LEVELS}
        # Only the units that activate the probe are this contract: another
        # service may ship its own socket in the same directory. A socket unit
        # without Service= activates the service of the same stem.
        probe_units = {
            path.name
            for path in SYSTEMD.glob("*.socket")
            if SERVICE.name
            in (unit(path).get("Socket", {}).get("Service") or [f"{path.stem}.service"])
        }
        self.assertEqual(
            probe_units,
            expected_units,
            f"{SYSTEMD}: socket units of {SERVICE.name}",
        )
        for level in LEVELS:
            path = SYSTEMD / f"hostobs-{level}.socket"
            self.assertTrue(
                path.is_file(), f"{path}: missing socket unit for configured level {level}"
            )
            sock = unit(path)
            expected = [
                ("Unit", "After", "systemd-tmpfiles-setup.service"),
                ("Socket", "FileDescriptorName", level),
                ("Socket", "Accept", "no"),
                ("Socket", "ListenStream", f"/run/hostobs/{level}/hostobs.sock"),
                ("Socket", "SocketGroup", f"hostobs-{level}"),
                ("Socket", "SocketUser", "root"),
                ("Socket", "SocketMode", "0660"),
                ("Socket", "DirectoryMode", "0750"),
                ("Socket", "Service", "hostobs-probe.service"),
                ("Install", "WantedBy", "sockets.target"),
            ]
            for section, key, value in expected:
                self.assertEqual(
                    self.one(sock, section, key, path),
                    value,
                    f"{path}: [{section}] {key}",
                )

    def test_tmpfiles_creates_runtime_and_socket_directories(self):
        expected = [("/run/hostobs", "0755", "root", "root", "-")]
        expected.append(("/run/hostobs/tiers", "0700", "hostobs", "hostobs", "-"))
        expected.extend(
            (f"/run/hostobs/{level}", "0750", "root", f"hostobs-{level}", "-")
            for level in LEVELS
        )
        parsed = tmpfiles_directories(TMPFILES)
        for entry in expected:
            self.assertIn(
                entry,
                parsed,
                f"{TMPFILES}: {entry[0]} must be {entry[2]}:{entry[3]} {entry[1]}",
            )
        directories = [entry[0] for entry in parsed]
        self.assertEqual(
            len(directories),
            len(set(directories)),
            f"{TMPFILES}: duplicate directory line",
        )
        self.assertNotIn(
            "/var/lib/hostobs/probe",
            [directory[0] for directory in directories],
            f"{TMPFILES}: StateDirectory owns durable probe storage",
        )

    def test_config_has_exactly_the_probe_process_keys(self):
        self.assertEqual(
            set(self.config), {"probe", "limits", "store"}, f"{CONFIG}: root table keys"
        )
        self.assertEqual(
            set(self.config["probe"]),
            {"levels", "tick_ms", "cgroup_root"},
            f"{CONFIG}: [probe] keys",
        )
        self.assertEqual(
            set(self.config["limits"]),
            {"max_rows", "annotate_rate_per_s", "annotate_burst"},
            f"{CONFIG}: [limits] keys",
        )
        self.assertEqual(
            self.config["probe"]["levels"],
            list(LEVELS),
            f"{CONFIG}: [probe] levels",
        )
        self.assertEqual(
            self.config["probe"]["tick_ms"], 1000, f"{CONFIG}: [probe] tick_ms"
        )
        self.assertEqual(
            self.config["probe"]["cgroup_root"],
            "/sys/fs/cgroup",
            f"{CONFIG}: [probe] cgroup_root",
        )
        self.assertEqual(
            self.config["limits"]["max_rows"], 1024, f"{CONFIG}: [limits] max_rows"
        )
        self.assertEqual(
            self.config["limits"]["annotate_rate_per_s"],
            50,
            f"{CONFIG}: [limits] annotate_rate_per_s",
        )
        self.assertEqual(
            self.config["limits"]["annotate_burst"],
            64,
            f"{CONFIG}: [limits] annotate_burst",
        )
        self.assertEqual(
            set(self.config["store"]),
            {"dir", "tmpfs_dir", "events_cap_bytes", "events_ring_bytes"},
            f"{CONFIG}: [store] keys",
        )
        self.assertEqual(
            self.config["store"],
            {
                "dir": "/var/lib/hostobs/probe",
                "tmpfs_dir": "/run/hostobs/tiers",
                "events_cap_bytes": 67_108_864,
                "events_ring_bytes": 4_194_304,
            },
            f"{CONFIG}: [store] values",
        )


def service_socket_units(service: dict[str, dict[str, list[str]]]) -> list[str]:
    values = service.get("Service", {}).get("Sockets", [])
    return [word for value in values for word in value.split()]


if __name__ == "__main__":
    unittest.main()
