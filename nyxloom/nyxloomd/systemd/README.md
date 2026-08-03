# Host-side liveness units — the invoker CR-16 does not ship

CR-16 built a deadman and left it without a way to fire. These three units are
that way. **Nothing in this repository installs them**; installing is an
operator action, deliberately, because it changes what a host does when
nyxloom dies.

## The gap these close

CR-16 gave nyxloom a durable heartbeat and a reader that needs no live daemon:
`nyxloom doctor --liveness` opens the store directly and reports how long it
has been since each project completed a reconcile pass. That mechanism was
verified end to end against copies of all five real production stores with the
daemon stopped, and it is genuinely independent of the daemon.

Its *invocation* was not. CR-16 shipped exactly one invoker — the container
healthcheck — and **Docker does not run healthchecks on a stopped container**.
So the incident the package exists to prevent (`nyxloom-prod-nyxloomd` sitting
`Exited (143)` for ten days while the notification channel crash-looped, with
nothing reporting either) would have gone exactly as unnoticed. Worse, even
when the healthcheck does run and fail, `unhealthy` is a state in
`docker inspect` that pages nobody.

Two properties are required and neither can live inside the container:

1. **An invoker that survives the container's death.** Nothing running inside
   a container can report that the container is gone.
2. **An escalation path that is not the daemon's own transport.** CR-16's
   second acceptance criterion is that a broken notification transport is
   reported *through a different path*. Routing the alarm back through ntfy
   would collapse the two paths into one, precisely in the case where that one
   is broken.

## What is here

| unit | role |
| --- | --- |
| `nyxloom-liveness.service` | runs `nyxloom doctor --liveness` against the host's state root; non-zero exit means a deadman or tick-error-streak fired |
| `nyxloom-liveness.timer` | drives it every 5 minutes, `Persistent=true` so a suspended host catches up |
| `nyxloom-liveness-alarm@.service` | `OnFailure=` handler — journal at `emerg`, `wall`, and an operator hook |

Detection latency is bounded by the timer interval **plus** the deadman's own
threshold (`reconcile_interval_seconds` × the configured multiple), not by the
timer alone. Probing much more often than that threshold only produces
duplicate reports of one condition.

## Install

```sh
sudo install -m0644 nyxloom-liveness.service nyxloom-liveness.timer \
    nyxloom-liveness-alarm@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nyxloom-liveness.timer
```

Verify it actually fires rather than assuming it does — with the daemon
stopped, the run should FAIL and the alarm should appear:

```sh
sudo systemctl start nyxloom-liveness.service ; echo "exit=$?"
journalctl -t nyxloom-liveness-alarm -n 20 --no-pager
```

A green result here while the daemon is down means the unit is not reading the
state root you think it is. Check `NYXLOOM_STATE` in the service file: it must
be the directory the daemon container bind-mounts, as the **host** sees it.

## Choosing the second channel

The shipped default writes to the journal at `emerg` and to `wall`. That is
genuinely independent of nyxloom, Docker and the network — and on an
unattended host it is **not sufficient**: nobody is reading either.

`nyxloom-liveness-alarm@.service` therefore calls
`/etc/nyxloom/liveness-alarm-hook` if it exists, with the failing unit name as
`$1`. Make it executable and have it reach a channel that does not share a
failure domain with what it is reporting on:

- a **different** ntfy instance or topic than `nyxloomd/secrets.env` uses —
  a different topic on the same crash-looping server is not a second path;
- an SMS or paging gateway;
- a mail relay on another host.

A placeholder that merely *looked* like paging would be worse than the honest
local default, which is why one is not shipped.

## What is still not covered

The deadman reports that reconciliation stopped. It cannot report that the
**host** died — that needs something off-box, which is outside nyxloom's
scope and belongs to whatever monitors the machine itself.
