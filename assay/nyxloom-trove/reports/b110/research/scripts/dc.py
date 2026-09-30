import ast, pathlib, collections, sys
root = pathlib.Path('src/assay')
rows=[]; bools=collections.Counter(); other=collections.Counter()
for p in sorted(root.rglob('*.py')):
    tree=ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for d in node.decorator_list:
                name=None
                if isinstance(d, ast.Call):
                    f=d.func
                    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                    if name=='dataclass':
                        kws={k.arg: (k.value.value if isinstance(k.value, ast.Constant) else ast.unparse(k.value)) for k in d.keywords}
                        rows.append((str(p), node.lineno, d.lineno, node.name, kws, isinstance(node, ast.ClassDef) and any(isinstance(n, ast.ClassDef) for n in [])))
                        for k,v in kws.items():
                            if isinstance(v,bool): bools[(k,v)]+=1
                            else: other[(k,v)]+=1
                elif isinstance(d,(ast.Name,ast.Attribute)):
                    name = d.id if isinstance(d, ast.Name) else d.attr
                    if name=='dataclass':
                        rows.append((str(p), node.lineno, d.lineno, node.name, None, False))
print('bool kw counts', dict(bools)); print('other', dict(other))
print('total dataclass decorators', len(rows), 'bare', sum(1 for r in rows if r[4] is None))
per=collections.defaultdict(list)
for r in rows: per[r[0]].append(r)
for f,rs in per.items():
    nb=sum(sum(1 for v in (r[4] or {}).values() if isinstance(v,bool)) for r in rs)
    print(f'{f}: classes={len(rs)} boolkw={nb}')
    for r in rs:
        print(f'   L{r[2]} {r[3]} {r[4]}')
