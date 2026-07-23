# Critic II

remarks, product ideas and points that were not emphasized enough:

- it wasn't clear but should be stated as requirement that a patched wings version will still be completely usable as before (without release manager)
- RCON can be assumed to be only used locally, containers do not expose a public port for it, so only for the release manager to talk to the container. 
- have a new parent slice for the release manager and place its running downloader-updaters into child-slices thus guarantee limited ressource usage? 
- `On first start, Wings calls the provider with the server UUID and validated selectors.` - it is not clear how wings knows why it should call the release manager 
- to clarify, `socket: /run/wings-providers/shared-release.sock` is the shared ressource? 
- the release manager and all its details sound nice but maybe we can build a MVP first to get the core functionality (background release creation, tmpfs for consumers, detailed log of its operations) and outline how a v2 will improve it.


# Task 

create a new proposal `wings-cgroups/shared-ramdisk-update-lifecycle-3-codex.md` and include a section specific to changes to the previous draft

# Slices/cgroup functionality

if we wanted add support for setting up and using slices and cgroups like our current patches allow (per-server per-phase min/low/high/max, bfq io weight, cpu weight, steady-phase console match, startup grace timeout, ramped application on phase change) with a parent and per-server slices:
```
CGroup /:
-.slice
├─wings.slice
│ ├─wings-mgmt.slice
│ │ └─docker-eac6b73f75b72ac82be4a82a6490dea4ead8717b2819f95e5c4bf7b1913d8496.scope …
│ │   └─4079102 /usr/bin/wings --config /etc/pterodactyl/config.yml
│ ├─wings-6c418fe79be1497187ec529f6e909f89.slice
│ │ └─docker-c278bb8e193e2d5c26af77af9f4d427a2d677e02cac6b51872ed9a88d903fb64.scope …
│ │   ├─3343932 /usr/bin/tini -g -- /entrypoint.sh
│ │   ├─3344059 /bin/bash /entrypoint.sh
│ │   └─3344528 /home/container/WS/Binaries/Linux/WSServer-Linux-Shipping Level01_Main -server -SteamServerName>
│ └─wings-b87c0a5b23874a1c8863ff23e6800a1d.slice
│   └─docker-49d127fa85ee7d9ed5f960cc58a4a56e3d2d370e9fc79cd0c8d43fa30980405d.scope …
│     ├─2082658 /usr/bin/tini -g -- /entrypoint.sh
│     ├─2082761 /bin/bash /entrypoint.sh
│     └─2083210 /home/container/WS/Binaries/Linux/WSServer-Linux-Shipping DLC_Level01_Main -server -SteamServer>
```

- would there be synergies with cgroups in wings with the release manager? clean cut for a independant cgroup proposal to extend wings?
- if you had to design the cgroups functionality with mentioned focus on general use cases for upstream wings from scratch, how would you do it (different to the current patches)?
- is the current cut into existing patches good 
- could we get cgroups defined via egg/server vars to be changed online on a running container?
  
Create either a separate proposal for cgroups in wings `wings-cgroups/shared-ramdisk-update-lifecycle-cgroups-1-codex.md` or fold it in as a another main section in the proposal you created above if you feel there is enough synergy and should be implemented together. 