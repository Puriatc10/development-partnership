# Docker restart plan — impact analysis and controlled procedure

**Scope:** halting `trading_bot_postgres` (owner container, `postgres:16-alpine`) by restarting the
Docker engine, and everything that must be true before that is safe.
**Date:** 2026-11-14, 12:20Z (15:50 local, IRST).
**Status:** analysis complete, **restart not executed**. No container was stopped, started or
restarted; no image was pulled; nothing was pruned, removed or deleted; no repo file outside this
document was modified.

**Verdict in one line:** the daemon is not merely "slow" — it degraded from *partially answering* to
*answering nothing* inside this session (`docker info` worked at ~11:42Z, timed out at 12:06Z), and the
owner's database has already been unavailable for roughly three hours (`FailingStreak 1161`,
`pg_isready` → `no response`). The restart is no longer a maintenance action that risks an outage; it
is the recovery step for an outage that is already running.

---

## 0. What changed versus the previous analysis

`reports/eryndex/crawl4ai-blocker-analysis.md` (10:21Z today) described a daemon that answered reads
and hung on mutations, and attributed the state to disk pressure left by an earlier 4 GB pull. Three
things are now measured that change the picture:

| New fact | Measurement | Consequence |
|---|---|---|
| The daemon has fully wedged | 16/16 bounded probes timed out at 12:06Z, including `docker version` and `docker info` | No further read-only workaround exists; diagnosis by probing is over |
| The owner DB is down, not just idle | `State.Health.Status=unhealthy`, `FailingStreak=1161`, `pg_isready` → `no response`, `psql -c 'select 1'` hangs | There is no healthy state to protect; the "downtime cost" of the restart is currently being paid anyway |
| Host memory/commit is exhausted, not (this time) disk | free physical **131 MB** of 16 109 MB; commit **36 763 / 37 321 MB**; `Memory Compression` 1.16 GB; `pagefile.sys` grew to 31.4 GB | The likely mechanism is host-level memory starvation of the WSL VM, not a storage-driver failure; fixing disk alone will not hold |

The disk did move during the session — **21 GiB → 11 GiB free in ~50 minutes** — but in the *opposite*
direction from the previous event's story: nothing was pulled, and the consumer is consistent with
pagefile/shadow-copy growth under commit pressure. That is recorded as an open question in §8, not as
a proven cause.

---

## 1. Current Docker state, as measured

Every call below ran under `timeout`. Where a command did not return, the number after `rc=124` is the
wall-clock wait before `timeout(1)` killed it. Raw output for the final matrix is in
`C:/Users/puria/AppData/Local/Temp/docker_probes.tsv` (scratch, outside the repo).

### 1.1 Engine

| Field | Value | How |
|---|---|---|
| Client | 25.0.3, API 1.44, `windows/amd64`, context `default` | `timeout 20 docker version` (answered) |
| Server | Docker Desktop **4.28.0 (139021)**, engine 25.0.3, Go 1.21.6, API 1.44 (min 1.24) | same |
| containerd / runc / init | 1.6.28 (`ae07eda`) / 1.1.12 (`v1.1.12-0`) / 0.19.0 | same |
| Storage driver | `overlay2`, `containerd-snapshotter=false` | `docker info`; `dockerd` init line |
| Docker Root Dir | `/var/lib/docker` | `docker info` |
| Cgroup | v1, driver `cgroupfs` | `docker info` |
| Guest kernel | `5.15.146.1-microsoft-standard-WSL2`, `x86_64` | `docker info` |
| Guest hostname / OS | `docker-desktop` / Docker Desktop, 16 CPUs | `docker info` |
| **Guest total memory** | **7.625 GiB** | `docker info` |
| Live Restore | `false` | `docker info` (⇒ every restart is a hard stop of the daemon) |
| Proxy | `http://http.docker.internal:3128`, `no-proxy hubproxy.docker.internal` | `docker info` |
| Daemon start | **2026-11-13T19:45:34Z** (16 h uptime), `Loading containers: done` 19:45:34.394Z, `API listen` 19:45:35.1Z | `dockerd.log` L2604-2617 |

`memoryMiB=2048` is set in `C:/Users/puria/AppData/Roaming/Docker/settings.json`, but the guest reports
**7.625 GiB**. On the WSL2 backend that setting is inert — the VM's memory comes from `%USERPROFILE%\.wslconfig`,
which **does not exist** on this machine (`cat ~/.wslconfig` → absent), so WSL applies its default (≈50 % of
16 GB). Any instruction of the form "raise `memoryMiB` to 4 GiB" is therefore a no-op here; the knob is
`memory=` in `.wslconfig`. The practical consequence is the opposite of the intent: the VM is allowed
~8 GB on a 16 GB host that is already out of commit.

### 1.2 Containers

`timeout 45 docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'` (answered, ~11:43Z):

```
875b9b02028b|trading_bot_postgres|postgres:16-alpine|Up 16 hours (unhealthy)|0.0.0.0:5432->5432/tcp
c578bf6f4e59|commodity_postgres|postgres:17-alpine|Exited (0) 6 weeks ago|
53f56e1682bd|commodity_minio|public.ecr.aws/chainguard/minio:latest|Exited (0) 6 weeks ago|
```

- Running: **1** (`trading_bot_postgres`, unhealthy — see §3).
- Stopped: **2** (`commodity_postgres`, `commodity_minio`), both `Exited (0)` six weeks ago. They carry
  no restart policy evidence in `ps`; they will stay stopped after the restart unless something starts them.

### 1.3 Images (sum ≈ **1.12 GB** of layers)

`timeout 45 docker images --format '{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Size}}|{{.CreatedSince}}'`:

| Repository:Tag | ID | Size | Age |
|---|---|---|---|
| postgres:16-alpine | 4278d7b1e3de | 294 MB | 8 days |
| alpine:latest | 3ea06e4cb95e | 8.43 MB | 8 days |
| postgres:17-alpine | 1bea307dfb3e | 297 MB | 3 months |
| node:22-alpine | 395425e54d98 | 165 MB | 3 months |
| cgr.dev/chainguard/minio:latest | 346be9cc3f47 | 175 MB | 7 weeks |
| public.ecr.aws/chainguard/minio:latest | 346be9cc3f47 | 175 MB | 7 weeks (same ID — one layer set, two tags) |
| minio/minio:RELEASE.2025-02-28T09-55-16Z | 377fe6127f60 | 180 MB | 20 months |

**No `crawl4ai` / `unclecode/*` image exists locally** — confirmed by the listing; the Crawl4AI work still
starts from zero bytes.

### 1.4 Volumes (names and mountpoints only)

`timeout 30 docker volume ls` (answered) and `docker volume inspect … --format`:

| Volume | Driver | Mountpoint | Compose origin |
|---|---|---|---|
| `crypto-trading-bot_postgres_data` | local | `/var/lib/docker/volumes/crypto-trading-bot_postgres_data/_data` | project `crypto-trading-bot`, volume `postgres_data` |
| `docker_postgres_data` | local | `/var/lib/docker/volumes/docker_postgres_data/_data` | project `docker`, volume `postgres_data` |
| `docker_minio_data` | local | `/var/lib/docker/volumes/docker_minio_data/_data` | project `docker`, volume `minio_data` |

No bind mounts of owner data were observed on any running container (§3).

### 1.4b What still works while the daemon is dead — the manifest fetch

`docker manifest inspect` is a **client-side** registry call: it does not touch the daemon. It answered
(`rc=0`, 35 270 bytes) at 12:24Z, roughly 15 minutes after the daemon stopped answering anything:

```bash
timeout 40 docker manifest inspect --verbose unclecode/crawl4ai:0.9.5
```

