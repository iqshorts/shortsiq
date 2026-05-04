import os
import json
import base64
import subprocess
import tempfile
import glob
import re
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import anthropic

app = Flask(__name__)
CORS(app)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── helpers ──────────────────────────────────────────────────────────────────

def run_ytdlp(*args):
    cmd = ["yt-dlp", "--no-check-certificates"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

def get_shorts_list(channel_url, limit=30):
    """Pull Shorts from a channel using yt-dlp flat playlist."""
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
        if len(parts) >= 4:
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


def extract_frames(video_id, hook_frames=30, midtail_frames=20):
    """
    Hook-weighted frame extraction with explicit frame budgets.
    hook_frames: number of frames to extract from first 10 seconds
    midtail_frames: split 75/25 between 10-30s and 30s+
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "video.%(ext)s")
        run_ytdlp(
            "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "--no-playlist",
            "-o", video_path,
            f"https://www.youtube.com/shorts/{video_id}"
        )
        files = glob.glob(os.path.join(tmpdir, "video.*"))
        if not files:
            return []
        video_file = files[0]

        # Get duration
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

        # HOOK ZONE: 0 - 10s (dense)
        hook_end = min(10.0, duration)
        if hook_frames > 0 and hook_end > 0:
            timestamps += [round(hook_end * i / hook_frames, 3)
                           for i in range(hook_frames)]

        # MID ZONE: 10s - 30s
        mid_start, mid_end = min(10.0, duration), min(30.0, duration)
        if mid_frames > 0 and mid_end > mid_start:
            span = mid_end - mid_start
            timestamps += [round(mid_start + span * i / mid_frames, 3)
                           for i in range(mid_frames)]

        # TAIL ZONE: 30s+
        tail_start = min(30.0, duration)
        if tail_frames > 0 and duration > tail_start:
            span = duration - tail_start
            timestamps += [round(tail_start + span * i / tail_frames, 3)
                           for i in range(tail_frames)]

        timestamps = sorted(set(timestamps))

        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        for i, ts in enumerate(timestamps):
            subprocess.run([
                "ffmpeg", "-ss", str(ts), "-i", video_file,
                "-frames:v", "1", "-q:v", "3", "-vf", "scale=540:-1",
                os.path.join(frames_dir, f"frame_{i:04d}.jpg"),
                "-y", "-loglevel", "quiet"
            ], capture_output=True)

        frames = []
        for f in sorted(glob.glob(os.path.join(frames_dir, "*.jpg"))):
            with open(f, "rb") as img:
                frames.append(base64.b64encode(img.read()).decode())
        return frames


def get_transcript(video_id):
    """Pull auto-generated transcript via yt-dlp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        run_ytdlp(
            "--write-auto-subs",
            "--sub-lang", "en",
            "--sub-format", "vtt",
            "--skip-download",
            "--no-playlist",
            "-o", os.path.join(tmpdir, "sub"),
            f"https://www.youtube.com/shorts/{video_id}"
        )
        vtt_files = glob.glob(os.path.join(tmpdir, "*.vtt"))
        if not vtt_files:
            return ""
        with open(vtt_files[0], "r", encoding="utf-8") as f:
            raw = f.read()
        # Strip VTT formatting
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
        # Deduplicate consecutive identical lines
        deduped = []
        for line in lines:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        return " ".join(deduped)


def analyze_with_claude(videos_data, mode="compare"):
    """Send video data to Claude for analysis."""
    
    content = []

    if mode == "compare":
        prompt = f"""You are an expert YouTube Shorts analyst. I'm giving you {len(videos_data)} YouTube Shorts to analyze.

For each video I'll provide: title, view count, transcript, and video frames.

Your job:
1. Identify WHY the high-performing videos outperform the low-performing ones
2. Is it IDEATION (concept, hook, story, stakes) or VISUALS (editing, pacing, production)?
3. Give a side-by-side comparison table with these metrics: Views, Hook (first line of transcript), Stakes, Concept strength, Visual quality
4. Give a HEADLINE FINDING — one clear sentence on what separates winners from losers
5. Give 3 actionable recommendations for this channel

Be direct, specific, and ruthless. Reference actual titles and timestamps. Format with clear headers."""

    elif mode == "retention":
        prompt = """You are an expert YouTube Shorts editor analyzing a video for retention issues.

I'm giving you video frames and a transcript.

Your job:
1. MAP THE DEAD ZONES — find every moment where a scroller would leave (static frames, slow pacing, weak audio hook, no visual change)
2. Analyze the FIRST 2 SECONDS — what does a half-asleep scroller see? Is there a visual hook?
3. Rate: Hook strength, Pacing, Visual variety, Audio hook
4. Give a DEAD ZONE MAP with timestamps
5. Tell me exactly what to fix, in order of impact

Be brutal. Format with clear headers and bullet points."""

    else:  # single video analysis
        prompt = """You are an expert YouTube Shorts analyst. Analyze this Short and tell me:
1. Hook analysis — how strong is the opening 2 seconds?
2. Content strategy — what's working, what isn't?
3. Virality score (1-10) and why
4. What would make this video 10x better?

Be specific and reference what you actually see in the frames."""

    content.append({"type": "text", "text": prompt})

    for i, vid in enumerate(videos_data):
        content.append({
            "type": "text",
            "text": f"\n\n{'='*50}\nVIDEO {i+1}: {vid['title']}\nViews: {vid['views']:,}\nURL: {vid['url']}\nTranscript: {vid.get('transcript', 'No transcript available')}\n{'='*50}\n"
        })
        for j, frame_b64 in enumerate(vid.get("frames", [])):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": frame_b64
                }
            })
            content.append({
                "type": "text",
                "text": f"[Frame {j+1}]"
            })

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": content}]
    )
    return response.content[0].text


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/shorts", methods=["POST"])
def get_shorts():
    data = request.json
    channel_url = data.get("channel_url", "")
    limit = int(data.get("limit", 30))
    if not channel_url:
        return jsonify({"error": "No channel URL provided"}), 400
    try:
        shorts = get_shorts_list(channel_url, limit)
        return jsonify({"shorts": shorts, "count": len(shorts)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.json
    video_ids = data.get("video_ids", [])
    titles = data.get("titles", {})
    views = data.get("views", {})
    mode = data.get("mode", "compare")

    if not video_ids:
        return jsonify({"error": "No videos selected"}), 400

    count = len(video_ids)
    HARD_LIMIT = 98  # stay just under Claude 100 image limit

    # Hook frames are always 30 per video if budget allows
    # Remaining frames after hook budget split across mid/tail per video
    ideal_hook = 30
    if ideal_hook * count <= HARD_LIMIT * 0.7:
        # Plenty of room — use full 30 hook frames
        hook_per_video = ideal_hook
    else:
        # Scale hook frames down so they use max 70% of budget
        hook_per_video = max(6, int(HARD_LIMIT * 0.7 / count))

    hook_total = hook_per_video * count
    remaining_total = HARD_LIMIT - hook_total
    midtail_per_video = max(0, remaining_total // count)
    max_frames = hook_per_video + midtail_per_video

    if count <= 2:
        frame_mode = "ultra"
        frame_note = f"Ultra detail — {hook_per_video} hook frames + {midtail_per_video} mid/tail per video ({max_frames} total per video)"
    elif count <= 4:
        frame_mode = "high"
        frame_note = f"High detail — {hook_per_video} hook frames + {midtail_per_video} mid/tail per video"
    elif count <= 7:
        frame_mode = "medium"
        frame_note = f"Medium — {hook_per_video} hook frames + {midtail_per_video} mid/tail per video"
    else:
        frame_mode = "minimal"
        frame_note = f"Minimal — {max_frames} frames per video ({count} videos)"

    videos_data = []
    for vid_id in video_ids:
        transcript = get_transcript(vid_id)
        frames = extract_frames(vid_id, hook_frames=hook_per_video, midtail_frames=midtail_per_video)
        videos_data.append({
            "id": vid_id,
            "title": titles.get(vid_id, vid_id),
            "views": views.get(vid_id, 0),
            "url": f"https://www.youtube.com/shorts/{vid_id}",
            "transcript": transcript,
            "frames": frames
        })

    analysis = analyze_with_claude(videos_data, mode)
    return jsonify({
        "analysis": analysis,
        "videos_analyzed": len(videos_data),
        "frame_mode": frame_mode,
        "frame_note": frame_note,
        "frames_per_video": max_frames
    })


@app.route("/api/scrape_video", methods=["POST"])
def scrape_video():
    """Scrape a single video — frames + transcript only (no analysis)."""
    data = request.json
    video_id = data.get("video_id", "")
    if not video_id:
        return jsonify({"error": "No video ID"}), 400
    transcript = get_transcript(video_id)
    frames = extract_frames(video_id)
    return jsonify({
        "video_id": video_id,
        "transcript": transcript,
        "frame_count": len(frames),
        "frames": frames
    })


if __name__ == "__main__":
    print("🎬 Shorts Analyzer running at http://localhost:5000")
    print("Make sure ANTHROPIC_API_KEY is set in your environment.")
    app.run(debug=True, port=5000)
