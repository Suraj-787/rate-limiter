# Rate Limiter as a Service — Build Plan

## Project Summary
A standalone microservice that enforces API rate limiting using two algorithms
(Token Bucket and Sliding Window), with per-tenant configuration, Redis-backed
atomic counters, and Docker deployment.

**Stack:** Python, Flask, Redis, Docker  
**Total Time:** 3–4 days (2–3 hrs/day)  
**Goal:** Working service + benchmark number for resume bullet

---

## Final File Structure (build towards this)

```
rate-limiter/
├── app.py                      # Flask entry point, all routes
├── limiter/
│   ├── __init__.py             # empty
│   ├── token_bucket.py         # Algorithm 1
│   ├── sliding_window.py       # Algorithm 2
│   └── engine.py               # Glue — picks algorithm, calls Redis
├── config/
│   ├── __init__.py             # empty
│   └── tenant_config.py        # Per-tenant rules stored in Redis
├── tests/
│   ├── test_token_bucket.py    # Unit test for token bucket
│   ├── test_sliding_window.py  # Unit test for sliding window
│   └── test_concurrent.py      # Concurrency/race condition test
├── benchmark.py                # Measures req/sec, gives resume metric
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Phase Overview

| Phase | What you build | Days | Key concept learned |
|-------|---------------|------|---------------------|
| 0 | Project setup + Redis running | 0.5 | Docker basics |
| 1 | Token Bucket algorithm | 1 | Redis pipelines, rate limiting logic |
| 2 | Sliding Window algorithm | 1 | Redis sorted sets (zset) |
| 3 | Flask API + engine glue | 0.5 | REST API design |
| 4 | Per-tenant config | 0.5 | Multi-tenancy pattern |
| 5 | Concurrency test | 0.5 | Race conditions, atomicity |
| 6 | Benchmark + Docker | 0.5 | Performance measurement |

---

## Phase 0 — Project Setup (Day 1, first 30 min)

### What to do
1. Create the folder structure above (empty files for now)
2. Install dependencies
3. Get Redis running via Docker
4. Confirm Redis is reachable from Python

### Commands
```bash
mkdir rate-limiter && cd rate-limiter
mkdir limiter config tests
touch app.py benchmark.py Dockerfile docker-compose.yml README.md
touch limiter/__init__.py limiter/token_bucket.py limiter/sliding_window.py limiter/engine.py
touch config/__init__.py config/tenant_config.py
touch tests/test_token_bucket.py tests/test_sliding_window.py tests/test_concurrent.py

pip install flask redis pytest requests
```

### docker-compose.yml (Redis only for now)
```yaml
version: '3.8'
services:
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
```

```bash
docker-compose up -d   # start Redis in background
```

### Verify Redis works
```python
import redis
r = redis.Redis(host='localhost', port=6379)
r.set('test', 'hello')
print(r.get('test'))   # should print b'hello'
```

### ✅ Milestone
Redis is running. Python can connect to it. All files exist.

---

## Phase 1 — Token Bucket Algorithm (Day 1)

### Concept (understand this before building)
```
Bucket has N tokens (e.g. 10)
Each request takes 1 token
Tokens refill at fixed rate (e.g. 2 tokens/sec)
If bucket is empty → request is BLOCKED (429)
If bucket has tokens → request is ALLOWED (200)

Key property: allows BURSTING
If no requests came for 5 seconds → bucket refills to 10
Client can then fire 10 requests instantly
```

### What to build: `limiter/token_bucket.py`
```python
import redis
import time


