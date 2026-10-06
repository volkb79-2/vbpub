"""get.py emitter — render templates/get.py.tmpl for a project.

CLI: cmru get-py [all|project[,project...]] [--config <toml>] [--output <file>]

The emitted get.py is a self-contained Python 3 transactional installer that handles
install, update, rollback, status, scope (system/user), bundled-wheel venv, SHA256 +
minisign-manifest verification, private GitHub asset auth, and the project-adapter
invocation contract (Seam 1). It ships INSIDE the release artifact.

Template variables use [[VARNAME]] syntax and are replaced in ONE pass. Every value that
lands in code is a ``json.dumps`` literal, and a leftover ``[[...]]`` placeholder or a value
outside the installer-field grammar (``config.installer_problems``) is a ``RenderError``
(exit 2), never a warning. See SPEC S6.1/S6.15 for the contract.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from cli_extended import (
    CliFailure,
    OptionSpec,
    Requires,
    VerbGroup,
    VerbSpec,
)

from cmru.cli_support import cmru_registry, target_argument


_TEMPLATE_RESOURCE = "templates/get.py.tmpl"
_EXTENSION_MARKER = "# @@EXTENSIONS@@\n"


class RenderError(ValueError):
    """A value or placeholder cannot be rendered safely into get.py (exit 2)."""


class ExtensionError(RenderError):
    """An installer extension fragment is missing, unsafe, or violates the contract."""


_PLACEHOLDER = re.compile(r"\[\[([A-Z_]+)\]\]")
_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _substitute(text: str, replacements: Dict[str, str]) -> str:
    """Single-pass ``[[NAME]]`` substitution: a substituted value is never re-scanned, and
    a placeholder with no replacement is a render error (never a warning)."""
    unknown: Set[str] = set()

    def sub(match: "re.Match[str]") -> str:
        key = match.group(0)
        if key not in replacements:
            unknown.add(key)
            return key
        return replacements[key]

    result = _PLACEHOLDER.sub(sub, text)
    if unknown:
        raise RenderError(f"get.py.tmpl: unreplaced placeholders: {sorted(unknown)}")
    return result


def _bindings(node: ast.AST) -> Dict[str, int]:
    """Names bound in ``node``'s own scope (not in nested function scopes) -> first line.

    Includes bindings nested in ``if``/``try``/``for``/``while``/``with``/``match``
    blocks, ``del`` targets, ``except ... as`` names, ``match`` captures and imports.
    """
    bound: Dict[str, int] = {}

    def add(name: str, child: ast.AST) -> None:
        bound.setdefault(name, getattr(child, "lineno", 0))

    def visit(child: ast.AST) -> None:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(child.name, child)
            return
        if isinstance(child, ast.Lambda):
            return
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            add(child.id, child)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            add(child.name, child)
        elif isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name:
            add(child.name, child)
        elif isinstance(child, ast.MatchMapping) and child.rest:
            add(child.rest, child)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                add((alias.asname or alias.name).split(".")[0], child)
        for sub in ast.iter_child_nodes(child):
            visit(sub)

    for stmt in ast.iter_child_nodes(node):
        visit(stmt)
    return bound


def _bound_names(node: ast.AST) -> Set[str]:
    return set(_bindings(node))


def _arg_annotations(args: ast.arguments) -> List[ast.expr]:
    every = args.posonlyargs + args.args + args.kwonlyargs
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return [a.annotation for a in every if a.annotation is not None]


def _args_names(args: ast.arguments) -> Set[str]:
    names = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _top_level_names(tree: ast.Module, *, include_imports: bool) -> Dict[str, int]:
    """Top-level binding names -> first line. Imports are optional (see callers)."""
    names: Dict[str, int] = {}
    for stmt in tree.body:
        found: List[Tuple[str, int]] = []
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((stmt.name, stmt.lineno))
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        found.append((sub.id, stmt.lineno))
        elif include_imports and isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                found.append(((alias.asname or alias.name).split(".")[0], stmt.lineno))
        for name, line in found:
            names.setdefault(name, line)
    return names


def _template_contract(core_source: str) -> Tuple[Set[str], Tuple[str, ...]]:
    """(template top-level non-import names, EXTENSION_API) of the rendered core."""
    tree = ast.parse(core_source)
    names = set(_top_level_names(tree, include_imports=False))
    api: Tuple[str, ...] = ()
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "EXTENSION_API"):
            api = tuple(ast.literal_eval(stmt.value))
    return names, api


def _undeclared_api_loads(
    tree: ast.Module, template_names: Set[str], api: Tuple[str, ...],
) -> List[Tuple[str, int]]:
    """Loads of template top-level names, outside EXTENSION_API, that no fragment
    scope (module, enclosing function, comprehension) shadows."""
    module_bound = _bound_names(tree)
    offenders: List[Tuple[str, int]] = []

    def walk(node: ast.AST, scopes: List[Set[str]]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
                walk(default, scopes)
            local = _args_names(node.args)
            if not isinstance(node, ast.Lambda):
                local |= _bound_names(node)
                for deco in node.decorator_list:
                    walk(deco, scopes)
                annotations = _arg_annotations(node.args)
                if node.returns is not None:
                    annotations.append(node.returns)
                for annotation in annotations:
                    walk(annotation, scopes)
            inner = scopes + [local]
            body = [node.body] if isinstance(node, ast.Lambda) else node.body
            for stmt in body:
                walk(stmt, inner)
            return
        if isinstance(node, ast.ClassDef):
            for sub in ast.iter_child_nodes(node):
                walk(sub, scopes)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            targets: Set[str] = set()
            for gen in node.generators:
                for sub in ast.walk(gen.target):
                    if isinstance(sub, ast.Name):
                        targets.add(sub.id)
            scopes = scopes + [targets]
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                and node.id in template_names and node.id not in api
                and not any(node.id in scope for scope in scopes)):
            offenders.append((node.id, node.lineno))
        for sub in ast.iter_child_nodes(node):
            walk(sub, scopes)

    for stmt in tree.body:
        walk(stmt, [module_bound])
    return offenders


def _check_extension(
    relpath: str,
    source: str,
    template_names: Set[str],
    api: Tuple[str, ...],
    claimed: Dict[str, str],
) -> None:
    """Refuse a fragment that violates the extension contract (checks a-e)."""
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError as exc:
        raise ExtensionError(
            f"extension {relpath}: does not parse (line {exc.lineno}): {exc.msg}"
        ) from exc

    # (d) stdlib-only imports.
    stdlib = sys.stdlib_module_names
    for node in ast.walk(tree):
        modules: List[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ExtensionError(
                    f"extension {relpath}: line {node.lineno}: relative import is not "
                    "allowed (stdlib only)"
                )
            if any(alias.name == "*" for alias in node.names):
                raise ExtensionError(
                    f"extension {relpath}: line {node.lineno}: star import is not allowed "
                    "(it could rebind any template name)"
                )
            modules = [node.module or ""]
        for module in modules:
            if module.split(".")[0] not in stdlib:
                raise ExtensionError(
                    f"extension {relpath}: line {node.lineno}: import of "
                    f"{module!r} is not standard library (get.py is stdlib-only)"
                )

    # (b) top-level names must not collide with the template or another fragment.
    # Imports never collide with another fragment (each fragment carries its own),
    # but an import that rebinds a template name is still a collision.
    own = _top_level_names(tree, include_imports=False)
    imported = _top_level_names(tree, include_imports=True)
    for name, line in list(own.items()) + [
        (n, ln) for n, ln in imported.items() if n not in own and n in template_names
    ]:
        if name in template_names:
            raise ExtensionError(
                f"extension {relpath}: line {line}: top-level name {name!r} collides "
                "with a template top-level name"
            )
        if name in claimed:
            raise ExtensionError(
                f"extension {relpath}: line {line}: top-level name {name!r} collides "
                f"with extension {claimed[name]}"
            )
    # Rebinding anywhere in module scope (inside top-level if/try/for/with/match
    # blocks, del targets, match captures) is a collision too, and so is a
    # `global`/`nonlocal` declaration naming a template name (it would let a
    # function rebind the template's own global).
    for name, line in _bindings(tree).items():
        if name in template_names:
            raise ExtensionError(
                f"extension {relpath}: line {line}: module-scope binding {name!r} "
                "rebinds a template top-level name"
            )
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in template_names:
                    raise ExtensionError(
                        f"extension {relpath}: line {node.lineno}: "
                        f"`{type(node).__name__.lower()} {name}` names a template "
                        "top-level name"
                    )
    for name in own:
        claimed[name] = relpath

    # (c) only EXTENSION_API template names may be used.
    offenders = _undeclared_api_loads(tree, template_names, api)
    if offenders:
        name, line = offenders[0]
        raise ExtensionError(
            f"extension {relpath}: line {line}: uses template name {name!r}, which is "
            f"not in EXTENSION_API ({', '.join(api)})"
        )

    # (e) at least one top-level _EXTENSIONS.append(<name>).
    for stmt in tree.body:
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr == "append"
                and isinstance(stmt.value.func.value, ast.Name)
                and stmt.value.func.value.id == "_EXTENSIONS"
                and len(stmt.value.args) == 1
                and isinstance(stmt.value.args[0], ast.Name)):
            return
    raise ExtensionError(
        f"extension {relpath}: no top-level `_EXTENSIONS.append(<name>)` registration"
    )


def _inline_extensions(
    template: str, extensions: List[Tuple[str, bytes]], replacements: Dict[str, str],
) -> str:
    """Replace the extension marker in the (unreplaced) template with the checked,
    wrapped fragments. The caller then substitutes ``[[VARNAME]]`` placeholders over
    the whole result, so a fragment may use them too."""
    if not extensions:
        return template.replace(_EXTENSION_MARKER, "")
    if template.count(_EXTENSION_MARKER) != 1:
        raise ValueError("get.py.tmpl must contain exactly one '# @@EXTENSIONS@@' marker line")
    core = _substitute(template, replacements)
    template_names, api = _template_contract(core)
    claimed: Dict[str, str] = {}
    blocks: List[str] = []
    for relpath, raw in extensions:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExtensionError(f"extension {relpath}: not valid UTF-8: {exc}") from exc
        shipped = _substitute(text, replacements)
        _check_extension(relpath, shipped, template_names, api, claimed)
        digest = hashlib.sha256(raw).hexdigest()
        body = text if text.endswith("\n") else text + "\n"
        blocks.append(
            f"# --- extension: {relpath} sha256={digest} ---\n"
            f"{body}"
            f"# --- end extension: {relpath} ---\n\n\n"
        )
    return template.replace(_EXTENSION_MARKER, "".join(blocks))


def _py_literal(value: object) -> str:
    """A Python source literal for ``value``; every string goes through ``json.dumps``
    (a JSON string literal is a valid, fully escaped Python one). Deterministic."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_py_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_py_literal(k)}: {_py_literal(v)}" for k, v in value.items()
        ) + "}"
    raise RenderError(f"cannot render {type(value).__name__} into get.py")


