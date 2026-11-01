"""Tiny Redis-backed HTTP metrics so all API processes share one counter (used by the admin dashboard)."""
import redis
import structlog

from app.core.config import settings

log = structlog.get_logger()
_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)
KEY = "metrics:http"


def record_request(ms: float, is_error: bool) -> None:
    try:
        pipe = _redis.pipeline()
        pipe.hincrby(KEY, "count", 1)
        pipe.hincrbyfloat(KEY, "total_ms", ms)
        if is_error:
            pipe.hincrby(KEY, "errors", 1)
        pipe.execute()
    except redis.RedisError:
        pass  # metrics must never break a request


def read_http_metrics() -> dict:
    try:
        raw = _redis.hgetall(KEY)
    except redis.RedisError:
        return {"requests": 0, "errors": 0, "avg_ms": 0.0, "available": False}
    count = int(raw.get(b"count", 0))
    total = float(raw.get(b"total_ms", 0))
    return {"requests": count, "errors": int(raw.get(b"errors", 0)),
            "avg_ms": round(total / count, 1) if count else 0.0, "available": True}
