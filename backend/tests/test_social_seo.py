"""SEO engine: per-platform limits, hashtag mix, keyword front-loading, #Shorts, tags, chapters."""
from backend.publisher import seo

TRANSCRIPT = [
    (0.0, 8.0, "Sam Altman explains why OpenAI over-diversified into too many products."),
    (8.0, 20.0, "The company refocused on pre-training and core model capability."),
    (20.0, 40.0, "He says execution and course correction matter more than strategy decks."),
    (40.0, 70.0, "Leadership lessons: admit mistakes early, refocus the team, ship faster."),
    (70.0, 95.0, "The next twelve months will decide which AI labs keep up."),
]


def ctx(duration=95.0, **kw):
    return seo.ClipContext(clip_id="c1", title="We over-diversified, then refocused: how OpenAI fixed its strategy",
                           duration_sec=duration, transcript=" ".join(t for *_x, t in TRANSCRIPT),
                           segments=TRANSCRIPT, outline="OpenAI strategy reset", reason="CEO accountability", **kw)


LLM_DRAFT = {
    "primary_keyword": "OpenAI strategy",
    "secondary_keywords": ["Sam Altman", "AI leadership", "pre-training", "course correction"],
    "hook": "OpenAI strategy reset: Sam Altman admits they over-diversified",
    "summary": "Sam Altman explains how OpenAI refocused on pre-training and core capability after spreading too thin.",
    "cta": "Was refocusing the right call?",
    "youtube_title": "OpenAI Strategy: Sam Altman on Over-Diversifying <and> Refocusing",
    "shorts_title": "OpenAI strategy: the mistake Sam Altman admits",
    "broad_hashtags": ["AI", "tech news", "startups"],
    "niche_hashtags": ["Sam Altman", "OpenAI", "AI leadership", "pre-training"],
    "youtube_tags": ["OpenAI strategy", "Sam Altman interview", "AI leadership", "OpenAI", "pre-training",
                     "AI news", "tech podcast"] + [f"tag number {i}" for i in range(60)],
    "chapters": [{"start": 3, "title": "Over-diversified"}, {"start": 20, "title": "Execution beats decks"},
                 {"start": 25, "title": "too close, dropped"}, {"start": 40, "title": "Leadership lessons"},
                 {"start": 90, "title": "too late, dropped"}],
}


def fake_llm(prompt):
    assert "TRANSCRIPT" in prompt and "[20s]" in prompt
    return dict(LLM_DRAFT), "fake-model"


def test_generate_all_targets_with_llm():
    res = seo.generate(ctx(), list(seo.RULES), llm=fake_llm)
    assert res["source"] == "llm" and res["model"] == "fake-model"
    tt = res["targets"]["tiktok"]["caption"]
    assert seo.utf16_len(tt["caption"]) <= 2200
    assert "openai strategy" in tt["caption"][:100].lower()
    assert 3 <= len(tt["hashtags"]) <= 5
    ig = res["targets"]["instagram"]["caption"]
    assert len(ig["hashtags"]) <= 30 and ig["caption"].count("#") == len(ig["hashtags"])
    sh = res["targets"]["youtube_shorts"]["caption"]
    assert len(sh["title"]) <= 100 and "#shorts" in sh["title"].lower()
    assert sh["hashtags"][0] == "Shorts"
    yt = res["targets"]["youtube"]["caption"]
    assert "<" not in yt["title"] and ">" not in yt["title"]
    assert len(yt["title"]) <= 100 and yt["title"].lower().startswith("openai strategy")
    assert [c["start"] for c in yt["chapters"]] == [0, 20, 40]
    assert "0:00 Over-diversified" in yt["description"]
    used = sum(len(t) + (2 if " " in t else 0) for t in yt["tags"]) + len(yt["tags"]) - 1
    assert used <= 500 and yt["tags"][0] == "OpenAI strategy"
    assert len(yt["description"].encode()) <= 5000
    for target in seo.RULES:
        assert res["targets"][target]["report"]["score"] >= 70, (target, res["targets"][target]["report"])


def test_heuristic_fallback_when_llm_fails():
    def broken(prompt):
        raise RuntimeError("offline")
    res = seo.generate(ctx(), ["tiktok", "youtube"], llm=broken)
    assert res["source"] == "heuristic" and "offline" in res["error"]
    assert res["targets"]["tiktok"]["caption"]["caption"]
    assert res["targets"]["youtube"]["caption"]["tags"]


def test_hashtag_normalization_and_dedupe():
    assert seo.normalize_hashtag("#machine learning!") == "MachineLearning"
    assert seo.normalize_hashtag("人工智能") == "人工智能"
    assert seo.normalize_hashtag("!!!") == ""
    assert seo.hashtag_list(["AI", "ai", "#AI", "Tech"], 5) == ["AI", "Tech"]


def test_chapters_rules():
    assert seo.valid_chapters([{"start": 0, "title": "a"}, {"start": 5, "title": "b"}], 100) == []
    out = seo.valid_chapters([{"start": 2, "title": "a"}, {"start": 30, "title": "b"}, {"start": 60, "title": "c"}], 100)
    assert out[0]["start"] == 0 and len(out) == 3


def test_fit_and_front_load():
    assert seo.fit("Hello world again. Second sentence", 22) == "Hello world again."
    assert seo.fit("alpha beta gamma delta", 15) == "alpha beta"
    assert seo._front_load("Great talk", "AI safety", 40, 100) == "AI safety: Great talk"
    assert seo._front_load("AI safety talk", "AI safety", 40, 100) == "AI safety talk"


def test_language_detection_and_cjk_keywords():
    assert seo.detect_language("这是一个关于人工智能的访谈") == "zh"
    kws = seo.extract_keywords("人工智能改变世界，人工智能的未来", "zh")
    assert any("人工智能" in k for k in kws)


def test_parse_json_object_tolerates_fences():
    assert seo.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
