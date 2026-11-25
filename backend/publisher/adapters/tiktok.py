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

from .. import config
from .base import AdapterError, BaseAdapter, ConnectedProfile, PublishContext, PublishResult, TokenBundle

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
PRIVACY_LABELS = {
    "PUBLIC_TO_EVERYONE": "Everyone",
    "MUTUAL_FOLLOW_FRIENDS": "Friends",
    "FOLLOWER_OF_CREATOR": "Followers",
    "SELF_ONLY": "Only me",
}
POSTING_BLOCKED = {
    "spam_risk_too_many_posts": "This TikTok account has reached today's post limit. Try again later.",
    "spam_risk_user_banned_from_posting": "This TikTok account is currently banned from posting.",
    "reached_active_user_cap": "This app has reached today's active-creator limit. Try again later.",
}
MUSIC_USAGE_URL = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
BRANDED_CONTENT_POLICY_URL = "https://www.tiktok.com/legal/page/global/bc-policy/en"
CONSENT_MUSIC = "By posting, you agree to TikTok's Music Usage Confirmation"
CONSENT_BRANDED = "By posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation"


def consent_text(brand_content: bool) -> str:
    return CONSENT_BRANDED if brand_content else CONSENT_MUSIC


def options_ready(options: dict | None) -> bool:
    return options_block_reason(options) is None


def options_block_reason(options: dict | None) -> str | None:
    """Why a TikTok job cannot be sent yet (UX-guideline checks). None = ready."""
    opts = options or {}
    privacy = str(opts.get("privacy_level") or "").upper()
    if privacy not in PRIVACY_LEVELS:
        return "Select a privacy level. TikTok requires a manual choice with no default."
    commercial = bool(opts.get("commercial_disclosure") or opts.get("brand_organic_toggle") or opts.get("brand_content_toggle"))
    brand_organic = bool(opts.get("brand_organic_toggle"))
    brand_content = bool(opts.get("brand_content_toggle"))
    if commercial and not (brand_organic or brand_content):
        return "You need to indicate if your content promotes yourself, a third party, or both."
    if brand_content and privacy == "SELF_ONLY":
        return "Branded content visibility cannot be set to private."
    if not opts.get("tiktok_consent"):
        return "Confirm that by posting, you agree to TikTok's Music Usage Confirmation."
    return None


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
    supports_revoke = True

    @property
    def pkce_style(self) -> str | None:  # type: ignore[override]
        # TikTok's PKCE uses a hex-encoded SHA-256 challenge (not base64url); TIKTOK_PKCE=false turns it off.
        return "hex" if config.tiktok_pkce() else None

    @property
    def uses_pkce(self) -> bool:
        return self.pkce_style is not None

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
        params = {
            "client_key": self.creds.client_id, "scope": _scopes(), "response_type": "code",
            "redirect_uri": self.redirect_uri, "state": state,
            "disable_auto_auth": "1",  # always show the consent page, so another account can be picked
        }
        if code_challenge and self.uses_pkce:
            params.update(code_challenge=code_challenge, code_challenge_method="S256")
        return AUTH_URL + "?" + urlencode(params)

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
        data = {"code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri}
        if code_verifier and self.uses_pkce:
            data["code_verifier"] = code_verifier
        token = self._token(data)
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

    def query_creator_info(self, token: TokenBundle) -> dict:
        """Latest creator_info, including posting-cap errors that must block the UI."""
        resp = self.request("POST", f"{API}/post/publish/creator_info/query/", "creator_info",
                            headers={"Authorization": f"Bearer {token.access_token}",
                                     "Content-Type": "application/json; charset=UTF-8"}, json={})
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = (body.get("error") or {}) if isinstance(body.get("error"), dict) else {}
        code = err.get("code")
        data = body.get("data") or {}
        if code in POSTING_BLOCKED:
            return {"can_post": False, "error_code": code, "error_message": POSTING_BLOCKED[code], "data": data}
        self._check(resp, "creator_info")
        return {"can_post": True, "error_code": None, "error_message": None, "data": data}

    def creator_info(self, token: TokenBundle) -> dict:
        result = self.query_creator_info(token)
        if not result["can_post"]:
            raise AdapterError(result["error_message"], code=result["error_code"])
        return result["data"]

    @staticmethod
    def choose_privacy(requested: str | None, options: list[str], audited: bool) -> tuple[str, str | None]:
        # Unaudited clients may only post SELF_ONLY. The compose UI still requires a manual
        # selection; this override is TikTok's unaudited-app restriction, not a default.
        if not audited:
            if options and "SELF_ONLY" not in options:
                raise AdapterError("TikTok app is unaudited and SELF_ONLY is not offered for this creator", code="privacy")
            return "SELF_ONLY", "TikTok app not audited yet: posted as private (SELF_ONLY)"
        wanted = str(requested or "").upper()
        if wanted not in PRIVACY_LEVELS:
            raise AdapterError("Select a privacy level. TikTok requires a manual choice with no default.",
                               code="privacy_required")
        if options and wanted not in options:
            raise AdapterError(f"TikTok privacy '{wanted}' is not available for this creator (allowed: {', '.join(options)})",
                               code="privacy_level_option_mismatch")
        return wanted, None

    def publish(self, ctx: PublishContext) -> PublishResult:
        blocked = options_block_reason(ctx.options)
        if blocked:
            raise AdapterError(blocked, code="tiktok_ux")
        info = self.creator_info(ctx.token)
        max_dur = info.get("max_video_post_duration_sec")
        if max_dur and ctx.duration_sec and ctx.duration_sec > float(max_dur):
            raise AdapterError(f"Clip is {ctx.duration_sec:.0f}s; this TikTok account allows {max_dur}s max")
        privacy, note = self.choose_privacy(ctx.options.get("privacy_level"), info.get("privacy_level_options") or [], _audited())
        # Interactions default off (disable_*=True) unless the user turned them on.
        # If TikTok reports the interaction disabled in the creator's settings, force it off.
        def _disabled(flag: str, creator_disabled: str) -> bool:
            if info.get(creator_disabled):
                return True
            if flag in (ctx.options or {}):
                return bool(ctx.options[flag])
            return True
        size = ctx.video_path.stat().st_size
        chunk_size, total = chunk_plan(size)
        caption = str(ctx.caption.get("title") or ctx.caption.get("caption") or "")[:2200]
        post_info = {
            "title": caption, "privacy_level": privacy,
            "disable_comment": _disabled("disable_comment", "comment_disabled"),
            "disable_duet": _disabled("disable_duet", "duet_disabled"),
            "disable_stitch": _disabled("disable_stitch", "stitch_disabled"),
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