def _plain(value: str, what: str) -> str:
    """A value that is substituted as TEXT (docstring / message position): a plain name."""
    if not isinstance(value, str) or not _PLAIN.fullmatch(value):
        raise RenderError(f"{what} {value!r} must match {_PLAIN.pattern} to be rendered "
                          "into get.py")
    return value


def render_get_py(
    *,
    project_name: str,
    repo_owner: str,
    repo_name: str,
    tag_prefix: str,
    asset_suffix: str = ".tar.xz",
    install_dir_system: str,
    install_dir_user: str,
    entrypoint: str = "",
    required_commands: Optional[List[str]] = None,
    preserve_paths: Optional[List[str]] = None,
    wheel_specs: Optional[List[Tuple[str, str]]] = None,
    manifest_name: str = "manifest.json",
    signature_name: str = "manifest.json.minisig",
    variants: Optional[List[Dict[str, Optional[str]]]] = None,
    template_path: Optional[Path] = None,
    extensions: Optional[List[Tuple[str, bytes]]] = None,
    manifest_pubkey: str = "",
    launchers: Optional[List[str]] = None,
) -> str:
    """Render the get.py template for a project.

    ``extensions`` is an ordered list of ``(project-relative path, fragment bytes)``
    inlined at the ``# @@EXTENSIONS@@`` marker after the render-time contract checks
    (see ``_check_extension``); raises ``ExtensionError`` on a violation.
    Every [[VARNAME]] placeholder is replaced in ONE pass: values that land in code are
    rendered through ``json.dumps``; values that land in docstring/message text must be
    plain names. Any bad value, or a placeholder left unreplaced, raises ``RenderError``
    (exit 2 from ``cmru get-py``); the result is byte-deterministic.
    """
    from cmru.config import installer_problems

    _plain(project_name, "project name")
    _plain(repo_owner, "github owner")
    _plain(repo_name, "github repo")
    _plain(tag_prefix, "tag prefix")
    launcher_list = launchers or []
    problems = installer_problems(
        install_dir_system=install_dir_system, install_dir_user=install_dir_user,
        asset_suffix=asset_suffix, entrypoint=entrypoint,
        manifest_name=manifest_name, signature_name=signature_name,
        required_commands=required_commands or [], preserve=preserve_paths or [],
        wheels=list(wheel_specs or []), launchers=launcher_list,
        manifest_pubkey=manifest_pubkey,
    )
    for variant in variants or []:
        _plain(variant["name"], "variant name")
    if problems:
        raise RenderError(problems[0])
    if template_path is None:
        template = resources.files("cmru").joinpath(_TEMPLATE_RESOURCE).read_text(
            encoding="utf-8"
        )
    else:
        template = template_path.read_text(encoding="utf-8")

    cmds = required_commands or []
    preserve = preserve_paths or []
    wheels = wheel_specs or []
    variant_list = variants or []

    # Comment string for the docstring header: ", cmd1, cmd2"
    if cmds:
        required_commands_comment = ", " + ", ".join(cmds)
    else:
        required_commands_comment = ""

    replacements = {
        # text position (docstring / messages; validated plain names)
        "[[PROJECT_NAME]]":              project_name,
        "[[TAG_PREFIX]]":                tag_prefix,
        "[[REQUIRED_COMMANDS_STR]]":     ", ".join(cmds) if cmds else "(none)",
        "[[REQUIRED_COMMANDS_COMMENT]]": required_commands_comment,
        # code position (a complete, json.dumps-escaped literal)
        "[[PROJECT_NAME_LIT]]":          _py_literal(project_name),
        "[[REPO_OWNER]]":                _py_literal(repo_owner),
        "[[REPO_NAME]]":                 _py_literal(repo_name),
        "[[TAG_PREFIX_LIT]]":            _py_literal(tag_prefix),
        "[[ASSET_SUFFIX]]":              _py_literal(asset_suffix),
        "[[INSTALL_DIR_SYSTEM]]":        _py_literal(install_dir_system),
        "[[INSTALL_DIR_USER]]":          _py_literal(install_dir_user),
        "[[ENTRYPOINT]]":                _py_literal(entrypoint),
        "[[MANIFEST_PUBKEY]]":           _py_literal(manifest_pubkey),
        "[[REQUIRED_COMMANDS_LIST]]":    _py_literal(cmds),
        "[[PRESERVE_PATHS_LIST]]":       _py_literal(preserve),
        "[[WHEEL_SPECS_LIST]]":          _py_literal([list(w) for w in wheels]),
        "[[LAUNCHERS_LIST]]":            _py_literal(launcher_list),
        "[[VARIANTS_LIST]]":             _py_literal(
            [{"name": v["name"], "label": v.get("label")} for v in variant_list]),
        "[[MANIFEST_NAME]]":             _py_literal(manifest_name),
        "[[SIGNATURE_NAME]]":            _py_literal(signature_name),
    }

    return _substitute(
        _inline_extensions(template, list(extensions or []), replacements), replacements,
    )


