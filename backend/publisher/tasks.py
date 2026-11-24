"""Celery tasks: celery-beat calls publisher_tick every minute; the API can kick it immediately."""
from __future__ import annotations

import logging
import os

from backend.core.celery_app import celery_app

logger = logging.getLogger(__name__)
LOCK_KEY = "autoclip:social-publisher:tick"


def _lock():
    try:
        import redis
        client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), socket_timeout=5)
        token = os.urandom(8).hex()
        if client.set(LOCK_KEY, token, nx=True, ex=1500):
            return client, token
        return None, None
    except Exception:  # noqa: BLE001 - without Redis, DB row locks still prevent double posting
        return False, None


@celery_app.task(name="backend.publisher.tasks.publisher_tick", ignore_result=True, soft_time_limit=1500, time_limit=1800)
def publisher_tick():
    client, token = _lock()
    if client is None:
        return {"skipped": "another tick is running"}
    try:
        from . import service
        result = service.tick()
        if result.get("processed") or result.get("auto_created"):
            logger.info("social publisher tick: %s", result)
        return result
    finally:
        if client:
            try:
                if (client.get(LOCK_KEY) or b"").decode() == token:
                    client.delete(LOCK_KEY)
            except Exception:  # noqa: BLE001
                pass


@celery_app.task(name="backend.publisher.tasks.refresh_tokens", ignore_result=True)
def refresh_tokens():
    from . import service
    return {"refreshed": service.refresh_expiring_tokens()}
