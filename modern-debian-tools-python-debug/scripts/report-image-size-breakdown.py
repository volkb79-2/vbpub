#!/usr/bin/env python3
"""Mechanically re-runnable image space breakdown, by layer and by logical group.

Ground truth is `docker inspect`/`docker history` (byte-exact). Fine-grained
attribution *inside* a merged layer (which files belong to `claude` vs `vault`
vs `awscli` inside the big "modern tools" RUN, say) comes from a fixed registry
of `du`/`importlib.metadata` probes against the live image filesystem — that
registry is inherently tied to today's Dockerfile, so this script always prints
how much of the image it could NOT attribute to a known leaf. A growing
unattributed total (or a growing `(unmatched layer)` bucket) means the registry
below needs updating to match a changed Dockerfile — that is the intended
signal, not a bug to silence.

Both the layer view (organized by which Dockerfile instruction produced the
bytes) and the logical view (organized by what the bytes are *for* — AI
coding-agent tools, service CLIs, runtimes, ...) are built from one shared
leaf registry, so a byte measured once renders consistently in both.
"""
from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = (
    "ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:"
    "trixie-py3.14-php8.5-latest"
)

# ── Docker plumbing ──────────────────────────────────────────────────────────


def run(argv: list[str], **kwargs) -> str:
    result = subprocess.run(
        argv, check=True, capture_output=True, text=True, **kwargs
    )
    return result.stdout


def docker_image_size(image: str) -> int:
    out = run(["docker", "inspect", image, "--format", "{{.Size}}"])
    return int(out.strip())


