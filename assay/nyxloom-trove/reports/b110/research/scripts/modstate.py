import ast, pathlib, sys
root = pathlib.Path("src/assay")
MUT_CALLS = {"dict","list","set","defaultdict","OrderedDict","Counter","deque","bytearray"}
MUT_METHODS = {"append","extend","insert","update","setdefault","add","pop","popitem","clear","remove","discard","__setitem__","appendleft","sort","reverse"}
for path in sorted(root.rglob("*.py")):
    tree = ast.parse(path.read_text(), str(path))
    mods = {}
    for node in tree.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]; value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            targets = [node.target]; value = node.value
        if value is None: continue
        mutable = isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp))
        if isinstance(value, ast.Call):
            f = value.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name in MUT_CALLS: mutable = True
        if mutable:
            for t in targets: mods[t.id] = node.lineno
    # find mutation sites anywhere (functions) of these names
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in mods and node.func.attr in MUT_METHODS:
                hits.append((node.func.value.id, node.lineno, node.func.attr))
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            tl = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for t in tl:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id in mods:
                    hits.append((t.value.id, node.lineno, "subscript-assign/del"))
        if isinstance(node, ast.Global):
            for n in node.names: hits.append((n, node.lineno, "global"))
    # exclude module-level (top) mutation that is part of construction? keep, but mark depth
    top_lines = {n.lineno for n in tree.body}
    if mods:
        print(f"== {path}: mutable module names: " + ", ".join(f"{k}@{v}" for k,v in mods.items()))
    for h in hits:
        print(f"   MUTATION {path}:{h[1]} {h[0]} .{h[2]}")
