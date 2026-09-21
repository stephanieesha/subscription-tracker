"""
Safety limits for a deployment that anyone with the link can reach. Both are OFF unless their
environment variable is set, so local runs and tests are never throttled:

  WRITE_LIMIT_PER_MINUTE  max changes (POST/PUT/PATCH/DELETE) per visitor per minute
  MAX_SUBSCRIPTIONS       max subscriptions the shared list can hold
"""

import os
import time
from collections import defaultdict, deque


def _int_setting(name: str) -> int:
    try:
        return int(os.environ.get(name, "0"))
    except ValueError:
        return 0


_recent_requests = defaultdict(deque)


def client_ip(request) -> str:
    # Behind Render's proxy the last X-Forwarded-For entry is the address that reached the
    # proxy; earlier entries can be forged by the visitor.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def too_many_requests(ip: str, now: float = None) -> bool:
    limit = _int_setting("WRITE_LIMIT_PER_MINUTE")
    if limit <= 0:
        return False
    now = time.time() if now is None else now
    window = _recent_requests[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        return True
    window.append(now)
    return False


def max_subscriptions() -> int:
    return _int_setting("MAX_SUBSCRIPTIONS")
