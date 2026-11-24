"""Instagram Reels publishing via the official Instagram Platform API.

Two login modes (INSTAGRAM_LOGIN_MODE):
  instagram (default) — Instagram API with Instagram Login, host graph.instagram.com,
      scopes instagram_business_basic + instagram_business_content_publish. Needs a
      Business or Creator (professional) account; no Facebook Page required.
  facebook — Instagram API with Facebook Login, host graph.facebook.com, scopes
      instagram_basic + instagram_content_publish + pages_show_list + pages_read_engagement
      (+ business_management). Needs the IG professional account linked to a Facebook Page.

Flow: POST /{ig-id}/media (media_type=REELS, video_url=<signed public URL>) -> poll
/{container}?fields=status_code until FINISHED -> POST /{ig-id}/media_publish.
"""
from __future__ import annotations

import time
from urllib.parse import urlencode

from .. import config
from .base import AdapterError, BaseAdapter, ConnectedProfile, PublishContext, PublishResult, TokenBundle

IG_SCOPES = "instagram_business_basic,instagram_business_content_publish"
FB_SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement,business_management"


class InstagramAdapter(BaseAdapter):
    platform = "instagram"

    @property
    def mode(self) -> str:
        return config.instagram_login_mode()

    @property
    def graph(self) -> str:
        host = "https://graph.instagram.com" if self.mode == "instagram" else "https://graph.facebook.com"
        return f"{host}/{config.meta_graph_version()}"

    def _json(self, resp, what: str) -> dict:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if resp.status_code >= 400 or err:
            err = err if isinstance(err, dict) else {"message": str(err or resp.text[:300])}
            code = err.get("code")
            reconnect = code in (190, 102) or resp.status_code == 401
            retryable = code in (1, 2, 4, 17, 32, 341, 9004, 2207001, 2207003) or resp.status_code >= 500
            raise AdapterError(f"Instagram {what}: {err.get('message')} (code {code}, subcode {err.get('error_subcode')})",
                               retryable=retryable, needs_reconnect=reconnect, code=str(code))
        return body

    # --- OAuth -----------------------------------------------------------
    def authorize_url(self, state, code_challenge):
        self.require_configured()
        if self.mode == "instagram":
            return "https://www.instagram.com/oauth/authorize?" + urlencode({
                "client_id": self.creds.client_id, "redirect_uri": self.redirect_uri,
                "response_type": "code", "scope": IG_SCOPES, "state": state, "enable_fb_login": "0",
                "force_authentication": "1"})
        return f"https://www.facebook.com/{config.meta_graph_version()}/dialog/oauth?" + urlencode({
            "client_id": self.creds.client_id, "redirect_uri": self.redirect_uri, "state": state,
            "scope": FB_SCOPES, "response_type": "code"})

    def exchange_code(self, code, code_verifier):
        self.require_configured()
        if self.mode == "instagram":
            return self._exchange_ig(code)
        return self._exchange_fb(code)

    def _exchange_ig(self, code):
        resp = self.request("POST", "https://api.instagram.com/oauth/access_token", "token", data={
            "client_id": self.creds.client_id, "client_secret": self.creds.client_secret,
            "grant_type": "authorization_code", "redirect_uri": self.redirect_uri, "code": code})
        body = self._json(resp, "code exchange")
        if isinstance(body.get("data"), list) and body["data"]:
            body = body["data"][0]
        short = body.get("access_token")
        if not short:
            raise AdapterError("Instagram returned no access token")
        resp = self.request("GET", "https://graph.instagram.com/access_token", "long-lived token", params={
            "grant_type": "ig_exchange_token", "client_secret": self.creds.client_secret, "access_token": short})
        ll = self._json(resp, "long-lived exchange")
        token = TokenBundle(access_token=ll["access_token"], expires_at=time.time() + float(ll.get("expires_in") or 5184000),
                            scopes=str(body.get("permissions") or IG_SCOPES).split(","), extra={"mode": "instagram"})
        resp = self.request("GET", f"{self.graph}/me", "profile", params={
            "fields": "user_id,username,name,profile_picture_url,account_type", "access_token": token.access_token})
        me = self._json(resp, "profile")
        ig_id = str(me.get("user_id") or me.get("id") or body.get("user_id") or "")
        if not ig_id:
            raise AdapterError("Instagram profile has no user id")
        return token, [ConnectedProfile(external_id=ig_id, display_name=me.get("name") or me.get("username"),
                                        username=me.get("username"), avatar_url=me.get("profile_picture_url"),
                                        metadata={"account_type": me.get("account_type"), "mode": "instagram"})]

    def _exchange_fb(self, code):
        v = config.meta_graph_version()
        resp = self.request("GET", f"https://graph.facebook.com/{v}/oauth/access_token", "token", params={
            "client_id": self.creds.client_id, "client_secret": self.creds.client_secret,
            "redirect_uri": self.redirect_uri, "code": code})
        short = self._json(resp, "code exchange").get("access_token")
        resp = self.request("GET", f"https://graph.facebook.com/{v}/oauth/access_token", "long-lived token", params={
            "grant_type": "fb_exchange_token", "client_id": self.creds.client_id,
            "client_secret": self.creds.client_secret, "fb_exchange_token": short})
        ll = self._json(resp, "long-lived exchange")
        token = TokenBundle(access_token=ll["access_token"],
                            expires_at=time.time() + float(ll.get("expires_in") or 5184000),
                            scopes=FB_SCOPES.split(","), extra={"mode": "facebook"})
        resp = self.request("GET", f"https://graph.facebook.com/{v}/me/accounts", "pages", params={
            "fields": "id,name,instagram_business_account{id,username,name,profile_picture_url}",
            "access_token": token.access_token, "limit": 50})
        profiles = []
        for page in self._json(resp, "pages").get("data") or []:
            ig = page.get("instagram_business_account")
            if ig and ig.get("id"):
                profiles.append(ConnectedProfile(external_id=str(ig["id"]), display_name=ig.get("name") or ig.get("username"),
                                                 username=ig.get("username"), avatar_url=ig.get("profile_picture_url"),
                                                 metadata={"page_id": page.get("id"), "page_name": page.get("name"), "mode": "facebook"}))
        if not profiles:
            raise AdapterError("No Instagram Business/Creator account linked to any Facebook Page you granted")
        return token, profiles

    def refresh(self, token):
        if (token.extra or {}).get("mode", "instagram") == "facebook":
            # Facebook long-lived user tokens cannot be extended server-side; the user reconnects.
            raise AdapterError("Facebook token expired; reconnect Instagram", needs_reconnect=True)
        resp = self.request("GET", "https://graph.instagram.com/refresh_access_token", "refresh", params={
            "grant_type": "ig_refresh_token", "access_token": token.access_token})
        body = self._json(resp, "refresh")
        return TokenBundle(access_token=body["access_token"], expires_at=time.time() + float(body.get("expires_in") or 5184000),
                           scopes=token.scopes, extra=token.extra)

    # --- publishing ------------------------------------------------------
    def publish(self, ctx: PublishContext) -> PublishResult:
        if not ctx.public_video_url:
            raise AdapterError("Instagram needs a public video URL (AUTOCLIP_PUBLIC_URL / signed media route)")
        caption = str(ctx.caption.get("caption") or "")[:2200]
        data = {"media_type": "REELS", "video_url": ctx.public_video_url, "caption": caption,
                "share_to_feed": "true" if ctx.options.get("share_to_feed", True) else "false",
                "access_token": ctx.token.access_token}
        if ctx.options.get("thumb_offset_ms") is not None:
            data["thumb_offset"] = str(int(ctx.options["thumb_offset_ms"]))
        resp = self.request("POST", f"{self.graph}/{ctx.account_external_id}/media", "create container", data=data)
        container = self._json(resp, "create container").get("id")
        if not container:
            raise AdapterError("Instagram returned no container id", retryable=True)
        return PublishResult(status="processing", remote_container_id=str(container))

    def poll(self, ctx: PublishContext) -> PublishResult:
        resp = self.request("GET", f"{self.graph}/{ctx.remote_container_id}", "container status", params={
            "fields": "status_code,status", "access_token": ctx.token.access_token})
        body = self._json(resp, "container status")
        code = body.get("status_code")
        if code in ("ERROR", "EXPIRED"):
            raise AdapterError(f"Instagram container {code}: {body.get('status') or ''}", retryable=True, code="remote_failed")
        if code != "FINISHED":
            return PublishResult(status="processing", remote_container_id=ctx.remote_container_id)
        resp = self.request("POST", f"{self.graph}/{ctx.account_external_id}/media_publish", "publish", data={
            "creation_id": ctx.remote_container_id, "access_token": ctx.token.access_token})
        media_id = self._json(resp, "publish").get("id")
        permalink = None
        if media_id:
            resp = self.request("GET", f"{self.graph}/{media_id}", "permalink", params={
                "fields": "permalink", "access_token": ctx.token.access_token})
            if resp.status_code < 400:
                permalink = (resp.json() or {}).get("permalink")
        return PublishResult(status="published", remote_id=str(media_id) if media_id else None,
                             remote_container_id=ctx.remote_container_id, remote_url=permalink)