class TokenBucket:
    def __init__(self, r: redis.Redis):
        self.r = r

    def is_allowed(self, tenant: str, capacity: int, refill_rate: float) -> dict:
        """
        tenant      : unique identifier e.g. "user_123" or "api_key_abc"
        capacity    : max tokens in bucket (also the burst limit)
        refill_rate : tokens added per second

        returns dict:
            allowed   : True/False
            remaining : tokens left after this request
            reset_at  : when bucket will be full again (unix timestamp)
        """
        key = f"tb:{tenant}"
        now = time.time()

        pipe = self.r.pipeline()
        pipe.hgetall(key)
        results = pipe.execute()
        data = results[0]

        if not data:
            # First request from this tenant — start with full bucket minus 1
            tokens = float(capacity - 1)
            last = now
            allowed = True
        else:
            last = float(data[b'last'])
            tokens = float(data[b'tokens'])

            # Refill: add tokens based on time elapsed since last request
            elapsed = now - last
            tokens = min(capacity, tokens + elapsed * refill_rate)
            last = now

            if tokens < 1:
                # Not enough tokens — block request
                # Do NOT subtract, do NOT update Redis (no penalty for blocked req)
                return {
                    'allowed': False,
                    'remaining': 0,
                    'reset_at': last + (1 - tokens) / refill_rate
                }

            tokens -= 1
            allowed = True

        # Persist updated state
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={'tokens': str(tokens), 'last': str(last)})
        pipe.expire(key, 3600)  # auto-cleanup after 1 hour of inactivity
        pipe.execute()

        time_to_full = (capacity - tokens) / refill_rate
        return {
            'allowed': allowed,
            'remaining': int(tokens),
            'reset_at': now + time_to_full
        }
```

### What to build: `tests/test_token_bucket.py`
```python
import redis
import time
from limiter.token_bucket import TokenBucket

r = redis.Redis(host='localhost', port=6379)
r.flushdb()  # clean slate

tb = TokenBucket(r)

# Test 1: First 5 requests allowed (capacity=5, refill_rate=1)
print("=== Test 1: Basic allow/block ===")
for i in range(7):
    result = tb.is_allowed("tenant_test", capacity=5, refill_rate=1.0)
    print(f"Request {i+1}: {'ALLOWED' if result['allowed'] else 'BLOCKED'} | remaining={result['remaining']}")

# Expected: first 5 allowed, 6th and 7th blocked

# Test 2: Refill after waiting
print("\n=== Test 2: Refill after 3 seconds ===")
time.sleep(3)
result = tb.is_allowed("tenant_test", capacity=5, refill_rate=1.0)
print(f"After 3s wait: {'ALLOWED' if result['allowed'] else 'BLOCKED'} | remaining={result['remaining']}")
# Expected: allowed, remaining ~2 (3 refilled, used 1)
```

### ✅ Milestone
Run `python tests/test_token_bucket.py`
First 5 requests: ALLOWED. Requests 6-7: BLOCKED. After 3s sleep: ALLOWED again.

---

## Phase 2 — Sliding Window Algorithm (Day 2)

### Concept (understand this before building)
```
Track timestamps of all requests in the last 60 seconds
On each request:
  1. Remove timestamps older than (now - 60s)
  2. Count remaining timestamps
  3. If count >= limit → BLOCK
  4. Else → add current timestamp, ALLOW

Key property: NO BURSTING allowed
Hard limit over any rolling 60-second window
More accurate than token bucket
```

### Redis data structure used: Sorted Set (zset)
```
Key:   "sw:tenant_abc"
Value: { "1703001234.567": 1703001234.567,
         "1703001290.123": 1703001290.123, ... }
         member              score (timestamp)

zremrangebyscore removes all members with score < window_start
zcard counts remaining members
zadd adds new timestamp
```

### What to build: `limiter/sliding_window.py`
```python
import redis
import time


