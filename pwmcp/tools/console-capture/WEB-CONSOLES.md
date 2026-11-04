# Web consoles through the pwmcp managed browser

Measured 2026-10-27 with pwmcp 0.1.0 (Chrome for Testing behind Playwright MCP, default window 1280x720, DPR 1),
Netcup SCP screen and Proxmox VE 9.2 (pve-manager 9.2.x, noVNC and xterm.js as shipped). No screenshots are kept in the
repository; every number below was measured, nothing is quoted from documentation.

## Driver: `web_console_session.py`

One MCP session = one fresh browser context, so login, navigation and capture must share a session. The tool starts a
long-lived session and takes one step at a time from a file inbox (see the module docstring for all ops):

```bash
export PWMCP_MCP_URL=http://pwmcp:8931/mcp
export UI_USER=... UI_PW=...                     # read inside the daemon only, never argv, never logged
D=$(mktemp -d); python3 -I web_console_session.py run --dir "$D" &
C="python3 -I web_console_session.py ctl --dir $D"
$C navigate https://HOST:8006/ ; $C type 'input[name=password]' --env UI_PW --submit
$C canvas 0 "$D/frames/a.png" ; $C measure ; $C press Enter ; $C quit
```

Things learned the hard way (all handled or documented in the tool):

* Playwright MCP tools take `target` (snapshot ref **or CSS selector**), not `ref`; tool-level errors come back as
  results with `is_error`, not as exceptions.
* Only ONE browser exists per pwmcp service: a second session fails with "Browser is already in use". Quit the first.
* The MCP HTTP stream dies after ~60 s without traffic (daemon exited with `httpx2.ReadTimeout`); the daemon pings
  every 15 s. A fresh daemon means a fresh browser context, i.e. log in again.
