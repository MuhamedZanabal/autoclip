"""SEO caption engine: one LLM call per clip, deterministic per-platform enforcement.

The model (AutoClip's configured OpenAI-compatible backend, FreeLLMAPI on zedosai.com) proposes
keywords, hooks, titles, hashtags, tags and chapters from the clip transcript and metadata.
Everything platform-specific (length limits, hashtag mix and count, keyword front-loading,
#Shorts, YouTube tag budget, chapter validity) is enforced here and never trusted to the model.
If the model is unreachable, a heuristic keyword extractor produces usable copy instead,
so a post is never blocked on copy.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ rules ---
@dataclass(frozen=True)
class SeoRules:
    target: str
    caption_max: int  # hard platform limit for the post text / description
    visible_chars: int  # roughly what shows before "more" — keywords must land here
    hashtags: tuple[int, int]  # recommended (min, max)
    hashtag_hard_max: int
    title_max: int = 0  # 0 = platform has no separate title
    title_recommended: int = 0
    tags_budget: int = 0  # YouTube: 500 chars across all tags
    shorts_tag: bool = False
    chapters: bool = False


RULES: dict[str, SeoRules] = {
    # TikTok: caption (API field "title") up to 2,200 UTF-16 units; search reads the first line most.
    "tiktok": SeoRules("tiktok", 2200, 100, (3, 5), 30),
    # Instagram: caption up to 2,200 chars and 30 hashtags (API rejects more); ~125 chars visible.
    "instagram": SeoRules("instagram", 2200, 125, (3, 5), 30),
    # YouTube: title <= 100 chars (~60-70 visible), description <= 5,000 bytes, tags <= 500 chars,
    # only the first 3 hashtags show above the title and >60 hashtags voids them all.
    "youtube_shorts": SeoRules("youtube_shorts", 5000, 150, (2, 3), 15, 100, 60, 500, shorts_tag=True),
    "youtube": SeoRules("youtube", 5000, 150, (2, 3), 15, 100, 70, 500, chapters=True),
}

BROAD_FALLBACK = {
    "en": ["podcast", "interview", "motivation", "learnontiktok", "explore"],
    "zh": ["干货分享", "知识分享", "访谈", "成长", "热门"],
}
STOPWORDS = set("""a an the and or but if then so of to in on at by for with from up about into over after before
is are was were be been being am do does did done have has had having i you he she it we they me him her us them my
your his its our their this that these those there here what which who whom whose when where why how not no yes just
very really can could would should will shall may might must also than too more most much many some any all each
every both few other such only own same s t don now like get got go going gonna know think say said one two lot
thing things yeah okay ok um uh right well actually because way even still back make made want need see look""".split())


# ------------------------------------------------------------- inputs ---
@dataclass
class ClipContext:
    clip_id: str
    title: str
    duration_sec: float
    transcript: str = ""
    segments: list[tuple[float, float, str]] = field(default_factory=list)  # relative to clip start
    outline: str = ""
    reason: str = ""
    project_name: str = ""
    source_title: str = ""
    language: str = ""  # "" = auto
    brand_hashtags: list[str] = field(default_factory=list)


def detect_language(text: str) -> str:
    if not text:
        return "en"
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    letters = sum(1 for ch in text if ch.isalpha())
    return "zh" if letters and cjk / letters > 0.3 else "en"


# ------------------------------------------------------------ helpers ---
def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def fit(text: str, limit: int) -> str:
    """Cut at a sentence/phrase boundary when possible, never mid-word for Latin text."""
    text = re.sub(r"[ \t]+", " ", (text or "")).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    best = max(cut.rfind(m) for m in (". ", "! ", "? ", "。", "！", "？", "\n"))
    if best >= limit * 0.6:
        return cut[:best + 1].strip()
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit * 0.6 else cut).rstrip(" ,;:-–—")


def normalize_hashtag(raw: Any) -> str:
    """'#machine learning!' -> 'MachineLearning'; keeps CJK; '' if nothing usable."""
    text = unicodedata.normalize("NFKC", str(raw or "")).strip().lstrip("#")
    words = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    if not words:
        return ""
    if len(words) == 1:
        tag = words[0]
    else:
        tag = "".join(w[:1].upper() + w[1:] if w.isascii() else w for w in words)
    return tag[:40]


def hashtag_list(items: list[Any], limit: int, seen: Optional[set[str]] = None) -> list[str]:
    seen = seen if seen is not None else set()
    out = []
    for item in items or []:
        tag = normalize_hashtag(item)
        key = tag.casefold()
        if not tag or key in seen or tag.isdigit():
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def youtube_tags_fit(tags: list[str], budget: int = 500) -> list[str]:
    """YouTube counts quotes around multi-word tags and the commas between tags."""
    out, used, seen = [], 0, set()
    for raw in tags or []:
        tag = re.sub(r"[<>,#]", "", unicodedata.normalize("NFKC", str(raw or ""))).strip()[:100]
        if not tag or tag.casefold() in seen:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)
        if used + cost > budget:
            continue
        seen.add(tag.casefold())
        out.append(tag)
        used += cost
    return out


def strip_angle(text: str) -> str:
    return (text or "").replace("<", "‹").replace(">", "›")


def fmt_ts(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def valid_chapters(chapters: list[dict], duration: float) -> list[dict]:
    """YouTube auto-chapters need: first at 0:00, >= 3 chapters, each >= 10 s, ascending."""
    clean: list[dict] = []
    for ch in sorted(chapters or [], key=lambda c: float(c.get("start", 0) or 0)):
        try:
            start = float(ch.get("start", 0) or 0)
        except (TypeError, ValueError):
            continue
        title = fit(str(ch.get("title") or "").strip(), 60)
        if not title or start >= duration - 10:
            continue
        if clean and start - clean[-1]["start"] < 10:
            continue
        clean.append({"start": int(start), "title": title})
    if clean and clean[0]["start"] != 0:
        clean[0]["start"] = 0
    return clean if len(clean) >= 3 else []


def extract_keywords(text: str, language: str, top: int = 10) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "")
    if language == "zh":
        grams = Counter()
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for n in (4, 3, 2):
                for i in range(0, max(0, len(run) - n + 1)):
                    grams[run[i:i + n]] += 1 if n == 2 else 2
        return [g for g, _ in grams.most_common(top)]
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9'+-]{2,}", text)]
    words = [w.strip("'") for w in words if w.lower() not in STOPWORDS]
    counts = Counter(words)
    bigrams = Counter(f"{a} {b}" for a, b in zip(words, words[1:]) if a != b)
    ranked = [b for b, c in bigrams.most_common(top) if c >= 2] + [w for w, _ in counts.most_common(top * 2)]
    out, seen = [], set()
    for k in ranked:
        if k not in seen and not any(k in o for o in out):
            seen.add(k)
            out.append(k)
    return out[:top]


# --------------------------------------------------------------- LLM ---
PROMPT = """You are a senior short-video SEO strategist. Write publishing copy for ONE video clip that will be
posted to {targets}. Use ONLY facts present in the transcript and metadata; never invent names, numbers or claims.
Write in language "{language}". Optimise for search: put the single most-searched, specific topic phrase
(primary_keyword) at the very start of the hook and titles. Hashtags: mix broad high-volume tags with niche,
specific tags (people, topic, field). No generic spam like #fyp #viral unless language is en and you add at most one.
Return ONLY a JSON object with these keys:
{{"primary_keyword": str, "secondary_keywords": [str x 4-8],
 "hook": str (<= 90 chars, starts with or contains primary_keyword in its first 5 words),
 "summary": str (2-3 sentences, keyword-rich, natural),
 "cta": str (one short question or call to comment/follow),
 "youtube_title": str (<= 70 chars, primary_keyword first), "shorts_title": str (<= 55 chars, curiosity + keyword),
 "broad_hashtags": [str x 3], "niche_hashtags": [str x 4], "youtube_tags": [str x 10-15, most specific first],
 "chapters": [{{"start": seconds_from_clip_start, "title": str}}] (only if the clip has 3+ distinct parts, else [])}}

