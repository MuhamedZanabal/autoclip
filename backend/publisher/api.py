"""REST API for the native social publisher, mounted at /api/v1/social.

Two routes are reachable without the AutoClip basic-auth (nginx carve-out):
  GET /social/oauth/{platform}/callback — validated by a single-use, 15-minute OAuth state
  GET /social/media/{job_id}.mp4        — validated by an HMAC signature with expiry
Everything else stays behind the existing /autoclip/ basic auth.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import apps, config, media, service
from .adapters import AdapterError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/social", tags=["social-publisher"])


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, AdapterError):
        return HTTPException(409 if exc.code == "not_configured" else 502, str(exc))
    logger.exception("social publisher error")
    return HTTPException(500, f"{type(exc).__name__}: {str(exc)[:300]}")


def _kick() -> None:
    try:
        from .tasks import publisher_tick
        publisher_tick.delay()
    except Exception as exc:  # noqa: BLE001 - beat will pick it up within a minute
        logger.info("could not enqueue immediate tick: %s", type(exc).__name__)


@router.get("/status")
def status():
    service.ensure_tables()
    return {"platforms": [config.platform_status(p) for p in config.PLATFORMS], "targets": list(config.TARGETS),
            "public_url": config.public_base_url(), "token_encryption": bool(config.token_key()),
            "notes": {"tiktok_audited": _flag("TIKTOK_APP_AUDITED"), "google_verified": _flag("GOOGLE_APP_VERIFIED")}}


def _flag(name: str) -> bool:
    import os
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- OAuth ---
@router.get("/oauth/{platform}/start")
def oauth_start(platform: str, redirect: bool = Query(True)):
    if platform not in config.PLATFORMS:
        raise HTTPException(404, "unknown platform")
    try:
        url = service.start_oauth(platform)
    except AdapterError as exc:
        if exc.code == "not_configured":
            return _not_configured(platform, redirect)
        raise _err(exc)
    return RedirectResponse(url, status_code=302) if redirect else {"authorize_url": url}


def _not_configured(platform: str, redirect: bool):
    """Not configured yet: send the browser to the Developer apps screen instead of failing."""
    st = config.platform_status(platform)
    setup_url = config.ui_url(f"/publishing?tab=apps&setup={platform}")
    if not redirect:
        return JSONResponse(status_code=409, content={"configured": False, "setup_url": setup_url, **st})
    return RedirectResponse(setup_url, status_code=302)


@router.get("/oauth/{platform}/callback")
def oauth_callback(platform: str, code: Optional[str] = None, state: Optional[str] = None,
                   error: Optional[str] = None, error_description: Optional[str] = None):
    if platform not in config.PLATFORMS:
        raise HTTPException(404, "unknown platform")
    back = config.ui_url("/publishing")  # the UI reads ?oauth=&result= and opens the Accounts tab
    if error or not code or not state:
        msg = error_description or error or "missing code"
        return RedirectResponse(f"{back}?oauth={platform}&tab=accounts&result=error&message={quote(msg[:200])}", status_code=302)
    try:
        accounts = service.finish_oauth(platform, code, state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OAuth callback for %s failed: %s", platform, str(exc)[:300])
        return RedirectResponse(f"{back}?oauth={platform}&tab=accounts&result=error&message={quote(str(exc)[:200])}", status_code=302)
    return RedirectResponse(f"{back}?oauth={platform}&tab=accounts&result=connected&count={len(accounts)}", status_code=302)


# ---------------------------------------------------- developer apps ---
class AppCredentialsBody(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=256)
    client_secret: Optional[str] = Field(None, max_length=512)  # blank = keep the stored secret


@router.get("/apps")
def list_apps():
    """Masked view of each platform's developer-app credentials. Secrets are never returned."""
    service.ensure_tables()
    return {"apps": [config.platform_status(p) for p in config.PLATFORMS],
            "token_encryption": bool(config.token_key()), "public_url": config.public_base_url()}


@router.put("/apps/{platform}")
def save_app(platform: str, body: AppCredentialsBody):
    if platform not in config.PLATFORMS:
        raise HTTPException(404, "unknown platform")
    try:
        apps.save(platform, body.client_id, body.client_secret)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return config.platform_status(platform)


@router.delete("/apps/{platform}")
def delete_app(platform: str):
    if platform not in config.PLATFORMS:
        raise HTTPException(404, "unknown platform")
    removed = apps.delete(platform)
    return {"removed": removed, **config.platform_status(platform)}


# -------------------------------------------------------------- accounts ---
@router.get("/accounts")
def accounts():
    service.ensure_tables()
    return {"accounts": service.list_accounts()}


@router.delete("/accounts/{account_id}")
def disconnect(account_id: str, revoke: bool = True):
    try:
        return service.disconnect(account_id, revoke)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc)


# -------------------------------------------------------------- settings ---
@router.get("/settings")
def get_settings():
    service.ensure_tables()
    return service.get_settings()


@router.put("/settings")
def put_settings(body: dict[str, Any]):
    try:
        return service.save_settings(body or {})
    except Exception as exc:  # noqa: BLE001
        raise _err(exc)


# ------------------------------------------------------------------ clips ---
@router.get("/clips")
def clips(project_id: Optional[str] = None):
    rows = media.list_clips(project_id)
    for r in rows:
        r["created_at"] = str(r.get("created_at") or "")
        r["status"] = str(r.get("status") or "")
    return {"clips": rows}


class SeoBody(BaseModel):
    clip_id: str
    targets: list[str] = Field(default_factory=lambda: list(config.TARGETS))


@router.post("/seo/generate")
def seo_generate(body: SeoBody):
    try:
        return service.generate_copy(body.clip_id, body.targets)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc)


# ------------------------------------------------------------------- jobs ---
class Target(BaseModel):
    account_id: str
    target: str


class PublishBody(BaseModel):
    clip_id: str
    targets: list[Target]
    captions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scheduled_at: Optional[str] = None
    draft: bool = False


@router.post("/jobs")
def create_jobs(body: PublishBody):
    if not body.targets:
        raise HTTPException(400, "pick at least one account")
    try:
        jobs = service.create_jobs(body.clip_id, [t.model_dump() for t in body.targets], captions=body.captions,
                                   scheduled_at=body.scheduled_at, options=body.options, draft=body.draft)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc)
    if not body.draft and not body.scheduled_at:
        _kick()
    return {"jobs": jobs}


@router.get("/jobs")
def list_jobs(status: Optional[str] = None):
    service.ensure_tables()
    return {"jobs": service.list_jobs(status)}


@router.patch("/jobs/{job_id}")
def update_job(job_id: str, body: dict[str, Any]):
    try:
        job = service.update_job(job_id, body or {})
    except Exception as exc:  # noqa: BLE001
        raise _err(exc)
    if (body or {}).get("action") in ("approve", "retry", "publish_now"):
        _kick()
    return job


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        service.delete_job(job_id)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc)
    return {"ok": True}


@router.post("/tick")
def run_tick():
    _kick()
    return {"ok": True}


# ---------------------------------------------------- public signed media ---
@router.get("/media/{job_id}.mp4")
def signed_media(job_id: str, exp: int = Query(...), sig: str = Query(...)):
    if not media.verify_media(job_id, exp, sig):
        raise HTTPException(403, "invalid or expired link")
    path = service.job_video_path(job_id)
    if path is None:
        raise HTTPException(404, "not available")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=600",
                                                               "X-Robots-Tag": "noindex"})
