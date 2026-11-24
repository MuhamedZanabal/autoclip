"""Queue/OAuth service against a real SQLite DB with a fake adapter (no network, no ffmpeg)."""
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.publisher import crypto, service
from backend.publisher.adapters import AdapterError, PublishResult, TokenBundle
from backend.publisher.adapters.base import ConnectedProfile


class FakeAdapter:
    platform = "tiktok"
    polls = 0
    fail_publish = None

    def __init__(self, client=None):
        pass

    def authorize_url(self, state, challenge):
        return f"https://auth.test/?state={state}"

    def exchange_code(self, code, verifier):
        return TokenBundle("acc", "ref", time.time() + 3600, scopes=["video.publish"]), [ConnectedProfile("open1", "Me", "me")]

    def refresh(self, token):
        return TokenBundle("acc2", "ref", time.time() + 3600)

    def revoke(self, token):
        pass

    def publish(self, ctx):
        if FakeAdapter.fail_publish:
            raise FakeAdapter.fail_publish
        assert ctx.caption["caption"]
        return PublishResult(status="processing", remote_id="pub1")

    def poll(self, ctx):
        FakeAdapter.polls += 1
        if FakeAdapter.polls == 1:
            raise AdapterError("network blip", retryable=True)
        return PublishResult(status="published", remote_id=ctx.remote_id, remote_url="https://tiktok.test/v/1")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_TOKEN_KEY", crypto.generate_key())
    monkeypatch.setenv("AUTOCLIP_PUBLIC_URL", "https://example.test/autoclip")
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "k")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "s")
    monkeypatch.setattr(service, "get_adapter", lambda platform, client=None: FakeAdapter())
    FakeAdapter.polls, FakeAdapter.fail_publish = 0, None
    import backend.models  # noqa: F401
    from backend.core.database import engine, Base
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for t in ("social_publish_jobs", "social_accounts", "social_oauth_states", "social_settings"):
            conn.execute(text(f"DELETE FROM {t}"))
    clip = {"id": "clip1", "project_id": "p1", "title": "OpenAI strategy reset", "duration": 40, "start_time": 0,
            "end_time": 40, "description": "", "recommendation_reason": "", "clip_metadata": None,
            "project_name": "P", "video_path": None, "score": 0.9}
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 100)
    monkeypatch.setattr(service.media, "get_clip", lambda cid: dict(clip))
    monkeypatch.setattr(service.media, "clip_segments", lambda row: [(0, 10, "OpenAI strategy talk")])
    monkeypatch.setattr(service.media, "render_for_target", lambda row, target, subtitles=True: (video, 40.0, []))
    monkeypatch.setattr(service.seo, "call_llm", lambda prompt: (_ for _ in ()).throw(RuntimeError("no llm in tests")))
    return clip


def _connect():
    url = service.start_oauth("tiktok")
    state = url.split("state=")[1]
    return state, service.finish_oauth("tiktok", "code", state)


def test_oauth_state_is_single_use_and_token_encrypted(env):
    state, accounts = _connect()
    assert accounts[0]["username"] == "me" and accounts[0]["platform"] == "tiktok"
    with pytest.raises(AdapterError):
        service.finish_oauth("tiktok", "code", state)
    with pytest.raises(AdapterError):
        service.finish_oauth("tiktok", "code", "forged")
    from backend.core.database import engine
    with engine.connect() as conn:
        blob = conn.execute(text("SELECT token_encrypted FROM social_accounts")).scalar()
    assert "acc" not in blob and crypto.decrypt_json(blob)["access_token"] == "acc"


def test_queue_publish_poll_with_safe_retry(env):
    _, accounts = _connect()
    jobs = service.create_jobs("clip1", [{"account_id": accounts[0]["id"], "target": "tiktok"}])
    job = jobs[0]
    assert job["status"] == "queued" and job["caption"]["caption"] and job["seo_report"]["source"] == "heuristic"
    out = service.process_job(job["id"])
    assert out["status"] == "processing" and out["remote_id"] == "pub1"
    # transient poll error keeps it processing (never re-uploads)
    out = service.process_job(job["id"])
    assert out["status"] == "processing" and out["attempts"] == 1
    out = service.process_job(job["id"])
    assert out["status"] == "published" and out["remote_url"] == "https://tiktok.test/v/1"


def test_retry_backoff_then_fail(env):
    _, accounts = _connect()
    job = service.create_jobs("clip1", [{"account_id": accounts[0]["id"], "target": "tiktok"}])[0]
    FakeAdapter.fail_publish = AdapterError("rate limited", retryable=True)
    out = service.process_job(job["id"])
    assert out["status"] == "queued" and out["attempts"] == 1 and out["next_attempt_at"]
    assert service.due_job_ids() == []  # backoff respected
    FakeAdapter.fail_publish = AdapterError("bad caption", retryable=False)
    service.update_job(job["id"], {"action": "publish_now"})
    out = service.process_job(job["id"])
    assert out["status"] == "failed" and "bad caption" in out["error"]
    retried = service.update_job(job["id"], {"action": "retry"})
    assert retried["status"] == "queued" and retried["attempts"] == 0


def test_draft_approval_and_caption_edit(env):
    _, accounts = _connect()
    job = service.create_jobs("clip1", [{"account_id": accounts[0]["id"], "target": "tiktok"}], draft=True)[0]
    assert job["status"] == "draft" and service.due_job_ids() == []
    edited = service.update_job(job["id"], {"caption": {"caption": "OpenAI strategy explained #AI #OpenAI #Tech"}})
    assert edited["caption"]["caption"].startswith("OpenAI strategy") and edited["seo_report"]["source"] == "edited"
    approved = service.update_job(job["id"], {"action": "approve"})
    assert approved["status"] == "queued" and service.due_job_ids() == [job["id"]]


def test_auto_publish_scan_only_new_clips(env, monkeypatch):
    _, accounts = _connect()
    settings = service.save_settings({"auto_publish": True, "require_approval": False,
                                      "targets": [{"account_id": accounts[0]["id"], "target": "tiktok"}]})
    assert settings["enabled_since"]
    since = service.parse_when(settings["enabled_since"])
    rows = [{"id": "clip1", "project_id": "p1", "score": 0.9, "created_at": since + timedelta(minutes=1)}]
    monkeypatch.setattr(service.media, "_rows", lambda sql, **kw: rows)
    created = service.auto_publish_scan()
    assert len(created) == 1 and created[0]["source"] == "auto"
    assert service.auto_publish_scan() == []  # idempotent per clip+account+target


def test_disconnect_cancels_pending(env):
    _, accounts = _connect()
    job = service.create_jobs("clip1", [{"account_id": accounts[0]["id"], "target": "tiktok"}], draft=True)[0]
    res = service.disconnect(accounts[0]["id"])
    assert res["cancelled_jobs"] == 1 and service.list_accounts() == []
