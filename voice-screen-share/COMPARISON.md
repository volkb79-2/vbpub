# Voice / chat / screen-share / remote-control: solution comparison

Scope: choose what to run, as ciu projects below `voice-screen-share/`, for a small technical team.
Status date: 2026-11-04. Release data re-checked against the GitHub releases API on 2026-11-03.
Host-agnostic: placeholders `<host>`, `<public-ip>` only.

## 0. Legend and evidence levels

| Mark | Meaning |
|---|---|
| ✓ | supported, as documented by the project |
| partial | works with limits (stated in the note) |
| ✗ | not offered |
| ? | not established |
| **[V]** | verified live 2026-11-03 (GitHub API, vendor docs, Docker Hub/ghcr) |
| **[S]** | seen in a search summary or secondary source only |
| **[R]** | recall, unverified |
| **[C]** | claim from the operator's source conversation, not re-checked |

Terms: **RC** = remote control of another person's screen (mouse and keyboard), as in RustDesk or Teams give/request control.
**Always-on voice** = permanent channels where joining means talking (TeamSpeak/Discord model).
**Noise suppression** = neural filter (RNNoise class); a noise gate or browser default echo/noise processing is `partial`.

## 1. Requirements

| # | Requirement | Hard / soft | Acceptance (see section 8) |
|---|---|---|---|
| R1 | Markdown chat (channels, threads, code blocks) | hard | post formatted message, edit, search |
| R2 | Voice with 3+ participants | hard | 3 accounts in one call, 30 min |
| R3 | Noise cancellation (neural, not only a gate) | hard | keyboard/fan listening test |
| R4 | Screen sharing to a group | hard | one sharer, two viewers |
| R5 | Screen REMOTE CONTROL with consent (request or give) | hard, the usual gap | viewer asks, sharer accepts, control works, revoke works |
| R6 | TeamSpeak/Discord-like always-on voice channels | soft | join channel, hear present users, switch text without leaving |
| R7 | Free, self-hostable, one instance, no federation | hard | ciu project, no SaaS dependency |
| R8 | Operable on the shared 8-core host (caps on every service, one gate at a time) | hard | memory/CPU limits in compose |

Finding that drives everything: **no single product covers R1-R6.** Chat+voice products give no remote control;
remote-control products give no chat or voice. Only Jitsi Meet Electron puts RC inside a call.

### 1b. Operator requirements (stated 2026-11-04) and coverage

The operator's own list, made explicit. Each item maps to the numbered requirements above and adds the acceptance wording
used by the voice analysis (`reports/MATTERMORE-VOICE-ANALYSIS-2026-11-04.md`, kept with the release-program reports).

| ID | Requirement | Maps to | Status of knowledge |
|---|---|---|---|
| O1 | Markdown chat (channels, threads, code blocks) | R1 | needed |
| O2 | **Persistent voice channels**: standing rooms, join/leave in one click, visible occupants, TeamSpeak/Discord feel | R6 (raised from soft to operator-stated) | needed |
| O3 | **Voice activation that reacts to speech only**: volume threshold plus content detection, not music or keyboard noise | new | needed |
| O4 | **Noise cancellation**, including in the **Windows desktop app** | R3 plus Windows client | needed |
| O5 | **Echo cancellation with speakers** (no headphones) | new | operator believes it already works: **to verify** (tests in the voice analysis, section 4.4) |
| O6 | Screen sharing to a group | R4 | needed |
| O7 | Remote control of a shared screen, with consent | R5 | needed |
| O8 | Self-hosted, deployed via ciu | R7, R8 | needed |

Coverage by solution. Flags: **V** verified (source or vendor docs read), **R** recall, **?** not established, **T** needs a test on our stack.
Cells say what the solution does today without custom code unless "plugin" or "patch" is named.

