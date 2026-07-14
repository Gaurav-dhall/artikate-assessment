# DESIGN.md — Section 2: Rate-Limited Async Job Queue

## The Problem in Concrete Terms

The system must send transactional emails (order confirmations, OTPs, alerts) where:
- The third-party provider enforces a hard cap of **200 emails/minute**
- Flash sales generate bursts of **2,000 requests in under 10 seconds**
- A worker crash mid-run must **never silently drop a job**
- Failures must be **retried automatically** with backoff, not lost

These four constraints together rule out simple solutions — a naive
`threading.Thread` approach has no persistence (crash = job lost), a
`time.sleep()` throttle blocks the process entirely, and in-memory queues
don't survive restarts. The design needs persistence, rate control,
and crash recovery as first-class properties.

---

## Architecture Decision: Why Celery + Redis

Three options were evaluated seriously.

### Option A: Celery + Redis (chosen)

**What it is:** Celery is a distributed task queue. Tasks are serialised
and pushed to a Redis broker. One or more worker processes pull from that
broker and execute tasks independently of the web process.

**Why it fits this problem:**

- **Persistence by default.** Tasks live in Redis between enqueue and
  execution. If a worker crashes after pulling a task but before
  completing it, the task is redelivered — provided `acks_late=True` is
  set (see SIGKILL section below). Without this, the broker marks a task
  acknowledged the moment the worker receives it, so a crash loses it.
  With `acks_late=True`, acknowledgement only happens after successful
  completion — crash safety is guaranteed at the broker level, not by
  application code.

- **Fine-grained retry control.** `autoretry_for`, `retry_backoff`,
  `retry_backoff_max`, and `retry_jitter` are first-class decorator
  parameters. Exponential backoff with jitter is two lines, not a manual
  retry loop. Two distinct retry paths are needed here (rate-limit
  throttle vs. genuine SMTP failure) and Celery supports both cleanly
  with different `max_retries` budgets.

- **Separation of concerns.** The web process enqueues; workers consume.
  A burst of 2,000 requests in 10 seconds is absorbed into Redis
  immediately (enqueue is O(1), sub-millisecond) without the web
  process blocking. Workers drain the queue at the rate the rate limiter
  permits — 200/minute — independently.

- **Production-proven.** Celery + Redis is the dominant stack for
  Django background tasks in production. The failure modes are
  well-documented and the operational tooling (Flower, `celery inspect`,
  `celery purge`) is mature.

**What it sacrifices:**

- Operational overhead: requires Redis running as a separate process.
  In a truly minimal setup (single dyno, no infrastructure), this is
  extra complexity.
- Celery's configuration surface is large. Misconfigured settings
  (`prefetch_multiplier`, `acks_late`, `reject_on_worker_lost`) can
  silently introduce the exact bugs they are meant to prevent. Each
  relevant setting is explicitly set and commented in `settings.py`
  rather than relying on defaults.

---

### Option B: Django Q

**What it is:** A task queue built specifically for Django. Uses the
Django ORM as its broker by default (tasks stored in the application's
own database), with optional Redis/ORM backends.

**Why it was not chosen:**

- **Database-as-broker is the wrong tool here.** With 2,000 tasks
  arriving in 10 seconds, every enqueue is an `INSERT` into the task
  table and every dequeue is a `SELECT ... FOR UPDATE`. Under burst load,
  this creates lock contention on the task table at the exact moment the
  application is under its heaviest stress. Redis handles the same
  operations at in-memory speeds with no locking.

- **Retry and backoff ergonomics are weaker.** Django Q supports
  retries via `Retry` exceptions but does not expose `retry_backoff`,
  `retry_jitter`, or `retry_backoff_max` as first-class parameters.
  Implementing exponential backoff with jitter requires manual
  `countdown` calculation in the task body — code that Celery's
  decorator handles out of the box and that is easy to get wrong under
  edge cases (e.g. overflow on large retry counts).

- **Dead-letter handling is not built in.** Tasks that exhaust retries
  are simply marked `failed` in the ORM table. There is no signal
  equivalent to Celery's `task_failure` to hook into for structured
  dead-letter recording without patching Django Q internals.

**Where Django Q would win:** a project that genuinely cannot add Redis
as a dependency, or one where simplicity of setup (no extra
infrastructure, just the existing database) outweighs the throughput
and retry-ergonomics gap. For this assessment's explicit burst-load and
retry requirements, the trade-offs go the wrong way.

---

### Option C: Custom Implementation

**What it is:** A hand-rolled queue using Redis lists (`RPUSH` / `BLPOP`)
or a database table, with Python worker threads or processes consuming
from it.

**Why it was not chosen:**

- **Reinventing solved problems, badly.** A production-quality task
  queue needs: at-least-once delivery guarantees, worker concurrency
  control, task serialisation, retry scheduling, dead-letter capture,
  graceful shutdown, heartbeating, and monitoring hooks. Celery
  implements all of these, tested at scale across thousands of
  deployments. A custom implementation would spend weeks reaching the
  same level of correctness.

