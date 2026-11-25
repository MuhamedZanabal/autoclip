"""Accounts, OAuth state, publish queue, retries and auto-publish."""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import or_

from . import config, crypto, media, seo
from .adapters import AdapterError, PublishContext, TokenBundle, get_adapter

logger = logging.getLogger(__name__)

STATE_TTL = timedelta(minutes=15)
RETRY_BACKOFF_MIN = (2, 10, 30, 90)
LOCK_TTL = timedelta(minutes=30)
PROCESSING_TIMEOUT = timedelta(hours=2)
ACTIVE = ("queued", "rendering", "uploading", "processing")
EDITABLE = ("draft", "queued", "failed")

DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_publish": False,            # publish every newly completed clip automatically
    "require_approval": True,         # auto-created jobs start as drafts until approved
    "targets": [],                    # [{"account_id": ..., "target": "tiktok|instagram|youtube_shorts|youtube"}]
    "min_score": 0.0,                 # only clips scoring at least this (0-1) are auto-queued
    "spacing_minutes": 60,            # gap between auto posts on the same account
    "max_per_day": 6,                 # per account, auto posts only
    "enabled_since": None,            # clips created before enabling are never auto-posted
    "language": "",                   # "" = detect from transcript
    "brand_hashtags": [],
    "burn_subtitles": True,
    "defaults": {
        # No default privacy_level: TikTok requires a manual per-post choice.
        # Interactions default off (disable_*=True) until the user turns them on.
        "tiktok": {"disable_comment": True, "disable_duet": True,
                   "disable_stitch": True, "is_aigc": False,
                   "brand_content_toggle": False, "brand_organic_toggle": False},
        "instagram": {"share_to_feed": True},
        "youtube_shorts": {"privacy_status": "public", "made_for_kids": False},
        "youtube": {"privacy_status": "public", "made_for_kids": False},
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def parse_when(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return naive(value)
    return naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def session():
    from backend.core.database import session_scope
    return session_scope()


def ensure_tables() -> None:
    from backend.core.database import engine
    from backend.models.social import SocialAccount, SocialOAuthState, SocialPublishJob, SocialSetting
    from backend.models.social import SocialAppCredential
    for model in (SocialAccount, SocialPublishJob, SocialOAuthState, SocialSetting, SocialAppCredential):
        model.__table__.create(bind=engine, checkfirst=True)


def _history(job, event: str, **data) -> None:
    entry = {"at": utcnow().isoformat() + "Z", "event": event, **{k: v for k, v in data.items() if v is not None}}
    job.history = [*(job.history or []), entry][-50:]


# --------------------------------------------------------------- settings ---
def get_settings() -> dict:
    from backend.models.social import SocialSetting
    with session() as db:
        row = db.get(SocialSetting, "publisher")
        stored = dict(row.value or {}) if row else {}
    merged = {**DEFAULT_SETTINGS, **stored}
    merged["defaults"] = {k: {**v, **((stored.get("defaults") or {}).get(k) or {})}
                          for k, v in DEFAULT_SETTINGS["defaults"].items()}
    return merged


def save_settings(patch: dict) -> dict:
    from backend.models.social import SocialSetting
    current = get_settings()
    allowed = set(DEFAULT_SETTINGS)
    for key, value in patch.items():
        if key not in allowed or key == "enabled_since":
            continue
        if key == "defaults" and isinstance(value, dict):
            for t, opts in value.items():
                if t in current["defaults"] and isinstance(opts, dict):
                    current["defaults"][t].update(opts)
            continue
        current[key] = value
    if patch.get("auto_publish") and not current.get("enabled_since"):
        current["enabled_since"] = utcnow().isoformat() + "Z"
    if patch.get("auto_publish") is False:
        current["enabled_since"] = None
    current["targets"] = [t for t in current.get("targets") or []
                          if isinstance(t, dict) and t.get("target") in config.TARGETS and t.get("account_id")]
    with session() as db:
        row = db.get(SocialSetting, "publisher")
        if row is None:
            row = SocialSetting(key="publisher", value=current)
            db.add(row)
        else:
            row.value = current
        db.commit()
    return current


# ------------------------------------------------------------------ OAuth ---
def pkce_pair(style: str | None) -> tuple[str, str | None]:
    """(code_verifier, code_challenge). Verifier: 86 chars from the RFC 7636 unreserved set."""
    verifier = secrets.token_urlsafe(64)[:86]
    if style == "hex":
        return verifier, hashlib.sha256(verifier.encode()).hexdigest()
    if style == "b64":
        return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, None


def start_oauth(platform: str) -> str:
    from backend.models.social import SocialOAuthState
    if platform not in config.PLATFORMS:
        raise ValueError("unknown platform")
    status = config.platform_status(platform)
    if not status["configured"]:
        raise AdapterError(f"{platform} is not configured: missing {', '.join(status['missing_env'])}", code="not_configured")
    adapter = get_adapter(platform)
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair(adapter.pkce_style)
    with session() as db:
        db.query(SocialOAuthState).filter(SocialOAuthState.created_at < utcnow() - STATE_TTL).delete()
        db.add(SocialOAuthState(state=state, platform=platform, code_verifier=verifier if challenge else None,
                                created_at=utcnow(), used=False))
        db.commit()
    return adapter.authorize_url(state, challenge)


def finish_oauth(platform: str, code: str, state: str) -> list[dict]:
    from backend.models.social import SocialAccount, SocialOAuthState
    with session() as db:
        row = db.get(SocialOAuthState, state)
        valid = (row is not None and not row.used and row.platform == platform
                 and naive(row.created_at) >= utcnow() - STATE_TTL)
        if row is not None:
            row.used = True  # any presentation burns the state, valid or not
            db.commit()
        if not valid:
            raise AdapterError("OAuth state is invalid or expired; start the connection again", code="bad_state")
        verifier = row.code_verifier
    adapter = get_adapter(platform)
    token, profiles = adapter.exchange_code(code, verifier)
    saved = []
    with session() as db:
        for prof in profiles:
            acct = (db.query(SocialAccount)
                    .filter(SocialAccount.platform == platform, SocialAccount.external_id == prof.external_id).first())
            if acct is None:
                acct = SocialAccount(platform=platform, external_id=prof.external_id)
                db.add(acct)
            acct.display_name, acct.username, acct.avatar_url = prof.display_name, prof.username, prof.avatar_url
            acct.scopes = ",".join(token.scopes)
            acct.token_encrypted = crypto.encrypt_json(token.to_dict())
            acct.access_expires_at = datetime.utcfromtimestamp(token.expires_at) if token.expires_at else None
            acct.refresh_expires_at = datetime.utcfromtimestamp(token.refresh_expires_at) if token.refresh_expires_at else None
            acct.status, acct.last_error = "active", None
            acct.account_metadata = prof.metadata or {}
            db.commit()
            saved.append(acct.public())
    return saved


def list_accounts() -> list[dict]:
    from backend.models.social import SocialAccount
    with session() as db:
        return [a.public() for a in db.query(SocialAccount).order_by(SocialAccount.platform, SocialAccount.created_at)]


def query_tiktok_creator(account_id: str) -> dict:
    """Latest TikTok creator_info used to render the Post to TikTok form."""
    from backend.models.social import SocialAccount
    from .adapters.tiktok import TikTokAdapter, _audited
    with session() as db:
        acct = db.get(SocialAccount, account_id)
        if acct is None:
            raise FileNotFoundError("account not found")
        if acct.platform != "tiktok":
            raise ValueError("creator info is only available for TikTok accounts")
        if acct.status != "active":
            raise AdapterError(f"account needs attention: {acct.status}", needs_reconnect=True)
        token = fresh_token(acct)
        db.commit()
        result = TikTokAdapter().query_creator_info(token)
    data = result.get("data") or {}
    return {
        "can_post": result["can_post"],
        "error_code": result.get("error_code"),
        "error_message": result.get("error_message"),
        "audited": _audited(),
        "creator_nickname": data.get("creator_nickname"),
        "creator_username": data.get("creator_username"),
        "creator_avatar_url": data.get("creator_avatar_url"),
        "privacy_level_options": data.get("privacy_level_options") or [],
        "comment_disabled": bool(data.get("comment_disabled")),
        "duet_disabled": bool(data.get("duet_disabled")),
        "stitch_disabled": bool(data.get("stitch_disabled")),
        "max_video_post_duration_sec": data.get("max_video_post_duration_sec"),
    }


def disconnect(account_id: str, revoke: bool = True) -> dict:
    from backend.models.social import SocialAccount, SocialPublishJob
    with session() as db:
        acct = db.get(SocialAccount, account_id)
        if acct is None:
            raise FileNotFoundError("account not found")
        revoked: Any = False
        adapter = get_adapter(acct.platform)
        if revoke and not adapter.supports_revoke:
            revoked = "unsupported"
        elif revoke:
            try:
                adapter.revoke(TokenBundle.from_dict(crypto.decrypt_json(acct.token_encrypted)))
                revoked = True
            except Exception as exc:  # noqa: BLE001 - disconnect must still succeed locally
                logger.warning("token revoke failed for %s: %s", acct.platform, type(exc).__name__)
        cancelled = (db.query(SocialPublishJob)
                     .filter(SocialPublishJob.account_id == account_id, SocialPublishJob.status.in_(("draft", "queued")))
                     .update({"status": "cancelled", "error": "account disconnected"}, synchronize_session=False))
        db.delete(acct)
        db.commit()
    settings = get_settings()
    settings["targets"] = [t for t in settings["targets"] if t.get("account_id") != account_id]
    save_settings({"targets": settings["targets"]})
    return {"ok": True, "revoked": revoked, "cancelled_jobs": cancelled}


def fresh_token(acct) -> TokenBundle:
    """Decrypt the token and refresh it if it expires within 10 minutes (Instagram: within 7 days)."""
    token = TokenBundle.from_dict(crypto.decrypt_json(acct.token_encrypted))
    window = 7 * 86400 if acct.platform == "instagram" else 600
    if token.expires_within(window):
        try:
            token = get_adapter(acct.platform).refresh(token)
        except AdapterError as exc:
            if acct.platform == "instagram" and not token.expires_within(60):
                return token  # still valid; retry refresh next time
            acct.status = "needs_reconnect" if exc.needs_reconnect else acct.status
            acct.last_error = str(exc)[:500]
            raise
        acct.token_encrypted = crypto.encrypt_json(token.to_dict())
        acct.access_expires_at = datetime.utcfromtimestamp(token.expires_at) if token.expires_at else None
        acct.status, acct.last_error = "active", None
    return token


def refresh_expiring_tokens() -> int:
    from backend.models.social import SocialAccount
    count = 0
    with session() as db:
        for acct in db.query(SocialAccount).filter(SocialAccount.status == "active"):
            try:
                fresh_token(acct)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("token refresh failed for %s account: %s", acct.platform, type(exc).__name__)
            db.commit()
    return count


# -------------------------------------------------------------------- SEO ---
def generate_copy(clip_id: str, targets: list[str]) -> dict:
    s = get_settings()
    row = media.get_clip(clip_id)
    ctx = media.clip_context(row, s.get("brand_hashtags"), s.get("language") or "")
    result = seo.generate(ctx, targets)
    result["clip"] = {"id": row["id"], "project_id": row["project_id"], "title": row["title"],
                      "duration_sec": ctx.duration_sec, "transcript_cues": len(ctx.segments)}
    return result


# ------------------------------------------------------------------- jobs ---
def create_jobs(clip_id: str, targets: list[dict], *, captions: Optional[dict] = None, scheduled_at: Any = None,
                options: Optional[dict] = None, source: str = "manual", draft: bool = False) -> list[dict]:
    """targets: [{"account_id", "target"}]. captions: {target: caption-dict} (generated when missing)."""
    from backend.models.social import SocialAccount, SocialPublishJob
    row = media.get_clip(clip_id)
    settings = get_settings()
    when = parse_when(scheduled_at)
    captions = dict(captions or {})
    reports: dict[str, Any] = {}
    missing = sorted({t["target"] for t in targets if t["target"] not in captions})
    if missing:
        gen = generate_copy(clip_id, missing)
        for t, v in gen["targets"].items():
            captions[t] = v["caption"]
            reports[t] = {**v["report"], "source": gen["source"], "model": gen.get("model")}
    created = []
    with session() as db:
        for t in targets:
            target = t.get("target")
            if target not in config.TARGETS:
                raise ValueError(f"unknown target {target}")
            acct = db.get(SocialAccount, t.get("account_id"))
            if acct is None or config.TARGET_PLATFORM[target] != acct.platform:
                raise ValueError(f"account {t.get('account_id')} cannot post as {target}")
            base_opts = dict(settings["defaults"].get(target, {}) or {})
            user_opts = dict((options or {}).get(target, {}) or {})
            if target == "tiktok":
                # Never inherit a saved default privacy — the user must choose per post.
                base_opts.pop("privacy_level", None)
                for flag in ("disable_comment", "disable_duet", "disable_stitch"):
                    base_opts.setdefault(flag, True)
            opts = {**base_opts, **user_opts, "burn_subtitles": settings.get("burn_subtitles", True)}
            # TikTok Direct Post requires per-post UX (privacy, consent). Auto jobs stay drafts
            # until the user completes the Post to TikTok form, even if require_approval is off.
            force_draft = draft or (source == "auto" and target == "tiktok")
            job = SocialPublishJob(
                project_id=row["project_id"], clip_id=clip_id, account_id=acct.id, target=target,
                status="draft" if force_draft else "queued", source=source, scheduled_at=when, next_attempt_at=when,
                caption=captions[target], seo_report=reports.get(target),
                options=opts,
                attempts=0, max_attempts=4, history=[])
            _history(job, "created", source=source, status=job.status)
            db.add(job)
            db.commit()
            created.append(job.public())
    return created


def list_jobs(status: Optional[str] = None, limit: int = 200) -> list[dict]:
    from backend.models.social import SocialPublishJob
    with session() as db:
        q = db.query(SocialPublishJob)
        if status:
            q = q.filter(SocialPublishJob.status.in_(status.split(",")))
        return [j.public() for j in q.order_by(SocialPublishJob.created_at.desc()).limit(limit)]


def update_job(job_id: str, patch: dict) -> dict:
    from backend.models.social import SocialPublishJob
    with session() as db:
        job = db.get(SocialPublishJob, job_id)
        if job is None:
            raise FileNotFoundError("job not found")
        action = patch.get("action")
        if "options" in patch and isinstance(patch["options"], dict):
            job.options = {**(job.options or {}), **patch["options"]}
        if action in ("approve", "publish_now", "retry") and job.target == "tiktok":
            from .adapters.tiktok import options_block_reason
            blocked = options_block_reason(job.options or {})
            if blocked:
                raise ValueError(blocked)
        if action == "approve":
            if job.status != "draft":
                raise ValueError("only drafts can be approved")
            job.status = "queued"
        elif action == "retry":
            if job.status not in ("failed", "cancelled"):
                raise ValueError("only failed or cancelled jobs can be retried")
            job.status, job.attempts, job.error, job.next_attempt_at = "queued", 0, None, None
            job.remote_container_id = None
        elif action == "cancel":
            if job.status in ("published",) or job.status in ("uploading", "processing"):
                raise ValueError(f"cannot cancel a job that is {job.status}")
            job.status = "cancelled"
        elif action == "publish_now":
            if job.status not in ("draft", "queued", "failed"):
                raise ValueError(f"cannot publish a job that is {job.status}")
            job.status, job.scheduled_at, job.next_attempt_at, job.error = "queued", None, None, None
        if "caption" in patch:
            if job.status not in EDITABLE:
                raise ValueError("caption can only be edited before upload")
            cap = dict(job.caption or {})
            cap.update({k: v for k, v in (patch["caption"] or {}).items()
                        if k in ("title", "caption", "description", "hashtags", "tags", "chapters")})
            if job.target in ("tiktok", "instagram") and "caption" in patch["caption"]:
                cap["description"] = cap["caption"]
            if job.target.startswith("youtube") and "description" in patch["caption"]:
                cap["caption"] = cap["description"]
            job.caption = cap
            job.seo_report = {**seo.score(job.target, cap, seo.ClipContext(
                clip_id=job.clip_id, title=cap.get("title", ""), duration_sec=float((job.seo_report or {}).get("duration_sec") or 0)
                or _clip_duration(job.clip_id))), "source": "edited"}
        if "scheduled_at" in patch:
            when = parse_when(patch["scheduled_at"])
            job.scheduled_at = when
            job.next_attempt_at = when
        _history(job, "updated", action=action)
        db.commit()
        return job.public()


def _clip_duration(clip_id: str) -> float:
    try:
        return float(media.get_clip(clip_id).get("duration") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def delete_job(job_id: str) -> None:
    from backend.models.social import SocialPublishJob
    with session() as db:
        job = db.get(SocialPublishJob, job_id)
        if job is None:
            raise FileNotFoundError("job not found")
        if job.status in ("uploading", "processing", "rendering"):
            raise ValueError("job is in flight")
        db.delete(job)
        db.commit()


def job_video_path(job_id: str) -> Optional[Path]:
    from backend.models.social import SocialPublishJob
    with session() as db:
        job = db.get(SocialPublishJob, job_id)
        if job is None or not job.video_path or job.status not in ACTIVE:
            return None
        path = Path(job.video_path)
        return path if path.is_file() else None


# -------------------------------------------------------------- processing ---
def _claim(db, job) -> bool:
    from backend.models.social import SocialPublishJob
    now = utcnow()
    n = (db.query(SocialPublishJob)
         .filter(SocialPublishJob.id == job.id, or_(SocialPublishJob.locked_until.is_(None), SocialPublishJob.locked_until < now))
         .update({"locked_until": now + LOCK_TTL}, synchronize_session=False))
    db.commit()
    return n == 1


def _fail(job, exc: Exception, acct=None) -> None:
    retryable = isinstance(exc, AdapterError) and exc.retryable
    if isinstance(exc, AdapterError) and exc.needs_reconnect and acct is not None:
        acct.status, acct.last_error = "needs_reconnect", str(exc)[:500]
    job.error = str(exc)[:1000]
    if retryable and job.attempts < job.max_attempts:
        delay = RETRY_BACKOFF_MIN[min(job.attempts - 1, len(RETRY_BACKOFF_MIN) - 1)]
        job.status = "queued"
        job.next_attempt_at = utcnow() + timedelta(minutes=delay)
        job.remote_container_id = None
        _history(job, "retry_scheduled", error=job.error, in_minutes=delay)
    else:
        job.status = "failed"
        _history(job, "failed", error=job.error)


def _context(job, acct, token) -> PublishContext:
    return PublishContext(token=token, account_external_id=acct.external_id, account_username=acct.username,
                          target=job.target, video_path=Path(job.video_path or "."), caption=job.caption or {},
                          options=job.options or {}, duration_sec=float((job.options or {}).get("duration_sec") or 0) or None,
                          remote_id=job.remote_id, remote_container_id=job.remote_container_id,
                          account_metadata=acct.account_metadata or {})


def _processing_expired(job) -> bool:
    started = next((parse_when(h.get("at")) for h in reversed(job.history or []) if h.get("event") == "uploading"), None)
    return bool(started) and started < utcnow() - PROCESSING_TIMEOUT


def process_job(job_id: str) -> dict:
    from backend.models.social import SocialAccount, SocialPublishJob
    with session() as db:
        job = db.get(SocialPublishJob, job_id)
        if job is None or job.status not in ("queued", "processing"):
            return {"skipped": True}
        if not _claim(db, job):
            return {"skipped": True, "reason": "locked"}
        db.refresh(job)
        acct = db.get(SocialAccount, job.account_id)
        try:
            if acct is None:
                raise AdapterError("account was disconnected")
            if acct.status != "active":
                raise AdapterError(f"account needs attention: {acct.status}", needs_reconnect=True)
            token = fresh_token(acct)
            db.commit()
            adapter = get_adapter(acct.platform)
            if job.status == "queued" and job.target == "tiktok":
                from .adapters.tiktok import options_block_reason
                blocked = options_block_reason(job.options or {})
                if blocked:
                    job.status = "draft"
                    job.error = blocked
                    job.next_attempt_at = None
                    _history(job, "needs_tiktok_consent", error=blocked)
                    return job.public()
            if job.status == "processing":
                if _processing_expired(job):
                    raise AdapterError("remote processing did not finish within 2 hours", code="remote_failed")
                res = adapter.poll(_context(job, acct, token))
            else:
                job.attempts += 1
                job.status = "rendering"
                _history(job, "rendering", attempt=job.attempts)
                db.commit()
                pre = str((job.options or {}).get("prerendered_path") or "")
                if pre and job.target == "youtube":
                    # Long-form video rendered by an external tool (tm-agent); only files under data/longform.
                    pre_path = Path(pre).resolve()
                    root = (Path(__file__).resolve().parents[2] / "data" / "longform").resolve()
                    if root not in pre_path.parents or not pre_path.is_file():
                        raise AdapterError(f"prerendered media not found or outside data/longform: {pre}")
                    path, duration, notes = pre_path, float(media.probe(pre_path).get("duration") or 0), ["prerendered long-form"]
                else:
                    row = media.get_clip(job.clip_id)
                    path, duration, notes = media.render_for_target(row, job.target,
                                                                    subtitles=bool((job.options or {}).get("burn_subtitles", True)))
                job.video_path = str(path)
                job.options = {**(job.options or {}), "duration_sec": duration}
                if job.target == "youtube_shorts" and duration > media.SHORT_MAX_SEC + 0.5:
                    raise AdapterError(f"Shorts must be <= 60 s (render is {duration:.0f}s)")
                job.status = "uploading"
                _history(job, "uploading", notes=notes or None)
                db.commit()
                ctx = _context(job, acct, token)
                if acct.platform == "instagram":
                    ctx.public_video_url = media.signed_media_url(job.id)
                res = adapter.publish(ctx)
            job.remote_id = res.remote_id or job.remote_id
            job.remote_container_id = res.remote_container_id or job.remote_container_id
            job.remote_url = res.remote_url or job.remote_url
            job.status = res.status
            job.error = None
            if res.status == "published":
                job.published_at = utcnow()
            elif res.status == "processing":
                job.next_attempt_at = utcnow() + timedelta(seconds=30)
            _history(job, res.status, remote_id=job.remote_id, url=job.remote_url, note=res.note)
        except Exception as exc:  # noqa: BLE001
            logger.warning("publish job %s failed: %s", job_id, str(exc)[:300])
            definitive = isinstance(exc, AdapterError) and (exc.code == "remote_failed" or exc.needs_reconnect)
            if job.status == "processing" and not definitive and not _processing_expired(job):
                # The upload already happened: a transient poll error must never cause a re-upload.
                job.next_attempt_at = utcnow() + timedelta(minutes=2)
                _history(job, "poll_error", error=str(exc)[:300])
            else:
                if job.status in ("rendering", "uploading", "processing"):
                    job.status = "queued"
                _fail(job, exc, acct)
        finally:
            job.locked_until = None
            db.commit()
        return job.public()


def due_job_ids(limit: int = 5) -> list[str]:
    from backend.models.social import SocialPublishJob
    now = utcnow()
    with session() as db:
        q = (db.query(SocialPublishJob.id)
             .filter(SocialPublishJob.status.in_(("queued", "processing")))
             .filter(or_(SocialPublishJob.next_attempt_at.is_(None), SocialPublishJob.next_attempt_at <= now))
             .filter(or_(SocialPublishJob.locked_until.is_(None), SocialPublishJob.locked_until < now))
             .order_by(SocialPublishJob.next_attempt_at.asc().nullsfirst(), SocialPublishJob.created_at.asc())
             .limit(limit))
        return [r[0] for r in q]


def recover_stuck() -> int:
    """Jobs left in rendering/uploading by a crashed worker go back to the queue."""
    from backend.models.social import SocialPublishJob
    with session() as db:
        n = (db.query(SocialPublishJob)
             .filter(SocialPublishJob.status.in_(("rendering", "uploading")),
                     or_(SocialPublishJob.locked_until.is_(None), SocialPublishJob.locked_until < utcnow()))
             .update({"status": "queued", "next_attempt_at": utcnow()}, synchronize_session=False))
        db.commit()
        return n


def auto_publish_scan() -> list[dict]:
    from backend.models.social import SocialAccount, SocialPublishJob
    s = get_settings()
    if not s.get("auto_publish") or not s.get("targets") or not s.get("enabled_since"):
        return []
    since = parse_when(s["enabled_since"])
    rows = media._rows(
        "SELECT id, project_id, score, created_at FROM clips WHERE upper(status) = 'COMPLETED' AND created_at >= :since "
        "ORDER BY created_at ASC LIMIT 100", since=since.strftime("%Y-%m-%d %H:%M:%S"))
    created = []
    for row in rows:
        if float(row.get("score") or 0) < float(s.get("min_score") or 0):
            continue
        with session() as db:
            existing = {(j.account_id, j.target) for j in db.query(SocialPublishJob).filter(SocialPublishJob.clip_id == row["id"])}
            todo = []
            for t in s["targets"]:
                if (t["account_id"], t["target"]) in existing:
                    continue
                acct = db.get(SocialAccount, t["account_id"])
                if acct is None or acct.status != "active":
                    continue
                todo.append(t)
            schedule = {}
            for t in todo:
                last = (db.query(SocialPublishJob.scheduled_at, SocialPublishJob.created_at)
                        .filter(SocialPublishJob.account_id == t["account_id"], SocialPublishJob.source == "auto",
                                SocialPublishJob.status != "cancelled")
                        .order_by(SocialPublishJob.created_at.desc()).first())
                base = utcnow()
                if last:
                    prev = naive(last[0]) or naive(last[1])
                    base = max(base, prev + timedelta(minutes=int(s.get("spacing_minutes") or 0)))
                day_count = (db.query(SocialPublishJob)
                             .filter(SocialPublishJob.account_id == t["account_id"], SocialPublishJob.source == "auto",
                                     SocialPublishJob.created_at >= utcnow() - timedelta(days=1)).count())
                if day_count >= int(s.get("max_per_day") or 6):
                    base = max(base, utcnow() + timedelta(days=1))
                schedule[(t["account_id"], t["target"])] = base
        for t in todo:
            try:
                created += create_jobs(row["id"], [t], scheduled_at=schedule[(t["account_id"], t["target"])],
                                       source="auto", draft=bool(s.get("require_approval")))
            except Exception as exc:  # noqa: BLE001
                logger.warning("auto-publish job creation failed for clip %s: %s", row["id"], exc)
    return created


def tick() -> dict:
    """One scheduler pass: recover, auto-queue new clips, then process due jobs."""
    ensure_tables()
    recovered = recover_stuck()
    created = auto_publish_scan()
    processed = []
    for job_id in due_job_ids():
        processed.append({"id": job_id, "status": process_job(job_id).get("status")})
    return {"recovered": recovered, "auto_created": len(created), "processed": processed}
