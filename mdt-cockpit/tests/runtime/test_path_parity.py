"""11b - one managed PATH shape everywhere, and runtime additions survive (D5).

Every shell form - `sh -c`, `bash -c`, `bash -lc`, `bash -ic`, `zsh -ic`, `zsh -lc` - must show
the same MANAGED SHAPE: the image's prefix entries first in the D5 order, `~/.local/bin` last,
each managed entry exactly once. The FULL path is identical only among shells that inherit the
same environment untouched (the non-login ones): a login shell legitimately differs (the VS Code
family's Microsoft `00-restore-env.sh` adds entries, zsh does not run it), so for login shells
the lane asserts the shape plus "every inherited entry is still there, in its relative order".

An entry added to the environment at runtime (`docker run -e PATH=/opt/probe/bin:...`, which is
what a consumer `containerEnv.PATH`, a Dev Containers feature or the userEnvProbe login shell do)
must survive every one of them: the image patches Debian's /etc/profile to keep an inherited
PATH and its hook only MERGES the managed entries into place.
"""
from __future__ import annotations

import unittest

import harness

SHELLS = (
    ("sh -c", ["sh", "-c"]),
    ("bash -c", ["bash", "-c"]),
    ("bash -lc", ["bash", "-lc"]),
    ("bash -ic", ["bash", "-ic"]),
    ("zsh -ic", ["zsh", "-ic"]),
    ("zsh -lc", ["zsh", "-lc"]),
)
NON_LOGIN = ("sh -c", "bash -c", "bash -ic", "zsh -ic")
SURVIVAL_SHELLS = ("bash -lc", "zsh -lc", "bash -ic", "zsh -ic")
PROBE = "/opt/probe/bin"
DEBIAN_DEFAULTS = (
    "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games",            # non-root
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",        # root
)


def _is_subsequence(wanted: list[str], entries: list[str]) -> bool:
    remaining = iter(entries)
    return all(entry in remaining for entry in wanted)


