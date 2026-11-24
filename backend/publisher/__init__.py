"""Native social auto-publisher for AutoClip.

Replaces the Upload-Post / Bilibili-cookie flow with first-party OAuth adapters
(TikTok Content Posting API, Instagram Graph API Reels, YouTube Data API v3),
an encrypted token store, an SEO caption engine and a Celery-driven publish queue.
Only official APIs are used: no password automation, scraping or bot-protection bypass.
"""
