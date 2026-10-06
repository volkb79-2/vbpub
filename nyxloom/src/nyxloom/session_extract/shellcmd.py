"""Mechanical, quote-aware analysis of a Bash command line, shared by the
external-effects ledger (ledger.py), the `--effect-calls` / `--read-calls`
classification (adapters/claude_code.py) and nothing else.

Zero LLM calls, no execution, no filesystem access: a regex/tokenizer over
the command text. It answers two heuristic questions about a command:

  * which of its shell SEGMENTS are external effects (MUTATING forms only:
    `git push`, `systemctl restart`, `docker rm`, a mutating `curl`, a netcup
    `nc.py ... create`, ...; an `ssh`/`scp` counts only when its REMOTE
    command is such a form, or the `scp` uploads), and
  * whether every segment is a read-only orientation command.

Segmentation is quote-aware: `;`, `&&`, `||`, `|`, `&`, newlines, `(`, `)`
and backticks split segments only OUTSIDE quotes, so
`grep "x; git push" f` and `git commit -m "... && ssh h rm"` are ONE segment
whose head is `grep` / `git commit` and neither is an effect. Wrappers
(`sudo`, `nice`, `ionice`, `timeout`, `time`, `nohup`, `exec`, `command`,
`env X=1`, `xargs`, leading `VAR=x`, absolute paths like `/usr/bin/git`) are
stripped before the segment is matched, and `bash -c '...'` / `sh -c '...'`
and `ssh host '...'` are recursed into (bounded depth).

KNOWN RESIDUALS (documented, filed as backlog NL-35): a command whose head is
hidden behind shell-variable indirection (`NC="python3 nc.py"; $NC ... delete`,
`$SSH host ...`), shell functions/aliases, `eval`, scripts that push
internally, commands built by command substitution, and a mutating flag
placed inside a quoted `curl` argument. Those are NOT detected (a missed
effect); a user-supplied `--effect-pattern` can add them.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable

_GIT = r"git(?:\s+(?:-[Cc]\s+\S+|--[\w-]+(?:=\S+)?|-P))*"
_NC = r"^(?:python3?\s+)?(?:\S*/)?(?:nc\.py|scp-api)\b"

# MUTATING forms only (operator ruling 2026-10-06, D2): each is matched
# (re.search, case-insensitive) against one normalised shell segment.
DEFAULT_EFFECT_PATTERNS: tuple[str, ...] = (
    rf"^{_GIT}\s+(?:push|merge(?!-)|rebase|reset\s+--hard)\b",
    rf"^{_GIT}\s+tag\b(?!.*\s(?:-l|--list)\b)",
    r"^systemctl(?:\s+-\S+)*\s+(?:start|stop|restart|reload|enable|disable|mask|unmask|kill|"
    r"reboot|poweroff|isolate|daemon-reload)\b",
    r"^apt(?:-get)?(?:\s+-\S+)*\s+(?:install|remove|purge|upgrade|full-upgrade|dist-upgrade|"
    r"autoremove)\b",
    r"^dpkg(?:\s+\S+)*?\s+(?:-i|-r|-P|--install|--remove|--purge)(?:\s|$)",
    r"^docker\s+(?:container\s+)?(?:rm|stop|kill|run|restart)\b",
    r"^docker\s+(?:volume|network|image)\s+rm\b",
    r"^curl\b.*\s(?:-X|--request)[\s=]*[\"']?(?:POST|PUT|PATCH|DELETE)\b",
    r"^curl\b.*\s(?:-d|--data\S*|--json|-F|--form\S*|-T|--upload-file)(?:\s|=|$)",
    rf"{_NC}.*\s(?:create|delete|attach-iso|power|install-host)(?:\s|$)",
    rf"{_NC}.*\sboot-order\s+set\b",
)

_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.DOTALL)

_ASSIGN = r"[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S*)"
_WRAPPER_RES = (
    re.compile(r"^(?:do|then|else|elif|if|while|until|!|\{)\s+"),
    re.compile(rf"^{_ASSIGN}\s+"),
    re.compile(r"^sudo(?:\s+-\S+)*\s+"),
    re.compile(r"^nice(?:\s+-n\s*\d+|\s+-\d+)?\s+"),
    re.compile(r"^ionice(?:\s+-c\s*\d+)?(?:\s+-n\s*\d+)?\s+"),
    re.compile(r"^timeout(?:\s+-\S+)*\s+\d+[smhd]?\s+"),
    re.compile(r"^(?:time|nohup|exec|command|builtin)(?:\s+-p)?\s+"),
    re.compile(rf"^env(?:\s+(?:-u\s+\S+|-\S+|{_ASSIGN}))*\s+"),
    re.compile(r"^xargs(?:\s+-\S+)*\s+"),
)
# A directory prefix on the FIRST word only (`/usr/bin/git push`, `./nc.py x`).
_DIRPREFIX_RE = re.compile(r"^[^\s'\"]*/(?=[^\s/'\"]+(?:\s|$))")
_SHELL_C_RE = re.compile(r"^(?:ba|z|da|k)?sh\s+-\S*c\S*\s+(?P<arg>.+)$", re.DOTALL)

_WRAPPER_PASSES = 12
_MAX_DEPTH = 3

_SSH_OPTS_WITH_ARG = frozenset("bcDEeFIiJLlmOopQRSWw")
_SCP_OPTS_WITH_ARG = frozenset("cFiJloPS")


def split_segments(command: str) -> list[str]:
    """Split `command` into shell segments at UNQUOTED `;`, `&&`, `||`, `|`,
    `&`, newlines, parentheses and backticks. Heredoc bodies are dropped.
    Quotes stay in the returned text (callers match on it)."""
    command = _HEREDOC_RE.sub("", command)
    segments: list[str] = []
    buf: list[str] = []
    quote = ""
    skip = False

    def flush() -> None:
        text = "".join(buf).strip()
        if text:
            segments.append(text)
        buf.clear()

    for i, ch in enumerate(command):
        if skip:
            buf.append(ch)
            skip = False
            continue
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"':
                skip = True
            elif ch == quote:
                quote = ""
            continue
        if ch == "\\":
            buf.append(ch)
            skip = True
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in ";\n()`|":
            flush()
        elif ch == "&":
            prev = command[i - 1] if i else ""
            nxt = command[i + 1: i + 2]
            if prev in ("<", ">") or nxt == ">":
                buf.append(ch)
            else:
                flush()
        else:
            buf.append(ch)
    flush()
    return segments


def strip_wrappers(segment: str) -> str:
    """Strip leading keywords, `VAR=x`, sudo/nice/ionice/timeout/time/env/
    command/xargs wrappers and a directory prefix on the first word."""
    seg = segment.strip()
    for _ in range(_WRAPPER_PASSES):
        before = seg
        for rx in _WRAPPER_RES:
            seg = rx.sub("", seg, count=1).strip()
        seg = _DIRPREFIX_RE.sub("", seg, count=1)
        if seg == before:
            break
    return seg


def normalized_segments(command: str, depth: int = 0) -> list[str]:
    """Wrapper-stripped segments of `command`, with `bash -c '...'` bodies
    expanded in place (bounded depth)."""
    out: list[str] = []
    for raw in split_segments(command):
        seg = strip_wrappers(raw)
        if not seg:
            continue
        m = _SHELL_C_RE.match(seg)
        if m is not None and depth < _MAX_DEPTH:
            out.extend(normalized_segments(_first_arg(m.group("arg")), depth + 1))
            continue
        out.append(seg)
    return out


def _first_arg(arg: str) -> str:
    try:
        parts = shlex.split(arg)
    except ValueError:
        return arg.strip("'\" ")
    return parts[0] if parts else ""


def _positionals(tokens: list[str], opts_with_arg: frozenset[str]) -> list[str]:
    """Non-option tokens of an ssh/scp argv (after the program name)."""
    out: list[str] = []
    skip_next = False
    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
        elif tok.startswith("-") and len(tok) > 1:
            skip_next = len(tok) == 2 and tok[1] in opts_with_arg
        else:
            out.append(tok)
    return out


def ssh_remote_command(segment: str) -> str | None:
    """The remote command of an `ssh [opts] host cmd...` segment (None when
    there is none, or the segment is not parseable)."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    pos = _positionals(tokens, _SSH_OPTS_WITH_ARG)
    rest = pos[1:]
    if not rest:
        return None
    return rest[0] if len(rest) == 1 else shlex.join(rest)


