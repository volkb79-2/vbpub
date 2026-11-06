"""MDT-077: host-setup publishes the run-gate gates admission cap from host capacity.

Hermetic: pure derivation over env dicts and fake host totals; every host effect (docker, run-gate) goes through a
fake ``run`` callable. Nothing here starts a container, reads the real /proc or touches /etc/mdt."""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mdt_admission as adm  # noqa: E402
import mdt_capacity as cap  # noqa: E402

GIB = 1024 ** 3
MIB = 1024 ** 2

# Both test hosts are HOST_ROLE=dedicated-dev and 4 vCPU / 8 GB; the operator published 2 on v1001 and later 4 on r1002.
DEDICATED = {"HOST_ROLE": "dedicated-dev", "DEV_HOST_MEMORY_KEEP": "1G"}


def derive(values: dict[str, str], *, nproc: int = 4, mem_gib: int = 8, swap_gib: int = 0) -> adm.Derivation:
    return adm.derive(values, nproc=nproc, mem_total=mem_gib * GIB, swap_total=swap_gib * GIB)


class DerivationTests(unittest.TestCase):
    def test_v1001_shape_publishes_two(self) -> None:
        d = derive(DEDICATED, nproc=4, mem_gib=8)
        self.assertEqual((d.max_concurrent, d.source, d.unreadable_policy), (2, "derived", "refuse"))
        self.assertIn("cpu 4 cores", d.basis)
        self.assertIn("= 2", d.basis)

    def test_double_the_host_doubles_the_cap(self) -> None:
        self.assertEqual(derive(DEDICATED, nproc=8, mem_gib=16).max_concurrent, 4)

    def test_dev_cpu_quota_pin_bounds_the_gates_tier_budget(self) -> None:
        default = derive(DEDICATED, nproc=8, mem_gib=16)
        lower_pin = derive({**DEDICATED, "DEV_CPU_QUOTA": "200%"}, nproc=8, mem_gib=16)
        higher_pin = derive({**DEDICATED, "DEV_CPU_QUOTA": "1200%"}, nproc=8, mem_gib=16)
        self.assertEqual(default.max_concurrent, 4)
        self.assertEqual(lower_pin.max_concurrent, 1)
        self.assertIn("DEV_CPU_QUOTA=200%", lower_pin.basis)
        self.assertEqual(higher_pin.max_concurrent, 4)
        self.assertIn("DEV_CPU_QUOTA=1200%", higher_pin.basis)

    def test_r1002_hand_raised_four_is_reachable_by_keys_not_by_default(self) -> None:
        # r1002 is also 4 vCPU / 8 GB; the operator raised it to 4 for a dstdns campaign (v1001 stayed at 2)
        self.assertEqual(derive(DEDICATED, nproc=4, mem_gib=8).max_concurrent, 2)
        self.assertEqual(derive({**DEDICATED, "ADMISSION_MAX_CONCURRENT": "4"}, nproc=4, mem_gib=8).max_concurrent, 4)
        keep_formula = {**DEDICATED, "ADMISSION_CPUS_PER_GATE": "1", "ADMISSION_MEMORY_PER_GATE": "1G"}
        self.assertEqual(derive(keep_formula, nproc=4, mem_gib=8).max_concurrent, 4)

    def test_excluded_cores_and_tier_exclusion_shrink_the_cpu_term(self) -> None:
        values = {**DEDICATED, "MDT_EXCLUDED_CPU_CORES": "2", "DEV_TIER_EXCLUDED_CPU_CORES": "0"}
        self.assertEqual(derive(values, nproc=8, mem_gib=16).max_concurrent, 3)  # (8-2)/2
        values["DEV_TIER_EXCLUDED_CPU_CORES"] = "2"
        self.assertEqual(derive(values, nproc=8, mem_gib=16).max_concurrent, 2)  # (8-2-2)/2

    def test_gates_cpu_quota_pin_wins_and_dash_means_the_dev_share(self) -> None:
        self.assertEqual(derive({**DEDICATED, "DEV_GATES_CPU_QUOTA": "200%"}, nproc=8, mem_gib=16).max_concurrent, 1)
        self.assertEqual(derive({**DEDICATED, "DEV_GATES_CPU_QUOTA": "600%"}, nproc=8, mem_gib=16).max_concurrent, 3)
        # a pin above MDT's own cores is bounded by them
        self.assertEqual(derive({**DEDICATED, "DEV_GATES_CPU_QUOTA": "6400%"}, nproc=8, mem_gib=16).max_concurrent, 4)
        values = {**DEDICATED, "DEV_GATES_CPU_QUOTA": "-", "DEV_TIER_EXCLUDED_CPU_CORES": "4"}
        self.assertEqual(derive(values, nproc=8, mem_gib=16).max_concurrent, 4)

    def test_memory_term_binds_when_it_is_smaller(self) -> None:
        # 16 GB, keep 1G, tiers protect 6G: gates MemoryHigh = 15G - 6G = 9G -> 4 per 2G; cpu says 8
        values = {**DEDICATED, "DEV_INTERACTIVE_MEMORY_LOW": "4G", "DEV_BUILDKITD_MEMORY_LOW": "2G"}
        d = derive(values, nproc=16, mem_gib=16)
        self.assertEqual(d.max_concurrent, 4)
        self.assertIn("memory 9G", d.basis)
        self.assertEqual(derive({**values, "ADMISSION_MEMORY_PER_GATE": "1G"}, nproc=16, mem_gib=16).max_concurrent, 8)

    def test_shared_prod_uses_the_configured_gates_memory_high(self) -> None:
        shared = {"DEV_GATES_MEMORY_HIGH": "6G"}
        self.assertEqual(derive(shared, nproc=16, mem_gib=32).max_concurrent, 3)
        self.assertIn("DEV_GATES_MEMORY_HIGH=6G", derive(shared, nproc=16, mem_gib=32).basis)
        # the example's 800M is under one gate's budget: the minimum of 1, stated, never 0
        small = derive({"DEV_GATES_MEMORY_HIGH": "800M"}, nproc=16, mem_gib=32)
        self.assertEqual(small.max_concurrent, 1)
        self.assertIn("raised to the minimum of 1", small.basis)

    def test_no_gates_memory_high_leaves_only_the_cpu_term(self) -> None:
        for raw in ("", "-"):
            d = derive({"DEV_GATES_MEMORY_HIGH": raw}, nproc=6, mem_gib=8)
            self.assertEqual(d.max_concurrent, 3)
            self.assertIn("memory: no term", d.basis)

    def test_a_tiny_host_still_gets_one(self) -> None:
        d = derive(DEDICATED, nproc=1, mem_gib=2)
        self.assertEqual(d.max_concurrent, 1)
        self.assertIn("raised to the minimum of 1", d.basis)

    def test_cpus_per_gate_override_including_a_fraction(self) -> None:
        self.assertEqual(derive({**DEDICATED, "ADMISSION_CPUS_PER_GATE": "1"}, nproc=4, mem_gib=16).max_concurrent, 4)
        self.assertEqual(derive({**DEDICATED, "ADMISSION_CPUS_PER_GATE": "1.5"}, nproc=6, mem_gib=16).max_concurrent, 4)
        self.assertEqual(derive({**DEDICATED, "ADMISSION_CPUS_PER_GATE": "4"}, nproc=8, mem_gib=16).max_concurrent, 2)

    def test_pin_wins_over_everything_and_says_so(self) -> None:
        d = derive({**DEDICATED, "ADMISSION_MAX_CONCURRENT": "7"}, nproc=2, mem_gib=2)
        self.assertEqual((d.max_concurrent, d.source), (7, "pin"))
        self.assertIn("pinned", d.basis)
        # `auto` and empty are the same as absent
        for raw in ("auto", ""):
            self.assertEqual(derive({**DEDICATED, "ADMISSION_MAX_CONCURRENT": raw}).source, "derived")

    def test_unreadable_policy_follows_the_key(self) -> None:
        self.assertEqual(derive({**DEDICATED, "ADMISSION_UNREADABLE_POLICY": "unbudgeted"}).unreadable_policy, "unbudgeted")
        self.assertEqual(derive({**DEDICATED, "ADMISSION_UNREADABLE_POLICY": ""}).unreadable_policy, "refuse")

    def test_capacity_errors_surface_as_config_errors(self) -> None:
        with self.assertRaises(adm.AdmissionConfigError):
            derive({**DEDICATED, "MDT_EXCLUDED_CPU_CORES": "4"}, nproc=4)  # leaves no cores
        with self.assertRaises(adm.AdmissionConfigError):
            derive({**DEDICATED, "DEV_HOST_MEMORY_KEEP": "8G"}, mem_gib=8)  # leaves no RAM
        with self.assertRaises(adm.AdmissionConfigError):
            derive({"DEV_GATES_MEMORY_HIGH": "lots"})
        with self.assertRaises(adm.AdmissionConfigError):
            derive({"DEV_GATES_CPU_QUOTA": "fast"})

    def test_the_formula_is_the_documented_one(self) -> None:
        # exhaustive small grid: the published number is max(1, min(floor(cores/2), floor(high/2G)))
        for nproc in (1, 2, 3, 4, 7, 8, 16):
            for high_gib in (1, 2, 3, 7, 20):
                values = {"DEV_GATES_MEMORY_HIGH": f"{high_gib}G"}
                want = max(1, min(nproc // 2, high_gib // 2))
                self.assertEqual(derive(values, nproc=nproc, mem_gib=64).max_concurrent, want, (nproc, high_gib))


class ValidatorTests(unittest.TestCase):
    def test_every_key_has_a_validator_and_defaults_are_valid(self) -> None:
        self.assertEqual(set(adm.VALIDATORS), {
            "ADMISSION_PUBLISH", "ADMISSION_MAX_CONCURRENT", "ADMISSION_UNREADABLE_POLICY", "ADMISSION_CPUS_PER_GATE",
            "ADMISSION_MEMORY_PER_GATE", "ADMISSION_TICKET_IMAGE", "ADMISSION_RUN_GATE", "ADMISSION_COCKPIT",
            "ADMISSION_COCKPIT_USER",
        })
        for key, validate in adm.VALIDATORS.items():
            self.assertIsNone(validate(""), key)  # empty = the default, everywhere

    def test_accepted_and_refused_values(self) -> None:
        good = {"ADMISSION_PUBLISH": ["auto", "missing", "replace", "off"], "ADMISSION_MAX_CONCURRENT": ["auto", "1", "12"],
                "ADMISSION_UNREADABLE_POLICY": ["refuse", "unbudgeted"], "ADMISSION_CPUS_PER_GATE": ["2", "0.5", "1.5"],
                "ADMISSION_MEMORY_PER_GATE": ["256M", "2G", "1.5G"],
                "ADMISSION_TICKET_IMAGE": ["tester-unified:local", "ghcr.io/x/y:1.2@sha256:ab"],
                "ADMISSION_RUN_GATE": ["run-gate", "/home/vscode/.venvs/gate-tools2502/bin/run-gate"],
                "ADMISSION_COCKPIT": ["mdt-cockpit", "a.b_c-1"], "ADMISSION_COCKPIT_USER": ["vscode", "1003", "_svc"]}
        bad = {"ADMISSION_PUBLISH": ["yes", "AUTO"], "ADMISSION_MAX_CONCURRENT": ["0", "-1", "two", "1.5"],
               "ADMISSION_UNREADABLE_POLICY": ["allow", "Refuse"], "ADMISSION_CPUS_PER_GATE": ["0", "0.4", "x", "2.55"],
               "ADMISSION_MEMORY_PER_GATE": ["0", "100M", "lots"], "ADMISSION_TICKET_IMAGE": ["a b", "x;y", "-x"],
               "ADMISSION_RUN_GATE": ["-run-gate", "run gate", "$(x)", "a;b"], "ADMISSION_COCKPIT": ["-x", "a b", "a/b"],
               "ADMISSION_COCKPIT_USER": ["Root", "a b"]}
        for key, values in good.items():
            for value in values:
                self.assertIsNone(adm.VALIDATORS[key](value), (key, value))
        for key, values in bad.items():
            for value in values:
                self.assertIsNotNone(adm.VALIDATORS[key](value), (key, value))

    def test_parse_settings_refuses_a_bad_key_by_name(self) -> None:
        with self.assertRaisesRegex(adm.AdmissionConfigError, "ADMISSION_PUBLISH"):
            adm.parse_settings({"ADMISSION_PUBLISH": "sometimes"})

    def test_parse_settings_defaults(self) -> None:
        s = adm.parse_settings({})
        self.assertEqual((s.publish, s.max_concurrent, s.unreadable_policy, s.cpus_per_gate, s.memory_per_gate),
                         ("auto", None, "refuse", Decimal(2), 2 * GIB))
        self.assertEqual((s.ticket_image, s.run_gate, s.cockpit, s.cockpit_user), ("", "", "", "vscode"))


def show_text(pub: adm.Published | None, enabled: bool = True) -> str:
    head = f"run-gate admission: enabled={str(enabled).lower()} (config: /tmp/x/run-gate.toml)\n"
    if pub is None:
        return head + "run-gate admission: no published object\n"
    return head + (f"run-gate admission: ciu-admission-{pub.generation or 1} generation={pub.generation or 1} "
                   f"max_concurrent={pub.max_concurrent} unreadable_policy={pub.unreadable_policy} state=created\n")


class FakeHost:
    """The Docker daemon and run-gate, as far as mdt_admission can see them. Records every call."""

    def __init__(self, *, cockpit_running: bool = True, rg_in_cockpit: bool = True, image_present: bool = True,
                 cockpit_image: str = "tester-unified:local", published: adm.Published | None = None,
                 show_rc: int = 0, show_override: str | None = None, set_rc: int = 0, set_text: str = "",
                 apply_set: bool = True, image_error: str = "", unreadable_existing: bool = False,
                 show_sequence: list[str] | None = None) -> None:
        self.cockpit_running, self.rg_in_cockpit, self.image_present = cockpit_running, rg_in_cockpit, image_present
        self.cockpit_image, self.published = cockpit_image, published
        self.show_rc, self.show_override, self.set_rc, self.set_text, self.apply_set = show_rc, show_override, set_rc, set_text, apply_set
        self.image_error, self.unreadable_existing = image_error, unreadable_existing
        self.show_sequence = list(show_sequence or [])
        self.calls: list[dict] = []
        self.configs: list[str] = []

    def verbs(self) -> list[list[str]]:
        return [c["verb"] for c in self.calls if c.get("verb")]

    def __call__(self, argv, *, input=None, env=None, cwd=None, timeout=90.0):  # noqa: A002
        argv = list(argv)
        call = {"argv": argv, "input": input, "env": env, "cwd": cwd}
        self.calls.append(call)
        if argv[1:3] == ["inspect", "-f"] and argv[3] == "{{.State.Running}}":
            return adm.Proc(0 if self.cockpit_running else 1, "true\n" if self.cockpit_running else "", "")
        if argv[1:3] == ["inspect", "-f"] and argv[3] == "{{.Config.Image}}":
            return adm.Proc(0, self.cockpit_image + "\n")
        if argv[1:3] == ["image", "inspect"]:
            call["image"] = argv[3]
            return adm.Proc(0 if self.image_present else 1, "", self.image_error)
        if argv[1] == "exec" and "command -v" in " ".join(argv):
            return adm.Proc(0 if self.rg_in_cockpit else 1)
        if "admission" in argv:
            verb = argv[argv.index("admission") + 1:]
            call["verb"] = verb
            if input is not None:
                self.configs.append(input)
            elif cwd is not None:
                self.configs.append((Path(cwd) / "run-gate.toml").read_text())
            if verb[0] == "show":
                if self.show_override is not None:
                    return adm.Proc(self.show_rc, self.show_override)
                if self.show_sequence:
                    return adm.Proc(self.show_rc, self.show_sequence.pop(0))
                if self.unreadable_existing and self.published is None:
                    return adm.Proc(self.show_rc, "run-gate admission: no published object\n")
                return adm.Proc(self.show_rc, show_text(self.published))
            if verb[0] == "set":
                if self.set_rc == 0 and self.apply_set:
                    flags = dict(zip(verb[1::2], verb[2::2]))
                    gen = (self.published.generation or 0) + 1 if self.published else 1
                    if self.unreadable_existing and "--replace" not in verb:
                        return adm.Proc(2, "", "refusing to replace unreadable admission object")
                    if self.published and "--replace" not in verb:
                        return adm.Proc(2, "", "a published admission object already exists; pass --replace")
                    self.published = adm.Published(int(flags["--max-concurrent"]), flags["--unreadable-policy"], gen)
                    self.unreadable_existing = False
                return adm.Proc(self.set_rc, "", self.set_text)
        raise AssertionError(f"unexpected call {argv}")


TOOLS = {"docker": "/usr/bin/docker", "run-gate": "/usr/local/bin/run-gate",
         "/opt/rg/bin/run-gate": "/opt/rg/bin/run-gate"}


def which_with(**missing_or_found: object):
    table = dict(TOOLS)
    for name, present in missing_or_found.items():
        key = name.replace("__", "/").replace("_", "-")
        if not present:
            table.pop(key, None)
    return lambda name: table.get(name)


HOST_SETTINGS = adm.parse_settings({"ADMISSION_TICKET_IMAGE": "tester-unified:local"})
COCKPIT_SETTINGS = adm.parse_settings({"ADMISSION_COCKPIT": "mdt-cockpit"})
WANT = adm.Derivation(2, "derived", "min(cpu 4 cores / 2 = 2) = 2", "refuse")


def do_publish(settings: adm.Settings, host: FakeHost, derivation: adm.Derivation = WANT, which=None,
               *, operator_reconcile: bool = False) -> tuple[str, list[str]]:
    lines: list[str] = []
    result = adm.publish(settings, derivation, run=host, which=which or which_with(), log=lines.append,
                         operator_reconcile=operator_reconcile)
    return result, lines


class PublishTests(unittest.TestCase):
    def test_fresh_host_gets_the_cap_published_and_read_back(self) -> None:
        host = FakeHost()
        result, lines = do_publish(HOST_SETTINGS, host)
        self.assertEqual(result, "published")
        self.assertEqual(host.published, adm.Published(2, "refuse", 1))
        self.assertEqual(host.verbs(), [["show"], ["set", "--max-concurrent", "2", "--unreadable-policy", "refuse"], ["show"]])
        self.assertTrue(any("published gates cap max_concurrent=2" in line for line in lines))

    def test_host_mode_runs_the_verb_in_a_private_dir_with_the_gates_slice(self) -> None:
        host = FakeHost()
        do_publish(HOST_SETTINGS, host)
        verb_calls = [c for c in host.calls if c.get("verb")]
        self.assertTrue(all(c["argv"][0] == "/usr/local/bin/run-gate" for c in verb_calls))
        self.assertTrue(all(c["env"]["CGROUP_PARENT_DEV_GATES"] == "dev-gates.slice" for c in verb_calls))
        self.assertEqual(len({c["cwd"] for c in verb_calls}), 1)
        self.assertNotEqual(verb_calls[0]["cwd"], str(Path.cwd()))
        self.assertFalse(Path(verb_calls[0]["cwd"]).exists(), "the temporary config directory is removed")
        self.assertIn('ticket_image = "tester-unified:local"', host.configs[0])
        self.assertIn("enabled = true", host.configs[0])
        self.assertIn('unreadable_policy = "refuse"', host.configs[0])
        self.assertIn("schema_version = 1", host.configs[0])

    def test_cockpit_mode_runs_in_the_container_as_its_user_with_the_config_on_stdin(self) -> None:
        host = FakeHost(cockpit_image="tester-unified:local")
        result, _ = do_publish(adm.parse_settings({"ADMISSION_COCKPIT": "mdt-cockpit", "ADMISSION_COCKPIT_USER": "runner",
                                                   "ADMISSION_RUN_GATE": "/opt/rg/bin/run-gate"}), host)
        self.assertEqual(result, "published")
        argv = next(c["argv"] for c in host.calls if c.get("verb") == ["show"])
        self.assertEqual(argv[:9], ["/usr/bin/docker", "exec", "-i", "-u", "runner", "-e", "CGROUP_PARENT_DEV_GATES=dev-gates.slice",
                                    "mdt-cockpit", "sh"])
        self.assertEqual(argv[-3:], ["/opt/rg/bin/run-gate", "admission", "show"])
        self.assertIn('ticket_image = "tester-unified:local"', host.configs[0])  # the cockpit's own image
        self.assertTrue(all(c["cwd"] is None for c in host.calls))

    def test_explicit_ticket_image_beats_the_cockpits_image(self) -> None:
        host = FakeHost(cockpit_image="other:image")
        do_publish(adm.parse_settings({"ADMISSION_COCKPIT": "mdt-cockpit", "ADMISSION_TICKET_IMAGE": "mine:1"}), host)
        self.assertIn('ticket_image = "mine:1"', host.configs[0])
        self.assertEqual([c["image"] for c in host.calls if "image" in c], ["mine:1"])

    def test_the_policy_reaches_both_the_verb_and_the_config(self) -> None:
        host = FakeHost()
        do_publish(HOST_SETTINGS, host, adm.Derivation(3, "pin", "x", "unbudgeted"))
        self.assertIn(["set", "--max-concurrent", "3", "--unreadable-policy", "unbudgeted"], host.verbs())

    def test_idempotent_when_value_and_policy_already_match(self) -> None:
        host = FakeHost(published=adm.Published(2, "refuse", 4))
        result, lines = do_publish(HOST_SETTINGS, host)
        self.assertEqual(result, "unchanged")
        self.assertEqual(host.verbs(), [["show"]], "no set at all: no new generation")
        self.assertTrue(any("nothing to do" in line and "generation 4" in line for line in lines))

    def test_running_twice_publishes_once(self) -> None:
        host = FakeHost()
        self.assertEqual(do_publish(HOST_SETTINGS, host)[0], "published")
        self.assertEqual(do_publish(HOST_SETTINGS, host)[0], "unchanged")
        self.assertEqual(sum(1 for v in host.verbs() if v[0] == "set"), 1)

    def test_full_install_with_default_auto_keeps_a_different_readable_cap(self) -> None:
        host = FakeHost(published=adm.Published(4, "refuse", 1))
        result, lines = do_publish(HOST_SETTINGS, host)
        self.assertEqual(result, "kept")
        self.assertEqual(host.verbs(), [["show"]])
        self.assertEqual(host.published.max_concurrent, 4)
        self.assertTrue(any("ADMISSION_PUBLISH=auto" in line and "keeps it" in line for line in lines))

    def test_admission_only_reconciles_a_different_cap_under_default_auto(self) -> None:
        host = FakeHost(published=adm.Published(4, "refuse", 1))
        result, lines = do_publish(HOST_SETTINGS, host, operator_reconcile=True)
        self.assertEqual(result, "replaced")
        self.assertIn(["set", "--max-concurrent", "2", "--unreadable-policy", "refuse", "--replace"], host.verbs())
        self.assertEqual(host.published.max_concurrent, 2)
        self.assertTrue(any("4/refuse -> 2/refuse" in line for line in lines))

    def test_replace_mode_reconciles_a_different_cap_on_full_install(self) -> None:
        settings = adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_PUBLISH": "replace"})
        host = FakeHost(published=adm.Published(4, "refuse", 1))
        self.assertEqual(do_publish(settings, host)[0], "replaced")
        self.assertIn(["set", "--max-concurrent", "2", "--unreadable-policy", "refuse", "--replace"], host.verbs())

    def test_a_different_policy_alone_is_replaced(self) -> None:
        host = FakeHost(published=adm.Published(2, "unbudgeted", 1))
        self.assertEqual(do_publish(HOST_SETTINGS, host, operator_reconcile=True)[0], "replaced")
        self.assertEqual(host.published.unreadable_policy, "refuse")

    def test_missing_mode_publishes_when_absent_and_never_replaces(self) -> None:
        settings = adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_PUBLISH": "missing"})
        host = FakeHost()
        self.assertEqual(do_publish(settings, host)[0], "published")
        host = FakeHost(published=adm.Published(4, "refuse", 1))
        result, lines = do_publish(settings, host)
        self.assertEqual(result, "kept")
        self.assertEqual(host.verbs(), [["show"]])
        self.assertTrue(any("keeps it" in line for line in lines))

    def test_missing_mode_never_replaces_even_for_explicit_reconciliation(self) -> None:
        settings = adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_PUBLISH": "missing"})
        host = FakeHost(published=adm.Published(4, "refuse", 1))
        self.assertEqual(do_publish(settings, host, operator_reconcile=True)[0], "kept")
        self.assertEqual(host.verbs(), [["show"]])

    def test_full_auto_does_not_replace_an_unreadable_object(self) -> None:
        host = FakeHost(unreadable_existing=True, show_sequence=[
            "run-gate admission: ciu-admission-3 generation=3 max_concurrent=None unreadable_policy=refuse state=created\n",
        ])
        with self.assertRaisesRegex(adm.AdmissionFailure, "refusing to replace unreadable"):
            do_publish(HOST_SETTINGS, host)
        self.assertEqual(host.verbs(), [["show"], ["set", "--max-concurrent", "2", "--unreadable-policy", "refuse"]])
        self.assertTrue(host.unreadable_existing)

    def test_admission_only_replaces_an_unreadable_published_object(self) -> None:
        host = FakeHost(unreadable_existing=True, show_sequence=[
            "run-gate admission: ciu-admission-3 generation=3 max_concurrent=None unreadable_policy=refuse state=created\n",
        ])
        self.assertEqual(do_publish(HOST_SETTINGS, host, operator_reconcile=True)[0], "replaced")
        self.assertIn(["set", "--max-concurrent", "2", "--unreadable-policy", "refuse", "--replace"], host.verbs())
        self.assertEqual(host.published.max_concurrent, 2)
        self.assertFalse(host.unreadable_existing)

    def test_off_touches_nothing(self) -> None:
        host = FakeHost()
        result, lines = do_publish(adm.parse_settings({"ADMISSION_PUBLISH": "off", "ADMISSION_TICKET_IMAGE": "t:1"}), host)
        self.assertEqual((result, host.calls), ("skipped", []))
        self.assertIn("ADMISSION_PUBLISH=off", lines[0])

    def test_skips_are_loud_notes_naming_the_missing_prerequisite(self) -> None:
        cases = {
            "docker CLI": (HOST_SETTINGS, FakeHost(), which_with(docker=False)),
            "not installed on the host": (HOST_SETTINGS, FakeHost(), which_with(run_gate=False)),
            "is not running": (COCKPIT_SETTINGS, FakeHost(cockpit_running=False), None),
            "not installed in the cockpit": (COCKPIT_SETTINGS, FakeHost(rg_in_cockpit=False), None),
            "is not present locally": (HOST_SETTINGS, FakeHost(image_present=False), None),
            "no ticket image": (adm.parse_settings({}), FakeHost(), None),
        }
        for needle, (settings, host, which) in cases.items():
            result, lines = do_publish(settings, host, which=which)
            self.assertEqual(result, "skipped", needle)
            self.assertIn(needle, lines[0])
            self.assertIn("NOTE", lines[0])
            self.assertIn("--admission-only", lines[0])
            self.assertFalse(host.verbs(), f"{needle}: run-gate must not be invoked")

    def test_docker_daemon_failure_is_not_reported_as_a_missing_ticket_image(self) -> None:
        host = FakeHost(image_present=False, image_error="Cannot connect to the Docker daemon at unix:///run/docker.sock")
        result, lines = do_publish(HOST_SETTINGS, host)
        self.assertEqual(result, "skipped")
        self.assertIn("Docker daemon", lines[0])
        self.assertIn("not reachable", lines[0])
        self.assertNotIn("not present locally", lines[0])

    def test_an_explicit_run_gate_path_is_used_on_the_host(self) -> None:
        host = FakeHost()
        do_publish(adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_RUN_GATE": "/opt/rg/bin/run-gate"}), host)
        self.assertEqual({c["argv"][0] for c in host.calls if c.get("verb")}, {"/opt/rg/bin/run-gate"})
        self.assertEqual(do_publish(adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_RUN_GATE": "/nope/run-gate"}),
                                    FakeHost())[0], "skipped")

    def test_a_failing_set_raises_with_run_gates_reason(self) -> None:
        host = FakeHost(set_rc=2, set_text="refusing to replace unreadable admission object 'ciu-admission-3'")
        with self.assertRaisesRegex(adm.AdmissionFailure, "unreadable admission object"):
            do_publish(HOST_SETTINGS, host)

    def test_a_failing_show_raises(self) -> None:
        with self.assertRaisesRegex(adm.AdmissionFailure, r"show` failed \(rc 2\)"):
            do_publish(HOST_SETTINGS, FakeHost(show_rc=2, show_override="boom"))

    def test_an_unrecognised_show_output_is_an_error_never_absent(self) -> None:
        host = FakeHost(show_override="run-gate admission: something new\n")
        with self.assertRaisesRegex(adm.AdmissionFailure, "format changed"):
            do_publish(HOST_SETTINGS, host)
        self.assertFalse([v for v in host.verbs() if v[0] == "set"], "never publish blind over an unreadable state")

    def test_a_read_back_that_differs_raises(self) -> None:
        host = FakeHost(apply_set=False)
        with self.assertRaisesRegex(adm.AdmissionFailure, "read-back"):
            do_publish(HOST_SETTINGS, host)


class ParseShowTests(unittest.TestCase):
    def test_parses_the_real_line_shape(self) -> None:
        text = ("run-gate admission: enabled=true (config: /x/run-gate.toml)\n"
                "run-gate admission: ciu-admission-7 generation=7 max_concurrent=3 unreadable_policy=refuse state=created\n")
        self.assertEqual(adm.parse_show(text), adm.Published(3, "refuse", 7))

    def test_none_for_no_object(self) -> None:
        self.assertIsNone(adm.parse_show(show_text(None)))

    def test_an_unparseable_published_limit_is_distinguished_from_no_object(self) -> None:
        text = "run-gate admission: ciu-admission-7 generation=7 max_concurrent=None unreadable_policy=refuse state=created\n"
        with self.assertRaisesRegex(adm.UnreadablePublished, "unparseable max_concurrent"):
            adm.parse_show(text)

    def test_the_cap_comes_from_the_object_line_not_from_enabled(self) -> None:
        self.assertEqual(adm.parse_show(show_text(adm.Published(1, "unbudgeted"), enabled=False)).max_concurrent, 1)


class CheckTests(unittest.TestCase):
    def lines(self, settings: adm.Settings, host: FakeHost, derivation: adm.Derivation = WANT, which=None) -> list[str]:
        return adm.check(settings, derivation, run=host, which=which or which_with())

    def test_matching_cap_is_ok_and_writes_nothing(self) -> None:
        host = FakeHost(published=adm.Published(2, "refuse", 1))
        out = self.lines(HOST_SETTINGS, host)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("OK "), out)
        self.assertEqual(host.verbs(), [["show"]])

    def test_a_missing_object_is_a_fail(self) -> None:
        out = self.lines(HOST_SETTINGS, FakeHost())
        self.assertTrue(out[0].startswith("FAIL no gates cap is published"), out)
        self.assertIn("--admission-only", out[0])

    def test_a_manual_value_is_a_warn_and_a_pinned_mismatch_is_a_fail(self) -> None:
        host = FakeHost(published=adm.Published(4, "refuse", 1))
        self.assertTrue(self.lines(HOST_SETTINGS, host)[0].startswith("WARN "))
        pinned = adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_MAX_CONCURRENT": "2"})
        self.assertTrue(self.lines(pinned, host, adm.Derivation(2, "pin", "pin", "refuse"))[0].startswith("FAIL "))

    def test_pinned_mismatch_advice_matches_the_publish_mode(self) -> None:
        published = FakeHost(published=adm.Published(4, "refuse", 1))
        derivation = adm.Derivation(2, "pin", "pin", "refuse")
        auto = self.lines(adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_PUBLISH": "auto",
                                               "ADMISSION_MAX_CONCURRENT": "2"}),
                          published, derivation)[0]
        self.assertIn("--admission-only", auto)
        missing = self.lines(adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_PUBLISH": "missing",
                                                  "ADMISSION_MAX_CONCURRENT": "2"}),
                             published, derivation)[0]
        self.assertIn("ADMISSION_PUBLISH=replace", missing)
        self.assertIn("sudo ./host-setup/install.sh`", missing)
        self.assertNotIn("--admission-only", missing)

    def test_a_policy_mismatch_is_reported(self) -> None:
        host = FakeHost(published=adm.Published(2, "unbudgeted", 1))
        out = self.lines(HOST_SETTINGS, host)
        self.assertTrue(out[0].startswith("WARN "))
        self.assertIn("unreadable_policy=unbudgeted", out[0])

    def test_missing_mode_warns_when_a_published_cap_differs(self) -> None:
        settings = adm.parse_settings({"ADMISSION_TICKET_IMAGE": "t:1", "ADMISSION_PUBLISH": "missing"})
        out = self.lines(settings, FakeHost(published=adm.Published(4, "refuse", 1)))
        self.assertTrue(out[0].startswith("WARN "), out)

    def test_off_is_info(self) -> None:
        out = self.lines(adm.parse_settings({"ADMISSION_PUBLISH": "off"}), FakeHost())
        self.assertTrue(out[0].startswith("INFO "))

    def test_unreadable_here_is_a_warn_naming_the_cause(self) -> None:
        out = self.lines(HOST_SETTINGS, FakeHost(), which=which_with(run_gate=False))
        self.assertTrue(out[0].startswith("WARN "))
        self.assertIn("not installed on the host", out[0])

    def test_a_failing_show_is_a_fail(self) -> None:
        out = self.lines(HOST_SETTINGS, FakeHost(show_rc=2, show_override="boom"))
        self.assertTrue(out[0].startswith("FAIL "))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.meminfo = self.tmp / "meminfo"
        self.meminfo.write_text("MemTotal:        8388608 kB\nSwapTotal:             0 kB\n")

    def env(self, text: str) -> Path:
        path = self.tmp / "host-setup.env"
        path.write_text(text)
        return path

    def run_main(self, *argv: str, host: FakeHost | None = None, which=None) -> tuple[int, str, str]:
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = adm.main(list(argv), run=host or FakeHost(), which=which or which_with())
        return rc, out.getvalue(), err.getvalue()

    def test_derive_prints_key_value_lines_for_the_v1001_shape(self) -> None:
        env = self.env('HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=1G\nADMISSION_TICKET_IMAGE="t:1"\n')
        rc, out, _ = self.run_main("derive", str(env), "--meminfo", str(self.meminfo), "--nproc", "4")
        self.assertEqual(rc, 0)
        lines = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual(lines["ADMISSION_MAX_CONCURRENT"], "2")
        self.assertEqual(lines["ADMISSION_UNREADABLE_POLICY"], "refuse")
        self.assertEqual(lines["ADMISSION_SOURCE"], "derived")
        self.assertEqual(lines["ADMISSION_PUBLISH"], "auto")

    def test_publish_end_to_end_with_fakes(self) -> None:
        env = self.env('HOST_ROLE=dedicated-dev\nADMISSION_TICKET_IMAGE="t:1"\n')
        host = FakeHost()
        rc, out, err = self.run_main("publish", str(env), "--meminfo", str(self.meminfo), "--nproc", "4", host=host)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("published gates cap max_concurrent=2", out)
        self.assertEqual(host.published.max_concurrent, 2)

    def test_publish_failure_exits_2_with_the_reason(self) -> None:
        env = self.env('HOST_ROLE=dedicated-dev\nADMISSION_TICKET_IMAGE="t:1"\n')
        rc, _, err = self.run_main("publish", str(env), "--meminfo", str(self.meminfo), "--nproc", "4",
                                   host=FakeHost(set_rc=2, set_text="nope"))
        self.assertEqual(rc, 2)
        self.assertIn("ERROR: admission:", err)
        self.assertIn("nope", err)

    def test_a_skipped_publish_exits_0(self) -> None:
        env = self.env("HOST_ROLE=dedicated-dev\n")
        rc, out, _ = self.run_main("publish", str(env), "--meminfo", str(self.meminfo), "--nproc", "4", which=which_with(run_gate=False))
        self.assertEqual(rc, 0)
        self.assertIn("NOTE not published", out)

    def test_a_bad_key_is_exit_2_for_publish_and_a_fail_line_for_check(self) -> None:
        env = self.env("ADMISSION_PUBLISH=sometimes\n")
        rc, _, err = self.run_main("publish", str(env), "--meminfo", str(self.meminfo), "--nproc", "4")
        self.assertEqual(rc, 2)
        self.assertIn("ADMISSION_PUBLISH", err)
        rc, out, _ = self.run_main("check", str(env), "--meminfo", str(self.meminfo), "--nproc", "4")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("FAIL "), out)

    def test_check_prints_parseable_lines(self) -> None:
        env = self.env("HOST_ROLE=dedicated-dev\n")
        rc, out, _ = self.run_main("check", str(env), "--meminfo", str(self.meminfo), "--nproc", "4",
                                   host=FakeHost(published=adm.Published(2, "refuse", 3)),
                                   which=which_with())
        self.assertEqual(rc, 0)
        # no ADMISSION_TICKET_IMAGE and no cockpit: the check cannot read, and says why
        self.assertRegex(out, r"^(OK|WARN|FAIL|INFO) ")

    def test_default_run_maps_missing_program_and_timeout_to_rc_not_exceptions(self) -> None:
        self.assertEqual(adm.default_run(["/nonexistent/mdt-admission-test"]).rc, 127)
        self.assertEqual(adm.default_run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2).rc, 124)
        done = adm.default_run([sys.executable, "-c", "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"])
        self.assertEqual((done.rc, done.out.strip(), done.err.strip()), (3, "a", "b"))


