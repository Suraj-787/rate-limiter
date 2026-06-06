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
