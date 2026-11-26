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
        for key, value in HARDENING.items():
            self.assertEqual(
                self.one(service, "Service", key, SERVICE),
                value,
                f"{SERVICE}: [Service] {key}",
            )
        forbidden = {
            "ProtectProc": "the probe needs host-wide /proc",
            "ProcSubset": "the probe needs host-wide /proc",
            "PrivatePIDs": "HOB-90 requires one PID namespace",
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
        actual_units = {path.name for path in SYSTEMD.glob("hostobs-*.socket")}
        self.assertEqual(
            actual_units,
            expected_units,
            f"{SYSTEMD}: socket unit set",
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

    def test_tmpfiles_creates_the_socket_directories(self):
        expected = [("/run/hostobs", "0755", "root", "root", "-")]
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
        self.assertEqual(
            len(parsed),
            len(expected),
            f"{TMPFILES}: exactly one d line per parent and configured level",
        )
        self.assertEqual(
            len(parsed),
            len(set(parsed)),
            f"{TMPFILES}: duplicate directory line",
        )

    def test_config_has_exactly_the_probe_process_keys(self):
        self.assertEqual(
            set(self.config), {"probe", "limits"}, f"{CONFIG}: root table keys"
        )
        self.assertEqual(
            set(self.config["probe"]),
            {"levels", "tick_ms", "cgroup_root"},
            f"{CONFIG}: [probe] keys",
        )
        self.assertEqual(set(self.config["limits"]), {"max_rows"}, f"{CONFIG}: [limits] keys")
        self.assertEqual(
            self.config["probe"]["levels"],
            list(LEVELS),
            f"{CONFIG}: [probe] levels",
        )
        socket_levels = {
            path.name.removeprefix("hostobs-").removesuffix(".socket")
            for path in SYSTEMD.glob("hostobs-*.socket")
        }
        self.assertEqual(
            set(self.config["probe"]["levels"]),
            socket_levels,
            f"{CONFIG}: [probe] levels must match the socket unit set",
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


def service_socket_units(service: dict[str, dict[str, list[str]]]) -> list[str]:
    values = service.get("Service", {}).get("Sockets", [])
    return [word for value in values for word in value.split()]


if __name__ == "__main__":
    unittest.main()
