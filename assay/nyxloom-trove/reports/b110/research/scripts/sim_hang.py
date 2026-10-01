import ast, importlib, inspect, sys, textwrap
sys.path.insert(0, "src")
CAP = 200_000
class Stall(Exception): pass
def run_capped(fn, *a):
    n = [0]
    def tr(frame, ev, arg):
        if ev == "line":
            n[0] += 1
            if n[0] > CAP: raise Stall()
        return tr
    sys.settrace(tr)
    try:
        r = fn(*a); out = ("ok", repr(r)[:40])
    except Stall: out = ("HANG", "")
    except Exception as e: out = ("raise", type(e).__name__)
    finally: sys.settrace(None)
    return out
def mutant(modname, fnname, lineno, old, new):
    mod = importlib.import_module(modname)
    src_lines = inspect.getsource(mod).splitlines(keepends=True)
    fn = getattr(mod, fnname)
    lines, start = inspect.getsourcelines(fn)
    if lineno is None:
        body = "".join(lines)
    else:
        rel = lineno - start
        assert old in lines[rel], (lineno, lines[rel])
        lines = list(lines); lines[rel] = lines[rel].replace(old, new, 1)
        body = "".join(lines)
    ns = dict(mod.__dict__)
    exec(compile(textwrap.dedent(body), f"<{fnname}>", "exec"), ns)
    return ns, ns[fnname]
def case(modname, fnname, lineno, old, new, inputs, helper=None, helper_fn=None):
    ns, fn = mutant(modname, fnname, lineno, old, new)
    if helper:  # mutate a helper, then rebuild caller in same ns
        pass
    for label, inp in inputs:
        print(f"  {modname.split('.')[-1]}:{lineno} {old!r}->{new!r} [{label}] ->", run_capped(fn, *inp))
GO = [("multiline", ("package x\n\nfunc f() {}\n",)), ("trail//", ("package x\n// c",)),
      ("open/*", ("package x\n/* open",)), ("open`", ("package x\nvar s = `open",)), ("closed`", ("package x\nvar s = `a`\n",))]
print("go.py _strip_comments_and_literals")
case("assay.adapters.go", "_strip_comments_and_literals", None, "", "", GO)
for ln, o, n in [(321, 'two == "//"', 'two != "//"'), (323, "end == -1", "end != -1"), (330, "close == -1", "close != -1"),
                 (328, 'two == "/*"', 'two != "/*"'), (337, "ch == '\"'", "ch != '\"'"), (351, 'ch == "`"', 'ch != "`"'),
                 (319, "i < n", "i <= n"), (339, "end is None", "end is not None")]:
    case("assay.adapters.go", "_strip_comments_and_literals", ln, o, n, GO)
# 292 lives in _scan_raw_string: mutate helper then call the caller in that namespace
import assay.adapters.go as g
ns, raw = mutant("assay.adapters.go", "_scan_raw_string", 292, "end == -1", "end != -1")
g_orig = g._scan_raw_string; g._scan_raw_string = raw
for label, inp in GO: print(f"  go:292 [{label}] ->", run_capped(g._strip_comments_and_literals, *inp))
g._scan_raw_string = g_orig
JS = [("multiline", ("const a = 1;\nconst b = 2;\n",)), ("trail//", ("const a = 1;\n// c",)), ("open/*", ("const a = 1;\n/* open",))]
print("javascript.py _strip_comments")
for ln, o, n in [(None, "", ""), (243, 'two == "//"', 'two != "//"'), (245, "end == -1", "end != -1"), (247, 'two == "/*"', 'two != "/*"'), (249, "close == -1", "close != -1"), (241, "i < n", "i <= n")]:
    case("assay.adapters.javascript", "_strip_comments", ln, o, n, JS)
SQL = [("comment-line", (b"SELECT 1;\n-- c\nSELECT 2;\n",)), ("trail--", (b"SELECT 1; -- c",)), ("open$$", (b"SELECT 1; $$ open",)),
       ("pos$1-in-comment", (b"-- $1\nSELECT $1",)), ("plain", (b"SELECT 1;\n",))]
print("sql_lex.py _lex_once")
for ln, o, n in [(None, "", ""), (193, "b == _DASH", "b != _DASH"), (193, "_DASH and i + 1 < n", "_DASH or i + 1 < n"), (193, "< n and source", "< n or source"),
                 (193, "source[i + 1] == _DASH", "source[i + 1] != _DASH"), (195, "end == -1", "end != -1"),
                 (201, "b == _SLASH", "b != _SLASH"), (270, "tag_end is not None", "tag_end is None"), (273, "close == -1", "close != -1"),
                 (245, "b == _SINGLE_QUOTE", "b != _SINGLE_QUOTE"), (268, "b == _DOLLAR", "b != _DOLLAR"), (204, "j < n and depth > 0", "j < n or depth > 0")]:
    case("assay.adapters.sql_lex", "_lex_once", ln, o, n, SQL)
MOD = [("trail//", ("module x\n// c",), ), ("normal", ("module x\n",))]
print("go_modfile.py _tokens (list())")
for ln, o, n in [(None, "", ""), (393, "newline == -1", "newline != -1"), (384, 'char == "\\n"', 'char != "\\n"')]:
    ns, fn = mutant("assay.adapters.go_modfile", "_tokens", ln, o, n)
    for label, inp in MOD:
        print(f"  go_modfile:{ln} {o!r}->{n!r} [{label}] ->", run_capped(lambda t: list(fn(t, source="go.mod")), *inp))
