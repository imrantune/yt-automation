# Spartacus Arena Product Overview

## What This Product Does

`yt-automation` is a fully automated content pipeline that creates Spartacus-style gladiator episodes and tracks narrative continuity in a database. The core system can run without manual editing once APIs and infrastructure are configured.

Primary outcome per run:
- Generate one episode script with continuity rules
- Produce scene voiceovers
- Generate scene video clips using provider fallback
- Merge clips + narration into one final video
- Update character stats (wins, losses, deaths) in DB
- Persist all state transitions and logs in PostgreSQL

## Architecture

### Config Layer (`config/settings.py`)
- Loads `.env` values with strict validation at startup
- **Fails fast** if required keys are missing (`OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `DATABASE_URL`, `REDIS_URL`)
- Validates provider order entries against known set (`wan21`, `minimax`, `runway`)
- Controls Wan2.1 frame limits (`WAN21_MAX_FRAMES`, default 49) to prevent OOM
- Creates output folders automatically

### Persistence Layer (`database/`)
- **Models** (`models.py`): SQLAlchemy ORM for Series, Episode, Scene, Character, CharacterStat, VideoJob, JobLog
  - Uses `datetime.now(timezone.utc)` (non-deprecated) for all timestamps
  - Episode and Character have `updated_at` columns with auto-update
- **Connection** (`connection.py`): session management with `session_scope()` context manager
  - DB helper functions (log_job_step, set_episode_status, etc.) never call rollback -- transaction control belongs to the caller
  - `log_job_step_isolated()` uses a separate session so failure logs survive parent rollbacks
- **Migration** (`migrations/`): Alembic with lowercase enum values matching ORM
  - `server_default` on all `created_at` columns for raw SQL safety
  - `ON DELETE CASCADE` / `SET NULL` on all foreign keys
  - Downgrade drops all enum types to allow clean re-runs

### Generation Pipeline (`pipeline/`)

**ConsistencyManager** (`consistency_manager.py`)
- Fetches alive characters, recent episode summaries, and rule flags
- Kill rule only fires when alive character count exceeds minimum threshold (3)
- Raises early if no alive characters exist (prevents nonsensical scripts)
- `apply_character_results()` persists GPT's character outcomes to DB:
  - Creates CharacterStat rows per episode
  - Updates Character.wins, Character.losses
  - Sets Character.is_alive = False on death events

**ScriptGenerator** (`script_generator.py`)
- GPT-4o strict JSON generation with validated scene structure
- Handles empty `response.choices` from OpenAI (content filter edge case)
- Unique title enforcement with retry (up to 5 suffix attempts)
- Character results are persisted after script generation (continuity works)

**VoiceoverGenerator** (`voiceover.py`)
- Per-scene ElevenLabs narration with content size validation (rejects < 1KB responses)
- Skips scenes with empty narration text
- Failure logs written via isolated session (always committed)

**VideoGenerator** (`video_generator.py`)
- Provider abstraction with ordered fallback chain
- Wan2.1: frame count capped to `WAN21_MAX_FRAMES` (default 49) to prevent OOM
- Wan2.1: correctly accesses `result.frames[0]` (nested list from diffusers)
- Minimax/Runway: `OSError` caught in write paths so disk failures trigger fallback
- Polling loops have debug-level logging for visibility

**FFmpeg Merge** (`merge_episode_assets`)
- Re-encodes all clips to canonical format before concat (handles mixed provider codecs/resolutions)
- Audio concat uses `-f concat` demuxer (not broken `concat:` protocol)
- All subprocess calls capture stderr for diagnostics
- Temp files (manifests, intermediates) cleaned up after merge
- `ffprobe` duration parsing gracefully handles `N/A` output

## Video Provider Strategy

Default provider order:
1. `Wan 2.1` local (primary, free, Apple Silicon MPS)
2. `Minimax` API (secondary)
3. `Runway` API (tertiary)

Switching behavior:
- Configure with `VIDEO_PROVIDER_ORDER` in `.env`
- Only valid values: `wan21`, `minimax`, `runway` (validated at startup)
- Example fast switch: `VIDEO_PROVIDER_ORDER=minimax,runway`

## Database Status Flows

**Episode**: `pending -> scripting -> voiceover -> video_gen -> editing -> ready`
On failure: `failed`

**VideoJob**: `pending -> running -> ready`
On failure: `failed`

**Scene**: `pending -> voiceover_done -> video_done`
On failure: `failed`

### Job Logging
- Every pipeline step writes to `job_logs` with step name, status, and message
- Success logs use the main session (committed with the step)
- **Failure logs use an isolated session** so they survive transaction rollbacks
- This means failures are always visible in the database

## Story Continuity Rules

- Only alive characters are fed into script generation context
- Last 3 episode summaries included in prompt context
- Character results (wins/losses/deaths) are persisted to DB after each episode
- Rule flags computed by episode number:
  - Every 5 episodes: one death event (only if > 3 alive characters)
  - Every 10 episodes: new character introduction
  - Every 20 episodes: grand tournament theme
- Duplicate title prevention with multi-attempt suffix generation

## End-to-End Runtime Flow

1. Create running `VideoJob` (committed immediately)
2. Generate and persist script/scenes via GPT-4o
3. Apply character results to DB (wins/losses/deaths)
4. Generate scene voiceovers via ElevenLabs
5. Generate scene clips via provider fallback chain
6. Normalize all clips to canonical codec/resolution
7. Merge media with FFmpeg (concat demuxer, proper re-encoding)
8. Mark job and episode as `ready`

### Failure behavior
- Each step's failure is logged via isolated session (always persisted)
- Main session is rolled back on any error
- Job and episode are marked `failed` in a separate session
- Pipeline exits cleanly without uncontrolled crash
- Celery tasks have retry with exponential backoff (up to 3 retries)

## Celery Scheduling

- `generate_episode`: daily at 2AM UTC, auto-retry 3x with 60-600s backoff, 1h time limit
- `generate_week_batch`: Sunday 1AM UTC, fault-tolerant (one failure doesn't stop remaining), 8h time limit
- Tasks import `run_pipeline` lazily to avoid circular imports

## Infrastructure

### Docker (`docker-compose.yml`)
- PostgreSQL 16 + Redis 7 with health checks
- Credentials parameterized via env vars (not hardcoded)
- Redis has password authentication
- Ports bound to `127.0.0.1` only (not exposed to network)

### Dependencies (`requirements.txt`)
- All packages have version ranges pinned (major+minor)
- No unused packages (removed `flask`, `ffmpeg-python`)

### Environment (`.env.example`)
- Complete template with comments explaining required vs optional keys
- Redis URL includes password parameter
- `WAN21_MAX_FRAMES` documented

## How To Operate

1. Copy env: `cp .env.example .env` and fill in API keys
2. Start infra: `docker-compose up -d`
3. Run migrations: `.venv311/bin/alembic upgrade head`
4. Seed initial data: `.venv311/bin/python -m database.seed`
5. Run one episode: `.venv311/bin/python main.py`
6. CLI commands:
   - `python cli.py status` -- recent episodes
   - `python cli.py characters` -- character roster
   - `python cli.py jobs` -- recent job statuses
   - `python cli.py generate` -- trigger one episode
   - `python cli.py schedule` -- show cron schedule
   - `python cli.py open-final-dir` -- print output directory

## Current Boundaries (Phase 1)

Included:
- Foundation, DB, script generation with continuity, voiceover, video generation with provider fallback, FFmpeg merge, scheduling, CLI

Planned for next phases:
- Subtitle burn-in (Whisper)
- Thumbnail generation (DALL-E 3 + Pillow)
- YouTube upload (Data API v3)
- YouTube Shorts generator
- Flask dashboard
- Analytics tracking
- Telegram alerts
- Multi-series support
