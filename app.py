import os
import socket

from flask import Flask, request, jsonify
import redis
from limiter.engine import RateLimiterEngine

app = Flask(__name__)
r = redis.Redis(
    host=os.environ.get('REDIS_HOST', 'localhost'),
    port=6379,
    decode_responses=False,
)
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


def _find_free_port(start=8080, attempts=20):
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                continue
    raise RuntimeError(f'No free port found in range {start}-{start + attempts - 1}')


if __name__ == '__main__':
    port = int(os.environ['PORT']) if 'PORT' in os.environ else _find_free_port()
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    print(f' * Running on http://127.0.0.1:{port}')
    app.run(host='0.0.0.0', port=port, debug=debug)