class WiringTests(unittest.TestCase):
    """The installer, the checker, the example, the wizard and the README all name the same keys."""

    def setUp(self) -> None:
        self.install = (ROOT / "install.sh").read_text()
        self.check = (ROOT / "scripts" / "check.sh").read_text()
        self.example = (ROOT / "host-setup.env.example").read_text()
        self.readme = (ROOT / "README.md").read_text()
        self.project_readme = (ROOT.parent / "README.md").read_text()
        self.design = (ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text()
        self.consumers = (ROOT.parent / "docs" / "CONSUMERS.md").read_text()

    def test_example_assigns_every_key_with_its_default(self) -> None:
        for key in adm.VALIDATORS:
            self.assertRegex(self.example, rf"(?m)^{key}=", key)
        # the shipped values validate and the shipped default policy publishes (auto) as derived
        values = cap._parse_env(ROOT / "host-setup.env.example")
        for key, validate in adm.VALIDATORS.items():
            self.assertIsNone(validate(values[key]), (key, values[key]))
        self.assertEqual(values["ADMISSION_PUBLISH"], "auto")
        self.assertEqual(values["ADMISSION_MAX_CONCURRENT"], "auto")
        self.assertEqual(values["ADMISSION_UNREADABLE_POLICY"], "refuse")

    def test_keys_are_runtime_optional_so_an_older_installed_env_still_validates(self) -> None:
        pattern = next(line for line in (ROOT / "runtime-optional-keys.re").read_text().splitlines()
                       if line.strip() and not line.startswith("#"))
        for key in adm.VALIDATORS:
            self.assertRegex(key, pattern)

    def test_wizard_strict_validator_knows_every_key(self) -> None:
        spec = importlib.util.spec_from_file_location("mdt_wizard_admission_t", ROOT / "mdt-host-setup-wizard.py")
        wizard = importlib.util.module_from_spec(spec)
        sys.modules["mdt_wizard_admission_t"] = wizard
        spec.loader.exec_module(wizard)
        validators = wizard._config_value_validators()
        for key in adm.VALIDATORS:
            self.assertIn(key, validators, key)
        self.assertIsNotNone(validators["ADMISSION_PUBLISH"]("sometimes"))
        self.assertIsNone(validators["ADMISSION_PUBLISH"]("auto"))

    def test_installer_ships_runs_and_documents_the_step(self) -> None:
        self.assertIn('install -m 0644 "$HERE/mdt_admission.py"', self.install)
        self.assertIn('python3 "$HERE/mdt_admission.py" "${args[@]}"', self.install)
        self.assertIn("--admission-only", self.install)
        self.assertIn("ADMISSION_FAILED", self.install)
        self.assertIn("--admission-only", self.readme)
        self.assertIn("ADMISSION_MAX_CONCURRENT", self.readme)

    def test_the_admission_step_runs_after_docker_and_never_before_the_slices_exist(self) -> None:
        full = self.install.split('BASELINE_HANDLED=0', 1)[0]
        self.assertGreater(full.rfind("publish_admission full "), full.rfind("systemctl start dev.slice"))

    def test_checker_runs_the_one_implementation_and_counts_its_lines(self) -> None:
        self.assertIn("mdt_admission.py", self.check)
        self.assertRegex(self.check, r'(?s)_ADMISSION_PY.*check')
        for level in ("OK", "WARN", "FAIL", "INFO"):
            self.assertRegex(self.check, rf"{level}\\ \*\)")

    def test_readme_documents_the_formula_with_the_shipped_defaults(self) -> None:
        self.assertIn("max(1, min(", self.readme)
        self.assertIn(adm.DEFAULT_CPUS_PER_GATE, self.readme)
        self.assertIn(adm.DEFAULT_MEMORY_PER_GATE, self.readme)

    def test_shell_syntax_of_the_touched_scripts(self) -> None:
        for script in (ROOT / "install.sh", ROOT / "scripts" / "check.sh"):
            done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_checker_section_maps_every_level_and_survives_a_crashing_helper(self) -> None:
        text = self.check
        start = text.index('echo "== run-gate gates admission cap (MDT-077) =="')
        section = text[start:text.index('echo "== per-host CPU benchmark (HB-3) =="')]
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        conf = tmp / "host-setup.env"
        conf.write_text("HOST_ROLE=dedicated-dev\n")

        def run_section(helper_body: str | None, conf_path: Path = conf) -> str:
            env = {"CONF": str(conf_path), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
            if helper_body is not None:
                helper = tmp / "fake_admission.py"
                helper.write_text(helper_body)
                env["ADMISSION_PY"] = str(helper)
            else:
                env["ADMISSION_PY"] = str(tmp / "absent.py")
            prelude = ('ok(){ echo "ok:$*"; }; warn(){ echo "warn:$*"; }; fail(){ echo "fail:$*"; }; '
                       'info(){ echo "info:$*"; }; ')
            done = subprocess.run(["bash", "-c", prelude + section.replace("$(dirname \"$0\")", "/nonexistent")],
                                  capture_output=True, text=True, env=env, check=False)
            return done.stdout

        lines = ('import sys\nprint("OK all good")\nprint("WARN differs")\nprint("FAIL missing")\n'
                 'print("INFO off")\nprint("garbled")\n')
        out = run_section(lines)
        for expected in ("ok:all good", "warn:differs", "fail:missing", "info:off", "warn:gates admission report: garbled"):
            self.assertIn(expected, out)
        out = run_section("raise SystemExit(3)\n")
        self.assertIn("fail:the gates admission check failed to run", out)
        self.assertIn("warn:", run_section(None))

    def test_installer_help_lists_the_mode(self) -> None:
        done = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0)
        self.assertIn("--admission-only", done.stdout)
        self.assertTrue(re.search(r"ADMISSION_PUBLISH", done.stdout))

    def test_install_admission_function_reconciles_with_fake_run_gate_on_path(self) -> None:
        function = re.search(r"(?ms)^publish_admission\(\) \{\n.*?^\}", self.install)
        self.assertIsNotNone(function, "install.sh exposes the admission step as a testable function")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        calls = tmp / "calls.log"
        state = tmp / "published.txt"
        state.write_text("run-gate admission: ciu-admission-1 generation=1 max_concurrent=4 unreadable_policy=refuse state=created\n")
        config = tmp / "host-setup.env"
        config.write_text('HOST_ROLE=dedicated-dev\nADMISSION_TICKET_IMAGE="ticket:local"\n')
        meminfo = tmp / "meminfo"
        meminfo.write_text("MemTotal:        8388608 kB\nSwapTotal:             0 kB\n")
        docker = fake_bin / "docker"
        docker.write_text("#!/bin/sh\nprintf 'docker %s\\n' \"$*\" >> \"$CALLS\"\n[ \"$1 $2\" = 'image inspect' ]\n")
        docker.chmod(0o755)
        run_gate = fake_bin / "run-gate"
        run_gate.write_text(
            "#!/bin/sh\n"
            "printf 'run-gate %s\\n' \"$*\" >> \"$CALLS\"\n"
            "[ \"$1 $2\" = 'admission show' ] || [ \"$1 $2\" = 'admission set' ] || exit 64\n"
            "if [ \"$2\" = show ]; then cat \"$STATE\"; exit 0; fi\n"
            "replace=0; n=; policy=\n"
            "for arg in \"$@\"; do [ \"$arg\" = --replace ] && replace=1; done\n"
            "[ ! -f \"$STATE\" ] || [ \"$replace\" -eq 1 ] || { echo 'published object exists; pass --replace' >&2; exit 2; }\n"
            "while [ \"$#\" -gt 0 ]; do case \"$1\" in --max-concurrent) n=$2; shift 2;; --unreadable-policy) policy=$2; shift 2;; *) shift;; esac; done\n"
            "printf 'run-gate admission: ciu-admission-2 generation=2 max_concurrent=%s unreadable_policy=%s state=created\\n' \"$n\" \"$policy\" > \"$STATE\"\n"
        )
        run_gate.chmod(0o755)
        shell = function.group(0) + '\npublish_admission operator "$CONFIG" --meminfo "$MEMINFO" --nproc 4\n'
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ.get('PATH', '')}", HERE=str(ROOT),
                   CONFIG=str(config), MEMINFO=str(meminfo), CALLS=str(calls), STATE=str(state))
        done = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("run-gate admission show", calls.read_text())
        self.assertRegex(calls.read_text(), r"run-gate admission set --max-concurrent 2 --unreadable-policy refuse --replace")
        self.assertIn("max_concurrent=2", state.read_text())

    def test_admission_only_action_reconciles_with_fake_run_gate_on_path(self) -> None:
        publish_function = re.search(r"(?ms)^publish_admission\(\) \{\n.*?^\}", self.install)
        only_function = re.search(r"(?ms)^admission_only\(\) \{\n.*?^\}", self.install)
        self.assertIsNotNone(publish_function, "install.sh exposes the admission publisher as a testable function")
        self.assertIsNotNone(only_function, "install.sh exposes the --admission-only action as a testable function")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        calls = tmp / "calls.log"
        state = tmp / "published.txt"
        state.write_text("run-gate admission: ciu-admission-1 generation=1 max_concurrent=4 unreadable_policy=refuse state=created\n")
        config = tmp / "host-setup.env"
        config.write_text("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=1G\nADMISSION_TICKET_IMAGE=ticket:local\n")
        meminfo = tmp / "meminfo"
        meminfo.write_text("MemTotal:        8388608 kB\nSwapTotal:             0 kB\n")
        python = fake_bin / "python3"
        python.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in *mdt-host-setup-wizard.py) exit 0;; esac\n"
            "exec \"$REAL_PYTHON\" \"$@\"\n"
        )
        python.chmod(0o755)
        docker = fake_bin / "docker"
        docker.write_text("#!/bin/sh\nprintf 'docker %s\\n' \"$*\" >> \"$CALLS\"\n[ \"$1 $2\" = 'image inspect' ]\n")
        docker.chmod(0o755)
        run_gate = fake_bin / "run-gate"
        run_gate.write_text(
            "#!/bin/sh\n"
            "printf 'run-gate %s\\n' \"$*\" >> \"$CALLS\"\n"
            "[ \"$1 $2\" = 'admission show' ] || [ \"$1 $2\" = 'admission set' ] || exit 64\n"
            "if [ \"$2\" = show ]; then cat \"$STATE\"; exit 0; fi\n"
            "replace=0; n=; policy=\n"
            "for arg in \"$@\"; do [ \"$arg\" = --replace ] && replace=1; done\n"
            "[ \"$replace\" -eq 1 ] || { echo 'operator reconcile must pass --replace' >&2; exit 2; }\n"
            "while [ \"$#\" -gt 0 ]; do case \"$1\" in --max-concurrent) n=$2; shift 2;; --unreadable-policy) policy=$2; shift 2;; *) shift;; esac; done\n"
            "printf 'run-gate admission: ciu-admission-2 generation=2 max_concurrent=%s unreadable_policy=%s state=created\\n' \"$n\" \"$policy\" > \"$STATE\"\n"
        )
        run_gate.chmod(0o755)
        shell = (publish_function.group(0) + "\n" + only_function.group(0)
                 + '\nadmission_only "$CONFIG" "$MEMINFO" 4\n')
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ.get('PATH', '')}", HERE=str(ROOT),
                   CONFIG=str(config), MEMINFO=str(meminfo), CALLS=str(calls), STATE=str(state),
                   REAL_PYTHON=sys.executable)
        done = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, env=env, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(calls.read_text(), r"run-gate admission set --max-concurrent 2 --unreadable-policy refuse --replace")
        self.assertIn("max_concurrent=2", state.read_text())

    def test_full_and_explicit_install_paths_select_their_publish_policy(self) -> None:
        self.assertRegex(self.install, r"publish_admission full \"\$CONFIG_PATH\"")
        self.assertRegex(self.install, r"admission_only /etc/mdt/host-setup\.env /proc/meminfo")
        self.assertRegex(self.install, r'publish_admission operator "\$config_path"')

    def test_publication_modes_and_cross_document_links_stay_in_sync(self) -> None:
        for mode in adm.PUBLISH_MODES:
            self.assertIn(f"`{mode}`", self.readme)
            self.assertIn(mode, self.example)
            self.assertIn(f"`{mode}`", self.consumers)
        self.assertIn("DEV_CPU_QUOTA", self.readme)
        self.assertIn("DEV_GATES_CPU_QUOTA", self.design)
        self.assertIn("ADMISSION_MAX_CONCURRENT", self.consumers)
        self.assertIn("sudo ./host-setup/install.sh --admission-only", self.readme)
        self.assertIn("sudo ./host-setup/install.sh --admission-only", self.consumers)
        self.assertIn("#mdt-077-admission-cap-publication-policy", self.readme)
        self.assertIn("## MDT-077 admission-cap publication policy", self.design)
        self.assertIn("#run-gate-admission-cap", self.readme)
        self.assertIn("### Run-Gate admission cap", self.consumers)

    def test_every_local_anchor_link_in_the_project_guides_resolves_to_a_heading(self) -> None:
        sources = (
            ROOT.parent / "README.md",
            ROOT.parent / "docs" / "CONSUMERS.md",
            ROOT.parent / "docs" / "DESIGN-GUIDE.md",
            ROOT / "README.md",
        )
        for source in sources:
            text = source.read_text()
            links = re.findall(r"\]\(([^)]+)\)", text)
            links.extend(re.findall(r"(?m)^\[[^\]]+\]:\s*(<[^>]+>|\S+)", text))
            for raw in links:
                target_text = raw.strip().split(maxsplit=1)[0].strip("<>")
                parsed = urlsplit(target_text)
                if not parsed.fragment or parsed.scheme or parsed.netloc:
                    continue
                fragment = unquote(parsed.fragment)
                target = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source.resolve()
                with self.subTest(source=source.relative_to(ROOT.parent), target=target_text):
                    self.assertEqual(target.suffix.lower(), ".md", "anchor target must be a Markdown document")
                    self.assertTrue(target.is_file(), f"anchor target does not exist: {target}")
                    slugs = self._markdown_heading_slugs(target.read_text())
                    self.assertIn(fragment, slugs, f"{target_text} does not match a heading in {target}")

    @staticmethod
    def _markdown_heading_slugs(text: str) -> set[str]:
        counts: dict[str, int] = {}
        slugs: set[str] = set()
        in_fence = False
        for line in text.splitlines():
            if re.match(r"^\s*(`{3,}|~{3,})", line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
            if match is None:
                continue
            heading = re.sub(r"<[^>]*>", "", match.group(1))
            heading = re.sub(r"!?\[([^]]+)\]\([^)]*\)", r"\1", heading)
            heading = re.sub(r"`+", "", heading).lower()
            base = re.sub(r"[^\w -]", "", heading, flags=re.UNICODE)
            base = base.strip().replace(" ", "-")
            count = counts.get(base, 0)
            counts[base] = count + 1
            slugs.add(base if count == 0 else f"{base}-{count}")
        return slugs

    def test_focused_host_setup_gate_is_declared_and_discoverable(self) -> None:
        gate = (ROOT.parent / "run-gate.toml").read_text()
        self.assertIn("[lanes.host-setup-admission]", gate)
        self.assertIn("host-setup/tests/test_admission.py", gate)
        self.assertIn("host-setup/tests/test_wizard_flow.py", gate)
        self.assertIn("run-gate host-setup-admission", self.project_readme)


if __name__ == "__main__":
    unittest.main()