| Result | Value |
|---|---|
| Platform | `linux/amd64` (manifest list also carries `arm64` + two `unknown` attestation entries) |
| **Manifest digest** | **`sha256:636a6f9e5127f54771f14f709bf1b95693aacc30512426b26d09768b0269f1f4`** |
| Config digest / size | `sha256:065f475c90ac75fe388141597b0cdba118ebf7e03f59e2a37456c89e6b864c59`, 25 811 B |
| Layers | 32 |
| **Compressed download size** | **2,275,447,767 B = 2.12 GiB (2.28 GB)** |
| Largest layers | 708.9 / 601.6 / 372.8 / 231.1 / 173.3 MB (these five = 1.94 GiB, 91 % of the total) |

Two things follow. First, the previously-unknown image size is now a measured number, and it is **smaller than
the 4–5 GB the earlier attempts assumed** — the 4.6 GB those attempts consumed is consistent with 2.12 GiB
compressed expanding ~2× into overlay2. Second, this is the cleanest possible isolation proof: the same
process, over the same network, against the same registry, fetched a manifest while the daemon could not
create a lease. **The registry path is fine; the fault is entirely local to the daemon.**

### 1.5 Networks

**Could not be measured.** `docker network ls` → `rc=124` at 30 s (and again at 25 s in the matrix).
`docker network inspect bridge` → `rc=124` at 20 s / 15 s, zero bytes written to the output file.
The only network name obtainable came indirectly, from `HostConfig.NetworkMode` on the container:
`crypto-trading-bot_default`. Presumably `bridge`, `host`, `none` and `crypto-trading-bot_default`
exist; that is an inference, not a measurement, and is marked unknown in §8.

### 1.6 Host disk and the Docker VM disk

```
df -k /c   total=498,910,204K  used=487,448,080K  avail=11,462,124K   → 10.93 GiB free, 98 %
```

| Time (UTC) | Free | Note |
|---|---|---|
| 11:07Z (session start) | 21.18 GiB (456 G used) | matches the previous report's "21.17 GiB" |
| 12:06Z | 12 G | after the probe matrix began |
| 12:15Z | 11 G | |
| 12:19Z | 11 G (10.93 GiB) | stable for the last 4 minutes |

`C:/Users/puria/AppData/Local/Docker/wsl/data/ext4.vhdx`:

```
stat: 15,091,105,792 bytes  → 15.09 GB / 14.06 GiB
mtime: 2026-11-14 01:21:41 +0330 (yesterday), size unchanged throughout this session
```

Second, much smaller disk image (the boot distro, not data): `.../Docker/wsl/distro/ext4.vhdx` = 145 MB.

Host memory, same instants:

```
total visible 16,109 MB · free physical 131–237 MB · commit 36,763 / 37,321 MB (≈558 MB headroom)
pagefile: 31,455,126,016 bytes on disk, Win32_PageFileUsage reports 21,212 MB allocated / 4,574 MB used
"Memory Compression" process: 1,162 MB · vmmemWSL working set: 288 MB
```

The gap between the pagefile's 31.4 GB file size and the 21.2 GB figure Windows reports is itself
unexplained and is listed in §8.

### 1.7 Probe ledger — what hung, and for how long

WSL distro state at 11:58Z (`timeout 20 wsl -l -v`):

```
* docker-desktop-data    Running   2
  Ubuntu                 Stopped   2
  docker-desktop         Running   2
```

**Matrix run 12:06:24Z → 12:12:15Z — every command timed out, including ones that had worked 10 minutes earlier:**

| Command | rc | Waited | Output |
|---|---|---|---|
| `docker version` | 124 | 20.2 s | — |
| `docker info` | 124 | 25.2 s | — |
| `docker ps -a` | 124 | 25.2 s | — |
| `docker images` | 124 | 25.2 s | — |
| `docker volume ls` | 124 | 25.2 s | — |
| `docker network ls` | 124 | 25.2 s | — |
| `docker network inspect bridge` | 124 | 15.2 s | — |
| `docker inspect trading_bot_postgres` | 124 | 25.2 s | — |
| `docker top trading_bot_postgres` | 124 | 20.2 s | — |
| `docker system df` | 124 | 20.2 s | — |
| `docker stats --no-stream` | 124 | 20.2 s | — |
| `docker logs --tail 20 trading_bot_postgres` | 124 | 20.2 s | — |
| `docker exec … pg_isready -U postgres -d trading_bot` | 124 | 20.2 s | — |
| `docker exec … psql -tAc 'select 1'` | 124 | 20.2 s | — |
| `docker image inspect postgres:16-alpine` | 124 | 20.2 s | — |
| `docker ps` | 124 | 20.2 s | — |

**Earlier in the same session (≈11:36–11:58Z), the split was still partial:**

| Command | Timeout | Result |
|---|---|---|
| `docker version` | 20 s | **ok** (full client+server JSON) |
| `docker info --format '{{json .}}'` | **45 s** | **hung**, 0 bytes — while plain `docker info` returned |
| `docker info` | 30 s | **ok** (full text dump) |
| `docker network ls` | 30 s | **hung**, 0 bytes |
| `docker volume ls` | 30 s | **ok** |
| `docker network inspect bridge` | 20 s | **hung**, 0 bytes |
| `docker inspect trading_bot_postgres` | 40 s | **ok** (8 049 bytes) |
| `docker logs --tail 30` | 30 s | **ok** (twice, ~11:50Z) |
| `docker logs --tail 100 / 150 / 200 / 300 / 600 / 5000 / (full)` | 25–45 s each | **all hung**, 0 bytes |
| `docker exec … pg_isready` | 25 s | **ok**, exit 2, `no response` (~11:55Z) |
| `docker exec … psql -tAc 'select 1'` | 20 s | **hung** (~12:00Z) |
| `docker stats --no-stream` | 20 s | **hung** |
| `docker system df` | 30 s | **hung** |
| `docker ps -q` | 30 s | **hung** (~11:59Z) — the same read that had answered at 11:43Z |

Two things follow, and they matter for the procedure: (a) the failure is *progressive*, so a successful
read today proves nothing about the next minute; (b) `docker logs` is size-sensitive (`--tail 30` works,
`--tail 100` does not), which points at the I/O path rather than at the log data.

I deliberately did **not** re-run the `docker network create` probe. With the daemon no longer answering
`docker info`, a create probe cannot discriminate between "network store wedged" and "everything wedged",
and each blocked handler adds load to an already starved VM. The previous run's `rc=124 at 45 s` plus the
store-timeout warnings in §2 carry that proof.

---

## 2. Daemon evidence

### 2.1 Log paths

| File | Size / rotation | Content |
|---|---|---|
| `C:/Users/puria/AppData/Local/Docker/log/vm/dockerd.log` | 777 KB + `.0`…`.8` ≈ 1 MB each (≈10 MB window) | the engine log — the primary evidence, readable from Windows |
| `C:/Users/puria/AppData/Local/Docker/log/vm/01-docker.log` (+`.0`…`.5`) | 968 KB + 6×1 MB | Docker Desktop's wrapper log |
| `C:/Users/puria/AppData/Local/Docker/log.txt`, `log.0-4.txt`, `debug.log` | 119 KB … 395 KB | Desktop lifecycle / backend |

The `vm/dockerd.log` window is ~10 MB and currently turns over in hours, not days, because of the
per-10-second `superfluous response.WriteHeader` lines below. Anything older than that window is gone.

### 2.2 The repeated errors, verbatim, with counts

**A. Lease creation fails on every image create — 6 occurrences, all today:**

