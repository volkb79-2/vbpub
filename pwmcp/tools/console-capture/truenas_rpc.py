"""Minimal TrueNAS JSON-RPC 2.0 websocket client (wss://HOST/api/current).

Credentials come from the environment only: TRUENAS_HOST, TRUENAS_USER, TRUENAS_PASSWORD.
CLI: truenas_rpc.py METHOD [JSON_PARAMS_ARRAY]   (prints the result as JSON)
"""
import asyncio, json, os, ssl, sys
import websockets


class RPC:
    def __init__(self, host=None, user=None, password=None):
        self.host = host or os.environ["TRUENAS_HOST"]
        self.user = user or os.environ["TRUENAS_USER"]
        self.password = password or os.environ["TRUENAS_PASSWORD"]
        self._id = 0

    async def __aenter__(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # throwaway self-signed test install
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
