import json, ast, collections, re, pathlib, sys
F='/workspaces/vbpub/.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy/.worktrees/assay-b105-gate-copy-30eec294/assay/.assay/progress-self-qualification.jsonl'
recs=[json.loads(l) for l in open(F)]
t=[r for r in recs if r.get('event')=='test' and r['duration_s']>=0.5]
by=collections.defaultdict(list)
for r in t:
    f,n=r['nodeid'].split('::',1)
    by[f].append((re.sub(r'\[.*','',n), r['duration_s'], n))
out=[]
for f,items in by.items():
    src=pathlib.Path(f).read_text()
    tree=ast.parse(src)
    funcs={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef))}
    for base,d,full in items:
        fn=funcs.get(base)
        if fn is None:
            out.append(f'{f}::{full} {d:.2f} NOT-TOPLEVEL'); continue
        args=[a.arg for a in fn.args.args]
        body=ast.get_source_segment(src, fn)
        kws=[k for k in ['standalone','installed_assay','pip','venv','wheel','sleep','timeout','budget','run_lane','main(','subprocess','git_repo','prepared_snapshot','pytest','hang','deadline','Popen','zipapp','build'] if k in body]
        out.append(f'{f}:{fn.lineno}-{fn.end_lineno} {base}{full[len(base):]} {d:.2f} args={args} kws={kws}')
print('\n'.join(out))
