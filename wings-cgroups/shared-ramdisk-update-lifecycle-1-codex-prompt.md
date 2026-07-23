# Environment

You are on a native host with mixed use (prod game server, dev host as remote side of VSC, 20+ spawned containers), you can get passwordless root. we want to use cgroups and slices for ressource guarantees or limitations to allow prod game server experience. we are hard memory constrained and thus want to employ every option to reduce effective RAM needed or use it more effectively (KSM incl zero pages, ZSWAP with disk paging, shared ramdisk layer for game server files to protect from file cache eviction and, forced KSM opt-in via shim, ... )

We do have 2 game servers currently active:
b87c0a5b-2387-4a1c-8863-ff23e6800a1d (DLC_Level01_Main, shall become main server)
6c418fe7-9be1-4971-87ec-529f6e909f89 (Level01_Main, shall become client)

the game state in `world.db` and settings `GameXishu.json` must not be touched or lost. 

we already run a custom patched wings version `wings-local:1.13.1-cgroup.11 ` to allow use of customized egg variables. see `wings-cgroups`, the latest patches/image version is not used, yet. 

wings compose definition used in `/root/ptero-wings-patched-cgroups/docker-compose.yml`, used wings config in `/etc/pterodactyl/config.yml`.

related files:

Egg (game_stuff/soulmask/egg-soulmask-rcon-ksm-cgroups.json): 

SOULMASK.md: new "Cluster wiring — MAIN/CLIENT" section with the resolved networking answer, the concrete values to set (MAIN_SERVER_PORT=7900, the DNS-name form of CLIENT_SERVER_CONNECT), and the -serverid risk caveat.

MEASUREMENTS.md M7: full 9-row data table across the whole session, with the ramp-up/negative-profit explanation, the cross-instance-restart-disturbance finding (restarting one disturbs the other's readings), the "fresh-boot numbers are inflated by template memory" explanation, and your CoW-divergence hypothesis written up as the confirmed mechanism — plus a revised decision procedure (wait for flattening, don't restart mid-measurement).

wings-ps.sh deployed to /usr/local/sbin/ and mirrored in the repo — gives you real panel names in docker ps-style output going forward.

Soulmask2 (b87c0a5b) and Soulmask2b (6c418fe7) both running with LD_PRELOAD active, KSM profit positive on both


# Problem statement

We have a old system using a single ram disk to hold a copy of the pak files to be mounted into the container. then we aimed to improve it and map more folders and files via ramdisks. then architectural problems became apparent. the discussion below reflects that process and give you context. 


# Goal / Task

weigh alternative architectures / solutions towards a automated, error-avoiding, error-detecting, robust, easy maintainable, elegant setup. it would be perfect if wings could intelligently and safe start containers, handle (or call to a service) to perform supporting operations (e.g. mount, ramdisk handling). at the same time it needs to be secure, wings is running public. 

support for the update lifecycle of the game server (and its companion) is essential. e.g. game updates need to be persisted on disk and we probably should ensure if using ramdisk(s), that all files modified during game updates are part of the mapping to guarantee update modification happen holistically to disc or not at all.

we *do* want to improve wings where needed (the latest patches might not be what we want. also other patches can be modified.) . inspect wings code `https://github.com/pterodactyl/wings/` as needed. 

create a detailed analysis document `wings-cgroups/shared-ramdisk-update-lifecycle-1-codex.md` and propose different solutions.

interview me as needed. be open minded. the attached conversation is only for context and understand system state, not to guide you towards a solution. 


# Past conversation log

We are running a prod soulmask game server via pterodactyl wings and want to run a compananion 


This matches how §9b describes the service: it's a boot-time/reconcile mechanism (Before=docker.service, discovers targets from instances.d/*.env), not something triggered by a Panel-driven server create. Since the new instance's instances.d/<uuid>.env almost certainly didn't exist yet when that service last ran, it never got the tmpfs treatment. This needs a manual fix on the host — add the instance's entry and re-run/restart the ramdisk service — and I'd guess (not certain, since I can't read the script) that it needs the new container stopped while the bind-mount is established under its volume path, since a running container has already snapshotted its own mount namespace. That's a call for whoever can read soulmask-pak-ramdisk.sh on the host — happy to review it if you paste it or grant a way for me to read it.

