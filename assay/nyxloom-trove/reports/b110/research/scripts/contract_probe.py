import ast, dataclasses, importlib, inspect, json, pathlib, sys
PROJECT_ROOT = pathlib.Path('.').resolve()
PACKAGE = PROJECT_ROOT / "src" / "assay"
_PARAMS = ("init","repr","eq","order","unsafe_hash","frozen","match_args","kw_only","slots","weakref_slot")
_FIELD = ("init","repr","compare","hash","kw_only")
def module_names():
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE.parent).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        yield ".".join(parts), path
def observed():
    out = {}
    for name, path in module_names():
        module = importlib.import_module(name)
        found = {q: o for q, o in vars(module).items()
                 if inspect.isclass(o) and dataclasses.is_dataclass(o) and o.__module__ == name}
        # AST cross-check: every @dataclass(...) in the file is reachable at module level
        decorated = {n.name for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                     if isinstance(n, ast.ClassDef) and any(
                        (isinstance(d, ast.Call) and getattr(d.func, "id", None) == "dataclass")
                        or getattr(d, "id", None) == "dataclass" for d in n.decorator_list)}
        assert decorated == set(found), (name, decorated ^ set(found))
        for q, cls in sorted(found.items()):
            p = cls.__dataclass_params__
            out[f"{name}.{cls.__qualname__}"] = {
                "params": {k: getattr(p, k) for k in _PARAMS},
                "fields": {f.name: {k: getattr(f, k) for k in _FIELD}
                           | ({"default": f.default} if isinstance(f.default, bool) else {})
                           for f in dataclasses.fields(cls)},
            }
    return out
o = observed()
print(len(o), sum(v["params"]["frozen"] for v in o.values()), sum(v["params"]["kw_only"] for v in o.values()))
print(sum(len(v["fields"]) for v in o.values()), 'fields;', sum(1 for v in o.values() for f in v["fields"].values() if "default" in f), 'bool defaults')
s = json.dumps({"format": 1, "classes": o}, indent=1, sort_keys=True); print(len(s), 'bytes json')
