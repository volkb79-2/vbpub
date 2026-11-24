#!/usr/bin/env python3
"""measure.py <jsonl>... : first request after the LAST restart (compact_boundary, else file start = /clear),
its input split by char share, orientation reads in requests 1..10, growth. Prints only names, paths, sizes, tokens."""
import importlib.util, json, re, statistics, sys
PATH_RE = re.compile(r"(?:~|\.{0,2})?/?[\w.\-]+(?:/[\w.\-]+)+|[\w.\-]+\.(?:md|ya?ml|sh|py|json|jsonl|txt|toml|env)\b")
from datetime import datetime

spec = importlib.util.spec_from_file_location("jm", "/workspaces/vbpub/nyxloom/tools/jsonl-metrics.py")
jm = importlib.util.module_from_spec(spec); sys.modules["jm"] = jm; spec.loader.exec_module(jm)  # reuse extract_bash_paths, no change to it

LISTING = {"skill_listing", "deferred_tools_delta", "agent_listing_delta", "mcp_instructions_delta"}
FILES = {"file", "compact_file_reference", "plan_file_reference", "edited_text_file", "nested_memory"}
ORIENT = 10


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def measure(path):
    recs = []
    for l in open(path, errors="replace"):
        try:
            recs.append(json.loads(l))
        except Exception:
            recs.append({})
    # the LAST restart that already has >= ORIENT requests after it (a newer one still orienting is reported, not measured)
    bounds = [(0, None)] + [(i, e.get("compactMetadata") or {}) for i, e in enumerate(recs) if e.get("subtype") == "compact_boundary"]
    def nreq_after(b):
        return len({(r.get("message") or {}).get("id") for r in recs[b:] if r.get("type") == "assistant" and (r.get("message") or {}).get("usage")})
    pending = []
    while len(bounds) > 1 and nreq_after(bounds[-1][0]) < ORIENT:
        pending.append(bounds.pop())
    start, meta = bounds[-1]
    shapes = {e.get("shapeHash"): e for e in recs if e.get("type") == "api-request-shape"}
    # requests after the restart, deduped by message.id (one request = one id; content blocks share usage)
    reqs, seen = [], {}
    stop = pending[-1][0] if pending else len(recs)
    for i in range(start, stop):
        e = recs[i]
        if e.get("type") != "assistant":
            continue
        m = e.get("message") or {}
        u = m.get("usage")
        if not u:
            continue
        mid = m.get("id") or f"line{i}"
        if mid not in seen:
            seen[mid] = len(reqs)
            ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            reqs.append(dict(line=i, ts=e.get("timestamp"), ctx=ctx, inp=u.get("input_tokens") or 0,
                             cr=u.get("cache_read_input_tokens") or 0, cc=u.get("cache_creation_input_tokens") or 0,
                             out=u.get("output_tokens") or 0, tools=[]))
        r = reqs[seen[mid]]
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                r["tools"].append((b.get("id"), b.get("name"), b.get("input") or {}))
    if not reqs:
        return None
    first = reqs[0]
    # char shares of what the first request carried, from the records between the restart and the first response
    cat = dict(system_tools=0, summary=0, files=0, instructions=0, listings=0, hook=0, prompt_preserved=0, other=0)
    files = []
    other_types = {}
    snap, shape = None, None
    for i in range(start, first["line"]):
        e = recs[i]
        t = e.get("type")
        if t == "user" and not e.get("isMeta"):
            c = (e.get("message") or {}).get("content")
            n = len(c) if isinstance(c, str) else len(json.dumps(c))
            cat["summary" if e.get("isCompactSummary") else "prompt_preserved"] += n
        elif t == "attachment":
            a = e.get("attachment") or {}
            at = a.get("type")
            if at == "prompt_snapshot":
                snap = a
                continue
            if at in FILES:
                fc = ((a.get("content") or {}).get("file") or {}).get("content") or ""
                n = len(fc) if fc else len(json.dumps(a))
                cat["files"] += n
                files.append((a.get("filename") or a.get("displayPath"), at, n))
            elif at == "instructions":
                cat["instructions"] += sum(len(f.get("content") or "") for f in a.get("files") or [])
            elif at in LISTING:
                cat["listings"] += (len(a.get("content") or "") + len("\n".join(a.get("addedLines") or []))
                                    + len("".join(a.get("addedBlocks") or [])) + len(json.dumps(a.get("surfacedDefinitions") or [])))
            elif at and at.startswith("hook"):
                cat["hook"] += len(a.get("content") or a.get("stdout") or "")
            else:
                cat["other"] += len(json.dumps(a))
                other_types[at] = other_types.get(at, 0) + len(json.dumps(a))
        elif t == "api-request" and shape is None:
            shape = shapes.get(e.get("shapeHash"))
    if snap is None:  # the snapshot is written once and reused: take the newest one before the first response
        for i in range(first["line"], -1, -1):
            a = recs[i].get("attachment") or {}
            if a.get("type") == "prompt_snapshot":
                snap = a; break
    sp = sum(len(x) for x in (snap or {}).get("systemPrompt") or [])
    tl = len(json.dumps(((shape or {}).get("shape") or {}).get("tools") or []))
    cat["system_tools"] = sp + tl
    total_chars = sum(cat.values()) or 1
    rest_chars = (total_chars - cat["system_tools"]) or 1
    split = {"system_tools(cache_read)": first["cr"]}
    split.update({k: round((first["ctx"] - first["cr"]) * v / rest_chars) for k, v in cat.items() if k != "system_tools"})
    est_sys = round(first["ctx"] * cat["system_tools"] / total_chars)
    # orientation: tool calls in requests 1..ORIENT with their result sizes
    results = {}
    for e in recs[start:]:
        if e.get("type") == "user":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = len(json.dumps(b.get("content")))
    reads = []
    for k, r in enumerate(reqs[:ORIENT]):
        delta = (reqs[k + 1]["ctx"] - r["ctx"]) if k + 1 < len(reqs) else None
        for tid, name, inp in r["tools"]:
            if name == "Read":
                ps = [inp.get("file_path")]
            elif name == "Bash":
                cmd = inp.get("command") or ""
                ps = [p for p, _ in jm.extract_bash_paths(cmd)] or sorted(set(PATH_RE.findall(cmd)))[:6] or ["(no path)"]
            elif name in ("Grep", "Glob"):
                ps = [inp.get("path") or inp.get("pattern")]
            else:
                ps = ["-"]
            reads.append((k + 1, name, ps, results.get(tid, 0) // 4, delta))
    n = min(ORIENT, len(reqs))
    o = reqs[n - 1]
    growth_orient = (o["ctx"] - first["ctx"]) / max(1, n - 1)
    later = [reqs[j + 1]["ctx"] - reqs[j]["ctx"] for j in range(n - 1, len(reqs) - 1)]
    later_pos = [d for d in later if d > -5000]  # drop shrink steps (none expected without a compaction)
    hours = (ts(reqs[-1]["ts"]) - ts(o["ts"])).total_seconds() / 3600 if len(reqs) > n else 0
    return dict(file=path.split("/")[-1][:8], restart="compact L%d %s pre=%s post=%s dur=%ss" % (
        start, meta.get("trigger"), meta.get("preTokens"), meta.get("postTokens"), (meta.get("durationMs") or 0) // 1000) if meta else "file start (/clear)",
        first=first, split=split, est_sys=est_sys, other_types=other_types, chars=cat, files=files, reads=reads, n=n, orient_ctx=o["ctx"], orient_ts=o["ts"],
        growth_orient=growth_orient, later_median=statistics.median(later_pos) if later_pos else None,
        later_mean=statistics.mean(later_pos) if later_pos else None, nreq=len(reqs), end_ctx=reqs[-1]["ctx"],
        pending=["L%d pre=%s post=%s" % (b, m.get("preTokens"), m.get("postTokens")) for b, m in pending],
        end_ts=reqs[-1]["ts"], per_hour=((reqs[-1]["ctx"] - o["ctx"]) / hours) if hours else None)


for p in sys.argv[1:]:
    d = measure(p)
    if not d:
        print(p, "no requests"); continue
    f = d["first"]
    print(f"=== {d['file']} restart: {d['restart']}" + (f"  (newer, still orienting: {d['pending']})" if d['pending'] else ""))
    print(f"  req1 {f['ts']} ctx={f['ctx']} (input={f['inp']} cache_read={f['cr']} cache_create={f['cc']}) out={f['out']}")
    print("  split by char share (tokens): " + " ".join(f"{k}={v}" for k, v in d["split"].items()))
    print("  chars: " + " ".join(f"{k}={v}" for k, v in d["chars"].items()) + f"  (system_tools by char share would be {d['est_sys']})")
    print("  other by type (chars): " + " ".join(f"{k}={v}" for k, v in sorted(d["other_types"].items(), key=lambda x: -x[1])))
    for fn, at, n in d["files"]:
        print(f"    re-attached {at}: {fn} ~{n // 4} tok")
    print(f"  req{d['n']} {d['orient_ts']} ctx={d['orient_ctx']}  growth req1..{d['n']}: {d['growth_orient']:.0f} tok/req")
    print(f"  after orientation: {d['nreq'] - d['n']} requests, median +{d['later_median']} mean +{d['later_mean'] and round(d['later_mean'])} tok/req, "
          f"end ctx={d['end_ctx']} at {d['end_ts']}, {d['per_hour'] and round(d['per_hour'])} tok/h")
    for k, name, ps, tok, delta in d["reads"]:
        print(f"    req{k:<2} {name:<10} ~{tok:>6} tok result, req delta {delta}  {' '.join(str(x) for x in ps)}")
