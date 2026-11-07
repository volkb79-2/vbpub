"""Redaction for the pre-reinstall evidence export (standard library only).

This single file is the only copy of the redaction logic: ``pre-reinstall-export.py``
imports it for the controller-side pass and embeds its source verbatim into the
remote collector, so both passes can never drift apart.

Two independent mechanisms live here on purpose:

* ``redact_text`` / ``redact_json`` rewrite secret-looking values (per line, so
  an empty parent key can never swallow the next line's key).
* ``has_unredacted`` is the fail-closed second pass.  It deliberately shares no
  regex or control flow with the redactor: it tokenises each line, looks for
  secret-ish stems anywhere (substring, case-insensitive) and demands that
  whatever follows an assignment/option separator is the redaction marker.

Shapes the redactor handles (each has a shape test): ``key: value`` / ``key=value``
pairs in any file (quoted JSON keys, camelCase keys), line-start environment
assignments, systemd ``Environment=NAME=value``, YAML children below a secret
parent key or a block scalar, ``chpasswd`` input (YAML block, pipe, heredoc),
URL userinfo for any scheme, ``--password``/``--password=``/``-p`` options,
``Authorization`` headers, bearer tokens, common raw token formats and PEM
private keys.  It is a best-effort filter for an allowlisted evidence set, not a
general secret scanner; the second pass refuses (the file is skipped) when it
still sees an unredacted assignment.
"""

import json
import re

MARKER = "***REDACTED***"
PRIVATE_KEY_MARKER = "***REDACTED PRIVATE KEY***"

# Stems matched as substrings of the normalised key (camelCase split, separators
# removed) and short words matched only as whole words.
LONG_STEMS = (
    "token", "passw", "passph", "passcode", "secret", "credential",
    "authorization", "private", "apikey", "accesskey", "jwt", "psk",
    "presharedkey", "bearer",
)
SHORT_WORDS = ("pass", "pat", "pw", "pwd")

MAX_REDACTION_FILE_BYTES = 1024 * 1024
MAX_REDACTION_LINE_BYTES = 64 * 1024

PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----")
PRIVATE_KEY_END_RE = re.compile(r"-----END [^-\r\n]*PRIVATE KEY-----")
SENSITIVE_PATH_RE = re.compile(
    r"(?i)(?:token|password|passwd|secret|private.?key|credential|api.?key|(?<![a-z0-9])pass(?![a-z0-9])|\.ssh)"
)

_VALUE = r"""(?:"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|[^\s,;}\]"']+)"""
_VALUE_RE = re.compile(_VALUE)
_KEY_AT_RE = re.compile(
    r"""(?<![A-Za-z0-9_.-])(?P<kq>["']?)(?P<key>[A-Za-z0-9_.-]+)(?P=kq)[ \t]*(?P<sep>[:=])[ \t]*"""
)
_ASSIGNMENT_RE = re.compile(
    r"""^(?P<prefix>[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z0-9_.-]+)[ \t]*=[ \t]*)(?P<value>.*)$"""
)
_AUTHORIZATION_RE = re.compile(
    r"""(?i)(?P<prefix>(?<![A-Za-z0-9_.-])["']?authorization["']?[ \t]*[:=][ \t]*)"""
    r"""(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,}\r\n]+)"""
)
_BEARER_RE = re.compile(r"""(?i)(?P<prefix>\bbearer[ \t]+)(?P<value>[^\s,;"']+)""")
_OPTION_RE = re.compile(r"""(?<![A-Za-z0-9_-])--(?P<name>[A-Za-z0-9][A-Za-z0-9_-]*)(?P<sep>=|[ \t]+)""")
_SHORT_P_RE = re.compile(r"""(?<![\w-])-p(?:(?P<space>[ \t]+)|(?=[^\s-]))""")
_URL_USERINFO_RE = re.compile(
    r"""(?i)(?<![A-Za-z0-9+.-])(?P<scheme>[A-Za-z][A-Za-z0-9+.-]{0,31}://)[^\s/?#"']*@"""
)
_WEBHOOK_PATH_RE = re.compile(
    r"""(?i)(?P<prefix>https?://[^/\s?#]+/(?:[^/?#\s]+/)*hooks/)(?P<token>[^/?#\s]+)"""
)
_NETRC_PASSWORD_RE = re.compile(
    r"""(?i)(?<![A-Za-z0-9_-])(?P<prefix>password|passwd)(?P<space>[ \t]+)(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?![=:])[^\s]+)"""
)
_CURL_USER_RE = re.compile(
    r"""(?i)(?<![A-Za-z0-9_-])(?P<prefix>--user(?:[ \t]*=[ \t]*|[ \t]+)|-u(?:[ \t]+|(?=[^ \t])))(?P<quote>["']?)(?P<user>[^:\s"']+):(?P<password>[^,\s"']+)(?P=quote)"""
)
_TOKEN_FORMAT_RE = re.compile(
    r"""\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"""
    r"""|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,})"""
)
_CHPASSWD_PAIR_RE = re.compile(r"""(?P<user>[A-Za-z0-9_.-]+):(?P<pw>[^\s"'|<>]+)""")
_HEREDOC_RE = re.compile(r"""<<-?[ \t]*['"]?(?P<delimiter>[A-Za-z0-9_]+)['"]?""")
_BLOCK_OPENERS = {"{", "[", "|", ">", "|-", ">-", "|+", ">+"}


