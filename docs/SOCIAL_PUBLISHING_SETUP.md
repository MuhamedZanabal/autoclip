# Social publishing setup (TikTok, Instagram Reels, YouTube Shorts and YouTube)

AutoClip's native publisher posts finished clips through each platform's **official API**, with the
**official OAuth sign-in** for each account. It never stores passwords, never automates a login form,
never scrapes, and never works around bot protection.

This guide covers the zedosai.com deployment (`https://zedosai.com/autoclip/`). For another domain, replace
`https://zedosai.com/autoclip` with your `AUTOCLIP_PUBLIC_URL`.

| Platform | API | Redirect URI to register (exact) |
|---|---|---|
| TikTok | Content Posting API (Direct Post, `FILE_UPLOAD`) + Login Kit | `https://zedosai.com/autoclip/api/v1/social/oauth/tiktok/callback` |
| Instagram | Instagram API with Instagram Login (Reels via `/media` + `/media_publish`) | `https://zedosai.com/autoclip/api/v1/social/oauth/instagram/callback` |
| YouTube | YouTube Data API v3 `videos.insert` (resumable) | `https://zedosai.com/autoclip/api/v1/social/oauth/youtube/callback` |

The **Publishing → Accounts** tab shows each platform's redirect URI and which `.env` variables are still missing.

---

## 0. Server `.env` (all platforms)

Edit `/opt/autoclip/.env` on the server. It must stay `chmod 600` and must never be committed.

```bash
AUTOCLIP_PUBLIC_URL=https://zedosai.com/autoclip
# Generate once. Changing it later makes stored tokens unreadable, so everyone has to reconnect.
#   docker exec autoclip-app python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SOCIAL_TOKEN_KEY=<generated>

TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_APP_AUDITED=false          # set to true only after TikTok approves the Direct Post audit

INSTAGRAM_LOGIN_MODE=instagram    # or "facebook", see section 2b
INSTAGRAM_APP_ID=                 # the *Instagram* app ID from "API setup with Instagram login"
INSTAGRAM_APP_SECRET=
META_APP_ID=                      # only used when INSTAGRAM_LOGIN_MODE=facebook
META_APP_SECRET=
META_GRAPH_VERSION=v26.0

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_APP_VERIFIED=false         # set to true once the OAuth consent screen is published / verified
```

Apply the changes. Rebuilding is only needed after a code change:

```bash
cd /opt/autoclip && sudo docker compose up -d
```

Optional variables:

- `SOCIAL_MEDIA_URL_TTL`: lifetime in seconds of the signed media links (default 7200).
- `SOCIAL_SEO_LLM_MODEL`: a separate model for caption writing. By default it uses `API_MODEL_NAME` through `OPENAI_BASE_URL` (FreeLLMAPI).
- `TIKTOK_SCOPES`: defaults to `user.info.basic,video.publish`. Add `user.info.profile` if your TikTok app has it approved; that gives full `@username` post links.

---

## 1. TikTok: Content Posting API, Direct Post

1. Go to <https://developers.tiktok.com/> and log in. Open **Manage apps**, then **Connect an app**, and create the app for your organisation or individual account.
2. Fill in the **App details**:
   - Category, description and an icon of 1024×1024 px.
   - A **Terms of Service URL** and a **Privacy Policy URL**. Both must be public pages on your site, for example `https://zedosai.com/terms` and `https://zedosai.com/privacy`.
   - **Platforms: Web**, with the website URL `https://zedosai.com`.
3. Under **Add products**, add **Login Kit** and **Content Posting API**.
   - In Content Posting API, turn on **Direct Post**.
   - AutoClip uses `FILE_UPLOAD`, so you do **not** need URL-prefix or domain verification. That verification is only required for `PULL_FROM_URL`.
4. In **Login Kit → Redirect URI**, add exactly:
   `https://zedosai.com/autoclip/api/v1/social/oauth/tiktok/callback`
5. Under **Scopes**, request `user.info.basic` and `video.publish`. `user.info.profile` is optional.
6. Copy the **Client key** into `TIKTOK_CLIENT_KEY` and the **Client secret** into `TIKTOK_CLIENT_SECRET`.
7. To test before review, use **Sandbox**: add your own TikTok account as a *target user*, then connect it from **Publishing → Accounts**.
8. Submit the app for **review**. Explain that AutoClip uploads the user's own edited clips through Direct Post, and attach a screen recording of the connect → compose → post flow.
9. **The audit (important).** TikTok's docs say: "All content posted by unaudited clients will be restricted to private viewing mode."
   - Until the Content Posting API **audit** passes, keep `TIKTOK_APP_AUDITED=false`. AutoClip then posts every TikTok as `SELF_ONLY` (private) and the queue shows a note saying so.
   - After TikTok approves the audit, set `TIKTOK_APP_AUDITED=true`. The privacy you choose (default `PUBLIC_TO_EVERYONE`) is then used. It is always checked against the creator's `privacy_level_options` from `creator_info/query`.
   - Request the audit in the developer portal under Content Posting API → **Apply for audit / Direct Post review**.
