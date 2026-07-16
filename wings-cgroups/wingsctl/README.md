# The wrapper: `wingsctl.py`

Wrapper script around the wings API, place next to the `docker-compose.yml` of the wings container.

Installed, executable, stdlib-only (no pip), reads the node token from `/etc/pterodactyl/config.yml` at runtime and never prints it. From your root console:

```python
python3 /root/ptero-wings/wingsctl.py list                 # all servers + state
python3 /root/ptero-wings/wingsctl.py status <uuid>
python3 /root/ptero-wings/wingsctl.py power  <uuid> stop   # start|stop|restart|kill
python3 /root/ptero-wings/wingsctl.py logs   <uuid> 100
```

It talks to wings through the traefik route (`https://wings.gstammtisch.dchive.de`, valid cert — override with `--url` or `WINGS_URL` if you ever bypass the proxy). I verified list, status, and the actual power stop end-to-end; logs is best-effort (untested endpoint variant).

★ Insight ─────────────────────────────────────
Two things learned probing wings' API: (1) modern wings authorizes the node API with the bare token from config.yml — sending the panel-style token_id.token gets a 403, not a 401, which is wings telling you "wrong value" vs "no header" — so the script tries bare-token first with the combined form as fallback. (2) Power actions return 202 immediately; the egg's stop sequence runs async, hence the docker wait to confirm the actual exit.
─────────────────────────────────────────────────


## stopping containers the right way

wings runs a crash-detection watchdog — the RCON `SaveAndExit` makes the game exit (with code `110`, nonzero at that)

```bash
root@gstammtisch:/usr/local/sbin# ./exec-soulmask-rcon.sh SaveAndExit 30
[rcon] container: 3f8c08c30ff2 (b87c0a5b-2387-4a1c-8863-ff23e6800a1d)
[rcon] connection test: List_OnlinePlayers
[rcon] connection OK
[rcon] > SaveAndExit 30
World is closing (110)...
```

... but wings' desired state is still "running," so it counts as a crash and restarts it. 

The right way is to tell wings to stop the server, which flips the desired state so nothing restarts. Wings has no per-server power CLI, but its HTTP API (the same one the panel uses) accepts the node token from `config.yml` — perfect for a small wrapper. Let me look at your compose setup first.

Soulmask is stopped and staying stopped — Exited (130), wings reports state=offline, and no watchdog restart because the desired state is now offline. 