def _words(key):
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [word for word in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if word]


def key_is_sensitive(key):
    """True when the key name looks like it names a secret (camelCase aware)."""
    words = _words(key)
    joined = "".join(words)
    if any(stem in joined for stem in LONG_STEMS):
        return True
    if joined.endswith(("pass", "pw")):
        return True
    if "webhook" in joined and "url" in joined:
        return True
    return any(word in SHORT_WORDS for word in words)


def _benign_short_p_value(value):
    """``-p`` values that are plainly not passwords: paths, ports, pids, options."""
    return (
        value.startswith(("/", ".", "~", "-"))
        or (value.isdigit() and len(value) <= 5)
    )


def _mask(value):
    if value[:1] in ("'", '"'):
        quote = value[0]
        closed = len(value) > 1 and value[-1] == quote
        return quote + MARKER + (quote if closed else "")
    return MARKER


def _indent(line):
    return len(line) - len(line.lstrip(" \t"))


def _redact_private_key_blocks(text):
    """Mask PEM blocks in one forward pass, keeping incomplete blocks visible."""
    output = []
    pending = []
    inside = False
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]
        position = 0
        if inside:
            end = PRIVATE_KEY_END_RE.search(body, position)
            if end is None:
                pending.append(raw)
                continue
            output.append(PRIVATE_KEY_MARKER)
            pending.clear()
            inside = False
            position = end.end()
        while True:
            start = PRIVATE_KEY_RE.search(body, position)
            if start is None:
                output.append(body[position:] + eol)
                break
            end = PRIVATE_KEY_END_RE.search(body, start.end())
            if end is None:
                output.append(body[position:start.start()])
                pending = [body[start.start():] + eol]
                inside = True
                break
            output.append(body[position:start.start()])
            output.append(PRIVATE_KEY_MARKER)
            position = end.end()
    if pending:
        output.extend(pending)
    return "".join(output)


def _same_indent_sequence(line, indent):
    return len(line) > indent and line[indent] == "-" and (
        len(line) == indent + 1 or line[indent + 1] in " \t"
    )