* Leaving a console page raises a `beforeunload` dialog that blocks every tool until `dialog` (browser_handle_dialog).
* A self-signed certificate shows Chrome's interstitial: `click '#details-button'`, then `click '#proceed-link'`.
* The TrueNAS JSON-RPC helper (`truenas_rpc.py`, used to read a VM's display URI/password) verifies TLS by default; for a throwaway
  self-signed TrueNAS test install set `TRUENAS_INSECURE_TLS=1` (warns on stderr) or `TRUENAS_CA_FILE=<pem>`. See README, "TrueNAS API client TLS".
* `browser_type` does fill password inputs (the TrueNAS note about empty password fields applies to spice-html5 only).
* Screen-reading ops never need credentials; typing a secret into a guest uses `typekeys --file/--env`
  (one `press_key` per character, no secret in JS).

## Netcup SCP screen (server "Screen" popup)

URL `https://www.servercontrolpanel.de/scp-ui/servers/<SERVER_ID>/screen?popup=1` (after the Keycloak login form:
`input[name=username]`, `input[name=password]`). One `<canvas>`, 1024x768 bitmap = 1024x768 CSS px at DPR 1 (window
1280x720, canvas centred at left=128, taller than the window so the page scrolls), `toDataURL` and `getImageData` work.
Mouse (`page.mouse.click` at canvas coordinates) and key presses reach the guest: a `z` appeared at the cursor cell and
changed a 16x11 px box. SCP actions that change the server (ISO attach) additionally require a re-authentication popup
(a second tab, `tab 1`, same login form) - this is a password re-entry, not a 2FA prompt.

## Comparison table (Proxmox VE 9.2, VM with TCG; "A" = std VGA, guest Alpine 3.24 virt)

| Property | noVNC (VM display) | xterm.js (VM serial, `serial0: socket`) | xterm.js (node shell) | SPICE |
|---|---|---|---|---|
| URL / popup | `/?console=kvm&novnc=1&vmid=<ID>&vmname=<N>&node=<NODE>&resize=<off\|scale\|remote>&cmd=` - its own page (title "QEMU (<name>) - noVNC"), popup from the UI, direct URL works with the login cookie | `/?console=kvm&xtermjs=1&vmid=<ID>&vmname=<N>&node=<NODE>&cmd=` (title "<node> - Proxmox Console") | `/?console=shell&xtermjs=1&vmid=0&vmname=&node=<NODE>&cmd=` | UI button / `POST /nodes/<n>/qemu/<id>/spiceproxy` returns `.vv` connection data + one-time ticket; proxy `http://<host>:3128` |
| Transport | WebSocket to VNC (vncproxy ticket) | WebSocket to the serial socket (termproxy) | WebSocket to a login shell (termproxy) | SPICE over HTTP CONNECT proxy, native client only |
| Element carrying the screen | one 2D `<canvas>` (noVNC) | xterm.js: 2D link-layer canvas + a WebGL canvas on top of a hidden 32x26 measuring canvas | same | none in the browser: there is no spice-html5 in PVE; the `.vv` needs virt-viewer. Not testable here |
| Bitmap vs CSS size | equals the guest framebuffer; CSS = bitmap with `off`/`remote`, scaled with `scale` | 7x18 px cells: bitmap = cols*7 x rows*18 = CSS (e.g. 270x59 cells = 1890x1062 in a 1920x1080 window, 5 px border) | same | n/a |
| DPR | 1 | 1 | 1 | n/a |
| Window resize | `off`: canvas stays at guest size (centred when window is larger, scrolls when smaller); `scale`: CSS shrinks/grows with the aspect kept (window 1280x720 -> 1152x720, 1920x1080 -> 1728x1080, 800x600 -> 800x500, 1024x768 -> 1024x640) while the bitmap stays 1280x800; `remote`: no change with std VGA (needs a guest that follows an extended-desktop-size request) | terminal reflows: window 1280x720 -> 179x39 cells (1253x702), 800x600 -> 110x32 (770x576), 1920x1080 -> 270x59 (1890x1062) | same | n/a |
| Scale-to-fit control | `<select id=noVNC_setting_resize>` (off / scale / remote), URL param `resize=` initialises it; change by setting `.value` and dispatching `change` | none (always fits the window) | none | n/a |
| `toDataURL` / `getImageData` | work, intrinsic bitmap, lossless | **blank**: the WebGL canvas has no preserved drawing buffer (transparent PNG), the 2D layer is only the link layer; `getContext('2d')` on the WebGL canvas returns null. Use page screenshots or the DOM/JS buffer instead | same | n/a |
| Screenshot equals canvas | yes, pixel-identical (max diff 0 over 1280x800, `off`, DPR 1) after cropping at the canvas offset (e.g. 320,140 in a 1920x1080 window); not equal in `scale` (screenshot is resampled) | screenshot is the only pixel source | same | n/a |
| **DOM / text readable without OCR** | no (pixels only) | **yes: `window.term.buffer.active.getLine(i).translateToString(true)`**; `term.cols`/`term.rows`; no DOM text spans (canvas/WebGL renderer, not DOM renderer) | **yes, same API** (measured: banner line and `root@<node>:~# ls` read back exactly) | n/a |
| Cursor blink box | text cursor of the guest console: 8x2 px (underline) at 1280x800, appears/disappears (distinct frames differ only in that box) | none: 8 screenshots 0.4 s apart were identical, no blink | not measured (same widget) | n/a |
| Input path | `press`/`typekeys` into the focused canvas reached the guest (first changed frame about 1.7 s after the key with a 0.6 s tool call, under TCG); mouse clicks via canvas coordinates | clicking `.xterm-screen` then `typekeys` worked; login to the serial getty succeeded | typing `ls` + Enter produced the command and its output | n/a |
| Quirks | `beforeunload` dialog when leaving; noVNC shows no frame until the guest draws; a VM with `vga: serial0` has no noVNC display | the guest must put a getty/console on ttyS0 (Alpine ISO does; with std VGA the serial stays empty "starting serial terminal on interface serial0"); a restarted VM needs a reconnect | termproxy needs the logged-in user (cookie) | `no spice port` error unless `vga: qxl` (or another spice-capable display) |

### Effect of the VM VGA type on the noVNC canvas (Alpine guest, KMS/fbcon, identical VM otherwise)

| `vga:` | canvas bitmap |
|---|---|
| std | 1280x800 |
| virtio | 1280x800 |
| qxl | 1024x768 |
| cirrus | 800x600 |
| vmware | 720x400 (VGA text mode, no KMS driver bound) |
| serial0 | no display; xterm.js serial terminal only (Alpine prints its login on ttyS0) |

The size is the guest's mode, not something the web UI negotiates; it only matters that the bitmap is whatever the
guest framebuffer is, 1:1, so reading the canvas gives the true screen regardless of the window size.

## Consequences for automation

* Prefer **xterm.js (node shell, or VM serial)** whenever the target can: exact text through `term.buffer`, no OCR,
  deterministic. A VM can get a serial console without changing the guest only if its kernel already uses ttyS0.
* For graphical consoles (noVNC, Netcup SCP) read the canvas with `toDataURL` (1:1 with the guest framebuffer); use
  `resize=off` or ignore CSS scaling; blink is limited to the cursor cell box.
* Screenshots are only needed where `toDataURL` is blank (xterm.js WebGL).
