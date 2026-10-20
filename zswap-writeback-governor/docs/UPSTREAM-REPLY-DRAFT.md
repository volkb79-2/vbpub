# Upstream reply draft (for the OPERATOR to send under their own name)

Notes for the sender, not part of the mail:

- Send as plain text, no HTML, no attachments, wrapped at 72 columns,
  bottom-posted. Everything between the two `----8<----` lines is the mail body.
- Fill the TODOs from lore.kernel.org: the To/Cc list and the message-id of
  the specific message you reply to. The links in the README came from a web
  search of LKML mirrors, not from lore; **find the thread on lore and use its
  headers**. Never type a message-id from memory.
- Quote only the paragraph you answer; keep the author's attribution line.
- Every claim is labelled [measured] or [inferred] in the text. Keep the
  labels; maintainers discount unlabelled inference. If you re-run anything
  (the synthetic in `../synthetic/`), update the numbers first.
- Not yet done when this was written: the VM reproduction has not been run, and
  the reclaim caller of the stall has not been identified (the stack probe was
  cut off). The text says so; do not delete those sentences unless a later
  measurement replaces them.

```
----8<---- (mail starts below) ----8<----
To:  TODO: from lore.kernel.org, the thread's To: line
Cc:  TODO: from lore.kernel.org, the thread's Cc: list (linux-mm,
     cgroups maintainers, zswap maintainers as listed there)
Subject: Re: TODO: exact subject of the "mm/zswap: Implement proactive
 writeback" message you reply to
In-Reply-To: TODO: <message-id of that message, copied from
 lore.kernel.org>
References: TODO: the References: header of that message plus its
 message-id

On TODO: date, TODO: author wrote:
> TODO: quote only the paragraph being answered (for example the one on
> why memory.zswap.max is not sufficient)

I would like to add a data point from a production-like user of
this problem, and one question about the shrinker path.

Use case [measured]: a latency-sensitive game server (one process,
roughly 7.5 GB resident, 30 fps tick) in a memcg on a 16 GB host that
is overcommitted by design (builds and containers share the box). The
kernel is 7.2.6 (Debian), zswap with zstd, max_pool_percent=25,
shrinker_enabled=Y, memory.zswap.writeback=1 and memory.zswap.max=max
on the game's cgroup. Swap is on two partitions of a virtual disk
whose writes are capped at a flat 14 MiB/s (iostat shows 14334-14338
kB/s across samples at 100% util), with nr_requests=16.

What we see [measured]: about 7.7 minutes after the game's cgroup
protection was lowered (memory.min 4000M, memory.low 5000M, memory.high
8000M, usage about 7.5G) and 2.5 minutes after a load test ended, the
zswap pool was 769 MB at the last monitor row before a 146 s gap in
which the monitor printed nothing, 750 MB at the first row after it,
and 0 six seconds later; the trajectory inside the gap was not
observed. Swap slot usage grew by 2.1 GB over the episode (the
monitor's swap-used column; the written bytes are known only from
iostat's flat 14 MiB/s). The game's frame rate, 29.9 before, was 5.9
when sampling resumed and back to 29.8 about 40 s later. In a second,
smaller episode, under a synthetic dd/fio load run by our test
harness, the game's threads spent 16.03 s of their 16.25 s in D state
in shrink_memcg_cb. Over the first episode (no controller-run load
was active, but a load test had ended 2.5 minutes before) the game's
threads spent 455.8 s (summed over threads) blocked in:

    blk_mq_get_tag
    ... submit_bio_noacct_nocheck
    shrink_memcg_cb
    __list_lru_walk_one
    list_lru_walk_one

The tracing used kstack(10), so the frames above list_lru_walk_one are
not recorded and I cannot tell you whether the caller was global direct
reclaim (a separate 21 s in try_to_free_pages was seen in the same
window) or memcg reclaim from memory.high [measured: stack truncated;
the caller is unknown]. We are building a VM reproduction that records
the full stack to settle that. TODO(sender): decide whether to
promise a follow-up with that reproduction; it has not been run yet.

Reading mm/zswap.c and mm/shrinker.c at v7.2 [inferred from source,
not from a trace of this host], I believe the following explains the
shape. Please correct me where I have it wrong:

 - Writeback in shrink_memcg_cb() runs synchronously in the reclaiming
   task and, as far as I can see in zswap.c and page_io.c (I have not
   read the block layer), sleeps only when the request queue is out of
   tags, so the task that happens to be in reclaim pays the device
   time.
 - There is no per-pass byte budget: do_shrink_slab() scales the scan
   target with 2 * freeable >> priority and loops in batches of 128
   while total_scan >= batch or >= freeable. Once the head of the list
   is unreferenced, every batch is productive and the loop runs to the
   end of total_scan, so a visit at a low priority (or several at a
   moderate one with accumulated nr_deferred) can write back a large
   part of the list.
 - The second-chance referenced bit gives no age, and arming is slow: an
   all-referenced batch is LRU_ROTATE (not counted as isolated), so
   zswap_shrinker_scan() returns SHRINK_STOP and do_shrink_slab()
   breaks, i.e. about one batch of 128 entries is cleared per visit.
   Entries stored in a burst (here a min/low/high step-down moved about
   780 MB into the pool in under 20 s) need roughly N/128 visits to
   become eligible, after which they go quickly. I have no per-visit
   data, so I cannot say how long that took here.
 - The memcg protection check in shrink_node_memcgs() is all or nothing
   for shrink_slab(), while get_scan_count() scales the LRU scan by
   protection/usage; zswap_shrinker_count() does not look at
   memory.min/low. A cgroup above its low therefore gets the
   shrinker at full strength.
 - Writeback done by the shrinker does not seem to be credited as
   reclaim progress (the shrink_slab() return value is discarded in
   shrink_node_memcgs() and zswap does not touch reclaim_state), so an
   episode does not end earlier because it wrote pages back.

Why this hurts, and why memory.zswap.max does not help us [inferred]:
lowering memory.zswap.max acts only on the next store, and the
over-limit case writes back one entry per store, so it neither drains
the pool in pieces nor stops the shrinker; turning the shrinker or
memory.zswap.writeback off is not an option for us: keeping
writeback and the shrinker enabled is an operator requirement on this
host.

Which is why a budgeted proactive interface looks right to us:
memory.reclaim with source=zswap, driven by a daemon in its own cgroup
at a rate derived from the measured device write rate, would keep the
stall out of the latency-sensitive task and let us drain ahead of
pressure. I support that series.

Two questions:

 1. Would a budget or rate limit on the reactive shrinker path itself
    (a cap on the entries written back per shrinker invocation, or a
    per-memcg byte rate) be acceptable, in addition to the proactive
    interface? A proactive daemon cannot bound a burst that the
    in-reclaim shrinker produces on whatever remains in the pool.
 2. Is there a reason the writeback bio is not submitted with
    REQ_NOWAIT-like semantics, so that a reclaimer never sleeps on the
    block layer, or has deferring the I/O to the zswap-shrink
    workqueue been considered? I understand the progress-accounting
    problem this raises and have not tried either.

I can test patches on this setup. TODO: the sender decides whether to
offer a Tested-by or a reproducer script, and whether to attach the
VM reproduction in a follow-up mail (not with this one).

Thanks,
TODO: operator's name and address, as they sign on the list
----8<---- (mail ends above) ----8<----
```
