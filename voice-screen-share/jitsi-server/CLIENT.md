# Client: Jitsi Meet Electron (desktop app)

A browser can join a conference and share its screen. **Remote control needs the desktop app
on both ends** (the one who controls and the one who shares). Everything below was checked
against the official releases on 2026-11-04.

## Version

**Jitsi Meet Electron v2026.10.0**, released 2026-10-21 (14 days old on 2026-11-04, so it
meets the vetting buffer), Apache-2.0, <https://github.com/jitsi/jitsi-meet-electron/releases/tag/v2026.10.0>.

- Remote control returned in **v2026.8.0** (2026-08-04; release note: "bring back remote
  control and ask for consent with a translated native dialog", PR #1156). Use 2026.8.0 or
  later on both ends. Older builds do not have it.
- v2026.10.0 also updates bundled Electron to 43.8.3 (removes trust for the compromised
  eMudhra intermediate CA chain; the app ships its own root store, so updating is the only
  fix) and adds the `--defaultServer` command-line argument.

## Download and install

Server URL for the app: `https://jitsi.<PUBLIC_FQDN>` (the `public_host`). Download only from the GitHub release
page above (asset URLs: `https://github.com/jitsi/jitsi-meet-electron/releases/download/v2026.10.0/<asset>`). Published sha256 (from the release
assets), check before installing:

| Platform | Asset | sha256 |
|---|---|---|
| Windows | `jitsi-meet.exe` | `30e56ea530efd1eeee44ef9ef9f8db9e3d2890f1543024574de5d56bffe5d9dc` |
| macOS | `jitsi-meet.dmg` | `6e45229b85b82ec5461636f5720e1283bda094d1677cb5b753e1c024eba62d4f` |
| Linux x86_64 (AppImage) | `jitsi-meet-x86_64.AppImage` | `e0fcac6c8a560ade5ae13d518a9efe17115e5375c4a5cb02fcbf6313bfd8a4e4` |
| Linux amd64 (deb) | `jitsi-meet-amd64.deb` | `6af39ff99d83cd6d9832283133d77197a14c4df528ab3b33561350725c536357` |
| Linux arm64 | `jitsi-meet-arm64.AppImage` / `.deb` | `a7bb3f29...4a874c0` / `cdd280b7...5d372c05a` (see release page) |

```bash
# Linux example
curl -LO https://github.com/jitsi/jitsi-meet-electron/releases/download/v2026.10.0/jitsi-meet-x86_64.AppImage
echo "e0fcac6c8a560ade5ae13d518a9efe17115e5375c4a5cb02fcbf6313bfd8a4e4  jitsi-meet-x86_64.AppImage" | sha256sum -c -
chmod +x jitsi-meet-x86_64.AppImage && ./jitsi-meet-x86_64.AppImage
```

Platform notes: macOS needs the Screen Recording permission (and Accessibility for being
controlled) granted in System Settings; Linux remote control works on X11, Wayland support
depends on the compositor/portal and is not verified here.

## Point it at this server

The default server of the app is `meet.jit.si`. Change it:

1. Open the app, **Settings** (gear), field **Server URL**, enter `https://jitsi.<your-domain>`
   (the host from `public_host`; no trailing path), save.
2. Or start with `--defaultServer https://jitsi.<your-domain>` (new in 2026.10.0).
3. Or open a deeplink: `jitsi-meet://jitsi.<your-domain>/room-name`.

Log in with an account from [`USERS.md`](USERS.md) when prompted (the server requires it to
join). Each participant uses their own account.

## Screen share

In a meeting use the toolbar's share-screen button, pick a screen or window in the picker.
Share **audio** from the picker where the OS supports it. (Browsers can do this too.)

## Remote control

Both ends must run the desktop app, 2026.8.0 or later, in the same meeting.

1. The person whose screen is to be controlled **shares their screen** (entire screen or a
   window) in the meeting.
2. The other participant opens the context menu of the sharer's tile (or the screen-share
   tile) and chooses the remote-control action (**Start remote control**).
3. The sharer gets a **native consent dialog** (translated, since 2026.8.0) asking whether to
   allow the participant to control their computer. Only after **Allow** is input forwarded.
4. Either side ends it from the meeting UI; the sharer can stop sharing at any time.

The exact menu wording is taken from upstream conventions, not from a click-through on this
server: confirm it in the app and adjust this file. Remote control is peer-to-peer input
forwarding over the Jitsi data channel; it works through this server as long as media/data
reaches the JVB (UDP 10000 open).

## Noise suppression

The Jitsi Meet web app shipped by `ghcr.io/jitsi/web:stable-11248-3` includes the RNNoise worklet
(`noise-suppressor-worklet.min.js`, `rnnoise.wasm`) and the setting **"Extra noise
suppression"**. Turn it on in **Settings > Audio** (in-meeting: the "..." / more-actions menu).
It does not work together with stereo audio, and needs a microphone-level input device that is
not already denoised by the OS. Verified by the strings and files in the image; the in-app
click path is not verified here.

## Troubleshooting

- Joins, no audio/video from others: UDP 10000 blocked (host or provider firewall); see
  `README.md`.
- Login loop: the account is not registered; run `ciu up` again (hook is idempotent) and read the
  password from the credentials file named in its output.
- Certificate warning: `jitsi.<domain>` has no DNS record to the host, so the edge cannot
  issue its certificate.
