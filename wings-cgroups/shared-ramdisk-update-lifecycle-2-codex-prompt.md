
# Critic I 

remarks, product ideas and points that were not emphasized enough:

- storage alternatives: a main pain point in production was the use of the regular file system. even though the game server did no IO itself, e.g. docker image operation on the host would negatively impact server reponsiveness hard. we concluded this was because of eviction of pak files from page cache and thus we chose tmpfs for isolation in combination with the zswap cold page out funcktionality.
- while we try to create a solution towards our actual soulmask servers, it should be kept as general as possible. we aim at getting required/beneficiary wings patches approved upstream and integrated. thus they need to solve general use cases and appeal to the maintainers. the soulmask-part is free to our design, but in the same way it would be nice to be reusable for other game servers / use cases, so it should be a general solution with a soulmask game server/cluster as its application.
- your solution should assume as starting point a vanilla wings. although we do have some patches for wings, it would be perfectly fine to redefine new required patches/functionality against vanilla wings. we can create a v2 wings patches branch.
- so to abstract it comes down to this, correct?
  - patches for wings to improve it in general, add missing functionality
  - 3rd party tooling usable by e.g. patched wings (shared tmpfs layer, consistent updates, rollback/snaptshot, multiple RO consumers)
  - soulmask-specific configuration (paths in image, steam app-id, ...)
- `root-owned cluster config` it would be very user-friendly if configuration could alternatively rely on eggs/server variables. e.g. how does adding a server look like, what steps need to be taken?
- `The manager requires every configured cluster consumer to be offline.` - if the manager runs  a update transaction "get new game files" into a new directory it could do so while games are running in the background? any restart on a server container would just return the latest release. using a wings per-server variable toggle each server could request the latest or previous known good version to be given. 
- `CLIENT starts only after MAIN is steady` who guarantees that? we still want the wings users to have control over their servers, e.g. issue restart any time. use egg vars and wings logic vs requiring wings to talk to a 3rd party manager? 
- did we consider this (and want to use it?): any service we run on the host could access wings API (using credentials in wings config file). are there scenarios where this is usually not acceptable so we might dismiss it? 
  - a cluster update could then be controlled by CLI tool call for our manager (controlled stop of containers, run manager update/release, controlled start)
  - auto-detection of available steam updates? manager could connect to servers via RCON and send message? (see our `exec-soulmask-rcon.sh`, even `SaveAndExit` but wings will interpret volunatary shutdown as crash and restart). so stop container through wings API, apply update and restart
  