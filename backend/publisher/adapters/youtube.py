"""YouTube Data API v3 resumable upload (videos.insert) for Shorts and long-form.

Shorts: vertical (9:16) video, <= 60 s here (YouTube now allows up to 3 min), with #Shorts in
title/description. Google reversed the July-2020 "unverified projects upload private-only" rule
(YouTube Data API revision history, 2026): public/unlisted uploads work within the default quota
of 100 videos.insert calls/day. What still applies to an unverified OAuth app is the consent side:
while the consent screen is in "Testing", only listed test users can connect and their refresh
tokens expire after 7 days. Set GOOGLE_APP_VERIFIED=true once the app is published/verified.
"""
from __future__ import annotations

import time
from urllib.parse import urlencode

from .base import AdapterError, BaseAdapter, ConnectedProfile, PublishContext, PublishResult, TokenBundle

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
CHUNK = 8 * 256 * 1024 * 4  # 8 MiB, a multiple of 256 KiB as the resumable protocol requires


class YouTubeAdapter(BaseAdapter):
    platform = "youtube"

    def authorize_url(self, state, code_challenge):
        self.require_configured()
        params = {"client_id": self.creds.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
                  "scope": SCOPES, "access_type": "offline", "prompt": "consent select_account",
                  "include_granted_scopes": "true", "state": state}
        if code_challenge:
            params.update(code_challenge=code_challenge, code_challenge_method="S256")
        return AUTH_URL + "?" + urlencode(params)

    def _token(self, data: dict, keep_refresh: str | None = None) -> TokenBundle:
        resp = self.request("POST", TOKEN_URL, "token", data={
            "client_id": self.creds.client_id, "client_secret": self.creds.client_secret, **data})
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or not body.get("access_token"):
            err = body.get("error")
            raise AdapterError(f"Google token request failed: {err or resp.status_code}: {body.get('error_description', '')}",
                               needs_reconnect=err in ("invalid_grant", "unauthorized_client") or resp.status_code in (400, 401),
                               retryable=resp.status_code >= 500)
        return TokenBundle(access_token=body["access_token"], refresh_token=body.get("refresh_token") or keep_refresh,
                           expires_at=time.time() + float(body.get("expires_in") or 3600),
                           scopes=str(body.get("scope") or SCOPES).split())

    def exchange_code(self, code, code_verifier):
        self.require_configured()
        data = {"code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri}
        if code_verifier:
            data["code_verifier"] = code_verifier
        token = self._token(data)
        if not token.refresh_token:
            raise AdapterError("Google returned no refresh token; remove the app at myaccount.google.com/permissions and reconnect")
        resp = self.request("GET", f"{API}/channels", "channels", params={"part": "snippet", "mine": "true"},
                            headers={"Authorization": f"Bearer {token.access_token}"})
        self.raise_for_http(resp, "YouTube channels.list")
        items = resp.json().get("items") or []
        if not items:
            raise AdapterError("This Google account has no YouTube channel; create one first")
        profiles = []
        for ch in items:
            sn = ch.get("snippet") or {}
            thumb = ((sn.get("thumbnails") or {}).get("default") or {}).get("url")
            profiles.append(ConnectedProfile(external_id=ch["id"], display_name=sn.get("title"),
                                             username=sn.get("customUrl"), avatar_url=thumb))
        return token, profiles

    def refresh(self, token):
        if not token.refresh_token:
            raise AdapterError("YouTube token has no refresh_token", needs_reconnect=True)
        fresh = self._token({"grant_type": "refresh_token", "refresh_token": token.refresh_token}, keep_refresh=token.refresh_token)
        fresh.extra = token.extra
        return fresh

    def revoke(self, token):
        self.request("POST", "https://oauth2.googleapis.com/revoke", "revoke",
                     params={"token": token.refresh_token or token.access_token})

    @staticmethod
    def build_resource(ctx: PublishContext) -> dict:
        cap = ctx.caption
        privacy = str(ctx.options.get("privacy_status") or "public").lower()
        if privacy not in ("public", "unlisted", "private"):
            privacy = "public"
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(ctx.options.get("made_for_kids", False)),
                  "embeddable": True}
        if ctx.options.get("publish_at"):
            status.update(privacyStatus="private", publishAt=ctx.options["publish_at"])
        if ctx.options.get("contains_synthetic_media") is not None:
            status["containsSyntheticMedia"] = bool(ctx.options["contains_synthetic_media"])
        snippet = {"title": str(cap.get("title") or "Untitled")[:100],
                   "description": str(cap.get("description") or ""),
                   "tags": list(cap.get("tags") or []),
                   "categoryId": str(ctx.options.get("category_id") or cap.get("category_id") or "22")}
        lang = ctx.options.get("language") or cap.get("language")
        if lang:
            snippet["defaultLanguage"] = lang
            snippet["defaultAudioLanguage"] = lang
        return {"snippet": snippet, "status": status}

    def publish(self, ctx: PublishContext) -> PublishResult:
        size = ctx.video_path.stat().st_size
        headers = {"Authorization": f"Bearer {ctx.token.access_token}", "Content-Type": "application/json; charset=UTF-8",
                   "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"}
        resp = self.request("POST", UPLOAD + "?" + urlencode({"uploadType": "resumable", "part": "snippet,status"}),
                            "start upload", headers=headers, json=self.build_resource(ctx))
        self.raise_for_http(resp, "YouTube videos.insert (start)")
        session = resp.headers.get("location")
        if not session:
            raise AdapterError("YouTube did not return a resumable session URL", retryable=True)
        offset, video = 0, None
        with open(ctx.video_path, "rb") as fh:
            while offset < size:
                fh.seek(offset)
                body = fh.read(CHUNK)
                end = offset + len(body) - 1
                up = self.request("PUT", session, "upload chunk", content=body, headers={
                    "Authorization": f"Bearer {ctx.token.access_token}", "Content-Type": "video/mp4",
                    "Content-Length": str(len(body)), "Content-Range": f"bytes {offset}-{end}/{size}"})
                if up.status_code == 308:
                    rng = up.headers.get("range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    continue
                self.raise_for_http(up, "YouTube upload")
                video = up.json()
                break
        if not video or not video.get("id"):
            raise AdapterError("YouTube upload finished without a video id", retryable=True)
        vid = video["id"]
        url = f"https://youtube.com/shorts/{vid}" if ctx.target == "youtube_shorts" else f"https://www.youtube.com/watch?v={vid}"
        privacy = ((video.get("status") or {}).get("privacyStatus"))
        note = f"YouTube reports privacy '{privacy}'" if privacy and privacy != self.build_resource(ctx)["status"]["privacyStatus"] else None
        return PublishResult(status="published", remote_id=vid, remote_url=url, note=note)

    def poll(self, ctx):
        return PublishResult(status="published", remote_id=ctx.remote_id)