- **The failure modes are non-obvious.** `BLPOP` pops a task off the
  list atomically, but if the worker crashes before completing it, the
  task is gone — there is no built-in equivalent of `acks_late`. A
  reliable custom implementation needs a "processing" set, a visibility
  timeout, and a reaper process to recover stale tasks — essentially
  reimplementing the parts of Celery that matter most for this problem.

**Where a custom implementation would win:** a system with extremely
unusual constraints (e.g. a task payload too large for Redis, strict
latency requirements that Celery's overhead violates, or a deployment
environment where no external dependencies are permitted). None of those
apply here.

---

## Rate Limiter Design

### Algorithm Choice: Sliding Window Log (Redis Sorted Set)

Three standard algorithms were considered:

**Fixed window (Redis `INCR` + `EXPIRE`):** Simplest to implement.
Maintains a counter per fixed 60-second clock interval. Critical flaw:
a burst of 200 requests in the last second of one window, followed by
200 in the first second of the next, results in 400 sends in a 2-second
span — double the intended rate, with no violation detectable by the
counter. For a provider-enforced hard cap, this boundary-edge burst
is unacceptable.

**Token bucket (Redis `DECR` + TTL):** Closer to correct. A bucket
starts with N tokens and refills at a steady rate. Better for smoothing
bursty traffic, but the refill logic is hard to implement atomically
in Redis without a Lua script that is essentially as complex as the
sliding window, and the "current fill level" is less intuitive to reason
about in tests and monitoring dashboards than a direct "requests in the
last 60 seconds" count.

**Sliding window log (Redis sorted set + `ZREMRANGEBYSCORE`) (chosen):**
Stores the timestamp of each accepted request as a member of a sorted
set. On each check: remove members older than 60 seconds, count
remaining members, allow if below limit. The window is always exactly
"the last 60 seconds from now" — there are no fixed boundaries, so the
boundary-edge burst problem does not exist. The count visible in Redis
at any moment is the literal number of sends that have occurred in the
true last 60 seconds.

**Trade-off acknowledged:** the sorted set grows proportionally to
accepted requests (up to 200 members per window). At the rate limits
in this problem (200/minute) this is negligible. At much higher rates
(e.g. 200,000/minute) a fixed-window or token-bucket approach would
use less Redis memory.

---

### Atomicity Guarantee: Lua Script

The check-then-act sequence (read count → compare to limit → write new
entry) must be atomic. Without atomicity, two worker processes can both
read a count of 199 simultaneously, both decide "under limit," both
write — resulting in 201 sends, silently exceeding the cap.

Three standard approaches exist:

- **`MULTI`/`EXEC` (Redis transactions):** Queues commands and executes
  them together, but does not support conditional logic inside the
  transaction — the `if count < limit` check cannot be expressed, so
  optimistic locking (`WATCH`) would be needed, which introduces retry
  loops at the application layer.
- **Pipeline:** Sends multiple commands in one network round-trip, but
  does not guarantee atomicity — another client can interleave between
  the pipelined commands.
- **Lua script (chosen):** Redis executes Lua scripts atomically and
  single-threadedly. The entire read-compare-write sequence runs as one
  indivisible operation. No other Redis command can interleave. The
  conditional is expressed naturally inside the script. The script is
  registered once at startup via `register_script()` and cached on the
  Redis server — subsequent calls are a single `EVALSHA` command.

See `jobs/rate_limiter.py` for the full Lua script and inline comments
explaining each Redis command.

---

### Redis Failure Behaviour: Fail Closed

If Redis is unavailable (connection refused, timeout, crash), the rate
limiter's `is_allowed()` function catches `redis.exceptions.RedisError`
and returns `False` — **fail closed**, meaning email sending is blocked
until Redis recovers.

**Why fail closed, not fail open:**

Fail open (returning `True` on Redis error, allowing sends to proceed)
risks violating the provider's hard rate cap. If Redis goes down during
a burst and workers continue sending, the provider may throttle or
ban the account — an outcome worse than delayed delivery. Email delivery
delay is recoverable (tasks stay in Celery's queue and resume when Redis
returns). Provider account suspension may not be.

**What this sacrifices:** during a Redis outage, no emails go out even
if the provider would have capacity. Tasks will accumulate in Celery's
broker (also Redis). If the broker Redis and the rate-limiter Redis are
the same instance (as in this implementation), a Redis outage means
both the broker and the rate limiter are down simultaneously — tasks
cannot be consumed at all regardless of fail-open/closed choice, making
the distinction moot in that specific scenario. In a production system,
separating the broker Redis from the rate-limiter Redis instance would
make the fail-open/closed choice meaningful under partial failures.

---

## SIGKILL Handling

See `ANSWERS.md — Section 2` for the full written answer. In summary:

- `acks_late=True` on the task: broker acknowledgement deferred until
  after task completion, so a SIGKILL mid-task causes the broker to
  redeliver the task to another worker.
- `CELERY_TASK_REJECT_ON_WORKER_LOST = True` in settings: ensures the
  task is rejected (requeued) rather than marked as failed when the
  worker process disappears unexpectedly.
- `CELERY_WORKER_PREFETCH_MULTIPLIER = 1` in settings: limits each
  worker to holding one unacknowledged task at a time, so a crash
  affects at most one in-flight task, not a batch of prefetched ones.
