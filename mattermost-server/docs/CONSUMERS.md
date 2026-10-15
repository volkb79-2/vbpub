# Using Mattermost and Calls

This guide is for members using the self-hosted instance and operators
maintaining the media path. The main [README](../README.md) describes the
service; the [design guide](DESIGN-GUIDE.md) explains its network and privacy
choices.

## Who can sign in

The service is reachable at
[`https://mattermost.gstammtisch.dchive.de`](https://mattermost.gstammtisch.dchive.de).
This is an externally accessible endpoint with managed membership. Public
self-signup is disabled. Users and credentials are created and issued by the
operators through the repository's provisioning workflow.

## Start an audio call or share a screen

In a channel, use the Calls control in the channel header to start or join a
call. Grant the browser or desktop client microphone permission when asked.
During the call, use the screen-share control and choose the window or display
to share. The feature is available in supported browser and desktop clients.

The deployed Team Edition supports 1:1 audio calls and screen sharing with a
40-minute call limit. Group calls require a higher Mattermost plan. See the
[Mattermost Calls end-user guide](https://docs.mattermost.com/end-user-guide/collaborate/make-calls.html)
for client-specific controls.

## Network access required

Allow inbound TCP 443 for the web app and WebSocket signaling. Also allow
inbound UDP 8443 for preferred media and TCP 8443 as a media fallback. TCP and
UDP can use the same public port. This host forwards those media connections
through the tls-edge container to Mattermost; the Mattermost app does not
publish its own host ports. The alternate HTTPS endpoint remains on TCP 9443.

The host firewall or provider firewall must allow both protocols on 8443 for
clients to reach Calls. If TCP/UDP 8443 is blocked on a client network, the
call may signal successfully over HTTPS while media fails. The live host's
source config is under `tls-edge/ciu-stack/`; it is rendered and then deployed
from `/root/tls-edge-LIVE` with host-level Docker Compose, not CIU.

## Verify reachability

From a client network, confirm the web service responds:

```sh
curl -fsS https://mattermost.gstammtisch.dchive.de/api/v4/system/ping
```

Then place a test call between two accounts and confirm audio in both
directions and that a shared screen is visible. Those client checks verify the
complete signaling/media path; a successful HTTPS response alone does not
prove UDP or TCP media is reachable.