def _redact_line(line):
    """Return (new_line, opens_block, heredoc_delimiter) for one line (no EOL)."""
    heredoc = None
    opens_block = False
    if re.search(r"(?i)chpasswd", line) and not re.match(r"^[ \t]*chpasswd[ \t]*:", line, re.I):
        line = _CHPASSWD_PAIR_RE.sub(lambda m: m.group("user") + ":" + MARKER, line)
        found = _HEREDOC_RE.search(line)
        if found:
            heredoc = found.group("delimiter")
    line = _NETRC_PASSWORD_RE.sub(
        lambda m: m.group("prefix") + m.group("space") + _mask(m.group("value")),
        line,
    )
    line = _CURL_USER_RE.sub(
        lambda m: m.group("prefix") + m.group("quote") + m.group("user") + ":" + MARKER + m.group("quote"),
        line,
    )
    line = _URL_USERINFO_RE.sub(lambda m: m.group("scheme"), line)
    line = _WEBHOOK_PATH_RE.sub(lambda m: m.group("prefix") + MARKER, line)
    line = _TOKEN_FORMAT_RE.sub(MARKER, line)
    line = _AUTHORIZATION_RE.sub(lambda m: m.group("prefix") + MARKER, line)
    line = _BEARER_RE.sub(lambda m: m.group("prefix") + MARKER, line)

    assignment = _ASSIGNMENT_RE.match(line)
    if assignment and key_is_sensitive(assignment.group("key")) and assignment.group("value").strip():
        line = assignment.group("prefix") + MARKER

    # Long options: --password S1, --password=S1, --db-password S1
    out = []
    position = 0
    while True:
        found = _OPTION_RE.search(line, position)
        if not found:
            break
        out.append(line[position:found.end()])
        position = found.end()
        if not key_is_sensitive(found.group("name")):
            continue
        value = _VALUE_RE.match(line, found.end())
        if value is None or (value.group().startswith("-") and found.group("sep") != "="):
            continue
        out.append(_mask(value.group()))
        position = value.end()
    out.append(line[position:])
    line = "".join(out)

    # Short -p PASSWORD / -pPASSWORD (mysql, sshpass, ...)
    out = []
    position = 0
    while True:
        found = _SHORT_P_RE.search(line, position)
        if not found:
            break
        out.append(line[position:found.end()])
        position = found.end()
        value = _VALUE_RE.match(line, found.end())
        if value is None or _benign_short_p_value(value.group().strip("'\"")):
            continue
        out.append(_mask(value.group()))
        position = value.end()
    out.append(line[position:])
    line = "".join(out)

    # key: value / key=value pairs; non-secret keys are scanned THROUGH so a
    # secret nested in their value (Environment=NAME=value) is still found.
    out = []
    position = 0
    while True:
        found = _KEY_AT_RE.search(line, position)
        if not found:
            break
        out.append(line[position:found.end()])
        position = found.end()
        if not key_is_sensitive(found.group("key")):
            continue
        rest = line[found.end():]
        value = _VALUE_RE.match(line, found.end())
        if value is None:
            if found.group("sep") == ":" and (not rest.strip() or rest.lstrip().startswith("#")):
                opens_block = True
            continue
        if value.group() in _BLOCK_OPENERS and not line[value.end():].strip():
            opens_block = True
        if value.group() in ("{", "[") and line[value.end():].strip():
            # single-line JSON-ish object/array under a secret key: mask the rest of the line
            out.append(MARKER)
            position = len(line)
            break
        value_start = found.end()
        while value_start < len(line) and line[value_start] in " \t":
            value_start += 1
        if value_start < len(line) and line[value_start] not in "\"'":
            out.append(MARKER)
            position = len(line)
            break
        out.append(_mask(value.group()))
        position = value.end()
    out.append(line[position:])
    return "".join(out), opens_block, heredoc


def redact_text(text):
    """Redact secret-looking values in free text (per line, with block state)."""
    text = _redact_private_key_blocks(text)
    output = []
    block_indent = None
    heredoc = None
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]
        stripped = body.strip()
        indent = _indent(body)
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
                output.append(raw)
            else:
                output.append(body[:indent] + MARKER + eol)
            continue
        if block_indent is not None:
            if not stripped:
                output.append(raw)
                continue
            if indent > block_indent:
                output.append(body[:indent] + MARKER + eol)
                continue
            if indent == block_indent and _same_indent_sequence(body, indent):
                dash_end = indent + 1
                while dash_end < len(body) and body[dash_end] in " \t":
                    dash_end += 1
                output.append(body[:dash_end] + MARKER + eol)
                continue
            block_indent = None
        new, opens_block, delimiter = _redact_line(body)
        if opens_block:
            block_indent = indent
        if delimiter:
            heredoc = delimiter
        output.append(new + eol)
    return "".join(output)


