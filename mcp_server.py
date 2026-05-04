"""
ShortsIQ MCP Server
"""

import os
import json
import base64
import subprocess
import tempfile
import glob
import re
from mcp.server.fastmcp import FastMCP

from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "ShortsIQ",
    port=int(os.environ.get("PORT", 8080)),
    host="0.0.0.0",
    streamable_http_path="/api",
    sse_path="/api/sse",
    message_path="/api/messages/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def run_ytdlp(*args):
    cmd = ["yt-dlp", "--no-check-certificates"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def get_shorts_list(channel_url: str, limit: int = 30) -> list:
    url = channel_url.rstrip("/") + "/shorts"
    result = run_ytdlp(
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(view_count)s\t%(duration)s",
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
                "url": f"https://www.youtube.com/shorts/{vid_id}",
                "thumbnail": f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg"
            })
    return shorts


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
            if (line and
                not line.startswith("WEBVTT") and
                not line.startswith("NOTE") and
                not re.match(r"^\d{2}:\d{2}", line) and
                not re.match(r"^\d+$", line) and
                not line.startswith("<")):
                lines.append(line)
        deduped = []
        for line in lines:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        return " ".join(deduped)


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


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_channel_shorts(channel_url: str, limit: int = 30) -> str:
    """
    Fetch a list of YouTube Shorts from any channel with view counts.
    
    Args:
        channel_url: YouTube channel URL e.g. https://www.youtube.com/@southfloridapokemon
        limit: Number of Shorts to fetch (default 30, max 50)
    """
    try:
        shorts = get_shorts_list(channel_url, min(limit, 50))
        if not shorts:
            return "No Shorts found. Check the channel URL."
        lines = [f"Found {len(shorts)} Shorts from {channel_url}\n"]
        lines.append(f"{'#':<4} {'Views':<10} {'Dur':<6} {'Title':<50} URL")
        lines.append("-" * 90)
        for i, s in enumerate(shorts, 1):
            views = f"{s['views']:,}" if s['views'] else "—"
            dur = f"{s['duration']}s" if s['duration'] else "—"
            lines.append(f"{i:<4} {views:<10} {dur:<6} {s['title'][:48]:<50} {s['url']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def analyze_shorts(video_ids: str, titles: str = "", views: str = "", mode: str = "compare") -> str:
    """
    Analyze YouTube Shorts using frame extraction + transcripts. Finds why some go viral.

    Args:
        video_ids: Comma-separated YouTube video IDs (the part after /shorts/)
        titles: Comma-separated titles matching video_ids order (optional)
        views: Comma-separated view counts matching video_ids order (optional)
        mode: 'compare' (best vs worst), 'retention' (dead zone audit), 'single' (deep dive)
    """
    try:
        ids = [v.strip() for v in video_ids.split(",") if v.strip()]
        title_list = [t.strip() for t in titles.split(",")] if titles else []
        views_list = [v.strip() for v in views.split(",")] if views else []

        if not ids:
            return "No video IDs provided."

        count = len(ids)
        LIMIT = 90
        hook_per = min(30, max(6, int(LIMIT * 0.7 / count)))
        midtail_per = max(0, (LIMIT - hook_per * count) // count)

        videos_data = []
        for i, vid_id in enumerate(ids):
            transcript = get_transcript(vid_id)
            frames = extract_frames(vid_id, hook_frames=hook_per, midtail_frames=midtail_per)
            videos_data.append({
                "id": vid_id,
                "title": title_list[i] if i < len(title_list) else vid_id,
                "views": int(views_list[i]) if i < len(views_list) and views_list[i].isdigit() else 0,
                "url": f"https://www.youtube.com/shorts/{vid_id}",
                "transcript": transcript,
                "frames": frames
            })

        if mode == "compare":
            prompt = f"""You are an expert YouTube Shorts analyst. Analyze these {count} Shorts.
1. Is performance driven by IDEATION or VISUALS? Give a % split.
2. What separates high performers from low performers?
3. Side-by-side table: Views, Hook, Stakes, Concept, Visual quality
4. ONE headline finding
5. 3 actionable recommendations
Be direct. Reference actual titles."""
        elif mode == "retention":
            prompt = """Analyze this Short for retention issues:
1. DEAD ZONE MAP — every timestamp where viewers leave
2. First 2 seconds — what does a half-asleep scroller see?
3. Rate Hook/Pacing/Visual variety/Audio hook each /10
4. What to fix in order of impact"""
        else:
            prompt = """Analyze this Short:
1. Hook — how strong are the first 2 seconds?
2. What's working / what isn't?
3. Virality score 1-10 and why
4. What would make it 10x better?"""

        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        content = [{"type": "text", "text": prompt}]

        for vid in videos_data:
            content.append({"type": "text", "text": f"\n\n{'='*40}\n{vid['title']}\nViews: {vid['views']:,}\n{vid['url']}\nTranscript: {vid['transcript']}\n{'='*40}\n"})
            for j, frame_b64 in enumerate(vid["frames"]):
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}})
                content.append({"type": "text", "text": f"[Frame {j+1}]"})

        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": content}]
        )
        return response.content[0].text

    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_video_transcript(video_id: str) -> str:
    """
    Get the transcript/captions from a YouTube Short.
    
    Args:
        video_id: YouTube video ID (part after /shorts/ in URL)
    """
    try:
        transcript = get_transcript(video_id)
        if not transcript:
            return "No transcript available for this video."
        return f"Transcript for {video_id}:\n\n{transcript}"
    except Exception as e:
        return f"Error: {str(e)}"


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"ShortsIQ MCP Server starting on 0.0.0.0:{port}")
    uvicorn.run(
        mcp.streamable_http_app(),
        host="0.0.0.0",
        port=port,
        log_level="info",
        root_path="",
    )