```
[2026-11-14T07:42:58.935624293Z] level=error msg="Handler for POST /v1.44/images/create
  returned error: error creating temporary lease: context canceled"
[2026-11-14T07:43:05.051938301Z]  … same …
[2026-11-14T07:54:03.396254356Z]  … same …
[2026-11-14T08:13:06.225041563Z]  … same …
[2026-11-14T08:41:43.527102384Z]  … same …
[2026-11-14T10:21:07.441289085Z]  … same …
```

`grep -c` over `dockerd.log*` = **exactly 6**, all six from today, all on `images/create`, none on any
other endpoint. `context canceled` is the *client* (my `timeout`) giving up; the daemon never finished
creating the lease, i.e. the request was still blocked inside the daemon when the client disappeared.
This is the single most diagnostic line in the log: it localises the wedge to the **lease/datastore write
path**, which is reached before any registry byte is fetched — the reason "more patience" or "pull at
night" cannot help.

**B. libnetwork's endpoint store times out — 10 occurrences, 11:12:16Z → 11:58:05Z:**

```
[2026-11-14T11:12:16.803461680Z] level=warning msg="failed to get endpoint_count map from store: timeout"
[2026-11-14T11:14:18.940812922Z] … [11:24:25] … [11:25:37] … [11:26:37] …
[2026-11-14T11:46:17.812928398Z] … [11:55:03] … [11:56:05] … [11:57:05] … [11:58:05.521216923Z]
```

Ten warnings, ten network-namespace calls — one for each `docker network ls` / `network inspect bridge`
I issued between 11:12Z and 11:58Z. This is the mechanism behind the network hangs: the daemon's
network controller cannot read its own endpoint store within its internal timeout, and every
`/networks` handler blocks on it. It is also the last thing the daemon logged before it stopped
answering entirely (`11:58:05` store timeout → `11:58:33` the last log line of any kind).

**C. HTTP handler compaction — 3 427 occurrences since 2026-09-25, currently ~6/min:**

```
[2026-11-14T11:57:03.400128166Z] 2026/11/14 11:57:03 http: superfluous response.WriteHeader call
  from go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp.(*respWriterWrapper).WriteHeader
```

One every 10 s, i.e. one per `trading_bot_postgres` healthcheck. The `otelhttp` wrapper tries to write a
status line after the handler already wrote one — the signature of handlers returning **after** their
context is gone. The minute-by-minute cadence is the container's healthcheck hammering a handler that
cannot complete, which is itself part of the load.

**D. Older, unrelated pull failures** (pre-date today's wedge; recorded so they are not misread as
current): `denied: <!DOCTYPE HTML…` ×5, `Download failed after 5 attempts: EOF` ×3,
`Not continuing with pull after error: denied: requested access…` ×2. These are registry/proxy-era
noise, not this incident — an independent audit already established the registry is reachable
(`401` + Bearer on `registry-1.docker.io/v2/`, `200` on `auth.docker.io/token`).

### 2.3 What is *not* in the log (and why that is informative)

Searched explicitly for the storage driver's failure modes and found **none**:

```
grep -iE "overlay2|snapshot|failed to|no space|input/output|lease|metadb|boltdb|read-only" dockerd.log
→ only the lease lines (A), the store timeouts (B) and two 2026-09-27 "failed to close stdin" warnings
```

No `no space left on device`, no `input/output error`, no `overlay2` mount failure, no snapshotter error,
no `metadb`/boltdb corruption message. So the overlay2 *data* path is not reporting distress — the two
faults the log actually shows are (i) lease creation and (ii) the network store, both of which are
**metadata/datastore reads and writes**, not layer I/O. That is consistent with a process that is starved
of CPU/memory rather than a filesystem that is full: a full disk usually announces itself; starvation
does not.

### 2.4 The mechanism this evidence supports (stated as a hypothesis, not a proven chain)

Host numbers from §1.6 — 131 MB free physical, 558 MB commit headroom, 1.16 GB in Memory Compression,
`vmmemWSL` paged down to a 288 MB working set while the guest believes it has 7.6 GB — describe a host
that is over-committed, with the WSL VM's guest memory largely swapped to a pagefile on the same physical
disk. Under that condition:

1. any guest process touching a swapped page waits on host I/O that is itself contending with pagefile writes;
2. `dockerd` handlers block on their own datastore/lease calls, exceed internal timeouts, and — since they
   are goroutines on a small set of threads — stop returning;
3. `postgres` cannot `fork()` a backend, which is exactly the warning it emits (§3.4);
4. the healthcheck's `exec` creates a stuck process every 10 s, adding to the load that caused the stall.

This is *consistent with every measurement above* and explains the progression (reads answer while the
cache is warm, then stop). It is **not proven**: the decisive artefacts — a dockerd goroutine dump, guest
`/proc/meminfo`, `docker top` output — are all unreachable now, and there is no `SIGQUIT` path available
to me that would not itself be a daemon intervention. Recorded in §8 as the top unknown.

Also honest: my own probing is part of the load. ~17 blocked handlers, some held for 25–45 s, were left
inside the daemon over ~50 minutes. That is not the cause, but it plausibly accelerated the final wedge.
**Recommendation: nobody issues another `docker` command until the restart.**

### 2.5 What I could not read

- anything inside the VM: `/var/lib/docker` usage, guest `/proc/meminfo`, `dockerd` goroutine dump;
- `docker system df` (hung, 30 s) and `docker stats` (hung, 20 s) — so **actual disk used by Docker is unknown**;
- `docker top` (hung; earlier it returned exit 0 with *empty* output, which is itself wrong);
- full container logs (`--tail ≥ 100` hangs) — so the **first** `autovacuum` warning cannot be timestamped;
- `docker network ls`, `network inspect`, `volume inspect` for `CreatedAt` (all hung after the first pass);
- the Docker Desktop GUI's own state (not driven — no UI automation was used).

---

## 3. Impact analysis — `trading_bot_postgres`

### 3.1 Exact run configuration (from `docker inspect`, 8 049 bytes, captured while reads still answered)

| Field | Value |
|---|---|
| Name / ID | `trading_bot_postgres` / `875b9b02028b` |
| Image | `postgres:16-alpine`, image ID `sha256:4278d7b1e3de…1873` |
| Entrypoint / Cmd | `docker-entrypoint.sh` / `["postgres"]` |
| Restart policy | **`unless-stopped`** |
| Ports | `0.0.0.0:5432 -> 5432/tcp` (published on **all** interfaces, not just loopback) |
| Network | `crypto-trading-bot_default` (bridge, compose-created) |
| Mount | `crypto-trading-bot_postgres_data` → `/var/lib/postgresql/data`, `Type=volume`, `Driver=local`, `RW=true`, `Mode=z` |
| Environment (names only) | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, plus image-supplied `PATH`, `GOSU_VERSION`, `LANG`, `PG_MAJOR`, `PG_VERSION`, `PG_SHA256`, `DOCKER_PG_LLVM_DEPS`, `PGDATA`. Values deliberately not reproduced here. |
| Healthcheck | `CMD-SHELL pg_isready -U postgres -d trading_bot`; interval 10 s, timeout 5 s, retries 5 |
| Resource limits | none (`Memory=0`), `ShmSize=64 MB`, log driver `json-file` |
| Compose labels | project `crypto-trading-bot`, service `db`, container 1, config-hash `30807d2c…`, config file `C:\Users\puria\Desktop\code-workshop\projects\crypto-trading-bot\docker-compose.yml` |
| State at 11:54Z | `running`, **`unhealthy`**, `FailingStreak 1161`, `Pid 726`, `ExitCode 0`, `StartedAt 2026-11-13T19:45:34.811Z` |

