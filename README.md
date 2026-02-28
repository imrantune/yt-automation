# ⚔️ Spartacus Arena — AI YouTube Automation Pipeline

> Fully automated AI-powered YouTube channel that generates, voices, edits,
> and uploads Spartacus gladiator arena episodes daily — running locally on
> Apple M4 with zero cloud GPU cost.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Platform](https://img.shields.io/badge/Platform-macOS%20M4-black)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active%20Development-orange)

---

## 🎯 What This Does

This pipeline runs 100% automatically on your MacBook Air M4:

1. 🤖 **Generates scripts** using OpenAI GPT-4o with full character continuity
2. 🎙️ **Creates voiceovers** using ElevenLabs epic narrator voice
3. 🎬 **Generates video clips** using Wan 2.1 locally on Apple M4 Metal GPU (FREE)
4. 🎵 **Adds background music** via Suno AI API
5. ⚙️ **Edits and assembles** full episodes using FFmpeg with VideoToolbox HW acceleration
6. 📝 **Generates subtitles** using OpenAI Whisper on Apple Neural Engine (FREE)
7. 🖼️ **Creates thumbnails** using DALL-E 3
8. 📤 **Auto-uploads to YouTube** via YouTube Data API v3
9. 🗄️ **Tracks everything** in PostgreSQL database with full episode history

---

## 💻 System Requirements

| Spec | Minimum | Recommended (This Build) |
|------|---------|--------------------------|
| Mac Chip | Apple M1 | Apple M4 ✅ |
| RAM | 16GB | 24GB ✅ |
| Storage | 100GB free | 259GB free ✅ |
| macOS | Ventura 13+ | Sequoia 15.2 ✅ |
| Python | 3.10+ | 3.11+ ✅ |

> Tested on: **MacBook Air 13-inch M4 2025, 24GB RAM, macOS Sequoia 15.2**

---

## 💰 Monthly Running Cost

| Service | Purpose | Cost |
|---------|---------|------|
| OpenAI GPT-4o + DALL-E 3 | Scripts + Thumbnails | ~$16/mo |
| ElevenLabs Creator | Voiceover | ~$22/mo |
| Suno AI Pro | Background Music | ~$10/mo |
| Wan 2.1 (local M4) | Video Generation | **FREE** |
| Whisper (local) | Subtitles | **FREE** |
| FFmpeg (local) | Video Editing | **FREE** |
| PostgreSQL (Docker) | Database | **FREE** |
| Redis (Docker) | Task Queue | **FREE** |
| Runway ML (fallback only) | Cloud Video Fallback | ~$15/mo |
| **TOTAL** | | **~$48/mo** |

---

## 🏗️ Project Structure

```
spartacus-auto/
├── config/
│   ├── settings.py              # Config loader + validation
│   └── .env                     # API keys (gitignored)
├── database/
│   ├── models.py                # SQLAlchemy models
│   ├── connection.py            # DB session management
│   ├── seed.py                  # Seed characters + series
│   └── migrations/              # Alembic migrations
├── pipeline/
│   ├── script_generator.py      # GPT-4o script generation
│   ├── voiceover.py             # ElevenLabs voiceover
│   ├── video_generator.py       # Wan 2.1 MPS + Runway fallback
│   ├── consistency_manager.py   # Character history + continuity
│   └── thermal_monitor.py       # Mac thermal management
├── output/
│   ├── scripts/                 # Generated JSON scripts
│   ├── audio/                   # Voiceover MP3 files
│   ├── clips/                   # Raw video clips
│   └── final/                   # Finished episodes
├── scheduler/
│   └── tasks.py                 # Celery scheduled tasks
├── docker-compose.yml           # PostgreSQL + Redis
├── requirements.txt
├── .env.example
└── main.py                      # Run full pipeline
```

---

## 🚀 Quick Start

### 1. Clone the Repo

```bash
git clone https://github.com/YOUR_USERNAME/spartacus-auto.git
cd spartacus-auto
```

### 2. Install System Dependencies

```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install system packages
brew install python@3.11 ffmpeg git cmake
brew install --cask docker

# Verify FFmpeg has VideoToolbox support
ffmpeg -encoders | grep videotoolbox
```

### 3. Create Python Environment

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Install PyTorch with MPS (Apple Silicon)

```bash
pip install torch torchvision torchaudio
# Verify MPS is available
python -c "import torch; print('MPS:', torch.backends.mps.is_available())"
# Expected output: MPS: True
```

### 5. Download Wan 2.1 Model

```bash
# Model downloads automatically on first run via diffusers
# Or pre-download manually (recommended):
python -c "
from diffusers import WanPipeline
import torch
pipe = WanPipeline.from_pretrained(
    'Wan-AI/Wan2.1-T2V-1.3B',
    torch_dtype=torch.float16
)
print('Model downloaded successfully')
"
```

> ⚠️ Model is ~6GB. Download once and it caches locally in ~/.cache/huggingface/

### 6. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your API keys
nano .env
```

### 7. Start Docker Services

```bash
# Start Docker Desktop app first, then:
docker-compose up -d

# Verify services are running
docker-compose ps
```

### 8. Initialize Database

```bash
# Run migrations
alembic upgrade head

# Seed initial characters and series
python database/seed.py
```

### 9. Run Your First Episode

```bash
python main.py
```

> ⏱️ First episode takes ~45-90 min on M4 (Wan 2.1 generates 5 clips locally).
> Subsequent runs are faster as model stays cached in memory.

---

## 🎬 Pipeline Flow

```
[Cron 2AM] ──► GPT-4o Script ──► ElevenLabs Voiceover
                                         │
                                         ▼
                              Wan 2.1 M4 Metal GPU
                              (5 video clips, local)
                                         │
                                         ▼
                              Suno AI Background Music
                                         │
                                         ▼
                              FFmpeg VideoToolbox Edit
                              (merge clips + audio + music)
                                         │
                                         ▼
                              Whisper Neural Engine Subtitles
                                         │
                                         ▼
                              DALL-E 3 Thumbnail
                                         │
                                         ▼
                              YouTube Auto Upload ──► Done ✅
```

---

## 🗄️ Database Models

| Model | Description |
|-------|-------------|
| `Series` | The show (Spartacus Arena) |
| `Episode` | Each episode with status tracking |
| `Scene` | Individual scenes within an episode |
| `Character` | Gladiators with stats and alive/dead status |
| `CharacterStat` | Per-episode fight results |
| `VideoJob` | Video generation job tracking |
| `JobLog` | Full pipeline step logs with timestamps |

---

## ⚔️ Characters (Initial Roster)

| Name | Origin | Fighting Style | Personality |
|------|--------|----------------|-------------|
| Spartacus | Thrace | Dual Sword | Noble warrior |
| Crixus | Gaul | Shield & Gladius | Aggressive |
| Gannicus | Celt | Twin Blades | Wild & fearless |
| Oenomaus | Africa | Heavy Weapons | Disciplined |
| Agron | Germania | Spear | Loyal & fierce |

> Characters evolve over time. Every 5 episodes: 1 character dies.
> Every 10 episodes: 1 new character joins the arena.

---

## 🌡️ Mac Thermal Management

MacBook Air M4 has no fan — the pipeline manages thermals automatically:

| CPU Temp | Action |
|----------|--------|
| < 90°C | Normal generation |
| 90–95°C | Pause 5 minutes, then resume |
| > 95°C | Pause 10 minutes, then resume |
| < 80°C | Resume generation |

**Recommended schedule:** Run overnight (2AM) while plugged in.

Check your CPU temp manually:
```bash
sudo powermetrics --samplers smc -i1 -n1 | grep -i "CPU die"
```

---

## ⚙️ Configuration

All settings are controlled via `.env`:

```env
# AI APIs
OPENAI_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_voice_id
SUNO_API_KEY=your_key_here
RUNWAY_API_KEY=your_key_here          # Fallback only

# Database
DATABASE_URL=postgresql://spartacus:password@localhost:5432/spartacus_db
REDIS_URL=redis://localhost:6379/0

# Wan 2.1 Local Settings
WAN21_MODEL_ID=Wan-AI/Wan2.1-T2V-1.3B
WAN21_DEVICE=mps
WAN21_CLIP_DURATION=10
WAN21_COOLDOWN_SECONDS=30
WAN21_TEMP_PAUSE=90
WAN21_TEMP_STOP=95
WAN21_TEMP_RESUME=80

# Video
VIDEO_GENERATOR=wan21                 # wan21 | runway
VIDEO_CODEC=h264_videotoolbox
VIDEO_RESOLUTION=1920x1080
VIDEO_FPS=24
VIDEO_BITRATE=5000k
AUDIO_BITRATE=192k
```

---

## 📅 Automated Weekly Batch

Generate and upload 7 episodes automatically every Sunday night:

```bash
# Make executable
chmod +x generate_next_7.sh

# Run manually
./generate_next_7.sh

# Or add to cron (runs every Sunday at 2AM)
crontab -e
# Add: 0 2 * * 0 cd /path/to/spartacus-auto && ./generate_next_7.sh
```

---

## 🔄 Video Generator Fallback

The pipeline automatically falls back to Runway ML if Wan 2.1 fails:

```
Wan 2.1 (local M4 MPS)
        │
        ▼ fails?
Runway ML API (cloud)
        │
        ▼ fails?
Log error + skip clip + continue pipeline
```

Force cloud generation by setting:
```env
VIDEO_GENERATOR=runway
```

---

## 🧪 Running Tests

```bash
# Run all unit tests
python -m pytest tests/ -v

# Test specific modules
python -m pytest tests/test_script_generator.py -v
python -m pytest tests/test_video_generator.py -v
python -m pytest tests/test_db.py -v
```

---

## 📈 Expected YouTube Growth

| Timeframe | Videos Live | Expected Views/Video |
|-----------|------------|----------------------|
| Week 1 | 3–7 | 10–50 |
| Month 1 | 20–30 | 200–1,000 |
| Month 2 | 50–60 | 1,000–5,000 |
| Month 3+ | 90+ | Potential viral spikes |

> Daily uploads + strong thumbnails + consistent niche = compounding growth

---

## 🗺️ Roadmap

- [x] Phase 1 — Foundation, DB, Script, Voice, Video (local M4)
- [ ] Phase 2 — YouTube upload, Shorts generator, scheduling
- [ ] Phase 3 — Analytics tracking, A/B thumbnail testing, multi-series

---

## ⚠️ Disclaimer

This project uses AI-generated content for entertainment purposes.
All characters are fictional. API usage must comply with each
provider's terms of service. YouTube content must follow
YouTube's Community Guidelines.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Tech Stack Credits

- [OpenAI](https://openai.com) — GPT-4o + DALL-E 3 + Whisper
- [ElevenLabs](https://elevenlabs.io) — Text to Speech
- [Wan 2.1](https://github.com/Wan-Video/Wan2.1) — Open source video generation
- [Suno AI](https://suno.ai) — AI music generation
- [Runway ML](https://runwayml.com) — Cloud video fallback
- [FFmpeg](https://ffmpeg.org) — Video processing
- [Apple Metal](https://developer.apple.com/metal/) — M4 GPU acceleration
- [PostgreSQL](https://www.postgresql.org) — Database
- [Redis](https://redis.io) + [Celery](https://docs.celeryq.dev) — Task queue

---

*Built with ⚔️ on MacBook Air M4 — automated, relentless, unstoppable.*
