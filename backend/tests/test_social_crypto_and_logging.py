import logging
import time

import pytest

from backend.publisher import config, crypto


def test_token_roundtrip_and_wrong_key(monkeypatch):
    monkeypatch.setenv("SOCIAL_TOKEN_KEY", crypto.generate_key())
    blob = crypto.encrypt_json({"access_token": "secret-value"})
    assert "secret-value" not in blob
    assert crypto.decrypt_json(blob)["access_token"] == "secret-value"
    monkeypatch.setenv("SOCIAL_TOKEN_KEY", crypto.generate_key())
    with pytest.raises(crypto.CryptoNotConfigured):
        crypto.decrypt_json(blob)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("SOCIAL_TOKEN_KEY", raising=False)
    with pytest.raises(crypto.CryptoNotConfigured):
        crypto.encrypt_json({})


def test_signed_url_expiry(monkeypatch):
    monkeypatch.setenv("SOCIAL_TOKEN_KEY", crypto.generate_key())
    exp = int(time.time()) + 60
    sig = crypto.sign("media:j1", exp)
    assert crypto.verify("media:j1", exp, sig)
    assert not crypto.verify("media:j2", exp, sig)
    assert not crypto.verify("media:j1", exp, sig, now=exp + 1)
    assert not crypto.verify("media:j1", exp + 1, sig)


def test_platform_status_reports_missing(monkeypatch):
    for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "SOCIAL_TOKEN_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTOCLIP_PUBLIC_URL", "https://zedosai.com/autoclip/")
    st = config.platform_status("tiktok")
    assert not st["configured"] and "TIKTOK_CLIENT_KEY" in st["missing_env"]
    assert st["redirect_uri"] == "https://zedosai.com/autoclip/api/v1/social/oauth/tiktok/callback"


def test_uvicorn_access_record_keeps_args():
    from backend.core.log_redaction import RedactingFilter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/x?sig=abc", "1.1", 200), None)
    assert RedactingFilter().filter(rec)
    assert len(rec.args) == 5 and "abc" not in rec.args[2]
    assert "[REDACTED]" in rec.getMessage()