def _read_extensions(
    project_name: str, relpaths: List[str], project_root: Optional[Path],
) -> List[Tuple[str, bytes]]:
    """Read each declared extension fragment; it must exist inside the project dir."""
    if not relpaths:
        return []
    if project_root is None:
        raise ExtensionError(
            f"project {project_name!r}: extensions need a project directory to resolve against"
        )
    root = project_root.resolve()
    result: List[Tuple[str, bytes]] = []
    for rel in relpaths:
        path = (root / rel).resolve()
        if root != path and root not in path.parents:
            raise ExtensionError(
                f"extension {rel}: resolves outside the project directory ({path})"
            )
        if not path.is_file():
            raise ExtensionError(f"extension {rel}: file not found at {path}")
        result.append((rel, path.read_bytes()))
    return result


def render_from_config(project_name: str, config_path: Path) -> str:
    """Render get.py for a project using cmru.toml config (reads [installer] section)."""
    from cmru.config import load_forge_config
    config = load_forge_config(config_path)
    proj = config.projects.get(project_name)
    if not proj:
        raise ValueError(f"Project '{project_name}' not found in config")
    if not proj.installer:
        raise ValueError(
            f"Project '{project_name}' has no [project.{project_name}.installer] section"
        )

    ins = proj.installer
    wheel_specs: List[Tuple[str, str]] = [
        (w.path, w.distribution) for w in ins.wheels
    ]
    variants: List[Dict[str, Optional[str]]] = [
        {"name": v.name, "label": v.label} for v in proj.variants
    ]

    extensions = _read_extensions(project_name, ins.extensions, proj.project_root)

    return render_get_py(
        project_name=project_name,
        extensions=extensions or None,
        repo_owner=config.github.owner,
        repo_name=config.github.repo,
        tag_prefix=proj.prefix,
        asset_suffix=ins.asset_suffix,
        install_dir_system=ins.install_dir_system,
        install_dir_user=ins.install_dir_user,
        entrypoint=ins.entrypoint or "",
        required_commands=ins.required_commands or None,
        preserve_paths=ins.preserve or None,
        wheel_specs=wheel_specs or None,
        manifest_name=ins.manifest_name,
        signature_name=ins.signature_name,
        manifest_pubkey=ins.manifest_pubkey,
        launchers=ins.launchers or None,
        variants=variants or None,
    )