**One inconsistency worth fixing while it is cheap:** the compose label points at
`…\Desktop\code-workshop\projects\crypto-trading-bot\docker-compose.yml`, but that directory does not exist —
the real project is `…\Desktop\code-workspace\projects\crypto-trading-bot\` (compose file read: `services.db`,
`restart: unless-stopped`, `volumes: [postgres_data:/var/lib/postgresql/data]`, `volumes: postgres_data:`).
Anyone who runs `docker compose` from the label's path, or a tool that trusts the label, will fail to find
the project. This is cosmetic for the restart, but it means **the compose file must not be used to "recreate"
this container** until the paths are reconciled.

### 3.2 Named volume or bind mount?

**Named volume.** Three independent statements agree:

1. `Mounts[0].Type = "volume"`, `Name = crypto-trading-bot_postgres_data`,
   `Source = /var/lib/docker/volumes/crypto-trading-bot_postgres_data/_data` — a path under Docker's own
   root, not a Windows path;
2. `docker volume inspect` lists it as a `local` volume with compose labels;
3. the compose file declares `postgres_data:` in the top-level `volumes:` block (a bind mount would be
   `./something:/var/lib/postgresql/data`).

There is **no** Windows bind mount anywhere in this container, so no NTFS path is involved in the data path.

### 3.3 Does the data survive a Docker Desktop restart? — yes, and here is why

`data_survives = true`. The evidence, in increasing order of strength:

1. **The data is inside Docker's own VM disk**, not in the container's writable layer:
   `Source=/var/lib/docker/volumes/crypto-trading-bot_postgres_data/_data`. A `docker restart`/engine
   restart destroys neither the container nor its volume; only `docker rm -v`, `docker volume rm`,
   `docker volume prune` or a Docker Desktop **factory reset** does.
2. **The VM disk is a file on the host that the restart does not touch.** `ext4.vhdx` is 15,091,105,792
   bytes, last written 2026-11-14 01:21, size unchanged across this whole session. A Docker Desktop
   restart stops and restarts the VM *against the same file* — no re-image, no re-extract.
3. **The container itself is not being removed.** `restart: unless-stopped` + the existing container id
   means the postmaster is signalled, the VM reboots, and the same container starts again with the same
   volume reference. `unless-stopped` also means it will come back **without** anyone re-running compose.
4. **The volume predates the current container** (`postgres:16-alpine`, 8 days old image; container started
   16 h ago) and carried data through at least the 2026-11-13 daemon restart (§2.2, `Loading containers: done`
   at 19:45:34Z, container `StartedAt` 19:45:34.811Z — the container came back *by itself* after the last
   restart). That is a direct, measured precedent: **this exact restart has already happened once and the
   database came back.**
5. Counter-evidence that would falsify this is absent: no `docker volume rm`, no prune, no reset in the log;
   the only destructive risks are operator actions, which §4 forbids explicitly.

What a restart **does not** protect against is an *unclean* stop corrupting the data directory. That is
mitigated by the sequence in §4 (stop the container first if the daemon answers; accept WAL crash recovery
if it does not) and is the reason the plan forbids "reset/purge" as a recovery step.

### 3.4 What the owner actually loses — and what is already lost

**Already lost, before any restart (measured at 11:54–12:06Z):**

| Observation | Meaning |
|---|---|
| `State.Health.Status = unhealthy`, `FailingStreak = 1161` | at 10 s per check, continuous failure since ≈**08:41Z** (3 h 15 m) |
| health log: `/var/run/postgresql:5432 - no response` (last five samples 11:54:53 → 11:55:33) | the postmaster does not answer on the socket |
| `docker exec … pg_isready` → `no response`, exit 2 (11:55Z) | reproduced from inside the container, not just by the healthcheck |
| `docker exec … psql -tAc 'select 1'` → no return in 20 s | **no query is being served**; the DB is down, not merely "unhealthy" |
| container log: `WARNING: autovacuum worker took too long to start; canceled` every 60 s, for at least the last 30 minutes | the postmaster **cannot fork** a child — process/VM resource exhaustion |
| port 5432 still bound on 0.0.0.0 | TCP connects may succeed and then hang, which is worse for clients than a refusal |

So the "downtime cost" of the restart is **not** the thing at risk — the outage started around 08:41Z and
is still running. Any application pointed at `localhost:5432` is already failing, with hangs rather than
fast errors.

**During the restart window:**

- **Downtime:** engine stop + VM boot + postgres start. Expect **60–180 s** end-to-end on a healthy host;
  on this host, with 131 MB free RAM, budget **up to 5 minutes** and treat >5 min as the abort condition
  in §4.4.
- **In-flight connections:** all TCP sessions drop. Because nothing can connect today, the realistic count
  is "whatever clients are stuck retrying" — zero successful sessions. The `psql` probe I issued at 12:00Z
  is one such stuck session and dies with the restart.
- **Uncommitted work:** any backend holding an open transaction is rolled back. This cannot be measured —
  `pg_stat_activity` is unreachable — so the honest statement is "an unknown, probably small, amount of
  uncommitted work will roll back". Committed data is in WAL on the volume and is not affected unless the
  data directory is damaged, which is what §4.4's abort path exists to prevent.
- **Connection flood on recovery:** all retrying clients reconnect at once. Nothing on this host is
  configured to cap that (`max_connections` is image default 100); if the trading bot has no backoff, the
  first minute after recovery will look like a stampede. Worth watching, not worth pre-empting.
- **The two stopped side-car containers** (`commodity_postgres`, `commodity_minio`) stay stopped. If anyone
  expected them up, the restart is not what brings them back.
- **Nothing else:** no other container runs, so no other workload is disturbed.

**The one real data risk** is not the restart — it is an operator, mid-incident, deciding that the volume
"looks corrupt" and resetting Docker Desktop or deleting the volume. That is a one-way door: 3 h of
unreachable data becomes 0 h of data, permanently. §4.4 forbids it in the strongest terms available.

---

## 4. Controlled restart procedure

Run every command with `timeout`. Keep a scratch log; put the evidence copies under
`reports/eryndex/evidence/docker-restart-<UTC>/` (that path is inside this repo — create the directory only
when the owner authorises the run, and do not `git add` it).

### 4.0 Phase 0 — relieve memory first (no restart; may make the restart unnecessary)

The §2.4 hypothesis says the wedge is starvation-driven. If so, freeing the host may un-wedge the daemon
without touching anything. This step costs nothing and is reversible.

1. Close, in this order: **Chrome** (5 processes, ≈1.29 GB), **Microsoft Edge** (166 MB), **Steam**
   (`steamwebhelper`, 170 MB), **VS Code** (237 MB), and this Hermes session (799 MB) plus its
   `python` child (535 MB) once you no longer need it.
2. Target: **≥ 4 000 MB free physical** and **≥ 6 000 MB commit headroom** (today: 131 MB / 558 MB).
3. Wait 120 s, then probe exactly one thing:

```bash
timeout 20 docker info --format '{{.ServerVersion}} {{.Driver}} {{.MemTotal}}'
# expect: 25.0.3 overlay2 8.2xxxxxxxxe+09   (any answer = daemon responsive)
timeout 30 docker network ls
# expect: NETWORK ID / NAME / DRIVER / SCOPE rows incl. bridge, host, none, crypto-trading-bot_default
```

- Both answer → the daemon recovered. **Stop here.** Do not restart. The container still needs its own
  health restored; if `pg_isready` still says `no response` after 2 minutes, restart **only the container**
  (`timeout 90 docker restart -t 60 trading_bot_postgres`) — that is a smaller action than an engine restart
  and is the right one if the engine is healthy.
- Still hanging → proceed to 4.1. Do not repeat the probe more than twice; each one leaves a blocked handler.

### 4.1 Pre-checks (all must pass, else abort)

```bash
# 1. Disk. Need >= 15 GiB free to restart safely (VM growth + postgres WAL).
df -k /c | tail -1        # today: avail=11,462,124K (10.93 GiB) -> FAILS this gate
                          # abort if avail < 15 GiB; the restart itself needs ~2 GiB, but 15 is the
                          # floor at which the VM can also grow without hitting 100 % again.

