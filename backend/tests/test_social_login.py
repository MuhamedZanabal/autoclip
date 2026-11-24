"""One-click OAuth login: authorize URLs, state/CSRF, PKCE, token exchange, error paths,
developer-app credentials (encrypted, masked, .env precedence) and the HTTP routes.

Providers are mocked with httpx.MockTransport; nothing touches the network.
"""
import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.publisher import apps, config, crypto, service
from backend.publisher.adapters import AdapterError

PUBLIC = "https://example.test/autoclip"
ENV_KEYS = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
            "META_APP_ID", "META_APP_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TIKTOK_PKCE",
            "INSTAGRAM_LOGIN_MODE")


class Provider:
    """Records token-endpoint requests and answers like TikTok, Instagram and Google do."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.fail_token = False
        self.next_user = 1

    def form(self, req: httpx.Request) -> dict:
        return {k: v[0] for k, v in parse_qs(req.content.decode()).items()} if req.content else {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        url = str(req.url)
        if self.fail_token and ("oauth/token" in url or "oauth/access_token" in url or "oauth2.googleapis.com/token" in url):
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Code expired"})
        if url.startswith("https://open.tiktokapis.com/v2/oauth/token/"):
            return httpx.Response(200, json={"access_token": f"tt-access-{self.next_user}", "refresh_token": "tt-refresh",
                                             "expires_in": 86400, "refresh_expires_in": 31536000,
                                             "open_id": f"open-{self.next_user}", "scope": "user.info.basic,video.publish"})
        if url.startswith("https://open.tiktokapis.com/v2/user/info/"):
            n = self.next_user
            return httpx.Response(200, json={"data": {"user": {"open_id": f"open-{n}", "display_name": f"Creator {n}",
                                                               "avatar_url": f"https://p16.test/{n}.jpg"}},
                                             "error": {"code": "ok"}})
        if url.startswith("https://open.tiktokapis.com/v2/oauth/revoke/"):
            return httpx.Response(200, json={})
        if url.startswith("https://api.instagram.com/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "ig-short", "user_id": 17841400000000001,
                                             "permissions": "instagram_business_basic,instagram_business_content_publish"})
        if url.startswith("https://graph.instagram.com/access_token"):
            return httpx.Response(200, json={"access_token": "ig-long", "token_type": "bearer", "expires_in": 5183944})
        if url.startswith("https://graph.instagram.com/"):
            return httpx.Response(200, json={"user_id": "17841400000000001", "id": "17841400000000001",
                                             "username": "zedos.ai", "name": "ZedOS", "profile_picture_url": "https://ig.test/a.jpg",
                                             "account_type": "BUSINESS"})
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "g-access", "refresh_token": "g-refresh", "expires_in": 3599,
                                             "scope": "https://www.googleapis.com/auth/youtube.upload", "token_type": "Bearer"})
        if url.startswith("https://www.googleapis.com/youtube/v3/channels"):
            return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "ZedOS", "customUrl": "@zedos",
                                             "thumbnails": {"default": {"url": "https://yt.test/a.jpg"}}}}]})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        return httpx.Response(404, json={"error": f"unmocked {url}"})


@pytest.fixture()
def env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SOCIAL_TOKEN_KEY", crypto.generate_key())
    monkeypatch.setenv("AUTOCLIP_PUBLIC_URL", PUBLIC)
    import backend.models  # noqa: F401
    from backend.core.database import Base, engine
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for t in ("social_publish_jobs", "social_accounts", "social_oauth_states", "social_settings", "social_app_credentials"):
            conn.execute(text(f"DELETE FROM {t}"))
    apps.invalidate()
    provider = Provider()
    real = service.get_adapter
    client = httpx.Client(transport=httpx.MockTransport(provider))
    monkeypatch.setattr(service, "get_adapter", lambda platform, client_=None: real(platform, client=client))
    yield provider
    apps.invalidate()


def configure_env(monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "awtestclientkey")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "tt-secret-value")
    monkeypatch.setenv("INSTAGRAM_APP_ID", "1234567890")
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "ig-secret-value")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "123-abc.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret-value")


@pytest.fixture()
def http(env):
    from backend.publisher.api import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def frag_q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).fragment.split("?", 1)[1]).items()}


def stored_verifier(state: str) -> str | None:
    from backend.models.social import SocialOAuthState
    with service.session() as db:
        return db.get(SocialOAuthState, state).code_verifier


# ------------------------------------------------------------ authorize URLs ---
def test_tiktok_authorize_url_has_state_scopes_and_hex_pkce(env, monkeypatch):
    configure_env(monkeypatch)
    url = service.start_oauth("tiktok")
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    p = q(url)
    assert p["client_key"] == "awtestclientkey" and p["response_type"] == "code"
    assert p["scope"] == "user.info.basic,video.publish"
    assert p["redirect_uri"] == f"{PUBLIC}/api/v1/social/oauth/tiktok/callback"
    assert len(p["state"]) >= 40 and p["disable_auto_auth"] == "1"
    verifier = stored_verifier(p["state"])
    assert 43 <= len(verifier) <= 128
    assert p["code_challenge_method"] == "S256"
    assert p["code_challenge"] == hashlib.sha256(verifier.encode()).hexdigest()  # TikTok wants hex


def test_tiktok_pkce_can_be_disabled(env, monkeypatch):
    configure_env(monkeypatch)
    monkeypatch.setenv("TIKTOK_PKCE", "false")
    p = q(service.start_oauth("tiktok"))
    assert "code_challenge" not in p and stored_verifier(p["state"]) is None


def test_google_authorize_url_uses_rfc7636_pkce_and_offline_access(env, monkeypatch):
    configure_env(monkeypatch)
    url = service.start_oauth("youtube")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    p = q(url)
    assert p["client_id"] == "123-abc.apps.googleusercontent.com" and p["response_type"] == "code"
    assert p["access_type"] == "offline" and "consent" in p["prompt"] and "select_account" in p["prompt"]
    assert "https://www.googleapis.com/auth/youtube.upload" in p["scope"]
    verifier = stored_verifier(p["state"])
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert p["code_challenge"] == expected and p["code_challenge_method"] == "S256"


def test_instagram_authorize_url(env, monkeypatch):
    configure_env(monkeypatch)
    url = service.start_oauth("instagram")
    assert url.startswith("https://www.instagram.com/oauth/authorize?")
    p = q(url)
    assert p["client_id"] == "1234567890" and p["response_type"] == "code"
    assert set(p["scope"].split(",")) == {"instagram_business_basic", "instagram_business_content_publish"}
    assert p["redirect_uri"] == f"{PUBLIC}/api/v1/social/oauth/instagram/callback"
    assert p["force_authentication"] == "1"  # lets a second Instagram account sign in
    assert "code_challenge" not in p  # Instagram Login does not support PKCE


# ------------------------------------------------------- token exchange + PKCE ---
def test_tiktok_exchange_sends_verifier_and_stores_encrypted_token(env, monkeypatch):
    configure_env(monkeypatch)
    state = q(service.start_oauth("tiktok"))["state"]
    verifier = stored_verifier(state)
    accounts = service.finish_oauth("tiktok", "auth-code-1", state)
    token_req = next(r for r in env.calls if "oauth/token" in str(r.url))
    form = env.form(token_req)
    assert form["code"] == "auth-code-1" and form["grant_type"] == "authorization_code"
    assert form["code_verifier"] == verifier and form["client_key"] == "awtestclientkey"
    assert form["redirect_uri"] == f"{PUBLIC}/api/v1/social/oauth/tiktok/callback"
    assert accounts[0]["display_name"] == "Creator 1" and accounts[0]["avatar_url"].endswith("1.jpg")
    assert accounts[0]["access_expires_at"].endswith("Z")
    assert "tt-access" not in json.dumps(accounts)
    from backend.models.social import SocialAccount
    with service.session() as db:
        row = db.query(SocialAccount).one()
        assert "tt-access-1" not in row.token_encrypted
        assert crypto.decrypt_json(row.token_encrypted)["access_token"] == "tt-access-1"


def test_google_exchange_sends_verifier(env, monkeypatch):
    configure_env(monkeypatch)
    state = q(service.start_oauth("youtube"))["state"]
    verifier = stored_verifier(state)
    accounts = service.finish_oauth("youtube", "g-code", state)
    form = env.form(next(r for r in env.calls if "oauth2.googleapis.com/token" in str(r.url)))
    assert form["code_verifier"] == verifier and form["grant_type"] == "authorization_code"
    assert accounts[0]["external_id"] == "UC123"


def test_instagram_exchange(env, monkeypatch):
    configure_env(monkeypatch)
    state = q(service.start_oauth("instagram"))["state"]
    accounts = service.finish_oauth("instagram", "ig-code", state)
    form = env.form(next(r for r in env.calls if "api.instagram.com/oauth/access_token" in str(r.url)))
    assert "code_verifier" not in form and form["code"] == "ig-code"
    assert accounts[0]["username"] == "zedos.ai"


def test_multiple_accounts_per_platform_and_reconnect_updates(env, monkeypatch):
    configure_env(monkeypatch)
    for n in (1, 2, 1):  # second login with account 1 = Reconnect: updates, no duplicate
        env.next_user = n
        service.finish_oauth("tiktok", f"code{n}", q(service.start_oauth("tiktok"))["state"])
    accts = service.list_accounts()
    assert sorted(a["external_id"] for a in accts) == ["open-1", "open-2"]


# ------------------------------------------------------------- state / errors ---
def test_state_is_single_use_platform_bound_and_expires(env, monkeypatch):
    configure_env(monkeypatch)
    state = q(service.start_oauth("tiktok"))["state"]
    with pytest.raises(AdapterError) as ei:
        service.finish_oauth("youtube", "c", state)  # minted for TikTok
    assert ei.value.code == "bad_state"
    with pytest.raises(AdapterError):
        service.finish_oauth("tiktok", "c", state)  # already consumed by the failed attempt
    with pytest.raises(AdapterError):
        service.finish_oauth("tiktok", "c", "forged-state")
    from backend.models.social import SocialOAuthState
    old = q(service.start_oauth("tiktok"))["state"]
    with service.session() as db:
        db.get(SocialOAuthState, old).created_at = service.utcnow() - timedelta(minutes=16)
        db.commit()
    with pytest.raises(AdapterError):
        service.finish_oauth("tiktok", "c", old)
    assert not service.list_accounts()


def test_token_endpoint_error_stores_nothing(env, monkeypatch):
    configure_env(monkeypatch)
    env.fail_token = True
    state = q(service.start_oauth("youtube"))["state"]
    with pytest.raises(AdapterError):
        service.finish_oauth("youtube", "bad", state)
    assert not service.list_accounts()


# ------------------------------------------------------------------- routes ---
def test_start_route_redirects_to_provider(http, monkeypatch):
    configure_env(monkeypatch)
    for platform, host in (("tiktok", "www.tiktok.com"), ("instagram", "www.instagram.com"), ("youtube", "accounts.google.com")):
        r = http.get(f"/api/v1/social/oauth/{platform}/start", follow_redirects=False)
        assert r.status_code == 302 and urlparse(r.headers["location"]).hostname == host


def test_start_route_unconfigured_opens_setup_screen(http):
    r = http.get("/api/v1/social/oauth/tiktok/start", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"{PUBLIC}/#/publishing?tab=apps&setup=tiktok"
    r = http.get("/api/v1/social/oauth/tiktok/start?redirect=false")
    assert r.status_code == 409 and r.json()["setup_url"].endswith("setup=tiktok")


def test_callback_route_success_and_errors(http, monkeypatch):
    configure_env(monkeypatch)
    state = q(http.get("/api/v1/social/oauth/tiktok/start", follow_redirects=False).headers["location"])["state"]
    r = http.get(f"/api/v1/social/oauth/tiktok/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{PUBLIC}/#/publishing?")
    p = frag_q(r.headers["location"])
    assert p["result"] == "connected" and p["tab"] == "accounts" and p["count"] == "1"
    # replay of the same state
    p = frag_q(http.get(f"/api/v1/social/oauth/tiktok/callback?code=abc&state={state}", follow_redirects=False).headers["location"])
    assert p["result"] == "error" and "state" in p["message"].lower()
    # user pressed Cancel on the provider
    p = frag_q(http.get("/api/v1/social/oauth/youtube/callback?error=access_denied&state=x", follow_redirects=False).headers["location"])
    assert p["result"] == "error" and p["message"] == "access_denied" and p["oauth"] == "youtube"
    assert http.get("/api/v1/social/oauth/myspace/callback?code=1&state=2", follow_redirects=False).status_code == 404


def test_disconnect_revokes_where_supported(http, env, monkeypatch):
    configure_env(monkeypatch)
    for platform in ("youtube", "instagram"):
        loc = http.get(f"/api/v1/social/oauth/{platform}/start", follow_redirects=False).headers["location"]
        http.get(f"/api/v1/social/oauth/{platform}/callback?code=c&state={q(loc)['state']}", follow_redirects=False)
    accts = {a["platform"]: a for a in http.get("/api/v1/social/accounts").json()["accounts"]}
    assert http.delete(f"/api/v1/social/accounts/{accts['youtube']['id']}").json()["revoked"] is True
    assert any("oauth2.googleapis.com/revoke" in str(r.url) for r in env.calls)
    assert http.delete(f"/api/v1/social/accounts/{accts['instagram']['id']}").json()["revoked"] == "unsupported"


# ---------------------------------------------------------- developer apps ---
def test_developer_apps_are_encrypted_masked_and_never_returned(http):
    secret = "super-secret-value-1234"
    r = http.put("/api/v1/social/apps/tiktok", json={"client_id": "awdummyclientkey9", "client_secret": secret})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] and body["source"] == "db" and body["secret_set"]
    assert body["client_id_masked"] == "awdu…key9"
    listing = http.get("/api/v1/social/apps").text + http.get("/api/v1/social/status").text
    assert secret not in listing and "awdummyclientkey9" not in listing
    from backend.models.social import SocialAppCredential
    with service.session() as db:
        row = db.get(SocialAppCredential, "tiktok")
        assert secret not in row.client_secret_encrypted and "awdummyclientkey9" not in row.client_id_encrypted
    # blank secret keeps the stored one
    assert http.put("/api/v1/social/apps/tiktok", json={"client_id": "awdummyclientkey8", "client_secret": ""}).status_code == 200
    assert config.credentials("tiktok").client_secret == secret
    # the login button now goes straight to TikTok with the UI-entered key
    loc = http.get("/api/v1/social/oauth/tiktok/start", follow_redirects=False).headers["location"]
    assert urlparse(loc).hostname == "www.tiktok.com" and q(loc)["client_key"] == "awdummyclientkey8"
    # remove -> back to the setup screen
    assert http.delete("/api/v1/social/apps/tiktok").json()["removed"] is True
    loc = http.get("/api/v1/social/oauth/tiktok/start", follow_redirects=False).headers["location"]
    assert loc.endswith("tab=apps&setup=tiktok")


def test_env_takes_precedence_over_ui_credentials(http, monkeypatch):
    assert http.put("/api/v1/social/apps/youtube", json={"client_id": "ui-client.apps", "client_secret": "ui-secret"}).status_code == 200
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "env-client.apps")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "env-secret")
    st = http.get("/api/v1/social/apps").json()["apps"][2]
    assert st["platform"] == "youtube" and st["source"] == "env" and st["env_locked"]
    assert config.credentials("youtube").client_id == "env-client.apps"
    r = http.put("/api/v1/social/apps/youtube", json={"client_id": "other.apps", "client_secret": "x"})
    assert r.status_code == 400 and ".env" in r.json()["detail"]


def test_developer_app_validation(http):
    assert http.put("/api/v1/social/apps/tiktok", json={"client_id": "has space", "client_secret": "s"}).status_code == 400
    assert http.put("/api/v1/social/apps/tiktok", json={"client_id": "validkey", "client_secret": ""}).status_code == 400
    assert http.put("/api/v1/social/apps/myspace", json={"client_id": "validkey", "client_secret": "s"}).status_code == 404