def getpy_cli():
    """Build the registered grammar for ``cmru get-py``."""
    registry = cmru_registry(
        "cmru get-py",
        "Emit standalone get.py installers for registered projects.",
        single_command=True,
        no_args_action=True,
    )
    registry.register(VerbSpec(
        "get-py",
        description=(
            "Render the configured standalone installer for one or more projects. "
            "Without --output/--output-dir it prints one project's installer to "
            "stdout and writes nothing."
        ),
        group=VerbGroup.MODIFICATION.value,
        # Conditionally mutating (D4): only --output/--output-dir write, which
        # is also the only place --dry-run means anything.
        mutating=True,
        dry_run=True,
        include_confirmation=False,
        arguments=(target_argument(),),
        constraints=(
            Requires(
                "--dry-run", ("--output", "--output-dir"),
                "stdout mode writes nothing, so there is nothing to preview",
            ),
        ),
        options=(
            OptionSpec(
                ("--config",),
                f"path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}",
                metavar="FILE", parser_kwargs={"default": None},
            ),
            OptionSpec(
                ("--output",), "write one selected installer to this file",
                metavar="FILE", parser_kwargs={"default": None},
                mutually_exclusive_group="destination",
            ),
            OptionSpec(
                ("--output-dir",), "write one named installer per selected project",
                metavar="DIR", parser_kwargs={"default": None},
                mutually_exclusive_group="destination",
            ),
        ),
        include_json=False,
        include_progress=False,
        handler=_run_getpy,
    ))
    return registry.build()