# 2. Memory. Need >= 3 GB free physical (today: 131 MB -> fails; do 4.0 first).
powershell -NoProfile -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)"

# 3. Capture the container config to a file (do this only if inspect still answers).
timeout 30 docker inspect trading_bot_postgres > reports/eryndex/evidence/docker-restart-<UTC>/pg-inspect.json
timeout 30 docker inspect trading_bot_postgres --format '{{json .HostConfig}}' > .../pg-hostconfig.json
cd /c/Users/puria/Desktop/code-workspace/projects/crypto-trading-bot && \
  timeout 30 docker compose config > .../compose-resolved.yml   # contains secrets: keep out of git
# If inspect hangs: the config is already recorded in §3.1 of this document and in
# C:/Users/puria/AppData/Local/Temp/pginspect.json — that dump is sufficient to rebuild the container.

# 4. Backup evidence — state the truth, do not pretend.
timeout 20 docker exec trading_bot_postgres pg_dump -U postgres -Fc trading_bot > .../trading_bot.dump
# EXPECTED TO FAIL TODAY: psql/pg_dump hang (no response). A logical backup is unobtainable
# while the DB is unresponsive. A cold file copy of ext4.vhdx (14.06 GiB) is ALSO unobtainable:
# only 10.93 GiB free. Record that in the evidence note; the restart is data-preserving
# (§3.3) so a backup is desirable, not a precondition.

# 5. Record the baseline for the after/before comparison.
timeout 30 docker ps -a --format '{{.Names}}|{{.Status}}' > .../ps-before.txt
stat -c '%s %y' /c/Users/puria/AppData/Local/Docker/wsl/data/ext4.vhdx > .../vhdx-before.txt
df -k /c | tail -1 > .../df-before.txt
```

**Abort conditions (any one):** free < 15 GiB after 4.0; free physical < 3 GB; a backup or config capture
was *expected* to succeed and failed for a reason other than the known wedge; the owner has not confirmed
they can tolerate 5432 being down for 5 minutes.

### 4.2 The restart action

Docker Desktop 4.28 has **no** `docker desktop` CLI (`docker desktop version` → `'desktop' is not a docker
command`). The supported paths, in order of preference:

1. **Tray icon → "Restart"** (the GUI is running: five `Docker Desktop.exe` processes, started 2026-11-13 23:15).
   Equivalent: Dashboard → the bug icon → *Restart*. This restarts the engine and the VM without touching
   the disk image.
2. **Quit + relaunch** if the tray is unresponsive: tray → *Quit Docker Desktop*, confirm no
   `com.docker.backend.exe` remains, then launch `"C:\Program Files\Docker\Docker\Docker Desktop.exe"`.
3. **`wsl --shutdown` + relaunch** — only if 1 and 2 both fail.
   **Warning, emphatic: `wsl --shutdown` stops ALL WSL distributions, not just Docker's.** Right now that is
   harmless (`Ubuntu` is `Stopped`); if the owner has started `Ubuntu` and has unsaved work in it, that work
   dies with the command. Check `wsl -l -v` immediately before running it, and ask the owner to close
   anything running in `Ubuntu` first.

```bash
timeout 20 wsl -l -v      # Ubuntu MUST read "Stopped" before you consider step 3
timeout 60 wsl --shutdown # only if steps 1-2 failed
# then relaunch Docker Desktop and wait for the engine
```

Do **not** use: *Troubleshoot → Reset to factory defaults*, *Clean / Purge data*, `docker system prune`,
`docker volume prune`, `docker rm -v trading_bot_postgres`, `docker volume rm crypto-trading-bot_postgres_data`.
Every one of those is a data-loss action on the owner's only database.

### 4.3 Post-restart verification (in this order; each must pass before the next)

```bash
# 1. Engine is up and it is the same engine build.
timeout 30 docker info --format '{{.ServerVersion}}|{{.Driver}}|{{.DockerRootDir}}|{{.MemTotal}}'
# expect: 25.0.3|overlay2|/var/lib/docker|8.2xxxxxxxxe+09

# 2. The network subsystem answers — this is the read that failed all session.
timeout 30 docker network ls
# expect: 4+ rows; MUST include bridge, host, none, crypto-trading-bot_default

timeout 30 docker network inspect bridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
# expect: 172.17.0.0/16

# 3. The write path that produced the lease errors — a create with no image, no registry, no layer.
timeout 30 docker network create eryndex-probe-net
# expect: a bare 64-hex network id. Anything else = the write path is still wedged: STOP and go to 4.4.
timeout 30 docker network rm eryndex-probe-net
# expect: eryndex-probe-net   (this removes only the network created on the line above)

# 4. The container came back by itself (restart: unless-stopped).
timeout 30 docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}'
# expect: trading_bot_postgres|Up <n> seconds (healthy)|0.0.0.0:5432->5432/tcp
# "starting" is fine for up to 90 s; "unhealthy" after 90 s is not.

# 5. Postgres serves queries again.
timeout 20 docker exec trading_bot_postgres pg_isready -U postgres -d trading_bot
# expect: /var/run/postgresql:5432 - accepting connections   (exit 0)

timeout 30 docker exec trading_bot_postgres psql -U postgres -d trading_bot -tAc \
  "select count(*) from pg_database"
# expect: a small integer >= 3  (template0, template1, trading_bot at minimum)

# 6. The data is readable at the volume level too — uses the local alpine image, no pull.
timeout 60 docker run --rm -v crypto-trading-bot_postgres_data:/v:ro alpine:latest \
  sh -c 'ls /v | head; test -f /v/PG_VERSION && echo PG_VERSION=$(cat /v/PG_VERSION)'
# expect: PG_VERSION, base, global, pg_wal, pg_hba.conf … then PG_VERSION=16

# 7. User data, not just catalog — pick a real table the owner names.
timeout 30 docker exec trading_bot_postgres psql -U postgres -d trading_bot -tAc \
  "select relname, n_live_tup from pg_stat_user_tables order by n_live_tup desc limit 5"
# expect: the owner's tables with non-zero row counts.

