# Crawl4AI live verification — blocker analysis

**Directive 03.1 §3 · 2026-11-14 · verdict: `failed` · failure class: `docker`**

Third attempt at closing BLK-02 (Crawl4AI endpoint unverified). The first two attempts
died on disk exhaustion. **This attempt did not touch disk pressure at all** — free space
never moved (21.18 → 21.17 GiB). The blocker is now precisely located, and it is not the
one the previous attempts were fighting.

Evidence: `reports/eryndex/evidence/crawl4ai-live-verification.json`,
`reports/eryndex/evidence/crawl4ai-live-response.json`,
`reports/eryndex/evidence/crawl4ai-disk-watchdog.log`.

---

## 1. What was actually blocking

Two independent blockers, both fatal, both proven by command output rather than inferred.

### 1a. Primary — the Docker daemon's write path is wedged (`failure_class=docker`)

The daemon answers read-only calls quickly (`docker info`, `docker ps -a`, `docker images`,
`docker events`). Every **mutating** call hangs until killed:

| Command | Result |
|---|---|
| `docker network create eryndex-c4a-probe-net` | **exit 124 at 45 s** — pure boltdb write: no registry, no layer, no snapshot |
| `docker create --network none alpine:latest true` | **exit 124 at 45 s** |
| `docker run --rm --network none alpine:latest echo` | **exit 124 at 60 s** |
| `docker pull hello-world` | **exit 124 at 120 s**, printed only `Using default tag: latest` |
| `docker system df` | **exit 124 at 90 s** (known bad on this machine) |

The `network create` test is the decisive one: it needs no image, no filesystem, no network.
Its hang localises the fault inside the daemon, not in the pull or the container's runtime.

No container-create event appears in `docker events` for any of the probes — the requests
never got that far.

Daemon log corroboration (`%LOCALAPPDATA%/Docker/log/vm/dockerd.log`):

```
error msg="Handler for POST /v1.44/images/create returned error:
           error creating temporary lease: context canceled"
```

Six occurrences today: 07:42:58, 07:43:05, 07:54:03, 08:13:06, 08:41:43, 10:21:07 — the last
one is this run. `context canceled` is the client giving up; the daemon never completed the
lease. That is a daemon stuck before it ever reaches the network, which is why "pull more
patience" or "pull at night" would not have helped.

This state is consistent with a daemon that was never restarted after the earlier 100%-disk
event. It is a recovery-by-restart situation, and **restarting Docker Desktop restarts
`trading_bot_postgres`** — an owner container. That is the one action this task was not
allowed to take.

### 1b. Retracted — Docker Hub was reachable; it is the daemon's network subsystem that hangs

The first version of this document claimed the registry was 403'd. An independent audit
re-measured and the claim did not reproduce:

| Endpoint | Claimed here | Actually measured |
|---|---|---|
| `registry-1.docker.io/v2/` | `403 RBAC: access denied` | **`401 Unauthorized` + `WWW-Authenticate: Bearer`** — the normal anonymous challenge |
| `auth.docker.io/token` | `403` (cloudflare) | **`200`** with a real pull token |
| `hub.docker.com/v2/repositories/unclecode/crawl4ai/tags/` | `403` | **`200`**, 40 tags, `0.9.5` present |
| `files.pythonhosted.org` crawl4ai wheel | DNS failure | **`200`**, 494,036 bytes downloadable |

**Withdrawn:** the recommendation to add a Docker Hub mirror or proxy. It is unsupported by
measurement and would cost real setup work for no benefit. What is genuinely unknown is only
the image's *size* (needs a manifest fetch through a wedged daemon), not whether the tag exists.

**Corrected statement of the blocker:** the daemon's *network* subsystem hangs on both reads and
writes — `docker network ls`, `docker network inspect bridge` and `docker network create` all exit
124 — while container/image/volume reads answer normally. `example.com` returns 200 from this host.
So the image could not be fetched because the daemon cannot drive its network path, not because the
registry refused us.

## 2. Measured numbers

| Metric | Value |
|---|---|
| Disk free, before | **21.18 GiB** (`df -h`: 22G avail, 96% of 476 G) |
| Disk free, minimum | **21.17 GiB** (81 samples @ 15 s, 10:29:59–10:50:17Z, zero variance) |
| Disk free, after | **20.92 GiB** (final; ~250 MB host drift after the watchdog window) |
| Bytes consumed by the attempt | **~0** — no pull completed, so no layers were written |
| Docker VM RAM | **2048 MiB** configured (8 GiB reported total), swap 1024 MiB |
| Docker data disk cap | `diskSizeMiB=65536` (64 GiB) |
| WSL data vhdx | `…/Docker/wsl/data/ext4.vhdx` = **15 GiB**, never compacted |
| Docker | Desktop 25.0.3, driver `overlay2`, root `/var/lib/docker`, cgroup v1 |
| Adapter call | `POST /crawl`, no HTTP status, **2016 ms**, 0 content chars |
| Adapter outcome | `ProviderError(code=unavailable, retryable=True)`, 1 call, 0 retries |
| Session wall clock | 1 492 383 ms (~24.9 min) |
| Images pulled / containers created | **0 / 0** |

**The 27 GiB was never the problem this time.** It is enough for the pull only if the
daemon writes at all; it was not even given the chance to fail on space.

## 3. What the run did prove

