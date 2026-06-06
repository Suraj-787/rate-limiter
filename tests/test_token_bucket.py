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