ksm-optin.so is only on the existing instance's volume, and isn't wired up on either. find shows /home/container/ksm-optin.so present on b87c0a5b but absent on the new instance. And on b87c0a5b, the running WSServer process has no LD_PRELOAD in its environment — so the shim isn't active despite being present. Kernel-wide ksm/run=1 (scanning is on) but pages_shared=0/pages_sharing=0 — nothing has opted in yet on either instance. So: not started on either side yet, contrary to what "has been copied into it, yet" might have suggested — it's present but inert on one, absent on the other.

Disk reality for the "more files than just paks are identical" observation — breakdown per instance:

┌─────────────┬────────────────┬─────────────────────┐
│             │ new (6c418fe7) │ existing (b87c0a5b) │
├─────────────┼────────────────┼─────────────────────┤
│ WS/Content  │ 1.7G           │ 1.7G                │
├─────────────┼────────────────┼─────────────────────┤
│ WS/Binaries │ 138M           │ 138M                │
├─────────────┼────────────────┼─────────────────────┤
│ WS/Config   │ 4.2M           │ 4.2M                │
├─────────────┼────────────────┼─────────────────────┤
│ WS/Saved    │ 344K (fresh)   │ 255M (world data)   │
└─────────────┴────────────────┴─────────────────────┘

So ~1.84G of Content + Binaries is byte-identical between instances today and fully duplicated on disk (only Saved and Config are legitimately per-instance). Disk itself isn't tight (129G free), but this matters directly for your KSM goal: KSM only merges anonymous pages (heap/stack/anon-mmap) — it cannot touch file-backed page cache. Two independent on-disk copies of the same binary/asset content, even if byte-identical, live at different inodes and get cached in RAM as two separate page-cache copies; KSM never sees them. That's exactly why the pak file specifically got pulled onto one shared tmpfs earlier — it's the only mechanism that dedups file-backed content in RAM.

Pros/cons: how to handle the shared install tree

A. Independent installs (status quo for the new instance) — let each server steamcmd update itself.
Simplest, zero extra tooling, no risk of one instance's update touching a file a sibling still has open, versions can diverge if you ever want staged testing. Cost: ~1.8G duplicated disk (trivial here) and, more importantly, duplicated page cache in RAM for that content — no amount of KSM recovers this, since it's file-backed, not anonymous.

B. Extend the existing shared-tmpfs/bind-mount pattern from Paks to the whole read-only tree (Binaries + rest of Content), writable mounts only for Saved + Config.
This is the direct generalization of what's already proven and running for the pak file. Deterministic, zero CPU cost, and — unlike KSM — actually reaches page cache, which is most of the duplicated bytes here. Downside: couples both instances to the same game version (can't test one instance on a newer build while the other stays put), and needs a real "golden copy" update flow — either one instance's volume is canonical and steamcmd-updates normally with the other read-only-bind-mounting from it (fragile: deleting/reinstalling instance 1 breaks instance 2), or a separate golden directory outside both volumes gets updated once and both bind-mount it read-only (symmetric, closer to what the pak-ramdisk tooling already does, but is more work to build).

C. Hardlink farm (cp -al) instead of bind mounts.
Since both volumes sit on the same ext4 filesystem, hardlinking Binaries+Content genuinely shares the same inode — and page cache is keyed by inode, so this gets the same RAM-dedup benefit as B with zero new mount infrastructure. But ext4 has no reflink, and steamcmd updates typically do write-new-then-atomic-rename, which silently breaks the hardlink and reverts to independent copies until the next manual re-sync — easy to forget, and you won't notice until you check disk usage or page-cache RAM again.