def is_scp_upload(segment: str) -> bool:
    """`scp ... host:path` -- the LAST operand is remote (an upload)."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    pos = _positionals(tokens, _SCP_OPTS_WITH_ARG)
    return len(pos) >= 2 and ":" in pos[-1].split("/", 1)[0]


def _head(segment: str) -> str:
    parts = segment.split(None, 1)
    return parts[0] if parts else ""


def _segment_is_effect(
    seg: str, compiled: list[re.Pattern[str]], scp_uploads: bool, depth: int,
) -> bool:
    head = _head(seg)
    if head == "ssh" and depth < _MAX_DEPTH:
        remote = ssh_remote_command(seg)
        if remote is not None and any(
            _segment_is_effect(s, compiled, scp_uploads, depth + 1)
            for s in normalized_segments(remote)
        ):
            return True
    if head == "scp" and scp_uploads and is_scp_upload(seg):
        return True
    return any(rx.search(seg) for rx in compiled)


def effect_segments(
    command: str,
    patterns: Iterable[str] = DEFAULT_EFFECT_PATTERNS,
    scp_uploads: bool = True,
) -> list[str]:
    """The normalised shell segments of `command` that are external effects
    (empty = none). `scp_uploads=False` drops the built-in scp-upload rule
    (`--no-default-effect-patterns`)."""
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    return [s for s in normalized_segments(command) if _segment_is_effect(s, compiled, scp_uploads, 0)]


_SAFE_REDIRECT_RE = re.compile(r"\d*>&\d+|\d*>>?\s*/dev/null|&>\s*/dev/null")
_READ_HEADS = frozenset({
    "ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "stat", "file", "pwd",
    "echo", "printf", "test", "[", "which", "type", "du", "df", "ps", "id", "whoami", "uname",
    "date", "sort", "uniq", "cut", "tr", "awk", "jq", "diff", "cmp", "sha256sum", "md5sum",
    "realpath", "basename", "dirname", "readlink", "tree", "nproc", "free", "uptime", "hostname",
    "true", "false", "sleep", "cd", "journalctl", "apt-cache", "sed", "find", "lsblk",
    "ss", "getent", "env", "printenv", "nl", "column", "xxd", "od", "less", "more",
})
_READ_RES = tuple(re.compile(p) for p in (
    rf"^{_GIT}\s+(?:status|log|diff|show|rev-parse|ls-files|ls-tree|merge-base|blame|describe|"
    r"rev-list|cat-file|shortlog|grep|remote\s+-v|config\s+--get|worktree\s+list|stash\s+list|"
    r"branch(?!\s+-[dDmMcC]))\b",
    r"^docker\s+(?:ps|logs|inspect|images|stats|top|port|version|info|volume\s+ls|network\s+ls|"
    r"compose\s+ps)\b",
    r"^systemctl(?:\s+-\S+)*\s+(?:status|is-active|is-enabled|is-failed|show|cat|list-\S+)\b",
    r"^dpkg\s+(?:-l|-s|-L|-S|--list|--status|--listfiles)\b",
    r"^apt(?:-get)?\s+(?:list|show|policy|search)\b",
    r"^pip3?\s+(?:list|show|freeze)\b",
))
_UNSAFE_ARGS = {
    "sed": frozenset({"-i", "--in-place"}),
    "find": frozenset({"-exec", "-execdir", "-delete", "-ok", "-fprint", "-fprintf"}),
}


def _segment_is_read_only(seg: str, depth: int) -> bool:
    if ">" in _SAFE_REDIRECT_RE.sub("", seg):
        return False
    tokens = seg.split()
    head = tokens[0]
    if head == "ssh" and depth < _MAX_DEPTH:
        remote = ssh_remote_command(seg)
        if remote is None:
            return False
        parts = normalized_segments(remote)
        return bool(parts) and all(_segment_is_read_only(s, depth + 1) for s in parts)
    if head in _READ_HEADS:
        unsafe = _UNSAFE_ARGS.get(head, frozenset())
        return not any(t in unsafe for t in tokens[1:])
    return any(rx.search(seg) for rx in _READ_RES)


def is_read_only(command: str) -> bool:
    """Every segment of `command` is a read-only orientation command."""
    parts = normalized_segments(command)
    return bool(parts) and all(_segment_is_read_only(s, 0) for s in parts)