10. TikTok's UX guidelines for audited apps require four things before each post:
    - Show the creator's nickname.
    - Let the user pick privacy and interaction settings.
    - Show the commercial-content toggles.
    - Get explicit consent.

    The Compose tab and the "require my approval" automation mode cover this flow. A fully automatic, no-review TikTok mode may not pass the audit, so keep **Require my approval** on for TikTok if the reviewer asks.

Limits from TikTok's docs:
- Each user token allows 6 `video/init` calls per minute.
- Captions can be up to 2,200 UTF-16 characters.
- TikTok also applies a daily post cap per creator (`spam_risk_too_many_posts`) and a daily cap on active publishing users per client.

---

## 2a. Instagram: Instagram API with Instagram Login (default, no Facebook Page needed)

**Requirements:** an Instagram **Business** or **Creator** (professional) account. A personal account cannot publish through the API. To switch, go to Instagram → Settings → Account type and tools → Switch to professional account.

1. Go to <https://developers.facebook.com/apps/>. Choose **Create app** and pick the use case **"Manage messaging & content on Instagram"**. The app type is **Business**.
2. Open **Instagram → API setup with Instagram business login**.
   - Copy the **Instagram app ID** into `INSTAGRAM_APP_ID` and the **Instagram app secret** into `INSTAGRAM_APP_SECRET`. These are different from the Meta app ID shown at the top of the dashboard.
   - Under **3. Set up Instagram business login → Business login settings → OAuth redirect URIs**, add exactly:
     `https://zedosai.com/autoclip/api/v1/social/oauth/instagram/callback`
   - Permissions used: `instagram_business_basic` and `instagram_business_content_publish`.
3. **App settings → Basic**: add the Privacy Policy URL, the Terms URL, a **User data deletion** URL or instructions, the app icon and a category.
4. **Development mode:** only Instagram accounts with a role on the app can connect.
   - Add yours under **App roles → Roles → Instagram Testers**.
   - Then accept the invite in Instagram under Settings → **Apps and websites → Tester invites**. On some app versions you accept it at <https://www.instagram.com/accounts/manage_access/>.
5. **Going live for other accounts** requires all of the following:
   - **App Review** for **Advanced Access** to `instagram_business_basic` and `instagram_business_content_publish`.
   - **Business Verification** of your Meta business portfolio.
   - A screencast of the connect → publish flow.
   - Switching the app to **Live** mode.

   Connecting only your own accounts works in Development mode with Standard Access.
6. **How publishing works:** Instagram's servers download the Reel from a signed, expiring URL, for example `https://zedosai.com/autoclip/api/v1/social/media/<job>.mp4?exp=…&sig=…`.
   - This path is deliberately **outside** basic auth. Each link only works for its own in-flight job until it expires.
   - The container is checked every 30 s until `FINISHED`, then published with `/media_publish`.

Instagram's limits:
- Captions can be up to 2,200 characters, with no more than 30 hashtags.
- Reels must be 3 s to 15 min long, MP4/H.264 + AAC, with 9:16 recommended.
- Each account has a rolling 24-hour cap on API posts. Check `/<IG_ID>/content_publishing_limit`; it has historically been 50.
- Tokens last 60 days. AutoClip refreshes them automatically 7 days before expiry.

## 2b. Instagram: Facebook Login mode (optional)

Set `INSTAGRAM_LOGIN_MODE=facebook`, `META_APP_ID` and `META_APP_SECRET`. In this mode:

- The IG professional account must be **linked to a Facebook Page** you manage.
- Add the product **Facebook Login for Business**, using the same redirect URI.
- The permissions are `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` and `business_management`. Each needs App Review for Advanced Access.
- A Page that requires Page Publishing Authorization (PPA) must complete it first.
- Facebook long-lived user tokens cannot be refreshed on the server. Reconnect about every 60 days; the account card turns red when that's needed.

---

## 3. YouTube: Data API v3 (Shorts and long-form)

1. Go to <https://console.cloud.google.com/> and create a project, for example "AutoClip Publisher".
2. Open **APIs & Services → Library** and enable **YouTube Data API v3**.
3. Open **Google Auth Platform**, also called the OAuth consent screen.
   - **Branding:** app name, support email, logo, the home page `https://zedosai.com`, Privacy Policy and Terms URLs, and the authorised domain `zedosai.com`.
   - **Audience:** set **External**. While the status is **Testing**, add each Google account that will connect as a **Test user**.
   - **Data access:** add the scopes `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/youtube.readonly`.
4. Open **Clients → Create client** and choose **Web application**.
   - **Authorised redirect URIs:** `https://zedosai.com/autoclip/api/v1/social/oauth/youtube/callback`
   - Copy the client ID into `GOOGLE_CLIENT_ID` and the secret into `GOOGLE_CLIENT_SECRET`.