My take: given your stated goal is specifically to measure KSM's RAM savings, doing A (leave Binaries/Content duplicated) and then running KSM will understate what's achievable and muddy the measurement, because the ~1.8G file-backed duplication sits entirely outside what KSM can report on. I'd fix the pak-ramdisk gap for this instance first (that mechanism already exists and works), and separately decide whether Binaries/Content are worth generalizing into B — probably yes eventually, but it's not blocking: MEASUREMENTS.md §M7 already scopes the KSM number to anonymous memory specifically, so you can run the KSM measurement now with duplicated Binaries/Content and just know that number is only the anon-heap slice, not total possible savings. Don't reach for C — the silent-decay-on-update failure mode is the kind of thing that quietly invalidates a measurement without any error surfacing.

Concrete next steps, in order:
1. Get the new instance's instances.d/<uuid>.env created (mirror the existing one) and the pak-ramdisk service re-applied to it — needs host access I don't have from here.
2. Copy ksm-optin.so onto the new instance's volume too.
3. Prefix the Startup Command with LD_PRELOAD=/home/container/ksm-optin.so on both instances (currently neither is opted in — the .so sitting unused on b87c0a5b isn't doing anything).
4. Then run both with players on for ≥1h and read /sys/kernel/mm/ksm/* per the M7 decision rule.


New parallel system (old pak scripts completely untouched):
- soulmask-static-ramdisk-setup.sh / -teardown.sh, soulmask-static-ramdisk.service, soulmask-static.slice — mirrors the pak pattern's structure exactly, but generalized: the actual list of what to share lives in /etc/gstammtisch/static-ramdisk-paths.conf, not hardcoded in the script, so adding more later is a one-line config edit, not a code change.
- Scope (per your answer): Engine, WS/Binaries, linux64, Steam directories + steamclient.so, libsteamwebrtc.so files — ~380M total, skipping the tiny .txt/.sh files.
- STATIC_RAMDISK=1 added as a new opt-in key (parallel to PAK_RAMDISK) in instance-defaults.env and both instances.d files — both soulmask2 and soulmask2b are opted in.

Deployed and verified live: 12 bind targets (6 paths × 2 instances) confirmed as tmpfs mounts. Caught and fixed one real mistake along the way — I initially ran the setup script directly instead of via systemctl start, which meant the copy was charged to my own shell's cgroup instead of soulmask-static.slice; fixed by tearing down and redoing it properly through systemd (confirmed via the unit's own Mem peak accounting).

---

lets walk through how a game update works if we have the ramdisk(s) activated. doesnt the steam udpate write into the ramdisk and on container restart the updated content is gone?

Good instinct, and the actual mechanics are worse than "content reverts on restart" — and it's live now: both instances have AUTO_UPDATE=1, so this isn't hypothetical, it'll matter the next time Soulmask ships a patch.

What actually happens, precisely: A bind mount isn't a snapshot or a copy-on-write layer — it's the same underlying filesystem visible at multiple paths simultaneously. Any write through any path bound to it modifies the one real storage, and every other path sharing that mount sees the change immediately, whether the write is in-place or steamcmd's usual write-temp-then-rename pattern. This is fundamentally different from the jdupes hardlink approach already documented for disk-based sharing — a hardlink is a directory-entry relationship: when one instance's steamcmd renames a new file over the old one, only that instance's directory entry gets repointed to the new inode; the sibling's entry is untouched, so hardlinks safely (if silently) diverge on update, needing a periodic re-jdupes to re-establish sharing. A bind mount has no such divergence protection — there's only one real directory, period.

So the concrete failure mode isn't "gone on restart," it's:
1. Whichever instance's steamcmd runs first writes directly into the shared tmpfs, instantly changing what every sibling instance sees too — including one that's still running, and including instances whose own steamcmd hasn't validated or even started that update.
2. Each instance's own steamcmd bookkeeping (steamcmd/, steamapps/*.acf) is — correctly — not shared, so it has no idea the files under it just changed from a sibling's update. That instance's manifest state and its actual files can now disagree.
3. Worse: since the bind mount masks whatever's really underneath it, each instance's own original copy sitting under the mount never gets touched by its own steamcmd while sharing is active — it just goes stale. If sharing is ever turned off for that instance, it reverts to a version that's been silently rotting since the ramdisk was set up.
4. If two instances' auto-updates happen to fire around the same time, there's a real write race into the same shared storage.

This applies equally to the pre-existing pak-ramdisk system, not just the one I just built — it's never actually been exercised against a real game update either; the setup script's only nod to updates is "raise SOULMASK_RAMDISK_SIZE," which doesn't address any of this.

Verified the actual entrypoint.sh (pulled straight from the stopped container rather than assuming) — this answers your third question precisely:

if [ -z "${AUTO_UPDATE}" ] || [ "${AUTO_UPDATE}" == "1" ]; then
    ./steamcmd/steamcmd.sh ... +app_update ${SRCDS_APPID} ... +quit
fi

AUTO_UPDATE=0 is a total, clean bypass — steamcmd isn't even invoked, so its own self-update (of the steamcmd tool itself, under /home/container/steamcmd/ — correctly not shared by either ramdisk) never runs either. No partial-update edge case. The game just launches against whatever's currently on disk.

"How to update now": since AUTO_UPDATE=1 re-checks on every boot, the trigger is just restarting that instance via the Panel — same as any ordinary Pterodactyl steamcmd egg, ramdisk or not.

Now, your idea — designate one instance as the sole writer, let it write straight into the shared tmpfs since it's already bind-mounted there, and the sibling inherits it automatically — is actually correct and simpler than what I was proposing. Here's why it works cleanly: the owner instance is bind-mounted to the shared tmpfs, so when its steamcmd writes to e.g. WS/Binaries, that write lands in the one real shared storage, and every sibling sharing that same bind sees it instantly — no separate "refresh the ramdisk" step needed at all. My earlier three-option framing assumed we'd need an explicit resync step; we don't, if only one instance ever writes.

Concrete design:
- soulmask2/b87c0a5b (cluster MAIN) = permanent update owner, AUTO_UPDATE=1 (already the case, no change).
- soulmask2b/6c418fe7 and any future sharing instance = AUTO_UPDATE=0 permanently (Panel edit needed — currently 1). It never touches these files itself again; its own steamcmd//steamapps/ manifest bookkeeping is simply never consulted, so there's no desync to worry about.
- This also has a nice side benefit: only one instance ever downloads from Steam instead of every instance redundantly re-fetching identical content.

One real remaining risk, and one operational discipline point:
1. Race on concurrent boot: if a non-owner starts fresh while the owner's update is mid-write, it could read a half-written file. Rule: when you know (or suspect) an update is pending, restart the owner first, wait for it to reach steady/running, then restart siblings. I can build a small sequencing helper for this if you want it automated rather than manual discipline.
2. Tmpfs size isn't re-checked after updates — the owner writes directly through the bind, completely bypassing my setup script's size-check logic, which only ran once at initial population. If a future update grows Engine/Binaries past the 1G tmpfs budget, steamcmd would hit ENOSPC mid-update. Tmpfs can be grown live (mount -o remount,size=X) without unmounting, so this is fixable, but nothing currently watches for it.

On Wings: no changes needed for this design — it's just an AUTO_UPDATE value per server plus start-ordering discipline, both already fully supported. A future Wings patch could add a "wait for this other server to reach steady before starting" dependency hint, but that's a bigger lift for a problem this design already avoids by not needing automatic reordering.

---

we do have the event when the server is registered. (also that means the server is don e with its load), cant we double-use this to trigger starting any registered child container? we would add to the egg in a var the id of the 2nd server, if the main server is registered it would trigger starting the other server (or more, make it a list). we would configure the 2nd server to be non-autostart. 

This is a genuinely good idea, and simpler than it might first appear: Wings already has the exact instrumentation needed. WINGS_CG_STEADY_MATCH is precisely "the server finished loading and registered" — the same console-line trigger that ends the cgroup startup phase. Reusing that single event to also fire "start these other server UUIDs" avoids inventing a second signal, and it directly kills the update-race I flagged: if the client is gated on the main reaching steady (not just started), it can never start mid-update. It also generalizes past just updates — it's a clean solution for ordinary cluster start-ordering too.

---

just another idea to run by you. i find it kind of ugly that we have the whole ramdisk things in separate service, manual container config. couldnt we integrate this into wings as well? based on set vars (paths) before container creation the ramdisk/bindmount will be created - solved? the 2nd container adds as reference the main container and can use the ramdisk mounmts as well. run this by the running opus maybe as well

This is a much bigger idea than the first one — worth thinking through before committing engineering time to it.

My take: the direction is genuinely appealing, and not just for aesthetics. If Wings itself set up the bind-mount as part of its own Environment.Create() flow (based on server variables), it would actually be more correct, not just cleaner — the current host-script approach only runs once at boot (Before=docker.service), which is exactly why we had to manually re-run soulmask-pak-ramdisk-setup.sh when the second instance was added mid-session. If Wings did this on every container create/recreate, that gap disappears entirely — any newly added server picks it up automatically, no manual step.

But it's a meaningfully bigger and riskier change than the child-server-start patch, for a few reasons:
1. Wings would need to perform actual mount/umount operations on the host — a materially more powerful (and more dangerous if buggy) capability than the systemd/D-Bus cgroup property writes this patch stack does today.
2. It needs real lifecycle/reference-counting design: when does the shared tmpfs get created vs. reused vs. torn down, especially across server deletion (not just start/stop) — that's a harder problem than the child-start feature's fire-and-forget trigger.
3. The "reference the main container's ramdisk" mechanic needs Wings to resolve another server by UUID and track whether its ramdisk already exists — similar shape to the WINGS_CG_CHILD_SERVERS list-of-UUIDs pattern already being built, but with real teardown semantics on top.

I don't think this should be bundled into the currently-running agent's task (it's mid-implementation on a different, well-scoped feature) — this deserves its own dedicated design pass first, not a rushed implementation. Let me get Opus's architectural take on it now, in parallel, as a design/feasibility assessment rather than a build — using the Plan agent type specifically so it explores the real Wings source and produces a design recommendation without writing any code yet.

Meanwhile, the ramdisk-in-Wings design review came back, and it's a clear, well-grounded verdict: don't move the actual mount execution into Wings.

Why, concretely (not just "risky"):
- The whole patch stack works today because Wings never touches host state directly — it's distroless (no shell, no mount/umount binaries), unprivileged (no CAP_SYS_ADMIN, confirmed by reading the actual Dockerfile and deployment compose files), and everything privileged goes through D-Bus to systemd instead of raw syscalls. Mount operations need CAP_SYS_ADMIN and have to happen in the host mount namespace, not Wings' own — that's a real architectural break, not a style preference.
- It found the actual hook point (container.go's Create(), right before ContainerCreate) and confirmed the ordering logic would genuinely work — but also found the hard part I hadn't fully appreciated: teardown. Unlike the child-servers feature, a shared tmpfs needs reference counting across every server that uses it, and Wings has no existing mechanism for that (systemd tracks its own transient units; nothing tracks a mount's owners). Getting that wrong risks unmounting a tmpfs a running server still depends on — data disappearing mid-session, not just a missed optimization.
- It also caught a design flaw in my proposed asymmetric "client references main by UUID" approach: our existing host script is actually symmetric (first opted-in volume with the content wins as source), which avoids exactly the ordering dependency and single-point-of-failure my version would have introduced.

Its recommendation — a much smaller "Phase 0" that captures most of the value: don't reimplement the mount logic in Wings at all. Just have Wings trigger the existing, proven host service over D-Bus (the same systemd connection the cgroup patches already use) synchronously at container-create time. That alone fixes both real gotchas we hit this session — the "new instance needs a manual re-run" problem and the "wrong cgroup charge if invoked outside systemctl" problem — with about the same risk profile as the child-servers patch, since it's just telling systemd to do what it already knows how to do, not new privileged code.

--- 

reuse the opus agent that already worked on wings and have it implement its suggestion. meanwhile explain it again more detailed, how the flow works , what is happening. my other was that if there is no mount in wings, we could have running service/container it could talk to to order the ramdisk things to be done (we can limit what service can do/will accept). i just want to get away from manual things on the host

Good instinct — and it turns out your idea and the recommended "Phase 0" are the same underlying pattern, just with an existing implementation of the "narrow-capability service" already sitting there. Let me walk through the flow in detail, then explain why.

How the Phase 0 flow actually works, step by step

Today, without Wings involved at all: a host-level systemd unit (soulmask-static-ramdisk.service, Before=docker.service) runs exactly once, at host boot. It reads the opt-in config files, creates a shared tmpfs, copies the static content into it once, and bind-mounts it into every currently-configured server's volume paths. This only runs at boot — a server added afterward is invisible to it until someone manually re-triggers the script (the exact pain point we hit twice this session).

With Phase 0: nothing about how the ramdisk gets built changes — the host script stays exactly as it is. What changes is when it runs and who triggers it:

1. A server is about to be created — this happens far more often than you'd think: initial install, every ordinary Stop→Start, every Panel restart, and Wings' own boot sequence all funnel through the same Create() function in Wings (confirmed by the design review — Wings actually destroys and recreates the container on every start, not just the first one).
2. Right before Wings asks Docker to actually create that container (specifically, before the container's /home/container bind mount gets established), Wings checks: does this server opt into ramdisk sharing?
3. If yes, Wings uses its existing D-Bus connection to systemd — the same one it already uses to set cgroup slice properties — to ask systemd to (re)start the soulmask-static-ramdisk.service unit. This is a single, narrow request: "run this specific, already-defined unit," nothing more expressive than that.
4. Wings waits for systemd to confirm that unit's ExecStart actually finished (the cgroup code already has this exact "block until the D-Bus job completes" pattern, so it's not new machinery) — with a timeout, and a decision to just proceed with whatever's on disk if it times out or fails, rather than refuse to start the server over a missing optimization.
5. Only once that's confirmed does Wings let Docker actually create the container. Docker's bind of /home/container is recursive and captures whatever's nested underneath at that exact moment — so the now-freshly-(re)established tmpfs bind is guaranteed to be there.
6. Because this happens on every server creation, not just at boot, a server added six months from now gets the exact same treatment automatically, the very first time Wings creates its container — zero manual host steps.
7. Because the unit is started through systemd rather than by directly invoking the script, systemd forks the ExecStart process under the unit's own Slice= — so the "wrong cgroup gets charged" mistake I made earlier this session structurally can't happen again; there's no other way to invoke it now.

What Phase 0 does not solve is teardown/reference-counting (when to actually tear the shared tmpfs down once servers start being deleted) — that's still an open problem, but a lower-stakes one, since even unsolved, the actual mount logic stays in the already-proven host script; Wings is only ever the trigger, never the implementer.

Your "separate limited service" idea

This is genuinely the same idea — you've essentially independently arrived at Phase 0's shape. The key realization: systemd already is that narrow-capability service. Wings' D-Bus connection to it can't run arbitrary commands — it can only ask systemd to operate on units by name, and only units systemd already knows about from static unit files on disk. It's not "Wings gains a mount capability"; it's "Wings asks an already-privileged process to do something that process is already configured to do." That's a materially narrower and safer interface than building a bespoke daemon from scratch, which would mean reinventing process supervision, an IPC/API surface, and access control — things systemd already does, battle-tested, for free.

Where I'd push further on your "limit what it will accept" instinct, though: right now the design would still let an egg variable specify which unit to trigger, and if that's a free-text string, a compromised or misconfigured server could in principle ask Wings to restart any systemd unit on the host — not what we want. This codebase already has the exact precedent for fixing that: WINGS_CGROUP_PARENT (an existing variable) is validated against a node-level allowlist (docker.allowed_cgroup_parents) before Wings will honor it, rejecting anything outside it with a logged warning instead of acting on it. I'll have the agent apply the identical pattern here — Wings only ever triggers a small, node-configured allowlist of unit names, never an arbitrary string from the egg.

--- 

`creates a shared tmpfs, copies the static content into it once, and bind-mounts it into every currently-configured server's volume paths` this does not sound like if someone writes to the tmpfs, the content goes persisted to disk? but this is what we would need for the update path of main server? otherwise explain tmpfs/bindmount concept here on more details.

You've caught something real and important — this isn't a nitpick, it's a genuine gap in the design, and it applies to the already-running-in-production pak-ramdisk too, not just the new one. Let me explain the mechanics precisely, because the distinction matters a lot here.

tmpfs: what it actually is

tmpfs is a RAM-backed filesystem — its content lives in kernel page cache / anonymous memory, not on any physical disk block device (it can get pushed to swap under memory pressure, but that's just RAM overflow, not a persistence mechanism — swap isn't consulted to "restore" anything after a reboot). There is no disk behind it at all. If the tmpfs is unmounted, or the host reboots, everything in it is gone, permanently, with nothing to recover from.

bind mount: what it actually is

A bind mount doesn't copy anything — it makes the same underlying storage visible at a second path. There's exactly one real copy of the data; the bind mount is just another door into the same room. Write through either door, and you're writing the same bytes, visible instantly through both doors.

Why "owner writes directly through the bind" seemed to work, and why it's actually broken

When I described the single-update-owner design, I said the owner's steamcmd writes land directly in the shared tmpfs, propagating to siblings for free — that part is true. What I got wrong: that write only ever touches the tmpfs. It never touches the real, on-disk copy that's sitting underneath the bind mount, masked and completely untouched. That real copy is frozen at whatever state it was in the moment ramdisk-sharing was first turned on for that server — before any of this session's updates.

So the actual state of the world right now is:
- The tmpfs holds whatever's currently "live" — correct today, since no real Soulmask update has happened yet this session (every check has been "already up to date").
- The real disk underneath every server's bind mount, owner included, is stuck at the pre-sharing version, silently rotting.

The tmpfs does survive ordinary container restarts (Panel restart, crash recovery) — that's not the problem, since the tmpfs mount itself is independent of any single container's lifecycle. The problem is specifically: a host reboot, or any event that tears down and rebuilds the ramdisk (which is exactly what the setup script does at every boot), destroys the tmpfs and repopulates it from whichever server's real on-disk copy it finds — which, after an update happened only in the tmpfs, is the stale pre-update version. A routine reboot weeks after an update would silently revert the game to an old version, on every sharing instance, all at once — and it could get worse than that: the owner's own steamcmd bookkeeping (steamapps/*.acf, correctly not shared) would still say "already up to date," since a lightweight +app_update check normally trusts its own manifest rather than re-verifying every file's actual checksum — so it might not even notice or fix the reversion on its next boot. That's a silent, self-perpetuating mismatch, not just a one-time lost update.

This is a real gap in the already-deployed pak-ramdisk too, not just the new static one — it just hasn't bitten us yet because no real update has happened during this whole engagement.

What the update flow actually needs

Writes that need to survive a reboot have to land on real, persistent disk — RAM alone can't do that, no matter how we bind-mount it. So the correct sequence is closer to what I originally sketched before I oversimplified it:

1. Before the owner's steamcmd runs, detach it from the shared tmpfs (unmount its bind), exposing its own real on-disk copy underneath.
2. Let steamcmd update that real, persistent copy directly.
3. Refresh the shared tmpfs from that now-updated real copy (re-copy the changed files into the same tmpfs, in place).
4. Re-attach the owner's bind (and siblings never needed to move — they're already bound to the same tmpfs, so once step 3 lands, they see the new content automatically).

This is genuinely more work than either running agent's current task covers — the child-servers patch and the D-Bus ramdisk-trigger patch both help with starting things in the right order, but neither touches how an update gets promoted from ephemeral tmpfs to persistent storage. That's a separate, still-open problem, and given it also affects the pak-ramdisk already running in production, I'd treat it as a priority rather than something to defer indefinitely. Want me to think through a concrete design for this now, or wait until the two running agents report back and fold it into the same review pass?

---

