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


ENV_NAMES = {
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
    "instagram": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"),
    "instagram_facebook": ("META_APP_ID", "META_APP_SECRET"),
    "youtube": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
}

CONSOLES = {
    "tiktok": {"console_url": "https://developers.tiktok.com/apps/",
               "docs_url": "https://developers.tiktok.com/doc/content-posting-api-get-started",
               "id_label": "Client key", "secret_label": "Client secret"},
    "instagram": {"console_url": "https://developers.facebook.com/apps/",
                  "docs_url": "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login",
                  "id_label": "Instagram app ID", "secret_label": "Instagram app secret"},
    "youtube": {"console_url": "https://console.cloud.google.com/apis/credentials",
                "docs_url": "https://developers.google.com/youtube/v3/guides/uploading_a_video",
                "id_label": "OAuth client ID", "secret_label": "OAuth client secret"},
}
SETUP_GUIDE_URL = "https://github.com/MuhamedZanabal/autoclip/blob/zedosai-main/docs/SOCIAL_PUBLISHING_SETUP.md"


def env_names(platform: str) -> tuple[str, str]:
    if platform == "instagram" and instagram_login_mode() == "facebook":
        return ENV_NAMES["instagram_facebook"]
    if platform not in ENV_NAMES:
        raise ValueError(f"unknown platform: {platform}")
    return ENV_NAMES[platform]


def env_credentials(platform: str) -> AppCredentials:
    id_name, secret_name = env_names(platform)
    return AppCredentials(_env(id_name), _env(secret_name))


def credentials_source(platform: str) -> str | None:
    """'env' when the server .env provides the pair (it always wins), 'db' when entered in the UI."""
    env = env_credentials(platform)
    if env.client_id or env.client_secret:
        return "env" if env.configured else None
    from . import apps
    cid, sec = apps.stored(platform)
    return "db" if cid and sec else None


def credentials(platform: str) -> AppCredentials:
    env = env_credentials(platform)
    if env.client_id or env.client_secret:  # any .env value takes precedence over the UI store
        return env
    from . import apps
    cid, sec = apps.stored(platform)
    return AppCredentials(cid, sec)


def tiktok_pkce() -> bool:
    return _env("TIKTOK_PKCE", "true").lower() in ("1", "true", "yes")


def token_key() -> str:
    return _env("SOCIAL_TOKEN_KEY")


def media_url_ttl_sec() -> int:
    try:
        return max(300, int(_env("SOCIAL_MEDIA_URL_TTL", "7200")))
    except ValueError:
        return 7200


def missing_env(platform: str) -> list[str]:
    missing = []
    if not credentials(platform).configured:
        missing.extend(n for n in env_names(platform) if not _env(n))
    if not token_key():
        missing.append("SOCIAL_TOKEN_KEY")
    if not _env("AUTOCLIP_PUBLIC_URL"):
        missing.append("AUTOCLIP_PUBLIC_URL")
    return missing


def platform_status(platform: str) -> dict:
    from . import apps
    missing = missing_env(platform)
    creds = credentials(platform)
    source = credentials_source(platform)
    env = env_credentials(platform)
    return {
        "platform": platform,
        "configured": not missing,
        "missing_env": missing,
        "source": source,
        "env_locked": bool(env.client_id or env.client_secret),
        "client_id_masked": apps.mask(creds.client_id),
        "secret_set": bool(creds.client_secret),
        "env_names": list(env_names(platform)),
        "redirect_uri": redirect_uri(platform),
        "pkce": platform == "youtube" or (platform == "tiktok" and tiktok_pkce()),
        "setup_guide_url": SETUP_GUIDE_URL,
        **CONSOLES[platform],
        **({"login_mode": instagram_login_mode()} if platform == "instagram" else {}),
    }
