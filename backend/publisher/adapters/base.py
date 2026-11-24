"""Common adapter contract. Adapters speak only to official, documented platform APIs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from .. import config

USER_AGENT = "AutoClip-Publisher/1.0 (+https://github.com/MuhamedZanabal/autoclip)"


class AdapterError(Exception):
    """retryable: transient (5xx, rate limit, network). needs_reconnect: token revoked/invalid."""

    def __init__(self, message: str, *, retryable: bool = False, needs_reconnect: bool = False, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.needs_reconnect = needs_reconnect
        self.code = code


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None  # epoch seconds
    refresh_expires_at: Optional[float] = None
    scopes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_at": self.expires_at, "refresh_expires_at": self.refresh_expires_at,
                "scopes": self.scopes, "extra": self.extra}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenBundle":
        return cls(access_token=data.get("access_token", ""), refresh_token=data.get("refresh_token"),
                   expires_at=data.get("expires_at"), refresh_expires_at=data.get("refresh_expires_at"),
                   scopes=list(data.get("scopes") or []), extra=dict(data.get("extra") or {}))

    def expires_within(self, seconds: float) -> bool:
        return bool(self.expires_at) and (self.expires_at - time.time()) < seconds


@dataclass
class ConnectedProfile:
    external_id: str
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishContext:
    token: TokenBundle
    account_external_id: str
    account_username: str | None
    target: str
    video_path: Path
    caption: dict[str, Any]
    options: dict[str, Any]
    duration_sec: float | None = None
    public_video_url: str | None = None  # signed, expiring URL (Instagram pulls from it)
    remote_id: str | None = None
    remote_container_id: str | None = None
    account_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    status: str  # "published" | "processing"
    remote_id: str | None = None
    remote_container_id: str | None = None
    remote_url: str | None = None
    note: str | None = None


class BaseAdapter:
    platform = ""

    #: PKCE challenge encoding: "b64" (RFC 7636 base64url S256), "hex" (TikTok's hex SHA-256 S256) or None.
    pkce_style: str | None = None
    #: Whether disconnect can revoke the grant through the platform API.
    supports_revoke: bool = False

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    @property
    def uses_pkce(self) -> bool:
        return self.pkce_style is not None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0),
                                        headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        return self._client

    @property
    def creds(self) -> config.AppCredentials:
        return config.credentials(self.platform)

    @property
    def redirect_uri(self) -> str:
        return config.redirect_uri(self.platform)

    def require_configured(self) -> None:
        if not self.creds.configured:
            raise AdapterError(f"{self.platform} app credentials are not configured", code="not_configured")

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def raise_for_http(resp: httpx.Response, what: str) -> None:
        if resp.status_code < 400:
            return
        body = resp.text[:400]
        retryable = resp.status_code in (408, 429) or resp.status_code >= 500
        needs_reconnect = resp.status_code == 401
        raise AdapterError(f"{what} failed: HTTP {resp.status_code}: {body}",
                           retryable=retryable, needs_reconnect=needs_reconnect, code=str(resp.status_code))

    def request(self, method: str, url: str, what: str, **kw) -> httpx.Response:
        try:
            resp = self.client.request(method, url, **kw)
        except httpx.TransportError as exc:
            raise AdapterError(f"{what} failed: network error {type(exc).__name__}", retryable=True) from exc
        return resp

    # --- contract --------------------------------------------------------
    def authorize_url(self, state: str, code_challenge: str | None) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def exchange_code(self, code: str, code_verifier: str | None) -> tuple[TokenBundle, list[ConnectedProfile]]:
        raise NotImplementedError

    def refresh(self, token: TokenBundle) -> TokenBundle:
        raise NotImplementedError

    def revoke(self, token: TokenBundle) -> None:
        return None

    def publish(self, ctx: PublishContext) -> PublishResult:
        raise NotImplementedError

    def poll(self, ctx: PublishContext) -> PublishResult:
        raise NotImplementedError
