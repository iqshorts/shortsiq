#!/bin/bash
# ShortsIQ - Start Script

echo ""
echo "  ███████╗██╗  ██╗ ██████╗ ██████╗ ████████╗███████╗██╗ ██████╗ "
echo "  ██╔════╝██║  ██║██╔═══██╗██╔══██╗╚══██╔══╝██╔════╝██║██╔═══██╗"
echo "  ███████╗███████║██║   ██║██████╔╝   ██║   ███████╗██║██║   ██║"
echo "  ╚════██║██╔══██║██║   ██║██╔══██╗   ██║   ╚════██║██║██║▄▄ ██║"
echo "  ███████║██║  ██║╚██████╔╝██║  ██║   ██║   ███████║██║╚██████╔╝"
echo "  ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝ ╚══▀▀═╝ "
echo ""
echo "  YouTube Shorts Intelligence Tool"
echo ""

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "  ⚠️  ANTHROPIC_API_KEY not set."
  echo "  Run: export ANTHROPIC_API_KEY=your_key_here"
  echo ""
  read -p "  Enter your Anthropic API key: " key
  export ANTHROPIC_API_KEY=$key
fi

# Check dependencies
echo "  Checking dependencies..."
pip install -r requirements.txt -q

# Check ffmpeg
if ! command -v ffmpeg &> /dev/null; then
  echo "  ⚠️  ffmpeg not found. Installing..."
  if [[ "$OSTYPE" == "darwin"* ]]; then
    brew install ffmpeg
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    sudo apt-get install -y ffmpeg -q
  else
    echo "  Please install ffmpeg manually: https://ffmpeg.org/download.html"
    exit 1
  fi
fi

echo "  ✅ All good. Starting server..."
echo "  → Open http://localhost:5000 in your browser"
echo ""

python app.py
