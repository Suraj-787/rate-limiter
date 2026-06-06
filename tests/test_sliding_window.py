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
