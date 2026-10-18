"""11f - the system-wide defaults are effective with an EMPTY home.

For every tool the image configures system-wide (D7) this proves two things in a
container whose home is an empty tmpfs: the tool actually loads the shipped default,
and a file the user creates in the home overrides it. Each test names its observable.

The tools that need a terminal (nano, htop, mc) cannot report a setting
non-interactively, so the observable is the set of files the real tool opens, captured
with `strace` while it runs under `script` (a pseudo-terminal) for a few seconds: the
system file is opened successfully, and when the user's own file exists the lookup
order changes accordingly (htop and mc stop at the user's file; nano reads the user's
file after the system one so it can override per setting).
"""
from __future__ import annotations

import hashlib
import re
import unittest

import harness

HOME_TMPFS = "/home/vscode:rw,mode=0700,uid=1000,gid=1000"


def _opened(trace: str) -> list[str]:
    """Paths successfully opened (openat returning a descriptor), in order."""
    paths = []
    for line in trace.splitlines():
        match = re.search(r'openat\([^,]+, "([^"]+)", [^)]*\)\s+= (\d+)', line)
        if match:
            paths.append(match.group(1))
    return paths


class Contract:
    def box(self, script: str, **kwargs) -> harness.Completed:
        """Run `script` with an empty tmpfs home."""
        kwargs.setdefault("env", {"TERM": "xterm"})
        return harness.sh(self.image.ref, script, tmpfs=[HOME_TMPFS], workdir="/tmp", **kwargs)

    def trace(self, command: str, *, seconds: int = 4) -> list[str]:
        """Files `command` opens, run on a pty for a few seconds (failsafe, not a verdict)."""
        result = self.box(
            f"strace -f -e trace=openat -o /tmp/trace.out timeout {seconds} script -qec '{command}' /dev/null "
            ">/dev/null 2>&1 </dev/null; cat /tmp/trace.out"
        )
        self.assertOk(result, f"strace of {command}")
        return _opened(result.stdout)

    # bash -------------------------------------------------------------------------
    def test_bash_loads_the_system_default_and_the_users_bashrc_overrides_it(self) -> None:
        """Observable: HISTCONTROL / PROMPT_DIRTRIM set by the shipped bashrc, alias ll from aliases.sh."""
        default = self.box("bash -ic 'echo \"HC=$HISTCONTROL DT=$PROMPT_DIRTRIM\"; alias ll' 2>/dev/null")
        self.assertOk(default)
        self.assertIn("HC=ignoreboth DT=4", default.stdout)
        self.assertIn("alias ll='ls -l'", default.stdout)
        override = self.box(
            "printf 'HISTCONTROL=erasedups\\n' > ~/.bashrc; bash -ic 'echo \"HC=$HISTCONTROL\"' 2>/dev/null"
        )
        self.assertIn("HC=erasedups", override.stdout)

    def test_login_shells_get_the_shell_env_defaults_and_a_user_shell_env_overrides_them(self) -> None:
        """Observable: EDITOR and LESSOPEN from the image's shell.env; the user's file is sourced after."""
        default = self.box("bash -lc 'echo \"E=$EDITOR\"; echo \"L=$LESSOPEN\"'")
        self.assertOk(default)
        self.assertIn("E=nvim", default.stdout)
        self.assertIn("L=|/usr/local/share/mdt-cockpit/lesspipe.sh %s", default.stdout)
        override = self.box(
            "mkdir -p ~/.config/mdt-cockpit; printf 'export EDITOR=nano\\n' > ~/.config/mdt-cockpit/shell.env; "
            "bash -lc 'echo \"E=$EDITOR\"'"
        )
        self.assertIn("E=nano", override.stdout)

    def test_user_aliases_are_layered_over_the_image_aliases(self) -> None:
        result = self.box(
            "mkdir -p ~/.config/mdt-cockpit; printf \"alias ll='ls -la'\\nalias mine='true'\\n\" "
            "> ~/.config/mdt-cockpit/aliases.sh; bash -ic 'alias ll; alias mine; alias gs' 2>/dev/null"
        )
        self.assertOk(result)
        self.assertIn("alias ll='ls -la'", result.stdout)
        self.assertIn("alias mine='true'", result.stdout)
        self.assertIn("alias gs='git status -sb'", result.stdout, "an image alias the user did not touch survives")

    # zsh --------------------------------------------------------------------------
    def test_zsh_loads_the_system_default_and_the_users_zshrc_overrides_it(self) -> None:
        """Observable: the Ctrl-R binding (fzf integration from the shipped zshrc)."""
        default = self.box("zsh -ic 'bindkey \"^R\"' 2>/dev/null")
        self.assertOk(default)
        self.assertIn("fzf-history-widget", default.stdout)
        override = self.box(
            "printf 'bindkey \"^R\" history-incremental-search-forward\\n' > ~/.zshrc; "
            "zsh -ic 'bindkey \"^R\"' 2>/dev/null"
        )
        self.assertIn("history-incremental-search-forward", override.stdout)
        self.assertNotIn("fzf-history-widget", override.stdout)

    def test_oh_my_zsh_is_image_owned_on_the_vsc_family_only(self) -> None:
        result = self.box("zsh -ic 'echo \"T=${ZSH_THEME-unset} Z=${ZSH-unset} C=${ZSH_CACHE_DIR-unset}\"' 2>/dev/null")
        self.assertOk(result)
        if self.is_vsc:
            self.assertIn("T=devcontainers Z=/opt/mdt/oh-my-zsh C=/home/vscode/.cache/oh-my-zsh", result.stdout)
        else:
            self.assertIn("T=unset Z=unset C=unset", result.stdout)

    # nano / htop / mc: files the real tool opens -----------------------------------
    def test_nano_reads_the_system_nanorc_that_includes_the_image_defaults(self) -> None:
        opened = self.trace("nano /tmp/probe.txt")
        system = opened.index("/etc/nanorc")
        self.assertIn("/usr/local/share/mdt-cockpit/nanorc", opened[system:], "the include must be processed")

    def test_a_user_nanorc_is_read_after_the_system_one_so_it_can_override(self) -> None:
        result = self.box(
            "printf 'set nolinenumbers\\n' > ~/.nanorc; "
            "strace -f -e trace=openat -o /tmp/trace.out timeout 4 script -qec 'nano /tmp/probe.txt' /dev/null "
            ">/dev/null 2>&1 </dev/null; cat /tmp/trace.out"
        )
        opened = _opened(result.stdout)
        self.assertIn("/home/vscode/.nanorc", opened)
        self.assertLess(opened.index("/etc/nanorc"), opened.index("/home/vscode/.nanorc"))

    def test_htop_falls_back_to_the_system_htoprc_and_a_user_file_replaces_it(self) -> None:
        self.assertIn("/etc/htoprc", self.trace("htop -d 5"))
        result = self.box(
            "mkdir -p ~/.config/htop; cp /etc/htoprc ~/.config/htop/htoprc; "
            "strace -f -e trace=openat -o /tmp/trace.out timeout 4 script -qec 'htop -d 5' /dev/null "
            ">/dev/null 2>&1 </dev/null; cat /tmp/trace.out"
        )
        opened = _opened(result.stdout)
        self.assertIn("/home/vscode/.config/htop/htoprc", opened)
        self.assertNotIn("/etc/htoprc", opened, "htop stops at the user's file")

    def test_mc_reads_the_system_ini_and_a_user_ini_replaces_it(self) -> None:
        self.assertIn("/etc/mc/mc.ini", self.trace("mc"))
        result = self.box(
            "mkdir -p ~/.config/mc; cp /etc/mc/mc.ini ~/.config/mc/ini; "
            "strace -f -e trace=openat -o /tmp/trace.out timeout 4 script -qec 'mc' /dev/null "
            ">/dev/null 2>&1 </dev/null; cat /tmp/trace.out"
        )
        opened = _opened(result.stdout)
        self.assertIn("/home/vscode/.config/mc/ini", opened)
        self.assertNotIn("/etc/mc/mc.ini", opened, "mc stops at the user's file")

    def test_the_system_files_are_the_shipped_defaults(self) -> None:
        """The files in /etc are byte-identical to the repository's sources, and the three
        hook lines are present in the Debian files they extend."""
        sources = {
            "/etc/htoprc": "customization/htoprc",
            "/etc/mc/mc.ini": "customization/mc.ini",
            "/etc/xdg/nvim/sysinit.vim": "customization/nvim/sysinit.vim",
            "/usr/local/share/mdt-cockpit/bashrc": "customization/bashrc",
            "/usr/local/share/mdt-cockpit/zshrc": "customization/zshrc",
            "/usr/local/share/mdt-cockpit/nanorc": "customization/nanorc",
            "/usr/local/share/mdt-cockpit/profile.sh": "customization/profile.sh",
            "/usr/local/share/mdt-cockpit/shell.env": "customization/shell.env",
        }
        result = self.box("sha256sum " + " ".join(sources))
        self.assertOk(result)
        in_image = {line.split()[1]: line.split()[0] for line in result.stdout.splitlines()}
        for path, source in sources.items():
            with self.subTest(path=path):
                expected = hashlib.sha256((harness.ROOT / source).read_bytes()).hexdigest()
                self.assertEqual(in_image[path], expected, f"{path} differs from {source}")
        hooks = self.box(
            "S=/usr/local/share/mdt-cockpit; "
            "grep -F \"include \\\"$S/nanorc\\\"\" /etc/nanorc >/dev/null && echo nano-includes; "
            "grep -F \"$S/bashrc\" /etc/bash.bashrc >/dev/null && echo bash-hook; "
            "grep -F \"$S/zshrc\" /etc/zsh/zshrc >/dev/null && echo zsh-hook"
        )
        self.assertEqual(hooks.stdout.split(), ["nano-includes", "bash-hook", "zsh-hook"], hooks.output)

    # nvim --------------------------------------------------------------------------
    def test_neovim_starts_the_system_tree_until_the_user_has_a_config(self) -> None:
        """Observable: `vim.g.mdt_system_config`, set by sysinit.vim immediately before it runs
        /etc/xdg/nvim/init.lua; it is unset once the user has an init.lua of their own.

        This proves the system tree is WIRED. Whether the tree is a working NvChad setup is
        a separate, open question (MDT-007: the pinned archive is the NvChad core repository,
        whose init.lua is a placeholder), so it is deliberately not asserted here.
        """
        probe = "nvim --headless -c \"lua io.write(tostring(vim.g.mdt_system_config))\" -c q 2>/dev/null"
        default = self.box(probe)
        self.assertOk(default)
        self.assertTrue(default.stdout.strip().splitlines()[-1].endswith("/etc/xdg/nvim"), default.output)
        override = self.box(
            "mkdir -p ~/.config/nvim && printf 'vim.g.user_config = 1\\n' > ~/.config/nvim/init.lua; "
            "nvim --headless -c \"lua io.write(tostring(vim.g.mdt_system_config)..'|'..tostring(vim.g.user_config))\" "
            "-c q 2>/dev/null"
        )
        self.assertEqual(override.stdout.strip().splitlines()[-1], "nil|1")

    # less --------------------------------------------------------------------------
    def test_less_pipes_files_through_the_image_lesspipe_and_the_override_works(self) -> None:
        """Observable: syntax-highlighted output contains ANSI colour escapes, plain `less` does not."""
        script = (
            "bash -lc 'printf \"def f():\\n    return 1\\n\" > /tmp/x.py; "
            "less /tmp/x.py | od -An -c | grep -c \"033\" ; LESSOPEN= less /tmp/x.py | od -An -c | grep -c 033 || true'"
        )
        result = self.box(script)
        counts = result.stdout.split()
        self.assertGreater(int(counts[0]), 0, "lesspipe (bat) must colour the file\n" + result.output)
        self.assertEqual(counts[-1], "0", "with LESSOPEN cleared the file is plain")


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