def redact_json(value, key=""):
    if key and key_is_sensitive(key):
        return MARKER
    if isinstance(value, dict):
        return {k: redact_json(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


# ---------------------------------------------------------------------------
# Second pass: independent, tokenising, fail-closed.  No regex or helper of the
# redactor above is reused except the marker constants and the benign -p rule.
# ---------------------------------------------------------------------------

_STEM_SCAN = re.compile(
    r"(?i)token|passw|passph|passcode|secret|credential|authorization|private|api[-_]?key|access[-_]?key|jwt|psk|preshared[-_]?key|bearer|webhook[-_]?url"
    r"|(?<![A-Za-z0-9])(?:pass|pat|pw|pwd)(?![A-Za-z0-9])|(?-i:(?<=[a-z0-9])(?:Pass|Pat|Pw|Pwd)(?![a-z]))"
    r"|(?i:[A-Za-z0-9_.-]+(?:pass|pw|pwd))(?![A-Za-z0-9])"
)


def _is_marker(rest):
    return rest.lstrip("\"'").startswith(MARKER)


def _is_marker_at(line, index):
    while index < len(line) and line[index] in " \t\"'":
        index += 1
    return line.startswith(MARKER, index)


def _stem_leaks(line):
    for token in re.finditer(r"[A-Za-z0-9_.-]+", line):
        if not _STEM_SCAN.search(token.group()):
            continue
        index = token.end()
        if index < len(line) and line[index] in "\"'":
            index += 1
        spaces_end = index
        while spaces_end < len(line) and line[spaces_end] in " \t":
            spaces_end += 1
        if spaces_end < len(line) and line[spaces_end] in ":=":
            value_start = spaces_end + 1
            while value_start < len(line) and line[value_start] in " \t":
                value_start += 1
            if value_start < len(line) and line[value_start] != "#" and not _is_marker_at(line, value_start):
                return True
            continue
        # option / bearer forms: whitespace then a value
        if spaces_end > index and spaces_end < len(line):
            word = token.group().lower()
            optionish = word.startswith("--") and (
                token.start() == 0 or line[token.start() - 1].isspace()
            )
            optionish = optionish or word == "bearer"
            if optionish and line[spaces_end] != "-" and not _is_marker_at(line, spaces_end):
                return True
    return False


def _short_p_leaks(line):
    tokens = line.split()
    for position, token in enumerate(tokens):
        if token == "-p" and position + 1 < len(tokens):
            value = tokens[position + 1]
            if not _benign_short_p_value(value.strip("'\"")) and not _is_marker(value):
                return True
        elif token.startswith("-p") and len(token) > 2 and not token.startswith("--"):
            value = token[2:]
            if not _benign_short_p_value(value.strip("'\"")) and not _is_marker(value):
                return True
    return False


def _netrc_password_leaks(line):
    words = line.split()
    for index, word in enumerate(words[:-1]):
        if word.lower() not in ("password", "passwd"):
            continue
        value = words[index + 1].strip("\"'")
        if value not in (":", "=") and value and not _is_marker(value):
            return True
    return False


def _curl_user_leaks(line):
    words = line.split()
    index = 0
    while index < len(words):
        option = words[index]
        value = None
        lowered = option.lower()
        if lowered in ("-u", "--user") and index + 1 < len(words):
            index += 1
            value = words[index]
        elif lowered.startswith("--user="):
            value = option.split("=", 1)[1]
        elif lowered.startswith("-u") and not lowered.startswith("--") and len(option) > 2:
            value = option[2:]
        if value is not None:
            username_password = value.strip("\"'")
            _username, separator, password = username_password.partition(":")
            if separator and password and not _is_marker(password):
                return True
        index += 1
    return False


def has_unredacted(text):
    """True when the (already redacted) text still looks like it carries a secret."""
    if "PRIVATE KEY-----" in text and re.search(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----", text):
        return True
    block_indent = None
    heredoc = None
    for raw in text.splitlines():
        stripped = raw.strip()
        indent = _indent(raw)
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
            elif stripped and not _is_marker(stripped):
                return True
            continue
        if block_indent is not None:
            same_indent_sequence = indent == block_indent and stripped.startswith("-") and (
                len(stripped) == 1 or stripped[1] in " \t"
            )
            if stripped and (indent > block_indent or same_indent_sequence):
                child = stripped[1:].strip() if same_indent_sequence else stripped
                if child and not _is_marker(child):
                    return True
                continue
            if stripped:
                block_indent = None
        if re.search(r"://[^\s/?#\"']*@", raw):
            return True
        if re.search(
            r"(?i)https?://[^/\s?#]+/(?:[^/?#\s]+/)*hooks/(?!\*{3}REDACTED\*{3})[^/?#\s]+",
            raw,
        ):
            return True
        if re.search(
            r"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_|glpat-|xox[abprs]-|AKIA|sk-)[A-Za-z0-9_-]{8,}", raw
        ):
            return True
        if "chpasswd" in raw.lower():
            for piece in raw.replace("|", " ").split():
                _user, colon, password = piece.strip("'\"").partition(":")
                if colon and password and password != MARKER and "/" not in piece and "=" not in piece:
                    return True
            found = re.search(r"<<-?\s*['\"]?([A-Za-z0-9_]+)", raw)
            if found:
                heredoc = found.group(1)
        if (
            _stem_leaks(raw)
            or _short_p_leaks(raw)
            or _netrc_password_leaks(raw)
            or _curl_user_leaks(raw)
        ):
            return True
        # a secret-named parent (empty, block scalar or masked) opens a block whose
        # deeper lines must all be masked
        name, separator, value = stripped.split("#", 1)[0].partition(":")
        if separator and _STEM_SCAN.search(name) and (
            value.strip() in _BLOCK_OPENERS | {"", MARKER, '"' + MARKER + '"'}
        ):
            block_indent = indent
    return False


# ---------------------------------------------------------------------------
# File-level entry point
# ---------------------------------------------------------------------------

def strip_jsonc_comments(text):
    output = []
    index = 0
    in_string = escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                if text[index] in "\r\n":
                    output.append(text[index])
                index += 1
            index = min(len(text), index + 2)
        else:
            output.append(char)
            index += 1
    return "".join(output)


def _check_redaction_budgets(name, content):
    if len(content) > MAX_REDACTION_FILE_BYTES:
        raise ValueError(
            f"file exceeds the {MAX_REDACTION_FILE_BYTES}-byte redaction limit: {name}"
        )
    for line in content.splitlines():
        if len(line) > MAX_REDACTION_LINE_BYTES:
            raise ValueError(
                f"line exceeds the {MAX_REDACTION_LINE_BYTES}-byte redaction limit: {name}"
            )


def redact_bytes(name, content):
    """Return redacted bytes, or raise ValueError (the caller must skip the file)."""
    if SENSITIVE_PATH_RE.search(name):
        raise ValueError("sensitive export path refused: " + name)
    _check_redaction_budgets(name, content)
    if b"\x00" in content:
        raise ValueError("binary NUL content refused: " + name)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("non-text content refused: " + name) from exc

    try:
        parsed = json.loads(strip_jsonc_comments(text))
    except json.JSONDecodeError:
        pass
    else:
        cleaned = (json.dumps(redact_json(parsed), indent=2, sort_keys=True) + "\n").encode()
        if has_unredacted(cleaned.decode("utf-8")):
            raise ValueError("unredacted secret-like value remains in " + name)
        return cleaned

    if name.endswith(".jsonl"):
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.dumps(redact_json(json.loads(strip_jsonc_comments(line))), sort_keys=True))
            except json.JSONDecodeError:
                rows.append(redact_text(line))
        text = "\n".join(rows) + ("\n" if rows else "")
    else:
        text = redact_text(text)
    if has_unredacted(text):
        raise ValueError("unredacted secret-like value remains in " + name)
    return text.encode()
