"""Developer-app credentials (client ID / key and client secret) entered from the UI.

Stored Fernet-encrypted in ``social_app_credentials`` with SOCIAL_TOKEN_KEY. Values in the
server .env always win. Secrets never leave the backend: the API only returns masked views.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from . import config, crypto

logger = logging.getLogger(__name__)

_CACHE: dict = {"at": 0.0, "data": None}
_LOCK = threading.Lock()
CACHE_TTL = 15.0
_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{4,256}$")


def _session():
    from backend.core.database import session_scope
    return session_scope()


def ensure_table() -> None:
    from backend.core.database import engine
    from backend.models.social import SocialAppCredential
    SocialAppCredential.__table__.create(bind=engine, checkfirst=True)


def invalidate() -> None:
    with _LOCK:
        _CACHE["at"], _CACHE["data"] = 0.0, None


def load_all() -> dict[str, tuple[str, str]]:
    """{platform: (client_id, client_secret)} from the DB, decrypted; cached for a few seconds."""
    with _LOCK:
        if _CACHE["data"] is not None and time.monotonic() - _CACHE["at"] < CACHE_TTL:
            return dict(_CACHE["data"])
    data: dict[str, tuple[str, str]] = {}
    try:
        from backend.models.social import SocialAppCredential
        ensure_table()
        with _session() as db:
            for row in db.query(SocialAppCredential).all():
                try:
                    cid = crypto.decrypt_json(row.client_id_encrypted).get("v", "")
                    sec = crypto.decrypt_json(row.client_secret_encrypted).get("v", "")
                except crypto.CryptoNotConfigured:
                    logger.warning("stored %s app credentials cannot be decrypted", row.platform)
                    continue
                data[row.platform] = (cid, sec)
    except Exception as exc:  # noqa: BLE001 - a DB hiccup must not break .env-configured platforms
        logger.warning("could not load app credentials: %s", type(exc).__name__)
        return {}
    with _LOCK:
        _CACHE["at"], _CACHE["data"] = time.monotonic(), dict(data)
    return data


def stored(platform: str) -> tuple[str, str]:
    return load_all().get(platform, ("", ""))


def save(platform: str, client_id: str, client_secret: Optional[str]) -> None:
    """Create or update. An empty/None secret keeps the stored one (so the UI never needs it back)."""
    from backend.models.social import SocialAppCredential
    if platform not in config.PLATFORMS:
        raise ValueError("unknown platform")
    if config.env_credentials(platform).configured:
        raise ValueError(f"{platform} is configured in the server .env, which takes precedence; edit it there")
    if not config.token_key():
        raise ValueError("SOCIAL_TOKEN_KEY is not set on the server, so credentials cannot be stored encrypted")
    client_id = (client_id or "").strip()
    secret = (client_secret or "").strip()
    if not _ID_RE.match(client_id):
        raise ValueError("client ID / key must be 4-256 characters of letters, digits, '.', '_' or '-'")
    if secret and (len(secret) > 512 or any(c.isspace() for c in secret)):
        raise ValueError("client secret must be at most 512 characters with no spaces")
    ensure_table()
    with _session() as db:
        row = db.get(SocialAppCredential, platform)
        if row is None:
            if not secret:
                raise ValueError("client secret is required")
            row = SocialAppCredential(platform=platform)
            db.add(row)
        row.client_id_encrypted = crypto.encrypt_json({"v": client_id})
        if secret:
            row.client_secret_encrypted = crypto.encrypt_json({"v": secret})
        row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    invalidate()


def delete(platform: str) -> bool:
    from backend.models.social import SocialAppCredential
    ensure_table()
    with _session() as db:
        row = db.get(SocialAppCredential, platform)
        if row is None:
            return False
        db.delete(row)
        db.commit()
    invalidate()
    return True


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "•" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"
