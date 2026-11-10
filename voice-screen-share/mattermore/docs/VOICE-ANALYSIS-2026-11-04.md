<!-- origin: 2026-11-04; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/MATTERMORE-VOICE-ANALYSIS-2026-11-04.md -->
<!-- relation: Mattermore voice analysis -->

# Mattermore / Mattermost Calls: persistent voice, voice activation, desktop noise cancellation, echo (2026-11-04)

Companion to `MATTERMORE-CAPABILITIES-2026-11-04.md` (same directory) and `vbpub/voice-screen-share/COMPARISON.md`.
Research and design only; the single repo change is the COMPARISON.md requirements update (branch `docs-vss-requirements-20261104`, not pushed).

Method: shallow clones into `<scratchpad>/vsrc/{calls,desktop,rtcd}` (removed at the end): mattermost-plugin-calls HEAD `b3cc038` (2026-10-26; Mattermore pins v1.12.6), mattermost/desktop HEAD `3c1c5f3` (6.5.0-develop.1, Electron 45.2.1 per package.json), mattermost/rtcd HEAD; GitHub API for licences/dates; `gh api` for mattermost-mobile and jitsi-meet files. Paths are relative to the named repo.
Tags: **[V]** = read in source/API this session; **[R]** = recalled, not verified; **[T]** = needs a test on our stack.

## 0. Findings that change earlier assumptions

1. **Group-call limit is solved in source [V].** `server/enterprise/license.go:60`: `GroupCallsAllowed() = isAtLeastProfessionalLicensed() || os.Getenv("MM_CALLS_GROUP_CALLS_ALLOWED") == "true"`; the same line exists at tag v1.12.6 (checked via API). `userCanStartOrJoin` (`server/session.go:254`) refuses non-DM calls without it (`errGroupCallsNotAllowed`). `MaxCallParticipants` on-prem defaults to 0 = unlimited (`server/configuration.go:206-207`, `plugin.json` setting "hosting: on-prem"). So with the env var, stock Calls (not only Mattermore) allows 3+ participants in channels. Caveat: `server/enterprise/` carries the Mattermost Source Available licence (file `server/enterprise/LICENSE`), which says production use needs an E20 subscription: the env switch is a technical fact, the licence position is the operator's call.
2. A Calls **call does not exist without a live WebRTC session**. The first joiner creates it (`server/websocket.go:~797-840`, `len(state.sessions)==1`), the last leaver ends it (`cleanCallState`, `server/state.go:368`). There is no "never ends" mode and no server-side hold. A "persistent room" is therefore a UX over repeated start/end, not a server object.
3. **Each start creates a channel post and, for DM/GM only, push/ring** (`createCallStartedPost`, `server/plugin.go:307-364`; `sendPushNotifications` returns early unless DM or GM, `server/push_notifications.go:89`). Channels never ring [V]. The post can be a reply in an existing thread (`joinData.ThreadID`, `RootId: threadID`) [V].
4. Calls already has **speaking detection on the server** (rtcd `InitVAD` from the RTP `ssrc-audio-level` header extension, `rtcd/service/rtc/session.go:585`, `sfu.go:484-522`; events `user_voice_on/off`, `server/websocket.go:668-671`) but it only drives the speaking indicator. It does not gate anything [V].
5. On **Desktop the call lives in its own BrowserWindow** loaded from the server: `src/app/callsWidgetWindow.ts:172` `plugins/${CALLS_PLUGIN_ID}/standalone/widget.html`, `alwaysOnTop 'screen-saver'` (L323). So the plugin bundle that we build and serve IS what runs in the Desktop widget; no Desktop fork [V]. Desktop has **no `globalShortcut` use anywhere in `src/`** (grep empty) [V].
6. Mobile (`mattermost-mobile`, `app/products/calls/`) has its own Calls client (React Native; `connection/rtcd` and a `connection/livekit` folder; `audioCaptureDefaults {autoGainControl, echoCancellation, noiseSuppression: true}`). Plugin webapp code (sidebar presence, VAD, RNNoise) does NOT run on mobile [V]. The Calls plugin HEAD contains no LiveKit code (grep empty), so the mobile LiveKit path is either ahead of the plugin or for another server: watch for a Calls protocol change on upgrade [V, interpretation R].

## 1. Persistent voice channel (TeamSpeak/Discord model)