SIZE_RE = re.compile(r"^([\d.]+)\s*([A-Za-z]*)$")
UNITS = {"": 1, "B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def parse_docker_size(text: str) -> int:
    text = text.strip()
    match = SIZE_RE.match(text)
    if not match:
        raise ValueError(f"Unrecognized docker size token: {text!r}")
    value, unit = match.groups()
    multiplier = UNITS.get(unit)
    if multiplier is None:
        raise ValueError(f"Unknown size unit {unit!r} in {text!r}")
    return round(float(value) * multiplier)


def docker_history(image: str) -> list[tuple[int, str]]:
    """Parse `docker history` output into (size_bytes, created_by) pairs.

    A `RUN <<EOF ... EOF` heredoc instruction embeds literal newlines in its
    CreatedBy field, so a naive line split misreads its continuation lines as
    malformed new records. Only a line whose first tab-separated field parses
    as a docker size token starts a new record; anything else is folded into
    the previous record's CreatedBy text.
    """
    out = run(
        [
            "docker",
            "history",
            "--no-trunc",
            "--format",
            "{{.Size}}\t{{.CreatedBy}}",
            image,
        ]
    )
    layers: list[tuple[int, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        size_text, sep, created_by = line.partition("\t")
        try:
            size = parse_docker_size(size_text) if sep else None
        except ValueError:
            size = None
        if size is None:
            if layers:
                prev_size, prev_created_by = layers[-1]
                layers[-1] = (prev_size, f"{prev_created_by} {line}")
            continue
        layers.append((size, created_by))
    return layers


def run_probe_script(image: str, script: str) -> dict[str, int]:
    """Run one shell script as root inside the image and parse KEY<TAB>BYTES lines."""
    out = subprocess.run(
        ["docker", "run", "--rm", "--user", "root", "--entrypoint", "bash", image, "-c", script],
        capture_output=True,
        text=True,
    ).stdout
    values: dict[str, int] = {}
    for line in out.splitlines():
        key, sep, raw = line.partition("\t")
        if not sep:
            continue
        try:
            values[key] = int(raw.strip())
        except ValueError:
            continue
    return values


# ── Git staleness stamping ───────────────────────────────────────────────────


def git_last_commit(relative_path: str) -> tuple[str, str] | None:
    try:
        out = run(
            ["git", "log", "-1", "--format=%h|%cI", "--", relative_path],
            cwd=PROJECT_ROOT,
        ).strip()
    except subprocess.CalledProcessError:
        return None
    if not out:
        return None
    commit_hash, _, date = out.partition("|")
    return commit_hash, date


# ── Leaf registry ────────────────────────────────────────────────────────────


@dataclass
class Leaf:
    key: str
    label: str
    layer_path: tuple[str, ...]
    logical_path: tuple[str, ...]
    du_paths: tuple[str, ...] = ()
    du_excludes: tuple[str, ...] = ()
    note: str = ""
    bytes_value: int = field(default=0, init=False)
    present: bool = field(default=False, init=False)


def du_sum_script(paths: tuple[str, ...], excludes: tuple[str, ...] = ()) -> str:
    # Paths deliberately remain unquoted shell globs (e.g. "opencode-linux-*")
    # so a single probe covers versioned installs. Exclude patterns are quoted
    # because they must reach du as patterns rather than expand in the shell.
    joined = " ".join(paths)
    excluded = " ".join(f"--exclude={shlex.quote(pattern)}" for pattern in excludes)
    command = " ".join(part for part in ("du -sbc", excluded, joined) if part)
    return f'{command} 2>/dev/null | tail -1 | awk \'{{print $1}}\''


LEAVES: list[Leaf] = [
    # ── Base image: groups with no clean internal probe stay atomic (one leaf
    #    per layer-path, so their weight passes through to both views intact).
    Leaf("base_rootfs", "Debian rootfs & core OS utils (git/mercurial/svn/openssh/wget/...)", ("Base image", "Debian rootfs & core OS utils"), ("Base OS & build toolchain",)),
    Leaf("base_features", "VS Code devcontainer Features (python/node/git/common-utils installers)", ("Base image", "VS Code devcontainer Features"), ("Base OS & build toolchain",)),
    # The toolchain+python-build group spans several upstream layers (apt build
    # deps, the from-source compile, a later imagemagick purge, base pip
    # upgrade) that only docker-history can see as one blob; the interpreter
    # itself IS separately measurable on the live filesystem, so it gets a real
    # probe and the rest is a named (not generic) residual — see measure().
    Leaf(
        "base_python_build", "CPython interpreter (built from source)",
        ("Base image", "Build toolchain + Python built from source"), ("Runtimes", "Python (base interpreter)"),
        ("/usr/local/lib/python3.*", "/usr/local/include/python3.*",
         "/usr/local/bin/python3.*", "/usr/local/bin/idle3*", "/usr/local/bin/pydoc3*", "/usr/local/bin/pip3*"),
        ("*/site-packages",),
    ),
    Leaf("base_toolchain_residual", "C/C++ build toolchain + Python build deps (gcc/g++/dev headers, kept for future pip compiles)", ("Base image", "Build toolchain + Python built from source"), ("Base OS & build toolchain",)),
    # ── mdt layers: system-level ─────────────────────────────────────────────
    Leaf("mdt_core_apt", "Core system apt packages", ("mdt layers", "Core system apt packages"), ("Base OS & build toolchain",)),
    Leaf("mdt_skopeo", "skopeo (Debian testing pin)", ("mdt layers", "skopeo"), ("Base OS & build toolchain",)),
    Leaf("mdt_nodejs", "Node.js runtime (nodesource)", ("mdt layers", "Node.js runtime"), ("Runtimes", "Node.js")),
    Leaf("mdt_nvim", "Neovim + NvChad", ("mdt layers", "Neovim + NvChad"), ("Editor tooling",)),
    Leaf("mdt_pgredis", "postgresql-client + redis-tools", ("mdt layers", "postgresql-client + redis-tools"), ("Service / infrastructure CLIs",)),
    Leaf("mdt_dockercli", "Docker CLI + buildx + compose", ("mdt layers", "Docker CLI + buildx + compose"), ("Service / infrastructure CLIs",)),
    Leaf("mdt_misc", "misc config/customization copies", ("mdt layers", "misc config/customization copies"), ("Base OS & build toolchain",)),
    # PHP: real dpkg payload vs a named (not generic) "forgot to clean apt lists" residual.
    Leaf("php_packages", "PHP package payload (dpkg Installed-Size)", ("mdt layers", "PHP 8.5 runtime"), ("Runtimes", "PHP")),
    Leaf("php_apt_residual", "uncleaned apt lists/cache (this RUN is missing `rm -rf /var/lib/apt/lists/*`)", ("mdt layers", "PHP 8.5 runtime"), ("Runtimes", "PHP")),
    # ── "Modern tools" mega-layer ────────────────────────────────────────────
    Leaf("mt_claude", "claude (Claude Code, root binary)", ("mdt layers", '"Modern tools" mega-layer'), ("AI coding-agent tools",), ("/usr/local/bin/claude",)),
    Leaf("mt_antigravity", "antigravity (+ duplicate `agy` copy)", ("mdt layers", '"Modern tools" mega-layer'), ("AI coding-agent tools",), ("/usr/local/bin/antigravity", "/usr/local/bin/agy")),
    Leaf("mt_awscli", "awscli", ("mdt layers", '"Modern tools" mega-layer'), ("Service / infrastructure CLIs",), ("/usr/local/aws",)),
    Leaf("mt_vault", "vault", ("mdt layers", '"Modern tools" mega-layer'), ("Service / infrastructure CLIs",), ("/usr/local/bin/vault",)),
    Leaf("mt_consul", "consul", ("mdt layers", '"Modern tools" mega-layer'), ("Service / infrastructure CLIs",), ("/usr/local/bin/consul",)),
    Leaf("mt_gh", "gh (GitHub CLI, incl. man pages)", ("mdt layers", '"Modern tools" mega-layer'), ("Service / infrastructure CLIs",), ("/usr/local/bin/gh", "/usr/local/share/man/man1/gh*")),
    Leaf(
        "mt_inspection", "container/security inspection tools (dtop/lazydocker/dive/syft/hadolint/grype/cdebug)",
        ("mdt layers", '"Modern tools" mega-layer'), ("Container & security inspection tools",),
        ("/usr/local/bin/dtop", "/usr/local/bin/lazydocker", "/usr/local/bin/syft",
         "/usr/local/bin/hadolint", "/usr/local/bin/grype", "/usr/local/bin/cdebug"),
    ),
    Leaf(
        "mt_modern_cli", "modern CLI replacements (bat/delta/fd/ripgrep/rga/fzf/yq/shellcheck/crane/b2/regctl/htop)",
        ("mdt layers", '"Modern tools" mega-layer'), ("Modern CLI replacements",),
        ("/usr/local/bin/bat", "/usr/local/bin/delta", "/usr/local/bin/fd", "/usr/local/bin/fdfind",
         "/usr/local/bin/rg", "/usr/local/bin/rga*", "/usr/local/bin/fzf", "/usr/local/bin/yq",
         "/usr/local/bin/shellcheck", "/usr/local/bin/crane", "/usr/local/bin/b2",
         "/usr/local/bin/regctl", "/usr/local/bin/htop"),
    ),
    Leaf(
        "mt_glances", "glances + pip dependency closure (system python)",
        ("mdt layers", '"Modern tools" mega-layer'), ("Runtimes", "Python (base interpreter)"),
        ("/usr/local/lib/python3.*/site-packages/glances*",
         "/usr/local/lib/python3.*/site-packages/psutil*",
         "/usr/local/lib/python3.*/site-packages/ujson*",
         "/usr/local/lib/python3.*/site-packages/defusedxml*"),
    ),
    # ── AI CLI tools, npm "user" mode ────────────────────────────────────────
    Leaf("ai_openclaw", "openclaw", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.local/lib/node_modules/openclaw",)),
    Leaf("ai_copilot", "copilot (@github/copilot)", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.local/lib/node_modules/@github",)),
    Leaf("ai_codex", "codex (standalone installer)", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.codex",)),
    Leaf("ai_opencode", "opencode", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.local/lib/node_modules/opencode-linux-*",)),
    Leaf("ai_reasonix", "reasonix", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.local/lib/node_modules/reasonix",)),
    Leaf("ai_claudelink", "claudelink", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.local/lib/node_modules/claudelink",)),
    Leaf("ai_pi", "pi (@earendil-works/pi-coding-agent)", ("mdt layers", 'AI CLI tools, npm "user" mode'), ("AI coding-agent tools",), ("/home/vscode/.local/lib/node_modules/@earendil-works",)),
    # ── Primary venv + aider ─────────────────────────────────────────────────
    # Aider is its own Dockerfile RUN, right after venv+toolkit.txt creation
    # (see the Dockerfile comment there) — specifically so its own dependency
    # closure (tree-sitter grammars, litellm, tokenizers, ...) is directly
    # visible in docker-history as its own layer, rather than needing a
    # dependency-closure probe to split it back out of a merged one.
    Leaf("venv_general", "general dev toolkit (toolkit.txt)", ("mdt layers", "Primary venv"), ("Runtimes", "Python (venv)")),
    Leaf("mdt_aider", "aider-chat + its transitive dependency closure (own layer)", ("mdt layers", "Aider (venv mode)"), ("AI coding-agent tools",)),
    Leaf("mdt_wheels", "first-party wheels (ciu/cmru/topos/nyxloom)", ("mdt layers", "First-party wheels"), ("First-party wheels",), ("/home/vscode/.venv/lib/python3.*/site-packages/ciu*", "/home/vscode/.venv/lib/python3.*/site-packages/cmru*", "/home/vscode/.venv/lib/python3.*/site-packages/topos*", "/home/vscode/.venv/lib/python3.*/site-packages/nyxloom*")),
    # ── Manifest generation ──────────────────────────────────────────────────
    Leaf("manifest_root_copilot_cache", "/root/.cache/copilot [dead weight — root is never used at runtime]", ("mdt layers", "Manifest generation step"), ("Dead weight / build artifacts",), ("/root/.cache/copilot",)),
]

DU_LEAVES = [leaf for leaf in LEAVES if leaf.du_paths]


# ── Layer categorization (docker history -> named groups) ───────────────────

# (label_path, substring_or_regex) — first match wins. Anything unmatched with
# nonzero size is reported explicitly rather than silently dropped.
LAYER_RULES: list[tuple[tuple[str, ...], str]] = [
    (("Base image", "Debian rootfs & core OS utils"), r"# debian\.sh --arch"),
    (("Base image", "Debian rootfs & core OS utils"), r"ca-certificates\s+curl\s+gnupg\s+netbase"),
    (("Base image", "Debian rootfs & core OS utils"), r"git\s+mercurial\s+openssh-client\s+subversion"),
    (("Base image", "Debian rootfs & core OS utils"), r"install-subversion\.sh"),
    (("Base image", "Build toolchain + Python built from source"), r"libbluetooth-dev\s+tk-dev\s+uuid-dev"),
    (("Base image", "Build toolchain + Python built from source"), r"autoconf\s+automake\s+bzip2"),
    (("Base image", "Build toolchain + Python built from source"), r"wget -O python\.tar\.xz"),
    (("Base image", "Build toolchain + Python built from source"), r"apt-get purge -y imagemagick"),
    (("Base image", "Build toolchain + Python built from source"), r"gitpython=="),
    (("Base image", "VS Code devcontainer Features"), r"build-features-src/python_3"),
    (("Base image", "VS Code devcontainer Features"), r"build-features-src/node_2"),
    (("Base image", "VS Code devcontainer Features"), r"build-features-src/git_1"),
    (("Base image", "VS Code devcontainer Features"), r"build-features-src/common-utils_0"),
    (("mdt layers", "Core system apt packages"), r"sed 's/#\.\*//' /tmp/packages\.list"),
    (("mdt layers", "skopeo"), r"testing\.list"),
    (("mdt layers", "Node.js runtime"), r"deb\.nodesource\.com"),
    (("mdt layers", "Neovim + NvChad"), r"nvim_root="),
    # NOTE: must come before the PHP rule below — this RUN's own body declares
    # a `php_system_packages=(... "php${PHP_VERSION}-cli" ...)` array literal,
    # which would otherwise false-positive match the (much narrower) PHP rule.
    (("mdt layers", '"Modern tools" mega-layer'), r"STAGE_DIR=\"/tmp/tool-artifacts-staging\""),
    (("mdt layers", "PHP 8.5 runtime"), r"php\$\{PHP_VERSION\}-cli|packages\.sury\.org"),
    (("mdt layers", "postgresql-client + redis-tools"), r"postgresql\.org/media/keys"),
    (("mdt layers", "Docker CLI + buildx + compose"), r"download\.docker\.com/linux/debian"),
    (("mdt layers", 'AI CLI tools, npm "user" mode'), r"install_ai_cli_tools\.py user"),
    (("mdt layers", "Primary venv"), r"python3 -m venv /home/vscode/\.venv"),
    (("mdt layers", "Aider (venv mode)"), r"install_ai_cli_tools\.py venv"),
    (("mdt layers", "First-party wheels"), r"find-links=\"\$\{wheels_dir\}\""),
    (("mdt layers", "Manifest generation step"), r"manifest_dir=\"/usr/local/share/modern-debian-tools-python-debug\""),
    (("mdt layers", "misc config/customization copies"), r"zshrc_source=|share_dir=\"/usr/local/share/modern-debian-tools-python-debug\""),
]


def classify_layer(created_by: str) -> tuple[str, ...] | None:
    for path, pattern in LAYER_RULES:
        if re.search(pattern, created_by):
            return path
    return None


# ── Tree building & rendering ────────────────────────────────────────────────


@dataclass
class Node:
    name: str
    children: dict[str, "Node"] = field(default_factory=dict)
    own_bytes: int = 0

    def child(self, name: str) -> "Node":
        return self.children.setdefault(name, Node(name))

    def total(self) -> int:
        # own_bytes (a direct insert AT this exact path) and children (inserts
        # further down) are not mutually exclusive — e.g. a base-image leaf
        # inserted atomically at ("Base OS & build toolchain",) shares that
        # node with a subdivided leaf inserted at
        # ("Base OS & build toolchain", "C/C++ toolchain residual"). Both
        # must count.
        return self.own_bytes + sum(c.total() for c in self.children.values())


def insert(root: Node, path: tuple[str, ...], bytes_value: int) -> None:
    node = root
    for part in path:
        node = node.child(part)
    node.own_bytes += bytes_value


def fmt_bytes(n: int) -> str:
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def render_tree(node: Node, grand_total: int, prefix: str = "", is_root: bool = True) -> list[str]:
    lines: list[str] = []
    entries = sorted(node.children.items(), key=lambda kv: -kv[1].total())
    for idx, (name, child) in enumerate(entries):
        last = idx == len(entries) - 1
        connector = "└─ " if last else "├─ "
        total = child.total()
        pct = (total / grand_total * 100) if grand_total else 0.0
        label = f"{prefix}{connector}{name}"
        lines.append(f"{label:<70} {fmt_bytes(total):>10} {pct:6.1f}%")
        extension = "   " if last else "│  "
        lines.extend(render_tree(child, grand_total, prefix + extension, is_root=False))
    return lines


# ── Main ──────────────────────────────────────────────────────────────────────


def measure(image: str, threshold_pct: float) -> tuple[Node, Node, int, int]:
    grand_total = docker_image_size(image)

    # 1. Layer sizes, classified.
    layer_bytes: dict[tuple[str, ...], int] = {}
    unmatched: list[tuple[int, str]] = []
    for size, created_by in docker_history(image):
        if size == 0:
            continue
        path = classify_layer(created_by)
        if path is None:
            unmatched.append((size, created_by))
            continue
        layer_bytes[path] = layer_bytes.get(path, 0) + size

    # 2. Fine-grained probes: one batched docker run, KEY<TAB>BYTES lines.
    script_lines = [du_leaf_line(leaf) for leaf in DU_LEAVES]
    script_lines.append(
        'PHP_BYTES="$(dpkg-query -W -f=\'${Installed-Size}\\n\' '
        '$(dpkg-query -W -f=\'${Package}\\n\' 2>/dev/null | grep -E "^(php|composer)" ) 2>/dev/null '
        '| awk \'{s+=$1} END {print s*1024}\')"'
    )
    script_lines.append('echo "php_packages\t${PHP_BYTES:-0}"')
    probe_values = run_probe_script(image, "\n".join(script_lines))

    # 3. Fill in directly-probed leaf values.
    for leaf in LEAVES:
        if leaf.key in probe_values:
            leaf.bytes_value = probe_values[leaf.key]
            leaf.present = leaf.bytes_value > 0

    leaves_by_layer_parent: dict[tuple[str, ...], list[Leaf]] = {}
    for leaf in LEAVES:
        leaves_by_layer_parent.setdefault(leaf.layer_path, []).append(leaf)
    leaf_by_key = {leaf.key: leaf for leaf in LEAVES}

    # 4. Named residuals: "this layer's own docker-history total minus the one
    #    thing we bothered to measure precisely" — computed against the raw
    #    per-layer diff, never a live whole-directory du (which could span
    #    bytes a *different* layer already owns).
    def named_residual(layer_path: tuple[str, ...], measured_key: str, residual_key: str) -> None:
        raw = layer_bytes.get(layer_path, 0)
        measured = leaf_by_key[measured_key].bytes_value
        residual_leaf = leaf_by_key[residual_key]
        residual_leaf.bytes_value = max(raw - measured, 0)
        residual_leaf.present = residual_leaf.bytes_value > 0

    named_residual(("Base image", "Build toolchain + Python built from source"), "base_python_build", "base_toolchain_residual")
    named_residual(("mdt layers", "PHP 8.5 runtime"), "php_packages", "php_apt_residual")

    # 5. Build both trees from one shared registry.
    layer_tree = Node("root")
    logical_tree = Node("root")

    manually_handled = {
        ("Base image", "Build toolchain + Python built from source"),
        ("mdt layers", "PHP 8.5 runtime"),
    }
    subdivided_layers = {
        ("mdt layers", '"Modern tools" mega-layer'),
        ("mdt layers", 'AI CLI tools, npm "user" mode'),
        ("mdt layers", "Manifest generation step"),
    }

    for path in manually_handled:
        for c in leaves_by_layer_parent.get(path, []):
            if c.bytes_value > 0:
                insert(layer_tree, path + (c.label,), c.bytes_value)
                insert(logical_tree, c.logical_path + (c.label,), c.bytes_value)

    for path, total_size in layer_bytes.items():
        if path in manually_handled:
            continue
        if path in subdivided_layers:
            children = leaves_by_layer_parent.get(path, [])
            attributed = sum(c.bytes_value for c in children)
            for c in children:
                if c.bytes_value > 0:
                    insert(layer_tree, path + (c.label,), c.bytes_value)
                    insert(logical_tree, c.logical_path + (c.label,), c.bytes_value)
            residual = total_size - attributed
            if residual > 0:
                insert(layer_tree, path + ("(other: not individually itemized)",), residual)
                insert(logical_tree, ("Unclassified", "(other, within: " + " > ".join(path) + ")"), residual)
        else:
            # Atomic 1:1 layer<->leaf mapping — the layer's own docker-history
            # total is the ground truth; forward it whole to both views.
            insert(layer_tree, path, total_size)
            children = leaves_by_layer_parent.get(path, [])
            if len(children) == 1:
                # Always insert one level below the group (never directly at
                # the group's own path) so a group node never mixes own_bytes
                # with children from an unrelated, differently-labeled leaf
                # that happens to share the same logical_path.
                insert(logical_tree, children[0].logical_path + (children[0].label,), total_size)
            elif children:
                # Shouldn't happen given the registry above, but fail loud
                # rather than silently dropping weight from the logical view.
                raise AssertionError(
                    f"{path} has {len(children)} leaves but no probe/residual "
                    "to split its total_size across them"
                )
            else:
                insert(logical_tree, ("Unclassified", path[-1]), total_size)

    for size, created_by in unmatched:
        snippet = created_by.strip()[:80]
        insert(layer_tree, ("(unmatched layers)", snippet), size)
        insert(logical_tree, ("Unclassified", "(unmatched layer)", snippet), size)

    return layer_tree, logical_tree, grand_total, sum(s for s, _ in unmatched)


def du_leaf_line(leaf: Leaf) -> str:
    return f'echo "{leaf.key}\t$({du_sum_script(leaf.du_paths, leaf.du_excludes)})"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--view", choices=("layer", "logical", "both"), default="both")
    parser.add_argument("--threshold", type=float, default=1.0, help="unattributed %% that triggers a warning")
    args = parser.parse_args(argv)

    dockerfile_commit = git_last_commit("Dockerfile")
    script_commit = git_last_commit("scripts/report-image-size-breakdown.py")

    print(f"# Image size breakdown — {args.image}\n")
    if dockerfile_commit:
        print(f"- Dockerfile last changed: `{dockerfile_commit[0]}` ({dockerfile_commit[1]})")
    if script_commit:
        print(f"- Categorization rules (this script) last updated: `{script_commit[0]}` ({script_commit[1]})")
    else:
        print("- Categorization rules (this script): not yet committed — no staleness comparison possible")
    if dockerfile_commit and script_commit and dockerfile_commit[1] > script_commit[1]:
        print(
            "- ⚠ Dockerfile changed AFTER this script's rules — verify the "
            "unattributed total below before trusting the grouping."
        )
    print()

    layer_tree, logical_tree, grand_total, unmatched_bytes = measure(args.image, args.threshold)

    def print_view(title: str, tree: Node) -> None:
        print(f"## {title}\n")
        print("```")
        print(f"{'COMPONENT':<70} {'SIZE':>10} {'% IMAGE':>7}")
        print("-" * 90)
        for line in render_tree(tree, grand_total):
            print(line)
        print("```\n")

    if args.view in ("layer", "both"):
        print_view("By layer", layer_tree)

    if args.view in ("logical", "both"):
        print_view("By logical group", logical_tree)

    attributed = layer_tree.total()
    diff = grand_total - attributed
    reported_diff = max(diff, 0)
    reported_diff_pct = (reported_diff / grand_total * 100) if grand_total else 0.0
    print("```")
    print(f"TOTAL (docker image size):        {fmt_bytes(grand_total)}")
    print(f"Sum of attributed leaves:          {fmt_bytes(attributed)}")
    if diff < 0:
        flag = f" ⚠ ERROR: attribution exceeds image by {fmt_bytes(-diff)} — REVISIT THE REGISTRY ABOVE"
    elif reported_diff_pct > args.threshold:
        flag = " ⚠ REVISIT THE REGISTRY ABOVE"
    else:
        flag = " (OK)"
    print(f"UNATTRIBUTED:                      {fmt_bytes(reported_diff)} ({reported_diff_pct:.1f}%){flag}")
    print("```")
    return 1 if diff < 0 or reported_diff_pct > args.threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
