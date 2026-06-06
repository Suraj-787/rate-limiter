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