### 1.1 What Calls supports today (all [V] unless marked)
| Question | Answer | Where |
|---|---|---|
| Call that never ends / auto-starts | No. Ends when last session leaves. Auto-start needs a real RTC peer, which only a client or the job bot (needs `JobID`, `handleJoin` rejects bot joins without it) can provide | `websocket.go:741-750`, `state.go:368` |
| Join without ringing | Yes in channels: ringing only for DM/GM and only if `EnableRinging` | `push_notifications.go:75-90` |
| "Default call" per channel | Partial: per-channel enable/disable + global `DefaultEnabled` (called TestMode in the client), no "this channel is a standing room" flag | `session.go:245-275`, `configuration.go:107-111`, `api.go:445-455` |
| Presence in sidebar | Only a small "active call" icon + tooltip of participant names on the channel link (`registerSidebarChannelLinkLabelComponent(ChannelLinkLabel)`), shown while a call is ongoing; channel header shows a join button | `webapp/src/index.tsx:358`, `components/channel_link_label/component.tsx` |
| Channel toast "call started, join" | Yes (`registerChannelToastComponent(ChannelCallToast)`) | `index.tsx:360` |
| Join persistence across reload | Browser: no. The client is an in-memory `window.callsClient` created in `connectCall` (`index.tsx:707`); no sessionStorage/localStorage rejoin logic exists (the only localStorage use is the background-blur setting, `local_storage.ts`). A page reload drops the session. Desktop: the call is in a separate widget window, so reloading the main window does not drop it (architecture, `callsWidgetWindow.ts`) [T] | as listed |
| Max participants | Setting `MaxCallParticipants`, 0 = unlimited on-prem (see 0.1); practical limit is SFU CPU/bandwidth and the UI grid | `plugin.json:39-45` |
| Mobile | Standard Calls UI of the app; join from the channel; no plugin presence UI | section 0.6 |
| Switch between rooms | `SwitchCallModal` asks to leave the current call; one call per client | `index.tsx:366`, `:875-877` |
| Server-side data available to a second plugin | REST `GET /plugins/com.mattermost.calls/channels` returns active calls for channels the user is in (marked DEPRECATED but live); `GET /calls/{channel_id}/active`; WS events `custom_com.mattermost.calls_{call_start,call_end,user_joined,user_left,user_muted,user_unmuted,user_voice_on,user_voice_off,user_screen_on,...}` broadcast to the channel | `api_router.go:98-109`, `index.tsx:218-330`, `websocket.go:~833` |