5. **Testing versus production**:
   - In **Testing**, only listed test users can connect, and Google expires their **refresh tokens after 7 days**. They have to reconnect weekly.
   - Click **Publish app**. Because these scopes are *sensitive*, Google asks for **verification** before showing an unverified-app warning-free screen to more than 100 users. Verification needs:
     - Domain ownership of zedosai.com in Search Console.
     - A privacy policy on that domain.
     - A demo video of the OAuth flow.
     - A written justification for each scope.
   - Once published and verified, set `GOOGLE_APP_VERIFIED=true`.
6. **Upload visibility:** Google *reversed* its July 2020 rule. As of the YouTube Data API revision history, "videos uploaded via the videos.insert endpoint from unverified API projects are no longer restricted to private viewing mode". Public and unlisted uploads work without an audit.
   - The **YouTube API Services audit** (the quota extension form) is only needed to go beyond the default quota of **100 `videos.insert` calls/day** per project. Daily quotas reset at midnight Pacific Time.
   - If YouTube still applies a different privacy than requested, the queue entry shows it.

What AutoClip does for each kind of video:

- **Shorts:**
  - A vertical 1080×1920 render, capped at 60 s. YouTube now accepts up to 3 min, but AutoClip keeps 60 s for the widest reach.
  - `#Shorts` goes in the title (when it fits) and the description.
  - The link format is `https://youtube.com/shorts/<id>`.
- **YouTube (long-form):**
  - The original-aspect clip.
  - Chapters are added automatically when the clip has at least 3 distinct parts: the first at 0:00, each at least 10 s long.
  - Tags stay within YouTube's 500-character budget.
  - Category 22 (People & Blogs) is the default.

---

## 4. Using it

1. Open **AutoClip → Publishing → Accounts** and click **Connect** for each platform. You sign in on the platform's own page, and it redirects back.
2. **Compose:**
   - Pick a clip and the destinations.
   - Click **Generate SEO captions** to get a title, caption, description, hashtags, tags and chapters for each platform, with an SEO score and checklist. Edit anything you like.
   - Then **Publish now**, **Schedule**, or **Save as drafts**.
3. **Automation:**
   - Turn on **Auto-publish** and choose the destination accounts.
   - Every clip that finishes processing after that moment is queued automatically. Older clips are never posted.
   - Optional settings: approval, a minimum clip score, the spacing between posts, a daily cap per account, the caption language, brand hashtags, and default privacy.
4. **Queue:** shows each job's status: draft, queued, rendering, uploading, processing, published, failed or cancelled.
   - Failed uploads are retried after 2, 10, 30 and 90 minutes.
   - Remote processing is polled every 30 s.
   - A transient polling error never re-uploads a video.

## 5. Environment variable reference

| Variable | Required | Purpose |
|---|---|---|
| `AUTOCLIP_PUBLIC_URL` | yes | Base for redirect URIs and signed media links |
| `SOCIAL_TOKEN_KEY` | yes | Fernet key that encrypts OAuth tokens at rest (DB table `social_accounts`) |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | TikTok | From the TikTok developer app |
| `TIKTOK_APP_AUDITED` | TikTok | `true` only after the Direct Post audit; until then posts are `SELF_ONLY` |
| `TIKTOK_SCOPES` | no | Default `user.info.basic,video.publish` |
| `INSTAGRAM_LOGIN_MODE` | no | `instagram` (default) or `facebook` |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` | Instagram (instagram mode) | From "API setup with Instagram business login" |
| `META_APP_ID` / `META_APP_SECRET` | Instagram (facebook mode) | Meta app credentials |
| `META_GRAPH_VERSION` | no | Default `v26.0` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | YouTube | OAuth web client |
| `GOOGLE_APP_VERIFIED` | no | `true` once the consent screen is published or verified (only changes UI hints) |
| `SOCIAL_MEDIA_URL_TTL` | no | Seconds a signed media link stays valid (default 7200) |
| `SOCIAL_SEO_LLM_MODEL` | no | A separate model for caption writing |
| `SOCIAL_SEO_LLM_FALLBACK_MODELS` | no | Models tried in order when the first one fails or returns bad JSON (default `auto`) |

## 6. Security model

- OAuth tokens are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before they reach SQLite. They are never returned by the API or written to logs.
- Request logging redacts every URL query value. That covers the signed links, OAuth codes and states.
- The public OAuth callback only accepts a single-use `state` that is at most 15 minutes old. YouTube also uses PKCE (S256).
- Signed media links are HMAC-SHA256 over the job ID and expiry, using a key derived from `SOCIAL_TOKEN_KEY`. They only resolve while the job is in flight.
- Disconnecting an account revokes the token at the platform (TikTok and Google revoke endpoints), deletes it locally, and cancels pending posts.