# 8. The exact code path that failed six times today: image create / lease.
timeout 120 docker pull alpine:latest
# expect: "latest: Pulling from library/alpine" then "Image is up to date for sha256:3ea06e4cb95e"
#         exit 0, ZERO bytes downloaded (the image is already local) — this is a lease test, not a download.
```

Then record `df -k /c`, `stat -c '%s' ext4.vhdx`, `docker system df` (which must now answer) into the
evidence directory, and diff against `df-before.txt` / `vhdx-before.txt`.

### 4.4 Abort / rollback path — if the daemon comes back unhealthy

| Symptom after restart | Action |
|---|---|
| `docker info` still times out after **5 min**, host memory is now fine | One second restart (tray). Then read the **tail of `dockerd.log`** for the *new* boot's errors — a fresh `error creating temporary lease` or `no space left on device` changes the diagnosis. Capture `dockerd.log` + `01-docker.log` to the evidence dir. |
| Daemon up, but `docker network create` still hangs | Do not pull anything. This is the wedge surviving a restart, which points at the VM disk itself (vhdx/metadata). Next step is **guest-side**: `wsl --shutdown`, then compact/inspect the vhdx (§6), then restart. Still no prune, no reset. |
| Daemon up, container `unhealthy` > 90 s | `timeout 30 docker logs --tail 50 trading_bot_postgres`. If it shows `database system was interrupted; last known up at …` followed by `redo` lines, it is doing crash recovery — wait, it is normal and bounded. If it shows `FATAL: could not write to file "pg_wal/…": No space left on device`, stop and free disk first (this is the failure mode that would genuinely hurt). |
| Container `unhealthy` AND the volume directory is missing/empty (`ls /v` shows nothing) | **Do not** `initdb`, do not re-run compose with a fresh volume, do not reset Docker. Stop and recover the vhdx (copy the file, mount it read-only elsewhere). This is the scenario where an immediate cold copy matters more than uptime — and there is only 10.93 GiB free, so freeing space becomes the emergency. |
| Docker Desktop reports "Docker Engine stopped" / backend crash | Read `AppData/Local/Docker/log.txt` + `debug.log`. Restart once more. Escalate to the owner rather than "resetting" anything. |

**Hard rule for the whole procedure:** the only irreversible action available is deleting the volume or
resetting Docker Desktop. Every other state in this document is recoverable by waiting, restarting again, or
freeing space. Never trade 3 hours of unreachable data for 0 hours of data.

---

## 5. Proving the daemon is healthy *without* a multi-GB pull

Cheapest-to-most-expensive, each one testing a different subsystem. Do them in order; stop at the first
failure.

| # | Command | What it proves | Cost | Expected |
|---|---|---|---|---|
| 1 | `timeout 30 docker network create t && timeout 30 docker network rm t` | daemon metadata **write** + libnetwork store read — the two things that failed today | 0 bytes | 64-hex id, then the name |
| 2 | `timeout 30 docker network inspect bridge` | libnetwork store **read** (hung 2× today) | 0 | JSON, subnet `172.17.0.0/16` |
| 3 | `timeout 120 docker pull alpine:latest` | the **lease path** that logged all six errors, without downloading a layer (`alpine:latest` = 8.43 MB, already present → manifest check only) | ~0 (KB of manifest) | `Image is up to date for sha256:3ea06e4cb95e` |
| 4 | `timeout 60 docker run --rm --network none alpine:latest echo ok` | container create + rootfs mount + exec + teardown, the `docker create`/`run` path that hung at 45–60 s in the previous run | 0 | `ok`, exit 0 |
| 5 | `timeout 120 docker pull hello-world` | a *real* tiny download through the full registry path incl. lease + layer write | ~13 KB image (≈2 MB with metadata) | `Status: Downloaded newer image for hello-world:latest` |
| 6 | `timeout 60 docker run --rm hello-world` | the classic smoke test end-to-end | 0 | the greeting, exit 0 |

`alpine:latest` before `hello-world` is deliberate: it exercises the **failing code path** (lease creation on
`/images/create`) with **zero network and zero layer writes**, so a pass there isolates the fault to the
registry rather than the datastore. If 1–2 pass and 3 fails, the problem is the lease/registry handshake.
If 1 fails, nothing else will work — go to §4.4.

Do **not** use `docker system df` as a health check: it hung at 20–30 s on the wedged daemon and is not a
reliable signal. Use it only to *report* usage after the daemon is proven healthy.

---

## 6. Disk and vhdx plan

### 6.1 The numbers

| Item | Value |
|---|---|
| `C:` free | **10.93 GiB** (11,462,124 KiB), 98 % used of 476 GB — was 21.18 GiB at session start |
| `ext4.vhdx` (Docker data) | **15,091,105,792 B = 15.09 GB = 14.06 GiB**, mtime 2026-11-14 01:21, never compacted |
| Docker's actual payload (images only) | ≈ **1.12 GB** of layers (7 tags, 6 layer sets) |
| Volumes | 3 named volumes; content size **unknown** (`docker system df` and `du` inside the VM both unreachable) |
| Guest-RAM setting in effect | 7.625 GiB (WSL default) with `memoryMiB=2048` inert, no `.wslconfig` |
| Pagefile | 31.4 GB file, 21.2 GB reported allocated, 4.6 GB used |

The gap between 14.06 GiB of vhdx and ≈1.12 GB of images is the compaction opportunity — **but the volume and
build-cache sizes are unknown**, so the reclaimable amount cannot be promised. Plausible range: 8–12 GB.
Measure before believing it (§6.5).

### 6.2 Minimum free space for the Crawl4AI pull

The image is now a measured size, not an assumption: **2.12 GiB compressed** (§1.4b). Overlay2 expands it
roughly 2×, plus a writable layer and Chromium's cache.

| Threshold | Value | Why |
|---|---|---|
| Abort floor (watchdog) | **5 GiB** | below this, the daemon's own writes fail and the host has historically hung at 15 MB |
| Minimum before starting the pull | **20 GiB** | 2.12 GiB compressed → ~4.5–5 GiB on disk in overlay2, plus the vhdx grows in 1 GiB steps, plus the container's browser/model cache. The previous attempt consumed ~4.6 GB before hanging — consistent with exactly this expansion |
| Today | **10.93 GiB** | ⇒ **the pull is not authorised by this plan.** The download itself would probably fit; the *expansion plus cache plus VM growth* would not, and the failure mode is the 100 %-disk event that produced this wedge. |

The floor stays at 20 GiB even though the image is smaller than feared: the abort cost of pulling into a
10.93 GiB margin is another wedge, and the earlier attempt proves the expansion is real. Free space first
(§6.4), then pull with the watchdog.

### 6.3 vhdx compaction — what it needs

Compaction only reclaims blocks the guest filesystem no longer uses, and it can only be done **while the
virtual disk is not mounted**. Three working paths, in order of preference:

**(a) `Optimize-VHD` (available on this box, needs elevation).** `Get-Command Optimize-VHD` resolves; the
current shell is **not** elevated (`IsInRole(Administrator)` → `False`), so this needs an admin PowerShell:

```powershell
# 1. all distros stopped first (this is the step that needs the owner's agreement)
wsl --shutdown
# 2. compact
Optimize-VHD -Path "$env:LOCALAPPDATA\Docker\wsl\data\ext4.vhdx" -Mode Full
# 3. relaunch Docker Desktop, then verify (§4.3)
```

**(b) `diskpart compact vdisk` (admin, no Hyper-V module needed):**

```
diskpart
  select vdisk file="C:\Users\puria\AppData\Local\Docker\wsl\data\ext4.vhdx"
  attach vdisk readonly
  compact vdisk
  detach vdisk
  exit
