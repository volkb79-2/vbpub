import ast, pathlib, collections
root = pathlib.Path("src/assay")
BENIGN = {"frozenset","MappingProxyType","compile","tuple","TypeVar","namedtuple","field","NewType","dict","set","list","int","float","str","len","sorted","Literal","getLogger","Path","object","re.compile","cast","range","ReasonCode","Outcome","enumerate","zip"}
for path in sorted(root.rglob("*.py")):
    tree = ast.parse(path.read_text(), str(path))
    for node in tree.body:
        vals = []
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(node, "value", None) is not None:
            vals = [node.value]
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            vals = [node.value]
        for v in vals:
            for sub in ast.walk(v):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = ast.unparse(f)
                    short = name.split(".")[-1]
                    if name in BENIGN or short in BENIGN: continue
                    print(f"{path}:{node.lineno}: {name}(...)")
