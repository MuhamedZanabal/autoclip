"""Clip lookup, transcript extraction, per-platform rendering and signed public media URLs."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

from . import config, crypto
from .seo import ClipContext

logger = logging.getLogger(__name__)

SHORT_MAX_SEC = 180.0  # YouTube Shorts allow up to 3 minutes (since Oct 2024)


# ----------------------------------------------------------------- clips ---
def _rows(sql: str, **params) -> list[dict]:
    from backend.core.database import engine
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def list_clips(project_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    sql = ("SELECT c.id, c.project_id, c.title, c.status, c.start_time, c.end_time, c.duration, c.score, "
           "c.video_path, c.created_at, p.name AS project_name FROM clips c LEFT JOIN projects p ON p.id = c.project_id ")
    params: dict[str, Any] = {"limit": limit}
    if project_id:
        sql += "WHERE c.project_id = :pid "
        params["pid"] = project_id
    sql += "ORDER BY c.created_at DESC LIMIT :limit"
    return _rows(sql, **params)


def get_clip(clip_id: str) -> dict:
    rows = _rows("SELECT c.*, p.name AS project_name, p.project_metadata AS project_metadata FROM clips c "
                 "LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = :cid", cid=clip_id)
    if not rows:
        raise FileNotFoundError(f"clip {clip_id} not found")
    return rows[0]


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


def _project_dir(project_id: str) -> Path:
    from backend.core.path_utils import get_project_directory
    return get_project_directory(project_id)


def clip_window(row: dict) -> tuple[float, float]:
    """(start, end) seconds of the clip inside the project's raw input / SRT."""
    try:
        from backend.services.publish_export import load_clip_meta
        from backend.pipeline.quality import to_seconds
        meta = load_clip_meta(row["project_id"], row["id"])
        if meta.get("source_type") != "studio":
            return to_seconds(meta["start_time"]), to_seconds(meta["end_time"])
    except Exception:  # noqa: BLE001 - fall back to DB integers
        pass
    start = float(row.get("start_time") or 0)
    end = float(row.get("end_time") or 0) or start + float(row.get("duration") or 0)
    return start, end


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(content: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", content.replace("\r", "")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        for i, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                body = " ".join(lines[i + 1:]).strip()
                if body:
                    cues.append((start, end, re.sub(r"<[^>]+>", "", body)))
                break
    return cues


def clip_segments(row: dict) -> list[tuple[float, float, str]]:
    try:
        from backend.services.publish_export import find_source_srt
        srt = find_source_srt(row["project_id"])
    except Exception:  # noqa: BLE001
        srt = None
    if not srt or not Path(srt).is_file():
        return []
    start, end = clip_window(row)
    out = []
    for s, e, t in parse_srt(Path(srt).read_text(encoding="utf-8", errors="ignore")):
        if e > start and s < end:
            out.append((max(0.0, s - start), max(0.0, e - start), t))
    return out


def clip_context(row: dict, brand_hashtags: Optional[list[str]] = None, language: str = "") -> ClipContext:
    meta = _json(row.get("clip_metadata"))
    segments = clip_segments(row)
    start, end = clip_window(row)
    origin = {}
    try:
        origin_path = _project_dir(row["project_id"]) / "metadata" / "input_origin.json"
        if origin_path.is_file():
            origin = json.loads(origin_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        origin = {}
    source_title = str(origin.get("title") or origin.get("source_title") or "")[:150]
    return ClipContext(
        clip_id=row["id"], title=str(row.get("title") or meta.get("generated_title") or "Untitled clip"),
        duration_sec=float(row.get("duration") or max(0.0, end - start)),
        transcript=" ".join(t for _s, _e, t in segments)[:8000], segments=segments,
        outline=str(meta.get("outline") or row.get("description") or "")[:600],
        reason=str(row.get("recommendation_reason") or meta.get("recommend_reason") or "")[:400],
        project_name=str(row.get("project_name") or ""), source_title=source_title,
        language=language, brand_hashtags=list(brand_hashtags or []))


# ------------------------------------------------------------- rendering ---
def probe(path: Path) -> dict:
    try:
        from backend.utils.ffmpeg_utils import get_ffprobe_path
        out = subprocess.run([get_ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {"width": stream.get("width"), "height": stream.get("height"),
                "duration": float((data.get("format") or {}).get("duration") or 0)}
    except Exception:  # noqa: BLE001
        return {}


def _ffmpeg_render(src: Path, out: Path, start: float, duration: float, vertical: bool) -> Path:
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    vf = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1" if vertical
          else "scale='min(1920,iw)':-2,setsar=1")
    tmp = out.with_suffix(".part.mp4")
    cmd = [get_ffmpeg_path(), "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}", "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not tmp.is_file():
        raise RuntimeError(f"ffmpeg render failed: {proc.stderr[-400:]}")
    tmp.replace(out)
    return out


def render_for_target(row: dict, target: str, *, subtitles: bool = True) -> tuple[Path, float, list[str]]:
    """Return (path, duration, warnings). Vertical targets get a 9:16 render capped at 60 s."""
    vertical = target in config.VERTICAL_TARGETS
    notes: list[str] = []
    out_dir = _project_dir(row["project_id"]) / "output" / "social"
    out_dir.mkdir(parents=True, exist_ok=True)
    cached = out_dir / f"{row['id']}_{'vertical' if vertical else 'landscape'}.mp4"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached, float(probe(cached).get("duration") or 0), notes
    # 1) AutoClip's own export pipeline (frame-accurate cut, burned captions, framing).
    try:
        from backend.services.publish_export import ExportRequest, export_clip
        # The exporter keys clips by the pipeline id in metadata/clips_metadata.json ("1", "2", ...),
        # while the DB uses UUIDs, so try the pipeline id first.
        ids = [i for i in dict.fromkeys([str(_json(row.get("clip_metadata")).get("id") or ""), str(row["id"])]) if i]
        res, last = None, None
        for cid in ids:
            try:
                res = export_clip(ExportRequest(row["project_id"], cid, "shorts" if vertical else "original",
                                                subtitles=subtitles, title_card=False))
                break
            except FileNotFoundError as exc:
                last = exc
        if res is None:
            raise last or FileNotFoundError("clip not found by exporter")
        path = Path(res["path"])
        if path.is_file():
            notes.extend(res.get("warnings") or [])
            cached.unlink(missing_ok=True)
            try:
                cached.symlink_to(path)
            except OSError:
                cached = path
            return path, float(res.get("duration_sec") or probe(path).get("duration") or 0), notes
    except Exception as exc:  # noqa: BLE001
        notes.append(f"export pipeline unavailable ({type(exc).__name__}: {str(exc)[:120]}); used direct ffmpeg render")
    # 2) Direct render from the existing clip file or the raw source.
    clip_file = Path(row["video_path"]) if row.get("video_path") else None
    start, end = clip_window(row)
    if clip_file and clip_file.is_file():
        src, s = clip_file, 0.0
        dur = float(probe(clip_file).get("duration") or (end - start))
    else:
        from backend.services.publish_export import find_source_video
        src, s, dur = find_source_video(row["project_id"]), start, end - start
    if vertical and dur > SHORT_MAX_SEC:
        notes.append(f"trimmed from {dur:.0f}s to {SHORT_MAX_SEC:.0f}s for vertical short-form")
        dur = SHORT_MAX_SEC
    _ffmpeg_render(src, cached, s, max(1.0, dur), vertical)
    return cached, float(probe(cached).get("duration") or dur), notes


# ---------------------------------------------------------- signed URLs ---
def signed_media_url(job_id: str, ttl: Optional[int] = None) -> str:
    exp = int(time.time()) + (ttl or config.media_url_ttl_sec())
    sig = crypto.sign(f"media:{job_id}", exp)
    return f"{config.public_api_base()}/social/media/{job_id}.mp4?exp={exp}&sig={sig}"


def verify_media(job_id: str, exp: int, sig: str) -> bool:
    return crypto.verify(f"media:{job_id}", int(exp), sig)
