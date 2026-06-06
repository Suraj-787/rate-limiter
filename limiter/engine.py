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
