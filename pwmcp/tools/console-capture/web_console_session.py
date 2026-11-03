#!/usr/bin/env python3
"""Long-lived pwmcp MCP session driven through a file inbox: log in to a web UI, open a canvas console
(Netcup SCP screen, Proxmox noVNC, ...), capture the canvas, send keys / mouse, measure it.

Why a daemon: every MCP session is a fresh browser context, so login and capture must happen in ONE session.
Why an inbox: the caller (a human or an agent) issues steps one at a time and looks at each frame before the next.

  web_console_session.py run  --dir DIR           # start the session (PWMCP_MCP_URL required); runs until `quit`
  web_console_session.py ctl  --dir DIR OP [...]   # queue one op, wait for and print its result (JSON)

ops (ctl):
  dialog | tab N | navigate URL | snapshot | wait SECONDS | waittext TEXT | resize WxH | tabs
  click REF [--element DESC] [--double] | press KEY | eval JS-FILE-OR-EXPR
  type REF (--text T | --env NAME | --file F) [--slowly] [--submit]   # --env/--file: value read inside the daemon,
                                                                       # never logged, never in JS
  keys REF-less typing into the focused element: typekeys (--text T | --env NAME | --file F)  # one key press per char
  canvas N OUT.png        # canvas.toDataURL of the N-th <canvas> (intrinsic bitmap, lossless)
  shot OUT.png            # page screenshot
  measure                 # canvas bitmap vs CSS size, DPR, window, toDataURL/getImageData ability
  mouse N X Y [--button left|right] [--dbl]   # click at canvas-bitmap coordinates (mapped through the CSS size)
  quit
Secrets (--env/--file) are read in the daemon process; `ps` and the op log never contain them. Use throwaway-grade
handling anyway: keep files 0600 and delete them afterwards.
"""
import argparse, asyncio, base64, json, os, sys, time, glob

MEASURE = """() => { const out=[]; for (const c of document.querySelectorAll('canvas')) { const cs=getComputedStyle(c); const r=c.getBoundingClientRect();
 let td=null, gid=null, err=null; try{td=c.toDataURL('image/png').length}catch(e){err=String(e)}
 try{const x=c.getContext('2d'); gid=x?x.getImageData(0,0,1,1).data.length:'no-2d'}catch(e){gid=String(e)}
 out.push({id:c.id,w:c.width,h:c.height,cssW:r.width,cssH:r.height,left:r.left,top:r.top,clientW:c.clientWidth,clientH:c.clientHeight,
  styleW:c.style.width,styleH:c.style.height,imageRendering:cs.imageRendering,transform:cs.transform,display:cs.display,toDataURLlen:td,getImageData:gid,err}) }
 return JSON.stringify({dpr:devicePixelRatio,innerW:innerWidth,innerH:innerHeight,outerW:outerWidth,outerH:outerHeight,url:location.origin+location.pathname,canvases:out}); }"""

MOUSE = """async (page) => { const r = await page.evaluate((n) => { const c=document.querySelectorAll('canvas')[n]; const b=c.getBoundingClientRect();
 return {l:b.left,t:b.top,w:b.width,h:b.height,cw:c.width,ch:c.height} }, %(n)d);
 const x = r.l + %(x)f * r.w / r.cw, y = r.t + %(y)f * r.h / r.ch;
 await page.mouse.click(x, y, {button: '%(button)s', clickCount: %(cc)d}); return {x, y}; }"""


def decode(text):
    raw = text.split("### Result", 1)[-1].strip()
    try:
        return json.JSONDecoder().raw_decode(raw)[0]
    except Exception:
        return raw


def secret_value(req):
    if req.get("env"):
        return os.environ[req["env"]]
    if req.get("file"):
        return open(req["file"]).read().rstrip("\n")
    return req.get("text", "")


