import ast, pathlib, collections
c=collections.Counter(); ex=[]
for p in sorted(pathlib.Path('src/assay').rglob('*.py')):
    t=ast.parse(p.read_text())
    for n in ast.walk(t):
        if isinstance(n, ast.ClassDef) and any(isinstance(d, ast.Call) and getattr(d.func,'id',None)=='dataclass' for d in n.decorator_list):
            for s in n.body:
                if isinstance(s, ast.AnnAssign) and s.value is not None:
                    v=s.value
                    if isinstance(v, ast.Constant):
                        k=type(v.value).__name__ if v.value is not None else 'None'
                        c[k]+=1
                        if isinstance(v.value,bool): ex.append(f'{p}:{s.lineno} {n.name}.{s.target.id}={v.value}')
                    elif isinstance(v,(ast.Tuple,ast.List,ast.Dict)) : c['container-literal']+=1
                    else: c['other:'+type(v).__name__]+=1
print(c); print('\n'.join(ex))
