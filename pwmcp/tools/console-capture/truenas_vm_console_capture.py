#!/usr/bin/env python3
"""Capture a TrueNAS VM's SPICE web console (spice-html5 canvas) through the pwmcp managed browser.

Credentials from the environment only: TRUENAS_HOST, TRUENAS_USER, TRUENAS_PASSWORD (the API login, used just to read
the VM's display URI and display password over JSON-RPC, see truenas_rpc.py) and PWMCP_MCP_URL.
The display password is never printed or written. Read-only: no key or mouse input is sent to the guest.

Usage: truenas_vm_console_capture.py VM_ID OUT_DIR [--viewports 1280x720,1920x1080]
Per viewport it writes: shot_<WxH>.png (browser screenshot), canvas_<WxH>.png (canvas.toDataURL, intrinsic bitmap) and
measure.json (canvas size, CSS size, DPR, image-rendering, toDataURL/getImageData ability, screenshot-vs-canvas check).
One MCP session per run (each session is a fresh browser context).
"""
import argparse, asyncio, base64, io, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from truenas_rpc import RPC

MEASURE = """() => { const out=[]; for (const c of document.querySelectorAll('canvas')) { const cs=getComputedStyle(c); const r=c.getBoundingClientRect();
 let td=null, gid=null, err=null; try{td=c.toDataURL('image/png').length}catch(e){err=String(e)}
 try{const x=c.getContext('2d'); gid=x?x.getImageData(0,0,1,1).data.length:'no-2d'}catch(e){gid=String(e)}
 out.push({id:c.id,w:c.width,h:c.height,cssW:r.width,cssH:r.height,left:r.left,top:r.top,clientLeft:c.clientLeft,clientTop:c.clientTop,clientW:c.clientWidth,clientH:c.clientHeight,scrollX:scrollX,scrollY:scrollY,styleW:c.style.width,styleH:c.style.height,
  imageRendering:cs.imageRendering,transform:cs.transform,display:cs.display,toDataURLlen:td,getImageData:gid,err}) }
 return JSON.stringify({dpr:devicePixelRatio,innerW:innerWidth,innerH:innerHeight,canvases:out}); }"""


def decode(text):
    raw = text.split("### Result", 1)[-1].strip()
    return json.JSONDecoder().raw_decode(raw)[0]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vm_id", type=int)
    ap.add_argument("out")
    ap.add_argument("--viewports", default="1280x720,1920x1080")
    ap.add_argument("--settle", type=int, default=8)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    host = os.environ["TRUENAS_HOST"]
    async with RPC() as rpc:
        uri = (await rpc.call("vm.get_display_web_uri", a.vm_id, host))["uri"]
        devs = await rpc.call("vm.get_display_devices", a.vm_id)
        pw = devs[0]["attributes"].get("password") or ""
    # the URI is built from the host we pass; keep scheme/path as the middleware gave them
    from PIL import Image
    results = []
    async with streamable_http_client(os.environ["PWMCP_MCP_URL"]) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            T = lambda n, p, t=60: s.call_tool(n, p, read_timeout_seconds=t)
            await T("browser_navigate", {"url": uri})
            await T("browser_wait_for", {"time": 6})
            snap = (await T("browser_snapshot", {})).content[0].text
            m = re.search(r'textbox.*?\[ref=(e\d+)\]', snap)
            print("password prompt found:", bool(m), file=sys.stderr)
            if m:  # password prompt of spice-html5
                # browser_type leaves the type=password input empty and a Playwright click on Submit does not reach the
                # module-scoped handler, so fill the prompt from page JS and send the Enter keyup the page listens for.
                # The value is never printed.
                await T("browser_evaluate", {"function": "() => { const i=document.querySelector('.pw_prompt input'); i.value=%s; i.dispatchEvent(new KeyboardEvent('keyup',{keyCode:13,which:13,key:'Enter'})); return 1 }" % json.dumps(pw)})
            await T("browser_wait_for", {"time": a.settle})
            for vp in a.viewports.split(","):
                W, H = map(int, vp.split("x"))
                await T("browser_resize", {"width": W, "height": H})
                await T("browser_wait_for", {"time": 4})
                meas = json.loads(decode((await T("browser_evaluate", {"function": MEASURE})).content[0].text))
                meas["viewport"] = vp
                canv = [c for c in meas["canvases"] if c["w"] > 1 and c["h"] > 1]
                shot = await T("browser_take_screenshot", {"type": "png"})
                for c in shot.content:
                    if c.type == "image":
                        open(f"{a.out}/shot_{vp}.png", "wb").write(base64.b64decode(c.data))
                if canv:
                    idx = meas["canvases"].index(canv[0])
                    d = decode((await T("browser_evaluate", {"function": f"() => document.querySelectorAll('canvas')[{idx}].toDataURL('image/png')"})).content[0].text)
                    open(f"{a.out}/canvas_{vp}.png", "wb").write(base64.b64decode(d.split(",", 1)[1]))
                    cv = Image.open(f"{a.out}/canvas_{vp}.png").convert("RGB"); sh = Image.open(f"{a.out}/shot_{vp}.png").convert("RGB")
                    meas["canvas_png_size"] = cv.size; meas["screenshot_size"] = sh.size
                    # does any integer-offset crop of the canvas equal the screenshot region at the canvas position?
                    c0 = canv[0]; x0, y0 = round(c0["left"] + c0["clientLeft"]), round(c0["top"] + c0["clientTop"])
                    # compare the part of the canvas visible in the screenshot, ignoring a 2 px outer ring (the page draws a
                    # border/shadow over the canvas edge pixels: measured +16 grey on the outermost ring)
                    inset = 2
                    vx0, vy0 = max(x0 + inset, 0), max(y0 + inset, 0)
                    vx1, vy1 = min(x0 + cv.width - inset, sh.width), min(y0 + cv.height - inset, sh.height)
                    if c0["clientW"] == cv.width and vx1 > vx0 and vy1 > vy0:
                        from PIL import ImageChops
                        sub = sh.crop((vx0, vy0, vx1, vy1))
                        ref = cv.crop((vx0 - x0, vy0 - y0, vx1 - x0, vy1 - y0))
                        meas["screenshot_vs_canvas_interior"] = {"compared_px": [vx1 - vx0, vy1 - vy0], "identical": ImageChops.difference(sub, ref).getbbox() is None,
                                                                 "canvas_fully_visible": x0 >= 0 and y0 >= 0 and x0 + cv.width <= sh.width and y0 + cv.height <= sh.height}
                    else:
                        meas["screenshot_vs_canvas_interior"] = "not comparable"
                results.append(meas)
    json.dump(results, open(f"{a.out}/measure.json", "w"), indent=1)
    print(json.dumps(results, indent=1))

asyncio.run(main())
