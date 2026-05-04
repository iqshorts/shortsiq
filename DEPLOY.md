# Deploying ShortsIQ as an MCP Server

Once deployed, you can use ShortsIQ directly inside Claude by typing naturally:
"Get me the Shorts from @southfloridapokemon"
"Compare the best and worst Shorts from this channel"
"Analyze why this Short went viral"

---

## Deploy to Railway (recommended, free to start)

### 1. Create a GitHub repo
- Go to github.com and create a new repo called `shortsiq`
- Upload all files from this folder to it

### 2. Deploy on Railway
- Go to railway.app and sign up (free)
- Click "New Project" → "Deploy from GitHub repo"
- Select your `shortsiq` repo
- Railway auto-detects the config and deploys

### 3. Set environment variables in Railway
In your Railway project → Variables tab, add:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 4. Get your deployment URL
Railway gives you a URL like:
`https://shortsiq-production.up.railway.app`

Your MCP endpoint will be:
`https://shortsiq-production.up.railway.app/mcp`

### 5. Connect to Claude.ai
- Go to claude.ai → Settings → Integrations
- Click "Add Integration"  
- Paste your MCP URL: `https://shortsiq-production.up.railway.app/mcp`
- Click Connect

That's it. Claude will now have access to your ShortsIQ tools.

---

## Available MCP Tools

Once connected, Claude can use these tools automatically:

| Tool | What it does |
|------|-------------|
| `get_channel_shorts` | Fetch Shorts list from any channel with view counts |
| `analyze_shorts` | Full analysis — frames + transcript + Claude breakdown |
| `scrape_video` | Extract frames and transcript from a single video |
| `resolve_channel_handle` | Get channel info from a @handle |

---

## Running locally for testing

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key
python mcp_server.py
```

Then in Claude Code or any MCP client, point to:
`http://localhost:8000/mcp`

---

## Cost estimate

- Railway hobby plan: $5/month
- Anthropic API: ~$0.10-0.30 per analysis (Sonnet)
- yt-dlp + ffmpeg: free
