from __future__ import annotations

from .base import AdapterError, PublishContext, PublishResult, TokenBundle
from .instagram import InstagramAdapter
from .tiktok import TikTokAdapter
from .youtube import YouTubeAdapter

_ADAPTERS = {"tiktok": TikTokAdapter, "instagram": InstagramAdapter, "youtube": YouTubeAdapter}


def get_adapter(platform: str, client=None):
    try:
        return _ADAPTERS[platform](client=client)
    except KeyError as exc:
        raise ValueError(f"unknown platform: {platform}") from exc


__all__ = ["AdapterError", "PublishContext", "PublishResult", "TokenBundle", "get_adapter"]