def _run_getpy(args, _runtime) -> None:
    from cmru.cli import _resolve_config, load_config
    from cmru.delegate_targets import resolve_target

    cfg_path = _resolve_config(args.config)
    loaded = load_config(cfg_path)
    configs, project_order = loaded[1], loaded[2]
    names = resolve_target(args.target, cfg_path, configs, project_order)
    if args.output and len(names) != 1:
        raise CliFailure(
            "--output FILE is only valid for one project; use --output-dir for multiple projects",
            exit_code=2,
            show_help=True,
        )
    if len(names) > 1 and not args.output_dir:
        # CLI-12: concatenated installers on stdout are an unrunnable file that
        # exits 0 (`cmru get-py > get.py` at the estate root).
        raise CliFailure(
            f"{len(names)} projects selected ({', '.join(names)}); stdout carries one "
            "installer. Select one project, or write each to --output-dir DIR",
            exit_code=2,
            show_help=True,
        )
    try:
        scripts = {name: render_from_config(name, cfg_path) for name in names}
    except RenderError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    if args.dry_run:
        if args.output_dir:
            for name in names:
                print(f"[DRY RUN] Would write {Path(args.output_dir) / (name + '-get.py')}")
        else:
            print(f"[DRY RUN] Would write {args.output}")
        return
    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            out = output_dir / f"{name}-get.py"
            out.write_text(scripts[name], encoding="utf-8")
            out.chmod(0o755)
            print(f"[INFO] Written to {out}")
        return
    if args.output:
        out = Path(args.output)
        out.write_text(next(iter(scripts.values())), encoding="utf-8")
        out.chmod(0o755)
        print(f"[INFO] Written to {out}")
    else:
        sys.stdout.write(next(iter(scripts.values())))


def getpy_main(argv: Optional[list] = None) -> int:
    """Entry point for ``cmru get-py`` when called directly."""
    return getpy_cli().run(argv=argv)
