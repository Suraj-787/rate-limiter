import os
import threading

import redis
import requests

API_URL = os.environ.get('API_URL', 'http://127.0.0.1:8080')

# Reset state
r = redis.Redis(host='localhost', port=6379)
r.flushdb()

# Set limit to 100 for this tenant
requests.post(
    f'{API_URL}/config/concurrent_test',
    json={'algorithm': 'sliding_window', 'limit': '100', 'window': '60'},
)

allowed_count = 0
blocked_count = 0
lock = threading.Lock()


def send_request():
    global allowed_count, blocked_count
    resp = requests.post(f'{API_URL}/check', json={'tenant': 'concurrent_test'})
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

print(f'Allowed : {allowed_count}')
print(f'Blocked : {blocked_count}')
print(f'Total   : {allowed_count + blocked_count}')

# This assertion is the key — must never exceed 100
assert allowed_count <= 100, f'RACE CONDITION: {allowed_count} allowed, expected <= 100'
print('✅ No race condition detected')