async def run(d):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    inbox, outbox = f"{d}/inbox", f"{d}/outbox"
    for p in (inbox, outbox, f"{d}/frames"):
        os.makedirs(p, exist_ok=True)
    open(f"{d}/pid", "w").write(str(os.getpid()))
    async with streamable_http_client(os.environ["PWMCP_MCP_URL"]) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def T(n, p, t=60):
                r_ = await s.call_tool(n, p, read_timeout_seconds=t)
                if getattr(r_, "is_error", getattr(r_, "isError", False)):  # tool-level errors come back as results, not exceptions
                    raise RuntimeError(n + ": " + "\n".join(getattr(c, "text", "") for c in r_.content)[:500])
                return r_
            txt = lambda res: "\n".join(c.text for c in res.content if getattr(c, "text", None))
            idle_since = time.time()
            last_ping = time.time()
            while True:
                items = sorted(glob.glob(f"{inbox}/*.json"))
                if not items:
                    if time.time() - idle_since > 3600:
                        break  # hard bound: an unattended session ends after 1 h idle
                    await asyncio.sleep(0.5)
                    if time.time() - last_ping > 15:  # the MCP HTTP stream times out after ~60 s of silence
                        last_ping = time.time()
                        try:
                            await s.send_ping()
                        except Exception:
                            pass
                    continue
                idle_since = time.time()
                f = items[0]
                req = json.load(open(f))
                os.unlink(f)
                op, res = req["op"], {"ok": True}
                try:
                    if op == "quit":
                        json.dump(res, open(f"{outbox}/{req['id']}.json", "w"))
                        break
                    elif op == "navigate":
                        res["out"] = txt(await T("browser_navigate", {"url": req["url"]}))[:1500]
                    elif op == "snapshot":
                        res["out"] = txt(await T("browser_snapshot", {}))
                    elif op == "wait":
                        await T("browser_wait_for", {"time": float(req["seconds"])}, 120)
                    elif op == "waittext":
                        await T("browser_wait_for", {"text": req["text"]}, 120)
                    elif op == "resize":
                        W, H = map(int, req["size"].split("x"))
                        await T("browser_resize", {"width": W, "height": H})
                    elif op == "tabs":
                        res["out"] = txt(await T("browser_tabs", {"action": "list"}))
                    elif op == "dialog":  # accept a JS dialog (e.g. beforeunload when leaving a console page)
                        await T("browser_handle_dialog", {"accept": True})
                    elif op == "tab":  # select tab N (popups such as re-authentication or consoles open as new tabs)
                        res["out"] = txt(await T("browser_tabs", {"action": "select", "index": int(req["ref"])}))[:300]
                    elif op == "click":
                        a = {"element": req.get("element") or "element", "target": req["ref"]}
                        if req.get("double"):
                            a["doubleClick"] = True
                        await T("browser_click", a)
                    elif op == "press":
                        await T("browser_press_key", {"key": req["key"]})
                    elif op == "eval":
                        res["out"] = decode(txt(await T("browser_evaluate", {"function": req["js"]})))
                    elif op == "type":
                        a = {"element": req.get("element") or "input", "target": req["ref"], "text": secret_value(req),
                             "slowly": bool(req.get("slowly")), "submit": bool(req.get("submit"))}
                        await T("browser_type", a, 120)
                    elif op == "typekeys":  # one key press per char into the focused element (canvas consoles)
                        for ch in secret_value(req):
                            await T("browser_press_key", {"key": "Space" if ch == " " else ch})
                            await asyncio.sleep(float(req.get("delay", 0.05)))
                    elif op == "canvas":
                        data = decode(txt(await T("browser_evaluate", {"function": f"() => document.querySelectorAll('canvas')[{int(req['n'])}].toDataURL('image/png')"})))
                        open(req["out"], "wb").write(base64.b64decode(data.split(",", 1)[1]))
                        res["bytes"] = os.path.getsize(req["out"])
                    elif op == "shot":
                        shot = await T("browser_take_screenshot", {"type": "png"})
                        for c in shot.content:
                            if c.type == "image":
                                open(req["out"], "wb").write(base64.b64decode(c.data))
                        res["bytes"] = os.path.getsize(req["out"])
                    elif op == "measure":
                        res["out"] = json.loads(decode(txt(await T("browser_evaluate", {"function": MEASURE}))))
                    elif op == "mouse":
                        code = MOUSE % dict(n=int(req["n"]), x=float(req["x"]), y=float(req["y"]),
                                            button=req.get("button", "left"), cc=2 if req.get("dbl") else 1)
                        res["out"] = txt(await T("browser_run_code_unsafe", {"code": code}))[:300]
                    else:
                        res = {"ok": False, "error": "unknown op " + op}
                except Exception as e:
                    res = {"ok": False, "error": str(e)[:600]}
                json.dump(res, open(f"{outbox}/{req['id']}.json", "w"))
    open(f"{d}/DONE", "w").write("exit\n")


def ctl(a):
    d = a.dir
    rid = f"{int(time.time() * 1000)}"
    req = {"id": rid, "op": a.op}
    pos = a.args
    names = {"navigate": ["url"], "wait": ["seconds"], "waittext": ["text"], "resize": ["size"], "click": ["ref"],
             "press": ["key"], "tab": ["ref"], "eval": ["js"], "type": ["ref"], "canvas": ["n", "out"], "shot": ["out"],
             "mouse": ["n", "x", "y"]}.get(a.op, [])
    for k, v in zip(names, pos):
        req[k] = v
    if a.op == "eval" and os.path.isfile(req.get("js", "")):
        req["js"] = open(req["js"]).read()
    for k in ("text", "env", "file", "element", "button", "delay"):
        if getattr(a, k) is not None:
            req[k] = getattr(a, k)
    req["slowly"], req["submit"], req["double"], req["dbl"] = a.slowly, a.submit, a.double, a.dbl
    tmp = f"{d}/inbox/.{rid}.tmp"
    open(tmp, "w").write(json.dumps(req))
    os.rename(tmp, f"{d}/inbox/{rid}.json")
    out = f"{d}/outbox/{rid}.json"
    end = time.time() + a.timeout
    while time.time() < end:
        if os.path.exists(out):
            print(open(out).read())
            return 0
        time.sleep(0.5)
    print(json.dumps({"ok": False, "error": "timeout"}))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--dir", required=True)
    c = sub.add_parser("ctl"); c.add_argument("--dir", required=True); c.add_argument("--timeout", type=float, default=120)
    c.add_argument("op"); c.add_argument("args", nargs="*")
    for k in ("text", "env", "file", "element", "button", "delay"):
        c.add_argument("--" + k)
    for k in ("slowly", "submit", "double", "dbl"):
        c.add_argument("--" + k, action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        asyncio.run(run(a.dir))
    else:
        sys.exit(ctl(a))


if __name__ == "__main__":
    main()