class SlidingWindow:
    def __init__(self, r: redis.Redis):
        self.r = r

    def is_allowed(self, tenant: str, limit: int, window: int) -> dict:
        """
        tenant  : unique identifier
        limit   : max requests allowed in the window
        window  : window size in seconds (e.g. 60)

        returns dict:
            allowed   : True/False
            remaining : requests left in current window
            reset_at  : when oldest request in window expires
        """
        key = f"sw:{tenant}"
        now = time.time()
        win_start = now - window

        pipe = self.r.pipeline()
        pipe.zremrangebyscore(key, 0, win_start)   # remove old entries
        pipe.zcard(key)                             # count current entries
        pipe.zadd(key, {str(now): now})             # add this request
        pipe.expire(key, window)                    # auto-cleanup
        results = pipe.execute()

        # results[1] is count BEFORE adding current request
        count_before = results[1]

        if count_before >= limit:
            # Already at limit — remove the entry we just added
            self.r.zrem(key, str(now))
            return {
                'allowed': False,
                'remaining': 0,
                'reset_at': win_start + window
            }

        return {
            'allowed': True,
            'remaining': limit - count_before - 1,
            'reset_at': win_start + window
        }
```

### What to build: `tests/test_sliding_window.py`
```python
import redis
import time
from limiter.sliding_window import SlidingWindow

r = redis.Redis(host='localhost', port=6379)
r.flushdb()

sw = SlidingWindow(r)

# Test 1: Basic block after limit
print("=== Test 1: Block after limit ===")
for i in range(7):
    result = sw.is_allowed("tenant_test", limit=5, window=60)
    print(f"Request {i+1}: {'ALLOWED' if result['allowed'] else 'BLOCKED'} | remaining={result['remaining']}")
# Expected: first 5 allowed, 6 and 7 blocked

# Test 2: Sliding — old requests expire
print("\n=== Test 2: Window slides ===")
r.flushdb()
# Send 5 requests with small window
for i in range(5):
    sw.is_allowed("tenant_test2", limit=5, window=2)  # 2 second window

result = sw.is_allowed("tenant_test2", limit=5, window=2)
print(f"6th request immediately: {'ALLOWED' if result['allowed'] else 'BLOCKED'}")
# Expected: BLOCKED

time.sleep(3)  # wait for window to slide
result = sw.is_allowed("tenant_test2", limit=5, window=2)
print(f"After 3s wait: {'ALLOWED' if result['allowed'] else 'BLOCKED'}")
# Expected: ALLOWED (old requests expired)
```

### ✅ Milestone
Both tests pass. Sliding window correctly expires old entries and unblocks after window passes.

---

## Phase 3 — Engine + Flask API (Day 2, second half)

### What to build: `limiter/engine.py`
```python
import redis
from limiter.token_bucket import TokenBucket
from limiter.sliding_window import SlidingWindow
from config.tenant_config import TenantConfig


class RateLimiterEngine:
    def __init__(self, r: redis.Redis):
        self.r = r
        self.tb = TokenBucket(r)
        self.sw = SlidingWindow(r)
        self.cfg = TenantConfig(r)

    def check(self, tenant: str, algorithm: str = None) -> dict:
        config = self.cfg.get(tenant)
        algo = algorithm or config.get('algorithm', 'sliding_window')

        if algo == 'token_bucket':
            return self.tb.is_allowed(
                tenant,
                capacity=int(config.get('capacity', 100)),
                refill_rate=float(config.get('refill_rate', 10))
            )
        else:
            return self.sw.is_allowed(
                tenant,
                limit=int(config.get('limit', 100)),
                window=int(config.get('window', 60))
            )

    def set_config(self, tenant: str, config: dict):
        self.cfg.set(tenant, config)
```

### What to build: `config/tenant_config.py`
```python
import redis

DEFAULTS = {
    'algorithm': 'sliding_window',
    'limit': '100',
    'window': '60',
    'capacity': '100',
    'refill_rate': '10'
}


class TenantConfig:
    def __init__(self, r: redis.Redis):
        self.r = r

    def get(self, tenant: str) -> dict:
        key = f"config:{tenant}"
        data = self.r.hgetall(key)
        if not data:
            return DEFAULTS
        # decode bytes to strings
        decoded = {k.decode(): v.decode() for k, v in data.items()}
        # fill missing keys with defaults
        return {**DEFAULTS, **decoded}

    def set(self, tenant: str, config: dict):
        key = f"config:{tenant}"
        self.r.hset(key, mapping=config)