The adapter's transport layer and error taxonomy behave correctly against a dead endpoint:
`TransportUnavailable` → `ProviderError("unavailable")`, retryable, one attempt,
`metrics = {calls: 1, retries: 0, failures: 1}`, `health()` → `healthy: false`. That is a
clean fail-closed result, not a crash.

It also captured **the exact request body the adapter emits**, which is what exposed the
`extract` defect: the body was `{urls, crawler_config: {cache_mode}}` — `extract` never sent.

## 4. Adapter defects found (source review; latent, not causal)

None of these caused today's failure. All of them would surface the moment a live server
answers, which is exactly why they matter.

1. **`integrations/crawl4ai/adapter.py:88-94` — citation corruption (high).**
   v0.9.5's `CrawlResult.model_dump()` sets `result["markdown"] =
   self._markdown.model_dump()` (`crawl4ai/models.py:281`) — a **nested dict**, not a string.
   The adapter reads it as text, so `text` is a dict: truthy, `status` becomes `"ok"`, and
   `Evidence.excerpt = str(dict)[:600]` writes `"{'raw_markdown': '# Example Domain..."`
   into the evidence row, with `content_hash` = sha256 of that repr. Silent corruption of
   every provenance record.
2. **`integrations/crawl4ai/adapter.py:70` — `cache_mode` shape/value.**
   `CacheMode` is a plain `Enum` with lowercase values (`crawl4ai/cache_context.py:16-20`:
   `BYPASS = "bypass"`), and the server parses `crawler_config` through
   `CrawlerRunConfig.load(..., provenance=UNTRUSTED)` (`deploy/docker/api.py:771`), which
   expects the serialized `{type:…, params:{…}}` form. The adapter sends bare `"ENABLED"`.
   `"BYPASS" != CacheMode.BYPASS`, so cache bypass is at best a silent no-op; whether the
   untrusted gate 400s instead is **unverified** (no server).
3. **`integrations/crawl4ai/adapter.py:57-63` vs `68-71` — `extract` validated then dropped.**
   Confirmed on the wire in this run.
4. **`integrations/crawl4ai/adapter.py:5` — `browser_config` documented, never sent.**
   No page timeout, user agent, or viewport control.
5. **`contracts/provider.py:41-70` — no provider-issued id field.**
   `Evidence` cannot store an id, and v0.9.5's `CrawlResult` does not supply one. A
   citation-grade row keyed on a provider id is **not achievable today**; only
   `url + content_hash + retrieved_at` is available.

Response envelope is otherwise correct: `{"success": true, "results": [...]}`
(`deploy/docker/api.py:898-900`) matches the adapter's `_as_items(..., ("results", ...))`.

## 5. What would prevent a repeat

- **Minimum free space: 20 GiB, with a 5 GiB abort floor** (watchdog proven working —
  81 samples, 15 s interval, zero drift). Required because the image is multi-GB plus a
  per-container model/browser cache. Exact image size remains **unknown** (a manifest fetch
  needs a healthy daemon); the earlier attempt consumed ~4.6 GB before hanging, which is the
  only local datapoint.
- **Pull with a guard, never bare.** `timeout` on every docker call, abort below 5 GiB free.
  A bare `docker pull` is how C: reached 100% twice.
- **`docker system df` must stay wrapped in `timeout`** — it hangs on this daemon.
- **Raise Docker Desktop memory to ≥4 GiB** (`memoryMiB=2048` today). Crawl4AI runs
  Chromium; 2 GiB for the whole VM that also hosts postgres is a likely OOM, independent of
  the current wedge.
- **Compact the WSL vhdx.** `ext4.vhdx` is 15 GiB against a 96%-full host volume and has
  never been compacted; `diskTRIM=true` helps only inside the guest. Compaction is safe
  *after* the daemon restart, not before (it needs the vhdx unmounted, i.e. a Docker/WSL
  shutdown — forbidden here).
- **Solve the daemon, not the registry.** The registry is reachable (1b); no mirror or proxy
  is needed. Every remaining blocker is local to the Docker runtime.

## 6. Exact owner action that remains

Ordered, and deliberately not performed by this agent:

1. **Restart Docker Desktop** in a window where `trading_bot_postgres` may bounce. This is
   the fix for the wedge (1a). It cannot be done by an agent: the daemon owns that container
   and restarting it stops owner data.
2. **Nothing to do about the registry** — an independent audit measured `registry-1.docker.io`
   returning the normal `401` + Bearer challenge, a `200` token exchange, `0.9.5` present on the
   Hub tags API, and a downloadable PyPI wheel. The earlier "provide a mirror/proxy" step is
   withdrawn; it would have cost real work and fixed nothing.
3. **Set Docker Desktop memory to ≥4 GiB**, and **compact the WSL vhdx** while the VM is
   down — reclaims up to 15 GiB and gives the pull a real margin.
4. Re-run this directive. With a healthy daemon the sequence is: small pull → big pull with
   watchdog → `docker run … -p 127.0.0.1:11235:11235` → `/health` → **one** `POST /crawl`
   through the adapter. Fix defects 1-3 first, or the "successful" crawl will produce a
   corrupted evidence row and hide the bug.

**No source file, test, or integration was modified. No image was pulled, no container was
created, no owner data or cache was touched, and `trading_bot_postgres` kept the same 15-hour
uptime throughout.**
