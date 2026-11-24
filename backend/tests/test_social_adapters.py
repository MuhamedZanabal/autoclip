"""Platform adapters against mocked official endpoints (no network)."""
import json
import time
from pathlib import Path

import httpx
import pytest

from backend.publisher import crypto
from backend.publisher.adapters import AdapterError, PublishContext, TokenBundle
from backend.publisher.adapters.instagram import InstagramAdapter
from backend.publisher.adapters.tiktok import TikTokAdapter, chunk_plan
from backend.publisher.adapters.youtube import YouTubeAdapter


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("AUTOCLIP_PUBLIC_URL", "https://example.test/autoclip")
    monkeypatch.setenv("SOCIAL_TOKEN_KEY", crypto.generate_key())
    for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
              "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.setenv(k, "x-" + k.lower())
    monkeypatch.delenv("TIKTOK_APP_AUDITED", raising=False)
    monkeypatch.delenv("INSTAGRAM_LOGIN_MODE", raising=False)


def video(tmp_path: Path, size=1000) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\0" * size)
    return p


def ctx(tmp_path, target, **kw):
    return PublishContext(token=TokenBundle("tok", "ref", time.time() + 3600), account_external_id="acct",
                          account_username="me", target=target, video_path=video(tmp_path),
                          caption={"caption": "Hello #AI", "title": "Title", "description": "Desc", "tags": ["a"]},
                          options={}, duration_sec=30, **kw)


def test_chunk_plan():
    assert chunk_plan(1000) == (1000, 1)
    assert chunk_plan(64 * 1024 * 1024) == (64 * 1024 * 1024, 1)
    size = 150 * 1024 * 1024
    chunk, n = chunk_plan(size)
    assert chunk == 10 * 1024 * 1024 and n == 15 and size - chunk * (n - 1) <= 128 * 1024 * 1024


def test_tiktok_privacy_rules():
    assert TikTokAdapter.choose_privacy("PUBLIC_TO_EVERYONE", ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], False)[0] == "SELF_ONLY"
    assert TikTokAdapter.choose_privacy("FOLLOWER_OF_CREATOR", ["FOLLOWER_OF_CREATOR"], True)[0] == "FOLLOWER_OF_CREATOR"
    with pytest.raises(AdapterError):
        TikTokAdapter.choose_privacy("PUBLIC_TO_EVERYONE", ["SELF_ONLY"], True)


def test_tiktok_publish_and_poll(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, str(req.url)))
        if req.url.path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                                                      "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if req.url.path.endswith("/video/init/"):
            body = json.loads(req.content)
            assert body["post_info"]["privacy_level"] == "SELF_ONLY"
            assert body["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1000, "chunk_size": 1000, "total_chunk_count": 1}
            return httpx.Response(200, json={"data": {"publish_id": "p1", "upload_url": "https://upload.test/u?x=1"},
                                             "error": {"code": "ok"}})
        if req.url.host == "upload.test":
            assert req.headers["content-range"] == "bytes 0-999/1000"
            return httpx.Response(201)
        if req.url.path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [123]},
                                             "error": {"code": "ok"}})
        raise AssertionError(req.url)

    ad = TikTokAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = ctx(tmp_path, "tiktok")
    res = ad.publish(c)
    assert res.status == "processing" and res.remote_id == "p1" and "private" in res.note
    c.remote_id = "p1"
    done = ad.poll(c)
    assert done.status == "published" and done.remote_url == "https://www.tiktok.com/@me/video/123"


def test_tiktok_token_error_maps_to_reconnect(tmp_path):
    def handler(req):
        return httpx.Response(401, json={"error": {"code": "access_token_invalid", "message": "bad"}})
    ad = TikTokAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AdapterError) as ei:
        ad.publish(ctx(tmp_path, "tiktok"))
    assert ei.value.needs_reconnect


def test_instagram_container_poll_publish(tmp_path):
    state = {"polls": 0}

    def handler(req: httpx.Request):
        if req.method == "POST" and req.url.path.endswith("/acct/media"):
            form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            assert form["media_type"] == "REELS" and "video_url" in form
            return httpx.Response(200, json={"id": "c1"})
        if req.url.path.endswith("/c1"):
            state["polls"] += 1
            return httpx.Response(200, json={"status_code": "IN_PROGRESS" if state["polls"] == 1 else "FINISHED"})
        if req.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "m1"})
        if req.url.path.endswith("/m1"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/abc"})
        raise AssertionError(req.url)

    ad = InstagramAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = ctx(tmp_path, "instagram", public_video_url="https://example.test/autoclip/api/v1/social/media/j.mp4?exp=1&sig=s")
    res = ad.publish(c)
    assert res.status == "processing" and res.remote_container_id == "c1"
    c.remote_container_id = "c1"
    assert ad.poll(c).status == "processing"
    done = ad.poll(c)
    assert done.status == "published" and done.remote_url.endswith("/reel/abc")
    assert "graph.instagram.com/v26.0" in ad.graph


def test_instagram_requires_public_url(tmp_path):
    with pytest.raises(AdapterError):
        InstagramAdapter(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))).publish(
            ctx(tmp_path, "instagram"))


def test_youtube_resumable_upload(tmp_path):
    def handler(req: httpx.Request):
        if req.method == "POST":
            assert req.url.params["uploadType"] == "resumable"
            body = json.loads(req.content)
            assert body["snippet"]["title"] == "Title" and body["status"]["privacyStatus"] == "public"
            assert req.headers["x-upload-content-length"] == "1000"
            return httpx.Response(200, headers={"Location": "https://upload.test/session"})
        if req.method == "PUT":
            return httpx.Response(200, json={"id": "vid1", "status": {"privacyStatus": "private"}})
        raise AssertionError(req.url)

    ad = YouTubeAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = ad.publish(ctx(tmp_path, "youtube_shorts"))
    assert res.status == "published" and res.remote_url == "https://youtube.com/shorts/vid1"
    assert "private" in res.note  # surfaced when YouTube applies a different privacy than requested


def test_authorize_urls_carry_redirect_and_scopes():
    tt = TikTokAdapter().authorize_url("st", None)
    assert "video.publish" in tt and "redirect_uri=https%3A%2F%2Fexample.test%2Fautoclip%2Fapi%2Fv1%2Fsocial%2Foauth%2Ftiktok%2Fcallback" in tt
    yt = YouTubeAdapter().authorize_url("st", "chal")
    assert "access_type=offline" in yt and "youtube.upload" in yt and "code_challenge=chal" in yt
    ig = InstagramAdapter().authorize_url("st", None)
    assert ig.startswith("https://www.instagram.com/oauth/authorize") and "instagram_business_content_publish" in ig


def test_not_configured(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY")
    with pytest.raises(AdapterError) as ei:
        TikTokAdapter().authorize_url("s", None)
    assert ei.value.code == "not_configured"