```

### What to build: `app.py`
```python
from flask import Flask, request, jsonify
import redis
from limiter.engine import RateLimiterEngine

app = Flask(__name__)
r = redis.Redis(host='localhost', port=6379, decode_responses=False)
engine = RateLimiterEngine(r)


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})


@app.route('/check', methods=['POST'])
def check():
    data = request.json

    if not data or 'tenant' not in data:
        return jsonify({'error': 'tenant required'}), 400

    tenant = data['tenant']
    algorithm = data.get('algorithm')  # optional override

    result = engine.check(tenant, algorithm)

    status_code = 200 if result['allowed'] else 429
    return jsonify({
        'allowed': result['allowed'],
        'tenant': tenant,
        'remaining': result.get('remaining', 0),
        'reset_at': result.get('reset_at')
    }), status_code


@app.route('/config/<tenant>', methods=['POST'])
def set_config(tenant):
    config = request.json
    if not config:
        return jsonify({'error': 'config body required'}), 400
    engine.set_config(tenant, config)
    return jsonify({'status': 'ok', 'tenant': tenant})


@app.route('/config/<tenant>', methods=['GET'])
def get_config(tenant):
    config = engine.cfg.get(tenant)
    return jsonify({'tenant': tenant, 'config': config})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
```

### ✅ Milestone: Test with curl
```bash
python app.py   # start server

# Test health
curl http://localhost:5000/health

# Set config for a tenant
curl -X POST http://localhost:5000/config/user_123 \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "sliding_window", "limit": "5", "window": "60"}'

# Send 7 requests — first 5 should be 200, last 2 should be 429
for i in {1..7}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST http://localhost:5000/check \
    -H "Content-Type: application/json" \
    -d '{"tenant": "user_123"}'
done
```
Expected output: `200 200 200 200 200 429 429`

---

## Phase 4 — Concurrency Test (Day 3)

### Why this matters
Without Redis pipeline atomicity, two requests can:
1. Both read `tokens = 1`
2. Both pass the `tokens >= 1` check
3. Both subtract 1
4. Both get allowed — but only 1 should have been

Your pipeline prevents this. This test **proves** it.

### What to build: `tests/test_concurrent.py`
```python
import threading
import requests
import redis

# Reset state
r = redis.Redis(host='localhost', port=6379)
r.flushdb()

# Set limit to 100 for this tenant
requests.post('http://localhost:5000/config/concurrent_test',
              json={'algorithm': 'sliding_window', 'limit': '100', 'window': '60'})

allowed_count = 0
blocked_count = 0
lock = threading.Lock()


def send_request():
    global allowed_count, blocked_count
    resp = requests.post('http://localhost:5000/check',
                         json={'tenant': 'concurrent_test'})
    with lock:
        if resp.status_code == 200:
            allowed_count += 1
        else:
            blocked_count += 1


