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
