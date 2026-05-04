"""
ShortsIQ — Unified Server
Serves the web app on / and MCP tools on /api
Single deployment handles everything.
"""

import os
import json
import base64
import subprocess
import tempfile
import glob
import re
import threading
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import anthropic
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from wsgiref.handlers import BaseHandler
from a2wsgi import WSGIMiddleware

# ── Anthropic client ──────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Flask web app ─────────────────────────────────────────────────────────────
flask_app = Flask(__name__)
CORS(flask_app)

# ── MCP server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "ShortsIQ",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

def run_ytdlp(*args):
    cmd = ["yt-dlp", "--no-check-certificates"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def get_shorts_list(channel_url: str, limit: int = 30) -> list:
    url = channel_url.rstrip("/") + "/shorts"
    result = run_ytdlp(
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(view_count)s\t%(duration)s\t%(thumbnail)s",
        "--playlist-end", str(limit),
        url
    )
    shorts = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            vid_id = parts[0].strip()
            shorts.append({
                "id": vid_id,
                "title": parts[1].strip() if len(parts) > 1 else "Unknown",
                "views": int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else 0,
                "duration": int(parts[3]) if len(parts) > 3 and parts[3].strip().isdigit() else 0,
                "thumbnail": parts[4].strip() if len(parts) > 4 else f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/shorts/{vid_id}"
            })
    return shorts


def extract_frames(video_id: str, hook_frames: int = 10, midtail_frames: int = 5) -> list:
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "video.%(ext)s")
        run_ytdlp(
            "-f", "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
            "--no-playlist", "-o", video_path,
            f"https://www.youtube.com/shorts/{video_id}"
        )
        files = glob.glob(os.path.join(tmpdir, "video.*"))
        if not files:
            return []
        video_file = files[0]

        probe = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_file
        ], capture_output=True, text=True)
        duration = 60.0
        try:
            info = json.loads(probe.stdout)
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    duration = float(stream.get("duration", 60))
                    break
        except Exception:
            pass

        mid_frames  = max(0, round(midtail_frames * 0.75))
        tail_frames = max(0, midtail_frames - mid_frames)
        timestamps  = []

        hook_end = min(10.0, duration)
        if hook_frames > 0 and hook_end > 0:
            timestamps += [round(hook_end * i / hook_frames, 3) for i in range(hook_frames)]

        mid_start, mid_end = min(10.0, duration), min(30.0, duration)
        if mid_frames > 0 and mid_end > mid_start:
            span = mid_end - mid_start
            timestamps += [round(mid_start + span * i / mid_frames, 3) for i in range(mid_frames)]

        tail_start = min(30.0, duration)
        if tail_frames > 0 and duration > tail_start:
            span = duration - tail_start
            timestamps += [round(tail_start + span * i / tail_frames, 3) for i in range(tail_frames)]

        timestamps = sorted(set(timestamps))
        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        for i, ts in enumerate(timestamps):
            subprocess.run([
                "ffmpeg", "-ss", str(ts), "-i", video_file,
                "-frames:v", "1", "-q:v", "4", "-vf", "scale=480:-1",
                os.path.join(frames_dir, f"frame_{i:04d}.jpg"),
                "-y", "-loglevel", "quiet"
            ], capture_output=True)

        frames = []
        for f in sorted(glob.glob(os.path.join(frames_dir, "*.jpg"))):
            with open(f, "rb") as img:
                frames.append(base64.b64encode(img.read()).decode())
        return frames


def get_transcript(video_id: str) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        run_ytdlp(
            "--write-auto-subs", "--sub-lang", "en",
            "--sub-format", "vtt", "--skip-download",
            "--no-playlist", "-o", os.path.join(tmpdir, "sub"),
            f"https://www.youtube.com/shorts/{video_id}"
        )
        vtt_files = glob.glob(os.path.join(tmpdir, "*.vtt"))
        if not vtt_files:
            return ""
        with open(vtt_files[0], "r", encoding="utf-8") as f:
            raw = f.read()
        lines = []
        for line in raw.split("\n"):
            line = line.strip()
            if (line and not line.startswith("WEBVTT") and not line.startswith("NOTE")
                    and not re.match(r"^\d{2}:\d{2}", line)
                    and not re.match(r"^\d+$", line) and not line.startswith("<")):
                lines.append(line)
        deduped = []
        for line in lines:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        return " ".join(deduped)


def run_analysis(videos_data: list, mode: str = "compare") -> str:
    if mode == "compare":
        prompt = f"""You are an expert YouTube Shorts analyst. Analyze these {len(videos_data)} Shorts.
1. Is performance driven by IDEATION or VISUALS? Give a % split.
2. What separates high performers from low performers?
3. Side-by-side table: Views, Hook, Stakes, Concept strength, Visual quality
4. HEADLINE FINDING in one sentence
5. 3 actionable recommendations
Be direct. Reference actual titles and timestamps."""
    elif mode == "retention":
        prompt = """Analyze this Short for retention:
1. DEAD ZONE MAP — every timestamp where viewers would leave
2. First 2 seconds — what does a half-asleep scroller see?
3. Rate Hook/Pacing/Visual variety/Audio hook each /10
4. What to fix in order of impact. Be brutal."""
    else:
        prompt = """Analyze this Short:
1. Hook — first 2 seconds strength
2. What's working / what isn't
3. Virality score 1-10 and why
4. What would make it 10x better?"""

    content = [{"type": "text", "text": prompt}]
    for vid in videos_data:
        content.append({"type": "text", "text": f"\n\n{'='*40}\n{vid['title']}\nViews: {vid['views']:,}\n{vid['url']}\nTranscript: {vid['transcript']}\n{'='*40}\n"})
        for j, frame_b64 in enumerate(vid.get("frames", [])):
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}})
            content.append({"type": "text", "text": f"[Frame {j+1}]"})

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": content}]
    )
    return response.content[0].text


