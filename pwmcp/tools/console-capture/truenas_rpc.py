"""Minimal TrueNAS JSON-RPC 2.0 websocket client (wss://HOST/api/current).

Credentials come from the environment only: TRUENAS_HOST, TRUENAS_USER, TRUENAS_PASSWORD.
CLI: truenas_rpc.py METHOD [JSON_PARAMS_ARRAY]   (prints the result as JSON)

TLS: the server certificate and host name are VERIFIED by default. A throwaway TrueNAS test install
usually has a self-signed certificate; for THAT case only (standing operator decision, 2026-10-27) set
TRUENAS_INSECURE_TLS=1, which turns verification off, and prints a warning to stderr on every
connection. The password is sent in clear inside the TLS session (PASSWORD_PLAIN), so never set the
variable for a host that holds real data. Alternative that keeps verification: TRUENAS_CA_FILE=<pem>
(trust that certificate / CA only).
"""
import asyncio, json, os, ssl, sys
import websockets

INSECURE_ENV = "TRUENAS_INSECURE_TLS"
CA_FILE_ENV = "TRUENAS_CA_FILE"
_TRUE = ("1", "true", "yes", "on")


def insecure_tls_requested(environ=None):
    """True only for an explicit opt-in value of TRUENAS_INSECURE_TLS (unset, empty, 0 and anything else: verify)."""
    env = os.environ if environ is None else environ
    return env.get(INSECURE_ENV, "").strip().lower() in _TRUE


def make_ssl_context(insecure=False, environ=None):
    """Verifying context by default (optionally trusting TRUENAS_CA_FILE); no verification only when insecure=True."""
    env = os.environ if environ is None else environ
    if insecure:
        print(f"WARNING: {INSECURE_ENV} is set: TLS certificate verification is DISABLED for the TrueNAS API "
              "connection (throwaway test infrastructure only; the password is sent over an unverified channel).",
              file=sys.stderr)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ca_file = env.get(CA_FILE_ENV, "").strip()
    return ssl.create_default_context(cafile=ca_file or None)


class RPC:
    def __init__(self, host=None, user=None, password=None):
        self.host = host or os.environ["TRUENAS_HOST"]
        self.user = user or os.environ["TRUENAS_USER"]
        self.password = password or os.environ["TRUENAS_PASSWORD"]
        self.insecure_tls = insecure_tls_requested()
        self._id = 0

    async def __aenter__(self):
        ctx = make_ssl_context(insecure=self.insecure_tls)
        self.ws = await websockets.connect(f"wss://{self.host}/api/current", ssl=ctx, max_size=None, open_timeout=30)
        ok = await self.call("auth.login_ex", {"mechanism": "PASSWORD_PLAIN", "username": self.user, "password": self.password})
        if ok.get("response_type") != "SUCCESS":
            raise RuntimeError("login failed: %s" % ok.get("response_type"))
        return self

    async def __aexit__(self, *a):
        await self.ws.close()

    async def call(self, method, *params, timeout=120):
        self._id += 1
        i = self._id
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": list(params)}))
        while True:
            m = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError(f"{method}: {json.dumps(m['error'])[:1500]}")
                return m["result"]

    async def job(self, method, *params, timeout=900):
        jid = await self.call(method, *params)
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            r = (await self.call("core.get_jobs", [["id", "=", jid]]))[0]
            if r["state"] in ("SUCCESS", "FAILED", "ABORTED"):
                if r["state"] != "SUCCESS":
                    raise RuntimeError(f"{method} job {r['state']}: {str(r.get('error'))[:800]}")
                return r.get("result")
            await asyncio.sleep(2)
        raise TimeoutError(method)


async def _main():
    async with RPC() as c:
        print(json.dumps(await c.call(sys.argv[1], *json.loads(sys.argv[2] if len(sys.argv) > 2 else "[]")), indent=1, default=str))

if __name__ == "__main__":
    asyncio.run(_main())
