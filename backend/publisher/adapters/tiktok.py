"""TikTok Content Posting API — Direct Post (FILE_UPLOAD).

Docs: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
Unaudited clients may only post SELF_ONLY (private); set TIKTOK_APP_AUDITED=true after the audit.
FILE_UPLOAD is used so no domain/URL-prefix verification is needed (PULL_FROM_URL requires it).
"""
from __future__ import annotations

import math
import os
import time
from urllib.parse import urlencode

from .base import AdapterError, BaseAdapter, ConnectedProfile, PublishContext, PublishResult, TokenBundle

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")


def chunk_plan(size: int, chunk: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok media-transfer rules.

    Files up to 64 MB go up in a single PUT. Larger files use 5–64 MB chunks and the final chunk
    absorbs the remainder (TikTok allows it up to 128 MB), so total = floor(size / chunk_size).
    """
    if size <= 0:
        raise ValueError("empty video")
    if size <= MAX_CHUNK:
        return size, 1  # one PUT; also covers the "< 5 MB must be a single chunk" rule
    chunk = max(MIN_CHUNK, min(MAX_CHUNK, chunk))
    return chunk, max(1, math.floor(size / chunk))


def _audited() -> bool:
    return os.getenv("TIKTOK_APP_AUDITED", "false").strip().lower() in ("1", "true", "yes")


def _scopes() -> str:
    return os.getenv("TIKTOK_SCOPES", "user.info.basic,video.publish").strip()


class TikTokAdapter(BaseAdapter):
    platform = "tiktok"

    def _check(self, resp, what: str) -> dict:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = (body.get("error") or {}) if isinstance(body.get("error"), dict) else {}
        code = err.get("code")
        if resp.status_code >= 400 or (code and code != "ok"):
            msg = err.get("message") or body.get("error_description") or resp.text[:300]
            reconnect = resp.status_code == 401 or code in ("access_token_invalid", "scope_not_authorized")
            retryable = resp.status_code in (429,) or resp.status_code >= 500 or code == "rate_limit_exceeded"
            raise AdapterError(f"TikTok {what}: {code or resp.status_code}: {msg}", retryable=retryable,
                               needs_reconnect=reconnect, code=code or str(resp.status_code))
        return body

    def authorize_url(self, state: str, code_challenge: str | None) -> str:
        self.require_configured()
        return AUTH_URL + "?" + urlencode({
            "client_key": self.creds.client_id, "scope": _scopes(), "response_type": "code",
            "redirect_uri": self.redirect_uri, "state": state,
        })

    def _token(self, data: dict) -> TokenBundle:
        resp = self.request("POST", f"{API}/oauth/token/", "token", data={
            "client_key": self.creds.client_id, "client_secret": self.creds.client_secret, **data},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or body.get("error") or not body.get("access_token"):
            raise AdapterError(f"TikTok token exchange failed: {body.get('error') or resp.status_code}: "
                               f"{body.get('error_description', '')}", needs_reconnect=True,
                               retryable=resp.status_code >= 500)
        now = time.time()
        return TokenBundle(access_token=body["access_token"], refresh_token=body.get("refresh_token"),
                           expires_at=now + float(body.get("expires_in") or 86400),
                           refresh_expires_at=now + float(body.get("refresh_expires_in") or 0) if body.get("refresh_expires_in") else None,
                           scopes=[s for s in str(body.get("scope") or "").split(",") if s],
                           extra={"open_id": body.get("open_id")})

    def exchange_code(self, code, code_verifier):
        self.require_configured()
        token = self._token({"code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri})
        fields = "open_id,union_id,avatar_url,display_name"
        if "user.info.profile" in token.scopes:
            fields += ",username"
        resp = self.request("GET", f"{API}/user/info/", "user info", params={"fields": fields},
                            headers={"Authorization": f"Bearer {token.access_token}"})
        user = (self._check(resp, "user info").get("data") or {}).get("user") or {}
        open_id = user.get("open_id") or token.extra.get("open_id")
        if not open_id:
            raise AdapterError("TikTok did not return an open_id")
        return token, [ConnectedProfile(external_id=open_id, display_name=user.get("display_name"),
                                        username=user.get("username"), avatar_url=user.get("avatar_url"))]

    def refresh(self, token):
        if not token.refresh_token:
            raise AdapterError("TikTok token has no refresh_token", needs_reconnect=True)
        fresh = self._token({"grant_type": "refresh_token", "refresh_token": token.refresh_token})
        fresh.extra = {**token.extra, **{k: v for k, v in fresh.extra.items() if v}}
        return fresh

    def revoke(self, token):
        if not self.creds.configured:
            return
        self.request("POST", f"{API}/oauth/revoke/", "revoke", data={
            "client_key": self.creds.client_id, "client_secret": self.creds.client_secret,
            "token": token.access_token})

    def creator_info(self, token: TokenBundle) -> dict:
        resp = self.request("POST", f"{API}/post/publish/creator_info/query/", "creator_info",
                            headers={"Authorization": f"Bearer {token.access_token}",
                                     "Content-Type": "application/json; charset=UTF-8"}, json={})
        return self._check(resp, "creator_info").get("data") or {}

    @staticmethod
    def choose_privacy(requested: str | None, options: list[str], audited: bool) -> tuple[str, str | None]:
        if not audited:
            if options and "SELF_ONLY" not in options:
                raise AdapterError("TikTok app is unaudited and SELF_ONLY is not offered for this creator", code="privacy")
            return "SELF_ONLY", "TikTok app not audited yet: posted as private (SELF_ONLY)"
        wanted = (requested or "PUBLIC_TO_EVERYONE").upper()
        if not options or wanted in options:
            return wanted, None
        raise AdapterError(f"TikTok privacy '{wanted}' is not available for this creator (allowed: {', '.join(options)})",
                           code="privacy_level_option_mismatch")

    def publish(self, ctx: PublishContext) -> PublishResult:
        info = self.creator_info(ctx.token)
        max_dur = info.get("max_video_post_duration_sec")
        if max_dur and ctx.duration_sec and ctx.duration_sec > float(max_dur):
            raise AdapterError(f"Clip is {ctx.duration_sec:.0f}s; this TikTok account allows {max_dur}s max")
        privacy, note = self.choose_privacy(ctx.options.get("privacy_level"), info.get("privacy_level_options") or [], _audited())
        size = ctx.video_path.stat().st_size
        chunk_size, total = chunk_plan(size)
        caption = str(ctx.caption.get("caption") or ctx.caption.get("title") or "")[:2200]
        post_info = {
            "title": caption, "privacy_level": privacy,
            "disable_comment": bool(ctx.options.get("disable_comment", False) or info.get("comment_disabled")),
            "disable_duet": bool(ctx.options.get("disable_duet", False) or info.get("duet_disabled")),
            "disable_stitch": bool(ctx.options.get("disable_stitch", False) or info.get("stitch_disabled")),
            "video_cover_timestamp_ms": int(ctx.options.get("cover_timestamp_ms", 1000)),
            "brand_content_toggle": bool(ctx.options.get("brand_content_toggle", False)),
            "brand_organic_toggle": bool(ctx.options.get("brand_organic_toggle", False)),
            "is_aigc": bool(ctx.options.get("is_aigc", False)),
        }
        resp = self.request("POST", f"{API}/post/publish/video/init/", "video init",
                            headers={"Authorization": f"Bearer {ctx.token.access_token}",
                                     "Content-Type": "application/json; charset=UTF-8"},
                            json={"post_info": post_info, "source_info": {
                                "source": "FILE_UPLOAD", "video_size": size,
                                "chunk_size": chunk_size, "total_chunk_count": total}})
        data = self._check(resp, "video init").get("data") or {}
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not publish_id or not upload_url:
            raise AdapterError("TikTok init returned no publish_id/upload_url", retryable=True)
        with open(ctx.video_path, "rb") as fh:
            for index in range(total):
                start = index * chunk_size
                end = size - 1 if index == total - 1 else start + chunk_size - 1
                fh.seek(start)
                body = fh.read(end - start + 1)
                up = self.request("PUT", upload_url, "chunk upload", content=body, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                if up.status_code not in (200, 201, 206):
                    self.raise_for_http(up, f"TikTok chunk {index + 1}/{total}")
        return PublishResult(status="processing", remote_id=publish_id, note=note)

    def poll(self, ctx: PublishContext) -> PublishResult:
        resp = self.request("POST", f"{API}/post/publish/status/fetch/", "status",
                            headers={"Authorization": f"Bearer {ctx.token.access_token}",
                                     "Content-Type": "application/json; charset=UTF-8"},
                            json={"publish_id": ctx.remote_id})
        data = self._check(resp, "status").get("data") or {}
        status = data.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            post_id = str(ids[0]) if ids else None
            url = None
            if post_id:
                user = ctx.account_username
                url = f"https://www.tiktok.com/@{user}/video/{post_id}" if user else f"https://www.tiktok.com/video/{post_id}"
            return PublishResult(status="published", remote_id=ctx.remote_id, remote_url=url,
                                 note=None if post_id else "Published privately (no public post id)")
        if status == "FAILED":
            raise AdapterError(f"TikTok processing failed: {data.get('fail_reason') or 'unknown'}", code="remote_failed",
                               retryable=data.get("fail_reason") in ("internal", "file_format_check_failed_retry"))
        return PublishResult(status="processing", remote_id=ctx.remote_id)