| Solution | O1 md chat | O2 persistent rooms | O3 speech-only activation | O4 noise cancel (Windows desktop client) | O5 echo cancel, speakers | O6 screen share | O7 remote control | O8 self-host/ciu |
|---|---|---|---|---|---|---|---|---|
| Mattermost TE + Calls | ✓ [V] | ✗ per-call, ring only DM/GM, small active-call icon [V source] | ✗ mute and PTT (PTT only in popout, no global key) [V]; server-side speaking indicator only [V] | partial: browser NS only [V]; Desktop runs plugin bundle in its own widget window [V] | partial: `echoCancellation:true` + playout via `<audio>` [V code]; effect **T**; share-audio loopback may echo on Windows [V code, R effect] | ✓ one sharer per call [V] | ✗ [V] | ✓ exists; 3+ participants need the env var `MM_CALLS_GROUP_CALLS_ALLOWED=true` or a paid licence [V source] |
| Mattermore (fork) | ✓ [V] | ✗ same as above; plugin route M (design in the analysis) | ✗; RNNoise-VAD gate patch M [design R] | partial; RNNoise AudioWorklet patch M, works in the Desktop widget without a Desktop fork [V mechanism] | as Mattermost; keep `echoCancellation` on, worklet strictly after capture [design] | ✓ [V] | ✗; RustDesk handoff S, agent M-L [design] | ✓ AGPL; own plugin bundle needs a higher plugin version [V] |
| Mumble | ✗ | ✓ best fit [V] | ✓ amplitude/SNR VAD and PTT, global hotkeys on the desktop client [R] | ✓ client RNNoise [V] | partial: client echo cancellation option [R], **T** | ✗ | ✗ | ✓ S |
| Jitsi + Electron | partial in-meeting chat | partial: fixed room names, no presence [V] | ✗ no gate; talk-while-muted hint only [R] | ✓ RNNoise toggle [R], Electron on Windows [V] | ✓ browser AEC [R], **T** | ✓ | ✓ Electron both ends [V] | ✓ M |
| RustDesk OSS | partial 1:1 | ✗ | ✗ | ✗ | ✗ | ✓ view | ✓ [V] | ✓ S |
| Matrix + LiveKit (Cinny/Element) | ✓ | partial: voice/video rooms [C] | ✗ [?] | partial: browser NS only [S] | browser AEC [R], **T** | ✓ | ✗ | ✓ L |
| Fluxer | ✓ | ✓ Discord model [V docs] | ? | partial: RNNoise proposed [C] | browser AEC [R], **T** | ✓ | ? | ✓ L |
| Stoat | ✓ | ✓ Discord model [V] | ? | partial | browser AEC [R], **T** | ✓ | ✗ | ✓ L |
| **Scenario A** (Mattermost + Mumble + RustDesk + Jitsi) | ✓ | ✓ Mumble | ✓ Mumble VAD/PTT [R] | ✓ Mumble client | partial **T** | ✓ Jitsi | ✓ RustDesk or Jitsi Electron | ✓ |
| **Mattermore + custom plugins** (voice dock, audio pipeline, control relay) | ✓ | M plugin, no mobile presence, no global PTT [design] | M-L [design] | M [design] | partial **T** | ✓ | S prototype, M-L real [design] | ✓ AGPL, patch repo |

