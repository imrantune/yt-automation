# Spartacus Arena Product Overview

## What This Product Does

`yt-automation` is a fully automated YouTube channel pipeline that creates Spartacus-style gladiator episodes with narrative continuity. It generates scripts, voiceovers, video clips, subtitles, thumbnails, YouTube Shorts, SEO metadata, and optionally uploads everything to YouTube -- all without manual intervention.

## Architecture

### Config Layer (`config/settings.py`)
- Loads `.env` with strict validation at startup
- Fails fast if required keys missing (DB, Redis, OpenAI, ElevenLabs)
- Validates video provider order against known set
- `require_api_keys=False` mode for infra-only contexts (migrations, seeding, web dashboard)

### Persistence Layer (`database/`)
- **Models**: Series, Episode, Scene, Character (with `voice_id`), CharacterStat, VideoJob, JobLog, EpisodeSEO, Short, ApiCostLog
- **Enums**: stored lowercase via `values_callable` to match PostgreSQL enum values
- **Connection**: `session_scope()` context manager, isolated failure logging
- **Migrations**: Alembic with 4 revisions (initial schema + Phase 2 + character voice_id + API cost logs)

### Generation Pipeline (`pipeline/`)

| Module | Purpose |
|--------|---------|
| `consistency_manager.py` | Story continuity, character results, kill/new character rules |
| `script_generator.py` | GPT-4o script generation with scene structure |
| `seo.py` | GPT-powered YouTube title, description, tags, hashtags |
| `voiceover.py` | ElevenLabs per-scene narration with per-character voice override |
| `cost_tracker.py` | Real-time API cost tracking per episode (OpenAI, ElevenLabs, Minimax, Runway) |
| `subtitles.py` | Whisper API transcription to SRT files |
| `video_generator.py` | Wan2.1/Minimax/Runway video clips with fallback |
| `music.py` | Background music mixing per scene type |
| `thumbnail.py` | DALL-E 3 image + Pillow text overlay |
| `shorts.py` | Vertical 9:16 Short from climax scene |
| `youtube_upload.py` | YouTube Data API v3 upload + thumbnail set |

### Web Dashboard (`web/`)
- **FastAPI + Jinja2 + Tailwind CSS + Video.js** dark theme UI
- Dashboard overview with episode/job/character stats, active pipeline tracker, Wan 2.1 download status
- Episode detail: Video.js player for full episode + per-scene audio/video players + Shorts players
- **Voice management**: per-scene voiceover regeneration with voice selector (20+ ElevenLabs voices), per-character voice assignment on Characters page
- Job list + log viewer with status badges, step timeline, and durations
- Character roster with win/loss stats, alive status, and assigned voice
- Generate page with live pipeline progress bar and log polling
- Settings page showing API key status and config
- **API cost tracking**: per-episode cost breakdown on detail page, total cost on dashboard with modal breakdown by service
- API endpoints: `POST /api/generate`, `GET /api/voices`, `POST /api/scenes/{id}/regenerate-voice`, `POST /api/characters/{id}/voice`, `GET /api/episodes/{id}/costs`, `GET /api/costs/summary`, `GET /api/pipelines/active`, `GET /api/wan21/status`
- Auto-generated API docs at `/docs`

## Full Pipeline Flow

```
1. Script (GPT-4o) -> Episode + Scenes + Character Results
2. SEO (GPT-4o) -> Optimized title, description, tags, hashtags
3. Voiceover (ElevenLabs) -> Per-scene MP3 narration
4. Subtitles (Whisper) -> Per-scene SRT files
5. Video Clips (Wan2.1 / Minimax / Runway) -> Per-scene MP4
6. Music Mix (FFmpeg) -> Background music under narration per scene type
7. FFmpeg Merge -> Normalize codecs, concat clips + audio, burn subtitles
8. Thumbnail (DALL-E 3 + Pillow) -> 1280x720 PNG with text overlay
9. Shorts (FFmpeg) -> 1080x1920 vertical clip from climax scene
10. YouTube Upload (Data API v3) -> Video + Short + thumbnail + SEO metadata
```

## Video Provider Strategy

Default order: Wan 2.1 (local) -> Minimax (cloud) -> Runway (cloud)

Configure via `VIDEO_PROVIDER_ORDER` in `.env`. Only valid values: `wan21`, `minimax`, `runway`.

## Background Music

Place royalty-free MP3/WAV tracks in `assets/music/` subdirectories:
- `tension/` -- intro and general scenes
- `battle/` -- fight scenes
- `epic/` -- climax scenes
- `calm/` -- outro scenes

Music is mixed at -18dB under narration. If no tracks found, narration plays without music.

## Database Status Flows

**Episode**: `pending -> scripting -> voiceover -> video_gen -> editing -> ready -> uploaded`

**VideoJob**: `pending -> running -> ready`

**Scene**: `pending -> voiceover_done -> video_done`

On failure at any step: status set to `failed`, logged via isolated session.

## Story Continuity Rules

- Only alive characters fed into script context
- Last 3 episode summaries included in prompt
- Character results (wins/losses/deaths) persisted per episode
- Kill rule: every 5 episodes (only if > 3 alive characters)
- New character: every 10 episodes
- Grand tournament: every 20 episodes

## Celery Scheduling

- `generate_episode`: daily at 2AM UTC, auto-retry 3x, 1h time limit
- `generate_week_batch`: Sunday 1AM UTC, fault-tolerant, 8h time limit

## Infrastructure

- **Docker**: PostgreSQL 16 + Redis 7, ports bound to localhost only
- **Web**: FastAPI on port 8000 with auto-reload
- **Dependencies**: All pinned in `requirements.txt`

## How To Operate

```bash
# Setup
cp .env.example .env          # Fill in API keys
docker-compose up -d           # Start Postgres + Redis
.venv311/bin/alembic upgrade head
.venv311/bin/python -m database.seed

# Run
.venv311/bin/python main.py                              # Generate one episode
.venv311/bin/uvicorn web.app:app --host 0.0.0.0 --port 8000  # Start dashboard
.venv311/bin/python cli.py generate                      # CLI generate
.venv311/bin/python cli.py status                        # Check episodes

# Dashboard
http://localhost:8000          # Web UI
http://localhost:8000/docs     # API docs
```

## Environment Variables

**Required**: `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`

**Optional**: `MINIMAX_API_KEY`, `RUNWAY_API_KEY`, `YOUTUBE_CLIENT_SECRET_PATH`, `YOUTUBE_CREDENTIALS_PATH`

## Project Structure

```
config/settings.py              -- Environment config + validation
database/models.py              -- SQLAlchemy ORM models
database/connection.py          -- Session management + helpers
database/migrations/            -- Alembic migrations
database/seed.py                -- Initial data seeder
pipeline/consistency_manager.py -- Story continuity
pipeline/script_generator.py    -- GPT script generation
pipeline/seo.py                 -- YouTube SEO optimization
pipeline/voiceover.py           -- ElevenLabs narration
pipeline/subtitles.py           -- Whisper subtitles
pipeline/video_generator.py     -- Video providers + FFmpeg merge
pipeline/music.py               -- Background music mixer
pipeline/thumbnail.py           -- DALL-E 3 thumbnails
pipeline/shorts.py              -- YouTube Shorts extractor
pipeline/youtube_upload.py      -- YouTube Data API uploader
web/app.py                      -- FastAPI dashboard
web/templates/                  -- Jinja2 HTML templates
main.py                         -- Pipeline orchestrator
cli.py                          -- Click CLI
scheduler/tasks.py              -- Celery scheduled tasks
assets/music/                   -- Royalty-free background tracks
```