class Contract:
    def _image_path(self) -> list[str]:
        return self.image.env["PATH"].split(":")

    def _argv(self, label: str) -> list[str]:
        return dict(SHELLS)[label]

    def _path_of(self, argv: list[str], **kwargs) -> list[str]:
        result = harness.run(self.image.ref, [*argv, 'echo "PATH-IS:$PATH"'], **kwargs)
        self.assertEqual(result.returncode, 0, result.output)
        lines = [line for line in result.stdout.splitlines() if line.startswith("PATH-IS:")]
        self.assertEqual(len(lines), 1, f"expected one PATH line\n{result.output}")
        return lines[0].removeprefix("PATH-IS:").split(":")

    def assertManagedShape(self, entries: list[str], label: str) -> None:
        prefix, suffix = list(harness.PATH_PREFIX), list(harness.PATH_SUFFIX)
        self.assertEqual(entries[: len(prefix)], prefix, f"{label}: managed prefix first, in order\n{entries}")
        self.assertEqual(entries[-len(suffix):], suffix, f"{label}: ~/.local/bin last\n{entries}")
        for managed in prefix + suffix:
            self.assertEqual(entries.count(managed), 1, f"{label}: {managed} must appear exactly once")

    def test_the_images_own_path_has_the_d5_shape(self) -> None:
        self.assertManagedShape(self._image_path(), "image ENV PATH")
        self.assertNotIn("MDT_IMAGE_PATH", self.image.env, "an env snapshot cannot survive a cleared environment")

    def test_every_shell_shows_the_same_managed_shape_and_keeps_every_inherited_entry(self) -> None:
        inherited = [entry for entry in self._image_path() if entry not in harness.PATH_PREFIX + harness.PATH_SUFFIX]
        for label, argv in SHELLS:
            with self.subTest(shell=label):
                entries = self._path_of(argv, user="vscode")
                self.assertManagedShape(entries, label)
                self.assertTrue(_is_subsequence(inherited, entries), f"{label}: an inherited entry was lost or reordered\n{entries}")

    def test_shells_that_inherit_the_environment_untouched_see_the_identical_full_path(self) -> None:
        expected = self.image.env["PATH"].split(":")
        for label in NON_LOGIN:
            with self.subTest(shell=label):
                self.assertEqual(self._path_of(self._argv(label), user="vscode"), expected, label)

    def test_a_runtime_added_entry_survives_every_shell_in_its_place(self) -> None:
        """What a consumer containerEnv.PATH, a Dev Containers feature or the userEnvProbe login
        shell hand to the container: the added entry must still be there, once, after the managed
        prefix and before the suffix."""
        env = {"PATH": f"{PROBE}:{self.image.env['PATH']}"}
        for label in SURVIVAL_SHELLS:
            with self.subTest(shell=label):
                entries = self._path_of(self._argv(label), user="vscode", env=env)
                self.assertEqual(entries.count(PROBE), 1, f"{label}: the runtime-added entry was lost or duplicated\n{entries}")
                if "-l" in label:
                    # a login shell runs the hook: managed prefix first, the added entry right after it
                    self.assertManagedShape(entries, label)
                    self.assertLess(entries.index(harness.PATH_PREFIX[-1]), entries.index(PROBE))
                else:
                    # a non-login shell runs no hook: it keeps exactly what it inherited, so the
                    # caller's own order stands - but every managed entry is still there, once
                    managed = list(harness.PATH_PREFIX) + list(harness.PATH_SUFFIX)
                    self.assertTrue(_is_subsequence(managed, entries), f"{label}: {entries}")
                    for entry in managed:
                        self.assertEqual(entries.count(entry), 1, f"{label}: {entry}")

    def test_login_shells_with_a_cleared_environment_get_the_managed_entries_and_the_default(self) -> None:
        """su -, sudo -i, ssh and cron start login shells WITHOUT the image's ENV: the managed
        entries come from the file the build wrote, the rest from the patched /etc/profile."""
        script = 'echo "PATH-IS:$PATH"'
        cases = {
            "env -i bash -lc": (["env", "-i", "bash", "-lc", script], {"user": "vscode"}),
            "env -i zsh -lc": (["env", "-i", "zsh", "-lc", script], {"user": "vscode"}),
            "su -l vscode -c (as root)": (["su", "-l", "vscode", "-c", script], {"user": "0"}),
        }
        for label, (argv, kwargs) in cases.items():
            with self.subTest(shell=label):
                result = harness.run(self.image.ref, argv, **kwargs)
                self.assertEqual(result.returncode, 0, result.output)
                line = [x for x in result.stdout.splitlines() if x.startswith("PATH-IS:")][-1]
                entries = line.removeprefix("PATH-IS:").split(":")
                self.assertManagedShape(entries, label)
                middle = ":".join(entries[len(harness.PATH_PREFIX): -len(harness.PATH_SUFFIX)])
                if label.startswith("su "):  # PAM/login.defs ENV_PATH is the Debian default
                    self.assertTrue(any(default in middle for default in DEBIAN_DEFAULTS), f"{label}: no Debian default in {middle}")
                else:
                    # `env -i`: the shell supplies its OWN compiled-in default when PATH is unset (bash:
                    # /usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:., zsh: /bin:/usr/bin:...),
                    # so /etc/profile's ${PATH:-...} never sees an empty value
                    self.assertIn("/usr/bin", middle.split(":"), label)
                    self.assertIn("/bin", middle.split(":"), label)

    def test_sourcing_the_hook_again_changes_nothing(self) -> None:
        script = 'before="$PATH"; . /usr/local/share/mdt-cockpit/path-hook.sh; test "$before" = "$PATH" && echo SAME || echo "CHANGED:$PATH"'
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell):
                result = harness.run(self.image.ref, [shell, "-lc", script], user="vscode")
                self.assertEqual(result.stdout.strip().splitlines()[-1], "SAME", result.output)

    def test_etc_profile_keeps_an_inherited_path_and_nothing_else_changed(self) -> None:
        result = harness.sh(self.image.ref, "grep -c 'PATH=\"${PATH:-' /etc/profile; grep -c '^export PATH$' /etc/profile")
        self.assertEqual(result.stdout.split(), ["2", "1"], result.output)

    def test_a_missing_entries_file_is_reported_not_skipped(self) -> None:
        """The hook must not silently do nothing: one line on stderr, PATH left alone."""
        for login_shell in ("/bin/bash", "/usr/bin/zsh"):
            with self.subTest(shell=login_shell):
                result = harness.run(
                    self.image.ref,
                    ["bash", "-c",
                     "mv /usr/local/share/mdt-cockpit/path-entries /tmp/path-entries.gone && "
                     f"env -i {login_shell} -lc 'echo \"PATH-IS:$PATH\"'"],
                    user="0",
                )
                self.assertEqual(result.returncode, 0, result.output)
                self.assertIn("mdt-cockpit: /usr/local/share/mdt-cockpit/path-entries is missing", result.stderr)
                path_lines = [line for line in result.stdout.splitlines() if line.startswith("PATH-IS:")]
                self.assertEqual(len(path_lines), 1)
                self.assertNotIn("/opt/mdt", path_lines[0], "the managed entries must not appear from nowhere")

    def test_a_command_resolves_the_same_in_every_shell(self) -> None:
        """The order has an observable consequence: `python` is the venv's everywhere."""
        for label, argv in SHELLS:
            with self.subTest(shell=label):
                result = harness.run(self.image.ref, [*argv, "echo \"WHICH:$(command -v python)\""], user="vscode")
                self.assertOk(result, label)
                self.assertIn("WHICH:/opt/mdt/venv/bin/python", result.stdout.splitlines(), label)


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