Reading the table: no single product meets O2, O3, O4 (Windows) and O7 together. Mumble covers O2-O4 without customisation; RustDesk or Jitsi Electron
covers O7; Mattermost covers O1, O6. O5 is untested everywhere and is checked first (a one-day test), because the same pipeline decisions
(noise suppression after the browser's echo canceller, never instead of it) depend on it.
Correction to the matrix in section 2: the "3+ participants need a paid plan" question is settled in source: the Calls server accepts non-DM calls
when `MM_CALLS_GROUP_CALLS_ALLOWED=true` (present at tag v1.12.6); `server/enterprise/` carries the Source Available licence, so the licence position
is a separate operator decision.

## 2. Capability matrix, part A: functions

Rows are candidates that can be part of the answer. `RC` column states consent model and client requirement.

| Solution | Markdown chat | Persistent voice rooms | Group calls (limit) | Screen share | Remote control | Noise suppression |
|---|---|---|---|---|---|---|
| **Mattermost Team Edition + Calls** (live 11.10.1; latest 12.0.1 [V]) | ✓ full md, threads | ✗ calls attach to a channel, no occupancy UX | **unresolved**: docs say 3+ needs a paid plan [V]; other docs say "unlimited, recommended 50" [S]; test | ✓ 1:1 free [V]; group per limit | ✗ (same prototype route as Mattermore) | partial: browser-level only (`noiseSuppression` constraint, no neural filter) [V source] |
| **Mattermore** (patch-set fork, v1.6.2 [V]) | ✓ same as Mattermost v12 | ✗ same call model | ✓ unlocks group audio 3+ via `MM_CALLS_GROUP_CALLS_ALLOWED` (unverified in practice); group video "being ported" | ✓ one sharer per call, VP8/AV1, no transcoding (section 11) | ✗ native; prototype route: plugin hands off a `rustdesk://` link after sharer approval (S), real agent M-L | partial: browser-level only, upgradeable with an RNNoise AudioWorklet patch (M) |
| **Mumble** (server+client, v1.5.939 [V]) | ✗ (text-only side chat) | **✓ best TeamSpeak feel**: channel tree, PTT/VAD, ACLs | ✓ tens of users, `MUMBLE_CONFIG_USERS` | ✗ | ✗ | **✓ client RNNoise** [V mumble.info 1.5.629 RC3 blog] |
| **Jitsi Meet + Jitsi Meet Electron** (Electron v2026.10.0 [V]) | partial: in-meeting chat only | partial: permanent room names, no presence | ✓ JVB SFU, dozens (hardware bound) | ✓ web and Electron | **✓ Electron only, since 2026.8.0, native consent dialog; both ends need the desktop app** [V release notes] | ✓ RNNoise in web app [R] |
| **RustDesk OSS** (client 1.5.1, server 1.1.16 [V]) | partial: built-in 1:1 text chat | ✗ | ✗ (1:1 sessions, multi-viewer unverified) | ✓ view only | **✓ core feature, per-session accept or ID+password, P2P with relay fallback** | ✗ (audio forwarded raw) |
| **Matrix: Synapse/Tuwunel + Element Web/Cinny + LiveKit** | ✓ md | partial: Cinny voice/video rooms (4.11.1+ [C]); no always-on presence feel | ✓ MatrixRTC on LiveKit, dozens | ✓ | ✗ | partial: browser defaults; Krisp is LiveKit-Cloud-only [S] |
| **Fluxer** (2026.1102.205049, 2026-11-02 [V]) | ✓ md-like | **✓ Discord model**, visible occupants | ✓ LiveKit | ✓ | ? not verified | partial: RNNoise proposed upstream, inclusion unconfirmed [C] |
| **Stoat** (ex Revolt; repo v0.16.2 [V]) | ✓ md | **✓ Discord model** (LiveKit voice-ingress) | ✓ | ✓ (screenshare audio issues reported [S]) | ✗ | partial: browser dependent |
| **Zulip** (12.3, 2026-09-21 [V]) | ✓ md, topic threads | ✗ | via Jitsi/BigBlueButton | via Jitsi | via Jitsi Electron | via Jitsi |
| **MeshCentral** (1.3.1, 2026-10-23 [V]) | ✗ | ✗ | ✗ | ✓ view | **✓ browser-based remote desktop; agent installed; attended and unattended** | ✗ |
| **Rocket.Chat** (8.9.0, 2026-10-05 [V]) | ✓ md | ✗ | via Jitsi (video conference) [R] | via Jitsi | via Jitsi Electron | via Jitsi |

Row notes:
- Mattermost 3-participant limit: the first thing to settle empirically; it decides whether Mattermore is needed at all.
- Mattermore/Calls internals (screen share, RC, noise suppression, build and mount): section 11.

## 3. Capability matrix, part B: security, access, clients, licence, maintenance

| Solution | E2EE | SSO / guests | Clients | Licence | Latest release (verified 2026-11-03) / maintenance |
|---|---|---|---|---|---|
| Mattermost TE + Calls | ✗ (TLS only) | SSO largely paid; guests paid [R] | web, desktop, mobile | MIT/AGPL server, Calls plugin source-available [R] | 12.0.1 (2026-10-22), vendor-backed |
| Mattermore | ✗ | ✓ OIDC SSO + guests unlocked; LDAP/SAML stay locked | same as Mattermost; image amd64 only | AGPL-3.0 | v1.6.2 (2026-10-23), ~10 releases in 2 months, **single maintainer, 9 stars**, rebase lag vs upstream |
| Mumble | ✗ for voice (transport encrypted, server sees audio) | server password + ACL, no SSO | desktop (Win/Linux/macOS), mobile apps (third party) | BSD-3 | v1.5.939 (2026-10-11), long-lived, healthy |
| Jitsi + Electron | optional insertable-streams E2EE in browsers [R] | `ENABLE_AUTH=1` + guests; JWT/OIDC possible [R] | web, Electron (Win/Linux/macOS), mobile apps | Apache-2.0 | Electron v2026.10.0 (2026-10-21); docker-jitsi-meet stable-11248-3 on GitHub 2026-11-02; RC was once removed and restored |
| RustDesk OSS | ✓ session E2E, keyed server (`ENCRYPTED_ONLY`) | none in OSS (SSO/address book Pro-only [V]) | Win/Linux/macOS/Android/iOS, web client (limited) | AGPL-3.0 | client 1.5.1 (2026-11-01), server 1.1.16 (2026-07-20) |
| Matrix stack | ✓ for text (Olm/Megolm); calls E2EE via MatrixRTC [R] | OIDC (MAS) or password; guests optional | web, Element X mobile, Element Desktop (repo archived [V]: use web), Cinny | AGPL-3.0 (Synapse, Element, Cinny); Apache-2.0 (Tuwunel, LiveKit) | Synapse v1.164.2 (2026-11-02), Cinny v4.12.8 (2026-10-25), LiveKit v1.13.11 (2026-10-25), Tuwunel v1.9.4 (2026-10-10) |
| Fluxer | ? (E2EE calls planned later in 2026 [C]) | own accounts; SSO ? | web, native desktop (self-hosted support since 2026-10-12 [C]) | AGPL-3.0 | 2026.1102 (2026-11-02), 10.7k stars [V]; young |
| Stoat | ✗ | own accounts | web/PWA dependable; native apps may not accept self-hosted [C] | AGPL-3.0 | v0.16.2 (2026-10-19); self-hosted compose images still v0.15.5 [V]; docs thin |
| Zulip | ✗ | SSO ✓, guests ✓ | web, desktop, mobile | Apache-2.0 | 12.3 (2026-09-21) |
| MeshCentral | agent channel TLS | users/2FA built in | browser; agents on Win/Linux/macOS | Apache-2.0 | 1.3.1 (2026-10-23) |
| Rocket.Chat | optional E2EE rooms [R] | SSO mostly paid | web, desktop, mobile | MIT core, enterprise parts proprietary [R] | 8.9.0 (2026-10-05) |

## 4. Capability matrix, part C: operations

| Solution | Ports and UDP need | RAM/CPU class | Services | ciu effort | tls-edge change | Key risks |
|---|---|---|---|---|---|---|
| Mattermost TE (+Calls) | 443 HTTPS; Calls media **8443 TCP+UDP**; live edge also has alt HTTPS 9443 | ~1 GB, medium | 2 (app + postgres) | exists (S, moved into this area) | none; Calls entrypoints already live | 3-party limit unresolved; major-version upgrade path |
| Mattermore | same as Mattermost; media 8443 needed but tryout publishes none | ~1 GB | 2 | tryout skeleton exists (S to start) | none for HTTPS; media must not share 8443 with live Calls | single maintainer; v12 data not compatible with live 11.10.1 DB: separate DB only |
| Mumble | **64738 TCP+UDP**, own TLS, no HTTP | ~50 MB, tiny | 1 | **S** | new TCP+UDP entrypoints or direct publish | no chat/screen; UDP firewall |
| Jitsi | web 443 behind edge; **JVB 10000/udp** (+4443/tcp fallback); `JVB_ADVERTISE_IPS=<public-ip>` | ~1.5-2 GB, JVB scales with participants | 4 (web, prosody, jicofo, jvb) | **M** | HTTP none; UDP 10000 entrypoint | RC needs Electron on both ends; Linux/Wayland untested; env option names partly recall |
| RustDesk | **21115/tcp, 21116/tcp+udp, 21117/tcp** (+21118/21119 web client) | ~50-128 MB | 2 (hbbs, hbbr) or 1 (s6 image) | **S** | raw TCP/UDP entrypoints (UDP has no hostname match) or direct publish | key file loss breaks every client; no OSS web console |
| Matrix stack | 443 HTTP routes; **LiveKit 7881/tcp, 7882/udp**; WS 7880 behind edge | ~2-3 GB | 5-6 (synapse, postgres, element/cinny, livekit, lk-jwt-service) | **L** | UDP 7882 + TCP 7881 entrypoints | cross-client call compatibility; MatrixRTC flags and versions must align; no RC |
| Fluxer | 80/443/7881 TCP, 443/7882 UDP (or behind-proxy 127.0.0.1:8080 + 7881/7882) | 2 cores / 4 GB minimum [V docs.fluxer.com/operator] | compose bundle, many | **L** (own proxy assumptions) | UDP 443 and 7882 collide with edge/Matrix: trial alone | young; calls do not work over an HTTP-only tunnel; needs TURN reachable by clients |
| Stoat | caddy 80/443; LiveKit 7881/tcp + 50000-50100/udp | ~12 services + mongo, valkey, rabbitmq, minio | ~12-15 | **L** | UDP range entrypoints | thin docs; voice-button patch reports [S]; heavy |
| Zulip | 443 | 2-4 GB | 4-6 | M | none | no voice of its own |
| MeshCentral | 443 behind proxy (`REVERSE_PROXY` / `TLSOffload`) | small Node | 1 | S-M | none | admin/fleet oriented, heavier ceremony than RustDesk |
| Rocket.Chat | 443 | 2-4 GB + mongo | 2-3 | M | none | licence not fully OSS; no voice |

Effort scale: S = one service, compose + defaults, under a day; M = several services/secrets, 1-3 days; L = multi-service with own
database, proxy routes and media wiring, a week or more including live test.

## 5. Noise suppression: where it runs

| Fact | Evidence |
|---|---|
| Neural suppression is a **client** function almost everywhere; an SFU forwards encoded audio and cannot add it | [V] architecture |
| Mumble client: RNNoise-based suppression plus VAD/PTT | [V] mumble.info 1.5.629 RC3 blog |
| Jitsi web app ships RNNoise suppression (toggle in audio settings) | [R] |
| LiveKit clients (Matrix, Fluxer, Stoat): browser built-in WebRTC noise suppression/echo cancel; Krisp is LiveKit-Cloud only, not self-hosted | [S] docs.livekit.io |
| RNNoise: BSD-3, v0.2 (2024-04), repo idle since 2025-02: stable, unmaintained. DeepFilterNet: v0.5.6 (2023-08), dormant | [V] |
| A noise gate (Havok) is not neural suppression | agreed |
| Mattermost/Mattermore: Calls passes only browser `noiseSuppression`; an RNNoise AudioWorklet patch is the upgrade (section 11.3) | [V] source |
| Workstation-level fallback: NVIDIA Broadcast/RTX Voice, or PipeWire `filter-chain` with RNNoise/DeepFilterNet LADSPA | [R] |

Consequence: R3 is met by choosing clients with a built-in neural filter (Mumble, Jitsi) or by an OS-level filter on every
workstation. No server choice changes it.

## 6. Ruled out or deferred

| Candidate | Reason | Evidence |
|---|---|---|
| Mostlymatter (Framasoft fork) | removes user/message limits only; nothing says it changes call limits; adds fork maintenance. "Mattermore" is a different project (section 2) | [S] |
| TeamSpeak 6 | proprietary, still beta, final licensing unannounced; Mumble does the job | [S] |
| Apache Guacamole | clientless gateway to RDP/VNC/SSH, not peer share-and-control; needs a VNC/RDP server on every target; DB schema is yours to initialise | [V] docs 1.6.0 |
| Sunshine + Moonlight | host streams desktop with GPU encode; superb latency but 1 host to few clients, pairing PIN, not a conference tool | [V] repos, ports [S] |
| NoMachine | proprietary, business licence terms unverified | [R] |
| Kasm Workspaces CE | containerised desktops; community edition capped at 5 sessions, non-commercial; not peer control | [S] |
| noVNC | building block, no consent/request model | [R] |
| DWService | no self-hostable server found | [S] |
| Remotely | unmaintained: last release 2024-08-12, last push 2024-12; GPL-3.0 | [V] |
| Chrome Remote Desktop, Teams | proprietary, vendor-hosted | excluded by free + self-host |
| Spacebar | Discord-API clone, voice maturity unverified | [S] |
| Zam, Havok, Jorvik (Matrix clients) | beta/alpha, 10 stars or less, custom voice stacks that may not interoperate | [C], Zam [V] |
| Element Call standalone app | being phased out for in-app MatrixRTC | [S] |
| Conduit, Dendrite homeservers | beta; Tuwunel/Continuwuity are active successors | [C] |
| ESS Community | Helm/Kubernetes packaging aimed at 1-100 non-commercial users | [C] |
| Backspace (Discord clone) | advertised RNNoise; not researched further | [C] |

## 7. Combination scenarios

Coverage uses ✓/partial/✗ against R1-R6.

| Scenario | R1 chat | R2 3+ voice | R3 noise | R4 share | R5 RC | R6 always-on | Misses |
|---|---|---|---|---|---|---|---|
| **A. Mattermost + Mumble + RustDesk + Jitsi** | ✓ | ✓ (Mumble, Jitsi) | ✓ (Mumble client, Jitsi) | ✓ (Jitsi) | ✓ (RustDesk; Jitsi Electron in-call) | ✓ (Mumble) | four apps, no single window; Calls limit moot |
| A-lite. Mattermost + Mumble + RustDesk | ✓ | ✓ | ✓ (Mumble only) | partial (Calls 1:1, or group if limit allows) | ✓ | ✓ | no group screen share unless limit lifted |
| **B. Mattermore only** | ✓ | ✓ audio 3+ | partial | ✓ | ✗ | ✗ | RC, always-on, neural noise; single maintainer |
| B+. Mattermore + Mumble + RustDesk | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | replaces Jitsi by Mattermore for group share; fork risk |
| **C. Matrix only** (Tuwunel/Synapse + Cinny + LiveKit) | ✓ | ✓ | partial | ✓ | ✗ | partial | RC; presence UX client-specific |
| C+. Matrix + Mumble + RustDesk | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | replaces Mattermost; biggest build |
| **D. Fluxer only** | ✓ | ✓ | partial | ✓ | ✗ (?) | ✓ | RC; maturity; own proxy; E2EE later |
| D+. Fluxer + RustDesk | ✓ | ✓ | partial | ✓ | ✓ | ✓ | neural noise unconfirmed |
| E. Jitsi Electron only | partial | ✓ | ✓ | ✓ | ✓ | partial | no real chat or presence |

| Scenario | Operational cost (RAM, services) | Migration effort from today's Mattermost |
|---|---|---|
| A | ~2.2 GB extra, 7 containers, 3 new UDP/TCP entrypoint sets | none: purely additive, each piece removable |
| A-lite | ~0.4 GB extra, 3 containers | none |
| B | ~1 GB, 2 containers, swap of the app image | medium: v12 vs live 11.10.1 means a fresh DB or a tested upgrade; users re-onboard |
| B+ | B + Mumble/RustDesk | medium (as B) |
| C | ~2-3 GB, 5-6 containers, 2 media entrypoints | high: users, history and bots re-created; no importer assumed |
| C+ | C + ~0.4 GB | high |
| D | 4 GB minimum, bundle with own proxy | high, and trial must be isolated (port clashes) |
| E | ~2 GB, 4 containers | none, but weakest chat |

## 8. Port plan for the voice-screen-share area

Today's edge: 80/443 HTTP(S), **9443/tcp alt HTTPS**, **8443/tcp and 8443/udp Mattermost Calls** (media pair 8444 internal).
Reserve numbers once, for all projects.

| Service | Port | Proto | Exposure | Route | Collision check |
|---|---|---|---|---|---|
| Mattermost / Mattermore app | 8065 | TCP | internal | Traefik Host rule on 443 | none |
| Mattermost Calls media | 8443 | TCP+UDP | public | tls-edge entrypoints (live) | taken: no other service on 8443 |
| Mattermore tryout media | any free port, never 8443/8444/9443 | TCP+UDP | LAN trial only | direct publish | do not run beside live Calls on 8443 |
| Mumble | 64738 | TCP+UDP | public | own TLS: edge TCP `HostSNI(*)` passthrough + UDP entrypoint, or direct publish | free |
| RustDesk hbbs | 21115 | TCP | public | raw entrypoint or direct publish | free |
| RustDesk hbbs | 21116 | TCP+UDP | public | UDP: one backend per port, no hostname match | free |
| RustDesk hbbr | 21117 | TCP | public | raw entrypoint or direct publish | free |
| RustDesk web client (optional) | 21118, 21119 | TCP (WS) | public | only if web client wanted | free |
| Jitsi web | 80 behind edge | TCP | internal | Host rule on 443 | none |
| Jitsi JVB | 10000 | UDP | public | new UDP entrypoint or direct publish; `JVB_ADVERTISE_IPS=<public-ip>` | free; keep out of Matrix range |
| Jitsi JVB fallback | 4443 | TCP | public (optional) | entrypoint or publish | free |
| LiveKit (Matrix trial) | 7881 | TCP | public | entrypoint | clashes with Fluxer/Stoat LiveKit if run together |
| LiveKit (Matrix trial) | 7882 | UDP | public | entrypoint | same clash; Fluxer also uses 7882/udp |
| LiveKit WS | 7880 | TCP | internal | Traefik `/livekit/sfu` | none |
| lk-jwt-service | 8080 | TCP | internal | Traefik `/livekit/jwt` | none |
| Synapse | 8008 | TCP | internal | Traefik `/_matrix`, `/.well-known/matrix/client` | none |
| Fluxer (trial) | 80/443/7881 TCP, 443/7882 UDP | | public | own proxy, `--behind-proxy --bind 127.0.0.1:8080` possible | **UDP 443 and 7882** clash with edge/Matrix |
| Stoat LiveKit (trial) | 7881 TCP, 50000-50100 UDP | | public | direct publish | 7881 clash |
| coturn (only if needed) | 3478 UDP/TCP, 5349 TLS, relay range | | public | direct | try LiveKit built-in TURN first |

Rules:
- Decide once: tls-edge media entrypoints (cleanest, but an edge change + release + host firewall + provider firewall) versus direct
  publish of ONE port per service for the trial (breaks the "no host ports on app containers" convention, trial only).
- Every media port must be open in both firewall layers and advertised as the public address.
- Never use host networking to cure a container start problem.
- Run Fluxer or Stoat alone, never beside Matrix/Jitsi.

## 9. Recommendation and ordered trial plan

Default combination: **scenario A**, built additively in three small trials. Keep Mattermost for chat. Mumble gives always-on voice
and neural noise suppression, RustDesk gives consent-based RC, Jitsi gives group screen share with in-call RC.
Matrix and Fluxer are replace-chat experiments, only after A is judged.

Alternative to A: **a custom plugin set on Mattermore** (scenario B+) instead of running Mumble, RustDesk and Jitsi separately.
What it costs (section 11): an RNNoise AudioWorklet patch for noise suppression (M, 1-2 weeks); remote control as a separate
plugin, prototype via `rustdesk://` handoff (S, still needs RustDesk) or a standalone Rust agent with consent dialog (M-L, 2-4
weeks plus hardening); per-user volume (S-M) and persistent voice channel UI (M-L) to approach Mumble; group video re-port (M).
Ongoing cost: a patch repo rebased against each upstream Calls/server bump, our own bundle with a higher plugin version, a single
maintainer upstream, and AGPL source publication. It yields one window and one login, but still no always-on voice parity with
Mumble and no in-call remote control without the agent. Recommendation: run trials 1-3 first, then build the plugins only if the
separate-apps experience is judged too fragmented.

| Order | Trial | Why this order | Effort |
|---|---|---|---|
| 0 | Mattermost Calls 3-account test on the existing project; ask the operator whether "Mattermore" was meant as the `dennisklappe` fork | 5 minutes, settles the group-call limit and whether Mattermore is needed | none |
| 1 | `voice-screen-share/mumble-server` | smallest, immediate TeamSpeak feel, covers R2/R3/R6 | S |
| 2 | `voice-screen-share/rustdesk-server` | only free consent-based RC; independent of everything else | S |
| 3 | `voice-screen-share/jitsi-server` | group share + in-call RC; needs UDP and Electron on both ends | M |
| 4 | Decide: Jitsi Electron RC vs RustDesk on Windows+Linux pairs; keep the better one or both | removes redundancy | none |
| 5 (only if replacing Mattermost) | `matrix-rtc` (Tuwunel or Synapse + Cinny + LiveKit); separately Fluxer alone | replace-chat experiments | L |

Project sketches (details reproduced from the research, all tags at least 14 days old [V] 2026-11-03):

| Project | Image pins | Key config | Resources |
|---|---|---|---|
| mumble-server | `mumblevoip/mumble-server:v1.5.939` (alt v1.6.870) | `MUMBLE_SUPERUSER_PASSWORD`, `MUMBLE_CONFIG_SERVERPASSWORD` secrets, `MUMBLE_CONFIG_USERS=30`, `MUMBLE_CONFIG_BANDWIDTH=72000`; data hostdir `/data` | 0.5 cpu / 256 MB |
| rustdesk-server | `rustdesk/rustdesk-server:1.1.16` (hbbs, hbbr) or `-s6` single container | `hbbs -r <host>:21117`, `ENCRYPTED_ONLY=1`; hostdir `/root` holds `id_ed25519(.pub)`: back up | 0.3 cpu / 128 MB total |
| jitsi-server | `jitsi/{web,prosody,jicofo,jvb}:stable-11031`; `stable-11248-3` not on Docker Hub at check time | `PUBLIC_URL`, `DISABLE_HTTPS=1`, `ENABLE_AUTH=1`, `ENABLE_GUESTS=1`, `JVB_ADVERTISE_IPS=<public-ip>`, three GEN_LOCAL secrets | web/prosody 0.5/256 MB, jicofo 0.5/512 MB, jvb 2/1 GB |

All projects follow the estate pattern: `ciu.toml` + `ciu.defaults.toml.j2` + `ciu.compose.yml.j2` + `README.md` + `run-gate.toml`;
`image` and `image_tag` separate, digest recorded; secrets via `GEN_LOCAL` and `*_FILE`; hostdirs, not named volumes; `cap_drop: ALL`,
`no-new-privileges`; mem/cpu caps on every service; one gate container at a time on the shared host.

## 10. Acceptance checks per trial

Common setup: 3 accounts on different operating systems (at least one Linux, one Windows), 30-60 minutes, one account on a
different network (mobile hotspot) to exercise NAT/UDP.

| Trial | Checks (pass criteria) |
|---|---|
| 0. Mattermost Calls | three accounts join one call: pass if the third is admitted; record the version and any "upgrade" message. Screen share by one account visible to both others |
| 1. Mumble | all three join the same channel and hear each other; join/leave audible; switch to Mattermost text without leaving the channel; **noise listening test**: with RNNoise suppression on, one person types loudly and runs a fan while talking, two listeners rate it against suppression off (A/B, same speaker, record both); mobile-hotspot user connects over UDP (not forced TCP); wrong server password refused |
| 2. RustDesk | client configured with `<host>` and public key connects; unkeyed/default client refused when `ENCRYPTED_ONLY=1`; viewer requests control, **sharer must accept per session**; mouse/keyboard work both ways; sharer can revoke mid-session and control stops; connection from the hotspot account works P2P or via relay; relay-only mode works when UDP is blocked |
| 3. Jitsi | three accounts in one room; anonymous user cannot create a room (`ENABLE_AUTH`); screen share seen by both others; **Electron remote control**: requester asks, native consent dialog appears on the sharer, accepted control works, deny and revoke work; check on Windows<->Linux; JVB advertises the public address (media flows with the hotspot account); RNNoise toggle audible in the same A/B test as Mumble; CPU and memory of JVB under 3 participants stays within the cap |
| 4. Decision | score Jitsi Electron RC vs RustDesk on: consent clarity, latency, Linux/Wayland, setup effort for a new user (minutes), firewall footprint |
| 5. Matrix / Fluxer | cross-client call compatibility: one user on Cinny, one on Element Web, one on a third client, all join the same call; persistent presence: do others see who is in the room; Fluxer: join a voice channel, hear present users, share screen, stay in voice while changing text channels; confirm media works through the chosen edge/direct-publish arrangement; neural noise present or absent |

Exit criteria for scenario A: R1-R6 each demonstrably met by at least one trial, all media ports open and documented in the section 8
table, each ciu project passes its own gate, and an independent adversarial review precedes any merge.

## 11. Mattermore / Mattermost Calls internals

Source: source-level research of the Mattermore tag v1.6.2, the Calls plugin (Mattermore pins v1.12.6), rtcd v1.2.6 and
calls-common, 2026-11-04. All items [V] in source unless marked.

### 11.1 Screen share implementation

| Aspect | Finding |
|---|---|
| Capture | browser `getDisplayMedia` (no resolution/fps constraints); Desktop app uses `getUserMedia` with `chromeMediaSource: desktop` and the plugin's own source-picker modal |
| Media stack | native `RTCPeerConnection` wrapped by `RTCPeer` in `@mattermost/calls-common`; one DataChannel for signaling |
| SFU | `rtcd` (Go, pion/webrtc), embedded in the plugin or run as a separate service; re-publishes RTP to every receiver, **no transcoding** |
| Codecs | Opus, VP8 (screen default), AV1 (optional); no VP9, no H.264 |
| Simulcast | off by default; when on: 500 kbps / 5 fps and 2.5 Mbps / 20 fps layers; Firefox falls back to one 1 Mbps / 10 fps encoding |
| Limits | **one sharer per call** (server-enforced), host can force-stop a share, share audio only where the browser/OS provides it (Windows [R]); mobile can view, not share [R] |
| Group video | **disabled in Mattermore** (patch 0002 needs a port to the Calls 1.12.5 widget rewrite) |

### 11.2 Remote control

Not available. The Calls WebSocket whitelists message types (no input events), rtcd has no application DataChannel, a browser cannot
inject OS input, and the Desktop app exposes no injection API to plugins. Closest prior art: Jitsi Meet Electron (robotjs in the
Electron main process, native consent dialog in main, input events over a channel; Apache-2.0).

| Route | What | Effort | Notes |
|---|---|---|---|
| **Prototype: RustDesk handoff** | separate small plugin: "Request control" next to a share; sharer approves; plugin posts a call-scoped `rustdesk://<id>` link; RustDesk's own accept dialog stays authoritative | **S** (2-4 days) | second app and separate capture, not inside the call; never put a permanent password in the URL; launching RustDesk as a separate process is licence-clean |
| **Real feature** | standalone Rust agent (enigo) on the sharer machine listening on localhost + separate plugin for relay over plugin WS events + consent in the agent's native dialog; view/control toggle, one controller, time limit, kill hotkey, on-screen banner | **M-L** (2-4 weeks, plus hardening) | keeps Mattermost Desktop unforked; Wayland and macOS permissions are friction; injection is RCE-adjacent so consent must be enforced natively |
| Desktop fork with built-in injector | Mattermost Desktop (Apache-2.0) plus robotjs-style module | L, ongoing | signing, auto-update, per-OS permissions: not recommended |

### 11.3 Noise suppression

Today: only browser constraints (`noiseSuppression`, echo cancel, AGC passed to `getUserMedia`); no AudioWorklet or insertable
streams; Mattermore patches do not touch audio. Browser NS is classical DSP, good for stationary noise only.

| Upgrade | Licence | Effort | Notes |
|---|---|---|---|
| `@jitsi/rnnoise-wasm` AudioWorklet before `replaceTrack` (Jitsi's own pattern) | Apache-2.0 (RNNoise BSD-3) | **M** (1-2 weeks) | new Calls patch in the Mattermore series; hook the mic paths in `client.ts` (initial, device change, unmute); setting Off/Standard; disable browser NS when on; needs CSP, Electron, Firefox/Safari checks and an A/B recording test; mobile app stays native |
| DeepFilterNet as a "High" option | MIT/Apache-2.0 | +M | better on non-stationary noise, heavier CPU; upstream idle since 2024-10 |
| Krisp, Maxine/RTX Voice | proprietary | n/a | not usable in an AGPL self-hosted stack |

Teams reference (secondary sources only [S]): sender-side deep-neural-network suppression with Auto/Low/High/Off; which model it
ships is not documented.

### 11.4 Shipping a custom build

The Calls plugin is built separately (clone upstream tag, apply `patches/calls/*`, `make dist`, re-stamp the manifest to id
`com.mattermost.calls`, version `1000.<upstream>`) and prepackaged into the image at `/mattermost/prepackaged_plugins/`. A ciu
project can therefore build its own bundle from its own patch repo and mount or `COPY` it with a **higher version** (the server only
replaces a prepackaged plugin when the new version is strictly higher). Additive features (remote-control relay, PTT UI) belong in
their own plugin id to avoid rebasing against each upstream Calls bump. Changes to codecs/layers also need forks of `calls-common`
and `rtcd` (go.mod `replace`, or run an external rtcd container). Licence: a modified Calls build is AGPL-3.0; publish the patch
repo (planned anyway).

### 11.5 Discord/TeamSpeak gaps in Calls

| Gap | State | Effort |
|---|---|---|
| Push-to-talk | exists as `space` in the popout/expanded view only; global PTT needs the Desktop app | in-window S; global L |
| Per-user volume | absent | S-M (Web Audio gain per participant) |
| Persistent voice channels with sidebar occupancy | absent; server already allows calls without ringing in channels | M-L plugin/webapp UI |
| Multiple simultaneous screen shares | absent, server hard limit of 1 | server change |
| Group video (Mattermore) | disabled | M re-port |

Cheapest wins in order: per-user volume, noise suppression, in-window PTT polish, RustDesk handoff, group-video re-port.

## 12. Evidence status and next steps

Verified live 2026-11-03: GitHub releases, licences and activity for mumble, rustdesk (+server), jitsi-meet-electron, fluxer, stoat,
synapse, cinny, livekit, tuwunel, mattermost, mattermore, zulip, Rocket.Chat, MeshCentral; Docker Hub/ghcr tags and digests
for the pinned images; rustdesk.com self-host port table; Fluxer operator docs; Guacamole docs. Search summary only: Mattermost
call limits, Mostlymatter, Kasm limits, DWService, Sunshine ports, LiveKit/Krisp, TeamSpeak 6. Recall: Jitsi RNNoise and env
option names, NoMachine terms, resource figures marked `~`, Rocket.Chat/Mattermost licence details.

Next steps:
1. Run trial 0 (3-account Mattermost call) and settle the Mattermore question.
2. Create the Mumble and RustDesk ciu projects, then Jitsi; decide the tls-edge entrypoint versus direct-publish approach once.
3. Run the section 10 checks, fill section 11, then record the scenario decision here.
