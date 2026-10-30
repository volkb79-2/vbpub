"""11d + 11e - a fresh home, the finalizer, and clean interactive shells.

A new, empty named volume is mounted at /home/vscode (handed to the run uid by a root
pre-step, exactly what a new host source is). The template's own `postCreateCommand`
is parsed from templates/devcontainer.json and run as `vscode`.

* after one run the home holds ONLY the comment-only `~/.zshrc` stub;
* a second run leaves the home tree - paths, modes, owners, content hashes -
  byte-identical;
* user files (`~/.zshrc`, `~/.config/htop/htoprc`) pre-created with sentinel content
  are never replaced;
* `bash -ic true` / `zsh -ic true` on a pseudo-terminal print nothing but the cgroup/KSM
  banner: no new-user wizard, no Oh My Zsh permission or cache complaints.
"""
from __future__ import annotations

import re
import unittest
from contextlib import contextmanager

import harness

# Everything the TOOLS themselves create in a fresh home while the interactive shells above
# run (measured, then justified). Anything else appearing there fails the test.
TOOL_CREATED_NATIVE = {
    ".zshrc": "the comment-only stub the finalizer writes (and only that content)",
}
TOOL_CREATED_VSC = {
    **TOOL_CREATED_NATIVE,
    ".zcompdump": "the base image's /etc/zsh/zshrc sources nvm's bash_completion, whose bare `compinit` "
                  "writes ~/.zcompdump (ZSH_COMPDUMP only moves Oh My Zsh's own dump)",
    ".cache": "root of Oh My Zsh's cache directory (ZSH_CACHE_DIR=~/.cache/oh-my-zsh: completion dump, "
              "alias probes), created because the Oh My Zsh distribution itself is read-only image content",
}
BANNER = re.compile(r"^\s+(cgroup|ksm)\b.*$")

SNAPSHOT = (
    "cd /home/vscode && find . -mindepth 1 | LC_ALL=C sort | while IFS= read -r p; do "
    "if [ -L \"$p\" ]; then c=\"-> $(readlink \"$p\")\"; "
    "elif [ -f \"$p\" ]; then c=\"$(sha256sum < \"$p\" | cut -d' ' -f1)\"; else c=-; fi; "
    "printf '%s|%s|%s\\n' \"$p\" \"$(stat -c '%a %U:%G %F' \"$p\")\" \"$c\"; done"
)