### 1.2 Hooks available to a separate plugin (no Calls fork)
Webapp (Mattermost plugin registry, as used by Calls itself, `index.tsx`): `registerWebSocketEventHandler` (can subscribe to ANOTHER plugin's custom events by name: the Calls events above), `registerGlobalComponent` (always-mounted React component: dock/overlay), `registerSidebarChannelLinkLabelComponent` (per-channel label), `registerChannelHeaderButtonAction`/`registerCallButtonAction`, `registerReducer`, `registerReconnectHandler`, `registerUserSettings`, `registerSlashCommandWillBePostedHook`. [V as used in Calls; availability of each on the current server webapp is R except those Calls itself calls.]
Join/leave from outside Calls, **[V]**: (a) `window.postMessage({type: 'connectCall', channelID}, origin)` is handled in `index.tsx:864-867` (same-origin only, `ev.origin !== window.origin` check at L861) and calls `connectCall(channelID)` plus `followThread`; (b) navigating to `/<team>/channels/<name>?join_call=true` is handled by `JoinCallWatcher` (`components/join_call_watcher.tsx`, wired `index.tsx:1097-1104`); (c) slash command `/call join|leave` [R, `slash_commands.tsx` exists]. Leaving: `window.callsClient.disconnect()` [R]; `callActions` is exposed on `window` (`index.tsx:1200`) [V declared, API surface R].
Server (Go plugin API): `OnActivate`, `ServeHTTP`, `API.PublishWebSocketEvent`, KV store, `API.CreateChannel`, `MessageHasBeenPosted` hook (to hide or delete the "started a call" post in room channels), `PluginHTTP` (server-to-server request to `com.mattermost.calls` REST with a `Mattermost-User-Id` header, for example `POST /plugins/com.mattermost.calls/{channel_id}` with `{enabled:true}`, `api_router.go:99`) [hook names R].

### 1.3 Design: plugin `com.volkb.voicerooms` (separate id, own repo, own license; survives Calls bumps)
Concept: "voice room" = an ordinary public/private channel flagged as a room. Presence comes from Calls' own state; our plugin adds the UX and flag.

Server (Go, small):
- KV: set of room channel IDs; slash command `/voiceroom add|remove|list` (admin or channel admin); REST `GET /rooms` returns rooms visible to the user with current occupants (joins our set with `GET /plugins/com.mattermost.calls/channels` for that user via `PluginHTTP`; cache 2-5 s, or maintain from WS events).
- On `add`: ensure Calls is enabled for the channel (`PluginHTTP POST` above), pin a root post "Voice room" and store its post id so calls can attach to that thread (see 1.4: post spam).
- Optional `MessageHasBeenPosted` filter: delete the `custom_calls` "started a call" post in room channels (or the plugin's own thread) to avoid one post per join cycle. This is the only point where Calls' own behaviour is overridden.
Webapp (TypeScript/React):
- Redux slice `rooms{channelId -> {name, occupants[], speaking[]}}`, seeded from `GET /rooms`, kept live from Calls WS events (`call_start`, `user_joined`, `user_left`, `call_end`, `user_voice_on/off` for speaking rings) and re-seeded in `registerReconnectHandler`.
- `registerGlobalComponent(VoiceDock)`: a left-bottom or right-docked panel "Voice rooms" listing rooms, avatars of occupants with speaking ring, and **Join / Leave** buttons. Join = `window.postMessage({type:'connectCall', channelID}, window.origin)`. This does not depend on sidebar slots that might not exist.
- Optional second step: sidebar placement via `registerSidebarChannelLinkLabelComponent` showing a count per room channel, and a collapsible "Voice" category created by the plugin (categories are server API: `CreateChannelSidebarCategory` [R]).
- Auto-rejoin: store `{channelId, ts}` in `sessionStorage` on join; after reload the plugin's first mounted component calls `connectCall` again if `ts` is recent and the user setting "rejoin voice after reload" is on. Browsers may block audio autoplay until a gesture, and the mic permission prompt can reappear: [T]. Desktop does not need it (see 1.1).
- User setting (registerUserSettings): "Join voice rooms muted/unmuted", "Auto-rejoin".
- Mattermore-specific: none required beyond group-calls env var (section 0.1). Plugin id must not equal `com.mattermost.calls`.

### 1.4 Risks and unsolved edges
| Risk | Detail | Mitigation |
|---|---|---|
| Post spam | one `custom_calls` post per start/end cycle; DM-style ring not an issue | attach to the room's pinned thread (`ThreadID` is honoured, `plugin.go:347`) by sending the thread id from our join path; webapp `connectCall` only forwards `channelID` in the postMessage path (`index.tsx:866`), so full control needs either a small Calls patch (pass `title/rootId` through the message) or the `MessageHasBeenPosted` filter |
| No server-side keeper | the room is empty and "ended" when everyone leaves; `call_end` events fire; occupancy UI must tolerate flapping | design the dock around channel ids, not call ids |
| First joiner is host, host leave | host powers (mute others, end call) move; `host_controls` need a Professional licence (`HostControlsAllowed`, `license.go:55`) so on unlicensed servers there are no host controls [V] | acceptable for friendly teams |
| One call per client | switching rooms = leave + join; no multi-room listen | expected (TeamSpeak is also one channel at a time) |
| Mobile parity | no presence dock, no auto-rejoin; the standard header button still works; mobile shows the channel call badge | accept; document |
| Browser reload / tab close | call drops (1.1) | auto-rejoin (best effort) or recommend Desktop |
| Upgrade drift | we depend on the `connectCall` postMessage type, the `custom_com.mattermost.calls_*` event names and the `/channels` REST route (comment says deprecated) | pin Calls version, add a smoke test (join via postMessage) to the release gate; fall back to `?join_call=true` link |
| rtcd restart / SFU restart | calls end; clients get close/error; rejoin required | none needed for MVP |

### 1.5 Effort and prototype path
| Step | Effort | Content |
|---|---|---|
| P0 spike (1-2 days) | S | throwaway webapp-only plugin: `registerGlobalComponent` dock listing hard-coded channel ids, `GET /channels` poll every 5 s, Join button via postMessage; verifies the three assumptions (event names, postMessage origin, REST route) on our Mattermore image |
| P1 MVP (about 1 week) | S-M | server KV + `/voiceroom` + `GET /rooms`; WS-driven occupancy and speaking rings; join/leave; per-user setting; docs; ciu build/mount via the section 4 route of the capabilities report |
| P2 polish (1-2 weeks) | M | thread-attached/hidden call post (small Calls patch or filter), auto-rejoin after reload, sidebar count label, channel category "Voice", unit tests with a mocked Calls WS, browser test |
| P3 Discord parity (open ended) | L | per-user volume (Calls patch, S-M, see capabilities report section 5), global PTT (Desktop fork or companion app: L), mobile presence (needs mobile app change: not feasible) |
Overall: **M** for a usable persistent room dock (P0-P2, about 3 weeks one dev); true TeamSpeak parity is not reachable in Mattermost (no global PTT, no mobile presence, one-room-at-a-time, server-less rooms). Mumble remains the better TeamSpeak-class tool (COMPARISON.md scenarios A/B+).

## 2. Voice activation: speech only, by threshold and content

### 2.1 What Calls has today [V]
- Mute/unmute: `client.ts:901-951` `mute()` = `peer.replaceTrack(audioTrack.id, null)` + `track.enabled=false`; `unmute()` re-adds/replaces the track. Mic acquisition `getUserMedia({...defaultAudioTrackOptions})`, options `{autoGainControl, echoCancellation, noiseSuppression: true}` (`client.ts:114-118`, used at L330/L340 and device change L722-730).
- Push-to-talk: `shortcuts.ts:11,30` `space = PUSH_TO_TALK`, implemented only in the expanded/popout view (`components/expanded_view/component.tsx:376-410`): keydown unmute, keyup or window blur mute. No global hotkey (Desktop has none, section 0.5).
- No client VAD, no AudioContext/AudioWorklet in the webapp (grep). Server-side speaking indicator only (section 0.4).

### 2.2 Integration point
Same seam as noise suppression (capabilities report section 3): wrap the stream right after each `getUserMedia` (3 sites above): `MediaStreamAudioSourceNode -> [RNNoise worklet] -> [gate: GainNode driven by decision] -> MediaStreamAudioDestinationNode`, then use `destination.stream.getAudioTracks()[0]` as `this.audioTrack`. Keep the SAME output track for the life of the call and open/close by gain, never by `replaceTrack`: no renegotiation, no click from track swap, the server's `ssrc-audio-level` VAD continues to work (silence = low level). Mute stays authoritative: gate cannot open while the user-level mute (`audioTrack.enabled=false`/null sender) is on. This is the pattern Jitsi's `NoiseSuppressionEffect.ts` uses ([V] jitsi-meet `react/features/stream-effects/noise-suppression/`: `createMediaStreamSource(stream)` -> worklet -> `createMediaStreamDestination()`, `outputTrack.enabled = original.enabled`).

### 2.3 Detection options (licence/maintenance from GitHub API 2026-11-04 [V]; behaviour claims [R] unless stated)
| Technique | What it detects | Licence / status | CPU, latency | Notes |
|---|---|---|---|---|
| Energy threshold (AnalyserNode / worklet RMS) with hysteresis and hold | loudness only | none | negligible, frame = 128 samples (2.7 ms) | trivially fooled by keyboard, music, TV; needed as the first, cheap stage; open at T_on, close at T_off = T_on - 6 dB, hold 200-400 ms, attack 5-10 ms |
| RNNoise per-frame voice probability | speech vs noise, trained as part of denoising | BSD-3 (xiph/rnnoise, last push 2025-02) [V]; `rnnoise_process_frame` returns a float VAD probability ([V] `include/rnnoise.h:94`); Jitsi's `RnnoiseProcessor.calculateAudioFrameVAD` returns the "VAD score 0-1" ([V] jitsi-meet `rnnoise/RnnoiseProcessor.ts:146-184`); `@jitsi/rnnoise-wasm` Apache-2.0, pushed 2026-10-27 [V] | 10 ms frames at 48 kHz; about 1-3% of a core [R]; +10 ms | free by-product when RNNoise suppression is already in the chain: **no extra model**. Good against keyboard/fan; weak on babble, TV speech, music with vocals |
| WebRTC VAD (libfvad, GMM) | speech-likelihood, modes 0-3 | libfvad BSD-3, last push 2024-04 [V] | negligible; 10-30 ms frames | classical, mediocre in noise, no music rejection; only useful as a fallback, a WASM build is not published as a maintained npm package [R] |
| Silero VAD via `@ricky0123/vad-web` | neural speech probability per 32 ms frame (16 kHz), with redemption/pad logic | Silero MIT (v6.2.3, 2026-09-23) [V]; vad-web npm `0.0.31` ISC (2026-09-12) [V], unpacked 6.7 MB incl. ONNX runtime wasm [V]; onnxruntime MIT [V] | a few % of one core [R]; 32 ms frames plus configurable redemption; ~2 MB model | best open speech detector; singing/rap/vocal music frequently triggers, instrumental music mostly does not [R]; runs in its own AudioWorklet+WASM so check CSP and wasm loading [T] |
| YAMNet (AudioSet, 521 classes incl. "Speech", "Music", "Keyboard") | event classes, 0.975 s windows | Apache-2.0 in tensorflow/models (repo field NOASSERTION, per-file licence R) | TF.js/TFLite, 3.7M params, tens of ms per window [R] | window too long for gating; usable only as slow "music veto": close the gate when `Music` stays above threshold for >1.5 s and `Speech` below |
| Speech/music discriminators (spectral flux, 4 Hz modulation energy, ZCR, chroma stability; Essentia, inaSpeechSegmenter) | broadcast-style speech vs music segmentation | various [R] | offline-oriented | no robust real-time voice-over-music separation; speech over music is ambiguous by definition |
| Speaker verification / personal VAD (only the owner's voice) | "my voice" vs others | research models | heavy | the only real answer to TV/roommates/music-with-vocals, but a project of its own; skip |

Honest limits: none of these reliably separates "you speaking while music plays in the room" from "music only", and singing is speech-like to every model. Expect false opens on vocal music and false closes on whispering. The design must therefore expose sensitivity, a manual PTT/open-mic override, and a visible gate indicator. Keyboard noise is the case these models handle well (RNNoise and Silero both reject transient clicks, [R] behaviour, [T] to confirm with the operator's mechanical keyboard).

### 2.4 Gate design
Per 10 ms RNNoise frame: `p = vad_prob`, `e = rms_dBFS`. Open when `p >= 0.85 AND e >= T_energy` for N=3 frames (30 ms); close when `p < 0.5` for the hold time (default 300 ms, configurable 150-800 ms). Look-ahead: keep a 40-60 ms delay line in the worklet so the decision opens the gate BEFORE the first syllable emerges (adds that latency to all outgoing audio: acceptable, typical Opus path is 40-100 ms); open with a 5 ms ramp, close with a 60-120 ms ramp. Optional second stage: Silero probability fused with RNNoise (`open` if either > threshold on a user "Strict/Normal/Loose" mapping); optional YAMNet music veto. UI: sensitivity slider with live level meter and gate-state LED in `registerUserSettings` and the call widget; a "Voice activation / Open mic / Push-to-talk" selector (Mumble-like). AGC caution: Chromium AGC raises gain during silences and lifts the noise floor; keep AGC on for normal use but the gate thresholds must be calibrated post-AGC; a "calibrate for 3 s of silence" button reduces false opens.
Cost on the CPU side stays near zero when RNNoise already runs; Silero adds a second WASM runtime (a few MB, a few % CPU) [R].

### 2.5 Recommendation and effort
1. **Stage 1 (M, 1-2 weeks, shares work with noise suppression)**: RNNoise worklet + VAD-prob gate + energy hysteresis, selector Off/VAD/PTT, level meter. Requires the RNNoise patch (capabilities report section 3), so schedule as ONE patch series: `00NN-audio-pipeline` (NS + gate), then `00NN+1-vad-ui`.
2. Stage 2 (M, +1 week): Silero via vad-web as optional "Strict" mode, if Stage 1 false-opens on speech-like noise in the operator's environment.
3. Stage 3 (L, experimental): music veto using YAMNet; only if the operator's actual problem is sustained background music. Do not promise speech-only.
Order of value: VAD on RNNoise first; the rest only after listening tests (A/B recordings with keyboard, fan, music, TV).
Mumble does VAD and PTT natively (COMPARISON.md section 5), so for pure TeamSpeak-style use this feature is already covered by running Mumble.

## 3. Noise cancellation in the Windows desktop app

### 3.1 Same pipeline applies [V]
Mattermost Desktop (Electron 45.2.1 at HEAD [V package.json]) runs Calls in a separate `BrowserWindow` loading `plugins/com.mattermost.calls/standalone/widget.html` from OUR server (`src/app/callsWidgetWindow.ts:172,188-230`), preload `externalAPI.js`. The widget bundle is the plugin's `standalone` webpack entry, so an AudioWorklet pipeline compiled into the plugin appears in Desktop with no Desktop change. Users get it when they next open a call (widget reloads on each join; the Desktop caches per HTTP headers [T]). The plugin version must be higher than the prepackaged one (capabilities report section 4).

### 3.2 Differences to verify
| Topic | Desktop specifics |
|---|---|
| Electron `getUserMedia` | standard Chromium; constraints `echoCancellation/noiseSuppression/autoGainControl` honoured as in Chrome. Permission via `permissionsManager` (`src/main/security/permissionsManager.ts:45-56,109-124,147-190`): grants `media` for trusted origins; on Windows/macOS checks `systemPreferences.getMediaAccessStatus('microphone')` and asks `askForMediaAccess`; renderer-side Windows check in `NewServerModal.tsx:84` [V]. Windows privacy switch "Let desktop apps access your microphone" must be on |
| AudioWorklet and WASM | supported in current Chromium [R]; the worklet module must come from a URL the widget page may load (same origin plugin static path) or a Blob URL; widget CSP is the Mattermost server's [T]. WASM SIMD speeds RNNoise/DFN [R] |
| Screen source picker | main process `desktopCapturer.getSources` (`callsWidgetWindow.ts:545`), with macOS screen-permission checks (L518-551); plugin modal `components/screen_source_modal` [V]. Nothing for us to add |
| Global PTT hotkey | none: no `globalShortcut` in the Desktop source [V]. Window PTT works only when the popout/expanded view has focus (section 2.1). A real global hotkey needs a Desktop fork (L, signing, auto-update) or a tiny Windows companion (AutoHotkey/Rust) that talks to the plugin over `ws://<host-address>` with a session token (M, install friction) [design R] |
| Always-on-top widget | `setAlwaysOnTop(true,'screen-saver')` (L323) [V]: the call window floats over games/apps |
| Native Windows effects | Windows 11 "Voice Clarity" / audio effects (AEC, deep noise suppression exposed through the audio-effects APIs and APOs on supported devices/drivers) are applied by the OS or driver, not by the app, and are per-device; Chromium does not expose a switch for them. They may be ON underneath the app already, in which case Chromium NS + RNNoise would be a third stage [R]. Treat as an environment variable of the test, not as a feature we build on |
| Double/triple processing | when RNNoise is active, set `noiseSuppression:false` in the constraints (keep `echoCancellation:true`); watch for pumping/underwater artifacts if the OS effect is also on [T] |

### 3.3 Effort and risks
Effort: same patch as section 2/capabilities report section 3 (M); Desktop-specific test matrix adds about 2-3 days: Windows 10/11, built-in mic vs USB headset vs Bluetooth, OS effects on/off, CPU on a low-end laptop. Risks: CSP/worklet loading in the widget; widget cache staleness; battery/CPU on laptops; Windows exclusive-mode mics; plugin updates propagate only on next widget load. No Desktop fork needed for noise cancellation. A Desktop fork is only needed for global PTT, native input injection (remote control) and OS-level effects control.

## 4. Echo cancellation with speakers

### 4.1 What Calls does [V]
- Mic: `getUserMedia({audio: {autoGainControl:true, echoCancellation:true, noiseSuppression:true, deviceId...}})` (`client.ts:114-118,330,340,722-730`). That is Chromium's WebRTC audio processing module: AEC3 (adaptive filter + nonlinear suppressor + delay estimator), AGC, NS. In Electron the same Chromium stack is used.
- Playback: each remote participant gets an `HTMLAudioElement` (`document.createElement('audio')`, autoplay, `setSinkId` for the chosen output; `components/call_widget/component.tsx:417-428,1033`). Playback therefore goes through Chromium's normal audio output path, which is also where AEC3 takes its far-end reference from. This is the favourable setup [V code, R about the AEC internals].
- So "it already works" is plausible for the single-participant speakers case, but nobody has measured it: **[T]** status.

### 4.2 Known failure modes (all [R] unless noted)
1. Output device differs from the capture device's loopback path (`setSinkId` to a non-default device, USB DAC, HDMI monitor): Chromium's AEC reference is keyed to the audio output stream, and cross-device playout drift and delay can exceed what AEC3's delay estimator tracks; symptoms: echo or ducked mic. [T]
2. Bluetooth headsets: HFP/A2DP profile switching and variable latency; speaker-mic sets often have their own AEC (double processing).
3. **Shared screen audio loopback (important here)**: the Desktop path captures system audio via `getUserMedia({audio:{mandatory:{chromeMediaSource:'desktop'}}})` (`utils.ts:276-296` [V]). On Windows that is a loopback of everything played to the default output, including the other participants' voices, and it is sent back into the call as screen audio (separate Opus track, `sfu.go:432-436` `trackTypeScreenAudio` [V]). Participants then hear their own voice (delayed, through the SFU) as echo from the sharer, and AEC cannot cancel it because it is not in the mic path. Mitigation: share audio only when needed, use headphones for that session, or patch the Desktop path to exclude the call window audio (Electron `loopbackWithMute` or per-process loopback options [R]); [T].
4. Two devices in the same room both in the call (laptop + phone): each hears the other's playout; AEC cannot cancel across devices; needs mute discipline or one audio device.
5. Firefox: has its own AEC (WebRTC AEC3 in recent versions [R]); `noiseSuppression` and simulcast screen differences already noted; behaviour differs from Chromium; test separately.
6. Chrome tab capture + `suppressLocalAudioPlayback` and system-audio options exist in `getDisplayMedia` [R]; Calls passes only `audio: Boolean(withAudio)` (`utils.ts:301-304` [V]).
7. Very loud speakers plus a closely placed mic: AEC3 residual echo, "double talk" clipping of the near-end voice; high AGC gain worsens it.
8. Residual echo then reaching RNNoise/VAD post-AEC: residual is speech-like and may be passed or may open a VAD gate; lower gate sensitivity when speakers are used [T].

### 4.3 AEC and a custom noise-suppression worklet: order
- Browser AEC runs INSIDE capture: by the time JavaScript sees the `MediaStreamTrack`, AEC (and the browser's NS/AGC) is already applied, using the browser's own far-end reference. Therefore the safe and only practical order is **capture (AEC on) -> custom AudioWorklet (RNNoise/gate) -> destination track -> sender**. The worklet is strictly downstream of AEC and cannot disturb the reference, because AEC never sees the worklet's output. This is exactly Jitsi's shape ([V] `NoiseSuppressionEffect.ts`).
- A worklet BEFORE the browser AEC is not possible with `getUserMedia` constraints (there is no hook between the microphone and the browser's audio processing). The only way to put custom processing "before" is `echoCancellation:false`, then running your own AEC with your own reference signal (the remote mix you play) in WASM: a speex/WebRTC-AEC3 port exists [R] but alignment, drift, clock skew and `setSinkId` latency become your problem. Not recommended.
- What does break AEC: (a) turning `echoCancellation` off to "avoid double processing" when RNNoise is on (only turn NS off, never AEC); (b) playing the remote audio through a separate `AudioContext` with a different sink or an extra delay buffer instead of the plain `<audio>` element (e.g. a per-user-volume feature implemented with `createMediaStreamSource -> GainNode -> destination` can fail on some Chromium versions unless the stream is also attached to a muted `<audio>` element [R]); keep per-user volume via `HTMLAudioElement.volume` when possible; (c) routing the call's playout to a device other than the one AEC uses [T]; (d) adding large playout latency (hundreds of ms) with a buffered effect on the RECEIVE side.
- Worklet-produced tracks (`MediaStreamAudioDestinationNode`) bypass the browser's audio processing on the send side [R], so there is no double-AEC and the earlier note that a "custom worklet on the processed track can break AEC reference alignment" is not accurate for the post-AEC placement: the reference alignment is internal to capture; what can break it is changing the PLAYBACK path or disabling AEC.
- Recommendation: keep `echoCancellation:true` always; `noiseSuppression:false` only when RNNoise is on; keep `autoGainControl:true`; worklet after capture; remote audio stays on `HTMLAudioElement`; put any receive-side processing (volume) after the element, not before it.

### 4.4 Acceptance tests for speakers use (to run on the stack as is, BEFORE and AFTER adding the worklet)
Setup: operator on speakers (named model, volume 50% and 80%), remote participant on headphones, both recorded locally (Audacity/OBS) plus the remote's received audio.
1. Single-talk far end: remote talks 60 s; remote must not hear own voice (echo return loss: measure the remote's recording of the received stream: residual echo below about -40 dB relative to the talker, or "inaudible" in a blind listen).
2. Double talk: both talk for 30 s; operator's voice must not be clipped or gated for more than 200 ms per sentence.
3. Output device matrix: default output, explicit `setSinkId` USB/HDMI device, Bluetooth speaker.
4. Volume sweep 30/60/100%; mic gain at OS default.
5. Screen share with audio ON from Windows Desktop: does the remote hear own voice back (failure mode 3)? Expected FAIL on current code: record it.
6. Firefox and Chrome browser clients vs Desktop widget, same sequence.
7. Pipeline A/B: baseline (browser NS+AEC), RNNoise on with `noiseSuppression:false`, RNNoise on with `noiseSuppression:true` (double): confirm no echo regression and no artifacts.
8. Gate on: confirm residual echo does not open the VAD gate (RNNoise p stays below threshold on playout-only audio).
9. Keyboard typing while far end talks: no clicks transmitted.
Acceptance: tests 1-4 and 6-9 pass on the operator's real hardware; test 5 either passes or is documented as "use headphones for share-audio sessions" with the patch proposed in 4.2.3.

## 5. Effort and order summary
| # | Feature | Effort | Order | Risk | Dependency |
|---|---|---|---|---|---|
| 0 | Group calls (env var) | S | before anything | licence position of `server/enterprise` (operator) | none |
| A | Echo verification on speakers (tests only) | S (1 day) | first (baseline) | none | none |
| B | RNNoise noise suppression (browser + Desktop widget) | M (1-2 wk) | 1 | CSP/worklet loading, CPU, double processing | Calls patch series, own bundle |
| C | Voice activation (RNNoise VAD gate; optional Silero) | M (+1-2 wk after B), Silero +M, music veto L | 2 | false opens on vocal music, onset clipping, AGC interplay | B |
| D | Persistent voice channel plugin (dock, presence, join) | S spike, M full (about 3 wk) | 3 (independent, can run parallel to B/C) | dependence on Calls internals (postMessage, WS names), post spam, no mobile parity | separate plugin id |
| E | Global PTT / per-user volume | S-M volume, L global PTT | later | Desktop fork for global PTT | none |
Overall judgement: B+C are the highest-value, lowest-risk pair (one patch series, one test campaign). D is a nice-to-have that cannot reach TeamSpeak parity; if persistent rooms are the main need, Mumble does it today.

## 6. Sources
- Calls plugin: `server/enterprise/license.go`, `server/session.go`, `server/websocket.go`, `server/state.go`, `server/plugin.go`, `server/push_notifications.go`, `server/api_router.go`, `server/configuration.go`, `plugin.json`, `webapp/src/{index.tsx,client.ts,utils.ts,shortcuts.ts,local_storage.ts,components/{call_widget,expanded_view,channel_link_label,join_call_watcher.tsx}}` at https://github.com/mattermost/mattermost-plugin-calls (HEAD b3cc038; v1.12.6 checked for the env var)
- rtcd `service/rtc/{session.go,sfu.go}`: https://github.com/mattermost/rtcd
- Desktop `src/app/callsWidgetWindow.ts`, `src/main/security/permissionsManager.ts`, `package.json` (electron 45.2.1): https://github.com/mattermost/desktop (3c1c5f3)
- Mobile `app/products/calls/connection/livekit/connection.ts`: https://github.com/mattermost/mattermost-mobile
- Jitsi `react/features/stream-effects/{noise-suppression,rnnoise}`: https://github.com/jitsi/jitsi-meet ; https://github.com/jitsi/rnnoise-wasm
- https://github.com/xiph/rnnoise (`include/rnnoise.h`), https://github.com/snakers4/silero-vad, https://github.com/ricky0123/vad (npm @ricky0123/vad-web 0.0.31), https://github.com/dpirch/libfvad, https://github.com/microsoft/onnxruntime
- Not verified, recalled: Windows Voice Clarity/audio-effects behaviour, Chromium AEC internals and `setSinkId` interaction, Firefox AEC3, YAMNet specifics, CPU figures, Electron loopback options.
