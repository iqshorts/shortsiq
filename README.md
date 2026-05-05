# ShortsIQ — YouTube Shorts Intelligence Tool

Reverse-engineered Algrow-style Shorts analyzer built with yt-dlp + Claude.

## What it does

- Pulls any channel's Shorts with view counts
- Extracts video frames using ffmpeg
- Grabs transcripts via yt-dlp auto-captions
- Sends everything to Claude for deep analysis
- Compares best vs worst performers (ideation vs visuals)
- Retention audits with dead zone mapping

## Setup

### Requirements
- Python 3.9+
- ffmpeg
- An Anthropic API key

### Install & Run

```bash
# 1. Set your API key
export ANTHROPIC_API_KEY=sk-ant-your-key-here

# 2. Run the start script
chmod +x start.sh
./start.sh
```

Or manually:
```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000

### Install ffmpeg (if needed)
- **Mac**: `brew install ffmpeg`
- **Windows**: Download from https://ffmpeg.org/download.html, add to PATH
- **Linux**: `sudo apt-get install ffmpeg`

## How to use

1. Paste a YouTube channel URL (e.g. `https://www.youtube.com/@southfloridapokemon`)
2. Click **Fetch Shorts** — pulls the last 30 Shorts with view counts
3. Click cards to select videos you want to analyze
4. Choose analysis mode:
   - **Compare best vs worst** — finds why some go viral and others don't
   - **Single video analysis** — deep dive on one video
   - **Retention audit** — dead zone map + what to fix
5. Click **Analyze with Claude** — takes 1-2 min (downloading + processing)

## Analysis modes

### Compare best vs worst
Select 2-4 videos (mix of high and low performers). Claude will:
- Tell you if it's ideation or visuals driving performance
- Give a side-by-side breakdown table
- Identify the hook pattern that works
- Give actionable recs

### Retention audit
Select 1 video. Claude will:
- Map every dead zone by timestamp
- Analyze the first 2 seconds
- Tell you exactly what to fix in order of impact

## Notes

- Frame extraction uses ffmpeg at 0.5fps (1 frame every 2 seconds)
- Transcripts are pulled from YouTube auto-captions
- Analysis uses Claude Opus for best results
- First run on a video takes longer (download + extract)
# test Tue May  5 01:12:52 UTC 2026
