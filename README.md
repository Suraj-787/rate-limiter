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
```bash
docker-compose up --build
curl -X POST http://localhost:8080/check \
  -H "Content-Type: application/json" \
  -d '{"tenant": "user_123", "algorithm": "sliding_window"}'
```

## Local Development
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker-compose up -d redis
PYTHONPATH=. python app.py
```

## Performance
~1,000 req/sec on local Docker deployment (Flask dev server; use a production WSGI server for higher throughput).

## Key Design Decisions
- Redis pipelines for atomic read-modify-write (prevents race conditions)
- Per-tenant config stored in Redis (no restarts needed for config changes)
- Algorithm selectable per-request or set as tenant default
