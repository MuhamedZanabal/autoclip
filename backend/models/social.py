"""Tables for the native social publisher (accounts, publish jobs, OAuth states, settings)."""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from .base import BaseModel, Base


def iso_utc(value):
    """ISO-8601 in UTC with an explicit Z (SQLite hands datetimes back naive)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        from datetime import timezone
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat() + "Z"


class SocialAccount(BaseModel):
    __tablename__ = "social_accounts"

    platform = Column(String(20), nullable=False, index=True)  # tiktok / instagram / youtube
    external_id = Column(String(128), nullable=False)  # open_id / ig user id / channel id
    display_name = Column(String(255), nullable=True)
    username = Column(String(255), nullable=True)
    avatar_url = Column(Text, nullable=True)
    scopes = Column(Text, nullable=True)
    token_encrypted = Column(Text, nullable=False)  # Fernet(JSON{access_token, refresh_token, ...})
    access_expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="active")  # active / needs_reconnect / disabled
    last_error = Column(Text, nullable=True)
    account_metadata = Column(JSON, nullable=True)

    def public(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "external_id": self.external_id,
            "display_name": self.display_name,
            "username": self.username,
            "avatar_url": self.avatar_url,
            "scopes": (self.scopes or "").split(",") if self.scopes else [],
            "status": self.status,
            "last_error": self.last_error,
            "access_expires_at": iso_utc(self.access_expires_at),
            "metadata": self.account_metadata or {},
            "created_at": iso_utc(self.created_at),
        }


class SocialPublishJob(BaseModel):
    __tablename__ = "social_publish_jobs"

    project_id = Column(String(36), nullable=False, index=True)
    clip_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(36), nullable=False, index=True)
    target = Column(String(20), nullable=False)  # tiktok / instagram / youtube_shorts / youtube
    # draft (awaiting approval) / queued / rendering / uploading / processing / published / failed / cancelled
    status = Column(String(20), nullable=False, default="queued", index=True)
    source = Column(String(20), nullable=False, default="manual")  # manual / auto
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=4)
    caption = Column(JSON, nullable=True)  # {title, description, hashtags, tags, chapters, caption}
    seo_report = Column(JSON, nullable=True)
    options = Column(JSON, nullable=True)  # privacy, disable_comment, share_to_feed, made_for_kids ...
    video_path = Column(Text, nullable=True)
    remote_id = Column(String(255), nullable=True)
    remote_container_id = Column(String(255), nullable=True)
    remote_url = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    history = Column(JSON, nullable=True)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    def public(self) -> dict:
        iso = iso_utc
        return {
            "id": self.id, "project_id": self.project_id, "clip_id": self.clip_id,
            "account_id": self.account_id, "target": self.target, "status": self.status,
            "source": self.source, "scheduled_at": iso(self.scheduled_at),
            "next_attempt_at": iso(self.next_attempt_at), "attempts": self.attempts,
            "max_attempts": self.max_attempts, "caption": self.caption or {},
            "seo_report": self.seo_report or {}, "options": self.options or {},
            "remote_id": self.remote_id, "remote_url": self.remote_url, "error": self.error,
            "history": (self.history or [])[-20:], "published_at": iso(self.published_at),
            "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
        }


class SocialOAuthState(Base):
    __tablename__ = "social_oauth_states"

    state = Column(String(128), primary_key=True)
    platform = Column(String(20), nullable=False)
    code_verifier = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, nullable=False, default=False)


class SocialSetting(Base):
    __tablename__ = "social_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=True)


class SocialAppCredential(Base):
    """Developer-app credentials entered in the UI (Fernet-encrypted). .env values take precedence."""
    __tablename__ = "social_app_credentials"

    platform = Column(String(20), primary_key=True)
    client_id_encrypted = Column(Text, nullable=False)
    client_secret_encrypted = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)