def build_videos_data(ids, titles_map, views_map):
    LIMIT = 90
    count = len(ids)
    hook_per = min(30, max(6, int(LIMIT * 0.7 / count)))
    midtail_per = max(0, (LIMIT - hook_per * count) // count)
    max_frames = hook_per + midtail_per

    if count <= 2: frame_note = f"Ultra — {hook_per} hook + {midtail_per} mid/tail per video"
    elif count <= 4: frame_note = f"High — {hook_per} hook + {midtail_per} mid/tail per video"
    elif count <= 7: frame_note = f"Medium — {hook_per} hook + {midtail_per} mid/tail per video"
    else: frame_note = f"Minimal — {max_frames} frames per video"

    videos_data = []
    for vid_id in ids:
        transcript = get_transcript(vid_id)
        frames = extract_frames(vid_id, hook_frames=hook_per, midtail_frames=midtail_per)
        videos_data.append({
            "id": vid_id,
            "title": titles_map.get(vid_id, vid_id),
            "views": views_map.get(vid_id, 0),
            "url": f"https://www.youtube.com/shorts/{vid_id}",
            "transcript": transcript,
            "frames": frames
        })
    return videos_data, frame_note, max_frames


# ── Flask routes ───────────────────────────────────────────────────────────────

@flask_app.route("/")
def index():
    return render_template("index.html")

@flask_app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "ShortsIQ"})

@flask_app.route("/api/shorts", methods=["POST"])
def api_shorts():
    data = request.json
    channel_url = data.get("channel_url", "")
    limit = int(data.get("limit", 30))
    if not channel_url:
        return jsonify({"error": "No channel URL"}), 400
    try:
        shorts = get_shorts_list(channel_url, limit)
        return jsonify({"shorts": shorts, "count": len(shorts)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flask_app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.json
    ids = data.get("video_ids", [])
    titles = data.get("titles", {})
    views = data.get("views", {})
    mode = data.get("mode", "compare")
    if not ids:
        return jsonify({"error": "No videos selected"}), 400
    try:
        videos_data, frame_note, max_frames = build_videos_data(ids, titles, views)
        analysis = run_analysis(videos_data, mode)
        return jsonify({"analysis": analysis, "videos_analyzed": len(videos_data), "frame_note": frame_note, "frames_per_video": max_frames})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── MCP tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_channel_shorts(channel_url: str, limit: int = 30) -> str:
    """Fetch YouTube Shorts from any channel with view counts.
    Args:
        channel_url: YouTube channel URL e.g. https://www.youtube.com/@southfloridapokemon
        limit: Number of Shorts to fetch (default 30, max 50)
    """
    try:
        shorts = get_shorts_list(channel_url, min(limit, 50))
        if not shorts:
            return "No Shorts found."
        lines = [f"Found {len(shorts)} Shorts\n", f"{'#':<4} {'Views':<10} {'Dur':<6} {'Title':<50} URL", "-"*90]
        for i, s in enumerate(shorts, 1):
            views = f"{s['views']:,}" if s['views'] else "—"
            dur = f"{s['duration']}s" if s['duration'] else "—"
            lines.append(f"{i:<4} {views:<10} {dur:<6} {s['title'][:48]:<50} {s['url']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def analyze_shorts(video_ids: str, titles: str = "", views: str = "", mode: str = "compare") -> str:
    """Analyze YouTube Shorts using frame extraction + transcripts.
    Args:
        video_ids: Comma-separated YouTube video IDs
        titles: Comma-separated titles (optional, matches video_ids order)
        views: Comma-separated view counts (optional)
        mode: 'compare', 'retention', or 'single'
    """
    try:
        ids = [v.strip() for v in video_ids.split(",") if v.strip()]
        title_list = [t.strip() for t in titles.split(",")] if titles else []
        views_list = [v.strip() for v in views.split(",")] if views else []
        titles_map = {ids[i]: title_list[i] if i < len(title_list) else ids[i] for i in range(len(ids))}
        views_map = {ids[i]: int(views_list[i]) if i < len(views_list) and views_list[i].isdigit() else 0 for i in range(len(ids))}
        videos_data, _, _ = build_videos_data(ids, titles_map, views_map)
        return run_analysis(videos_data, mode)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_video_transcript(video_id: str) -> str:
    """Get transcript from a YouTube Short.
    Args:
        video_id: YouTube video ID (part after /shorts/)
    """
    try:
        t = get_transcript(video_id)
        return f"Transcript:\n\n{t}" if t else "No transcript available."
    except Exception as e:
        return f"Error: {str(e)}"


# ── Combined ASGI app ──────────────────────────────────────────────────────────

mcp_asgi = mcp.streamable_http_app()
flask_asgi = WSGIMiddleware(flask_app)

async def health_route(request):
    return JSONResponse({"status": "ok", "service": "ShortsIQ"})

combined = Starlette(routes=[
    Route("/health", health_route),
    Mount("/api/mcp", app=mcp_asgi),
    Mount("/", app=flask_asgi),
])

app = CORSMiddleware(
    combined,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"ShortsIQ starting on 0.0.0.0:{port}")
    print(f"  Web app: http://0.0.0.0:{port}/")
    print(f"  MCP:     http://0.0.0.0:{port}/api/mcp")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
