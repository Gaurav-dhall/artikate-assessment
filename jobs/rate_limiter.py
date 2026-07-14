import time
import uuid
import redis
from django.conf import settings

redis_client = redis.Redis.from_url(settings.CELERY_BROKER_URL)

RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window)
    return 1
else
    return 0
end
"""

_rate_limit_script = redis_client.register_script(RATE_LIMIT_SCRIPT)

DEFAULT_KEY = "email_rate_limiter"
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_MAX_REQUESTS = 200


def is_allowed(key=DEFAULT_KEY, window=DEFAULT_WINDOW_SECONDS, limit=DEFAULT_MAX_REQUESTS):
    """
    Atomically checks and reserves a slot in the sliding window.
    key/window/limit are parameterised so tests can exercise this exact
    production logic against an isolated Redis key, without touching or
    resetting the real production counter.
    """
    now = time.time()
    member = f"{now}-{uuid.uuid4()}"
    try:
        result = _rate_limit_script(keys=[key], args=[now, window, limit, member])
        return bool(result)
    except redis.exceptions.RedisError:
        return False