# gaps.py <jsonl>...: request gaps > 55 min and whether the next request hit the cache (cache_read share); tokens only.
import json, sys
from datetime import datetime
for p in sys.argv[1:]:
    reqs, seen = [], set()
    for l in open(p, errors="replace"):
        try: e = json.loads(l)
        except Exception: continue
        m = e.get("message") or {}
        u = m.get("usage")
        if e.get("type") != "assistant" or not u or m.get("id") in seen: continue
        seen.add(m.get("id"))
        reqs.append((datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")), u.get("cache_read_input_tokens") or 0,
                     u.get("cache_creation_input_tokens") or 0, u.get("input_tokens") or 0))
    out = []
    for a, b in zip(reqs, reqs[1:]):
        gap = (b[0] - a[0]).total_seconds() / 60
        if gap > 55:
            out.append(f"{a[0]:%m-%d %H:%M} gap {gap:.0f}m next cr={b[1]} cc={b[2]}")
    print(p.split("/")[-1][:8], len(reqs), "requests;", "; ".join(out) or "no gap > 55 min")
