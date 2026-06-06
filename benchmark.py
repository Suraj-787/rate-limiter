import concurrent.futures
import os
import time

import redis
import requests

API_URL = os.environ.get('API_URL', 'http://127.0.0.1:8080')

# Reset
r = redis.Redis(host='localhost', port=6379)
r.flushdb()

# Set very high limit so nothing gets blocked during benchmark
requests.post(
    f'{API_URL}/config/bench_tenant',
    json={'algorithm': 'token_bucket', 'capacity': '999999', 'refill_rate': '999999'},
)

NUM_REQUESTS = 10000
MAX_WORKERS = 100


def req(_):
    return requests.post(
        f'{API_URL}/check',
        json={'tenant': 'bench_tenant', 'algorithm': 'token_bucket'},
    )


print(f'Sending {NUM_REQUESTS} requests with {MAX_WORKERS} workers...')
start = time.time()

with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
    results = list(ex.map(req, range(NUM_REQUESTS)))

elapsed = time.time() - start
rps = NUM_REQUESTS / elapsed

success = sum(1 for resp in results if resp.status_code == 200)

print(f"\n{'='*40}")
print(f'Total requests : {NUM_REQUESTS}')
print(f'Successful     : {success}')
print(f'Time elapsed   : {elapsed:.2f}s')
print(f'Throughput     : {rps:.0f} req/sec')
print(f"{'='*40}")
print(f'\nResume bullet metric: ~{int(rps / 1000)}k req/sec')