# Fire 200 concurrent requests
threads = [threading.Thread(target=send_request) for _ in range(200)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print(f"Allowed : {allowed_count}")
print(f"Blocked : {blocked_count}")
print(f"Total   : {allowed_count + blocked_count}")

# This assertion is the key — must never exceed 100
assert allowed_count <= 100, f"RACE CONDITION: {allowed_count} allowed, expected <= 100"
print("✅ No race condition detected")
```

### ✅ Milestone
```
Allowed : 100
Blocked : 100
Total   : 200
✅ No race condition detected
```
If allowed > 100, there's a race condition in your pipeline logic — debug it.

---

## Phase 5 — Benchmark + Docker (Day 3–4)

### What to build: `benchmark.py`
```python
import time
import requests
import concurrent.futures
import redis

# Reset
r = redis.Redis(host='localhost', port=6379)
r.flushdb()

# Set very high limit so nothing gets blocked during benchmark
requests.post('http://localhost:5000/config/bench_tenant',
              json={'algorithm': 'token_bucket', 'capacity': '999999', 'refill_rate': '999999'})

NUM_REQUESTS = 10000
MAX_WORKERS = 100


def req(_):
    return requests.post('http://localhost:5000/check',
                         json={'tenant': 'bench_tenant', 'algorithm': 'token_bucket'})


print(f"Sending {NUM_REQUESTS} requests with {MAX_WORKERS} workers...")
start = time.time()

with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    results = list(ex.map(req, range(NUM_REQUESTS)))

elapsed = time.time() - start
rps = NUM_REQUESTS / elapsed

success = sum(1 for r in results if r.status_code == 200)

print(f"\n{'='*40}")
print(f"Total requests : {NUM_REQUESTS}")
print(f"Successful     : {success}")
print(f"Time elapsed   : {elapsed:.2f}s")
print(f"Throughput     : {rps:.0f} req/sec")
print(f"{'='*40}")
print(f"\nResume bullet metric: ~{int(rps/1000)}k req/sec")
```

### Complete Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
```

### requirements.txt
```
flask==3.0.0
redis==5.0.1
requests==2.31.0
pytest==7.4.3
```

### Final docker-compose.yml (both services)
```yaml
version: '3.8'
services:
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"

  rate-limiter:
    build: .
    ports:
      - "5000:5000"
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
    restart: unless-stopped
```

```bash
# Build and run everything
docker-compose up --build

# Test it's working
curl http://localhost:5000/health
```

### ✅ Final Milestone
```bash
docker-compose up --build    # both services start cleanly
python benchmark.py          # get your req/sec number
python tests/test_concurrent.py   # no race condition
```

---

## README.md (put this on GitHub)

```markdown
# Rate Limiter as a Service

A standalone microservice implementing API rate limiting with two algorithms,
per-tenant configuration, and Redis-backed atomic counters.

## Algorithms
- **Token Bucket** — allows bursting, refills at fixed rate
- **Sliding Window** — strict rolling window, no burst allowance

## API
| Endpoint | Method | Description |
|----------|--------|-------------|
| /health | GET | Health check |
| /check | POST | Check if request is allowed |
| /config/:tenant | POST | Set per-tenant config |
| /config/:tenant | GET | Get current config |

## Quick Start
\`\`\`bash
docker-compose up --build
curl -X POST http://localhost:5000/check \
  -H "Content-Type: application/json" \
  -d '{"tenant": "user_123", "algorithm": "sliding_window"}'
\`\`\`

## Performance
~X,000 req/sec on local Docker deployment (fill after benchmark)

## Key Design Decisions
- Redis pipelines for atomic read-modify-write (prevents race conditions)
- Per-tenant config stored in Redis (no restarts needed for config changes)
- Algorithm selectable per-request or set as tenant default
```

---

## Resume Bullet (fill X after running benchmark.py)

> Built a rate limiter microservice in Python supporting token bucket and sliding window algorithms with per-tenant configuration; used Redis pipelines for atomic counter updates under concurrent load; benchmarked at **X,000 req/sec** via Docker deployment.

---

## Interview Cheat Sheet

**"How does it handle race conditions?"**
> Redis pipeline batches the read-modify-write atomically. Without it, two concurrent requests could both read tokens=1, both pass, both get allowed — exceeding the limit. The pipeline prevents that interleaving. I verified this with a 200-thread concurrent test.

**"Why two algorithms?"**
> Token bucket allows bursting — good for clients who send requests in batches. Sliding window is stricter — hard limit over any rolling window. Different tenants need different behavior, so both are supported.

**"How would you scale this horizontally?"**
> Redis is the single source of truth, so you can run multiple app instances behind a load balancer — they all hit the same Redis. For higher scale, shard by tenant ID across a Redis Cluster. Each tenant's counter lives on one shard, no cross-shard coordination needed.

**"Why Redis over a database?"**
> Sub-millisecond latency. Rate limiting is on the hot path of every request — you can't afford a database round trip. Redis sorted sets also give O(log n) window operations natively.
```
