"""Read a Go module's own path out of its ``go.mod`` -- and nothing else.

This is the DERIVE half of A-404 (DA-8). A Go cover profile keys every record
by the package's IMPORT path (``<module path>/<dir>/<file>.go``) while
``git diff`` names the same file relative to the repository top, so something
must supply the module path before the two spellings can be joined.
``DESIGN-GUIDE.md`` §5 already names covergate's own ``-module srdm`` flag as
defaults anti-pattern #1 -- "a literal standing in for a fact that lives
authoritatively in the project's layout ... A library cannot ship any of them;
it must read them" -- so assay reads it, from the one file that owns it.

# Deliberately NOT a ``go.mod`` parser

Only the ``module`` directive is recognised. ``go``, ``toolchain``,
``require``, ``exclude``, ``replace``, ``retract``, ``tool``, ``godebug`` and
``ignore`` are skipped as ordinary tokens; nothing here understands semantic
versions, replacement targets or block semantics beyond the one factored form
the ``module`` directive itself may take. A general parser would be a second
implementation of `golang.org/x/mod/modfile` that assay cannot keep in step
with a toolchain it does not ship, and every directive it understood would be
a fact assay had no use for.

# The grammar, and where it was read from

The Go Modules Reference gives the directive as::

    ModuleDirective = "module" ( ModulePath | "(" newline ModulePath newline ")" ) newline .

and the lexical rules below were read directly out of the toolchain that
produces the profiles this join has to match -- `go1.25.14`'s own vendored
copy, ``/usr/local/go/src/cmd/vendor/golang.org/x/mod/modfile/{read.go,
rule.go}``, inside ``tester-unified-go:local`` (A-334: the real thing, not a
recollection of it). Four rules are load-bearing here and each is transcribed,
not guessed:

* **Comments are ``//`` only.** ``read.go``'s scanner emits ``_EOLCOMMENT``
  for ``//`` and calls ``in.Error("mod files must use // comments (not /* */
  comments)")`` on ``/*`` -- in two places, once between tokens and once
  *inside* an identifier scan. So ``/*`` is a syntax error in a ``go.mod``,
  not a comment, and this module refuses it rather than stripping it.
* **An identifier ends at ``//``.** The identifier loop breaks on
  ``in.peekPrefix("//")``, which is why ``module example.com/x // a comment``
  yields the path without the comment while ``example.com/x`` itself keeps
  every ``/`` it contains.
* **``isIdent`` is a NEGATIVE rule**: every printable, non-space rune is an
  identifier rune except ``(``, ``)``, ``[``, ``]``, ``{``, ``}`` and ``,``.
  A bare module path is therefore an ordinary identifier token.
* **Only ``"``-quoted strings are accepted as a directive argument.**
  ``rule.go``'s ``parseString`` unquotes a ``"``-prefixed token with
  ``strconv.Unquote`` and REFUSES anything else containing a quote character
  ("unquoted string cannot contain quote"). The lexer does scan a backquoted
  token, but ``parseString`` then rejects it, so a backquoted module path is
  not valid input to ``cmd/go`` and is not accepted here either.

``module`` also appears in ``rule.go``'s list of verbs that may be written in
the factored ``module ( ... )`` form, and ``f.add`` refuses it with "usage:
module module/path" unless it carries exactly one argument. Both are honoured.

# Escapes

A ``"``-quoted argument is unquoted for ``\\\\`` and ``\\"`` only; every other
backslash escape raises rather than being decoded or passed through. The Go
Modules Reference restricts a module path's characters to ASCII letters,
digits and ``-``, ``.``, ``_``, ``~``, ``+``, ``/``, so no escape can appear in
a legitimate one -- and a narrow decoder that quietly mis-decoded ``\\n`` into
a newline would produce a prefix that silently matches nothing, which is the
failure this whole seam exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from .. import safeio
from ..errors import AssayError, Outcome, ReasonCode

__all__ = [
    "MAX_GO_MOD_BYTES",
    "ModuleDeclaration",
    "find_module_declaration",
    "parse_module_directive",
]

#: Read ceiling for a ``go.mod``. Generous by three orders of magnitude for
#: the file's real shape (srdm's own is under 2 KiB) and present for the same
#: reason every other read in this project is bounded: an input assay does not
#: produce must not be able to size assay's own memory.
MAX_GO_MOD_BYTES = 1024 * 1024

#: The punctuation ``read.go``'s ``isIdent`` excludes, verbatim.
_PUNCTUATION = frozenset("()[]{},")

_MODULE_FILE_NAME = "go.mod"


@dataclass(frozen=True)
class ModuleDeclaration:
    """A module path together with the ``go.mod`` it was read out of.

    The provenance is not decoration: A-404 (c) requires the refusal for a
    profile key outside the module to NAME the file whose ``module`` directive
    produced the prefix, because the consumer's next action is to open it.
    """

    #: The ``module`` directive's argument, unquoted, with no trailing slash.
    module_path: str
    #: The ``go.mod``'s own repo-top-relative POSIX path.
    module_file: str


def _refuse(message: str) -> AssayError:
    """Every refusal in this module is the same pair, and the choice is
    recorded in A-404 (b): a lane that declares a Go judge over a directory
    whose module cannot be established is a lane/tree mismatch, which is what
    ``BAD_LANE_CONFIG`` already names (``evaluate.py``'s own "project root is
    not contained by its repository" refusal is the same shape). It is
    deliberately NOT ``UNREADABLE_ARTIFACT``: nothing is wrong with the
    coverage artifact, and blaming it would send a consumer to re-run their
    tests over a lane-configuration fault.
    """
    return AssayError(
        message, outcome=Outcome.ERROR, reason_code=ReasonCode.BAD_LANE_CONFIG
    )


def find_module_declaration(
    repo_top: Path, project_root: Path
) -> ModuleDeclaration:
    """The Go module *project_root* belongs to, read from the nearest
    ``go.mod`` at or above it and no higher than *repo_top*.

    Searched nearest-first so a monorepo lane whose ``cwd`` is one module's
    root picks that module and not an ancestor's -- and stopped at *repo_top*
    because a ``go.mod`` outside the repository under judgment is not part of
    the tree the verdict is about (the same containment rule
    :func:`assay.evaluate.resolve_coverage_keys` already applies to
    *project_root* itself).

    Raises ``ERROR``/``BAD_LANE_CONFIG`` when *project_root* is not contained
    by *repo_top*, when no ``go.mod`` exists in that range at all, or when the
    one found carries no usable ``module`` directive. There is no default to
    fall back to: an empty module path means "strip nothing", which is exactly
    the silent wrong answer B057 measured.
    """
    top = repo_top.resolve()
    start = project_root.resolve()
    try:
        relative = start.relative_to(top)
    except ValueError:
        raise _refuse(
            f"the project root {project_root} is not contained by its own "
            f"repository top {repo_top}, so the Go module it belongs to "
            f"cannot be established"
        ) from None

    searched: list[str] = []
    for directory in _self_and_ancestors(PurePosixPath(relative.as_posix())):
        candidate = (
            _MODULE_FILE_NAME
            if directory == PurePosixPath(".")
            else str(directory / _MODULE_FILE_NAME)
        )
        searched.append(candidate)
        raw = safeio.read_bounded_file(top, candidate, limit=MAX_GO_MOD_BYTES)
        if raw is None:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _refuse(
                f"{candidate!r} is not valid UTF-8 and its `module` directive "
                f"could not be read: {exc}"
            ) from exc
        return ModuleDeclaration(
            module_path=parse_module_directive(text, source=candidate),
            module_file=candidate,
        )

    raise _refuse(
        f"no {_MODULE_FILE_NAME} exists at or above the lane's own working "
        f"directory (searched {', '.join(repr(name) for name in searched)} "
        f"under {top}), so this lane's Go module path cannot be derived -- a "
        f"lane declaring judge.language = \"go\" must have its `cwd` at, or "
        f"inside, a Go module"
    )


def _self_and_ancestors(relative: PurePosixPath) -> Iterator[PurePosixPath]:
    """*relative* then each of its parents, nearest first, ending at ``.``."""
    current = relative
    while True:
        yield current
        if current == PurePosixPath("."):
            return
        current = current.parent


def parse_module_directive(text: str, *, source: str) -> str:
    """The single ``module`` directive's argument in *text*, unquoted.

    *source* names the file for the refusal messages only. Raises
    ``ERROR``/``BAD_LANE_CONFIG`` for a file with no ``module`` directive, one
    whose directive carries no argument, one declaring an empty path, or input
    the real lexer rejects (``/* */``, a backquoted argument).
    """
    tokens = list(_tokens(text, source=source))
    at_line_start = True
    depth = 0
    for index, (kind, value) in enumerate(tokens):
        if kind == "newline":
            at_line_start = True
            continue
        if kind == "punct" and value in "([{":
            depth += 1
        elif kind == "punct" and value in ")]}":
            depth = max(0, depth - 1)
        elif at_line_start and depth == 0 and kind == "ident" and value == "module":
            # Only a VERB position counts. Inside another directive's factored
            # block (`require ( ... )`) a line also starts with an argument
            # token, and matching one there would read some dependency's path
            # as this module's own.
            return _module_argument(tokens, index + 1, source=source)
        at_line_start = False

    raise _refuse(
        f"{source!r} carries no `module` directive, so the module path a Go "
        f"coverage profile's keys are prefixed with cannot be read from it"
    )


def _module_argument(
    tokens: list[tuple[str, str]], index: int, *, source: str
) -> str:
    """The directive's one argument, in either form the grammar allows."""
    if index < len(tokens) and tokens[index] == ("punct", "("):
        # The factored form. `rule.go` lists "module" among the verbs that may
        # be written as a LineBlock, and `f.add` then refuses any line not
        # carrying exactly one argument ("usage: module module/path"), so
        # exactly one argument on its own line is the whole of what is legal.
        index += 1
        while index < len(tokens) and tokens[index][0] == "newline":
            index += 1
        argument = _argument_at(tokens, index, source=source)
        index += 1
        while index < len(tokens) and tokens[index][0] == "newline":
            index += 1
        if index >= len(tokens) or tokens[index] != ("punct", ")"):
            raise _refuse(
                f"{source!r}'s factored `module ( ... )` block does not close "
                f"after a single module path; `module` takes exactly one "
                f"argument"
            )
        return argument
    return _argument_at(tokens, index, source=source)


def _argument_at(
    tokens: list[tuple[str, str]], index: int, *, source: str
) -> str:
    if index >= len(tokens) or tokens[index][0] not in ("ident", "string"):
        raise _refuse(
            f"{source!r}'s `module` directive carries no module path; the "
            f"directive is `module <module/path>`"
        )
    kind, value = tokens[index]
    path = _unquote(value, source=source) if kind == "string" else value
    if not path:
        raise _refuse(
            f"{source!r} declares an empty module path; an empty prefix "
            f"strips nothing, which is indistinguishable from having no "
            f"module path at all"
        )
    return path.rstrip("/")


def _unquote(token: str, *, source: str) -> str:
    """Decode a ``"``-quoted directive argument.

    Backquoted arguments are refused, not decoded: ``rule.go``'s
    ``parseString`` unquotes only a ``"``-prefixed token and errors on any
    other token containing a quote character, so ``cmd/go`` itself does not
    accept one.
    """
    if token.startswith("`"):
        raise _refuse(
            f"{source!r}'s `module` directive uses a backquoted argument, "
            f"which the Go toolchain's own go.mod parser refuses "
            f"(golang.org/x/mod/modfile: \"unquoted string cannot contain "
            f"quote\"); use the bare or double-quoted form"
        )
    body = token[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(body):
            raise _refuse(
                f"{source!r}'s `module` directive ends in a trailing "
                f"backslash"
            )
        escape = body[index + 1]
        if escape not in ('"', "\\"):
            raise _refuse(
                f"{source!r}'s `module` directive contains the escape "
                f"'\\{escape}', which no legal module path needs -- a module "
                f"path is ASCII letters, digits and -._~+/ (Go Modules "
                f"Reference, \"Module paths\") -- so it is refused rather "
                f"than decoded"
            )
        out.append(escape)
        index += 2
    return "".join(out)


def _tokens(text: str, *, source: str) -> Iterator[tuple[str, str]]:
    """*text* as ``read.go``'s own token kinds, narrowed to what the
    ``module`` directive needs: ``newline``, ``punct``, ``string``, ``ident``.

    Whitespace other than a newline is skipped, ``//`` runs to end of line and
    is dropped, and ``/*`` raises -- the lexer's own three behaviours, in its
    own order.
    """
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\n":
            yield ("newline", "\n")
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline == -1 else newline
            continue
        if text.startswith("/*", index):
            raise _refuse(
                f"{source!r} uses a /* */ comment, which a go.mod may not: "
                f"the Go toolchain's own parser refuses it with \"mod files "
                f"must use // comments (not /* */ comments)\""
            )
        if char in _PUNCTUATION:
            yield ("punct", char)
            index += 1
            continue
        if char in ('"', "`"):
            index, token = _scan_string(text, index, source=source)
            yield ("string", token)
            continue
        start = index
        while index < length:
            candidate = text[index]
            if candidate.isspace() or candidate in _PUNCTUATION:
                break
            if text.startswith("//", index):
                break
            if text.startswith("/*", index):
                raise _refuse(
                    f"{source!r} uses a /* */ comment, which a go.mod may "
                    f"not: the Go toolchain's own parser refuses it with "
                    f"\"mod files must use // comments (not /* */ comments)\""
                )
            index += 1
        if index == start:
            # Unreachable for any input `isIdent` accepts; a non-printable
            # rune would land here rather than looping forever.
            raise _refuse(
                f"{source!r} contains the unexpected input character "
                f"{text[start]!r}"
            )
        yield ("ident", text[start:index])


def _scan_string(text: str, index: int, *, source: str) -> tuple[int, str]:
    """The quoted token beginning at *index*, returned with its own quotes."""
    quote = text[index]
    start = index
    index += 1
    while True:
        if index >= len(text):
            raise _refuse(f"{source!r} has an unterminated string literal")
        char = text[index]
        if char == "\n":
            raise _refuse(
                f"{source!r} has a newline inside a string literal, which the "
                f"Go toolchain's own go.mod parser refuses"
            )
        if char == quote:
            return index + 1, text[start : index + 1]
        if char == "\\" and quote != "`":
            index += 2
            continue
        index += 1
