"""Token encryption (Fernet) and short-lived HMAC signatures.

SOCIAL_TOKEN_KEY is a urlsafe-base64 32-byte Fernet key kept only in the server .env.
OAuth tokens are stored encrypted at rest; the media-URL signing key is derived from it
so there is one secret to rotate.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from . import config


class CryptoNotConfigured(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = config.token_key()
    if not key:
        raise CryptoNotConfigured("SOCIAL_TOKEN_KEY is not set")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CryptoNotConfigured("SOCIAL_TOKEN_KEY is not a valid Fernet key") from exc


def encrypt_json(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()


def decrypt_json(blob: str | None) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        return json.loads(_fernet().decrypt(blob.encode()).decode())
    except InvalidToken as exc:
        raise CryptoNotConfigured("stored token cannot be decrypted with the current SOCIAL_TOKEN_KEY") from exc


def _signing_key() -> bytes:
    key = config.token_key()
    if not key:
        raise CryptoNotConfigured("SOCIAL_TOKEN_KEY is not set")
    return hashlib.sha256(b"autoclip-social-media-url|" + key.encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(payload: str, expires_at: int) -> str:
    mac = hmac.new(_signing_key(), f"{payload}|{expires_at}".encode(), hashlib.sha256).digest()
    return _b64(mac)


def verify(payload: str, expires_at: int, signature: str, now: float | None = None) -> bool:
    if (now if now is not None else time.time()) > expires_at:
        return False
    try:
        expected = sign(payload, expires_at)
    except CryptoNotConfigured:
        return False
    return hmac.compare_digest(expected, signature or "")


def generate_key() -> str:
    return Fernet.generate_key().decode()
