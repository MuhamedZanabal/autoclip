"""Environment-driven configuration for the social publisher.

All secrets come from the process environment (the server's chmod-600 .env passed
through docker compose). Nothing here is ever returned to the browser except the
boolean "configured" flags and the public redirect URIs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

PLATFORMS = ("tiktok", "instagram", "youtube")
# A publish *target* is what a job posts as. YouTube accounts can post Shorts or long-form.
TARGETS = ("tiktok", "instagram", "youtube_shorts", "youtube")
TARGET_PLATFORM = {"tiktok": "tiktok", "instagram": "instagram", "youtube_shorts": "youtube", "youtube": "youtube"}
VERTICAL_TARGETS = frozenset({"tiktok", "instagram", "youtube_shorts"})


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def public_base_url() -> str:
    """Public URL of the AutoClip UI, e.g. https://zedosai.com/autoclip (no trailing slash)."""
    return _env("AUTOCLIP_PUBLIC_URL", "http://localhost:3000").rstrip("/")


def public_api_base() -> str:
    return public_base_url() + "/api/v1"


def redirect_uri(platform: str) -> str:
    override = _env(f"{platform.upper()}_REDIRECT_URI")
    return override or f"{public_api_base()}/social/oauth/{platform}/callback"


def ui_url(fragment: str = "/publishing") -> str:
    return f"{public_base_url()}/#{fragment}"


def meta_graph_version() -> str:
    return _env("META_GRAPH_VERSION", "v26.0")


def instagram_login_mode() -> str:
    """'instagram' = Instagram API with Instagram Login (no Facebook Page needed);
    'facebook' = Instagram API with Facebook Login (IG professional account linked to a Page)."""
    mode = _env("INSTAGRAM_LOGIN_MODE", "instagram").lower()
    return mode if mode in ("instagram", "facebook") else "instagram"


@dataclass(frozen=True)
class AppCredentials:
    client_id: str
    client_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def credentials(platform: str) -> AppCredentials:
    if platform == "tiktok":
        return AppCredentials(_env("TIKTOK_CLIENT_KEY"), _env("TIKTOK_CLIENT_SECRET"))
    if platform == "instagram":
        if instagram_login_mode() == "facebook":
            return AppCredentials(_env("META_APP_ID"), _env("META_APP_SECRET"))
        return AppCredentials(_env("INSTAGRAM_APP_ID"), _env("INSTAGRAM_APP_SECRET"))
    if platform == "youtube":
        return AppCredentials(_env("GOOGLE_CLIENT_ID"), _env("GOOGLE_CLIENT_SECRET"))
    raise ValueError(f"unknown platform: {platform}")


def token_key() -> str:
    return _env("SOCIAL_TOKEN_KEY")


def media_url_ttl_sec() -> int:
    try:
        return max(300, int(_env("SOCIAL_MEDIA_URL_TTL", "7200")))
    except ValueError:
        return 7200


def missing_env(platform: str) -> list[str]:
    names = {
        "tiktok": ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"],
        "instagram": (["META_APP_ID", "META_APP_SECRET"] if instagram_login_mode() == "facebook"
                      else ["INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"]),
        "youtube": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
    }[platform]
    missing = [n for n in names if not _env(n)]
    if not token_key():
        missing.append("SOCIAL_TOKEN_KEY")
    if not _env("AUTOCLIP_PUBLIC_URL"):
        missing.append("AUTOCLIP_PUBLIC_URL")
    return missing


def platform_status(platform: str) -> dict:
    missing = missing_env(platform)
    return {
        "platform": platform,
        "configured": not missing,
        "missing_env": missing,
        "redirect_uri": redirect_uri(platform),
        **({"login_mode": instagram_login_mode()} if platform == "instagram" else {}),
    }