```

**(c) `wsl --manage docker-desktop-data --set-sparse true`** — supported by the installed WSL **2.1.5**, and
does *not* need elevation, but it changes how Docker's own data distro is stored. Docker Desktop 4.28 predates
official support for sparse disks, so this is the option to use only if (a) and (b) are unavailable, and only
after a config backup of `~/.docker` and a note that a failed sparse conversion is not trivially reversible.

**`wsl --shutdown` stops ALL distributions.** Verified state right now: `docker-desktop` Running,
`docker-desktop-data` Running, **`Ubuntu` Stopped**. So today the command is harmless for `Ubuntu` — but it
must be re-checked immediately before use, because if the owner starts `Ubuntu` (or any other distro) with
unsaved work, `wsl --shutdown` kills it without warning. Say this out loud to the owner; do not assume.

Ordering, non-negotiable: **restart → verify healthy (§4.3) → then compact**. Compacting first requires a VM
shutdown, which is the same intervention as the restart but with an extra failure mode injected (a partially
converted or truncated vhdx is a much worse incident than a wedged daemon). Do not combine the two.

### 6.4 Where the 9.8 GiB went, and what to free first

`C:` lost **21.18 GiB → 10.93 GiB** between 11:07Z and 12:19Z with **no pull, no build and no container
started**. Candidates, none proven:

1. **Pagefile growth** under commit pressure — measured: `pagefile.sys` is 31.4 GB, `CurrentUsage` 4.6 GB,
   and Windows reports 21.2 GB allocated; `Memory Compression` holds 1.16 GB. This is the leading candidate
   because the timing matches the commit-limit saturation exactly.
2. **VSS shadow copies / System Restore** — invisible to a normal `find` (I scanned `C:\Users\puria\AppData\Local`,
   `C:\Windows\Temp`, `C:\ProgramData`, `Desktop` for files >100 MB modified in the last 3 h: **zero hits**), which
   is consistent with the consumer living in `System Volume Information`.
3. Windows Update staging.

To resolve it before the pull: stop Chrome/Edge/Steam (they also free the RAM that caused the pagefile to
grow), then check `vssadmin list shadowstorage` (admin) and Windows' *Storage settings → Temporary files*.
Freeing memory and shadow storage attacks the same 9.8 GiB from both ends. If free space does not come back
above 15 GiB, the restart should still proceed (it needs ~2 GiB) but the **pull must not**.

### 6.5 Measure before and after

```bash
df -k /c | tail -1
stat -c '%s %y' /c/Users/puria/AppData/Local/Docker/wsl/data/ext4.vhdx
timeout 60 docker system df -v          # only when the daemon is healthy; gives images/volumes/build cache
```

Record all three into `reports/eryndex/evidence/docker-restart-<UTC>/`. Success criteria for a compaction:
vhdx smaller than the pre-compaction size by ≥ 3 GB, `df` free space increased by a similar amount, and
§4.3 steps 1–7 still pass afterwards (compaction must not change what the daemon sees).

---

## 7. What I would do after the owner authorises the restart

### 7.1 Preconditions (all must hold)

1. §4.3 steps 1–8 pass; `docker pull alpine:latest` returned "up to date".
2. Free space **≥ 20 GiB** (§6.2) and a watchdog proving it.
3. The five adapter defects below are fixed and their fixtures green — otherwise a "successful" crawl writes a
   corrupted evidence row and hides the bug (the failure mode already documented in
   `crawl4ai-blocker-analysis.md` §4.1).
4. Docker Desktop's effective VM memory raised via **`.wslconfig`** (`memory=6GB`, `swap=2GB`) — not
   `settings.json:memoryMiB`, which is inert on WSL2 (§1.1). Crawl4AI runs Chromium; 8 GB of guest memory on a
   host with 131 MB free is how we got here.

### 7.2 The Crawl4AI sequence

```bash
# 1. Pin the digest. Client-side, no daemon needed — already done on 2026-11-14T12:24Z (§1.4b):
#    manifest sha256:636a6f9e5127...f1f4, 2.12 GiB compressed, 32 layers. Re-run only to confirm it has not moved.
timeout 60 docker manifest inspect --verbose unclecode/crawl4ai:0.9.5
# expect: the same digest. If it differs, the tag moved — pull by digest:
#    docker pull unclecode/crawl4ai@sha256:636a6f9e5127f54771f14f709bf1b95693aacc30512426b26d09768b0269f1f4

# 2. Disk watchdog, 5 GiB floor, 15 s sampling, abort kills the pull.
#    (script already proven: reports/eryndex/evidence/crawl4ai-disk-watchdog.log, 81 samples, zero drift)
scripts/watch_disk.sh --floor-gib 5 --interval 15 --log reports/eryndex/evidence/crawl4ai-disk-watchdog.log &
watchdog_pid=$!

# 3. The one big transfer, bounded, never bare.
timeout 900 docker pull unclecode/crawl4ai:0.9.5
rc=$?; kill "$watchdog_pid" 2>/dev/null
[ $rc -ne 0 ] && { echo "pull failed rc=$rc — do not retry blindly; check dockerd.log tail"; }

# 4. Run it, loopback only, Chromium's shm requirement honoured.
timeout 90 docker run -d --name eryndex-crawl4ai \
  -p 127.0.0.1:11235:11235 --shm-size=1g \
  -e CRAWL4AI_API_TOKEN="$CRAWL4AI_API_TOKEN" \
  unclecode/crawl4ai:0.9.5
# expect: a container id. `--shm-size=1g` is required for Chromium (provider-facts.md:242).

# 5. Health.
timeout 30 curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:11235/health
# expect: 200 within ~20 s of start; retry up to 5× at 5 s intervals before calling it failed.

# 6. Exactly one crawl, through the adapter, against a page whose content is known.
python -c "from integrations.crawl4ai.adapter import Crawl4AIProvider; ..."   # one request, payload
#    {'urls': ['https://example.com'], 'extract': ['text'], 'max_pages': 1}
# expect: status 'ok', warnings == ('crawl4ai: documented surface (v0.9.5) not yet verified...',) cleared
#         only after endpoint_verified=True is set deliberately, 1 page, text startswith '# Example Domain',
#         content_hash == sha256 of that plain text, evidence excerpt has no "{'raw_markdown'".

