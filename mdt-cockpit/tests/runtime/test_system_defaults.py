"""11f - the system-wide defaults are effective with an EMPTY home.

For every tool the image configures system-wide (D7) this proves two things in a
container whose home is an empty tmpfs: the tool actually loads the shipped default,
and a file the user creates in the home overrides it. Each test names its observable.

The tools that need a terminal (nano, htop, mc) cannot report a setting
non-interactively, so the observable is the set of files the real tool opens, captured
with `strace` while the tool runs on a real pseudo-terminal that the test DRIVES: it waits
until the terminal shows the tool's interface (so the configuration has certainly been
read) and only then sends the quit key. No timer ends a tool and no timer decides a
verdict. The system file is opened successfully, and when the user's own file exists the
lookup order changes accordingly (htop and mc stop at the user's file; nano reads the
user's file after the system one so it can override per setting).
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

    def trace(self, command: str, steps: list[tuple[str, bytes]], *, prepare: str = "") -> list[str]:
        """Files `command` opens while it runs on a driven pty (see the module docstring)."""
        script = (
            f"{prepare}strace -f -e trace=openat -o /tmp/trace.out {command}; "
            "echo MDT-TRACE-BEGIN; cat /tmp/trace.out; echo MDT-TRACE-END"
        )
        session = harness.drive(
            self.image.ref, ["bash", "-c", script], steps,
            tmpfs=[HOME_TMPFS], workdir="/tmp",
            env={"TERM": "xterm-256color"},  # mc.ini selects a 256-colour skin
        )
        self.assertGreaterEqual(session.completed_steps, 1, f"{command} never showed its interface:\n{session.screen[-1500:]}")
        self.assertIn("MDT-TRACE-END", session.screen, f"{command} did not exit after the quit key:\n{session.screen[-800:]}")
        trace = session.screen.split("MDT-TRACE-BEGIN", 1)[1].split("MDT-TRACE-END", 1)[0]
        return _opened(trace)

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

    # tmux / git / readline / vim: non-interactive observables ----------------------
    def test_tmux_reads_the_system_conf_and_a_user_conf_overrides_it(self) -> None:
        """Observable: the prefix and history-limit tmux reports for a detached server."""
        default = self.box("tmux new-session -d -s probe && tmux show-options -g prefix history-limit mouse")
        self.assertOk(default)
        self.assertIn("prefix C-Space", default.stdout)
        self.assertIn("history-limit 10000", default.stdout)
        self.assertIn("mouse on", default.stdout)
        override = self.box(
            "printf 'set -g prefix C-a\\n' > ~/.tmux.conf; tmux new-session -d -s probe && tmux show-options -g prefix"
        )
        self.assertIn("prefix C-a", override.stdout)

    def test_git_uses_the_system_defaults_and_the_users_gitconfig_overrides_them(self) -> None:
        default = self.box("git config --get core.pager; git config --get merge.conflictStyle; git config --get push.autoSetupRemote")
        self.assertOk(default)
        self.assertEqual(default.stdout.split(), ["delta", "zdiff3", "true"])
        override = self.box("git config --global core.pager cat; git config --get core.pager")
        self.assertEqual(override.stdout.split(), ["cat"])

    def test_readline_defaults_are_appended_to_the_system_inputrc_and_a_user_inputrc_replaces_it(self) -> None:
        """Observable: bind -p output; a user ~/.inputrc replaces /etc/inputrc unless it includes it."""
        default = self.box("bash -ic 'bind -p' 2>/dev/null | grep -F -e history-search-backward -e backward-kill-word")
        self.assertOk(default)
        self.assertIn('"\\e[A": history-search-backward', default.stdout)
        replaced = self.box("printf 'set bell-style none\\n' > ~/.inputrc; bash -ic 'bind -p' 2>/dev/null | grep -c history-search-backward || true")
        self.assertEqual(replaced.stdout.strip(), "0")

    def test_vim_reads_the_system_vimrc_local(self) -> None:
        """Observable: shiftwidth/expandtab as vim reports them in ex mode."""
        result = self.box("vim -es -c 'redir @a | set shiftwidth? expandtab? | redir END' -c 'put a' -c '%p' -c 'q!' /dev/null")
        self.assertOk(result)
        self.assertRegex(result.stdout, r"shiftwidth=4")
        self.assertIn("expandtab", result.stdout)

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
    NANO = [("GNU nano", b"\x18")]            # header drawn -> Ctrl-X
    HTOP = [("F10", b"q")]                      # function-key bar drawn -> q
    MC = [("10Quit|Quit", b"\x1b[21~"), (r"(?i)really want|\[\s*Yes", b"\r")]  # F10, then confirm if asked

    def test_nano_reads_the_system_nanorc_that_includes_the_image_defaults(self) -> None:
        opened = self.trace("nano /tmp/probe.txt", self.NANO)
        system = opened.index("/etc/nanorc")
        self.assertIn("/usr/local/share/mdt-cockpit/nanorc", opened[system:], "the include must be processed")

    def test_a_user_nanorc_is_read_after_the_system_one_so_it_can_override(self) -> None:
        opened = self.trace("nano /tmp/probe.txt", self.NANO, prepare="printf 'set nolinenumbers\\n' > ~/.nanorc; ")
        self.assertIn("/home/vscode/.nanorc", opened)
        self.assertLess(opened.index("/etc/nanorc"), opened.index("/home/vscode/.nanorc"))

    def test_htop_falls_back_to_the_system_htoprc_and_a_user_file_replaces_it(self) -> None:
        self.assertIn("/etc/htoprc", self.trace("htop -d 5", self.HTOP))
        opened = self.trace(
            "htop -d 5", self.HTOP, prepare="mkdir -p ~/.config/htop; cp /etc/htoprc ~/.config/htop/htoprc; "
        )
        self.assertIn("/home/vscode/.config/htop/htoprc", opened)
        self.assertNotIn("/etc/htoprc", opened, "htop stops at the user's file")

    def test_mc_reads_the_system_ini_and_a_user_ini_replaces_it(self) -> None:
        self.assertIn("/etc/mc/mc.ini", self.trace("mc", self.MC))
        opened = self.trace("mc", self.MC, prepare="mkdir -p ~/.config/mc; cp /etc/mc/mc.ini ~/.config/mc/ini; ")
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
    # The shipped "NvChad" tree is the NvChad CORE repository, whose init.lua is a placeholder that
    # prints this on every start (backlog MDT-011). It is the only stderr a start may produce.
    MDT_007_PLACEHOLDER = "use this along starter!!!"

    def test_neovim_starts_cleanly_with_the_system_tree_and_only_the_known_placeholder_message(self) -> None:
        """nvim headless exits 0 and its stderr holds nothing but the MDT-011 placeholder: a Lua
        error in /etc/xdg/nvim (or in sysinit.vim) would show up here, which the previous probe of
        `vim.g.mdt_system_config` (set by sysinit.vim itself) could not see. When MDT-011 is fixed
        this test must start asserting an EMPTY stderr."""
        result = self.box("nvim --headless -c 'lua io.write(tostring(vim.g.mdt_system_config))' -c q")
        self.assertEqual(result.returncode, 0, result.output)
        self.assertTrue(result.stdout.strip().endswith("/etc/xdg/nvim"), "the system tree must be the one started")
        messages = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        self.assertEqual(
            messages, [self.MDT_007_PLACEHOLDER],
            f"exactly one placeholder message per start (two would mean the tree ran twice); stderr:\n{result.stderr}",
        )

    def test_a_user_init_lua_replaces_the_system_tree(self) -> None:
        result = self.box(
            "mkdir -p ~/.config/nvim && printf 'vim.g.user_config = 1\\n' > ~/.config/nvim/init.lua; "
            "nvim --headless -c \"lua io.write(tostring(vim.g.mdt_system_config)..'|'..tostring(vim.g.user_config))\" -c q"
        )
        self.assertEqual(result.returncode, 0, result.output)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "nil|1")
        self.assertEqual(result.stderr.strip(), "", "the placeholder belongs to the system tree, not to a user config")

    def test_a_starter_style_tree_gets_its_lazy_lockfile_redirected_into_the_state_directory(self) -> None:
        """sysinit.vim redirects lazy.nvim's lockfile (which defaults to the config directory, here
        the read-only /etc/xdg/nvim) into ~/.local/state/nvim when the tree has a configs.lazy
        module. The shipped tree has none (MDT-011), so this runs against a fixture tree written
        into the throw-away container's /etc/xdg/nvim. The fixture's init.lua also appends to a
        file each time it runs: the tree must run exactly ONCE per start (sysinit.vim used to
        `dofile` it on top of Neovim's own fallback to $XDG_CONFIG_DIRS, running it twice)."""
        fixture = (
            "mkdir -p /etc/xdg/nvim/lua/configs && "
            "printf 'return { lockfile = \"/etc/xdg/nvim/lazy-lock.json\" }\\n' > /etc/xdg/nvim/lua/configs/lazy.lua && "
            "printf 'io.write(require(\"configs.lazy\").lockfile)\\nlocal f = io.open(\"/tmp/runs\", \"a\"); f:write(\"run\\\\n\"); f:close()\\n' "
            "> /etc/xdg/nvim/init.lua && "
            "runuser -u vscode -- env HOME=/home/vscode nvim --headless -c q; echo; echo \"RUNS=$(wc -l < /tmp/runs)\""
        )
        result = harness.sh(self.image.ref, fixture, user="0", tmpfs=[HOME_TMPFS], workdir="/tmp")
        self.assertEqual(result.returncode, 0, result.output)
        self.assertIn("/home/vscode/.local/state/nvim/lazy-lock.json", result.stdout)
        self.assertNotIn("/etc/xdg/nvim/lazy-lock.json", result.stdout)
        self.assertIn("RUNS=1", result.stdout, "the system init.lua must run exactly once per start")
        self.assertEqual(result.stderr.strip(), "")

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