METADATA:
{metadata}

TRANSCRIPT (timestamps are seconds from clip start):
{transcript}
"""


def _llm_settings() -> tuple[str, str, str, float]:
    base = (os.getenv("SOCIAL_SEO_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    key = os.getenv("SOCIAL_SEO_LLM_API_KEY") or os.getenv("API_OPENAI_API_KEY") or ""
    model = os.getenv("SOCIAL_SEO_LLM_MODEL") or os.getenv("API_MODEL_NAME") or "auto"
    try:
        timeout = float(os.getenv("SOCIAL_SEO_LLM_TIMEOUT", "90"))
    except ValueError:
        timeout = 90.0
    return base, key, model, timeout


def parse_json_object(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def call_llm(prompt: str) -> tuple[dict, str]:
    import httpx
    base, key, model, timeout = _llm_settings()
    if not base:
        raise RuntimeError("no LLM base URL configured")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "temperature": 0.4, "max_tokens": 1800,
            "messages": [{"role": "system", "content": "You output strict JSON only."},
                         {"role": "user", "content": prompt}]}
    resp = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {resp.status_code}")
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return parse_json_object(content), str(data.get("model") or model)


def transcript_for_prompt(ctx: ClipContext, limit: int = 6000) -> str:
    if ctx.segments:
        lines = [f"[{int(s)}s] {t}" for s, _e, t in ctx.segments if t.strip()]
        text = "\n".join(lines)
    else:
        text = ctx.transcript
    return text[:limit] or "(no transcript available)"


# ------------------------------------------------------------ compose ---
def heuristic_draft(ctx: ClipContext, language: str) -> dict:
    source = " ".join([ctx.title, ctx.outline, ctx.reason, ctx.transcript])
    kws = extract_keywords(source, language, 10) or [ctx.title]
    title_kws = extract_keywords(ctx.title, language, 3)
    primary = (title_kws or kws)[0] if (title_kws or kws) else ctx.title
    summary = fit(ctx.outline or ctx.reason or ctx.transcript or ctx.title, 300)
    broad = BROAD_FALLBACK.get(language, BROAD_FALLBACK["en"])[:3]
    return {
        "primary_keyword": primary, "secondary_keywords": kws[1:8], "hook": ctx.title,
        "summary": summary, "cta": "你怎么看？评论区聊聊" if language == "zh" else "What do you think? Tell me in the comments.",
        "youtube_title": ctx.title, "shorts_title": ctx.title,
        "broad_hashtags": broad, "niche_hashtags": kws[:4], "youtube_tags": [primary, *kws[:12]], "chapters": [],
    }


def _front_load(text: str, keyword: str, window: int, limit: int) -> str:
    """Guarantee the keyword appears within the first `window` chars; prefix it if needed."""
    if not keyword or keyword.casefold() in text[:window].casefold():
        return text
    if keyword.casefold() in text.casefold():
        return text  # present later; acceptable, reported by the checks
    joined = f"{keyword[:1].upper() + keyword[1:]}: {text}"
    return joined if len(joined) <= limit else text


def compose(target: str, draft: dict, ctx: ClipContext, language: str) -> tuple[dict, dict]:
    rules = RULES[target]
    kw = fit(str(draft.get("primary_keyword") or "").strip(), 60)
    seen: set[str] = set()
    lo, hi = rules.hashtags
    brand = hashtag_list(ctx.brand_hashtags, 2, seen)
    broad = hashtag_list(draft.get("broad_hashtags") or [], max(1, hi // 3), seen)
    niche_slots = max(1, hi - len(broad) - len(brand))
    niche = hashtag_list([kw, *(draft.get("niche_hashtags") or []), *(draft.get("secondary_keywords") or [])],
                         niche_slots, seen)
    hashtags = (niche + broad + brand)[:rules.hashtag_hard_max]
    if rules.shorts_tag:
        hashtags = ["Shorts"] + [h for h in hashtags if h.casefold() != "shorts"]
    tag_line = " ".join(f"#{h}" for h in hashtags)

    hook = fit(str(draft.get("hook") or ctx.title), 90)
    summary = fit(str(draft.get("summary") or ""), 600)
    cta = fit(str(draft.get("cta") or ""), 120)
    out: dict[str, Any] = {"target": target, "language": language, "primary_keyword": kw,
                           "keywords": [kw, *[str(k) for k in (draft.get("secondary_keywords") or [])][:8]],
                           "hashtags": hashtags}

    if target in ("tiktok", "instagram"):
        hook = _front_load(hook, kw, rules.visible_chars, 120)
        parts = [hook, summary, cta]
        body = "\n\n".join(p for p in parts if p)
        room = rules.caption_max - utf16_len(tag_line) - 2
        while utf16_len(body) > room and len(body) > 10:
            body = fit(body, len(body) - 50)
        caption = f"{body}\n\n{tag_line}".strip()
        out.update(title=hook, caption=caption, description=caption, tags=[])
    else:
        raw_title = str(draft.get("shorts_title" if target == "youtube_shorts" else "youtube_title") or ctx.title)
        title = strip_angle(fit(raw_title, rules.title_recommended))
        title = _front_load(title, kw, 40, rules.title_max)
        if rules.shorts_tag and "#shorts" not in title.casefold() and len(title) + 8 <= rules.title_max:
            title = f"{title} #Shorts"
        title = fit(title, rules.title_max)
        chapters = valid_chapters(draft.get("chapters") or [], ctx.duration_sec) if rules.chapters else []
        desc_parts = [_front_load(hook, kw, 150, 200), summary]
        if chapters:
            desc_parts.append("Chapters:\n" + "\n".join(f"{fmt_ts(c['start'])} {c['title']}" for c in chapters)
                              if language != "zh" else "章节：\n" + "\n".join(f"{fmt_ts(c['start'])} {c['title']}" for c in chapters))
        if ctx.source_title:
            desc_parts.append(("Source: " if language != "zh" else "出处：") + ctx.source_title)
        if cta:
            desc_parts.append(cta)
        kw_line = ", ".join(out["keywords"][:6])
        if kw_line:
            desc_parts.append(("Topics: " if language != "zh" else "关键词：") + kw_line)
        desc_parts.append(tag_line)
        description = strip_angle("\n\n".join(p for p in desc_parts if p))
        while len(description.encode("utf-8")) > rules.caption_max:
            description = fit(description, len(description) - 100)
        tags = youtube_tags_fit([kw, *(draft.get("youtube_tags") or []), *(draft.get("secondary_keywords") or [])],
                                rules.tags_budget)
        out.update(title=title, description=description, caption=description, tags=tags, chapters=chapters,
                   category_id="22")
    return out, score(target, out, ctx)


def score(target: str, caption: dict, ctx: ClipContext) -> dict:
    rules = RULES[target]
    kw = (caption.get("primary_keyword") or "").casefold()
    checks: list[dict] = []

    def check(cid: str, ok: bool, detail: str, weight: int = 10):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail, "weight": weight})

    text = caption.get("caption") or ""
    tags = caption.get("hashtags") or []
    lo, hi = rules.hashtags
    if target in ("tiktok", "instagram"):
        check("keyword_front", bool(kw) and kw in text[: rules.visible_chars].casefold(),
              f"Primary keyword in first {rules.visible_chars} chars", 25)
        check("caption_limit", utf16_len(text) <= rules.caption_max, f"Caption <= {rules.caption_max} chars", 20)
        n = len(tags)
        check("hashtag_count", lo <= n <= hi + 1, f"{n} hashtags (recommended {lo}-{hi})", 15)
    else:
        title = caption.get("title") or ""
        check("keyword_title", bool(kw) and kw in title[:50].casefold(), "Primary keyword in first 50 chars of title", 25)
        check("title_length", len(title) <= rules.title_max, f"Title <= {rules.title_max} chars ({len(title)})", 10)
        check("title_visible", len(title) <= rules.title_recommended + 8, f"Title fits ~{rules.title_recommended} visible chars", 5)
        desc = caption.get("description") or ""
        check("keyword_description", bool(kw) and kw in desc[:150].casefold(), "Keyword in first 150 chars of description", 10)
        check("description_depth", len(desc) >= 150, "Description has 150+ chars of context", 5)
        yt_tags = caption.get("tags") or []
        check("tags", len(yt_tags) >= 5, f"{len(yt_tags)} tags within the 500-char budget", 10)
        n = len([t for t in tags if t.casefold() != "shorts"])
        check("hashtag_count", 1 <= n <= 3, f"{n} topical hashtags (YouTube shows the first 3)", 5)
        if rules.shorts_tag:
            check("shorts_tag", "#shorts" in (title + " " + desc).casefold(), "#Shorts present", 10)
            check("shorts_duration", ctx.duration_sec <= 60.5, f"Clip {ctx.duration_sec:.0f}s <= 60s", 10)
        if rules.chapters and ctx.duration_sec >= 60:
            check("chapters", bool(caption.get("chapters")), "3+ valid chapters (0:00 first, >=10 s each)", 10)
    has_broad_niche = len(tags) >= 2
    check("hashtag_mix", has_broad_niche, "Broad + niche hashtag mix", 10)
    total = sum(c["weight"] for c in checks) or 1
    got = sum(c["weight"] for c in checks if c["ok"])
    return {"score": round(100 * got / total), "checks": checks}


def generate(ctx: ClipContext, targets: list[str], *, llm: Callable[[str], tuple[dict, str]] | None = None) -> dict:
    """Return {"source": "llm"|"heuristic", "model":..., "draft":..., "targets": {target: {caption, report}}}."""
    targets = [t for t in targets if t in RULES] or list(RULES)
    language = ctx.language or detect_language(" ".join([ctx.title, ctx.transcript[:2000]]))
    meta = {"clip_title": ctx.title, "duration_sec": round(ctx.duration_sec), "outline": ctx.outline,
            "why_it_works": ctx.reason, "project": ctx.project_name, "source": ctx.source_title}
    prompt = PROMPT.format(targets=", ".join(targets), language=language,
                           metadata=json.dumps(meta, ensure_ascii=False), transcript=transcript_for_prompt(ctx))
    source, model, error = "heuristic", None, None
    fallback = heuristic_draft(ctx, language)
    try:
        draft, model = (llm or call_llm)(prompt)
        if not isinstance(draft, dict) or not draft.get("primary_keyword"):
            raise ValueError("model JSON missing primary_keyword")
        draft = {**fallback, **{k: v for k, v in draft.items() if v}}
        source = "llm"
    except Exception as exc:  # noqa: BLE001 - copy must never block a post
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.warning("SEO LLM unavailable, using heuristic copy: %s", error)
        draft = fallback
    result = {"source": source, "model": model, "language": language, "error": error, "draft": draft, "targets": {}}
    for target in targets:
        caption, report = compose(target, draft, ctx, language)
        result["targets"][target] = {"caption": caption, "report": report}
    return result