# 7. Evidence artifact, written only after 6 passes.
reports/eryndex/evidence/crawl4ai-live-verification.json   # request, response, digest, timings, disk before/after
reports/eryndex/evidence/crawl4ai-live-response.json       # the raw response body
reports/eryndex/evidence/crawl4ai-disk-watchdog.log        # the 15 s samples
```

Then flip `endpoint_verified = True` in `integrations/crawl4ai/adapter.py` in the same commit as the passing
fixture — not before, and never as a side effect of a run that produced a `partial` status.

### 7.3 The five adapter defects to fix first

All five are latent, none caused today's failure, and every one of them becomes visible the moment a live
server answers. Line numbers are against the current files.

**1. `integrations/crawl4ai/adapter.py:88-94` — `markdown` is a nested dict, read as text.**
v0.9.5's `CrawlResult.model_dump()` sets `result["markdown"] = self._markdown.model_dump()`
(`crawl4ai/models.py:281`), i.e. a dict with `raw_markdown`, `markdown_with_citations`, `fit_markdown`…
The adapter takes it as the page text, so `text` is a truthy dict → `status="ok"` → `Evidence.excerpt =
str(dict)[:600]` writes `"{'raw_markdown': '# Example Domain…"` and `content_hash` becomes the SHA-256 of a
Python repr. Every provenance row is silently wrong while everything reports success.
*Intent of the fix:* normalise before use — if the value is a `Mapping`, take `raw_markdown`, else
`markdown_with_citations`, else `fit_markdown`; if it is a `str`, keep it. Fail closed (`invalid_response`)
when a page has a `markdown` key that yields no string, instead of coercing it.
*Fixture that proves it:* `tests/fixtures/crawl4ai_v095_crawlresult.json` — a real saved response body with
`{"markdown": {"raw_markdown": "# Example Domain\n…", "fit_markdown": "…"}}`. Assert `isinstance(page["text"], str)`,
`page["text"].startswith("# Example Domain")`, `page["status"] == "ok"`,
`content_hash == hashlib.sha256(page["text"].encode()).hexdigest()`, and `"'raw_markdown'" not in evidence.excerpt`.
The last assertion is the one that fails today.

**2. `integrations/crawl4ai/adapter.py:70` — `cache_mode` sent as a bare uppercase string.**
`CacheMode` is a plain `Enum` whose values are lowercase (`crawl4ai/cache_context.py:16-20`: `BYPASS = "bypass"`),
and the server parses `crawler_config` via `CrawlerRunConfig.load(..., provenance=UNTRUSTED)`
(`deploy/docker/api.py:771`), which expects the serialised `{type: …, params: {…}}` form. The adapter sends
`"ENABLED"` / `"BYPASS"`. `"BYPASS" != CacheMode.BYPASS`, so cache bypass is at best a silent no-op; whether
the untrusted gate 400s is unverified because there is no server yet.
*Intent of the fix:* send the enum **value** (`"bypass"` / `"enabled"`, lowercase as the library defines them) and,
if the gate rejects a bare mapping, wrap it as `{"type": "CrawlerRunConfig", "params": {...}}`. Evidence first:
one live request decides which of the two shapes is correct — do not guess twice.
*Fixture that proves it:* a unit test on the built body (`assert body["crawler_config"]["cache_mode"] == "bypass"`
when `bypass_cache=True`), plus one live assertion — two consecutive crawls of the same URL with
`bypass_cache=False` where the second shows a cache hit (or the server echoes the effective config), which is
the only thing that proves the value was *applied* rather than merely accepted.

**3. `integrations/crawl4ai/adapter.py:57-63` vs `:68-71` — `extract` is validated then dropped.**
`validate()` requires `extract ⊆ {"text","links","metadata"}` and `_call()` never puts it in the body. The wire
capture from the last run proves it: the body was `{urls, crawler_config: {cache_mode}}`. A caller asking for
links gets text-only output and a `"ok"` status — a data-quality lie, and the reason a citation might point at
content the adapter never actually requested.
*Intent of the fix:* map the three values onto real `CrawlerRunConfig` knobs (text → the markdown generator /
`only_text` filter, links → a link-extraction strategy, metadata → the metadata the result already carries), and
**raise `ProviderError("schema_violation")` for anything that cannot be mapped** rather than accepting and
ignoring it. Silently dropping a caller's requirement is worse than refusing it.
*Fixture that proves it:* a body-shape unit test (`extract=["links"]` → the serialised config contains a link
strategy; `extract=["text","links"]` → both present) and a response fixture whose page has `links` populated,
asserting the adapter surfaces them into the page dict. Today, `extract` never appears in either — the test
asserting its presence is the proof.

**4. `integrations/crawl4ai/adapter.py:5` — the docstring promises `browser_config`; the code never sends it.**
The module docstring advertises `{"urls": [...], "browser_config": {...}, "crawler_config": {...}}`, so a reader
believes page timeout, user agent and viewport are controllable. Nothing is: no page timeout means a slow page
is bounded only by the HTTP client's timeout, and no user agent means the default (detectable) one.
*Intent of the fix:* either implement it (validate a small allow-list — `page_timeout`, `user_agent`, `viewport`,
`headless` — and pass it as `browser_config` in the body), or delete the claim from the docstring and from
`docs/architecture/integrations/provider-facts.md`. Do not leave documentation that describes a capability the
code does not have; that is how a "verified" surface becomes a lie.
*Fixture that proves it:* if implemented, a body test (`payload["browser_config"]["page_timeout"]` appears in the
request body); if removed, a documentation test — this repo already has `tests/test_architecture_boundaries.py`
as the natural home for "documented surface == implemented surface".

**5. `contracts/provider.py:41-70` — no provider-issued id on `Evidence`.**
`Evidence` carries `provider` (a free-form `name`, here `"crawl4ai"`) and `provider_version`, but no stable
identifier, and v0.9.5's `CrawlResult` supplies none either. A citation row therefore cannot be joined to a
provider registry entry, cannot be deduplicated across two providers that share a name, and cannot be
invalidated when a provider's behaviour changes. Only `url + content_hash + retrieved_at` is available — which
is a citation, but not a *provenanced* citation.
*Intent of the fix:* add `provider_id: str` to `ProviderInfo` (default `f"{name}@{version}"`, validated
non-empty, emitted in `to_dict()`), and mirror it onto `Evidence` so stored rows self-identify. Keep the
default so existing rows and tests keep parsing — the field is additive.
*Fixture that proves it:* extend `tests/test_provider_contract.py` — `ProviderResult.to_dict()["provider"]`
contains a non-empty `provider_id` equal to `"crawl4ai@0.9.5"`; an `Evidence` built without it gets the default;
and a backwards-compatibility test that a serialised row lacking `provider_id` still loads (proving the change
does not break stored evidence).

---

## 8. Unknowns — facts I could not measure

Marked explicitly rather than guessed:

1. **What actually consumed 9.8 GiB of `C:` during this session** (pagefile 31.4 GB is the leading candidate;
   VSS shadow copies and Windows Update staging are the others; a >100 MB recent-file scan in the user and
   temp trees found nothing). Resolve with `vssadmin list shadowstorage` and Storage settings before the pull.
2. **Disk actually used inside the vhdx** — `docker system df` hung at 30 s; `du` inside the VM is unreachable.
   Therefore the reclaimable amount from compaction is an estimate (8–12 GB), not a number.
3. ~~Crawl4AI image size and digest~~ — **resolved during this session**: 2.12 GiB compressed, 32 layers,
   manifest `sha256:636a6f9e5127…f1f4`, obtained client-side via `docker manifest inspect` while the daemon was
   dead (§1.4b). The *uncompressed* on-disk size is still an estimate (~4.5–5 GiB), which `docker image inspect`
   will state exactly after the restart.
4. **Whether the current network set is intact** — `docker network ls` never returned. Only
   `crypto-trading-bot_default` is known, and only indirectly, via `HostConfig.NetworkMode`.
5. **The exact first `autovacuum` warning time** — `docker logs --tail ≥ 100` hangs, so the 60 s warnings can
   only be seen for the last 30 minutes; the 3 h figure is derived from `FailingStreak 1161 × 10 s`, not from a
   log line.
6. **Open transactions / in-flight clients at restart time** — `pg_stat_activity` is unreachable.
7. **Whether `postgres` will need WAL crash recovery** on restart, and how long it will take — depends on whether
   the postmaster can shut down cleanly, which depends on whether it can `fork()`, which it currently cannot.
8. **Whether the daemon wedge is memory starvation or a corrupted datastore** — §2.4 is a hypothesis built from
   host numbers plus the absence of storage errors, not a proven causal chain. A goroutine dump (only obtainable
   at the next wedge, with `SIGQUIT`, which is itself an intervention) would settle it.
9. **The volume's `CreatedAt`** — `docker volume inspect` answered once for name/driver/mountpoint but the
   daemon stopped answering before the full JSON could be read; the volume's age is inferred from compose labels
   and the container's 16 h uptime.
10. **The Docker Desktop GUI's internal state** (engine status as *it* sees it) — not inspected; no UI automation
    was used, by design.

---

## Appendix — evidence index

| Artefact | Path | Contents |
|---|---|---|
| Probe ledger (final matrix) | `C:/Users/puria/AppData/Local/Temp/docker_probes.tsv` | 16 probes, timestamps, rc, ms |
| Container inspect (raw) | `C:/Users/puria/AppData/Local/Temp/pginspect.json` | 8 049 bytes, full config §3.1 is derived from |
| Container log tail | `C:/Users/puria/AppData/Local/Temp/pglogs.txt` | last 30 lines (the only readable slice) |
| Probe script | `C:/Users/puria/AppData/Local/Temp/docker_probe_matrix.sh` | reproducible bounded matrix |
| Engine log | `C:/Users/puria/AppData/Local/Docker/log/vm/dockerd.log` (+`.0`…`.8`) | §2.2 quotes, ~10 MB window |
| Compose source | `…/code-workspace/projects/crypto-trading-bot/docker-compose.yml` | named volume + `unless-stopped` |
| Crawl4AI manifest | `C:/Users/puria/AppData/Local/Temp/c4a_manifest.json` | 35 270 B, digest + 32 layer sizes (§1.4b) |

All scratch artefacts are outside the repository, as required. Nothing in the repo was modified except this
file.