class Contract:
    @contextmanager
    def fresh_home(self):
        with harness.volume() as name:
            harness.own_volume(self.image.ref, name)
            yield name

    def in_home(self, volume: str, script: str, **kwargs) -> harness.Completed:
        return harness.sh(
            self.image.ref, script, volumes=[f"{volume}:/home/vscode"], workdir="/tmp", **kwargs
        )

    def finalize(self, volume: str) -> harness.Completed:
        return self.in_home(volume, harness.template_post_create_command())

    def snapshot(self, volume: str) -> list[str]:
        result = self.in_home(volume, SNAPSHOT)
        self.assertOk(result, "home snapshot")
        return result.stdout.splitlines()

    def test_a_fresh_home_receives_only_the_zshrc_stub(self) -> None:
        with self.fresh_home() as volume:
            self.assertEqual(self.snapshot(volume), [], "the volume starts empty")
            result = self.finalize(volume)
            self.assertOk(result, "postCreateCommand")
            self.assertIn("finalize complete", result.stdout)
            entries = self.snapshot(volume)
            self.assertEqual([entry.split("|")[0] for entry in entries], ["./.zshrc"], "\n".join(entries))
            content = self.in_home(volume, "cat ~/.zshrc").stdout
            code = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
            self.assertEqual(code, [], "the stub must be comment-only")

    def test_a_second_finalizer_run_leaves_the_home_byte_identical(self) -> None:
        with self.fresh_home() as volume:
            self.assertOk(self.finalize(volume))
            first = self.snapshot(volume)
            self.assertOk(self.finalize(volume))
            self.assertEqual(self.snapshot(volume), first)

    def test_user_files_with_sentinel_content_are_never_replaced(self) -> None:
        with self.fresh_home() as volume:
            seed = self.in_home(
                volume,
                "printf 'SENTINEL-ZSHRC\\n' > ~/.zshrc; mkdir -p ~/.config/htop; "
                "printf 'SENTINEL-HTOPRC\\n' > ~/.config/htop/htoprc",
            )
            self.assertOk(seed)
            before = self.snapshot(volume)
            self.assertOk(self.finalize(volume))
            self.assertOk(self.finalize(volume))
            self.assertEqual(self.snapshot(volume), before, "finalize touched a user file or added one")
            self.assertEqual(self.in_home(volume, "cat ~/.zshrc").stdout, "SENTINEL-ZSHRC\n")
            self.assertEqual(self.in_home(volume, "cat ~/.config/htop/htoprc").stdout, "SENTINEL-HTOPRC\n")

    # The template's own environment (containerEnv + the variable VS Code sets), so that the
    # vscode-settings and managed-BuildKit steps run instead of being skipped as on a "local" run.
    WORKSPACE = "/workspaces/ws"
    BUILDX_STATE = {
        ".config", ".config/docker", ".config/docker/buildx", ".config/docker/buildx/.lock",
        ".config/docker/buildx/activity", ".config/docker/buildx/activity/mdt-managed",
        ".config/docker/buildx/current", ".config/docker/buildx/defaults",
        ".config/docker/buildx/instances", ".config/docker/buildx/instances/mdt-managed",
    }
    BUILDER_REFUSAL = (
        "managed BuildKit builder 'mdt-managed' is not reachable at 'unix:///run/mdt-buildkitd/buildkitd.sock'"
    )

    def finalize_in_the_template_environment(self, home: str, workspace: str) -> harness.Completed:
        env = {**harness.template_container_env(), "REMOTE_CONTAINERS": "1"}
        # `cd` inside the script, not `-w`: with -w Docker creates the directory in the image
        # layer as root first and then copies that ownership onto the (empty) mounted volume.
        return harness.sh(
            self.image.ref, f"cd {self.WORKSPACE} && {harness.template_post_create_command()}",
            volumes=[f"{home}:/home/vscode", f"{workspace}:{self.WORKSPACE}"], env=env,
        )

    def test_the_template_environment_runs_the_vscode_settings_and_buildkit_steps_and_is_idempotent(self) -> None:
        """No docker socket and no network reach the container, so the managed-builder step cannot
        succeed: it must REFUSE with its own message (and it must never fall back to another
        builder). Before refusing it writes buildx's own instance state under BUILDX_CONFIG in the
        home - those entries are pinned below. A second run changes nothing."""
        with self.fresh_home() as home, harness.volume() as workspace:
            harness.own_volume(self.image.ref, workspace, path=self.WORKSPACE, mode="0755")
            first = self.finalize_in_the_template_environment(home, workspace)
            self.assertEqual(first.returncode, 1, first.output)
            self.assertIn(self.BUILDER_REFUSAL, first.output)
            self.assertIn("created .vscode/settings.json", first.output)
            self.assertIn("wrote the comment-only ~/.zshrc stub", first.output)
            self.assertIn("base-image tools verified", first.output)

            home_after_first = self.snapshot(home)
            created = {entry.split("|")[0].removeprefix("./") for entry in home_after_first}
            self.assertEqual(created, {".zshrc"} | self.BUILDX_STATE, "\n".join(home_after_first))
            workspace_tree = harness.sh(
                self.image.ref,
                f"cd {self.WORKSPACE} && find . -mindepth 1 | LC_ALL=C sort && cat .vscode/settings.json",
                volumes=[f"{workspace}:{self.WORKSPACE}"],
            ).stdout
            self.assertTrue(workspace_tree.startswith("./.vscode\n./.vscode/settings.json\n"), workspace_tree)
            self.assertIn('"python.terminal.activateEnvironment": false', workspace_tree)

            second = self.finalize_in_the_template_environment(home, workspace)
            self.assertEqual(second.returncode, 1, second.output)
            self.assertIn(self.BUILDER_REFUSAL, second.output)
            self.assertNotIn("created .vscode/settings.json", second.output)
            self.assertNotIn("wrote the comment-only", second.output)
            self.assertEqual(self.snapshot(home), home_after_first, "the home changed on the second run")
            self.assertEqual(
                harness.sh(
                    self.image.ref, f"cd {self.WORKSPACE} && find . -mindepth 1 | LC_ALL=C sort && cat .vscode/settings.json",
                    volumes=[f"{workspace}:{self.WORKSPACE}"],
                ).stdout,
                workspace_tree,
            )

    def _shell_output(self, volume: str, shell: str) -> str:
        # If the new-user wizard appears the shell would wait for a key: answer it (so the
        # session ends) - the assertions below then fail on the wizard text.
        session = harness.drive(
            self.image.ref, [shell, "-ic", "true"], [("zsh-newuser-install", b"q")],
            volumes=[f"{volume}:/home/vscode"], workdir="/tmp", env={"TERM": "xterm-256color"},
        )
        self.assertEqual(session.returncode, 0, f"{shell} -ic true:\n{session.screen}")
        return session.screen

    def test_interactive_shells_in_the_finalized_home_print_nothing_but_the_banner(self) -> None:
        with self.fresh_home() as volume:
            self.assertOk(self.finalize(volume))
            for shell in ("bash", "zsh"):
                with self.subTest(shell=shell):
                    residue = [
                        line for line in self._shell_output(volume, shell).splitlines()
                        if line.strip() and not BANNER.match(line)
                    ]
                    self.assertEqual(residue, [], f"{shell} -ic true printed more than the banner")
            shadows = self.in_home(
                volume, "ls -d ~/.local/bin/claude ~/.local/share/claude 2>&1 | grep -vc 'No such file'; true"
            ).stdout.strip()
            self.assertEqual(shadows, "0", "a native Claude install appeared in the home and would shadow updates")
            created = {entry.split("|")[0].removeprefix("./").split("/")[0] for entry in self.snapshot(volume)}
            allowed = TOOL_CREATED_VSC if self.is_vsc else TOOL_CREATED_NATIVE
            unexplained = sorted(created - set(allowed))
            self.assertEqual(unexplained, [], f"unexplained home entries after the shells ran: {unexplained}")

    def test_without_the_stub_zsh_would_start_its_new_user_wizard(self) -> None:
        """The premise of the stub: this is the failure the finalizer prevents."""
        with self.fresh_home() as volume:
            session = harness.drive(
                self.image.ref, ["zsh", "-ic", "true"], [("zsh-newuser-install", b"q")],
                volumes=[f"{volume}:/home/vscode"], workdir="/tmp", env={"TERM": "xterm"},
            )
            self.assertEqual(session.completed_steps, 1, f"the wizard never appeared:\n{session.screen}")
            self.assertIn("zsh-newuser-install", session.screen)


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
